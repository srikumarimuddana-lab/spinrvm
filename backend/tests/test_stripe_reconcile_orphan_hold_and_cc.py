"""Tests for the 2026-09-23 additions to utils/stripe_reconcile.py.

  * STRIPE_ORPHAN_HOLD: an uncaptured (requires_capture) PI with no ride row,
    scanned over an 8-day lookback rather than yesterday.
  * CANCELLED_CAPTURED_UNREFUNDED: the read-only scheduled dry run of
    scripts/reconcile_cancelled_captured_refunds.py. It must never refund.
  * Domain tagging on the pre-existing discrepancy log lines.
  * Parity with the script: the duplicated fee rule and outcome classification
    must not drift from the human-run script's own.

All Stripe/Supabase access is mocked. See
docs/change-log/2026-09-23-orphaned-hold-and-detection-loop.md.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import utils.stripe_reconcile as sr

_OLD = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()


def _stripe_with(daily: list, holds: list) -> MagicMock:
    """Stripe mock: 1st PaymentIntent.list = the daily window, 2nd = the hold scan."""
    mock = MagicMock()
    results = []
    for pis in (daily, holds):
        r = MagicMock()
        r.auto_paging_iter.return_value = iter(pis)
        results.append(r)
    mock.PaymentIntent.list.side_effect = results
    return mock


def _hold(pi_id: str, status: str = "requires_capture", scope: str | None = None, ride_id: str = "r_meta") -> dict:
    meta = {"ride_id": ride_id, "source": "ride_booking_authorization"}
    if scope:
        meta["scope"] = scope
    return {"id": pi_id, "status": status, "amount_capturable": 3150, "metadata": meta, "created": 1}


def _cc_ride(**over) -> dict:
    r = {
        "id": "ride_1",
        "status": "cancelled",
        "auth_status": "captured",
        "payment_intent_id": "pi_1",
        "refund_amount": None,
        "cancellation_fee_admin": None,
        "cancellation_fee_driver": None,
        "cancel_fee_payment_intent_id": None,
        "payment_status": "pending",
        "updated_at": _OLD,
    }
    r.update(over)
    return r


# ── STRIPE_ORPHAN_HOLD ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.anyio
async def test_orphan_hold_flags_only_unlinked_requires_capture():
    stripe_mock = MagicMock()
    r = MagicMock()
    r.auto_paging_iter.return_value = iter(
        [_hold("pi_orphan"), _hold("pi_linked"), _hold("pi_topup", scope="wallet_topup")]
    )
    stripe_mock.PaymentIntent.list.return_value = r
    db_mock = AsyncMock()
    db_mock.get_rows_batched_in.return_value = [{"id": "ride_x", "payment_intent_id": "pi_linked"}]
    with patch.object(sr, "db_supabase", db_mock), patch.object(sr, "metrics") as m:
        out = await sr._reconcile_orphan_holds(stripe_mock)

    assert [d["payment_intent_id"] for d in out] == ["pi_orphan"]
    assert out[0]["type"] == "STRIPE_ORPHAN_HOLD"
    assert out[0]["amount_capturable"] == 3150
    assert out[0]["metadata_ride_id"] == "r_meta"
    # Linkage is checked against ALL rides by payment_intent_id, not the
    # paid-yesterday subset (a live searching ride's hold must not flag).
    assert db_mock.get_rows_batched_in.await_args.args == ("rides", "payment_intent_id", ["pi_orphan", "pi_linked"])
    m.inc.assert_called_once_with("spinr_payment_stripe_orphan_hold_total", by=1)


@pytest.mark.unit
@pytest.mark.anyio
async def test_orphan_hold_ignores_other_statuses():
    stripe_mock = MagicMock()
    r = MagicMock()
    r.auto_paging_iter.return_value = iter(
        [_hold("pi_s", status="succeeded"), _hold("pi_c", status="canceled"), _hold("pi_a", status="requires_action")]
    )
    stripe_mock.PaymentIntent.list.return_value = r
    db_mock = AsyncMock()
    with patch.object(sr, "db_supabase", db_mock), patch.object(sr, "metrics"):
        out = await sr._reconcile_orphan_holds(stripe_mock)
    assert out == []
    assert db_mock.get_rows_batched_in.await_args.args[2] == []


@pytest.mark.unit
@pytest.mark.anyio
async def test_orphan_hold_window_is_8_day_lookback_with_1h_grace():
    stripe_mock = MagicMock()
    r = MagicMock()
    r.auto_paging_iter.return_value = iter([])
    stripe_mock.PaymentIntent.list.return_value = r
    now = datetime.now(timezone.utc).timestamp()
    with patch.object(sr, "db_supabase", AsyncMock()), patch.object(sr, "metrics"):
        await sr._reconcile_orphan_holds(stripe_mock)
    created = stripe_mock.PaymentIntent.list.call_args.kwargs["created"]
    assert abs(created["gte"] - (now - 8 * 86400)) < 60
    assert abs(created["lte"] - (now - 3600)) < 60


@pytest.mark.unit
@pytest.mark.anyio
async def test_orphan_hold_db_failure_is_unknown_not_all_orphans():
    stripe_mock = MagicMock()
    r = MagicMock()
    r.auto_paging_iter.return_value = iter([_hold("pi_1")])
    stripe_mock.PaymentIntent.list.return_value = r
    db_mock = AsyncMock()
    db_mock.get_rows_batched_in.side_effect = RuntimeError("DB down")
    with patch.object(sr, "db_supabase", db_mock), patch.object(sr, "metrics") as m:
        assert await sr._reconcile_orphan_holds(stripe_mock) is None
    # Only the check-failed counter moves; the orphan counter does not.
    m.inc.assert_called_once_with(sr._CHECK_FAILED_METRIC, {"check": "orphan_hold"})


@pytest.mark.unit
@pytest.mark.anyio
async def test_orphan_hold_stripe_failure_returns_none():
    stripe_mock = MagicMock()
    stripe_mock.PaymentIntent.list.side_effect = RuntimeError("Stripe 500")
    with patch.object(sr, "db_supabase", AsyncMock()), patch.object(sr, "metrics") as m:
        assert await sr._reconcile_orphan_holds(stripe_mock) is None
    m.inc.assert_called_once_with(sr._CHECK_FAILED_METRIC, {"check": "orphan_hold"})


@pytest.mark.unit
@pytest.mark.anyio
async def test_tick_reports_orphan_hold_and_cc_in_audit_summary():
    stripe_mock = _stripe_with([], [_hold("pi_orphan")])

    async def _get_rows(table, filters, **kw):
        if filters.get("status") == "cancelled":
            return [_cc_ride()] if kw.get("offset", 0) == 0 else []
        return []

    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = _get_rows
    db_mock.get_rows_batched_in.return_value = []
    db_mock.insert_one.return_value = {"id": "log1"}
    with (
        patch.object(sr, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch.object(sr, "db_supabase", db_mock),
        patch.object(sr, "_read_capture_state", AsyncMock(return_value={"captured_cents": 210, "refunded_cents": 0})),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        await sr._run_reconciliation_tick()

    details = db_mock.insert_one.call_args[0][1]["details"]
    assert details["stripe_orphan_holds"] == 1
    assert details["cancelled_captured_unrefunded"] == {"would_refund": 1}
    types = sorted(d["type"] for d in details["discrepancy_detail"])
    assert types == ["CANCELLED_CAPTURED_UNREFUNDED", "STRIPE_ORPHAN_HOLD"]
    # Detection only: nothing but the one audit_logs row is written.
    db_mock.update_one.assert_not_awaited()
    assert db_mock.insert_one.await_count == 1


# ── CANCELLED_CAPTURED_UNREFUNDED ──────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.anyio
async def test_cc_detector_filters_like_the_script_and_never_writes():
    rows = [
        _cc_ride(id="keep"),
        _cc_ride(id="refunded", refund_amount="2.10"),
        _cc_ride(id="no_pi", payment_intent_id=None),
        _cc_ride(id="completed", status="completed"),  # blanket-mock guard
        _cc_ride(id="open_hold", auth_status="authorized"),
        _cc_ride(id="too_fresh", updated_at=datetime.now(timezone.utc).isoformat()),
    ]
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = rows
    read = AsyncMock(return_value={"captured_cents": 210, "refunded_cents": 0})
    with (
        patch.object(sr, "db_supabase", db_mock),
        patch.object(sr, "_read_capture_state", read),
        patch.object(sr, "metrics"),
    ):
        counts, flagged = await sr._detect_cancelled_captured()

    assert [c.kwargs["ride_id"] for c in read.await_args_list] == ["keep"]
    assert counts == {"would_refund": 1}
    assert flagged == [
        {
            "type": "CANCELLED_CAPTURED_UNREFUNDED",
            "ride_id": "keep",
            "payment_intent_id": "pi_1",
            "outcome": "would_refund",
        }
    ]
    filters = db_mock.get_rows.await_args.args[1]
    assert filters["status"] == "cancelled" and filters["auth_status"] == "captured"
    # Bounded window: see _CANCELLED_CAPTURED_LOOKBACK.
    since = datetime.fromisoformat(filters["cancelled_at"]["$gte"])
    assert abs((datetime.now(timezone.utc) - since) - timedelta(days=14)) < timedelta(minutes=1)
    assert db_mock.get_rows.await_args.kwargs["order"] == "id"
    db_mock.update_one.assert_not_awaited()
    db_mock.insert_one.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_cc_detector_never_touches_the_refund_path():
    """The background tick must never reach refund_excess_capture."""
    import utils.stripe_charge as sc

    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [_cc_ride()]
    boom = AsyncMock(side_effect=AssertionError("refund issued from a background loop"))
    with (
        patch.object(sr, "db_supabase", db_mock),
        patch.object(sc, "read_capture_state", AsyncMock(return_value={"captured_cents": 500, "refunded_cents": 0})),
        patch.object(sc, "refund_excess_capture", boom),
        patch.object(sr, "metrics"),
    ):
        counts, _ = await sr._detect_cancelled_captured()
    assert counts == {"would_refund": 1}
    boom.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize(
    "state,ride_over,expected,alert",
    [
        (None, {}, "unknown", True),
        (
            {"captured_cents": 210, "refunded_cents": 0, "pending_refund_cents": 210},
            {},
            "pending_refund_on_stripe",
            False,
        ),
        ({"captured_cents": 210, "refunded_cents": 210}, {}, "already_refunded_on_stripe", True),
        ({"captured_cents": 0, "refunded_cents": 0}, {}, "captured_nothing", True),
        ({"captured_cents": 210, "refunded_cents": 0}, {"cancellation_fee_admin": "2.10"}, "not_needed", False),
        ({"captured_cents": 500, "refunded_cents": 0}, {"cancellation_fee_admin": "1.00"}, "would_refund", True),
    ],
)
async def test_cc_detector_outcomes_and_metrics(state, ride_over, expected, alert):
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = [_cc_ride(**ride_over)]
    with (
        patch.object(sr, "db_supabase", db_mock),
        patch.object(sr, "_read_capture_state", AsyncMock(return_value=state)),
        patch.object(sr, "metrics") as m,
    ):
        counts, flagged = await sr._detect_cancelled_captured()
    assert counts == {expected: 1}
    assert bool(flagged) is alert
    incs = {c.args[1]["outcome"]: c.kwargs["by"] for c in m.inc.call_args_list}
    assert set(incs) == set(sr._CC_ALERT_OUTCOMES)  # every series emitted, 0 when clean
    assert incs.get(expected, 0) == (1 if alert else 0)


@pytest.mark.unit
@pytest.mark.anyio
async def test_cc_detector_pages_past_already_refunded_rows():
    """Refunded rides keep matching the query, so a full page must not end the scan."""
    page_size = sr._CANCELLED_CAPTURED_PAGE
    full = [_cc_ride(id=f"done{i}", refund_amount="1.00") for i in range(page_size)]
    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = [full, [_cc_ride(id="late")]]
    read = AsyncMock(return_value={"captured_cents": 210, "refunded_cents": 0})
    with (
        patch.object(sr, "db_supabase", db_mock),
        patch.object(sr, "_read_capture_state", read),
        patch.object(sr, "metrics"),
    ):
        counts, flagged = await sr._detect_cancelled_captured()
    assert [f["ride_id"] for f in flagged] == ["late"]
    assert db_mock.get_rows.await_args_list[1].kwargs["offset"] == page_size


@pytest.mark.unit
@pytest.mark.anyio
async def test_cc_detector_reports_truncation_at_ceiling():
    page = [_cc_ride(id="x", refund_amount="1.00")] * sr._CANCELLED_CAPTURED_PAGE
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = page
    with patch.object(sr, "db_supabase", db_mock), patch.object(sr, "metrics") as m:
        counts, _ = await sr._detect_cancelled_captured()
    assert counts.get("scan_truncated") == 1
    # 10 pages + the one-row probe past the ceiling.
    assert db_mock.get_rows.await_count == sr._CANCELLED_CAPTURED_MAX_PAGES + 1
    m.inc.assert_any_call(sr._CHECK_FAILED_METRIC, {"check": "cancelled_captured"})


@pytest.mark.unit
@pytest.mark.anyio
async def test_cc_detector_exact_ceiling_is_not_truncation():
    page = [_cc_ride(id="x", refund_amount="1.00")] * sr._CANCELLED_CAPTURED_PAGE
    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = [page] * sr._CANCELLED_CAPTURED_MAX_PAGES + [[]]
    with patch.object(sr, "db_supabase", db_mock), patch.object(sr, "metrics"):
        counts, _ = await sr._detect_cancelled_captured()
    assert "scan_truncated" not in counts


@pytest.mark.unit
@pytest.mark.anyio
async def test_cc_detector_one_bad_row_is_unknown_and_does_not_stop_the_rest():
    rows = [_cc_ride(id="bad", cancellation_fee_admin="not-a-number"), _cc_ride(id="good")]
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = rows
    read = AsyncMock(return_value={"captured_cents": 210, "refunded_cents": 0})
    with (
        patch.object(sr, "db_supabase", db_mock),
        patch.object(sr, "_read_capture_state", read),
        patch.object(sr, "metrics"),
    ):
        counts, flagged = await sr._detect_cancelled_captured()
    assert counts == {"unknown": 1, "would_refund": 1}
    assert {f["ride_id"]: f["outcome"] for f in flagged} == {"bad": "unknown", "good": "would_refund"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_backfill_skips_current_state_checks():
    stripe_mock = _stripe_with([], [_hold("pi_orphan")])
    db_mock = AsyncMock()
    db_mock.get_rows.return_value = []
    db_mock.insert_one.return_value = {"id": "log1"}
    detect = AsyncMock(return_value=({}, []))
    with (
        patch.object(sr, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch.object(sr, "db_supabase", db_mock),
        patch.object(sr, "_detect_cancelled_captured", detect),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        await sr._run_reconciliation_tick(target_date=datetime.now(timezone.utc).date() - timedelta(days=3))
    details = db_mock.insert_one.call_args[0][1]["details"]
    assert details["stripe_orphan_holds"] == "skipped_backfill"
    assert details["cancelled_captured_unrefunded"] == "skipped_backfill"
    assert stripe_mock.PaymentIntent.list.call_count == 1
    detect.assert_not_awaited()


@pytest.mark.unit
def test_alert_series_are_preregistered_at_zero():
    """increase() ignores a series' first sample, so every series must exist at 0 before a finding."""
    text = sr.metrics.render_prometheus()
    for o in sr._CC_ALERT_OUTCOMES:
        assert f'spinr_payment_cancelled_captured_unrefunded_total{{outcome="{o}"' in text
    assert "spinr_payment_stripe_orphan_hold_total" in text
    for c in sr._CHECK_NAMES:
        assert f'spinr_payment_reconcile_check_failed_total{{check="{c}"' in text


