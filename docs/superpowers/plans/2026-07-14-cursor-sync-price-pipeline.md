# 价格管道游标同步重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 价格采集与回补合并为一条"窗口拉取 + 幂等写入"路径，删除滚动追平/启动回补/gap_repair/小时 job 四套机制，新闻打标挂到 5min 扫描尾部。

**Architecture:** 每轮 `scan()` 对每个源算一个窗口 `max(now−CAP, min(cursor−30min, now−24h))`，调既有 `fetch_history` 拉取，`_save_records` 幂等入库（游标=数据库本身）。gap_filler（休市合成）与新闻/预测扫描本体不动。spec 见 `docs/superpowers/specs/2026-07-14-cursor-sync-price-pipeline-design.md`（v2.1 定稿）。

**Tech Stack:** Python/SQLAlchemy/SQLite、yfinance（curl_cffi 会话）、ccxt OKX raw API、APScheduler、pytest。

**约定：**
- 本机 python 一律用 `D:\anaconda\python.exe`（PATH 里的 python 是 stub，exit 49）。
- 测试命令 `D:\anaconda\python.exe -m pytest`（pytest.ini 已限定 tests/）。
- 每个 Task 一个 commit；commit message 结尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 任何一步全套测试变红且非预期 → 停下排查，不得跳过。

---

### Task 1: 窗口公式 `sync_window_start` + 游标查询 `_latest_by_symbol`

**Files:**
- Modify: `config.py`（价格扫描配置区，`SCAN_ROLLING_BACKFILL_INTERVALS` 附近）
- Modify: `scanners/price_scanner.py`（模块级新增两个函数 + import）
- Test: `tests/test_cursor_sync.py`（新建）

- [ ] **Step 1.1: 写失败测试**

新建 `tests/test_cursor_sync.py`：

```python
# -*- coding: utf-8 -*-
"""游标同步（2026-07-14 重构）：窗口公式、幂等写入返回、scan 单路径。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.price import PriceSnapshot
from scanners.price_scanner import sync_window_start, _latest_by_symbol

NOW = datetime(2026, 7, 14, 12, 0, 0)


@pytest.fixture()
def make_session():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


# ---------- 窗口公式三态 + 种子 ----------

def test_normal_floor_is_24h():
    latest = {"A": NOW - timedelta(minutes=5), "B": NOW - timedelta(minutes=10)}
    assert sync_window_start(latest, NOW, cap_hours=168) == NOW - timedelta(hours=24)


def test_downtime_stretches_to_cursor_minus_30min():
    latest = {"A": NOW - timedelta(hours=70), "B": NOW - timedelta(hours=69)}
    assert sync_window_start(latest, NOW, cap_hours=168) == NOW - timedelta(hours=70, minutes=30)


def test_cap_bounds_the_window():
    latest = {"A": NOW - timedelta(days=10)}
    assert sync_window_start(latest, NOW, cap_hours=72) == NOW - timedelta(hours=72)


def test_empty_symbol_seeds_full_cap():
    latest = {"A": NOW - timedelta(minutes=5), "B": None}
    assert sync_window_start(latest, NOW, cap_hours=72) == NOW - timedelta(hours=72)


# ---------- 游标查询 ----------

def test_latest_by_symbol_reads_max_ts_and_none_for_missing(make_session):
    s = make_session()
    for m in (10, 5):
        s.add(PriceSnapshot(timestamp=NOW - timedelta(minutes=m), asset_class="crypto",
                            symbol="BTC/USDT", name="BTC", price=100.0, source="okx_swap_5m"))
    s.commit()
    latest = _latest_by_symbol(s, ["BTC/USDT", "ETH/USDT"])
    assert latest["BTC/USDT"] == NOW - timedelta(minutes=5)
    assert latest["ETH/USDT"] is None
    s.close()
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py -v`
Expected: FAIL（ImportError: cannot import name 'sync_window_start'）

- [ ] **Step 1.3: 实现**

`config.py`——在 `SCAN_ROLLING_BACKFILL_INTERVALS`（约 :123）后新增：

```python
# 游标同步"至少回看"地板（小时，两源共用）：平时每轮固定回看 24h，晚到 ≤24h 的 bar 自动进库
# （原 gap_repair 的覆盖搬进主路径）；停机更久时窗口按库内游标自动拉长（见 sync_window_start）。
SYNC_MIN_LOOKBACK_HOURS = int(os.getenv("SYNC_MIN_LOOKBACK_HOURS", "24"))
```

