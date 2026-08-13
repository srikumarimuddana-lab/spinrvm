"""Additional targeted branch coverage for routes/rides/matching.py, on top
of tests/test_dispatch_match_attempt_branches.py and tests/test_dispatch_cascade.py
(ACTION_ITEMS.md A1).

Covers: match_driver_to_ride's outer recovery shell (both the "pending
offers exist -> no re-arm" and "pending lookup itself fails -> re-arm
anyway" branches), the requires_wav / 500-row-cap / presence-outer-exception
guards, the parent-service-area subscription inheritance branch, the
quota-exhausted filter branch, the cascade parent-area cascade-map
inheritance + requires_wav + presence-reachable branches, the ETA-ranking
timeout fallback, the max_offers claim-loop break, the rider/incentive/
service-area-polygon enrichment exception branches, the earnings-label
formatting fallback, and the single-offer timeout handler's miss-threshold
lookup exception, auto-offline branch (including its WS/push notify), the
driver-notify exception, and the offer-skip Redis-set exception.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


def _make_ride(vehicle_type_id="vt-std", service_area_id="area-1", status="searching", **kw):
    base = {
        "id": "ride-1",
        "rider_id": "rider-1",
        "vehicle_type_id": vehicle_type_id,
        "service_area_id": service_area_id,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "requires_wav": False,
        "status": status,
    }
    base.update(kw)
    return base


def _make_driver(driver_id, vehicle_type_id="vt-std", lat=52.14, lng=-106.68):
    return {
        "id": driver_id,
        "vehicle_type_id": vehicle_type_id,
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "lat": lat,
        "lng": lng,
        "average_rating": 4.8,
        "user_id": driver_id,
    }


def _rows_by_table(*, drivers=None, subscriptions=None, areas=None, cascade_drivers=None):
    """Build a ``get_rows`` side_effect keyed on TABLE, not call order.

    The dispatch path reads several tables (``drivers``, ``service_areas`` for the
    cross-service-area scope, ``driver_subscriptions``, ``subscription_plans`` …)
    and the sequence shifts whenever a filter is added. A positional
    ``side_effect=[...]`` list silently mis-assigns its values when that happens —
    the driver pool receives a subscription payload, or an intended exception
    fires on the wrong query — so these fakes match on intent instead.

    Any value may be an Exception instance, raised for that table.
    ``cascade_drivers``, when given, answers the second ``drivers`` query (the
    vehicle-cascade pool); the first gets ``drivers``.
    """
    areas = [{"id": "area-1", "parent_service_area_id": None}] if areas is None else areas
    seen_drivers = {"n": 0}

    async def _side_effect(table, filters=None, **kwargs):
        if table == "drivers":
            seen_drivers["n"] += 1
            value = cascade_drivers if (cascade_drivers is not None and seen_drivers["n"] > 1) else drivers
        elif table == "service_areas":
            value = areas
        elif table == "driver_subscriptions":
            value = subscriptions
        else:
            value = []
        if isinstance(value, BaseException):
            raise value
        return [] if value is None else value

    return _side_effect


# ── match_driver_to_ride outer recovery shell ───────────────────────────


async def test_match_driver_to_ride_no_pending_offers_rearms_retry():
    from backend.routes.rides.matching import match_driver_to_ride

    with (
        patch(
            "backend.routes.rides.matching._match_driver_to_ride_attempt",
            AsyncMock(side_effect=RuntimeError("mid-dispatch blowup")),
        ),
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])
        await match_driver_to_ride("ride-1", attempt=1)

    mock_retry.assert_called_once_with("ride-1", delay=30, attempt=2)


async def test_match_driver_to_ride_pending_offers_exist_skips_rearm():
    from backend.routes.rides.matching import match_driver_to_ride

    with (
        patch(
            "backend.routes.rides.matching._match_driver_to_ride_attempt",
            AsyncMock(side_effect=RuntimeError("mid-dispatch blowup")),
        ),
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[{"id": "off-1"}])
        await match_driver_to_ride("ride-1", attempt=0)

    mock_retry.assert_not_called()


async def test_match_driver_to_ride_pending_lookup_fails_rearms_anyway():
    from backend.routes.rides.matching import match_driver_to_ride

    with (
        patch(
            "backend.routes.rides.matching._match_driver_to_ride_attempt",
            AsyncMock(side_effect=RuntimeError("mid-dispatch blowup")),
        ),
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(side_effect=RuntimeError("ride_offers lookup also down"))
        await match_driver_to_ride("ride-1", attempt=0)

    mock_retry.assert_called_once_with("ride-1", delay=10, attempt=1)


# ── candidate-pool guards ────────────────────────────────────────────────


async def test_requires_wav_adds_wav_filter():
    ride = _make_ride(service_area_id=None, requires_wav=True)
    driver = _make_driver("drv-1")

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False)),
        ),
        patch("backend.routes.rides.matching._deps.filter_and_rank_drivers", return_value=[]),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[driver])
        from backend.routes.rides.matching import _match_driver_to_ride_attempt

        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    called_filter = mock_db.get_rows.call_args_list[0].args[1]
    assert called_filter.get("is_wav") is True


async def test_candidate_pool_hits_500_cap_logs_warning():
    """500-row cap warning branch — assert on the retry side-effect since the
    project logs via loguru (not stdlib logging, so caplog can't see it)."""
    ride = _make_ride(service_area_id=None)
    drivers = [_make_driver(f"drv-{i}") for i in range(500)]

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False)),
        ),
        patch("backend.routes.rides.matching._deps.filter_and_rank_drivers", return_value=[]),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=drivers)
        from backend.routes.rides.matching import _match_driver_to_ride_attempt

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert result is None
    mock_retry.assert_called_once()


