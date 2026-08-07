"""板块涨跌 scanner。

读取本地 cache 里的 BMAC pivot pkl（spot/swap），结合 cmc_symbol_categories 表的
symbol→板块映射，算各板块等权平均涨跌（1h/24h/168h/720h），写 sector_returns 表。

公共计算函数 `compute_all_sector_returns()` 被 SectorScanner（持久化用）和
sector_service.get_leaderboard（live 读取用）共享，确保 UI 永远和 token 钻取
来自同一份 pivot —— 不会出现"leaderboard 是 2 小时前 snapshot，token 是 live"的错位。

约定：
- 一行 = 一个 (snapshot_at, CMC category) — 同一 snapshot_at 一次 scan 写 N 行
- snapshot_at = pivot 的最新 candle_begin_time（UTC naive）
- 涨跌单位：百分比（与现有 PriceSnapshot.change_pct 一致）
- 现货优先，缺现货才用永续（spot 价格干净，swap 主要补盲点）
- 在板块映射里但不在 BMAC pivot 里的 symbol 直接跳过（数据缺失）
- 单板块匹配活跃 symbol < MIN_TOKENS_PER_SECTOR 视为信号太弱，写 0 行
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Optional

import pandas as pd
from loguru import logger
from sqlalchemy import delete
from sqlalchemy.orm import Session

import config
from database import SessionLocal
from models.sector import SectorReturn
from services import cmc_client, remote_fs


# ============================================================
# Symbol 规范化
# ----------------
# BMAC pivot 列名形如 "ETHUSDT", "1000PEPEUSDT", "1MBABYDOGEUSDT", "BEAMXUSDT"。
# CMC 存的 symbol 形如 "ETH", "PEPE", "BABYDOGE", "BEAM"。
# 规则（顺序敏感）：
#   1. 去 "USDT" 后缀
#   2. 应用特殊映射（BEAMX→BEAM, DODOX→DODO）
#   3. 去数量前缀（1000000 / 1000 / 1M 按长度降序匹配）
# ============================================================
_QUOTE_SUFFIXES = ("USDT",)
_PREFIXES_TO_REMOVE = ("1000000", "1000", "1M")
_SYMBOL_MAPPING = {"BEAMX": "BEAM", "DODOX": "DODO"}
_VALID_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,20}$")

# 一个板块至少要这么多匹配的 symbol 才计算（信号太少就跳过）
MIN_TOKENS_PER_SECTOR = 10

# 涨跌计算的 lookback bar 数（pivot 是 1h 频率，所以 1, 24, 168, 720 对应 1h, 1d, 1w, 30d）
RETURN_LOOKBACKS = {
    "ret_1h": 1,
    "ret_24h": 24,
    "ret_168h": 168,
    "ret_720h": 720,
}


def normalize_pivot_symbol(col: str) -> Optional[str]:
    """BMAC pivot 列名 → CMC 标准 symbol。失败返回 None（脏数据，过滤掉）。"""
    if not col or not isinstance(col, str):
        return None
    upper = col.strip().upper()
    if not _VALID_SYMBOL_RE.match(upper):
        return None  # 中文/编码乱码这类直接丢

    # 1. 去 quote suffix
    base = upper
    for suf in _QUOTE_SUFFIXES:
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    if not base:
        return None

    # 2. 特例映射
    if base in _SYMBOL_MAPPING:
        return _SYMBOL_MAPPING[base]

    # 3. 数量前缀（按长度降序匹配，避免 1000 命中 1000000）
    for prefix in _PREFIXES_TO_REMOVE:
        if base.startswith(prefix) and len(base) > len(prefix):
            return base[len(prefix):]
    return base


# ============================================================
# Pivot 加载
# ============================================================
def _cache_path(filename: str) -> Path:
    return Path(config.LOCAL_CACHE_DIR) / filename


def _load_pivot(market: str) -> Optional[dict]:
    """market 取 'spot' 或 'swap'。返回 dict 或 None（cache 文件缺失）。"""
    fname = f"preprocess_1h_resample__{config.REMOTE_OFFSET}__market_pivot_{market}_{datetime.utcnow().year}.pkl"
    path = _cache_path(fname)
    if not path.exists():
        logger.warning("缓存不存在: {}（remote_puller 还没拉到？）", path)
        return None
    try:
        obj = remote_fs.load_pickle(path)
    except Exception as exc:
        logger.warning("加载 pivot 失败 {}: {}", path, exc)
        return None
    if not isinstance(obj, dict) or "close" not in obj:
        logger.warning("pivot 结构异常 {}: type={}, keys={}",
                       path, type(obj).__name__,
                       list(obj.keys()) if isinstance(obj, dict) else None)
        return None
    return obj


# ============================================================
# 涨跌计算
# ============================================================
def _timestamp_to_utc_naive(ts) -> Optional[datetime]:
    if ts is None:
        return None
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    if ts.tzinfo is not None:
        return ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts


def _latest_snapshot_for_close(close_df: pd.DataFrame) -> Optional[datetime]:
    if close_df.empty:
        return None
    return _timestamp_to_utc_naive(close_df.index.max())


def _slice_close_as_of(close_df: pd.DataFrame, as_of: Optional[datetime]) -> pd.DataFrame:
    sorted_df = close_df.sort_index()
    if as_of is None:
        return sorted_df
    target = pd.Timestamp(as_of)
    index_tz = getattr(sorted_df.index, "tz", None)
    if index_tz is not None and target.tzinfo is None:
        target = target.tz_localize("UTC")
    elif index_tz is None and target.tzinfo is not None:
        target = target.tz_convert("UTC").tz_localize(None)
    return sorted_df.loc[sorted_df.index <= target]


def _compute_returns_for_close(
    close_df: pd.DataFrame,
    *,
    as_of: Optional[datetime] = None,
) -> tuple[Optional[datetime], dict[str, dict[str, float]]]:
    """给一份 close DataFrame（index=DatetimeIndex, columns=symbol），算每个 symbol 的多周期涨跌。

    Returns:
        (snapshot_at_utc_naive, {pivot_col: {ret_1h: float | None, ret_24h: ..., ...}})
    """
    close_df = _slice_close_as_of(close_df, as_of)
    if close_df.empty:
        return None, {}

    # snapshot_at = pivot 最新一行的 candle_begin_time
    snapshot_at = _latest_snapshot_for_close(close_df)

    out: dict[str, dict[str, float]] = {}
    latest = close_df.iloc[-1]

    for ret_name, lookback in RETURN_LOOKBACKS.items():
        if len(close_df) <= lookback:
            # 历史不够，这个周期全部为 None
            continue
        past = close_df.iloc[-(lookback + 1)]
        # 向量化算每个 symbol 的涨跌（NaN 自动传播）
        ret = (latest - past) / past * 100.0
        for col, val in ret.items():
            if pd.isna(val):
                continue
            out.setdefault(col, {})[ret_name] = float(round(val, 4))
    return snapshot_at, out


# ============================================================
# 公共计算（被 scanner 持久化路径 + service live 读取路径共享）
# ============================================================
@dataclass
class SectorAggregate:
    """单个板块的均值/中位数聚合结果。"""
    category: str
    group_name: Optional[str]
    token_count: int
    ret_1h: Optional[float]
    ret_24h: Optional[float]
    ret_168h: Optional[float]
    ret_720h: Optional[float]
    ret_1h_median: Optional[float]
    ret_24h_median: Optional[float]
    ret_168h_median: Optional[float]
    ret_720h_median: Optional[float]
    # {market: FlowSide|None} —— 勾稽门没过或该市场无数据时该侧为 None
    flows: dict = field(default_factory=dict)


@dataclass
class SectorComputeResult:
    snapshot_at: Optional[datetime]
    aggregates: list[SectorAggregate]
    active_symbols: int
    considered_cats: int
    skipped_thin: list[str]
    # {market: 中文失败原因} —— 为空表示两个市场的勾稽门都通过
    flow_gate_failures: dict[str, str] = field(default_factory=dict)
    skipped_reason: Optional[str] = None  # 失败时填


@dataclass
class MarketData:
    """一次 pivot 加载的产物 —— 涨跌与资金流共用，避免重复反序列化 30MB pkl。

    snapshot_at = 现货/永续最新 bar 的**较早者**，两个市场对齐到同一时刻，
    免得一个板块快照里混两个市场的不同时间。
    """
    snapshot_at: Optional[datetime]
    spot_pivot: Optional[dict]
    swap_pivot: Optional[dict]

    def pivot(self, market: str) -> Optional[dict]:
        return self.spot_pivot if market == "spot" else self.swap_pivot


def _close_of(pivot: Optional[dict]):
    return None if pivot is None else pivot.get("close")


def _load_market_data(*, use_pivot_cache: bool = False) -> MarketData:
    """加载 spot + swap pivot 并对齐快照时刻。

    Args:
        use_pivot_cache: True 时调 sector_service._load_pivot_cached（mtime 缓存，
                         供 live 读取路径用以避免每次反序列化）；False 时调
                         _load_pivot（无缓存，更适合定时 scanner，每次都用最新文件）
    """
    if use_pivot_cache:
        # 延迟 import 避免循环 (sector_service 导入了 sector_scanner)
        from services.sector_service import _load_pivot_cached as _loader
    else:
        _loader = _load_pivot

    spot_pivot = _loader("spot")
    swap_pivot = _loader("swap")

    latest_times = [
        ts for ts in (
            _latest_snapshot_for_close(_close_of(spot_pivot)) if _close_of(spot_pivot) is not None else None,
            _latest_snapshot_for_close(_close_of(swap_pivot)) if _close_of(swap_pivot) is not None else None,
        )
        if ts is not None
    ]
    snapshot_at = min(latest_times) if len(latest_times) > 1 else (
        latest_times[0] if latest_times else None)
    return MarketData(snapshot_at=snapshot_at, spot_pivot=spot_pivot, swap_pivot=swap_pivot)


def _per_symbol_returns_from(market: MarketData) -> dict[str, dict[str, float]]:
    """MarketData → {规范化 symbol: {ret_1h: ...}}。现货优先（后写覆盖永续）。"""
    if market.snapshot_at is None:
        return {}

    sym_to_returns: dict[str, dict[str, float]] = {}
    # 顺序敏感：先永续后现货，现货有的就盖掉永续（现货价格更干净）
    for market_name in ("swap", "spot"):
        close = _close_of(market.pivot(market_name))
        if close is None:
            continue
        _, returns = _compute_returns_for_close(close, as_of=market.snapshot_at)
        for col, rets in returns.items():
            nsym = normalize_pivot_symbol(col)
            if nsym:
                sym_to_returns[nsym] = rets
    return sym_to_returns


def compute_all_sector_returns(
    session: Session, *, use_pivot_cache: bool = False
) -> SectorComputeResult:
    """对当前本地 pivot + DB 板块映射做完整的板块聚合计算（不写 DB）。

    被两边共用:
    - SectorScanner.scan() 调，拿到结果后写 DB
    - sector_service.get_leaderboard() 调，拿到结果直接序列化给前端
    保证两者用同一份 pivot 算出同一个 snapshot_at + 同一组聚合数。

    资金流（2026-08-07）与涨跌共用这一份 pivot；每个市场先过勾稽门，
    没过的市场整轮资金流写 None，涨跌不受任何影响。
    """
    from services import sector_flows  # 延迟 import 避免循环

    market_data = _load_market_data(use_pivot_cache=use_pivot_cache)
    snapshot_at = market_data.snapshot_at
    sym_to_returns = _per_symbol_returns_from(market_data)

    if snapshot_at is None:
        return SectorComputeResult(
            snapshot_at=None, aggregates=[], active_symbols=0,
            considered_cats=0, skipped_thin=[], skipped_reason="no_pivot",
        )
    if not sym_to_returns:
        return SectorComputeResult(
            snapshot_at=snapshot_at, aggregates=[], active_symbols=0,
            considered_cats=0, skipped_thin=[], skipped_reason="no_symbols",
        )

    # 资金流：每个市场各自过闸，一边失败不连坐另一边
    flow_gate_failures: dict[str, str] = {}
    flows_by_market: dict[str, dict[str, dict[str, float]]] = {}
    for market_name in sector_flows.MARKETS:
        pivot = market_data.pivot(market_name)
        if pivot is None:
            continue  # 该市场 pivot 本来就没拉到，不算勾稽失败（涨跌侧同样没有）
        reason = sector_flows.check_flow_gate(pivot)
        if reason:
            flow_gate_failures[market_name] = reason
            logger.warning("资金流勾稽门未通过 market={}: {}", market_name, reason)
            continue
        flows_by_market[market_name] = sector_flows.per_symbol_flows(
            pivot, as_of=snapshot_at)

    cat_to_syms = cmc_client.load_category_to_symbols(session)
    if not cat_to_syms:
        return SectorComputeResult(
            snapshot_at=snapshot_at, aggregates=[], active_symbols=len(sym_to_returns),
            considered_cats=0, skipped_thin=[], skipped_reason="no_mapping",
            flow_gate_failures=flow_gate_failures,
        )

    whitelist = set(config.all_whitelisted_cmc_categories())
    aggregates: list[SectorAggregate] = []
    considered_cats = 0
    skipped_thin: list[str] = []

    for category, cmc_symbols in sorted(cat_to_syms.items()):
        if category not in whitelist:
            continue
        considered_cats += 1
        matched = cmc_symbols & sym_to_returns.keys()
        if len(matched) < MIN_TOKENS_PER_SECTOR:
            skipped_thin.append(f"{category}({len(matched)})")
            continue
        agg: dict[str, list[float]] = {k: [] for k in RETURN_LOOKBACKS}
        for sym in matched:
            rets = sym_to_returns[sym]
            for ret_name in RETURN_LOOKBACKS:
                if ret_name in rets:
                    agg[ret_name].append(rets[ret_name])
        means: dict[str, Optional[float]] = {
            ret_name: (round(sum(values) / len(values), 4) if values else None)
            for ret_name, values in agg.items()
        }
        medians: dict[str, Optional[float]] = {
            ret_name: (round(float(median(values)), 4) if values else None)
            for ret_name, values in agg.items()
        }
        # 资金流的成员集合按各市场宽表实际有的列取，与涨跌口径的 matched 无关
        flows = {
            market_name: sector_flows.aggregate_side(
                flows_by_market.get(market_name, {}), cmc_symbols)
            for market_name in sector_flows.MARKETS
        }
        aggregates.append(SectorAggregate(
            category=category,
            group_name=config.cmc_category_to_group(category),
            token_count=len(matched),
            ret_1h=means["ret_1h"],
            ret_24h=means["ret_24h"],
            ret_168h=means["ret_168h"],
            ret_720h=means["ret_720h"],
            ret_1h_median=medians["ret_1h"],
            ret_24h_median=medians["ret_24h"],
            ret_168h_median=medians["ret_168h"],
            ret_720h_median=medians["ret_720h"],
            flows=flows,
        ))

    return SectorComputeResult(
        snapshot_at=snapshot_at,
        aggregates=aggregates,
        active_symbols=len(sym_to_returns),
        considered_cats=considered_cats,
        skipped_thin=skipped_thin,
        flow_gate_failures=flow_gate_failures,
        skipped_reason=None,
    )


# ============================================================
# Scanner 主类
# ============================================================
class SectorScanner:
    name = "sector_scanner"

    def __init__(self, *, session: Optional[Session] = None):
        self._injected_session = session  # 测试时可注入

    def scan(self) -> dict:
        """跑一次完整扫描。返回 stats dict。"""
        own_session = self._injected_session is None
        session = self._injected_session or SessionLocal()
        try:
            result = compute_all_sector_returns(session, use_pivot_cache=False)

            if result.skipped_reason:
                logger.warning("sector_scan 跳过: {}", result.skipped_reason)
                return {"sectors_written": 0, "skipped_reason": result.skipped_reason}

            from services import sector_flows  # 延迟 import 避免循环

            # 写库：先删同 snapshot_at 的旧行（处理重跑），再写新行
            rows = [
                SectorReturn(
                    snapshot_at=result.snapshot_at,
                    category=a.category,
                    group_name=a.group_name,
                    token_count=a.token_count,
                    ret_1h=a.ret_1h,
                    ret_24h=a.ret_24h,
                    ret_168h=a.ret_168h,
                    ret_720h=a.ret_720h,
                    ret_1h_median=a.ret_1h_median,
                    ret_24h_median=a.ret_24h_median,
                    ret_168h_median=a.ret_168h_median,
                    ret_720h_median=a.ret_720h_median,
                    **sector_flows.to_columns(a.flows),
                )
                for a in result.aggregates
            ]
            if rows:
                session.execute(
                    delete(SectorReturn).where(SectorReturn.snapshot_at == result.snapshot_at)
                )
                session.add_all(rows)
                session.commit()

            logger.info(
                "sector_scan 完成: snapshot_at={} 写 {} 板块（考虑 {}/{}, "
                "活跃 symbol 不足跳过 {}）",
                result.snapshot_at, len(rows), result.considered_cats,
                len(config.all_whitelisted_cmc_categories()), len(result.skipped_thin),
            )
            if result.skipped_thin:
                logger.debug("token<{} 跳过: {}", MIN_TOKENS_PER_SECTOR, result.skipped_thin)

            # 勾稽失败主动推送（页面上的「—」太安静，没人会注意到）
            if result.flow_gate_failures:
                try:
                    from services import sector_flow_monitoring
                    sector_flow_monitoring.alert_flow_gate_failures(
                        result.flow_gate_failures)
                except Exception as exc:
                    # 告警失败绝不能拖垮扫描本身
                    logger.warning("资金流勾稽告警推送失败: {}", exc)

            return {
                "snapshot_at": result.snapshot_at,
                "sectors_written": len(rows),
                "considered_cats": result.considered_cats,
                "skipped_thin": len(result.skipped_thin),
                "active_symbols": result.active_symbols,
                "flow_gate_failures": dict(result.flow_gate_failures),
            }
        except Exception:
            if own_session:
                session.rollback()
            raise
        finally:
            if own_session:
                session.close()
