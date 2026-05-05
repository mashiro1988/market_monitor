# 预测市场页面 grid 重设计 + UI 跟踪管理

**Date:** 2026-05-05
**Branch:** `predictions-grid-redesign`
**Status:** Approved (brainstorming) — pending plan

## 背景与动机

预测市场页面（`frontend/src/pages/PredictionsPage.tsx`）当前每个 family 渲染一张全宽 `height=320` 的 MultiLineChart，一个浏览器视口只能看见 1 张图，纵向浏览效率差。同时跟踪的市场列表硬编码在 `config.py` 的 `POLYMARKET.tracked_tags / tracked_slugs` 中，新增/删除市场必须改源码并重启进程。

本次改动目标：
1. 把图表布局改为 2 列 grid，单卡更小更紧凑，一个标准浏览器视口可以同时看见 2-4 张图。
2. 提供 UI 输入跟踪列表，支持单个 market/event slug 或一个 tag（家族），改完即时生效。

## 范围

**包含：**
- 新建 `tracked_markets` SQLite 表 + SQLAlchemy 模型
- 启动时从 `config.POLYMARKET` seed 一次（已存在则跳过）
- `PolymarketSource.fetch()` 改为查 DB 而非读 config
- 新增 4 个 REST 端点 (CRUD)
- 前端：grid 布局 + 跟踪管理面板（折叠）
- 新 CSS：`.prediction-grid`、`.prediction-card`

**不包含：**
- 每张卡独立时间窗口（用户先选方案 3：保持顶部全局时间窗口）
- 添加时在线校验 slug/tag 是否在 Polymarket 存在（依赖现有的扫描静默忽略）
- 单市场详情下拉（移除，单市场也以卡片形式展示）
- 后端推送/轮询 reload 通知（扫描器每次扫描前查 DB 即可）

## 数据模型

### 新表 `tracked_markets`

```python
class TrackedMarket(Base):
    __tablename__ = "tracked_markets"

    id = Column(Integer, primary_key=True)
    kind = Column(String(16), nullable=False)        # "slug" | "tag"
    identifier = Column(String(255), nullable=False) # polymarket slug 或 tag 名
    display_name = Column(String(255), nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    __table_args__ = (
        UniqueConstraint("kind", "identifier", name="uq_tracked_kind_identifier"),
    )
```

`kind` 不用 enum 类型，用 string + 应用层校验（与项目其他模型风格一致）。

### Seed 行为

`database.py` 的 `init_db()` 在建表后调用一次 `seed_tracked_markets()`：
- 遍历 `config.POLYMARKET["tracked_slugs"]` → upsert `kind="slug"` 行
- 遍历 `config.POLYMARKET["tracked_tags"]` → upsert `kind="tag"` 行
- 已存在 `(kind, identifier)` 的跳过（不覆盖 `enabled` / `display_name`，避免抹掉用户改动）

`config.py` 里的列表保留作为初始数据来源；之后是否清空由用户决定，本次不删。

## 后端 API

新增 4 个端点，挂在已有 `/api/predictions` 前缀下：

| 方法   | 路径                             | 说明                                    |
| ------ | -------------------------------- | --------------------------------------- |
| GET    | `/api/predictions/tracked`       | 列出所有 TrackedMarket（按 created_at） |
| POST   | `/api/predictions/tracked`       | 添加，body: `{kind, identifier, display_name?}` |
| PATCH  | `/api/predictions/tracked/{id}`  | body: `{enabled?, display_name?}`       |
| DELETE | `/api/predictions/tracked/{id}`  | 物理删除                                 |

**校验规则（POST）：**
- `kind` 必须是 `"slug"` 或 `"tag"`
- `identifier` 非空、≤255 字符、去 trailing whitespace
- `(kind, identifier)` 重复 → 返回 409

**Schema:**
```python
class TrackedMarketSchema(BaseModel):
    id: int
    kind: Literal["slug", "tag"]
    identifier: str
    display_name: str | None
    enabled: bool
    created_at: datetime

class TrackedMarketCreate(BaseModel):
    kind: Literal["slug", "tag"]
    identifier: str
    display_name: str | None = None

class TrackedMarketUpdate(BaseModel):
    enabled: bool | None = None
    display_name: str | None = None
```

文件位置：`schemas/predictions.py`（已存在）下追加。Service：`services/prediction_service.py` 新增 `list_tracked / create_tracked / update_tracked / delete_tracked`。

## 扫描器改造

`scanners/sources/polymarket/source.py`：

- 删除 `__init__` 里读 `tracked_tags / tracked_slugs` 的两行
- `fetch()` 入口改为查询 DB：

