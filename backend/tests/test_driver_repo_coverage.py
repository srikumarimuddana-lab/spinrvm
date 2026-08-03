"""Coverage for repositories/driver_repo.py (A1c, Sub-tier C).

Driver lookups, location, availability, and atomic dispatch claims,
extracted from db_supabase.py during the god-object decomposition. Was at
73% coverage (137 stmts / 37 missing) — the missing lines are mostly the
"unconfigured supabase" early-returns, the Redis row-cache hit/miss
branches, the RPC-exception handlers, and the heading-normalisation
try/except in `update_driver_location`.

Query-builder mocking follows the pattern in test_corporate_repo_coverage.py:
`driver_repo.supabase` is patched to a self-chaining `MagicMock` so
optional filter calls (`.eq/.is_/.limit/.in_/.or_/...`) don't break the
chain; only `.execute()` differs per test. `run_sync` (repositories/_base.py)
is patched to a synchronous passthrough (`await run_sync(fn) -> fn()`) so
tests don't depend on the real thread-pool/retry machinery — that
machinery already has its own coverage elsewhere (test_dispatch_db_errors.py
et al.).

The row-level Redis cache helpers (`_read_cached_row`, `_write_cached_row`,
`invalidate_driver_cache`) are imported by name into `driver_repo`'s own
module namespace (see the dual-import block at the top of the source
file), so they're patched via `driver_repo.<name>`, not
`repositories._base.<name>` — patching the `_base` module spelling would
not affect the already-bound reference `driver_repo` holds.

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md): `claim_ride_atomic`
(repositories/driver_repo.py) previously built its `.or_()` filter with a
raw f-string — `f"driver_id.is.null,driver_id.eq.{driver_id}"` — instead of
routing `driver_id` through `_postgrest_or_value` (repositories/_base.py),
which every other or-clause builder in this codebase uses specifically
because PostgREST splits an or-group on unescaped `,`/`(`/`)`/`"`
characters (see CLAUDE.md's "Query filters" section: "the layer owns
escaping, callers pass raw input"). Now routes through `_postgrest_or_value`
like its siblings. See `TestClaimRideAtomicOrClauseBug` below, which now
asserts the corrected, escaped behavior.

Also observed, not a bug: the dual-import fallback block at the top of the
source file (`except ImportError: from repositories._base import (...)`,
lines 25-38) is intentionally unreachable under the normal
`backend.repositories.driver_repo` import path exercised by this test
suite — it only activates for the top-level (non-package) `driver_repo`
import used when running as `python driver_repo.py`-style flat imports,
which this repo's test harness never does. Left uncovered by design,
consistent with how other repository coverage suites in this file's
sibling (test_corporate_repo_coverage.py) treat the same pattern.

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.anyio

_CHAIN_METHODS = (
    "table",
    "select",
    "eq",
    "is_",
    "in_",
    "or_",
    "limit",
    "update",
)


def _chain(execute_return=None, execute_side_effect=None):
    """A MagicMock whose every chainable query-builder method returns
    itself, so any combination of optional filters still reaches
    `.execute()`."""
    q = MagicMock()
    for method in _CHAIN_METHODS:
        getattr(q, method).return_value = q
    if execute_side_effect is not None:
        q.execute.side_effect = execute_side_effect
    else:
        q.execute.return_value = execute_return
    return q


def _res(data=None):
    r = MagicMock()
    r.data = data
    return r


async def _passthrough_run_sync(func, *_a, **_k):
    return func()


# ═══════════════════════ get_driver_by_id ═══════════════════════


class TestGetDriverById:
    async def test_unconfigured_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        assert await driver_repo.get_driver_by_id("d1") is None

    async def test_positive_cache_hit_skips_db(self, monkeypatch):
        from backend.repositories import driver_repo

        sb = MagicMock()
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "_read_cached_row", AsyncMock(return_value={"id": "d1"}))
        result = await driver_repo.get_driver_by_id("d1")
        assert result == {"id": "d1"}
        sb.table.assert_not_called()

    async def test_negative_cache_hit_returns_none(self, monkeypatch):
        """`{}` is the cache's negative-hit sentinel meaning 'confirmed
        no such driver' — must return None without hitting the DB."""
        from backend.repositories import driver_repo

        sb = MagicMock()
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "_read_cached_row", AsyncMock(return_value={}))
        result = await driver_repo.get_driver_by_id("d1")
        assert result is None
        sb.table.assert_not_called()

    async def test_cache_miss_fetches_and_writes_cache(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[{"id": "d1", "status": "active"}]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)
        monkeypatch.setattr(driver_repo, "_read_cached_row", AsyncMock(return_value=None))
        write_mock = AsyncMock()
        monkeypatch.setattr(driver_repo, "_write_cached_row", write_mock)

        result = await driver_repo.get_driver_by_id("d1")
        assert result == {"id": "d1", "status": "active"}
        write_mock.assert_awaited_once()