async def test_presence_filter_outer_exception_fails_open():
    ride = _make_ride(service_area_id=None)
    driver = _make_driver("drv-1")

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, 1.0) for d in drivers],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(side_effect=RuntimeError("presence store exploded")),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.claim_driver_atomic = AsyncMock(return_value=False)
        from backend.routes.rides.matching import _match_driver_to_ride_attempt

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # Fails open -> driver still in pool -> claim attempted.
    assert result is None
    mock_db.claim_driver_atomic.assert_awaited_once_with("drv-1")


async def test_subscription_inherits_from_parent_area():
    """Child (airport sub-region) area has no subscription_required itself
    but its parent does -> the inherited flag still gates dispatch."""
    ride = _make_ride()
    driver = _make_driver("drv-1")
    child_area = {"id": "area-1", "subscription_required": False, "parent_service_area_id": "area-parent"}
    parent_area = {"id": "area-parent", "subscription_required": True}

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False)),
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(side_effect=[child_area, parent_area])
        # No active subs -> subscribed set stays empty -> driver filtered out
        # -> no eligible drivers -> retry. That still exercises the
        # parent-inheritance branch (385-389) before the empty-result path.
        mock_db.get_rows = AsyncMock(side_effect=[[driver], []])

        from backend.routes.rides.matching import _match_driver_to_ride_attempt

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert result is None
    mock_retry.assert_called_once()


async def test_quota_filter_drops_exhausted_drivers():
    ride = _make_ride()
    driver = _make_driver("drv-1")

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, 1.0) for d in drivers],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None])),
        patch("backend.utils.spinr_pass.area_timezone", AsyncMock(return_value="UTC")),
        patch("backend.utils.spinr_pass.exhausted_driver_ids", AsyncMock(return_value={"drv-1"})),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value={"id": "area-1", "subscription_required": False})
        mock_db.get_rows = AsyncMock(
            side_effect=_rows_by_table(
                drivers=[driver],
                subscriptions=[{"driver_id": "drv-1", "started_at": "2020-01-01", "rides_per_day": 5}],
            )
        )

        from backend.routes.rides.matching import _match_driver_to_ride_attempt

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert result is None
    mock_retry.assert_called_once()


# ── cascade extra branches ───────────────────────────────────────────────


