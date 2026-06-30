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
