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
        condition_id = str(market.get("conditionId") or "").strip()
        question = market.get("question", "")

        if not condition_id or not question:
            if question:
                logger.warning("跳过 Polymarket 市场：缺少 conditionId question={}", question[:80])
            return records

        outcome_prices = _json_list(market.get("outcomePrices", ""))
        outcomes = _json_list(market.get("outcomes", ""))
        volume = float(market.get("volume", 0) or 0)
        if not isinstance(outcomes, list) or not isinstance(outcome_prices, list):
            return records
        if not outcomes or len(outcome_prices) != len(outcomes):
            logger.warning("跳过 Polymarket 市场：outcomes/outcomePrices 长度不匹配 question={}", question[:80])
            return records

        for i, outcome in enumerate(outcomes):
            prob = float(outcome_prices[i])
            if prob < 0 or prob > 1:
                logger.warning("跳过 Polymarket 市场：概率越界 question={} prob={}", question[:80], prob)
                return []
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
