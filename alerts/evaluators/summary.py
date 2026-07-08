from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

import config
from alerts.rules import AlertRuleType
from alerts.types import PriceThresholdSummary, PriceWindowMove
from chart_utils import format_beijing_time
from models.news import NewsItem
from models.price import PriceSnapshot


def _get_session():
    # Keep tests and older callers that monkeypatch alerts.engine.get_session working.
    from alerts import engine as engine_module

    return engine_module.get_session()


class HourlySummaryMixin:
    @staticmethod
    def _format_summary_pct(value: float | None) -> str:
        if value is None:
            return "N/A"
        color = "info" if value > 0 else ("warning" if value < 0 else "comment")
        return f"<font color=\"{color}\">{value:+.2f}%</font>"

    @staticmethod
    def _snapshot_change_pct(session, snapshot: PriceSnapshot, minutes: int) -> float | None:
        """用市场概览同口径计算单个快照的 5m / 1h / 24h 涨跌幅。"""
        if snapshot.timestamp is None:
            return None
        target = snapshot.timestamp - timedelta(minutes=minutes)
        tolerances_min = {5: 8, 60: 20, 1440: 240}
        tolerance = timedelta(minutes=tolerances_min.get(minutes, minutes))
        candidates = session.query(PriceSnapshot).filter(
            PriceSnapshot.symbol == snapshot.symbol,
            PriceSnapshot.timestamp <= target,
            PriceSnapshot.timestamp >= target - tolerance,
        ).all()
        if not candidates:
            return None
        base = min(candidates, key=lambda row: abs(row.timestamp - target))
        if base.price in (None, 0):
            return None
        return (snapshot.price - base.price) / base.price * 100

    @staticmethod
    def _latest_snapshots_for_symbols(session, symbols: list[str], now: datetime) -> dict[str, PriceSnapshot]:
        """按配置顺序所需 symbols 取最近 10 天内最新快照，休市时也保留上个交易日。"""
        cutoff = now - timedelta(days=10)
        rows = session.query(PriceSnapshot).filter(
            PriceSnapshot.symbol.in_(symbols),
            PriceSnapshot.timestamp >= cutoff,
        ).order_by(PriceSnapshot.timestamp.desc()).all()

        latest: dict[str, PriceSnapshot] = {}
        for row in rows:
            if row.symbol not in latest:
                latest[row.symbol] = row
        return latest

    def _hourly_price_threshold_summaries(
        self,
        session,
        symbols: list[str],
        since: datetime,
        until: datetime,
    ) -> list[PriceThresholdSummary]:
        """归纳过去一小时内默认观察品种触发 price_change 阈值的情况。"""
        symbol_set = set(symbols)
        symbol_order = {symbol: index for index, symbol in enumerate(symbols)}
        summaries: list[PriceThresholdSummary] = []

        for rule in self.rules:
            if not rule.enabled or rule.rule_type != AlertRuleType.PRICE_CHANGE:
                continue

            configured_symbol = rule.params.get("symbol")
            target_symbols = [configured_symbol] if configured_symbol else symbols
            target_symbols = [symbol for symbol in target_symbols if symbol in symbol_set]
            if not target_symbols:
                continue

            threshold = float(rule.params.get("threshold_pct", 3.0))
            window_minutes = int(rule.params.get("window_minutes", 0) or 0)
            if window_minutes <= 0:
                continue

            for symbol in target_symbols:
                snapshots = session.query(PriceSnapshot).filter(
                    PriceSnapshot.symbol == symbol,
                    PriceSnapshot.timestamp >= since,
                    PriceSnapshot.timestamp <= until,
                ).order_by(PriceSnapshot.timestamp.asc()).all()

                hits: list[tuple[PriceSnapshot, PriceWindowMove]] = []
                for snapshot in snapshots:
                    move = self._price_window_move_for_snapshot(session, snapshot, window_minutes)
                    if move is not None and abs(move.change_pct) >= threshold:
                        hits.append((snapshot, move))

                if not hits:
                    continue

                strongest_snapshot, strongest_move = max(hits, key=lambda item: abs(item[1].change_pct))
                summaries.append(PriceThresholdSummary(
                    symbol=symbol,
                    name=strongest_snapshot.name,
                    asset_class=strongest_snapshot.asset_class,
                    threshold_pct=threshold,
                    window_minutes=window_minutes,
                    trigger_count=len(hits),
                    strongest_move=strongest_move,
                ))

        summaries.sort(key=lambda item: symbol_order.get(item.symbol, 999))
        return summaries

    def send_hourly_summary(self):
        """发送每小时市场状态摘要"""
        for rule in self.rules:
            if not rule.enabled or rule.rule_type != AlertRuleType.HOURLY_SUMMARY:
                continue
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes, rule.channels):
                continue

            session = _get_session()
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                one_hour_ago = now - timedelta(hours=1)
                summary_symbols = config.MARKET_OVERVIEW_DEFAULT_SYMBOLS

                latest_by_symbol = self._latest_snapshots_for_symbols(session, summary_symbols, now)
                if not latest_by_symbol:
                    continue

                lines = [
                    f"**时间**: {format_beijing_time(now)} 北京时间",
                    "",
                    "**观察品种**",
                ]

                for symbol in summary_symbols:
                    p = latest_by_symbol.get(symbol)
                    if p is None:
                        lines.append(f"> {symbol}: N/A")
                        continue

                    change_5m = self._snapshot_change_pct(session, p, 5)
                    change_1h = self._snapshot_change_pct(session, p, 60)
                    change_24h = self._snapshot_change_pct(session, p, 1440)
                    ts = format_beijing_time(p.timestamp, "%m-%d %H:%M") if p.timestamp else ""
                    lines.append(
                        f"> {p.name} ({p.symbol}): {self._format_price_value(p.price, p.asset_class)}"
                        f" | 5m {self._format_summary_pct(change_5m)}"
                        f" | 1h {self._format_summary_pct(change_1h)}"
                        f" | 24h {self._format_summary_pct(change_24h)}"
                        f" <font color=\"comment\">{ts}</font>"
                    )
                lines.append("")

                lines.append("**价格阈值触发（过去1小时）**")
                threshold_summaries = self._hourly_price_threshold_summaries(
                    session=session,
                    symbols=summary_symbols,
                    since=one_hour_ago,
                    until=now,
                )
                if threshold_summaries:
                    for item in threshold_summaries:
                        move = item.strongest_move
                        color = "warning" if move.change_pct < 0 else "info"
                        start_time = format_beijing_time(move.start_time, "%m-%d %H:%M")
                        end_time = format_beijing_time(move.end_time, "%H:%M")
                        lines.append(
                            f"> <font color=\"{color}\">{item.name} ({item.symbol})</font>: "
                            f"触发 {item.trigger_count} 次，最强 {item.window_minutes}m {move.change_pct:+.2f}% "
                            f"（阈值 ±{item.threshold_pct:.2f}%）\n"
                            f"> 时间区间: {start_time}-{end_time} 北京时间；"
                            f"价格 {self._format_price_value(move.start_price, item.asset_class)} → "
                            f"{self._format_price_value(move.end_price, item.asset_class)}"
                        )
                else:
                    lines.append("> 过去1小时默认观察品种未触发已配置价格阈值。")
                lines.append("")

                news_count = session.query(NewsItem).filter(
                    NewsItem.timestamp >= one_hour_ago,
                ).count()
                lines.append(f"**新闻**: 过去1小时 {news_count} 条")

                content = "\n".join(lines)
                title = f"市场概览 | {format_beijing_time(now, '%H:%M')} 北京时间"
                self._dispatch(rule, title, content)

            except Exception as e:
                logger.error(f"生成每小时摘要失败: {e}")
            finally:
                session.close()
