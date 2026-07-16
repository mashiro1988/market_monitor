from __future__ import annotations

from datetime import datetime

from alerts._session import get_alert_session
from alerts.rules import AlertRule, AlertRuleType
from chart_utils import format_beijing_time
from models.sector import SectorReturn


PERIOD_FIELDS = {
    "1h": ("ret_1h", "ret_1h_median"),
    "24h": ("ret_24h", "ret_24h_median"),
    "168h": ("ret_168h", "ret_168h_median"),
    "7d": ("ret_168h", "ret_168h_median"),
    "720h": ("ret_720h", "ret_720h_median"),
    "30d": ("ret_720h", "ret_720h_median"),
}


class SectorAlertMixin:
    def evaluate_sectors(self):
        """Evaluate latest persisted sector returns for threshold breaks."""
        for rule in self.rules:
            if not rule.enabled or rule.rule_type != AlertRuleType.SECTOR_SPIKE:
                continue
            self._evaluate_sector_rule(rule)

    def _evaluate_sector_rule(self, rule: AlertRule) -> None:
        period = str(rule.params.get("period", "24h")).strip().lower()
        fields = PERIOD_FIELDS.get(period)
        if not fields:
            return
        mean_field, median_field = fields

        threshold = _to_float(rule.params.get("threshold_pct"), default=5.0)
        if threshold is None or threshold <= 0:
            return
        direction = str(rule.params.get("direction", "both")).strip().lower()
        metric = str(rule.params.get("metric", "median")).strip().lower()
        min_token_count = max(1, int(rule.params.get("min_token_count", 10) or 10))
        top_n = max(1, int(rule.params.get("top_n", 8) or 8))

        session = get_alert_session()
        try:
            latest = session.query(SectorReturn.snapshot_at).order_by(SectorReturn.snapshot_at.desc()).first()
            if not latest:
                return
            snapshot_at = latest[0]
            rows = (
                session.query(SectorReturn)
                .filter(SectorReturn.snapshot_at == snapshot_at)
                .all()
            )
        finally:
            session.close()

        triggered = []
        for row in rows:
            if row.token_count < min_token_count:
                continue
            mean_change = getattr(row, mean_field, None)
            median_change = getattr(row, median_field, None)
            trigger_change = _trigger_change(mean_change, median_change, metric, threshold, direction)
            if trigger_change is None:
                continue
            marker = _sector_marker(period, row.category, snapshot_at)
            if self._sector_marker_delivered(rule, marker):
                continue
            triggered.append((row, mean_change, median_change, float(trigger_change), marker))

        if not triggered:
            return

        triggered.sort(key=lambda item: abs(item[3]), reverse=True)
        triggered = triggered[:top_n]
        title = f"板块异动 | {len(triggered)} 个板块 {period} 超阈值"
        content = "\n".join(
            self._format_sector_line(row, mean_change, median_change, trigger_change, period, snapshot_at)
            for row, mean_change, median_change, trigger_change, _ in triggered
        )
        marker_text = "\n".join(marker for _, _, _, _, marker in triggered)
        for channel_name in rule.channels:
            channel = self.channels.get(channel_name)
            if not channel:
                continue
            delivered = channel.send(title, content)
            self._log_alert(rule.name, f"{marker_text}\n{title}\n{content}", channel_name, delivered)

    def _sector_marker_delivered(self, rule: AlertRule, marker: str) -> bool:
        delivered = self._delivered_channels_since(
            rule.name,
            datetime.min,
            channels=rule.channels,
            exact_marker=marker,
        )
        required = set(rule.channels or [])
        return required.issubset(delivered) if required else bool(delivered)

    @staticmethod
    def _format_sector_line(
        row: SectorReturn,
        mean_change: float | None,
        median_change: float | None,
        trigger_change: float,
        period: str,
        snapshot_at: datetime,
    ) -> str:
        color = "warning" if trigger_change < 0 else "info"
        group = f" / {row.group_name}" if row.group_name else ""
        snapshot = format_beijing_time(snapshot_at, "%m-%d %H:%M 北京时间")
        return (
            f"> <font color=\"{color}\">{row.category}{group}: {period} 中位 {_fmt_pct(median_change)}"
            f" / 均值 {_fmt_pct(mean_change)}</font>\n"
            f"> snapshot: {snapshot}，tokens: {row.token_count}"
        )


def _sector_marker(period: str, category: str, snapshot_at: datetime) -> str:
    return f"sector:{period}:{category}:{snapshot_at.isoformat()}"


def _trigger_change(
    mean_change: float | None,
    median_change: float | None,
    metric: str,
    threshold: float,
    direction: str,
) -> float | None:
    candidates: list[float] = []
    if metric in {"mean", "avg", "average"}:
        candidates = [float(mean_change)] if mean_change is not None else []
    elif metric in {"either", "any"}:
        candidates = [
            float(v) for v in (median_change, mean_change)
            if v is not None
        ]
    elif metric == "both":
        if mean_change is None or median_change is None:
            return None
        mean_v = float(mean_change)
        median_v = float(median_change)
        if _direction_hit(mean_v, threshold, direction) and _direction_hit(median_v, threshold, direction):
            return median_v
        return None
    else:
        candidates = [float(median_change)] if median_change is not None else []

    hits = [value for value in candidates if _direction_hit(value, threshold, direction)]
    if not hits:
        return None
    return max(hits, key=abs)


def _direction_hit(change: float, threshold: float, direction: str) -> bool:
    if direction in {"up", "long", "positive", "above", "gain", "gains"}:
        return change >= threshold
    if direction in {"down", "short", "negative", "below", "drop", "loss"}:
        return change <= -threshold
    return abs(change) >= threshold


def _to_float(value, *, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"
