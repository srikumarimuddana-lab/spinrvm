"""Coverage for the already-captured-hold refund backfill.

Two units under test, both fully mocked (no real Supabase, no real Stripe):

  * ``utils/stripe_charge.py::read_capture_state`` — the read-only Stripe probe
    that makes a re-run of the backfill safe past Stripe's 24h idempotency-key
    window (``amount_received`` does not drop when a refund is issued, so the
    intent's existing refunds have to be summed separately).
  * ``backend/scripts/reconcile_cancelled_captured_refunds.py`` — the
    operator-run script itself: candidate selection, the dry-run default, the
    three idempotency guards, and the never-refund-on-unknown-state rule.

Companion to test_refund_excess_capture.py / test_cancel_already_captured_refund.py,
which cover the live cancel path this backfill is the historical counterpart to.
See docs/change-log/2026-09-21-cancellation-captured-hold-refund-backfill.md.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.stripe_charge import read_capture_state

_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "reconcile_cancelled_captured_refunds.py",
)


def _load_script():
    """Import the script by path — backend/scripts is not an importable package here.

    ``sys.path`` is restored afterwards: the script prepends ``backend/`` to it at
    import time (so its own dual-import works when run as a CLI), and leaving that
    in place for the rest of the session would let ``import utils.x`` resolve to a
    second module object distinct from ``backend.utils.x`` — exactly the
    duplicate-binding hazard CLAUDE.md's "Patch target for DB" rule warns about.
    """
    saved_path = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("reconcile_cancelled_captured_refunds", _SCRIPT_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved_path


script = _load_script()


# --------------------------------------------------------------------------
# read_capture_state
# --------------------------------------------------------------------------


def _patch_stripe(*, amount_received=210, refunds=(), has_more=False, raises=None):
    mock_stripe = MagicMock()
    intent = MagicMock()
    intent.amount_received = amount_received
    mock_stripe.PaymentIntent.retrieve.return_value = intent
    if raises is not None:
        mock_stripe.PaymentIntent.retrieve.side_effect = raises
    listing = MagicMock()
    listing.data = list(refunds)
    listing.has_more = has_more
    mock_stripe.Refund.list.return_value = listing
    return patch("backend.utils.stripe_charge.stripe", mock_stripe), mock_stripe


def _refund(amount, status="succeeded"):
    r = MagicMock()
    r.amount = amount
    r.status = status
    r.id = "re_1"
    return r


def _patch_secret(secret="sk_test_xxx"):
    return patch(
        "backend.utils.stripe_charge.get_app_settings",
        AsyncMock(return_value={"stripe_secret_key": secret}),
    )


@pytest.mark.unit
@pytest.mark.anyio
async def test_read_capture_state_reports_captured_and_refunded():
    stripe_patch, _ = _patch_stripe(amount_received=254, refunds=[_refund(54)])
    with stripe_patch, _patch_secret():
        state = await read_capture_state(ride_id="r1", payment_intent_id="pi_1")
    assert state == {
        "captured_cents": 254,
        "refunded_cents": 54,
        "pending_refund_cents": 0,
        "succeeded_refund_ids": ["re_1"],
    }


@pytest.mark.unit
@pytest.mark.anyio
async def test_read_capture_state_separates_pending_from_succeeded_refunds():
    stripe_patch, _ = _patch_stripe(
        amount_received=500,
        refunds=[_refund(100, status="succeeded"), _refund(200, status="pending"),
                 _refund(50, status="requires_action")],
    )
    with stripe_patch, _patch_secret():
        state = await read_capture_state(ride_id="r1", payment_intent_id="pi_1")
    assert state["refunded_cents"] == 100
    assert state["pending_refund_cents"] == 250


@pytest.mark.unit
@pytest.mark.anyio
async def test_read_capture_state_ignores_failed_refunds():
    """A failed refund never left the account — counting it would understate what is owed."""
    stripe_patch, _ = _patch_stripe(amount_received=210, refunds=[_refund(210, status="failed")])
    with stripe_patch, _patch_secret():
        state = await read_capture_state(ride_id="r1", payment_intent_id="pi_1")
    assert state["refunded_cents"] == 0


@pytest.mark.unit
@pytest.mark.anyio
async def test_read_capture_state_unknown_on_extra_refund_pages():
    stripe_patch, _ = _patch_stripe(refunds=[_refund(10)], has_more=True)
    with stripe_patch, _patch_secret():
        assert await read_capture_state(ride_id="r1", payment_intent_id="pi_1") is None


@pytest.mark.unit
@pytest.mark.anyio
async def test_read_capture_state_unknown_on_stripe_error_and_missing_config():
    from backend.utils.stripe_charge import _StripeBaseError

    stripe_patch, _ = _patch_stripe(raises=_StripeBaseError("boom"))
    with stripe_patch, _patch_secret():
        assert await read_capture_state(ride_id="r1", payment_intent_id="pi_1") is None

    stripe_patch, _ = _patch_stripe()
    with stripe_patch, _patch_secret(secret=""):
        assert await read_capture_state(ride_id="r1", payment_intent_id="pi_1") is None

    assert await read_capture_state(ride_id="r1", payment_intent_id="") is None


# --------------------------------------------------------------------------
# the script
# --------------------------------------------------------------------------


def _ride(**over):
    base = {
        "id": "ride_1",
        "rider_id": "rider_1",
        "payment_intent_id": "pi_1",
        "status": "cancelled",
        "auth_status": "captured",
        "cancellation_fee_admin": 0,
        "cancellation_fee_driver": 0,
        "refund_amount": 0,
        "grand_total": "2.10",
        "tax_amount": "0.20",
    }
    base.update(over)
    return base


class _Deps:
    """Patches every module the script imports lazily inside its functions."""

    def __init__(
        self, *, state, refund_status="refunded", refund_amount=Decimal("2.10"), ledger_id="led_1", claimed=True
    ):
        self.db = MagicMock()
        self.read_capture_state = AsyncMock(return_value=state)
        outcome = MagicMock()
        outcome.status = refund_status
        outcome.charged_amount = refund_amount
        outcome.payment_intent_id = "pi_1"
        self.refund_excess_capture = AsyncMock(return_value=outcome)
        self.reconcile_confirmed_stripe_refund = AsyncMock(return_value={"outcome": "applied"})

    def __enter__(self):
        import backend.services.payment_service as ps
        import backend.utils.stripe_charge as sc

        self._patches = [
            patch.dict(
                "sys.modules",
                {
                    "db_supabase": self.db,
                    "services.payment_service": ps,
                    "utils.money": __import__("backend.utils.money", fromlist=["x"]),
                    "utils.stripe_charge": sc,
                },
            ),
            patch.object(sc, "read_capture_state", self.read_capture_state),
            patch.object(sc, "refund_excess_capture", self.refund_excess_capture),
            patch.object(ps, "reconcile_confirmed_stripe_refund", self.reconcile_confirmed_stripe_refund),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *a):
        for p in reversed(self._patches):
            p.stop()
        return False


@pytest.mark.unit
@pytest.mark.anyio
async def test_dry_run_never_refunds():
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0}) as d:
        result = await script.reconcile_one(_ride(), apply_changes=False)
    assert result == "would_refund"
    d.refund_excess_capture.assert_not_awaited()
    d.reconcile_confirmed_stripe_refund.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_zero_fee_refunds_the_full_capture_through_atomic_projection():
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0}) as d:
        result = await script.reconcile_one(_ride(), apply_changes=True)
    assert result == "refunded"
    assert d.refund_excess_capture.await_args.kwargs["fee_owed"] == Decimal("0")
    d.reconcile_confirmed_stripe_refund.assert_awaited_once_with(ride_id="ride_1", payment_intent_id="pi_1")


@pytest.mark.unit
@pytest.mark.anyio
async def test_partial_fee_is_kept_and_marks_partially_refunded():
    ride = _ride(cancellation_fee_admin="1.00", cancellation_fee_driver="0.50")
    with _Deps(state={"captured_cents": 500, "refunded_cents": 0}, refund_amount=Decimal("3.50")) as d:
        result = await script.reconcile_one(ride, apply_changes=True)
    assert result == "refunded"
    assert d.refund_excess_capture.await_args.kwargs["fee_owed"] == Decimal("1.50")
    d.reconcile_confirmed_stripe_refund.assert_awaited_once_with(ride_id="ride_1", payment_intent_id="pi_1")


@pytest.mark.unit
@pytest.mark.anyio
async def test_existing_stripe_refund_is_flagged_not_refunded_again():
    """The guard that makes a re-run safe past Stripe's 24h idempotency window."""
    with _Deps(state={"captured_cents": 210, "refunded_cents": 210}) as d:
        result = await script.reconcile_one(_ride(), apply_changes=True)
    assert result == "accounting_repaired"
    d.refund_excess_capture.assert_not_awaited()
    d.reconcile_confirmed_stripe_refund.assert_awaited_once_with(ride_id="ride_1", payment_intent_id="pi_1")


