"""Saved addresses: one Home / one Work, PATCH, ordering, validation (2026-09-25).

Runs the real routes/addresses.py -> db_supabase -> repositories/_base.py
path on top of conftest's autouse ``mock_supabase_client``, so the filters
asserted below are the ones actually compiled into the PostgREST query
chain — not a hand-mocked db_supabase that would accept any filter.

Fixture values are synthetic (no real addresses or coordinates).
"""

from __future__ import annotations

import asyncio
import contextvars
import importlib
import itertools
import os
import sys
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RIDER = {"id": "user_1", "role": "rider", "phone": "+10000000000"}
PAYLOAD = {"name": "Home", "address": "1 Test Street", "lat": 50.0, "lng": -100.0, "icon": "home"}


@pytest.fixture
def client():
    import dependencies
    from backend.server import app

    app.dependency_overrides[dependencies.get_current_user] = lambda: RIDER
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def table(mock_supabase_client):
    """Make the shared mock table chainable for writes and record per-op data."""
    t = mock_supabase_client.table.return_value
    state = {"op": None}
    responses = {"select": [], "update": [], "delete": [], "insert": []}

    def _op(name):
        def _f(*_a, **_k):
            state["op"] = name
            return t

        return _f

    for name in responses:
        getattr(t, name).side_effect = _op(name)

    def _execute():
        r = MagicMock()
        r.data = responses[state["op"]]
        r.count = len(r.data)
        return r

    t.execute.side_effect = _execute
    t.responses = responses
    return t


def _patch_verify(result):
    """Patch the verifier on whichever module path the app imported."""
    import importlib

    patches = []
    for mod_path in ("routes.addresses", "backend.routes.addresses"):
        try:
            mod = importlib.import_module(mod_path)
        except ImportError:
            continue
        p = patch.object(mod, "verify_address_matches_coordinate", AsyncMock(return_value=result))
        p.start()
        patches.append(p)
    return patches


@pytest.fixture(autouse=True)
def verify_ok():
    """Default: the geocode verifier fails open with no place_id (no network)."""
    patches = _patch_verify((True, None, None))
    yield
    for p in patches:
        p.stop()


def _op_order(table):
    """The write ops the route issued, in order."""
    ops = ("update", "delete", "insert")
    return [c[0] for c in table.mock_calls if c[0] in ops]


