# Market Monitor 公网部署 + 本地数据后端 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `services/remote_fs.py` 加一个 `REMOTE_BACKEND=local` 本地文件后端（TDD），然后把 market_monitor 部署到腾讯云日本 Ubuntu 服务器，经 Nginx（Basic Auth + Let's Encrypt HTTPS）+ systemd 单 worker 实现公网访问，数据直接读同机 BMAC 产出。

**Architecture:** 数据层本就是「取文件→本地缓存→读缓存」；只需让 `list_dir`/`stat_remote`/`pull` 三个底层原语在 `local` 模式下走 `os`/`shutil` 而非 paramiko SFTP，上层 (`find_latest_ready`/`load_*`) 与 `remote_puller` 不动。部署侧用 systemd 跑 `uvicorn api.app:app`（绑 127.0.0.1，单 worker），Nginx 反代加鉴权与 TLS。

**Tech Stack:** Python 3 / FastAPI / APScheduler / pandas / pytest（阶段 1）；Ubuntu / systemd / Nginx / certbot（阶段 2）。

**完整设计与 Runbook：** `docs/specs/deployment.md`（本计划是它的「带检查点的执行版」，命令细节以该 spec 为准，避免重复）。

---

## 阶段总览

- **阶段 1（本地 Windows，D:\market_monitor）**：`remote_fs` 本地后端，TDD，6 个 task，最后推 GitHub `main`。
- **阶段 2（服务器 Ubuntu）**：按 `deployment.md` Runbook 逐 Phase 执行，每步有验证门。

> 阶段 1 必须先合进 `main`，阶段 2 服务器才能 clone 到带 `REMOTE_BACKEND=local` 的代码。

## File Structure

- **Modify** `services/remote_fs.py` — 加 `REMOTE_BACKEND` 开关 + `_is_local_backend()` + `_atomic_copy_local()`；在 `list_dir`/`stat_remote`/`pull` 顶部分支本地实现。职责不变，只多一个后端。
- **Create** `tests/test_remote_fs_local.py` — 本地后端单测（用 `tmp_path`+`monkeypatch`，不触网）。
- **Reference**（不改）`services/remote_puller.py` — 前缀/路径已与本地数据逐字匹配。
- **Reference**（已写好）`docs/specs/deployment.md` — 阶段 2 的命令来源。
- **(可选) Modify** 本地地图文档 `ARCHITECTURE.md`/`DATAFLOW.md`/`DECISIONS.md`/`PENDING.md`（均 gitignored，仅本地）— 按 AGENTS.md 约定记录新后端。

---

## 阶段 1：`remote_fs` 本地文件后端（TDD）

> 全部在本地仓库 `D:\market_monitor` 执行。建议开分支：`git switch -c feat/local-data-backend`。
> 测试命令统一：`python -m pytest tests/test_remote_fs_local.py -v`

### Task 1: 加 `REMOTE_BACKEND` 开关 + 回归断言

**Files:**
- Modify: `services/remote_fs.py`（在 `REMOTE_DATA_ROOT`/`LOCAL_CACHE_DIR` 定义附近，约 131–133 行）
- Test: `tests/test_remote_fs_local.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_remote_fs_local.py
import pickle
import pandas as pd
import pytest

from services import remote_fs


def test_is_local_backend_reflects_flag(monkeypatch):
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "sftp")
    assert remote_fs._is_local_backend() is False
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "local")
    assert remote_fs._is_local_backend() is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_remote_fs_local.py::test_is_local_backend_reflects_flag -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'REMOTE_BACKEND'` 或 `_is_local_backend`）

- [ ] **Step 3: 最小实现**

在 `services/remote_fs.py` 顶部 `import` 区加 `import shutil`；在 `SFTP_READ_TIMEOUT` 定义之后加：

