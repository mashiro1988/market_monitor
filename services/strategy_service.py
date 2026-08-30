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


# ---------- 每日检查状态机（设计稿 §5） ----------

def _last_status_kind(db, position_id: int) -> str | None:
    """该批次最近一次 daily_ok/stop_breach 事件的 kind，用于"未破→破"转换检测。"""
    row = (
        db.query(StrategyEvent.kind)
        .filter(StrategyEvent.position_id == position_id,
                StrategyEvent.kind.in_(["daily_ok", "stop_breach"]))
        .order_by(StrategyEvent.id.desc())
        .first()
    )
    return row[0] if row else None


def _has_event(db, position_id: int, kind: str) -> bool:
    return (
        db.query(StrategyEvent.id)
        .filter(StrategyEvent.position_id == position_id, StrategyEvent.kind == kind)
        .first()
        is not None
    )


def _emit(db, *, symbol: str, kind: str, message: str, payload: dict,
          position_id: int | None = None, push: bool = False, channel=None) -> StrategyEvent:
    """写事件；push=True 时经企业微信发出并镜像一条 AlertLog（告警页可见）。"""
    delivered = False
    if push:
        channel = channel or WeChatWorkChannel()
        title = f"【持仓策略】{message.splitlines()[0]}"
        delivered = bool(channel.send(title, message))
        log = AlertLog(timestamp=_utc_now(), rule_name=RULE_NAME,
                       message=f"{title}\n{message}"[:8000],
                       channel="wechat_work", delivered=delivered)
        db.add(log)
    event = StrategyEvent(symbol=symbol, position_id=position_id, kind=kind,
                          message=message, payload_json=json.dumps(payload, ensure_ascii=False),
                          pushed=delivered)
    db.add(event)
    db.commit()
    return event


def _batch_state_for(pos: StrategyPosition, candles, v_used: float, settings) -> eng.BatchState:
    return eng.batch_state(
        entry_price=pos.entry_price, entry_at=pos.entry_at, quantity=pos.quantity,
        candles=candles, v_used=v_used, x_soft=settings.x_soft, x_hard=settings.x_hard,
    )