`scanners/price_scanner.py`——顶部 import 增加 `from sqlalchemy import func`；类定义前新增模块级函数：

```python
def sync_window_start(latest_by_symbol: dict[str, datetime | None], now: datetime,
                      cap_hours: float) -> datetime:
    """游标同步窗口起点：max(now − CAP, min(cursor − 30min, now − 24h))。

    cursor = 各品种"库内最新 bar 时刻"的最小值（最落后品种决定，快的品种多拉部分幂等跳过）；
    任一品种库空 → 直接取 now − CAP（首轮种子拉满）。三种取值是同一条公式，不是三个机制。"""
    cap_start = now - timedelta(hours=float(cap_hours))
    cursors = list(latest_by_symbol.values())
    if not cursors or any(c is None for c in cursors):
        return cap_start
    floor_start = now - timedelta(hours=float(config.SYNC_MIN_LOOKBACK_HOURS))
    cursor_start = min(cursors) - timedelta(minutes=30)
    return max(cap_start, min(cursor_start, floor_start))


def _latest_by_symbol(session, symbols: list[str]) -> dict[str, datetime | None]:
    """各品种库内最新 bar 时刻；没有行的品种为 None。"""
    latest: dict[str, datetime | None] = {sym: None for sym in symbols}
    rows = (
        session.query(PriceSnapshot.symbol, func.max(PriceSnapshot.timestamp))
        .filter(PriceSnapshot.symbol.in_(symbols))
        .group_by(PriceSnapshot.symbol)
        .all()
    )
    latest.update({sym: ts for sym, ts in rows})
    return latest
```

- [ ] **Step 1.4: 跑测试确认通过**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py -v`
Expected: 5 passed

- [ ] **Step 1.5: Commit**

```
git add config.py scanners/price_scanner.py tests/test_cursor_sync.py
git commit -m "feat(sync): 游标同步窗口公式 sync_window_start + 游标查询（TDD）"
```

---

### Task 2: `_save_records` 返回本轮实际插入的记录列表

**Files:**
- Modify: `scanners/price_scanner.py:144-243`（`_save_records`）
- Test: `tests/test_cursor_sync.py`（追加）

- [ ] **Step 2.1: 写失败测试**（追加到 `tests/test_cursor_sync.py`）

```python
from scanners.base import PriceRecord
import scanners.price_scanner as ps_module
from scanners.price_scanner import PriceScanner


def _rec(ts, symbol="NQ=F", price=100.0, source="yfinance"):
    return PriceRecord(asset_class="futures", symbol=symbol, name="纳指期货",
                       price=price, source=source, timestamp=ts)


def test_save_records_returns_only_inserted(make_session, monkeypatch):
    monkeypatch.setattr(ps_module, "get_session", make_session)
    scanner = PriceScanner()
    t1, t2 = NOW - timedelta(minutes=10), NOW - timedelta(minutes=5)
    first = scanner._save_records([_rec(t1)], NOW)
    assert [r.timestamp for r in first] == [t1]
    second = scanner._save_records([_rec(t1), _rec(t2)], NOW)   # t1 已存在
    assert [r.timestamp for r in second] == [t2]
    third = scanner._save_records([_rec(t1), _rec(t2)], NOW)    # 全部已存在 → 幂等
    assert third == []
```

- [ ] **Step 2.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py::test_save_records_returns_only_inserted -v`
Expected: FAIL（返回 int 不可迭代 / 断言失败）

- [ ] **Step 2.3: 实现**——`_save_records` 内三处改动：

1. `inserted = 0`（:150）→ `inserted: list[PriceRecord] = []`
2. 真实覆盖合成分支（:206 附近）`inserted += 1` → `inserted.append(r)`
3. 插入分支（:234 附近）`inserted += 1` → `inserted.append(r)`
4. 异常分支（:241）`return 0` → `return []`
5. docstring 首行改为：`"""将价格记录写入数据库；重复 (symbol, timestamp) 跳过。返回本轮实际插入（含真实覆盖合成）的记录。"""`

调用方同步：`backfill_range` 里两处 `inserted_yfinance = self._save_records(...)` 的日志 `新增 {inserted_yfinance} 条` 改为 `新增 {len(inserted_yfinance)} 条`（该方法 Task 7 会删除，此处仅保持过渡期日志不崩）。

