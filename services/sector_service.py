"""板块板块板块的读取层 — 给 API / 前端用。

Phase 1 提供：
- get_leaderboard()           最新 snapshot 的所有板块聚合，按 24h 降序
- get_sector_tokens(category) 某板块下所有 symbol 的当前涨跌（用于展开/钻取）

板块聚合数据来自 sector_returns 表（由 sector_scanner 定期写入）。
单 symbol 涨跌实时从 BMAC pivot 本地缓存现算 — 避免存储爆炸。
pivot 加载用 mtime-based 内存缓存避免每次请求都重新反序列化 20MB pkl。
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from models.sector import CmcSymbolCategory, SectorReturn
from scanners.sector_scanner import (
    MIN_TOKENS_PER_SECTOR,
    _close_of,
    _compute_returns_for_close,
    _load_market_data,
    normalize_pivot_symbol,
)
from schemas.sectors import (
    SectorFlows,
    SectorFlowSide,
    SectorLeaderboardResponse,
    SectorLeaderboardRow,
    SectorTokenRow,
    SectorTokensResponse,
)
from services import remote_fs, sector_flows
from services.time_utils import timestamp_pair


# ============================================================
# Pivot 内存缓存（按 mtime 失效）
# ============================================================
# market ("spot"/"swap") -> (mtime, pivot_dict)
_pivot_cache: dict[str, tuple[float, dict]] = {}
_pivot_lock = threading.Lock()


def _pivot_path(market: str) -> Path:
    fname = (
        f"preprocess_1h_resample__{config.REMOTE_OFFSET}__market_pivot_"
        f"{market}_{datetime.utcnow().year}.pkl"
    )
    return Path(config.LOCAL_CACHE_DIR) / fname


def _load_pivot_cached(market: str) -> Optional[dict]:
    path = _pivot_path(market)
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    with _pivot_lock:
        cached = _pivot_cache.get(market)
        if cached and cached[0] == mtime:
            return cached[1]
    # 加载在锁外（pickle.load 可能耗时）
    try:
        obj = remote_fs.load_pickle(path)
    except Exception as exc:
        logger.warning("加载 {} pivot 失败: {}", market, exc)
        return None
    if not isinstance(obj, dict) or "close" not in obj:
        return None
    with _pivot_lock:
        _pivot_cache[market] = (mtime, obj)
    return obj


def _flows_of(source) -> SectorFlows:
    """sector_returns 行 / 币级现算 dict → API 的 flows 结构。列名约定在 sector_flows。"""
    payload = sector_flows.from_row(source)
    return SectorFlows(
        spot=SectorFlowSide(**payload["spot"]) if payload["spot"] else None,
        swap=SectorFlowSide(**payload["swap"]) if payload["swap"] else None,
    )


# ============================================================
# 板块榜单（读 sector_returns 表，由 remote_puller 在拉到新 pivot 后立即触发写入）
# ============================================================
def get_leaderboard(session: Session) -> SectorLeaderboardResponse:
    """返回最新 snapshot 的所有 sector_returns 行，按 ret_24h 降序（NaN 末尾）。

    sector_returns 当前只由 post-pull 同步触发写入: remote_puller 拉到新 pivot
    后立刻跑 scanner。若 post-pull 的 sector_scan 失败，同一个 cutoff 目前不会
    自动重扫，直到下一个 pivot cutoff 或手动触发 scanner。

    /api/sectors/{cat}/tokens 仍然从 pivot 现算；正常 post-pull 成功时 DB 几乎
    同步更新，两者 snapshot_at 在绝大多数时刻是一致的（除非用户恰好在 pull
    过程中那 5-30s 窗口里拿到不一致的快照）。
    """
    latest_snap = session.execute(
        select(SectorReturn.snapshot_at)
        .order_by(SectorReturn.snapshot_at.desc())
        .limit(1)
    ).scalar()

    exclusion_note = getattr(config, "SECTOR_EXCLUSION_NOTE", None) or None

    if latest_snap is None:
        return SectorLeaderboardResponse(
            snapshot_at=None, rows=[], exclusion_note=exclusion_note)

    rows = session.execute(
        select(SectorReturn).where(
            SectorReturn.snapshot_at == latest_snap,
            SectorReturn.token_count >= MIN_TOKENS_PER_SECTOR,
        )
    ).scalars().all()

    # 排序：24h 中位数优先，NaN 排末尾
    def _sort_key(r: SectorReturn) -> tuple[int, float]:
        val = r.ret_24h_median if r.ret_24h_median is not None else r.ret_24h
        if val is None:
            return (1, 0.0)
        return (0, -val)

    rows_sorted = sorted(rows, key=_sort_key)

    return SectorLeaderboardResponse(
        snapshot_at=timestamp_pair(latest_snap),
        exclusion_note=exclusion_note,
        rows=[
            SectorLeaderboardRow(
                category=r.category,
                group=r.group_name,
                token_count=r.token_count,
                ret_1h=r.ret_1h,
                ret_24h=r.ret_24h,
                ret_168h=r.ret_168h,
                ret_720h=r.ret_720h,
                ret_1h_median=r.ret_1h_median,
                ret_24h_median=r.ret_24h_median,
                ret_168h_median=r.ret_168h_median,
                ret_720h_median=r.ret_720h_median,
                flows=_flows_of(r),
            )
            for r in rows_sorted
        ],
    )


# ============================================================
# 板块详情（钻取）
# ============================================================
def get_sector_tokens(session: Session, category: str) -> SectorTokensResponse:
    """对一个板块返回其下所有 symbol 当前的涨跌 + 两个市场的资金流。

    步骤：
    1. 查 cmc_symbol_categories 拿这个板块的 symbol 集合
    2. 加载两份 pivot（spot + swap），对齐到同一 snapshot（走 scanner 的 MarketData）
    3. 涨跌：现货优先匹配，缺现货才用永续（一行一个 market）
    4. 资金流：现货与永续各自独立算，同一行同时挂两侧（与 market 字段无关）
    5. 返回按 24h 涨跌排好序的列表
    """
    cmc_symbols = {
        row[0]
        for row in session.execute(
            select(CmcSymbolCategory.symbol).where(CmcSymbolCategory.category == category)
        ).all()
    }

    if not cmc_symbols:
        return SectorTokensResponse(category=category, group=None, snapshot_at=None, tokens=[])

    market_data = _load_market_data(use_pivot_cache=True)
    snapshot_at = market_data.snapshot_at
    if snapshot_at is None:
        return SectorTokensResponse(
            category=category,
            group=config.cmc_category_to_group(category),
            snapshot_at=None,
            tokens=[],
        )

    # 涨跌：两个市场各自算（列名 → 涨跌），现货优先
    returns_by_market: dict[str, dict[str, dict[str, float]]] = {}
    for market_name in sector_flows.MARKETS:
        close = _close_of(market_data.pivot(market_name))
        if close is None:
            continue
        _, returns_by_market[market_name] = _compute_returns_for_close(
            close, as_of=snapshot_at)

    # 资金流：宽表优先、缺 taker 字段则回退读单币文件（两侧独立，与榜单同一口径）
    flows_by_market: dict[str, dict[str, dict[str, float]]] = {}
    for market_name in sector_flows.MARKETS:
        pivot = market_data.pivot(market_name)
        if pivot is None:
            continue
        flows, reason = sector_flows.resolve_per_symbol_flows(
            pivot, market_name, as_of=snapshot_at)
        if not reason:
            flows_by_market[market_name] = flows

    def _token_flows(nsym: str) -> SectorFlows:
        sides: dict[str, Optional[SectorFlowSide]] = {}
        for market_name in sector_flows.MARKETS:
            values = flows_by_market.get(market_name, {}).get(nsym)
            sides[market_name] = SectorFlowSide(**values) if values else None
        return SectorFlows(spot=sides["spot"], swap=sides["swap"])

    rows: list[SectorTokenRow] = []
    seen_normalized: set[str] = set()
    excluded_symbols = set(getattr(config, "SECTOR_EXCLUDED_SYMBOLS", ()))
    # spot 优先（先扫 spot，得到的 base sym 标记 seen，swap 里再有同名 sym 就跳过）
    for market_name in ("spot", "swap"):
        for col, rets in returns_by_market.get(market_name, {}).items():
            nsym = normalize_pivot_symbol(col)
            if not nsym or nsym not in cmc_symbols or nsym in seen_normalized:
                continue
            seen_normalized.add(nsym)
            rows.append(SectorTokenRow(
                symbol=nsym,
                binance_symbol=col,
                market=market_name,
                ret_1h=rets.get("ret_1h"),
                ret_24h=rets.get("ret_24h"),
                ret_168h=rets.get("ret_168h"),
                ret_720h=rets.get("ret_720h"),
                flows=_token_flows(nsym),
                excluded=nsym in excluded_symbols,
            ))

    # 排序：先把巨头沉到最底（否则 BTC 涨得多会排在中间，容易被误读成板块成员），
    # 组内再按 24h 降序、NaN 末尾。
    def _sort_key(r: SectorTokenRow) -> tuple[int, int, float]:
        bucket = 1 if r.excluded else 0
        if r.ret_24h is None:
            return (bucket, 1, 0.0)
        return (bucket, 0, -r.ret_24h)

    rows.sort(key=_sort_key)

    return SectorTokensResponse(
        category=category,
        group=config.cmc_category_to_group(category),
        snapshot_at=timestamp_pair(snapshot_at) if snapshot_at else None,
        tokens=rows,
    )