def run_daily_check(*, db=None, symbol: str = DEFAULT_SYMBOL, channel=None) -> list[str]:
    """每日 UTC 收盘后的核心检查。返回本次产生的事件 kind 列表（含未推送）。

    执行顺序遵循清晨 housekeeping 的时间语义：先更新波动率闩锁（vol_update），
    再逐批判定防线（stop_breach/daily_ok/b2_unlocked），末尾算减仓建议与重入场观察。
    """
    own = db is None
    db = db or SessionLocal()
    produced: list[str] = []
    try:
        settings = get_settings(db)
        candles = fetch_daily_candles(symbol)
        if not candles:
            logger.warning(f"[strategy] {symbol} 无已确认日K，跳过本轮")
            return produced

        closes = [c.close for c in candles]
        vols = eng.ewma_vol_series(closes, alpha=settings.ewma_alpha)
        if not vols:
            return produced
        vol_latest = vols[-1]

        state = db.get(StrategySymbolState, symbol)
        if state is None:
            state = StrategySymbolState(symbol=symbol)
            db.add(state)
        prev_used = state.v_used
        new_used = eng.walk_latch([vol_latest], threshold=settings.vol_update_threshold,
                                  seed=prev_used)[-1]
        vol_changed = prev_used is not None and new_used != prev_used
        state.v_used = new_used
        state.v_used_at = _utc_now()
        db.commit()

        if vol_changed:
            _emit(db, symbol=symbol, kind="vol_update",
                  payload={"prev": prev_used, "new": new_used},
                  message=f"波动率闩锁更新：{prev_used:.2%} → {new_used:.2%}")
            produced.append("vol_update")

        budget_usd = settings.capital * settings.risk_budget_pct
        positions = open_positions(db, symbol)
        total_occupy = 0.0
        breach_soft_level: float | None = None

        for pos in positions:
            st = _batch_state_for(pos, candles, new_used, settings)
            total_occupy += st.occupy_usd
            payload = {"soft": st.soft_stop, "hard": st.hard_stop, "close": st.last_close,
                       "anchor": st.anchor_high, "v_used": new_used}

            if st.breached:
                if _last_status_kind(db, pos.id) != "stop_breach":
                    _emit(db, symbol=symbol, position_id=pos.id, kind="stop_breach", push=True,
                          channel=channel, payload=payload,
                          message=(f"{pos.batch_label} 日收盘 {st.last_close:.4f} 跌破软止损 "
                                   f"{st.soft_stop:.4f}，按框架应清仓；已进入重入场观察（30 天）"))
                    produced.append("stop_breach")
                    breach_soft_level = st.soft_stop    # 仅新发生的破线上膛/刷新观察（最新覆盖，§2.6）；
                                                        # 持续破线不重置时钟，否则过期判定永远轮不上
            else:
                _emit(db, symbol=symbol, position_id=pos.id, kind="daily_ok", payload=payload,
                      message=(f"{pos.batch_label} 收盘 {st.last_close:.4f} ≥ 软止损 "
                               f"{st.soft_stop:.4f}（余量 {st.distance_pct:+.1%}），持有"))
                produced.append("daily_ok")

            if st.locked and not _has_event(db, pos.id, "b2_unlocked"):
                _emit(db, symbol=symbol, position_id=pos.id, kind="b2_unlocked", push=True,
                      channel=channel, payload=payload,
                      message=(f"{pos.batch_label} 软止损 {st.soft_stop:.4f} 已抬过成本 "
                               f"{pos.entry_price:.4f}：锁盈，额度释放，可开始找微观确认事件"))
                produced.append("b2_unlocked")

        if breach_soft_level is not None:
            state.reentry_level = breach_soft_level
            state.reentry_breached_at = _utc_now()
            db.commit()
        still_breached_holding = breach_soft_level is None and any(
            _last_status_kind(db, p.id) == "stop_breach" for p in positions
        )

        if vol_changed and total_occupy > budget_usd and positions:
            unlocked = [
                (p, _batch_state_for(p, candles, new_used, settings)) for p in positions
            ]
            target_qty = sum(
                budget_usd / (p.entry_price - st.soft_stop)
                for p, st in unlocked if p.entry_price > st.soft_stop
            )
            _emit(db, symbol=symbol, kind="reduce_suggest", push=True, channel=channel,
                  payload={"total_occupy": total_occupy, "budget": budget_usd,
                           "target_qty": target_qty},
                  message=(f"波动率变更后占用 ${total_occupy:,.0f} 超预算 ${budget_usd:,.0f}，"
                           f"贴预算目标持仓约 {target_qty:,.0f} 枚"))
            produced.append("reduce_suggest")

        # 重入场观察（独立于持仓存在，用户平仓后仍继续盯；先判过期再判站回，设计稿 §2.6）
        if state.reentry_level is not None and breach_soft_level is None:
            last_close = closes[-1]
            aged_days = (_utc_now() - (state.reentry_breached_at or _utc_now())).days
            if aged_days > REENTRY_WINDOW_DAYS:
                _emit(db, symbol=symbol, kind="reentry_expired",
                      payload={"level": state.reentry_level},
                      message=f"重入场观察满 {REENTRY_WINDOW_DAYS} 天未站回，观察结束")
                produced.append("reentry_expired")
                state.reentry_level = None
                state.reentry_breached_at = None
                db.commit()
            elif last_close > state.reentry_level and not still_breached_holding:
                _emit(db, symbol=symbol, kind="reentry_ready", push=True, channel=channel,
                      payload={"level": state.reentry_level, "close": last_close},
                      message=(f"价格收盘 {last_close:.4f} 站回原止损线 {state.reentry_level:.4f} 上方，"
                               f"可按计算器评估重入场（全新批次、新预算、新止损）"))
                produced.append("reentry_ready")
                state.reentry_level = None
                state.reentry_breached_at = None
                db.commit()

        return produced
    finally:
        if own:
            db.close()


# ---------- overview 与计算器（设计稿 §4 / §2.7） ----------

def _live_price(db, symbol: str) -> tuple[float | None, datetime | None]:
    """横幅现价：5m 管道最新价（instId 前缀 → PriceSnapshot symbol "BASE/USDT"）。"""
    base = symbol.split("-")[0]
    row = (
        db.query(PriceSnapshot.price, PriceSnapshot.timestamp)
        .filter(PriceSnapshot.symbol == f"{base}/USDT")
        .order_by(PriceSnapshot.timestamp.desc())
        .first()
    )
    return (row[0], row[1]) if row else (None, None)


