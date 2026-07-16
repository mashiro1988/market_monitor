from __future__ import annotations

from datetime import timedelta

from alerts._session import get_alert_session
from alerts.rules import AlertRuleType
from models.prediction import PredictionMarket
from scanners.base import PredictionRecord


class PredictionAlertMixin:
    def evaluate_predictions(self, prediction_records: list[PredictionRecord]):
        """评估预测市场相关的告警规则"""
        for rule in self.rules:
            if not rule.enabled or rule.rule_type != AlertRuleType.PREDICTION_SHIFT:
                continue
            if self._is_in_cooldown(rule.name, rule.cooldown_minutes, rule.channels):
                continue

            threshold = rule.params.get("threshold_pct", 5.0)
            window_minutes = int(rule.params.get("window_minutes", 0) or 0)

            session = get_alert_session()
            try:
                triggered = []
                records_by_market: dict[str, list[PredictionRecord]] = {}
                for r in prediction_records:
                    records_by_market.setdefault(r.market_id, []).append(r)
                records_to_check: list[PredictionRecord] = []
                for records in records_by_market.values():
                    outcomes = {r.outcome.lower() for r in records}
                    if outcomes == {"yes", "no"}:
                        records_to_check.append(next((r for r in records if r.outcome.lower() == "yes"), records[0]))
                    else:
                        records_to_check.extend(records)

                for r in records_to_check:
                    latest = session.query(PredictionMarket).filter(
                        PredictionMarket.market_id == r.market_id,
                        PredictionMarket.outcome == r.outcome,
                    ).order_by(PredictionMarket.timestamp.desc()).first()

                    prev_probability = None
                    if latest and latest.probability is not None:
                        latest_ts = getattr(latest, "timestamp", None)
                        if window_minutes > 0 and latest_ts is not None:
                            target_ts = latest_ts - timedelta(minutes=window_minutes)
                            previous = session.query(PredictionMarket).filter(
                                PredictionMarket.market_id == r.market_id,
                                PredictionMarket.outcome == r.outcome,
                                PredictionMarket.timestamp <= target_ts,
                            ).order_by(PredictionMarket.timestamp.desc()).first()
                            prev_probability = previous.probability if previous else latest.prev_probability
                        elif (
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