@pytest.mark.unit
@pytest.mark.anyio
async def test_cc_detector_db_failure_returns_none():
    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = RuntimeError("DB down")
    with patch.object(sr, "db_supabase", db_mock), patch.object(sr, "metrics") as m:
        assert await sr._detect_cancelled_captured() == (None, [])
    m.inc.assert_called_once_with(sr._CHECK_FAILED_METRIC, {"check": "cancelled_captured"})


# ── Domain tagging on existing discrepancy lines ───────────────────────────


@pytest.mark.unit
@pytest.mark.anyio
async def test_discrepancy_logs_carry_payments_domain(caplog):
    paid = {
        "id": "ride_p",
        "payment_intent_id": "pi_gone",
        "grand_total": "25.00",
        "total_fare": "25.00",
        "tip_amount": "0",
        "status": "completed",
        "payment_status": "paid",
        "ride_completed_at": datetime.combine(
            (datetime.now(timezone.utc) - timedelta(days=1)).date(), datetime.min.time(), tzinfo=timezone.utc
        )
        .replace(hour=12)
        .isoformat(),
    }
    stripe_mock = _stripe_with([{"id": "pi_orph", "status": "succeeded", "amount_received": 100, "metadata": {}}], [])

    async def _get_rows(table, filters, **kw):
        return [paid] if "ride_completed_at" in filters else []

    db_mock = AsyncMock()
    db_mock.get_rows.side_effect = _get_rows
    with (
        caplog.at_level(logging.ERROR, logger=sr.logger.name),
        patch.object(sr, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch.object(sr, "db_supabase", db_mock),
        patch.dict(sys.modules, {"stripe": stripe_mock}),
    ):
        await sr._run_reconciliation_tick()

    by_type = {}
    for rec in caplog.records:
        for t in ("DB_PAID_STRIPE_MISSING", "STRIPE_ORPHAN ", "COMPLETE with"):
            if t in rec.getMessage():
                by_type[t] = rec
    assert set(by_type) == {"DB_PAID_STRIPE_MISSING", "STRIPE_ORPHAN ", "COMPLETE with"}
    assert all(getattr(r, "domain", None) == "payments" for r in by_type.values())
    assert by_type["DB_PAID_STRIPE_MISSING"].ride_id == "ride_p"


# ── Parity with the human-run script ───────────────────────────────────────

_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "reconcile_cancelled_captured_refunds.py",
)


