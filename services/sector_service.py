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
from models.sector import CmcSymbolCategory
from scanners.sector_scanner import (
    RETURN_LOOKBACKS,
    _compute_returns_for_close,
    compute_all_sector_returns,
    normalize_pivot_symbol,
)
from schemas.sectors import (
    SectorLeaderboardResponse,
    SectorLeaderboardRow,
    SectorTokenRow,
    SectorTokensResponse,
)
from services import remote_fs
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


# ============================================================
# 板块榜单（live 计算，不读 DB —— 跟 token 钻取 snapshot 一致）
# ============================================================
def get_leaderboard(session: Session) -> SectorLeaderboardResponse:
    """从本地 pivot 缓存 live 算所有板块的等权聚合，按 ret_24h 降序（NaN 末尾）。

    **不读 sector_returns 表**（虽然 sector_scanner 会写入，但只用于 phase 2 告警 +
    历史趋势查询，不喂 UI）。这样保证：leaderboard 的 snapshot_at 跟同一会话里
    /api/sectors/{cat}/tokens 的 snapshot_at 永远一致 —— 不会出现"板块 1h 是几小时前的，
    token 1h 是 live"的错位。
    """
    result = compute_all_sector_returns(session, use_pivot_cache=True)

    # 排序：24h 降序，NaN 排末尾
    def _sort_key(a) -> tuple[int, float]:
        val = a.ret_24h
        if val is None:
            return (1, 0.0)
        return (0, -val)

    aggregates_sorted = sorted(result.aggregates, key=_sort_key)

    return SectorLeaderboardResponse(
        snapshot_at=timestamp_pair(result.snapshot_at) if result.snapshot_at else None,
        rows=[
            SectorLeaderboardRow(
                category=a.category,
                group=a.group_name,
                token_count=a.token_count,
                ret_1h=a.ret_1h,
                ret_24h=a.ret_24h,
                ret_168h=a.ret_168h,
                ret_720h=a.ret_720h,
            )
            for a in aggregates_sorted
        ],
    )


# ============================================================
# 板块详情（钻取）
# ============================================================
def get_sector_tokens(session: Session, category: str) -> SectorTokensResponse:
    """对一个板块返回其下所有 symbol 当前的涨跌。

    步骤：
    1. 查 cmc_symbol_categories 拿这个板块的 symbol 集合
    2. 加载两份 pivot（spot + swap）
    3. 现货优先匹配，缺现货才用永续
    4. 算 1h/24h/168h/720h 涨跌
    5. 返回排好序的列表
    """
    cmc_symbols = {
        row[0]
        for row in session.execute(
            select(CmcSymbolCategory.symbol).where(CmcSymbolCategory.category == category)
        ).all()
    }

    if not cmc_symbols:
        return SectorTokensResponse(category=category, group=None, snapshot_at=None, tokens=[])

    spot_pivot = _load_pivot_cached("spot")
    swap_pivot = _load_pivot_cached("swap")
    if spot_pivot is None and swap_pivot is None:
        return SectorTokensResponse(
            category=category,
            group=config.cmc_category_to_group(category),
            snapshot_at=None,
            tokens=[],
        )

    # 算两边的 per-symbol 涨跌
    snapshot_at: Optional[datetime] = None
    spot_returns: dict[str, dict[str, float]] = {}
    swap_returns: dict[str, dict[str, float]] = {}
    if spot_pivot is not None:
        s, spot_returns = _compute_returns_for_close(spot_pivot["close"])
        snapshot_at = s
    if swap_pivot is not None:
        s, swap_returns = _compute_returns_for_close(swap_pivot["close"])
        if snapshot_at is None or (s is not None and s > snapshot_at):
            snapshot_at = s

    # 对每个 binance pivot 列名规范化 → 看是否在我们关心的 CMC symbol 集合里
    rows: list[SectorTokenRow] = []
    seen_normalized: set[str] = set()

    # spot 优先（先扫 spot，得到的 base sym 标记 seen，swap 里再有同名 sym 就跳过）
    for col, rets in spot_returns.items():
        nsym = normalize_pivot_symbol(col)
        if not nsym or nsym not in cmc_symbols:
            continue
        seen_normalized.add(nsym)
        rows.append(SectorTokenRow(
            symbol=nsym,
            binance_symbol=col,
            market="spot",
            ret_1h=rets.get("ret_1h"),
            ret_24h=rets.get("ret_24h"),
            ret_168h=rets.get("ret_168h"),
            ret_720h=rets.get("ret_720h"),
        ))
    # swap 补 spot 没覆盖的
    for col, rets in swap_returns.items():
        nsym = normalize_pivot_symbol(col)
        if not nsym or nsym not in cmc_symbols or nsym in seen_normalized:
            continue
        rows.append(SectorTokenRow(
            symbol=nsym,
            binance_symbol=col,
            market="swap",
            ret_1h=rets.get("ret_1h"),
            ret_24h=rets.get("ret_24h"),
            ret_168h=rets.get("ret_168h"),
            ret_720h=rets.get("ret_720h"),
        ))

    # 按 24h 降序，NaN 末尾
    def _sort_key(r: SectorTokenRow) -> tuple[int, float]:
        if r.ret_24h is None:
            return (1, 0.0)
        return (0, -r.ret_24h)

    rows.sort(key=_sort_key)

    return SectorTokensResponse(
        category=category,
        group=config.cmc_category_to_group(category),
        snapshot_at=timestamp_pair(snapshot_at) if snapshot_at else None,
        tokens=rows,
    )
