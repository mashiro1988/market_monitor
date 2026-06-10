from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

import requests
from loguru import logger
from sqlalchemy.orm import Session

import config
from models.news import NewsItem, NewsPriceAnnotation
from models.price import PriceSnapshot
from schemas.annotations import (
    AnnotationCreateRequest,
    AnnotationDetail,
    AnnotationListItem,
    AnnotationResponse,
    AnnotationSymbol,
    AutoAnnotateBatchItem,
    AutoAnnotateBatchRequest,
    AutoAnnotateBatchResponse,
    AutoAnnotateRequest,
    AutoAnnotateResponse,
    ContextNewsResponse,
    PriceRuleSchema,
    PriceWindowSchema,
    ReferenceChange,
)
from services.news_service import to_news_schema
from services.time_utils import parse_datetime, timestamp_pair, utc_now_naive

TARGET_PRICE_SYMBOLS = ["BTC/USDT", "NQ=F"]

def _nearest_snapshot_any(rows: list[PriceSnapshot], target: datetime, tolerance_minutes: int) -> PriceSnapshot | None:
    """rows 里与 target 时间差最小且 ≤ 容差者（不限前后，区别于 _nearest_snapshot）。"""
    best = None
    best_delta = None
    for row in rows:
        delta = abs((row.timestamp - target).total_seconds())
        if delta > tolerance_minutes * 60:
            continue
        if best_delta is None or delta < best_delta:
            best, best_delta = row, delta
    return best


def _iter_reference_assets():
    """展开 config.ANNOTATION_REFERENCE_ASSETS：(symbol, label[, unit])，unit 缺省 "pct"。"""
    for entry in config.ANNOTATION_REFERENCE_ASSETS:
        sym, label = entry[0], entry[1]
        unit = entry[2] if len(entry) > 2 else "pct"
        yield sym, label, unit


def _reference_change_for_window(
    rows: list[PriceSnapshot], window_start: datetime, window_end: datetime, tolerance_minutes: int,
    unit: str = "pct",
) -> float | None:
    """同期变动：端点最近快照。unit=pct → (end-start)/start*100；unit=bp（收益率）→ (end-start)*100。
    任一端无数据 → None。"""
    s = _nearest_snapshot_any(rows, window_start, tolerance_minutes)
    e = _nearest_snapshot_any(rows, window_end, tolerance_minutes)
    if s is None or e is None or not s.price:
        return None
    if unit == "bp":
        return (e.price - s.price) * 100
    return (e.price - s.price) / abs(s.price) * 100


def _load_reference_rows(session: Session, cutoff: datetime) -> dict[str, list[PriceSnapshot]]:
    """一次性把所有「宏观同期对标」资产（config.ANNOTATION_REFERENCE_ASSETS）的快照捞出，按 symbol 分组。"""
    symbols = [entry[0] for entry in config.ANNOTATION_REFERENCE_ASSETS]
    if not symbols:
        return {}
    rows = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol.in_(symbols), PriceSnapshot.timestamp >= cutoff)
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    by_symbol: dict[str, list[PriceSnapshot]] = {sym: [] for sym in symbols}
    for row in rows:
        by_symbol.setdefault(row.symbol, []).append(row)
    return by_symbol


def _reference_changes_for_window(
    ref_rows: dict[str, list[PriceSnapshot]],
    window_start: datetime,
    window_end: datetime,
    tolerance_minutes: int,
    annotated_symbol: str,
) -> list[ReferenceChange]:
    """按 config.ANNOTATION_REFERENCE_ASSETS 逐个算同期变动；标注品种本身 → is_self（不对标自己）。"""
    out: list[ReferenceChange] = []
    for sym, label, unit in _iter_reference_assets():
        if sym == annotated_symbol:
            out.append(ReferenceChange(symbol=sym, label=label, pct=None, unit=unit, is_self=True))
            continue
        pct = _reference_change_for_window(ref_rows.get(sym, []), window_start, window_end, tolerance_minutes, unit)
        out.append(ReferenceChange(symbol=sym, label=label, pct=pct, unit=unit))
    return out


def _reference_changes_payload(refs: list[ReferenceChange]) -> dict[str, str | None]:
    """同期对标变动 → 喂给 reasoner 的紧凑 dict：{label: "+1.23%" / "+10.0bp"}；无数据 None；标注品种本身不列。"""
    out: dict[str, str | None] = {}
    for ref in refs:
        if ref.is_self:
            continue
        if ref.pct is None:
            out[ref.label] = None
        elif ref.unit == "bp":
            out[ref.label] = f"{ref.pct:+.1f}bp"
        else:
            out[ref.label] = f"{ref.pct:+.2f}%"
    return out


def _reference_changes_for_annotation(
    session: Session,
    window_start: datetime,
    window_end: datetime,
    symbol: str,
    ref_rows: dict[str, list[PriceSnapshot]] | None = None,
) -> dict[str, str | None]:
    """单个标注窗口的同期对标涨跌（payload 形式）。ref_rows 不传则按窗口起点现查。"""
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)
    if ref_rows is None:
        ref_rows = _load_reference_rows(session, window_start - timedelta(minutes=tolerance_minutes + 5))
    refs = _reference_changes_for_window(ref_rows, window_start, window_end, tolerance_minutes, symbol)
    return _reference_changes_payload(refs)

# 每个标注窗口前后取的候选新闻范围：向前 15 分钟，向后 30 分钟。
# 与 upsert_annotation 写入 context_start / context_end 的偏移保持一致。
CONTEXT_PRE_MINUTES_DEFAULT = 15
CONTEXT_POST_MINUTES_DEFAULT = 30


def _annotation_news_sources() -> list[str]:
    """标注上下文 / 自动标注 候选新闻的源白名单——只读取在 `config.NEWS_SOURCES` 里启用的。
    切换 / 增减英文源（CNBC / Reuters 等）时只改 config，不动业务代码。"""
    return [k for k, v in config.NEWS_SOURCES.items() if v.get("enabled")]

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

