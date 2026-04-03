"""
告警引擎 - 评估规则、冷却去重、分发通知

支持两种模式：
1. 即时推送：超阈值变化立即推送，超阈值项用 <font color="warning"> 特别标注
2. 定时摘要：每小时汇总全品种概览推送
"""
from datetime import datetime, timedelta, timezone
from loguru import logger
from database import get_session
from models.alert_log import AlertLog
from models.price import PriceSnapshot
from models.news import NewsItem
from models.prediction import PredictionMarket
from alerts.rules import AlertRule
from alerts.channels.wechat_work import WeChatWorkChannel
from alerts.channels.console import ConsoleChannel
from scanners.base import PriceRecord, NewsRecord, PredictionRecord
import config


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
            if not rule.enabled or rule.rule_type not in ("price_change", "price_level"):
                continue
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes):
                continue

            if rule.rule_type == "price_change":
                threshold = rule.params.get("threshold_pct", 3.0)
                target_symbol = rule.params.get("symbol")

                triggered = []
                for r in price_records:
                    if target_symbol and r.symbol != target_symbol:
                        continue
                    if r.change_pct is not None and abs(r.change_pct) >= threshold:
                        triggered.append(r)

                if triggered:
                    lines = []
                    for r in triggered:
                        color = "warning" if r.change_pct < 0 else "info"
                        lines.append(
                            f"> <font color=\"{color}\">{r.name} ({r.symbol}): "
                            f"${r.price:,.2f} ({r.change_pct:+.2f}%)</font>"
                        )
                    content = "\n".join(lines)
                    title = f"价格异动 | {len(triggered)} 个品种超阈值 ({threshold}%)"
                    alerts_to_send.append((rule, title, content))

        for rule, title, content in alerts_to_send:
            self._dispatch(rule, title, content)

    def evaluate_news(self, news_records: list[NewsRecord]):
        """评估新闻相关的告警规则"""
        for rule in self.rules:
            if not rule.enabled or rule.rule_type != "news_importance":
                continue
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes):
                continue

            min_importance = rule.params.get("min_importance", 8)
            important_news = [n for n in news_records if (n.importance or 0) >= min_importance]

            if important_news:
                lines = []
                for n in important_news[:10]:  # 最多推送10条
                    lines.append(f"> **[{n.source}]** {n.title}")
                content = "\n".join(lines)
                title = f"重要新闻 | {len(important_news)} 条"
                self._dispatch(rule, title, content)

    def evaluate_predictions(self, prediction_records: list[PredictionRecord]):
        """评估预测市场相关的告警规则"""
        for rule in self.rules:
            if not rule.enabled or rule.rule_type != "prediction_shift":
                continue
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes):
                continue

            threshold = rule.params.get("threshold_pct", 5.0)

            # 需要从数据库查前一次概率来比较
            session = get_session()
            try:
                triggered = []
                for r in prediction_records:
                    prev = session.query(PredictionMarket).filter(
                        PredictionMarket.market_id == r.market_id,
                        PredictionMarket.outcome == r.outcome,
                    ).order_by(PredictionMarket.timestamp.desc()).first()

                    if prev and prev.probability is not None:
                        shift = abs(r.probability - prev.probability) * 100
                        if shift >= threshold:
                            triggered.append((r, prev.probability, shift))
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

    def send_hourly_summary(self):
        """发送每小时市场状态摘要"""
        for rule in self.rules:
            if not rule.enabled or rule.rule_type != "hourly_summary":
                continue
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes):
                continue

            session = get_session()
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                one_hour_ago = now - timedelta(hours=1)

                # 最新价格快照
                from sqlalchemy import func
                latest_prices = session.query(PriceSnapshot).filter(
                    PriceSnapshot.timestamp >= one_hour_ago,
                ).order_by(PriceSnapshot.timestamp.desc()).all()

                # 按 symbol 去重取最新
                seen = {}
                for p in latest_prices:
                    if p.symbol not in seen:
                        seen[p.symbol] = p

                if not seen:
                    continue

                # 按资产类别分组
                groups = {}
                for symbol, p in seen.items():
                    groups.setdefault(p.asset_class, []).append(p)

                lines = []
                class_names = {
                    "stock_index": "美股指数",
                    "futures": "美股期货",
                    "asian_index": "亚洲指数",
                    "bond": "债券利率",
                    "commodity": "商品",
                    "crypto": "加密货币",
                }
                for cls, items in groups.items():
                    cls_name = class_names.get(cls, cls)
                    lines.append(f"**{cls_name}**")
                    for p in sorted(items, key=lambda x: x.name):
                        if p.change_pct is not None:
                            color = "warning" if abs(p.change_pct) >= 2.0 else ("info" if p.change_pct >= 0 else "comment")
                            pct_str = f"<font color=\"{color}\">{p.change_pct:+.2f}%</font>"
                        else:
                            pct_str = "N/A"
                        lines.append(f"> {p.name}: {p.price:,.4g} ({pct_str})")
                    lines.append("")

                # 最近1小时新闻数
                news_count = session.query(NewsItem).filter(
                    NewsItem.timestamp >= one_hour_ago,
                ).count()
                lines.append(f"**新闻**: 过去1小时 {news_count} 条")

                content = "\n".join(lines)
                title = f"市场概览 | {now.strftime('%H:%M')} UTC"
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
