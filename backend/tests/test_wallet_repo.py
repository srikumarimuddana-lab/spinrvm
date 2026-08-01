"""repositories/wallet_repo.py — wallet & Stripe repository unit tests.

Wallet & Stripe repository: atomic wallet RPCs (increment/credit/delta/pay/
transfer), promo-slot RPCs, fare-split, and Stripe webhook idempotency
helpers. Extracted from db_supabase.py (Phase 4 god-object decomposition).
No dedicated test file existed before this one — only indirect coverage via
route-level tests (routes/wallet.py etc.).

Money-adjacent: this module is the caller-facing wrapper around Postgres RPCs
that do the actual locking/idempotency (wallet_apply_delta, wallet_pay_for_ride,
fare_split_pay_share, ...). These tests pin two things per function: the
"Supabase client not configured" branch, and — per CLAUDE.md's "never
silently swallow a DB/payment error" rule — that a DB-layer failure actually
propagates as a raised exception (DatabaseError/ValueError/RuntimeError)
rather than being logged and swallowed, for every function except the two
explicitly documented as best-effort (release_promo_user_slot,
mark_stripe_event_processed) — see the note on the latter below.

Patch target: `repositories.wallet_repo.supabase` (the domain-module binding,
not `repositories._base.supabase`) — per CLAUDE.md's "Patch target for DB"
convention, since wallet_repo defines its own functions rather than
re-exporting _base's generic CRUD helpers.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_request_deadline():
    """Defend against a pre-existing test-pollution bug (NOT in wallet_repo.py):
    several tests in test_utils_extended.py's TestDeadline* class call
    `set_request_deadline(...)` directly and never reset the contextvar
    afterward (no `reset_token` cleanup) -- e.g. `test_deadline_exhausted_past`
    / `test_remaining_seconds_past_deadline` leak a permanently-PAST deadline
    into the shared contextvar. Because `contextvars.ContextVar` values set
    outside a task boundary persist for the rest of the OS thread, that leak
    reaches every later test in the same pytest process that calls
    `repositories._base.run_sync` (this module's every RPC helper does) --
    `run_sync`'s very first check (`_base.py`'s `remaining_seconds() <= 0`)
    then raises `ServiceUnavailableException` before ever touching the mock,
    which is exactly the failure this repo's full-suite run surfaced when
    this file was added (all 67 tests pass in isolation; ~53 fail when run
    after test_utils_extended.py in the same process). Not this file's bug
    to fix (out of scope for a wallet_repo-coverage PR to touch an unrelated
    deadline-propagation test file) -- reset the contextvar to a known-good
    `None` state for every test here instead, which also incidentally heals
    the leak for whatever runs after this file.
    """
    from backend.utils.deadline import set_request_deadline

    set_request_deadline(None)
    yield


def _rpc_mock(data):
    """Build a MagicMock supabase client whose `.rpc(...).execute()` returns `data`."""
    client = MagicMock()
    response = MagicMock()
    response.data = data
    client.rpc.return_value.execute.return_value = response
    return client


def _rpc_raises(exc: Exception):
    client = MagicMock()
    client.rpc.return_value.execute.side_effect = exc
    return client


# ─────────────────────────────────────────────────────────────────────────────
# wallet_increment_balance
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wallet_increment_balance_raises_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import wallet_increment_balance
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_increment_balance("w1", Decimal("5.00"))


@pytest.mark.asyncio
async def test_wallet_increment_balance_happy_path_returns_decimal():
    mock_sb = _rpc_mock("15.50")
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_increment_balance

        result = await wallet_increment_balance("w1", Decimal("5.00"))

    assert result == Decimal("15.50")
    args, kwargs = mock_sb.rpc.call_args
    assert args[0] == "wallet_increment_balance"
    assert args[1] == {"p_wallet_id": "w1", "p_amount": "5.00"}


@pytest.mark.asyncio
async def test_wallet_increment_balance_raises_when_no_data_returned():
    mock_sb = _rpc_mock(None)
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_increment_balance
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_increment_balance("w1", Decimal("5.00"))


@pytest.mark.asyncio
async def test_wallet_increment_balance_propagates_db_error():
    mock_sb = _rpc_raises(RuntimeError("connection refused"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_increment_balance
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_increment_balance("w1", Decimal("5.00"))


# ─────────────────────────────────────────────────────────────────────────────
# wallet_apply_credit
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wallet_apply_credit_raises_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import wallet_apply_credit
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_apply_credit(
                wallet_id="w1", user_id="u1", type_="promo", amount=Decimal("10.00"), reference_id="ref1"
            )


@pytest.mark.asyncio
async def test_wallet_apply_credit_happy_path_returns_first_row():
    row = {"transaction_id": "t1", "balance_after": "25.00", "deduped": False}
    mock_sb = _rpc_mock([row])
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_apply_credit

        result = await wallet_apply_credit(
            wallet_id="w1",
            user_id="u1",
            type_="promo",
            amount=Decimal("10.00"),
            reference_id="ref1",
            description="promo credit",
            metadata={"promo_id": "p1"},
        )

    assert result == row
    args, _ = mock_sb.rpc.call_args
    assert args[0] == "wallet_apply_credit"
    assert args[1]["p_amount"] == "10.00"
    assert args[1]["p_metadata"] == {"promo_id": "p1"}


@pytest.mark.asyncio
async def test_wallet_apply_credit_defaults_metadata_to_empty_dict():
    mock_sb = _rpc_mock([{"transaction_id": "t1", "balance_after": "0", "deduped": False}])
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_apply_credit

        await wallet_apply_credit(wallet_id="w1", user_id="u1", type_="promo", amount=Decimal("1"), reference_id=None)

    args, _ = mock_sb.rpc.call_args
    assert args[1]["p_metadata"] == {}
    assert args[1]["p_reference_id"] is None


@pytest.mark.asyncio
async def test_wallet_apply_credit_raises_when_no_row_returned():
    mock_sb = _rpc_mock([])
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_apply_credit
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_apply_credit(
                wallet_id="w1", user_id="u1", type_="promo", amount=Decimal("1"), reference_id="r"
            )


@pytest.mark.asyncio
async def test_wallet_apply_credit_propagates_db_error():
    mock_sb = _rpc_raises(RuntimeError("boom"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_apply_credit
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_apply_credit(
                wallet_id="w1", user_id="u1", type_="promo", amount=Decimal("1"), reference_id="r"
            )


# ─────────────────────────────────────────────────────────────────────────────
# wallet_apply_delta
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wallet_apply_delta_raises_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import wallet_apply_delta
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_apply_delta(
                wallet_id="w1", user_id="u1", type_="fee", delta=Decimal("-5.00"), reference_id="r1"
            )


@pytest.mark.asyncio
async def test_wallet_apply_delta_happy_path_with_floor_and_clamp():
    row = {"transaction_id": "t1", "balance_after": "0.00", "applied_delta": "-3.00", "deduped": False}
    mock_sb = _rpc_mock([row])
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_apply_delta

        result = await wallet_apply_delta(
            wallet_id="w1",
            user_id="u1",
            type_="cancellation_fee",
            delta=Decimal("-5.00"),
            reference_id="r1",
            floor=Decimal("0"),
            clamp_to_floor=True,
        )

    assert result == row
    args, _ = mock_sb.rpc.call_args
    assert args[0] == "wallet_apply_delta"
    assert args[1]["p_delta"] == "-5.00"
    assert args[1]["p_floor"] == "0"
    assert args[1]["p_clamp_to_floor"] is True


@pytest.mark.asyncio
async def test_wallet_apply_delta_without_floor_sends_none():
    mock_sb = _rpc_mock([{"transaction_id": "t1", "balance_after": "5", "applied_delta": "5", "deduped": False}])
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_apply_delta

        await wallet_apply_delta(wallet_id="w1", user_id="u1", type_="topup", delta=Decimal("5"), reference_id="r2")

    args, _ = mock_sb.rpc.call_args
    assert args[1]["p_floor"] is None
    assert args[1]["p_clamp_to_floor"] is False


@pytest.mark.asyncio
async def test_wallet_apply_delta_raises_when_no_row_returned():
    mock_sb = _rpc_mock([])
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_apply_delta
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_apply_delta(wallet_id="w1", user_id="u1", type_="fee", delta=Decimal("-1"), reference_id="r")


@pytest.mark.asyncio
async def test_wallet_apply_delta_propagates_db_error():
    mock_sb = _rpc_raises(RuntimeError("db down"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_apply_delta
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_apply_delta(wallet_id="w1", user_id="u1", type_="fee", delta=Decimal("-1"), reference_id="r")


# ─────────────────────────────────────────────────────────────────────────────
# wallet_pay_for_ride
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wallet_pay_for_ride_raises_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import wallet_pay_for_ride
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_pay_for_ride("w1", "ride1", Decimal("20.00"))


@pytest.mark.asyncio
async def test_wallet_pay_for_ride_happy_path_returns_new_balance():
    mock_sb = _rpc_mock("30.00")
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_pay_for_ride

        result = await wallet_pay_for_ride("w1", "ride1", Decimal("20.00"), Decimal("5.00"))

    assert result == Decimal("30.00")
    args, _ = mock_sb.rpc.call_args
    assert args[0] == "wallet_pay_for_ride"
    assert args[1]["p_tip_amount"] == "5.00"


@pytest.mark.asyncio
async def test_wallet_pay_for_ride_defaults_tip_to_zero():
    mock_sb = _rpc_mock("10.00")
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_pay_for_ride

        await wallet_pay_for_ride("w1", "ride1", Decimal("10.00"))

    args, _ = mock_sb.rpc.call_args
    assert args[1]["p_tip_amount"] == "0"


@pytest.mark.asyncio
async def test_wallet_pay_for_ride_returns_none_when_already_paid():
    mock_sb = _rpc_mock(None)
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_pay_for_ride

        result = await wallet_pay_for_ride("w1", "ride1", Decimal("10.00"))

    assert result is None


@pytest.mark.asyncio
async def test_wallet_pay_for_ride_insufficient_funds_raises_value_error():
    mock_sb = _rpc_raises(Exception("insufficient_funds: balance too low"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_pay_for_ride

        with pytest.raises(ValueError, match="insufficient_funds"):
            await wallet_pay_for_ride("w1", "ride1", Decimal("10.00"))


@pytest.mark.asyncio
async def test_wallet_pay_for_ride_wallet_not_found_raises_value_error():
    mock_sb = _rpc_raises(Exception("wallet not found for id w1"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_pay_for_ride

        with pytest.raises(ValueError, match="wallet_not_found"):
            await wallet_pay_for_ride("w1", "ride1", Decimal("10.00"))


@pytest.mark.asyncio
async def test_wallet_pay_for_ride_fare_underpaid_raises_value_error():
    mock_sb = _rpc_raises(Exception("fare_underpaid: amount mismatch"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_pay_for_ride

        with pytest.raises(ValueError, match="fare_underpaid"):
            await wallet_pay_for_ride("w1", "ride1", Decimal("10.00"))


@pytest.mark.asyncio
async def test_wallet_pay_for_ride_ride_not_payable_raises_value_error():
    mock_sb = _rpc_raises(Exception("ride_not_payable: wrong status"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_pay_for_ride

        with pytest.raises(ValueError, match="ride_not_payable"):
            await wallet_pay_for_ride("w1", "ride1", Decimal("10.00"))


@pytest.mark.asyncio
async def test_wallet_pay_for_ride_unknown_error_propagates_as_database_error():
    mock_sb = _rpc_raises(RuntimeError("some other unexpected failure"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_pay_for_ride
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_pay_for_ride("w1", "ride1", Decimal("10.00"))


# ─────────────────────────────────────────────────────────────────────────────
# wallet_transfer
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wallet_transfer_raises_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import wallet_transfer
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_transfer("s1", "r1", Decimal("10.00"))


@pytest.mark.asyncio
async def test_wallet_transfer_happy_path_list_row():
    mock_sb = _rpc_mock([{"sender_balance": "5.00", "recipient_balance": "15.00"}])
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_transfer

        sender_bal, recipient_bal = await wallet_transfer("s1", "r1", Decimal("10.00"))

    assert sender_bal == Decimal("5.00")
    assert recipient_bal == Decimal("15.00")


@pytest.mark.asyncio
async def test_wallet_transfer_happy_path_dict_row():
    mock_sb = _rpc_mock({"sender_balance": "1.00", "recipient_balance": "2.00"})
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_transfer

        sender_bal, recipient_bal = await wallet_transfer("s1", "r1", Decimal("1.00"))

    assert sender_bal == Decimal("1.00")
    assert recipient_bal == Decimal("2.00")


@pytest.mark.asyncio
async def test_wallet_transfer_insufficient_funds_raises_value_error():
    mock_sb = _rpc_raises(Exception("insufficient_funds"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_transfer

        with pytest.raises(ValueError, match="insufficient_funds"):
            await wallet_transfer("s1", "r1", Decimal("10.00"))


@pytest.mark.asyncio
async def test_wallet_transfer_wallet_not_found_raises_value_error():
    mock_sb = _rpc_raises(Exception("wallet not found"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_transfer

        with pytest.raises(ValueError, match="wallet_not_found"):
            await wallet_transfer("s1", "r1", Decimal("10.00"))


@pytest.mark.asyncio
async def test_wallet_transfer_unknown_error_propagates_as_database_error():
    mock_sb = _rpc_raises(RuntimeError("weird failure"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_transfer
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_transfer("s1", "r1", Decimal("10.00"))


@pytest.mark.asyncio
async def test_wallet_transfer_raises_when_no_data_returned():
    mock_sb = _rpc_mock(None)
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import wallet_transfer
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await wallet_transfer("s1", "r1", Decimal("10.00"))


# ─────────────────────────────────────────────────────────────────────────────
# increment_promo_uses
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_increment_promo_uses_raises_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import increment_promo_uses
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await increment_promo_uses("promo1", 100)


@pytest.mark.parametrize(
    "data,expected", [(True, True), (1, True), ([1], True), (False, False), (None, False), ([], False)]
)
@pytest.mark.asyncio
async def test_increment_promo_uses_branches(data, expected):
    mock_sb = _rpc_mock(data)
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import increment_promo_uses

        result = await increment_promo_uses("promo1", 100)

    assert result is expected
    args, _ = mock_sb.rpc.call_args
    assert args[0] == "increment_promo_uses"
    assert args[1] == {"p_promo_id": "promo1", "p_max_uses": 100}


@pytest.mark.asyncio
async def test_increment_promo_uses_propagates_db_error():
    mock_sb = _rpc_raises(RuntimeError("boom"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import increment_promo_uses
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await increment_promo_uses("promo1", 100)


# ─────────────────────────────────────────────────────────────────────────────
# claim_promo_user_slot
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_promo_user_slot_raises_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import claim_promo_user_slot
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await claim_promo_user_slot("promo1", "u1", 1)


@pytest.mark.parametrize(
    "data,expected",
    [
        (True, True),
        (1, True),
        ([True], True),
        ([1], True),
        (False, False),
        ([False], False),
        (None, False),
        ([], False),
    ],
)
@pytest.mark.asyncio
async def test_claim_promo_user_slot_branches(data, expected):
    mock_sb = _rpc_mock(data)
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import claim_promo_user_slot

        result = await claim_promo_user_slot("promo1", "u1", 1)

    assert result is expected
    args, _ = mock_sb.rpc.call_args
    assert args[1] == {"p_promo_id": "promo1", "p_user_id": "u1", "p_max_per_user": 1}


@pytest.mark.asyncio
async def test_claim_promo_user_slot_propagates_db_error():
    mock_sb = _rpc_raises(RuntimeError("boom"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import claim_promo_user_slot
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await claim_promo_user_slot("promo1", "u1", 1)


# ─────────────────────────────────────────────────────────────────────────────
# release_promo_user_slot
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_release_promo_user_slot_is_noop_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import release_promo_user_slot

        # Best-effort by design: no supabase configured -> silently returns,
        # does NOT raise (documented in the docstring as best-effort).
        assert await release_promo_user_slot("promo1", "u1") is None


@pytest.mark.asyncio
async def test_release_promo_user_slot_happy_path_calls_rpc():
    mock_sb = _rpc_mock(True)
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import release_promo_user_slot

        result = await release_promo_user_slot("promo1", "u1")

    assert result is None
    args, _ = mock_sb.rpc.call_args
    assert args[0] == "release_promo_user_slot"
    assert args[1] == {"p_promo_id": "promo1", "p_user_id": "u1"}


@pytest.mark.asyncio
async def test_release_promo_user_slot_propagates_db_error():
    """Unlike claim_stripe_event's best-effort helpers below, this one has NO
    try/except around run_sync -- a DB error here surfaces loudly rather than
    being swallowed, consistent with CLAUDE.md's error-handling rule."""
    mock_sb = _rpc_raises(RuntimeError("boom"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import release_promo_user_slot
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await release_promo_user_slot("promo1", "u1")


# ─────────────────────────────────────────────────────────────────────────────
# fare_split_pay_share
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fare_split_pay_share_raises_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import fare_split_pay_share
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await fare_split_pay_share("w1", "p1", Decimal("5.00"))


@pytest.mark.asyncio
async def test_fare_split_pay_share_happy_path_returns_decimal():
    mock_sb = _rpc_mock("12.34")
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import fare_split_pay_share

        result = await fare_split_pay_share("w1", "p1", Decimal("5.00"))

    assert result == Decimal("12.34")
    args, _ = mock_sb.rpc.call_args
    assert args[0] == "fare_split_pay_share"
    assert args[1]["p_amount"] == "5.00"


@pytest.mark.asyncio
async def test_fare_split_pay_share_raises_when_no_data_returned():
    mock_sb = _rpc_mock(None)
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import fare_split_pay_share
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await fare_split_pay_share("w1", "p1", Decimal("5.00"))


@pytest.mark.asyncio
async def test_fare_split_pay_share_insufficient_funds_raises_value_error():
    mock_sb = _rpc_raises(Exception("insufficient_funds"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import fare_split_pay_share

        with pytest.raises(ValueError, match="insufficient_funds"):
            await fare_split_pay_share("w1", "p1", Decimal("5.00"))


@pytest.mark.asyncio
async def test_fare_split_pay_share_unknown_error_propagates_as_database_error():
    mock_sb = _rpc_raises(RuntimeError("weird failure"))
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import fare_split_pay_share
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await fare_split_pay_share("w1", "p1", Decimal("5.00"))


# ─────────────────────────────────────────────────────────────────────────────
# claim_stripe_event
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_stripe_event_raises_runtime_error_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import claim_stripe_event

        with pytest.raises(RuntimeError, match="not configured"):
            await claim_stripe_event("evt_1", "checkout.session.completed", {"id": "evt_1"})


@pytest.mark.asyncio
async def test_claim_stripe_event_happy_path_inserts_and_returns_true():
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.return_value.execute.return_value = MagicMock()
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import claim_stripe_event

        result = await claim_stripe_event("evt_1", "checkout.session.completed", {"amount": Decimal("5.00")})

    assert result is True
    mock_sb.table.assert_any_call("stripe_events")
    insert_call = mock_sb.table.return_value.insert.call_args[0][0]
    assert insert_call["event_id"] == "evt_1"
    # Decimal payload values must be serialized (via _serialize_for_api) before
    # being handed to PostgREST's JSON encoder.
    assert insert_call["payload"]["amount"] == "5.00"


@pytest.mark.asyncio
async def test_claim_stripe_event_duplicate_already_processed_returns_false():
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.return_value.execute.side_effect = Exception(
        "duplicate key value violates unique constraint (23505)"
    )
    select_response = MagicMock()
    select_response.data = [{"processed_at": "2026-08-01T00:00:00Z"}]
    mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
        select_response
    )
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import claim_stripe_event

        result = await claim_stripe_event("evt_1", "checkout.session.completed", {"id": "evt_1"})

    assert result is False


@pytest.mark.asyncio
async def test_claim_stripe_event_duplicate_stuck_unprocessed_logs_critical_and_returns_false():
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.return_value.execute.side_effect = Exception("already exists")
    select_response = MagicMock()
    select_response.data = [{"processed_at": None}]
    mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = (
        select_response
    )
    with (
        patch("repositories.wallet_repo.supabase", mock_sb),
        patch("repositories.wallet_repo.logger") as mock_logger,
    ):
        from repositories.wallet_repo import claim_stripe_event

        result = await claim_stripe_event("evt_1", "checkout.session.completed", {"id": "evt_1"})

    assert result is False
    mock_logger.critical.assert_called_once()


@pytest.mark.asyncio
async def test_claim_stripe_event_non_duplicate_error_propagates():
    mock_sb = MagicMock()
    mock_sb.table.return_value.insert.return_value.execute.side_effect = RuntimeError("network unreachable")
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import claim_stripe_event
        from utils.error_handling import DatabaseError

        with pytest.raises(DatabaseError):
            await claim_stripe_event("evt_1", "checkout.session.completed", {"id": "evt_1"})


# ─────────────────────────────────────────────────────────────────────────────
# mark_stripe_event_processed
#
# NOTE (bug found in PR #3098, fixed here): this function had an explicit
# `if not supabase: return` no-op AND a bare `except Exception:
# logger.warning(...)` swallow around the DB update, with return type `None`
# in both the success and failure case -- the caller has no way to detect
# that the stamp failed short of grepping logs. The old docstring argued
# this was an intentional, bounded trade-off because "a reconciliation job
# distinguishes stuck vs processed events via the stripe_events table" --
# that claim was checked while fixing this and found to be false: nothing
# in this codebase actually scans stripe_events for processed_at IS NULL
# rows (grepped; only the reactive, retry-triggered check inside
# claim_stripe_event above references processed_at, and that only fires if
# Stripe retries the *same* event_id, which it won't for an already-2xx'd
# event). Fix: `logger.warning` -> `logger.error` with `extra={"domain":
# "payments", ...}` so the failure trips the loguru->Sentry bridge
# (`backend/server.py`'s `_loguru_sentry_sink`, level=ERROR) instead of
# vanishing into a log line nobody greps. The return type is intentionally
# left as `None` (not changed to `bool`) -- there is still no retry/replay
# lever a caller could pull today, so a signature change would be dead
# weight; building the actual reconciliation sweep is tracked separately as
# ACTION_ITEMS.md C10, not bundled into this fix.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_stripe_event_processed_is_noop_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import mark_stripe_event_processed

        assert await mark_stripe_event_processed("evt_1") is None


@pytest.mark.asyncio
async def test_mark_stripe_event_processed_happy_path_updates_row():
    mock_sb = MagicMock()
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import mark_stripe_event_processed

        result = await mark_stripe_event_processed("evt_1")

    assert result is None
    mock_sb.table.assert_any_call("stripe_events")
    mock_sb.table.return_value.update.assert_called_once()
    update_payload = mock_sb.table.return_value.update.call_args[0][0]
    assert "processed_at" in update_payload
    mock_sb.table.return_value.update.return_value.eq.assert_called_once_with("event_id", "evt_1")


@pytest.mark.asyncio
async def test_mark_stripe_event_processed_swallows_db_error_but_logs_loudly():
    """The caller still gets None back either way (Stripe already has its
    2xx, there is no retry lever to pull from here) -- but the failure must
    surface loudly, not silently, per CLAUDE.md's "do not silently swallow
    a payment error" rule. Was `logger.warning`; fixed to `logger.error` so
    it trips the loguru->Sentry bridge (backend/server.py's
    `_loguru_sentry_sink`, level=ERROR) with the payments domain tag."""
    mock_sb = MagicMock()
    mock_sb.table.return_value.update.return_value.eq.return_value.execute.side_effect = RuntimeError("db down")
    with (
        patch("repositories.wallet_repo.supabase", mock_sb),
        patch("repositories.wallet_repo.logger") as mock_logger,
    ):
        from repositories.wallet_repo import mark_stripe_event_processed

        result = await mark_stripe_event_processed("evt_1")

    assert result is None
    mock_logger.error.assert_called_once()
    _, kwargs = mock_logger.error.call_args
    assert kwargs["extra"]["domain"] == "payments"
    assert kwargs["extra"]["event_id"] == "evt_1"


# ─────────────────────────────────────────────────────────────────────────────
# unclaim_stripe_event
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unclaim_stripe_event_returns_false_when_supabase_unconfigured():
    with patch("repositories.wallet_repo.supabase", None):
        from repositories.wallet_repo import unclaim_stripe_event

        assert await unclaim_stripe_event("evt_1") is False


@pytest.mark.asyncio
async def test_unclaim_stripe_event_happy_path_returns_true():
    mock_sb = MagicMock()
    with patch("repositories.wallet_repo.supabase", mock_sb):
        from repositories.wallet_repo import unclaim_stripe_event

        result = await unclaim_stripe_event("evt_1")

    assert result is True
    mock_sb.table.assert_any_call("stripe_events")
    mock_sb.table.return_value.delete.return_value.eq.assert_called_once_with("event_id", "evt_1")
    mock_sb.table.return_value.delete.return_value.eq.return_value.is_.assert_called_once_with("processed_at", "null")


@pytest.mark.asyncio
async def test_unclaim_stripe_event_returns_false_on_db_error():
    """The failure IS signalled to the caller via the boolean return (unlike
    mark_stripe_event_processed above) -- the docstring says the caller must
    escalate on False. This is the documented, deliberate degrade-with-signal
    pattern, not a swallow."""
    mock_sb = MagicMock()
    mock_sb.table.return_value.delete.return_value.eq.return_value.is_.return_value.execute.side_effect = RuntimeError(
        "db down"
    )
    with (
        patch("repositories.wallet_repo.supabase", mock_sb),
        patch("repositories.wallet_repo.logger") as mock_logger,
    ):
        from repositories.wallet_repo import unclaim_stripe_event

        result = await unclaim_stripe_event("evt_1")

    assert result is False
    mock_logger.warning.assert_called_once()
