"""Integration tests for /api/predictions/tracked endpoints."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from api.app import create_app
from database import get_session
from models.tracked_market import TrackedMarket


def _client() -> TestClient:
    return TestClient(create_app(enable_scheduler=False))


def _cleanup_test_rows():
    s = get_session()
    try:
        s.query(TrackedMarket).filter(TrackedMarket.identifier.like("test_%")).delete(synchronize_session=False)
        s.commit()
    finally:
        s.close()


def test_list_tracked_returns_seeded_rows():
    c = _client()
    r = c.get("/api/predictions/tracked")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert any(row["kind"] == "slug" for row in data)


def test_create_update_delete_cycle():
    _cleanup_test_rows()
    c = _client()

    create = c.post("/api/predictions/tracked", json={
        "kind": "slug",
        "identifier": "test_create_market",
        "display_name": "Test display",
    })
    assert create.status_code == 200
    body = create.json()
    assert body["enabled"] is True
    new_id = body["id"]

    duplicate = c.post("/api/predictions/tracked", json={
        "kind": "slug",
        "identifier": "test_create_market",
    })
    assert duplicate.status_code == 409

    patch = c.patch(f"/api/predictions/tracked/{new_id}", json={"enabled": False})
    assert patch.status_code == 200
    assert patch.json()["enabled"] is False

    patch_404 = c.patch("/api/predictions/tracked/999999", json={"enabled": False})
    assert patch_404.status_code == 404

    delete = c.delete(f"/api/predictions/tracked/{new_id}")
    assert delete.status_code == 200

    delete_404 = c.delete(f"/api/predictions/tracked/{new_id}")
    assert delete_404.status_code == 404

    _cleanup_test_rows()


def test_create_validates_kind():
    c = _client()
    r = c.post("/api/predictions/tracked", json={"kind": "bogus", "identifier": "x"})
    assert r.status_code == 422


def test_create_rejects_empty_identifier():
    _cleanup_test_rows()
    c = _client()
    r = c.post("/api/predictions/tracked", json={"kind": "slug", "identifier": "   "})
    assert r.status_code == 400
    _cleanup_test_rows()