- [ ] **Step 2.4: 跑测试确认通过 + 回归**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py tests/test_save_records_overwrite.py -v`
Expected: 全部 passed（overwrite 测试只断言库状态，不受返回类型影响）

- [ ] **Step 2.5: Commit**

```
git add scanners/price_scanner.py tests/test_cursor_sync.py
git commit -m "feat(sync): _save_records 返回本轮实际插入的记录（告警/面板数字从此有意义）"
```

---

### Task 3: yfinance `fetch_history` 改精确窗口（start/end 取代 period="7d"）

**Files:**
- Modify: `scanners/sources/yfinance_source.py:20`（PERIOD → CAP_HOURS）、`:167-177`（fetch 的 download，本 Task 不动，Task 7 删）、`:232-242`（fetch_history 的 download）

- [ ] **Step 3.1: 实现**（机械改动，无独立单测——行为由 Task 4 的 scan 测试与部署后验收报告覆盖）

`:20` 常量替换：

```python
    # 同步窗口封顶（小时）：yahoo 5m 数据可得范围内留裕量；窗口起点由 PriceScanner 按游标公式计算。
    CAP_HOURS = 168
```

（`PERIOD = "7d"` 删除。`fetch()` 里引用 `self.PERIOD` 的 download 调用**本 Task 暂时同步改为 `period="7d"` 字面量**，Task 7 连同 `fetch()` 一起删除——保持中间态可运行。）

`fetch_history`（:232-242）download 调用改为精确窗口（yfinance 对 naive datetime 按本地时区解释，必须传 tz-aware UTC）：

```python
            df = yf.download(
                ticker_list,
                start=start_ts.replace(tzinfo=timezone.utc),
                end=end_ts.replace(tzinfo=timezone.utc),
                interval=self.INTERVAL,
                prepost=False,
                auto_adjust=True,
                progress=False,
                threads=True,
                session=self._session,
            )
```

- [ ] **Step 3.2: 全套回归**

Run: `D:\anaconda\python.exe -m pytest -q`
Expected: 全绿（现有测试不打真实 yahoo；若有 mock yf.download 的测试因参数断言失败，按新参数更新断言）

- [ ] **Step 3.3: Commit**

```
git add scanners/sources/yfinance_source.py
git commit -m "feat(sync): yfinance fetch_history 精确窗口（tz-aware start/end），解析量降为 7d 方案的 1/5"
```

---

### Task 4: `PriceScanner.scan()` 重写为游标同步单路径

**Files:**
- Modify: `scanners/price_scanner.py:27-64`（scan）、`:131-142`（_fetch_safe 保留给 CNBC）、新增 `_fetch_history_safe`
- Test: `tests/test_cursor_sync.py`（追加）

- [ ] **Step 4.1: 写失败测试**（追加）

```python
import config


class FakeHistorySource:
    """可编程的 fetch_history 源：记录被调用的窗口，按轮次返回预设记录。"""
    def __init__(self, name, rounds):
        self.name = name
        self.rounds = list(rounds)      # 每轮返回的 list[PriceRecord]
        self.calls = []                 # [(start, end)]

    def fetch_history(self, start_ts, end_ts):
        self.calls.append((start_ts, end_ts))
        return self.rounds.pop(0) if self.rounds else []


class FakeQuoteSource:
    name = "cnbc_bond_quote"
    def fetch(self):
        return []


class NoopGapFiller:
    def run(self, session, okx_source, scan_time):
        return 0


def _make_scanner(make_session, monkeypatch, yf_rounds, okx_rounds):
    monkeypatch.setattr(ps_module, "get_session", make_session)
    scanner = PriceScanner()
    yf = FakeHistorySource("yfinance", yf_rounds)
    yf._all_tickers = lambda: {"NQ=F": ("futures", "纳指期货")}
    yf.CAP_HOURS = 168
    scanner.yfinance = yf
    scanner.okx = FakeHistorySource("okx", okx_rounds)
    scanner.cnbc_bonds = FakeQuoteSource()
    scanner.gap_filler = NoopGapFiller()
    monkeypatch.setattr(config, "PRICE_SOURCES",
                        {**config.PRICE_SOURCES, "crypto": {"BTC": "BTCUSDT"}})
    return scanner


