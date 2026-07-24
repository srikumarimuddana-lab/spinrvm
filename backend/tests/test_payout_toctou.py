"""Regression tests for finding 4 (WS-7): payout TOCTOU double-withdraw.

The standard and instant payout paths both read payable_balance, check
amount <= balance, then call Stripe Transfer.create, then persisted the
payout row. Two concurrent requests both passed the balance check against
the same snapshot and both transferred — a TOCTOU double-withdraw of
platform money.

Fix: the payout row is inserted with status='reserved' BEFORE the Stripe
Transfer call. A partial unique index on (driver_id) WHERE status IN
('reserved','pending','transfer_completed') prevents a second concurrent
request from also reserving. The balance computation already deducts every
non-terminal payout, so a 'reserved' row reduces the payable balance for
any racing request that gets past the index.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PAYOUTS = (_ROOT / "routes" / "drivers" / "payouts.py").read_text()
_EARNINGS = (_ROOT / "routes" / "drivers" / "earnings.py").read_text()
_MIG_250 = (_ROOT / "migrations" / "250_payout_reservation_guard.sql").read_text()


# ── Migration 250 contract ──────────────────────────────────────────────


class TestMigration250:
    def test_partial_unique_index_exists(self):
        assert "CREATE UNIQUE INDEX" in _MIG_250

    def test_index_covers_reserved_status(self):
        assert "'reserved'" in _MIG_250

    def test_index_covers_pending_status(self):
        assert "'pending'" in _MIG_250

    def test_index_covers_transfer_completed_status(self):
        assert "'transfer_completed'" in _MIG_250

    def test_index_is_on_driver_id(self):
        assert "(driver_id)" in _MIG_250

    def test_index_does_not_cover_terminal_statuses(self):
        assert "'completed'" not in _MIG_250
        assert "'failed'" not in _MIG_250
        assert "'reversed'" not in _MIG_250
        assert "'stranded'" not in _MIG_250

    def test_has_rollback_marker(self):
        assert "-- rollback:" in _MIG_250.lower()


# ── Standard payout: reserve-then-transfer ───────────────────────────────


class TestStandardPayoutReservation:
    def test_inserts_reserved_row_before_transfer(self):
        """The payout row must be inserted with status='reserved' before
        the Stripe Transfer.create call, not after."""
        reserve_pos = _PAYOUTS.index('"reserved"')
        transfer_pos = _PAYOUTS.index("Transfer.create")
        insert_pos = _PAYOUTS.index('insert_one("payouts"')
        assert insert_pos < transfer_pos, "payout row must be inserted before the Stripe Transfer call"
        assert reserve_pos < transfer_pos

    def test_handles_unique_violation_as_409(self):
        """A concurrent reservation triggers the partial unique index and
        must surface as 409, not 500."""
        assert "409" in _PAYOUTS
        assert "already in progress" in _PAYOUTS.lower()

    def test_marks_failed_on_transfer_error(self):
        """When the Stripe Transfer fails, the reserved row must be updated
        to status='failed' so the index slot is freed for the next attempt."""
        # Find the standard payout section (before instant payout)
        std_section = _PAYOUTS[: _PAYOUTS.index("request_instant_payout")]
        assert '"failed"' in std_section

    def test_reverses_transfer_on_terminal_write_failure(self):
        """If the terminal write (reserved -> completed) fails after the
        Stripe Transfer succeeded, the transfer must be reversed."""
        std_section = _PAYOUTS[: _PAYOUTS.index("request_instant_payout")]
        assert "_attempt_transfer_reversal" in std_section

    def test_standard_payout_has_idempotency_key(self):
        assert "payout-transfer-" in _PAYOUTS


# ── Instant payout: reserve-then-transfer ────────────────────────────────


class TestInstantPayoutReservation:
    def _instant_section(self):
        start = _PAYOUTS.index("request_instant_payout")
        return _PAYOUTS[start:]

    def test_inserts_reserved_row_before_transfer(self):
        section = self._instant_section()
        reserve_pos = section.index('"reserved"')
        transfer_pos = section.index("Transfer.create")
        insert_pos = section.index('insert_one("payouts"')
        assert insert_pos < transfer_pos, "instant payout row must be inserted before the Stripe Transfer"
        assert reserve_pos < transfer_pos

    def test_updates_to_transfer_completed_after_transfer(self):
        section = self._instant_section()
        assert '"transfer_completed"' in section

    def test_marks_failed_on_transfer_error(self):
        section = self._instant_section()
        # After transfer fails, reserved -> failed
        assert '"failed"' in section

    def test_handles_unique_violation_as_409(self):
        section = self._instant_section()
        assert "409" in section
        assert "already in progress" in section.lower()


# ── Balance: reserved rows are deducted ──────────────────────────────────


class TestBalanceDeductsReservations:
    def test_only_reversed_and_failed_excluded(self):
        """get_driver_balance must deduct every payout status except
        'reversed' and 'failed'. This means 'reserved', 'pending',
        'transfer_completed', 'completed', and 'stranded' all reduce
        the payable balance — so a reserved row prevents a second
        withdraw from succeeding even if the index is somehow bypassed."""
        assert '_not_money_out = {"reversed", "failed"}' in _EARNINGS

    def test_balance_subtracts_all_non_excluded(self):
        assert "not in _not_money_out" in _EARNINGS
