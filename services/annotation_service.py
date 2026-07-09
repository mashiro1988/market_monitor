from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from typing import NamedTuple

import requests
from loguru import logger
from sqlalchemy.orm import Session

import config
from models.news import NewsItem, NewsPriceAnnotation
from models.price import PriceSnapshot
from schemas.annotations import (
    MARKET_REACTION_TYPES,
    NEWS_CAUSAL_ROLES,
    AnnotationCreateRequest,
    AnnotationDetail,
    AnnotationListItem,
    AnnotationResponse,
    AnnotationSymbol,
    AutoAnnotateBatchItem,
    AutoAnnotateBatchRequest,
    AutoAnnotateBatchResponse,
    AutoAnnotateRefineRequest,
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
REFERENCE_SEGMENT_MINUTES = 60

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


def _reference_endpoints(rows: list[PriceSnapshot], window_start: datetime, window_end: datetime,
                         tolerance_minutes: int) -> tuple[float | None, float | None]:
    """窗口内绝对起点/终点（端点最近快照价）；任一端无数据 → None。"""
    s = _nearest_snapshot_any(rows, window_start, tolerance_minutes)
    e = _nearest_snapshot_any(rows, window_end, tolerance_minutes)
    return (s.price if s else None), (e.price if e else None)


def _reference_rows_cutoff(window_start: datetime, tolerance_minutes: int) -> datetime:
    return window_start - timedelta(minutes=REFERENCE_SEGMENT_MINUTES + tolerance_minutes + 5)


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
    correlations_by_symbol: dict[str, float] | None = None,
) -> list[ReferenceChange]:
    """按 config.ANNOTATION_REFERENCE_ASSETS 逐个算同期变动；标注品种本身 → is_self（不对标自己）。"""
    out: list[ReferenceChange] = []
    for sym, label, unit in _iter_reference_assets():
        rows = ref_rows.get(sym, [])
        pre_pct = _reference_change_for_window(
            rows, window_start - timedelta(minutes=REFERENCE_SEGMENT_MINUTES), window_start, tolerance_minutes, unit
        )
        pct = _reference_change_for_window(rows, window_start, window_end, tolerance_minutes, unit)
        post_pct = _reference_change_for_window(
            rows, window_end, window_end + timedelta(minutes=REFERENCE_SEGMENT_MINUTES), tolerance_minutes, unit
        )
        price_start, price_end = _reference_endpoints(rows, window_start, window_end, tolerance_minutes)
        if sym == annotated_symbol:
            out.append(ReferenceChange(
                symbol=sym,
                label=label,
                pre_pct=pre_pct,
                price_start=price_start,
                price_end=price_end,
                pct=pct,
                post_pct=post_pct,
                correlation=None,
                unit=unit,
                is_self=True,
            ))
            continue
        out.append(ReferenceChange(
            symbol=sym,
            label=label,
            pre_pct=pre_pct,
            price_start=price_start,
            price_end=price_end,
            pct=pct,
            post_pct=post_pct,
            correlation=(correlations_by_symbol or {}).get(sym),
            unit=unit,
        ))
    return out


def _reference_correlations_for_window(
    session: Session,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, float]:
    """标注品种与各对标在窗口 ±1h 的 5min 收益率 Pearson 相关，按 symbol 返回。"""
    from services import window_signals

    corr_start = window_start - timedelta(minutes=60)
    corr_end = window_end + timedelta(minutes=60)
    correlations: dict[str, float] = {}
    for ref_symbol, _label, _unit in _iter_reference_assets():
        if ref_symbol == symbol:
            continue
        r = window_signals.pearson_correlation(session, symbol, ref_symbol, corr_start, corr_end)
        if r is not None:
            correlations[ref_symbol] = round(r, 2)
    return correlations


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


def _format_reference_value(value: float | None, unit: str) -> str | None:
    if value is None:
        return None
    if unit == "bp":
        return f"{value:+.1f}bp"
    return f"{value:+.2f}%"


def _reference_change_segments_payload(refs: list[ReferenceChange]) -> dict[str, dict[str, str | None]]:
    out: dict[str, dict[str, str | None]] = {}
    for ref in refs:
        out[ref.label] = {
            "pre_1h": _format_reference_value(ref.pre_pct, ref.unit),
            "window": _format_reference_value(ref.pct, ref.unit),
            "post_1h": _format_reference_value(ref.post_pct, ref.unit),
        }
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
        ref_rows = _load_reference_rows(session, _reference_rows_cutoff(window_start, tolerance_minutes))
    refs = _reference_changes_for_window(ref_rows, window_start, window_end, tolerance_minutes, symbol)
    return _reference_changes_payload(refs)


def _reference_change_segments_for_annotation(
    session: Session,
    window_start: datetime,
    window_end: datetime,
    symbol: str,
    ref_rows: dict[str, list[PriceSnapshot]] | None = None,
) -> dict[str, dict[str, str | None]]:
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)
    if ref_rows is None:
        ref_rows = _load_reference_rows(session, _reference_rows_cutoff(window_start, tolerance_minutes))
    refs = _reference_changes_for_window(ref_rows, window_start, window_end, tolerance_minutes, symbol)
    return _reference_change_segments_payload(refs)