@pytest.mark.unit
@pytest.mark.anyio
async def test_pending_stripe_refund_is_reported_separately_and_never_duplicated():
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0, "pending_refund_cents": 210}) as d:
        result = await script.reconcile_one(_ride(), apply_changes=True)
    assert result == "pending_refund_on_stripe"
    d.refund_excess_capture.assert_not_awaited()
    d.reconcile_confirmed_stripe_refund.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_unknown_stripe_state_never_refunds():
    with _Deps(state=None) as d:
        result = await script.reconcile_one(_ride(), apply_changes=True)
    assert result == "unknown"
    d.refund_excess_capture.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_fee_covering_the_capture_needs_no_refund():
    ride = _ride(cancellation_fee_admin="2.10")
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0}) as d:
        result = await script.reconcile_one(ride, apply_changes=True)
    assert result == "not_needed"
    d.refund_excess_capture.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_failed_refund_is_reported_and_writes_nothing():
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0}, refund_status="failed") as d:
        result = await script.reconcile_one(_ride(), apply_changes=True)
    assert result == "failed"
    d.reconcile_confirmed_stripe_refund.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_atomic_projection_conflict_or_error_surfaces_as_ledger_failed():
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0}) as stale:
        stale.reconcile_confirmed_stripe_refund.return_value = {"outcome": "stale"}
        assert await script.reconcile_one(_ride(), apply_changes=True) == "ledger_failed"
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0}) as failed:
        failed.reconcile_confirmed_stripe_refund.side_effect = RuntimeError("RPC down")
        assert await script.reconcile_one(_ride(), apply_changes=True) == "ledger_failed"


