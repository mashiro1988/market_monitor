import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from database import Base
import models  # noqa: F401  注册模型

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        yield s
    finally:
        s.close()

def test_gapfill_anchor_table_created_and_upsertable(session):
    from models.gapfill_anchor import GapfillAnchor
    assert "gapfill_anchor" in inspect(session.get_bind()).get_table_names()
    session.add(GapfillAnchor(symbol="NQ=F", real_ts=datetime(2026,6,26,21,0),
                              real_close=22000.0, perp_price=706.0))
    session.commit()
    row = session.get(GapfillAnchor, "NQ=F")
    assert row.real_close == 22000.0 and row.perp_price == 706.0
    assert row.updated_at is not None   # Task 3 的锚点时效比较依赖此字段在 INSERT 后非 None

def test_fetch_instrument_bars_parses_closed_bars_ascending(monkeypatch):
    from scanners.sources.okx_source import OkxPriceSource, PerpBar
    src = OkxPriceSource.__new__(OkxPriceSource)          # 绕过 __init__（不建真 exchange）
    src.proxy = ""
    monkeypatch.setattr(src, "_make_exchange", lambda: object())
    # OKX candle: [ts(start,ms), o,h,l,c, vol, volCcy, volCcyQuote, confirm]；newest-first
    canned = {
        "QQQ-USDT-SWAP": [
            ["1782700200000","705","707","704","706","10","0","0","1"],   # 较新
            ["1782699900000","704","706","703","705","9","0","0","1"],    # 较旧
        ],
        "XAU-USDT-SWAP": [["1782700200000","4085","4090","4080","4088","1","0","0","1"]],
    }
    monkeypatch.setattr(src, "_fetch_candles",
                        lambda exchange, inst_id, limit=12: canned.get(inst_id, []))
    out = src.fetch_instrument_bars(["QQQ-USDT-SWAP", "XAU-USDT-SWAP"])
    assert isinstance(out["QQQ-USDT-SWAP"][0], PerpBar)
    closes = [b.close for b in out["QQQ-USDT-SWAP"]]
    assert closes == [705.0, 706.0]                       # 升序（旧→新）
    assert out["XAU-USDT-SWAP"][-1].close == 4088.0
    from datetime import datetime, timezone
    expected = datetime.fromtimestamp(1782700200000/1000 + 300, timezone.utc).replace(tzinfo=None)
    assert out["XAU-USDT-SWAP"][-1].bar_end == expected   # 镜像生产 _closed_candle_points 的口径
