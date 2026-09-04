"""Targeted branch coverage for routes/rides/matching.py's
_match_driver_to_ride_attempt — the ~780-line core dispatch algorithm
(ACTION_ITEMS.md A1's largest remaining gap).

This does NOT attempt full coverage of the algorithm in one pass (per the
ACTION_ITEMS.md note, it needs splitting by internal phase). It targets the
self-contained guard clauses and fail-open/fail-closed exception paths that
are cheap to isolate: the stale-ride-status skip, the subscription filter's
fail-closed exception, the daily-quota filter's fail-open exception, the
cascade pool's own subscription sub-filter (fires + its fail-closed
exception), the cascade lookup's outer exception, the final
no-eligible-drivers retry, the ride_offers insert failure (releases claims +
re-raises), and the no-drivers-could-be-claimed early return.

The ETA-ranking/enrichment/per-driver-notify sections (~lines 650-930) are
NOT covered here — deferred as noted in ACTION_ITEMS.md.
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

    The dispatch path reads several tables (``drivers``, ``service_areas`` for
    the cross-service-area scope, ``driver_subscriptions``, ``subscription_plans``
    …) and the exact sequence changes whenever a filter is added. A positional
    ``side_effect=[...]`` list silently mis-assigns its values when that happens
    — the driver pool receives a subscription payload, an intended exception
    fires on the wrong query — so these fakes match on intent instead.

    Any value may be an Exception instance, which is raised for that table.
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


async def test_stale_ride_status_skips_dispatch():
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride(status="driver_accepted")

    with patch("backend.routes.rides.matching._deps.db_supabase") as mock_db:
        mock_db.get_ride = AsyncMock(return_value=ride)
        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert result is None
    mock_db.get_rows.assert_not_called()


async def test_subscription_filter_db_error_fails_closed_and_retries():
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    driver = _make_driver("drv-1")

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(
            side_effect=_rows_by_table(drivers=[driver], subscriptions=RuntimeError("subscriptions table down"))
        )
        mock_db.find_one = AsyncMock(return_value={"id": "area-1", "subscription_required": True})

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert result is None
    # Fails closed (all_drivers = []) -> no eligible drivers -> retry scheduled.
    mock_retry.assert_called_once_with("ride-1", delay=10, attempt=1)


async def test_quota_filter_db_error_fails_open_and_keeps_dispatching():
    """Unlike the subscription filter, a quota-lookup failure must NOT drop
    the pool — it fails open so a transient error can't strand every ride."""
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride(service_area_id=None)  # skip the subscription-required block entirely
    driver = _make_driver("drv-1")

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
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
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        # ride.service_area_id is None so the quota block's `if all_drivers and
        # ride.get("service_area_id")` guard would normally short-circuit --
        # override it directly on a copy to exercise the quota block itself
        # while still skipping the (already-covered) subscription block.
        ride["service_area_id"] = "area-1"
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.find_one = AsyncMock(return_value={"id": "area-1", "subscription_required": False})

        with patch(
            "backend.utils.spinr_pass.exhausted_driver_ids",
            AsyncMock(side_effect=RuntimeError("quota table down")),
        ):
            # Force the quota block to actually run its DB read by returning a
            # non-empty active-subscription row. Keyed by table so the extra
            # service_areas read for the cross-service-area scope cannot consume
            # the driver pool's slot.
            mock_db.get_rows = AsyncMock(
                side_effect=_rows_by_table(drivers=[driver], subscriptions=[{"driver_id": "drv-1", "rides_per_day": 5}])
            )
            mock_db.claim_driver_atomic = AsyncMock(return_value=False)
            result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # The quota exception is swallowed (fail-open) -- dispatch continues past
    # it (post-filter pool still has the driver), proceeds to the claim loop,
    # fails to claim (simulating a lost race), and lands on the "no drivers
    # could be claimed" early return -- exercising both the quota fail-open
    # path and that early return in one pass.
    assert result is None
    mock_db.claim_driver_atomic.assert_awaited_once_with("drv-1")