def test_scan_empty_db_seeds_cap_window(make_session, monkeypatch):
    scanner = _make_scanner(make_session, monkeypatch, [[]], [[]])
    scanner.scan()
    (yf_start, yf_end), = scanner.yfinance.calls
    (okx_start, okx_end), = scanner.okx.calls
    assert (yf_end - yf_start) == timedelta(hours=168)      # 库空 → 种子拉满 CAP
    assert (okx_end - okx_start) == timedelta(hours=int(config.PRICE_BACKFILL_MAX_HOURS))


def test_scan_normal_uses_24h_floor_and_returns_inserted(make_session, monkeypatch):
    t_old = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=10)
    t_new = t_old + timedelta(minutes=5)
    scanner = _make_scanner(
        make_session, monkeypatch,
        yf_rounds=[[_rec(t_old)], [_rec(t_old), _rec(t_new)]],
        okx_rounds=[[], []],
    )
    first = scanner.scan()
    assert [r.timestamp for r in first] == [t_old]
    second = scanner.scan()                                  # t_old 已在库 → 只插 t_new
    assert [r.timestamp for r in second] == [t_new]
    yf_start2, yf_end2 = scanner.yfinance.calls[1]
    assert (yf_end2 - yf_start2) == timedelta(hours=24)      # 游标新鲜 → 24h 地板


def test_scan_heals_mid_window_hole(make_session, monkeypatch):
    base = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(hours=1)
    t1, t2, t3 = base, base + timedelta(minutes=5), base + timedelta(minutes=10)
    scanner = _make_scanner(
        make_session, monkeypatch,
        yf_rounds=[[_rec(t1), _rec(t3)], [_rec(t1), _rec(t2), _rec(t3)]],   # 第一轮源端缺 t2
        okx_rounds=[[], []],
    )
    scanner.scan()
    healed = scanner.scan()                                  # 第二轮源补全 → 洞被填
    assert [r.timestamp for r in healed] == [t2]
```

- [ ] **Step 4.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py -v -k scan`
Expected: FAIL（scan 仍走旧 fetch() 路径，FakeHistorySource 无 fetch 方法 → AttributeError）

- [ ] **Step 4.3: 实现**——`scan()` 整体替换 + 新增 `_fetch_history_safe`：

```python
    def scan(self) -> list[PriceRecord]:
        """执行一次同步扫描：每源一个窗口，拉取 → 幂等写入（采集=回补，无第二条写路径）。

        返回**本轮实际插入**的记录（含真实覆盖合成点）。追平轮会带出历史 bar，
        告警侧有 staleness 保护（alerts/evaluators/price.py）自动跳过旧 bar。"""
        inserted: list[PriceRecord] = []
        scan_time = datetime.now(timezone.utc).replace(tzinfo=None)
        self._reset_source_statuses()

        session = get_session()
        try:
            yf_latest = _latest_by_symbol(session, list(self.yfinance._all_tickers()))
            okx_latest = _latest_by_symbol(
                session, [f"{base}/USDT" for base in config.PRICE_SOURCES.get("crypto", {})]
            )
        finally:
            session.close()
        yf_start = sync_window_start(yf_latest, scan_time, cap_hours=self.yfinance.CAP_HOURS)
        okx_start = sync_window_start(okx_latest, scan_time,
                                      cap_hours=float(config.PRICE_BACKFILL_MAX_HOURS))

        # 1. yfinance: 股指、期货、亚洲指数、商品（至少回看 24h，停机自动拉长，封顶 7d）
        inserted.extend(self._save_records(
            self._fetch_history_safe(self.yfinance, yf_start, scan_time), scan_time))

        # 2. OKX 加密货币：同一条窗口公式（封顶 72h；正常 24h 单页一次调用）
        okx_records = self._fetch_history_safe(self.okx, okx_start, scan_time)
        expected_crypto = set(config.PRICE_SOURCES.get("crypto", {}).keys())
        missing_crypto = sorted(expected_crypto - {r.name for r in okx_records})
        if missing_crypto:
            logger.warning(f"[PriceScanner] OKX 本轮未返回 {missing_crypto}，等待下一轮窗口自愈")
        inserted.extend(self._save_records(okx_records, scan_time))

        # 3. CNBC: 美债/日债收益率（仅当前报价口径，无历史，不参与缺口语义）
        inserted.extend(self._save_records(self._fetch_safe(self.cnbc_bonds), scan_time))

        # 休市补点：真实写库后，用 OKX 永续补休市空档（独立 session，失败不影响扫描结果）
        try:
            gap_session = get_session()
            try:
                n = self.gap_filler.run(gap_session, self.okx, scan_time)
                if n:
                    logger.info(f"[PriceScanner] gap-fill 写出 {n} 条休市代理点")
            finally:
                gap_session.close()
        except Exception as e:
            logger.error(f"[PriceScanner] gap-fill 失败: {type(e).__name__}: {e}")

        logger.info(f"[PriceScanner] 扫描完成，新插入 {len(inserted)} 条价格记录")
        return inserted

    def _fetch_history_safe(self, source, start_ts: datetime, end_ts: datetime) -> list[PriceRecord]:
        """安全调用数据源区间拉取，捕获异常并记录源健康状态。"""
        try:
            logger.info(f"[PriceScanner] 同步 {source.name} "
                        f"{start_ts.isoformat()} → {end_ts.isoformat()} UTC...")
            records = source.fetch_history(start_ts, end_ts)
            self._record_source_status(source.name, records, stage="scan")
            logger.info(f"[PriceScanner] {source.name} 返回 {len(records)} 条记录")
            return records
        except Exception as e:
            self._record_source_error(source.name, e, stage="scan")
            logger.error(f"[PriceScanner] {source.name} 同步失败: {e}")
            return []
```