def _window_signals_payload(session: Session, symbol: str,
                            window_start: datetime, window_end: datetime) -> dict:
    """喂给 auto-annotate reasoner 的窗口派生信号（v11 字段 / Phase2 rolling 口径统一）：
    - s_scores / max_ref / sync_ref_count / machine_class：共振分证据链。s = 段窗内 rolling 曲线
      |S| 峰值（与标注页曲线同一个数，"所见即所判"）；含符号（美元反向为常态），判级用 |S|。
    - reference_change_segments：固定三段方向链（含标注品种本身），交叉验证用。
    - trigger_move_start_bj / _pct：窗口内首个显著波动 5min bar（真加速触发时点）。
    - pre_window_move_pct：窗口前 1h 净变动（情绪反转识别）。"""
    from services import window_signals
    from services.behavior_classifier import _classify_cell, _news_ids, _points
    from services.resonance_score import chg_map, rolling_peak

    labels_by_symbol = {sym: label for sym, label, _unit in _iter_reference_assets()}
    tiers = config.BEHAVIOR_TIERS.get(symbol)
    s_scores: dict[str, dict] = {}
    if tiers:
        roll_points = int(config.BEHAVIOR_ROLLING_POINTS)
        pre_pad = timedelta(minutes=5 * (roll_points - 1) + 15)   # 拖尾窗回看
        post_pad = timedelta(minutes=75)
        btc_chg = chg_map(_points(session, symbol, window_start - pre_pad, window_end + post_pad))
        for ref in config.BEHAVIOR_REF_SYMBOLS:
            ref_tiers = config.BEHAVIOR_TIERS.get(ref)
            if not ref_tiers or ref == symbol:
                continue
            r = rolling_peak(
                btc_chg,
                chg_map(_points(session, ref, window_start - pre_pad, window_end + post_pad)),
                float(tiers[0]), float(ref_tiers[0]), window_start, window_end,
                points=roll_points, coverage_min=config.BEHAVIOR_COVERAGE_MIN,
            )
            if r is not None:
                s_scores[labels_by_symbol.get(ref, ref)] = {
                    "s": round(r[0], 2), "ess": round(r[1], 1), "coverage": round(r[2], 2),
                }
    max_label, max_abs = None, None
    for label, v in s_scores.items():
        if max_abs is None or abs(v["s"]) > max_abs:
            max_label, max_abs = label, abs(v["s"])
    machine_class = (
        _classify_cell(max_abs, bool(s_scores), bool(_news_ids(session, window_start, window_end)))
        if tiers else None
    )
    trig = window_signals.first_trigger_segment(session, symbol, window_start, window_end)
    pre = window_signals.pre_window_move(session, symbol, window_start, minutes=60)
    return {
        "s_scores": s_scores,
        "max_ref": {"label": max_label, "abs_s": round(max_abs, 2)} if max_label is not None else None,
        "sync_ref_count": sum(1 for v in s_scores.values() if abs(v["s"]) >= config.BEHAVIOR_S_MID),
        "machine_class": machine_class,
        "reference_change_segments": _reference_change_segments_for_annotation(
            session, window_start, window_end, symbol
        ),
        "trigger_move_start_bj": (trig["start"] + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if trig else None,
        "trigger_move_pct": round(trig["pct"], 2) if trig else None,
        "pre_window_move_pct": round(pre, 2) if pre is not None else None,
    }


def _parse_reference_changes(raw: str | None) -> dict[str, str | None] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    parsed: dict[str, str | None] = {}
    for key, val in value.items():
        if not isinstance(key, str):
            continue
        parsed[key] = val if isinstance(val, str) or val is None else str(val)
    return parsed

# 每个标注窗口前后取的候选新闻范围：默认向前 30 分钟（2026-06-11 从 15 放宽：
# 慢反应场景消息常早于触发点），向后 30 分钟。60m 档窗口由 ANNOTATION_WINDOW_SCALES
# 的 pre_minutes 指定前 60；请求里带 context_pre_minutes 即覆盖默认。
# ±1h（annotation-refinements Part B）：候选新闻窗口拉宽到窗口前后各 60min，
# 兜底市场滞后反应（driver 在窗口前较久）+ 新闻源迟报（driver 在窗口后较久才推）。
CONTEXT_PRE_MINUTES_DEFAULT = 60
CONTEXT_POST_MINUTES_DEFAULT = 60


def _annotation_news_sources() -> list[str]:
    """标注上下文 / 自动标注 候选新闻的源白名单——只读取在 `config.NEWS_SOURCES` 里启用的。
    切换 / 增减英文源（CNBC / Reuters 等）时只改 config，不动业务代码。"""
    return [k for k, v in config.NEWS_SOURCES.items() if v.get("enabled")]

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

AUTO_ANNOTATE_SYSTEM_PROMPT = """你是一名买方量化研究员，专门分析单一资产在短时间窗口内的价格异动与新闻的因果关系：
给每条候选新闻标注**因果角色（causal_role）**，并给出本窗口归因的**置信度（confidence）**。

你将收到：
1. 一段价格异动窗口的元数据（symbol、起止时间、价格变化百分比、阈值、同期对标品种涨跌 reference_changes、固定三段方向链 reference_change_segments，以及共振分证据 s_scores / max_ref / sync_ref_count / machine_class）。
2. 该窗口前后一段时间内的候选新闻条目（含 id、北京时间、来源、LLM 评分、标题、内容片段）。

**标签体系**：

每条新闻的 causal_role（输出里**只列非 noise** 的候选，未列出的一律默认 noise）：
- driver 驱动：触发或推动本窗口异动的**主**事件——同一事件簇里信息量最大 / 最主要的**一条**标 driver
- redundant 同簇冗余：与 driver 同一事件簇的**其它**相关报道（首报 / 补充 / 后续 / 不同来源转述）→ 标 redundant，相关但非主代表，**不要降级成 noise**（训练时不当负样本）
- noise 噪音：与本窗口异动无关（默认值，**不要写进输出**）。注意：**迟到首报也算 noise**——新闻描述的事件发生在窗口开始之前、且价格在前一时段已朝事件方向反应过（市场已定价），它对本窗口没有新增信息

窗口级 confidence（0-1）：0.8-1.0 = 新闻与价格方向、时间、资产高度匹配（无 driver 时 = 确认候选里没有信息）；0.5-0.8 = 可能是主因之一；<0.5 = 归因不确定。

**首要原则：宁可保守，不要乱标。**
- 默认所有候选都是 noise。**只有当某条新闻能给出强而具体的因果链条**（"X 事件直接导致 symbol 价格 Y"），才标 driver。
- 模糊关联、宏观背景文章、与该 symbol 仅有间接联系的新闻 → 保持 noise；行情综述 / 事后总结 / 解释性、离题、方向相反的消息 → 一律默认 noise；驱动事件的重复转述 / 后续报道 → 标 redundant（同事件簇的其它报道）。**唯一例外**见下方 reference_changes 段：被跨资产签名确认的重大宏观 / 地缘**突发事件**，不算"间接联系"。
- 没把握时不给 driver（保持 noise），用 confidence 表达确定程度 —— 这份数据要拿去训练模型，假阳性会污染训练集。
- 下文条款中的「选入 / 不选」语义统一对应：**选入 = 标 driver（主）或 redundant（同簇其它）；不选 = 保持 noise**（综述 / 解释 / 离题 / 方向相反一律默认 noise，不单列）。

**关于窗口派生信号（window 里的 s_scores / max_ref / machine_class / trigger_move_start_bj / pre_window_move_pct）—— 重点判据**：
1. **没明显相关新闻就别标 driver**：默认所有候选 noise，只有能给出强而具体因果链条的才 driver。
2. **权重看首个触发段、不是窗口起点**：`trigger_move_start_bj` 是窗口内**第一个显著波动 5min 的起点**（价格真正开始剧烈反应的触发时点）。候选新闻**离它越近权重越高**（重点看它往前 ~5min 到它之后一小段），窗口前面平的一段基本没信息。它为 null 时退回看整段。
3. **共振分 = 在跟谁走**：`s_scores` 是本品种与各对标的**共振分 S**（−1~+1：本品种使劲的时刻，对标朝同方向跟了几成，异动点加权；取值 = 段窗内 rolling 曲线的 |S| **峰值**，与标注页联动曲线所见同数）。**符号只表示方向关系**（美元指数为负 = 正常反向联动），**判联动强弱一律用 |S|**：`max_ref` 给出最强参照——**max|S| ≥ 0.5 = 宏观耦合（共振）**，优先找能驱动**最强参照**的突发事件新闻；0.3–0.5 = 弱共振（仅参考）；< 0.3 = 独立行情。每个分数附 `ess`（证据厚度：< 5 = 分数只靠一两根 K 线撑起，**证据薄**，据此下调 confidence）和 `coverage`（参照覆盖）。`sync_ref_count` = |S|≥0.3 的参照个数（区分"只跟单一资产走"与"全市场共振"）。`machine_class` 是机器按 S × 新闻命中打的预分类，作为推理起点、可按证据推翻。一条新闻**只描述价格走势本身、给不出背后真实世界事件**的 → noise（大类联动由共振分解释，不由这种描述性新闻解释）。
4. **跨资产签名确认真伪**：方向对但直觉冲击不大、且价格反应滞后的新闻，别急着标 driver——要**整套跨资产签名对齐**才认。例：鹰派联储（"通胀在升、年内加息"）应看到 **BTC/纳指下行 + 美元指数走强 + 美债利率上行 同步**；若这些 corroborating 资产（看 reference_changes / reference_change_segments）**不配合**，就该怀疑不是这条在驱动，降级 noise。
5. **急反转多半是情绪、无 driver**：`pre_window_move_pct` 是窗口前 1h 净变动——若**窗口前猛涨、窗口却猛跌**（或反之），这种急反转很可能是**纯情绪 / 仓位挤压**（多杀多 / 空杀空）、**没有新闻驱动**。这种形态倾向 no driver（情绪类），别硬凑 driver；除非候选里有量级重大、且跨资产签名对齐的硬事件。

**核心推理顺序（必须执行）**：
1. 先看 `machine_class` 与 `max_ref` 定行情性质：max|S| ≥ 0.5 → 宏观耦合（候选里有量级匹配的新闻 = 新闻驱动；没有 = 纯共振，别硬凑 driver）；< 0.3 → 独立行情（只考虑该资产专属新闻）；`s_scores` 为空 = **无对照（宏观休市）≠ 无宏观新闻**——周末突发事件照样可标 driver，按「对标不可用」条款做纯事件判断。ESS < 5 或 coverage 低时在 reasoning 里明示证据薄、下调 confidence。
2. 共振时围绕**最强参照**找新闻：最强是纳指/日经 → 宏观、利率、地缘、政策；黄金/美元/美债 → 避险、通胀、利率、流动性；原油 → 供给、地缘、库存/OPEC。
3. 再用 `reference_changes` 与三段方向链（reference_change_segments）交叉验证：候选新闻必须能解释一条完整资产联动链。例：通胀风险下降 / 加息预期走弱，通常应看到美元指数或美债利率走弱，并可能推升黄金或风险资产；如果新闻含义与实际资产链条相反，就降级 noise。
4. 只有「最强参照的新闻 + 其它资产验证 + 时间靠近触发段」三者合起来说得通，才标 driver；缺一项时宁可不标，用 confidence 表达不确定。

**关于 LLM 评分（candidates 里的 llm_score）的处理**：
- 这个分数不可靠，**不要**把它作为筛选依据。8 分新闻可能与本次异动毫无关系，4 分新闻反而可能是真正触发。
- 完全基于新闻**内容本身**（标题 + 摘要）判断与本窗口异动的因果关系。

**关于同期对标品种涨跌（window.reference_changes）**：
- 窗口元数据附带同一时段宏观对标品种（纳指=NQ 期货（近 23 小时交易，美股收盘后仍有效）/ 原油 / 黄金 / 美债2Y / 美元指数 / BTC 等）的变动；null 表示该品种休市或无数据。美债2Y 以**基点**表示（+10.0bp = 收益率上行 0.10 个百分点），其余为涨跌百分比。
- 用跨资产签名判断异动性质，再决定在候选里找哪类新闻。签名里的「股指」判别同样适用于 BTC 等风险资产标的。**签名是路由提示，不是一票否决的检查清单**——真实市场里多个签名经常混合矛盾（如地缘威胁压股指的同时，协议预期压油价），此时以「**风险资产同向共振 + 新闻发布时点与下跌起点 / 加速段吻合**」为最强证据，单个商品的方向不能否决归因：
  - 标的与对标品种同向联动（如纳指、BTC 同跌）→ 宏观共振，优先在候选里找宏观、地缘、政策类**突发事件**；
  - 股指跌 + 美债2Y 收益率**上行** → 加息预期 / 政策利率冲击（通胀数据超预期、央行鹰派）→ 找数据公布 / 央行决议 / 官员讲话；
  - 股指跌 + 美债2Y 收益率**下行** → 降息预期 / 衰退担忧 / 避险 → 找金融风险、增长恶化或重大突发（黄金上涨可作佐证，但**不是必要条件**）；
  - 股指下跌 + **原油上涨**，是地缘冲突 / 供给冲击的**核心**签名（军事行动、空袭、制裁、油轮遇袭等）。注意：黄金在地缘冲突中常涨但**不必然**——冲突持久化后市场会钝化、强美元或流动性挤兑也会压制金价，**黄金没涨不能用来否定地缘归因**；股指与原油同跌**且无重大地缘突发**时偏向衰退 / 需求担忧——但原油常被协议预期 / OPEC / 库存等自身因素独立驱动，**若风险资产同向共振且候选里有时间吻合的重大地缘突发，地缘归因优先于原油方向**；
  - 美元指数急升 + BTC/股指跌 → 美元流动性收紧 / 避险买美元；
  - 对标品种基本走平、只有标的自己异动 → 找该资产**专属**新闻（如 BTC 的 ETF / 链上 / 监管 / 交易所事件），此时宏观新闻不可选。**本条仅当主力对标（纳指 / 原油 / 黄金）处于交易时段且有数据时适用**——它们为 null 时见下面「对标不可用」条款，不得引用本条排除宏观新闻。
- 突发地缘 / 军事 / 制裁类新闻通常**不会提到 symbol 本身**：只要事件足够重大、发布时间与窗口吻合、且跨资产签名一致**（对标休市、签名不可用时，此项不作要求）**，就应视为对股指 / 风险资产的**直接**触发选入，不要因为"新闻没提该品种"而当作间接关联排除。注意区分：**新发生的事件**（首次交火、首次空袭）可选；对既有局势的回顾、分析、评论文章仍然不选。
- 标注品种本身若在对标清单里（如标注纳指 NQ=F 的窗口），它**不会出现在 reference_changes 里**——其涨跌就是窗口自身的 change_pct，键缺失不是数据故障。
- **对标不可用**：当 reference_changes **全部为 null**，或**主力对标（纳指 / 原油 / 黄金）为 null、仅剩美元指数 / 美债2Y 等低波动品种基本走平**——典型时段：北京时间凌晨 05:00-06:00 的 CME 日休、周末 / 假日（加密窗口高发）——跨资产签名**不可用：既不能用来确认，也绝不能用来排除**。美元指数或收益率走平**不构成**"没有宏观 / 地缘事件"的证据；此时 BTC 等 7×24 资产往往是突发事件**唯一**的即时反应者。处理方式：退回**纯事件判断**——只有当候选里存在**量级重大**的硬事件（军事打击 / 开战 / 重大制裁或监管落地，而非言论、分析、回顾），其发布时间与窗口起点或加速段**分钟级吻合**、方向一致时，才选入；达不到这个门槛就 no_clear_news=true。

**关于新闻时间戳的关键说明**：候选新闻的 time_bj 是新闻**发布时间**，不是事件发生时间。中文财经源（华尔街见闻、jin10）和翻译类英文源经常把欧美时段的事件延迟 5-30 分钟才推送到国内。所以：
- 窗口结束后 0-30 分钟内出现的新闻，如果其内容描述的事件**明显发生在窗口期间或之前**（如 FOMC 决议、CPI 公布、美股盘中事件），仍视为有效触发可以选中。
- 只有当新闻内容明显描述窗口结束之后才发生的事件（如另一场会议、次日数据、当晚才出的声明），才把它当作"窗口后新闻"忽略。
- 当不确定事件何时发生时，倾向于把它视为发布延迟、可作为触发。

**关于长窗口（多段合并的事件窗口）**：窗口可能由多段连续同向异动合并而成，跨度可达数小时。此时：
- 触发新闻经常出现在窗口**中段**，驱动的是后半段的延续或加速——**不要**仅因"新闻发布时间晚于窗口起点、解释不了行情起点"而排除；只要新闻发布后价格朝同方向延续/加速、事件量级与跨资产签名相符，就可作为触发选入。
- 同窗口内同时出现**升级与缓和**两类消息时（地缘场景常见），以与价格方向一致的一侧为候选；方向相反的消息存在，不构成排除理由——市场对两类消息的定价权重本就不同。
- 同一事件的多条连续快讯（同一讲话/同一袭击的不同来源、不同侧面）属于一个事件簇：信息量最大的**一条**标 driver，其余相关报道标 redundant（不必只选 1-3 条、也不要降级成 noise）。

判断步骤：
1. 先看 machine_class / max_ref 定行情性质（共振、弱共振、独立、无对照）；无对照时新闻命中检查照常做，不得用"参照没动"排除宏观新闻。
2. 围绕最强参照扫描候选新闻：共振时重点核对宏观 / 地缘 / 利率 / 政策类突发事件；独立行情时只考虑该资产专属新闻。
3. 用 reference_changes 与三段方向链做交叉验证，资产链条和新闻含义不一致时降级 noise；行情综述 / 收盘总结 / 解释性 / 方向相反 / 离题的消息一律默认 noise（不必单列）。
4. 没有 driver → summary 写"无明显因果新闻"，confidence 表达"确认无信息"的程度；有 driver → 给出 confidence，summary 用一句话写清楚因果链条。

只返回 JSON，不要 Markdown 标记，不要解释性正文。格式：
{
  "news_roles": {"<新闻id>": "driver|redundant", ...},   // 只列非 noise 的候选；id 必须真实存在于候选列表
  "confidence": 0.85,                // 0-1
  "window_class": "news_driven|pure_resonance|sentiment_tech",  // v12 窗口级三类结论（见下）
  "summary": "不超过 80 字的归因结论；无明显驱动时写'无明显因果新闻'即可"
}

**window_class 判定规则（v12）**：有 driver → `news_driven`；无 driver 且 max|S| ≥ 0.5 → `pure_resonance`
（跟着宏观走但候选里没有量级匹配的新闻）；无 driver 且 max|S| < 0.5（或无对照且无重大事件）→
`sentiment_tech`（情绪/仓位/技术面）。machine_class 是机器起点，你的结论可按证据推翻它。
"""


AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT = """你是一名买方量化研究员。你将一次性收到一组价格异动窗口及其各自的候选新闻列表，
对**每个**窗口分别完成：给候选新闻标注**因果角色（causal_role）**，并给出该窗口归因的**置信度（confidence）**。

**标签体系**：

每条新闻的 causal_role（每个窗口的输出里**只列非 noise** 的候选，未列出的一律默认 noise）：
- driver 驱动：触发或推动该窗口异动的**主**事件——同一事件簇里信息量最大 / 最主要的**一条**标 driver
- redundant 同簇冗余：与 driver 同一事件簇的**其它**相关报道（首报 / 补充 / 后续 / 不同来源转述）→ 标 redundant，相关但非主代表，**不要降级成 noise**（训练时不当负样本）
- noise 噪音：与该窗口异动无关（默认值，**不要写进输出**）。注意：**迟到首报也算 noise**——新闻描述的事件发生在窗口开始之前、且价格在前一时段已朝事件方向反应过（市场已定价），它对该窗口没有新增信息

窗口级 confidence（0-1）：0.8-1.0 = 高度匹配（无 driver 时 = 确认候选里没有信息）；0.5-0.8 = 可能是主因之一；<0.5 = 归因不确定。

输入结构：
{
  "windows": [
    {
      "id": int,                                  // 窗口编号，必须在返回中按相同 id 引用
      "symbol", "start_utc", "end_utc",
      "threshold_pct", "price_start", "price_end", "change_pct",
      "reference_changes": { "纳指": "+0.42%", "原油": "-1.10%", "美债2Y": "+6.0bp", ... },   // 同期对标品种变动；null=休市/无数据
      "reference_change_segments": { "纳指": {"pre_1h": "+0.10%", "window": "+0.31%", "post_1h": "+0.12%"}, ... },  // 固定三段方向链（含标注品种本身）
      "s_scores": { "纳指": {"s": 0.77, "ess": 4.3, "coverage": 1.0}, "美元指数": {"s": -0.33, "ess": 4.3, "coverage": 0.96}, ... },  // 共振分 S（−1~+1）；缺失=休市/覆盖不足
      "max_ref": {"label": "纳指", "abs_s": 0.77},   // 最强参照（判级用 max|S|）；null=无对照
      "sync_ref_count": 3,                            // |S|≥0.3 的参照个数
      "machine_class": "macro_news",                  // 机器预分类（S×新闻十字格），推理起点、可推翻
      "candidates": [{ "id": int, "time_bj", "source", "llm_score", ... }, ...]
    },
    ...
  ]
}

**首要原则：宁可保守，不要乱标。**
- 默认所有候选都是 noise。**只有当某条新闻能给出强而具体的因果链条**（"X 事件直接导致 symbol 价格 Y"），才标 driver。
- 模糊关联、宏观背景、与该 symbol 仅有间接联系的新闻 → 保持 noise；行情综述 / 事后总结 / 解释性、离题、方向相反的消息 → 一律默认 noise；驱动事件的重复转述 / 后续报道 → 标 redundant（同事件簇的其它报道）。**唯一例外**见下方 reference_changes 段：被跨资产签名确认的重大宏观 / 地缘**突发事件**，不算"间接联系"。
- 没把握时不给 driver（保持 noise），用 confidence 表达确定程度 —— 这份数据要拿去训练模型，假阳性会严重污染训练集。
- 不要为了"每个窗口都给点东西"而硬标 —— 一个 batch 里大多数窗口可能都没有 driver，这是正常的。
- 下文条款中的「选入 / 不选」语义统一对应：**选入 = 标 driver（主）或 redundant（同簇其它）；不选 = 保持 noise**（综述 / 解释 / 离题 / 方向相反一律默认 noise，不单列）。

**关于每个窗口的派生信号（window 里的 s_scores / max_ref / machine_class / trigger_move_start_bj / pre_window_move_pct）—— 重点判据**：
1. **没明显相关新闻就别标 driver**：默认所有候选 noise，只有能给出强而具体因果链条的才 driver。
2. **权重看首个触发段、不是窗口起点**：`trigger_move_start_bj` 是该窗口内**第一个显著波动 5min 的起点**（价格真正开始剧烈反应的触发时点）。候选新闻**离它越近权重越高**（重点看它往前 ~5min 到它之后一小段），窗口前面平的一段基本没信息。它为 null 时退回看整段。
3. **共振分 = 在跟谁走**：`s_scores` 是该品种与各对标的**共振分 S**（−1~+1，异动点加权的方向跟随度；取值 = 段窗内 rolling 曲线的 |S| **峰值**，所见即所判）。**符号只表示方向关系**（美元指数为负 = 正常反向联动），**判联动强弱一律用 |S|**：`max_ref` 给最强参照——**max|S| ≥ 0.5 = 宏观耦合**，优先找能驱动**最强参照**的突发事件新闻；0.3–0.5 = 弱共振（仅参考）；< 0.3 = 独立行情。`ess` < 5 = **证据薄**（分数只靠一两根 K 线，下调 confidence）；`sync_ref_count` 区分"跟单一资产"与"全市场共振"；`machine_class` 是机器预分类（推理起点、可推翻）。**只描述价格走势本身、给不出背后真实世界事件**的新闻 → noise（大类联动由共振分解释）。
4. **跨资产签名确认真伪**：方向对但直觉冲击不大、价格反应滞后的新闻别急着标 driver——要**整套跨资产签名对齐**才认（鹰派联储应看到 BTC/纳指↓ + 美元↑ + 美债利率↑ 同步）；corroborating 资产不配合 → 降级 noise。
5. **急反转多半是情绪、无 driver**：`pre_window_move_pct` = 窗口前 1h 净变动——**前猛涨、窗口猛跌**（或反之）这种急反转多是**纯情绪 / 仓位挤压**（多杀多 / 空杀空）、无新闻驱动，倾向 no driver；除非有量级重大且跨资产签名对齐的硬事件。

**核心推理顺序（必须执行）**：
1. 先看该窗口的 `machine_class` 与 `max_ref` 定行情性质：max|S| ≥ 0.5 → 宏观耦合（有量级匹配的新闻 = 新闻驱动；没有 = 纯共振，别硬凑 driver）；< 0.3 → 独立行情（只考虑该资产专属新闻）；`s_scores` 为空 = **无对照（宏观休市）≠ 无宏观新闻**——周末突发照样可标 driver，按「对标不可用」条款做纯事件判断。ESS < 5 或 coverage 低时在 reasoning 里明示证据薄、下调 confidence。
2. 共振时围绕**最强参照**找新闻：纳指/日经 → 宏观、利率、地缘、政策；黄金/美元/美债 → 避险、通胀、利率、流动性；原油 → 供给、地缘、库存/OPEC。
3. 再用 `reference_changes` 与三段方向链交叉验证：候选新闻必须能解释一条完整资产联动链。例：通胀风险下降 / 加息预期走弱，通常应看到美元指数或美债利率走弱，并可能推升黄金或风险资产；如果新闻含义与实际资产链条相反，就降级 noise。
4. 只有「最强参照的新闻 + 其它资产验证 + 时间靠近触发段」三者合起来说得通，才标 driver；缺一项时宁可不标，用 confidence 表达不确定。

**关于 LLM 评分（candidates 里的 llm_score）的处理**：
- 这个分数不可靠，**不要**把它作为筛选依据。8 分新闻可能与本次异动毫无关系，4 分新闻反而可能是真正触发。
- 完全基于新闻**内容本身**（标题 + 摘要）判断与本窗口异动的因果关系。

**关于同期对标品种涨跌（每个 window 的 reference_changes）**：
- 每个窗口附带同一时段宏观对标品种（纳指=NQ 期货（近 23 小时交易，美股收盘后仍有效）/ 原油 / 黄金 / 美债2Y / 美元指数 / BTC 等）的变动；null 表示该品种休市或无数据。美债2Y 以**基点**表示（+10.0bp = 收益率上行 0.10 个百分点），其余为涨跌百分比。
- 用跨资产签名判断该窗口异动性质，再决定在候选里找哪类新闻。签名里的「股指」判别同样适用于 BTC 等风险资产标的。**签名是路由提示，不是一票否决的检查清单**——真实市场里多个签名经常混合矛盾（如地缘威胁压股指的同时，协议预期压油价），此时以「**风险资产同向共振 + 新闻发布时点与下跌起点 / 加速段吻合**」为最强证据，单个商品的方向不能否决归因：
  - 标的与对标品种同向联动（如纳指、BTC 同跌）→ 宏观共振，优先在该窗口候选里找宏观、地缘、政策类**突发事件**；
  - 股指跌 + 美债2Y 收益率**上行** → 加息预期 / 政策利率冲击（通胀数据超预期、央行鹰派）→ 找数据公布 / 央行决议 / 官员讲话；
  - 股指跌 + 美债2Y 收益率**下行** → 降息预期 / 衰退担忧 / 避险 → 找金融风险、增长恶化或重大突发（黄金上涨可作佐证，但**不是必要条件**）；
  - 股指下跌 + **原油上涨**，是地缘冲突 / 供给冲击的**核心**签名（军事行动、空袭、制裁、油轮遇袭等）。注意：黄金在地缘冲突中常涨但**不必然**——冲突持久化后市场会钝化、强美元或流动性挤兑也会压制金价，**黄金没涨不能用来否定地缘归因**；股指与原油同跌**且无重大地缘突发**时偏向衰退 / 需求担忧——但原油常被协议预期 / OPEC / 库存等自身因素独立驱动，**若风险资产同向共振且候选里有时间吻合的重大地缘突发，地缘归因优先于原油方向**；
  - 美元指数急升 + BTC/股指跌 → 美元流动性收紧 / 避险买美元；
  - 对标品种基本走平、只有标的自己异动 → 找该资产**专属**新闻（如 BTC 的 ETF / 链上 / 监管 / 交易所事件），此时宏观新闻不可选。**本条仅当主力对标（纳指 / 原油 / 黄金）处于交易时段且有数据时适用**——它们为 null 时见下面「对标不可用」条款，不得引用本条排除宏观新闻。
- 突发地缘 / 军事 / 制裁类新闻通常**不会提到 symbol 本身**：只要事件足够重大、发布时间与窗口吻合、且跨资产签名一致**（对标休市、签名不可用时，此项不作要求）**，就应视为对股指 / 风险资产的**直接**触发选入，不要因为"新闻没提该品种"而当作间接关联排除。注意区分：**新发生的事件**（首次交火、首次空袭）可选；对既有局势的回顾、分析、评论文章仍然不选。
- 标注品种本身若在对标清单里（如标注纳指 NQ=F 的窗口），它**不会出现在 reference_changes 里**——其涨跌就是窗口自身的 change_pct，键缺失不是数据故障。
- **对标不可用**：当 reference_changes **全部为 null**，或**主力对标（纳指 / 原油 / 黄金）为 null、仅剩美元指数 / 美债2Y 等低波动品种基本走平**——典型时段：北京时间凌晨 05:00-06:00 的 CME 日休、周末 / 假日（加密窗口高发）——跨资产签名**不可用：既不能用来确认，也绝不能用来排除**。美元指数或收益率走平**不构成**"没有宏观 / 地缘事件"的证据；此时 BTC 等 7×24 资产往往是突发事件**唯一**的即时反应者。处理方式：退回**纯事件判断**——只有当候选里存在**量级重大**的硬事件（军事打击 / 开战 / 重大制裁或监管落地，而非言论、分析、回顾），其发布时间与窗口起点或加速段**分钟级吻合**、方向一致时，才选入；达不到这个门槛就 no_clear_news=true。

**关于新闻时间戳的关键说明**：候选新闻的 time_bj 是新闻**发布时间**，不是事件发生时间。中文财经源（华尔街见闻、jin10）和翻译类英文源经常把欧美时段的事件延迟 5-30 分钟才推送到国内。所以：
- 窗口结束后 0-30 分钟内出现的新闻，如果其内容描述的事件**明显发生在窗口期间或之前**（如 FOMC 决议、CPI 公布、美股盘中事件），仍视为有效触发可以选中。
- 只有当新闻内容明显描述窗口结束之后才发生的事件（如另一场会议、次日数据、当晚才出的声明），才把它当作"窗口后新闻"忽略。
- 当不确定事件何时发生时，倾向于把它视为发布延迟、可作为触发。

**关于长窗口（多段合并的事件窗口）**：窗口可能由多段连续同向异动合并而成，跨度可达数小时。此时：
- 触发新闻经常出现在窗口**中段**，驱动的是后半段的延续或加速——**不要**仅因"新闻发布时间晚于窗口起点、解释不了行情起点"而排除；只要新闻发布后价格朝同方向延续/加速、事件量级与跨资产签名相符，就可作为触发选入。
- 同窗口内同时出现**升级与缓和**两类消息时（地缘场景常见），以与价格方向一致的一侧为候选；方向相反的消息存在，不构成排除理由——市场对两类消息的定价权重本就不同。
- 同一事件的多条连续快讯（同一讲话/同一袭击的不同来源、不同侧面）属于一个事件簇：信息量最大的**一条**标 driver，其余相关报道标 redundant（不必只选 1-3 条、也不要降级成 noise）。

每个窗口的判断步骤：
1. 先看该窗口的 machine_class / max_ref 定行情性质（共振、弱共振、独立、无对照）；无对照时新闻命中检查照常做，不得用"参照没动"排除宏观新闻。
2. 围绕最强参照扫描候选新闻：共振时重点核对宏观 / 地缘 / 利率 / 政策类突发事件；独立行情时只考虑该资产专属新闻。
3. 用 reference_changes 与三段方向链做交叉验证，资产链条和新闻含义不一致时降级 noise；行情综述 / 收盘总结 / 解释性 / 方向相反 / 离题的消息一律默认 noise（不必单列）。
4. 没有 driver → summary 写"无明显因果新闻"，confidence 表达"确认无信息"的程度；有 driver → 给出 confidence，summary 用一句话写清楚因果链条。

每个 window 必须在输出 items 中**有且只有一项**，window_id 与输入的 id 严格对应。每个 item 必须独立给出 reasoning，不要写"同上"或跨窗口共用。

只返回 JSON：
{
  "items": [
    {
      "window_id": int,
      "news_roles": {"<新闻id>": "driver|redundant", ...},   // 只列非 noise；id 只能来自该 window 自己的 candidates
      "confidence": 0.85,                // 0-1
      "window_class": "news_driven|pure_resonance|sentiment_tech",  // v12 三类结论：有driver=news_driven；无driver且max|S|≥0.5=pure_resonance；否则=sentiment_tech
      "summary": "不超过 80 字的归因结论；无明显驱动时写'无明显因果新闻'即可",
      "reasoning": "150-250 字解释：为什么这样标角色 / 为什么本窗口没有主驱动 / 排除了哪些误判候选。该字段只属于这个 window，禁止跨窗口复用。"
    },
    ...
  ]
}
"""

# 一次批量调用最多塞 N 个窗口，避免上下文过长把 reasoning 时间和成本拉爆。
CROSS_ASSET_SEGMENT_GUIDE = """

**v12 补充：窗口级三类结论 + rolling 峰值口径**
- 每个窗口输出 `window_class` 三类结论（news_driven / pure_resonance / sentiment_tech），规则见输出契约；这是给人工审核的建议，保存时人可改判。
- `s_scores` 的取值口径 = 段窗内 rolling 曲线 |S| 峰值（"所见即所判"）：分类读数与标注页曲线是同一个数。

**v11 补充：共振分 S 取代同步相关性判据；三段方向链继续用于交叉验证**
- 旧输入 `correlations`（窗口 ±1h 同步 Pearson）**已移除**：同步相关对时序错位敏感、实测判别力接近随机（错位对照 lift≈1），不要再向任何"相关系数"要证据。联动强弱一律看 `s_scores` / `max_ref`（判级用 **max|S|**：≥0.5 共振、0.3–0.5 弱共振、<0.3 独立）；不要做 lag 推断，也不要自行调窗口寻找最佳解释。
- `reference_change_segments` 固定口径不变：`pre_1h`=窗口前 1h，`window`=窗口内，`post_1h`=窗口后 1h；包含标注品种本身（比较基准）。`reference_changes` 仍不包含标注品种本身。
- 推理优先级：先 `machine_class` / `max_ref` 定行情性质，再围绕最强参照找新闻，最后用三段方向链和其它大类资产交叉验证；方向链与新闻含义相反时降级 noise，**证据薄（ess<5）时下调 confidence**。
- `s_scores` 为空 = **无对照（宏观休市）≠ 无宏观新闻**：周末重大突发按「对标不可用」条款做纯事件判断，照样可标 driver。
- 示例：通胀风险下降 / 加息预期走弱，应倾向看到美元指数或美债利率走弱，并可能推升黄金或风险资产；若实际三段方向链相反，候选新闻降级 noise。
"""

AUTO_ANNOTATE_SYSTEM_PROMPT += CROSS_ASSET_SEGMENT_GUIDE
AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT += CROSS_ASSET_SEGMENT_GUIDE

AUTO_ANNOTATE_BATCH_LIMIT = 10

# 提示词版本戳：每次实质性修改两份 system prompt 时更新；随标注落库（prompt_version 列），
# 用于按提示词版本切分自动标注数据。
ANNOTATION_PROMPT_VERSION = "v12-20260710"


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


def _scales_for(symbol: str, threshold_pct: float | None, window_minutes: int | None) -> list[dict]:
    """窗口档位（news-impact-engine Phase 2，单档）：显式传参（调试路径）→ 用传入的 threshold/window；
    否则用 config.ANNOTATION_WINDOW_SCALES（单 15min 档）；再退回告警规则单档。无 net_min。"""
    if threshold_pct is not None and window_minutes is not None:
        return [{"window_minutes": int(window_minutes), "threshold_pct": float(threshold_pct),
                 "pre_minutes": CONTEXT_PRE_MINUTES_DEFAULT}]
    scales = getattr(config, "ANNOTATION_WINDOW_SCALES", {}).get(symbol)
    if scales:
        return scales
    rule = {r.symbol: r for r in load_alert_price_rules()}.get(symbol)
    if rule is None:
        return []
    return [{"window_minutes": rule.window_minutes, "threshold_pct": rule.threshold_pct,
             "pre_minutes": CONTEXT_PRE_MINUTES_DEFAULT}]


def _scale_events(rows: list[PriceSnapshot], display_cutoff: datetime, tolerance_minutes: int,
                  scale: dict, merge_gap: timedelta) -> list[dict]:
    """单档触发扫描 + 同向相邻合并 → 原始窗口 dict 列表（news-impact-engine Phase 2）。
    触发：窗口开收净 = (current − baseline_{T−wm})/baseline ≥ threshold（baseline = 窗口初开盘，含第一根 bar）。
    合并：同方向 且 **覆盖区间相邻**（新触发 start_dt 与上一窗 end_dt 间隔 ≤ merge_gap）→ 并进上一个；
          变方向 或 区间断档（> merge_gap）→ 上一个窗口走完，另起一个。
    **必须用 start_dt（不是 end_dt）**：每个触发覆盖 [current−wm, current]，窗口 start 也回看 wm；用 end_dt 比
    会无视这段覆盖、把一根没触发的连续行情误拆成两个**重叠**窗口（线上实测 BTC 20:50→21:15 与 21:10→21:25）。
    合并后的首尾净幅度仍必须满足 threshold；若合并把净幅度稀释到阈值以下，则退回为原始触发段。"""
    wm = int(scale["window_minutes"])
    threshold = float(scale["threshold_pct"])

    triggers: list[dict] = []
    for current in rows:
        if current.timestamp < display_cutoff:
            continue
        baseline = _nearest_snapshot(rows, current.timestamp - timedelta(minutes=wm),
                                     current.timestamp, tolerance_minutes)
        if baseline is None or not baseline.price:
            continue
        change_pct = ((current.price - baseline.price) / abs(baseline.price)) * 100
        if abs(change_pct) < threshold:
            continue
        triggers.append({
            "start_dt": baseline.timestamp, "end_dt": current.timestamp,
            "price_start": baseline.price, "price_end": current.price,
            "sign": 1 if change_pct >= 0 else -1,
            "asset_class": current.asset_class, "name": current.name,
        })
    if not triggers:
        return []

    triggers.sort(key=lambda t: t["end_dt"])
    events: list[list[dict]] = []
    for t in triggers:
        if (events and events[-1][-1]["sign"] == t["sign"]
                and (t["start_dt"] - events[-1][-1]["end_dt"]) <= merge_gap):   # 覆盖区间相邻才并（防重叠拆窗）
            events[-1].append(t)
        else:
            events.append([t])

    def _event_payload(ev: list[dict]) -> dict | None:
        first, last = ev[0], ev[-1]
        if not first["price_start"]:
            return None
        net_pct = ((last["price_end"] - first["price_start"]) / abs(first["price_start"])) * 100
        if abs(net_pct) < threshold:
            return None
        if (net_pct >= 0) != (first["sign"] >= 0):
            return None
        return {
            "start": first["start_dt"], "end": last["end_dt"],
            "sign": first["sign"], "segments": len(ev),
            "asset_class": first["asset_class"], "name": first["name"],
            "wm": wm, "pre": int(scale.get("pre_minutes", CONTEXT_PRE_MINUTES_DEFAULT)),
        }

    out: list[dict] = []
    for ev in events:
        payload = _event_payload(ev)
        if payload is not None:
            out.append(payload)
        elif len(ev) > 1:
            for single in ev:
                single_payload = _event_payload([single])
                if single_payload is not None:
                    out.append(single_payload)
    return out


def _behavior_segment_events(session: Session, symbol: str, display_cutoff: datetime,
                             scale: dict, asset_class: str, name: str) -> list[dict]:
    """behavior_segments（0.5 档以上）→ 标注窗口 raw dict（Phase 2：标注页唯一窗口源）。
    段证据（档位/S/机器类/人工类/簇拥 0.3 计数）随行携带，标注页工作台直接展示。"""
    from models.behavior import BehaviorSegment

    rows = (
        session.query(BehaviorSegment)
        .filter(BehaviorSegment.symbol == symbol,
                BehaviorSegment.end_dt >= display_cutoff,
                BehaviorSegment.tier_idx >= 1)
        .order_by(BehaviorSegment.start_dt.asc())
        .all()
    )
    pad = timedelta(hours=1)
    minors = (
        session.query(BehaviorSegment.start_dt, BehaviorSegment.end_dt)
        .filter(BehaviorSegment.symbol == symbol,
                BehaviorSegment.tier_idx == 0,
                BehaviorSegment.end_dt >= display_cutoff - pad)
        .all()
    ) if rows else []
    out = []
    for r in rows:
        cluster03 = sum(1 for ms, me in minors if ms <= r.end_dt + pad and me >= r.start_dt - pad)
        out.append({
            "start": r.start_dt, "end": r.end_dt, "sign": r.direction, "segments": 1,
            "asset_class": asset_class, "name": name,
            "wm": int(scale["window_minutes"]),
            "pre": int(scale.get("pre_minutes", CONTEXT_PRE_MINUTES_DEFAULT)),
            "tier_idx": r.tier_idx, "tier_max": r.tier_max,
            "s_scores": json.loads(r.s_scores) if r.s_scores else {},
            "machine_class": r.classification, "human_class": r.human_class,
            "cluster03_count": cluster03,
        })
    return out


def load_price_windows(
    session: Session,
    symbol: str,
    hours: int,
    threshold_pct: float | None = None,
    window_minutes: int | None = None,
) -> list[PriceWindowSchema]:
    """单 15min 开收净标注窗口（news-impact-engine Phase 2）：开收净触发 + 同向相邻合并、
    变向/5min 断档收口。窗口 compute-on-read 不落库。显式传 threshold/window 走单档调试路径。"""
    scales = _scales_for(symbol, threshold_pct, window_minutes)
    if not scales:
        return []
    hours = max(1, min(int(hours or 72), 24 * 30))
    scale = scales[0]                                  # 单档（Phase 2）
    max_wm = int(scale["window_minutes"])
    cutoff = utc_now_naive() - timedelta(hours=hours, minutes=max_wm + 10)
    rows = (
        session.query(PriceSnapshot)
        .filter(PriceSnapshot.symbol == symbol, PriceSnapshot.timestamp >= cutoff)
        .order_by(PriceSnapshot.timestamp.asc())
        .all()
    )
    display_cutoff = utc_now_naive() - timedelta(hours=hours)
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)
    ref_rows = _load_reference_rows(
        session,
        cutoff - timedelta(minutes=REFERENCE_SEGMENT_MINUTES + tolerance_minutes + 5),
    )
    merge_gap = timedelta(minutes=max(1, int(getattr(config, "ANNOTATION_EVENT_MERGE_GAP_MINUTES", 60))))
    price_at = {row.timestamp: row.price for row in rows}

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

    def _match_annotation(w_start: datetime, w_end: datetime) -> int | None:
        """精确命中优先；否则重叠≥50%（以较短一方为分母）取最佳——段边界(0.3基座)比旧 0.5 窗口宽。"""
        exact = annotation_index.get((w_start, w_end))
        if exact is not None:
            return exact
        best_id, best_ratio = None, 0.0
        for row in annotation_rows:
            if row.window_start is None or row.window_end is None:
                continue
            overlap = (min(row.window_end, w_end) - max(row.window_start, w_start)).total_seconds()
            if overlap <= 0:
                continue
            shorter = min((row.window_end - row.window_start).total_seconds(),
                          (w_end - w_start).total_seconds()) or 1.0
            ratio = overlap / shorter
            if ratio > best_ratio:
                best_id, best_ratio = row.id, ratio
        return best_id if best_ratio >= 0.5 else None

    # Phase 2（2026-07-09 拍板）：标注页唯一窗口源 = behavior_segments（0.5 档以上，0.3 只作簇拥上下文）。
    # 显式传 threshold/window 的调试路径仍走原始扫描（回放/校验用）。
    if threshold_pct is None and window_minutes is None and rows:
        merged = _behavior_segment_events(session, symbol, display_cutoff, scale,
                                          rows[-1].asset_class, rows[-1].name)
    else:
        merged = _scale_events(rows, display_cutoff, tolerance_minutes, scale, merge_gap)

    # A策略①（2026-06-28 简化）：**只冻结「最新且仍在生长边缘」的那一个窗口**——它后面还没有更晚的
    # 窗口/价格来确认它走完，可能随新 bar 继续合并。更早的窗口后面都已有更晚窗口 → 已走完 → 可标。
    # 例外：最新窗口若已很久没动（收盘/静默，超过 live 余量）也判走完、可标。已标窗口被 backfill 改动
    # 由 needs_review 兜底，不靠冻结。
    latest_end = max((m["end"] for m in merged), default=None)
    live_edge_cutoff = utc_now_naive() - timedelta(minutes=int(getattr(config, "ANNOTATION_SETTLE_MARGIN_MINUTES", 30)))
    windows: list[tuple[datetime, PriceWindowSchema]] = []
    for m in merged:
        p_start = price_at.get(m["start"])
        p_end = price_at.get(m["end"])
        if not p_start or not p_end:
            continue
        net_pct = (p_end - p_start) / abs(p_start) * 100
        windows.append((m["end"], PriceWindowSchema(
            symbol=symbol,
            asset_class=m["asset_class"],
            name=m["name"],
            window_start=timestamp_pair(m["start"]),
            window_end=timestamp_pair(m["end"]),
            configured_window_minutes=m["wm"],
            actual_window_minutes=round((m["end"] - m["start"]).total_seconds() / 60, 1),
            price_start=p_start,
            price_end=p_end,
            change_pct=net_pct,
            segment_count=m["segments"],
            annotation_id=_match_annotation(m["start"], m["end"]),
            annotatable=not (m["end"] == latest_end and m["end"] > live_edge_cutoff),
            is_primary=True,
            tier_idx=m.get("tier_idx"),
            tier_max=m.get("tier_max"),
            s_scores=m.get("s_scores", {}),
            machine_class=m.get("machine_class"),
            human_class=m.get("human_class"),
            cluster03_count=m.get("cluster03_count", 0),
            context_pre_minutes=m["pre"],
            references=_reference_changes_for_window(
                ref_rows,
                m["start"],
                m["end"],
                tolerance_minutes,
                symbol,
                correlations_by_symbol=_reference_correlations_for_window(session, symbol, m["start"], m["end"]),
            ),
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
        raise ValueError("window_start_utc/window_end_utc is missing or invalid")
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


def _write_back_window_class(session: Session, symbol: str,
                             window_start: datetime, window_end: datetime,
                             window_class: str | None) -> None:
    """Phase 2（2026-07-09）：标注保存 = 人工审核。把窗口级三类回写到重叠的行为段 human_class。
    匹配：同 symbol、tier≥0.5 档、区间重叠 ≥50%（以较短一方为分母——段边界是 0.3 基座合并，
    通常比旧 0.5 窗口宽）。找不到段时静默跳过（并行期/历史窗口兜底），不影响标注本身。"""
    if not window_class:
        return
    from models.behavior import BehaviorSegment
    from services.behavior_classifier import WINDOW_CLASSES

    if window_class not in WINDOW_CLASSES:
        raise ValueError(f"window_class 非法: {window_class!r}（可选: {', '.join(WINDOW_CLASSES)}）")
    candidates = (
        session.query(BehaviorSegment)
        .filter(BehaviorSegment.symbol == symbol,
                BehaviorSegment.tier_idx >= 1,
                BehaviorSegment.start_dt <= window_end,
                BehaviorSegment.end_dt >= window_start)
        .all()
    )
    best, best_ratio = None, 0.0
    for seg in candidates:
        overlap = (min(seg.end_dt, window_end) - max(seg.start_dt, window_start)).total_seconds()
        shorter = min((seg.end_dt - seg.start_dt).total_seconds(),
                      (window_end - window_start).total_seconds()) or 1.0
        ratio = overlap / shorter
        if ratio > best_ratio:
            best, best_ratio = seg, ratio
    if best is not None and best_ratio >= 0.5:
        best.human_class = window_class
        best.human_confirmed_at = datetime.utcnow()


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
    pre_minutes = request.context_pre_minutes or CONTEXT_PRE_MINUTES_DEFAULT
    existing.context_start = window_start - timedelta(minutes=pre_minutes)
    existing.context_end = window_end + timedelta(minutes=CONTEXT_POST_MINUTES_DEFAULT)
    existing.threshold_pct = request.threshold_pct
    existing.price_start = start_snapshot.price
    existing.price_end = end_snapshot.price
    existing.change_pct = ((end_snapshot.price - start_snapshot.price) / abs(start_snapshot.price)) * 100 if start_snapshot.price else None
    existing.reference_changes = json.dumps(
        _reference_changes_for_annotation(session, window_start, window_end, request.symbol),
        ensure_ascii=False,
    )
    # v2 标签归一化落库；causal_news_ids / no_clear_news 自 v2 起为派生兼容字段
    roles, reaction, confidence, selected_ids, no_clear = _normalize_v2_labels(request)
    existing.news_roles = json.dumps({str(k): v for k, v in roles.items()}, ensure_ascii=False)
    existing.market_reaction_type = reaction
    existing.confidence = confidence
    existing.causal_news_ids = json.dumps(selected_ids, ensure_ascii=False)
    if request.candidate_news_ids is not None:
        existing.candidate_news_ids = json.dumps(request.candidate_news_ids, ensure_ascii=False)
    existing.no_clear_news = no_clear
    existing.notes = (request.notes or "").strip() or None
    existing.labeler = (request.labeler or "").strip() or None
    if request.auto_reasoning is not None:
        existing.auto_reasoning = request.auto_reasoning.strip() or None
    if request.auto_summary is not None:
        existing.auto_summary = request.auto_summary.strip() or None
    # AI 原始标注快照 + 提示词版本：人机分歧（auto_news_roles vs news_roles）是难例信号。
    # 版本戳取保存时的当前常量（与自动标注时刻可能差几分钟，可接受）。
    if request.auto_news_roles is not None:
        existing.auto_news_roles = json.dumps(
            {str(k): v for k, v in request.auto_news_roles.items()}, ensure_ascii=False
        )
    if request.auto_reasoning is not None or request.auto_summary is not None:
        existing.prompt_version = ANNOTATION_PROMPT_VERSION
    existing.updated_at = utc_now_naive()
    # Phase 2：标注保存 = 人工审核 → 窗口级三类回写行为段 human_class（重叠≥50% 匹配）。
    _write_back_window_class(session, request.symbol, window_start, window_end, request.window_class)
    session.commit()
    return AnnotationResponse(id=existing.id)


def _parse_news_roles(raw: str | None) -> dict[int, str]:
    """news_roles JSON（{news_id: causal_role}）→ dict[int,str]；坏数据回退空。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    out: dict[int, str] = {}
    for key, role in value.items():
        try:
            nid = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(role, str) and role in NEWS_CAUSAL_ROLES and role != "noise":
            out[nid] = role
    return out


def _derive_compat_fields(roles: dict[int, str], reaction: str | None) -> tuple[list[int], bool]:
    """v2.1 roles → 旧消费方兼容字段：selected = 全部 driver；
    no_clear = 无 driver（或 reaction 显式 no_news_driver）。"""
    selected = [nid for nid, r in roles.items() if r == "driver"]
    no_clear = not selected
    if reaction == "no_news_driver":
        no_clear = True
    return selected, no_clear


def _normalize_v2_labels(
    request: AnnotationCreateRequest,
) -> tuple[dict[int, str], str | None, float | None, list[int], bool]:
    """落库前把请求归一化为 v2 标签（docs/specs/annotation-v2.md §1 兼容映射）。

    返回 (news_roles, market_reaction_type, confidence, selected_news_ids, no_clear_news)。
    旧格式请求（news_roles=None）：selected 第一条 → primary、其余 → secondary；
    no_clear → market_reaction_type='no_news_driver'。非法枚举值直接 ValueError。"""
    if request.news_roles is not None:
        roles: dict[int, str] = {}
        for nid, role in request.news_roles.items():
            if role not in NEWS_CAUSAL_ROLES:
                raise ValueError(f"非法 causal_role: {role!r}")
            if role != "noise":           # noise 是默认值，不落库
                roles[int(nid)] = role
        reaction = request.market_reaction_type
    else:
        roles = {int(nid): "driver" for nid in request.selected_news_ids}
        reaction = request.market_reaction_type or ("no_news_driver" if request.no_clear_news else None)

    if reaction is not None and reaction not in MARKET_REACTION_TYPES:
        raise ValueError(f"非法 market_reaction_type: {reaction!r}")

    has_driver = any(role == "driver" for role in roles.values())
    has_redundant = any(role == "redundant" for role in roles.values())
    if has_redundant and not has_driver:
        raise ValueError("redundant requires at least one driver news item")
    if reaction in {"macro_policy", "event_driven"} and not has_driver:
        raise ValueError(f"{reaction} requires at least one driver news item")
    if reaction == "no_news_driver" and has_driver:
        raise ValueError("no_news_driver cannot be saved with driver news items")

    confidence = request.confidence
    if request.news_roles is not None and confidence is None:
        raise ValueError("归因置信度不能为空，请先选择高 / 中 / 低置信度")
    if confidence is not None:
        confidence = max(0.0, min(1.0, float(confidence)))

    selected, no_clear = _derive_compat_fields(roles, reaction)
    return roles, reaction, confidence, selected, no_clear


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
        news_roles=_parse_news_roles(row.news_roles),
        market_reaction_type=row.market_reaction_type,
        confidence=row.confidence,
        auto_news_roles=_parse_news_roles(row.auto_news_roles),
        prompt_version=row.prompt_version,
        eval_set=bool(row.eval_set),
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
        ref_rows = _load_reference_rows(session, _reference_rows_cutoff(earliest, tolerance_minutes))
    else:
        ref_rows = {}
    # Phase3b A策略③：当前重算窗口里"边界还对得上"的标注 id（按 symbol 缓存）。
    # 一条标注的 id 不在其中 = 它的 (start,end) 被 backfill 劈/并/挪了 → needs_review。
    cur_ids_by_symbol: dict[str, set[int]] = {}

    def _cur_ann_ids(sym: str) -> set[int]:
        if sym not in cur_ids_by_symbol:
            wins = load_price_windows(session, sym, hours)
            cur_ids_by_symbol[sym] = {w.annotation_id for w in wins if w.annotation_id is not None}
        return cur_ids_by_symbol[sym]

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
            references=_reference_changes_for_window(
                ref_rows,
                row.window_start,
                row.window_end,
                tolerance_minutes,
                row.symbol,
                correlations_by_symbol=_reference_correlations_for_window(
                    session, row.symbol, row.window_start, row.window_end
                ),
            ),
            no_clear_news=bool(row.no_clear_news),
            selected_count=len(selected_ids),
            market_reaction_type=row.market_reaction_type,
            confidence=row.confidence,
            eval_set=bool(row.eval_set),
            needs_review=(row.id not in _cur_ann_ids(row.symbol)),
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
    signals: dict | None = None,
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
            **(signals or {}),
        },
        "candidate_news": items,
    }
    return f"共 {len(items)} 条候选新闻。\n{json.dumps(body, ensure_ascii=False)}"


def _call_deepseek_reasoner(user_content: str) -> tuple[str, str, float]:
    """单轮：system + user。返回 (content, reasoning_content, duration_seconds)。"""
    return _call_deepseek_reasoner_messages([
        {"role": "system", "content": AUTO_ANNOTATE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ])


def _call_deepseek_reasoner_messages(messages: list[dict]) -> tuple[str, str, float]:
    """调 DeepSeek v4-pro thinking，传**完整 messages**（支持多轮，refine 互动重标用）。
    返回 (content, reasoning_content, duration_seconds)。"""
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，无法调用自动标注")

    payload = {
        "model": config.DEEPSEEK_REASONER_MODEL,
        "messages": messages,
        "thinking": {
            "type": "enabled",
            "reasoning_effort": config.DEEPSEEK_REASONER_EFFORT,
        },
        "response_format": {"type": "json_object"},
        # max_tokens 同时覆盖 reasoning_content + content；effort=max 时推理可能吃掉大半，
        # 4000 偶发把 content 截成空（DeepSeek 返回空 content 报错），留足余量。
        "max_tokens": 8000,
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


class AutoV2Parsed(NamedTuple):
    """v2 自动标注解析结果；selected/no_clear 为派生兼容字段。"""
    news_roles: dict[int, str]
    market_reaction_type: str | None
    confidence: float | None
    selected_news_ids: list[int]
    no_clear_news: bool
    summary: str
    window_class: str | None = None   # v12：窗口级三类建议


def _loads_reasoner_json(raw: str) -> dict:
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
    return data


def _extract_v2_labels(data: dict, valid_ids: set[int]) -> AutoV2Parsed:
    """从 reasoner 的 v2 JSON（news_roles / market_reaction_type / confidence / summary）
    提取标签：幻觉 id、非法 role/type 直接丢弃，confidence clamp 到 [0,1]。"""
    roles: dict[int, str] = {}
    raw_roles = data.get("news_roles")
    if isinstance(raw_roles, dict):
        for key, role in raw_roles.items():
            try:
                nid = int(key)
            except (TypeError, ValueError):
                continue
            if nid not in valid_ids:
                continue
            if isinstance(role, str) and role in NEWS_CAUSAL_ROLES and role != "noise":
                roles[nid] = role

    reaction = data.get("market_reaction_type")
    if not isinstance(reaction, str) or reaction not in MARKET_REACTION_TYPES:
        reaction = None

    confidence = data.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence))) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None

    summary = data.get("summary") or ""
    if not isinstance(summary, str):
        summary = str(summary)

    # v12：窗口级三类建议（非法值丢弃，不猜）
    from services.behavior_classifier import WINDOW_CLASSES
    window_class = data.get("window_class")
    if not isinstance(window_class, str) or window_class not in WINDOW_CLASSES:
        window_class = None

    selected, no_clear = _derive_compat_fields(roles, reaction)
    return AutoV2Parsed(roles, reaction, confidence, selected, no_clear, summary.strip()[:240], window_class)


def _parse_auto_annotate_v2(raw: str, valid_ids: set[int]) -> AutoV2Parsed:
    """单窗口 reasoner 输出 → v2 标签。"""
    return _extract_v2_labels(_loads_reasoner_json(raw), valid_ids)


def auto_annotate(session: Session, request: AutoAnnotateRequest) -> AutoAnnotateResponse:
    """**只调用模型，不写库**。前端拿到结果后由用户 review，再调 POST /api/annotations 保存。"""
    window_start = parse_datetime(request.window_start_utc)
    window_end = parse_datetime(request.window_end_utc)
    if window_start is None or window_end is None:
        raise ValueError("window_start_utc/window_end_utc 不能为空")

    start_snapshot = _find_window_snapshot(session, request.symbol, window_start)
    end_snapshot = _find_window_snapshot(session, request.symbol, window_end)

    pre_minutes = request.context_pre_minutes or CONTEXT_PRE_MINUTES_DEFAULT
    context_start = window_start - timedelta(minutes=pre_minutes)
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
        # 没有候选新闻就直接返回 no_clear，省掉一次 API 调用。
        return AutoAnnotateResponse(
            selected_news_ids=[],
            no_clear_news=True,
            news_roles={},
            market_reaction_type="no_news_driver",
            confidence=None,
            window_class=None,
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
        signals=_window_signals_payload(session, request.symbol, window_start, window_end),
    )

    logger.info(
        f"[AutoAnnotate] 调 DeepSeek {config.DEEPSEEK_REASONER_MODEL}，"
        f"effort={config.DEEPSEEK_REASONER_EFFORT}，候选 {len(candidate_news)} 条新闻"
    )
    content, reasoning, duration = _call_deepseek_reasoner(user_payload)
    valid_ids = {int(row.id) for row in candidate_news}
    parsed = _parse_auto_annotate_v2(content, valid_ids)
    logger.info(
        f"[AutoAnnotate] 完成，耗时 {duration:.1f}s，标注 {len(parsed.news_roles)} 条非噪音，"
        f"type={parsed.market_reaction_type} no_clear={parsed.no_clear_news}"
    )

    return AutoAnnotateResponse(
        selected_news_ids=parsed.selected_news_ids,
        no_clear_news=parsed.no_clear_news,
        news_roles=parsed.news_roles,
        market_reaction_type=parsed.market_reaction_type,
        confidence=parsed.confidence,
        window_class=parsed.window_class,
        summary=parsed.summary,
        reasoning=reasoning,
        model=config.DEEPSEEK_REASONER_MODEL,
        duration_seconds=round(duration, 2),
        candidate_count=len(candidate_news),
    )


def _fetch_candidate_news(session: Session, window_start: datetime, window_end: datetime,
                          pre_minutes: int) -> list[NewsItem]:
    """窗口 ±（pre_minutes / 默认 post）范围内的候选新闻，时间升序。"""
    context_start = window_start - timedelta(minutes=pre_minutes)
    context_end = window_end + timedelta(minutes=CONTEXT_POST_MINUTES_DEFAULT)
    return (
        session.query(NewsItem)
        .filter(
            NewsItem.source.in_(_annotation_news_sources()),
            NewsItem.timestamp >= context_start,
            NewsItem.timestamp <= context_end,
        )
        .order_by(NewsItem.timestamp.asc())
        .all()
    )


def auto_annotate_refine(session: Session, request: AutoAnnotateRefineRequest) -> AutoAnnotateResponse:
    """互动重标（Part C）：把原始 payload + 上一轮输出 + 用户纠正当作**多轮对话**再调 reasoner。
    **只调模型、不写库**，前端拿新结果套用到角色上，用户满意再保存。"""
    window_start = parse_datetime(request.window_start_utc)
    window_end = parse_datetime(request.window_end_utc)
    if window_start is None or window_end is None:
        raise ValueError("window_start_utc/window_end_utc 不能为空")
    if not (request.user_message or "").strip():
        raise ValueError("纠正内容（user_message）不能为空")

    pre_minutes = request.context_pre_minutes or CONTEXT_PRE_MINUTES_DEFAULT
    candidate_news = _fetch_candidate_news(session, window_start, window_end, pre_minutes)
    if not candidate_news:
        raise ValueError("窗口前后没有候选新闻，无法重标")

    start_snapshot = _find_window_snapshot(session, request.symbol, window_start)
    end_snapshot = _find_window_snapshot(session, request.symbol, window_end)
    user_payload = _build_auto_annotate_user_payload(
        request,
        candidate_news,
        start_snapshot.price if start_snapshot else None,
        end_snapshot.price if end_snapshot else None,
        ((end_snapshot.price - start_snapshot.price) / abs(start_snapshot.price) * 100)
        if start_snapshot and end_snapshot and start_snapshot.price else None,
        reference_changes=_reference_changes_for_annotation(session, window_start, window_end, request.symbol),
        signals=_window_signals_payload(session, request.symbol, window_start, window_end),
    )

    prior = {
        "news_roles": {str(k): v for k, v in (request.prior_news_roles or {}).items()},
        "confidence": request.prior_confidence,
        "summary": request.prior_summary or "",
    }
    messages = [
        {"role": "system", "content": AUTO_ANNOTATE_SYSTEM_PROMPT},
        {"role": "user", "content": user_payload},
        {"role": "assistant", "content": json.dumps(prior, ensure_ascii=False)},
        {"role": "user", "content":
            f"用户对上面这版标注的纠正意见：{request.user_message.strip()}\n"
            f"请据此重新标注这个窗口，仍然只返回同样格式的 JSON。"},
    ]
    logger.info(f"[AutoAnnotateRefine] 互动重标，候选 {len(candidate_news)} 条，纠正: {request.user_message.strip()[:60]}")
    content, reasoning, duration = _call_deepseek_reasoner_messages(messages)
    parsed = _parse_auto_annotate_v2(content, {int(row.id) for row in candidate_news})

    return AutoAnnotateResponse(
        selected_news_ids=parsed.selected_news_ids,
        no_clear_news=parsed.no_clear_news,
        news_roles=parsed.news_roles,
        market_reaction_type=parsed.market_reaction_type,
        confidence=parsed.confidence,
        window_class=parsed.window_class,
        summary=parsed.summary,
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
        _load_reference_rows(session, _reference_rows_cutoff(min(parsed_starts), tolerance_minutes))
        if parsed_starts else {}
    )

    for idx, w in enumerate(windows):
        window_start = parse_datetime(w.window_start_utc)
        window_end = parse_datetime(w.window_end_utc)
        if window_start is None or window_end is None:
            raise ValueError(f"窗口 {idx} 的 window_start_utc / window_end_utc 不能为空")

        start_snapshot = _find_window_snapshot(session, w.symbol, window_start)
        end_snapshot = _find_window_snapshot(session, w.symbol, window_end)

        context_start = window_start - timedelta(minutes=w.context_pre_minutes or CONTEXT_PRE_MINUTES_DEFAULT)
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
            **_window_signals_payload(session, w.symbol, window_start, window_end),
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
) -> dict[int, tuple[AutoV2Parsed, str]]:
    """从 reasoner 返回的 JSON 解析 items（v2 标签），按 window_id 映射回去；过滤幻觉。

    返回 {window_id: (AutoV2Parsed, reasoning)}。reasoning 是模型在结构化 JSON 里
    给出的**该窗口专属**解释，与 message.reasoning_content（整批 thinking）不同。"""
    data = _loads_reasoner_json(raw)
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("reasoner 批量返回缺少 items 列表")

    by_window: dict[int, tuple[AutoV2Parsed, str]] = {}
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
        parsed = _extract_v2_labels(item, valid_ids)
        reasoning = item.get("reasoning") or ""
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)
        by_window[wid] = (parsed, reasoning.strip())

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

    _empty = AutoV2Parsed({}, None, None, [], True, "")
    results: list[AutoAnnotateBatchItem] = []
    for idx, meta in enumerate(window_metas):
        # 模型可能漏给某个窗口；漏的视为 no_clear，summary 留空，
        # 让前端 UI 提示用户检查/重新单独跑该窗口。
        item_parsed, item_reasoning = parsed.get(idx, (_empty, ""))
        results.append(AutoAnnotateBatchItem(
            symbol=meta["symbol"],
            window_start_utc=meta["window_start_utc"],
            window_end_utc=meta["window_end_utc"],
            selected_news_ids=item_parsed.selected_news_ids,
            no_clear_news=item_parsed.no_clear_news,
            news_roles=item_parsed.news_roles,
            market_reaction_type=item_parsed.market_reaction_type,
            confidence=item_parsed.confidence,
            window_class=item_parsed.window_class,
            summary=item_parsed.summary,
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


def set_eval_set(session: Session, annotation_id: int, value: bool) -> int:
    """把标注冻结进/移出评估集（训练导出默认排除 eval_set 行）。"""
    row = session.query(NewsPriceAnnotation).filter(NewsPriceAnnotation.id == annotation_id).first()
    if row is None:
        raise ValueError(f"标注 #{annotation_id} 不存在")
    row.eval_set = bool(value)
    session.commit()
    return annotation_id


def export_training_jsonl(session: Session, days: int = 365, split: str = "train") -> list[str]:
    """标注训练集导出（docs/specs/annotation-v2.md §4）：每行一个窗口样本。

    候选新闻全量展开（未标条目 causal_role=noise，即负样本），窗口带同期对标涨跌。
    schema_version：confidence 非空 → 2（v2 人工标注），否则 1（旧迁移样本，低保真）。
    split：train（默认，排除评估集）/ eval（只要评估集）/ all。"""
    cutoff = utc_now_naive() - timedelta(days=max(1, int(days or 365)))
    query = (
        session.query(NewsPriceAnnotation)
        .filter(NewsPriceAnnotation.window_end >= cutoff)
    )
    if split == "train":
        query = query.filter(NewsPriceAnnotation.eval_set.is_(False))
    elif split == "eval":
        query = query.filter(NewsPriceAnnotation.eval_set.is_(True))
    elif split != "all":
        raise ValueError(f"非法 split: {split!r}（train/eval/all）")
    rows = query.order_by(NewsPriceAnnotation.window_end.asc()).all()
    lines: list[str] = []
    tolerance_minutes = max(config.SCAN_INTERVALS["price"] * 2, 1)
    starts = [row.window_start for row in rows if row.window_start]
    ref_rows = (
        _load_reference_rows(session, min(starts) - timedelta(minutes=tolerance_minutes + 5))
        if starts
        else {}
    )
    for row in rows:
        roles = _parse_news_roles(row.news_roles)
        cand_ids = _parse_news_ids(row.candidate_news_ids)
        if not cand_ids:
            cand_ids = _parse_news_ids(row.causal_news_ids)
        news_rows = (
            session.query(NewsItem).filter(NewsItem.id.in_(cand_ids)).all() if cand_ids else []
        )
        by_id = {n.id: n for n in news_rows}
        candidates = []
        for nid in cand_ids:
            n = by_id.get(nid)
            candidates.append({
                "id": nid,
                "time_bj": (n.timestamp + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M") if n and n.timestamp else None,
                "source": n.source if n else None,
                "title": (n.title or "") if n else "",
                "content": (n.content or "")[:1000] if n else "",
                "llm_score": n.llm_importance if n else None,
                "causal_role": roles.get(nid, "noise"),    # 人/LLM 直接标的角色（driver/redundant/noise）
            })
        selected_ids = _parse_news_ids(row.causal_news_ids)
        # 合流语义：优先读落库快照（audit-fixes），缺失才按窗口重算（带 ref_rows 预载）；
        # 派生信号（s_scores/三段，v11）保持 compute-on-read（feat/onchain-market-overview）。
        reference_changes = _parse_reference_changes(row.reference_changes)
        if reference_changes is None and row.window_start and row.window_end:
            reference_changes = _reference_changes_for_annotation(
                session, row.window_start, row.window_end, row.symbol, ref_rows=ref_rows
            )
        signals = (
            _window_signals_payload(session, row.symbol, row.window_start, row.window_end)
            if row.window_start and row.window_end else {}
        )
        record = {
            "schema_version": 2 if row.confidence is not None else 1,
            "window": {
                "symbol": row.symbol,
                "asset_class": row.asset_class,
                "start_utc": row.window_start.isoformat() if row.window_start else None,
                "end_utc": row.window_end.isoformat() if row.window_end else None,
                "price_start": row.price_start,
                "price_end": row.price_end,
                "change_pct": row.change_pct,
                "threshold_pct": row.threshold_pct,
                "reference_changes": reference_changes or {},
                "s_scores": signals.get("s_scores", {}),
                "reference_change_segments": signals.get("reference_change_segments", {}),
            },
            "candidates": candidates,
            "labels": {
                "news_roles": {str(k): v for k, v in roles.items()},
                "selected_news_ids": selected_ids,
                "no_clear_news": bool(row.no_clear_news),
                "market_reaction_type": row.market_reaction_type,
                "confidence": row.confidence,
                "summary": row.notes or row.auto_summary or "",
            },
            # AI 原始标注（人改前）：与 labels 的差异 = 人机分歧难例信号
            "auto_labels": {
                "news_roles": {str(k): v for k, v in _parse_news_roles(row.auto_news_roles).items()},
                "summary": row.auto_summary or "",
            },
            "prompt_version": row.prompt_version,
            "eval_set": bool(row.eval_set),
            "labeler": row.labeler,
            "annotated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    return lines


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
