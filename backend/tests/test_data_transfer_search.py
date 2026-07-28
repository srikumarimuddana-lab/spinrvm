"""Unit tests for backend/routes/admin/data_transfer_search.py's filter
construction. Covers _text_filter/_build_filters directly (pure functions,
no DB) rather than the route handler, since exercising the handler needs a
DB round-trip test double heavier than this module's filter logic warrants.
"""

from backend.routes.admin.data_transfer_search import _build_filters, _text_filter


def test_text_filter_matches_name_email_phone_case_insensitive():
    clause = _text_filter("Jane")
    assert clause == {
        "$or": [
            {"full_name": {"$regex": "Jane", "$options": "i"}},
            {"email": {"$regex": "Jane", "$options": "i"}},
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
