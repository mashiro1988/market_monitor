# 行为面板日聚合口径 UTC 日 → 北京日 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把行为面板的日聚合桶从 UTC 日（北京 08:00 日结）改成北京日（北京 00:00 日结），并让已存档的旧口径 PIT 行与新口径物理隔离、互不误读。

**Architecture:** 日界换算集中到 `services/time_utils.py` 两个纯函数（`bj_date_of` / `bj_day_bounds`），聚合层只换边界不动统计逻辑。存档表 `behavior_daily_summaries` 把 `utc_date` 改名 `bucket_date` 并新增 `date_basis`（`'utc'`/`'bj'`），读路径只认 `'bj'` 行；一次性回算脚本补齐最近 14 个北京日的 `bj` 行。

**Tech Stack:** Python 3.11 / SQLAlchemy / SQLite 3.41 / FastAPI / APScheduler / pytest；前端 React + TypeScript + vitest，`types.ts` 由 `scripts/generate_openapi_types.py` 自动生成。

**Spec:** `docs/superpowers/specs/2026-07-29-behavior-panel-beijing-day-design.md`

**本机命令前缀：** 本项目 `python` 在 PATH 上是会 exit 49 的桩，**必须**用 `D:/anaconda/python.exe`。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `services/time_utils.py` | 改 | 新增 `bj_date_of` / `bj_day_bounds`——全项目唯一的北京日界换算点 |
| `services/behavior_classifier.py` | 改 | 聚合分桶改北京日；写入带 `date_basis='bj'`；日报目标日抽成可测函数 |
| `models/behavior.py` | 改 | `utc_date` → `bucket_date`，新增 `date_basis`，唯一索引改四列 |
| `database.py` | 改 | 新增 `migrate_behavior_daily_basis(conn)` 独立迁移函数，并在 `_ensure_sqlite_schema` 里调用 |
| `services/behavior_views.py` | 改 | 日期序列改北京日；查存档行加 `date_basis='bj'` 过滤；输出字段改 `bj_date` |
| `schemas/behavior.py` | 改 | `BehaviorDailySchema.utc_date` → `bj_date` |
| `api/app.py` | 改 | 日报 cron `hour=0` → `hour=16`（UTC）= 北京 00:05 |
| `scripts/backfill_behavior_bj_daily.py` | 建 | 一次性回算最近 N 个已结束北京日的 `bj` 行，幂等，默认 dry-run |
| `tests/test_bj_day_bucket.py` | 建 | 日界换算 + 跨界归属 + 口径隔离 + 日报目标日 |
| `tests/test_behavior_daily_migration.py` | 建 | 旧结构表迁移正确性 + 幂等 |
| `tests/test_backfill_behavior_bj_daily.py` | 建 | 回算脚本幂等 + dry-run 不写 |
| `tests/test_behavior_models.py` | 改 | PIT 追加用例改新列名 |
| `tests/test_behavior_classifier.py` | 改 | 日汇总用例改新列名 |
| `tests/test_behavior_api.py` | 改 | 端点用例改 `bj_date` |
| `frontend/src/api/types.ts` | 生成 | 跑 `npm run generate:api-types`，**不手改** |
| `frontend/src/pages/behaviorFormat.ts` | 改 | `d.utc_date` → `d.bj_date` |
| `frontend/src/pages/behaviorFormat.test.ts` | 改 | 测试夹具字段名 |
| `frontend/src/pages/BehaviorPage.tsx` | 改 | 标题「近 14 个 UTC 日」→「近 14 个北京日」 |
| `GLOSSARY.md` | 改 | 追加「日界 / bucket boundary」「PIT 表」「迁移」三词 |

---

### Task 1: 北京日界换算函数

**Files:**
- Modify: `services/time_utils.py`
- Test: `tests/test_bj_day_bucket.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_bj_day_bucket.py`：

```python
# -*- coding: utf-8 -*-
"""北京日界（2026-07-29 口径切换）：换算函数 + 跨界归属 + 口径隔离 + 日报目标日。"""
from datetime import datetime, timedelta

from services.time_utils import bj_date_of, bj_day_bounds


def test_bj_date_of_maps_utc_instant_to_beijing_day():
    # 北京 = UTC+8：UTC 15:59 还是当天，UTC 16:00 就翻到次日
    assert bj_date_of(datetime(2026, 7, 28, 15, 59)) == "2026-07-28"
    assert bj_date_of(datetime(2026, 7, 28, 16, 0)) == "2026-07-29"
    assert bj_date_of(datetime(2026, 7, 28, 0, 0)) == "2026-07-28"
    assert bj_date_of(None) is None


def test_bj_day_bounds_spans_utc_16_to_16():
    start, end = bj_day_bounds("2026-07-29")
    assert start == datetime(2026, 7, 28, 16, 0)
    assert end == datetime(2026, 7, 29, 16, 0)


def test_bounds_and_date_of_are_consistent():
    start, end = bj_day_bounds("2026-07-29")
    assert bj_date_of(start) == "2026-07-29"                      # 左闭
    assert bj_date_of(end - timedelta(seconds=1)) == "2026-07-29"
    assert bj_date_of(end) == "2026-07-30"                        # 右开
```

- [ ] **Step 2: 跑测试确认失败**

```bash
D:/anaconda/python.exe -m pytest tests/test_bj_day_bucket.py -v
```

Expected: FAIL — `ImportError: cannot import name 'bj_date_of' from 'services.time_utils'`

- [ ] **Step 3: 实现**

在 `services/time_utils.py` 末尾（`parse_datetime` 之后）追加：

```python
def bj_date_of(value: datetime | None) -> str | None:
    """naive UTC 时刻 → 它属于哪个北京日 'YYYY-MM-DD'。"""
    bj = to_bj_naive(value)
    return bj.strftime("%Y-%m-%d") if bj else None


def bj_day_bounds(bj_date: str) -> tuple[datetime, datetime]:
    """北京日 'YYYY-MM-DD' → [start, end) 的 naive UTC 边界。
    北京日 D 的 00:00 = UTC (D-1) 16:00；中国无夏令时，固定 +8 偏移成立。"""
    start = datetime.strptime(bj_date, "%Y-%m-%d") - BJ_OFFSET
    return start, start + timedelta(days=1)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
D:/anaconda/python.exe -m pytest tests/test_bj_day_bucket.py -v
```