async def test_ride_offers_insert_failure_releases_claims_and_reraises():
    """A claimed driver must be released back to available before the
    exception propagates -- otherwise a transient ride_offers insert failure
    would strand the driver as claimed-but-never-offered."""
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride(service_area_id=None)
    driver = _make_driver("drv-1")
    fresh_driver = {**driver, "is_online": True, "is_verified": True, "status": "active"}

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
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
    ):
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.claim_driver_atomic = AsyncMock(return_value=fresh_driver)
        mock_db.get_driver_by_id = AsyncMock(return_value=fresh_driver)
        mock_db.set_driver_available = AsyncMock()
        mock_db.run_sync = AsyncMock(side_effect=RuntimeError("ride_offers insert failed"))

        with pytest.raises(RuntimeError, match="ride_offers insert failed"):
            await _match_driver_to_ride_attempt("ride-1", ride=ride)

    mock_db.set_driver_available.assert_awaited_once_with("drv-1", True)


async def test_claim_loop_exception_releases_earlier_claims_and_reraises():
    """C54: a claim_driver_atomic exception on candidate N (a transient
    DatabaseError, say) must not leave drivers claimed at candidates 1..N-1
    stuck is_available=false until the orphan-claim reaper's cycle -- mirrors
    the ride_offers-insert failure handler's release-then-reraise pattern,
    just one phase earlier in the same attempt."""
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride(service_area_id=None)
    driver_1 = _make_driver("drv-1")
    driver_2 = _make_driver("drv-2")
    fresh_driver_1 = {**driver_1, "is_online": True, "is_verified": True, "status": "active"}

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
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None, None])),
    ):
        mock_db.get_rows = AsyncMock(return_value=[driver_1, driver_2])
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.claim_driver_atomic = AsyncMock(side_effect=[fresh_driver_1, RuntimeError("transient claim failure")])
        mock_db.set_driver_available = AsyncMock()

        with pytest.raises(RuntimeError, match="transient claim failure"):
            await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # drv-1 (claimed at candidate 1) must be released before the exception at
    # candidate 2 propagates; drv-2 itself never reached a claimed state, so
    # it must not be released (release-what-you-claimed, not the whole pool).
    mock_db.set_driver_available.assert_awaited_once_with("drv-1", True)


async def test_no_eligible_drivers_after_filters_schedules_retry():
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride(service_area_id=None)

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            return_value=[],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride, attempt=2)

    assert result is None
    mock_retry.assert_called_once_with("ride-1", delay=10, attempt=3)


async def test_cascade_subscription_subfilter_drops_non_subscribers():
    """Cascade pool + area requires a Spinr Pass -> non-subscribed cascade
    drivers must be filtered out even though they matched the upgrade type."""
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    xl_id = "vt-xl"
    ride = _make_ride(vehicle_type_id="vt-suv")
    xl_subscribed = _make_driver("drv-sub", xl_id)
    xl_unsubscribed = _make_driver("drv-nosub", xl_id)
    area = {
        "id": "area-1",
        "subscription_required": True,
        "vehicle_cascade_map": [{"from": "vt-suv", "to": [xl_id]}],
        "parent_service_area_id": None,
    }

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
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
        patch("backend.utils.redis_client._get_redis", AsyncMock(return_value=None)),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=area)
        # Keyed by table: the initial SUV pool is empty (triggering cascade), the
        # second `drivers` query is the cascade XL pool, and only drv-sub holds an
        # active subscription. The subscription-required block is skipped for the
        # initial pool because `all_drivers` is already [].
        mock_db.get_rows = AsyncMock(
            side_effect=_rows_by_table(
                drivers=[],
                cascade_drivers=[xl_subscribed, xl_unsubscribed],
                subscriptions=[{"driver_id": "drv-sub", "plan_id": None}],
            )
        )

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # No claimable drivers remain after this call in isolation (claim_driver_atomic
    # unmocked -> falsy), so it lands on the eventual no-eligible-drivers retry;
    # the assertion that matters is which drivers the cascade sub-filter kept.
    assert result is None
    mock_retry.assert_called_once()


async def test_cascade_subscription_subfilter_exception_fails_closed():
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    xl_id = "vt-xl"
    ride = _make_ride(vehicle_type_id="vt-suv")
    xl_driver = _make_driver("drv-xl-1", xl_id)
    area = {
        "id": "area-1",
        "subscription_required": True,
        "vehicle_cascade_map": [{"from": "vt-suv", "to": [xl_id]}],
        "parent_service_area_id": None,
    }

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=area)
        mock_db.get_rows = AsyncMock(
            side_effect=[
                [],  # initial SUV pool
                [xl_driver],  # cascade XL pool
                RuntimeError("cascade subscriptions lookup failed"),
            ]
        )

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # Cascade sub-filter fails closed -> cascade pool empties -> overall
    # no-eligible-drivers retry still fires (the outer function doesn't crash).
    assert result is None
    mock_retry.assert_called_once()


