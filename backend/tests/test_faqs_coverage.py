"""Coverage gap-closer for routes/faqs.py (public unauthenticated FAQ read).

Existing coverage (test_utils_extended.py::TestFaqsEndpoint) only exercises the
no-location path (service_area_id/lat/lng all None), which short-circuits
`_resolve_area_scope` before it ever touches the lat/lng resolution branch or
the service-area-lookup failure branch. This file closes those gaps plus the
final area-scope filtering behavior on the response list itself.

Distinct from `backend/tests/test_admin_faqs_crud.py`, which covers the
already-closed admin CRUD surface at `routes/admin/faqs.py` — not touched here.
"""

import asyncio
from unittest.mock import AsyncMock, patch

try:
    from backend.routes import faqs as faqs_route
except ImportError:  # pragma: no cover - bare-path test runs
    from routes import faqs as faqs_route


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# _resolve_area_scope
# --------------------------------------------------------------------------- #


class TestResolveAreaScope:
    def test_explicit_service_area_id_skips_point_resolution(self):
        """service_area_id given: resolve_service_area_for_point must NOT be
        called at all (explicit id wins)."""
        with (
            patch("backend.routes.fares.resolve_area_scope", AsyncMock(return_value={"area-1", "parent-1"})),
            patch("backend.routes.fares.resolve_service_area_for_point", AsyncMock()) as point_fn,
        ):
            scope = _run(faqs_route._resolve_area_scope("area-1", None, None))
        assert scope == {"area-1", "parent-1"}
        point_fn.assert_not_awaited()

    def test_lat_lng_resolves_point_to_area(self):
        """No explicit service_area_id but lat/lng given: resolves via
        resolve_service_area_for_point, then feeds the found area id into
        resolve_area_scope. Covers the lat/lng branch (lines 33-34)."""
        with (
            patch(
                "backend.routes.fares.resolve_service_area_for_point",
                AsyncMock(return_value={"id": "area-9"}),
            ) as point_fn,
            patch("backend.routes.fares.resolve_area_scope", AsyncMock(return_value={"area-9"})) as scope_fn,
        ):
            scope = _run(faqs_route._resolve_area_scope(None, 52.1, -106.6))
        point_fn.assert_awaited_once_with(52.1, -106.6)
        scope_fn.assert_awaited_once_with("area-9")
        assert scope == {"area-9"}

    def test_lat_lng_point_resolves_to_no_area(self):
        """resolve_service_area_for_point returns None (point outside any
        service area) -> area_id stays None -> resolve_area_scope(None)."""
        with (
            patch("backend.routes.fares.resolve_service_area_for_point", AsyncMock(return_value=None)),
            patch("backend.routes.fares.resolve_area_scope", AsyncMock(return_value=set())) as scope_fn,
        ):
            scope = _run(faqs_route._resolve_area_scope(None, 52.1, -106.6))
        scope_fn.assert_awaited_once_with(None)
        assert scope == set()

    def test_only_lat_without_lng_is_treated_as_no_location(self):
        """Partial coordinates (lat set, lng missing) must not attempt point
        resolution -- both must be present."""
        with (
            patch("backend.routes.fares.resolve_service_area_for_point", AsyncMock()) as point_fn,
            patch("backend.routes.fares.resolve_area_scope", AsyncMock(return_value=set())),
        ):
            _run(faqs_route._resolve_area_scope(None, 52.1, None))
        point_fn.assert_not_awaited()

    def test_exception_during_resolution_is_caught_and_logged(self):
        """Any failure anywhere in the resolve path degrades to an empty scope
        (global-FAQs-only) instead of a 500. Covers lines 36-38."""
        with patch(
            "backend.routes.fares.resolve_area_scope",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            scope = _run(faqs_route._resolve_area_scope("area-1", None, None))
        assert scope == set()


# --------------------------------------------------------------------------- #
# get_public_faqs — end-to-end filtering behavior
# --------------------------------------------------------------------------- #


class TestGetPublicFaqsFiltering:
    def test_global_faq_always_included_even_with_scope(self):
        rows = [{"id": "g1", "service_area_ids": []}, {"id": "g2"}]
        with (
            patch("backend.routes.faqs.db_supabase.get_rows", AsyncMock(return_value=rows)),
            patch("backend.routes.fares.resolve_area_scope", AsyncMock(return_value={"area-1"})),
        ):
            result = _run(faqs_route.get_public_faqs(category=None, audience=None, service_area_id="area-1"))
        assert {r["id"] for r in result} == {"g1", "g2"}

    def test_area_tagged_faq_included_when_scope_overlaps(self):
        rows = [
            {"id": "a1", "service_area_ids": ["area-1"]},
            {"id": "a2", "service_area_ids": ["area-9"]},
        ]
        with patch("backend.routes.faqs.db_supabase.get_rows", AsyncMock(return_value=rows)):
            with patch("backend.routes.fares.resolve_area_scope", AsyncMock(return_value={"area-1", "area-2"})):
                result = _run(faqs_route.get_public_faqs(category=None, audience=None, service_area_id="area-1"))
        assert [r["id"] for r in result] == ["a1"]

    def test_area_tagged_faq_excluded_when_no_location_context(self):
        rows = [{"id": "a1", "service_area_ids": ["area-1"]}, {"id": "g1"}]
        with patch("backend.routes.faqs.db_supabase.get_rows", AsyncMock(return_value=rows)):
            result = _run(faqs_route.get_public_faqs(category=None, audience=None))
        assert [r["id"] for r in result] == ["g1"]

    def test_lat_lng_query_params_scope_the_response(self):
        rows = [{"id": "a1", "service_area_ids": ["area-5"]}]
        with (
            patch("backend.routes.faqs.db_supabase.get_rows", AsyncMock(return_value=rows)),
            patch(
                "backend.routes.fares.resolve_service_area_for_point",
                AsyncMock(return_value={"id": "area-5"}),
            ),
            patch("backend.routes.fares.resolve_area_scope", AsyncMock(return_value={"area-5"})),
        ):
            result = _run(faqs_route.get_public_faqs(category=None, audience=None, lat=52.0, lng=-106.0))
        assert [r["id"] for r in result] == ["a1"]

    def test_response_ordered_by_sort_order_ascending(self):
        # Neither app re-sorts client-side (rider-app/driver-app both trust
        # this array's order for display) — a lower sort_order must come
        # first regardless of the DB fetch order.
        rows = [
            {"id": "third", "sort_order": 2},
            {"id": "first", "sort_order": 0},
            {"id": "second", "sort_order": 1},
        ]
        with patch("backend.routes.faqs.db_supabase.get_rows", AsyncMock(return_value=rows)):
            result = _run(faqs_route.get_public_faqs(category=None, audience=None))
        assert [r["id"] for r in result] == ["first", "second", "third"]

    def test_response_missing_sort_order_treated_as_zero(self):
        rows = [{"id": "explicit-zero", "sort_order": 0}, {"id": "no-field"}, {"id": "positive", "sort_order": 1}]
        with patch("backend.routes.faqs.db_supabase.get_rows", AsyncMock(return_value=rows)):
            result = _run(faqs_route.get_public_faqs(category=None, audience=None))
        # "positive" sorts strictly last; the two zero-equivalent rows keep
        # their relative fetch order ahead of it (stable sort).
        assert [r["id"] for r in result] == ["explicit-zero", "no-field", "positive"]