Expected: PASS，3 passed

- [ ] **Step 5: 提交**

```bash
git add services/time_utils.py tests/test_bj_day_bucket.py
git commit -m "feat(time): add Beijing-day boundary helpers"
```

---

### Task 2: 聚合层改北京日分桶

**Files:**
- Modify: `services/behavior_classifier.py:208-234`（`aggregate_day`）、`:237-280`（`day_direction_extras`）、`:283-284`（`day_type_of`）
- Test: `tests/test_bj_day_bucket.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_bj_day_bucket.py` 顶部 import 段补上：

```python
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.behavior import BehaviorSegment
from services import behavior_classifier as bc
```

并在文件末尾追加：

```python
@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seg(start: datetime, direction: int = 1, tier_idx: int = 1, tier_max: float = 0.5,
         net_pct: float = 0.6, classification: str = "pure_resonance") -> BehaviorSegment:
    return BehaviorSegment(
        symbol="BTC/USDT", start_dt=start, end_dt=start + timedelta(minutes=15),
        direction=direction, tier_idx=tier_idx, tier_max=tier_max,
        net_pct=net_pct, amp_pct=abs(net_pct) + 0.1, key_ts=start + timedelta(minutes=5),
        classification=classification, class_version="v2",
        s_scores=json.dumps({"NQ=F": {"s": 0.7, "ess": 4.0, "coverage": 1.0}}),
        news_ids=json.dumps([]),
    )


def test_segments_split_across_utc_16_land_in_adjacent_beijing_days(session):
    """UTC 15:59 与 16:01 的两个段必须落进相邻两个北京日。"""
    session.add(_seg(datetime(2026, 7, 28, 15, 59)))       # 北京 07-28 23:59
    session.add(_seg(datetime(2026, 7, 28, 16, 1)))        # 北京 07-29 00:01
    session.commit()

    counts_28, _, _ = bc.aggregate_day(session, "BTC/USDT", "2026-07-28")
    counts_29, _, _ = bc.aggregate_day(session, "BTC/USDT", "2026-07-29")
    assert counts_28["0.5"] == {"up": 1, "down": 0}
    assert counts_29["0.5"] == {"up": 1, "down": 0}


def test_day_direction_extras_uses_beijing_bounds(session):
    session.add(_seg(datetime(2026, 7, 28, 15, 59), net_pct=0.6))
    session.add(_seg(datetime(2026, 7, 28, 16, 1), net_pct=0.9))
    session.commit()

    assert bc.day_direction_extras(session, "BTC/USDT", "2026-07-28")["up_net_sum"] == 0.6
    assert bc.day_direction_extras(session, "BTC/USDT", "2026-07-29")["up_net_sum"] == 0.9


def test_day_type_follows_beijing_date(session):
    # 2026-07-25 是周六；UTC 日口径下 07-24 16:30 属于 UTC 周五，北京口径属于周六
    assert bc.day_type_of("2026-07-25") == "weekend"
    assert bc.day_type_of("2026-07-24") == "weekday"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
D:/anaconda/python.exe -m pytest tests/test_bj_day_bucket.py -v -k "adjacent or extras"
```

Expected: FAIL —两个段都被算进 `2026-07-28`（旧的 UTC 分桶），`counts_29` 里没有 `"0.5"` 键，报 `KeyError: '0.5'`

- [ ] **Step 3: 实现**

`services/behavior_classifier.py` 顶部 import 区（第 24 行 `from services.resonance_score import ...` 之后）加一行：

```python
from services.time_utils import bj_date_of, bj_day_bounds
```

把 `aggregate_day` 的签名与取数改成（**只改日期边界，循环体一个字不动**）：

```python
def aggregate_day(session: Session, symbol: str, bj_date: str) -> tuple[dict, dict, float]:
    """按段的 start_dt 归**北京日**聚合 → (counts, composition, down_net_sum)。
    PIT 写入与当日盘中 live 读数（behavior_views）共用同一口径。"""
    day_start, day_end = bj_day_bounds(bj_date)
    rows = (
        session.query(BehaviorSegment)
        .filter(BehaviorSegment.symbol == symbol,
                BehaviorSegment.start_dt >= day_start,
                BehaviorSegment.start_dt < day_end)
        .all()
    )
```

把 `day_direction_extras` 同样改（同样只改这三行）：

```python
def day_direction_extras(session: Session, symbol: str, bj_date: str) -> dict:
    """方向拆分读数（2026-07-10 行为面板重画）：涨段净幅合计 + 情绪·技术面段的
    涨/跌个数与净幅。**compute-on-read**、不进 PIT——净幅只依赖段原始数据（settle 后不变），
    情绪归属按"人工优先"的当前结论（人工改判要立刻反映到趋势图，冻结旧结论反而误导）。"""
    day_start, day_end = bj_day_bounds(bj_date)
    rows = (
        session.query(BehaviorSegment)
        .filter(BehaviorSegment.symbol == symbol,
                BehaviorSegment.start_dt >= day_start,
                BehaviorSegment.start_dt < day_end)
        .all()
    )
```

`day_type_of` 只改形参名与 docstring，函数体不变：

```python
def day_type_of(bj_date: str) -> str:
    """北京日 'YYYY-MM-DD' → weekday / weekend（分桶互比用）。"""
    return "weekend" if datetime.strptime(bj_date, "%Y-%m-%d").weekday() >= 5 else "weekday"
```

- [ ] **Step 4: 跑测试确认通过**

```bash
D:/anaconda/python.exe -m pytest tests/test_bj_day_bucket.py -v
```

Expected: PASS，6 passed

- [ ] **Step 5: 提交**

```bash
git add services/behavior_classifier.py tests/test_bj_day_bucket.py
git commit -m "feat(behavior): bucket daily aggregates by Beijing day"
```

---

### Task 3: 存档表列改名 + 口径标记列 + 迁移

**Files:**
- Modify: `models/behavior.py:6`（模块 docstring）、`:49-63`（`BehaviorDailySummary`）
- Modify: `database.py`（新增 `migrate_behavior_daily_basis`，并在 `_ensure_sqlite_schema` 调用）
- Test: `tests/test_behavior_daily_migration.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_behavior_daily_migration.py`：