```python
# 数据后端：'sftp'（默认，连远程 BMAC）| 'local'（直接读同机产出目录）
REMOTE_BACKEND: str = os.getenv("REMOTE_BACKEND", "sftp").strip().lower()


def _is_local_backend() -> bool:
    return REMOTE_BACKEND == "local"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_remote_fs_local.py::test_is_local_backend_reflects_flag -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add services/remote_fs.py tests/test_remote_fs_local.py
git commit -m "feat(remote_fs): add REMOTE_BACKEND switch (sftp default)"
```

### Task 2: `list_dir` 本地实现

**Files:** Modify `services/remote_fs.py:list_dir`（约 287 行）；Test 同文件。

- [ ] **Step 1: 写失败测试 + 共用 fixture**

```python
@pytest.fixture
def local_backend(tmp_path, monkeypatch):
    """把 remote_fs 切到 local 后端，数据根/缓存指向 tmp。"""
    data_root = tmp_path / "data"
    cache = tmp_path / "cache"
    data_root.mkdir()
    cache.mkdir()
    monkeypatch.setattr(remote_fs, "REMOTE_BACKEND", "local")
    monkeypatch.setattr(remote_fs, "REMOTE_DATA_ROOT", str(data_root) + "/")
    monkeypatch.setattr(remote_fs, "LOCAL_CACHE_DIR", cache)
    monkeypatch.setattr(remote_fs, "_manifest", remote_fs.Manifest(cache))
    return data_root, cache


def test_list_dir_local(local_backend):
    data_root, _ = local_backend
    (data_root / "a.pkl").write_bytes(b"x" * 10)
    (data_root / "b.ready").write_text("ok")
    rows = remote_fs.list_dir(str(data_root))
    names = {n for (n, _s, _m) in rows}
    assert names == {"a.pkl", "b.ready"}
    sizes = {n: s for (n, s, _m) in rows}
    assert sizes["a.pkl"] == 10
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_remote_fs_local.py::test_list_dir_local -v`
Expected: FAIL（local 模式下仍走 SFTP，会因无 `REMOTE_HOST` 抛 RuntimeError）

- [ ] **Step 3: 最小实现** — 在 `list_dir` 顶部（`cleaned = ...` 之后）加本地分支：

```python
    cleaned = remote_path.rstrip("/") or "/"
    if _is_local_backend():
        entries: list[tuple[str, int, float]] = []
        with os.scandir(cleaned) as it:
            for e in it:
                try:
                    st = e.stat()
                except OSError:
                    continue
                entries.append((e.name, int(st.st_size), float(st.st_mtime)))
        return entries
    with _session.sftp() as sftp:   # ← 以下为原有 SFTP 逻辑，保持不变
        ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_remote_fs_local.py::test_list_dir_local -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add services/remote_fs.py tests/test_remote_fs_local.py
git commit -m "feat(remote_fs): local backend for list_dir"
```

### Task 3: `stat_remote` 本地实现

**Files:** Modify `services/remote_fs.py:stat_remote`（约 298 行）；Test 同文件。

- [ ] **Step 1: 写失败测试**

```python
def test_stat_remote_local(local_backend):
    data_root, _ = local_backend
    f = data_root / "x.pkl"
    f.write_bytes(b"12345")
    size, mtime = remote_fs.stat_remote(str(f))
    assert size == 5
    assert mtime > 0
    assert remote_fs.stat_remote(str(data_root / "missing.pkl")) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_remote_fs_local.py::test_stat_remote_local -v`
Expected: FAIL

- [ ] **Step 3: 最小实现** — 在 `stat_remote` 顶部加本地分支：

```python
def stat_remote(remote_path: str) -> Optional[tuple[int, float]]:
    if _is_local_backend():
        try:
            st = os.stat(remote_path)
            return (int(st.st_size), float(st.st_mtime))
        except FileNotFoundError:
            return None
    try:                              # ← 以下为原有 SFTP 逻辑
        with _session.sftp() as sftp:
            ...
```

