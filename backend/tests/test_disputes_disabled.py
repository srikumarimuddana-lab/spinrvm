"""In-app disputes are disabled (owner decision 2026-09-25).

POST /api/v1/disputes and PUT /api/admin/disputes/{id}/resolve answer 410
before any DB read/write, Zoho ticket, Stripe refund or push. The original
handlers were deleted 2026-09-25 (cleanup follow-up); only the 410 stubs
remain in routes/disputes.py. The rider/driver GET list/detail, which no
client ever called, were deleted with them. The admin GET list/stats/detail
stay as a read-only historical view, served only by routes/admin/support.py.
See docs/change-log/2026-09-25-disable-in-app-disputes.md and
docs/change-log/2026-09-25-remove-disabled-dispute-code.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import routes.admin.support as support_mod
import routes.disputes as d

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "a@spinr.app", "modules": []}


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def side_effects(monkeypatch):
    """Every side effect the deleted handlers had; each must stay uncalled.

    routes/disputes.py no longer imports db_supabase, push, Zoho or Stripe at
    all, so these are patched at their source modules: anything reached from
    a request to the stubs would hit these mocks.
    """
    import stripe

    import db_supabase
    import features
    import services.zoho_desk_integration as zoho_integ

    mocks = {
        "get_rows": AsyncMock(return_value=[]),
        "get_ride": AsyncMock(return_value=None),
        "insert_one": AsyncMock(),
        "update_one": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(db_supabase, name, mock)
    mocks["push"] = AsyncMock()
    mocks["zoho_create_ticket"] = AsyncMock(return_value={})
    mocks["stripe_refund"] = MagicMock()
    monkeypatch.setattr(features, "send_push_notification", mocks["push"])
    monkeypatch.setattr(zoho_integ.zoho, "create_ticket", mocks["zoho_create_ticket"])
    monkeypatch.setattr(stripe.Refund, "create", mocks["stripe_refund"])
    return mocks


def _assert_no_side_effects(mocks):
    called = sorted(name for name, mock in mocks.items() if mock.called)
    assert called == [], f"disabled endpoint had side effects: {called}"


def test_disputes_module_holds_only_the_410_stubs():
    """The cleanup deleted every old handler; only the two stubs are routed."""
    user_routes = [(sorted(r.methods), r.path, r.endpoint.__name__) for r in d.api_router.routes]
    admin_routes = [(sorted(r.methods), r.path, r.endpoint.__name__) for r in d.admin_router.routes]
    assert user_routes == [(["POST"], "/disputes", "create_dispute_disabled")]
    assert admin_routes == [(["PUT"], "/disputes/{dispute_id}/resolve", "admin_resolve_dispute_disabled")]
    for gone in (
        "create_dispute",
        "admin_resolve_dispute",
        "admin_get_disputes",
        "get_user_disputes",
        "get_dispute",
        "CreateDisputeRequest",
        "ResolveDisputeRequest",
    ):
        assert not hasattr(d, gone), gone


def test_user_create_dispute_is_410_with_support_email(test_client, side_effects):
    resp = test_client.post(
        "/api/v1/disputes",
        json={"ride_id": "ride-1", "reason": "earnings_issue", "description": "x"},
    )
    assert resp.status_code == 410, resp.text
    detail = resp.json()["detail"]
    # Structured so the 4xx PII scrubber doesn't redact the support address.
    assert detail["code"] == "IN_APP_DISPUTES_DISABLED"
    assert detail["message"] == (
        "In-app disputes are not available. For questions about a charge, "
        "email support@spinr.ca or contact your card issuer."
    )
    _assert_no_side_effects(side_effects)


def test_user_create_dispute_is_410_even_with_old_or_empty_body(test_client, side_effects):
    # Old builds may send any shape; the answer is 410, never a 422.
    resp = test_client.post("/api/v1/disputes", json={})
    assert resp.status_code == 410, resp.text
    _assert_no_side_effects(side_effects)


def test_admin_resolve_is_410_and_moves_no_money(test_client, app_fixture, side_effects):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _ADMIN
    resp = test_client.put(
        "/api/admin/disputes/d1/resolve",
        json={"resolution": "approved", "refund_amount": 10, "admin_note": "goodwill"},
    )
    assert resp.status_code == 410, resp.text
    assert "read-only" in resp.text
    _assert_no_side_effects(side_effects)


@pytest.mark.parametrize(("modules", "status"), [(["support"], 403), (["disputes"], 410)])
def test_admin_resolve_keeps_disputes_module_gate(test_client, app_fixture, side_effects, modules, status):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: {"id": "admin-2", "role": "admin", "modules": modules}
    resp = test_client.put("/api/admin/disputes/d1/resolve", json={"resolution": "rejected"})
    assert resp.status_code == status, resp.text
    _assert_no_side_effects(side_effects)


def test_user_read_endpoints_are_gone(test_client, app_fixture, side_effects):
    """GET /disputes and /disputes/{id} were deleted: no rider/driver/shared
    client (current source or git history) ever called them. The list path
    keeps its POST 410 stub, so GET there is 405; the detail path is 404."""
    from dependencies import get_current_user

    app_fixture.dependency_overrides[get_current_user] = lambda: {"id": "rider-1", "role": "rider"}
    assert test_client.get("/api/v1/disputes").status_code == 405
    assert test_client.get("/api/v1/disputes/d-missing").status_code == 404
    _assert_no_side_effects(side_effects)


# ---------- GET /admin/disputes: exactly one handler ----------
#
# routes/admin/support.py and routes/disputes.py both used to register
# GET /disputes on the admin router. support.py is mounted first, so it has
# always been the one serving; the disputes.py copy was dead and is deleted.
# scripts/check_route_shadowing.py skips the routes.admin package, so this
# test is the pin.


def _flatten(routes, prefix=""):
    """(full path, route) for every route, recursing through the lazy
    `_IncludedRouter` wrapper newer FastAPI uses for include_router() (same
    walk as test_documents.py's websocket-route test, plus the include
    prefix, which the wrapper keeps on `include_context`)."""
    out = []
    for r in routes:
        original_router = getattr(r, "original_router", None)
        if original_router is not None:
            ctx_prefix = getattr(getattr(r, "include_context", None), "prefix", "") or ""
            out.extend(_flatten(original_router.routes, prefix + ctx_prefix))
        elif hasattr(r, "path"):
            out.append((prefix + r.path, r))
    return out


@pytest.mark.parametrize("prefix", ["/api/admin", "/api/v1/admin"])
@pytest.mark.parametrize(
    ("suffix", "handler"),
    [
        ("/disputes", "admin_get_disputes"),
        ("/disputes/stats", "admin_get_dispute_stats"),
        ("/disputes/{dispute_id}", "admin_get_dispute_details"),
    ],
)
def test_admin_dispute_reads_have_exactly_one_handler(app_fixture, prefix, suffix, handler):
    matches = [
        r
        for path, r in _flatten(app_fixture.routes)
        if path == prefix + suffix and "GET" in (getattr(r, "methods", None) or set())
    ]
    assert len(matches) == 1, [(r.endpoint.__module__, r.endpoint.__name__) for r in matches]
    assert matches[0].endpoint is getattr(support_mod, handler)


def test_route_walk_reaches_the_disputes_admin_router(app_fixture):
    """Guard for the test above: the walk must see routes/disputes.py's
    admin_router (where the deleted duplicate GET lived), or a re-added
    duplicate there would go uncounted."""
    found = {
        (path, r.endpoint)
        for path, r in _flatten(app_fixture.routes)
        if path.endswith("/admin/disputes/{dispute_id}/resolve") and "PUT" in (getattr(r, "methods", None) or set())
    }
    assert found == {
        ("/api/admin/disputes/{dispute_id}/resolve", d.admin_resolve_dispute_disabled),
        ("/api/v1/admin/disputes/{dispute_id}/resolve", d.admin_resolve_dispute_disabled),
    }


def test_admin_dispute_list_response_shape_is_unchanged(test_client, app_fixture, monkeypatch):
    """Same shape support.py's handler has always served: the dispute row plus
    `user_name` joined at read time (no ride_status/ride_fare, which only the
    deleted, never-reached disputes.py copy added)."""
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _ADMIN
    row = {"id": "d1", "user_id": "u1", "ride_id": "r1", "status": "open", "reason": "overcharged"}

    async def _get_rows(table, *args, **kwargs):
        if table == "disputes":
            return [row]
        if table == "users":
            return [{"id": "u1", "first_name": "Ada", "last_name": "L"}]
        raise AssertionError(f"unexpected table {table}")

    monkeypatch.setattr(support_mod.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows))
    resp = test_client.get("/api/admin/disputes", params={"status": "all"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{**row, "user_name": "Ada L"}]
