"""Coverage tests for backend/routes/disputes.py (A1c Sub-tier C).

Test-only change — no application code modified. Written by reading the
source; pytest was NOT run against this file or any other (per task
instructions, the full suite runs once at the end by someone else).

Covers: the create_dispute push-notification failure swallow (104-105),
admin_get_disputes end-to-end including the batch user/ride enrichment
join and its "user missing" / "ride missing" fallbacks (151-189), and
admin_resolve_dispute's not-found / already-resolved / refund-exceeds-
fare / no-payment-intent (manual refund) / Stripe-not-configured /
Stripe-exception / resolved-notification-failure branches
(201, 204, 210, 224-228, 235, 257-259, 310-311).

Lines 17-22 (the `try: from .. import ...` package-relative import branch
in the dual-import block) are intentionally left uncovered here, matching
every other route coverage file in this suite (grepped: none of them
attempt to flip that branch via sys.modules/importlib.reload tricks). The
only way to exercise it is to import the module for the first time in a
context where relative imports resolve, which depends on process-wide
import state set up before this file loads and can't be forced safely
from inside a single test module without risking corrupting other tests'
already-cached `routes.disputes` / `backend.routes.disputes` module
objects (and FastAPI router double-registration). Not attempted.

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 3):
`admin_resolve_dispute`'s rider-notification wording previously compared
`req.resolution == "refund"` — never a valid value (the documented set
is `approved | partial_refund | rejected`) — so the notification always
said "reviewed" regardless of outcome. Now maps `approved`/`partial_refund`
-> "approved" and `rejected` -> "rejected", via an explicit dict lookup.
`test_resolve_notification_wording_bug_pinned` below now asserts the
corrected wording.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.routes.disputes import (
    CreateDisputeRequest,
    ResolveDisputeRequest,
    admin_get_disputes,
    admin_resolve_dispute,
    create_dispute,
)

pytestmark = pytest.mark.anyio

RIDER = {"id": "user_1", "role": "rider"}
ADMIN = {"id": "admin_1", "email": "ops@spinr.ca", "role": "admin"}

RIDE_ROW = {
    "id": "ride_1",
    "rider_id": "user_1",
    "status": "completed",
    "total_fare": "25.50",
}

DISPUTE_ROW = {
    "id": "disp_1",
    "ride_id": "ride_1",
    "user_id": "user_1",
    "status": "open",
    "original_fare": 50.00,
}


def _patch_disputes(**overrides):
    """Patch the `backend.routes.disputes` module's dependencies.

    Defaults are the "everything succeeds, nothing found" shape; pass
    kwargs to override individual targets, mirroring the sibling
    test_dispute_refund_cents.py pattern in this directory.
    """
    defaults = {
        "backend.routes.disputes.db_supabase.get_rows": AsyncMock(return_value=[]),
        "backend.routes.disputes.db_supabase.get_ride": AsyncMock(return_value=None),
        "backend.routes.disputes.db_supabase.insert_one": AsyncMock(return_value={"id": "new"}),
        "backend.routes.disputes.db_supabase.update_one": AsyncMock(return_value={"id": "disp_1"}),
        "backend.routes.disputes.send_push_notification": AsyncMock(),
        "backend.routes.disputes.log_admin_action": AsyncMock(),
        "backend.routes.disputes.get_app_settings": AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
        "backend.routes.disputes.create_ticket_for_dispute": AsyncMock(),
    }
    defaults.update(overrides)
    patchers = [patch(target, obj) for target, obj in defaults.items()]
    return patchers


class _MultiPatch:
    """Small helper to apply a list of patch() objects as one context manager."""

    def __init__(self, patchers):
        self._patchers = patchers

    def __enter__(self):
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()


# ──────────────────────────── create_dispute: notification failure ────────────────────────────


async def test_create_dispute_notification_failure_does_not_fail_request():
    """Lines 104-105: send_push_notification raising must be swallowed
    (logged at debug) — the dispute is already persisted by that point."""
    with _MultiPatch(
        _patch_disputes(
            **{
                "backend.routes.disputes.db_supabase.get_ride": AsyncMock(return_value=dict(RIDE_ROW)),
                "backend.routes.disputes.db_supabase.get_rows": AsyncMock(return_value=[]),
                "backend.routes.disputes.send_push_notification": AsyncMock(side_effect=RuntimeError("push down")),
            }
        )
    ):
        req = CreateDisputeRequest(ride_id="ride_1", reason="overcharged", description="too high")
        result = await create_dispute(req=req, current_user=dict(RIDER))
    assert result["success"] is True
    assert result["dispute"]["status"] == "open"


async def test_create_dispute_notification_targets_rider_app():
    """N10 batch 2: the created-dispute push must pass target_app="rider"
    (fcm_token_rider) rather than falling through to the legacy fcm_token
    column — the dispute filer is always a rider account."""
    push = AsyncMock()
    with _MultiPatch(
        _patch_disputes(
            **{
                "backend.routes.disputes.db_supabase.get_ride": AsyncMock(return_value=dict(RIDE_ROW)),
                "backend.routes.disputes.db_supabase.get_rows": AsyncMock(return_value=[]),
                "backend.routes.disputes.send_push_notification": push,
            }
        )
    ):
        req = CreateDisputeRequest(ride_id="ride_1", reason="overcharged", description="too high")
        await create_dispute(req=req, current_user=dict(RIDER))
    push.assert_awaited_once()
    assert push.await_args.kwargs["target_app"] == "rider"


# ──────────────────────────── admin_get_disputes ────────────────────────────


async def test_admin_get_disputes_enriches_user_and_ride():
    """Lines 151-189: full happy path — status filter applied, user_ids/
    ride_ids batched, user name + ride status/fare joined in."""
    dispute = {"id": "disp_1", "user_id": "user_1", "ride_id": "ride_1", "status": "open"}
    user = {"id": "user_1", "first_name": "Pat", "last_name": "Rider"}
    ride = {"id": "ride_1", "status": "completed", "total_fare": 25.5}

    async def fake_get_rows(table, filters=None, **kwargs):
        if table == "disputes":
            assert filters == {"status": "open"}
            return [dispute]
        if table == "users":
            return [user]
        if table == "rides":
            return [ride]
        raise AssertionError(f"unexpected table {table}")

    with _MultiPatch(
        _patch_disputes(**{"backend.routes.disputes.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows)})
    ):
        result = await admin_get_disputes(status="open", limit=50, offset=0, current_admin=dict(ADMIN))

    assert len(result) == 1
    assert result[0]["user_name"] == "Pat Rider"
    assert result[0]["ride_status"] == "completed"
    assert result[0]["ride_fare"] == 25.5


async def test_admin_get_disputes_no_status_filter_and_missing_user_ride():
    """No `status` kwarg -> filters stays {} (still exercises the branch
    that skips `filters["status"] = status`). Also covers the "user not
    found" -> "Unknown" and "ride not found" -> None fallbacks."""
    dispute = {"id": "disp_2", "user_id": "ghost_user", "ride_id": "ghost_ride", "status": "open"}

    async def fake_get_rows(table, filters=None, **kwargs):
        if table == "disputes":
            assert filters == {}
            return [dispute]
        # users/rides lookups for ghost_user/ghost_ride return nothing
        return []

    with _MultiPatch(
        _patch_disputes(**{"backend.routes.disputes.db_supabase.get_rows": AsyncMock(side_effect=fake_get_rows)})
    ):
        result = await admin_get_disputes(status=None, limit=50, offset=0, current_admin=dict(ADMIN))

    assert len(result) == 1
    assert result[0]["user_name"] == "Unknown"
    assert result[0]["ride_status"] is None
    assert result[0]["ride_fare"] is None


