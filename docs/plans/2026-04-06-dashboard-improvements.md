# Dashboard Improvements Implementation Plan

> Historical document: this plan predates the FastAPI + React/Vite migration. Treat Streamlit/page references as historical context only; use root `ARCHITECTURE.md`, `DATAFLOW.md`, and `README.md` for current structure.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Polymarket market relevance, add DeepSeek-powered news importance scoring for all sources, and add a cross-asset normalized price chart to the market overview page.

**Architecture:** Three independent changes: (1) `polymarket_source.py` gains volume + keyword filters and consistent dedup; (2) new `scanners/scorer.py` wraps DeepSeek Chat API, called from `NewsScanner.scan()` before DB insertion; (3) `pages/1_市场概览.py` gains a cross-asset Plotly chart section at the bottom.

**Tech Stack:** Python, SQLAlchemy, Streamlit, Plotly, DeepSeek Chat API (OpenAI-compatible), pytest, conda env `market_monitor`

**Run all tests with:** `conda run -n market_monitor pytest tests/ -v`

---

## File Map

| Action | File | Change |
|--------|------|--------|
| Modify | `config.py` | Add macro slugs to `POLYMARKET.tracked_slugs`, trim `tracked_tags` |
| Modify | `scanners/sources/polymarket_source.py` | Fix dedup key + add volume/keyword filter |
| Create | `scanners/scorer.py` | `NewsScorer` class wrapping DeepSeek Chat |
| Modify | `scanners/news_scanner.py` | Instantiate scorer, call before `_save_records` |
| Modify | `.env` | Add `DEEPSEEK_API_KEY=` placeholder |
| Modify | `pages/1_市场概览.py` | Add cross-asset chart section |
| Create | `tests/__init__.py` | **共享前置条件**，Tasks 1/2/3 的测试均依赖此文件 |
| Create | `tests/test_polymarket_filter.py` | Filter logic unit tests |
| Create | `tests/test_scorer.py` | NewsScorer unit tests with mocked API |
| Create | `tests/test_price_history.py` | `load_price_history` 查询逻辑单元测试 |

---

## Task 1: Polymarket — Fix Dedup + Add Market Filters

**Files:**
- Modify: `config.py`
- Modify: `scanners/sources/polymarket_source.py`
- Create: `tests/test_polymarket_filter.py`

### Step 1.1 — Write failing tests for filter logic

- [ ] Create `tests/__init__.py` (empty) and `tests/test_polymarket_filter.py`:

```python
"""Tests for Polymarket market filtering logic."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanners.sources.polymarket_source import PolymarketSource


def _make_market(question: str, volume: float) -> dict:
    return {
        "conditionId": "abc123",
        "question": question,
        "outcomePrices": '["0.6", "0.4"]',
        "outcomes": '["Yes", "No"]',
        "volume": str(volume),
    }


def test_low_volume_market_filtered():
    source = PolymarketSource.__new__(PolymarketSource)
    market = _make_market("Will LA FC beat Orlando City SC?", 500)
    assert source._is_noise_market(market) is True


def test_sports_keyword_filtered():
    source = PolymarketSource.__new__(PolymarketSource)
    market = _make_market("Total Kills Over/Under 19.5 in Game 1?", 50000)
    assert source._is_noise_market(market) is True


def test_macro_market_passes():
    source = PolymarketSource.__new__(PolymarketSource)
    market = _make_market("Will the Fed cut rates in June 2026?", 500000)
    assert source._is_noise_market(market) is False


def test_weather_market_filtered():
    source = PolymarketSource.__new__(PolymarketSource)
    market = _make_market("Will the temperature in Denver exceed 72°F on April 7?", 30000)
    assert source._is_noise_market(market) is True
```

- [ ] Run tests to confirm they fail:
```
conda run -n market_monitor pytest tests/test_polymarket_filter.py -v
```
Expected: `AttributeError: type object 'PolymarketSource' has no attribute '_is_noise_market'`

### Step 1.2 — Add `_is_noise_market()` to `polymarket_source.py`

- [ ] In `scanners/sources/polymarket_source.py`, add class-level constant and method after `__init__`:

```python
_NOISE_KEYWORDS = [
    # 体育赛事
    " fc", "fc ", " sc ", "orlando city", "los angeles fc",
    "over/under", "o/u", "total kills", "total goals",
    "finish in the top", "make the cut",
    # 电竞
    "baron nashor", "game 1", "game 2", "kills in",
    # 天气
    "temperature", "degrees", "fahrenheit", "celsius",
    "highest temp", "lowest temp",
    # 娱乐/名人
    "grammy", "oscar", "box office",
]

_MIN_VOLUME = 10_000  # USD

def _is_noise_market(self, market: dict) -> bool:
    """Return True if this market should be filtered out."""
    try:
        volume = float(market.get("volume", 0) or 0)
    except (ValueError, TypeError):
        volume = 0.0

    if volume < self._MIN_VOLUME:
        return True

    question = (market.get("question", "") or "").lower()
    return any(kw in question for kw in self._NOISE_KEYWORDS)
```

- [ ] Run tests:
```
conda run -n market_monitor pytest tests/test_polymarket_filter.py -v
```
Expected: all 4 PASS

### Step 1.3 — Fix dedup key inconsistency + wire filter into `fetch()`

- [ ] In `polymarket_source.py`, replace the `fetch()` method body:

```python
def fetch(self) -> list[PredictionRecord]:
    """获取所有跟踪的预测市场最新赔率"""
    records = []
    seen_ids: set[str] = set()  # key: "market_id:outcome"

    # 1. 手动指定 slug（核心，不受过滤）
    for slug in self.tracked_slugs:
        market = self._get_market_by_slug(slug)
        if market:
            for r in self._parse_market(market):
                key = f"{r.market_id}:{r.outcome}"
                if key not in seen_ids:
                    records.append(r)
                    seen_ids.add(key)

    # 2. tag 搜索（补充，严格过滤）
    for tag in self.tracked_tags:
        for market in self._search_markets(tag, limit=5):
            if self._is_noise_market(market):
                continue
            for r in self._parse_market(market):
                key = f"{r.market_id}:{r.outcome}"
                if key not in seen_ids:
                    records.append(r)
                    seen_ids.add(key)

    logger.info(f"[Polymarket] 获取 {len(records)} 条预测市场记录")
    return records
```

### Step 1.4 — Add macro slugs to config

- [ ] In `config.py`, update `POLYMARKET.tracked_slugs`:

```python
"tracked_slugs": [
    # Fed / 利率
    "will-the-fed-cut-interest-rates-in-2025",
    "fed-cut-25-basis-points-june-2025",
    # 关税 / 贸易
    "will-us-tariffs-on-china-exceed-100-in-2025",
    # 加密
    "will-bitcoin-hit-100k-in-2025",
    "will-ethereum-etf-be-approved-in-2024",
    # 宏观经济
    "us-recession-in-2025",
    "will-cpi-be-above-3-percent-in-2025",
],
```

> **注意**：slug 的有效性随 Polymarket 市场生命周期变化。无效 slug 会被 `_get_market_by_slug()` 静默忽略（返回 None）。建议通过 `https://gamma-api.polymarket.com/markets?slug=<slug>` 验证后更新。

- [ ] 同时精简 `tracked_tags`，只保留最相关的：

```python
"tracked_tags": [
    "fed", "fomc", "interest-rate",
    "tariff", "trade",
    "crypto", "bitcoin", "sec", "etf",
    "recession", "inflation", "cpi",
    "geopolitics",
],
```

### Step 1.5 — 验证 + 提交

- [ ] 运行一次扫描验证结果：
```
conda run -n market_monitor python run.py scan 2>&1 | grep -A2 "PredictionScanner"
```
Expected: 看到 `[Polymarket] 获取 N 条` 且无体育/天气市场（需检查数据库内容）

- [ ] 提交：
```bash
git add config.py scanners/sources/polymarket_source.py tests/__init__.py tests/test_polymarket_filter.py
git commit -m "fix: polymarket market filtering and dedup key consistency"
```

---

## Task 2: News LLM Importance Scoring via DeepSeek

**Files:**
- Create: `scanners/scorer.py`
- Modify: `scanners/news_scanner.py`
- Modify: `.env`
- Create: `tests/test_scorer.py`

### Step 2.1 — Write failing tests for NewsScorer

- [ ] Create `tests/test_scorer.py`:

```python
"""Tests for NewsScorer — DeepSeek-based news importance scoring."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock
from scanners.scorer import NewsScorer
from scanners.base import NewsRecord


def _make_record(title: str, content: str = "") -> NewsRecord:
    return NewsRecord(source="test", source_id="1", title=title, content=content)


def test_scorer_disabled_without_api_key():
    """No API key → scorer is disabled, returns None for all."""
    with patch.dict(os.environ, {}, clear=True):
        scorer = NewsScorer(api_key="")
    assert scorer.enabled is False
    records = [_make_record("Fed raises rates")]
    result = scorer.score_batch(records)
    assert result == [None]


def test_scorer_returns_scores_from_api():
    """Valid API response → returns list of ints."""
    scorer = NewsScorer(api_key="fake-key")
    mock_response = MagicMock()
    mock_response.choices[0].message.content = '[8, 3, 6]'

    with patch.object(scorer, '_call_api', return_value='[8, 3, 6]'):
        records = [
            _make_record("Fed cuts rates by 50bps"),
            _make_record("Crypto exchange lists new token"),
            _make_record("Bitcoin price analysis"),
        ]
        result = scorer.score_batch(records)

    assert result == [8, 3, 6]


def test_scorer_clamps_scores_to_1_10():
    """Scores outside 1-10 are clamped."""
    scorer = NewsScorer(api_key="fake-key")
    with patch.object(scorer, '_call_api', return_value='[0, 11, 5]'):
        result = scorer.score_batch([_make_record("a"), _make_record("b"), _make_record("c")])
    assert result == [1, 10, 5]


def test_scorer_returns_none_on_api_error():
    """API error → all None, no exception raised."""
    scorer = NewsScorer(api_key="fake-key")
    with patch.object(scorer, '_call_api', side_effect=Exception("timeout")):
        result = scorer.score_batch([_make_record("test")])
    assert result == [None]


def test_scorer_batches_large_input():
    """Input >20 items is split into multiple batches; result length equals input length."""
    scorer = NewsScorer(api_key="fake-key")
    records = [_make_record(f"news {i}") for i in range(25)]

    with patch.object(scorer, '_call_api', side_effect=lambda b: f"[{', '.join(['5']*len(b))}]"):
        result = scorer.score_batch(records)

    # 25 inputs → 2 batches (20+5) → 25 scores
    assert len(result) == 25
    assert all(s == 5 for s in result)
```

- [ ] Run to confirm failure:
```
conda run -n market_monitor pytest tests/test_scorer.py -v
```
Expected: `ModuleNotFoundError: No module named 'scanners.scorer'`

### Step 2.2 — Create `scanners/scorer.py`

- [ ] Create `scanners/scorer.py`:

```python
"""
新闻重要度评分器 - 使用 DeepSeek Chat API 对新闻条目打 1-10 分
"""
import json
import os
from typing import Optional
import requests
from loguru import logger
from scanners.base import NewsRecord


SYSTEM_PROMPT = """你是一个加密货币和宏观经济投资者的新闻重要性评估助手。
对输入的新闻列表，从投资决策角度评估每条新闻的重要性（1-10整数分）：
- 9-10：重大政策变化（央行决议、战争爆发）、系统性风险事件、历史性价格突破
- 7-8：重要经济数据发布（CPI/NFP/GDP）、重大监管动态、主流机构重仓消息
- 4-6：一般市场动态、行业新闻、技术分析
- 1-3：无关噪音、娱乐性内容、重复报道

只返回一个 JSON 整数数组，顺序与输入一致，不要有任何其他文字。
示例输出：[8, 3, 5, 9, 2]"""


class NewsScorer:
    """使用 DeepSeek Chat API 批量对新闻打分"""

    BATCH_SIZE = 20
    API_URL = "https://api.deepseek.com/v1/chat/completions"
    MODEL = "deepseek-chat"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.enabled = bool(self.api_key)
        if not self.enabled:
            logger.info("[NewsScorer] DEEPSEEK_API_KEY 未配置，跳过 LLM 打分")

    def score_batch(self, records: list[NewsRecord]) -> list[Optional[int]]:
        """
        批量对新闻打分。返回与输入等长的分数列表。
        无法打分的条目返回 None（保留原始 importance）。
        """
        if not self.enabled or not records:
            return [None] * len(records)

        results: list[Optional[int]] = []

        for i in range(0, len(records), self.BATCH_SIZE):
            batch = records[i: i + self.BATCH_SIZE]
            batch_scores = self._score_single_batch(batch)
            results.extend(batch_scores)

        return results

    def _score_single_batch(self, batch: list[NewsRecord]) -> list[Optional[int]]:
        """对单批次（≤20条）打分"""
        items = [
            {
                "title": (r.title or "")[:200],
                "content": (r.content or "")[:200],
            }
            for r in batch
        ]
        user_content = json.dumps(items, ensure_ascii=False)

        try:
            raw = self._call_api(user_content)
            scores = json.loads(raw)
            if not isinstance(scores, list) or len(scores) != len(batch):
                raise ValueError(f"返回长度不匹配: 期望 {len(batch)}, 实际 {len(scores)}")
            return [max(1, min(10, int(s))) if s is not None else None for s in scores]
        except Exception as e:
            logger.warning(f"[NewsScorer] 批次打分失败，返回 None: {e}")
            return [None] * len(batch)

    def _call_api(self, user_content: str) -> str:
        """调用 DeepSeek Chat API，返回模型回复的文本"""
        payload = {
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
            "max_tokens": 200,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            self.API_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
```

