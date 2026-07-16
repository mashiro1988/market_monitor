from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config
from alerts._session import get_alert_session
from alerts.rules import AlertRule, AlertRuleType
from chart_utils import format_beijing_time
from models.alert_log import AlertLog
from scanners.base import NewsRecord


class NewsAlertMixin:
    _WECHAT_MARKDOWN_LIMIT = 3500  # 企业微信 markdown 正文最大 4096 字节，预留裕度

    def _already_alerted(
        self,
        rule_name: str,
        source: str,
        source_id: str,
        channels: list[str] | None = None,
    ) -> bool:
        """检查该新闻条目是否已在所有目标通道推送过。"""
        marker = f"news:{source}:{source_id}"
        cutoff = datetime.min
        delivered = self._delivered_channels_since(
            rule_name, cutoff, channels=channels, exact_marker=marker
        )
        required = set(channels or [])
        return required.issubset(delivered) if required else bool(delivered)

    @staticmethod
    def _parse_news_alert_message(message: str | None) -> tuple[list[str], str, str] | None:
        if not message:
            return None
        lines = message.splitlines()
        markers: list[str] = []
        idx = 0
        while idx < len(lines) and lines[idx].startswith("news:"):
            markers.append(lines[idx])
            idx += 1
        if not markers or idx >= len(lines):
            return None
        title = lines[idx]
        content = "\n".join(lines[idx + 1 :])
        if not title or not content:
            return None
        return markers, title, content

    def _news_markers_delivered(self, rule_name: str, markers: list[str], channel: str) -> bool:
        for marker in markers:
            delivered = self._delivered_channels_since(
                rule_name,
                datetime.min,
                channels=[channel],
                exact_marker=marker,
            )
            if channel not in delivered:
                return False
        return True

    def _retry_failed_news_alerts(self, rule: AlertRule) -> None:
        retry_hours = int(getattr(config, "NEWS_ALERT_RETRY_HOURS", 24))
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=max(1, retry_hours))
        session = get_alert_session()
        try:
            rows = (
                session.query(AlertLog)
                .filter(
                    AlertLog.rule_name == rule.name,
                    AlertLog.timestamp >= cutoff,
                    AlertLog.delivered == False,
                    AlertLog.message.like("news:%"),
                )
                .order_by(AlertLog.timestamp.desc())
                .limit(50)
                .all()
            )
        finally:
            session.close()

        seen: set[tuple[str, tuple[str, ...]]] = set()
        for row in rows:
            parsed = self._parse_news_alert_message(row.message)
            if parsed is None:
                continue
            markers, title, content = parsed
            key = (row.channel, tuple(markers))
            if key in seen:
                continue
            seen.add(key)
            if self._news_markers_delivered(rule.name, markers, row.channel):
                continue
            channel = self.channels.get(row.channel)
            if not channel:
                continue
            delivered = channel.send(title, content)
            marker_text = "\n".join(markers)
            self._log_alert(rule.name, f"{marker_text}\n{title}\n{content}", row.channel, delivered)

    def evaluate_news(self, news_records: list[NewsRecord]):
        """评估新闻相关的告警规则 - 每条新闻只推送一次，按发布时间降序分页推送"""
        for rule in self.rules:
            if not rule.enabled or rule.rule_type != AlertRuleType.NEWS_IMPORTANCE:
                continue
            self._retry_failed_news_alerts(rule)

            min_importance = rule.params.get("min_importance", 8)
            important_news = [
                n for n in news_records
                if self._is_important_news(n, min_importance)
            ]

            new_items = [
                n for n in important_news
                if not self._already_alerted(rule.name, n.source, n.source_id, rule.channels)
            ]
            if not new_items:
                continue

            new_items.sort(
                key=lambda n: n.published_at or datetime.min,
                reverse=True,
            )

            total = len(new_items)
            pages: list[list[NewsRecord]] = []
            cur: list[NewsRecord] = []
            cur_bytes = 0
            for n in new_items:
                line = self._format_news_line(n)
                line_bytes = len(line.encode("utf-8")) + 1
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
