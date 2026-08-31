"""Regression tests for the oversized-``$in`` URL failure and its fix.

Background — the failure these guard against
--------------------------------------------
``{"col": {"$in": [...]}}`` compiles to a PostgREST ``col=in.(v1,v2,…)`` URL
*query parameter*, so the id list travels in the request LINE, not a body. At
fleet size (~910 drivers x 36-char UUIDs) that is a ~35 KB URL, and the edge
proxy in front of PostgREST rejects it with a plain-text ``Bad Request`` before
PostgREST ever sees it. The client cannot parse that body as JSON, so it
surfaces as the opaque ``APIError: JSON could not be generated`` — and nothing
appears in PostgREST's own logs, because the request never arrived.

Observed in production on 2026-08-31: 207 rejected requests in 24h across
``/rest/v1/users`` (admin drivers/stats), ``/rest/v1/driver_documents`` (the
document-expiry sweep) and ``/rest/v1/rides`` (the stale-intent reconciler).

Two things are covered here:

1. ``repositories._base.get_rows_batched_in`` — the batching helper. It is a
   shared primitive with real correctness weight, and an early draft of it
   silently truncated fan-out results (see the dedicated test below).
2. ``routes.admin.drivers.admin_get_drivers``'s ``photo_status`` path, which
   could not use the helper's output directly because it must still return an
   ORDERED, PAGED result. That page is asserted to equal what the single
   ordered query it replaced would have returned.

All DB access is mocked — no real Supabase call is made.
"""

import sys
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

# A UUID plus its `%2C` separator costs ~39 characters inside `in.(…)`. The
# edge proxy rejected 35,573; every observed successful request was far below
# that. This is the ceiling the batch size has to keep each request under.
_URL_SAFE_CHARS = 8000
_CHARS_PER_ID = 39


def _uid(i: int) -> str:
    """A realistically-sized (36-char) id, so batch-size maths is meaningful."""
    return f"{i:08d}-0000-4000-8000-{i:012d}"


# ---------------------------------------------------------------------------
# get_rows_batched_in
# ---------------------------------------------------------------------------