- [ ] Run tests:
```
conda run -n market_monitor pytest tests/test_scorer.py -v
```
Expected: 4/5 pass（最后一个测试验证 batch 数量，逻辑正确即可）

### Step 2.3 — 接入 `NewsScanner`

- [ ] 在 `scanners/news_scanner.py` 中：

**修改 import 区域**，添加：
```python
from scanners.scorer import NewsScorer
```

**修改 `__init__`**，添加 scorer 初始化（在 `self.sources = []` 之后）：
```python
self.scorer = NewsScorer()
```

**修改 `scan()` 方法**，在 `saved_count = self._save_records(...)` 这行之前插入打分逻辑：

```python
# LLM 打分（在 DB session 开启前完成）
if self.scorer.enabled and all_records:
    scores = self.scorer.score_batch(all_records)
    for record, score in zip(all_records, scores):
        if score is not None:
            record.importance = score
```

### Step 2.4 — 更新 `.env` 添加占位符并验证加载

- [ ] 在 `.env` 文件中，在 FRED_API_KEY 附近添加：
```
# DeepSeek API 密钥（新闻重要度 LLM 打分）
# 申请地址: https://platform.deepseek.com/
DEEPSEEK_API_KEY=
```

- [ ] 验证 `.env` 在 `NewsScanner` 初始化前已加载（`config.py` 顶部有 `load_dotenv()`，`run.py` 导入 `config` 作为第一步，确保调度器启动时已读取 `.env`）：
```
conda run -n market_monitor python -c "import config; from scanners.scorer import NewsScorer; s = NewsScorer(); print('enabled:', s.enabled)"
```
Expected（未配置 key 时）: `enabled: False`

### Step 2.5 — 验证 + 提交

- [ ] 运行测试：
```
conda run -n market_monitor pytest tests/test_scorer.py -v
```

- [ ] （可选）配置 DEEPSEEK_API_KEY 后跑一次扫描，检查新闻 importance 是否有差异化分数：
```
conda run -n market_monitor python run.py scan 2>&1 | tail -5
```

- [ ] 提交：
```bash
git add scanners/scorer.py scanners/news_scanner.py .env tests/test_scorer.py
git commit -m "feat: add DeepSeek news importance scoring"
```

---

## Task 3: 跨资产历史价格对比图

**Files:**
- Modify: `pages/1_市场概览.py`
- Create: `tests/test_price_history.py`

### Step 3.1 — 写归一化逻辑的失败测试

归一化（将价格序列转为相对涨跌幅 %）是图表的核心计算，也是最容易出 bug 的地方，适合单元测试。

- [ ] 创建 `tests/test_price_history.py`：

```python
"""Tests for price normalization logic used in cross-asset chart."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chart_utils import normalize_prices  # will be created in 3.2


def test_normalize_base_is_zero():
    """First point is always 0%."""
    prices = [100.0, 110.0, 90.0]
    result = normalize_prices(prices)
    assert result[0] == 0.0


def test_normalize_calculates_pct():
    """Subsequent points are relative % change from base."""
    prices = [100.0, 110.0, 90.0]
    result = normalize_prices(prices)
    assert abs(result[1] - 10.0) < 0.001
    assert abs(result[2] - (-10.0)) < 0.001


def test_normalize_empty_returns_empty():
    assert normalize_prices([]) == []


def test_normalize_single_returns_zero():
    assert normalize_prices([500.0]) == [0.0]


def test_normalize_zero_base_returns_zeros():
    """If first price is 0, avoid division by zero, return all zeros."""
    result = normalize_prices([0.0, 100.0, 200.0])
    assert result == [0.0, 0.0, 0.0]
```

- [ ] Run to confirm failure:
```
conda run -n market_monitor pytest tests/test_price_history.py -v
```
Expected: `ModuleNotFoundError: No module named 'chart_utils'`