```python
from database import get_session
from models.tracked_market import TrackedMarket

def fetch(self) -> list[PredictionRecord]:
    session = get_session()
    try:
        rows = session.query(TrackedMarket).filter(TrackedMarket.enabled.is_(True)).all()
        slugs = [r.identifier for r in rows if r.kind == "slug"]
        tags = [r.identifier for r in rows if r.kind == "tag"]
    finally:
        session.close()
    # ... 原有 slug 优先 / tag 候选发现的逻辑保持不变
```

`config.POLYMARKET.discovery_limit / min_volume` 仍从 config 读，不入库（这些是扫描行为参数，不是市场清单本身）。

## 前端

### 路由 / 页面结构

`PredictionsPage.tsx` 重写：

```
[PageHeader: "预测市场" + 最后更新时间]
[Toolbar: 时间窗口 + 搜索]
[<details> 跟踪管理 — 默认收起]
   ├ 添加表单行
   └ 已跟踪表（DataTable 复用）
[Grid: 主题卡片 (families)]
[Grid: 单市场卡片 (没有 family 的 markets)]
```

移除原"单市场明细"下拉 + 大图。

### 卡片组件 `<PredictionCard>`

新建 `frontend/src/components/PredictionCard.tsx`：

```tsx
type Props = {
  title: string;
  subtitle?: string;
  data: ChartPoint[];
  keys: string[];
  meta?: { volume?: number; outcomes?: number; updatedAt?: string };
};
```

内部布局：header (title + subtitle) → MultiLineChart `height={240}` → footer (meta)。

### 跟踪管理面板组件 `<TrackedMarketsPanel>`

新建 `frontend/src/components/TrackedMarketsPanel.tsx`：

- 顶部添加表单：`kind` SelectControl (`slug` / `tag`) + `identifier` TextInput + `display_name` TextInput + `添加` 按钮
- 下方 DataTable：列 = 类型 / identifier / 显示名 / 启用 (Toggle) / 操作 (删除)
- 用 `useMutation` + `queryClient.invalidateQueries(["prediction-tracked"])`

### API client (`frontend/src/api/client.ts`)

新增：
```ts
api.predictionTracked()
api.createPredictionTracked(payload)
api.updatePredictionTracked(id, payload)
api.deletePredictionTracked(id)
```

### CSS

在 `frontend/src/styles.css` 末尾追加：

```css
.prediction-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 12px;
}

.prediction-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--panel);
  padding: 12px;
}

.prediction-card-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.prediction-card-head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.prediction-card-head .muted-text {
  font-size: 11px;
}

.prediction-card-foot {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 11px;
  color: var(--muted);
}

@media (max-width: 1080px) {
  .prediction-grid {
    grid-template-columns: 1fr;
  }
}
```

`MultiLineChart` 在卡片内 `height={240}`，比原来的 320 短，但宽度也只有原来一半，整体比例反而更舒服。

## 错误处理

- 后端 POST 重复：返回 409 + JSON `{detail: "已存在"}`，前端 toast 提示
- 后端 PATCH/DELETE id 不存在：返回 404
- 前端添加表单：identifier 空 → 禁用按钮，不发请求
- 扫描器：现有 try/except 已经吞了无效 slug/tag，不需额外处理

## 测试

**后端：**
- `tests/test_tracked_market_seed.py`：seed 幂等（多次调用不重复）；不覆盖已有行的 `enabled`
- `tests/test_predictions_tracked_api.py`：CRUD 端点的快乐路径 + 409 / 404
- `tests/test_polymarket_source_db.py`：扫描器从 DB 读 enabled 行；`enabled=False` 的不出现在结果

**前端：**
- 已有 `vitest` 基础。本次新组件规模小，重点测：
  - `<PredictionCard>` 渲染 title/keys
  - `<TrackedMarketsPanel>` 添加按钮在 identifier 空时 disabled

## 迁移与回滚

- `database.py` 的 `Base.metadata.create_all()` 会自动建新表 — 现有部署一启动就有这张空表 + seed 数据
- 回滚 = 恢复 `PolymarketSource.fetch()` 读 config 的逻辑；DB 表保留但忽略

## 验收

1. 启动后 `tracked_markets` 表里能看到从 config 迁来的所有 slug + tag
2. 在 UI 添加 `kind=slug, identifier=will-trump-win-2028` 后下次扫描能拉到对应市场
3. 删除某行后该市场不再出现在新快照里（旧快照保留）
4. 浏览器 1920×1080 视口下，预测市场页能同时看见 ≥4 张图（每行 2 张，纵向 2 行）
5. `enabled=False` 切换后该市场不再被扫描