- [ ] **Step 4: 跑测试确认通过** — Expected: PASS
- [ ] **Step 5: 提交**

```bash
git add services/remote_fs.py tests/test_remote_fs_local.py
git commit -m "feat(remote_fs): local backend for stat_remote"
```

### Task 4: `pull` 本地原子拷贝

**Files:** Modify `services/remote_fs.py`（加 `_atomic_copy_local`；改 `pull` 第 3 步下载块，约 394–404 行）；Test 同文件。

- [ ] **Step 1: 写失败测试**

```python
def test_pull_local_copies_then_skips(local_backend):
    data_root, cache = local_backend
    rel = "preprocess_1h_resample/30m/market_pivot_spot_2026.pkl"
    src = data_root / rel
    src.parent.mkdir(parents=True)
    df = pd.DataFrame({"BTC": [1.0, 2.0]})
    src.write_bytes(pickle.dumps(df))

    out = remote_fs.pull(rel)
    assert out is not None and out.exists()
    assert out.parent == cache
    pd.testing.assert_frame_equal(remote_fs.load_pkl_as_df(out), df)

    # 源未变 → 命中 manifest，不重复拷贝（mtime 不变）
    first_mtime = out.stat().st_mtime
    assert remote_fs.pull(rel) == out
    assert out.stat().st_mtime == first_mtime
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_remote_fs_local.py::test_pull_local_copies_then_skips -v`
Expected: FAIL（下载块仍走 `_session.sftp()`）

- [ ] **Step 3: 最小实现**

(a) 在 `_atomic_write_from_sftp` 附近加：

```python
def _atomic_copy_local(src_path: str, local_path: Path) -> None:
    """本地后端：把 src_path 原子拷到 local_path（先写 .tmp 再 os.replace）。"""
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
```

(b) 把 `pull` 第 3 步的下载块改成分支（其余不动，`stat_remote` 已在 local 模式工作）：

```python
    try:
        if _is_local_backend():
            _atomic_copy_local(remote_full, local_path)
        else:
            with _session.sftp() as sftp:
                _atomic_write_from_sftp(sftp, remote_full, local_path)
    except Exception as exc:
        logger.warning("pull 失败（取文件）: {} ({})", remote_full, exc)
        return local_path if local_path.exists() else None
```

- [ ] **Step 4: 跑测试确认通过** — Expected: PASS
- [ ] **Step 5: 提交**

```bash
git add services/remote_fs.py tests/test_remote_fs_local.py
git commit -m "feat(remote_fs): local backend for pull (atomic copy)"
```

### Task 5: `find_latest_ready` 端到端（local，前缀过滤）

**Files:** Test only（验证 `find_latest_ready` 经本地 `list_dir` 正确工作 + 忽略异前缀 `.ready`）。

- [ ] **Step 1: 写测试**

```python
def test_find_latest_ready_local(local_backend):
    data_root, _ = local_backend
    d = data_root / "preprocess_1h_resample" / "30m"
    d.mkdir(parents=True)
    for ts in (1780470000, 1780479000, 1780460000):
        (d / f"market_pivot_spot_{ts}.ready").write_text("x")
    (d / "spot_dict_1780479000.ready").write_text("x")  # 异前缀，须忽略
    assert remote_fs.find_latest_ready(
        "preprocess_1h_resample/30m/", "market_pivot_spot"
    ) == ("market_pivot_spot_1780479000.ready", 1780479000)
```

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/test_remote_fs_local.py::test_find_latest_ready_local -v`
Expected: PASS（无需改代码——`find_latest_ready` 调用已分支的 `list_dir`）

- [ ] **Step 3: 提交**

```bash
git add tests/test_remote_fs_local.py
git commit -m "test(remote_fs): find_latest_ready over local backend"
```

### Task 6: `remote_puller` 本地冒烟（端到端，不跑 sector_scan）

**Files:** Test only。验证真实 `PHASE1_DATASETS` 规格能从本地目录拉到。

- [ ] **Step 1: 写测试**

```python
def test_puller_pull_if_newer_local(local_backend, monkeypatch):
    from services import remote_puller

    data_root, _ = local_backend
    spec = remote_puller.PHASE1_DATASETS[0]  # market_pivot_spot
    d = data_root / "preprocess_1h_resample" / "30m"
    d.mkdir(parents=True)
    ts = 1780479000  # → year 2026
    (d / f"market_pivot_spot_{ts}.ready").write_text("x")
    (d / "market_pivot_spot_2026.pkl").write_bytes(
        pickle.dumps(pd.DataFrame({"BTC": [1.0]}))
    )
    puller = remote_puller.RemotePuller([spec])
    assert puller._pull_if_newer(spec) is True    # 首次拉到
    assert puller._pull_if_newer(spec) is False   # cutoff 不变 → 跳过
