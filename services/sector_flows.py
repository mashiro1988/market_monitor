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

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

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
    bad = both & ((tb < 0) | (tb > qv * (1 + _IDENTITY_RTOL)))
    total = int(both.to_numpy().sum())
    if total == 0:
        return "无任何有效成交额格子"
    violations = int(bad.to_numpy().sum())
    max_ratio = float(getattr(config, "FLOW_IDENTITY_VIOLATION_MAX_RATIO", 0.001))
    ratio = violations / total
    if ratio > max_ratio:
        return (f"恒等式违规占比 {ratio:.4%} 超过上限 {max_ratio:.4%}"
                f"（{violations}/{total} 格 taker_buy 为负或大于 quote_volume）")

    # 3b) 同一恒等式单独看最新一根 bar。
    # 上面的全矩阵占比会被历史稀释：2000 行里坏掉整根最新 bar 才 0.05%，够不着 0.1% 的线
    # （2026-08-07 彩排实测）。而最新 bar 是 1h 列直接读的那根，也是写入损坏最常出现的地方。
    latest_both = int(both.iloc[-1].sum())
    if latest_both:
        latest_violations = int(bad.iloc[-1].sum())
        latest_ratio = latest_violations / latest_both
        latest_max = float(getattr(config, "FLOW_LATEST_BAR_VIOLATION_MAX_RATIO", 0.05))
        if latest_ratio > latest_max:
            return (f"最新 bar 恒等式违规占比 {latest_ratio:.2%} 超过上限 {latest_max:.2%}"
                    f"（{latest_violations}/{latest_both} 个币 taker_buy 为负或大于 quote_volume）")

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


# ============================================================
# 回退路径：直接读单币 1h resample 文件
# ------------------------------------------------------------
# 为什么需要这条路（2026-08-08 实证）：
# taker 字段本来就在 BMAC 的单币文件里，宽表里没有只是因为 make_market_pivot 没把它
# 透视进去。给 BMAC 打补丁能解决，但 mmon.top 读的那个 BMAC 跑在第三方交易框架容器
# （xbxtempleton/qronos-trading-framework）里 —— 改不得（要动别人的镜像、重启等于
# 重启整个交易框架），也留不住（docker pull 一更新就冲掉）。所以直接读源文件。
# ============================================================
PER_SYMBOL_DIR_TMPL = "binance_{market}_1h_resample/{offset}/"
_SYMBOL_FILE_RE = re.compile(r"^([A-Z0-9]{1,20}USDT)\.pkl$")


def _window_sums(qv: pd.Series, tb: pd.Series) -> dict[str, float]:
    """单个币的 (总成交额, 主动买入额) 两条序列 → 四个窗口的 net/qv。

    与宽表路径共用同一套口径：只算两边都有值的 bar；整窗口无有效 bar 就不产出该窗口。
    """
    out: dict[str, float] = {}
    for window, lookback in FLOW_WINDOWS.items():
        q = qv.iloc[-lookback:]
        t = tb.iloc[-lookback:]
        valid = q.notna() & t.notna()
        if not bool(valid.any()):
            continue
        q_sum = float(q[valid].sum())
        t_sum = float(t[valid].sum())
        out[f"qv_{window}"] = q_sum
        out[f"net_{window}"] = 2.0 * t_sum - q_sum
    return out