```python
# -*- coding: utf-8 -*-
"""behavior_daily_summaries 北京日口径迁移（2026-07-29）：改名 + 加口径列 + 重建索引 + 幂等。"""
import pytest
from sqlalchemy import create_engine, inspect, text


@pytest.fixture()
def legacy_engine(tmp_path):
    """造一张切换前的旧结构表：有 utc_date、无 date_basis、三列唯一索引、两行存量。"""
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE behavior_daily_summaries (
                id INTEGER NOT NULL PRIMARY KEY,
                symbol VARCHAR(30) NOT NULL,
                utc_date VARCHAR(10) NOT NULL,
                day_type VARCHAR(10) NOT NULL,
                counts TEXT NOT NULL,
                composition TEXT NOT NULL,
                down_net_sum FLOAT,
                computed_at DATETIME NOT NULL
            )
        """))
        conn.execute(text(
            "CREATE UNIQUE INDEX ix_behavior_daily_pit "
            "ON behavior_daily_summaries (symbol, utc_date, computed_at)"
        ))
        for d, at in (("2026-07-08", "2026-07-09 00:05:00"), ("2026-07-09", "2026-07-10 00:05:00")):
            conn.execute(text(
                "INSERT INTO behavior_daily_summaries "
                "(symbol, utc_date, day_type, counts, composition, down_net_sum, computed_at) "
                "VALUES ('BTC/USDT', :d, 'weekday', '{}', '{}', -3.87, :at)"
            ), {"d": d, "at": at})
    return eng


def test_migration_renames_column_and_marks_legacy_rows_utc(legacy_engine):
    from database import migrate_behavior_daily_basis

    with legacy_engine.begin() as conn:
        assert migrate_behavior_daily_basis(conn) is True

    with legacy_engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("behavior_daily_summaries")}
        assert "bucket_date" in cols and "utc_date" not in cols
        assert "date_basis" in cols
        rows = conn.execute(text(
            "SELECT bucket_date, date_basis, down_net_sum FROM behavior_daily_summaries "
            "ORDER BY bucket_date"
        )).all()
        assert [r.bucket_date for r in rows] == ["2026-07-08", "2026-07-09"]
        assert {r.date_basis for r in rows} == {"utc"}      # 存量一律标旧口径
        assert rows[0].down_net_sum == -3.87                # 其余字段不动


def test_migration_rebuilds_unique_index_with_basis(legacy_engine):
    from database import migrate_behavior_daily_basis

    with legacy_engine.begin() as conn:
        migrate_behavior_daily_basis(conn)

    with legacy_engine.connect() as conn:
        pit = next(i for i in inspect(conn).get_indexes("behavior_daily_summaries")
                   if i["name"] == "ix_behavior_daily_pit")
        assert list(pit["column_names"]) == ["symbol", "bucket_date", "date_basis", "computed_at"]
        assert pit["unique"]


def test_migration_is_idempotent(legacy_engine):
    from database import migrate_behavior_daily_basis

    with legacy_engine.begin() as conn:
        assert migrate_behavior_daily_basis(conn) is True
    with legacy_engine.begin() as conn:
        assert migrate_behavior_daily_basis(conn) is False   # 第二次全 no-op
    with legacy_engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM behavior_daily_summaries")).scalar()
        assert n == 2                                         # 没重复、没丢行


def test_migration_skips_when_table_absent(tmp_path):
    from database import migrate_behavior_daily_basis

    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with eng.begin() as conn:
        assert migrate_behavior_daily_basis(conn) is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
D:/anaconda/python.exe -m pytest tests/test_behavior_daily_migration.py -v
```

Expected: FAIL — `ImportError: cannot import name 'migrate_behavior_daily_basis' from 'database'`

- [ ] **Step 3: 实现模型改动**

`models/behavior.py` 模块 docstring 第 6 行改：

```python
- BehaviorDailySummary：日汇总 **point-in-time 追加表**——同一 (bucket_date, date_basis) 每次重算都新增一行，
  读取取 computed_at 最新一条；历史读数永久可回溯（回测校准的前提）。
  date_basis 区分日界口径：'utc' = 2026-07-29 之前的 UTC 日桶（只读存档），'bj' = 北京日桶（现行）。
```

`BehaviorDailySummary` 类体改：

```python
class BehaviorDailySummary(Base):
    __tablename__ = "behavior_daily_summaries"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(30), nullable=False)
    bucket_date = Column(String(10), nullable=False)     # "YYYY-MM-DD"，含义由 date_basis 决定
    date_basis = Column(String(3), nullable=False, server_default="utc", default="bj")
    day_type = Column(String(10), nullable=False)        # weekday / weekend（分桶互比）
    counts = Column(Text, nullable=False)                # JSON {tier_pct: {up, down}}（0.3 档=计数层全量）
    composition = Column(Text, nullable=False)           # JSON {macro_news/pure_resonance/industry_news/sentiment/no_ref_news/no_ref_pending}
    down_net_sum = Column(Float, nullable=True)          # 跌段净幅合计（%）
    computed_at = Column(DateTime, nullable=False)       # PIT 戳：追加不覆盖，读取取最新

    __table_args__ = (
        Index("ix_behavior_daily_pit", "symbol", "bucket_date", "date_basis", "computed_at", unique=True),
    )
```

注意 `server_default="utc"` 管旧行（DDL 层默认），`default="bj"` 管 ORM 新建行（Python 层默认）——两者故意不同。

- [ ] **Step 4: 实现迁移函数**

`database.py`：在 `migrate_legacy_annotations` 之前（第 128 行 `# v2.0 → v2.1 枚举映射` 那段注释之前）插入：

