"""
告警引擎 - 评估规则、冷却去重、分发通知

支持两种模式：
1. 即时推送：超阈值变化立即推送，超阈值项用 <font color="warning"> 特别标注
2. 定时摘要：每小时汇总全品种概览推送
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from loguru import logger
from database import get_session
from models.alert_log import AlertLog
from models.price import PriceSnapshot
from models.news import NewsItem
from models.prediction import PredictionMarket
from alerts.rules import AlertRule, AlertRuleType
from alerts.channels.wechat_work import WeChatWorkChannel
from alerts.channels.console import ConsoleChannel
from scanners.base import PriceRecord, NewsRecord, PredictionRecord
from chart_utils import format_beijing_time
import config


@dataclass(frozen=True)
class PriceWindowMove:
    """Price movement measured over an alert rule window."""
    change_pct: float
    start_time: datetime
    end_time: datetime
    start_price: float
    end_price: float
    low_price: float
    high_price: float


@dataclass(frozen=True)
class PriceThresholdSummary:
    """Aggregated price threshold hits for hourly summary."""
    symbol: str
    name: str
    asset_class: str
    threshold_pct: float
    window_minutes: int
    trigger_count: int
    strongest_move: PriceWindowMove


class AlertEngine:
    """告警引擎"""

    def __init__(self):
        self.channels = {
            "wechat_work": WeChatWorkChannel(),
            "console": ConsoleChannel(),
        }
        self.rules: list[AlertRule] = []
        self._load_rules()

    def _load_rules(self):
        """从配置加载告警规则"""
        for rule_cfg in config.ALERT_RULES:
            self.rules.append(AlertRule(
                name=rule_cfg["name"],
                rule_type=rule_cfg["rule_type"],
                params=rule_cfg.get("params", {}),
                channels=rule_cfg.get("channels", ["wechat_work"]),
                cooldown_minutes=rule_cfg.get("cooldown_minutes", 30),
                enabled=rule_cfg.get("enabled", True),
            ))

    def _is_in_cooldown(self, rule_name: str, cooldown_minutes: int) -> bool:
        """检查规则是否在冷却期内"""
        session = get_session()
        try:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=cooldown_minutes)
            recent = session.query(AlertLog).filter(
                AlertLog.rule_name == rule_name,
                AlertLog.timestamp >= cutoff,
                AlertLog.delivered == True,
            ).first()
            return recent is not None
        finally:
            session.close()

    def _log_alert(self, rule_name: str, message: str, channel: str, delivered: bool):
        """记录告警发送日志"""
        session = get_session()
        try:
            log = AlertLog(
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                rule_name=rule_name,
                message=message[:2000],
                channel=channel,
                delivered=delivered,
            )
            session.add(log)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"记录告警日志失败: {e}")
        finally:
            session.close()

    def _dispatch(self, rule: AlertRule, title: str, content: str):
        """分发告警到各通道"""
        for channel_name in rule.channels:
            channel = self.channels.get(channel_name)
            if not channel:
                logger.warning(f"未知告警通道: {channel_name}")
                continue
            delivered = channel.send(title, content)
            self._log_alert(rule.name, f"{title}\n{content}", channel_name, delivered)

    def evaluate_prices(self, price_records: list[PriceRecord]):
        """评估价格相关的告警规则"""
        alerts_to_send = []

        for rule in self.rules:
            if not rule.enabled or rule.rule_type not in (AlertRuleType.PRICE_CHANGE, AlertRuleType.PRICE_LEVEL):
                continue
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes):
                continue

            if rule.rule_type == AlertRuleType.PRICE_CHANGE:
                threshold = rule.params.get("threshold_pct", 3.0)
                target_symbol = rule.params.get("symbol")
                window_minutes = int(rule.params.get("window_minutes", 0) or 0)

                triggered = []
                for r in price_records:
                    if target_symbol and r.symbol != target_symbol:
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

        for rule, title, content in alerts_to_send:
            self._dispatch(rule, title, content)

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

        session = get_session()
        try:
            return AlertEngine._price_window_move_from_session(
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
        return AlertEngine._price_window_move_from_session(
            session=session,
            symbol=snapshot.symbol,
            current_ts=current_ts,
            current_price=snapshot.price,
            window_minutes=window_minutes,
        )

    @staticmethod
    def _price_window_change_pct(record: PriceRecord, window_minutes: int) -> float | None:
        """Compatibility helper for tests and older callers."""
        move = AlertEngine._price_window_move(record, window_minutes)
        return move.change_pct if move else None


    def _already_alerted(self, source: str, source_id: str) -> bool:
        """检查该新闻条目是否已推送过（匹配日志中任意位置的 marker）"""
        session = get_session()
        try:
            marker = f"news:{source}:{source_id}"
            exists = session.query(AlertLog).filter(
                AlertLog.rule_name == "important_news",
                AlertLog.message.like(f"%{marker}%"),
                AlertLog.delivered == True,
            ).first()
            return exists is not None
        finally:
            session.close()

    _WECHAT_MARKDOWN_LIMIT = 3500  # 企业微信 markdown 正文最大 4096 字节，预留裕度

    def evaluate_news(self, news_records: list[NewsRecord]):
        """评估新闻相关的告警规则 - 每条新闻只推送一次，按发布时间降序分页推送"""
        for rule in self.rules:
            if not rule.enabled or rule.rule_type != AlertRuleType.NEWS_IMPORTANCE:
                continue

            min_importance = rule.params.get("min_importance", 8)
            important_news = [
                n for n in news_records
                if self._is_important_news(n, min_importance)
            ]

            # 过滤掉已经推送过的条目
            new_items = [n for n in important_news if not self._already_alerted(n.source, n.source_id)]
            if not new_items:
                continue

            # 按发布时间降序（最新的在前）；无 published_at 的排最后
            new_items.sort(
                key=lambda n: n.published_at or datetime.min,
                reverse=True,
            )

            total = len(new_items)

            # 按 markdown 字节上限分片，避免企业微信截断
            pages: list[list[NewsRecord]] = []
            cur: list[NewsRecord] = []
            cur_bytes = 0
            for n in new_items:
                line = self._format_news_line(n)
                line_bytes = len(line.encode("utf-8")) + 1  # +1 for "\n"
                if cur and cur_bytes + line_bytes > self._WECHAT_MARKDOWN_LIMIT:
                    pages.append(cur)
                    cur = []
                    cur_bytes = 0
                cur.append(n)
                cur_bytes += line_bytes
            if cur:
                pages.append(cur)

            for page_idx, page in enumerate(pages, start=1):
                lines = [self._format_news_line(n) for n in page]
                content = "\n".join(lines)
                if len(pages) > 1:
                    title = f"重要新闻 | {total} 条 ({page_idx}/{len(pages)})"
                else:
                    title = f"重要新闻 | {total} 条"

                # 在日志里记录本页所有 marker，供后续去重
                markers = "\n".join(f"news:{n.source}:{n.source_id}" for n in page)
                for channel_name in rule.channels:
                    channel = self.channels.get(channel_name)
                    if not channel:
                        continue
                    delivered = channel.send(title, content)
                    self._log_alert(rule.name, f"{markers}\n{title}\n{content}", channel_name, delivered)

    @staticmethod
    def _is_important_news(n: NewsRecord, min_importance: int) -> bool:
        """新闻告警触发：LLM 高分，或 Jin10 源端 important 标志。"""
        if (n.llm_importance or 0) >= min_importance:
            return True
        return n.source == "jin10" and n.importance == 1

    @staticmethod
    def _format_news_line(n: NewsRecord) -> str:
        """生成单条新闻的 markdown 行"""
        time_str = format_beijing_time(n.published_at, "%m-%d %H:%M 北京时间") if n.published_at else ""
        time_tag = f" <font color=\"comment\">{time_str}</font>" if time_str else ""
        jin10_tag = ""
        if n.source == "jin10":
            is_important = n.importance == 1
            jin10_tag = f" <font color=\"comment\">[Jin10重要:{'是' if is_important else '否'}]</font>"
        if n.llm_importance is not None:
            score_text = f"LLM {n.llm_importance}分"
        elif n.source == "jin10":
            is_important = n.importance == 1
            score_text = f"Jin10重要:{'是' if is_important else '否'}"
        else:
            score_text = "未评分"
        return f"> **[{n.source}]** <font color=\"warning\">[{score_text}]</font>{jin10_tag}{time_tag} {n.title}"

    def evaluate_predictions(self, prediction_records: list[PredictionRecord]):
        """评估预测市场相关的告警规则"""
        for rule in self.rules:
            if not rule.enabled or rule.rule_type != AlertRuleType.PREDICTION_SHIFT:
                continue
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes):
                continue

            threshold = rule.params.get("threshold_pct", 5.0)

            session = get_session()
            try:
                triggered = []
                for r in prediction_records:
                    latest = session.query(PredictionMarket).filter(
                        PredictionMarket.market_id == r.market_id,
                        PredictionMarket.outcome == r.outcome,
                    ).order_by(PredictionMarket.timestamp.desc()).first()

                    prev_probability = None
                    if latest and latest.probability is not None:
                        # PredictionScanner writes the current row before alert evaluation.
                        # When latest is the just-saved row, use its prev_probability field.
                        if (
                            latest.prev_probability is not None
                            and abs(latest.probability - r.probability) < 1e-12
                        ):
                            prev_probability = latest.prev_probability
                        else:
                            prev_probability = latest.probability

                    if prev_probability is not None:
                        shift = abs(r.probability - prev_probability) * 100
                        if shift >= threshold:
                            triggered.append((r, prev_probability, shift))
            finally:
                session.close()

            if triggered:
                lines = []
                for r, prev_prob, shift in triggered[:10]:
                    direction = "↑" if r.probability > prev_prob else "↓"
                    lines.append(
                        f"> <font color=\"warning\">{r.question[:80]}</font>\n"
                        f">   {r.outcome}: {prev_prob:.1%} → {r.probability:.1%} "
                        f"({direction}{shift:.1f}%)"
                    )
                content = "\n".join(lines)
                title = f"预测市场异动 | {len(triggered)} 个市场概率显著变化"
                self._dispatch(rule, title, content)

    @staticmethod
    def _format_price_value(price: float, asset_class: str) -> str:
        if asset_class == "bond":
            return f"{price:.3f}%"
        if asset_class == "crypto":
            return f"${price:,.2f}"
        return f"{price:,.2f}"

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
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes):
                continue

            session = get_session()
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

                # 最近1小时新闻数
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

    def evaluate_all(
        self,
        price_records: list[PriceRecord] | None = None,
        news_records: list[NewsRecord] | None = None,
        prediction_records: list[PredictionRecord] | None = None,
    ):
        """统一评估所有告警规则"""
        if price_records:
            self.evaluate_prices(price_records)
        if news_records:
            self.evaluate_news(news_records)
        if prediction_records:
            self.evaluate_predictions(prediction_records)
