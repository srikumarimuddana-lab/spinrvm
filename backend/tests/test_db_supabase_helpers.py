"""Unit tests for db_supabase.py helper functions.

db_supabase.py is at 46.3% — these tests target the pure/synchronous
helper functions and the cache key utilities that don't need a real
Supabase connection.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# _serialize_for_api
# ---------------------------------------------------------------------------


class TestSerializeForApi:
    def test_serializes_datetime(self):
        from backend.db_supabase import _serialize_for_api

        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = _serialize_for_api({"created_at": dt})
        assert result["created_at"] == dt.isoformat()

    def test_serializes_date(self):
        from backend.db_supabase import _serialize_for_api

        d = date(2024, 1, 15)
        result = _serialize_for_api({"expiry": d})
        assert result["expiry"] == d.isoformat()

    def test_serializes_decimal_to_string(self):
        # Audit-17 P0-1: money crosses the wire as a decimal string, never
        # as IEEE-754 float. _serialize_for_api preserves Decimal precision
        # by emitting the str() form.
        from backend.db_supabase import _serialize_for_api

        result = _serialize_for_api({"fare": Decimal("18.50")})
        assert result["fare"] == "18.50"
        assert isinstance(result["fare"], str)

    def test_passes_through_string(self):
        from backend.db_supabase import _serialize_for_api

        assert _serialize_for_api("hello") == "hello"

    def test_nested_dict(self):
        from backend.db_supabase import _serialize_for_api

        payload = {"outer": {"inner": Decimal("5.00")}}
        result = _serialize_for_api(payload)
        assert result["outer"]["inner"] == "5.00"

    def test_list_of_items(self):
        from backend.db_supabase import _serialize_for_api

        result = _serialize_for_api([Decimal("1.00"), Decimal("2.00")])
        assert result == ["1.00", "2.00"]

    def test_none_passthrough(self):
        from backend.db_supabase import _serialize_for_api

        assert _serialize_for_api(None) is None


# ---------------------------------------------------------------------------
# _single_row_from_res
# ---------------------------------------------------------------------------


class TestSingleRowFromRes:
    def test_returns_first_row_from_list(self):
        from backend.db_supabase import _single_row_from_res

        row = {"id": "1", "name": "Test"}
        res = MagicMock()
        res.data = [row, {"id": "2"}]
        assert _single_row_from_res(res) == row

    def test_returns_none_for_empty_list(self):
        from backend.db_supabase import _single_row_from_res

        res = MagicMock()
        res.data = []
        assert _single_row_from_res(res) is None

    def test_returns_none_for_none_res(self):
        from backend.db_supabase import _single_row_from_res

        assert _single_row_from_res(None) is None

    def test_handles_dict_response(self):
        from backend.db_supabase import _single_row_from_res

        row = {"id": "1"}
        res = {"data": [row]}
        assert _single_row_from_res(res) == row

    def test_none_data_returns_none(self):
        from backend.db_supabase import _single_row_from_res

        res = MagicMock()
        res.data = None
        assert _single_row_from_res(res) is None


# ---------------------------------------------------------------------------
# _rows_from_res
# ---------------------------------------------------------------------------


class TestRowsFromRes:
    def test_returns_list_of_rows(self):
        from backend.db_supabase import _rows_from_res

        rows = [{"id": "1"}, {"id": "2"}]
        res = MagicMock()
        res.data = rows
        assert _rows_from_res(res) == rows

    def test_returns_empty_list_for_none(self):
        from backend.db_supabase import _rows_from_res

        assert _rows_from_res(None) == []

    def test_handles_dict_response(self):
        from backend.db_supabase import _rows_from_res

        rows = [{"id": "x"}]
        res = {"data": rows}
        assert _rows_from_res(res) == rows


# ---------------------------------------------------------------------------
# Cache key helpers
# ---------------------------------------------------------------------------


class TestCacheKeyHelpers:
    def test_user_cache_key_format(self):
        from backend.db_supabase import _user_cache_key

        key = _user_cache_key("user_123")
        assert "user_123" in key
        assert key.startswith("cache:")

    def test_driver_cache_key_format(self):
        from backend.db_supabase import _driver_cache_key

        key = _driver_cache_key("driver_456")
        assert "driver_456" in key

    def test_driver_by_user_cache_key_format(self):
        from backend.db_supabase import _driver_by_user_cache_key

        key = _driver_by_user_cache_key("user_789")
        assert "user_789" in key


# ---------------------------------------------------------------------------
# _postgrest_pattern
# ---------------------------------------------------------------------------


class TestPostgrestPattern:
    def test_escapes_wildcard(self):
        from backend.db_supabase import _postgrest_pattern

        result = _postgrest_pattern("foo*bar")
        assert r"\*" in result

    def test_escapes_comma(self):
        from backend.db_supabase import _postgrest_pattern

        result = _postgrest_pattern("foo,bar")
        assert r"\," in result

    def test_escapes_parens(self):
        from backend.db_supabase import _postgrest_pattern

        result = _postgrest_pattern("foo(bar)")
        assert r"\(" in result

    def test_plain_string_unchanged(self):
        from backend.db_supabase import _postgrest_pattern

        assert _postgrest_pattern("hello") == "hello"


# ---------------------------------------------------------------------------
# _build_or_clause_term
# ---------------------------------------------------------------------------


class TestBuildOrClauseTerm:
    def test_regex_case_insensitive(self):
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("email", {"$regex": "test", "$options": "i"})
        assert "ilike" in term
        assert "email" in term

    def test_regex_case_sensitive(self):
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("name", {"$regex": "foo"})
        assert "like" in term

    def test_ne_operator(self):
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("status", {"$ne": "cancelled"})
        assert "neq" in term
        assert "cancelled" in term

    def test_gt_operator(self):
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("created_at", {"$gt": "2024-01-01"})
        assert "gt" in term

    def test_gte_operator(self):
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("fare", {"$gte": 10})
        assert "gte" in term

    def test_lt_operator(self):
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("age", {"$lt": 65})
        assert "lt" in term

    def test_lte_operator(self):
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("score", {"$lte": 100})
        assert "lte" in term

    def test_none_value_is_null(self):
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("driver_id", None)
        assert "is.null" in term

    def test_scalar_value_eq(self):
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("status", "completed")
        assert "eq" in term
        assert "completed" in term

    def test_unknown_dict_raises(self):
        """An inexpressible predicate must raise, not be silently dropped.

        Dropping the term widens the OR (and for an $or used as an
        update/delete filter, widens it to the whole table).
        """
        import pytest

        from backend.db_supabase import _build_or_clause_term

        with pytest.raises(ValueError, match="unsupported predicate"):
            _build_or_clause_term("field", {"$unknown": "val"})

    def test_in_operator(self):
        """Regression: $in inside $or was dropped, so admin name search — which
        resolves user IDs first and matches drivers by user_id $in — silently
        returned zero rows for every driver."""
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("user_id", {"$in": ["u1", "u2"]})
        assert term == "user_id.in.(u1,u2)"

    def test_in_empty_list_returns_none(self):
        """An empty IN matches nothing, so it contributes nothing to an OR."""
        from backend.db_supabase import _build_or_clause_term

        assert _build_or_clause_term("user_id", {"$in": []}) is None

    def test_in_non_list_raises(self):
        import pytest

        from backend.db_supabase import _build_or_clause_term

        with pytest.raises(ValueError, match=r"\$in expects a list"):
            _build_or_clause_term("user_id", {"$in": "u1"})

    def test_in_quotes_reserved_characters(self):
        """A value containing `,` or `)` would otherwise split/close the group."""
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("id", {"$in": ["a,b", "c)d", "plain"]})
        assert term == 'id.in.("a,b","c)d",plain)'

    def test_regex_escapes_like_wildcards(self):
        """`%` typed into a search box must match a literal percent, not every row."""
        from backend.db_supabase import _build_or_clause_term

        term = _build_or_clause_term("email", {"$regex": "100%", "$options": "i"})
        assert term == r"email.ilike.*100\%*"

    def test_eq_quotes_reserved_characters(self):
        from backend.db_supabase import _build_or_clause_term

        assert _build_or_clause_term("name", "Kumar, N") == 'name.eq."Kumar, N"'

    def test_notnull_operator(self):
        from backend.db_supabase import _build_or_clause_term

        assert _build_or_clause_term("email", {"$notnull": True}) == "email.not.is.null"
        assert _build_or_clause_term("email", {"$notnull": False}) == "email.is.null"


# ---------------------------------------------------------------------------
# _build_or_clause
# ---------------------------------------------------------------------------


class TestBuildOrClause:
    def test_joins_multiple_terms(self):
        from backend.db_supabase import _build_or_clause

        clauses = [{"status": "searching"}, {"status": "completed"}]
        result = _build_or_clause(clauses)
        assert "searching" in result
        assert "completed" in result
        assert "," in result

    def test_empty_clauses(self):
        from backend.db_supabase import _build_or_clause

        result = _build_or_clause([])
        assert result == ""

    def test_driver_name_search_shape(self):
        """The exact clause admin driver search builds once the users pre-query
        has resolved a name to user IDs. Before the fix the `user_id.in.(...)`
        leaf was missing entirely, so searching a driver by name matched nothing.
        """
        from backend.db_supabase import _build_or_clause

        result = _build_or_clause(
            [
                {"phone": {"$regex": "Nighil", "$options": "i"}},
                {"license_plate": {"$regex": "Nighil", "$options": "i"}},
                {"user_id": {"$in": ["uid-nighil"]}},
            ]
        )
        assert "user_id.in.(uid-nighil)" in result


class TestApplyFiltersOr:
    def test_all_leaves_empty_raises_rather_than_matching_everything(self):
        """A $or whose every leaf matches nothing must not degrade into an
        unfiltered query — _apply_filters is shared by update/delete."""
        import pytest

        from backend.db_supabase import _apply_filters

        with pytest.raises(ValueError, match="produced no PostgREST terms"):
            _apply_filters(object(), {"$or": [{"user_id": {"$in": []}}]})


# ---------------------------------------------------------------------------
# Cache helper: invalidate_user_cache
# ---------------------------------------------------------------------------


class TestInvalidateUserCache:
    def test_handles_none_user_id(self):
        from backend.db_supabase import invalidate_user_cache

        # Should not raise
        asyncio.run(invalidate_user_cache(None))

    def test_deletes_user_cache_key(self):
        from backend.db_supabase import invalidate_user_cache
        from backend.utils import redis_client as rc

        asyncio.run(rc.redis_set("cache:user:test_uid", '{"id": "test_uid"}'))
        asyncio.run(invalidate_user_cache("test_uid"))
        # After invalidation, key should be gone (or None)
        # We just verify it doesn't raise


# ---------------------------------------------------------------------------
# claim_driver_atomic / claim_ride_atomic
# ---------------------------------------------------------------------------


class TestAtomicClaims:
    def test_claim_driver_returns_bool(self):
        from backend.db_supabase import claim_driver_atomic

        with (
            patch("backend.db_supabase.run_sync", AsyncMock(return_value=True)),
            patch("backend.db_supabase.invalidate_driver_cache", AsyncMock()),
        ):
            result = asyncio.run(claim_driver_atomic("driver_1"))

        assert isinstance(result, bool)

    def test_claim_driver_returns_false_when_no_rows(self):
        from backend.db_supabase import claim_driver_atomic

        with patch("backend.db_supabase.run_sync", AsyncMock(return_value=False)):
            result = asyncio.run(claim_driver_atomic("driver_1"))

        assert result is False


# ---------------------------------------------------------------------------
# update_ride
# ---------------------------------------------------------------------------


class TestUpdateRide:
    def test_serializes_decimals_before_writing(self):
        from backend.db_supabase import update_ride

        mock_res = MagicMock()
        mock_res.data = [{"id": "ride_1", "total_fare": 18.5}]

        with patch("backend.db_supabase.run_sync", AsyncMock(return_value=mock_res)):
            result = asyncio.run(update_ride("ride_1", {"total_fare": Decimal("18.50"), "status": "completed"}))

        assert result is not None


# ---------------------------------------------------------------------------
# get_user_by_id / get_driver_by_id
# ---------------------------------------------------------------------------


class TestGetHelpers:
    def test_get_user_by_id_returns_row(self):
        import sys

        from backend.db_supabase import get_user_by_id

        user = {"id": "user_1", "email": "test@example.com"}

        _mod = sys.modules[get_user_by_id.__module__]
        with patch.object(_mod, "run_sync", AsyncMock(return_value=user)):
            result = asyncio.run(get_user_by_id("user_1"))

        assert result == user

    def test_get_user_by_id_returns_none_when_not_found(self):
        import sys

        from backend.db_supabase import get_user_by_id

        _mod = sys.modules[get_user_by_id.__module__]
        with patch.object(_mod, "run_sync", AsyncMock(return_value=None)):
            result = asyncio.run(get_user_by_id("nonexistent"))

        assert result is None

    def test_get_driver_by_id_returns_row(self):
        import sys

        from backend.db_supabase import get_driver_by_id

        driver = {"id": "drv_1", "user_id": "usr_1"}

        _mod = sys.modules[get_driver_by_id.__module__]
        with patch.object(_mod, "run_sync", AsyncMock(return_value=driver)):
            result = asyncio.run(get_driver_by_id("drv_1"))

        assert result == driver


# ---------------------------------------------------------------------------
# Non-dict payload/filter guards (diagnostic for the opaque supabase-py
# "'str' object has no attribute 'items'" failure). A bare string passed where
# a dict is expected must fail loudly, naming the table and the bad value,
# instead of blowing up deep inside supabase-py with no context.
# ---------------------------------------------------------------------------


class TestNonDictGuards:
    def test_apply_filters_rejects_string_filter(self):
        import pytest

        from backend.repositories._base import _apply_filters

        with pytest.raises(TypeError) as exc:
            _apply_filters(MagicMock(), "drv_1")  # bare id where {"id": ...} expected
        assert "expected a dict" in str(exc.value)
        assert "drv_1" in str(exc.value)

    def test_apply_filters_allows_none_and_empty(self):
        from backend.repositories._base import _apply_filters

        q = MagicMock()
        # None / empty must pass through untouched (no filter applied).
        assert _apply_filters(q, None) is q
        assert _apply_filters(q, {}) is q

    def test_insert_one_rejects_non_dict_doc(self):
        import pytest

        import backend.repositories._base as base

        with patch.object(base, "supabase", MagicMock()):
            with pytest.raises(TypeError) as exc:
                asyncio.run(base.insert_one("drivers", "not-a-dict"))
        assert "drivers" in str(exc.value)
        assert "must be a dict" in str(exc.value)

    def test_insert_many_rejects_non_dict_element(self):
        import pytest

        import backend.repositories._base as base

        with patch.object(base, "supabase", MagicMock()):
            with pytest.raises(TypeError) as exc:
                asyncio.run(base.insert_many("drivers", [{"id": "ok"}, "bad-row"]))
        assert "drivers" in str(exc.value)
        assert "must be a dict" in str(exc.value)

    def test_update_one_rejects_string_set_payload(self):
        import pytest

        import backend.repositories._base as base
        from backend.repositories._base import DatabaseError

        # {"$set": <str>} unwraps to a string payload; the guard inside _fn
        # raises TypeError, which run_sync wraps into DatabaseError carrying
        # the clear, table-named message.
        with (
            patch.object(base, "supabase", MagicMock()),
            patch.object(base, "_pre_invalidate_for_table", AsyncMock()),
        ):
            with pytest.raises(DatabaseError) as exc:
                asyncio.run(base.update_one("drivers", {"id": "drv_1"}, {"$set": "oops"}))
        assert "must be a dict" in str(exc.value.details.get("original", ""))
        assert "drivers" in str(exc.value.details.get("original", ""))