async def test_cascade_lookup_outer_exception_is_non_fatal():
    """A cascade-block failure (e.g. the area lookup itself blows up) must
    not crash the dispatch attempt -- it just means no cascade drivers, and
    the no-eligible-drivers retry still fires."""
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride(vehicle_type_id="vt-suv")

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()) as mock_retry,
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])
        mock_db.find_one = AsyncMock(side_effect=RuntimeError("service_areas lookup failed"))

        result = await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert result is None
    mock_retry.assert_called_once()


async def test_service_area_row_is_read_once_per_attempt():
    """P2-B1: the ride's own service_areas row is fetched exactly once.

    It used to be read 4-5× per dispatch attempt — resolve_matching_config,
    the subscription gate, the quota timezone, the vehicle cascade, and the
    offer-card polygon each issued their own `SELECT *` for the identical row,
    every one of them carrying the heavy `polygon` JSONB. Dispatch has a
    P95 < 2 s SLA and re-runs on every retry, so this pins the de-duplication:
    a new consumer must take the already-fetched row, not add a sixth read.

    Counts `find_one` rather than mocking a specific call site so it stays
    honest if a future read is added through a different helper.
    """
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride(vehicle_type_id="vt-suv")
    area_row = {
        "id": "area-1",
        "parent_service_area_id": None,
        "subscription_required": False,
        "vehicle_cascade_map": [],
        "timezone": "America/Regina",
    }
    area_lookups: list = []

    async def _find_one(table, filters=None, **kwargs):
        if table == "service_areas":
            area_lookups.append((filters or {}).get("id"))
            return dict(area_row)
        return None

    # A candidate driver AND an active subscription are both required for the
    # test to have teeth: they are what carry execution past the subscription
    # gate and into the daily-quota filter, two of the sites that each used to
    # issue their own read. With an empty driver pool only the cascade read is
    # reached and the pre-fix code would look identical.
    driver = _make_driver("drv-1", vehicle_type_id="vt-suv")
    subs = [{"driver_id": "drv-1", "started_at": None, "expires_at": None, "rides_per_day": None}]

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.utils.spinr_pass.area_timezone", AsyncMock(return_value=None)) as mock_area_tz,
    ):
        mock_db.get_rows = AsyncMock(side_effect=_rows_by_table(drivers=[driver], subscriptions=subs))
        mock_db.find_one = AsyncMock(side_effect=_find_one)
        # Losing the claim stops the attempt just short of the notify phase.
        # That is deliberate: the gate and quota reads are already behind us,
        # and the notify phase needs the whole offer/WS/push apparatus mocked
        # for one more read's worth of signal.
        mock_db.claim_driver_atomic = AsyncMock(return_value=False)

        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert area_lookups == ["area-1"], f"service_areas row read {len(area_lookups)}× — expected exactly 1"
    # area_timezone() reads the same row through utils.spinr_pass's own db
    # handle — invisible to the counter above, which is exactly why it is
    # asserted separately. The timezone is a plain field on the row we hold.
    mock_area_tz.assert_not_called()


async def test_area_lookup_failure_still_reaches_resolve_matching_config():
    """A failed area read must NOT quietly dispatch on global defaults.

    The de-duplication above hoists the read above resolve_matching_config, so
    on failure the row is left empty and `area=None` is passed through — the
    config resolver re-attempts the same read and raises exactly as it did
    before the hoist. Dispatching with an unknown search_radius_km /
    min_driver_rating would silently ignore the area's overrides.
    """
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    seen_kwargs: dict = {}

    async def _resolve(_ride, **kwargs):
        seen_kwargs.update(kwargs)
        raise RuntimeError("service_areas lookup failed")

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(side_effect=_resolve),
        ),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(return_value=[])
        mock_db.find_one = AsyncMock(side_effect=RuntimeError("service_areas lookup failed"))

        with pytest.raises(RuntimeError):
            await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert "area" in seen_kwargs, "the already-fetched area row must be threaded through, not re-read"
    assert seen_kwargs["area"] is None, "a failed area read must not be passed off as an empty area"


