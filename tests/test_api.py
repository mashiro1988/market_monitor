from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from api.app import create_app


def client() -> TestClient:
    return TestClient(create_app(enable_scheduler=False))


def test_health_and_status():
    c = client()
    health = c.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    status = c.get("/api/status")
    assert status.status_code == 200
    assert "database" in status.json()


def test_market_latest_and_csv():
    c = client()
    latest = c.get("/api/market/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert "items" in body
    if body["items"]:
        assert "timestamp_utc" in body["items"][0]
        assert "timestamp_bj" in body["items"][0]

    csv_response = c.get("/api/market/table.csv?hours=24")
    assert csv_response.status_code == 200
    assert "text/csv" in csv_response.headers["content-type"]
    assert "北京时间" in csv_response.content.decode("utf-8-sig").splitlines()[0]


def test_news_pagination_filters():
    c = client()
    response = c.get("/api/news?hours_back=72&page_size=5&min_llm_importance=1")
    assert response.status_code == 200
    body = response.json()
    assert body["page_size"] == 5
    assert "zh_count" in body
    assert "en_count" in body


def test_prediction_families_and_alert_rules():
    c = client()
    families = c.get("/api/predictions/families?hours=720")
    assert families.status_code == 200
    assert isinstance(families.json(), list)

    rules = c.get("/api/alerts/rules")
    assert rules.status_code == 200
    assert isinstance(rules.json(), list)


def test_scan_task_skips_when_running(monkeypatch):
    import services.task_service as task_service

    task_service._TASKS.clear()
    task_service._RUNNING_SCAN_ID = None
    first = task_service.TaskRecord(
        task_id="running",
        status="running",
        created_at=datetime.utcnow(),
        started_at=datetime.utcnow(),
    )
    task_service._TASKS[first.task_id] = first
    task_service._RUNNING_SCAN_ID = first.task_id

    c = client()
    response = c.post("/api/tasks/scan")
    assert response.status_code == 200
    assert response.json()["status"] == "skipped"

    task_service._TASKS.clear()
    task_service._RUNNING_SCAN_ID = None


def test_task_retention_cleanup():
    import services.task_service as task_service

    task_service._TASKS.clear()
    old = task_service.TaskRecord(
        task_id="old",
        status="succeeded",
        created_at=datetime.utcnow() - timedelta(hours=25),
    )
    task_service._TASKS[old.task_id] = old
    assert task_service.get_task("old") is None
