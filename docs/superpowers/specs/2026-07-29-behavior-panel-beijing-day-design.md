# 行为面板日聚合口径：UTC 日 → 北京日

日期：2026-07-29　状态：已由用户批准（方案 B / 列改名 bucket_date / 代码完成后直接上 mmon.top）

## 1. 背景与证据

- 行为面板（`/behavior`，`frontend/src/pages/BehaviorPage.tsx`）上**没有任何时钟时间**，
  用户看到的"时间"只有两处：标题 `① 日趋势 · 近 14 个 UTC 日`（`BehaviorPage.tsx:56`）
  和三张图 X 轴的 `MM-DD`（`behaviorFormat.ts:54`，来自 `BehaviorDailySchema.utc_date`）。
- 那个日期是**按 UTC 日切分的聚合桶**，即北京时间 08:00 → 次日 08:00。
  `models/behavior.py:54` 与 `schemas/behavior.py:52` 的注释原文即"UTC 日界 = 北京 8 点"。
  分桶实现在 `services/behavior_classifier.py:211` / `:241`：把日期字符串按 naive UTC
  零点解析，取 `[day, day+1)` 过滤 `BehaviorSegment.start_dt`。
- 用户反馈：图上写 `07-28` 但实际覆盖北京 28 日 08:00 到 29 日 08:00，与"今天"的心智
  模型对不上，读起来费劲。
- 因此"改成北京时间"**必然是改分桶口径**，不是改显示格式——纯改标签解决不了痛点。

## 2. 决策（用户已拍板）

### 2.1 日界定义

北京日 `D` ≡ UTC 区间 `[D-1 16:00, D 16:00)`（北京 00:00 = 前一天 UTC 16:00）。

中国自 1991 年起无夏令时，固定 +8 小时偏移正确，无需引入时区数据库。项目已有
`services/time_utils.py:BJ_OFFSET = timedelta(hours=8)`，沿用。

### 2.2 存档表：加口径列，新旧物理隔离（方案 B）

`behavior_daily_summaries` 是 point-in-time 追加表（同一日期每次重算追加一行，
读取取 `computed_at` 最新）。改口径后同一个日期字符串会**同名不同义**——旧行是
UTC 桶，新行是北京桶——混在一列里读就是静默出错。

处理：

1. `utc_date` 列**改名 `bucket_date`**（SQLite `ALTER TABLE ... RENAME COLUMN`，
   索引定义自动跟随；旧行内容一个字节不动，可回滚）。需 SQLite ≥ 3.25；服务器已
   满足更高的 ≥3.27（`VACUUM INTO` 已在用，见 `2026-07-22-deploy-backup-vacuum-into-design.md`）。
2. 新增 `date_basis VARCHAR(3) NOT NULL DEFAULT 'utc'`——旧行自动落 `'utc'`，
   往后写入一律 `'bj'`。
3. 唯一索引 `ix_behavior_daily_pit` 从 `(symbol, utc_date, computed_at)` 重建为
   `(symbol, bucket_date, date_basis, computed_at)`，防止两套口径互相顶掉。
   旧行 `date_basis` 恒为 `'utc'`，四元组唯一性由原三元组唯一性保证，重建不会撞唯一约束。
4. 读路径（`behavior_views.daily_series`）**只查 `date_basis='bj'`**；旧 `utc` 行永久
   保留作历史档案，面板不再读取。

**已否决的备选**：

- 方案 A（只加口径列、不回算）：切换后头 14 天构成柱全走现算，两周后才逐步固化。
  用户选了 B——立刻回算，面板马上回到"已锁账"状态。
- 方案 C（不加列，新行直接写北京日）：靠"只认某时刻之后写的行"这种隐式规则区分新旧，
  排查时极易踩坑。否决。

### 2.3 已知且已接受的副作用

面板过去 13 天的**构成柱**当前读的是存档行里"机器当时的看法"。回算写入的 `bj` 行
`computed_at` = 回算那一刻，其 `composition` 由 `aggregate_day` 按"人工优先"
（`human_class` 覆盖 `classification`）计算，因此**会带上用户后来补的人工标注**，
数字与旧 `utc` 行不等价。

这与项目既有原则一致——`behavior_classifier.py:240` 注释："人工改判要立刻反映到趋势图，
冻结旧结论反而误导"。用户已确认接受。

## 3. 改动清单

### 3.1 `services/time_utils.py`（新增两个共用函数，全项目唯一换算点）

```python
def bj_date_of(value: datetime | None) -> str | None:
    """naive UTC 时刻 → 它属于哪个北京日 'YYYY-MM-DD'。"""

def bj_day_bounds(bj_date: str) -> tuple[datetime, datetime]:
    """北京日 'YYYY-MM-DD' → [start, end) 的 naive UTC 边界。"""
```

### 3.2 `services/behavior_classifier.py`

- `aggregate_day(session, symbol, bj_date)`：`datetime.strptime(...)` + `[day, day+1)`
  改为 `bj_day_bounds(bj_date)`。**统计逻辑一个字不动**——档位阈值、情绪口径、
  净幅规则、`no_ref` 注记全部原样（用户已校准的参数不在本次改动范围内）。
- `day_direction_extras(session, symbol, bj_date)`：同上，只换边界。
- `day_type_of`：函数名与函数体均不变（周末判定按传入日期字符串），仅入参语义改为
  北京日——形参 `utc_date` 改名为 `bj_date`，docstring 同步说明。