# ═══════════════════════ get_driver_by_user_id_cached ═══════════════════════


class TestGetDriverByUserIdCached:
    async def test_unconfigured_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        assert await driver_repo.get_driver_by_user_id_cached("u1") is None

    async def test_positive_cache_hit_skips_db(self, monkeypatch):
        from backend.repositories import driver_repo

        sb = MagicMock()
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "_read_cached_row", AsyncMock(return_value={"id": "d1", "user_id": "u1"}))
        result = await driver_repo.get_driver_by_user_id_cached("u1")
        assert result == {"id": "d1", "user_id": "u1"}
        sb.table.assert_not_called()

    async def test_negative_cache_hit_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        sb = MagicMock()
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "_read_cached_row", AsyncMock(return_value={}))
        result = await driver_repo.get_driver_by_user_id_cached("u1")
        assert result is None
        sb.table.assert_not_called()

    async def test_cache_miss_with_rows_writes_cache(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[{"id": "d1", "user_id": "u1"}]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)
        monkeypatch.setattr(driver_repo, "_read_cached_row", AsyncMock(return_value=None))
        write_mock = AsyncMock()
        monkeypatch.setattr(driver_repo, "_write_cached_row", write_mock)

        result = await driver_repo.get_driver_by_user_id_cached("u1")
        assert result == {"id": "d1", "user_id": "u1"}
        write_mock.assert_awaited_once()

    async def test_cache_miss_no_rows_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)
        monkeypatch.setattr(driver_repo, "_read_cached_row", AsyncMock(return_value=None))
        monkeypatch.setattr(driver_repo, "_write_cached_row", AsyncMock())

        result = await driver_repo.get_driver_by_user_id_cached("u1")
        assert result is None


# ═══════════════════════ get_service_area_for_point ═══════════════════════


class TestGetServiceAreaForPoint:
    async def test_unconfigured_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        assert await driver_repo.get_service_area_for_point(50.0, -104.0) is None

    async def test_success_returns_first_row(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[{"id": "area-1", "name": "Regina"}]))
        sb = MagicMock()
        sb.rpc.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.get_service_area_for_point(50.4, -104.6)
        assert result == {"id": "area-1", "name": "Regina"}
        sb.rpc.assert_called_once_with("get_service_area_for_point", {"lat": 50.4, "lng": -104.6})

    async def test_no_matching_area_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[]))
        sb = MagicMock()
        sb.rpc.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        assert await driver_repo.get_service_area_for_point(0.0, 0.0) is None

    async def test_exception_logged_and_returns_none(self, monkeypatch):
        """RPC failures must not propagate — this lookup degrades to None
        rather than blowing up the caller (unlike claim/dispatch paths,
        which intentionally do let DB errors surface)."""
        from backend.repositories import driver_repo

        q = _chain(execute_side_effect=RuntimeError("rpc unavailable"))
        sb = MagicMock()
        sb.rpc.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        assert await driver_repo.get_service_area_for_point(50.0, -104.0) is None


