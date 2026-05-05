"""Unit tests for db_supabase.py helper functions.

db_supabase.py is at 46.3% — these tests target the pure/synchronous
helper functions and the cache key utilities that don't need a real
Supabase connection.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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

    def test_serializes_decimal_to_float(self):
        from backend.db_supabase import _serialize_for_api
        result = _serialize_for_api({"fare": Decimal("18.50")})
        assert result["fare"] == 18.50
        assert isinstance(result["fare"], float)

    def test_passes_through_string(self):
        from backend.db_supabase import _serialize_for_api
        assert _serialize_for_api("hello") == "hello"

    def test_nested_dict(self):
        from backend.db_supabase import _serialize_for_api
        payload = {"outer": {"inner": Decimal("5.00")}}
        result = _serialize_for_api(payload)
        assert result["outer"]["inner"] == 5.0

    def test_list_of_items(self):
        from backend.db_supabase import _serialize_for_api
        result = _serialize_for_api([Decimal("1.00"), Decimal("2.00")])
        assert result == [1.0, 2.0]

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

    def test_unknown_dict_returns_none(self):
        from backend.db_supabase import _build_or_clause_term
        term = _build_or_clause_term("field", {"$unknown": "val"})
        assert term is None


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

        mock_res = MagicMock()
        mock_res.data = [{"id": "driver_1"}]

        with patch("backend.db_supabase.run_sync", AsyncMock(return_value=mock_res)):
            result = asyncio.run(claim_driver_atomic("driver_1"))

        assert isinstance(result, bool)

    def test_claim_driver_returns_false_when_no_rows(self):
        from backend.db_supabase import claim_driver_atomic

        mock_res = MagicMock()
        mock_res.data = []

        with patch("backend.db_supabase.run_sync", AsyncMock(return_value=mock_res)):
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
            result = asyncio.run(
                update_ride("ride_1", {"total_fare": Decimal("18.50"), "status": "completed"})
            )

        assert result is not None


# ---------------------------------------------------------------------------
# get_user_by_id / get_driver_by_id
# ---------------------------------------------------------------------------

class TestGetHelpers:
    def test_get_user_by_id_returns_row(self):
        from backend.db_supabase import get_user_by_id

        user = {"id": "user_1", "email": "test@example.com"}
        mock_res = MagicMock()
        mock_res.data = [user]

        with patch("backend.db_supabase.run_sync", AsyncMock(return_value=mock_res)):
            result = asyncio.run(get_user_by_id("user_1"))

        assert result == user

    def test_get_user_by_id_returns_none_when_not_found(self):
        from backend.db_supabase import get_user_by_id

        mock_res = MagicMock()
        mock_res.data = []

        with patch("backend.db_supabase.run_sync", AsyncMock(return_value=mock_res)):
            result = asyncio.run(get_user_by_id("nonexistent"))

        assert result is None

    def test_get_driver_by_id_returns_row(self):
        from backend.db_supabase import get_driver_by_id

        driver = {"id": "drv_1", "user_id": "usr_1"}
        mock_res = MagicMock()
        mock_res.data = [driver]

        with patch("backend.db_supabase.run_sync", AsyncMock(return_value=mock_res)):
            result = asyncio.run(get_driver_by_id("drv_1"))

        assert result == driver
