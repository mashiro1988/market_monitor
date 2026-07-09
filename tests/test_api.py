from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import api.app as app_module
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


def test_optional_api_auth(monkeypatch):
    import config

    monkeypatch.setattr(config, "APP_AUTH_TOKEN", "secret-token")
    c = client()

    health = c.get("/api/health")
    assert health.status_code == 200

    blocked = c.get("/api/status")
    assert blocked.status_code == 401
    assert blocked.json()["code"] == "UNAUTHORIZED"

    allowed = c.get("/api/status", headers={"Authorization": "Bearer secret-token"})
    assert allowed.status_code == 200


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


def test_market_history_rejects_invalid_datetime():
    c = client()
    response = c.get("/api/market/history", params={"start_utc": "not-a-date"})
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_DATETIME"


def test_annotation_context_news_rejects_invalid_datetime():
    c = client()
    response = c.get(
        "/api/annotations/context-news",
        params={
            "window_start_utc": "not-a-date",
            "window_end_utc": "2026-01-01T00:00:00",
        },
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_DATETIME"


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


def test_scheduler_registers_operational_jobs(monkeypatch):
    class FakeScheduler:
        def __init__(self, timezone):
            self.timezone = timezone
            self.jobs = []
            self.started = False

        def add_job(self, func, trigger, **kwargs):
            self.jobs.append({"func": func, "trigger": trigger, **kwargs})

        def start(self):
            self.started = True

    created = {}

    def fake_scheduler(timezone):
        scheduler = FakeScheduler(timezone)
        created["scheduler"] = scheduler
        return scheduler

    monkeypatch.setattr(app_module, "BackgroundScheduler", fake_scheduler)
    monkeypatch.setattr(app_module, "configure_proxy_env", lambda: None)
    monkeypatch.setattr(app_module, "create_tables", lambda: None)

    scheduler = app_module._start_background_scheduler()

    job_ids = {job["id"] for job in scheduler.jobs}
    assert scheduler.started is True
    assert {
        "scan_cycle",
        "startup_backfill",
        "hourly_summary",
        "remote_data_cycle",
        "gap_repair",
        "data_retention",
        "cmc_bootstrap",
        "cmc_refresh",
    }.issubset(job_ids)
