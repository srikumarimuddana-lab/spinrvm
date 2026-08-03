"""
A1c Sub-tier C coverage: backend/repositories/driver_repo.py (72.99% -> target 90%+).

`test_set_driver_available_invariant.py` already pins the
is_available => is_online clamp for `set_driver_available(available=True)`.
Every other function/branch in this module has no direct unit test (only
indirect exercise through route/service tests that mock at a higher layer),
so this file closes:

- `get_driver_by_id` / `get_driver_by_user_id_cached`: no-supabase, cache-hit
  (empty-dict sentinel -> None, and a real cached row), cache-miss write-through.
- `get_service_area_for_point`: no-supabase, success, RPC exception (logged,
  returns None).
- `find_nearby_drivers`: no-supabase, success.
- `update_driver_location`: no-supabase, valid heading normalization (mod 360),
  invalid heading swallowed (TypeError/ValueError), no heading supplied.
- `set_driver_available`: no-supabase-client warning path, `available=False`
  (no read needed, releases the claim stamp), `total_rides_inc` on an
  otherwise-`available=False` release (still needs a read for the increment).
- `match_and_claim_driver`: no-supabase, driver found (cache invalidated),
  no eligible driver (None, no invalidation).
- `claim_driver_atomic`: no-supabase, claim won, claim lost (race).
- `update_acceptance_rate`: no-supabase early return, EWMA math for
  accepted/rejected, and the swallow-on-exception branch (no re-raise).
- `claim_ride_atomic`: no-supabase, claim won, claim lost.
- `get_driver_status_by_user`: no-supabase, found with explicit status,
  found with missing status (defaults to "active"), not found (None).

Patch target follows the established pattern in
`test_set_driver_available_invariant.py`: `patch.object(driver_repo, "supabase", ...)`
and `patch.object(driver_repo, "run_sync", ...)` — this module binds its own
`supabase`/`run_sync` names via the dual-import block, so patching
`backend.repositories.driver_repo.<name>` (not `_base.<name>`) is what the
function under test actually reads, per CLAUDE.md's patch-target rule.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio


async def _passthrough_run_sync(func, *_a, **_k):
    return func()


def _mk_result(data):
    return {"data": data}


# ---------------------------------------------------------------------------
# get_driver_by_id / get_driver_by_user_id_cached
# ---------------------------------------------------------------------------


class TestGetDriverById:
    async def test_no_supabase_returns_none(self):
        from backend.repositories import driver_repo

        with patch.object(driver_repo, "supabase", None):
            assert await driver_repo.get_driver_by_id("d1") is None

    async def test_cache_hit_empty_sentinel_returns_none(self):
        from backend.repositories import driver_repo

        with (
            patch.object(driver_repo, "supabase", MagicMock()),
            patch.object(driver_repo, "_read_cached_row", AsyncMock(return_value={})),
            patch.object(driver_repo, "run_sync", AsyncMock()) as mock_run_sync,
        ):
            result = await driver_repo.get_driver_by_id("d1")
        assert result is None
        mock_run_sync.assert_not_called()

    async def test_cache_hit_returns_cached_row(self):
        from backend.repositories import driver_repo

        cached_row = {"id": "d1", "name": "cached"}
        with (
            patch.object(driver_repo, "supabase", MagicMock()),
            patch.object(driver_repo, "_read_cached_row", AsyncMock(return_value=cached_row)),
            patch.object(driver_repo, "run_sync", AsyncMock()) as mock_run_sync,
        ):
            result = await driver_repo.get_driver_by_id("d1")
        assert result == cached_row
        mock_run_sync.assert_not_called()

    async def test_cache_miss_fetches_and_writes_through(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value = _mk_result(
            [{"id": "d1", "name": "fresh"}]
        )
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "_read_cached_row", AsyncMock(return_value=None)),
            patch.object(driver_repo, "_write_cached_row", AsyncMock()) as mock_write,
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            result = await driver_repo.get_driver_by_id("d1")
        assert result["id"] == "d1"
        mock_write.assert_awaited_once()


class TestGetDriverByUserIdCached:
    async def test_no_supabase_returns_none(self):
        from backend.repositories import driver_repo

        with patch.object(driver_repo, "supabase", None):
            assert await driver_repo.get_driver_by_user_id_cached("u1") is None

    async def test_cache_hit_empty_sentinel_returns_none(self):
        from backend.repositories import driver_repo

        with (
            patch.object(driver_repo, "supabase", MagicMock()),
            patch.object(driver_repo, "_read_cached_row", AsyncMock(return_value={})),
        ):
            assert await driver_repo.get_driver_by_user_id_cached("u1") is None

    async def test_cache_miss_no_rows_returns_none_and_writes_sentinel(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.is_.return_value.limit.return_value.execute.return_value = _mk_result(
            []
        )
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "_read_cached_row", AsyncMock(return_value=None)),
            patch.object(driver_repo, "_write_cached_row", AsyncMock()) as mock_write,
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            result = await driver_repo.get_driver_by_user_id_cached("u1")
        assert result is None
        mock_write.assert_awaited_once_with(
            driver_repo._driver_by_user_cache_key("u1"), None, ttl=driver_repo._DRIVER_BY_USER_CACHE_TTL_SECONDS
        )


# ---------------------------------------------------------------------------
# get_service_area_for_point
# ---------------------------------------------------------------------------


class TestGetServiceAreaForPoint:
    async def test_no_supabase_returns_none(self):
        from backend.repositories import driver_repo

        with patch.object(driver_repo, "supabase", None):
            assert await driver_repo.get_service_area_for_point(50.0, -104.0) is None

    async def test_success_returns_first_row(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = _mk_result([{"id": "area-1"}])
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            result = await driver_repo.get_service_area_for_point(50.0, -104.0)
        assert result == {"id": "area-1"}

    async def test_no_matching_area_returns_none(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = _mk_result([])
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            result = await driver_repo.get_service_area_for_point(50.0, -104.0)
        assert result is None

    async def test_rpc_exception_logged_and_returns_none(self):
        from backend.repositories import driver_repo

        with (
            patch.object(driver_repo, "supabase", MagicMock()),
            patch.object(driver_repo, "run_sync", AsyncMock(side_effect=RuntimeError("rpc down"))),
        ):
            result = await driver_repo.get_service_area_for_point(50.0, -104.0)
        assert result is None


# ---------------------------------------------------------------------------
# find_nearby_drivers
# ---------------------------------------------------------------------------


class TestFindNearbyDrivers:
    async def test_no_supabase_returns_empty_list(self):
        from backend.repositories import driver_repo

        with patch.object(driver_repo, "supabase", None):
            assert await driver_repo.find_nearby_drivers(50.0, -104.0, 5000) == []

    async def test_success_returns_rows(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = _mk_result([{"id": "d1"}, {"id": "d2"}])
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            result = await driver_repo.find_nearby_drivers(50.0, -104.0, 5000)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# update_driver_location
# ---------------------------------------------------------------------------


class TestUpdateDriverLocation:
    async def test_no_supabase_returns_none(self):
        from backend.repositories import driver_repo

        with patch.object(driver_repo, "supabase", None):
            assert await driver_repo.update_driver_location("d1", 50.0, -104.0) is None

    async def test_heading_normalized_mod_360(self):
        from backend.repositories import driver_repo

        captured = {}

        def _update(payload):
            captured["payload"] = payload
            m = MagicMock()
            m.eq.return_value.execute.return_value = _mk_result([{"id": "d1"}])
            return m

        sb = MagicMock()
        sb.table.return_value.update.side_effect = _update
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            result = await driver_repo.update_driver_location("d1", 50.0, -104.0, heading=395)
        assert captured["payload"]["heading"] == pytest.approx(35.0)
        assert result is True

    async def test_invalid_heading_swallowed(self):
        from backend.repositories import driver_repo

        captured = {}

        def _update(payload):
            captured["payload"] = payload
            m = MagicMock()
            m.eq.return_value.execute.return_value = _mk_result([{"id": "d1"}])
            return m

        sb = MagicMock()
        sb.table.return_value.update.side_effect = _update
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            await driver_repo.update_driver_location("d1", 50.0, -104.0, heading="not-a-number")
        assert "heading" not in captured["payload"]

    async def test_no_heading_omits_field(self):
        from backend.repositories import driver_repo

        captured = {}

        def _update(payload):
            captured["payload"] = payload
            m = MagicMock()
            m.eq.return_value.execute.return_value = _mk_result([{"id": "d1"}])
            return m

        sb = MagicMock()
        sb.table.return_value.update.side_effect = _update
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            await driver_repo.update_driver_location("d1", 50.0, -104.0)
        assert "heading" not in captured["payload"]


# ---------------------------------------------------------------------------
# set_driver_available — release (available=False) branches not covered by
# test_set_driver_available_invariant.py (which only exercises available=True)
# ---------------------------------------------------------------------------


class TestSetDriverAvailableRelease:
    async def test_no_supabase_logs_warning_and_returns_none(self):
        from backend.repositories import driver_repo

        with (
            patch.object(driver_repo, "supabase", None),
            patch.object(driver_repo, "invalidate_driver_cache", AsyncMock()),
        ):
            result = await driver_repo.set_driver_available("d1", available=True)
        assert result is None

    async def test_release_does_not_read_and_clears_claim_stamp_never_set(self):
        from backend.repositories import driver_repo

        captured = {}

        def _update(payload):
            captured["payload"] = payload
            m = MagicMock()
            m.eq.return_value.execute.return_value = _mk_result([{"id": "d1", "user_id": "u1"}])
            return m

        sb = MagicMock()
        tbl = MagicMock()
        tbl.update.side_effect = _update
        sb.table.return_value = tbl
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
            patch.object(driver_repo, "invalidate_driver_cache", AsyncMock()),
        ):
            await driver_repo.set_driver_available("d1", available=False)
        # available=False -> no select() needed (needs_read is False)
        tbl.select.assert_not_called()
        assert captured["payload"]["is_available"] is False
        # Only released (available=True) clears the claim stamp field.
        assert "availability_claimed_at" not in captured["payload"]

    async def test_release_with_total_rides_inc_still_reads(self):
        from backend.repositories import driver_repo

        captured = {}

        def _update(payload):
            captured["payload"] = payload
            m = MagicMock()
            m.eq.return_value.execute.return_value = _mk_result([{"id": "d1", "user_id": "u1"}])
            return m

        sb = MagicMock()
        tbl = MagicMock()
        tbl.select.return_value.eq.return_value.execute.return_value = _mk_result(
            [{"total_rides": 10, "is_online": True}]
        )
        tbl.update.side_effect = _update
        sb.table.return_value = tbl
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
            patch.object(driver_repo, "invalidate_driver_cache", AsyncMock()),
        ):
            await driver_repo.set_driver_available("d1", available=False, total_rides_inc=1)
        tbl.select.assert_called_once()
        assert captured["payload"]["total_rides"] == 11


# ---------------------------------------------------------------------------
# match_and_claim_driver
# ---------------------------------------------------------------------------


class TestMatchAndClaimDriver:
    async def test_no_supabase_returns_none(self):
        from backend.repositories import driver_repo

        with patch.object(driver_repo, "supabase", None):
            result = await driver_repo.match_and_claim_driver("vt-1", 50.0, -104.0, 5.0)
        assert result is None

    async def test_driver_found_invalidates_cache(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = _mk_result([{"id": "d1"}])
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
            patch.object(driver_repo, "invalidate_driver_cache", AsyncMock()) as mock_invalidate,
        ):
            result = await driver_repo.match_and_claim_driver("vt-1", 50.0, -104.0, 5.0)
        assert result == {"id": "d1"}
        mock_invalidate.assert_awaited_once_with(driver_id="d1")

    async def test_no_eligible_driver_returns_none_no_invalidation(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.rpc.return_value.execute.return_value = _mk_result([])
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
            patch.object(driver_repo, "invalidate_driver_cache", AsyncMock()) as mock_invalidate,
        ):
            result = await driver_repo.match_and_claim_driver("vt-1", 50.0, -104.0, 5.0)
        assert result is None
        mock_invalidate.assert_not_called()


# ---------------------------------------------------------------------------
# claim_driver_atomic
# ---------------------------------------------------------------------------


class TestClaimDriverAtomic:
    async def test_no_supabase_returns_false(self):
        from backend.repositories import driver_repo

        with (
            patch.object(driver_repo, "invalidate_driver_cache", AsyncMock()),
            patch.object(driver_repo, "supabase", None),
        ):
            assert await driver_repo.claim_driver_atomic("d1") is False

    async def test_claim_won(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = _mk_result(
            [{"id": "d1"}]
        )
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
            patch.object(driver_repo, "invalidate_driver_cache", AsyncMock()) as mock_invalidate,
        ):
            claimed = await driver_repo.claim_driver_atomic("d1")
        assert claimed is True
        # Invalidated before AND after a successful claim.
        assert mock_invalidate.await_count == 2

    async def test_claim_lost_race(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = _mk_result([])
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
            patch.object(driver_repo, "invalidate_driver_cache", AsyncMock()) as mock_invalidate,
        ):
            claimed = await driver_repo.claim_driver_atomic("d1")
        assert claimed is False
        # Only the pre-claim invalidation happened, not the post-claim one.
        assert mock_invalidate.await_count == 1


# ---------------------------------------------------------------------------
# update_acceptance_rate
# ---------------------------------------------------------------------------


class TestUpdateAcceptanceRate:
    async def test_no_supabase_returns_none(self):
        from backend.repositories import driver_repo

        with patch.object(driver_repo, "supabase", None):
            assert await driver_repo.update_acceptance_rate("d1", True) is None

    async def test_accepted_moves_rate_toward_one(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = _mk_result(
            [{"acceptance_rate": 0.5}]
        )
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            await driver_repo.update_acceptance_rate("d1", True)
        # new = 0.1*1 + 0.9*0.5 = 0.55
        update_call = sb.table.return_value.update.call_args[0][0]
        assert update_call["acceptance_rate"] == pytest.approx(0.55)

    async def test_rejected_moves_rate_toward_zero(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = _mk_result(
            [{"acceptance_rate": 0.5}]
        )
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            await driver_repo.update_acceptance_rate("d1", False)
        update_call = sb.table.return_value.update.call_args[0][0]
        # new = 0.1*0 + 0.9*0.5 = 0.45
        assert update_call["acceptance_rate"] == pytest.approx(0.45)

    async def test_no_existing_row_defaults_to_1_0(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = _mk_result([])
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            await driver_repo.update_acceptance_rate("d1", True)
        update_call = sb.table.return_value.update.call_args[0][0]
        # new = 0.1*1 + 0.9*1.0 = 1.0
        assert update_call["acceptance_rate"] == pytest.approx(1.0)

    async def test_exception_is_swallowed_not_raised(self):
        from backend.repositories import driver_repo

        with (
            patch.object(driver_repo, "supabase", MagicMock()),
            patch.object(driver_repo, "run_sync", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            # Must not raise.
            await driver_repo.update_acceptance_rate("d1", True)


# ---------------------------------------------------------------------------
# claim_ride_atomic
# ---------------------------------------------------------------------------


class TestClaimRideAtomicOrClauseEscaping:
    """Fixed: `claim_ride_atomic` now routes `driver_id` through
    `_postgrest_or_value` (repositories/_base.py) like every other or-clause
    builder in this codebase, per CLAUDE.md's "Query filters" convention --
    the layer owns escaping, callers pass raw input. Was previously a raw
    f-string interpolation (found-not-fixed during the A1c Sub-tier C
    coverage pass); these tests assert the corrected, escaped behavior."""

    async def test_or_clause_escapes_reserved_characters_in_driver_id(self):
        from backend.repositories import driver_repo

        chain = MagicMock()
        chain.eq.return_value.in_.return_value.or_.return_value.execute.return_value = _mk_result([{"id": "ride-1"}])
        sb = MagicMock()
        sb.table.return_value.update.return_value = chain
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            # A driver_id containing a comma is now double-quoted by
            # `_postgrest_or_value` so it's treated as part of ONE literal
            # value, rather than splitting the or-group into a spurious
            # extra term (the pre-fix behavior).
            malicious_driver_id = "d1,driver_id.eq.d2"
            claimed = await driver_repo.claim_ride_atomic("ride-1", malicious_driver_id)

        assert claimed is True
        called_arg = chain.eq.return_value.in_.return_value.or_.call_args.args[0]
        assert called_arg == 'driver_id.is.null,driver_id.eq."d1,driver_id.eq.d2"'
        # The pre-fix (buggy) unescaped shape must NOT be sent.
        assert called_arg != "driver_id.is.null,driver_id.eq.d1,driver_id.eq.d2"

    async def test_normal_uuid_driver_id_unaffected(self):
        """Sanity check: for a driver_id with no PostgREST-reserved
        characters (the normal case -- a UUID from an authenticated JWT)
        the clause matches what escaping would also produce."""
        from backend.repositories import driver_repo

        chain = MagicMock()
        chain.eq.return_value.in_.return_value.or_.return_value.execute.return_value = _mk_result([{"id": "ride-1"}])
        sb = MagicMock()
        sb.table.return_value.update.return_value = chain
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            claimed = await driver_repo.claim_ride_atomic("ride-1", "d1-uuid")

        assert claimed is True
        called_arg = chain.eq.return_value.in_.return_value.or_.call_args.args[0]
        assert called_arg == "driver_id.is.null,driver_id.eq.d1-uuid"


class TestClaimRideAtomic:
    async def test_no_supabase_returns_false(self):
        from backend.repositories import driver_repo

        with patch.object(driver_repo, "supabase", None):
            assert await driver_repo.claim_ride_atomic("ride-1", "d1") is False

    async def test_claim_won(self):
        from backend.repositories import driver_repo

        chain = MagicMock()
        chain.eq.return_value.in_.return_value.or_.return_value.execute.return_value = _mk_result([{"id": "ride-1"}])
        sb = MagicMock()
        sb.table.return_value.update.return_value = chain
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            claimed = await driver_repo.claim_ride_atomic("ride-1", "d1")
        assert claimed is True

    async def test_claim_lost(self):
        from backend.repositories import driver_repo

        chain = MagicMock()
        chain.eq.return_value.in_.return_value.or_.return_value.execute.return_value = _mk_result([])
        sb = MagicMock()
        sb.table.return_value.update.return_value = chain
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            claimed = await driver_repo.claim_ride_atomic("ride-1", "d1")
        assert claimed is False


# ---------------------------------------------------------------------------
# get_driver_status_by_user
# ---------------------------------------------------------------------------


class TestGetDriverStatusByUser:
    async def test_no_supabase_returns_none(self):
        from backend.repositories import driver_repo

        with patch.object(driver_repo, "supabase", None):
            assert await driver_repo.get_driver_status_by_user("u1") is None

    async def test_found_with_explicit_status(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = _mk_result(
            [{"status": "suspended"}]
        )
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            result = await driver_repo.get_driver_status_by_user("u1")
        assert result == "suspended"

    async def test_found_missing_status_defaults_active(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        # A non-empty dict without a "status" key -- {} itself is falsy and
        # would hit the "not found" branch instead of the default-value one.
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = _mk_result([{"id": "d1"}])
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            result = await driver_repo.get_driver_status_by_user("u1")
        assert result == "active"

    async def test_not_found_returns_none(self):
        from backend.repositories import driver_repo

        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value = _mk_result([])
        with (
            patch.object(driver_repo, "supabase", sb),
            patch.object(driver_repo, "run_sync", _passthrough_run_sync),
        ):
            result = await driver_repo.get_driver_status_by_user("u1")
        assert result is None