@pytest.mark.unit
@pytest.mark.anyio
async def test_candidate_selection_filters_and_scopes():
    rows = [
        _ride(id="keep"),
        _ride(id="drop_refunded", refund_amount="2.10"),
        _ride(id="drop_no_pi", payment_intent_id=None),
    ]
    with _Deps(state=None) as d:
        d.db.get_rows = AsyncMock(return_value=rows)
        found, scanned = await script.find_candidates(limit=50, ride_ids=["keep", "drop_refunded"])
        filters = d.db.get_rows.await_args.args[1]
        kwargs = d.db.get_rows.await_args.kwargs

    assert [r["id"] for r in found] == ["keep"]
    # Pre-filter count: rides this script already refunded keep matching the
    # query, so paging must be judged on rows scanned, not candidates kept.
    assert scanned == 3
    assert filters["status"] == "cancelled"
    assert filters["auth_status"] == "captured"
    assert filters["id"] == {"$in": ["keep", "drop_refunded"]}
    # Unique ordering key: ties under a non-unique one can silently skip a row
    # between --offset pages.
    assert kwargs["order"] == "id"
    assert kwargs["offset"] == 0


@pytest.mark.unit
@pytest.mark.anyio
async def test_fee_already_charged_on_its_own_pi_is_not_withheld_again():
    """The historical population's fee was billed on a SECOND PaymentIntent.

    Subtracting it from the captured booking hold as well would withhold it
    twice — the rider would be short the fee amount on money they already paid.
    """
    ride = _ride(
        cancellation_fee_admin="5.00",
        cancel_fee_payment_intent_id="pi_fee_1",  # a DIFFERENT PI from payment_intent_id
        payment_status="paid",
        grand_total="30.00",
    )
    with _Deps(state={"captured_cents": 3000, "refunded_cents": 0}, refund_amount=Decimal("30.00")) as d:
        result = await script.reconcile_one(ride, apply_changes=True)

    assert result == "refunded"
    # Full capture refunded, fee NOT deducted a second time.
    assert d.refund_excess_capture.await_args.kwargs["fee_owed"] == Decimal("0")
    # The atomic projector derives the summary from the actual confirmed Stripe
    # aggregate; the script must not race it with a second summary write.
    d.db.update_one.assert_not_called()