- [ ] **Step 4.4: 跑测试确认通过 + 全套回归**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py -v` 然后 `D:\anaconda\python.exe -m pytest -q`
Expected: cursor_sync 全 passed；全套绿（若 `test_api`/`test_gap_filler` 有对 scan 旧行为的依赖，按"返回=插入记录"新口径更新断言）

- [ ] **Step 4.5: Commit**

```
git add scanners/price_scanner.py tests/test_cursor_sync.py
git commit -m "feat(sync): scan() 重写为游标同步单路径——窗口拉取+幂等写入，采集即回补"
```

---

### Task 5: scan_runtime 手术——滚动回填只剩新闻、启动回补只剩新闻、打标挂尾

**Files:**
- Modify: `services/scan_runtime.py:211-214`（调用点）、`:258-278`（_run_rolling_backfill）、`:281-296`（run_price_backfill_once 删）、`:315-348`（startup）
- Modify: `run.py:25-33`（死 import 清理）
- Modify: `config.py:123`（SCAN_ROLLING_BACKFILL_INTERVALS 删除）
- Test: `tests/test_cursor_sync.py`（追加打标挂尾用例）

- [ ] **Step 5.1: 写失败测试**（追加）

```python
from services import scan_runtime


def test_tag_new_news_skips_without_key(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "", raising=False)
    called = []
    monkeypatch.setattr("services.news_tagging.tag_untagged",
                        lambda session, limit: called.append(limit) or 0)
    scan_runtime._tag_new_news()
    assert called == []                          # 无 key 静默跳过


def test_tag_new_news_invokes_tagger_with_limit(monkeypatch, make_session):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr("services.scan_runtime.get_session", make_session, raising=False)
    called = []
    monkeypatch.setattr("services.news_tagging.tag_untagged",
                        lambda session, limit: called.append(limit) or 3)
    scan_runtime._tag_new_news()
    assert called == [200]


def test_tag_new_news_error_does_not_raise(monkeypatch, make_session):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr("services.scan_runtime.get_session", make_session, raising=False)
    def boom(session, limit):
        raise RuntimeError("api down")
    monkeypatch.setattr("services.news_tagging.tag_untagged", boom)
    scan_runtime._tag_new_news()                 # 不应抛出
```

- [ ] **Step 5.2: 跑测试确认失败**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py -v -k tag`
Expected: FAIL（AttributeError: no attribute '_tag_new_news'）

- [ ] **Step 5.3: 实现**

`services/scan_runtime.py`：

(a) `_run_rolling_backfill`（:258-278）整体替换为**新闻专用**（价格部分退役——scan 窗口自愈；`import config` 与 `recent_closed_interval_window` 已在文件内）：