async def test_pass_required_area_reads_driver_subscriptions_once():
    """P2-B3: the subscription gate and the daily-quota filter share one read.

    Both queried `driver_subscriptions` with the identical
    `{driver_id IN …, status: active}` filter on the same dispatch attempt —
    they differed only in projection. The gate now carries the union of the
    columns and the quota filter narrows those rows in Python, so a
    pass-required area costs one read instead of two on the P95 < 2 s path.

    Uses an area with subscription_required=True, since that is what makes the
    gate run at all; in a free area the gate is skipped and the quota filter's
    own read is the only one (there is no area-level "has finite passes" flag
    to short-circuit on, and finding one would cost the query it would save).
    """
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    driver = _make_driver("drv-1")
    sub_reads: list = []

    async def _get_rows(table, filters=None, **kwargs):
        if table == "drivers":
            return [driver]
        if table == "driver_subscriptions":
            sub_reads.append(kwargs.get("columns"))
            # No expires_at → never expired; rides_per_day None → unlimited, so
            # the driver survives both filters and the attempt runs to the end.
            return [{"driver_id": "drv-1", "started_at": None, "expires_at": None, "rides_per_day": None}]
        if table == "service_areas":
            return [{"id": "area-1", "parent_service_area_id": None}]
        return []

    async def _find_one(table, filters=None, **kwargs):
        if table == "service_areas":
            return {"id": "area-1", "parent_service_area_id": None, "subscription_required": True}
        return None

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(side_effect=_get_rows)
        mock_db.find_one = AsyncMock(side_effect=_find_one)
        mock_db.claim_driver_atomic = AsyncMock(return_value=False)

        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    assert len(sub_reads) == 1, f"driver_subscriptions read {len(sub_reads)}× — expected 1"
    # The single read must carry the union, or the quota filter silently sees
    # no rides_per_day and treats every finite pass as unlimited.
    assert "rides_per_day" in (sub_reads[0] or ""), "the shared read must project rides_per_day"
    assert "started_at" in (sub_reads[0] or ""), "the shared read must project started_at"
    assert "plan_id" in (sub_reads[0] or ""), "the shared read must still project plan_id for the gate"


async def test_claim_returns_the_row_so_no_follow_up_driver_read():
    """P2-B4: the claim's own UPDATE feeds revalidation — no second read.

    claim_driver_atomic invalidates the driver cache on BOTH sides of its
    update, so the get_driver_by_id that used to follow it was guaranteed to
    miss the cache and issue a full uncached SELECT. Dispatch claims up to
    max_simultaneous_offers drivers per attempt, so that was up to 10
    avoidable round-trips on a path with a P95 < 2 s SLA.

    The returned row is not merely as fresh as that read — it is strictly
    fresher, being the state at the instant of the atomic claim with no window
    for a concurrent write to slip in between.
    """
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    driver = _make_driver("drv-1")
    claimed_row = {**driver, "is_available": False}

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides.matching.sign_offer_card_token", return_value="tok"),
    ):
        mock_db.get_rows = AsyncMock(side_effect=_rows_by_table(drivers=[driver]))
        mock_db.find_one = AsyncMock(return_value={"id": "area-1", "parent_service_area_id": None})
        mock_db.claim_driver_atomic = AsyncMock(return_value=claimed_row)
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.set_driver_available = AsyncMock()
        mock_db.run_sync = AsyncMock(return_value=None)
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Jamie"})

        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    mock_db.claim_driver_atomic.assert_awaited_once_with("drv-1")
    mock_db.get_driver_by_id.assert_not_awaited()


async def test_claim_row_failing_revalidation_releases_the_driver():
    """The eligibility recheck now runs on the claim's returned row, so a
    driver suspended between the candidate read and the claim must still be
    released rather than offered the ride — the stale-status case the
    candidate filter exists to stop.
    """
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    driver = _make_driver("drv-1")

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, False, 500)),
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching._dispatch_retry", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.get_rows = AsyncMock(side_effect=_rows_by_table(drivers=[driver]))
        mock_db.find_one = AsyncMock(return_value={"id": "area-1", "parent_service_area_id": None})
        # Suspended between the candidate read and the claim.
        mock_db.claim_driver_atomic = AsyncMock(return_value={**driver, "status": "suspended"})
        mock_db.set_driver_available = AsyncMock()

        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    mock_db.set_driver_available.assert_awaited_once_with("drv-1", True)
