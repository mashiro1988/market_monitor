from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config
from alerts._session import get_alert_session
from alerts.rules import AlertRuleType
from alerts.types import PriceWindowMove
from chart_utils import format_beijing_time
from models.price import PriceSnapshot
from scanners.base import PriceRecord


class PriceAlertMixin:
    def evaluate_prices(self, price_records: list[PriceRecord]):
        """评估价格相关的告警规则"""
        alerts_to_send = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        max_staleness = timedelta(
            minutes=max(0, int(getattr(config, "ALERT_PRICE_MAX_STALENESS_MINUTES", 30)))
        )

        for rule in self.rules:
            if not rule.enabled or rule.rule_type not in (AlertRuleType.PRICE_CHANGE, AlertRuleType.PRICE_LEVEL):
                continue
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes, rule.channels):
                continue

            if rule.rule_type == AlertRuleType.PRICE_CHANGE:
                threshold = rule.params.get("threshold_pct", 3.0)
                target_symbol = rule.params.get("symbol")
                window_minutes = int(rule.params.get("window_minutes", 0) or 0)

                triggered = []
                for r in price_records:
                    if target_symbol and r.symbol != target_symbol:
                        continue
                    # 陈旧数据保护：源停更（休市/掉线）时不对旧 bar 反复告警
                    if self._is_stale_for_alert(r.timestamp, now, max_staleness):
                        continue
                    move = self._price_window_move(r, window_minutes)
                    if move is not None and abs(move.change_pct) >= threshold:
                        triggered.append((r, move))

                if triggered:
                    lines = []
                    for r, move in triggered:
                        color = "warning" if move.change_pct < 0 else "info"
                        start_time = format_beijing_time(move.start_time, "%m-%d %H:%M")
                        end_time = format_beijing_time(move.end_time, "%H:%M")
                        lines.append(
                            f"> <font color=\"{color}\">{r.name} ({r.symbol}): "
                            f"{window_minutes}m {move.change_pct:+.2f}%</font>\n"
                            f"> 时间区间: {start_time}-{end_time} 北京时间\n"
                            f"> 价格: ${move.start_price:,.2f} → ${move.end_price:,.2f} "
                            f"(区间 ${move.low_price:,.2f}-${move.high_price:,.2f})"
                        )
                    content = "\n".join(lines)
                    title = f"价格异动 | {len(triggered)} 个品种超阈值 ({window_minutes}m / {threshold}%)"
                    alerts_to_send.append((rule, title, content))
            elif rule.rule_type == AlertRuleType.PRICE_LEVEL:
                target_symbol = rule.params.get("symbol")
                triggered = []
                for r in price_records:
                    if target_symbol and r.symbol != target_symbol:
                        continue
                    if self._is_stale_for_alert(r.timestamp, now, max_staleness):
                        continue
                    hit = self._price_level_hit(r, rule.params)
                    if hit is not None:
                        triggered.append((r, hit))

                if triggered:
                    lines = []
                    for r, hit in triggered:
                        direction, level = hit
                        comparator = ">=" if direction == "above" else "<="
                        lines.append(
                            f"> <font color=\"warning\">{r.name} ({r.symbol}): "
                            f"{self._format_price_value(r.price, r.asset_class)} "
                            f"{comparator} {self._format_price_value(level, r.asset_class)}</font>"
                        )
                    content = "\n".join(lines)
                    title = f"Price level | {len(triggered)} symbol(s) triggered"
                    alerts_to_send.append((rule, title, content))

        for rule, title, content in alerts_to_send:
            self._dispatch(rule, title, content)

    @staticmethod
    def _price_level_hit(record: PriceRecord, params: dict) -> tuple[str, float] | None:
        def _to_float(value) -> float | None:
            try:
                if value is None:
                    return None
                return float(value)
            except (TypeError, ValueError):
                return None

        above = _to_float(params.get("above", params.get("above_price", params.get("min_price"))))
        below = _to_float(params.get("below", params.get("below_price", params.get("max_price"))))
        if above is not None and record.price >= above:
            return "above", above
        if below is not None and record.price <= below:
            return "below", below

        level = _to_float(params.get("level", params.get("price")))
        if level is None:
            return None
        direction = str(params.get("direction", "above")).strip().lower()
        if direction in {"above", "gte", "ge", ">=", ">"} and record.price >= level:
            return "above", level
        if direction in {"below", "lte", "le", "<=", "<"} and record.price <= level:
            return "below", level
        return None

    @staticmethod
    def _is_stale_for_alert(ts, now: datetime, max_staleness: timedelta) -> bool:
        """当前价 bar 是否过旧、不应告警（源停更：休市/周末/掉线）。
        max_staleness<=0 关闭此保护（永远不算 stale）。"""
        if max_staleness.total_seconds() <= 0:
            return False
        if ts is None:
            return True
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        return (now - ts) > max_staleness

    @staticmethod
    def _price_window_move(record: PriceRecord, window_minutes: int) -> PriceWindowMove | None:
        """按规则窗口计算价格移动；无窗口配置时回退到源端 change_pct。"""
        if window_minutes <= 0:
            if record.change_pct is None or record.prev_price in (None, 0):
                return None
            end_time = record.timestamp or datetime.now(timezone.utc).replace(tzinfo=None)
            start_time = end_time - timedelta(minutes=window_minutes)
            low_price = min(record.prev_price, record.price)
            high_price = max(record.prev_price, record.price)
            return PriceWindowMove(
                change_pct=record.change_pct,
                start_time=start_time,
                end_time=end_time,
                start_price=record.prev_price,
                end_price=record.price,
                low_price=low_price,
                high_price=high_price,
            )

        current_ts = record.timestamp
        if current_ts is None:
            return None
        if current_ts.tzinfo is not None:
            current_ts = current_ts.astimezone(timezone.utc).replace(tzinfo=None)

        session = get_alert_session()
        try:
            return PriceAlertMixin._price_window_move_from_session(
                session=session,
                symbol=record.symbol,
                current_ts=current_ts,
                current_price=record.price,
                window_minutes=window_minutes,
            )
        finally:
            session.close()

    @staticmethod
    def _price_window_move_from_session(
        session,
        symbol: str,
        current_ts: datetime,
        current_price: float,
        window_minutes: int,
    ) -> PriceWindowMove | None:
        """Calculate a window move using an existing DB session."""
        target_ts = current_ts - timedelta(minutes=window_minutes)
        scan_interval = int(config.SCAN_INTERVALS.get("price", 5) or 5)
        tolerance = timedelta(minutes=max(scan_interval * 2, 1))

        candidates = session.query(PriceSnapshot).filter(
            PriceSnapshot.symbol == symbol,
            PriceSnapshot.timestamp >= target_ts - tolerance,
            PriceSnapshot.timestamp <= target_ts + tolerance,
            PriceSnapshot.timestamp < current_ts,
        ).all()
        if not candidates:
            return None
        base = min(candidates, key=lambda row: abs(row.timestamp - target_ts))
        if base.price in (None, 0):
            return None
        range_rows = session.query(PriceSnapshot).filter(
            PriceSnapshot.symbol == symbol,
            PriceSnapshot.timestamp >= base.timestamp,
            PriceSnapshot.timestamp <= current_ts,
        ).all()
        prices = [row.price for row in range_rows if row.price is not None]
        prices.append(current_price)
        low_price = min(prices)
        high_price = max(prices)
        change_pct = (current_price - base.price) / abs(base.price) * 100
        return PriceWindowMove(
            change_pct=change_pct,
            start_time=base.timestamp,
            end_time=current_ts,
            start_price=base.price,
            end_price=current_price,
            low_price=low_price,
            high_price=high_price,
        )

    @staticmethod
    def _price_window_move_for_snapshot(session, snapshot: PriceSnapshot, window_minutes: int) -> PriceWindowMove | None:
        """Calculate a configured window move for an already persisted snapshot."""
        if snapshot.timestamp is None:
            return None
        current_ts = snapshot.timestamp
        if current_ts.tzinfo is not None:
            current_ts = current_ts.astimezone(timezone.utc).replace(tzinfo=None)
        return PriceAlertMixin._price_window_move_from_session(
            session=session,
            symbol=snapshot.symbol,
            current_ts=current_ts,
            current_price=snapshot.price,
            window_minutes=window_minutes,
        )

    @staticmethod
    def _price_window_change_pct(record: PriceRecord, window_minutes: int) -> float | None:
        """Compatibility helper for tests and older callers."""
        move = PriceAlertMixin._price_window_move(record, window_minutes)
        return move.change_pct if move else None

    @staticmethod
    def _format_price_value(price: float, asset_class: str) -> str:
        if asset_class == "bond":
            return f"{price:.3f}%"
        if asset_class == "crypto":
            return f"${price:,.2f}"
        return f"{price:,.2f}"