AUTO_ANNOTATE_SYSTEM_PROMPT = """你是一名买方量化研究员，专门分析单一资产在短时间窗口内的价格异动是否由特定新闻事件触发。

你将收到：
1. 一段价格异动窗口的元数据（symbol、起止时间、价格变化百分比、阈值，以及同期对标品种涨跌 reference_changes）。
2. 该窗口前后一段时间内的候选新闻条目（含 id、北京时间、来源、LLM 评分、标题、内容片段）。

**首要原则：宁可空选，不要乱选。**
- 你的默认答案是 `no_clear_news=true, selected_news_ids=[]`。**只有当某条新闻能给出强而具体的因果链条**（"X 事件直接导致 symbol 价格 Y"），才把它选进来。
- 看到模糊关联、事后总结、行情综述、宏观背景文章、与该 symbol 仅有间接联系的新闻，**全部不选**。**唯一例外**见下方 reference_changes 段：被跨资产签名确认的重大宏观 / 地缘**突发事件**，不算"间接联系"。
- 没把握时，**选 no_clear_news=true**。漏选远比误选可接受 —— 这份数据要拿去训练模型，假阳性会污染训练集。

**关于 LLM 评分（candidates 里的 llm_score）的处理**：
- 这个分数不可靠，**不要**把它作为筛选依据。8 分新闻可能与本次异动毫无关系，4 分新闻反而可能是真正触发。
- 完全基于新闻**内容本身**（标题 + 摘要）判断与本窗口异动的因果关系。

**关于同期对标品种涨跌（window.reference_changes）**：
- 窗口元数据附带同一时段宏观对标品种（纳指 / 原油 / 黄金 / 美债10Y / 美元指数 / BTC 等）的变动；null 表示该品种休市或无数据。美债10Y 以**基点**表示（+10.0bp = 收益率上行 0.10 个百分点），其余为涨跌百分比。
- 用跨资产签名判断异动性质，再决定在候选里找哪类新闻（签名里的「股指」判别同样适用于 BTC 等风险资产标的）：
  - 标的与对标品种同向联动（如纳指、BTC 同跌）→ 宏观共振，优先在候选里找宏观、地缘、政策类**突发事件**；
  - 股指跌 + 美债10Y 收益率**上行** → 利率冲击（通胀数据超预期、央行鹰派）→ 找数据公布 / 央行决议 / 官员讲话；
  - 股指跌 + 美债10Y 收益率**下行** → 避险 / 衰退担忧 → 找地缘、金融风险、增长恶化类突发（黄金上涨可作佐证，但**不是必要条件**）；
  - 股指下跌 + **原油上涨**，是地缘冲突 / 供给冲击的**核心**签名（军事行动、空袭、制裁、油轮遇袭等）。注意：黄金在地缘冲突中常涨但**不必然**——冲突持久化后市场会钝化、强美元或流动性挤兑也会压制金价，**黄金没涨不能用来否定地缘归因**；股指与原油同跌偏向衰退 / 需求担忧；
  - 美元指数急升 + BTC/股指跌 → 美元流动性收紧 / 避险买美元；
  - 对标品种基本走平、只有标的自己异动 → 找该资产**专属**新闻（如 BTC 的 ETF / 链上 / 监管 / 交易所事件），此时宏观新闻不可选。
- 突发地缘 / 军事 / 制裁类新闻通常**不会提到 symbol 本身**：只要事件足够重大、发布时间与窗口吻合、且跨资产签名一致，就应视为对股指 / 风险资产的**直接**触发选入，不要因为"新闻没提该品种"而当作间接关联排除。注意区分：**新发生的事件**（首次交火、首次空袭）可选；对既有局势的回顾、分析、评论文章仍然不选。
- 标注品种本身若在对标清单里（如标注纳指 NQ=F 的窗口），它**不会出现在 reference_changes 里**——其涨跌就是窗口自身的 change_pct，键缺失不是数据故障。
- 当 reference_changes 的值**全部为 null**（对标品种集体休市，周末 / 假日的加密货币窗口很常见）：视为没有任何跨资产信息，**忽略本段全部指引（包括上面的地缘例外）**，回退到首要原则，按默认保守标准判断。

**关于新闻时间戳的关键说明**：候选新闻的 time_bj 是新闻**发布时间**，不是事件发生时间。中文财经源（华尔街见闻、jin10）和翻译类英文源经常把欧美时段的事件延迟 5-30 分钟才推送到国内。所以：
- 窗口结束后 0-30 分钟内出现的新闻，如果其内容描述的事件**明显发生在窗口期间或之前**（如 FOMC 决议、CPI 公布、美股盘中事件），仍视为有效触发可以选中。
- 只有当新闻内容明显描述窗口结束之后才发生的事件（如另一场会议、次日数据、当晚才出的声明），才把它当作"窗口后新闻"忽略。
- 当不确定事件何时发生时，倾向于把它视为发布延迟、可作为触发。

**关于长窗口（多段合并的事件窗口）**：窗口可能由多段连续同向异动合并而成，跨度可达数小时。此时：
- 触发新闻经常出现在窗口**中段**，驱动的是后半段的延续或加速——**不要**仅因"新闻发布时间晚于窗口起点、解释不了行情起点"而排除；只要新闻发布后价格朝同方向延续/加速、事件量级与跨资产签名相符，就可作为触发选入。
- 同窗口内同时出现**升级与缓和**两类消息时（地缘场景常见），以与价格方向一致的一侧为候选；方向相反的消息存在，不构成排除理由——市场对两类消息的定价权重本就不同。
- 同一事件的多条连续快讯（同一讲话/同一袭击的不同来源、不同侧面）属于一个事件簇，选其中信息量最大的 1-3 条即可，不必全选。

判断步骤：
1. 先看 reference_changes，按上面的签名表判断本次异动更像「宏观 / 风险事件共振」还是「标的独立行情」（全为 null 则跳过本步）；
2. 再扫一遍候选新闻，列出**与该 symbol 在所述时段内有强直接因果关系**的新闻——第 1 步判为共振时，重点核对宏观 / 地缘 / 政策类突发事件；判为独立行情时，只考虑该资产专属新闻；
3. 如果第 2 步结果为空 → `no_clear_news=true, selected_news_ids=[]`，summary 写"无明显因果新闻"；
4. 否则把这些新闻 id 放进 selected_news_ids，summary 用一句话写清楚因果链条。

只返回 JSON，不要 Markdown 标记，不要解释性正文。格式：
{
  "selected_news_ids": [int, ...],   // 必须是候选新闻列表里实际存在的 id；空选则为 []
  "no_clear_news": bool,             // 没强因果就 true
  "summary": "不超过 80 字的因果归因结论；no_clear_news=true 时写'无明显因果新闻'即可"
}
"""


AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT = """你是一名买方量化研究员。你将一次性收到一组价格异动窗口及其各自的候选新闻列表，
对**每个**窗口分别判断哪些新闻是因果触发。

输入结构：
{
  "windows": [
    {
      "id": int,                                  // 窗口编号，必须在返回中按相同 id 引用
      "symbol", "start_utc", "end_utc",
      "threshold_pct", "price_start", "price_end", "change_pct",
      "reference_changes": { "纳指": "+0.42%", "原油": "-1.10%", "美债10Y": "+6.0bp", ... },   // 同期对标品种变动；null=休市/无数据
      "candidates": [{ "id": int, "time_bj", "source", "llm_score", ... }, ...]
    },
    ...
  ]
}

**首要原则：宁可空选，不要乱选。**
- 每个窗口的默认答案是 `no_clear_news=true, selected_news_ids=[]`。**只有当某条新闻能给出强而具体的因果链条**（"X 事件直接导致 symbol 价格 Y"），才把它选进来。
- 模糊关联、事后总结、行情综述、宏观背景、与该 symbol 仅有间接联系的新闻，**全部不选**。**唯一例外**见下方 reference_changes 段：被跨资产签名确认的重大宏观 / 地缘**突发事件**，不算"间接联系"。
- 没把握时，**选 no_clear_news=true**。漏选远比误选可接受 —— 这份数据要拿去训练模型，假阳性会严重污染训练集。
- 不要为了"每个窗口都给点东西"而硬选 —— 一个 batch 里大多数窗口结果可能都是 no_clear_news=true，这是正常的。

**关于 LLM 评分（candidates 里的 llm_score）的处理**：
- 这个分数不可靠，**不要**把它作为筛选依据。8 分新闻可能与本次异动毫无关系，4 分新闻反而可能是真正触发。
- 完全基于新闻**内容本身**（标题 + 摘要）判断与本窗口异动的因果关系。

**关于同期对标品种涨跌（每个 window 的 reference_changes）**：
- 每个窗口附带同一时段宏观对标品种（纳指 / 原油 / 黄金 / 美债10Y / 美元指数 / BTC 等）的变动；null 表示该品种休市或无数据。美债10Y 以**基点**表示（+10.0bp = 收益率上行 0.10 个百分点），其余为涨跌百分比。
- 用跨资产签名判断该窗口异动性质，再决定在候选里找哪类新闻（签名里的「股指」判别同样适用于 BTC 等风险资产标的）：
  - 标的与对标品种同向联动（如纳指、BTC 同跌）→ 宏观共振，优先在该窗口候选里找宏观、地缘、政策类**突发事件**；
  - 股指跌 + 美债10Y 收益率**上行** → 利率冲击（通胀数据超预期、央行鹰派）→ 找数据公布 / 央行决议 / 官员讲话；
  - 股指跌 + 美债10Y 收益率**下行** → 避险 / 衰退担忧 → 找地缘、金融风险、增长恶化类突发（黄金上涨可作佐证，但**不是必要条件**）；
  - 股指下跌 + **原油上涨**，是地缘冲突 / 供给冲击的**核心**签名（军事行动、空袭、制裁、油轮遇袭等）。注意：黄金在地缘冲突中常涨但**不必然**——冲突持久化后市场会钝化、强美元或流动性挤兑也会压制金价，**黄金没涨不能用来否定地缘归因**；股指与原油同跌偏向衰退 / 需求担忧；
  - 美元指数急升 + BTC/股指跌 → 美元流动性收紧 / 避险买美元；
  - 对标品种基本走平、只有标的自己异动 → 找该资产**专属**新闻（如 BTC 的 ETF / 链上 / 监管 / 交易所事件），此时宏观新闻不可选。
- 突发地缘 / 军事 / 制裁类新闻通常**不会提到 symbol 本身**：只要事件足够重大、发布时间与窗口吻合、且跨资产签名一致，就应视为对股指 / 风险资产的**直接**触发选入，不要因为"新闻没提该品种"而当作间接关联排除。注意区分：**新发生的事件**（首次交火、首次空袭）可选；对既有局势的回顾、分析、评论文章仍然不选。
- 标注品种本身若在对标清单里（如标注纳指 NQ=F 的窗口），它**不会出现在 reference_changes 里**——其涨跌就是窗口自身的 change_pct，键缺失不是数据故障。
- 当 reference_changes 的值**全部为 null**（对标品种集体休市，周末 / 假日的加密货币窗口很常见）：视为没有任何跨资产信息，**忽略本段全部指引（包括上面的地缘例外）**，回退到首要原则，按默认保守标准判断。

**关于新闻时间戳的关键说明**：候选新闻的 time_bj 是新闻**发布时间**，不是事件发生时间。中文财经源（华尔街见闻、jin10）和翻译类英文源经常把欧美时段的事件延迟 5-30 分钟才推送到国内。所以：
- 窗口结束后 0-30 分钟内出现的新闻，如果其内容描述的事件**明显发生在窗口期间或之前**（如 FOMC 决议、CPI 公布、美股盘中事件），仍视为有效触发可以选中。
- 只有当新闻内容明显描述窗口结束之后才发生的事件（如另一场会议、次日数据、当晚才出的声明），才把它当作"窗口后新闻"忽略。
- 当不确定事件何时发生时，倾向于把它视为发布延迟、可作为触发。

**关于长窗口（多段合并的事件窗口）**：窗口可能由多段连续同向异动合并而成，跨度可达数小时。此时：
- 触发新闻经常出现在窗口**中段**，驱动的是后半段的延续或加速——**不要**仅因"新闻发布时间晚于窗口起点、解释不了行情起点"而排除；只要新闻发布后价格朝同方向延续/加速、事件量级与跨资产签名相符，就可作为触发选入。
- 同窗口内同时出现**升级与缓和**两类消息时（地缘场景常见），以与价格方向一致的一侧为候选；方向相反的消息存在，不构成排除理由——市场对两类消息的定价权重本就不同。
- 同一事件的多条连续快讯（同一讲话/同一袭击的不同来源、不同侧面）属于一个事件簇，选其中信息量最大的 1-3 条即可，不必全选。

每个窗口的判断步骤：
1. 先看该窗口的 reference_changes，按上面的签名表判断异动更像「宏观 / 风险事件共振」还是「标的独立行情」（全为 null 则跳过本步）；
2. 再扫一遍该窗口的候选新闻，列出**与该 symbol 在所述时段内有强直接因果关系**的新闻——第 1 步判为共振时，重点核对宏观 / 地缘 / 政策类突发事件；判为独立行情时，只考虑该资产专属新闻；
3. 如果第 2 步结果为空 → `no_clear_news=true, selected_news_ids=[]`，summary 写"无明显因果新闻"；
4. 否则把这些新闻 id 放进 selected_news_ids，summary 用一句话写清楚因果链条。

每个 window 必须在输出 items 中**有且只有一项**，window_id 与输入的 id 严格对应。每个 item 必须独立给出 reasoning，不要写"同上"或跨窗口共用。

只返回 JSON：
{
  "items": [
    {
      "window_id": int,
      "selected_news_ids": [int, ...],   // 只能引用对应 window 自己 candidates 列表里的 id；空选则为 []
      "no_clear_news": bool,             // 没强因果就 true
      "summary": "不超过 80 字的归因结论；no_clear_news=true 时写'无明显因果新闻'即可",
      "reasoning": "150-250 字解释：为什么选这些新闻 / 为什么本窗口没有强因果新闻 / 排除了哪些误判候选。该字段只属于这个 window，禁止跨窗口复用。"
    },
    ...
  ]
}
"""

