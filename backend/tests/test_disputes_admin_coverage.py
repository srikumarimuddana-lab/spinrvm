"""
A1c Sub-tier C coverage: backend/routes/disputes.py (73.88% -> target 90%+).

`test_p3_addresses_favorites_safety_disputes.py` covers the user-facing
`create_dispute` / `get_user_disputes` / `get_dispute` endpoints.
`test_dispute_refund_cents.py` covers `admin_resolve_dispute`'s
dollars-to-cents HALF_UP conversion and idempotency key on the happy
(partial_refund, Stripe succeeds) path only.

NOTE: like `routes/promotions.py`'s `admin_router`, this module's
`admin_router` (`admin_get_disputes` / `admin_resolve_dispute`) is never
mounted in `backend/server.py` — the live `/api/admin/disputes` surface is
`routes/admin/support.py` (confirmed: `grep -n "disputes" backend/server.py`
shows only `disputes_router`, this module's user-facing `api_router`,
included; `test_admin_support_routes.py` targets
`routes/admin/support.py` explicitly, not this module). Both functions are
exercised here as plain async functions (as `test_dispute_refund_cents.py`
already does for `admin_resolve_dispute`), not via HTTP, for coverage
purposes — flagged as a finding, not fixed.

This file closes:
- `admin_get_disputes`: empty list, enrichment with known user/ride,
  enrichment with an unknown user_id ("Unknown" fallback), status filter
  applied.
- `admin_resolve_dispute`: dispute not found (404), already-resolved (400),
  refund exceeding `original_fare` (400), no `payment_intent_id` on the ride
  (`manual_required`, DB-only), Stripe not configured (503), Stripe refund
  API exception (502, dispute stays open), `rejected` resolution (no refund
  attempted, `status="rejected"`), `approved` with no `refund_amount` (skips
  the refund block entirely), and the rider push-notification failure being
  swallowed rather than failing the request.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.anyio

_ADMIN = {"id": "admin_1", "email": "ops@spinr.ca", "role": "admin"}


def _dispute(**overrides):
    base = {
        "id": "disp_1",
        "ride_id": "ride_1",
        "user_id": "user_1",
        "status": "open",
        "original_fare": 50.00,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# admin_get_disputes
# ---------------------------------------------------------------------------


class TestAdminGetDisputes:
    async def test_empty_disputes_returns_empty_list(self):
        from backend.routes.disputes import admin_get_disputes

        with patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = await admin_get_disputes(current_admin=dict(_ADMIN))
        assert result == []

    async def test_enriches_with_user_name_and_ride_info(self):
        from backend.routes.disputes import admin_get_disputes

        disputes = [_dispute(user_id="user_1", ride_id="ride_1")]
        users = [{"id": "user_1", "first_name": "Jane", "last_name": "Doe"}]
        rides = [{"id": "ride_1", "status": "completed", "total_fare": 25.50}]

        async def fake_get_rows(table, filters=None, **kwargs):
            if table == "disputes":
                return disputes
            if table == "users":
                return users
            if table == "rides":
                return rides
            return []

        with patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)):
            result = await admin_get_disputes(current_admin=dict(_ADMIN))
        assert result[0]["user_name"] == "Jane Doe"
        assert result[0]["ride_status"] == "completed"
        assert result[0]["ride_fare"] == 25.50

    async def test_unknown_user_falls_back_to_unknown_label(self):
        from backend.routes.disputes import admin_get_disputes

        disputes = [_dispute(user_id="ghost-user", ride_id=None)]

        async def fake_get_rows(table, filters=None, **kwargs):
            if table == "disputes":
                return disputes
            if table == "users":
                return []  # user_id present but lookup returns nothing
            return []

        with patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)):
            result = await admin_get_disputes(current_admin=dict(_ADMIN))
        assert result[0]["user_name"] == "Unknown"
        assert result[0]["ride_status"] is None
        assert result[0]["ride_fare"] is None

    async def test_status_filter_passed_through(self):
        from backend.routes.disputes import admin_get_disputes

        mock_get_rows = AsyncMock(return_value=[])
        with patch("backend.routes.disputes.db_supabase.get_rows", mock_get_rows):
            await admin_get_disputes(status="open", current_admin=dict(_ADMIN))
        first_call = mock_get_rows.call_args_list[0]
        assert first_call.args[0] == "disputes"
        assert first_call.args[1] == {"status": "open"}


# ---------------------------------------------------------------------------
# admin_resolve_dispute — remaining branches
# ---------------------------------------------------------------------------


class TestAdminResolveDispute:
    async def test_dispute_not_found_raises_404(self):
        from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

        req = ResolveDisputeRequest(resolution="approved")
        with patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await admin_resolve_dispute(dispute_id="ghost", req=req, current_admin=dict(_ADMIN))
        assert exc.value.status_code == 404

    async def test_already_resolved_raises_400(self):
        from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

        req = ResolveDisputeRequest(resolution="approved")
        dispute = _dispute(status="resolved")
        with patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dispute])):
            with pytest.raises(HTTPException) as exc:
                await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
        assert exc.value.status_code == 400
        assert "already resolved" in exc.value.detail.lower()

    async def test_refund_exceeding_original_fare_raises_400(self):
        from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

        req = ResolveDisputeRequest(resolution="partial_refund", refund_amount=Decimal("999.00"))
        dispute = _dispute(original_fare=50.00)
        with patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dispute])):
            with pytest.raises(HTTPException) as exc:
                await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
        assert exc.value.status_code == 400
        assert "exceeds original fare" in exc.value.detail

    async def test_no_payment_intent_marks_manual_required(self):
        from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

        req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
        dispute = _dispute()
        with (
            patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dispute])),
            patch("backend.routes.disputes.db_supabase.get_ride", AsyncMock(return_value={"id": "ride_1"})),
            patch("backend.routes.disputes.db_supabase.update_one", AsyncMock()) as mock_update,
            patch("backend.routes.disputes.log_admin_action", AsyncMock()),
            patch("backend.routes.disputes.send_push_notification", AsyncMock()),
        ):
            result = await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
        assert result["refund"]["status"] == "manual_required"
        assert result["refund"]["reason"] == "no_payment_intent"
        update_payload = mock_update.call_args.args[2]
        assert update_payload["refund_result"]["status"] == "manual_required"

    async def test_stripe_not_configured_raises_503(self):
        from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

        req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
        dispute = _dispute()
        with (
            patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dispute])),
            patch(
                "backend.routes.disputes.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_1", "stripe_charge_id": "pi_1"}),
            ),
            patch("backend.routes.disputes.get_app_settings", AsyncMock(return_value={})),
        ):
            with pytest.raises(HTTPException) as exc:
                await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
        assert exc.value.status_code == 503

    async def test_stripe_refund_exception_raises_502_and_leaves_dispute_open(self):
        from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

        req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
        dispute = _dispute()
        with (
            patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dispute])),
            patch(
                "backend.routes.disputes.db_supabase.get_ride",
                AsyncMock(return_value={"id": "ride_1", "stripe_charge_id": "pi_1"}),
            ),
            patch(
                "backend.routes.disputes.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("stripe.Refund.create", MagicMock(side_effect=Exception("card issuer down"))),
            patch("backend.routes.disputes.db_supabase.update_one", AsyncMock()) as mock_update,
        ):
            with pytest.raises(HTTPException) as exc:
                await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
        assert exc.value.status_code == 502
        # Dispute must NOT be marked resolved when Stripe fails.
        mock_update.assert_not_called()

    async def test_rejected_resolution_skips_refund_and_marks_rejected(self):
        from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

        req = ResolveDisputeRequest(resolution="rejected", admin_note="not eligible")
        dispute = _dispute()
        with (
            patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dispute])),
            patch("backend.routes.disputes.db_supabase.update_one", AsyncMock()) as mock_update,
            patch("backend.routes.disputes.log_admin_action", AsyncMock()),
            patch("backend.routes.disputes.send_push_notification", AsyncMock()),
        ):
            result = await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
        assert result["resolution"] == "rejected"
        update_payload = mock_update.call_args.args[2]
        assert update_payload["status"] == "rejected"
        assert "refund_result" not in update_payload

    async def test_approved_without_refund_amount_skips_refund_block(self):
        from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

        req = ResolveDisputeRequest(resolution="approved")  # no refund_amount
        dispute = _dispute()
        with (
            patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dispute])),
            patch("backend.routes.disputes.db_supabase.get_ride", AsyncMock()) as mock_get_ride,
            patch("backend.routes.disputes.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.disputes.log_admin_action", AsyncMock()),
            patch("backend.routes.disputes.send_push_notification", AsyncMock()),
        ):
            result = await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
        mock_get_ride.assert_not_called()
        assert result["refund"] is None

    async def test_push_notification_failure_does_not_fail_request(self):
        from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

        req = ResolveDisputeRequest(resolution="rejected")
        dispute = _dispute()
        with (
            patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dispute])),
            patch("backend.routes.disputes.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.disputes.log_admin_action", AsyncMock()),
            patch(
                "backend.routes.disputes.send_push_notification",
                AsyncMock(side_effect=Exception("push down")),
            ),
        ):
            result = await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
        assert result["success"] is True