async def test_cascade_requires_wav_and_parent_map_and_presence_reachable():
    xl_id = "vt-xl"
    ride = _make_ride(vehicle_type_id="vt-suv", requires_wav=True)
    xl_driver = _make_driver("drv-xl-1", xl_id)
    child_area = {
        "id": "area-1",
        "subscription_required": False,
        "vehicle_cascade_map": [],  # empty -> must inherit from parent
        "parent_service_area_id": "area-parent",
    }
    parent_area = {
        "id": "area-parent",
        "vehicle_cascade_map": [{"from": "vt-suv", "to": [xl_id]}],
    }

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, 1.0) for d in drivers],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=({"drv-xl-1"}, True)),
        ),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(side_effect=[child_area, parent_area])
        mock_db.get_rows = AsyncMock(
            side_effect=_rows_by_table(
                drivers=[],  # initial (empty) SUV pool
                cascade_drivers=[xl_driver],
                areas=[
                    {"id": "area-1", "parent_service_area_id": "area-parent"},
                    {"id": "area-parent", "parent_service_area_id": None},
                ],
            )
        )
        mock_db.claim_driver_atomic = AsyncMock(return_value=False)

        from backend.routes.rides.matching import _match_driver_to_ride_attempt

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # Cascade pool built with is_wav filter from the parent's inherited map.
    # Located by intent, not call index: the dispatch path also reads
    # service_areas, so a fixed position silently points at the wrong query.
    driver_calls = [
        c
        for c in mock_db.get_rows.call_args_list
        if c.args and c.args[0] == "drivers" and len(c.args) > 1 and isinstance(c.args[1], dict)
    ]
    assert len(driver_calls) >= 2, f"expected an initial and a cascade drivers query, got {driver_calls}"
    assert driver_calls[1].args[1].get("is_wav") is True
    assert result is None
    mock_retry.assert_called_once()


async def test_cascade_redis_filter_exception_is_non_fatal():
    xl_id = "vt-xl"
    ride = _make_ride(vehicle_type_id="vt-suv")
    xl_driver = _make_driver("drv-xl-1", xl_id)
    area = {
        "id": "area-1",
        "subscription_required": False,
        "vehicle_cascade_map": [{"from": "vt-suv", "to": [xl_id]}],
        "parent_service_area_id": None,
    }

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, 1.0) for d in drivers],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(side_effect=RuntimeError("cascade presence check failed")),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=area)
        mock_db.get_rows = AsyncMock(side_effect=[[], [xl_driver]])
        mock_db.claim_driver_atomic = AsyncMock(return_value=False)

        from backend.routes.rides.matching import _match_driver_to_ride_attempt

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # Cascade Redis filter blew up but the pool is still used unfiltered.
    assert result is None
    mock_db.claim_driver_atomic.assert_awaited_once_with("drv-xl-1")


# ── ETA ranking / claim loop / enrichment ────────────────────────────────


async def _run_full_dispatch(ride, driver, *, use_eta=False, eta_side_effect=None, max_offers=1):
    """Drives a full successful dispatch (claim -> insert offers -> notify)
    so the enrichment/ETA/claim-loop sections execute."""
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    fresh_driver = {**driver}
    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, max_offers, use_eta)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, 1.0) for d in drivers],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None])),
        patch("backend.routes.rides.matching._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.routes.rides.matching.sign_offer_card_token", return_value="tok"),
    ):
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.claim_driver_atomic = AsyncMock(return_value=True)
        mock_db.get_driver_by_id = AsyncMock(return_value=fresh_driver)
        mock_db.set_driver_available = AsyncMock()
        mock_db.run_sync = AsyncMock(return_value=None)
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Jamie"})

        if eta_side_effect is not None:
            with patch("backend.routes.rides.matching.batch_get_etas", eta_side_effect):
                result = await _match_driver_to_ride_attempt("ride-1", ride=ride)
        else:
            result = await _match_driver_to_ride_attempt("ride-1", ride=ride)
    return result, mock_db


async def test_eta_ranking_timeout_falls_back_to_haversine():
    ride = _make_ride(service_area_id=None, pickup_nav_lat=52.13, pickup_nav_lng=-106.67)
    driver = _make_driver("drv-1")

    result, mock_db = await _run_full_dispatch(
        ride,
        driver,
        use_eta=True,
        eta_side_effect=AsyncMock(side_effect=RuntimeError("maps API exploded")),
    )

    assert result is None  # match_driver_to_ride_attempt returns None on success too
    mock_db.claim_driver_atomic.assert_awaited_once_with("drv-1")