```python
def _run_news_rolling_backfill(news_scanner, now: datetime):
    """每轮扫描后补最近 2 个已收盘新闻 interval（价格侧已由游标同步窗口自愈，2026-07-14 重构）。"""
    news_interval = max(1, int(config.SCAN_INTERVALS.get("news", 5)))
    news_start, news_end = recent_closed_interval_window(news_interval, 2, now)
    logger.info(
        f"[ScanCatchup] 回补最近 2 个新闻 interval: "
        f"{news_start.isoformat()} - {news_end.isoformat()} UTC"
    )
    news_scanner.backfill_range(news_start, news_end, score_records=False)
```

(b) 新增 `_tag_new_news`（放在 `_run_news_rolling_backfill` 之后）：

```python
def _tag_new_news() -> None:
    """给未打标新闻打内容标签（游标语义：tagged_at IS NULL 即待办；原每小时 job 收编，2026-07-14）。
    无 DEEPSEEK_API_KEY 静默跳过；异常自吞不影响扫描。"""
    if not getattr(config, "DEEPSEEK_API_KEY", ""):
        return
    from database import get_session
    from services.news_tagging import tag_untagged
    session = get_session()
    try:
        tagged = tag_untagged(session, limit=200)
        if tagged:
            logger.info(f"[NewsTagging] 本轮打标 {tagged} 条")
    except Exception as exc:
        logger.exception(f"[NewsTagging] 打标失败，不影响本轮扫描: {exc}")
    finally:
        session.close()
```

注意：`_tag_new_news` 需要模块级可 monkeypatch 的 `get_session`——把 `from database import get_session` 提到**模块顶部** import 区（scan_runtime 现无此顶级 import），函数内直接用 `get_session()`。

(c) `run_scan_once` 调用点（:211-214）替换：

```python
        try:
            _run_news_rolling_backfill(news_scanner, scan_started_at)
        except Exception as exc:
            logger.exception(f"[ScanCatchup] news rolling backfill failed, continuing scan: {exc}")

        _tag_new_news()
```

(d) `run_price_backfill_once`（:281-296）**整个删除**。

(e) `run_startup_backfill_once`（:315-348）整体替换（保持 `([], news)` 返回形状，`api/app.py:70` 调用方零改动）：

```python
def run_startup_backfill_once():
    """启动后回补停机期间缺失的新闻；价格由常规扫描的游标同步窗口自愈（2026-07-14 重构）。"""
    configure_proxy_env()
    with _scan_lock() as acquired:
        if not acquired:
            return [], []

        from database import create_tables
        create_tables()

        from scanners.news_scanner import NewsScanner
        news_records = NewsScanner().backfill_missing_history()
        return [], news_records
```

（原函数里 `PriceScanner` import、价格回补与耗时追加段全部删除；`datetime`/`timezone` 若仅此处使用则保留——文件其他函数仍用。）

`run.py:29-30`：import 列表删除 `run_news_backfill_once`、`run_price_backfill_once` 两行（全仓无调用方的死 import；`run_news_backfill_once` 函数本体保留在 scan_runtime——新闻侧手动工具，不在本案范围）。

`config.py:123`：删除 `SCAN_ROLLING_BACKFILL_INTERVALS`（新函数固定 2 个 interval，唯一消费方已改写）。

- [ ] **Step 5.4: 跑测试确认通过 + 全套回归**

Run: `D:\anaconda\python.exe -m pytest tests/test_cursor_sync.py -v -k tag` 然后 `D:\anaconda\python.exe -m pytest -q`
Expected: 3 个 tag 用例 passed；全套绿（`test_scan_windows` 不引用滚动回填，已核实）

- [ ] **Step 5.5: Commit**

```
git add services/scan_runtime.py run.py config.py tests/test_cursor_sync.py
git commit -m "feat(sync): 滚动回填只剩新闻、启动回补只剩新闻、LLM 打标挂 5min 扫描尾部"
```

---

### Task 6: 删除小时 job 与 gap_repair

**Files:**
- Modify: `api/app.py:99-125`（gap_repair_cycle 函数）、`:233-240`（add_job 块）
- Delete: `services/gap_repair.py`、`tests/test_gap_repair.py`
- Modify: `config.py`（GAP_REPAIR_LOOKBACK_HOURS 删除）

- [ ] **Step 6.1: 删除**

