"""Admin safety-incident queue routes (TestClient).

Covers the SOS/safety-report triage queue an admin uses: list (default
open+in_progress scope, search, DB failure -> 503), detail (found, 404,
reporter/ride enrichment), and update (status transition stamps
resolved_at/resolved_by, no-op on empty body, DB failure -> 503, audit log
call). routes/admin/safety.py had 0% test coverage prior to this file.

get_rows is mocked with a table-name dispatcher, not a positional
side_effect list — TestClient(app)'s lifespan starts the real background
loops (retention_purge, corporate_autotopup, ...), which may also call
db_supabase functions concurrently during a test's patch window. A
positional side_effect list is not safe against that interleaving; dispatch
by table name is.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {
    "id": "admin-1",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["dashboard", "support"],
}

_INCIDENT = {
    "id": "inc-1",
    "reported_by_user_id": "user-1",
    "role": "rider",
    "category": "unsafe_driving",
    "status": "open",
    "severity": "sev2",
    "ride_id": "ride-1",
    "assigned_to_admin_id": None,
    "reported_at": "2026-07-01T00:00:00Z",
    "description": "Driver ran a red light.",
}

_USER = {
    "id": "user-1",
    "first_name": "Jane",
    "last_name": "Doe",
    "email": "jane@example.com",
    "phone": "+13065551234",
}

_RIDE = {
    "id": "ride-1",
    "ride_code": "SPX-1",
    "status": "completed",
    "rider_id": "user-1",
    "driver_id": "drv-1",
    "pickup_address": "100 Main St",
    "dropoff_address": "200 Elm St",
    "total_fare": "23.65",
    "ride_started_at": "2026-07-01T00:00:00Z",
    "ride_completed_at": "2026-07-01T00:20:00Z",
}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def _get_rows_by_table(**table_results):
    """Dispatch get_rows(table, ...) by table name, immune to interleaved
    calls from background loops started by TestClient(app)'s lifespan."""
    calls = []

    async def _fake(table, filters=None, **kw):
        calls.append((table, filters))
        result = table_results.get(table, [])
        if isinstance(result, Exception):
            raise result
        return result

    _fake.calls = calls
    return _fake


# ── list ──────────────────────────────────────────────────────────────────