class TestGetRowsBatchedIn:
    """The batching helper in repositories/_base.py."""

    @staticmethod
    def _install(rows_by_key, monkeypatch):
        """Replace `get_rows` in the helper's OWN module globals.

        Resolved via ``__module__`` rather than a hard-coded import path: the
        backend is importable as both ``repositories._base`` and
        ``backend.repositories._base`` (the dual-import pattern), and patching
        the wrong module object would leave the real ``get_rows`` in place and
        make these tests silently vacuous.
        """
        from repositories._base import get_rows_batched_in

        module = sys.modules[get_rows_batched_in.__module__]
        calls = []

        async def _fake_get_rows(table, filters=None, order=None, desc=False, limit=None, offset=None, columns="*"):
            key_col = next(c for c, v in filters.items() if isinstance(v, dict) and "$in" in v and c != "status")
            ids = filters[key_col]["$in"]
            calls.append({"ids": ids, "limit": limit, "offset": offset, "filters": filters, "columns": columns})
            matched = [r for i in ids for r in rows_by_key.get(i, [])]
            start = offset or 0
            return matched[start : start + (limit if limit is not None else len(matched))]

        monkeypatch.setattr(module, "get_rows", _fake_get_rows)
        return get_rows_batched_in, calls

    async def test_splits_fleet_sized_set_into_url_safe_requests(self, monkeypatch):
        """910 ids must not go out as one request line."""
        rows = {_uid(i): [{"id": _uid(i)}] for i in range(910)}
        batched, calls = self._install(rows, monkeypatch)

        out = await batched("users", "id", list(rows))

        assert len(out) == 910, "every id must still be resolved"
        assert len(calls) > 1, "a 910-id set must be split, not sent as one URL"
        widest = max(len(c["ids"]) for c in calls)
        assert widest * _CHARS_PER_ID < _URL_SAFE_CHARS, (
            f"widest request carries {widest} ids (~{widest * _CHARS_PER_ID} chars) — "
            "that is the request line the edge proxy rejects"
        )

    async def test_does_not_truncate_when_one_key_matches_many_rows(self, monkeypatch):
        """Regression: an early draft capped each batch at len(chunk).

        One key can match many rows — a driver has many rides — so capping a
        batch at the number of ids silently drops rows. An under-count reads as
        correct data, which is why this is asserted explicitly.
        """
        rows = {_uid(i): [{"k": _uid(i), "n": j} for j in range(12)] for i in range(300)}
        batched, _ = self._install(rows, monkeypatch)

        out = await batched("rides", "driver_id", list(rows))

        assert len(out) == 300 * 12

    async def test_limit_caps_total_across_batches_and_stops_early(self, monkeypatch):
        rows = {_uid(i): [{"k": _uid(i), "n": j} for j in range(12)] for i in range(300)}
        batched, calls = self._install(rows, monkeypatch)

        out = await batched("rides", "driver_id", list(rows), limit=50)

        assert len(out) == 50, "limit caps the TOTAL, not each batch"
        assert len(calls) == 1, "the limit was satisfied by the first batch — later ones must be skipped"

    async def test_empty_values_issues_no_query_at_all(self, monkeypatch):
        """An empty set means "matches nothing", never an unfiltered scan.

        Dropping the filter instead would match the whole table — and because
        `_apply_filters` is shared with update/delete, that class of mistake
        can write it too.
        """
        batched, calls = self._install({}, monkeypatch)

        out = await batched("users", "id", [])

        assert out == []
        assert calls == []

    async def test_extra_filters_and_columns_reach_every_batch(self, monkeypatch):
        rows = {_uid(i): [{"k": i}] for i in range(400)}
        batched, calls = self._install(rows, monkeypatch)

        await batched(
            "rides",
            "driver_id",
            list(rows),
            {"status": {"$in": ["in_progress"]}},
            columns="id,driver_id",
        )

        assert len(calls) > 1, "test is only meaningful if the set actually split"
        assert all(c["filters"].get("status") == {"$in": ["in_progress"]} for c in calls)
        assert all(c["columns"] == "id,driver_id" for c in calls)

    async def test_caller_filters_are_not_mutated(self, monkeypatch):
        """The helper writes the batch key into a COPY of the caller's dict."""
        rows = {_uid(i): [{"k": i}] for i in range(300)}
        batched, _ = self._install(rows, monkeypatch)
        extra = {"status": {"$in": ["active"]}}

        await batched("drivers", "user_id", list(rows), extra)

        assert extra == {"status": {"$in": ["active"]}}, "caller's filters dict was mutated"


# ---------------------------------------------------------------------------
# _sort_key
# ---------------------------------------------------------------------------


class TestSortKey:
    """Python-side ordering must reproduce Postgres NULL placement.

    Postgres puts NULLs LAST on ASC and FIRST on DESC. Verified against the
    live database when this was written:
        ORDER BY v ASC  -> 1,2,3,NULL
        ORDER BY v DESC -> NULL,3,2,1
    """

    def test_nulls_sort_last_ascending(self):
        from routes.admin.drivers import _sort_key

        assert sorted([3, 1, None, 2], key=_sort_key) == [1, 2, 3, None]

    def test_nulls_sort_first_descending(self):
        from routes.admin.drivers import _sort_key

        assert sorted([3, 1, None, 2], key=_sort_key, reverse=True) == [None, 3, 2, 1]

    def test_all_null_column_does_not_raise(self):
        """The bool decides first, so None never reaches a `<` against None."""
        from routes.admin.drivers import _sort_key

        assert sorted([None, None], key=_sort_key) == [None, None]

    def test_strings_order_without_comparing_against_none(self):
        from routes.admin.drivers import _sort_key

        assert sorted(["Bo", None, "Ana"], key=_sort_key) == ["Ana", "Bo", None]


# ---------------------------------------------------------------------------
# admin_get_drivers — photo_status page equality
# ---------------------------------------------------------------------------