- `write_daily_summary`：写入时带 `date_basis='bj'`。
- `run_daily_summary`：`yesterday` 改为"北京日的昨天" = `bj_date_of(utcnow)` 减一天。

### 3.3 `models/behavior.py`

`BehaviorDailySummary`：`utc_date` → `bucket_date`，新增 `date_basis`，索引改四列。

### 3.4 `database.py:_ensure_sqlite_schema`

在现有 `behavior_segments` 段落后新增 `behavior_daily_summaries` 段落，与
`human_class` / `dismissed` 同一套路，幂等可重复跑：

```
if "behavior_daily_summaries" in table_names:
    existing = {列名集合}
    if "utc_date" in existing and "bucket_date" not in existing:
        ALTER TABLE behavior_daily_summaries RENAME COLUMN utc_date TO bucket_date
    if "date_basis" not in existing:
        ALTER TABLE behavior_daily_summaries ADD COLUMN date_basis VARCHAR(3) NOT NULL DEFAULT 'utc'
    DROP INDEX IF EXISTS ix_behavior_daily_pit
    CREATE UNIQUE INDEX IF NOT EXISTS ix_behavior_daily_pit
        ON behavior_daily_summaries (symbol, bucket_date, date_basis, computed_at)
```

全新库走 `create_all` 直接建出新结构，上述守卫全部 no-op（索引 drop/create 结果等价）。

### 3.5 `services/behavior_views.py:daily_series`

- 日期序列从"UTC 今天往前推"改为"北京今天往前推"。
- 查存档行时增加 `date_basis == "bj"` 过滤。
- 输出字段 `utc_date=` 改 `bj_date=`。

### 3.6 `schemas/behavior.py`

`BehaviorDailySchema.utc_date` → `bj_date`，注释改为"北京日界（00:00–24:00 北京时间）"。

### 3.7 `api/app.py`

`behavior_daily_summary` 的 `CronTrigger(hour=0, minute=5)` → `CronTrigger(hour=16, minute=5)`
（调度器 `timezone="UTC"`，16:05 UTC = 北京 00:05）。docstring 同步改。

### 3.8 `scripts/backfill_behavior_bj_daily.py`（新增）

风格对齐 `scripts/backfill_ledger.py`：

- 默认 `--dry-run` 只打印将写入什么；`--commit` 才落库。
- `--days N`（默认 14）：按北京日重算最近 N 天，写 `date_basis='bj'` 行，
  `computed_at` = 回算那一刻。
- 幂等：目标日已有 `bj` 行则跳过，重复跑不堆垃圾。
- 不触碰任何 `utc` 行。

### 3.9 前端

- `frontend/src/api/types.ts` 是 `scripts/generate_openapi_types.py` **自动生成**的
  （文件头明写"Do not edit by hand"），跑 `npm run generate:api-types` 重新生成。
- `frontend/src/pages/behaviorFormat.ts:54`：`d.utc_date.slice(5)` → `d.bj_date.slice(5)`。
- `frontend/src/pages/BehaviorPage.tsx:56`：`近 14 个 UTC 日` → `近 14 个北京日`。

## 4. 测试

改现有断言口径：`tests/test_behavior_classifier.py`、`tests/test_behavior_api.py`、
`tests/test_behavior_models.py`、`frontend/src/pages/behaviorFormat.test.ts`。

新增用例：

1. **跨界归属**：`start_dt` 为 UTC 15:59 与 16:01 的两个段，必须落进相邻两个北京日。
2. **口径隔离**：同一 `bucket_date` 同时存在 `utc` 行与 `bj` 行时，`daily_series`
   只读到 `bj` 行。
3. **迁移幂等**：旧结构表（含 `utc_date`、无 `date_basis`、三列索引 + 若干旧行）
   跑两次 `_ensure_sqlite_schema`，结果一致、旧行 `date_basis` 全为 `'utc'`、
   旧行其余字段不变。参照 `tests/test_annotation_v2.py:test_migration_v1_and_v20_rows`。
4. **回算脚本幂等**：跑两次产出行数一致。
5. **日报归属**：给 `run_daily_summary` 注入固定的"当前 UTC 时刻"（如 2026-07-29 16:05
   UTC），断言它汇总的是刚结束的那个北京日（2026-07-29），而非 UTC 昨天。

## 5. 上线（mmon.top，用户已授权）

实盘数据只在 mmon.top 服务器（本地库是滞后备份），改动必须在服务器上走一遍：

1. 部署前备份（`deploy.sh` 的 `backup_sqlite()` 已是 VACUUM INTO + `integrity_check`，
   见 `2026-07-22-deploy-backup-vacuum-into-design.md`）。
2. 部署 → 重启触发 `_ensure_sqlite_schema` 完成改名/加列/重建索引。
3. 跑回算脚本：先 `--dry-run` 看输出，确认无误再 `--commit`。
4. 验收：打开行为面板确认 X 轴日期与北京日一致；抽查一个跨 UTC 16:00 的段落进了正确的北京日；
   确认存档表里 `utc` 行数未变、新增了 14 行 `bj`。

回滚：旧 `utc` 行完好无损，代码回退 + 读路径改回 `date_basis='utc'` 即可恢复原状
（`bucket_date` 列名不影响回退读取）。

## 6. 明确不在本次范围

- 不改任何已校准的阈值/参数（档位、覆盖度、S 中枢等）。
- 不改段检测、分类、告警逻辑。
- 不改新闻标注页（`/annotations`）——其联动曲线已用 `timestamp_bj` 显示北京时间。
- 不改 `list_segments`（滚动窗口口径，与日界无关）。
