"""Tests for NewsScorer — DeepSeek-based news importance scoring."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock
from scanners.scorer import NewsScorer
from scanners.base import NewsRecord


def _make_record(title: str, content: str = "") -> NewsRecord:
    return NewsRecord(source="test", source_id="1", title=title, content=content)


def test_scorer_disabled_without_api_key():
    """No API key → scorer is disabled, returns None for all."""
    with patch.dict(os.environ, {}, clear=True):
        scorer = NewsScorer(api_key="")
    assert scorer.enabled is False
    records = [_make_record("Fed raises rates")]
    result = scorer.score_batch(records)
    assert result == [None]


def test_scorer_returns_scores_from_api():
    """Valid API response → returns list of ints."""
    scorer = NewsScorer(api_key="fake-key")

    with patch.object(scorer, '_call_api', return_value='[8, 3, 6]'):
        records = [
            _make_record("Fed cuts rates by 50bps"),
            _make_record("Crypto exchange lists new token"),
            _make_record("Bitcoin price analysis"),
        ]
        result = scorer.score_batch(records)

    assert result == [8, 3, 6]


def test_scorer_clamps_scores_to_1_10():
    """Scores outside 1-10 are clamped."""
    scorer = NewsScorer(api_key="fake-key")
    with patch.object(scorer, '_call_api', return_value='[0, 11, 5]'):
        result = scorer.score_batch([_make_record("a"), _make_record("b"), _make_record("c")])
    assert result == [1, 10, 5]


def test_scorer_returns_none_on_api_error():
    """API error → all None, no exception raised."""
    scorer = NewsScorer(api_key="fake-key")
    with patch.object(scorer, '_call_api', side_effect=Exception("timeout")):
        result = scorer.score_batch([_make_record("test")])
    assert result == [None]


def test_scorer_batches_large_input():
    """Input >20 items is split into multiple batches; result length equals input length."""
    scorer = NewsScorer(api_key="fake-key")
    records = [_make_record(f"news {i}") for i in range(25)]

    with patch.object(scorer, '_score_single_batch', side_effect=lambda b: [5] * len(b)):
        result = scorer.score_batch(records)

    # 25 inputs → 2 batches (20+5) → 25 scores
    assert len(result) == 25
    assert all(s == 5 for s in result)


def test_loads_json_accepts_code_fence():
    payload = NewsScorer._loads_json('```json\n{"items":[{"idx":0,"importance":8}]}\n```', "score")
    assert payload["items"][0]["importance"] == 8


def test_loads_json_rejects_empty_content():
    try:
        NewsScorer._loads_json("", "score")
    except ValueError as e:
        assert "返回空内容" in str(e)
    else:
        raise AssertionError("empty response should fail")


def test_enrich_batch_uses_structured_scores():
    scorer = NewsScorer(api_key="fake-key", model="deepseek-v4-flash")
    records = [_make_record("Fed cuts rates")]

    with patch.object(
        scorer,
        "_score_single_batch_structured",
        return_value=[{"importance": 9, "reason": "意外降息影响风险资产"}],
    ):
        result = scorer.enrich_batch(records)

    assert result[0].llm_importance == 9
    assert result[0].llm_importance_reason == "意外降息影响风险资产"
    assert result[0].llm_model == "deepseek-v4-flash"