def _load_script():
    saved = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("_rccr_parity", _SCRIPT_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path[:] = saved


_FEE_CASES = [
    {},
    {"cancellation_fee_admin": "1.00", "cancellation_fee_driver": "0.50"},
    # fee collected on a DIFFERENT, paid PI -> 0 owed from the capture
    {"cancellation_fee_admin": "5.00", "cancel_fee_payment_intent_id": "pi_fee", "payment_status": "paid"},
    # same PI (partial-capture path) -> still owed
    {"cancellation_fee_admin": "5.00", "cancel_fee_payment_intent_id": "pi_1", "payment_status": "paid"},
    # different PI but charge failed -> still owed
    {"cancellation_fee_admin": "5.00", "cancel_fee_payment_intent_id": "pi_fee", "payment_status": "failed"},
    {"cancellation_fee_driver": "0.005"},
]


@pytest.mark.unit
def test_cc_fee_rule_matches_script():
    script = _load_script()
    import utils.money as money

    with patch.dict(sys.modules, {"utils.money": money}):
        for over in _FEE_CASES:
            ride = _cc_ride(**over)
            expected = money.dollars_to_cents(script._fee_still_owed_from_capture(ride))
            assert sr._cc_fee_owed_cents(ride) == expected, over


@pytest.mark.unit
@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [
        None,
        {"captured_cents": 210, "refunded_cents": 0, "pending_refund_cents": 5},
        {"captured_cents": 210, "refunded_cents": 10},
        {"captured_cents": 0, "refunded_cents": 0},
        {"captured_cents": 500, "refunded_cents": 0},
        {"captured_cents": 150, "refunded_cents": 0},
    ],
)
@pytest.mark.parametrize("over", _FEE_CASES)
async def test_cc_classification_matches_script_dry_run(state, over):
    """Same ride + Stripe state -> the scheduled detector and the script's dry run agree."""
    script = _load_script()
    import utils.money as money
    import utils.stripe_charge as sc

    ride = _cc_ride(**over)
    with (
        patch.dict(sys.modules, {"utils.money": money, "utils.stripe_charge": sc}),
        patch.object(sc, "read_capture_state", AsyncMock(return_value=state)),
        patch.object(sc, "refund_excess_capture", AsyncMock(side_effect=AssertionError)),
        patch.dict(sys.modules, {"services.payment_service": MagicMock()}),
    ):
        script_outcome = await script.reconcile_one(ride, apply_changes=False)
    assert sr._classify_cc(ride, state) == script_outcome


@pytest.mark.unit
def test_cc_fee_is_decimal_not_float():
    # 0.1 + 0.2 in float is 0.30000000000000004 -> would round to 30 either way,
    # but 1.005 is the classic float trap; Decimal HALF_UP gives 101.
    assert sr._cc_fee_owed_cents(_cc_ride(cancellation_fee_admin="1.005")) == sr.dollars_to_cents(Decimal("1.005"))
