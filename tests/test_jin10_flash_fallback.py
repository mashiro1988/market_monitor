# -*- coding: utf-8 -*-
"""jin10 flash-api 被封（海外 IP 403）时的降级链：

1. flash.jin10.com 的 SSR 页面带完整快讯（含 API 同款 20 位 id），可直接解析兜底；
2. API 非 200 / 返回体异常不再静默当 0 条——兜底也失败时必须上抛，
   让 news_scanner 把源记为 error（2026-07-10 事故：403 静默 18 小时无告警）。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime

import pytest

from scanners.sources import jin10_source as j10
from scanners.sources.jin10_source import Jin10Source


# 与线上 SSR 页同构的最小 fixture：
# item1 有标题（right-common-title）+ 正文；item2 无标题、is-important、正文带内嵌标签和实体；
# item3 是 VIP 锁定条目（无 flash-text 正文）→ 应跳过。
FLASH_HTML = """
<div id="flash20260710132213682800" class="jin-flash-item-container is-normal">
  <div class="item-time has-title">13:22:13</div>
  <div class="right-common"><b class="right-common-title">野村：维持长飞光纤目标价266港元及&ldquo;买入&rdquo;</b></div>
  <div class="right-content"><div class="collapse-container is-normal"><div class="collapse-content">
    <div class="flash-text">金十数据7月10日讯，野村发报告指，维持长飞光纤光缆(06869.HK)目标价266港元。</div>
  </div></div></div>
</div>
<div id="flash20260710132134276800" class="jin-flash-item-container is-important">
  <div class="item-time">13:21:34</div>
  <div class="right-content"><div class="collapse-container is-normal"><div class="collapse-content">
    <div class="flash-text">阿布扎比国家石油公司订购了<b>四艘</b>新液化天然气运输船，总价值约为9亿美元&amp;后续扩张。</div>
  </div></div></div>
</div>
<div id="flash20260710132100111800" class="jin-flash-item-container is-normal">
  <div class="item-time">13:21:00</div>
  <div class="right-content"><div class="collapse-container is-locked"><span class="vip-desc">VIP专享快讯</span></div></div>
</div>
"""


class _Resp:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        # 模拟 requests 真实行为：flash 页响应头不带 charset 时 .text 按 Latin-1 解码
        # （2026-07-10 事故二：兜底上线后中文全变 mojibake 入库），.content 才是原始字节。
        self.content = text.encode("utf-8")
        self.text = self.content.decode("latin-1")
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _fake_get(routes):
    """routes: {url_substring: _Resp | Exception}；记录调用过的 url。"""
    calls = []

    def get(url, *args, **kwargs):
        calls.append(url)
        for key, resp in routes.items():
            if key in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected url: {url}")

    return get, calls


# ---------- 解析器 ----------

def test_parse_flash_html_extracts_items():
    records = Jin10Source._parse_flash_html(FLASH_HTML)
    assert len(records) == 2                                   # VIP 锁定条目跳过

    r1, r2 = records
    assert r1.source == "jin10"
    assert r1.source_id == "20260710132213682800"              # API 同款 id → 去重兼容
    # id 前 14 位是北京时间 → 存储为 UTC naive（-8h）
    assert r1.published_at == datetime(2026, 7, 10, 5, 22, 13)
    assert r1.title == "野村：维持长飞光纤目标价266港元及“买入”"    # 实体反转义
    assert r1.content.startswith("金十数据7月10日讯，野村发报告指")
    assert r1.importance == 0

    assert r2.source_id == "20260710132134276800"
    assert r2.importance == 1                                  # is-important
    assert "四艘" in r2.content and "<b>" not in r2.content     # 内嵌标签剥掉
    assert "&amp;" not in r2.content and "&后续扩张" in r2.content
    assert r2.title == r2.content[:100]                        # 无标题 → 正文前 100 字，与 API 路径同规则


# ---------- fetch 降级链 ----------

def test_fetch_falls_back_to_flash_page_on_403(monkeypatch):
    src = Jin10Source()
    get, calls = _fake_get({
        "flash-api.jin10.com": _Resp(403, '{"status":403,"message":"Access denied"}',
                                     {"status": 403, "message": "Access denied"}),
        "flash.jin10.com": _Resp(200, FLASH_HTML),
    })
    monkeypatch.setattr(j10.requests, "get", get)
    records = src.fetch()
    assert [r.source_id for r in records] == ["20260710132213682800", "20260710132134276800"]
    assert any("flash.jin10.com" in u for u in calls)
    # 解码必须走 content+UTF-8，不能信 .text 的默认 Latin-1（否则中文变 å¾·å½ 式乱码）
    assert records[0].title.startswith("野村")
    assert "维持长飞光纤" in records[0].title


def test_fetch_api_ok_does_not_touch_flash_page(monkeypatch):
    src = Jin10Source()
    payload = {"status": 200, "data": [{
        "id": 123, "time": "2026-07-10 13:00:00", "important": 1,
        "data": {"title": "T", "content": "C"},
    }]}
    get, calls = _fake_get({
        "flash-api.jin10.com": _Resp(200, json.dumps(payload), payload),
        "flash.jin10.com": _Resp(200, FLASH_HTML),
    })
    monkeypatch.setattr(j10.requests, "get", get)
    records = src.fetch()
    assert len(records) == 1 and records[0].source_id == "123"
    assert not any(u.startswith("https://flash.jin10.com") for u in calls)


def test_fetch_raises_when_api_and_fallback_both_fail(monkeypatch):
    """403 + 页面也挂 → 必须抛异常（news_scanner 记 source error），不得再静默 0 条。"""
    src = Jin10Source()
    get, _calls = _fake_get({
        "flash-api.jin10.com": _Resp(403, "denied", {"status": 403}),
        "flash.jin10.com": _Resp(500, "oops"),
    })
    monkeypatch.setattr(j10.requests, "get", get)
    with pytest.raises(Exception):
        src.fetch()


def test_fetch_api_empty_data_is_not_error(monkeypatch):
    """200 + status 200 + 空 data = 安静的 5 分钟，返回 0 条、不降级、不抛。"""
    src = Jin10Source()
    payload = {"status": 200, "data": []}
    get, calls = _fake_get({
        "flash-api.jin10.com": _Resp(200, json.dumps(payload), payload),
    })
    monkeypatch.setattr(j10.requests, "get", get)
    assert src.fetch() == []
    assert len(calls) == 1


# ---------- 回补降级 ----------

def test_fetch_backfill_falls_back_single_page(monkeypatch):
    """API 挂时回补退化为单页快照：只回窗口内条目，不翻页。"""
    src = Jin10Source()
    get, _calls = _fake_get({
        "flash-api.jin10.com": _Resp(403, "denied", {"status": 403}),
        "flash.jin10.com": _Resp(200, FLASH_HTML),
    })
    monkeypatch.setattr(j10.requests, "get", get)
    records = src.fetch_backfill(
        start_time=datetime(2026, 7, 10, 5, 22, 0),   # UTC：只含 13:22:13 那条
        end_time=datetime(2026, 7, 10, 5, 30, 0),
    )
    assert [r.source_id for r in records] == ["20260710132213682800"]