class TestSecondHomeReplacesFirst:
    def test_second_home_updates_existing_row_in_place(self, client, table):
        table.responses["select"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        table.responses["update"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        r = client.post("/api/v1/addresses", json=PAYLOAD)
        assert r.status_code == 200
        assert r.json()["id"] == "old_home"  # id kept
        table.update.assert_called_once()
        table.insert.assert_not_called()
        # Scoped to this rider's Home rows; the oldest one is kept.
        assert call("user_id", "user_1") in table.eq.call_args_list
        assert call("icon", "home") in table.eq.call_args_list
        table.order.assert_called_with("created_at", desc=False)
        assert call("id", "old_home") in table.eq.call_args_list
        # Any leftover duplicate Home is removed, never the kept row --
        # and BEFORE the kept row is written (see TestConcurrentSaves).
        table.delete.assert_called_once()
        table.neq.assert_called_with("id", "old_home")
        assert _op_order(table) == ["delete", "update"]

    def test_first_home_inserts(self, client, table):
        table.responses["select"] = []
        r = client.post("/api/v1/addresses", json=PAYLOAD)
        assert r.status_code == 200
        table.insert.assert_called_once()
        table.update.assert_not_called()
        table.delete.assert_not_called()

    def test_kept_home_gone_before_the_write_inserts(self, client, table):
        # The read found a Home, but a concurrent request removed it before
        # our update landed (0 rows): the save must still produce a Home.
        table.responses["select"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        table.responses["update"] = []
        r = client.post("/api/v1/addresses", json=PAYLOAD)
        assert r.status_code == 200
        table.insert.assert_called_once()

    def test_home_detected_by_label_when_untyped(self, client, table):
        # Legacy/untyped icon ("location") falls back to the label, and the
        # stored icon is normalised to the type so the next save finds it.
        table.responses["select"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        r = client.post("/api/v1/addresses", json={**PAYLOAD, "name": "home", "icon": "location"})
        assert r.status_code == 200
        table.update.assert_called_once()
        assert table.update.call_args.args[0]["icon"] == "home"
        assert r.json()["icon"] == "home"

    def test_typed_icon_wins_over_label(self, client, table):
        # "My house" typed as Home -> Home. "Home" typed as Gym -> not Home.
        table.responses["select"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        client.post("/api/v1/addresses", json={**PAYLOAD, "name": "My house", "icon": "home"})
        table.update.assert_called_once()
        table.update.reset_mock()
        table.insert.reset_mock()
        client.post("/api/v1/addresses", json={**PAYLOAD, "name": "Home", "icon": "gym"})
        table.update.assert_not_called()
        table.insert.assert_called_once()

    def test_non_singleton_labels_can_repeat(self, client, table):
        for _ in range(2):
            r = client.post("/api/v1/addresses", json={**PAYLOAD, "name": "Gym", "icon": "gym"})
            assert r.status_code == 200
        table.update.assert_not_called()
        assert table.insert.call_count == 2

    def test_work_replaces_work_not_home(self, client, table):
        client.post("/api/v1/addresses", json={**PAYLOAD, "name": "Work", "icon": "work"})
        assert call("icon", "work") in table.eq.call_args_list
        assert call("icon", "home") not in table.eq.call_args_list


class TestPlaceId:
    def test_client_place_id_stored_when_verification_has_none(self, client, table):
        r = client.post(
            "/api/v1/addresses", json={**PAYLOAD, "icon": "other", "name": "Cafe", "place_id": "client_pid"}
        )
        assert r.status_code == 200
        assert table.insert.call_args.args[0]["place_id"] == "client_pid"

    def test_server_place_id_wins(self, client, table):
        patches = _patch_verify((True, None, "server_pid"))
        try:
            r = client.post(
                "/api/v1/addresses", json={**PAYLOAD, "icon": "other", "name": "Cafe", "place_id": "client_pid"}
            )
        finally:
            for p in patches:
                p.stop()
        assert r.json()["place_id"] == "server_pid"


class TestValidation:
    @pytest.mark.parametrize(
        "override",
        [{"lat": 91}, {"lat": -90.5}, {"lng": 181}, {"lng": -180.01}, {"icon": "rocket"}],
    )
    def test_rejects_out_of_range_or_unknown_icon(self, client, table, override):
        r = client.post("/api/v1/addresses", json={**PAYLOAD, **override})
        assert r.status_code == 422
        table.insert.assert_not_called()
        table.update.assert_not_called()

    def test_icon_is_case_insensitive(self, client, table):
        r = client.post("/api/v1/addresses", json={**PAYLOAD, "name": "Cafe", "icon": "School"})
        assert r.status_code == 200
        assert table.insert.call_args.args[0]["icon"] == "school"

    def test_mismatch_message_does_not_echo_address(self, client, table):
        patches = _patch_verify((False, "'1 Test Street' geocodes 12.3 km from the supplied location", None))
        try:
            r = client.post("/api/v1/addresses", json=PAYLOAD)
        finally:
            for p in patches:
                p.stop()
        assert r.status_code == 400
        assert "don't match" in r.json()["detail"]
        assert "1 Test Street" not in r.json()["detail"]
        table.insert.assert_not_called()


class TestOrdering:
    def test_get_orders_by_created_at(self, client, table):
        table.responses["select"] = [{"id": "a"}, {"id": "b"}]
        r = client.get("/api/v1/addresses")
        assert r.status_code == 200
        assert [a["id"] for a in r.json()] == ["a", "b"]
        table.order.assert_called_with("created_at", desc=False)


class TestPatch:
    def test_patch_other_users_row_is_404(self, client, table):
        table.responses["select"] = []  # owner-scoped lookup finds nothing
        r = client.patch("/api/v1/addresses/someone_elses", json={"name": "Mine now"})
        assert r.status_code == 404
        table.update.assert_not_called()
        assert call("user_id", "user_1") in table.eq.call_args_list

    def test_patch_renames_own_row(self, client, table):
        row = {"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}
        table.responses["select"] = [row]
        table.responses["update"] = [{**row, "name": "Coffee"}]
        r = client.patch("/api/v1/addresses/a1", json={"name": "Coffee"})
        assert r.status_code == 200
        assert r.json()["name"] == "Coffee"
        assert table.update.call_args.args[0] == {"name": "Coffee"}
        table.delete.assert_not_called()

    def test_patch_to_home_drops_previous_home(self, client, table):
        row = {"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}
        table.responses["select"] = [row]
        table.responses["update"] = [{**row, "icon": "home"}]
        r = client.patch("/api/v1/addresses/a1", json={"icon": "home"})
        assert r.status_code == 200
        table.delete.assert_called_once()
        table.neq.assert_called_with("id", "a1")
        # Cleanup strictly before the promote (see TestConcurrentSaves).
        assert _op_order(table) == ["delete", "update"]

    def test_patch_renaming_the_existing_home_deletes_nothing(self, client, table):
        row = {"id": "h1", "user_id": "user_1", "name": "Home", "icon": "home"}
        table.responses["select"] = [row]
        table.responses["update"] = [{**row, "name": "My house"}]
        r = client.patch("/api/v1/addresses/h1", json={"name": "My house"})
        assert r.status_code == 200
        table.delete.assert_not_called()

    def test_patch_location_requires_all_three(self, client, table):
        table.responses["select"] = [{"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}]
        r = client.patch("/api/v1/addresses/a1", json={"lat": 50.1})
        assert r.status_code == 422
        table.update.assert_not_called()

    def test_patch_location_is_verified_and_stores_place_id(self, client, table):
        row = {"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}
        table.responses["select"] = [row]
        table.responses["update"] = [row]
        r = client.patch(
            "/api/v1/addresses/a1",
            json={"address": "2 Test Street", "lat": 50.1, "lng": -100.1, "place_id": "client_pid"},
        )
        assert r.status_code == 200
        sent = table.update.call_args.args[0]
        assert sent["lat"] == 50.1 and sent["lng"] == -100.1
        assert sent["place_id"] == "client_pid"

    def test_patch_rejects_bad_lat(self, client, table):
        r = client.patch("/api/v1/addresses/a1", json={"address": "2 Test Street", "lat": 95, "lng": 0})
        assert r.status_code == 422

    def test_patch_empty_body_is_422(self, client, table):
        table.responses["select"] = [{"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}]
        r = client.patch("/api/v1/addresses/a1", json={})
        assert r.status_code == 422


# -- Concurrent saves (edge-case review 2026-09-25) ------------------------
#
# Two devices saving at once. The route handlers run against an in-memory
# fake of db_supabase whose every call waits for its turn in a schedule, so
# each test replays EVERY interleaving of the two requests' DB calls. The
# invariant: no interleaving leaves the rider with zero Homes. Before the
# fix (clean-up after the write) the PATCH/PATCH case lost both rows under
# A-promote, B-promote, A-clean, B-clean while both requests returned 200.

_turn_owner: contextvars.ContextVar[str] = contextvars.ContextVar("_turn_owner")


def _matches(row, filters):
    for k, v in (filters or {}).items():
        if isinstance(v, dict) and "$ne" in v:
            if row.get(k) == v["$ne"]:
                return False
        elif row.get(k) != v:
            return False
    return True


class _InterleavedDB:
    """Just the db_supabase calls routes/addresses.py makes, one turn each."""

    def __init__(self, rows, schedule):
        self.rows = [dict(r) for r in rows]
        self.schedule = list(schedule)
        self.cond = asyncio.Condition()

    async def _turn(self):
        me = _turn_owner.get()
        async with self.cond:
            await self.cond.wait_for(lambda: not self.schedule or self.schedule[0] == me)
            if self.schedule:
                self.schedule.pop(0)
            self.cond.notify_all()
        # Each op body below runs with no further await, so it is atomic.

    async def finished(self, name):
        async with self.cond:
            self.schedule = [n for n in self.schedule if n != name]
            self.cond.notify_all()

    async def get_rows(self, table, filters=None, order=None, desc=False, limit=None, **_kw):
        await self._turn()
        out = [dict(r) for r in self.rows if _matches(r, filters)]
        if order:
            out.sort(key=lambda r: str(r.get(order)), reverse=desc)
        return out[:limit] if limit else out

    async def find_one(self, table, filters=None):
        rows = await self.get_rows(table, filters, limit=1)
        return rows[0] if rows else None

    async def update_one(self, table, filters, update, **_kw):
        await self._turn()
        hit = [r for r in self.rows if _matches(r, filters)]
        for r in hit:
            r.update(update)
        return dict(hit[0]) if hit else None

    async def delete_many(self, table, filters):
        await self._turn()
        gone = [r for r in self.rows if _matches(r, filters)]
        self.rows = [r for r in self.rows if not _matches(r, filters)]
        return gone

    async def insert_one(self, table, doc):
        await self._turn()
        self.rows.append(dict(doc))
        return dict(doc)


def _addresses_module():
    for mod_path in ("routes.addresses", "backend.routes.addresses"):
        try:
            return importlib.import_module(mod_path)
        except ImportError:
            continue
    raise ImportError("routes.addresses")


def _schedules(turns_each=4):
    """Every interleaving of A's and B's DB calls (each makes at most 4)."""
    for a_slots in itertools.combinations(range(2 * turns_each), turns_each):
        yield ["A" if i in a_slots else "B" for i in range(2 * turns_each)]


def _run_interleaved(rows, schedule, req_a, req_b):
    from fastapi import HTTPException

    mod = _addresses_module()
    db = _InterleavedDB(rows, schedule)

    async def _one(name, make):
        _turn_owner.set(name)
        try:
            return await make(mod)
        except HTTPException as e:
            return e
        finally:
            await db.finished(name)

    async def _both():
        return await asyncio.gather(_one("A", req_a), _one("B", req_b))

    with patch.object(mod, "db_supabase", db):
        results = asyncio.run(_both())
    return db.rows, results


def _patch_req(address_id, **fields):
    return lambda mod: mod.update_saved_address(
        address_id, mod.SavedAddressUpdate(**fields), current_user=RIDER, x_app_platform="rider"
    )


def _post_req(**fields):
    return lambda mod: mod.create_saved_address(
        mod.SavedAddressCreate(**{**PAYLOAD, **fields}), current_user=RIDER, x_app_platform="rider"
    )


def _row(row_id, icon, created_at, name="Place"):
    return {
        "id": row_id,
        "user_id": "user_1",
        "name": name,
        "address": "1 Test Street",
        "lat": 50.0,
        "lng": -100.0,
        "icon": icon,
        "created_at": created_at,
    }


def _homes(rows):
    return [r for r in rows if r["user_id"] == "user_1" and r["icon"] == "home"]


class TestConcurrentSaves:
    def test_two_devices_promoting_different_rows_to_home_never_lose_both(self):
        rows = [_row("A", "other", "2026-01-01"), _row("B", "gym", "2026-01-02")]
        for schedule in _schedules():
            final, results = _run_interleaved(
                rows, schedule, _patch_req("A", icon="home"), _patch_req("B", icon="home")
            )
            assert _homes(final), f"no Home left under schedule {schedule}"
            assert all(isinstance(r, dict) for r in results), schedule

    def test_promote_racing_a_new_home_post_never_loses_home(self):
        rows = [_row("H", "home", "2026-01-01"), _row("X", "other", "2026-01-02")]
        for schedule in _schedules():
            final, _ = _run_interleaved(rows, schedule, _patch_req("X", icon="home"), _post_req())
            assert _homes(final), f"no Home left under schedule {schedule}"

    def test_two_home_posts_over_existing_duplicates_never_lose_home(self):
        rows = [_row("H1", "home", "2026-01-01"), _row("H2", "home", "2026-01-02")]
        for schedule in _schedules():
            final, results = _run_interleaved(rows, schedule, _post_req(), _post_req(name="My house"))
            assert _homes(final), f"no Home left under schedule {schedule}"
            assert all(isinstance(r, dict) for r in results), schedule

    def test_two_renames_of_duplicate_homes_delete_nothing(self):
        rows = [_row("H1", "home", "2026-01-01"), _row("H2", "home", "2026-01-02")]
        for schedule in _schedules():
            final, _ = _run_interleaved(
                rows, schedule, _patch_req("H1", name="Home A"), _patch_req("H2", name="Home B")
            )
            assert len(_homes(final)) == 2, schedule

    def test_home_work_swap_on_two_devices_is_a_known_gap_reported_as_409(self):
        # Known residual gap (change log §4): two devices swapping an existing
        # Home and Work can lose one of the two rows — making A the Work
        # replaces the old Work (B) before B is re-typed. If B's request
        # already started, it now gets a retryable 409 instead of a bare 404;
        # if it starts after A finished, B is genuinely gone (404). Either
        # way a lost row must never come back as a silent 200. This pins
        # today's behaviour until the partial unique index lands.
        from fastapi import HTTPException

        rows = [_row("A", "home", "2026-01-01"), _row("B", "work", "2026-01-02")]
        saw_409 = False
        for schedule in _schedules():
            final, results = _run_interleaved(
                rows, schedule, _patch_req("A", icon="work"), _patch_req("B", icon="home")
            )
            for r in results:
                if isinstance(r, HTTPException):
                    assert r.status_code in (404, 409), schedule
                    saw_409 = saw_409 or r.status_code == 409
            lost = 2 - len([x for x in final if x["user_id"] == "user_1"])
            failed = sum(isinstance(r, HTTPException) for r in results)
            assert lost == failed, f"a lost row must surface as an error ({schedule})"
        assert saw_409, "an overlapping swap should report the mid-request loss as 409"

    def test_sequential_saves_still_end_with_exactly_one_home(self):
        # Same fake, no overlap: A runs to completion, then B.
        rows = [_row("A", "other", "2026-01-01"), _row("B", "gym", "2026-01-02")]
        final, _ = _run_interleaved(
            rows, ["A"] * 4 + ["B"] * 4, _patch_req("A", icon="home"), _patch_req("B", icon="home")
        )
        assert [r["id"] for r in _homes(final)] == ["B"]


# -- Rider-app-only singleton rule (edge-case review 2026-09-25) -----------
#
# Driver-app builds before this fix save every address with icon "home";
# the one-Home rule must not make each driver save replace the last one.


@pytest.fixture
def as_user():
    """Override the authenticated user for one test (used with `client`)."""
    import dependencies
    from backend.server import app

    def _set(user):
        app.dependency_overrides[dependencies.get_current_user] = lambda: user

    return _set


DRIVER = {**RIDER, "is_driver": True}


class TestDriverAppSavesAreNotSingletons:
    def test_driver_app_home_save_twice_keeps_both_rows(self, client, table):
        table.responses["select"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        for _ in range(2):
            r = client.post("/api/v1/addresses", json=PAYLOAD, headers={"X-App-Platform": "driver"})
            assert r.status_code == 200
        assert table.insert.call_count == 2
        table.update.assert_not_called()
        table.delete.assert_not_called()

    def test_headerless_driver_falls_back_to_is_driver(self, client, table, as_user):
        as_user(DRIVER)
        table.responses["select"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        r = client.post("/api/v1/addresses", json=PAYLOAD)
        assert r.status_code == 200
        table.insert.assert_called_once()
        table.delete.assert_not_called()

    def test_rider_app_still_replaces_even_for_a_dual_role_user(self, client, table, as_user):
        as_user(DRIVER)
        table.responses["select"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        table.responses["update"] = [{"id": "old_home", "user_id": "user_1", **PAYLOAD}]
        r = client.post("/api/v1/addresses", json=PAYLOAD, headers={"X-App-Platform": "rider"})
        assert r.status_code == 200
        assert r.json()["id"] == "old_home"
        table.insert.assert_not_called()

    def test_driver_app_patch_to_home_deletes_nothing(self, client, table):
        row = {"id": "a1", "user_id": "user_1", "name": "Cafe", "icon": "other"}
        table.responses["select"] = [row]
        table.responses["update"] = [{**row, "icon": "home"}]
        r = client.patch("/api/v1/addresses/a1", json={"icon": "home"}, headers={"X-App-Platform": "driver"})
        assert r.status_code == 200
        table.delete.assert_not_called()
