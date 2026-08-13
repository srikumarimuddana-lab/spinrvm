"""
Cross-service-area dispatch guard tests.

A driver approved for Saskatoon must not receive a Regina ride offer just
because they are parked inside that ride's search radius — approval is a
per-area regulatory process, and proximity is not authorisation.

The reported bug: a Saskatoon-approved driver sitting in Regina got an offer
for a Regina rider's booking.

Design note on coverage. An earlier version of this file asserted the *shape*
of the filter dict handed to ``get_rows`` while mocking ``get_rows`` to return
``[]`` — which asserts the mock, not the behaviour, and still passed with the
guard deleted. The tests below instead:

  1. exercise the real predicate against real driver rows through
     ``_FakeAreaDB``, which actually evaluates the ``$in`` / ``$or`` filter the
     production code builds (including SQL's ``IN`` never matching NULL), and
  2. exercise ``filter_and_rank_drivers`` — the in-Python half of the guard —
     with hand-built rows.

Both fail if the guard is removed.
"""

import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from services.dispatch_service import DispatchService, filter_and_rank_drivers  # noqa: E402
from utils.service_area_scope import (  # noqa: E402
    build_driver_area_filter,
    driver_area_allowed,
    resolve_compatible_area_ids,
    resolve_dispatch_area_scope,
)

pytestmark = pytest.mark.anyio

REGINA = (50.4452, -104.6189)


# ── Fake DB that really evaluates the filter ────────────────────────────────


def _matches(row, filters):
    """Evaluate a Mongo-shaped filter against one row the way PostgREST would.

    Only the operators this guard emits are implemented. Crucially, ``$in``
    does NOT match NULL — that is the exact SQL semantic that made the first
    version of this fix drop every unassigned driver.
    """
    for key, want in (filters or {}).items():
        if key == "$or":
            if not any(_matches(row, leaf) for leaf in want):
                return False
            continue
        if key == "$and":
            if not all(_matches(row, sub) for sub in want):
                return False
            continue
        got = row.get(key)
        if isinstance(want, dict):
            if "$in" in want:
                # SQL: NULL IN (...) is never true.
                if got is None or got not in want["$in"]:
                    return False
            if "$gte" in want and not (got is not None and got >= want["$gte"]):
                return False
            if "$lte" in want and not (got is not None and got <= want["$lte"]):
                return False
            continue
        if got != want:
            return False
    return True


class _FakeAreaDB:
    """Minimal DB honouring service_areas parent/child links and driver filters."""

    def __init__(self, areas, drivers):
        self.areas = areas
        self.drivers = drivers
        self.driver_filters = []

    async def find_one(self, table, filters=None, **kwargs):
        if table == "service_areas":
            return next((a for a in self.areas if a["id"] == filters.get("id")), None)
        return None

    async def get_rows(self, table, filters=None, **kwargs):
        if table == "service_areas":
            # The scope resolver reads the whole (small) table once and builds
            # the tree in memory — one query, no per-level BFS.
            return [{"id": a["id"], "parent_service_area_id": a.get("parent_service_area_id")} for a in self.areas]
        if table == "drivers":
            self.driver_filters.append(filters)
            return [d for d in self.drivers if _matches(d, filters)]
        if table == "driver_subscriptions":
            return []
        return []


def _driver(did, area_id, **kw):
    row = {
        "id": did,
        "user_id": f"u_{did}",
        "service_area_id": area_id,
        "lat": REGINA[0],
        "lng": REGINA[1],
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "deleted_at": None,
        "vehicle_type_id": "economy",
    }
    row.update(kw)
    return row


SASK_AND_REGINA = [
    {"id": "regina", "is_active": True},
    {"id": "regina_airport", "is_active": True, "parent_service_area_id": "regina"},
    {"id": "regina_downtown", "is_active": True, "parent_service_area_id": "regina"},
    {"id": "saskatoon", "is_active": True},
]

SETTINGS_ON = {"enforce_driver_service_area": True, "service_area_allow_unassigned_drivers": False}
SETTINGS_ALLOW_NULL = {"enforce_driver_service_area": True, "service_area_allow_unassigned_drivers": True}
SETTINGS_OFF = {"enforce_driver_service_area": False}


# ── The reported bug ────────────────────────────────────────────────────────


