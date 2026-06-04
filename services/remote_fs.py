"""远程数据源接入层 — SFTP 客户端 + numpy 2.x shim + 原子写入 + 增量拉取。

承载所有"从 BMAC 服务器（root@47.243.252.92:/root/data_center/data/）读文件"的能力。
被 services/remote_puller.py（守护线程）和 services/cmc_client.py（按需触发）调用。

关键设计：
- numpy 2.x shim：服务器写 pkl 用 numpy 2.x，本地是 1.26.4，用 sys.modules 别名让旧版能读。
  详见 docs/remote_data_format.md §5。
- 原子写入：先写 path.tmp 再 os.replace(path)，避免 scanner 读到半写文件。
- 增量拉取：维护 .manifest.json（{remote_path: {mtime, size, local_path}}），
  按 mtime/size 跳过未变文件。
- 单连接复用：模块级 _SftpSession 单例，多次调用复用同一个 SSH 通道。
  失败时自动重连，但永不抛出（拉取失败 = 用上一次缓存）。
"""
from __future__ import annotations

import importlib
import json
import os
import shutil
import stat
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

import config

# ============================================================
# numpy 2.x shim
# ----------------
# 服务器 BMAC 用 numpy 2.x 写 pkl，引用 `numpy._core.*`。
# 本地 anaconda numpy 1.26.4 没这个命名空间，pickle.load 会报
# ModuleNotFoundError: No module named 'numpy._core'。
# 用 sys.modules 把 `numpy._core` 别名到 `numpy.core` 即可读取。
#
# 这段必须在任何 `import pickle` / `import pandas as pd` 之前生效（实际上也确实是）；
# 模块加载时执行一次，全进程范围内对所有 pickle.load 生效。
# ============================================================
def _install_numpy_core_shim() -> None:
    try:
        import numpy
    except ImportError:
        return
    # numpy 2.x：已有 _core，无需 shim；幂等 setdefault 不覆盖。
    try:
        import numpy.core  # noqa: F401
        import numpy.core.numeric  # noqa: F401
        import numpy.core.multiarray  # noqa: F401
    except ImportError:
        return

    sys.modules.setdefault("numpy._core", numpy.core)
    sys.modules.setdefault("numpy._core.numeric", numpy.core.numeric)
    sys.modules.setdefault("numpy._core.multiarray", numpy.core.multiarray)
    for sub in (
        "umath",
        "_methods",
        "fromnumeric",
        "_dtype",
        "_dtype_ctypes",
        "_internal",
        "arrayprint",
    ):
        try:
            mod = importlib.import_module(f"numpy.core.{sub}")
            sys.modules.setdefault(f"numpy._core.{sub}", mod)
        except ImportError:
            pass


_install_numpy_core_shim()

# 现在再 import pandas / pickle 才安全（实测 pandas 自带的 reducer 也会触发 numpy._core 路径）。
import pickle  # noqa: E402
import pandas as pd  # noqa: E402

# paramiko 在这里 import，因为它依赖 cryptography，可能有点重，错误延迟到运行时更友好。
try:
    import paramiko  # noqa: E402
except ImportError as exc:  # pragma: no cover - missing dep is a setup error
    paramiko = None  # type: ignore
    _PARAMIKO_IMPORT_ERROR: Optional[Exception] = exc
else:
    _PARAMIKO_IMPORT_ERROR = None


# ============================================================
# 配置（从 config 读取，缺失值就抛错而不是默认值，避免静默连错服务器）
# ============================================================
def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"环境变量 {name} 未设置；请在 .env 里配置（见 docs/specs/remote_data_integration.md §5）"
        )
    return value