# ═══════════════════════ find_nearby_drivers ═══════════════════════


class TestFindNearbyDrivers:
    async def test_unconfigured_returns_empty_list(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        assert await driver_repo.find_nearby_drivers(50.0, -104.0, 500.0) == []

    async def test_success_returns_rows(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[{"id": "d1"}, {"id": "d2"}]))
        sb = MagicMock()
        sb.rpc.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.find_nearby_drivers(50.0, -104.0, 500.0)
        assert result == [{"id": "d1"}, {"id": "d2"}]
        sb.rpc.assert_called_once_with("find_nearby_drivers", {"lat": 50.0, "lng": -104.0, "radius_meters": 500.0})


# ═══════════════════════ update_driver_location ═══════════════════════


class TestUpdateDriverLocation:
    async def test_unconfigured_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        assert await driver_repo.update_driver_location("d1", 50.0, -104.0) is None

    def _capture_update(self, captured):
        def _update(data):
            captured["data"] = data
            m = MagicMock()
            m.eq.return_value.execute.return_value = _res(data=[{"id": "d1"}])
            return m

        return _update

    async def test_success_without_heading_omits_heading_key(self, monkeypatch):
        from backend.repositories import driver_repo

        captured: dict = {}
        tbl = MagicMock()
        tbl.update.side_effect = self._capture_update(captured)
        sb = MagicMock()
        sb.table.return_value = tbl
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.update_driver_location("d1", 50.0, -104.0)
        assert result is True
        assert "heading" not in captured["data"]
        assert captured["data"]["lat"] == 50.0
        assert captured["data"]["lng"] == -104.0

    async def test_heading_normalized_modulo_360(self, monkeypatch):
        from backend.repositories import driver_repo

        captured: dict = {}
        tbl = MagicMock()
        tbl.update.side_effect = self._capture_update(captured)
        sb = MagicMock()
        sb.table.return_value = tbl
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        await driver_repo.update_driver_location("d1", 50.0, -104.0, heading=725)
        assert captured["data"]["heading"] == 5.0

    async def test_invalid_heading_silently_ignored(self, monkeypatch):
        """A device sending a garbage bearing must not wipe/skip the fix —
        heading is simply omitted from the payload, per the source
        comment ('so a fix with no bearing doesn't wipe the last good
        heading')."""
        from backend.repositories import driver_repo

        captured: dict = {}
        tbl = MagicMock()
        tbl.update.side_effect = self._capture_update(captured)
        sb = MagicMock()
        sb.table.return_value = tbl
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.update_driver_location("d1", 50.0, -104.0, heading="not-a-number")
        assert result is True
        assert "heading" not in captured["data"]


# ═══════════════════════ set_driver_available (unconfigured branch) ═══════════════════════
#
# The configured-supabase branches (online/offline clamp, total_rides_inc)
# are already covered by test_set_driver_available_invariant.py; only the
# `if not supabase` early-return (lines 141-143) was missing.