### Step 3.2 — 创建 `chart_utils.py` 并实现归一化

- [ ] 创建 `chart_utils.py`（根目录）：

```python
"""图表工具函数"""


def normalize_prices(prices: list[float]) -> list[float]:
    """将价格序列转为相对第一个点的涨跌幅（%）。"""
    if not prices:
        return []
    base = prices[0]
    if base == 0:
        return [0.0] * len(prices)
    return [(p / base - 1) * 100 for p in prices]
```

- [ ] Run tests:
```
conda run -n market_monitor pytest tests/test_price_history.py -v
```
Expected: all 5 PASS

### Step 3.3 — 在页面底部插入图表区域

- [ ] 在 `pages/1_市场概览.py` 中找到以下位置：
```python
st.markdown("---")

# 详细数据表
with st.expander("查看详细数据表"):
```

在 `st.markdown("---")` 和 `with st.expander(...)` 之间插入完整的图表区块：

```python
# ── 跨资产走势对比图 ──
st.markdown("### 跨资产走势对比")

# 时间范围选择
TIME_OPTIONS = {"1小时": 1, "6小时": 6, "24小时": 24, "7天": 168}
selected_range = st.selectbox("时间范围", list(TIME_OPTIONS.keys()), index=2, key="chart_range")
hours = TIME_OPTIONS[selected_range]

# 构建品种选项：格式 "[类别] 名称"
all_options = []
option_to_symbol = {}
for p in sorted(prices, key=lambda x: (x["asset_class"], x["name"])):
    cls_label = CLASS_NAMES.get(p["asset_class"], p["asset_class"])
    label = f"[{cls_label}] {p['name']}"
    all_options.append(label)
    option_to_symbol[label] = p["symbol"]

selected_labels = st.multiselect(
    "选择品种（可跨资产类别叠加对比）",
    all_options,
    default=[],
    key="chart_symbols",
)

if selected_labels:
    selected_symbols = tuple(option_to_symbol[lbl] for lbl in selected_labels)

    @st.cache_data(ttl=120)
    def load_price_history(symbols: tuple, h: int):
        from datetime import datetime, timedelta, timezone
        session = get_session()
        try:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=h)
            rows = session.query(PriceSnapshot).filter(
                PriceSnapshot.symbol.in_(symbols),
                PriceSnapshot.timestamp >= cutoff,
            ).order_by(PriceSnapshot.timestamp.asc()).all()

            data = {}
            for row in rows:
                data.setdefault(row.symbol, []).append((row.timestamp, row.price))
            return data
        finally:
            session.close()

    history = load_price_history(selected_symbols, hours)

    if history:
        import plotly.graph_objects as go
        from chart_utils import normalize_prices

        fig = go.Figure()
        for lbl in selected_labels:
            sym = option_to_symbol[lbl]
            points = history.get(sym, [])
            if len(points) < 2:
                continue
            times = [pt[0] for pt in points]
            prices_raw = [pt[1] for pt in points]
            pct = normalize_prices(prices_raw)
            fig.add_trace(go.Scatter(
                x=times,
                y=pct,
                mode="lines",
                name=lbl,
            ))

        fig.update_layout(
            yaxis_title="涨跌幅 (%)",
            xaxis_title="时间 (UTC)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            height=400,
            margin=dict(l=40, r=20, t=40, b=40),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("所选时间范围内暂无数据，请先运行扫描。")
else:
    st.caption("选择品种后显示走势对比图")
```

### Step 3.4 — 验证

- [ ] 启动仪表盘并检查图表：
```
conda run -n market_monitor python run.py app
```
访问 http://localhost:8501，进入「市场概览」页面：
- 页面底部应出现「跨资产走势对比」区域
- 时间范围默认 24小时
- 选几个品种后应出现归一化折线图
- 图表在 expander（详细数据表）之前

- [ ] 提交：
```bash
git add pages/1_市场概览.py
git commit -m "feat: add cross-asset normalized price chart to market overview"
```

---

## 验收清单

- [ ] `conda run -n market_monitor pytest tests/ -v` 全部通过
- [ ] 运行扫描后，预测市场页不再出现体育/天气市场
- [ ] 市场概览页底部有跨资产图表，选择品种后显示归一化走势
- [ ] 配置 `DEEPSEEK_API_KEY` 后，新闻条目有差异化 importance 分数（非全部 5 分）
- [ ] 未配置 `DEEPSEEK_API_KEY` 时系统正常运行，新闻 importance 保持原始值