def test_list_defaults_to_open_in_progress_scope(admin_client):
    fake_get_rows = _get_rows_by_table(safety_incidents=[_INCIDENT], users=[])
    with (
        patch("backend.db_supabase.get_rows", new=fake_get_rows),
        patch("backend.db_supabase.count_documents", new=AsyncMock(return_value=1)),
        patch(
            "routes.admin.safety._batch_fetch_drivers_and_users",
            new=AsyncMock(return_value=({}, {})),
        ),
    ):
        resp = admin_client.get("/api/admin/safety/incidents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["open_count"] == 1
    assert body["items"][0]["id"] == "inc-1"

    # First recorded call to the incidents table is the page query — assert
    # the default filter is the open+in_progress set, not an unfiltered scan.
    incidents_calls = [c for c in fake_get_rows.calls if c[0] == "safety_incidents"]
    assert incidents_calls[0][1]["status"] == {"$in": ["open", "in_progress"]}


def test_list_enriches_with_reporter_name(admin_client):
    with (
        patch(
            "backend.db_supabase.get_rows",
            new=_get_rows_by_table(safety_incidents=[_INCIDENT], users=[_USER]),
        ),
        patch("backend.db_supabase.count_documents", new=AsyncMock(return_value=1)),
        patch(
            "routes.admin.safety._batch_fetch_drivers_and_users",
            new=AsyncMock(return_value=({}, {})),
        ),
    ):
        resp = admin_client.get("/api/admin/safety/incidents")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["reporter_name"]


def test_list_search_is_pushed_into_db_filter_as_regex(admin_client):
    fake_get_rows = _get_rows_by_table(safety_incidents=[], users=[])
    with (
        patch("backend.db_supabase.get_rows", new=fake_get_rows),
        patch("backend.db_supabase.count_documents", new=AsyncMock(return_value=0)),
        patch(
            "routes.admin.safety._batch_fetch_drivers_and_users",
            new=AsyncMock(return_value=({}, {})),
        ),
    ):
        resp = admin_client.get("/api/admin/safety/incidents", params={"search": "red light"})
    assert resp.status_code == 200
    incidents_calls = [c for c in fake_get_rows.calls if c[0] == "safety_incidents"]
    filters = incidents_calls[0][1]
    # Raw term — the query layer escapes for ILIKE. Previously re.escape() turned
    # this into "red\\ light", which _escape_like then double-escaped into a
    # pattern matching a literal backslash, so no multi-word search ever matched.
    assert filters["description"]["$regex"] == "red light"
    assert filters["description"]["$options"] == "i"


def test_list_query_failure_maps_to_503(admin_client):
    with patch(
        "backend.db_supabase.get_rows",
        new=_get_rows_by_table(safety_incidents=RuntimeError("db down")),
    ):
        resp = admin_client.get("/api/admin/safety/incidents")
    assert resp.status_code == 503


def test_list_filters_by_severity_role_category_ride_id(admin_client):
    """Each optional filter param (severity, role, category, ride_id) must
    actually reach the DB filter dict — previously only status and search
    were exercised."""
    fake_get_rows = _get_rows_by_table(safety_incidents=[_INCIDENT], users=[])
    with (
        patch("backend.db_supabase.get_rows", new=fake_get_rows),
        patch("backend.db_supabase.count_documents", new=AsyncMock(return_value=1)),
        patch(
            "routes.admin.safety._batch_fetch_drivers_and_users",
            new=AsyncMock(return_value=({}, {})),
        ),
    ):
        resp = admin_client.get(
            "/api/admin/safety/incidents",
            params={
                "status": "resolved",
                "severity": "sev1",
                "role": "driver",
                "category": "unsafe_driving",
                "ride_id": "ride-1",
            },
        )
    assert resp.status_code == 200
    incidents_calls = [c for c in fake_get_rows.calls if c[0] == "safety_incidents"]
    filters = incidents_calls[0][1]
    # An explicit status overrides the default open+in_progress scope.
    assert filters["status"] == "resolved"
    assert filters["severity"] == "sev1"
    assert filters["role"] == "driver"
    assert filters["category"] == "unsafe_driving"
    assert filters["ride_id"] == "ride-1"


def test_list_count_failure_falls_back_without_500(admin_client):
    """Count queries are best-effort for pagination UI — a count failure must
    not take down the whole list response."""
    with (
        patch(
            "backend.db_supabase.get_rows",
            new=_get_rows_by_table(safety_incidents=[_INCIDENT], users=[]),
        ),
        patch("backend.db_supabase.count_documents", new=AsyncMock(side_effect=RuntimeError("count down"))),
        patch(
            "routes.admin.safety._batch_fetch_drivers_and_users",
            new=AsyncMock(return_value=({}, {})),
        ),
    ):
        resp = admin_client.get("/api/admin/safety/incidents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1  # falls back to offset + len(page)
    assert body["open_count"] is None


# ── detail ────────────────────────────────────────────────────────────────


def test_detail_returns_incident_reporter_and_ride(admin_client):
    with patch(
        "backend.db_supabase.get_rows",
        new=_get_rows_by_table(safety_incidents=[_INCIDENT], users=[_USER], rides=[_RIDE]),
    ):
        resp = admin_client.get("/api/admin/safety/incidents/inc-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["incident"]["id"] == "inc-1"
    assert body["reporter"]["email"] == "jane@example.com"
    assert body["ride"]["ride_code"] == "SPX-1"


def test_detail_404_when_incident_missing(admin_client):
    with patch("backend.db_supabase.get_rows", new=_get_rows_by_table(safety_incidents=[])):
        resp = admin_client.get("/api/admin/safety/incidents/nope")
    assert resp.status_code == 404


def test_detail_survives_reporter_lookup_failure(admin_client):
    """A broken reporter/ride lookup must not 500 the whole detail view —
    an admin triaging an active SOS incident needs the incident row even if
    an enrichment lookup fails."""
    with patch(
        "backend.db_supabase.get_rows",
        new=_get_rows_by_table(
            safety_incidents=[_INCIDENT],
            users=RuntimeError("users lookup down"),
            rides=[_RIDE],
        ),
    ):
        resp = admin_client.get("/api/admin/safety/incidents/inc-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["incident"]["id"] == "inc-1"
    assert body["reporter"] is None
    assert body["ride"]["ride_code"] == "SPX-1"


def test_detail_survives_ride_snapshot_lookup_failure(admin_client):
    """A broken ride-snapshot lookup must not 500 the whole detail view —
    mirrors test_detail_survives_reporter_lookup_failure but for the `rides`
    table specifically, which has its own independent try/except."""
    with patch(
        "backend.db_supabase.get_rows",
        new=_get_rows_by_table(
            safety_incidents=[_INCIDENT],
            users=[_USER],
            rides=RuntimeError("rides lookup down"),
        ),
    ):
        resp = admin_client.get("/api/admin/safety/incidents/inc-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["incident"]["id"] == "inc-1"
    assert body["reporter"]["email"] == "jane@example.com"
    assert body["ride"] is None


# ── update ────────────────────────────────────────────────────────────────


def test_update_to_resolved_stamps_resolved_at_and_by(admin_client):
    resolved = {**_INCIDENT, "status": "resolved", "resolved_at": "2026-07-02T00:00:00Z", "resolved_by": "admin-1"}
    calls_holder = {"n": 0}

    async def _fake_get_rows(table, filters=None, **kw):
        assert table == "safety_incidents"
        calls_holder["n"] += 1
        return [_INCIDENT] if calls_holder["n"] == 1 else [resolved]

    with (
        patch("backend.db_supabase.get_rows", new=_fake_get_rows),
        patch("backend.db_supabase.update_one", new=AsyncMock(return_value=None)) as update_one,
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(return_value="aud-1")) as log,
    ):
        resp = admin_client.patch("/api/admin/safety/incidents/inc-1", json={"status": "resolved"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] is True
    assert body["incident"]["status"] == "resolved"

    updates = update_one.call_args.args[2]
    assert updates["status"] == "resolved"
    assert "resolved_at" in updates
    assert updates["resolved_by"] == "admin-1"
    log.assert_awaited_once()


def test_update_does_not_restamp_already_resolved_incident(admin_client):
    """Re-saving a resolved incident (e.g. editing notes) must not clobber the
    original resolved_at with 'now'."""
    already_resolved = {**_INCIDENT, "status": "resolved", "resolved_at": "2026-06-01T00:00:00Z"}
    with (
        patch(
            "backend.db_supabase.get_rows",
            new=_get_rows_by_table(safety_incidents=[already_resolved]),
        ),
        patch("backend.db_supabase.update_one", new=AsyncMock(return_value=None)) as update_one,
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.patch("/api/admin/safety/incidents/inc-1", json={"status": "resolved"})
    assert resp.status_code == 200
    updates = update_one.call_args.args[2]
    assert "resolved_at" not in updates


def test_update_sets_assigned_to_admin_id(admin_client):
    with (
        patch(
            "backend.db_supabase.get_rows",
            new=_get_rows_by_table(safety_incidents=[_INCIDENT]),
        ),
        patch("backend.db_supabase.update_one", new=AsyncMock(return_value=None)) as update_one,
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.patch("/api/admin/safety/incidents/inc-1", json={"assigned_to_admin_id": "admin-2"})
    assert resp.status_code == 200
    updates = update_one.call_args.args[2]
    assert updates["assigned_to_admin_id"] == "admin-2"


def test_update_clears_assigned_to_admin_id_with_empty_string(admin_client):
    """Caller can pass an empty string to explicitly clear the assignment —
    the route coerces '' to None rather than storing the empty string."""
    with (
        patch(
            "backend.db_supabase.get_rows",
            new=_get_rows_by_table(safety_incidents=[{**_INCIDENT, "assigned_to_admin_id": "admin-2"}]),
        ),
        patch("backend.db_supabase.update_one", new=AsyncMock(return_value=None)) as update_one,
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.patch("/api/admin/safety/incidents/inc-1", json={"assigned_to_admin_id": ""})
    assert resp.status_code == 200
    updates = update_one.call_args.args[2]
    assert updates["assigned_to_admin_id"] is None


def test_update_sets_resolution_notes(admin_client):
    with (
        patch(
            "backend.db_supabase.get_rows",
            new=_get_rows_by_table(safety_incidents=[_INCIDENT]),
        ),
        patch("backend.db_supabase.update_one", new=AsyncMock(return_value=None)) as update_one,
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.patch(
            "/api/admin/safety/incidents/inc-1", json={"resolution_notes": "Spoke to both parties, resolved."}
        )
    assert resp.status_code == 200
    updates = update_one.call_args.args[2]
    assert updates["resolution_notes"] == "Spoke to both parties, resolved."


def test_update_with_empty_body_is_a_noop(admin_client):
    fake_get_rows = _get_rows_by_table(safety_incidents=[_INCIDENT])
    with patch("backend.db_supabase.get_rows", new=fake_get_rows):
        resp = admin_client.patch("/api/admin/safety/incidents/inc-1", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] is False
    assert body["incident"]["id"] == "inc-1"
    # only the existence check, no write attempted
    assert len([c for c in fake_get_rows.calls if c[0] == "safety_incidents"]) == 1


def test_update_404_when_incident_missing(admin_client):
    with patch("backend.db_supabase.get_rows", new=_get_rows_by_table(safety_incidents=[])):
        resp = admin_client.patch("/api/admin/safety/incidents/nope", json={"status": "closed"})
    assert resp.status_code == 404


def test_update_db_failure_maps_to_503(admin_client):
    with (
        patch(
            "backend.db_supabase.get_rows",
            new=_get_rows_by_table(safety_incidents=[_INCIDENT]),
        ),
        patch("backend.db_supabase.update_one", new=AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        resp = admin_client.patch("/api/admin/safety/incidents/inc-1", json={"severity": "sev1"})
    assert resp.status_code == 503


def test_update_audit_log_never_includes_resolution_notes():
    """resolution_notes can contain rider-written PII/free text describing an
    incident — the audit trail must record that a field changed, never the
    content."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "routes" / "admin" / "safety.py").read_text()
    start = src.index("async def admin_update_safety_incident(")
    body = src[start : src.index("\n\n\n", start) if "\n\n\n" in src[start:] else len(src)]
    assert 'k != "resolution_notes"' in body


def test_update_audit_log_failure_does_not_fail_the_request(admin_client):
    """The update itself must succeed even if the audit log write fails —
    losing the DB write because an audit call errored would be worse than a
    missing audit entry."""
    with (
        patch(
            "backend.db_supabase.get_rows",
            new=_get_rows_by_table(safety_incidents=[_INCIDENT]),
        ),
        patch("backend.db_supabase.update_one", new=AsyncMock(return_value=None)),
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(side_effect=RuntimeError("audit down"))),
    ):
        resp = admin_client.patch("/api/admin/safety/incidents/inc-1", json={"severity": "sev1"})
    assert resp.status_code == 200
    assert resp.json()["updated"] is True


# ── create ────────────────────────────────────────────────────────────────
# Corporate + admin portal review, round 2: "safety incidents can't be
# created ... from the admin side."


def test_create_writes_open_incident(admin_client):
    with (
        patch("backend.db_supabase.insert_one", new=AsyncMock(return_value=None)) as insert_one,
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(return_value="aud-1")) as log,
    ):
        resp = admin_client.post(
            "/api/admin/safety/incidents",
            json={"category": "unsafe_driving", "description": "Phoned in by rider.", "role": "rider"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()["incident"]
    assert body["status"] == "open"
    assert body["category"] == "unsafe_driving"
    assert body["role"] == "rider"
    assert body["id"]  # a uuid was generated
    inserted = insert_one.call_args.args[1]
    assert inserted["status"] == "open"
    log.assert_awaited_once()


def test_create_accepts_optional_ride_and_reporter(admin_client):
    with (
        patch("backend.db_supabase.insert_one", new=AsyncMock(return_value=None)) as insert_one,
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.post(
            "/api/admin/safety/incidents",
            json={
                "category": "assault",
                "description": "Driver reported by phone.",
                "role": "driver",
                "reported_by_user_id": "drv-1",
                "ride_id": "ride-1",
                "severity": "sev1",
            },
        )
    assert resp.status_code == 200, resp.text
    inserted = insert_one.call_args.args[1]
    assert inserted["reported_by_user_id"] == "drv-1"
    assert inserted["ride_id"] == "ride-1"
    assert inserted["severity"] == "sev1"


def test_create_rejects_blank_category(admin_client):
    resp = admin_client.post(
        "/api/admin/safety/incidents",
        json={"category": "", "description": "x"},
    )
    assert resp.status_code == 422


def test_create_db_failure_maps_to_503(admin_client):
    with patch("backend.db_supabase.insert_one", new=AsyncMock(side_effect=RuntimeError("db down"))):
        resp = admin_client.post(
            "/api/admin/safety/incidents",
            json={"category": "unsafe_driving", "description": "x"},
        )
    assert resp.status_code == 503


def test_create_audit_log_failure_does_not_fail_the_request(admin_client):
    with (
        patch("backend.db_supabase.insert_one", new=AsyncMock(return_value=None)),
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(side_effect=RuntimeError("audit down"))),
    ):
        resp = admin_client.post(
            "/api/admin/safety/incidents",
            json={"category": "unsafe_driving", "description": "x"},
        )
    assert resp.status_code == 200


# ── merge ─────────────────────────────────────────────────────────────────
# Corporate + admin portal review, round 2: "safety incidents can't be
# ... merged from the admin side."


def test_merge_marks_source_as_duplicate(admin_client):
    canonical = {**_INCIDENT, "id": "inc-canonical", "merged_into_incident_id": None}
    merged = {**_INCIDENT, "status": "duplicate", "merged_into_incident_id": "inc-canonical"}
    calls_holder = {"n": 0}

    async def _fake_get_rows(table, filters=None, **kw):
        assert table == "safety_incidents"
        calls_holder["n"] += 1
        if calls_holder["n"] == 1:
            return [_INCIDENT]  # source lookup
        if calls_holder["n"] == 2:
            return [canonical]  # canonical lookup
        return [merged]  # refresh after update

    with (
        patch("backend.db_supabase.get_rows", new=_fake_get_rows),
        patch("backend.db_supabase.update_one", new=AsyncMock(return_value=None)) as update_one,
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(return_value="aud-1")) as log,
    ):
        resp = admin_client.post(
            "/api/admin/safety/incidents/inc-1/merge",
            json={"canonical_incident_id": "inc-canonical"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["merged"] is True
    assert body["incident"]["status"] == "duplicate"

    updates = update_one.call_args.args[2]
    assert updates["status"] == "duplicate"
    assert updates["merged_into_incident_id"] == "inc-canonical"
    assert updates["resolved_by"] == "admin-1"
    log.assert_awaited_once()


def test_merge_rejects_self_merge(admin_client):
    resp = admin_client.post(
        "/api/admin/safety/incidents/inc-1/merge",
        json={"canonical_incident_id": "inc-1"},
    )
    assert resp.status_code == 422


def test_merge_404_when_source_missing(admin_client):
    with patch("backend.db_supabase.get_rows", new=_get_rows_by_table(safety_incidents=[])):
        resp = admin_client.post(
            "/api/admin/safety/incidents/nope/merge",
            json={"canonical_incident_id": "inc-canonical"},
        )
    assert resp.status_code == 404


def test_merge_404_when_canonical_missing(admin_client):
    """Source exists, canonical target doesn't — still a 404, not a
    silent write to a target that isn't real."""
    calls_holder = {"n": 0}

    async def _fake_get_rows(table, filters=None, **kw):
        calls_holder["n"] += 1
        return [_INCIDENT] if calls_holder["n"] == 1 else []

    with patch("backend.db_supabase.get_rows", new=_fake_get_rows):
        resp = admin_client.post(
            "/api/admin/safety/incidents/inc-1/merge",
            json={"canonical_incident_id": "nope"},
        )
    assert resp.status_code == 404


def test_merge_flattens_chain_to_the_root_canonical(admin_client):
    """Merging into an incident that is itself already a duplicate of
    something else must point at the chain's root, not build a 2-hop
    chain — otherwise "find every duplicate of X" stops being a
    single-hop query."""
    already_merged_canonical = {
        **_INCIDENT,
        "id": "inc-b",
        "status": "duplicate",
        "merged_into_incident_id": "inc-root",
    }
    calls_holder = {"n": 0}

    async def _fake_get_rows(table, filters=None, **kw):
        calls_holder["n"] += 1
        if calls_holder["n"] == 1:
            return [_INCIDENT]
        if calls_holder["n"] == 2:
            return [already_merged_canonical]
        return [_INCIDENT]

    with (
        patch("backend.db_supabase.get_rows", new=_fake_get_rows),
        patch("backend.db_supabase.update_one", new=AsyncMock(return_value=None)) as update_one,
        patch("routes.admin.safety.log_admin_action", new=AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.post(
            "/api/admin/safety/incidents/inc-1/merge",
            json={"canonical_incident_id": "inc-b"},
        )
    assert resp.status_code == 200, resp.text
    updates = update_one.call_args.args[2]
    assert updates["merged_into_incident_id"] == "inc-root"


def test_merge_db_failure_maps_to_503(admin_client):
    with (
        patch(
            "backend.db_supabase.get_rows",
            new=_get_rows_by_table(safety_incidents=[_INCIDENT]),
        ),
        patch("backend.db_supabase.update_one", new=AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        resp = admin_client.post(
            "/api/admin/safety/incidents/inc-1/merge",
            json={"canonical_incident_id": "inc-canonical"},
        )
    assert resp.status_code == 503