```python
def migrate_behavior_daily_basis(conn) -> bool:
    """behavior_daily_summaries 北京日口径切换（2026-07-29）：
    utc_date 改名 bucket_date + 新增 date_basis（存量标 'utc'）+ 唯一索引重建为四列。
    幂等：已迁移则全部 no-op。返回本次是否发生结构变更。"""
    insp = inspect(conn)
    if "behavior_daily_summaries" not in insp.get_table_names():
        return False
    cols = {c["name"] for c in insp.get_columns("behavior_daily_summaries")}
    changed = False
    if "utc_date" in cols and "bucket_date" not in cols:
        conn.execute(text("ALTER TABLE behavior_daily_summaries RENAME COLUMN utc_date TO bucket_date"))
        changed = True
    if "date_basis" not in cols:
        conn.execute(text("ALTER TABLE behavior_daily_summaries "
                          "ADD COLUMN date_basis VARCHAR(3) NOT NULL DEFAULT 'utc'"))
        changed = True
    want = ["symbol", "bucket_date", "date_basis", "computed_at"]
    pit = next((i for i in inspect(conn).get_indexes("behavior_daily_summaries")
                if i["name"] == "ix_behavior_daily_pit"), None)
    if pit is None or list(pit["column_names"]) != want or not pit.get("unique"):
        conn.execute(text("DROP INDEX IF EXISTS ix_behavior_daily_pit"))
        conn.execute(text("CREATE UNIQUE INDEX ix_behavior_daily_pit ON behavior_daily_summaries "
                          "(symbol, bucket_date, date_basis, computed_at)"))
        changed = True
    return changed
```

在 `_ensure_sqlite_schema` 的 `with engine.begin() as conn:` 块内，`sector_returns` 段落之后（第 126 行之后）追加：

```python
        # behavior_daily_summaries：北京日口径切换（2026-07-29 spec）——列改名 + 口径标记 + 索引重建。
        if "behavior_daily_summaries" in table_names:
            migrate_behavior_daily_basis(conn)
```

- [ ] **Step 5: 跑测试确认通过**

```bash
D:/anaconda/python.exe -m pytest tests/test_behavior_daily_migration.py -v
```

Expected: PASS，4 passed

- [ ] **Step 6: 改现有模型测试的列名**

`tests/test_behavior_models.py:62-78` 的 `test_daily_summary_pit_append` 改成：

```python
def test_daily_summary_pit_append(session):
    mk = lambda at: BehaviorDailySummary(
        symbol="BTC/USDT", bucket_date="2026-07-08", date_basis="bj", day_type="weekday",
        counts=json.dumps({"0.3": {"up": 8, "down": 11}}),
        composition=json.dumps({"sentiment": 3}),
        down_net_sum=-3.87, computed_at=at,
    )
    session.add(mk(datetime(2026, 7, 9, 0, 5)))
    session.commit()
    # PIT：同日重算 = 追加新行，不覆盖
    session.add(mk(datetime(2026, 7, 9, 6, 5)))
    session.commit()
    rows = (session.query(BehaviorDailySummary)
            .filter_by(symbol="BTC/USDT", bucket_date="2026-07-08", date_basis="bj")
            .order_by(BehaviorDailySummary.computed_at.desc()).all())
    assert len(rows) == 2
    assert rows[0].computed_at == datetime(2026, 7, 9, 6, 5)   # 读取取最新
```

- [ ] **Step 7: 跑测试确认通过**

```bash
D:/anaconda/python.exe -m pytest tests/test_behavior_models.py tests/test_behavior_daily_migration.py -v
```

Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add models/behavior.py database.py tests/test_behavior_daily_migration.py tests/test_behavior_models.py
git commit -m "feat(behavior): isolate daily PIT rows by date_basis, rename utc_date to bucket_date"
```

---

### Task 4: 写入带口径标记 + 日报目标日改北京日

**Files:**
- Modify: `services/behavior_classifier.py:287-299`（`write_daily_summary`）、`:313-322`（`run_daily_summary`）
- Test: `tests/test_bj_day_bucket.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_bj_day_bucket.py` 末尾追加（需在文件顶部 import 里补 `from models.behavior import BehaviorDailySummary`）：

```python
def test_write_daily_summary_marks_bj_basis(session):
    session.add(_seg(datetime(2026, 7, 28, 16, 1)))
    session.commit()
    row = bc.write_daily_summary(session, "BTC/USDT", "2026-07-29",
                                 now=datetime(2026, 7, 29, 16, 5))
    assert row.bucket_date == "2026-07-29"
    assert row.date_basis == "bj"
    assert json.loads(row.counts)["0.5"] == {"up": 1, "down": 0}


def test_summary_target_is_the_beijing_day_that_just_ended():
    # 正点：UTC 16:05 = 北京次日 00:05，刚结束的北京日是 07-29
    assert bc.summary_target_bj_date(datetime(2026, 7, 29, 16, 5)) == "2026-07-29"
    # 延迟到 UTC 23:00（北京 07:00）才跑，目标仍是 07-29
    assert bc.summary_target_bj_date(datetime(2026, 7, 29, 23, 0)) == "2026-07-29"
    # 提前到 UTC 15:55（北京 23:55，07-29 还没走完）：退回汇总 07-28，绝不汇总未完成的日子
    assert bc.summary_target_bj_date(datetime(2026, 7, 29, 15, 55)) == "2026-07-28"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
D:/anaconda/python.exe -m pytest tests/test_bj_day_bucket.py -v -k "bj_basis or summary_target"
```

Expected: 两条都 FAIL——
- `test_summary_target_...`：`AttributeError: module 'services.behavior_classifier' has no attribute 'summary_target_bj_date'`
- `test_write_daily_summary_marks_bj_basis`：`TypeError: 'utc_date' is an invalid keyword argument for BehaviorDailySummary`（模型已在 Task 3 改名，但 `write_daily_summary` 还在传旧关键字）

- [ ] **Step 3: 实现**

`services/behavior_classifier.py` 的 `write_daily_summary` 改：

```python
def write_daily_summary(session: Session, symbol: str, bj_date: str,
                        now: datetime | None = None) -> BehaviorDailySummary:
    """append 一条 PIT 记录（追加不覆盖，读取取 computed_at 最新）。口径固定 'bj'（北京日）。"""
    now = now or datetime.utcnow()
    counts, composition, down_sum = aggregate_day(session, symbol, bj_date)
    summary = BehaviorDailySummary(
        symbol=symbol, bucket_date=bj_date, date_basis="bj", day_type=day_type_of(bj_date),
        counts=json.dumps(counts), composition=json.dumps(composition),
        down_net_sum=down_sum, computed_at=now,
    )
    session.add(summary)
    session.commit()
    return summary