@pytest.mark.unit
@pytest.mark.anyio
async def test_fee_captured_from_the_booking_hold_itself_is_never_handed_back():
    """The partial-capture path stamps cancel_fee_payment_intent_id with the BOOKING PI.

    Treating a merely-truthy column as "collected elsewhere" would refund the fee
    on every fee-bearing cancel — after the driver was already paid their share.
    """
    ride = _ride(
        cancellation_fee_admin="5.00",
        cancel_fee_payment_intent_id="pi_1",  # same PI as payment_intent_id
        payment_status="paid",
    )
    with _Deps(state={"captured_cents": 500, "refunded_cents": 0}) as d:
        result = await script.reconcile_one(ride, apply_changes=True)

    assert result == "not_needed"
    d.refund_excess_capture.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_failed_fee_charge_stays_deducted_from_the_capture():
    """cancel_fee_payment_intent_id is stamped on failed attempts too (e.g. requires_action)."""
    ride = _ride(
        cancellation_fee_admin="5.00",
        cancel_fee_payment_intent_id="pi_fee_1",
        payment_status="failed",
        grand_total="30.00",
    )
    with _Deps(state={"captured_cents": 3000, "refunded_cents": 0}, refund_amount=Decimal("25.00")) as d:
        result = await script.reconcile_one(ride, apply_changes=True)

    assert result == "refunded"
    # The unpaid fee is still owed, so it is collected out of the capture.
    assert d.refund_excess_capture.await_args.kwargs["fee_owed"] == Decimal("5.00")


@pytest.mark.unit
@pytest.mark.anyio
async def test_captured_nothing_is_surfaced_not_silently_skipped():
    with _Deps(state={"captured_cents": 0, "refunded_cents": 0}) as d:
        result = await script.reconcile_one(_ride(), apply_changes=True)
    assert result == "captured_nothing"
    d.refund_excess_capture.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_stripe_not_needed_and_unconfigured_are_not_reported_as_failures():
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0}, refund_status="not_needed") as d:
        assert await script.reconcile_one(_ride(), apply_changes=True) == "not_needed"
        d.reconcile_confirmed_stripe_refund.assert_not_awaited()
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0}, refund_status="unconfigured"):
        assert await script.reconcile_one(_ride(), apply_changes=True) == "unknown"


@pytest.mark.unit
@pytest.mark.anyio
async def test_atomic_projection_error_after_real_refund_surfaces_for_reconciliation():
    """Money already left the account — this must not be bucketed as 'still owed'."""
    with _Deps(state={"captured_cents": 210, "refunded_cents": 0}) as d:
        d.reconcile_confirmed_stripe_refund.side_effect = RuntimeError("RPC down")
        result = await script.reconcile_one(_ride(), apply_changes=True)

    assert result == "ledger_failed"
    d.reconcile_confirmed_stripe_refund.assert_awaited_once()
