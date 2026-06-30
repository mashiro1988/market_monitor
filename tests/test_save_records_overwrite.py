import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database import Base
import models  # noqa: F401
from models.price import PriceSnapshot
from scanners.base import PriceRecord
from scanners.price_scanner import PriceScanner
import scanners.price_scanner as ps

T = datetime(2026, 6, 27, 12, 0)

@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    monkeypatch.setattr(ps, "get_session", lambda: s)      # _save_records 用注入会话
    try:
        yield s
    finally:
        try: s.close()
        except Exception: pass

def _scanner():
    return PriceScanner.__new__(PriceScanner)

def test_real_overwrites_existing_gapfill_row(session):
    session.add(PriceSnapshot(timestamp=T, asset_class="futures", symbol="NQ=F",
                              name="NQ=F", price=22124.0, source="okx_gapfill"))
    session.commit()
    rec = PriceRecord(asset_class="futures", symbol="NQ=F", name="NQ=F",
                      price=22050.0, source="yfinance", timestamp=T)
    _scanner()._save_records([rec], T)
    rows = session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=T).all()
    assert len(rows) == 1                       # 原地更新，非新增
    assert rows[0].source == "yfinance" and rows[0].price == 22050.0

def test_gapfill_incoming_does_not_overwrite_real(session):
    session.add(PriceSnapshot(timestamp=T, asset_class="futures", symbol="NQ=F",
                              name="NQ=F", price=22050.0, source="yfinance"))
    session.commit()
    rec = PriceRecord(asset_class="futures", symbol="NQ=F", name="NQ=F",
                      price=22124.0, source="okx_gapfill", timestamp=T)
    _scanner()._save_records([rec], T)
    row = session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=T).one()
    assert row.source == "yfinance" and row.price == 22050.0   # 真实不被合成覆盖

def test_real_does_not_overwrite_existing_real(session):
    session.add(PriceSnapshot(timestamp=T, asset_class="futures", symbol="NQ=F",
                              name="NQ=F", price=22050.0, source="yfinance"))
    session.commit()
    rec = PriceRecord(asset_class="futures", symbol="NQ=F", name="NQ=F",
                      price=99999.0, source="yfinance", timestamp=T)
    _scanner()._save_records([rec], T)
    row = session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=T).one()
    assert row.price == 22050.0                 # 既有真实不被覆盖（保持原 dedup 行为）

def test_next_real_bar_chains_prev_off_real_after_overwrite(session):
    session.add(PriceSnapshot(timestamp=T, asset_class="futures", symbol="NQ=F",
                              name="NQ=F", price=22124.0, source="okx_gapfill"))
    session.commit()
    recs = [
        PriceRecord(asset_class="futures", symbol="NQ=F", name="NQ=F", price=22050.0,
                    source="yfinance", timestamp=T),                          # 覆盖合成
        PriceRecord(asset_class="futures", symbol="NQ=F", name="NQ=F", price=22100.0,
                    source="yfinance", timestamp=T + timedelta(minutes=5)),   # 新真实
    ]
    _scanner()._save_records(recs, T)
    nxt = session.query(PriceSnapshot).filter_by(symbol="NQ=F", timestamp=T + timedelta(minutes=5)).one()
    assert nxt.prev_price == 22050.0            # 链算基于真实价，非被覆盖的合成 22124
