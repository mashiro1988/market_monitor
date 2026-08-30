# -*- coding: utf-8 -*-
"""持仓策略编排服务：OKX 日线现取现算 + 每日检查 + 事件推送 + overview 组装。

公式一律调 services/strategy_engine.py；本文件只做 IO 与状态机。
设计稿：docs/superpowers/specs/2026-08-28-position-strategy-design.md。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import ccxt
from loguru import logger

import config
from alerts.channels.wechat_work import WeChatWorkChannel
from database import SessionLocal
from models.alert_log import AlertLog
from models.price import PriceSnapshot
from models.strategy import StrategyEvent, StrategyPosition, StrategySettings, StrategySymbolState
from services import strategy_engine as eng

RULE_NAME = "strategy_action"          # AlertLog.rule_name，告警页可见
DEFAULT_SYMBOL = "VIRTUAL-USDT-SWAP"
CANDLE_LIMIT = 300
REENTRY_WINDOW_DAYS = 30
_TIMEOUT_MS = 15_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- OKX 日线（1Dutc = UTC 00:00 切日，设计稿 §2.1） ----------

def _parse_okx_candles(payload: dict) -> list[eng.DailyCandle]:
    """OKX 返回最新在前、九列 [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]；只留已确认，转升序。"""
    rows = payload.get("data") or []
    candles = [
        eng.DailyCandle(
            date=datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc).replace(tzinfo=None),
            open=float(r[1]), high=float(r[2]), low=float(r[3]), close=float(r[4]),
        )
        for r in rows if len(r) >= 9 and r[8] == "1"
    ]
    candles.sort(key=lambda c: c.date)
    return candles


def fetch_daily_candles(symbol: str) -> list[eng.DailyCandle]:
    """拉最近 300 根已确认 UTC 日 K。失败抛异常，由调用方决定降级语义。"""
    exchange = ccxt.okx({"enableRateLimit": True, "timeout": _TIMEOUT_MS})
    proxy = config.proxy_url()
    if proxy:
        exchange.httpsProxy = proxy
    payload = exchange.publicGetMarketCandles({
        "instId": symbol, "bar": "1Dutc", "limit": str(CANDLE_LIMIT),
    })
    return _parse_okx_candles(payload)


# ---------- 参数与批次 ----------

def get_settings(db) -> StrategySettings:
    """单行参数表 get-or-create（默认值即用户 2026-08-28 拍板值，定义在模型列默认里）。"""
    row = db.query(StrategySettings).first()
    if row is None:
        row = StrategySettings()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def open_positions(db, symbol: str) -> list[StrategyPosition]:
    return (
        db.query(StrategyPosition)
        .filter(StrategyPosition.symbol == symbol, StrategyPosition.status == "open")
        .order_by(StrategyPosition.entry_at.asc())
        .all()
    )
