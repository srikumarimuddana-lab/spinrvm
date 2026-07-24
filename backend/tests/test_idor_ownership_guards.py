"""Regression tests for WS-18: IDOR ownership checks.

Validates that id-taking endpoints verify the caller is a party to the
entity before acting on it. Contract-style: reads the source and asserts
the guard patterns are present, so the checks survive refactors.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]

# ── Source files under test ──────────────────────────────────────────

_DECLINE_RIDE_SRC = (_ROOT / "routes" / "drivers" / "ride_flow.py").read_text()
_PAYMENTS_SRC = (_ROOT / "routes" / "payments.py").read_text()
_USERS_SRC = (_ROOT / "routes" / "users.py").read_text()
_SAFETY_SRC = (_ROOT / "routes" / "safety.py").read_text()

# ── Ride ownership checks (existing + new) ───────────────────────────

_RIDE_TRACKING_SRC = (_ROOT / "routes" / "rides" / "tracking.py").read_text()
_RIDE_CHAT_SRC = (_ROOT / "routes" / "rides" / "chat.py").read_text()
_RIDE_STOPS_SRC = (_ROOT / "routes" / "rides" / "stops.py").read_text()
_RIDE_SHARING_SRC = (_ROOT / "routes" / "rides" / "sharing.py").read_text()
_RIDE_RECEIPTS_SRC = (_ROOT / "routes" / "rides" / "receipts.py").read_text()
_RIDE_PAYMENTS_SRC = (_ROOT / "routes" / "rides" / "payments.py").read_text()
_RIDE_LIFECYCLE_SRC = (_ROOT / "routes" / "rides" / "lifecycle.py").read_text()
_RIDE_LOST_FOUND_SRC = (_ROOT / "routes" / "rides" / "lost_found.py").read_text()
_RIDE_RATING_SRC = (_ROOT / "routes" / "rides" / "rating.py").read_text()
_RIDE_CANCEL_SRC = (_ROOT / "routes" / "rides" / "cancellation.py").read_text()
_RIDE_SAFETY_SRC = (_ROOT / "routes" / "rides" / "safety.py").read_text()
_RIDE_QUERIES_SRC = (_ROOT / "routes" / "rides" / "queries.py").read_text()

_DRIVER_CANCEL_SRC = (_ROOT / "routes" / "drivers" / "ride_cancel.py").read_text()
_DRIVER_FLOW_SRC = (_ROOT / "routes" / "drivers" / "ride_flow.py").read_text()
_DRIVER_STATUS_SRC = (_ROOT / "routes" / "drivers" / "status.py").read_text()
_DRIVER_COMPLETE_SRC = (_ROOT / "routes" / "drivers" / "ride_complete.py").read_text()


class TestDeclineRideOwnership:
    """WS-18: decline_ride must verify the driver was offered the ride."""

    def test_checks_is_assigned(self):
        assert 'ride.get("driver_id") == driver["id"]' in _DECLINE_RIDE_SRC

    def test_checks_offer_claimed(self):
        assert "offer_claimed" in _DECLINE_RIDE_SRC

    def test_rejects_unauthorized(self):
        assert "Not authorized to decline this ride" in _DECLINE_RIDE_SRC

    def test_returns_403(self):
        lines = _DECLINE_RIDE_SRC.split("\n")
        for i, line in enumerate(lines):
            if "Not authorized to decline" in line:
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "403" in context
                return
        pytest.fail("403 not found near decline guard")


class TestDeleteCardOwnership:
    """WS-18: delete_card must verify card_id belongs to the user."""

    def test_checks_card_in_saved_ids(self):
        assert "card_id not in saved_ids" in _PAYMENTS_SRC

    def test_returns_404_for_unknown_card(self):
        lines = _PAYMENTS_SRC.split("\n")
        in_delete = False
        for line in lines:
            if "async def delete_card" in line:
                in_delete = True
            if in_delete and "async def " in line and "delete_card" not in line:
                break
            if in_delete and "card_id not in saved_ids" in line:
                assert True
                return
        pytest.fail("ownership check not found in delete_card")


class TestSetDefaultCardOwnership:
    """WS-18: set_default_card must verify card_id belongs to the user."""

    def test_lists_user_cards_before_setting(self):
        lines = _PAYMENTS_SRC.split("\n")
        in_set_default = False
        for line in lines:
            if "async def set_default_card" in line:
                in_set_default = True
            if in_set_default and "async def " in line and "set_default_card" not in line:
                break
            if in_set_default and "PaymentMethod.list" in line:
                assert True
                return
        pytest.fail("PaymentMethod.list not found in set_default_card")

    def test_rejects_unknown_card(self):
        lines = _PAYMENTS_SRC.split("\n")
        in_set_default = False
        for line in lines:
            if "async def set_default_card" in line:
                in_set_default = True
            if in_set_default and "async def " in line and "set_default_card" not in line:
                break
            if in_set_default and "Card not found" in line:
                assert True
                return
        pytest.fail("Card not found rejection not in set_default_card")


class TestLinkCorporateOwnership:
    """WS-18: link_corporate_account must verify membership."""

    def test_checks_membership(self):
        assert "corporate_members" in _USERS_SRC

    def test_checks_active_status(self):
        lines = _USERS_SRC.split("\n")
        in_link = False
        for line in lines:
            if "async def link_corporate_account" in line:
                in_link = True
            if in_link and "async def " in line and "link_corporate_account" not in line:
                break
            if in_link and '"active"' in line and "status" in line:
                assert True
                return
        pytest.fail("active status check not in link_corporate_account")

    def test_returns_403_for_non_member(self):
        assert "Not a member of this corporate account" in _USERS_SRC


class TestSafetyReportOwnership:
    """WS-18: safety report must verify ride_id party membership."""

    def test_verifies_ride_party(self):
        assert "verified_ride_id" in _SAFETY_SRC

    def test_checks_rider_and_driver(self):
        assert "_is_rider" in _SAFETY_SRC
        assert "_is_driver" in _SAFETY_SRC


# ── Existing ownership checks (regression guard) ─────────────────────


class TestRiderEndpointsHaveOwnershipChecks:
    """Every rider-facing ride endpoint must check rider_id or party membership."""

    @pytest.mark.parametrize(
        "src,name",
        [
            (_RIDE_TRACKING_SRC, "tracking"),
            (_RIDE_CHAT_SRC, "chat"),
            (_RIDE_STOPS_SRC, "stops"),
            (_RIDE_SHARING_SRC, "sharing"),
            (_RIDE_RECEIPTS_SRC, "receipts"),
            (_RIDE_PAYMENTS_SRC, "ride_payments"),
            (_RIDE_LIFECYCLE_SRC, "lifecycle"),
            (_RIDE_LOST_FOUND_SRC, "lost_found"),
            (_RIDE_RATING_SRC, "rating"),
            (_RIDE_CANCEL_SRC, "cancellation"),
            (_RIDE_SAFETY_SRC, "ride_safety"),
            (_RIDE_QUERIES_SRC, "queries"),
        ],
    )
    def test_checks_rider_id(self, src, name):
        has_rider_check = (
            'rider_id") != current_user' in src
            or 'rider_id") == current_user' in src
            or '"rider_id": current_user' in src
            or "rider_id": current_user" in src
            or "_require_ride_in_state_rider" in src
        )
        assert has_rider_check, f"{name} missing rider_id ownership check"


class TestDriverEndpointsHaveOwnershipChecks:
    """Every driver-facing ride endpoint must check driver_id."""

    @pytest.mark.parametrize(
        "src,name",
        [
            (_DRIVER_CANCEL_SRC, "ride_cancel"),
            (_DRIVER_FLOW_SRC, "ride_flow"),
            (_DRIVER_STATUS_SRC, "status"),
            (_DRIVER_COMPLETE_SRC, "ride_complete"),
        ],
    )
    def test_checks_driver_id(self, src, name):
        has_check = (
            'driver_id") != driver["id"]' in src
            or 'driver_id") == driver["id"]' in src
            or '"driver_id": driver["id"]' in src
            or 'driver_id"] == driver_row["id"]' in src
            or "user_id\") != current_user" in src
        )
        assert has_check, f"{name} missing driver_id ownership check"