def _connect_kwargs() -> dict:
    """组装 paramiko.SSHClient.connect() 的参数。优先用私钥，其次用密码。"""
    kw: dict = {
        "hostname": _require_env("REMOTE_HOST"),
        "port": int(os.getenv("REMOTE_PORT", "22")),
        "username": _require_env("REMOTE_USER"),
        "timeout": float(os.getenv("REMOTE_CONNECT_TIMEOUT", "15")),
        "banner_timeout": 15,
        "auth_timeout": 15,
    }
    key_path = os.getenv("REMOTE_KEY_PATH", "").strip()
    password = os.getenv("REMOTE_PASSWORD", "").strip()
    if key_path:
        kw["key_filename"] = key_path
    elif password:
        kw["password"] = password
        kw["look_for_keys"] = False
        kw["allow_agent"] = False
    else:
        raise RuntimeError(
            "REMOTE_KEY_PATH 和 REMOTE_PASSWORD 至少需要设置一个；"
            "推荐用私钥（REMOTE_KEY_PATH）"
        )
    return kw


REMOTE_DATA_ROOT: str = os.getenv("REMOTE_DATA_ROOT", "/root/data_center/data/").rstrip("/") + "/"
LOCAL_CACHE_DIR: Path = Path(os.getenv("LOCAL_CACHE_DIR", "data/remote_cache")).resolve()
SFTP_READ_TIMEOUT: float = float(os.getenv("REMOTE_READ_TIMEOUT", "30"))

# 数据后端：'sftp'（默认，连远程 BMAC）| 'local'（直接读同机产出目录，无 SSH/凭据）
REMOTE_BACKEND: str = os.getenv("REMOTE_BACKEND", "sftp").strip().lower()


def _is_local_backend() -> bool:
    return REMOTE_BACKEND == "local"


# ============================================================
# Manifest（本地增量拉取的状态）
# ============================================================
@dataclass
class ManifestEntry:
    remote_path: str
    local_path: str
    size: int
    mtime: float  # unix timestamp, float
    fetched_at: float  # unix timestamp, float


class Manifest:
    """data/remote_cache/.manifest.json，记录每个已拉取文件的 mtime/size。
    用于增量拉取：再次访问时，若服务器 mtime/size 没变就跳过下载。
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.path = cache_dir / ".manifest.json"
        self._lock = threading.Lock()
        self._data: dict[str, ManifestEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in raw.items():
                self._data[k] = ManifestEntry(**v)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("manifest 加载失败 ({})，按全新拉取处理", exc)
            self._data = {}

    def _save(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {k: v.__dict__ for k, v in self._data.items()},
                f,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(tmp, self.path)

    def get(self, remote_path: str) -> Optional[ManifestEntry]:
        with self._lock:
            return self._data.get(remote_path)

    def update(self, entry: ManifestEntry) -> None:
        with self._lock:
            self._data[entry.remote_path] = entry
            self._save()


# ============================================================
# SFTP 会话单例
# ============================================================
class _SftpSession:
    """模块级单例。线程安全（用锁串行化 SFTP 操作 — paramiko Channel 非线程安全）。
    失败自动重连；连续 N 次失败标记 unhealthy，让调用方降级到本地缓存。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._client: Optional["paramiko.SSHClient"] = None
        self._sftp: Optional["paramiko.SFTPClient"] = None
        self._consecutive_failures = 0
        self._last_error: Optional[str] = None

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def _ensure_connected(self) -> None:
        if paramiko is None:
            raise RuntimeError(f"paramiko 未安装: {_PARAMIKO_IMPORT_ERROR}")
        if self._client is not None and self._sftp is not None:
            try:
                # 检查 channel 活性：listdir('.') 会立刻抛错如果断了
                self._sftp.stat(".")
                return
            except Exception:
                logger.info("SFTP 会话已断开，重连中")
                self._close_quietly()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(**_connect_kwargs())
        sftp = client.open_sftp()
        sftp.sock.settimeout(SFTP_READ_TIMEOUT)
        self._client = client
        self._sftp = sftp
        logger.info("SFTP 连接已建立: {}@{}", _connect_kwargs()["username"], _connect_kwargs()["hostname"])

    def _close_quietly(self) -> None:
        try:
            if self._sftp is not None:
                self._sftp.close()
        except Exception:
            pass
        try:
            if self._client is not None:
                self._client.close()
        except Exception:
            pass
        self._sftp = None
        self._client = None

    def close(self) -> None:
        with self._lock:
            self._close_quietly()

    @contextmanager
    def sftp(self):
        """`with session.sftp() as sftp:` 拿到一个保证活着的 SFTP 句柄。
        操作期间持有锁。"""
        with self._lock:
            try:
                self._ensure_connected()
                assert self._sftp is not None
                yield self._sftp
                self._consecutive_failures = 0
                self._last_error = None
            except Exception as exc:
                self._consecutive_failures += 1
                self._last_error = repr(exc)
                self._close_quietly()
                raise


