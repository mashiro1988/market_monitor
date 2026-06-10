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
    monkeypatch.setattr(
        config, "ANNOTATION_REFERENCE_ASSETS",
        [("NQ=F", "纳指"), ("CL=F", "原油"), ("GC=F", "黄金")],
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
    _price(session, "NQ=F", "纳指", "futures", W_START, 20000.0)
    _price(session, "NQ=F", "纳指", "futures", W_END, 19800.0)
    _price(session, "CL=F", "原油", "futures", W_START, 70.0)
    _price(session, "CL=F", "原油", "futures", W_END, 72.1)
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

    refs = _payload_json(captured["user"])["window"]["reference_changes"]
    assert refs["纳指"] == "-1.00%"
    assert refs["原油"] == "+3.00%"
    assert refs["黄金"] is None


def test_batch_payload_includes_reference_changes(session):
    _seed(session)
    user_content, _metas, _cands = annotation_service._build_auto_annotate_batch_user_payload(
        session, [_request()],
    )
    refs = _payload_json(user_content)["windows"][0]["reference_changes"]
    assert refs["纳指"] == "-1.00%"
    assert refs["原油"] == "+3.00%"
    assert refs["黄金"] is None


def test_reference_changes_exclude_annotated_symbol_itself(session):
    _seed(session)
    user_content, _metas, _cands = annotation_service._build_auto_annotate_batch_user_payload(
        session, [_request(symbol="NQ=F")],
    )
    refs = _payload_json(user_content)["windows"][0]["reference_changes"]
    assert "纳指" not in refs          # 自己不对标自己
    assert refs["原油"] == "+3.00%"


def test_prompts_document_reference_changes():
    for prompt in (
        annotation_service.AUTO_ANNOTATE_SYSTEM_PROMPT,
        annotation_service.AUTO_ANNOTATE_BATCH_SYSTEM_PROMPT,
    ):
        assert "reference_changes" in prompt
        assert "地缘" in prompt        # 跨资产风险事件解读指引
        # 全 null（对标品种集体休市，周末加密窗口的常态）必须有降级指引，
        # 否则模型可能拿"无矛盾"当"签名一致"，借地缘例外条款乱选。
        assert "全部为 null" in prompt
        # 标注品种本身不出现在 reference_changes 里（键缺失），必须向模型说明不是数据故障。
        assert "不会出现在 reference_changes" in prompt
