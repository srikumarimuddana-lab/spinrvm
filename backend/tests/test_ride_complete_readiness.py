"""Driver trip completion refreshes v2 readiness (T12-9)."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.drivers import ride_complete

pytestmark = pytest.mark.anyio


async def test_refresh_calls_trip_completed_without_epoch():
    confirm = AsyncMock(return_value={"code": "OK"})
    with patch("backend.repositories.driver_availability_repo.confirm_driver_ready", confirm):
        await ride_complete._refresh_readiness_after_trip({"id": "drv-1"}, "sess-1", "ride-9")
    confirm.assert_awaited_once_with("drv-1", None, "sess-1", "trip-completed:ride-9", reason="trip_completed")


@pytest.mark.parametrize("outcome", [{"code": "DRIVER_OFFLINE"}, {"code": "SESSION_SUPERSEDED"}, RuntimeError("down")])
async def test_refresh_never_raises(outcome):
    confirm = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    with patch("backend.repositories.driver_availability_repo.confirm_driver_ready", confirm):
        await ride_complete._refresh_readiness_after_trip({"id": "drv-1"}, "sess-1", "ride-9")
    confirm.assert_awaited_once()


def test_complete_ride_takes_the_token_session():
    import inspect

    params = inspect.signature(ride_complete.complete_ride).parameters
    assert "token_session_id" in params


def test_only_the_driver_route_refreshes_readiness():
    """Rider and admin completion paths must not refresh readiness."""
    import pathlib

    backend = pathlib.Path(ride_complete.__file__).resolve().parents[2]
    for rel in ("routes/rides/lifecycle.py", "routes/admin/rides.py"):
        text = (backend / rel).read_text(encoding="utf-8")
        assert "trip_completed" not in text, rel
        assert "_refresh_readiness_after_trip" not in text, rel
    source = pathlib.Path(ride_complete.__file__).read_text(encoding="utf-8")
    call = source.index("spawn(_refresh_readiness_after_trip(")
    guard = source.rindex('isinstance(driver.get("controller_session_id"), str)', 0, call)
    assert call - guard < 200
