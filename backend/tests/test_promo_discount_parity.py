"""Advertised vs charged promo discount parity.

GET /promo/available advertises a ``discount_amount``; the validate/apply
path charges one. Both must use the same Decimal HALF_UP math. The old
float ``round()`` in /available disagreed with the Decimal validate path
by a cent on half-cent products — e.g. $17.90 @ 15%: advertised $2.68,
charged $2.69.

Mirrors test_promo_rate_limit.py's minimal-app strategy: bare-path
imports, bare ``db_supabase`` patches, all DB calls mocked.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_FAKE_USER = {"id": "parity_user_1", "phone": "+15550001111", "role": "rider"}

# (ride_portion, percent, expected_discount). The first case is the
# regression: 17.90 * 15% = 2.685 → HALF_UP 2.69, but float round() gave
# 2.68 because the nearest double sits below the half.
_PERCENT_CASES = [
    ("17.90", 15, "2.69"),
    ("19.99", 10, "2.00"),
    ("33.35", 7.5, "2.50"),
    ("12.45", 12.5, "1.56"),
    ("100.00", 33, "33.00"),
]


def _promo(**extra) -> dict:
    base = {
        "id": "promo_parity",
        "code": "PARITY",
        "is_active": True,
        "free_ride": False,
        "discount_type": "percentage",
        "discount_value": 15,
        "max_discount": None,
        "max_uses": 0,
        "max_uses_per_user": 0,
        "uses": 0,
        "expiry_date": None,
        "assigned_user_ids": [],
        "first_ride_only": False,
        "new_user_days": 0,
        "inactive_days": 0,
        "min_total_rides": 0,
        "max_total_rides": 0,
        "total_budget": 0,
        "budget_used": 0,
        "min_ride_fare": 0,
        "service_area_id": None,
        "description": "parity test promo",
    }
    base.update(extra)
    return base


def _build_app() -> FastAPI:
    from slowapi.errors import RateLimitExceeded

    from dependencies import get_current_user
    from routes.promotions import api_router as promo_router
    from utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

    app = FastAPI()
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(promo_router)
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    return app


def _db_patches(promo: dict):
    async def _get_rows(table, *args, **kwargs):
        if table == "promotions":
            return [promo]
        return []

    return (
        patch("db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("db_supabase.count_documents", AsyncMock(return_value=0)),
        patch(
            "db_supabase.get_user_by_id",
            AsyncMock(return_value={"id": _FAKE_USER["id"], "created_at": "2020-01-01T00:00:00"}),
        ),
    )


def _available_discount(promo: dict, ride_fare: str, ride_portion: str) -> Decimal:
    app = _build_app()
    client = TestClient(app)
    p1, p2, p3 = _db_patches(promo)
    with p1, p2, p3:
        resp = client.get(f"/promo/available?ride_fare={ride_fare}&ride_portion={ride_portion}")
    assert resp.status_code == 200, resp.text
    entries = [e for e in resp.json() if e["promo_id"] == promo["id"]]
    assert entries, f"promo missing from /available response: {resp.json()}"
    return Decimal(str(entries[0]["discount_amount"]))


async def _validated_discount(promo: dict, ride_portion: str) -> Decimal:
    from starlette.requests import Request as StarletteRequest

    from routes.promotions import ValidatePromoRequest, validate_promo

    req = ValidatePromoRequest(code=promo["code"], ride_fare=Decimal(ride_portion))
    mock_request = StarletteRequest(
        {"type": "http", "method": "POST", "path": "/promo/validate", "query_string": b"", "headers": []}
    )
    p1, p2, p3 = _db_patches(promo)
    with p1, p2, p3:
        result = await validate_promo(mock_request, req, dict(_FAKE_USER))
    return Decimal(str(result["discount_amount"]))


class TestAvailableDiscountIsDecimal:
    @pytest.mark.parametrize(("portion", "percent", "expected"), _PERCENT_CASES)
    def test_advertised_percentage_discount_uses_half_up(self, portion, percent, expected):
        promo = _promo(discount_value=percent)
        assert _available_discount(promo, ride_fare="50.00", ride_portion=portion) == Decimal(expected)

    def test_advertised_flat_discount_capped_at_ride_portion(self):
        promo = _promo(discount_type="flat", discount_value=25)
        assert _available_discount(promo, ride_fare="50.00", ride_portion="17.90") == Decimal("17.90")

    def test_advertised_max_cap_applies(self):
        promo = _promo(discount_value=50, max_discount=8)
        assert _available_discount(promo, ride_fare="50.00", ride_portion="40.00") == Decimal("8.00")


@pytest.mark.anyio
class TestAdvertisedEqualsCharged:
    """Parity: the /available amount must equal the validate/apply amount."""

    @pytest.mark.parametrize(("portion", "percent", "expected"), _PERCENT_CASES)
    async def test_percentage_parity(self, portion, percent, expected):
        promo = _promo(discount_value=percent)
        charged = await _validated_discount(promo, portion)
        advertised = _available_discount(promo, ride_fare="50.00", ride_portion=portion)
        assert charged == Decimal(expected)
        assert advertised == charged

    async def test_flat_parity(self):
        promo = _promo(discount_type="flat", discount_value=5)
        charged = await _validated_discount(promo, "17.90")
        advertised = _available_discount(promo, ride_fare="50.00", ride_portion="17.90")
        assert charged == Decimal("5.00")
        assert advertised == charged