class TestSetDriverAvailableUnconfigured:
    async def test_returns_none_when_supabase_unconfigured(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        result = await driver_repo.set_driver_available("d1", available=True)
        assert result is None


# ═══════════════════════ match_and_claim_driver ═══════════════════════


class TestMatchAndClaimDriver:
    async def test_unconfigured_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        result = await driver_repo.match_and_claim_driver("vt1", 50.0, -104.0, 5.0)
        assert result is None

    async def test_success_returns_driver_and_invalidates_cache(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[{"id": "d1", "rating": 4.9}]))
        sb = MagicMock()
        sb.rpc.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)
        invalidate_mock = AsyncMock()
        monkeypatch.setattr(driver_repo, "invalidate_driver_cache", invalidate_mock)

        result = await driver_repo.match_and_claim_driver("vt1", 50.0, -104.0, 5.0, min_rating=4.0)
        assert result == {"id": "d1", "rating": 4.9}
        invalidate_mock.assert_awaited_once_with(driver_id="d1")
        sb.rpc.assert_called_once_with(
            "match_and_claim_driver",
            {
                "p_vehicle_type_id": "vt1",
                "p_pickup_lat": 50.0,
                "p_pickup_lng": -104.0,
                "p_radius_km": 5.0,
                "p_min_rating": 4.0,
            },
        )

    async def test_no_eligible_driver_returns_none_and_skips_invalidate(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[]))
        sb = MagicMock()
        sb.rpc.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)
        invalidate_mock = AsyncMock()
        monkeypatch.setattr(driver_repo, "invalidate_driver_cache", invalidate_mock)

        result = await driver_repo.match_and_claim_driver("vt1", 50.0, -104.0, 5.0)
        assert result is None
        invalidate_mock.assert_not_awaited()

    async def test_db_error_surfaces_not_swallowed(self, monkeypatch):
        """CLAUDE.md: RPC failures here must surface loudly (503-worthy),
        never collapse into 'no drivers available' -- source docstring
        explicitly says there's no try/except on purpose."""
        from backend.repositories import driver_repo

        async def _raise_run_sync(_func, *_a, **_k):
            raise RuntimeError("rpc down")

        sb = MagicMock()
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _raise_run_sync)

        with pytest.raises(RuntimeError):
            await driver_repo.match_and_claim_driver("vt1", 50.0, -104.0, 5.0)


# ═══════════════════════ claim_driver_atomic (unconfigured branch) ═══════════════════════


