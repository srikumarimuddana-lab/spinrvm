"""Coverage tests for backend/routes/admin/incentives.py.

Driver-incentive/bonus program management is money-adjacent (bonus payouts
to drivers), so write endpoints (create/update/toggle/delete) are prioritized
over the read-only list/stats endpoints per CLAUDE.md guidance. This is a
TEST-ONLY change — no application code touched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

_ADMIN = {
    "id": "admin-1",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["service_areas"],
}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def _mock_execute(data, count=None):
    """db_supabase.run_sync() runs ``func()`` synchronously inside a thread
    executor (mirroring the real supabase-py client, whose `.execute` is a
    plain sync call) — so the mock must be a sync-returning callable, not an
    AsyncMock, or run_sync ends up handed an unawaited coroutine.
    """

    def _exec():
        resp = MagicMock()
        resp.data = data
        resp.count = count
        return resp

    return MagicMock(side_effect=_exec)


def _mock_execute_raises(exc):
    return MagicMock(side_effect=exc)


# ── list_incentives ──────────────────────────────────────────────────


class TestListIncentives:
    def test_happy_path_returns_rows(self, admin_client):
        from backend import db_supabase

        rows = [{"id": "inc-1", "name": "Peak boost", "priority": 5}]
        db_supabase.supabase.table.return_value.execute = _mock_execute(rows)

        resp = admin_client.get("/api/admin/incentives")
        assert resp.status_code == 200
        assert resp.json() == rows

    def test_filters_applied_for_service_area_and_active(self, admin_client):
        from backend import db_supabase

        table = db_supabase.supabase.table.return_value
        table.execute = _mock_execute([])

        resp = admin_client.get("/api/admin/incentives", params={"service_area_id": "area-1", "active_only": True})
        assert resp.status_code == 200
        # eq() called for both service_area_id and is_active
        eq_calls = [c.args for c in table.eq.call_args_list]
        assert ("service_area_id", "area-1") in eq_calls
        assert ("is_active", True) in eq_calls

    def test_db_error_returns_503(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.execute = MagicMock(side_effect=RuntimeError("db down"))

        resp = admin_client.get("/api/admin/incentives")
        assert resp.status_code == 503


# ── incentive_claim_stats ────────────────────────────────────────────


class TestIncentiveClaimStats:
    def test_happy_path_sums_bonus_amount(self, admin_client):
        from backend import db_supabase

        claims = [{"bonus_amount": 5.0}, {"bonus_amount": 2.5}]
        db_supabase.supabase.table.return_value.execute = _mock_execute(claims)

        resp = admin_client.get("/api/admin/incentives/claims/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_claims"] == 2
        assert data["total_paid"] == 7.5

    def test_no_claims_returns_zero_total(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.execute = _mock_execute([])

        resp = admin_client.get("/api/admin/incentives/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["total_paid"] == 0.0

    def test_db_error_returns_503(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.execute = MagicMock(side_effect=RuntimeError("boom"))

        resp = admin_client.get("/api/admin/incentives/claims/stats")
        assert resp.status_code == 503


# ── get_incentive ────────────────────────────────────────────────────


class TestGetIncentive:
    def test_found(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.execute = _mock_execute({"id": "inc-1", "name": "X"})

        resp = admin_client.get("/api/admin/incentives/inc-1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "inc-1"

    def test_not_found_returns_404(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.execute = _mock_execute(None)

        resp = admin_client.get("/api/admin/incentives/missing")
        assert resp.status_code == 404

    def test_db_error_returns_503(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.execute = MagicMock(side_effect=RuntimeError("boom"))

        resp = admin_client.get("/api/admin/incentives/inc-1")
        assert resp.status_code == 503


# ── create_incentive (money-adjacent: sets bonus_amount) ─────────────


class TestCreateIncentive:
    def test_happy_path_builds_row_and_returns_created(self, admin_client):
        from backend import db_supabase

        created = {"id": "inc-new", "name": "Weekend boost", "bonus_amount": 15.0}
        db_supabase.supabase.table.return_value.insert.return_value.execute = _mock_execute([created])

        payload = {
            "name": "Weekend boost",
            "incentive_type": "per_ride",
            "bonus_amount": 15.0,
            "service_area_id": "area-1",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "max_budget": 1000.0,
        }
        resp = admin_client.post("/api/admin/incentives", json=payload)
        assert resp.status_code == 200
        assert resp.json()["id"] == "inc-new"

        insert_call = db_supabase.supabase.table.return_value.insert.call_args
        row = insert_call.args[0]
        assert row["bonus_amount"] == 15.0
        assert row["service_area_id"] == "area-1"
        assert row["max_budget"] == 1000.0

    def test_bonus_amount_over_500_rejected_by_validation(self, admin_client):
        payload = {"name": "Too big", "incentive_type": "flat", "bonus_amount": 501}
        resp = admin_client.post("/api/admin/incentives", json=payload)
        assert resp.status_code == 422

    def test_bonus_amount_zero_or_negative_rejected(self, admin_client):
        payload = {"name": "Bad", "incentive_type": "flat", "bonus_amount": 0}
        resp = admin_client.post("/api/admin/incentives", json=payload)
        assert resp.status_code == 422

    def test_invalid_incentive_type_rejected(self, admin_client):
        payload = {"name": "X", "incentive_type": "not_a_real_type", "bonus_amount": 5}
        resp = admin_client.post("/api/admin/incentives", json=payload)
        assert resp.status_code == 422

    def test_db_error_returns_503(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.insert.return_value.execute = MagicMock(
            side_effect=RuntimeError("boom")
        )

        payload = {"name": "X", "incentive_type": "per_ride", "bonus_amount": 5}
        resp = admin_client.post("/api/admin/incentives", json=payload)
        assert resp.status_code == 503


# ── update_incentive (money-adjacent: can change bonus_amount) ───────


class TestUpdateIncentive:
    def test_happy_path_updates_and_stamps_updated_at(self, admin_client):
        from backend import db_supabase

        updated = {"id": "inc-1", "bonus_amount": 20.0}
        db_supabase.supabase.table.return_value.update.return_value.eq.return_value.execute = _mock_execute([updated])

        resp = admin_client.patch("/api/admin/incentives/inc-1", json={"bonus_amount": 20.0})
        assert resp.status_code == 200
        assert resp.json()["bonus_amount"] == 20.0

        update_call = db_supabase.supabase.table.return_value.update.call_args
        updates = update_call.args[0]
        assert updates["bonus_amount"] == 20.0
        assert "updated_at" in updates

    def test_empty_body_returns_400(self, admin_client):
        resp = admin_client.patch("/api/admin/incentives/inc-1", json={})
        assert resp.status_code == 400

    def test_not_found_returns_404(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.update.return_value.eq.return_value.execute = _mock_execute([])

        resp = admin_client.patch("/api/admin/incentives/missing", json={"name": "Y"})
        assert resp.status_code == 404

    def test_bonus_amount_over_500_rejected(self, admin_client):
        resp = admin_client.patch("/api/admin/incentives/inc-1", json={"bonus_amount": 999})
        assert resp.status_code == 422

    def test_db_error_returns_503(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.update.return_value.eq.return_value.execute = MagicMock(
            side_effect=RuntimeError("boom")
        )

        resp = admin_client.patch("/api/admin/incentives/inc-1", json={"name": "Y"})
        assert resp.status_code == 503


# ── toggle_incentive ───────────────────────────────────────────────────


class TestToggleIncentive:
    def test_happy_path_flips_is_active(self, admin_client):
        from backend import db_supabase

        table = db_supabase.supabase.table.return_value
        select_result = MagicMock()
        select_result.data = {"id": "inc-1", "is_active": True}
        update_result = MagicMock()
        update_result.data = [{"id": "inc-1", "is_active": False}]

        # toggle_incentive first SELECTs (select/eq/single chain -> mock_table
        # itself, since conftest wires those to return mock_table) then
        # UPDATEs (update().eq() -> a distinct auto-generated mock branch).
        table.execute = MagicMock(return_value=select_result)
        table.update.return_value.eq.return_value.execute = MagicMock(return_value=update_result)

        resp = admin_client.patch("/api/admin/incentives/inc-1/toggle")
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_not_found_returns_404(self, admin_client):
        from backend import db_supabase

        select_result = MagicMock()
        select_result.data = None
        db_supabase.supabase.table.return_value.execute = MagicMock(return_value=select_result)

        resp = admin_client.patch("/api/admin/incentives/missing/toggle")
        assert resp.status_code == 404

    def test_db_error_returns_503(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.execute = MagicMock(side_effect=RuntimeError("boom"))

        resp = admin_client.patch("/api/admin/incentives/inc-1/toggle")
        assert resp.status_code == 503


# ── delete_incentive ───────────────────────────────────────────────────


class TestDeleteIncentive:
    def test_happy_path(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.delete.return_value.eq.return_value.execute = _mock_execute(
            [{"id": "inc-1"}]
        )

        resp = admin_client.delete("/api/admin/incentives/inc-1")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}

    def test_not_found_returns_404(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.delete.return_value.eq.return_value.execute = _mock_execute([])

        resp = admin_client.delete("/api/admin/incentives/missing")
        assert resp.status_code == 404

    def test_db_error_returns_503(self, admin_client):
        from backend import db_supabase

        db_supabase.supabase.table.return_value.delete.return_value.eq.return_value.execute = MagicMock(
            side_effect=RuntimeError("boom")
        )

        resp = admin_client.delete("/api/admin/incentives/inc-1")
        assert resp.status_code == 503