1. `api/app.py`：删 `gap_repair_cycle` 整个函数（:99-125，含 docstring）；删对应 `scheduler.add_job(gap_repair_cycle, CronTrigger(minute=37), id="gap_repair", ...)` 块（:233-240 附近，以实际行为准）。`CronTrigger` import 若仍被其他 job（data_retention :241、behavior_daily :259、cmc_refresh :276）使用则保留。
2. `git rm services/gap_repair.py tests/test_gap_repair.py`
3. `config.py`：删除 `GAP_REPAIR_LOOKBACK_HOURS` 定义行及其注释。

- [ ] **Step 6.2: 引用清零验证**

Run: `D:\anaconda\python.exe -m pytest -q` 并 grep 验证：
`rg -n "gap_repair|GAP_REPAIR|find_gaps|repair_symbols" --type py`
Expected: pytest 全绿；grep 仅剩（若有）`scripts/` 内历史脚本注释或零匹配——`scripts/backfill_ledger.py` 用的是 `news_tagging.backfill_traditional_open`，与 gap_repair 无关。

- [ ] **Step 6.3: Commit**

```
git add -A
git commit -m "feat(sync): 删除小时 gap_repair job 与 services/gap_repair.py——洞由主路径窗口自愈，验证走部署后线上报告"
```

---

### Task 7: 孤儿清理——两源 live fetch、backfill_* 方法、base 去抽象

**Files:**
- Modify: `scanners/sources/yfinance_source.py`（删 fetch :159-215、_pick_last_closed :79-96）
- Modify: `scanners/sources/okx_source.py`（删 fetch :271-286、_fetch_one :140-168、_pick_last_closed :121-138）
- Modify: `scanners/price_scanner.py`（删 backfill_missing_history :66-94、backfill_range :96-129）
- Modify: `scanners/base.py:104-107`（fetch 去 @abstractmethod）

- [ ] **Step 7.1: base.py 先去抽象**（否则删子类 fetch 后无法实例化）：

```python
    def fetch(self) -> list:
        """获取"当前"口径数据。仅剩即时报价类源（CNBC 债券）实现；
        K 线类源走 fetch_history 区间语义（游标同步重构 2026-07-14）。"""
        raise NotImplementedError(f"{self.name} 不支持即时 fetch，请用 fetch_history")
```

（删除 `@abstractmethod` 装饰器；`from abc import ABC, abstractmethod` 中 `abstractmethod` 若再无使用则改为 `from abc import ABC`。）

- [ ] **Step 7.2: 删四处孤儿**

- yfinance_source：`fetch()` 整个方法、`_pick_last_closed()` 整个方法（`_iter_closed_bars` 仍被 `_records_from_close_series` 使用，保留）。
- okx_source：`fetch()`、`_fetch_one()`、`_pick_last_closed()` 三个方法（`_closed_candle_points`/`_make_record`/`fetch_instrument_bars`/history 族保留——gapfill 与同步路径依赖）。
- price_scanner：`backfill_missing_history()`、`backfill_range()` 两个方法。

- [ ] **Step 7.3: 引用清零验证 + 全套回归**

`rg -n "backfill_range|backfill_missing_history|_pick_last_closed|_fetch_one\b" --type py`
Expected: 仅 news_scanner 的同名新闻方法（`news_scanner.backfill_range` 是新闻侧，保留）；价格侧零匹配。

Run: `D:\anaconda\python.exe -m pytest -q`
Expected: 全绿。

- [ ] **Step 7.4: Commit**

```
git add -A
git commit -m "refactor(sync): 清理孤儿——两源 live fetch/_pick_last_closed、price backfill_*，base.fetch 去抽象"
```

---

### Task 8: 文档四件套同步 + 验收待办 + 收尾

**Files:**
- Modify: `ARCHITECTURE.md`、`DATAFLOW.md`、`DECISIONS.md`、`PENDING.md`

- [ ] **Step 8.1: 文档同步**（AGENTS.md 契约：结构性变更同 commit 更新地图）

- `ARCHITECTURE.md` / `DATAFLOW.md`：价格管道段落改为"游标同步单路径"（窗口公式一行 + 无回补机制 + gap_filler 不变 + 打标在 5min 扫描尾部）；删除滚动回填/gap_repair/启动价格回补的所有提及；小时 job 从调度器清单移除。
- `DECISIONS.md` 顶部新增条目：

