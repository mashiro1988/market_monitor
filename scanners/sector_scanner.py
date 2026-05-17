"""板块涨跌 scanner。

读取本地 cache 里的 BMAC pivot pkl（spot/swap），结合 cmc_symbol_categories 表的
symbol→板块映射，算各板块等权平均涨跌（1h/24h/168h/720h），写 sector_returns 表。

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
from datetime import datetime, timezone
from pathlib import Path
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
MIN_TOKENS_PER_SECTOR = 3

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
def _compute_returns_for_close(close_df: pd.DataFrame) -> tuple[Optional[datetime], dict[str, dict[str, float]]]:
    """给一份 close DataFrame（index=DatetimeIndex, columns=symbol），算每个 symbol 的多周期涨跌。

    Returns:
        (snapshot_at_utc_naive, {pivot_col: {ret_1h: float | None, ret_24h: ..., ...}})
    """
    if close_df.empty:
        return None, {}

    # snapshot_at = pivot 最新一行的 candle_begin_time
    latest_ts = close_df.index.max()
    if hasattr(latest_ts, "to_pydatetime"):
        latest_ts = latest_ts.to_pydatetime()
    if latest_ts.tzinfo is not None:
        snapshot_at = latest_ts.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        snapshot_at = latest_ts

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
# Scanner 主类
# ============================================================
class SectorScanner:
    name = "sector_scanner"

    def __init__(self, *, session: Optional[Session] = None):
        self._injected_session = session  # 测试时可注入

    def scan(self) -> dict:
        """跑一次完整扫描。返回 stats dict。"""
        # 1. 加载本地 cache 的两份 pivot
        spot_pivot = _load_pivot("spot")
        swap_pivot = _load_pivot("swap")

        if spot_pivot is None and swap_pivot is None:
            logger.warning("sector_scan 跳过：spot / swap pivot 都没拉到")
            return {"sectors_written": 0, "skipped_reason": "no_pivot"}

        # 2. 从 close 算每 symbol 多周期涨跌
        spot_returns: dict[str, dict[str, float]] = {}
        swap_returns: dict[str, dict[str, float]] = {}
        snapshot_at: Optional[datetime] = None

        if spot_pivot is not None:
            s_at, spot_returns = _compute_returns_for_close(spot_pivot["close"])
            snapshot_at = s_at
        if swap_pivot is not None:
            s_at, swap_returns = _compute_returns_for_close(swap_pivot["close"])
            # 选 spot 与 swap 中较新的 snapshot_at（应该一致，但保险）
            if snapshot_at is None or (s_at is not None and s_at > snapshot_at):
                snapshot_at = s_at

        if snapshot_at is None:
            logger.warning("sector_scan 跳过：pivot 是空的")
            return {"sectors_written": 0, "skipped_reason": "empty_pivot"}

        # 3. 把 pivot 的 symbol 列规范化（pivot_col → normalized_symbol → returns）
        #    现货优先：若 spot 有 X 也算了，swap 也有 X 也算了，最终用 spot 的
        sym_to_returns: dict[str, dict[str, float]] = {}
        for col, rets in swap_returns.items():
            nsym = normalize_pivot_symbol(col)
            if not nsym:
                continue
            sym_to_returns[nsym] = rets
        # spot 覆盖 swap（spot 优先）
        for col, rets in spot_returns.items():
            nsym = normalize_pivot_symbol(col)
            if not nsym:
                continue
            sym_to_returns[nsym] = rets

        if not sym_to_returns:
            logger.warning("sector_scan 跳过：规范化后无可用 symbol")
            return {"sectors_written": 0, "skipped_reason": "no_symbols"}

        # 4. 读 cmc 板块映射
        own_session = self._injected_session is None
        session = self._injected_session or SessionLocal()
        try:
            cat_to_syms = cmc_client.load_category_to_symbols(session)
            if not cat_to_syms:
                logger.warning("sector_scan 跳过：cmc_symbol_categories 表为空，"
                               "先跑 python run.py refresh-sectors")
                return {"sectors_written": 0, "skipped_reason": "no_mapping"}

            # 5. 只算白名单内的板块（cat_to_syms 已经只包含白名单刷新过的板块；
            #    但白名单可能更新后还没刷，这里再做一次保险过滤）
            whitelist = set(config.all_whitelisted_cmc_categories())

            # 6. 对每个板块算等权平均
            rows_to_write: list[SectorReturn] = []
            considered_cats = 0
            skipped_thin: list[str] = []

            for category, cmc_symbols in sorted(cat_to_syms.items()):
                if category not in whitelist:
                    continue
                considered_cats += 1

                # 交集：CMC 列出 ∩ BMAC pivot 实际有数据
                matched = cmc_symbols & sym_to_returns.keys()
                if len(matched) < MIN_TOKENS_PER_SECTOR:
                    skipped_thin.append(f"{category}({len(matched)})")
                    continue

                # 等权平均（pandas 向量化也行，但行数小直接 Python 算更清晰）
                agg: dict[str, list[float]] = {k: [] for k in RETURN_LOOKBACKS}
                for sym in matched:
                    rets = sym_to_returns[sym]
                    for ret_name in RETURN_LOOKBACKS:
                        if ret_name in rets:
                            agg[ret_name].append(rets[ret_name])

                means: dict[str, Optional[float]] = {}
                for ret_name, values in agg.items():
                    means[ret_name] = (
                        round(sum(values) / len(values), 4) if values else None
                    )

                rows_to_write.append(SectorReturn(
                    snapshot_at=snapshot_at,
                    category=category,
                    group_name=config.cmc_category_to_group(category),
                    token_count=len(matched),
                    ret_1h=means["ret_1h"],
                    ret_24h=means["ret_24h"],
                    ret_168h=means["ret_168h"],
                    ret_720h=means["ret_720h"],
                ))

            # 7. 写库：先删同 snapshot_at 的旧行（处理重跑），再写新行
            if rows_to_write:
                session.execute(
                    delete(SectorReturn).where(SectorReturn.snapshot_at == snapshot_at)
                )
                session.add_all(rows_to_write)
                session.commit()

            logger.info(
                "sector_scan 完成: snapshot_at={} 写 {} 板块（白名单 {}/{} 命中, "
                "活跃 symbol 不足跳过 {}）",
                snapshot_at, len(rows_to_write), considered_cats, len(whitelist),
                len(skipped_thin),
            )
            if skipped_thin:
                logger.debug("token<{} 跳过: {}", MIN_TOKENS_PER_SECTOR, skipped_thin)

            return {
                "snapshot_at": snapshot_at,
                "sectors_written": len(rows_to_write),
                "considered_cats": considered_cats,
                "skipped_thin": len(skipped_thin),
                "active_symbols": len(sym_to_returns),
            }
        except Exception:
            if own_session:
                session.rollback()
            raise
        finally:
            if own_session:
                session.close()
