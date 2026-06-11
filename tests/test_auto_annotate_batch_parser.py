"""批量自动标注响应解析器（v2：news_roles + market_reaction_type + confidence + 逐窗口 reasoning）。"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.annotation_service import _parse_auto_annotate_batch_response


VALID_IDS = {0: {1, 2, 3}, 1: {10, 11, 12}}


def _wrap(items: list[dict]) -> str:
    return json.dumps({"items": items})


def test_parses_per_item_v2_labels_and_reasoning():
    raw = _wrap([
        {"window_id": 0, "news_roles": {"1": "primary_driver", "2": "secondary_driver"},
         "market_reaction_type": "risk_sentiment", "confidence": 0.8,
         "summary": "win0 因果", "reasoning": "win0 详细推理"},
        {"window_id": 1, "news_roles": {}, "market_reaction_type": "no_clear_driver",
         "confidence": 0.3, "summary": "win1 无明显新闻", "reasoning": "win1 详细推理"},
    ])
    result = _parse_auto_annotate_batch_response(raw, VALID_IDS)
    p0, r0 = result[0]
    assert p0.news_roles == {1: "primary_driver", 2: "secondary_driver"}
    assert p0.selected_news_ids == [1, 2]      # 派生：primary 在前
    assert p0.no_clear_news is False
    assert p0.market_reaction_type == "risk_sentiment"
    assert r0 == "win0 详细推理"
    p1, r1 = result[1]
    assert p1.no_clear_news is True
    assert p1.selected_news_ids == []
    assert r1 == "win1 详细推理"


def test_missing_reasoning_field_defaults_empty():
    raw = _wrap([
        {"window_id": 0, "news_roles": {"1": "primary_driver"},
         "market_reaction_type": "risk_sentiment", "confidence": 0.7, "summary": "s"},
    ])
    parsed, reasoning = _parse_auto_annotate_batch_response(raw, VALID_IDS)[0]
    assert parsed.selected_news_ids == [1]
    assert reasoning == ""


def test_filters_hallucinated_ids_and_bad_roles():
    raw = _wrap([
        {"window_id": 0,
         "news_roles": {"1": "primary_driver", "999": "primary_driver", "2": "bogus_role"},
         "market_reaction_type": "bogus_type", "confidence": 2.5,
         "summary": "s", "reasoning": "r"},
    ])
    parsed, _ = _parse_auto_annotate_batch_response(raw, VALID_IDS)[0]
    assert parsed.news_roles == {1: "primary_driver"}   # 999 幻觉、2 非法角色被丢
    assert parsed.market_reaction_type is None          # 非法类型被丢
    assert parsed.confidence == 1.0                     # clamp 到 [0,1]


def test_skips_unknown_window_id():
    raw = _wrap([
        {"window_id": 99, "news_roles": {"1": "primary_driver"},
         "market_reaction_type": "risk_sentiment", "summary": "s", "reasoning": "r"},
    ])
    assert _parse_auto_annotate_batch_response(raw, VALID_IDS) == {}


def test_extracts_json_from_markdown_fence():
    raw = "```json\n" + _wrap([
        {"window_id": 0, "news_roles": {"1": "primary_driver"},
         "market_reaction_type": "risk_sentiment", "summary": "s", "reasoning": "r"},
    ]) + "\n```"
    _, reasoning = _parse_auto_annotate_batch_response(raw, VALID_IDS)[0]
    assert reasoning == "r"


def test_summary_truncated_to_240():
    raw = _wrap([
        {"window_id": 0, "news_roles": {"1": "primary_driver"},
         "market_reaction_type": "risk_sentiment", "summary": "a" * 300, "reasoning": "r"},
    ])
    parsed, _ = _parse_auto_annotate_batch_response(raw, VALID_IDS)[0]
    assert len(parsed.summary) == 240


def test_non_dict_top_level_raises():
    with pytest.raises(ValueError, match="顶层"):
        _parse_auto_annotate_batch_response("[]", VALID_IDS)


def test_missing_items_raises():
    with pytest.raises(ValueError, match="items"):
        _parse_auto_annotate_batch_response("{}", VALID_IDS)
