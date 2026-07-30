"""Shared test factory/mock helpers.

Kept out of conftest.py because pytest loads conftest modules by file path,
not by package-qualified name, so `from tests.conftest import X` fails.
This module is a regular importable module for shared test data builders
and mock helpers used across multiple test files.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict


def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def close_spawned_coro(coro: Any, *args: Any, **kwargs: Any) -> None:
    """``side_effect`` for a mocked ``spawn()``/``asyncio.create_task`` seam.

    Production fire-and-forget dispatch always looks like
    ``_deps.spawn(some_coroutine(...))`` -- the coroutine argument is
    created by evaluating ``some_coroutine(...)`` *before* ``spawn`` (or,
    in tests that intercept the seam one level deeper, ``asyncio.create_task``
    itself) is even called. A bare ``MagicMock()`` standing in for either
    records the call and returns, but never awaits or otherwise consumes
    that coroutine -- it leaks until GC collects it, which can fail an
    unrelated test under pytest 9's unraisableexception plugin (A8).

    Real ``asyncio.create_task`` takes ownership of the coroutine; this
    mirrors that by explicitly closing it instead. Use as
    ``MagicMock(side_effect=close_spawned_coro)`` (or pass to the
    ``new=``/``side_effect=`` of whichever mock stands in for ``spawn`` or
    ``asyncio.create_task`` in a given test) wherever a test doesn't care
    what the spawned task actually does, only that it was invoked.
    """
    if coro is not None and hasattr(coro, "close"):
        coro.close()


def corporate_account_row(status_value: str = "active", **overrides: Any) -> Dict[str, Any]:
    """Build a CorporateAccountDetailResponse-shaped dict for route tests.

    The response schema in ``schemas.corporate`` requires every field here;
    missing any one raises a validation error when the handler serializes.
    """
    row: Dict[str, Any] = {
        "id": "c1",
        "name": "Acme",
        "status": status_value,
        "country_code": "CA",
        "currency": "CAD",
        "locale": "en-CA",
        "timezone": "America/Toronto",
        "size_tier": "smb",
        "is_active": True,
        "credit_limit": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def ride_row(**overrides: Any) -> Dict[str, Any]:
    """Build a rides-table-shaped dict with sensible Saskatchewan defaults."""
    base: Dict[str, Any] = {
        "id": _uid(),
        "rider_id": _uid(),
        "driver_id": None,
        "status": "searching",
        "payment_status": "pending",
        "payment_intent_id": None,
        "payment_retry_count": 0,
        "pickup_address": "123 Main St, Regina, SK",
        "dropoff_address": "456 Elm Ave, Regina, SK",
        "pickup_lat": 50.4452,
        "pickup_lng": -104.6189,
        "dropoff_lat": 50.4500,
        "dropoff_lng": -104.6100,
        "base_fare": "3.50",
        "total_fare": "12.00",
        "tax_amount": "1.32",
        "booking_fee": "2.00",
        "surge_multiplier": 1.0,
        "vehicle_type_id": _uid(),
        "created_at": _now(),
        "updated_at": _now(),
    }
    base.update(overrides)
    return base


def driver_row(**overrides: Any) -> Dict[str, Any]:
    """Build a drivers-table-shaped dict with sensible defaults."""
    base: Dict[str, Any] = {
        "id": _uid(),
        "user_id": _uid(),
        "status": "active",
        "is_online": False,
        "is_available": False,
        "vehicle_make": "Toyota",
        "vehicle_model": "Camry",
        "vehicle_year": 2022,
        "vehicle_color": "White",
        "license_plate": "SGI-1234",
        "rating": 4.8,
        "total_rides": 42,
        "total_earnings": "1200.00",
        "subscription_status": "active",
        "subscription_expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "stripe_customer_id": None,
        "current_lat": 50.4452,
        "current_lng": -104.6189,
        "created_at": _now(),
        "updated_at": _now(),
    }
    base.update(overrides)
    return base


def vehicle_type_row(**overrides: Any) -> Dict[str, Any]:
    """Build a vehicle_types-table-shaped dict with Economy defaults."""
    base: Dict[str, Any] = {
        "id": _uid(),
        "name": "Economy",
        "description": "Affordable everyday rides",
        "icon": "car",
        "capacity": 4,
        "is_active": True,
        "base_fare": "3.50",
        "per_km_rate": "1.50",
        "per_minute_rate": "0.25",
        "minimum_fare": "8.00",
        "booking_fee": "2.00",
        "created_at": _now(),
    }
    base.update(overrides)
    return base


def service_area_row(**overrides: Any) -> Dict[str, Any]:
    """Build a service_areas-table-shaped dict defaulting to Regina, SK."""
    base: Dict[str, Any] = {
        "id": _uid(),
        "name": "Regina",
        "city": "Regina",
        "province": "SK",
        "country": "CA",
        "is_active": True,
        "surge_active": False,
        "surge_multiplier": 1.0,
        "surge_source": "auto",
        "vehicle_pricing": [],
        "parent_service_area_id": None,
        "created_at": _now(),
    }
    base.update(overrides)
    return base


def user_row(**overrides: Any) -> Dict[str, Any]:
    """Build a users-table-shaped dict with rider role defaults."""
    base: Dict[str, Any] = {
        "id": _uid(),
        "phone": "+13065551234",
        "first_name": "Test",
        "last_name": "User",
        "role": "rider",
        "is_active": True,
        "email": None,
        "created_at": _now(),
    }
    base.update(overrides)
    return base


def promotion_row(**overrides: Any) -> Dict[str, Any]:
    """Build a promotions-table-shaped dict defaulting to a 10% discount code."""
    base: Dict[str, Any] = {
        "id": _uid(),
        "code": "SAVE10",
        "discount_type": "percent",
        "discount_value": "10.00",
        "max_uses": 100,
        "used_count": 0,
        "is_active": True,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "created_at": _now(),
    }
    base.update(overrides)
    return base


def payout_row(**overrides: Any) -> Dict[str, Any]:
    """Build a payouts-table-shaped dict in pending state."""
    base: Dict[str, Any] = {
        "id": _uid(),
        "driver_id": _uid(),
        "amount": "150.00",
        "status": "pending",
        "retry_count": 0,
        "stripe_transfer_id": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    base.update(overrides)
    return base