async def test_earnings_label_falls_back_on_bad_driver_earnings():
    ride = _make_ride(service_area_id=None, driver_earnings="not-a-number")
    driver = _make_driver("drv-1")

    result, mock_db = await _run_full_dispatch(ride, driver)

    assert result is None
    mock_db.claim_driver_atomic.assert_awaited_once_with("drv-1")


async def test_fetch_rider_exception_is_non_fatal():
    ride = _make_ride(service_area_id=None)
    driver = _make_driver("drv-1")

    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 1, False)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, 1.0) for d in drivers],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None])),
        patch("backend.routes.rides.matching._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.routes.rides.matching.sign_offer_card_token", return_value="tok"),
    ):
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.claim_driver_atomic = AsyncMock(return_value=True)
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.set_driver_available = AsyncMock()
        mock_db.run_sync = AsyncMock(return_value=None)
        mock_db.get_user_by_id = AsyncMock(side_effect=RuntimeError("rider lookup failed"))

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert result is None


async def test_service_area_polygon_fetch_exception_is_non_fatal():
    ride = _make_ride(service_area_id="area-1")
    driver = _make_driver("drv-1")

    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 1, False)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, 1.0) for d in drivers],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None])),
        patch("backend.routes.rides.matching._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.routes.rides.matching.sign_offer_card_token", return_value="tok"),
    ):
        # First find_one call (subscription-required check) returns falsy;
        # the polygon fetch's own find_one raises.
        mock_db.find_one = AsyncMock(side_effect=[None, RuntimeError("service_areas polygon lookup failed")])
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.claim_driver_atomic = AsyncMock(return_value=True)
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.set_driver_available = AsyncMock()
        mock_db.run_sync = AsyncMock(return_value=None)
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Jamie"})

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert result is None


async def test_max_offers_breaks_claim_loop():
    ride = _make_ride(service_area_id=None)
    d1, d2 = _make_driver("drv-1"), _make_driver("drv-2")

    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 1, False)),  # max_offers=1
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, float(i)) for i, d in enumerate(drivers)],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None, None])),
        patch("backend.routes.rides.matching._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.routes.rides.matching.sign_offer_card_token", return_value="tok"),
    ):
        mock_db.get_rows = AsyncMock(return_value=[d1, d2])
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.claim_driver_atomic = AsyncMock(return_value=True)
        mock_db.get_driver_by_id = AsyncMock(side_effect=[d1, d2])
        mock_db.set_driver_available = AsyncMock()
        mock_db.run_sync = AsyncMock(return_value=None)
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Jamie"})

        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # Only one driver claimed even though two were eligible — the loop broke
    # once max_offers was reached.
    mock_db.claim_driver_atomic.assert_awaited_once_with("drv-1")


# ── _offer_timeout_handler ───────────────────────────────────────────────


async def test_offer_timeout_handler_miss_threshold_lookup_exception_uses_default():
    from backend.routes.rides.matching import _offer_timeout_handler

    ride = {"id": "ride-1", "status": "driver_assigned", "driver_id": "drv-1", "rider_id": "rider-1"}

    with (
        patch("backend.routes.rides.matching.asyncio.sleep", AsyncMock()),
        patch("backend.routes.rides.matching._deps") as mock_deps,
        patch(
            "backend.utils.driver_presence.increment_miss_streak",
            AsyncMock(return_value=1),
        ),
    ):
        mock_deps.db.find_one = AsyncMock(return_value=ride)
        mock_deps.db.update_one = AsyncMock()
        mock_deps.get_app_settings = AsyncMock(side_effect=RuntimeError("settings down"))
        mock_deps.db_supabase.set_driver_available = AsyncMock(return_value={"is_available": True})
        mock_deps.record_period_transition = AsyncMock()
        mock_deps.manager.send_personal_message = AsyncMock()
        mock_deps.db_supabase.get_driver_by_id = AsyncMock(return_value={"user_id": "u-1"})

        with (
            patch("backend.utils.redis_client.redis_set", AsyncMock()),
            patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()),
        ):
            await _offer_timeout_handler("ride-1", "drv-1", "rider-1", timeout_seconds=1)

    # Below default threshold (3) with miss_count=1 -> normal release path ran.
    mock_deps.db_supabase.set_driver_available.assert_awaited_once_with("drv-1", available=True)