async def test_admin_get_disputes_empty_list_skips_user_ride_queries():
    """No disputes at all -> user_ids/ride_ids are empty -> the `if
    user_ids else []` / `if ride_ids else []` short-circuits fire and
    users/rides queries are never issued."""
    get_rows = AsyncMock(return_value=[])
    with _MultiPatch(_patch_disputes(**{"backend.routes.disputes.db_supabase.get_rows": get_rows})):
        result = await admin_get_disputes(status=None, limit=50, offset=0, current_admin=dict(ADMIN))

    assert result == []
    # Only the "disputes" query should have run — users/rides short-circuited.
    get_rows.assert_awaited_once()


# ──────────────────────────── admin_resolve_dispute ────────────────────────────


async def _resolve(req, dispute=None, ride=None, get_rows=None, extra_patches=None):
    overrides = {
        "backend.routes.disputes.db_supabase.get_rows": get_rows
        or AsyncMock(return_value=[dispute] if dispute else []),
        "backend.routes.disputes.db_supabase.get_ride": AsyncMock(return_value=ride),
    }
    if extra_patches:
        overrides.update(extra_patches)
    with _MultiPatch(_patch_disputes(**overrides)):
        return await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(ADMIN))


async def test_resolve_dispute_not_found():
    """Line 201."""
    req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
    with pytest.raises(Exception) as exc_info:
        await _resolve(req, dispute=None)
    assert getattr(exc_info.value, "status_code", None) == 404


