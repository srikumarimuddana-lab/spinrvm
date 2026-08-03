"""Coverage-only tests for backend/routes/faqs.py (A1c Sub-tier C).

Test-only change: no application code is modified. Existing coverage (see
test_utils_extended.py::TestFaqsEndpoint) already exercises get_public_faqs's
category/audience/no-filter/empty-result paths and the "no location context"
branch of _resolve_area_scope. This file closes the remaining gap reported at
78% (32 stmts, 7 missing): lines 28-29 (the `.fares` relative-import
ImportError fallback to the bare `routes.fares` import), 33-34 (the
lat/lng -> resolve_service_area_for_point branch), and 36-38 (the broad
except-and-degrade-to-empty-set branch) in
backend/routes/faqs.py::_resolve_area_scope.

No pytest/ruff/CI command was run to produce or validate this file; it was
written by reading backend/routes/faqs.py and backend/routes/fares.py
(resolve_area_scope / resolve_service_area_for_point signatures) directly.
"""

import sys
from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.faqs import _resolve_area_scope


@pytest.mark.unit
class TestResolveAreaScopeImportFallback:
    async def test_relative_import_failure_falls_back_to_bare_routes_fares_import(self):
        """Covers lines 28-29: force `from .fares import ...` (line 27) to
        raise ImportError by poisoning sys.modules['backend.routes.fares']
        with None -- Python's import system treats a None entry as a cached
        "this module failed to import" sentinel, so the relative import
        raises and execution falls into the `except ImportError:` branch,
        which does the bare `from routes.fares import ...` (works because
        backend/ is on sys.path per conftest.py).

        No service_area_id/lat/lng is supplied, so after the fallback import
        succeeds, area_id stays None and the real (unmocked)
        resolve_area_scope(None) short-circuits to an empty set immediately
        (see fares.py: `if not area_id: return set()`) without touching the
        DB -- keeping this test a pure import-fallback check.
        """
        with patch.dict(sys.modules, {"backend.routes.fares": None}):
            result = await _resolve_area_scope(None, None, None)
        assert result == set()


@pytest.mark.unit
class TestResolveAreaScopeLatLngBranch:
    async def test_lat_lng_resolves_service_area_and_scopes_by_its_id(self):
        """Covers lines 32-34: no explicit service_area_id, but lat/lng are
        both present, so resolve_service_area_for_point(lat, lng) is
        awaited and its returned area's "id" is threaded into
        resolve_area_scope."""
        fake_area = {"id": "area-yxe"}
        with (
            patch(
                "backend.routes.fares.resolve_service_area_for_point",
                AsyncMock(return_value=fake_area),
            ) as mock_resolve_point,
            patch(
                "backend.routes.fares.resolve_area_scope",
                AsyncMock(return_value={"area-yxe", "parent-sk"}),
            ) as mock_resolve_scope,
        ):
            result = await _resolve_area_scope(None, 52.1332, -106.6700)

        mock_resolve_point.assert_awaited_once_with(52.1332, -106.6700)
        mock_resolve_scope.assert_awaited_once_with("area-yxe")
        assert result == {"area-yxe", "parent-sk"}

    async def test_lat_lng_present_but_no_matching_area_yields_none_area_id(self):
        """Same branch (lines 32-34), but resolve_service_area_for_point
        returns None (point outside every known service area) -- the
        `area.get("id") if area else None` ternary's falsy side, still on
        line 34. resolve_area_scope(None) then short-circuits to an empty
        set per its own `if not area_id` guard."""
        with (
            patch(
                "backend.routes.fares.resolve_service_area_for_point",
                AsyncMock(return_value=None),
            ),
            patch(
                "backend.routes.fares.resolve_area_scope",
                AsyncMock(return_value=set()),
            ) as mock_resolve_scope,
        ):
            result = await _resolve_area_scope(None, 52.0, -106.0)

        mock_resolve_scope.assert_awaited_once_with(None)
        assert result == set()


@pytest.mark.unit
class TestResolveAreaScopeExceptionHandling:
    async def test_resolve_area_scope_failure_degrades_to_empty_set(self):
        """Covers lines 36-38: any exception raised while resolving the
        area scope (bad DB connection, malformed row, etc.) must be caught,
        logged loudly (exc_info=True per CLAUDE.md 'no silent swallow'
        convention), and degrade to an empty set (global-FAQs-only) rather
        than propagate a 500 to an unauthenticated public endpoint."""
        with (
            patch(
                "backend.routes.fares.resolve_area_scope",
                AsyncMock(side_effect=RuntimeError("service_areas lookup exploded")),
            ),
            patch("backend.routes.faqs.logger.error") as mock_log_error,
        ):
            result = await _resolve_area_scope("area-explicit", None, None)

        assert result == set()
        mock_log_error.assert_called_once()
        assert mock_log_error.call_args.kwargs.get("exc_info") is True

    async def test_resolve_service_area_for_point_failure_also_degrades_to_empty_set(self):
        """Same except branch (lines 36-38), reached via a failure in the
        lat/lng resolution step (line 33) instead of the final
        resolve_area_scope call -- both are inside the same try block."""
        with patch(
            "backend.routes.fares.resolve_service_area_for_point",
            AsyncMock(side_effect=RuntimeError("geo lookup exploded")),
        ):
            result = await _resolve_area_scope(None, 52.0, -106.0)

        assert result == set()


@pytest.mark.unit
class TestGetPublicFaqsAreaScopeIntegration:
    """Sanity-check that _resolve_area_scope's result is actually used to
    filter the public FAQ list (line 82), exercised via the real endpoint
    rather than the helper directly, using the explicit service_area_id
    path (no lat/lng) so it stays independent of the branch tests above."""

    async def test_service_area_id_scopes_out_unrelated_area_tagged_faqs(self):
        from backend.routes.faqs import get_public_faqs

        faqs = [
            {"id": "1", "service_area_ids": None},  # global -> always shown
            {"id": "2", "service_area_ids": ["area-yxe"]},  # in scope -> shown
            {"id": "3", "service_area_ids": ["area-other"]},  # out of scope -> hidden
        ]
        with (
            patch("backend.routes.faqs.db_supabase.get_rows", AsyncMock(return_value=faqs)),
            patch(
                "backend.routes.fares.resolve_area_scope",
                AsyncMock(return_value={"area-yxe"}),
            ),
        ):
            result = await get_public_faqs(
                category=None,
                audience=None,
                service_area_id="area-yxe",
                lat=None,
                lng=None,
            )

        assert [f["id"] for f in result] == ["1", "2"]