async def test_offer_timeout_handler_auto_offline_notifies_and_pushes():
    from backend.routes.rides.matching import _offer_timeout_handler

    ride = {"id": "ride-1", "status": "driver_assigned", "driver_id": "drv-1", "rider_id": "rider-1"}

    with (
        patch("backend.routes.rides.matching.asyncio.sleep", AsyncMock()),
        patch("backend.routes.rides.matching._deps") as mock_deps,
        patch(
            "backend.utils.driver_presence.increment_miss_streak",
            AsyncMock(return_value=3),
        ),
        patch("backend.utils.driver_presence.clear_presence", AsyncMock()),
        patch("backend.utils.driver_presence.reset_miss_streak", AsyncMock()),
    ):
        mock_deps.db.find_one = AsyncMock(return_value=ride)
        mock_deps.db.update_one = AsyncMock()
        mock_deps.get_app_settings = AsyncMock(return_value={"auto_offline_miss_threshold": 3})
        mock_deps.record_period_transition = AsyncMock()
        mock_deps.manager.send_personal_message = AsyncMock()
        mock_deps.send_push_notification = AsyncMock()
        mock_deps.db_supabase.get_driver_by_id = AsyncMock(return_value={"user_id": "u-1"})

        with (
            patch("backend.utils.redis_client.redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
            patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()),
        ):
            await _offer_timeout_handler("ride-1", "drv-1", "rider-1", timeout_seconds=1)

    mock_deps.send_push_notification.assert_awaited_once()
    # ACTION_ITEMS.md N10 (driver batch): the auto-offline push is
    # driver-directed and must pass target_app="driver".
    push_kwargs = mock_deps.send_push_notification.await_args.kwargs
    assert push_kwargs["target_app"] == "driver"
    # auto_offline WS message sent to the driver.
    ws_calls = mock_deps.manager.send_personal_message.await_args_list
    assert any(c.args[0].get("type") == "auto_offline" for c in ws_calls)


async def test_offer_timeout_handler_driver_notify_exception_is_swallowed():
    from backend.routes.rides.matching import _offer_timeout_handler

    ride = {"id": "ride-1", "status": "driver_assigned", "driver_id": "drv-1", "rider_id": "rider-1"}

    with (
        patch("backend.routes.rides.matching.asyncio.sleep", AsyncMock()),
        patch("backend.routes.rides.matching._deps") as mock_deps,
        patch(
            "backend.utils.driver_presence.increment_miss_streak",
            AsyncMock(return_value=1),
        ),
    ):
        mock_deps.db.find_one = AsyncMock(return_value=ride)
        mock_deps.db.update_one = AsyncMock()
        mock_deps.get_app_settings = AsyncMock(return_value={"auto_offline_miss_threshold": 3})
        mock_deps.db_supabase.set_driver_available = AsyncMock(return_value={"is_available": True})
        mock_deps.record_period_transition = AsyncMock()

        async def _ws_side_effect(payload, key):
            if key.startswith("rider_"):
                return None
            raise RuntimeError("driver ws unreachable")

        mock_deps.manager.send_personal_message = AsyncMock(side_effect=_ws_side_effect)
        mock_deps.db_supabase.get_driver_by_id = AsyncMock(return_value={"user_id": "u-1"})

        with (
            patch("backend.utils.redis_client.redis_set", AsyncMock()),
            patch("backend.routes.rides.matching.match_driver_to_ride", AsyncMock()),
        ):
            # Should not raise despite the driver-notify exception.
            await _offer_timeout_handler("ride-1", "drv-1", "rider-1", timeout_seconds=1)

    mock_deps.db_supabase.set_driver_available.assert_awaited_once()
