"""Regression tests for GET /api/admin/analytics/surge-history.

The endpoint used to fetch the OLDEST 500 surge_pricing rows (ascending
order, no DB-side time filter) and apply the cutoff in Python — once an
area accumulated more than 500 history rows the requested window came
back empty. It must push the cutoff and newest-first ordering into the
DB query (served by idx_surge_pricing_area_created, migration 142) and
project to the six charted columns.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {
    "id": "admin-001",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["dashboard"],
}


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _set_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _ADMIN
    yield
    app_fixture.dependency_overrides.clear()


@pytest.fixture
def client(test_client):
    return test_client


def _row(minutes_ago: int, multiplier: float = 1.5) -> dict:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "multiplier": multiplier,
        "demand_count": 3,
        "supply_count": 2,
        "ratio": 1.5,
        "source": "auto",
        "created_at": ts,
    }


class TestSurgeHistory:
    def test_time_filter_and_order_pushed_to_db(self, client):
        """Cutoff, newest-first order, and column projection are DB-side."""
        mock = AsyncMock(return_value=[_row(5), _row(10)])
        with patch("backend.routes.admin.analytics.db.get_rows", mock):
            resp = client.get(
                "/api/admin/analytics/surge-history",
                params={"area_id": "area-1", "hours": 24},
            )
        assert resp.status_code == 200

        args, kwargs = mock.call_args
        assert args[0] == "surge_pricing"
        filters = args[1]
        assert filters["service_area_id"] == "area-1"
        # DB-side cutoff: $gte against the requested window, not Python-side
        cutoff = filters["created_at"]["$gte"]
        cutoff_dt = datetime.fromisoformat(cutoff)
        age = datetime.now(timezone.utc) - cutoff_dt
        assert timedelta(hours=23, minutes=59) < age < timedelta(hours=24, minutes=1)
        # Newest-first so the LIMIT keeps the recent window, not the oldest rows
        assert kwargs.get("order") == "created_at"
        assert kwargs.get("desc") is True
        assert kwargs.get("columns") == "multiplier,demand_count,supply_count,ratio,source,created_at"

    def test_history_returned_in_chronological_order(self, client):
        """DB returns newest-first; the response reverses to chronological."""
        newest, oldest = _row(5), _row(10)
        mock = AsyncMock(return_value=[newest, oldest])
        with patch("backend.routes.admin.analytics.db.get_rows", mock):
            resp = client.get(
                "/api/admin/analytics/surge-history",
                params={"area_id": "area-1", "hours": 24},
            )
        assert resp.status_code == 200
        history = resp.json()["history"]
        assert [h["created_at"] for h in history] == [
            oldest["created_at"],
            newest["created_at"],
        ]
        assert history[0]["multiplier"] == 1.5