class TestClaimDriverAtomicUnconfigured:
    async def test_returns_false_when_supabase_unconfigured(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        assert await driver_repo.claim_driver_atomic("d1") is False


# ═══════════════════════ update_acceptance_rate ═══════════════════════


class TestUpdateAcceptanceRate:
    async def test_unconfigured_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        assert await driver_repo.update_acceptance_rate("d1", True) is None

    async def test_success_updates_ewma_on_accept(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(
            execute_side_effect=[
                _res(data={"acceptance_rate": "0.8"}),  # select
                _res(data=[{"id": "d1", "acceptance_rate": 0.82}]),  # update
            ]
        )
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        await driver_repo.update_acceptance_rate("d1", True)
        # new = alpha*1 + (1-alpha)*0.8 = 0.1 + 0.72 = 0.82
        update_payload = q.update.call_args.args[0]
        assert update_payload["acceptance_rate"] == 0.82

    async def test_success_updates_ewma_on_reject_with_missing_prior_rate(self, monkeypatch):
        """No prior acceptance_rate on the row -> defaults to 1.0 before
        the EWMA blend."""
        from backend.repositories import driver_repo

        q = _chain(
            execute_side_effect=[
                _res(data={}),  # select: no acceptance_rate key
                _res(data=[{"id": "d1"}]),  # update
            ]
        )
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        await driver_repo.update_acceptance_rate("d1", False)
        # old defaults to 1.0; new = 0.1*0 + 0.9*1.0 = 0.9
        update_payload = q.update.call_args.args[0]
        assert update_payload["acceptance_rate"] == 0.9

    async def test_db_error_caught_logged_and_swallowed(self, monkeypatch):
        """This is the one function in the module documented to swallow
        its DB error (background EWMA nudge, not a critical write) --
        must log and return None rather than propagate."""
        from backend.repositories import driver_repo

        async def _raise_run_sync(_func, *_a, **_k):
            raise RuntimeError("connection reset")

        sb = MagicMock()
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _raise_run_sync)

        result = await driver_repo.update_acceptance_rate("d1", True)
        assert result is None


# ═══════════════════════ claim_ride_atomic ═══════════════════════


class TestClaimRideAtomicOrClauseBug:
    """Fixed (2026-08-03): `claim_ride_atomic` now routes `driver_id`
    through `_postgrest_or_value` (repositories/_base.py) like every other
    or-clause builder in this codebase, per CLAUDE.md's "Query filters"
    convention — the layer owns escaping, callers pass raw input. Was
    previously a raw f-string interpolation (found-not-fixed during the
    A1c Sub-tier C coverage pass); this class now asserts the corrected,
    escaped behavior.
    """

    async def test_or_clause_escapes_reserved_characters_in_driver_id(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[{"id": "r1"}]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        # A driver_id containing a comma is now double-quoted by
        # `_postgrest_or_value` so it's treated as part of ONE literal
        # value, rather than splitting the or-group into a spurious extra
        # term (the pre-fix behavior).
        malicious_driver_id = "d1,driver_id.eq.d2"
        result = await driver_repo.claim_ride_atomic("ride-1", malicious_driver_id)

        assert result is True
        called_arg = q.or_.call_args.args[0]
        assert called_arg == 'driver_id.is.null,driver_id.eq."d1,driver_id.eq.d2"'
        # The pre-fix (buggy) unescaped shape must NOT be sent.
        assert called_arg != "driver_id.is.null,driver_id.eq.d1,driver_id.eq.d2"

    async def test_normal_uuid_driver_id_unaffected(self, monkeypatch):
        """Sanity check: for a driver_id with no PostgREST-reserved
        characters (the normal case -- a UUID from an authenticated JWT)
        the bug is latent and the clause matches what escaping would
        also produce."""
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[{"id": "r1"}]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.claim_ride_atomic("ride-1", "d1-uuid")
        assert result is True
        assert q.or_.call_args.args[0] == "driver_id.is.null,driver_id.eq.d1-uuid"


class TestClaimRideAtomic:
    async def test_no_rows_claimed_returns_false(self, monkeypatch):
        """Ride already taken / not in a claimable state -> 0 rows -> False,
        per the state-machine race-guard convention (CLAUDE.md)."""
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.claim_ride_atomic("ride-1", "d1")
        assert result is False


# ═══════════════════════ get_driver_status_by_user ═══════════════════════


class TestGetDriverStatusByUser:
    async def test_unconfigured_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        monkeypatch.setattr(driver_repo, "supabase", None)
        assert await driver_repo.get_driver_status_by_user("u1") is None

    async def test_found_returns_status(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[{"status": "suspended"}]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.get_driver_status_by_user("u1")
        assert result == "suspended"

    async def test_not_found_returns_none(self, monkeypatch):
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.get_driver_status_by_user("missing-user")
        assert result is None

    async def test_found_row_missing_status_key_defaults_active(self, monkeypatch):
        from backend.repositories import driver_repo

        # A non-empty dict lacking the "status" key, NOT `{}` — an empty
        # dict is falsy in Python, so `if driver else None` would treat a
        # genuinely-present-but-empty row the same as "no row found" and
        # return None instead of exercising `.get("status", "active")`'s
        # default. See TestDriverRowEmptyDictBug below for that edge case.
        q = _chain(execute_return=_res(data=[{"user_id": "u1"}]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.get_driver_status_by_user("u1")
        assert result == "active"

    async def test_found_but_empty_dict_row_is_treated_as_not_found_bug(self, monkeypatch):
        """FOUND NOT FIXED: `get_driver_status_by_user` returns
        `driver.get("status", "active") if driver else None`
        (repositories/driver_repo.py:356). An empty dict `{}` is falsy in
        Python, so a genuinely-present-but-empty row is indistinguishable
        from "no row found" and this returns None instead of "active" —
        the opposite of the sibling `test_found_row_missing_status_key_
        defaults_active` case just above, which uses a non-empty dict and
        gets the intended default. Not fixed here (test-only pass); pinned
        as current behavior."""
        from backend.repositories import driver_repo

        q = _chain(execute_return=_res(data=[{}]))
        sb = MagicMock()
        sb.table.return_value = q
        monkeypatch.setattr(driver_repo, "supabase", sb)
        monkeypatch.setattr(driver_repo, "run_sync", _passthrough_run_sync)

        result = await driver_repo.get_driver_status_by_user("u1")
        assert result is None