_session = _SftpSession()


def get_session_status() -> dict:
    """供 puller / 监控查询当前 SFTP 健康度。"""
    return {
        "consecutive_failures": _session.consecutive_failures,
        "last_error": _session.last_error,
    }


# ============================================================
# 公共 API
# ============================================================
def list_dir(remote_path: str) -> list[tuple[str, int, float]]:
    """列目录。返回 [(filename, size, mtime), ...]。
    paramiko 对某些服务器/路径在 listdir_attr 上不接受 trailing slash，统一去掉。"""
    cleaned = remote_path.rstrip("/") or "/"
    if _is_local_backend():
        local_entries: list[tuple[str, int, float]] = []
        with os.scandir(cleaned) as it:
            for entry in it:
                try:
                    st = entry.stat()
                except OSError:
                    continue
                local_entries.append((entry.name, int(st.st_size), float(st.st_mtime)))
        return local_entries
    with _session.sftp() as sftp:
        entries = []
        for attr in sftp.listdir_attr(cleaned):
            entries.append((attr.filename, attr.st_size, float(attr.st_mtime)))
        return entries


def stat_remote(remote_path: str) -> Optional[tuple[int, float]]:
    """返回 (size, mtime)。文件不存在返回 None。"""
    if _is_local_backend():
        try:
            st = os.stat(remote_path)
            return (int(st.st_size), float(st.st_mtime))
        except FileNotFoundError:
            return None
    try:
        with _session.sftp() as sftp:
            attr = sftp.stat(remote_path)
            return (attr.st_size, float(attr.st_mtime))
    except IOError as exc:
        if "No such file" in str(exc):
            return None
        raise


def find_latest_ready(remote_dir: str, prefix: str) -> Optional[tuple[str, int]]:
    """在 remote_dir 下找 {prefix}_{ts}.ready 命名的最大 ts 文件。
    返回 (ready_filename, cutoff_ts) 或 None。

    例：find_latest_ready('preprocess_1h_resample/30m/', 'market_pivot_spot')
        → ('market_pivot_spot_1778920500.ready', 1778920500)
    """
    full = REMOTE_DATA_ROOT + remote_dir.lstrip("/")
    try:
        entries = list_dir(full)
    except IOError as exc:
        logger.warning("list_dir 失败: {} ({})", full, exc)
        return None
    best: Optional[tuple[str, int]] = None
    for name, _size, _mtime in entries:
        if not (name.startswith(prefix + "_") and name.endswith(".ready")):
            continue
        ts_str = name[len(prefix) + 1 : -len(".ready")]
        try:
            ts = int(ts_str)
        except ValueError:
            continue
        if best is None or ts > best[1]:
            best = (name, ts)
    return best


# ============================================================
# 拉取 + 原子写
# ============================================================
_manifest = Manifest(LOCAL_CACHE_DIR)