def _fixture_drivers(n=400):
    """One driver per user, with NULLs and ties in every sortable column."""
    names = ["Ana", "Bo", "Cy", "Dee", "Eli"]
    out = []
    for i in range(n):
        out.append(
            {
                "id": f"drv-{i:04d}",
                "user_id": _uid(i),
                "phone": None,
                # NULLs at a different stride per column so no two columns share
                # a null pattern, and heavy tie repetition via the name cycle.
                "created_at": None if i % 13 == 0 else f"2026-{1 + i % 9:02d}-{10 + i % 18:02d}T00:00:00Z",
                "rating": None if i % 17 == 0 else round(1 + (i % 40) / 10, 1),
                "first_name": None if i % 23 == 0 else names[i % len(names)],
                "status": "active",
            }
        )
    return out


class TestPhotoStatusPageEquality:
    """`photo_status` resolves to a user_id set capped at 1000 by its own query.

    Passed straight through as `filters["user_id"] = {"$in": photo_uids}` that
    is a ~39 KB request line, so the endpoint now fetches in URL-safe batches
    and orders/pages in Python. Batching cannot delegate ordering to the
    database — each request is ordered independently, so a DB-side `order`
    would only sort within a batch — which makes "does the page still match?"
    the assertion that matters.
    """

    @staticmethod
    def _patches(drivers):
        by_uid = {}
        for d in drivers:
            by_uid.setdefault(d["user_id"], []).append(d)

        async def _get_rows(table, filters=None, **kwargs):
            if table == "users":
                if filters and "profile_image_status" in filters:
                    return [{"id": d["user_id"]} for d in drivers]
                return []  # enrichment lookup — unenriched rows are fine here
            return []

        async def _batched(table, column, values, extra_filters=None, **kwargs):
            assert table == "drivers" and column == "user_id"
            return [r for v in values for r in by_uid.get(v, [])]

        return (
            patch("db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("db_supabase.get_rows_batched_in", AsyncMock(side_effect=_batched)),
        )

    @pytest.mark.parametrize("sort_by", ["created_at", "rating", "name"])
    @pytest.mark.parametrize("sort_dir", ["asc", "desc"])
    @pytest.mark.parametrize("limit,offset", [(50, 0), (200, 0), (50, 150), (25, 375)])
    async def test_page_matches_the_single_ordered_query_it_replaced(self, sort_by, sort_dir, limit, offset):
        from routes.admin.drivers import _DRIVER_SORT_COLUMNS, _sort_key, admin_get_drivers

        drivers = _fixture_drivers()
        order_col = _DRIVER_SORT_COLUMNS[sort_by]
        desc = sort_dir != "asc"
        expected = [d["id"] for d in sorted(drivers, key=lambda r: _sort_key(r.get(order_col)), reverse=desc)][
            offset : offset + limit
        ]

        rows_patch, batched_patch = self._patches(drivers)
        with rows_patch, batched_patch:
            got = await admin_get_drivers(
                limit=limit,
                offset=offset,
                photo_status="approved",
                sort_by=sort_by,
                sort_dir=sort_dir,
            )

        assert [d["id"] for d in got] == expected

    async def test_no_matching_users_returns_empty_without_querying_drivers(self):
        from routes.admin.drivers import admin_get_drivers

        async def _get_rows(table, filters=None, **kwargs):
            return []

        batched = AsyncMock()
        with (
            patch("db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("db_supabase.get_rows_batched_in", batched),
        ):
            out = await admin_get_drivers(photo_status="pending_review")

        assert out == []
        batched.assert_not_awaited()

    async def test_photo_status_absent_keeps_the_database_side_ordered_query(self):
        """The unfiltered list must NOT pay for Python-side paging."""
        from routes.admin.drivers import admin_get_drivers

        seen = {}

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                seen["order"] = kwargs.get("order")
                seen["limit"] = kwargs.get("limit")
                seen["offset"] = kwargs.get("offset")
            return []

        batched = AsyncMock(return_value=[])
        with (
            patch("db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
            patch("db_supabase.get_rows_batched_in", batched),
        ):
            await admin_get_drivers(limit=25, offset=75)

        assert seen == {"order": "created_at", "limit": 25, "offset": 75}
        batched.assert_not_awaited()
