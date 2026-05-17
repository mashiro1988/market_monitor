"""远程数据 puller 守护线程。

跑一个后台线程,每 60s 轮询 BMAC 服务器的 .ready flag。
发现某 dataset 的 cutoff 比上次记录的新 -> 拉对应 pkl 到 data/remote_cache/。

为什么独立线程,不进 fast_scan 主循环:
- 解耦网络耗时和扫描节奏。SFTP 卡 30s 不应该让 5min 告警停摆。
- 不同 dataset 节奏不一样(pivot 1h, exginfo 几乎不变),专门的轮询比塞进 5min 浪费。
- 失败降级自然:拉不到就用上一次的 cache,scanner 一直能跑。

Phase 1 拉的 dataset(够板块计算 + symbol 映射):
- preprocess_1h_resample/{offset}/market_pivot_spot_{year}.pkl
- preprocess_1h_resample/{offset}/market_pivot_swap_{year}.pkl
- exginfo/spot_swap_matches.pkl

后续 phase 加入按需订阅币的 binance_swap_1h_resample/{offset}/{SYMBOL}USDT.pkl
和 binance_swap_5m/{SYMBOL}/{YYYYMM}.pkl。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from services import remote_fs


# ============================================================
# 配置
# ============================================================
POLL_INTERVAL_SECONDS = int(os.getenv("REMOTE_PULLER_POLL_SECONDS", "60"))
REMOTE_OFFSET = os.getenv("REMOTE_OFFSET", "30m")  # BMAC 的 hour offset 子目录


# ============================================================
# 待拉数据集声明
# ----------------
# 每条:
#   ready_dir       : 服务器上放 .ready 的目录(相对 REMOTE_DATA_ROOT)
#   ready_prefix    : .ready 文件名前缀,find_latest_ready 用
#   resolve_pkl_rel : (cutoff_ts: int) -> 待拉 pkl 的相对路径
# ============================================================
@dataclass
class DatasetSpec:
    name: str
    ready_dir: str
    ready_prefix: str
    resolve_pkl_rel: callable  # type: ignore[type-arg]


def _market_pivot_spot_pkl(cutoff_ts: int) -> str:
    """cutoff_ts 是 .ready 的 unix epoch。年份从 cutoff_ts 取最稳。"""
    year = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).year
    return f"preprocess_1h_resample/{REMOTE_OFFSET}/market_pivot_spot_{year}.pkl"


def _market_pivot_swap_pkl(cutoff_ts: int) -> str:
    year = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).year
    return f"preprocess_1h_resample/{REMOTE_OFFSET}/market_pivot_swap_{year}.pkl"


def _spot_swap_matches_pkl(cutoff_ts: int) -> str:
    return "exginfo/spot_swap_matches.pkl"


PHASE1_DATASETS: list[DatasetSpec] = [
    DatasetSpec(
        name="market_pivot_spot",
        ready_dir=f"preprocess_1h_resample/{REMOTE_OFFSET}/",
        ready_prefix="market_pivot_spot",
        resolve_pkl_rel=_market_pivot_spot_pkl,
    ),
    DatasetSpec(
        name="market_pivot_swap",
        ready_dir=f"preprocess_1h_resample/{REMOTE_OFFSET}/",
        ready_prefix="market_pivot_swap",
        resolve_pkl_rel=_market_pivot_swap_pkl,
    ),
    DatasetSpec(
        name="spot_swap_matches",
        ready_dir="exginfo/",
        ready_prefix="spot_swap_matches",
        resolve_pkl_rel=_spot_swap_matches_pkl,
    ),
]

# 拉到这些 dataset 后,立刻触发 sector_scan 把 DB 同步上去.
# 让 leaderboard (读 DB) 跟 token 钻取 (读 pivot) 的 snapshot_at 永远在同一秒.
PIVOT_DATASETS_TRIGGERING_SCAN = {"market_pivot_spot", "market_pivot_swap"}


# ============================================================
# 运行状态
# ============================================================
@dataclass
class DatasetStatus:
    name: str
    last_cutoff_ts: Optional[int] = None
    last_pull_at: Optional[datetime] = None  # UTC naive
    last_error: Optional[str] = None
    consecutive_errors: int = 0


@dataclass
class PullerStatus:
    started_at: Optional[datetime] = None  # UTC naive
    last_tick_at: Optional[datetime] = None
    tick_count: int = 0
    datasets: dict[str, DatasetStatus] = field(default_factory=dict)


# ============================================================
# Puller
# ============================================================
class RemotePuller:
    """守护线程封装。模块级单例 _puller 由 get_puller() 暴露。"""

    def __init__(self, datasets: list[DatasetSpec], poll_interval: int = POLL_INTERVAL_SECONDS):
        self._datasets = datasets
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status = PullerStatus(datasets={d.name: DatasetStatus(name=d.name) for d in datasets})
        self._status_lock = threading.Lock()

    # ---- 生命周期 ----
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.debug("remote_puller 已运行,start() 忽略")
            return
        self._stop_event.clear()
        self._status.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._thread = threading.Thread(target=self._run_loop, name="remote-puller", daemon=True)
        self._thread.start()
        logger.info("remote_puller 启动 (poll={}s, offset={})", self._poll_interval, REMOTE_OFFSET)

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        logger.info("remote_puller 停止中...")
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            logger.warning("remote_puller 线程未在 {}s 内退出", timeout)
        self._thread = None

    # ---- 主循环 ----
    def _run_loop(self) -> None:
        # 启动后先跑一次,不要等 60s
        self._tick()
        while not self._stop_event.is_set():
            # wait() 会在 stop_event 被 set 时立刻返回 True
            if self._stop_event.wait(timeout=self._poll_interval):
                break
            self._tick()

    def _tick(self) -> None:
        with self._status_lock:
            self._status.tick_count += 1
            self._status.last_tick_at = datetime.now(timezone.utc).replace(tzinfo=None)

        pivot_was_updated = False
        for spec in self._datasets:
            try:
                pulled = self._pull_if_newer(spec)
                if pulled and spec.name in PIVOT_DATASETS_TRIGGERING_SCAN:
                    pivot_was_updated = True
            except Exception as exc:
                # 永远不抛出 -- 让 puller 下一轮继续
                logger.exception("dataset {} 拉取异常: {}", spec.name, exc)
                with self._status_lock:
                    ds = self._status.datasets[spec.name]
                    ds.last_error = repr(exc)
                    ds.consecutive_errors += 1

        # pivot 有更新 -> 同步触发 sector_scan, 让 DB 立刻反映最新 pivot
        if pivot_was_updated:
            self._trigger_sector_scan()

    def _trigger_sector_scan(self) -> None:
        """拉到新 pivot 后跑一次 SectorScanner.scan(), 同步写 sector_returns."""
        try:
            # 延迟 import 避免循环依赖 (scanners.sector_scanner -> services.sector_service
            # -> services.remote_fs 之类的链)
            from scanners.sector_scanner import SectorScanner
            result = SectorScanner().scan()
            logger.info("post-pull sector_scan: {}", result)
        except Exception as exc:
            logger.exception("post-pull sector_scan 失败: {}", exc)

    def _pull_if_newer(self, spec: DatasetSpec) -> bool:
        """拉取 spec 对应的 pkl 如果有新 cutoff. 返回 True 仅当实际下载了新文件."""
        # 1) 找最新 .ready cutoff
        result = remote_fs.find_latest_ready(spec.ready_dir, spec.ready_prefix)
        if result is None:
            logger.debug("dataset {} 暂无 .ready", spec.name)
            return False
        ready_name, cutoff_ts = result

        # 2) 跟内存里上次记录比较
        with self._status_lock:
            ds = self._status.datasets[spec.name]
            last = ds.last_cutoff_ts

        if last is not None and cutoff_ts <= last:
            return False  # 不新, 跳过

        # 3) 触发拉取(remote_fs.pull 自己还会做 mtime/size 二次校验)
        pkl_rel = spec.resolve_pkl_rel(cutoff_ts)
        local = remote_fs.pull(pkl_rel)
        if local is None:
            logger.warning("dataset {} pkl 拉取失败 (ready={}, pkl_rel={})",
                           spec.name, ready_name, pkl_rel)
            with self._status_lock:
                ds = self._status.datasets[spec.name]
                ds.last_error = "pull returned None"
                ds.consecutive_errors += 1
            return False

        # 4) 记录成功
        with self._status_lock:
            ds = self._status.datasets[spec.name]
            ds.last_cutoff_ts = cutoff_ts
            ds.last_pull_at = datetime.now(timezone.utc).replace(tzinfo=None)
            ds.last_error = None
            ds.consecutive_errors = 0
        logger.info("dataset {} 已更新到 cutoff={} ({})",
                    spec.name, cutoff_ts,
                    datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat())
        return True

    # ---- 公共查询 ----
    def status(self) -> PullerStatus:
        with self._status_lock:
            # 浅拷贝就够,DatasetStatus 是 frozen 风格
            return PullerStatus(
                started_at=self._status.started_at,
                last_tick_at=self._status.last_tick_at,
                tick_count=self._status.tick_count,
                datasets={k: DatasetStatus(**v.__dict__) for k, v in self._status.datasets.items()},
            )

    def force_tick(self) -> None:
        """手动触发一次拉取(同步,在调用线程跑)。供 CLI / API 调试用。"""
        self._tick()


# ============================================================
# 模块单例
# ============================================================
_puller: Optional[RemotePuller] = None
_puller_lock = threading.Lock()


def get_puller() -> RemotePuller:
    """获取全局唯一 puller 实例。"""
    global _puller
    with _puller_lock:
        if _puller is None:
            _puller = RemotePuller(PHASE1_DATASETS)
        return _puller


def start_puller() -> None:
    """启动 puller(若未启动)。供 FastAPI lifespan 调用。"""
    get_puller().start()


def stop_puller() -> None:
    """停止 puller。供 FastAPI lifespan shutdown 调用。"""
    if _puller is not None:
        _puller.stop()


def get_status() -> PullerStatus:
    return get_puller().status()
