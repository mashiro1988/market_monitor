"""自动标注喂给 reasoner 的 payload 必须带同期对标品种（纳指/原油/黄金）涨跌。

对标清单来自 config.ANNOTATION_REFERENCE_ASSETS；标注品种本身不对标自己；
无数据（休市）的品种给 null。两份 system prompt 必须向模型解释这个字段。
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.news import NewsItem
from models.price import PriceSnapshot
from schemas.annotations import AutoAnnotateRequest
from services import annotation_service

W_START = datetime(2026, 6, 9, 17, 0)
W_END = datetime(2026, 6, 9, 17, 30)


@pytest.fixture
def session(monkeypatch):
    # 与 config 真实清单同构：6 个对标，美债10Y 用 bp 口径（3 元组第三项）。
    monkeypatch.setattr(
        config, "ANNOTATION_REFERENCE_ASSETS",
        [
            ("NQ=F", "纳指"),
            ("CL=F", "原油"),
            ("GC=F", "黄金"),
            ("US_10Y", "美债10Y", "bp"),
            ("DX-Y.NYB", "美元指数"),
            ("BTC/USDT", "BTC"),
        ],
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _price(session, symbol, name, asset_class, ts, price):
    session.add(PriceSnapshot(
        timestamp=ts, asset_class=asset_class, symbol=symbol,
        name=name, price=price, source="test",
    ))


def _seed(session):
    # 标注品种 BTC：窗口两端快照（auto_annotate 用精确时间戳取价）
    _price(session, "BTC/USDT", "BTC", "crypto", W_START, 100000.0)
    _price(session, "BTC/USDT", "BTC", "crypto", W_END, 97000.0)
    # 对标：纳指 -1.00%，原油 +3.00%，黄金无数据
    _price(session, "NQ=F", "纳指", "futures", W_START - timedelta(minutes=60), 19900.0)
    _price(session, "NQ=F", "纳指", "futures", W_START, 20000.0)
    _price(session, "NQ=F", "纳指", "futures", W_END, 19800.0)
    _price(session, "NQ=F", "纳指", "futures", W_END + timedelta(minutes=60), 19950.0)
    _price(session, "CL=F", "原油", "futures", W_START, 70.0)
    _price(session, "CL=F", "原油", "futures", W_END, 72.1)
    # 美债10Y：4.30% → 4.40%，bp 口径应显示 +10.0bp（而不是 +2.33%）
    _price(session, "US_10Y", "美债10Y", "bond", W_START, 4.30)
    _price(session, "US_10Y", "美债10Y", "bond", W_END, 4.40)
    # 美元指数不喂数据 → null
    # 至少一条候选新闻，否则 auto_annotate 直接短路不调模型
    session.add(NewsItem(
        timestamp=W_START + timedelta(minutes=5), source="jin10",
        title="美伊在霍尔木兹海峡附近交火", content="地缘冲突升级", language="zh",
    ))
    session.commit()


def _request(symbol="BTC/USDT"):
    return AutoAnnotateRequest(
        symbol=symbol,
        window_start_utc=W_START.isoformat(),
        window_end_utc=W_END.isoformat(),
        threshold_pct=1.0,
    )


def _payload_json(user_content: str) -> dict:
    # user 消息第一行是中文摘要，其后是 JSON 正文
    return json.loads(user_content.split("\n", 1)[1])


def test_auto_annotate_payload_includes_reference_changes(session, monkeypatch):
    _seed(session)
    captured = {}

    def fake_call(user_content):
        captured["user"] = user_content
        return json.dumps({"selected_news_ids": [], "no_clear_news": True, "summary": "无明显因果新闻"}), "", 0.1

    monkeypatch.setattr(annotation_service, "_call_deepseek_reasoner", fake_call)
    annotation_service.auto_annotate(session, _request())

    payload = _payload_json(captured["user"])["window"]
    refs = payload["reference_changes"]
    segments = payload["reference_change_segments"]
    assert refs["纳指"] == "-1.00%"
    assert refs["原油"] == "+3.00%"
    assert refs["黄金"] is None
    assert refs["美债10Y"] == "+10.0bp"   # 收益率用 bp，不用百分比涨跌
    assert refs["美元指数"] is None
    assert "BTC" not in refs              # 标注品种本身（BTC/USDT）不对标自己
    assert segments["纳指"]["pre_1h"] == "+0.50%"
    assert segments["纳指"]["window"] == "-1.00%"
    assert segments["纳指"]["post_1h"] == "+0.76%"


def test_batch_payload_includes_reference_changes(session):
    _seed(session)
    user_content, _metas, _cands = annotation_service._build_auto_annotate_batch_user_payload(
        session, [_request()],
    )
    payload = _payload_json(user_content)["windows"][0]
    refs = payload["reference_changes"]
    segments = payload["reference_change_segments"]
    assert refs["纳指"] == "-1.00%"
    assert refs["原油"] == "+3.00%"
    assert refs["黄金"] is None
    assert refs["美债10Y"] == "+10.0bp"
    assert segments["纳指"]["pre_1h"] == "+0.50%"
    assert segments["纳指"]["window"] == "-1.00%"
    assert segments["纳指"]["post_1h"] == "+0.76%"


def test_reference_changes_exclude_annotated_symbol_itself(session):
    _seed(session)
    user_content, _metas, _cands = annotation_service._build_auto_annotate_batch_user_payload(
        session, [_request(symbol="NQ=F")],
    )
    refs = _payload_json(user_content)["windows"][0]["reference_changes"]
    assert "纳指" not in refs          # 自己不对标自己
    assert refs["原油"] == "+3.00%"
    assert refs["BTC"] == "-3.00%"     # 标注纳指时，BTC 作为加密对照出现


def test_reference_change_schema_carries_unit(session):
    """UI 用的 ReferenceChange 列表也要带 unit，前端按 bp/% 分别渲染。"""
    _seed(session)
    from services.annotation_service import _load_reference_rows, _reference_changes_for_window
    from datetime import timedelta as _td
    ref_rows = _load_reference_rows(session, W_START - _td(minutes=75))
    refs = _reference_changes_for_window(
        ref_rows, W_START, W_END, 10, "BTC/USDT", correlations_by_symbol={"NQ=F": 0.82}
    )
    by_label = {r.label: r for r in refs}
    assert by_label["美债10Y"].unit == "bp"
    assert by_label["美债10Y"].pct == pytest.approx(10.0)
    assert by_label["纳指"].unit == "pct"
    assert by_label["纳指"].pre_pct == pytest.approx(0.5025, abs=0.01)
    assert by_label["纳指"].pct == pytest.approx(-1.0)
    assert by_label["纳指"].post_pct == pytest.approx(0.7576, abs=0.01)
    assert by_label["纳指"].correlation == pytest.approx(0.82)


def test_prompts_document_reference_changes():
    for prompt in (
        annotation_service.AUTO_ANNOTATE_SYSTEM_PROMPT,
        annotation_service.AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT,
    ):
        assert "reference_changes" in prompt
        assert "reference_change_segments" in prompt
        assert "核心推理顺序" in prompt
        assert "同步相关" in prompt
        assert "不要做 lag" in prompt
        assert "相关性低" in prompt
        assert "日经" in prompt
        assert "相关资产新闻 + 其它资产验证 + 时间靠近触发段" in prompt
        assert "地缘" in prompt        # 跨资产风险事件解读指引
        # 全 null（对标品种集体休市，周末加密窗口的常态）必须有降级指引，
        # 否则模型可能拿"无矛盾"当"签名一致"，借地缘例外条款乱选。
        assert "全部为 null" in prompt
        # 标注品种本身不出现在 reference_changes 里（键缺失），必须向模型说明不是数据故障。
        assert "不会出现在 reference_changes" in prompt
        # 美债10Y 用 bp 口径 + 利率冲击 vs 避险的方向判别指引。
        assert "bp" in prompt
        assert "利率冲击" in prompt
        # 长窗口（多段合并）指引：触发新闻常在窗口中段，不得因"晚于窗口起点"排除。
        # 实弹回放（2026-06-09 #26 窗口）证实缺这条会让模型用起点对齐理由拒选。
        assert "晚于窗口起点" in prompt
        # 黄金是地缘签名的佐证而非必要条件（2026-06 美伊冲突实测金价未涨），
        # 不得以"黄金没涨"否定地缘归因。
        assert "不必然" in prompt
        # 对标不可用（CME 日休/周末，主力对标 null、仅剩低波动品种走平）时，
        # 跨资产签名不得作为排除依据——退回纯事件判断。
        # 实证：2026-06-10 05:15 BTC 窗口，美军打击伊朗首报在候选里，模型却以
        # "reference change 无明显变化"拒选。
        assert "不能用来排除" in prompt
        assert "纯事件判断" in prompt
        # （Phase3a：contradictory 角色退场，原"不是因果标签"断言随之移除——
        #  方向相反的消息现一律默认 noise，见 news-impact-engine-phase3a-plan）
