"""Transform Gamma market payloads into internal prediction records."""

import json

from loguru import logger

from scanners.base import PredictionRecord


def _json_list(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return value


def parse_market(market: dict) -> list[PredictionRecord]:
    """Parse one Gamma market into one record per outcome."""
    records = []
    try:
        condition_id = market.get("conditionId", market.get("id", ""))
        question = market.get("question", "")

        if not condition_id or not question:
            return records

        outcome_prices = _json_list(market.get("outcomePrices", ""))
        outcomes = _json_list(market.get("outcomes", ""))
        volume = float(market.get("volume", 0) or 0)

        for i, outcome in enumerate(outcomes):
            prob = float(outcome_prices[i]) if i < len(outcome_prices) else 0.0
            records.append(PredictionRecord(
                market_id=str(condition_id),
                question=question[:500],
                outcome=str(outcome),
                probability=prob,
                volume=volume,
            ))
    except Exception as e:
        logger.error(f"解析 Polymarket 市场数据失败: {e}")

    return records