# 一次批量调用最多塞 N 个窗口，避免上下文过长把 reasoning 时间和成本拉爆。
AUTO_ANNOTATE_BATCH_LIMIT = 10


def load_alert_price_rules() -> list[PriceRuleSchema]:
    rules: list[PriceRuleSchema] = []
    for rule in config.ALERT_RULES:
        if not rule.get("enabled", True) or rule.get("rule_type") != "price_change":
            continue
        params = rule.get("params", {})
        symbol = params.get("symbol")
        threshold = params.get("threshold_pct")
        window_minutes = params.get("window_minutes")
        if symbol in TARGET_PRICE_SYMBOLS and threshold is not None:
            rules.append(
                PriceRuleSchema(
                    symbol=symbol,
                    threshold_pct=float(threshold),
                    window_minutes=int(window_minutes or config.SCAN_INTERVALS["price"]),
                )
            )
    return rules


def load_symbols(session: Session, hours: int = 72) -> list[AnnotationSymbol]:
    cutoff = utc_now_naive() - timedelta(hours=max(1, min(int(hours or 72), 24 * 30)))
    rule_symbols = {rule.symbol for rule in load_alert_price_rules()}
    rows = (
        session.query(PriceSnapshot.symbol, PriceSnapshot.name, PriceSnapshot.asset_class)
        .filter(PriceSnapshot.timestamp >= cutoff, PriceSnapshot.symbol.in_(list(rule_symbols or TARGET_PRICE_SYMBOLS)))
        .distinct()
        .order_by(PriceSnapshot.asset_class, PriceSnapshot.symbol)
        .all()
    )
    return [AnnotationSymbol(symbol=row.symbol, name=row.name, asset_class=row.asset_class) for row in rows]


def _nearest_snapshot(rows: list[PriceSnapshot], target_time: datetime, before_time: datetime, tolerance_minutes: int) -> PriceSnapshot | None:
    candidates = [
        row for row in rows
        if row.timestamp < before_time
        if abs((row.timestamp - target_time).total_seconds()) <= tolerance_minutes * 60
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs((row.timestamp - target_time).total_seconds()))


def load_price_windows(
    session: Session,
    symbol: str,
    hours: int,
    threshold_pct: float | None = None,
    window_minutes: int | None = None,
) -> list[PriceWindowSchema]:
    rule_map = {rule.symbol: rule for rule in load_alert_price_rules()}
    rule = rule_map.get(symbol)
    if rule is None and (threshold_pct is None or window_minutes is None):
        return []
    threshold_pct = float(threshold_pct if threshold_pct is not None else rule.threshold_pct)
    window_minutes = int(window_minutes if window_minutes is not None else rule.window_minutes)
    hours = max(1, min(int(hours or 72), 24 * 30))
    cutoff = utc_now_naive() - timedelta(hours=hours, minutes=window_minutes + 10)
    rows = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp >= cutoff)
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    display_cutoff = utc_now_naive() - timedelta(hours=hours)
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)
    ref_rows = _load_reference_rows(session, cutoff)

    # 一次性把这个 symbol 在显示窗口里的已有标注全部捞出来，按 (window_start, window_end) 建查找表。
    annotation_rows = (
        session.query(NewsPriceAnnotation.id, NewsPriceAnnotation.window_start, NewsPriceAnnotation.window_end)
        .filter(
            NewsPriceAnnotation.symbol == symbol,
            NewsPriceAnnotation.window_end >= display_cutoff,
        )
        .all()
    )
    annotation_index: dict[tuple[datetime, datetime], int] = {
        (row.window_start, row.window_end): row.id for row in annotation_rows
    }

    # Step 1：扫快照，收集超阈值触发；保留原始 datetime 供合并用。
    triggers: list[dict] = []
    for current in rows:
        if current.timestamp < display_cutoff:
            continue
        baseline_time = current.timestamp - timedelta(minutes=window_minutes)
        baseline = _nearest_snapshot(rows, baseline_time, current.timestamp, tolerance_minutes)
        if baseline is None or not baseline.price:
            continue
        change_pct = ((current.price - baseline.price) / abs(baseline.price)) * 100
        if abs(change_pct) < threshold_pct:
            continue
        triggers.append({
            "start_dt": baseline.timestamp,
            "end_dt": current.timestamp,
            "price_start": baseline.price,
            "price_end": current.price,
            "sign": 1 if change_pct >= 0 else -1,
            "asset_class": current.asset_class,
            "name": current.name,
        })

    if not triggers:
        return []

    # Step 2：按 window_end 升序，把同方向、相邻段静默间隔 ≤ merge_gap 的触发聚成一个事件。
    triggers.sort(key=lambda t: t["end_dt"])
    merge_gap = timedelta(minutes=max(1, int(getattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 60))))
    events: list[list[dict]] = []
    for t in triggers:
        if (
            events
            and events[-1][-1]["sign"] == t["sign"]
            and (t["start_dt"] - events[-1][-1]["end_dt"]) <= merge_gap
        ):
            events[-1].append(t)
        else:
            events.append([t])

    # Step 3：每个事件合成一个跨段窗口（净 + 峰值 + 振幅 + 段数）。
    windows: list[tuple[datetime, PriceWindowSchema]] = []
    for ev in events:
        first, last = ev[0], ev[-1]
        w_start, w_end = first["start_dt"], last["end_dt"]
        p_start, p_end = first["price_start"], last["price_end"]
        if not p_start:
            continue
        net_pct = (p_end - p_start) / abs(p_start) * 100
        windows.append((w_end, PriceWindowSchema(
            symbol=symbol,
            asset_class=first["asset_class"],
            name=first["name"],
            window_start=timestamp_pair(w_start),
            window_end=timestamp_pair(w_end),
            configured_window_minutes=window_minutes,
            actual_window_minutes=round((w_end - w_start).total_seconds() / 60, 1),
            price_start=p_start,
            price_end=p_end,
            change_pct=net_pct,
            segment_count=len(ev),
            annotation_id=annotation_index.get((w_start, w_end)),
            is_primary=True,
            references=_reference_changes_for_window(ref_rows, w_start, w_end, tolerance_minutes, symbol),
        )))

    # 最新事件在前；截断 200。
    windows.sort(key=lambda t: t[0], reverse=True)
    return [w for _, w in windows][:200]


