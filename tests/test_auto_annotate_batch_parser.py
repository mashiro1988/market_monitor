"""Tests for batch auto-annotate response parser, especially per-item reasoning."""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services.annotation_service import _parse_auto_annotate_batch_response


VALID_IDS = {0: {1, 2, 3}, 1: {10, 11, 12}}


def _wrap(items: list[dict]) -> str:
    return json.dumps({"items": items})


def test_parses_per_item_reasoning():
    raw = _wrap([
        {"window_id": 0, "selected_news_ids": [1, 2], "no_clear_news": False,
         "summary": "win0 因果", "reasoning": "win0 详细推理"},
        {"window_id": 1, "selected_news_ids": [], "no_clear_news": True,
         "summary": "win1 无明显新闻", "reasoning": "win1 详细推理"},
    ])
    result = _parse_auto_annotate_batch_response(raw, VALID_IDS)
    assert result[0] == ([1, 2], False, "win0 因果", "win0 详细推理")
    assert result[1] == ([], True, "win1 无明显新闻", "win1 详细推理")


def test_missing_reasoning_field_defaults_empty():
    raw = _wrap([
        {"window_id": 0, "selected_news_ids": [1], "no_clear_news": False, "summary": "s"},
    ])
    result = _parse_auto_annotate_batch_response(raw, VALID_IDS)
    assert result[0] == ([1], False, "s", "")


def test_filters_hallucinated_ids():
    raw = _wrap([
        {"window_id": 0, "selected_news_ids": [1, 999, 2], "no_clear_news": False,
         "summary": "s", "reasoning": "r"},
    ])
    result = _parse_auto_annotate_batch_response(raw, VALID_IDS)
    assert result[0][0] == [1, 2]


def test_skips_unknown_window_id():
    raw = _wrap([
        {"window_id": 99, "selected_news_ids": [1], "no_clear_news": False,
         "summary": "s", "reasoning": "r"},
    ])
    result = _parse_auto_annotate_batch_response(raw, VALID_IDS)
    assert result == {}


def test_extracts_json_from_markdown_fence():
    raw = "```json\n" + _wrap([
        {"window_id": 0, "selected_news_ids": [1], "no_clear_news": False,
         "summary": "s", "reasoning": "r"},
    ]) + "\n```"
    result = _parse_auto_annotate_batch_response(raw, VALID_IDS)
    assert result[0][3] == "r"


def test_summary_truncated_to_240():
    long = "a" * 300
    raw = _wrap([
        {"window_id": 0, "selected_news_ids": [1], "no_clear_news": False,
         "summary": long, "reasoning": "r"},
    ])
    result = _parse_auto_annotate_batch_response(raw, VALID_IDS)
    assert len(result[0][2]) == 240


def test_non_dict_top_level_raises():
    with pytest.raises(ValueError, match="顶层"):
        _parse_auto_annotate_batch_response("[]", VALID_IDS)


def test_missing_items_raises():
    with pytest.raises(ValueError, match="items"):
        _parse_auto_annotate_batch_response("{}", VALID_IDS)