def _atomic_write_from_sftp(sftp, remote_path: str, local_path: Path) -> None:
    """SFTP 把 remote_path 流式写到 local_path.tmp，最后 os.replace 成 local_path。
    本地短暂存在 .tmp 文件期间，scanner 永远看到的是上一次的稳定版本（或不存在）。
    """
    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = local_path.with_suffix(local_path.suffix + ".tmp")
    try:
        sftp.get(remote_path, str(tmp))
        os.replace(tmp, local_path)
    except Exception:
        # 清理半成品 .tmp
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _atomic_copy_local(src_path: str, local_path: Path) -> None:
    """本地后端：把 src_path 原子拷到 local_path（先写 .tmp 再 os.replace）。
    与 _atomic_write_from_sftp 同样的原子语义，只是源在本地盘。
    """
    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = local_path.with_suffix(local_path.suffix + ".tmp")
    try:
        shutil.copy2(src_path, str(tmp))
        os.replace(tmp, local_path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def pull(remote_rel_path: str, local_name: Optional[str] = None, *, force: bool = False) -> Optional[Path]:
    """拉一个远程文件到本地缓存。

    Args:
        remote_rel_path: 相对 REMOTE_DATA_ROOT 的路径，如 "preprocess_1h_resample/30m/market_pivot_spot_2026.pkl"
        local_name: 本地文件名（默认用 remote 文件名）。建议保留默认，否则 manifest 难索引。
        force: True 时跳过 mtime/size 检查，强制重新拉

    Returns:
        本地文件 Path（拉取成功或已是最新），或 None（远程不存在 / 拉取失败）
    """
    remote_full = REMOTE_DATA_ROOT + remote_rel_path.lstrip("/")
    local_path = LOCAL_CACHE_DIR / (local_name or remote_rel_path.replace("/", "__"))

    # 1) 看服务器侧 stat
    try:
        stat_info = stat_remote(remote_full)
    except Exception as exc:
        logger.warning("pull 失败（远程 stat）: {} ({})", remote_full, exc)
        return local_path if local_path.exists() else None

    if stat_info is None:
        logger.warning("远程文件不存在: {}", remote_full)
        return None

    size, mtime = stat_info

    # 2) 看本地 manifest 是否已经是最新
    if not force:
        entry = _manifest.get(remote_full)
        if entry and entry.size == size and entry.mtime == mtime and local_path.exists():
            return local_path

    # 3) 拉/拷
    logger.info("取数据文件: {} → {} ({} bytes)", remote_full, local_path.name, size)
    started = time.time()
    try:
        if _is_local_backend():
            _atomic_copy_local(remote_full, local_path)
        else:
            with _session.sftp() as sftp:
                _atomic_write_from_sftp(sftp, remote_full, local_path)
    except Exception as exc:
        logger.warning("pull 失败（取文件）: {} ({})", remote_full, exc)
        return local_path if local_path.exists() else None
    elapsed = time.time() - started
    logger.info("拉取完成: {} (耗时 {:.1f}s)", local_path.name, elapsed)

    _manifest.update(
        ManifestEntry(
            remote_path=remote_full,
            local_path=str(local_path),
            size=size,
            mtime=mtime,
            fetched_at=time.time(),
        )
    )
    return local_path


def pull_many(remote_rel_paths: Iterable[str], *, force: bool = False) -> dict[str, Optional[Path]]:
    """批量拉取，复用同一个 SFTP 会话。返回 {remote_rel_path: local_path or None}。"""
    result: dict[str, Optional[Path]] = {}
    for rel in remote_rel_paths:
        result[rel] = pull(rel, force=force)
    return result


# ============================================================
# 加载 pkl（自动应用 numpy shim，因为 shim 在模块顶层已生效）
# ============================================================
def load_pickle(local_path: Path):
    """从本地缓存加载 pkl。numpy 2.x 写的 pkl 也能读（shim 已生效）。"""
    with open(local_path, "rb") as f:
        return pickle.load(f)


def load_pkl_as_df(local_path: Path) -> pd.DataFrame:
    """如果 pkl 内是 DataFrame 直接返回；否则抛 TypeError。"""
    obj = load_pickle(local_path)
    if not isinstance(obj, pd.DataFrame):
        raise TypeError(f"{local_path} 内不是 DataFrame，是 {type(obj).__name__}")
    return obj


# ============================================================
# 时区辅助：服务器是 UTC, market_monitor DB 是 UTC naive
# ============================================================
def utc_naive(ts) -> datetime:
    """把 pandas Timestamp / datetime（含 tz 或不含）转成 UTC naive datetime。
    供 scanner 入库前调用。
    """
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if isinstance(ts, datetime):
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        return ts
    raise TypeError(f"无法转换 {type(ts).__name__} 为 datetime")
