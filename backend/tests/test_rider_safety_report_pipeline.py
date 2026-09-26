"""The rider app's "Report Safety Issue" screen posts to the legacy
POST /api/v1/tickets/safety-report (rider-app/app/report-safety.tsx).

That handler (features.create_safety_report) used to write a
`support_tickets` row with a `priority` field -- a column no migration ever
created, so every submission failed with PGRST204 (first observed in
production 2026-09-26 02:18 UTC). Even without that error it bypassed the real
safety pipeline: no `safety_incidents` row, no notify_safety_team (admin WS
alert, email, CRITICAL on-call log), no urgent Zoho ticket -- unlike the
driver app, which already uses POST /safety/report.

The legacy path now delegates to routes.safety.record_safety_incident (the body
of POST /safety/report) with role pinned to "rider", so every
installed rider app build gets the real pipeline without an app release.
"""

import ast
import asyncio
import os
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.unit

RIDER = {"id": "rider_user_1", "role": "rider", "phone": "+13061234567"}
# One account holding both roles, as get_current_user returns it: is_driver is
# True whenever a driver row exists, regardless of which app sent the request.
DUAL_ROLE = {"id": "dual_user_1", "role": "rider", "is_driver": True, "phone": "+13065550000"}
LEGACY_PATH = "/api/v1/tickets/safety-report"
_FEATURES_SRC = pathlib.Path(__file__).resolve().parents[1] / "features.py"


def _client_as(user):
    import dependencies
    from backend.server import app

    app.dependency_overrides[dependencies.get_current_user] = lambda: user
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    yield from _client_as(RIDER)


@pytest.fixture
def dual_role_client():
    yield from _client_as(DUAL_ROLE)


def _legacy_handler_module():
    """The module object that actually owns the mounted legacy route.

    Looked up from the live route table rather than assumed, because the
    backend is importable both as `features` and `backend.features`
    (dual-import pattern) and patching the wrong copy proves nothing.
    """
    from backend.server import app

    route = next(r for r in app.routes if getattr(r, "path", None) == LEGACY_PATH)
    return sys.modules[route.endpoint.__module__]


def _safety_db():
    m = MagicMock()
    m.insert_one = AsyncMock(return_value={"id": "new"})
    m.get_ride = AsyncMock(return_value=None)
    m.get_rows = AsyncMock(return_value=[])
    return m


def _run_spawned(spawned):
    """Run the coroutines the handler handed to _spawn, as the event loop would
    after the response has gone out."""
    for coro in spawned:
        asyncio.run(coro)


def test_legacy_rider_report_creates_safety_incident_and_notifies(client):
    safety_db = _safety_db()
    legacy_db = MagicMock()
    legacy_db.insert_one = AsyncMock(return_value={"id": "new"})
    notify = AsyncMock(return_value={"ws": True, "email_sent": False, "email_attempted": False})
    spawned = []
    with (
        patch("routes.safety.db_supabase", safety_db),
        patch("routes.safety.notify_safety_team", notify),
        patch("routes.safety.create_ticket_for_safety", AsyncMock(return_value=None)),
        patch("routes.safety._spawn", spawned.append),
        patch.object(_legacy_handler_module(), "db_supabase", legacy_db),
    ):
        r = client.post(LEGACY_PATH, json={"description": "Driver was driving dangerously"})
        # The response must not wait on the alert fan-out (sequential email can
        # outlast the app's 15s timeout) -- notify is scheduled, not yet awaited.
        notify.assert_not_awaited()
        _run_spawned(spawned)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["incident_id"]

    safety_db.insert_one.assert_awaited_once()
    table, row = safety_db.insert_one.await_args.args
    assert table == "safety_incidents"
    assert row["category"] == "Other"
    assert row["role"] == "rider"
    assert row["reported_by_user_id"] == RIDER["id"]
    assert row["description"] == "Driver was driving dangerously"
    assert row["status"] == "open"

    notify.assert_awaited_once()
    assert notify.await_args.args[0]["id"] == body["incident_id"]
    legacy_db.insert_one.assert_not_awaited()


def test_dual_role_account_reporting_from_rider_app_is_recorded_as_rider(dual_role_client):
    """get_current_user sets is_driver=True for any account with a driver row,
    whichever app is in use. The rider-app endpoint must still file a rider
    report, not a driver one."""
    safety_db = _safety_db()
    with (
        patch("routes.safety.db_supabase", safety_db),
        patch("routes.safety.notify_safety_team", AsyncMock(return_value={})),
        patch("routes.safety.create_ticket_for_safety", AsyncMock(return_value=None)),
        patch("routes.safety._spawn", lambda coro: coro.close()),
    ):
        r = dual_role_client.post(LEGACY_PATH, json={"description": "Unsafe pickup"})
    assert r.status_code == 200, r.text
    _, row = safety_db.insert_one.await_args.args
    assert row["role"] == "rider"


def test_driver_app_report_still_derives_role_from_is_driver(dual_role_client):
    """The shared /safety/report route (driver app) keeps its behaviour."""
    safety_db = _safety_db()
    with (
        patch("routes.safety.db_supabase", safety_db),
        patch("routes.safety.notify_safety_team", AsyncMock(return_value={})),
        patch("routes.safety.create_ticket_for_safety", AsyncMock(return_value=None)),
        patch("routes.safety._spawn", lambda coro: coro.close()),
    ):
        r = dual_role_client.post("/api/v1/safety/report", json={"category": "Other", "description": "Road hazard"})
    assert r.status_code == 200, r.text
    _, row = safety_db.insert_one.await_args.args
    assert row["role"] == "driver"


def test_legacy_rider_report_db_failure_is_a_503_not_a_silent_success(client):
    safety_db = _safety_db()
    safety_db.insert_one = AsyncMock(side_effect=Exception("db down"))
    with (
        patch("routes.safety.db_supabase", safety_db),
        patch("routes.safety.notify_safety_team", AsyncMock()),
        patch("routes.safety.create_ticket_for_safety", AsyncMock(return_value=None)),
    ):
        r = client.post(LEGACY_PATH, json={"description": "Something happened"})
    assert r.status_code == 503


@pytest.mark.parametrize("description", ["", "x" * 4001])
def test_legacy_rider_report_rejects_empty_or_oversized_description(client, description):
    """Same bounds as POST /safety/report (1..4000) -- enforced at the request
    model so a bad input is a clean 422, not a 500 from re-validation inside
    the delegated handler."""
    with (
        patch("routes.safety.db_supabase", _safety_db()),
        patch("routes.safety.notify_safety_team", AsyncMock()),
    ):
        r = client.post(LEGACY_PATH, json={"description": description})
    assert r.status_code == 422


def test_legacy_handler_imports_record_safety_incident_in_both_branches():
    """Production imports the backend top-level (server.py: `from routes...`),
    so the relative import raises ImportError and the *except* branch is what
    runs live -- the same trap that once left notify_safety_team unbound on
    POST /safety/report (test_safety_notify_import.py). Both branches of the
    delegation import must bind record_safety_incident."""
    tree = ast.parse(_FEATURES_SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_safety_report")
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries, "create_safety_report must import the safety pipeline via the dual-import pattern"
    try_names, except_names = set(), set()
    for t in tries:
        for stmt in t.body:
            if isinstance(stmt, ast.ImportFrom):
                try_names.update(a.name for a in stmt.names)
        for h in t.handlers:
            for stmt in h.body:
                if isinstance(stmt, ast.ImportFrom):
                    except_names.update(a.name for a in stmt.names)
    assert "record_safety_incident" in try_names
    assert "record_safety_incident" in except_names
