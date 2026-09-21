"""Unit tests for the pure insurance-period derivation.

`derive_insurance_period` is the single source of truth for CLAUDE.md's
Period 0-3 table. Before it existed the table was implemented twice —
once as four DB-query functions in `utils/insurance_period_reconciler.py`
and once as inline ternaries in `routes/drivers/status.py` — and the two
disagreed about a driver holding a live offer. These tests pin the table
itself, so a future divergence fails here rather than in production
insurance records.

Deliberately exhaustive over RideStatus: `test_every_ride_status_maps`
iterates the real enum, so adding a state to the machine without
deciding its insurance period fails the suite instead of silently
defaulting.
"""

import os
import sys

import pytest

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.ride_status import RideStatus  # noqa: E402
from backend.utils.insurance_periods import derive_insurance_period  # noqa: E402

# The authoritative table, transcribed from CLAUDE.md's "Insurance periods
# (TNC commercial insurance)" section. Written out as data rather than
# derived from the implementation — a test that recomputes the thing it is
# testing proves nothing.
_EXPECTED_BY_RIDE_STATUS = {
    RideStatus.IN_PROGRESS: 3,
    RideStatus.DRIVER_ASSIGNED: 2,
    RideStatus.DRIVER_ACCEPTED: 2,
    RideStatus.DRIVER_ARRIVED: 2,
    # No ride obligation — falls through to the online/offer signals.
    RideStatus.SCHEDULED: 1,
    RideStatus.SEARCHING: 1,
    RideStatus.COMPLETED: 1,
    RideStatus.CANCELLED: 1,
}


class TestRideStateDrivenPeriods:
    @pytest.mark.parametrize("status,expected", sorted(_EXPECTED_BY_RIDE_STATUS.items()))
    def test_ride_status_maps_to_documented_period(self, status, expected):
        assert derive_insurance_period(ride_status=status, is_online=True) == expected

    def test_every_ride_status_maps(self):
        """Adding a ride state must force an insurance-period decision.

        If someone extends RideStatus without adding a row above, this
        fails — which is the point. A new state silently inheriting
        Period 1 is exactly the misclassification CLAUDE.md calls a
        regulatory and insurance liability.
        """
        assert set(_EXPECTED_BY_RIDE_STATUS) == set(RideStatus)

    def test_passenger_aboard_outranks_everything(self):
        """Period 3 is not reachable from any other signal, and nothing overrides it."""
        assert (
            derive_insurance_period(
                ride_status=RideStatus.IN_PROGRESS,
                is_online=False,
                has_live_offer=True,
            )
            == 3
        )


class TestLiveOfferIsPeriodTwo:
    """The case the two previous implementations disagreed about.

    CLAUDE.md: "Period 2 starts on `driver_assigned` (not
    `driver_accepted`) — a driver becomes obligated to the ride the
    instant `claim_driver_atomic` succeeds and the offer is live." Batch
    dispatch holds no `rides.driver_id` link pre-acceptance, so the only
    signal is the pending `ride_offers` row.
    """

    def test_live_offer_with_no_ride_row_is_period_2(self):
        assert derive_insurance_period(ride_status=None, is_online=True, has_live_offer=True) == 2

    def test_live_offer_outranks_merely_online(self):
        online_only = derive_insurance_period(ride_status=None, is_online=True, has_live_offer=False)
        with_offer = derive_insurance_period(ride_status=None, is_online=True, has_live_offer=True)
        assert online_only == 1
        assert with_offer == 2

    def test_live_offer_counts_even_if_the_driver_toggled_offline(self):
        """The obligation is to the ride, not to the driver's toggle.

        This is the production bug this function exists to prevent:
        `routes/drivers/status.py` recorded Period 0 on a Go Offline tap
        without ever looking for a pending offer, overwriting the correct
        Period 2 row opened at claim time.
        """
        assert derive_insurance_period(ride_status=None, is_online=False, has_live_offer=True) == 2


class TestOnlineAndOfflineFallbacks:
    def test_online_with_nothing_else_is_period_1(self):
        assert derive_insurance_period(is_online=True) == 1

    def test_offline_with_nothing_else_is_period_0(self):
        assert derive_insurance_period(is_online=False) == 0

    def test_defaults_are_period_0(self):
        """An uninformed call must not invent coverage."""
        assert derive_insurance_period() == 0


class TestUnknownRideStatus:
    """CLAUDE.md wants a contract violation surfaced loudly; this module's
    own docstring forbids blocking the driver state machine. Both hold."""

    def test_unknown_status_does_not_raise(self, caplog):
        with caplog.at_level("ERROR"):
            period = derive_insurance_period(ride_status="teleported", is_online=True)
        assert period == 1
        assert any("outside RideStatus" in r.getMessage() for r in caplog.records)

    def test_unknown_status_never_degrades_upward(self):
        """A garbage status must not be able to invent commercial coverage."""
        assert derive_insurance_period(ride_status="teleported", is_online=False) == 0

    def test_unknown_status_still_respects_a_live_offer(self):
        assert derive_insurance_period(ride_status="teleported", has_live_offer=True) == 2


class TestCallSignature:
    def test_arguments_are_keyword_only(self):
        """A transposed positional arg would silently misstate SGI coverage."""
        with pytest.raises(TypeError):
            derive_insurance_period(RideStatus.IN_PROGRESS)  # type: ignore[misc]

    def test_result_is_always_a_valid_period(self):
        from backend.utils.insurance_periods import _VALID_PERIODS

        for status in list(RideStatus) + [None, "teleported"]:
            for online in (True, False):
                for offer in (True, False):
                    got = derive_insurance_period(ride_status=status, is_online=online, has_live_offer=offer)
                    assert got in _VALID_PERIODS