```

在 `run_daily_summary` 之前新增可测的目标日函数，并改 `run_daily_summary`：

```python
def summary_target_bj_date(now: datetime) -> str:
    """日报 job 的目标北京日 = 传入时刻往回一整天所属的北京日。
    正点（UTC 16:05 = 北京 00:05）取到刚结束那天；提前触发则退回上一天，绝不汇总未完成的日子。"""
    return bj_date_of(now - timedelta(days=1))


def run_daily_summary(now: datetime | None = None) -> dict:
    """北京 00:05（= UTC 16:05）汇总刚结束的那个北京日（PIT 追加）。"""
    from database import SessionLocal
    now = now or datetime.utcnow()
    session = SessionLocal()
    try:
        target = summary_target_bj_date(now)
        row = write_daily_summary(session, "BTC/USDT", target)
        return {"bj_date": row.bucket_date, "computed_at": row.computed_at.isoformat()}
    finally:
        session.close()
```

`datetime.now(timezone.utc)` 不再需要；若 `timezone` 在本文件其他地方没用到，删掉第 15 行 import 里的 `timezone`（先 `grep -n "timezone" services/behavior_classifier.py` 确认）。

- [ ] **Step 4: 跑测试确认通过**

```bash
D:/anaconda/python.exe -m pytest tests/test_bj_day_bucket.py -v
```

Expected: PASS，9 passed

- [ ] **Step 5: 改现有分类器测试**

`tests/test_behavior_classifier.py:131-142` 的 `test_daily_summary_pit` 改：

```python
def test_daily_summary_pit(session):
    btc = _btc_with_push()
    _seed_prices(session, "BTC/USDT", btc)
    bc.classify(session, "BTC/USDT", now=_now_after(btc))
    d = bc.bj_date_of(T0)                        # 段起点所属的北京日
    bc.write_daily_summary(session, "BTC/USDT", d, now=T0 + timedelta(hours=13))
    bc.write_daily_summary(session, "BTC/USDT", d, now=T0 + timedelta(hours=14))
    rows = session.query(BehaviorDailySummary).filter_by(bucket_date=d, date_basis="bj").all()
    assert len(rows) == 2                        # PIT 追加不覆盖
    counts = json.loads(rows[-1].counts)
    assert counts["0.5"]["up"] == 1
    assert rows[-1].day_type == bc.day_type_of(d)
```

- [ ] **Step 6: 跑测试确认通过**

```bash
D:/anaconda/python.exe -m pytest tests/test_behavior_classifier.py -v
```

Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add services/behavior_classifier.py tests/test_bj_day_bucket.py tests/test_behavior_classifier.py
git commit -m "feat(behavior): write bj-basis PIT rows, target Beijing day in daily job"
```

---

### Task 5: 读层改北京日序列 + 只认 bj 行 + API 字段改名

**Files:**
- Modify: `services/behavior_views.py:80-109`（`daily_series`）
- Modify: `schemas/behavior.py:51-52`
- Test: `tests/test_bj_day_bucket.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_bj_day_bucket.py` 末尾追加：

```python
def test_daily_series_ignores_legacy_utc_rows(session):
    """同一 bucket_date 上 utc 行与 bj 行并存时，读层只认 bj 行。"""
    from services import behavior_views

    today_bj = bj_date_of(datetime.utcnow())
    session.add(BehaviorDailySummary(
        symbol="BTC/USDT", bucket_date=today_bj, date_basis="utc", day_type="weekday",
        counts=json.dumps({"0.3": {"up": 99, "down": 99}}), composition=json.dumps({}),
        down_net_sum=-9.99, computed_at=datetime.utcnow(),
    ))
    session.commit()

    resp = behavior_views.daily_series(session, "BTC/USDT", days=1)
    day = resp.days[-1]
    assert day.bj_date == today_bj
    assert day.live is True                          # 没有 bj 行 → 现算，而不是读到那条 utc 行
    assert day.counts.get("0.3", {}).get("up", 0) != 99


def test_daily_series_reads_bj_row_when_present(session):
    from services import behavior_views

    today_bj = bj_date_of(datetime.utcnow())
    session.add(BehaviorDailySummary(
        symbol="BTC/USDT", bucket_date=today_bj, date_basis="bj", day_type="weekday",
        counts=json.dumps({"0.3": {"up": 7, "down": 2}}), composition=json.dumps({}),
        down_net_sum=-1.23, computed_at=datetime.utcnow(),
    ))
    session.commit()

    day = behavior_views.daily_series(session, "BTC/USDT", days=1).days[-1]
    assert day.live is False
    assert day.counts["0.3"]["up"] == 7
```

- [ ] **Step 2: 跑测试确认失败**

```bash
D:/anaconda/python.exe -m pytest tests/test_bj_day_bucket.py -v -k "daily_series"
```

Expected: FAIL — `AttributeError: 'BehaviorDailySchema' object has no attribute 'bj_date'`（读层仍输出 `utc_date`，且会读到那条 utc 行）

- [ ] **Step 3: 实现 schema 改名**

`schemas/behavior.py:51-52` 改：

```python
class BehaviorDailySchema(BaseModel):
    bj_date: str                          # 北京日界（北京 00:00–24:00 = UTC 16:00–16:00）
```

- [ ] **Step 4: 实现读层**

`services/behavior_views.py` 顶部第 32 行 import 改为：

```python
from services.time_utils import bj_date_of, timestamp_pair
```

`daily_series` 整体改为：

