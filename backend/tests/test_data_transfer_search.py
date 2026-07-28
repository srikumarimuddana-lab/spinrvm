"""Unit tests for backend/routes/admin/data_transfer_search.py's filter
construction. Covers _text_filter/_build_filters directly (pure functions,
no DB) rather than the route handler, since exercising the handler needs a
DB round-trip test double heavier than this module's filter logic warrants.
"""

from backend.routes.admin.data_transfer_search import _build_filters, _text_filter


def test_text_filter_matches_name_email_phone_case_insensitive():
    """Regression: an earlier version filtered on a `full_name` column that
    doesn't exist on either `users` or `drivers` (both have first_name/
    last_name; drivers additionally has `name`) — every search call failed
    with a real Postgres error. Must filter on first_name/last_name, not
    a nonexistent full_name column."""
    clause = _text_filter("Jane", table="users")
    assert clause == {
        "$or": [
            {"first_name": {"$regex": "Jane", "$options": "i"}},
            {"last_name": {"$regex": "Jane", "$options": "i"}},
            {"email": {"$regex": "Jane", "$options": "i"}},
            {"phone": {"$regex": "Jane", "$options": "i"}},
        ]
    }


def test_text_filter_excludes_email_for_drivers_table():
    """Regression: `drivers` has NO `email` column at all (email lives on
    `users`, joined via drivers.user_id) — including it in a driver-scoped
    filter also 500s, independent of the full_name bug above."""
    clause = _text_filter("Jane", table="drivers")
    assert clause == {
        "$or": [
            {"first_name": {"$regex": "Jane", "$options": "i"}},
            {"last_name": {"$regex": "Jane", "$options": "i"}},
            {"name": {"$regex": "Jane", "$options": "i"}},
            {"phone": {"$regex": "Jane", "$options": "i"}},
        ]
    }


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
    assert drivers_filters["$or"][0] == {"first_name": {"$regex": "jane", "$options": "i"}}
    assert {"email": {"$regex": "jane", "$options": "i"}} in users_filters["$or"]
    assert {"email": {"$regex": "jane", "$options": "i"}} not in drivers_filters["$or"]
