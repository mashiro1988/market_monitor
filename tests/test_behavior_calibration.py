# -*- coding: utf-8 -*-
"""校准核心 sanity（price-behavior-engine-plan Task 9）：合成数据上四件套能出数、报告可渲染。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models.price import PriceSnapshot
from services import behavior_calibration as cal

T0 = datetime(2026, 7, 1, 0, 0)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    # 两天数据：BTC 带一个 0.5 档脉冲 + 日内小波动；NQ 缩放跟随
    btc, nq = [], []
    for i in range(2 * 288):
        wob = 0.05 * ((i % 7) - 3) / 3
        btc.append(100.0 + wob)
        nq.append(100.0 + wob * 0.5)
    for j, k in ((300, 0.2), (301, 0.4), (302, 0.6)):
        btc[j] += k
        nq[j] += k * 0.4
    for i, (b, q) in enumerate(zip(btc, nq)):
        ts = T0 + timedelta(minutes=5 * i)
        s.add(PriceSnapshot(timestamp=ts, asset_class="crypto", symbol="BTC/USDT",
                            name="BTC", price=b, source="test"))
        s.add(PriceSnapshot(timestamp=ts, asset_class="futures", symbol="NQ=F",
                            name="纳指", price=q, source="test"))
    s.commit()
    yield s
    s.close()


NOW = T0 + timedelta(days=2, hours=1)


def test_anchor_table_shapes(session):
    rows = cal.anchor_table(session, "BTC/USDT", days=3, now=NOW)
    nq = next(r for r in rows if r["symbol"] == "NQ=F")
    assert nq["n_bars"] > 400
    assert nq["rarity"][0] < nq["rarity"][1] < nq["rarity"][2]     # 反解三档单调
    assert nq["volratio"][0] > 0
    assert isinstance(nq["divergence_pct"], float)
    # 无数据参照给占位行不炸
    assert any(r.get("n_bars") == 0 for r in rows)


def test_null_lift_and_sensitivity(session):
    nl = cal.null_lift_table(session, "BTC/USDT", days=3, now=NOW)
    nq = next(r for r in nl if r["symbol"] == "NQ=F")
    assert 0 <= nq["real"] <= 1 and 0 <= nq["null"] <= 1 and nq["n"] >= 1
    sens = cal.sensitivity_table(session, "NQ=F", "BTC/USDT", days=3, now=NOW)
    assert [r["mult"] for r in sens] == [0.5, 0.75, 1.0, 1.5, 2.0]
    assert sens[2]["flip_pct"] is None                              # ×1.0 是基准


def test_report_renders(session):
    anchor = cal.anchor_table(session, "BTC/USDT", days=3, now=NOW)
    nl = cal.null_lift_table(session, "BTC/USDT", days=3, now=NOW)
    sens = {"NQ=F": cal.sensitivity_table(session, "NQ=F", "BTC/USDT", days=3, now=NOW)}
    sb = cal.session_bias_table(session, "BTC/USDT", days=3, now=NOW)
    text = cal.render_report(anchor, nl, sens, sb, days=3, now=NOW)
    assert "双锚互证" in text and "错位对照" in text and "敏感性扫描" in text and "时段偏置" in text
    assert "NQ=F" in text