```python
def daily_series(session: Session, symbol: str, days: int = 14) -> BehaviorDailyResponse:
    """最近 N 个北京日：优先取每日最新 PIT 行（只认 date_basis='bj'）；
    没有（当日盘中/历史缺口/口径切换前）按同口径现算 live=True。"""
    now = datetime.utcnow()
    today_bj = datetime.strptime(bj_date_of(now), "%Y-%m-%d")
    out: list[BehaviorDailySchema] = []
    for offset in range(days - 1, -1, -1):
        bj_date = (today_bj - timedelta(days=offset)).strftime("%Y-%m-%d")
        row = (
            session.query(BehaviorDailySummary)
            .filter_by(symbol=symbol, bucket_date=bj_date, date_basis="bj")
            .order_by(BehaviorDailySummary.computed_at.desc())
            .first()
        )
        extras = day_direction_extras(session, symbol, bj_date)
        if row is not None:
            out.append(BehaviorDailySchema(
                bj_date=bj_date, day_type=row.day_type,
                counts=json.loads(row.counts),
                composition=merge_composition(json.loads(row.composition)),   # 历史六类 PIT 行读取归并
                down_net_sum=row.down_net_sum, computed_at=_tf(row.computed_at), live=False,
                **extras,
            ))
        else:
            counts, composition, down_sum = aggregate_day(session, symbol, bj_date)
            out.append(BehaviorDailySchema(
                bj_date=bj_date, day_type=day_type_of(bj_date),
                counts=counts, composition=composition,
                down_net_sum=down_sum, computed_at=_tf(now), live=True,
                **extras,
            ))
    return BehaviorDailyResponse(symbol=symbol, days=out)
```

- [ ] **Step 5: 跑测试确认通过**

```bash
D:/anaconda/python.exe -m pytest tests/test_bj_day_bucket.py -v
```

Expected: PASS，11 passed

- [ ] **Step 6: 改端点测试**

`tests/test_behavior_api.py:100-105` 那段改：

```python
    # 段落在哪个北京日取决于运行时刻（北京午夜后 utcnow-6h 落昨日）——按段起点算北京日取行
    from services.time_utils import bj_date_of
    seg_date = bj_date_of(datetime.fromisoformat(target["start"]["timestamp_utc"]))

    def _seg_day():
        days = client.get("/api/behavior/daily?days=2").json()["days"]
        return next(d for d in days if d["bj_date"] == seg_date)
```

- [ ] **Step 7: 跑测试确认通过**

```bash
D:/anaconda/python.exe -m pytest tests/test_behavior_api.py -v
```

Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add services/behavior_views.py schemas/behavior.py tests/test_bj_day_bucket.py tests/test_behavior_api.py
git commit -m "feat(behavior): serve Beijing-day series, read only bj-basis PIT rows"
```

---

### Task 6: 日报定时任务改北京 00:05

**Files:**
- Modify: `api/app.py:130-131`（docstring）、`:233-240`（cron）

- [ ] **Step 1: 改 cron 与注释**

`api/app.py:130-131` 的 docstring 改：

```python
    def behavior_daily_summary() -> None:
        """北京 00:05（= UTC 16:05）汇总刚结束的北京日行为日报（point-in-time 追加，不覆盖历史读数）。"""
```

`api/app.py:223` 的段落注释改：

```python
    # 价格行为引擎：与价格采集同节奏（5min），错峰 +2min 让本轮快照先落库；日报北京 00:05 汇总刚结束的北京日。