def load_context_news(session: Session, context_start: datetime, context_end: datetime) -> ContextNewsResponse:
    rows = (
        session.query(NewsItem)
        .filter(
            NewsItem.source.in_(_annotation_news_sources()),
            NewsItem.timestamp >= context_start,
            NewsItem.timestamp <= context_end,
        )
        .order_by(NewsItem.timestamp.asc())
        .all()
    )
    return ContextNewsResponse(items=[to_news_schema(row) for row in rows])


def load_context_news_for_window(
    session: Session,
    window_start_utc: str,
    window_end_utc: str,
    pre_minutes: int = CONTEXT_PRE_MINUTES_DEFAULT,
    post_minutes: int = CONTEXT_POST_MINUTES_DEFAULT,
) -> ContextNewsResponse:
    start = parse_datetime(window_start_utc)
    end = parse_datetime(window_end_utc)
    if start is None or end is None:
        return ContextNewsResponse(items=[])
    return load_context_news(
        session,
        start - timedelta(minutes=pre_minutes),
        end + timedelta(minutes=post_minutes),
    )


def _find_window_snapshot(session: Session, symbol: str, timestamp_value: datetime) -> PriceSnapshot | None:
    return (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp == timestamp_value)
        .first()
    )


def upsert_annotation(session: Session, request: AnnotationCreateRequest) -> AnnotationResponse:
    window_start = parse_datetime(request.window_start_utc)
    window_end = parse_datetime(request.window_end_utc)
    if window_start is None or window_end is None:
        raise ValueError("window_start_utc/window_end_utc 不能为空")

    start_snapshot = _find_window_snapshot(session, request.symbol, window_start)
    end_snapshot = _find_window_snapshot(session, request.symbol, window_end)
    if end_snapshot is None:
        raise ValueError("找不到窗口终点价格快照")
    if start_snapshot is None:
        raise ValueError("找不到窗口起点价格快照")

    existing = (
        session.query(NewsPriceAnnotation)
        .filter(
            NewsPriceAnnotation.symbol == request.symbol,
            NewsPriceAnnotation.window_start == window_start,
            NewsPriceAnnotation.window_end == window_end,
        )
        .first()
    )
    if existing is None:
        existing = NewsPriceAnnotation(
            symbol=request.symbol,
            window_start=window_start,
            window_end=window_end,
        )
        session.add(existing)

    existing.asset_class = end_snapshot.asset_class
    existing.context_start = window_start - timedelta(minutes=CONTEXT_PRE_MINUTES_DEFAULT)
    existing.context_end = window_end + timedelta(minutes=CONTEXT_POST_MINUTES_DEFAULT)
    existing.threshold_pct = request.threshold_pct
    existing.price_start = start_snapshot.price
    existing.price_end = end_snapshot.price
    existing.change_pct = ((end_snapshot.price - start_snapshot.price) / abs(start_snapshot.price)) * 100 if start_snapshot.price else None
    existing.causal_news_ids = json.dumps(request.selected_news_ids, ensure_ascii=False)
    if request.candidate_news_ids is not None:
        existing.candidate_news_ids = json.dumps(request.candidate_news_ids, ensure_ascii=False)
    existing.no_clear_news = request.no_clear_news
    existing.notes = (request.notes or "").strip() or None
    existing.labeler = (request.labeler or "").strip() or None
    if request.auto_reasoning is not None:
        existing.auto_reasoning = request.auto_reasoning.strip() or None
    if request.auto_summary is not None:
        existing.auto_summary = request.auto_summary.strip() or None
    existing.updated_at = utc_now_naive()
    session.commit()
    return AnnotationResponse(id=existing.id)


def _parse_news_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [int(item) for item in value if isinstance(item, (int, float, str)) and str(item).strip().lstrip("-").isdigit()]


