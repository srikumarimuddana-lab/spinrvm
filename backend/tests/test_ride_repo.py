"""repositories/ride_repo.py — unit tests for everything test_ride_route_contract.py
doesn't already cover (get_ride / _project_route_detail's v2-route-projection
contract, which has its own dedicated file).

This file covers the rest of the ride repository: ride CRUD (insert/update),
the atomic payment-processing claim, ride listing, admin-dashboard counters,
flags (incl. the auto-ban-at-3 threshold), complaints, lost-and-found,
location trail, live ride data, and user status/flags lookups.

Patch target: `repositories.ride_repo.supabase` / `repositories.ride_repo.run_sync`
(the domain-module bindings) — per CLAUDE.md's "Patch target for DB" convention.
Follows test_ride_route_contract.py's established local pattern for this exact
module: monkeypatch `run_sync` to call the wrapped function directly (no real
threading), and use lightweight fake query-builder objects rather than the
generic `mock_supabase_client` fixture, since most functions here need
per-table differentiated responses within a single call (e.g.
get_ride_details_enriched fans out to 6+ tables concurrently).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repositories import ride_repo

pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.run(coro)


async def _run_sync(fn):
    return fn()


class _Query:
    """Chainable fake query builder. `rows` is what .execute() returns as
    `.data`; `count` is set on the response when the caller's chain included
    a `count="exact"` select (mirrors real supabase-py's response shape)."""

    def __init__(self, rows, count=None, raise_on_execute=None):
        self._rows = rows
        self._count = count
        self._raise = raise_on_execute

    def select(self, *_a, **_k):
        return self

    def insert(self, *_a, **_k):
        return self

    def update(self, *_a, **_k):
        return self

    def delete(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def neq(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def lt(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def or_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self._raise:
            raise self._raise
        return SimpleNamespace(data=self._rows, count=self._count)


class _FakeSupabase:
    """Routes `.table(name)` to a per-table `_Query`, default empty-list for
    anything not explicitly configured (so an unconfigured table read is
    surfaced as an empty result rather than an AttributeError)."""

    def __init__(self, tables: dict):
        self._tables = tables
        self.tables_requested: list[str] = []

    def table(self, name):
        self.tables_requested.append(name)
        return self._tables.get(name, _Query([]))


# ─────────────────────────────────────────────────────────────────────────────
# create_route_snapshot_signed_url
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateRouteSnapshotSignedUrl:
    async def test_raises_on_blank_object_path(self):
        with pytest.raises(ValueError):
            await ride_repo.create_route_snapshot_signed_url("   ")

    async def test_raises_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            with pytest.raises(RuntimeError):
                await ride_repo.create_route_snapshot_signed_url("ride_1/route.png")

    async def test_happy_path_dict_response(self):
        client = MagicMock()
        client.storage.from_.return_value.create_signed_url.return_value = {"signedURL": "https://x/signed.png"}
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            url = await ride_repo.create_route_snapshot_signed_url("ride_1/route.png")
        assert url == "https://x/signed.png"

    async def test_happy_path_object_response(self):
        client = MagicMock()
        client.storage.from_.return_value.create_signed_url.return_value = SimpleNamespace(
            signedURL="https://x/signed2.png"
        )
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            url = await ride_repo.create_route_snapshot_signed_url("ride_1/route.png")
        assert url == "https://x/signed2.png"

    async def test_raises_when_no_url_in_response(self):
        client = MagicMock()
        client.storage.from_.return_value.create_signed_url.return_value = {}
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            with pytest.raises(RuntimeError):
                await ride_repo.create_route_snapshot_signed_url("ride_1/route.png")


# ─────────────────────────────────────────────────────────────────────────────
# _driver_profile_image
# ─────────────────────────────────────────────────────────────────────────────


class TestDriverProfileImage:
    async def test_empty_when_no_user_id(self):
        assert await ride_repo._driver_profile_image(None) == ""

    async def test_empty_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo._driver_profile_image("u1") == ""

    async def test_returns_profile_image_when_found(self):
        client = _FakeSupabase({"users": _Query([{"profile_image": "base64data"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo._driver_profile_image("u1") == "base64data"

    async def test_empty_when_row_not_found(self):
        client = _FakeSupabase({"users": _Query([])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo._driver_profile_image("u1") == ""


# ─────────────────────────────────────────────────────────────────────────────
# insert_ride
# ─────────────────────────────────────────────────────────────────────────────


class TestInsertRide:
    async def test_raises_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            with pytest.raises(RuntimeError):
                await ride_repo.insert_ride({"id": "ride_1"})

    async def test_happy_path_returns_inserted_row(self):
        client = _FakeSupabase({"rides": _Query([{"id": "ride_1", "status": "searching"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.insert_ride({"id": "ride_1"})
        assert result == {"id": "ride_1", "status": "searching"}

    async def test_db_error_is_logged_and_reraised(self):
        client = _FakeSupabase({"rides": _Query(None, raise_on_execute=RuntimeError("db down"))})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            with pytest.raises(RuntimeError, match="db down"):
                await ride_repo.insert_ride({"id": "ride_1"})


# ─────────────────────────────────────────────────────────────────────────────
# update_ride
# ─────────────────────────────────────────────────────────────────────────────


class TestUpdateRide:
    async def test_returns_none_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.update_ride("ride_1", {"status": "completed"}) is None

    async def test_happy_path(self):
        client = _FakeSupabase({"rides": _Query([{"id": "ride_1", "status": "completed"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.update_ride("ride_1", {"status": "completed"})
        assert result == {"id": "ride_1", "status": "completed"}

    async def test_unwraps_mongo_style_set_operator(self):
        captured = {}

        class _CapturingQuery(_Query):
            def update(self, payload):
                captured["payload"] = payload
                return self

        client = _FakeSupabase({"rides": _CapturingQuery([{"id": "ride_1"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            await ride_repo.update_ride("ride_1", {"$set": {"status": "completed"}})
        assert captured["payload"] == {"status": "completed"}


# ─────────────────────────────────────────────────────────────────────────────
# claim_ride_payment_processing (money-adjacent: gates whether this caller
# proceeds to charge Stripe)
# ─────────────────────────────────────────────────────────────────────────────


class TestClaimRidePaymentProcessing:
    async def test_raises_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            with pytest.raises(RuntimeError):
                await ride_repo.claim_ride_payment_processing("ride_1")

    async def test_returns_true_when_row_claimed(self):
        """The update's own filter (payment_status='pending') matched a row --
        this caller won the race and should proceed to charge Stripe."""
        client = _FakeSupabase({"rides": _Query([{"id": "ride_1", "payment_status": "processing"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.claim_ride_payment_processing("ride_1") is True

    async def test_returns_false_when_already_claimed_by_another_request(self):
        """Zero rows updated -- another concurrent request already flipped
        payment_status away from 'pending'. Caller must not charge Stripe
        again (409, not a retry)."""
        client = _FakeSupabase({"rides": _Query([])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.claim_ride_payment_processing("ride_1") is False

    async def test_db_error_propagates_not_swallowed(self):
        client = _FakeSupabase({"rides": _Query(None, raise_on_execute=RuntimeError("db unreachable"))})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            with pytest.raises(RuntimeError, match="db unreachable"):
                await ride_repo.claim_ride_payment_processing("ride_1")


# ─────────────────────────────────────────────────────────────────────────────
# get_rides_for_user / get_rides_for_driver / get_ride_count_by_date_range
# ─────────────────────────────────────────────────────────────────────────────


class TestGetRidesForUser:
    async def test_empty_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.get_rides_for_user("rider_1") == []

    async def test_happy_path(self):
        rows = [{"id": "ride_1"}, {"id": "ride_2"}]
        client = _FakeSupabase({"rides": _Query(rows)})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_rides_for_user("rider_1") == rows


class TestGetRidesForDriver:
    async def test_empty_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.get_rides_for_driver("driver_1") == []

    async def test_happy_path_no_filters(self):
        rows = [{"id": "ride_1"}]
        client = _FakeSupabase({"rides": _Query(rows)})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_rides_for_driver("driver_1") == rows

    async def test_with_statuses_and_date_filters(self):
        """Exercises the statuses/from_date/to_date branches -- asserts the
        chain is built without error and the rows still flow through."""
        rows = [{"id": "ride_1", "status": "completed"}]

        class _TrackingQuery(_Query):
            def __init__(self, rows):
                super().__init__(rows)
                self.or_called_with = None
                self.gte_called_with = None
                self.lt_called_with = None

            def or_(self, filters):
                self.or_called_with = filters
                return self

            def gte(self, col, val):
                self.gte_called_with = (col, val)
                return self

            def lt(self, col, val):
                self.lt_called_with = (col, val)
                return self

        q = _TrackingQuery(rows)
        client = _FakeSupabase({"rides": q})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.get_rides_for_driver(
                "driver_1",
                statuses=["completed", "cancelled"],
                from_date="2026-01-01",
                to_date="2026-02-01",
            )
        assert result == rows
        assert q.or_called_with == "status.eq.completed,status.eq.cancelled"
        assert q.gte_called_with == ("created_at", "2026-01-01")
        assert q.lt_called_with == ("created_at", "2026-02-01")


class TestGetRideCountByDateRange:
    async def test_zero_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.get_ride_count_by_date_range("2026-01-01", "2026-02-01") == 0

    async def test_returns_count(self):
        client = _FakeSupabase({"rides": _Query([], count=42)})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_ride_count_by_date_range("2026-01-01", "2026-02-01") == 42

    async def test_zero_when_response_has_no_count(self):
        client = MagicMock()
        client.table.return_value.select.return_value.limit.return_value.gte.return_value.lt.return_value.execute.return_value = SimpleNamespace()
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_ride_count_by_date_range("2026-01-01", "2026-02-01") == 0


# ─────────────────────────────────────────────────────────────────────────────
# create_flag -- the auto-ban-at-3-active-flags threshold is a real
# moderation/safety action (bans a user account), not just a CRUD insert.
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateFlag:
    async def test_raises_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            with pytest.raises(RuntimeError):
                await ride_repo.create_flag({"target_type": "rider", "target_id": "u1"})

    async def test_below_threshold_does_not_ban(self):
        client = _FakeSupabase(
            {
                "flags": _Query([{"id": "flag_1"}], count=2),
            }
        )
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.create_flag({"target_type": "rider", "target_id": "u1"})
        assert result["auto_banned"] is False
        assert result["active_flag_count"] == 2
        assert "users" not in client.tables_requested

    async def test_at_threshold_bans_rider_via_users_table(self):
        banned: dict = {}

        class _BanQuery(_Query):
            def update(self, payload):
                banned["payload"] = payload
                return self

        client = _FakeSupabase(
            {
                "flags": _Query([{"id": "flag_1"}], count=3),
                "users": _BanQuery([{"id": "u1"}]),
            }
        )
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.create_flag({"target_type": "rider", "target_id": "u1"})
        assert result["auto_banned"] is True
        assert banned["payload"] == {"status": "banned"}
        assert "users" in client.tables_requested

    async def test_at_threshold_bans_driver_via_drivers_table(self):
        client = _FakeSupabase(
            {
                "flags": _Query([{"id": "flag_1"}], count=5),
                "drivers": _Query([{"id": "d1"}]),
            }
        )
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.create_flag({"target_type": "driver", "target_id": "d1"})
        assert result["auto_banned"] is True
        assert "drivers" in client.tables_requested
        assert "users" not in client.tables_requested


# ─────────────────────────────────────────────────────────────────────────────
# create_complaint / resolve_complaint
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateComplaint:
    async def test_raises_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            with pytest.raises(RuntimeError):
                await ride_repo.create_complaint({"ride_id": "ride_1"})

    async def test_happy_path(self):
        client = _FakeSupabase({"complaints": _Query([{"id": "c1", "ride_id": "ride_1"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.create_complaint({"ride_id": "ride_1"})
        assert result == {"id": "c1", "ride_id": "ride_1"}


class TestResolveComplaint:
    async def test_none_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.resolve_complaint("c1", {"status": "resolved"}) is None

    async def test_happy_path(self):
        client = _FakeSupabase({"complaints": _Query([{"id": "c1", "status": "resolved"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.resolve_complaint("c1", {"status": "resolved"})
        assert result == {"id": "c1", "status": "resolved"}


# ─────────────────────────────────────────────────────────────────────────────
# create_lost_and_found / update_lost_and_found
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateLostAndFound:
    async def test_raises_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            with pytest.raises(RuntimeError):
                await ride_repo.create_lost_and_found({"ride_id": "ride_1"})

    async def test_happy_path(self):
        client = _FakeSupabase({"lost_and_found": _Query([{"id": "l1"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.create_lost_and_found({"ride_id": "ride_1"})
        assert result == {"id": "l1"}


class TestUpdateLostAndFound:
    async def test_none_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.update_lost_and_found("l1", {"status": "returned"}) is None

    async def test_happy_path(self):
        client = _FakeSupabase({"lost_and_found": _Query([{"id": "l1", "status": "returned"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.update_lost_and_found("l1", {"status": "returned"})
        assert result == {"id": "l1", "status": "returned"}


# ─────────────────────────────────────────────────────────────────────────────
# get_ride_location_trail / get_user_status / get_flags_for_target
# ─────────────────────────────────────────────────────────────────────────────


class TestGetRideLocationTrail:
    async def test_empty_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.get_ride_location_trail("ride_1") == []

    async def test_happy_path(self):
        rows = [{"lat": 50.4, "lng": -104.6}]
        client = _FakeSupabase({"driver_location_history": _Query(rows)})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_ride_location_trail("ride_1") == rows


class TestGetUserStatus:
    async def test_none_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.get_user_status("u1") is None

    async def test_returns_status(self):
        client = _FakeSupabase({"users": _Query([{"status": "suspended"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_user_status("u1") == "suspended"

    async def test_defaults_to_active_when_status_missing(self):
        client = _FakeSupabase({"users": _Query([{"id": "u1"}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_user_status("u1") == "active"

    async def test_none_when_user_not_found(self):
        client = _FakeSupabase({"users": _Query([])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_user_status("u1") is None


class TestGetFlagsForTarget:
    async def test_empty_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.get_flags_for_target("rider", "u1") == []

    async def test_happy_path(self):
        rows = [{"id": "flag_1", "target_type": "rider"}]
        client = _FakeSupabase({"flags": _Query(rows)})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_flags_for_target("rider", "u1") == rows


# ─────────────────────────────────────────────────────────────────────────────
# get_live_ride_data
# ─────────────────────────────────────────────────────────────────────────────


class TestGetLiveRideData:
    async def test_none_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.get_live_ride_data("ride_1") is None

    async def test_none_when_ride_not_found(self):
        client = _FakeSupabase({"rides": _Query([])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_live_ride_data("ride_1") is None

    async def test_ride_with_no_driver_or_rider_assigned(self):
        client = _FakeSupabase({"rides": _Query([{"id": "ride_1", "driver_id": None, "rider_id": None}])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.get_live_ride_data("ride_1")
        assert result == {"id": "ride_1", "driver_id": None, "rider_id": None}

    async def test_enriches_with_driver_and_rider_fields(self):
        driver_row = {
            "user_id": "u_driver",
            "name": "Dana Driver",
            "phone": "555-0100",
            "lat": 50.45,
            "lng": -104.6,
            "vehicle_make": "Toyota",
            "vehicle_model": "Camry",
            "vehicle_color": "Blue",
            "license_plate": "ABC123",
            "rating": 4.9,
        }
        client = _FakeSupabase(
            {
                "rides": _Query([{"id": "ride_1", "driver_id": "d1", "rider_id": "r1"}]),
                "drivers": _Query([driver_row]),
                "users": _Query([{"first_name": "Rita", "last_name": "Rider", "phone": "555-0200"}]),
            }
        )
        with (
            patch.object(ride_repo, "supabase", client),
            patch.object(ride_repo, "run_sync", _run_sync),
            patch.object(ride_repo, "_driver_profile_image", AsyncMock(return_value="b64img")),
        ):
            result = await ride_repo.get_live_ride_data("ride_1")
        assert result["driver_current_lat"] == 50.45
        assert result["driver_current_lng"] == -104.6
        assert result["driver_name"] == "Dana Driver"
        assert result["driver_vehicle"] == "Toyota Camry"
        assert result["driver_photo_url"] == "b64img"
        assert result["rider_name"] == "Rita Rider"
        assert result["rider_phone"] == "555-0200"


# ─────────────────────────────────────────────────────────────────────────────
# get_ride_details_enriched -- large fan-out function. Full happy-path plus
# the branches this backlog's convention treats as highest-value for a
# read/admin-enrichment endpoint: no-rider/no-driver skip paths, and the
# incentive-claims exception-swallow (deliberate, not a bug -- pinned here so
# a future change to that behavior shows up as an intentional diff).
# ─────────────────────────────────────────────────────────────────────────────


class TestGetRideDetailsEnriched:
    async def test_none_when_supabase_unconfigured(self):
        with patch.object(ride_repo, "supabase", None):
            assert await ride_repo.get_ride_details_enriched("ride_1") is None

    async def test_none_when_ride_not_found(self):
        client = _FakeSupabase({"rides": _Query([])})
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            assert await ride_repo.get_ride_details_enriched("ride_1") is None

    async def test_ride_with_no_rider_or_driver_skips_enrichment_gracefully(self):
        """A guest/deleted-account ride (no rider_id/driver_id) must not 500 --
        every enrichment branch is guarded on the id being present."""
        client = _FakeSupabase(
            {
                "rides": _Query([{"id": "ride_1", "rider_id": None, "driver_id": None}]),
                "complaints": _Query([]),
                "lost_and_found": _Query([]),
                "driver_location_history": _Query([]),
                "ride_offers": _Query([]),
                "ride_incentive_claims": _Query([]),
                "ride_routes": _Query([]),
            }
        )
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.get_ride_details_enriched("ride_1")
        assert result["flags"] == []
        assert result["complaints"] == []
        assert result["offers"] == []
        assert result["incentive_total"] == 0
        assert "rider_name" not in result
        assert "driver_name" not in result

    async def test_incentive_claims_query_failure_degrades_to_empty_not_raise(self):
        """Per the source's own try/except around _get_incentive_claims: a
        failure there must not fail the whole ride-detail read."""
        client = _FakeSupabase(
            {
                "rides": _Query([{"id": "ride_1", "rider_id": None, "driver_id": None}]),
                "complaints": _Query([]),
                "lost_and_found": _Query([]),
                "driver_location_history": _Query([]),
                "ride_offers": _Query([]),
                "ride_incentive_claims": _Query(None, raise_on_execute=RuntimeError("db blip")),
                "ride_routes": _Query([]),
            }
        )
        with patch.object(ride_repo, "supabase", client), patch.object(ride_repo, "run_sync", _run_sync):
            result = await ride_repo.get_ride_details_enriched("ride_1")
        assert result["incentive_claims"] == []
        assert result["incentive_total"] == 0

    async def test_full_happy_path_with_rider_driver_flags_offers_incentives(self):
        rider_row = {
            "first_name": "Rita",
            "last_name": "Rider",
            "phone": "555-0200",
            "email": "rita@example.com",
            "profile_image": "",
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
        }
        driver_row = {
            "user_id": "u_driver",
            "driver_code": "D001",
            "name": "Dana Driver",
            "phone": "555-0100",
            "vehicle_make": "Toyota",
            "vehicle_model": "Camry",
            "vehicle_color": "Blue",
            "vehicle_year": 2022,
            "vehicle_vin": "1HGCM82633A123456",
            "license_plate": "ABC123",
            "rating": 4.9,
            "status": "active",
            "vehicle_type_id": "vt1",
            "total_rides": 200,
            "service_area_id": "area1",
        }
        client = _FakeSupabase(
            {
                "rides": _Query([{"id": "ride_1", "rider_id": "r1", "driver_id": "d1"}]),
                "users": _Query([rider_row]),
                "drivers": _Query([driver_row]),
                "flags": _Query([{"id": "flag_1"}]),
                "complaints": _Query([{"id": "c1"}]),
                "lost_and_found": _Query([]),
                "driver_location_history": _Query([]),
                "ride_offers": _Query([{"driver_id": "d1", "status": "accepted"}]),
                "ride_incentive_claims": _Query([{"incentive_id": "inc1", "bonus_amount": "5.00"}]),
                "ride_incentives": _Query([{"id": "inc1", "name": "Peak Hours", "incentive_type": "surge"}]),
                "service_areas": _Query([{"name": "Regina", "city": "Regina"}]),
                "vehicle_types": _Query([{"name": "Sedan", "description": "", "capacity": 4}]),
                "ride_routes": _Query([]),
            }
        )
        with (
            patch.object(ride_repo, "supabase", client),
            patch.object(ride_repo, "run_sync", _run_sync),
            patch.object(ride_repo, "_driver_profile_image", AsyncMock(return_value="b64img")),
        ):
            result = await ride_repo.get_ride_details_enriched("ride_1")

        assert result["rider_name"] == "Rita Rider"
        assert result["rider_email"] == "rita@example.com"
        assert result["driver_name"] == "Dana Driver"
        assert result["driver_photo_url"] == "b64img"
        assert result["driver_vehicle_type_name"] == "Sedan"
        assert result["rider_flag_count"] == 1  # same flags row returned for both eq() calls in this fake
        assert result["offers"][0]["driver_name"] is None or isinstance(result["offers"][0]["driver_name"], str)
        assert result["incentive_claims"][0]["name"] == "Peak Hours"
        assert result["incentive_total"] == 5.0