class TestReportedBug:
    async def test_saskatoon_driver_does_not_receive_regina_ride(self):
        """The exact reported scenario: Saskatoon driver, Regina ride, same GPS."""
        db = _FakeAreaDB(
            SASK_AND_REGINA,
            [_driver("regina_drv", "regina"), _driver("saskatoon_drv", "saskatoon")],
        )
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers(
            {"vehicle_type_id": "economy", "service_area_id": "regina"},
            app_settings=SETTINGS_ON,
        )
        ids = {d["id"] for d in out}
        assert "regina_drv" in ids, "the in-area driver must still be dispatchable"
        assert "saskatoon_drv" not in ids, "cross-area driver must not receive the offer"

    async def test_guard_off_restores_previous_behaviour(self):
        """Kill switch: with the flag off, the Saskatoon driver is a candidate again."""
        db = _FakeAreaDB(
            SASK_AND_REGINA,
            [_driver("regina_drv", "regina"), _driver("saskatoon_drv", "saskatoon")],
        )
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers(
            {"vehicle_type_id": "economy", "service_area_id": "regina"},
            app_settings=SETTINGS_OFF,
        )
        assert {d["id"] for d in out} == {"regina_drv", "saskatoon_drv"}


# ── The regression the first version of this fix introduced ─────────────────


class TestUnassignedDrivers:
    """drivers.service_area_id is nullable with no backfill — NULL must not
    silently drop the whole unassigned cohort out of dispatch."""

    async def test_null_area_driver_kept_when_unassigned_allowed(self):
        db = _FakeAreaDB(
            SASK_AND_REGINA,
            [_driver("assigned", "regina"), _driver("unassigned", None)],
        )
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers(
            {"vehicle_type_id": "economy", "service_area_id": "regina"},
            app_settings=SETTINGS_ALLOW_NULL,
        )
        assert {d["id"] for d in out} == {"assigned", "unassigned"}

    async def test_null_area_driver_dropped_once_lockdown_enabled(self):
        db = _FakeAreaDB(
            SASK_AND_REGINA,
            [_driver("assigned", "regina"), _driver("unassigned", None)],
        )
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers(
            {"vehicle_type_id": "economy", "service_area_id": "regina"},
            app_settings=SETTINGS_ON,
        )
        assert {d["id"] for d in out} == {"assigned"}

    async def test_default_settings_allow_unassigned(self):
        """An empty settings dict must not lock out unassigned drivers."""
        db = _FakeAreaDB(SASK_AND_REGINA, [_driver("unassigned", None)])
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers(
            {"vehicle_type_id": "economy", "service_area_id": "regina"}, app_settings={}
        )
        assert [d["id"] for d in out] == ["unassigned"]

    async def test_empty_string_area_treated_as_unassigned(self):
        assert driver_area_allowed("", {"regina"}, allow_unassigned=True) is True
        assert driver_area_allowed("", {"regina"}, allow_unassigned=False) is False


# ── Area-tree compatibility ────────────────────────────────────────────────


class TestAreaTree:
    async def test_parent_driver_serves_child_area_ride(self):
        db = _FakeAreaDB(SASK_AND_REGINA, [_driver("d", "regina")])
        ids, complete = await resolve_compatible_area_ids(db, "regina_airport")
        assert complete
        assert "regina" in ids and "regina_airport" in ids

    async def test_child_driver_serves_parent_area_ride(self):
        db = _FakeAreaDB(SASK_AND_REGINA, [])
        ids, complete = await resolve_compatible_area_ids(db, "regina")
        assert complete
        assert {"regina", "regina_airport", "regina_downtown"} <= ids

    async def test_sibling_sub_areas_are_mutually_compatible(self):
        """A regina_airport driver must be able to serve a regina_downtown ride —
        both sit inside Regina. The direct-children-only version excluded this."""
        db = _FakeAreaDB(SASK_AND_REGINA, [_driver("airport_drv", "regina_airport")])
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers(
            {"vehicle_type_id": "economy", "service_area_id": "regina_downtown"},
            app_settings=SETTINGS_ON,
        )
        assert [d["id"] for d in out] == ["airport_drv"]

    async def test_other_city_never_enters_the_tree(self):
        db = _FakeAreaDB(SASK_AND_REGINA, [])
        ids, _ = await resolve_compatible_area_ids(db, "regina")
        assert "saskatoon" not in ids

    async def test_parent_cycle_reports_incomplete_and_does_not_hang(self):
        cyclic = [
            {"id": "a", "parent_service_area_id": "b"},
            {"id": "b", "parent_service_area_id": "a"},
        ]
        db = _FakeAreaDB(cyclic, [])
        ids, complete = await resolve_compatible_area_ids(db, "a")
        assert complete is False
        assert "a" in ids

    async def test_resolution_costs_one_service_areas_query(self):
        """Dispatch is a hot path with a P95 < 2s SLA and up to 30 retries per
        ride — the scope must not fan out into a query per tree level."""
        calls = []

        class _CountingDB(_FakeAreaDB):
            async def get_rows(self, table, filters=None, **kwargs):
                calls.append(table)
                return await super().get_rows(table, filters, **kwargs)

        db = _CountingDB(SASK_AND_REGINA, [])
        await resolve_compatible_area_ids(db, "regina_downtown")
        assert calls.count("service_areas") == 1, f"expected one service_areas read, got {calls}"

    async def test_missing_area_row_fails_safe_to_itself(self):
        db = _FakeAreaDB(SASK_AND_REGINA, [])
        ids, complete = await resolve_compatible_area_ids(db, "moose_jaw")
        assert ids == {"moose_jaw"}
        assert complete is False