def per_symbol_flows_from_files(
    market: str,
    *,
    as_of: Optional[datetime],
    offset: Optional[str] = None,
) -> dict[str, dict[str, float]]:
    """从 BMAC 单币 1h resample 文件算资金流 —— 宽表缺 taker 字段时的回退路径。

    产出与 per_symbol_flows() 完全同构（同样的键、同样的口径），调用方可直接替换。

    仅在**本地后端**（REMOTE_BACKEND=local，数据目录就在本机）启用：这条路要读几百个
    文件，SFTP 模式下等于几百次网络往返，不可接受 —— 那种情况下返回空，让调用方走
    「资金流不可用」并告警。
    """
    # 延迟 import 避免与 remote_fs 的顶层 numpy shim 抢加载顺序
    from services import remote_fs

    if not remote_fs._is_local_backend():
        logger.warning(
            "资金流回退路径需要读 {} 目录下的几百个单币文件，SFTP 模式下开销不可接受，跳过"
            "（要用这条路请把 REMOTE_BACKEND 设为 local）", market)
        return {}

    rel = PER_SYMBOL_DIR_TMPL.format(market=market, offset=offset or config.REMOTE_OFFSET)
    directory = remote_fs.REMOTE_DATA_ROOT + rel
    try:
        entries = remote_fs.list_dir(directory)
    except OSError as exc:
        logger.warning("资金流回退路径列目录失败 {}: {}", directory, exc)
        return {}

    out: dict[str, dict[str, float]] = {}
    skipped_no_cols = 0
    skipped_unreadable = 0
    for name, _size, _mtime in entries:
        matched = _SYMBOL_FILE_RE.match(name)
        if not matched:
            continue  # .ready 标记、乱码文件名等
        nsym = normalize_pivot_symbol(matched.group(1))
        if not nsym:
            continue
        try:
            df = remote_fs.load_pickle(Path(directory + name))
        except Exception:
            skipped_unreadable += 1
            continue
        pair = _series_pair_from_df(df, as_of)
        if pair is None:
            skipped_no_cols += 1
            continue
        sums = _window_sums(*pair)
        if not sums:
            continue
        bucket = out.setdefault(nsym, {})
        for key, value in sums.items():   # 同名 symbol 多个交易对合并求和
            bucket[key] = bucket.get(key, 0.0) + value

    if skipped_no_cols or skipped_unreadable:
        logger.debug("资金流回退路径 {}: 跳过缺字段 {} 个、读不动 {} 个",
                     market, skipped_no_cols, skipped_unreadable)
    return out


def _series_pair_from_df(df, as_of: Optional[datetime]):
    """单币 DataFrame → 截到 as_of 的 (总成交额, 主动买入额) 两条序列。缺字段返回 None。

    单币文件的 candle_begin_time 是**列**（tz-aware UTC），不是索引 —— 与宽表不同。
    """
    if not isinstance(df, pd.DataFrame):
        return None
    needed = {"candle_begin_time", QUOTE_VOLUME_KEY, TAKER_BUY_KEY}
    if not needed <= set(df.columns):
        return None
    frame = df[["candle_begin_time", QUOTE_VOLUME_KEY, TAKER_BUY_KEY]].copy()
    frame = frame.set_index(pd.DatetimeIndex(frame["candle_begin_time"]))
    frame = _slice_close_as_of(frame, as_of)
    if frame.empty:
        return None
    return frame[QUOTE_VOLUME_KEY], frame[TAKER_BUY_KEY]


def resolve_per_symbol_flows(
    pivot: Optional[dict],
    market: str,
    *,
    as_of: Optional[datetime],
) -> tuple[dict[str, dict[str, float]], Optional[str]]:
    """决定这个市场的资金流从哪儿取。返回 (flows, 失败原因|None)。

    优先宽表（一个文件，快）；宽表缺 taker 字段就回退读单币文件（几百个文件，但字段
    本来就在那儿）。**两条路都不通才算失败**，调用方据此写 None 并告警。

    调用方应先自行处理 pivot is None 的情况（那说明该市场连价格都没拉到，整块跳过）。
    """
    reason = check_flow_gate(pivot)
    if reason is None:
        return per_symbol_flows(pivot, as_of=as_of), None

    fallback = per_symbol_flows_from_files(market, as_of=as_of)
    if fallback:
        logger.info("资金流走回退路径 market={}（宽表不可用: {}），覆盖 {} 个币",
                    market, reason, len(fallback))
        return fallback, None
    return {}, reason


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