```

`api/app.py:233-240` 的 `add_job` 改（调度器 `timezone="UTC"`，见 `app.py:45`）：

```python
    scheduler.add_job(
        behavior_daily_summary,
        CronTrigger(hour=16, minute=5),          # UTC 16:05 = 北京 00:05
        id="behavior_daily_summary",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 2: 确认应用能起、job 注册正常**

```bash
D:/anaconda/python.exe -c "from api.app import create_app; app = create_app(enable_scheduler=False); print('app ok')"
```

Expected: 打印 `app ok`，无异常

- [ ] **Step 3: 提交**

```bash
git add api/app.py
git commit -m "chore(scheduler): run behavior daily summary at Beijing 00:05"
```

---

### Task 7: 一次性回算脚本

**Files:**
- Create: `scripts/backfill_behavior_bj_daily.py`
- Test: `tests/test_backfill_behavior_bj_daily.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_backfill_behavior_bj_daily.py`：

```python
# -*- coding: utf-8 -*-
"""北京日日汇总回算脚本：写入正确、幂等、dry-run 不落库。"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.behavior import BehaviorDailySummary, BehaviorSegment
from scripts.backfill_behavior_bj_daily import backfill

NOW = datetime(2026, 7, 30, 2, 0)        # 北京 2026-07-30 10:00


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    # 北京 07-28 与 07-29 各一个段
    for start in (datetime(2026, 7, 27, 18, 0), datetime(2026, 7, 28, 18, 0)):
        s.add(BehaviorSegment(
            symbol="BTC/USDT", start_dt=start, end_dt=start + timedelta(minutes=15),
            direction=1, tier_idx=1, tier_max=0.5, net_pct=0.6, amp_pct=0.7,
            key_ts=start + timedelta(minutes=5), classification="pure_resonance",
            class_version="v2", s_scores=json.dumps({}), news_ids=json.dumps([]),
        ))
    s.commit()
    yield s
    s.close()


def test_backfill_writes_bj_rows_for_completed_days(session):
    results = backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    assert [r["bj_date"] for r in results] == ["2026-07-27", "2026-07-28", "2026-07-29"]
    assert {r["action"] for r in results} == {"write"}
    rows = session.query(BehaviorDailySummary).filter_by(date_basis="bj").all()
    assert len(rows) == 3
    by_date = {r.bucket_date: json.loads(r.counts) for r in rows}
    assert by_date["2026-07-28"]["0.5"] == {"up": 1, "down": 0}   # UTC 07-27 18:00 = 北京 07-28 02:00
    assert by_date["2026-07-29"]["0.5"] == {"up": 1, "down": 0}
    assert by_date["2026-07-27"] == {}                             # 那天没段


def test_backfill_never_touches_today(session):
    results = backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    assert "2026-07-30" not in [r["bj_date"] for r in results]     # 今天还没走完，不写


def test_backfill_is_idempotent(session):
    backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    again = backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    assert {r["action"] for r in again} == {"skip"}
    assert session.query(BehaviorDailySummary).filter_by(date_basis="bj").count() == 3


def test_backfill_dry_run_writes_nothing(session):
    results = backfill(session, "BTC/USDT", days=3, commit=False, now=NOW)
    assert {r["action"] for r in results} == {"dry-run"}
    assert session.query(BehaviorDailySummary).count() == 0


def test_backfill_leaves_legacy_utc_rows_alone(session):
    session.add(BehaviorDailySummary(
        symbol="BTC/USDT", bucket_date="2026-07-28", date_basis="utc", day_type="weekday",
        counts=json.dumps({"0.3": {"up": 42, "down": 0}}), composition=json.dumps({}),
        down_net_sum=-1.0, computed_at=datetime(2026, 7, 29, 0, 5),
    ))
    session.commit()
    backfill(session, "BTC/USDT", days=3, commit=True, now=NOW)
    legacy = session.query(BehaviorDailySummary).filter_by(date_basis="utc").one()
    assert json.loads(legacy.counts)["0.3"]["up"] == 42            # 旧行原样
```

- [ ] **Step 2: 跑测试确认失败**

```bash
D:/anaconda/python.exe -m pytest tests/test_backfill_behavior_bj_daily.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backfill_behavior_bj_daily'`

- [ ] **Step 3: 实现脚本**

新建 `scripts/backfill_behavior_bj_daily.py`：

```python
# -*- coding: utf-8 -*-
"""行为日汇总回算：按北京日补写 date_basis='bj' 的 PIT 行（2026-07-29 口径切换配套）。

日聚合口径从 UTC 日改成北京日后，存档表里只剩旧的 date_basis='utc' 行，面板会全部走现算。
本脚本按北京日重算最近 N 个**已结束**的北京日并写入 bj 行，让面板立刻回到"已锁账"状态。
旧 utc 行只读不动。幂等：目标日已有 bj 行则跳过。

跑法（生产服务器,数据在那里）：
  .venv/bin/python scripts/backfill_behavior_bj_daily.py --days 14           # dry-run 先看
  .venv/bin/python scripts/backfill_behavior_bj_daily.py --days 14 --commit  # 落库
本地库自 2026-05-17 起停更,只能跑通流程、看不到近期数据（见 memory: local-env）。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_session
from models.behavior import BehaviorDailySummary
from services import behavior_classifier as bc
from services.time_utils import bj_date_of


def backfill(session, symbol: str, days: int, commit: bool,
             now: datetime | None = None) -> list[dict]:
    """回算最近 days 个已结束的北京日（不含今天）。commit=False 只试算不落库。"""
    now = now or datetime.utcnow()
    today_bj = datetime.strptime(bj_date_of(now), "%Y-%m-%d")
    results: list[dict] = []
    for offset in range(days, 0, -1):
        bj_date = (today_bj - timedelta(days=offset)).strftime("%Y-%m-%d")
        exists = (
            session.query(BehaviorDailySummary)
            .filter_by(symbol=symbol, bucket_date=bj_date, date_basis="bj")
            .first()
        )
        if exists is not None:
            results.append({"bj_date": bj_date, "action": "skip", "reason": "已有 bj 行"})
            continue
        counts, _composition, down_sum = bc.aggregate_day(session, symbol, bj_date)
        segments = sum(v.get("up", 0) + v.get("down", 0) for v in counts.values())
        if commit:
            bc.write_daily_summary(session, symbol, bj_date, now=now)
        results.append({"bj_date": bj_date, "action": "write" if commit else "dry-run",
                        "segments": segments, "down_net_sum": down_sum})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="回算最近 N 个已结束的北京日（不含今天）")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--commit", action="store_true", help="不加则只 dry-run 打印，不写库")
    args = ap.parse_args()

    session = get_session()
    try:
        results = backfill(session, args.symbol, args.days, args.commit)
        for r in results:
            if r["action"] == "skip":
                print(f"  {r['bj_date']}  跳过（{r['reason']}）")
            else:
                print(f"  {r['bj_date']}  段数 {r['segments']:>3}  "
                      f"跌净幅Σ {r['down_net_sum']:+.2f}%  [{r['action']}]")
        skipped = sum(1 for r in results if r["action"] == "skip")
        pending = len(results) - skipped
        if args.commit:
            print(f"\n完成：写入 {pending} 行，跳过 {skipped} 行。")
        else:
            print(f"\nDRY-RUN：将写入 {pending} 行，跳过 {skipped} 行。确认无误后加 --commit 落库。")
    finally:
        session.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
D:/anaconda/python.exe -m pytest tests/test_backfill_behavior_bj_daily.py -v
```

Expected: PASS，5 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/backfill_behavior_bj_daily.py tests/test_backfill_behavior_bj_daily.py
git commit -m "feat(scripts): add Beijing-day daily summary backfill"
```

---

### Task 8: 前端字段与标题

**Files:**
- Regenerate: `frontend/src/api/types.ts`
- Modify: `frontend/src/pages/behaviorFormat.ts:54`
- Modify: `frontend/src/pages/behaviorFormat.test.ts:19,47`
- Modify: `frontend/src/pages/BehaviorPage.tsx:56`

- [ ] **Step 1: 改前端测试夹具（先失败）**

`frontend/src/pages/behaviorFormat.test.ts` 第 19 行与第 47 行的 `utc_date:` 改成 `bj_date:`：

```typescript
        bj_date: "2026-07-08", day_type: "weekday", live: false,
```

```typescript
        bj_date: "2026-07-09", day_type: "weekday", live: true,
```

- [ ] **Step 2: 跑前端测试确认失败**

```bash
cd frontend && npm test
```

Expected: FAIL — `buildDailyRows` 读 `d.utc_date` 拿到 `undefined`，`date` 断言不匹配（`TypeError: Cannot read properties of undefined (reading 'slice')`）

- [ ] **Step 3: 重新生成 API 类型**

```bash
cd frontend && npm run generate:api-types
```

Expected: `frontend/src/api/types.ts` 里 `BehaviorDailySchema` 的 `utc_date: string;` 变成 `bj_date: string;`（该文件自动生成，不要手改）

- [ ] **Step 4: 改 behaviorFormat**

`frontend/src/pages/behaviorFormat.ts:54` 改：

```typescript
      date: d.bj_date.slice(5),
```

- [ ] **Step 5: 改面板标题**

`frontend/src/pages/BehaviorPage.tsx:56` 改：

```tsx
            <div className="panel-head"><h2>① 日趋势 · 近 14 个北京日（0.3 档只计数）</h2></div>
```

- [ ] **Step 6: 跑前端测试 + 类型检查**

```bash
cd frontend && npm test && npm run typecheck
```

Expected: 测试全绿，`tsc -b` 无报错

- [ ] **Step 7: 跑生成物一致性测试**

```bash
D:/anaconda/python.exe -m pytest tests/test_openapi_types.py -v
```

Expected: PASS（该测试会重跑生成器并断言 `types.ts` 无 diff——若失败说明第 3 步没跑或跑在了错的 Python 上）

- [ ] **Step 8: 提交**

```bash
git add frontend/src/api/types.ts frontend/src/pages/behaviorFormat.ts frontend/src/pages/behaviorFormat.test.ts frontend/src/pages/BehaviorPage.tsx
git commit -m "feat(frontend): show Beijing-day dates on behavior panel"
```

---

### Task 9: 全量验证 + 术语表

**Files:**
- Modify: `GLOSSARY.md`

- [ ] **Step 1: 跑全量后端测试**

```bash
D:/anaconda/python.exe -m pytest -q
```

Expected: 全绿。若有失败，逐个看是不是漏改的 `utc_date` 引用：

```bash
grep -rn "utc_date" --include=*.py --include=*.ts --include=*.tsx .
```

Expected: 只在 `docs/` 与迁移函数/测试的历史列名字符串里出现，业务代码里为 0

- [ ] **Step 2: 跑全量前端测试**

```bash
cd frontend && npm test
```

Expected: 全绿

- [ ] **Step 3: 追加术语表**

在 `GLOSSARY.md` 末尾追加（每词三行：是什么 / 本项目为何用 / 在哪个文件）：

```markdown
## 日界（bucket boundary）
- 是什么：把连续的时间流切成"一天一天"的那条线。
- 本项目为何用：行为面板按天统计涨跌段。日界原先切在 UTC 00:00（北京早 8 点），
  2026-07-29 起改切北京 00:00，让图上的日期就是用户心里的日期。切在哪里不改变段本身的数据，
  只改变它被算进哪一天。
- 在哪个文件：`services/time_utils.py`（`bj_day_bounds`）、`services/behavior_classifier.py`（`aggregate_day`）

## PIT 表（point-in-time，时点存档表）
- 是什么：只追加不覆盖的历史快照表，每行带一个"这是什么时候算出来的"时间戳。
- 本项目为何用：日汇总重算时不抹掉旧读数，回测校准时才能还原"当时看到的是什么"。
  类似财务的期末锁账，但锁的是读数而不是凭证。
- 在哪个文件：`models/behavior.py`（`BehaviorDailySummary`）

## 迁移（migration）
- 是什么：给已经存了数据的表改结构（加列、改列名、改索引）的一次性脚本，要能重复跑不出错。
- 本项目为何用：改日界要给存档表加"口径标记"列区分新旧，线上库有存量数据不能推倒重建。
- 在哪个文件：`database.py`（`_ensure_sqlite_schema` / `migrate_behavior_daily_basis`）
```

- [ ] **Step 4: 提交**

```bash
git add GLOSSARY.md
git commit -m "docs(glossary): add day boundary, PIT table, migration"
```

---

### Task 10: 上线 mmon.top（用户已授权，但每个动库步骤前先给用户看输出）

**前置：** Task 1–9 全绿且已 review。

- [ ] **Step 1: 合并到 main**

```bash
git checkout main && git merge --no-ff feat/behavior-bj-day
```

- [ ] **Step 2: 部署（`deploy.sh` 会先 VACUUM INTO 备份 + integrity_check）**

按 `docs/specs/deployment.md` 的既有流程部署。重启时 `_ensure_sqlite_schema` 自动完成改名/加列/重建索引。

- [ ] **Step 3: 验证迁移落地**

```bash
ssh mmon "cd /opt/market-monitor && sqlite3 data/market_monitor.db '.schema behavior_daily_summaries'"
```

Expected: 看到 `bucket_date`、`date_basis`，索引为四列 unique

- [ ] **Step 4: 回算 dry-run，把输出贴给用户确认**

```bash
ssh mmon "cd /opt/market-monitor && .venv/bin/python scripts/backfill_behavior_bj_daily.py --days 14"
```

Expected: 打印 14 行 `[dry-run]`，段数看着合理（不是全 0）。**先给用户看，确认后再 commit。**

- [ ] **Step 5: 回算落库**

```bash
ssh mmon "cd /opt/market-monitor && .venv/bin/python scripts/backfill_behavior_bj_daily.py --days 14 --commit"
```

Expected: `完成：写入 14 行，跳过 0 行。`

- [ ] **Step 6: 验收**

```bash
ssh mmon "cd /opt/market-monitor && sqlite3 data/market_monitor.db \"SELECT date_basis, COUNT(*) FROM behavior_daily_summaries GROUP BY date_basis\""
```

Expected: `utc|<原有行数不变>`、`bj|14`

再打开 `https://mmon.top` 行为面板确认：标题是「近 14 个北京日」、X 轴末位日期 = 今天的北京日期、构成结论区有数。

---

## Self-Review

**Spec 覆盖检查：** spec §2.1 日界定义 → Task 1；§2.2 存档表隔离（改名/加列/索引/只读 bj）→ Task 3 + Task 5；§2.3 副作用（已在 spec 记录，无需代码）；§3.1 → Task 1；§3.2 → Task 2 + Task 4；§3.3/§3.4 → Task 3；§3.5 → Task 5；§3.6 → Task 5 Step 3；§3.7 → Task 6；§3.8 → Task 7；§3.9 → Task 8；§4 测试五项 → 跨界(Task 2)、口径隔离(Task 5)、迁移幂等(Task 3)、回算幂等(Task 7)、日报归属(Task 4)；§5 上线 → Task 10；§6 不在范围 → 无任务，正确。**无缺口。**

**类型/命名一致性：** `bj_date_of` / `bj_day_bounds`（Task 1 定义，Task 2/4/5/7 使用）；`bucket_date` / `date_basis`（Task 3 定义，Task 4/5/7 使用）；`summary_target_bj_date`（Task 4 定义与使用）；`backfill(session, symbol, days, commit, now)`（Task 7 定义与测试签名一致）；schema 字段 `bj_date`（Task 5 定义，Task 8 前端消费）。**一致。**