async def test_resolve_dispute_already_resolved():
    """Line 204."""
    dispute = {**DISPUTE_ROW, "status": "resolved"}
    req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
    with pytest.raises(Exception) as exc_info:
        await _resolve(req, dispute=dispute)
    assert exc_info.value.status_code == 400
    assert "already resolved" in exc_info.value.detail


async def test_resolve_dispute_already_rejected_also_blocked():
    """Line 204's other truthy branch value ('rejected')."""
    dispute = {**DISPUTE_ROW, "status": "rejected"}
    req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
    with pytest.raises(Exception) as exc_info:
        await _resolve(req, dispute=dispute)
    assert exc_info.value.status_code == 400


async def test_resolve_dispute_refund_exceeds_original_fare():
    """Line 210: refund_amount > original_fare -> 400, no Stripe call."""
    dispute = {**DISPUTE_ROW, "original_fare": 50.00}
    req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("75.00"))
    with pytest.raises(Exception) as exc_info:
        await _resolve(req, dispute=dispute)
    assert exc_info.value.status_code == 400
    assert "exceeds original fare" in exc_info.value.detail


async def test_resolve_dispute_no_payment_intent_marks_manual_required():
    """Lines 224-228: ride has no stripe_charge_id/payment_intent_id ->
    logged and refund_result becomes manual_required, no Stripe call, but
    the dispute is still marked resolved."""
    dispute = dict(DISPUTE_ROW)
    ride = {"id": "ride_1"}  # no stripe_charge_id / payment_intent_id
    req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
    update_one = AsyncMock(return_value={"id": "disp_1"})
    with patch("stripe.Refund.create") as stripe_create:
        result = await _resolve(
            req,
            dispute=dispute,
            ride=ride,
            extra_patches={"backend.routes.disputes.db_supabase.update_one": update_one},
        )
    stripe_create.assert_not_called()
    assert result["success"] is True
    assert result["refund"] == {"status": "manual_required", "reason": "no_payment_intent"}
    saved = update_one.call_args.args[2]
    assert saved["status"] == "resolved"
    assert saved["refund_result"] == {"status": "manual_required", "reason": "no_payment_intent"}


async def test_resolve_dispute_stripe_not_configured():
    """Line 235: payment_intent_id present but no stripe_secret_key in
    app_settings -> 503, before any Stripe API call."""
    dispute = dict(DISPUTE_ROW)
    ride = {"id": "ride_1", "stripe_charge_id": "pi_123"}
    req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
    with pytest.raises(Exception) as exc_info:
        await _resolve(
            req,
            dispute=dispute,
            ride=ride,
            extra_patches={
                "backend.routes.disputes.get_app_settings": AsyncMock(return_value={"stripe_secret_key": ""})
            },
        )
    assert exc_info.value.status_code == 503
    assert "Stripe not configured" in exc_info.value.detail


async def test_resolve_dispute_stripe_refund_exception_raises_502():
    """Lines 257-259: Stripe.Refund.create raising -> logged as error and
    re-raised as a 502 so the admin can retry with the same idempotency key."""
    dispute = dict(DISPUTE_ROW)
    ride = {"id": "ride_1", "stripe_charge_id": "pi_123"}
    req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
    with (
        patch("stripe.Refund.create", side_effect=RuntimeError("card network down")),
        pytest.raises(Exception) as exc_info,
    ):
        await _resolve(req, dispute=dispute, ride=ride)
    assert exc_info.value.status_code == 502
    assert "retry to reuse idempotency key" in exc_info.value.detail


