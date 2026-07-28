"""Unit tests for backend/routes/admin/data_transfer_search.py's filter
construction. Covers _text_filter/_build_filters directly (pure functions,
no DB) rather than the route handler, since exercising the handler needs a
DB round-trip test double heavier than this module's filter logic warrants.
"""

from backend.routes.admin.data_transfer_search import _build_filters, _text_filter


def test_text_filter_users_matches_first_last_email_phone():
    clause = _text_filter("Jane", table="users")
    assert clause == {
        "$or": [
            {"first_name": {"$regex": "Jane", "$options": "i"}},
            {"last_name": {"$regex": "Jane", "$options": "i"}},
            {"email": {"$regex": "Jane", "$options": "i"}},
            {"phone": {"$regex": "Jane", "$options": "i"}},
        ]
    }


def test_text_filter_drivers_matches_name_email_phone():
    clause = _text_filter("Jane", table="drivers")
    assert clause == {
        "$or": [
            {"name": {"$regex": "Jane", "$options": "i"}},
            {"email": {"$regex": "Jane", "$options": "i"}},
            {"phone": {"$regex": "Jane", "$options": "i"}},
        ]
    }


def test_text_filter_defaults_to_users():
    assert _text_filter("x") == _text_filter("x", table="users")


def test_build_filters_empty_when_no_criteria():
    assert _build_filters(None, None, None) == {}


def test_build_filters_text_only():
    filters = _build_filters("jane", None, None)
    assert "$or" in filters
    assert "created_at" not in filters


def test_build_filters_date_range_only():
    filters = _build_filters(None, "2026-01-01", "2026-12-31")
    assert filters == {"created_at": {"$gte": "2026-01-01", "$lte": "2026-12-31"}}


def test_build_filters_date_from_only():
    filters = _build_filters(None, "2026-01-01", None)
    assert filters == {"created_at": {"$gte": "2026-01-01"}}


def test_build_filters_combines_text_and_date():
    filters = _build_filters("jane", "2026-01-01", "2026-12-31")
    assert "$or" in filters
    assert filters["created_at"] == {"$gte": "2026-01-01", "$lte": "2026-12-31"}


def test_build_filters_respects_table_param():
    users_filters = _build_filters("jane", None, None, table="users")
    drivers_filters = _build_filters("jane", None, None, table="drivers")
    assert users_filters["$or"][0] == {"first_name": {"$regex": "jane", "$options": "i"}}
    assert drivers_filters["$or"][0] == {"name": {"$regex": "jane", "$options": "i"}}