```markdown
## 2026-07-14 - 价格管道游标同步：采集=回补单路径，四层补 bar 机制与小时 job 退役

- 背景：yfinance.fetch() 每 5min 已下载 7 天全量 5m K 线却只留最后一根，滚动追平/每小时 gap_repair/启动回补
  三层机制一直在取回主路径丢弃的数据；四层交互复杂度是"48h 段重算"issue 的根源。yfinance 间歇限频的
  主因也是"每轮下两遍"（~9,600 请求/天）。
- 决策：scan() 每轮对每源算一个窗口 max(now−CAP, min(cursor−30min, now−24h))（cursor=库内最落后品种最新 bar；
  yf CAP=7d、OKX CAP=72h），调 fetch_history 幂等入库——游标即数据库本身。gap_repair 连监控都不留
  （用户拍板），验证=部署 3-7 天后线上只读缺口报告；小时 job 整个删除：traditional_open 入库即设
  （部署时一次性清存量 NULL），LLM 打标 tag_untagged（本身就是 tagged_at IS NULL 游标语义）挂到
  5min 新闻扫描尾部。请求量 ~9,600→~4,600/天。
- 拒绝的备选：保留小时缺口监控 job（休市缺口造成推送噪音，且主路径自愈后"源有库缺"趋近不可能）；
  yfinance 继续 period=7d（解析/查重 5 倍白干）；逐洞重算作用域（对账幂等已是护栏，包络+幂等结果相同）。
- 影响：调度器无任何"回头补数据"的 job；spec `docs/superpowers/specs/2026-07-14-cursor-sync-price-pipeline-design.md`；
  告警评估收到的 price_records 变为"本轮插入"，staleness 保护挡追平轮历史 bar；
  段重算脚本维持存档（补写事件仅剩长停机一种来源）。
```

- `PENDING.md`：快照区更新价格管道描述；A 级新增已完成条目（游标同步重构，**服务器待 `git pull && ./deploy.sh` + 部署后一次性 `backfill_traditional_open`**）；新增待办：

```markdown
- [ ] **游标同步验收报告**（部署后 3-7 天，≈2026-07-17~21）：线上只读查询——①各品种内部缺口清单
  （相邻 bar 间隔>7.5min）②晚到统计（created_at−timestamp>30min）③判定缺口全为休市形状即验收通过。
  查询方式见 memory remote-data-access；通过后关闭本案。
```

- [ ] **Step 8.2: 全套测试 + 前端类型检查跳过说明**

Run: `D:\anaconda\python.exe -m pytest -q`
Expected: 全绿。（本案不改 API schema，无需 `npm run typecheck`。）

- [ ] **Step 8.3: Commit**

```
git add ARCHITECTURE.md DATAFLOW.md DECISIONS.md PENDING.md
git commit -m "docs: 游标同步重构落地——四件套地图同步 + 验收报告待办"
```

- [ ] **Step 8.4: 部署提示**（人工/另行确认，不在本机执行）

服务器：`git pull && ./deploy.sh`，然后一次性清存量 NULL：
`/opt/market_monitor/.venv/bin/python -c "from database import get_session; from services.news_tagging import backfill_traditional_open; s=get_session(); print(backfill_traditional_open(s)); s.close()"`
记录部署时刻；3-7 天后出验收报告（PENDING 已挂）。

---

## Self-Review 记录

- **Spec 覆盖**：§3.1 窗口公式→Task 1/4；返回口径→Task 2/4；yfinance 精确窗口→Task 3；§3.2 报告→Task 8 待办；§3.3 退役清单逐行→Task 5（滚动/启动/run_price_backfill_once/SCAN_ROLLING）、Task 6（gap_repair/job/GAP_REPAIR_LOOKBACK）、Task 7（fetch/_pick_last_closed/_fetch_one/backfill_*/base）；打标挂尾→Task 5；§5 测试清单→Task 1/2/4/5 的用例一一对应；§6 文件清单全覆盖。
- **占位符**：无 TBD/伪码；所有代码块可直接粘贴。
- **类型一致性**：`sync_window_start(latest_by_symbol, now, cap_hours)` 三处调用签名一致；`_save_records` 返回 `list[PriceRecord]` 在 Task 2 定义、Task 4 消费；`CAP_HOURS` 挂 `YFinancePriceSource`，Task 4 经 `self.yfinance.CAP_HOURS` 引用，Fake 源同名属性对齐。
