"""CMC category refresh helpers."""
from datetime import datetime, timedelta

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
from database import Base
from models.sector import CmcSymbolCategory
from services import cmc_client


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._payload


def test_get_retries_cmc_rate_limit(monkeypatch):
    monkeypatch.setattr(config, "CMC_API_KEY", "test-key")
    monkeypatch.setattr(cmc_client.time, "sleep", lambda *_: None)
    responses = [
        _FakeResponse(429, {}),
        _FakeResponse(200, {"status": {"error_code": 0}, "data": [{"id": "x"}]}),
    ]
    calls: list[str] = []

    class _Session:
        trust_env = True

        def get(self, url, **kwargs):
            calls.append(url)
            return responses.pop(0)

    monkeypatch.setattr(cmc_client.requests, "Session", lambda: _Session())

    data = cmc_client._get("/v1/test")

    assert data["data"] == [{"id": "x"}]
    assert len(calls) == 2


def test_get_does_not_retry_bad_request(monkeypatch):
    monkeypatch.setattr(config, "CMC_API_KEY", "test-key")
    calls = 0

    class _Session:
        trust_env = True

        def get(self, url, **kwargs):
            nonlocal calls
            calls += 1
            return _FakeResponse(400, {})

    monkeypatch.setattr(cmc_client.requests, "Session", lambda: _Session())

    with pytest.raises(requests.exceptions.HTTPError):
        cmc_client._get("/v1/test")
    assert calls == 1


def test_needs_refresh_checks_each_whitelisted_category(monkeypatch):
    monkeypatch.setattr(config, "CMC_CACHE_TTL_DAYS", 7)
    monkeypatch.setattr(config, "all_whitelisted_cmc_categories", lambda: ["Layer 1", "DeFi"])

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        now = datetime.utcnow()
        session.add(CmcSymbolCategory(symbol="ETH", category="Layer 1", updated_at=now))
        session.commit()
        assert cmc_client.needs_refresh(session) is True

        session.add(CmcSymbolCategory(symbol="AAVE", category="DeFi", updated_at=now - timedelta(days=8)))
        session.commit()
        assert cmc_client.needs_refresh(session) is True

        for row in session.query(CmcSymbolCategory).all():
            row.updated_at = now
        session.commit()
        assert cmc_client.needs_refresh(session) is False
    finally:
        session.close()