```

- [ ] **Step 2: 跑测试** — Run: `python -m pytest tests/test_remote_fs_local.py::test_puller_pull_if_newer_local -v` — Expected: PASS
- [ ] **Step 3: 提交**

```bash
git add tests/test_remote_fs_local.py
git commit -m "test(remote_puller): pull_if_newer over local backend"
```

### Task 7: 全量回归 + 更新地图文档 + 推 main

- [ ] **Step 1: 跑全量后端测试**

Run: `python -m pytest -q`
Expected: 全绿（原有测试全部 + 新增 6 个；新后端不影响默认 sftp 路径）

- [ ] **Step 2:（可选）更新本地地图文档** — 按 `AGENTS.md` 约定，在 `DATAFLOW.md`/`DECISIONS.md`/`PENDING.md` 记一笔「remote_fs 增 local 后端，部署用本地数据」。这些文件 gitignored，仅本地留痕，不进提交。

- [ ] **Step 3: 合并并推送**

```bash
git switch main
git merge --no-ff feat/local-data-backend -m "feat: local filesystem backend for BMAC data"
git push origin main
```

Expected: GitHub `main` 含 `REMOTE_BACKEND` 本地后端。**阶段 2 的前提达成。**

---

## 阶段 2：服务器部署（按 `deployment.md` Runbook，逐 Phase 带验证门）

> 命令块见 `docs/specs/deployment.md` 对应 Phase；下面每个 task 给「做什么 + 通过标准」。**任一验证门不过，不进下一 task。**
> 关键纪律：**单 worker、先构建前端再启动、不动系统时区、跳过建 swap、`MemoryMax=2G`、用 `ubuntu` 用户。**

### Task 8: Phase 0 前置（域名 / DNS / 安全组）

- [ ] 注册域名；加 A 记录 → CVM 公网 IP（日本服务器，无需 ICP 备案）
- [ ] 腾讯云控制台安全组放行入站 22 / 80 / 443
- **验证门：** `nslookup <your-domain>` 解析到正确公网 IP；`ping`/`telnet <IP> 22` 可达

### Task 9: Phase 1 服务器初始化（`deployment.md` §5 Phase 1）

- [ ] 建目录 `/opt/market_monitor`（属主 ubuntu）；**跳过建 swap**（已有 5.9G）；**不改时区**
- [ ] 装 `python3-venv/build-essential/git/nginx/apache2-utils/certbot/python3-certbot-nginx` + Node 20
- **验证门：** `node -v`→v20.x；`nginx -v` 有版本；`python3 --version`≥3.10；`free -h` swap 仍在

### Task 10: Phase 2 代码 + venv + `.env`（本地数据后端）

- [ ] `git clone` 公开库到 `/opt/market_monitor`；建 venv，`pip install -r requirements.txt`
- [ ] 写 `.env`（`chmod 600`）：`REMOTE_BACKEND=local` + `REMOTE_DATA_ROOT=/home/ubuntu/data/firm/coin-realtime-data_v1.1.11/data/` + 各 API key；**不设** `REMOTE_HOST/PASSWORD`、不设 `PROXY_URL`
- [ ] `.venv/bin/python run.py setup`
- **验证门（关键，验本地数据后端真能读）：**

```bash
cd /opt/market_monitor
.venv/bin/python - <<'PY'
from services import remote_fs
print("backend =", remote_fs.REMOTE_BACKEND)            # 期望 local
r = remote_fs.find_latest_ready("preprocess_1h_resample/30m/", "market_pivot_spot")
print("latest ready =", r)                              # 期望 (xxx.ready, <ts>)，非 None
PY
```
Expected: `backend = local`，且打印出非 None 的 ready 文件。**若为 None，停下查 `REMOTE_DATA_ROOT` 路径/版本号。**

### Task 11: Phase 3 构建前端

- [ ] `cd frontend && npm install && npm run build`
- **验证门：** `ls frontend/dist/index.html` 存在；`ls frontend/dist/assets/` 非空

### Task 12: Phase 4 systemd（`deployment.md` §5 Phase 4）

- [ ] 写 `market-monitor.service`（`User=ubuntu`、单 worker、`ExecStartPre` 清锁、`MemoryMax=2G`）；`enable --now`
- **验证门：**
  - `systemctl is-active market-monitor` → `active`
  - `journalctl -u market-monitor -f`：看到 `background scheduler started`、~10s 后 `cmc_bootstrap`、首个 `remote_data_cycle finished`（含本地拉取 + sector_scan）
  - `curl -s http://127.0.0.1:8000/api/health | grep -q '"ok":true' && echo OK` → 打印 `OK`（响应体还含 timestamp 字段，故用包含匹配而非全等）
  - `ps -u ubuntu -o pid,rss,cmd | grep uvicorn`：**只有一个** uvicorn 进程

