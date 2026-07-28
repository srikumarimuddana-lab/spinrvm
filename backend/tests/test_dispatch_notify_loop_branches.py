"""Targeted branch coverage for routes/rides/matching.py's
_match_driver_to_ride_attempt notify loop and its surrounding
ETA-ranking/parallel-enrichment section (~lines 650-930) — the remaining
gap after PR #2557 covered the file's earlier guard clauses.

Builds a full happy-path claim -> offer-insert -> enrichment -> notify
sequence via a fake Supabase query-builder chain (mirrors production's
``supabase.table(...).select(...)...execute()`` shape), then targets the
notify loop's specific fail-open exception paths (quest-progress lookup,
offer-card URL signing, FCM push) individually.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio


class _Chain:
    """Minimal stand-in for a supabase-py PostgrestQueryBuilder chain --
    every builder method returns self; .execute() returns the canned response."""

    def __init__(self, response):
        self._response = response

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def or_(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def insert(self, *a, **kw):
        return self

    def update(self, *a, **kw):
        return self

    def execute(self):
        return self._response


def _table_router(responses: dict):
    def _table(name):
        return _Chain(responses.get(name, MagicMock(data=[])))

    return _table


def _make_ride(vehicle_type_id="vt-std", service_area_id="area-1", **kw):
    base = {
        "id": "ride-1",
        "rider_id": "rider-1",
        "vehicle_type_id": vehicle_type_id,
        "service_area_id": service_area_id,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "pickup_address": "100 Main St",
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "dropoff_address": "200 Broadway Ave",
        "requires_wav": False,
        "quiet_mode": False,
        "status": "searching",
        "driver_earnings": 12.5,
        "distance_km": 5.0,
        "duration_minutes": 12,
        "surge_multiplier": 1.0,
        "payment_method": "card",
        "planned_route_polyline": None,
    }
    base.update(kw)
    return base


def _make_driver(driver_id="drv-1"):
    return {
        "id": driver_id,
        "user_id": f"{driver_id}-user",
        "vehicle_type_id": "vt-std",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "lat": 52.14,
        "lng": -106.68,
        "average_rating": 4.8,
    }


def _base_patches(table_responses=None):
    """Returns the tuple of context managers common to every notify-loop test."""
    responses = table_responses or {}
    return (
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
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch(
            "backend.routes.rides.matching._deps._settings.PUBLIC_API_BASE_URL",
            "https://api.spinr.test",
            create=True,
        ),
        patch("backend.routes.rides.matching.sign_offer_card_token", return_value="tok-abc"),
    )


async def test_full_notify_loop_happy_path_builds_dispatch_payload():
    """End-to-end: claim -> insert offer -> enrichment -> per-driver notify,
    with a quest in progress and an active incentive, both surfaced in the
    WS payload -- covers the bulk of the ETA/claim/enrichment/notify block."""
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    driver = _make_driver()

    table_responses = {
        "quest_progress": MagicMock(
            data=[
                {
                    "current_value": 3,
                    "status": "active",
                    "quest": {"title": "Weekly Streak", "target_value": 5, "reward_amount": 10},
                }
            ]
        ),
        "ride_incentives": MagicMock(
            data=[
                {
                    "id": "inc-1",
                    "name": "Peak Bonus",
                    "bonus_amount": "5.00",
                    "incentive_type": "per_ride",
                    "service_area_id": None,
                    "vehicle_type_id": None,
                }
            ]
        ),
    }

    with ExitStack() as stack:
        mock_db = stack.enter_context(patch("backend.routes.rides.matching._deps.db_supabase"))
        for p in _base_patches(table_responses):
            stack.enter_context(p)

        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.find_one = AsyncMock(return_value={"id": "area-1", "polygon": None})
        mock_db.claim_driver_atomic = AsyncMock(return_value=True)
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Alex", "rating": 4.9, "profile_image": None})
        mock_db.supabase.table = MagicMock(side_effect=_table_router(table_responses))
        mock_db.run_sync = AsyncMock(side_effect=lambda fn: fn())
        mock_manager = MagicMock()
        mock_manager.send_personal_message = AsyncMock()
        stack.enter_context(patch("backend.routes.rides.matching._deps.manager", mock_manager))

        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    mock_manager.send_personal_message.assert_awaited_once()
    payload, target = mock_manager.send_personal_message.await_args.args
    assert target == f"driver_{driver['user_id']}"
    assert payload["type"] == "new_ride_assignment"
    assert payload["quest_hint"]["title"] == "Weekly Streak"
    assert payload["quest_hint"]["progress_pct"] == 60.0
    assert payload["incentives"][0]["name"] == "Peak Bonus"
    assert payload["total_bonus"] == 5.0
    assert payload["offer_card_url"] == "https://api.spinr.test/api/v1/offer-cards/ride-1.png?t=tok-abc"


async def test_quest_progress_lookup_failure_is_non_fatal():
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    driver = _make_driver()

    with ExitStack() as stack:
        mock_db = stack.enter_context(patch("backend.routes.rides.matching._deps.db_supabase"))
        for p in _base_patches():
            stack.enter_context(p)

        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.claim_driver_atomic = AsyncMock(return_value=True)
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Alex"})

        def _run_sync(fn):
            # First call is the ride_offers insert (must not raise); the
            # quest_progress select (inside the notify loop) raises.
            if not hasattr(_run_sync, "_called"):
                _run_sync._called = True
                return MagicMock(data=[])
            raise RuntimeError("quest_progress table down")

        mock_db.run_sync = AsyncMock(side_effect=_run_sync)
        mock_db.supabase.table = MagicMock(side_effect=lambda name: MagicMock())
        mock_manager = MagicMock()
        mock_manager.send_personal_message = AsyncMock()
        stack.enter_context(patch("backend.routes.rides.matching._deps.manager", mock_manager))

        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # Notify still proceeds despite the quest lookup failure.
    mock_manager.send_personal_message.assert_awaited_once()
    payload, _ = mock_manager.send_personal_message.await_args.args
    assert payload["quest_hint"] is None


async def test_offer_card_url_signing_failure_is_non_fatal():
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    driver = _make_driver()

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
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch(
            "backend.routes.rides.matching.sign_offer_card_token",
            side_effect=RuntimeError("signing key unavailable"),
        ),
    ):
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.claim_driver_atomic = AsyncMock(return_value=True)
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Alex"})
        mock_db.run_sync = AsyncMock(return_value=MagicMock(data=[]))
        mock_db.supabase.table = MagicMock(side_effect=lambda name: MagicMock())
        mock_manager = MagicMock()
        mock_manager.send_personal_message = AsyncMock()

        with patch("backend.routes.rides.matching._deps.manager", mock_manager):
            await _match_driver_to_ride_attempt("ride-1", ride=ride)

    mock_manager.send_personal_message.assert_awaited_once()
    payload, _ = mock_manager.send_personal_message.await_args.args
    assert payload["offer_card_url"] is None


async def test_eta_ranking_failure_falls_back_to_haversine():
    """use_eta=True but the Distance-Matrix batch call blows up -- ranking
    must fall back to haversine-derived ETAs, not crash the dispatch."""
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    driver = _make_driver()

    with (
        patch("backend.routes.rides.matching._deps.db_supabase") as mock_db,
        patch("backend.routes.rides.matching._deps.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "key"})),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 3, True)),  # use_eta=True
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
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch(
            "backend.routes.rides.matching._deps._settings.PUBLIC_API_BASE_URL",
            "https://api.spinr.test",
            create=True,
        ),
        patch("backend.routes.rides.matching.sign_offer_card_token", return_value="tok-abc"),
        patch(
            "backend.routes.rides.matching._deps.batch_get_etas",
            AsyncMock(side_effect=RuntimeError("distance matrix API down")),
        ),
    ):
        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.claim_driver_atomic = AsyncMock(return_value=True)
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Alex"})
        mock_db.run_sync = AsyncMock(return_value=MagicMock(data=[]))
        mock_db.supabase.table = MagicMock(side_effect=lambda name: MagicMock())
        mock_manager = MagicMock()
        mock_manager.send_personal_message = AsyncMock()

        with patch("backend.routes.rides.matching._deps.manager", mock_manager):
            await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # Ranking fell back and dispatch still completed -- one offer notified.
    mock_manager.send_personal_message.assert_awaited_once()


async def test_push_notification_failure_is_non_fatal():
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    driver = _make_driver()

    with ExitStack() as stack:
        mock_db = stack.enter_context(patch("backend.routes.rides.matching._deps.db_supabase"))
        for p in _base_patches():
            stack.enter_context(p)

        mock_db.get_rows = AsyncMock(return_value=[driver])
        mock_db.find_one = AsyncMock(return_value=None)
        mock_db.claim_driver_atomic = AsyncMock(return_value=True)
        mock_db.get_driver_by_id = AsyncMock(return_value=driver)
        mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Alex"})
        mock_db.run_sync = AsyncMock(return_value=MagicMock(data=[]))
        mock_db.supabase.table = MagicMock(side_effect=lambda name: MagicMock())
        mock_manager = MagicMock()
        mock_manager.send_personal_message = AsyncMock()
        stack.enter_context(patch("backend.routes.rides.matching._deps.manager", mock_manager))

        # Only the FCM-push spawn (wrapped in its own try/except, the first
        # spawn() call with one claimed driver) must raise; the later
        # _batch_offer_timeout_handler spawn is unrelated and must keep
        # succeeding.
        _spawn_calls = {"n": 0}

        def _spawn(coro):
            _spawn_calls["n"] += 1
            coro.close()
            if _spawn_calls["n"] == 1:
                raise RuntimeError("event loop full")

        stack.enter_context(patch("backend.routes.rides.matching._deps.spawn", side_effect=_spawn))

        await _match_driver_to_ride_attempt("ride-1", ride=ride)

    # The WS offer still went out even though the FCM push spawn blew up.
    mock_manager.send_personal_message.assert_awaited_once()