def get_overview(db, *, symbol: str = DEFAULT_SYMBOL) -> dict:
    """页面主接口：横幅 + 图 + 批次读数。拉取失败降级 data_stale=True（设计稿 §4）。"""
    settings = get_settings(db)
    state = db.get(StrategySymbolState, symbol)
    positions = open_positions(db, symbol)
    live, live_at = _live_price(db, symbol)
    base = {
        "symbol": symbol,
        "generated_at": _utc_now().isoformat(),
        "data_stale": False,
        "live_price": live,
        "live_price_at": live_at.isoformat() if live_at else None,
        "settings": {
            "capital": settings.capital, "risk_budget_pct": settings.risk_budget_pct,
            "x_soft": settings.x_soft, "x_hard": settings.x_hard,
            "ewma_alpha": settings.ewma_alpha,
            "vol_update_threshold": settings.vol_update_threshold,
        },
        "reentry": (
            {"level": state.reentry_level,
             "breached_at": state.reentry_breached_at.isoformat() if state.reentry_breached_at else None}
            if state and state.reentry_level is not None else None
        ),
    }
    empty_chart = {"days": [], "soft_line": [], "hard_current": None,
                   "cost_lines": [], "anchor_point": None, "entry_markers": []}
    try:
        candles = fetch_daily_candles(symbol)
    except Exception as exc:
        logger.warning(f"[strategy] overview 拉取 {symbol} 失败: {exc}")
        candles = []
    closes = [c.close for c in candles]
    vols = eng.ewma_vol_series(closes, alpha=settings.ewma_alpha) if len(closes) >= 2 else []
    vol_latest = vols[-1] if vols else None
    v_used = state.v_used if state and state.v_used is not None else vol_latest
    if not candles or v_used is None:
        return {**base, "data_stale": True, "vol_latest": vol_latest, "v_used": v_used,
                "verdict": "no_data", "budget_usd": settings.capital * settings.risk_budget_pct,
                "total_occupy_usd": 0.0, "batches": [], "chart": empty_chart}

    batches = []
    total_occupy = 0.0
    any_breach = False
    for pos in positions:
        st = _batch_state_for(pos, candles, v_used, settings)
        total_occupy += st.occupy_usd
        any_breach = any_breach or st.breached
        batches.append({
            "id": pos.id, "batch_label": pos.batch_label,
            "entry_at": pos.entry_at.isoformat(), "entry_price": pos.entry_price,
            "quantity": pos.quantity, "forecast": pos.forecast, "note": pos.note,
            "anchor_high": st.anchor_high, "soft_stop": st.soft_stop, "hard_stop": st.hard_stop,
            "breached": st.breached, "locked": st.locked,
            "occupy_usd": st.occupy_usd, "distance_pct": st.distance_pct,
            "pnl_usd": pos.quantity * (closes[-1] - pos.entry_price),
        })

    # 图：从最早批次入场前 5 根起截窗；软止损历史 = 闩锁重放近似（设计稿 §2.3 注）
    chart_days = candles
    soft_line: list[float | None] = [None] * len(chart_days)
    entry_markers = []
    anchor_point = None
    if positions:
        first_entry = min(p.entry_at for p in positions)
        first_idx = next((i for i, c in enumerate(candles) if c.close_time > first_entry), len(candles))
        start_idx = max(0, first_idx - 5)
        chart_days = candles[start_idx:]
        replay_used = eng.walk_latch(vols, threshold=settings.vol_update_threshold) if vols else []
        soft_line = []
        for i, c in enumerate(chart_days):
            gi = start_idx + i
            vu = replay_used[gi - 1] if 1 <= gi <= len(replay_used) else v_used
            stops = [
                eng.batch_state(entry_price=p.entry_price, entry_at=p.entry_at, quantity=p.quantity,
                                candles=candles[: gi + 1], v_used=vu,
                                x_soft=settings.x_soft, x_hard=settings.x_hard).soft_stop
                for p in positions if c.close_time > p.entry_at
            ]
            soft_line.append(max(stops) if stops else None)
        for p in positions:
            marker_candle = next((c for c in chart_days if c.close_time > p.entry_at), None)
            entry_markers.append({
                "date": (marker_candle.date if marker_candle else chart_days[-1].date).strftime("%m-%d"),
                "label": p.batch_label, "value": p.entry_price,
            })
        top = max(batches, key=lambda b: b["anchor_high"])
        anchor_candles = [c for c in chart_days if c.close == top["anchor_high"]]
        if anchor_candles:
            anchor_point = {"date": anchor_candles[-1].date.strftime("%m-%d"), "value": top["anchor_high"]}

    budget_usd = settings.capital * settings.risk_budget_pct
    verdict = "no_position" if not positions else ("breach" if any_breach else "hold")
    return {
        **base,
        "vol_latest": vol_latest, "v_used": v_used,
        "verdict": verdict, "budget_usd": budget_usd,
        "total_occupy_usd": total_occupy, "batches": batches,
        "chart": {
            "days": [{"date": c.date.strftime("%m-%d"), "close": c.close} for c in chart_days],
            "soft_line": soft_line,
            "hard_current": min((b["hard_stop"] for b in batches), default=None),
            "cost_lines": [{"label": b["batch_label"], "value": b["entry_price"]} for b in batches],
            "anchor_point": anchor_point,
            "entry_markers": entry_markers,
        },
    }


def simulate(db, *, price: float, forecast: int, vol: float | None = None,
             budget_pct: float | None = None, symbol: str = DEFAULT_SYMBOL) -> dict:
    """建仓计算器：vol/budget 缺省时用在用值与参数表值。"""
    settings = get_settings(db)
    if vol is None:
        state = db.get(StrategySymbolState, symbol)
        vol = state.v_used if state and state.v_used is not None else None
    if vol is None:
        candles = fetch_daily_candles(symbol)
        vols = eng.ewma_vol_series([c.close for c in candles], alpha=settings.ewma_alpha)
        vol = vols[-1]
    pct = budget_pct if budget_pct is not None else settings.risk_budget_pct
    budget_usd = settings.capital * pct
    sim = eng.simulate_entry(price=price, forecast=forecast, budget_usd=budget_usd,
                             vol=vol, x_soft=settings.x_soft)
    return {**sim, "vol": vol, "budget_usd": budget_usd,
            "leverage": sim["notional_usd"] / settings.capital if settings.capital else None}
