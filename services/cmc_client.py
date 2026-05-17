"""CoinMarketCap 板块映射客户端。

职责：
- 拉 CMC `/v1/cryptocurrency/categories` 取全量板块列表
- 对在白名单内的板块（config.SECTOR_WHITELIST）逐个拉 `/v1/cryptocurrency/category?id=`
  取其下币种，把 (symbol, category) 多对多关系 upsert 到 `cmc_symbol_categories` 表
- 用 7 天 TTL 控制：默认启动时检查最新 updated_at，距今 ≥ 7 天才刷新
- 提供 `python run.py refresh-sectors` CLI 强制刷新（force=True）

存的是 CMC 视角的 symbol（如 "ETH"、"BTC"），不带后缀。sector_scanner 自己负责把
binance 的 "ETHUSDT" 标准化回 "ETH" 后 JOIN。
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from typing import Iterable, Optional

import requests
from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import config
from database import SessionLocal
from models.sector import CmcSymbolCategory


# CMC API endpoints
_LIST_URL = "/v1/cryptocurrency/categories"
_DETAIL_URL = "/v1/cryptocurrency/category"

# 接受的 symbol 形态：纯大写字母 + 数字（避免乱码/中文/特殊字符）。
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,15}$")

# CMC API 在国内可直连，且走代理（Clash 等）容易在长会话里掐掉 SSL。
# 默认 bypass 代理；若用户在墙外或要强行走代理，设 CMC_USE_PROXY=1。
_CMC_USE_PROXY = os.getenv("CMC_USE_PROXY", "0").strip().lower() in {"1", "true", "yes", "on"}
# 重试参数（SSL 抖动、Clash 抽风、5xx 等都重试）
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_BACKOFF = 2.0  # 秒；第 N 次重试 sleep base * 2^N


def _headers() -> dict[str, str]:
    if not config.CMC_API_KEY:
        raise RuntimeError(
            "CMC_API_KEY 未设置；请在 .env 里配置（见 docs/specs/remote_data_integration.md §5）"
        )
    return {"Accepts": "application/json", "X-CMC_PRO_API_KEY": config.CMC_API_KEY}


def _request_proxies() -> Optional[dict]:
    """除非显式 opt-in（CMC_USE_PROXY=1），都直连 CMC。"""
    if _CMC_USE_PROXY:
        return config.proxies() or None
    return None  # requests 用 None 表示用环境默认（PROXY/HTTP_PROXY env 也忽略走系统默认；
    # 但 config.py 在代理不可用时已经清空 *_PROXY env 了；当代理可用时 env 是被设了的，
    # 所以这里要更显式地 bypass — 见下面 trust_env=False 处理）


def _get(path: str, params: Optional[dict] = None, *, timeout: float = 30.0) -> dict:
    url = config.CMC_API_BASE_URL.rstrip("/") + path
    last_exc: Exception | None = None
    for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
        try:
            # 用 Session + trust_env=False 强制忽略环境 HTTP_PROXY/HTTPS_PROXY
            # （config.PROXY 启用时会把这些 env 写上，我们要绕过）。
            session = requests.Session()
            session.trust_env = False
            resp = session.get(
                url,
                headers=_headers(),
                params=params or {},
                timeout=timeout,
                proxies=_request_proxies(),  # None = 直连
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", {})
            err_code = status.get("error_code", 0)
            if err_code:
                raise RuntimeError(f"CMC API 错误 {err_code}: {status.get('error_message')}")
            return data
        except (requests.exceptions.SSLError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as exc:
            last_exc = exc
            if attempt < _RETRY_MAX_ATTEMPTS:
                backoff = _RETRY_BASE_BACKOFF * (2 ** (attempt - 1))
                logger.warning("CMC {} 第 {} 次失败 ({}), {:.1f}s 后重试",
                               path, attempt, type(exc).__name__, backoff)
                time.sleep(backoff)
                continue
            raise
        except requests.exceptions.HTTPError as exc:
            # 5xx 也重试；4xx 直接抛
            status_code = exc.response.status_code if exc.response is not None else 0
            if 500 <= status_code < 600 and attempt < _RETRY_MAX_ATTEMPTS:
                last_exc = exc
                backoff = _RETRY_BASE_BACKOFF * (2 ** (attempt - 1))
                logger.warning("CMC {} 返回 {} 第 {} 次, {:.1f}s 后重试",
                               path, status_code, attempt, backoff)
                time.sleep(backoff)
                continue
            raise
    # 不可达，循环里要么 return 要么 raise
    if last_exc:
        raise last_exc
    raise RuntimeError("CMC _get unreachable")


# ============================================================
# 全量板块列表（分页）
# ============================================================
def fetch_all_categories() -> list[dict]:
    """拉 CMC 所有 categories（~350 个）。每条至少含 `id`, `name`, `num_tokens`。"""
    all_items: list[dict] = []
    start = 1
    limit = 200
    while True:
        data = _get(_LIST_URL, {"start": start, "limit": limit})
        items = data.get("data") or []
        if not items:
            break
        all_items.extend(items)
        if len(items) < limit:
            break
        start += limit
        time.sleep(config.CMC_REQUEST_INTERVAL_SECONDS)
    logger.info("CMC 全量板块: {} 个", len(all_items))
    return all_items


def fetch_category_coins(category_id: str) -> list[dict]:
    """拉某板块下的所有币。每条至少含 `symbol`, `name`, `id`。"""
    all_coins: list[dict] = []
    start = 1
    limit = 1000
    while True:
        data = _get(_DETAIL_URL, {"id": category_id, "start": start, "limit": limit})
        cat_data = data.get("data") or {}
        coins = cat_data.get("coins") or []
        if not coins:
            break
        all_coins.extend(coins)
        if len(coins) < limit:
            break
        start += limit
        time.sleep(config.CMC_REQUEST_INTERVAL_SECONDS)
    return all_coins


# ============================================================
# TTL 检查
# ============================================================
def needs_refresh(session: Session, *, ttl_days: Optional[int] = None) -> bool:
    """表为空 or MAX(updated_at) 距今 ≥ ttl_days → True。"""
    ttl = ttl_days if ttl_days is not None else config.CMC_CACHE_TTL_DAYS
    latest = session.execute(select(func.max(CmcSymbolCategory.updated_at))).scalar()
    if latest is None:
        return True
    age = datetime.utcnow() - latest
    return age >= timedelta(days=ttl)


# ============================================================
# 刷新主逻辑
# ============================================================
def refresh_categories(
    *,
    force: bool = False,
    session: Optional[Session] = None,
    whitelist: Optional[Iterable[str]] = None,
) -> dict[str, int]:
    """拉 CMC 数据，刷新 `cmc_symbol_categories` 表（只针对白名单内的板块）。

    Args:
        force:     True 时跳过 TTL 检查，强制刷新
        session:   传入 session 复用调用方事务；None 时自管 session
        whitelist: 覆盖 config.SECTOR_WHITELIST 的板块列表（CMC category 名）；
                   None 时用 config.all_whitelisted_cmc_categories()

    Returns:
        {"categories": N, "symbols": M, "skipped": K}
    """
    own_session = session is None
    if own_session:
        session = SessionLocal()

    try:
        if not force and not needs_refresh(session):
            logger.info("CMC 板块缓存仍在 TTL 内，跳过刷新")
            return {"categories": 0, "symbols": 0, "skipped": 1}

        wl_set = set(whitelist) if whitelist is not None else set(config.all_whitelisted_cmc_categories())
        if not wl_set:
            logger.warning("白名单为空，refresh_categories 退出")
            return {"categories": 0, "symbols": 0, "skipped": 1}

        logger.info("开始刷新 CMC 板块映射 (白名单 {} 项)", len(wl_set))

        # 1) 拉全量 category 元信息，找出白名单内的 id
        all_cats = fetch_all_categories()
        target_cats = [c for c in all_cats if (c.get("name") or "").strip() in wl_set]
        missing = wl_set - {c["name"] for c in target_cats}
        if missing:
            logger.warning("以下白名单板块在 CMC 找不到（拼写错？）: {}", sorted(missing))

        logger.info("白名单命中 {} / {} 个 CMC 板块", len(target_cats), len(wl_set))

        # 2) 对每个目标板块拉币种
        category_count = 0
        symbol_count = 0
        now = datetime.utcnow()
        for idx, cat in enumerate(target_cats, 1):
            cat_id = str(cat["id"])
            cat_name = cat["name"]
            logger.info("[{}/{}] 拉取板块 {} (id={}, num_tokens={})",
                        idx, len(target_cats), cat_name, cat_id, cat.get("num_tokens"))
            try:
                coins = fetch_category_coins(cat_id)
            except Exception as exc:
                logger.warning("板块 {} 拉取失败: {}", cat_name, exc)
                continue

            # 过滤并提取 symbol（CMC 给的 symbol 一般是基础符号，如 "ETH"）
            valid_symbols: list[str] = []
            for coin in coins:
                sym = (coin.get("symbol") or "").strip().upper()
                if not sym or not _SYMBOL_RE.match(sym):
                    continue
                valid_symbols.append(sym)
            valid_symbols = sorted(set(valid_symbols))

            # 删除该板块原有的所有映射 → 重新插入（处理"已退出板块的币种"）
            session.execute(delete(CmcSymbolCategory).where(CmcSymbolCategory.category == cat_name))
            session.add_all([
                CmcSymbolCategory(
                    symbol=sym,
                    category=cat_name,
                    category_id=cat_id,
                    updated_at=now,
                )
                for sym in valid_symbols
            ])
            session.commit()
            category_count += 1
            symbol_count += len(valid_symbols)

            # 限速：板块间也歇 2.5s
            time.sleep(config.CMC_REQUEST_INTERVAL_SECONDS)

        logger.info("CMC 板块刷新完成: {} 板块, 累计 {} 个 (symbol, category) 对",
                    category_count, symbol_count)
        return {"categories": category_count, "symbols": symbol_count, "skipped": 0}
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()


# ============================================================
# 读取（给 sector_scanner / sector_service 用）
# ============================================================
def load_category_to_symbols(session: Optional[Session] = None) -> dict[str, set[str]]:
    """返回 {category_name: {symbol, ...}, ...}。一行 SQL。"""
    own_session = session is None
    if own_session:
        session = SessionLocal()
    try:
        rows = session.execute(
            select(CmcSymbolCategory.category, CmcSymbolCategory.symbol)
        ).all()
        out: dict[str, set[str]] = {}
        for cat, sym in rows:
            out.setdefault(cat, set()).add(sym)
        return out
    finally:
        if own_session:
            session.close()