# ── Fail-safe behaviour ────────────────────────────────────────────────────


class TestFailSafe:
    async def test_incomplete_scope_narrows_to_own_area_not_wide_open(self):
        """A DB fault must not authorise the whole province, nor strand rides."""

        class _BrokenDB(_FakeAreaDB):
            async def get_rows(self, table, filters=None, **kwargs):
                if table == "service_areas":
                    raise RuntimeError("service_areas unavailable")
                return await super().get_rows(table, filters, **kwargs)

        db = _BrokenDB(SASK_AND_REGINA, [])
        ids, allow = await resolve_dispatch_area_scope(db, "regina", SETTINGS_ON)
        assert ids == {"regina"}, "must narrow to the ride's own area, not disable the guard"
        assert allow is False

    async def test_no_ride_area_disables_guard(self):
        db = _FakeAreaDB(SASK_AND_REGINA, [])
        ids, allow = await resolve_dispatch_area_scope(db, None, SETTINGS_ON)
        assert ids is None
        assert allow is True


# ── The in-Python half of the guard ────────────────────────────────────────


class TestFilterAndRankAreaGate:
    """filter_and_rank_drivers must gate on area too, so a pool sourced any
    other way (future RPC, cached list) is still checked."""

    RIDE = {
        "pickup_lat": REGINA[0],
        "pickup_lng": REGINA[1],
        "dropoff_lat": REGINA[0],
        "dropoff_lng": REGINA[1],
    }

    def test_drops_out_of_area_driver(self):
        pool = [_driver("regina_drv", "regina"), _driver("saskatoon_drv", "saskatoon")]
        out = filter_and_rank_drivers(
            self.RIDE, pool, "nearest", 4.0, 10.0, allowed_area_ids={"regina"}, allow_unassigned_area=False
        )
        assert [d["id"] for d, _ in out] == ["regina_drv"]

    def test_keeps_unassigned_when_allowed(self):
        pool = [_driver("unassigned", None)]
        out = filter_and_rank_drivers(
            self.RIDE, pool, "nearest", 4.0, 10.0, allowed_area_ids={"regina"}, allow_unassigned_area=True
        )
        assert [d["id"] for d, _ in out] == ["unassigned"]

    def test_drops_unassigned_when_locked_down(self):
        pool = [_driver("unassigned", None)]
        out = filter_and_rank_drivers(
            self.RIDE, pool, "nearest", 4.0, 10.0, allowed_area_ids={"regina"}, allow_unassigned_area=False
        )
        assert out == []

    def test_no_allowed_set_is_backward_compatible_noop(self):
        """Existing callers that pass no area set must be unaffected."""
        pool = [_driver("saskatoon_drv", "saskatoon")]
        out = filter_and_rank_drivers(self.RIDE, pool, "nearest", 4.0, 10.0)
        assert [d["id"] for d, _ in out] == ["saskatoon_drv"]


# ── Filter construction ────────────────────────────────────────────────────


class TestBuildDriverAreaFilter:
    def test_allow_unassigned_emits_or_with_is_null_leaf(self):
        f = build_driver_area_filter({"regina"}, allow_unassigned=True)
        assert f == {"$or": [{"service_area_id": {"$in": ["regina"]}}, {"service_area_id": None}]}

    def test_lockdown_emits_plain_in(self):
        f = build_driver_area_filter({"regina"}, allow_unassigned=False)
        assert f == {"service_area_id": {"$in": ["regina"]}}

    def test_area_ids_are_sorted_for_deterministic_queries(self):
        f = build_driver_area_filter({"c", "a", "b"}, allow_unassigned=False)
        assert f["service_area_id"]["$in"] == ["a", "b", "c"]

    def test_composes_with_existing_and_clause(self):
        """The guard claims $or; it must not collide with the geo-bounds $and."""
        base = {"is_online": True, "$and": [{"lat": {"$gte": 1}}, {"lat": {"$lte": 2}}]}
        base.update(build_driver_area_filter({"regina"}, allow_unassigned=True))
        assert "$and" in base and "$or" in base
        assert _matches(_driver("d", "regina", lat=1.5), base)
        assert not _matches(_driver("d", "saskatoon", lat=1.5), base)
