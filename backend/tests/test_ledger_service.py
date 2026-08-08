"""Ledger durability + double-entry tests.

Two behaviours are under test, matching the two defects this module fixed:

1. The financial_events write used to be silently best-effort — one failed
   INSERT and the 7-year CRA/SK tax record lost a real Stripe charge with
   nothing but a log line. It must now retry, treat a duplicate-key as success
   (the retry-after-lost-response case), and escalate loudly when exhausted —
   while still never raising, because the money has already moved.

2. The ledger was single-entry. Legs must balance, must never be written
   half-formed, and must stay OFF until the app_settings flag is flipped.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import ledger_service as ls

# ── Leg builders ─────────────────────────────────────────────────────


def test_charge_legs_balance():
    # $20.00 charge: $15.00 driver, $2.20 tax, $2.80 platform
    legs = ls.build_charge_legs(total_cents=2000, driver_cents=1500, tax_cents=220)
    ls.assert_balanced(legs)

    by_acct = {(leg.account, leg.side): leg.amount_cents for leg in legs}
    assert by_acct[(ls.ACCT_STRIPE_RECEIVABLE, ls.DEBIT)] == 2000
    assert by_acct[(ls.ACCT_DRIVER_PAYABLE, ls.CREDIT)] == 1500
    assert by_acct[(ls.ACCT_TAX_PAYABLE, ls.CREDIT)] == 220
    assert by_acct[(ls.ACCT_PLATFORM_REVENUE, ls.CREDIT)] == 280


def test_charge_legs_omit_zero_value_accounts():
    """A $0 tax leg would violate the DB CHECK (amount_cents > 0)."""
    legs = ls.build_charge_legs(total_cents=1000, driver_cents=1000, tax_cents=0)
    ls.assert_balanced(legs)
    assert all(leg.amount_cents > 0 for leg in legs)
    assert not any(leg.account == ls.ACCT_TAX_PAYABLE for leg in legs)
    assert not any(leg.account == ls.ACCT_PLATFORM_REVENUE for leg in legs)


def test_charge_legs_refuse_inconsistent_amounts():
    """driver + tax exceeding the total must produce NO legs, not a negative one."""
    assert ls.build_charge_legs(total_cents=1000, driver_cents=900, tax_cents=500) == []


def test_charge_legs_zero_total_produces_nothing():
    assert ls.build_charge_legs(total_cents=0, driver_cents=0, tax_cents=0) == []


def test_charge_legs_promo_larger_than_fees_still_balances():
    """REGRESSION: a promo bigger than the fee floor used to refuse all legs.

    driver_earnings is derived pre-discount (total_fare - admin_earnings) while
    the rider pays post-discount, so without the promo debit the residual is
    negative and build_charge_legs bails — which collapsed the entire charge
    into a degraded platform_revenue entry and paged on every promo ride.

    $20.00 fare (incl. $2.50 booking fee), $1.00 tax, $5.00 promo
      -> rider is charged $16.00, driver is owed $17.50.
    """
    legs = ls.build_charge_legs(total_cents=1600, driver_cents=1750, tax_cents=100, promo_cents=500)
    ls.assert_balanced(legs)

    by_acct = {(leg.account, leg.side): leg.amount_cents for leg in legs}
    assert by_acct[(ls.ACCT_STRIPE_RECEIVABLE, ls.DEBIT)] == 1600
    assert by_acct[(ls.ACCT_PROMO_EXPENSE, ls.DEBIT)] == 500
    assert by_acct[(ls.ACCT_DRIVER_PAYABLE, ls.CREDIT)] == 1750
    assert by_acct[(ls.ACCT_TAX_PAYABLE, ls.CREDIT)] == 100
    # Residual is the fee floor (booking + airport + area fees), NOT netted
    # down by the promo — the platform earns fees gross and expenses the
    # discount separately.
    assert by_acct[(ls.ACCT_PLATFORM_REVENUE, ls.CREDIT)] == 250


def test_charge_legs_small_promo_does_not_hide_in_platform_revenue():
    """A promo under the fee floor balanced before this change, but silently
    netted the discount out of platform_revenue. promo_expense exists so promo
    spend is visible; the residual must stay gross either way."""
    legs = ls.build_charge_legs(total_cents=1900, driver_cents=1750, tax_cents=100, promo_cents=200)
    ls.assert_balanced(legs)
    by_acct = {(leg.account, leg.side): leg.amount_cents for leg in legs}
    assert by_acct[(ls.ACCT_PROMO_EXPENSE, ls.DEBIT)] == 200
    assert by_acct[(ls.ACCT_PLATFORM_REVENUE, ls.CREDIT)] == 250


def test_charge_legs_promo_defaults_to_zero_and_omits_the_leg():
    """Callers that predate promo_cents keep their exact previous output."""
    assert ls.build_charge_legs(total_cents=2000, driver_cents=1500, tax_cents=220) == ls.build_charge_legs(
        total_cents=2000, driver_cents=1500, tax_cents=220, promo_cents=0
    )
    legs = ls.build_charge_legs(total_cents=2000, driver_cents=1500, tax_cents=220, promo_cents=0)
    assert not any(leg.account == ls.ACCT_PROMO_EXPENSE for leg in legs)


def test_charge_legs_refuse_negative_promo():
    """A negative discount is upstream corruption — refuse, never invert a leg."""
    assert ls.build_charge_legs(total_cents=2000, driver_cents=1500, tax_cents=220, promo_cents=-100) == []


def test_charge_legs_still_refuse_when_promo_cannot_cover_the_gap():
    """The consistency guard survives: promo shifts the threshold, it does not
    remove it. driver+tax exceeding total+promo still produces NO legs."""
    assert ls.build_charge_legs(total_cents=1000, driver_cents=900, tax_cents=500, promo_cents=100) == []


def test_refund_legs_balance_and_spare_the_driver():
    """Driver keeps their pay on a refund — the platform absorbs it."""
    legs = ls.build_refund_legs(refund_cents=2000, tax_reversed_cents=220)
    ls.assert_balanced(legs)

    by_acct = {(leg.account, leg.side): leg.amount_cents for leg in legs}
    assert by_acct[(ls.ACCT_STRIPE_RECEIVABLE, ls.CREDIT)] == 2000
    assert by_acct[(ls.ACCT_TAX_PAYABLE, ls.DEBIT)] == 220
    assert by_acct[(ls.ACCT_PLATFORM_REVENUE, ls.DEBIT)] == 1780
    assert not any(leg.account == ls.ACCT_DRIVER_PAYABLE for leg in legs), "driver_payable must be untouched"


def test_refund_legs_clamp_tax_over_refund():
    """Bad upstream tax must not create a negative platform leg."""
    legs = ls.build_refund_legs(refund_cents=500, tax_reversed_cents=900)
    ls.assert_balanced(legs)
    assert all(leg.amount_cents > 0 for leg in legs)


def test_assert_balanced_rejects_imbalance():
    with pytest.raises(ls.UnbalancedLedgerError):
        ls.assert_balanced([ls.Leg(ls.ACCT_STRIPE_RECEIVABLE, ls.DEBIT, 100)])


def test_assert_balanced_rejects_unknown_account():
    with pytest.raises(ls.UnbalancedLedgerError):
        ls.assert_balanced(
            [
                ls.Leg("not_a_real_account", ls.DEBIT, 100),
                ls.Leg(ls.ACCT_PLATFORM_REVENUE, ls.CREDIT, 100),
            ]
        )


def test_to_cents_uses_decimal_not_float():
    # 0.1 + 0.2 style drift would show up here as 1114 or 1116.
    assert ls.to_cents(Decimal("11.15")) == 1115
    assert ls.to_cents("11.15") == 1115
    assert ls.to_cents(None) == 0


# ── Durability ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_header_write_retries_then_succeeds():
    calls = {"n": 0}

    async def flaky(_table, _row):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("connection reset")
        return {}

    with (
        patch.object(ls.db_supabase, "insert_one", side_effect=flaky),
        patch.object(ls, "_INSERT_BACKOFF_SECONDS", (0, 0)),
    ):
        event_id = await ls.record_event(
            event_type="stripe_charge", user_id="u1", ride_id="r1", delta_cents=2000, ref="pi_1"
        )

    assert calls["n"] == 3, "must retry up to the attempt budget"
    assert event_id is not None, "a recovered write still returns the event id"


@pytest.mark.anyio
async def test_duplicate_key_counts_as_written():
    """A retry after a lost response hits the PK we supplied — that is success."""

    async def dup(_table, _row):
        raise RuntimeError("duplicate key value violates unique constraint (23505)")

    with patch.object(ls.db_supabase, "insert_one", side_effect=dup):
        event_id = await ls.record_event(event_type="stripe_charge", user_id="u1", ride_id="r1", delta_cents=2000)

    assert event_id is not None, "duplicate key must not be reported as a lost row"


@pytest.mark.anyio
async def test_exhausted_retries_escalate_but_never_raise():
    """The charge already settled — bookkeeping failure must not fail the request."""

    async def always_fail(_table, _row):
        raise RuntimeError("db down")

    with (
        patch.object(ls.db_supabase, "insert_one", side_effect=always_fail),
        patch.object(ls, "_INSERT_BACKOFF_SECONDS", (0, 0)),
        patch.object(ls, "escalate") as escalate,
    ):
        event_id = await ls.record_event(
            event_type="stripe_charge", user_id="u1", ride_id="r1", delta_cents=2000, ref="pi_x"
        )

    assert event_id is None, "a lost row must be reported as lost"
    assert escalate.called, "exhausted retries must page, not just log"
    ctx = escalate.call_args.args[1]
    assert ctx["ride_id"] == "r1" and ctx["delta_cents"] == 2000
    # A lost header is a hole in the 7-year tax record — it must NOT share an
    # alert tag with the lower-severity leg failures.
    assert escalate.call_args.kwargs.get("alert", ls.ALERT_HEADER_LOST) == ls.ALERT_HEADER_LOST


@pytest.mark.anyio
async def test_escalation_context_carries_no_pii():
    """PIPEDA: Sentry may carry IDs and amounts, never names/addresses/phones."""

    async def always_fail(_table, _row):
        raise RuntimeError("db down")

    with (
        patch.object(ls.db_supabase, "insert_one", side_effect=always_fail),
        patch.object(ls, "_INSERT_BACKOFF_SECONDS", (0, 0)),
        patch.object(ls, "escalate") as escalate,
    ):
        await ls.record_event(
            event_type="stripe_charge",
            user_id="u1",
            ride_id="r1",
            delta_cents=2000,
            metadata={"pickup_address": "Saskatoon", "driver_id": "d1"},
        )

    ctx = escalate.call_args.args[1]
    assert "metadata" not in ctx, "raw metadata must not be shipped to Sentry"
    assert set(ctx) <= {"event_type", "ride_id", "user_id", "delta_cents", "ref"}


# ── Flag gating ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_legs_not_written_when_flag_off():
    headers, batches = [], []

    async def cap_one(table, row):
        headers.append((table, row))
        return {}

    async def cap_many(table, rows):
        batches.append((table, rows))
        return []

    with (
        patch.object(ls.db_supabase, "insert_one", side_effect=cap_one),
        patch.object(ls.db_supabase, "insert_many", side_effect=cap_many),
        patch.object(ls, "double_entry_enabled", AsyncMock(return_value=False)),
    ):
        await ls.record_event(
            event_type="stripe_charge",
            user_id="u1",
            ride_id="r1",
            delta_cents=2000,
            legs=ls.build_charge_legs(2000, 1500, 220),
        )

    assert [t for t, _ in headers] == ["financial_events"]
    assert batches == [], "flag off must write no legs at all"


@pytest.mark.anyio
async def test_legs_written_when_flag_on_and_balance():
    headers, batches = [], []

    async def cap_one(table, row):
        headers.append((table, row))
        return {}

    async def cap_many(table, rows):
        batches.append((table, rows))
        return []

    with (
        patch.object(ls.db_supabase, "insert_one", side_effect=cap_one),
        patch.object(ls.db_supabase, "insert_many", side_effect=cap_many),
        patch.object(ls, "double_entry_enabled", AsyncMock(return_value=True)),
    ):
        await ls.record_event(
            event_type="stripe_charge",
            user_id="u1",
            ride_id="r1",
            delta_cents=2000,
            legs=ls.build_charge_legs(2000, 1500, 220),
        )

    assert len(batches) == 1, "all legs must go in ONE statement — never a per-row loop"
    table, legs = batches[0]
    assert table == "financial_event_entries"
    assert len(legs) == 4
    debits = sum(r["amount_cents"] for r in legs if r["side"] == "debit")
    credits = sum(r["amount_cents"] for r in legs if r["side"] == "credit")
    assert debits == credits == 2000
    assert all(r["amount_cents"] > 0 for r in legs), "DB CHECK requires positive amounts"
    assert {r["event_id"] for r in legs} == {headers[0][1]["id"]}, "legs must hang off the header id"


@pytest.mark.anyio
async def test_unbalanced_legs_are_skipped_not_half_written():
    headers, batches = [], []

    async def cap_one(table, row):
        headers.append((table, row))
        return {}

    async def cap_many(table, rows):
        batches.append((table, rows))
        return []

    with (
        patch.object(ls.db_supabase, "insert_one", side_effect=cap_one),
        patch.object(ls.db_supabase, "insert_many", side_effect=cap_many),
        patch.object(ls, "double_entry_enabled", AsyncMock(return_value=True)),
        patch.object(ls, "escalate") as escalate,
    ):
        await ls.record_event(
            event_type="stripe_charge",
            user_id="u1",
            ride_id="r1",
            delta_cents=2000,
            legs=[ls.Leg(ls.ACCT_STRIPE_RECEIVABLE, ls.DEBIT, 2000)],  # no counter-leg
        )

    assert [t for t, _ in headers] == ["financial_events"]
    assert batches == [], "a half-formed journal entry must not be written"
    assert escalate.called, "an unbalanced entry is a code defect and must surface"
    assert escalate.call_args.kwargs["alert"] == ls.ALERT_LEGS_UNBALANCED


@pytest.mark.anyio
async def test_header_still_written_when_legs_fail():
    """The header is the tax record — a leg failure must not take it down."""
    headers = []

    async def cap_one(table, row):
        headers.append((table, row))
        return {}

    async def fail_many(_table, _rows):
        raise RuntimeError("entries table missing")

    with (
        patch.object(ls.db_supabase, "insert_one", side_effect=cap_one),
        patch.object(ls.db_supabase, "insert_many", side_effect=fail_many),
        patch.object(ls, "double_entry_enabled", AsyncMock(return_value=True)),
        patch.object(ls, "_INSERT_BACKOFF_SECONDS", (0, 0)),
        patch.object(ls, "escalate") as escalate,
    ):
        event_id = await ls.record_event(
            event_type="stripe_charge",
            user_id="u1",
            ride_id="r1",
            delta_cents=2000,
            legs=ls.build_charge_legs(2000, 1500, 220),
        )

    assert event_id is not None, "the tax record must survive a leg failure"
    assert [t for t, _ in headers] == ["financial_events"]
    assert escalate.called, "lost legs must still surface"
    assert escalate.call_args.kwargs["alert"] == ls.ALERT_LEGS_LOST, (
        "lost legs must be distinguishable from a lost tax record"
    )


# ── Charge metadata (7-year tax record) ──────────────────────────────


def test_charge_metadata_captures_tax_for_the_7_year_record():
    """The ride row is hard-deleted at 7 years (purge Step B) while this ledger
    row is retained. Without tax_amount/tax_breakdown copied here, the surviving
    record for an aged charge would be an undifferentiated delta_cents — a gap
    the refund path never had (it captures tax_reversed)."""
    from backend.services.payment_service import _charge_event_metadata

    ride = {
        "total_fare": "18.00",
        "grand_total": "20.20",
        "tax_amount": "2.20",
        "tax_breakdown": {"GST": {"amount": 1.0}, "PST": {"amount": 1.2}},
        "driver_id": "driver_1",
        "payment_method": "card",
        "pickup_address": "1742 Main Street, Saskatoon, SK, S7K 3A1",
    }
    meta = _charge_event_metadata(ride, Decimal("2.00"))

    assert meta["tax_amount"] == "2.20"
    assert meta["tax_breakdown"] == {"GST": {"amount": 1.0}, "PST": {"amount": 1.2}}
    # PIPEDA: the address must still be city-only, not the full street address.
    assert meta["pickup_address"] == "Saskatoon"


def test_charge_metadata_tax_defaults_to_zero_not_missing():
    """A legacy/comp ride without tax must still produce a readable field."""
    from backend.services.payment_service import _charge_event_metadata

    meta = _charge_event_metadata({"total_fare": "0.00"}, None)
    assert meta["tax_amount"] == "0.00"
    assert meta["tax_breakdown"] == {}


def test_charge_metadata_empty_without_ride():
    from backend.services.payment_service import _charge_event_metadata

    assert _charge_event_metadata(None, None) == {"source": "process_payment"}