def get_annotation_detail(session: Session, annotation_id: int) -> AnnotationDetail:
    row = session.query(NewsPriceAnnotation).filter(NewsPriceAnnotation.id == annotation_id).first()
    if row is None:
        raise ValueError(f"标注 #{annotation_id} 不存在")
    selected_ids = _parse_news_ids(row.causal_news_ids)
    selected_news: list = []
    if selected_ids:
        news_rows = session.query(NewsItem).filter(NewsItem.id.in_(selected_ids)).all()
        # 保持前端展示顺序与原始 selected_ids 一致。
        by_id = {item.id: item for item in news_rows}
        ordered = [by_id[i] for i in selected_ids if i in by_id]
        selected_news = [to_news_schema(item) for item in ordered]
    return AnnotationDetail(
        id=row.id,
        symbol=row.symbol,
        asset_class=row.asset_class,
        window_start=timestamp_pair(row.window_start),
        window_end=timestamp_pair(row.window_end),
        context_start=timestamp_pair(row.context_start),
        context_end=timestamp_pair(row.context_end),
        threshold_pct=row.threshold_pct,
        price_start=row.price_start,
        price_end=row.price_end,
        change_pct=row.change_pct,
        selected_news_ids=selected_ids,
        selected_news=selected_news,
        candidate_news_ids=_parse_news_ids(row.candidate_news_ids),
        no_clear_news=bool(row.no_clear_news),
        notes=row.notes,
        labeler=row.labeler,
        auto_reasoning=row.auto_reasoning,
        auto_summary=row.auto_summary,
        created_at=timestamp_pair(row.created_at),
        updated_at=timestamp_pair(row.updated_at),
    )


def list_annotations(session: Session, symbol: str | None, hours: int) -> list[AnnotationListItem]:
    """已标注的轻量列表，按 window_end 倒序。symbol 为空则不过滤。"""
    hours = max(1, min(int(hours or 72), 24 * 30))
    cutoff = utc_now_naive() - timedelta(hours=hours)
    query = session.query(NewsPriceAnnotation).filter(NewsPriceAnnotation.window_end >= cutoff)
    if symbol:
        query = query.filter(NewsPriceAnnotation.symbol == symbol)
    rows = query.order_by(NewsPriceAnnotation.window_end.desc()).limit(500).all()
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)
    if rows:
        earliest = min(r.window_start for r in rows)
        ref_rows = _load_reference_rows(session, earliest - timedelta(minutes=tolerance_minutes + 5))
    else:
        ref_rows = {}
    items: list[AnnotationListItem] = []
    for row in rows:
        selected_ids = _parse_news_ids(row.causal_news_ids)
        items.append(AnnotationListItem(
            id=row.id,
            symbol=row.symbol,
            asset_class=row.asset_class,
            window_start=timestamp_pair(row.window_start),
            window_end=timestamp_pair(row.window_end),
            change_pct=row.change_pct,
            references=_reference_changes_for_window(ref_rows, row.window_start, row.window_end, tolerance_minutes, row.symbol),
            no_clear_news=bool(row.no_clear_news),
            selected_count=len(selected_ids),
            labeler=row.labeler,
            notes=row.notes,
            created_at=timestamp_pair(row.created_at),
            updated_at=timestamp_pair(row.updated_at),
        ))
    return items


def delete_annotation(session: Session, annotation_id: int) -> int:
    row = session.query(NewsPriceAnnotation).filter(NewsPriceAnnotation.id == annotation_id).first()
    if row is None:
        raise ValueError(f"标注 #{annotation_id} 不存在")
    session.delete(row)
    session.commit()
    return annotation_id


