"""PIPEDA data-minimization tests for the financial ledgers.

financial_events and wallet_transactions are 7-year tax/audit ledgers that
outlive the account-deletion scrub (users.py anonymizes the *ride* row only),
so their metadata must never retain an exact pickup/dropoff address — the
city-level area is the maximum allowed (CLAUDE.md: "Exact pickup/dropoff
addresses — log city/area only").

Regression for the finding: payment_service copied the full addresses
verbatim into both ledgers' metadata.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

RIDE_ID = "ride_pii_1"
RIDER_ID = "rider_pii_1"

PICKUP_FULL = "1742 Main Street, Saskatoon, SK, S7K 3A1"
DROPOFF_FULL = "88 Elm Drive, Regina, SK"


def _ride():
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": "driver_pii_1",
        "total_fare": "25.00",
        "grand_total": "25.00",
        "base_fare": "5.00",
        "distance_fare": "15.00",
        "time_fare": "5.00",
        "surge_multiplier": "1.0",
        "payment_method": "card",
        "pickup_address": PICKUP_FULL,
        "dropoff_address": DROPOFF_FULL,
    }


def _assert_city_only(meta: dict):
    assert meta["pickup_address"] == "Saskatoon"
    assert meta["dropoff_address"] == "Regina"
    for v in (meta["pickup_address"], meta["dropoff_address"]):
        assert "1742" not in v and "Main" not in v and "Elm" not in v and "S7K" not in v


@pytest.mark.asyncio
async def test_financial_events_metadata_is_city_only():
    from backend.services import payment_service

    insert_mock = AsyncMock(return_value=None)
    with patch("backend.services.payment_service.db_supabase.insert_one", insert_mock):
        await payment_service.record_payment_event(
            RIDE_ID,
            RIDER_ID,
            2500,
            "pi_test",
            ride=_ride(),
            tip_amount=Decimal("2.00"),
        )

    (table, row) = insert_mock.call_args.args
    assert table == "financial_events"
    _assert_city_only(row["metadata"])


@pytest.mark.asyncio
async def test_wallet_transactions_metadata_is_city_only():
    from backend.services import payment_service

    wallet = {"id": "wallet_pii_1", "user_id": RIDER_ID, "balance": "100.00", "is_active": True}
    insert_mock = AsyncMock(return_value=None)

    ps = "backend.services.payment_service."
    with (
        patch(ps + "db_supabase.find_one", AsyncMock(return_value=wallet)),
        patch(ps + "db_supabase.wallet_pay_for_ride", AsyncMock(return_value=Decimal("73.00"))),
        patch(ps + "db_supabase.insert_one", insert_mock),
        patch(ps + "db_supabase.update_ride", AsyncMock(return_value=None)),
    ):
        result = await payment_service.settle_wallet(_ride(), RIDE_ID, RIDER_ID, Decimal("27.00"), Decimal("2.00"))

    assert result.success is True
    wallet_tx = [c for c in insert_mock.call_args_list if c.args[0] == "wallet_transactions"]
    assert wallet_tx, "Expected a wallet_transactions ledger write"
    _assert_city_only(wallet_tx[0].args[1]["metadata"])


def test_area_only_never_returns_a_street():
    """Direct unit coverage of the shared redaction helper."""
    from backend.utils.pii import area_only

    assert area_only(PICKUP_FULL) == "Saskatoon"
    assert area_only("Oak Lane, Regina") == "Regina"
    assert area_only("Moose Jaw, SK") == "Moose Jaw"
    # Nothing usable → None, never a street fragment.
    assert area_only("1742 Main Street") is None
    assert area_only(None) is None


def test_area_only_geocoder_and_saint_city_formats():
    """Regression: geocoder addresses end in ', Canada' (must not collapse the
    city to the country), and 'St.'-prefixed cities must not be treated as a
    street suffix and erased."""
    from backend.utils.pii import area_only

    assert area_only("1742 Main St, Saskatoon, SK, Canada") == "Saskatoon"
    assert area_only("St. Albert, SK") == "St. Albert"
    assert area_only("St. John's, NL, Canada") == "St. John's"
    # Street suffixes still stripped when they are genuine streets.
    assert area_only("Main St, Saskatoon") == "Saskatoon"
