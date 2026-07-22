# -*- coding: utf-8 -*-
"""导入脚本核心：幂等落库、dry-run 不写、统计口径。"""
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models.price import PriceSnapshot
from scanners.base import PriceRecord
from scripts.price_dump import write_dump
from scripts.import_price_dump import run_import

START = datetime(2026, 7, 21, 20, 0)
END = datetime(2026, 7, 22, 12, 0)


@pytest.fixture()
def session_factory():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


def _dump(tmp_path: Path) -> Path:
    p = tmp_path / "dump.csv"
    recs = [
        PriceRecord(asset_class="futures", symbol="NQ=F", name="纳指期货",
                    price=20000.0, source="yfinance", timestamp=datetime(2026, 7, 21, 22, 10)),
        PriceRecord(asset_class="futures", symbol="NQ=F", name="纳指期货",
                    price=20010.0, source="yfinance", timestamp=datetime(2026, 7, 21, 22, 15)),
    ]
    write_dump(p, recs)
    return p


def test_import_inserts_and_chains(session_factory, tmp_path, monkeypatch):
    import scanners.price_scanner as ps_module
    monkeypatch.setattr(ps_module, "get_session", session_factory)

    stats = run_import(_dump(tmp_path), allowed_symbols={"NQ=F"},
                       start=START, end=END, dry_run=False)
    assert stats["inserted"] == 2

    s = session_factory()
    rows = s.query(PriceSnapshot).order_by(PriceSnapshot.timestamp).all()
    assert len(rows) == 2
    assert rows[1].prev_price == 20000.0            # 链条衔接
    assert rows[1].change_pct == pytest.approx(0.05, abs=1e-6)
    assert all(r.source == "yfinance" for r in rows)
    s.close()


def test_import_is_idempotent(session_factory, tmp_path, monkeypatch):
    import scanners.price_scanner as ps_module
    monkeypatch.setattr(ps_module, "get_session", session_factory)

    p = _dump(tmp_path)
    first = run_import(p, allowed_symbols={"NQ=F"}, start=START, end=END, dry_run=False)
    second = run_import(p, allowed_symbols={"NQ=F"}, start=START, end=END, dry_run=False)
    assert first["inserted"] == 2 and second["inserted"] == 0

    s = session_factory()
    assert s.query(PriceSnapshot).count() == 2
    s.close()


def test_dry_run_writes_nothing(session_factory, tmp_path, monkeypatch):
    import scanners.price_scanner as ps_module
    monkeypatch.setattr(ps_module, "get_session", session_factory)

    stats = run_import(_dump(tmp_path), allowed_symbols={"NQ=F"},
                       start=START, end=END, dry_run=True)
    assert stats["would_insert"] == 2

    s = session_factory()
    assert s.query(PriceSnapshot).count() == 0
    s.close()
