"""板块资金流（净流入）计算与勾稽 —— 净流入相关的所有口径只在这一个文件里。

数据来源：BMAC 宽表 pivot（2026-08-07 服务器补丁后）新增两个矩阵
  - quote_volume                    每根 1h bar 的总成交额（USDT）
  - taker_buy_quote_asset_volume    其中「主动买入」（吃单方向为买）的成交额

口径（设计稿 docs/superpowers/specs/2026-08-07-sector-net-inflow-design.md §2）：
  单 bar 净流入 = 主动买入额 − 主动卖出额 = 2 × taker_buy − quote_volume
  窗口值        = 最近 N 根 bar 求和（N = 1 / 24 / 168 / 720，与涨跌四档对齐）
  强度比率      = 窗口净流入 ÷ 窗口总成交额（不落库，读时现算）
  板块级        = 成分币先求和再算比率（成交量加权），现货与永续**永不混加**

安全底线：check_flow_gate() 不通过的市场，该轮资金流全部作废写 None，
涨跌链路完全不受影响 —— 宁可页面显示「—」，不可显示错数。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd

import config
from scanners.sector_scanner import _slice_close_as_of, normalize_pivot_symbol

# 宽表里两个新矩阵的键名（与服务器补丁写入的列名一致，零映射层）
QUOTE_VOLUME_KEY = "quote_volume"
TAKER_BUY_KEY = "taker_buy_quote_asset_volume"

# 窗口键 → 回看 bar 数。键名同时用于 DB 列名与 API 字段名，全栈一致。
FLOW_WINDOWS: dict[str, int] = {"1h": 1, "24h": 24, "168h": 168, "720h": 720}

# 恒等式判定的相对容差：允许 taker_buy 超出 quote_volume 百万分之一（浮点累加噪声）
_IDENTITY_RTOL = 1e-6


def check_flow_gate(pivot: Optional[dict]) -> Optional[str]:
    """资金流勾稽门。通过返回 None，不通过返回中文失败原因（进告警正文）。

    四项检查，任一不过即整市场作废：
      1. 两个新矩阵都在
      2. 与 close 的行索引、列集合完全一致（防半写/陈旧/串表）
      3. 0 <= 主动买入额 <= 总成交额 的逐格违规占比不超阈值（防数据串列）
      4. 最新 bar 上「成交额缺失率 − 收盘价缺失率」不超阈值（防新字段大面积没写）
    """
    if pivot is None:
        return "pivot 未加载"

    close = pivot.get("close")
    if close is None:
        return "缺字段: close"

    missing = [k for k in (QUOTE_VOLUME_KEY, TAKER_BUY_KEY) if pivot.get(k) is None]
    if missing:
        return f"缺字段: {', '.join(missing)}（服务器补丁未生效或已被升级覆盖）"

    qv: pd.DataFrame = pivot[QUOTE_VOLUME_KEY]
    tb: pd.DataFrame = pivot[TAKER_BUY_KEY]

    for name, frame in ((QUOTE_VOLUME_KEY, qv), (TAKER_BUY_KEY, tb)):
        if not frame.index.equals(close.index):
            return f"{name} 与 close 行索引不对齐（{len(frame.index)} vs {len(close.index)} 行）"
        if list(frame.columns) != list(close.columns):
            return f"{name} 与 close 列集合不对齐（{len(frame.columns)} vs {len(close.columns)} 列）"

    if close.empty:
        return "close 为空表"

    # 3) 恒等式：0 <= taker_buy <= quote_volume（只看两边都有值的格子）
    both = qv.notna() & tb.notna()
    total = int(both.to_numpy().sum())
    if total == 0:
        return "无任何有效成交额格子"
    negative = (tb < 0) & both
    over = (tb > qv * (1 + _IDENTITY_RTOL)) & both
    violations = int((negative | over).to_numpy().sum())
    max_ratio = float(getattr(config, "FLOW_IDENTITY_VIOLATION_MAX_RATIO", 0.001))
    ratio = violations / total
    if ratio > max_ratio:
        return (f"恒等式违规占比 {ratio:.4%} 超过上限 {max_ratio:.4%}"
                f"（{violations}/{total} 格 taker_buy 为负或大于 quote_volume）")

    # 4) 最新 bar 的缺失缺口：收盘价有值、成交额没值的比例
    latest_close = close.iloc[-1]
    latest_qv = qv.iloc[-1]
    width = len(close.columns)
    if width == 0:
        return "close 无任何列"
    gap = (latest_qv.isna().sum() - latest_close.isna().sum()) / width
    max_gap = float(getattr(config, "FLOW_NAN_GAP_MAX", 0.05))
    if gap > max_gap:
        return f"最新 bar 成交额缺失率比收盘价高 {gap:.2%}，超过上限 {max_gap:.2%}"

    return None


def per_symbol_flows(
    pivot: Optional[dict],
    *,
    as_of: Optional[datetime],
) -> dict[str, dict[str, float]]:
    """单市场 pivot → {规范化 symbol: {net_1h, qv_1h, net_24h, qv_24h, ...}}。

    调用前必须先过 check_flow_gate()。约定：
    - 窗口 = 截到 as_of 的最近 N 根 bar；bar 不够就按实际有的求和（新币不作废）
    - 某根 bar 只要 net/qv 任一为缺失，这根 bar 两边都不计入 —— 强度比率才自洽
    - 整窗口无有效 bar 的币不产出该窗口的键（区别于「净流入恰好为 0」）
    - 同一市场内多个交易对归一到同一 symbol 时合并求和（BEAMX/BEAM 这类）
    """
    if pivot is None:
        return {}
    qv_all = pivot.get(QUOTE_VOLUME_KEY)
    tb_all = pivot.get(TAKER_BUY_KEY)
    if qv_all is None or tb_all is None:
        return {}

    qv_all = _slice_close_as_of(qv_all, as_of)
    tb_all = _slice_close_as_of(tb_all, as_of)
    if qv_all.empty:
        return {}

    out: dict[str, dict[str, float]] = {}
    for window, lookback in FLOW_WINDOWS.items():
        qv = qv_all.iloc[-lookback:]
        tb = tb_all.iloc[-lookback:]
        valid = qv.notna() & tb.notna()
        qv_sum = qv.where(valid).sum(min_count=1)
        tb_sum = tb.where(valid).sum(min_count=1)
        net_sum = 2.0 * tb_sum - qv_sum
        for col in qv_all.columns:
            qv_val = qv_sum.get(col)
            if qv_val is None or pd.isna(qv_val):
                continue
            nsym = normalize_pivot_symbol(str(col))
            if not nsym:
                continue
            bucket = out.setdefault(nsym, {})
            bucket[f"qv_{window}"] = bucket.get(f"qv_{window}", 0.0) + float(qv_val)
            bucket[f"net_{window}"] = bucket.get(f"net_{window}", 0.0) + float(net_sum[col])
    return out


MARKETS = ("spot", "swap")


@dataclass
class FlowSide:
    """单市场、单板块的资金流聚合。net/qv 的键是窗口名（1h/24h/168h/720h）。"""
    tokens: int
    net: dict[str, Optional[float]]
    qv: dict[str, Optional[float]]


def aggregate_side(
    per_symbol: dict[str, dict[str, float]],
    members: set[str],
) -> Optional[FlowSide]:
    """把板块成分币的币级资金流加总。无任何成分币有数据时返回 None。

    tokens = 该市场下**实际有资金流数据**的成分币数，与涨跌口径的 token_count 可以不等
    （一个板块可能 30 个币有现货、35 个币有永续）。
    """
    matched = [sym for sym in members if sym in per_symbol]
    if not matched:
        return None

    net: dict[str, Optional[float]] = {}
    qv: dict[str, Optional[float]] = {}
    for window in FLOW_WINDOWS:
        net_total = 0.0
        qv_total = 0.0
        found = False
        for sym in matched:
            values = per_symbol[sym]
            qv_val = values.get(f"qv_{window}")
            if qv_val is None:
                continue
            found = True
            qv_total += qv_val
            net_total += values.get(f"net_{window}", 0.0)
        net[window] = round(net_total, 4) if found else None
        qv[window] = round(qv_total, 4) if found else None

    return FlowSide(tokens=len(matched), net=net, qv=qv)


def to_columns(sides: dict[str, Optional[FlowSide]]) -> dict[str, Optional[float]]:
    """{market: FlowSide|None} → 18 个 sector_returns 列的 kwargs。列名约定只此一处。"""
    out: dict[str, Optional[float]] = {}
    for market in MARKETS:
        side = sides.get(market)
        out[f"{market}_flow_tokens"] = side.tokens if side else None
        for window in FLOW_WINDOWS:
            out[f"{market}_net_{window}"] = side.net.get(window) if side else None
            out[f"{market}_qv_{window}"] = side.qv.get(window) if side else None
    return out


def from_row(row) -> dict[str, Optional[dict]]:
    """sector_returns 行 → {market: {tokens, net_1h, qv_1h, ...} | None}，供 API 序列化。

    整侧所有窗口都为空 → 该侧为 None（页面显示「—」而不是一排 0）。
    """
    out: dict[str, Optional[dict]] = {}
    for market in MARKETS:
        payload: dict[str, Optional[float]] = {
            "tokens": getattr(row, f"{market}_flow_tokens", None),
        }
        has_value = False
        for window in FLOW_WINDOWS:
            net = getattr(row, f"{market}_net_{window}", None)
            qv = getattr(row, f"{market}_qv_{window}", None)
            payload[f"net_{window}"] = net
            payload[f"qv_{window}"] = qv
            if net is not None or qv is not None:
                has_value = True
        out[market] = payload if has_value else None
    return out
