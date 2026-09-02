"""Dedicated worker app: outbox poller + wave-1 loops, /health, /metrics."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


async def _idle_loop(*_args, **_kwargs) -> None:
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise


def _patch_loops():
    return (
        patch("worker.run_outbox_worker", new=AsyncMock(side_effect=_idle_loop)),
        patch("utils.push_retry.push_retry_loop", new=_idle_loop),
        patch("utils.zoho_desk_sync.zoho_desk_sync_loop", new=_idle_loop),
        patch("utils.driver_onboarding_reminders.driver_onboarding_reminder_loop", new=_idle_loop),
    )


@pytest.fixture
def worker_client(monkeypatch):
    monkeypatch.setenv("METRICS_AUTH_TOKEN", "test-metrics-token")
    monkeypatch.setenv("ENV", "test")
    patches = list(_patch_loops())
    for p in patches:
        p.start()
    import worker as worker_mod

    extra = [
        patch.object(worker_mod, "init_firebase"),
        patch.object(worker_mod, "init_backend_sentry"),
        patch.object(worker_mod, "init_database", new=AsyncMock()),
    ]
    for p in extra:
        p.start()
    with TestClient(worker_mod.app) as client:
        yield client, worker_mod
    for p in extra:
        p.stop()
    for p in patches:
        p.stop()


def test_worker_loop_names_match_registry():
    from core.background_loop_registry import WORKER_WAVE1_LOOP_NAMES
    from utils.loop_monitor import LOOP_THRESHOLDS
    from worker import OUTBOX_LOOP_NAME, WORKER_LOOP_NAMES, WORKER_TASK_LABELS

    assert WORKER_LOOP_NAMES[0] == OUTBOX_LOOP_NAME
    assert WORKER_LOOP_NAMES[1:] == WORKER_WAVE1_LOOP_NAMES
    assert set(WORKER_TASK_LABELS) == set(WORKER_LOOP_NAMES)
    for name in WORKER_LOOP_NAMES:
        assert name in LOOP_THRESHOLDS
        assert LOOP_THRESHOLDS[name] < 7200


def test_worker_app_has_no_product_routers():
    import worker as worker_mod

    paths = []
    for route in worker_mod.app.routes:
        path = getattr(route, "path", "")
        paths.append(path)
    assert "/health" in paths
    assert "/metrics" in paths
    joined = " ".join(paths)
    assert "/api/v1" not in joined
    assert "/rides" not in joined
    assert "/admin" not in joined


def test_health_ok_when_supervised_tasks_running(worker_client):
    client, worker_mod = worker_client
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert set(body["tasks"]) == set(worker_mod.WORKER_LOOP_NAMES)
    assert all(item["running"] is True for item in body["tasks"].values())


def test_health_unhealthy_when_a_task_is_dead(worker_client):
    client, worker_mod = worker_client
    name = worker_mod.WORKER_LOOP_NAMES[0]
    worker_mod.app.state.worker_tasks[name].cancel()
    client.get("/health")
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unhealthy"
    assert resp.json()["tasks"][name]["running"] is False


def test_health_ok_when_loops_never_ticked_after_grace(worker_client):
    """Zoho (10 min) and onboarding reminders (15 min) stay never_ticked
    well past STARTUP_GRACE_S. That must not 503 the worker or Fly restarts it."""
    client, worker_mod = worker_client
    worker_mod.app.state.worker_started_mono = time.monotonic() - (worker_mod.STARTUP_GRACE_S + 5)
    never = {
        "healthy": True,
        "loops": {
            name: {
                "status": "never_ticked",
                "seconds_since_tick": None,
                "threshold_seconds": 600,
            }
            for name in worker_mod.WORKER_LOOP_NAMES
        },
    }
    with patch("utils.loop_monitor.get_loop_status", return_value=never):
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_health_unhealthy_when_a_loop_is_stale_after_grace(worker_client):
    client, worker_mod = worker_client
    worker_mod.app.state.worker_started_mono = time.monotonic() - (worker_mod.STARTUP_GRACE_S + 5)
    name = worker_mod.WORKER_LOOP_NAMES[0]
    stale = {
        "healthy": False,
        "loops": {name: {"status": "stale", "seconds_since_tick": 999, "threshold_seconds": 600}},
    }
    with patch("utils.loop_monitor.get_loop_status", return_value=stale):
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unhealthy"
    assert resp.json()["tasks"][name]["heartbeat"] == "stale"


def test_metrics_requires_bearer(worker_client):
    client, _ = worker_client
    denied = client.get("/metrics")
    assert denied.status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"})
    assert ok.status_code == 200
    assert "spinr_worker_task_healthy" in ok.text


def test_metrics_rejects_query_string_token(worker_client):
    client, _ = worker_client
    resp = client.get("/metrics?token=test-metrics-token")
    assert resp.status_code == 401


def test_worker_lifespan_calls_firebase_sentry_and_database(monkeypatch):
    monkeypatch.setenv("METRICS_AUTH_TOKEN", "test-metrics-token")
    monkeypatch.setenv("ENV", "test")
    patches = _patch_loops()
    for p in patches:
        p.start()
    try:
        import worker as worker_mod

        order: list[str] = []

        def _firebase() -> None:
            order.append("firebase")

        def _sentry(**_kwargs) -> None:
            order.append("sentry")

        async def _database() -> None:
            order.append("database")

        with (
            patch.object(worker_mod, "init_firebase", side_effect=_firebase) as firebase,
            patch.object(worker_mod, "init_backend_sentry", side_effect=_sentry) as sentry,
            patch.object(worker_mod, "init_database", new=AsyncMock(side_effect=_database)) as database,
        ):
            with TestClient(worker_mod.app):
                firebase.assert_called_once()
                sentry.assert_called_once_with(process_name="spinr worker")
                database.assert_awaited_once()
                assert order == ["sentry", "firebase", "database"]
    finally:
        for p in patches:
            p.stop()


def test_worker_production_startup_raises_when_database_init_fails(monkeypatch):
    monkeypatch.setenv("METRICS_AUTH_TOKEN", "test-metrics-token")
    monkeypatch.setenv("ENV", "production")
    patches = _patch_loops()
    for p in patches:
        p.start()
    try:
        import worker as worker_mod

        with (
            patch.object(worker_mod, "init_firebase"),
            patch.object(worker_mod, "init_backend_sentry"),
            patch.object(
                worker_mod,
                "init_database",
                new=AsyncMock(side_effect=RuntimeError("no supabase")),
            ),
        ):
            with pytest.raises(RuntimeError, match="no supabase"):
                with TestClient(worker_mod.app):
                    pass
    finally:
        for p in patches:
            p.stop()


def test_graceful_shutdown_cancels_tasks(monkeypatch):
    monkeypatch.setenv("METRICS_AUTH_TOKEN", "test-metrics-token")
    monkeypatch.setenv("ENV", "test")
    patches = _patch_loops()
    for p in patches:
        p.start()
    try:
        import worker as worker_mod

        with (
            patch.object(worker_mod, "init_firebase"),
            patch.object(worker_mod, "init_backend_sentry"),
            patch.object(worker_mod, "init_database", new=AsyncMock()),
        ):
            with TestClient(worker_mod.app):
                tasks = dict(worker_mod.app.state.worker_tasks)
                assert all(not t.done() for t in tasks.values())
            assert all(t.done() for t in tasks.values())
    finally:
        for p in patches:
            p.stop()
