"""Durable already-offered filter on the primary dispatch path.

ride_offers is UNIQUE (ride_id, driver_id) (migration 100). A driver already
offered a ride must never be ranked for it again: on the PostgREST claim path
the batch ride_offers insert would fail and release the whole batch. The Redis
offer-skip key covered this only for 300 s and not at all during a Redis
outage (its MGET fails open). `_already_offered_driver_ids` reads ride_offers
instead.

conftest's `_stub_already_offered_driver_ids` stubs the read for every other
test module; this module is exempt so the real helper runs here.
Harness mirrors test_dispatch_presence_failopen.py.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.tests._factories import close_spawned_coro

pytestmark = pytest.mark.anyio


def _ride() -> dict:
    return {
        "id": "ride-1",
        "rider_id": "rider-1",
        "vehicle_type_id": "vt-standard",
        "service_area_id": "area-1",
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "requires_wav": False,
        "status": "searching",
    }


def _driver(driver_id: str) -> dict:
    return {
        "id": driver_id,
        "user_id": driver_id,
        "vehicle_type_id": "vt-standard",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "lat": 52.14,
        "lng": -106.68,
        "rating": 4.8,
        "average_rating": 4.8,
    }


def _get_rows(drivers, offer_rows, offers_error=None):
    async def _fake(table, filters=None, **_kw):
        if table == "ride_offers":
            if offers_error:
                raise offers_error
            assert filters == {"ride_id": "ride-1"}
            return offer_rows
        if table == "drivers":
            return drivers
        return []

    return AsyncMock(side_effect=_fake)


async def _ranked_ids(drivers, offer_rows, *, mget_raises=False, offers_error=None) -> set:
    from backend.routes.rides import match_driver_to_ride

    ranked: list = []

    def _record_rank(ride_arg, pool, *a, **kw):
        ranked.append(list(pool))
        return [(d, 1.0) for d in pool]

    service_area = {"id": "area-1", "subscription_required": False, "vehicle_cascade_map": []}
    with (
        patch("backend.db_supabase.get_rows", _get_rows(drivers, offer_rows, offers_error)),
        patch("backend.db_supabase.find_one", AsyncMock(return_value=service_area)),
        # `legacy` is the provider whose candidate read goes through get_rows
        # (see test_dispatch_presence_failopen.py for why it is pinned).
        patch(
            "backend.routes.rides._deps.get_app_settings",
            AsyncMock(return_value={"dispatch_geo_provider": "legacy"}),
        ),
        patch("backend.routes.rides._deps.filter_and_rank_drivers", side_effect=_record_rank),
        patch("backend.routes.rides.matching._dispatch_retry", new_callable=AsyncMock),
        patch("backend.routes.rides._deps.asyncio.create_task", side_effect=close_spawned_coro),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            new_callable=AsyncMock,
            return_value=(set(), False),
        ),
        patch(
            "backend.utils.redis_client.redis_mget",
            new_callable=AsyncMock,
            side_effect=(RuntimeError("redis down") if mget_raises else None),
            return_value=(None if mget_raises else [None] * max(len(drivers), 1)),
        ),
        patch("backend.utils.redis_client._get_redis", new_callable=AsyncMock, return_value=None),
    ):
        await match_driver_to_ride("ride-1", ride=_ride())

    return {d["id"] for pool in ranked for d in pool}


async def test_driver_with_an_offer_row_is_not_ranked_again():
    # Redis skip key already expired (MGET returns nothing) — the DB row still excludes.
    ids = await _ranked_ids([_driver("d1"), _driver("d2")], [{"driver_id": "d1"}])
    assert ids == {"d2"}


async def test_redis_outage_still_excludes_already_offered_drivers():
    ids = await _ranked_ids(
        [_driver("d1"), _driver("d2")],
        [{"driver_id": "d1"}, {"driver_id": "d2"}],
        mget_raises=True,
    )
    assert ids == set()


async def test_no_offer_rows_keeps_the_pool():
    ids = await _ranked_ids([_driver("d1"), _driver("d2")], [])
    assert ids == {"d1", "d2"}


async def test_offer_read_failure_falls_back_to_redis_filter_only():
    ids = await _ranked_ids(
        [_driver("d1"), _driver("d2")],
        [],
        offers_error=RuntimeError("db blip"),
    )
    assert ids == {"d1", "d2"}


async def test_helper_reads_driver_ids_for_the_ride():
    from backend.routes.rides import matching

    rows = [{"driver_id": "d1"}, {"driver_id": None}, {"driver_id": "d2"}, {"driver_id": "d1"}]
    get_rows = AsyncMock(return_value=rows)
    with patch("backend.db_supabase.get_rows", get_rows):
        ids = await matching._already_offered_driver_ids("ride-9")

    assert ids == {"d1", "d2"}
    args, kwargs = get_rows.await_args
    assert args[:2] == ("ride_offers", {"ride_id": "ride-9"})
    assert kwargs["columns"] == "driver_id"