async def test_resolve_dispute_rejected_resolution_skips_stripe_and_sets_rejected_status():
    """resolution == 'rejected' -> refund block is skipped entirely
    (condition on line 216 requires resolution in approved/partial_refund),
    status becomes 'rejected', refund_amount defaults to 0."""
    dispute = dict(DISPUTE_ROW)
    req = ResolveDisputeRequest(resolution="rejected", admin_note="not eligible")
    update_one = AsyncMock(return_value={"id": "disp_1"})
    with patch("stripe.Refund.create") as stripe_create:
        result = await _resolve(
            req,
            dispute=dispute,
            ride=None,
            extra_patches={"backend.routes.disputes.db_supabase.update_one": update_one},
        )
    stripe_create.assert_not_called()
    assert result["success"] is True
    assert result["refund"] is None
    saved = update_one.call_args.args[2]
    assert saved["status"] == "rejected"
    assert saved["refund_amount"] == 0


async def test_resolve_dispute_notification_failure_does_not_fail_request():
    """Lines 310-311: send_push_notification raising during the resolved
    notification is swallowed — the resolution is already persisted."""
    dispute = dict(DISPUTE_ROW)
    ride = {"id": "ride_1", "stripe_charge_id": "pi_123"}
    req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))

    def fake_refund_create(**kwargs):
        refund = MagicMock()
        refund.status = "succeeded"
        refund.id = "re_1"
        return refund

    with patch("stripe.Refund.create", side_effect=fake_refund_create):
        result = await _resolve(
            req,
            dispute=dispute,
            ride=ride,
            extra_patches={
                "backend.routes.disputes.send_push_notification": AsyncMock(side_effect=RuntimeError("push down")),
            },
        )
    assert result["success"] is True
    assert result["refund"]["refund_id"] == "re_1"


async def test_resolve_notification_wording_bug_pinned():
    """Fixed (2026-08-03, see module docstring): the notification body now
    correctly says 'approved' for both `approved` and `partial_refund`
    resolutions, instead of always saying 'reviewed'."""
    dispute = dict(DISPUTE_ROW)
    ride = {"id": "ride_1", "stripe_charge_id": "pi_123"}
    req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
    captured = {}

    async def fake_push(user_id, title, body, data=None, **kwargs):
        captured["title"] = title
        captured["body"] = body
        captured["target_app"] = kwargs.get("target_app")

    def fake_refund_create(**kwargs):
        refund = MagicMock()
        refund.status = "succeeded"
        refund.id = "re_2"
        return refund

    with patch("stripe.Refund.create", side_effect=fake_refund_create):
        result = await _resolve(
            req,
            dispute=dispute,
            ride=ride,
            extra_patches={"backend.routes.disputes.send_push_notification": AsyncMock(side_effect=fake_push)},
        )

    assert result["success"] is True
    assert captured["body"] == "Your dispute has been approved. A refund of $10.00 has been issued."
    assert captured["target_app"] == "rider"


async def test_resolve_notification_wording_rejected():
    """A `rejected` resolution now says 'rejected', not the old generic
    'reviewed' fallback."""
    dispute = dict(DISPUTE_ROW)
    req = ResolveDisputeRequest(resolution="rejected")
    captured = {}

    async def fake_push(user_id, title, body, data=None, **kwargs):
        captured["body"] = body

    result = await _resolve(
        req,
        dispute=dispute,
        extra_patches={"backend.routes.disputes.send_push_notification": AsyncMock(side_effect=fake_push)},
    )

    assert result["success"] is True
    assert captured["body"] == "Your dispute has been rejected."


async def test_resolve_dispute_no_rider_id_skips_notification():
    """dispute.get('user_id') falsy -> the notification block is skipped
    entirely (covers the `if rider_id:` False path)."""
    dispute = {**DISPUTE_ROW, "user_id": None}
    req = ResolveDisputeRequest(resolution="rejected")
    push = AsyncMock()
    result = await _resolve(
        req,
        dispute=dispute,
        ride=None,
        extra_patches={"backend.routes.disputes.send_push_notification": push},
    )
    assert result["success"] is True
    push.assert_not_awaited()