### Task 13: Phase 5 Nginx + Basic Auth + HTTPS（`deployment.md` §5 Phase 5）

- [ ] `htpasswd -c /etc/nginx/.htpasswd <user>`；写站点配置（`auth_basic` + `proxy_pass 127.0.0.1:8000` + `proxy_read_timeout 600s`）；`nginx -t && reload`
- [ ] `certbot --nginx -d <your-domain> --redirect`
- **验证门：**
  - `curl -I http://<your-domain>` → 301 跳 https
  - `curl -I https://<your-domain>` → 401（未带认证，Basic Auth 生效）
  - `curl -s -u <user>:<pass> https://<your-domain>/api/health | grep -q '"ok":true' && echo OK` → 打印 `OK`

### Task 14: Phase 6 防火墙

- [ ] `ufw allow OpenSSH/80/443`；`ufw --force enable`
- **验证门：** `ufw status` 含 80/443/22；从外网浏览器仍可访问（确认没把自己关在外面）

### Task 15: Phase 7 验收（端到端）

- [ ] 浏览器开 `https://<your-domain>` → Basic Auth 弹窗 → 仪表盘加载（资源不 404）
- [ ] 「告警设置」发企业微信测试 → 手机收到
- [ ] 等 1–2 个 5m 周期：市场/新闻数据增长；**板块轮动页有数据**（证明本地 BMAC 后端贯通）
- [ ] `journalctl -u market-monitor --since "10 min ago" | grep -Ei "error|exception"` 无异常堆栈
- **验证门：** 以上全过 = **从 0 到 1 公网访问达成**。

---

## 回滚 / 应急

- 数据后端有问题：`.env` 改回 `REMOTE_BACKEND=sftp` + 远程凭据即可退回原行为（代码默认 sftp，向后兼容）。
- 服务起不来：`journalctl -u market-monitor -n 100 --no-pager` 看栈；`.scan.lock` 残留由 `ExecStartPre` 自动清。
- 前端 404：确认「先 `npm run build` 再 `systemctl restart`」顺序（`/assets` 挂载在导入时决定）。
