"""Atomic card-settlement finalizer — flag matrix and recovery contracts.

_finalize_card_settlement must guarantee exactly ONE financial_events header
per settlement across every path:

- flag off → byte-compatible legacy sequence (record_payment_event, then the
  money update_ride) — the production default today
- flag on → the settle_ride_card_payment RPC owns both writes in one
  transaction; the Python-side header/update are SKIPPED
- RPC absent (migration 288 not applied) → legacy fallback, warned
- ambiguous RPC error → re-read decides; an unverifiable state returns the
  503 "confirmation failed" shape rather than risking a duplicate header
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.repositories import ledger_repo
from backend.services import ledger_service as ls
from backend.services import payment_service as ps

RIDE_ID = "ride_atomic_1"
RIDER_ID = "rider_atomic_1"
PI = "pi_atomic_1"


def _ride(**overrides) -> dict:
    base = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": "driver_1",
        "total_fare": "18.00",
        "grand_total": "20.00",
        "tax_amount": "2.20",
        "driver_earnings": "15.00",
        "tip_amount": 0,
        "payment_method": "card",
        "pickup_address": "1742 Main Street, Saskatoon, SK",
        "dropoff_address": "88 Elm Drive, Regina, SK",
    }
    base.update(overrides)
    return base


def _finalize(**overrides):
    kwargs = dict(
        ride=_ride(),
        ride_id=RIDE_ID,
        rider_id=RIDER_ID,
        settled_amount=Decimal("20.00"),
        payment_intent_id=PI,
        tip_collected=Decimal("0"),
        auth_status="captured",
    )
    kwargs.update(overrides)
    return ps._finalize_card_settlement(**kwargs)


@pytest.mark.anyio
async def test_flag_off_runs_legacy_sequence():
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=False)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock()) as rpc,
        patch.object(ps, "record_payment_event", AsyncMock()) as rec,
        patch.object(ps.db_supabase, "update_ride", AsyncMock()) as upd,
        patch.object(ps.manager, "send_personal_message", AsyncMock()) as ws,
    ):
        result = await _finalize()

    rpc.assert_not_awaited()
    rec.assert_awaited_once()
    assert rec.call_args.kwargs["amount_cents"] == 2000
    upd.assert_awaited_once()
    written = upd.call_args.args[1]
    assert written["payment_status"] == "paid"
    assert written["payment_intent_id"] == PI
    assert written["auth_status"] == "captured"
    ws.assert_awaited_once()
    assert result.success is True and result.charged_amount == "20.00"


@pytest.mark.anyio
async def test_flag_on_rpc_owns_both_writes():
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock(return_value="evt_1")) as rpc,
        patch.object(ps, "record_payment_event", AsyncMock()) as rec,
        patch.object(ps.db_supabase, "update_ride", AsyncMock()) as upd,
        patch.object(ps.manager, "send_personal_message", AsyncMock()) as ws,
    ):
        result = await _finalize(tip_collected=Decimal("2.00"), settled_amount=Decimal("22.00"))

    rpc.assert_awaited_once()
    k = rpc.call_args.kwargs
    assert k["amount_cents"] == 2200
    assert k["tip_amount"] == Decimal("2.00")
    assert k["auth_status"] == "captured"
    assert k["metadata"]["source"] == "process_payment"
    # PIPEDA: metadata must carry city-only addresses, same as the legacy path.
    assert k["metadata"]["pickup_address"] == "Saskatoon"
    rec.assert_not_awaited(), "the RPC wrote the header — a Python header would be a duplicate"
    upd.assert_not_awaited(), "the RPC wrote the money fields"
    ws.assert_awaited_once()
    assert result.success is True


_EXTRAS = {"payment_method_id": "pm_new", "card_brand": None, "card_last4": None}


@pytest.mark.anyio
async def test_flag_on_display_extras_written_once_when_healthy():
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock(return_value="evt_1")),
        patch.object(ps, "record_payment_event", AsyncMock()),
        patch.object(ps.db_supabase, "update_ride", AsyncMock()) as upd,
        patch.object(ps.manager, "send_personal_message", AsyncMock()),
    ):
        result = await _finalize(extra_ride_fields=_EXTRAS)

    upd.assert_awaited_once_with(RIDE_ID, _EXTRAS)
    assert result.success is True


@pytest.mark.anyio
async def test_flag_on_display_extras_retry_then_succeed():
    """On the legacy path these fields ride the same update_ride as the paid
    flip. Under the RPC they are the one thing that lost atomicity, so a
    transient blip must not be the end of it."""
    calls = {"n": 0}

    async def flaky(_ride_id, _fields):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("db blip")
        return {}

    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock(return_value="evt_1")),
        patch.object(ps, "record_payment_event", AsyncMock()),
        patch.object(ps.db_supabase, "update_ride", side_effect=flaky),
        patch.object(ps, "_DISPLAY_FOLLOWUP_BACKOFF_SECONDS", (0, 0)),
        patch.object(ps.ledger_service, "escalate") as escalate,
        patch.object(ps.manager, "send_personal_message", AsyncMock()),
    ):
        result = await _finalize(extra_ride_fields=_EXTRAS)

    assert calls["n"] == 3, "must retry up to the attempt budget"
    escalate.assert_not_called(), "a recovered write is not an incident"
    assert result.success is True


@pytest.mark.anyio
async def test_flag_on_display_extras_exhausted_escalates_but_still_succeeds():
    """The stale-card state must become KNOWN — a bare log meant nobody
    learned the admin view was pointing at the rejected card. It still cannot
    fail a settled payment."""
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock(return_value="evt_1")),
        patch.object(ps, "record_payment_event", AsyncMock()),
        patch.object(ps.db_supabase, "update_ride", AsyncMock(side_effect=RuntimeError("db down"))) as upd,
        patch.object(ps, "_DISPLAY_FOLLOWUP_BACKOFF_SECONDS", (0, 0)),
        patch.object(ps.ledger_service, "escalate") as escalate,
        patch.object(ps.manager, "send_personal_message", AsyncMock()) as ws,
    ):
        result = await _finalize(extra_ride_fields=_EXTRAS)

    assert upd.await_count == ps._DISPLAY_FOLLOWUP_ATTEMPTS
    escalate.assert_called_once()
    assert escalate.call_args.kwargs["alert"] == ps.ALERT_CARD_DISPLAY_STALE
    # PIPEDA: the escalation context carries field NAMES, never card values.
    ctx = escalate.call_args[0][1]
    assert ctx["ride_id"] == RIDE_ID
    assert ctx["fields"] == sorted(_EXTRAS.keys())
    assert "pm_new" not in str(ctx)
    ws.assert_awaited_once(), "the rider is still told the payment completed"
    assert result.success is True, "display-only follow-up failure cannot fail a settled payment"


@pytest.mark.anyio
async def test_flag_on_already_paid_skips_side_effects():
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock(return_value=None)),
        patch.object(ps, "record_payment_event", AsyncMock()) as rec,
        patch.object(ps.manager, "send_personal_message", AsyncMock()) as ws,
    ):
        result = await _finalize()

    assert result.success is True and result.already_paid is True
    rec.assert_not_awaited()
    ws.assert_not_awaited(), "no money moved now — no duplicate payment_completed event"


@pytest.mark.anyio
async def test_rpc_unavailable_falls_back_to_legacy():
    """Partial deploy: flag flipped before migration 288 ran."""
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(
            ledger_repo,
            "settle_ride_card_payment",
            AsyncMock(side_effect=ledger_repo.SettleRpcUnavailable("PGRST202")),
        ),
        patch.object(ps, "record_payment_event", AsyncMock()) as rec,
        patch.object(ps.db_supabase, "update_ride", AsyncMock()) as upd,
        patch.object(ps.manager, "send_personal_message", AsyncMock()),
    ):
        result = await _finalize()

    rec.assert_awaited_once()
    upd.assert_awaited_once()
    assert result.success is True


@pytest.mark.anyio
async def test_ambiguous_error_committed_header_present():
    """Re-read says paid and the header is findable by ref → clean success,
    no second header."""
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock(side_effect=RuntimeError("timeout"))),
        patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=_ride(payment_status="paid"))),
        patch.object(ps.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "evt_1"}])),
        patch.object(ps, "record_payment_event", AsyncMock()) as rec,
        patch.object(ps.manager, "send_personal_message", AsyncMock()) as ws,
    ):
        result = await _finalize()

    rec.assert_not_awaited()
    ws.assert_awaited_once()
    assert result.success is True


@pytest.mark.anyio
async def test_ambiguous_error_committed_header_impossibly_missing_repairs():
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock(side_effect=RuntimeError("timeout"))),
        patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=_ride(payment_status="paid"))),
        patch.object(ps.db_supabase, "get_rows", AsyncMock(return_value=[])),
        patch.object(ps, "record_payment_event", AsyncMock()) as rec,
        patch.object(ps.manager, "send_personal_message", AsyncMock()),
    ):
        result = await _finalize()

    rec.assert_awaited_once(), "paid ride with no header is a tax-record hole — repair it"
    assert result.success is True


@pytest.mark.anyio
async def test_ambiguous_error_not_committed_runs_legacy():
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock(side_effect=RuntimeError("timeout"))),
        patch.object(ps.db_supabase, "get_ride", AsyncMock(return_value=_ride(payment_status="processing"))),
        patch.object(ps, "record_payment_event", AsyncMock()) as rec,
        patch.object(ps.db_supabase, "update_ride", AsyncMock()) as upd,
        patch.object(ps.manager, "send_personal_message", AsyncMock()),
    ):
        result = await _finalize()

    rec.assert_awaited_once()
    upd.assert_awaited_once()
    assert result.success is True


@pytest.mark.anyio
async def test_ambiguous_error_unverifiable_returns_503_never_double_writes():
    """RPC error + state re-read failure: if the RPC committed, a legacy run
    would write a second header (fresh event id — the paid-gate lives in the
    RPC). Surface the stuck state; retry/reconciliation own recovery."""
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=True)),
        patch.object(ledger_repo, "settle_ride_card_payment", AsyncMock(side_effect=RuntimeError("timeout"))),
        patch.object(ps.db_supabase, "get_ride", AsyncMock(side_effect=RuntimeError("db down"))),
        patch.object(ps, "record_payment_event", AsyncMock()) as rec,
        patch.object(ps.db_supabase, "update_ride", AsyncMock()) as upd,
        patch.object(ps.manager, "send_personal_message", AsyncMock()) as ws,
        patch.object(ls, "escalate") as escalate,
    ):
        result = await _finalize()

    rec.assert_not_awaited()
    upd.assert_not_awaited()
    ws.assert_not_awaited()
    assert result.success is False and result.status_code == 503
    # The rider is told "our team has been notified" — that must be backed by a
    # taggable page, not just a log line on-call has to grep for.
    escalate.assert_called_once()
    assert escalate.call_args.kwargs["alert"] == ls.ALERT_SETTLEMENT_UNVERIFIABLE
    ctx = escalate.call_args.args[1]
    assert ctx["ride_id"] == RIDE_ID and ctx["amount_cents"] == 2000


@pytest.mark.anyio
async def test_legacy_update_failure_still_returns_503():
    """Flag-off parity: header written, ride update fails → stuck-processing
    contract unchanged (financial_events row exists for recovery)."""
    with (
        patch.object(ps, "_atomic_settle_enabled", AsyncMock(return_value=False)),
        patch.object(ps, "record_payment_event", AsyncMock()) as rec,
        patch.object(ps.db_supabase, "update_ride", AsyncMock(side_effect=RuntimeError("db down"))),
        patch.object(ps.manager, "send_personal_message", AsyncMock()) as ws,
    ):
        result = await _finalize()

    rec.assert_awaited_once()
    ws.assert_not_awaited()
    assert result.success is False and result.status_code == 503


# ── wrapper error translation ────────────────────────────────────────


@pytest.mark.anyio
async def test_wrapper_translates_missing_function_to_unavailable():
    class _Res:
        data = "evt_1"

    calls = {}

    class _Rpc:
        def __init__(self, raise_missing):
            self.raise_missing = raise_missing

        def execute(self):
            if self.raise_missing:
                raise RuntimeError("PGRST202: Could not find the function settle_ride_card_payment")
            return _Res()

    class _Supabase:
        def __init__(self, raise_missing=False):
            self.raise_missing = raise_missing

        def rpc(self, name, params):
            calls["name"], calls["params"] = name, params
            return _Rpc(self.raise_missing)

    with patch.object(ledger_repo, "supabase", _Supabase(raise_missing=True)):
        with pytest.raises(ledger_repo.SettleRpcUnavailable):
            await ledger_repo.settle_ride_card_payment(
                ride_id=RIDE_ID,
                event_id="evt_1",
                user_id=RIDER_ID,
                amount_cents=2000,
                payment_intent_id=PI,
                tip_amount=Decimal("2.00"),
                metadata={},
            )

    with patch.object(ledger_repo, "supabase", _Supabase()):
        out = await ledger_repo.settle_ride_card_payment(
            ride_id=RIDE_ID,
            event_id="evt_1",
            user_id=RIDER_ID,
            amount_cents=2000,
            payment_intent_id=PI,
            tip_amount=Decimal("2.00"),
            metadata={},
        )

    assert out == "evt_1"
    assert calls["name"] == "settle_ride_card_payment"
    assert calls["params"]["p_tip_amount"] == "2.00", "Decimal must cross as str, never float"
    assert calls["params"]["p_amount_cents"] == 2000


@pytest.mark.anyio
async def test_wrapper_unconfigured_supabase_is_unavailable():
    with patch.object(ledger_repo, "supabase", None):
        with pytest.raises(ledger_repo.SettleRpcUnavailable):
            await ledger_repo.settle_ride_card_payment(
                ride_id=RIDE_ID,
                event_id="e",
                user_id=RIDER_ID,
                amount_cents=1,
                payment_intent_id=PI,
                tip_amount=Decimal("0"),
                metadata={},
            )