def _build_auto_annotate_user_payload(
    request: AutoAnnotateRequest,
    candidate_news,
    price_start: float | None,
    price_end: float | None,
    change_pct: float | None,
    reference_changes: dict[str, str | None] | None = None,
) -> str:
    """组装喂给 reasoner 的用户消息。控制总长度，避免 token 超标。"""
    items = []
    for row in candidate_news:
        items.append({
            "id": row.id,
            "time_bj": (row.timestamp + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if row.timestamp else None,
            "source": row.source,
            "llm_score": row.llm_importance,
            "jin10_important": bool(row.importance) if row.source == "jin10" else None,
            "title": (row.title or "")[:160],
            "content": (row.content or "")[:300],
        })

    body = {
        "window": {
            "symbol": request.symbol,
            "start_utc": request.window_start_utc,
            "end_utc": request.window_end_utc,
            "threshold_pct": request.threshold_pct,
            "price_start": price_start,
            "price_end": price_end,
            "change_pct": change_pct,
            "reference_changes": reference_changes or {},
        },
        "candidate_news": items,
    }
    return f"共 {len(items)} 条候选新闻。\n{json.dumps(body, ensure_ascii=False)}"


def _call_deepseek_reasoner(user_content: str) -> tuple[str, str, float]:
    """调 DeepSeek v4-pro thinking 模式，返回 (content, reasoning_content, duration_seconds)。"""
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法调用自动标注")

    payload = {
        "model": config.DEEPSEEK_REASONER_MODEL,
        "messages": [
            {"role": "system", "content": AUTO_ANNOTATE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "thinking": {
            "type": "enabled",
            "reasoning_effort": config.DEEPSEEK_REASONER_EFFORT,
        },
        "response_format": {"type": "json_object"},
        "max_tokens": 4000,
    }
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    started = time.monotonic()
    resp = requests.post(
        DEEPSEEK_API_URL,
        json=payload,
        headers=headers,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_REASONER_READ_TIMEOUT),
    )
    duration = time.monotonic() - started

    if resp.status_code >= 400:
        preview = resp.text[:300].replace("\n", " ")
        raise RuntimeError(f"DeepSeek 返回 {resp.status_code}: {preview}")

    body = resp.json()
    message = body["choices"][0].get("message", {})
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning_content") or "").strip()
    if not content:
        raise RuntimeError(f"DeepSeek 返回空 content（reasoning 预览: {reasoning[:200]}）")
    return content, reasoning, duration


def _parse_auto_annotate_response(raw: str, valid_ids: set[int]) -> tuple[list[int], bool, str]:
    """从 reasoner 的 JSON 输出解析 selected_news_ids / no_clear_news / summary。
    selected_news_ids 必须是 valid_ids 子集，过滤幻觉。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"reasoner 返回非 JSON: {text[:200]}")
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError(f"reasoner 返回顶层不是对象: {type(data)}")

    raw_ids = data.get("selected_news_ids") or []
    selected: list[int] = []
    if isinstance(raw_ids, list):
        seen: set[int] = set()
        for item in raw_ids:
            try:
                value = int(item)
            except (TypeError, ValueError):
                continue
            if value in valid_ids and value not in seen:
                selected.append(value)
                seen.add(value)

    no_clear_news = bool(data.get("no_clear_news"))
    summary = (data.get("summary") or "")
    if not isinstance(summary, str):
        summary = str(summary)
    summary = summary.strip()[:240]

    return selected, no_clear_news, summary


def auto_annotate(session: Session, request: AutoAnnotateRequest) -> AutoAnnotateResponse:
    """**只调用模型，不写库**。前端拿到结果后由用户 review，再调 POST /api/annotations 保存。"""
    window_start = parse_datetime(request.window_start_utc)
    window_end = parse_datetime(request.window_end_utc)
    if window_start is None or window_end is None:
        raise ValueError("window_start_utc/window_end_utc 不能为空")

    start_snapshot = _find_window_snapshot(session, request.symbol, window_start)
    end_snapshot = _find_window_snapshot(session, request.symbol, window_end)

    context_start = window_start - timedelta(minutes=CONTEXT_PRE_MINUTES_DEFAULT)
    context_end = window_end + timedelta(minutes=CONTEXT_POST_MINUTES_DEFAULT)
    candidate_news = (
        session.query(NewsItem)
        .filter(
            NewsItem.source.in_(_annotation_news_sources()),
            NewsItem.timestamp >= context_start,
            NewsItem.timestamp <= context_end,
        )
        .order_by(NewsItem.timestamp.asc())
        .all()
    )

    if not candidate_news:
        # 没有候选新闻就直接返回 no_clear_news，省掉一次 API 调用。
        return AutoAnnotateResponse(
            selected_news_ids=[],
            no_clear_news=True,
            summary="窗口前后没有可见的候选新闻，无法归因。",
            reasoning="",
            model=config.DEEPSEEK_REASONER_MODEL,
            duration_seconds=0.0,
            candidate_count=0,
        )

    user_payload = _build_auto_annotate_user_payload(
        request,
        candidate_news,
        start_snapshot.price if start_snapshot else None,
        end_snapshot.price if end_snapshot else None,
        ((end_snapshot.price - start_snapshot.price) / abs(start_snapshot.price) * 100)
        if start_snapshot and end_snapshot and start_snapshot.price else None,
        reference_changes=_reference_changes_for_annotation(session, window_start, window_end, request.symbol),
    )

    logger.info(
        f"[AutoAnnotate] 调 DeepSeek {config.DEEPSEEK_REASONER_MODEL}，"
        f"effort={config.DEEPSEEK_REASONER_EFFORT}，候选 {len(candidate_news)} 条新闻"
    )
    content, reasoning, duration = _call_deepseek_reasoner(user_payload)
    valid_ids = {int(row.id) for row in candidate_news}
    selected, no_clear_news, summary = _parse_auto_annotate_response(content, valid_ids)
    logger.info(
        f"[AutoAnnotate] 完成，耗时 {duration:.1f}s，选中 {len(selected)} 条，"
        f"no_clear_news={no_clear_news}"
    )

    return AutoAnnotateResponse(
        selected_news_ids=selected,
        no_clear_news=no_clear_news,
        summary=summary,
        reasoning=reasoning,
        model=config.DEEPSEEK_REASONER_MODEL,
        duration_seconds=round(duration, 2),
        candidate_count=len(candidate_news),
    )


def _build_auto_annotate_batch_user_payload(
    session: Session,
    windows: list[AutoAnnotateRequest],
) -> tuple[str, list[dict], dict[int, list[int]]]:
    """组装批量请求的 user 消息；同时返回每个 window 的 candidate id 列表（落库用）。

    返回 (user_content, window_metas, candidate_ids_by_window_idx)。
    window_metas 与 windows 索引对齐，记录 symbol / 时间 / 价格元数据，用于响应阶段回填。
    """
    payload_windows: list[dict] = []
    window_metas: list[dict] = []
    candidate_ids_by_window: dict[int, list[int]] = {}

    # 对标资产快照一次性捞出（覆盖最早窗口起点之前的容差区），逐窗口算同期涨跌。
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)
    parsed_starts = [s for s in (parse_datetime(w.window_start_utc) for w in windows) if s is not None]
    ref_rows = (
        _load_reference_rows(session, min(parsed_starts) - timedelta(minutes=tolerance_minutes + 5))
        if parsed_starts else {}
    )

    for idx, w in enumerate(windows):
        window_start = parse_datetime(w.window_start_utc)
        window_end = parse_datetime(w.window_end_utc)
        if window_start is None or window_end is None:
            raise ValueError(f"窗口 {idx} 的 window_start_utc / window_end_utc 不能为空")

        start_snapshot = _find_window_snapshot(session, w.symbol, window_start)
        end_snapshot = _find_window_snapshot(session, w.symbol, window_end)

        context_start = window_start - timedelta(minutes=CONTEXT_PRE_MINUTES_DEFAULT)
        context_end = window_end + timedelta(minutes=CONTEXT_POST_MINUTES_DEFAULT)
        candidate_news = (
            session.query(NewsItem)
            .filter(
                NewsItem.source.in_(_annotation_news_sources()),
                NewsItem.timestamp >= context_start,
                NewsItem.timestamp <= context_end,
            )
            .order_by(NewsItem.timestamp.asc())
            .all()
        )
        candidate_ids_by_window[idx] = [int(row.id) for row in candidate_news]

        candidates_payload = [
            {
                "id": row.id,
                "time_bj": (row.timestamp + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if row.timestamp else None,
                "source": row.source,
                "llm_score": row.llm_importance,
                "jin10_important": bool(row.importance) if row.source == "jin10" else None,
                "title": (row.title or "")[:160],
                "content": (row.content or "")[:300],
            }
            for row in candidate_news
        ]

        change_pct = None
        if start_snapshot and end_snapshot and start_snapshot.price:
            change_pct = (end_snapshot.price - start_snapshot.price) / abs(start_snapshot.price) * 100

        payload_windows.append({
            "id": idx,
            "symbol": w.symbol,
            "start_utc": w.window_start_utc,
            "end_utc": w.window_end_utc,
            "threshold_pct": w.threshold_pct,
            "price_start": start_snapshot.price if start_snapshot else None,
            "price_end": end_snapshot.price if end_snapshot else None,
            "change_pct": change_pct,
            "reference_changes": _reference_changes_for_annotation(
                session, window_start, window_end, w.symbol, ref_rows=ref_rows,
            ),
            "candidates": candidates_payload,
        })
        window_metas.append({
            "symbol": w.symbol,
            "window_start_utc": w.window_start_utc,
            "window_end_utc": w.window_end_utc,
            "candidate_count": len(candidate_news),
        })

    user_content = f"共 {len(payload_windows)} 个窗口。\n{json.dumps({'windows': payload_windows}, ensure_ascii=False)}"
    return user_content, window_metas, candidate_ids_by_window


def _parse_auto_annotate_batch_response(
    raw: str,
    valid_ids_by_window: dict[int, set[int]],
) -> dict[int, tuple[list[int], bool, str, str]]:
    """从 reasoner 返回的 JSON 解析 items，按 window_id 映射回去；过滤幻觉 id。

    返回 (selected_news_ids, no_clear_news, summary, reasoning) 四元组。
    reasoning 是模型在结构化 JSON 里给出的**该窗口专属**解释，
    与 message.reasoning_content（整批共享的 thinking trace）不同。
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"reasoner 批量返回非 JSON: {text[:200]}")
        data = json.loads(match.group(0))

    if not isinstance(data, dict):
        raise ValueError(f"reasoner 批量返回顶层不是对象: {type(data)}")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("reasoner 批量返回缺少 items 列表")

    by_window: dict[int, tuple[list[int], bool, str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            wid = int(item.get("window_id"))
        except (TypeError, ValueError):
            continue
        valid_ids = valid_ids_by_window.get(wid)
        if valid_ids is None:
            continue
        raw_ids = item.get("selected_news_ids") or []
        selected: list[int] = []
        if isinstance(raw_ids, list):
            seen: set[int] = set()
            for entry in raw_ids:
                try:
                    value = int(entry)
                except (TypeError, ValueError):
                    continue
                if value in valid_ids and value not in seen:
                    selected.append(value)
                    seen.add(value)
        no_clear_news = bool(item.get("no_clear_news"))
        summary = item.get("summary") or ""
        if not isinstance(summary, str):
            summary = str(summary)
        reasoning = item.get("reasoning") or ""
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)
        by_window[wid] = (selected, no_clear_news, summary.strip()[:240], reasoning.strip())

    return by_window


def auto_annotate_batch(session: Session, request: AutoAnnotateBatchRequest) -> AutoAnnotateBatchResponse:
    """**只调模型，不写库**。一次喂多个窗口给 reasoner，复用同一份 system prompt。
    超过 AUTO_ANNOTATE_BATCH_LIMIT 个窗口时报错（前端应分片调用）。"""
    windows = request.windows or []
    if not windows:
        raise ValueError("windows 不能为空")
    if len(windows) > AUTO_ANNOTATE_BATCH_LIMIT:
        raise ValueError(
            f"批量自动标注一次最多 {AUTO_ANNOTATE_BATCH_LIMIT} 个窗口，收到 {len(windows)}；"
            f"请前端分片调用"
        )

    user_content, window_metas, candidate_ids_by_window = _build_auto_annotate_batch_user_payload(session, windows)

    logger.info(
        f"[AutoAnnotateBatch] 调 DeepSeek {config.DEEPSEEK_REASONER_MODEL}，"
        f"effort={config.DEEPSEEK_REASONER_EFFORT}，{len(windows)} 个窗口，"
        f"总候选 {sum(m['candidate_count'] for m in window_metas)} 条"
    )
    content, reasoning, duration = _call_deepseek_reasoner_batch(user_content)

    valid_ids_by_window = {idx: set(ids) for idx, ids in candidate_ids_by_window.items()}
    parsed = _parse_auto_annotate_batch_response(content, valid_ids_by_window)

    results: list[AutoAnnotateBatchItem] = []
    for idx, meta in enumerate(window_metas):
        # 模型可能漏给某个窗口；漏的视为 no_clear_news=true，summary 留空，
        # 让前端 UI 提示用户检查/重新单独跑该窗口。
        selected, no_clear_news, summary, item_reasoning = parsed.get(idx, ([], True, "", ""))
        results.append(AutoAnnotateBatchItem(
            symbol=meta["symbol"],
            window_start_utc=meta["window_start_utc"],
            window_end_utc=meta["window_end_utc"],
            selected_news_ids=selected,
            no_clear_news=no_clear_news,
            summary=summary,
            reasoning=item_reasoning,
            candidate_count=meta["candidate_count"],
            candidate_news_ids=candidate_ids_by_window[idx],
        ))

    logger.info(
        f"[AutoAnnotateBatch] 完成，耗时 {duration:.1f}s，命中 {len(parsed)}/{len(windows)} 个窗口"
    )

    return AutoAnnotateBatchResponse(
        results=results,
        reasoning=reasoning,
        model=config.DEEPSEEK_REASONER_MODEL,
        duration_seconds=round(duration, 2),
        requested_count=len(windows),
        answered_count=len(parsed),
    )


def _call_deepseek_reasoner_batch(user_content: str) -> tuple[str, str, float]:
    """与 _call_deepseek_reasoner 同样的 thinking 模式调用，但用批量 system prompt + 更大 max_tokens。"""
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法调用批量自动标注")

    payload = {
        "model": config.DEEPSEEK_REASONER_MODEL,
        "messages": [
            {"role": "system", "content": AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "thinking": {
            "type": "enabled",
            "reasoning_effort": config.DEEPSEEK_REASONER_EFFORT,
        },
        "response_format": {"type": "json_object"},
        # max_tokens 同时覆盖 reasoning_content + content；批量场景里 thinking 容易吃掉
        "max_tokens": 16000,
    }
    headers = {
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    started = time.monotonic()
    resp = requests.post(
        DEEPSEEK_API_URL,
        json=payload,
        headers=headers,
        timeout=(config.DEEPSEEK_CONNECT_TIMEOUT, config.DEEPSEEK_REASONER_BATCH_READ_TIMEOUT),
    )
    duration = time.monotonic() - started

    if resp.status_code >= 400:
        preview = resp.text[:300].replace("\n", " ")
        raise RuntimeError(f"DeepSeek 返回 {resp.status_code}: {preview}")

    body = resp.json()
    message = body["choices"][0].get("message", {})
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning_content") or "").strip()
    if not content:
        raise RuntimeError(f"DeepSeek 批量返回空 content（reasoning 预览: {reasoning[:200]}）")
    return content, reasoning, duration
