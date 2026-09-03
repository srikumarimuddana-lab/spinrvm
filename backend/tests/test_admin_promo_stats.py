"""Admin promotion stats — daily_usage split by promo type.

The admin dashboard's Promotions Analytics chart has an "All Promos" /
"Public Codes Only" / "Private Coupons Only" filter. It was previously
wired up client-side with no matching backend data, so changing the
filter was a visible no-op. This covers the backend split that fixes it:
each daily_usage bucket now carries public_count/public_amount and
private_count/private_amount alongside the combined count/amount.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {
    "id": "admin-1",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["dashboard", "promotions"],
}

_PROMOS = [
    {"id": "promo-public", "promo_type": "discount", "is_active": True},
    {"id": "promo-private", "promo_type": "private", "is_active": True},
]


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def _usage_row(promo_id: str, created_at: str, discount: float):
    return {
        "id": f"usage-{promo_id}-{created_at}",
        "promo_id": promo_id,
        "user_id": "user-1",
        "code": "CODE",
        "discount_applied": discount,
        "created_at": created_at,
    }


def test_daily_usage_splits_public_and_private(admin_client):
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = [
        _usage_row("promo-public", f"{today}T10:00:00+00:00", 5.0),
        _usage_row("promo-private", f"{today}T11:00:00+00:00", 3.0),
        _usage_row("promo-private", f"{today}T12:00:00+00:00", 2.0),
    ]

    async def fake_get_rows(table, *args, **kwargs):
        if table == "promotions":
            return list(_PROMOS)
        if table == "promo_applications":
            return list(usage)
        return []

    # admin_get_promo_stats now aggregates (including the public/private
    # daily split) server-side via the admin_promo_stats RPC (migration
    # 393) instead of fetching all promotions/promo_applications rows and
    # bucketing in Python -- get_rows is unused by this endpoint now, but
    # left mocked above for parity with the fixture data the RPC mock below
    # mirrors.
    async def fake_rpc(name, params):
        assert name == "admin_promo_stats"
        return {
            "total_codes": 1,
            "active_codes": 1,
            "expired_codes": 0,
            "total_private": 1,
            "active_private": 1,
            "total_redemptions": 3,
            "total_discount": 10.0,
            "daily_usage": [
                {
                    "date": today,
                    "count": 3,
                    "amount": 10.0,
                    "public_count": 1,
                    "public_amount": 5.0,
                    "private_count": 2,
                    "private_amount": 5.0,
                }
            ],
        }

    with (
        patch("backend.db_supabase.get_rows", new=AsyncMock(side_effect=fake_get_rows)),
        patch("backend.db_supabase.rpc", new=AsyncMock(side_effect=fake_rpc)),
    ):
        resp = admin_client.get("/api/admin/promotions/stats")

    assert resp.status_code == 200
    body = resp.json()

    today_bucket = next(d for d in body["daily_usage"] if d["date"] == today)
    assert today_bucket["count"] == 3
    assert today_bucket["amount"] == 10.0
    assert today_bucket["public_count"] == 1
    assert today_bucket["public_amount"] == 5.0
    assert today_bucket["private_count"] == 2
    assert today_bucket["private_amount"] == 5.0

    # Combined totals must still equal the sum of the two splits — the
    # additive fields must never disagree with the pre-existing ones.
    assert today_bucket["count"] == today_bucket["public_count"] + today_bucket["private_count"]
    assert today_bucket["amount"] == today_bucket["public_amount"] + today_bucket["private_amount"]


def test_daily_usage_unknown_promo_id_counts_as_public(admin_client):
    """A usage row whose promo was deleted (no matching entry in `promotions`)
    must not be silently dropped from either the combined or the split totals."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = [_usage_row("promo-deleted", f"{today}T09:00:00+00:00", 4.0)]

    async def fake_get_rows(table, *args, **kwargs):
        if table == "promotions":
            return list(_PROMOS)
        if table == "promo_applications":
            return list(usage)
        return []

    # See test_daily_usage_splits_public_and_private above: the daily-usage
    # split (including the LEFT JOIN promo_id-not-found -> 'public' fallback,
    # `COALESCE(p.promo_type, 'public')` in migration 393) is now computed
    # server-side by the admin_promo_stats RPC.
    async def fake_rpc(name, params):
        return {
            "total_codes": 1,
            "active_codes": 1,
            "expired_codes": 0,
            "total_private": 1,
            "active_private": 1,
            "total_redemptions": 1,
            "total_discount": 4.0,
            "daily_usage": [
                {
                    "date": today,
                    "count": 1,
                    "amount": 4.0,
                    "public_count": 1,
                    "public_amount": 4.0,
                    "private_count": 0,
                    "private_amount": 0.0,
                }
            ],
        }

    with (
        patch("backend.db_supabase.get_rows", new=AsyncMock(side_effect=fake_get_rows)),
        patch("backend.db_supabase.rpc", new=AsyncMock(side_effect=fake_rpc)),
    ):
        resp = admin_client.get("/api/admin/promotions/stats")

    assert resp.status_code == 200
    today_bucket = next(d for d in resp.json()["daily_usage"] if d["date"] == today)
    assert today_bucket["count"] == 1
    assert today_bucket["public_count"] == 1
    assert today_bucket["private_count"] == 0
