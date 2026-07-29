"""Regression tests for admin driver search.

Reported symptom: searching the admin drivers list for a driver by name
("Nighil") returned no rows even though the driver exists.

Root cause was in the shared query layer, not this route:
``_build_or_clause_term`` had no ``$in`` branch, so it returned None and
``_build_or_clause`` dropped the term. Driver name search resolves the term to
user IDs first and then matches drivers with ``{"user_id": {"$in": ids}}`` — the
one leaf that can match a name — so that leaf never reached PostgREST and the
query degraded to "match the term against plate/phone/code", which a name never
does. See TestBuildOrClauseTerm in test_db_supabase_helpers.py for the
layer-level tests.

These tests assert the filter this route hands to the DB, plus the search
semantics layered on top: multi-token names, the drivers.name mirror, phone
normalization, and the users pre-query cap.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

try:
    from backend.routes.admin import drivers as admin_drivers
except ImportError:  # pragma: no cover
    from routes.admin import drivers as admin_drivers  # type: ignore


def _run_search(search: str, matching_users: list[dict] | None = None) -> dict:
    """Run admin_get_drivers(search=...) and return the `drivers` filter it built."""
    captured: dict = {}

    async def get_rows_side(table, filters=None, **kw):
        if table == "drivers":
            captured.update(filters or {})
            return []
        if table == "users":
            return list(matching_users or [])
        return []

    with patch(
        "backend.routes.admin.drivers.db_supabase.get_rows",
        AsyncMock(side_effect=get_rows_side),
    ):
        asyncio.run(admin_drivers.admin_get_drivers(search=search))
    return captured


def _or_clauses(filters: dict) -> list[dict]:
    return filters.get("$or", [])


def _regex_for(filters: dict, col: str) -> list[str]:
    return [c[col]["$regex"] for c in _or_clauses(filters) if col in c and "$regex" in c[col]]


# ---------------------------------------------------------------------------
# The reported bug
# ---------------------------------------------------------------------------


def test_name_match_reaches_the_driver_filter_as_user_id_in():
    """The bug: the users pre-query found "Nighil" but the $in leaf carrying
    that result never made it into the drivers query."""
    filters = _run_search("Nighil", matching_users=[{"id": "uid-nighil"}])

    in_clauses = [c["user_id"]["$in"] for c in _or_clauses(filters) if "user_id" in c and "$in" in c["user_id"]]
    assert in_clauses == [["uid-nighil"]], "user_id $in leaf missing — name search cannot match"


def test_name_search_renders_the_in_leaf_into_the_postgrest_clause():
    """End-to-end through the query layer: the clause the DB actually receives
    must contain the user_id IN term, not just the identifier ILIKEs."""
    from backend.db_supabase import _build_or_clause

    filters = _run_search("Nighil", matching_users=[{"id": "uid-nighil"}])
    clause = _build_or_clause(_or_clauses(filters))
    assert "user_id.in.(uid-nighil)" in clause


# ---------------------------------------------------------------------------
# Search semantics
# ---------------------------------------------------------------------------


def test_multi_token_name_is_anded_across_user_columns():
    """ "Nighil Kumar" must match first_name=Nighil + last_name=Kumar. A single
    ILIKE over the whole term matches neither column."""
    captured_user_filters: dict = {}

    async def get_rows_side(table, filters=None, **kw):
        if table == "users":
            captured_user_filters.update(filters or {})
            return [{"id": "uid-nighil"}]
        return []

    with patch(
        "backend.routes.admin.drivers.db_supabase.get_rows",
        AsyncMock(side_effect=get_rows_side),
    ):
        asyncio.run(admin_drivers.admin_get_drivers(search="Nighil Kumar"))

    and_clauses = captured_user_filters.get("$and")
    assert and_clauses is not None and len(and_clauses) == 2, "tokens must be ANDed, one $or per token"
    terms_per_token = [{next(iter(c.values()))["$regex"] for c in sub["$or"]} for sub in and_clauses]
    assert terms_per_token == [{"Nighil"}, {"Kumar"}]
    # Each token is ORed across every user column we search.
    for sub in and_clauses:
        assert {next(iter(c)) for c in sub["$or"]} == set(admin_drivers._DRIVER_SEARCH_USER_COLUMNS)


def test_single_token_skips_the_and_wrapper():
    """One token needs no $and — keeps the common query identical to before."""
    captured_user_filters: dict = {}

    async def get_rows_side(table, filters=None, **kw):
        if table == "users":
            captured_user_filters.update(filters or {})
        return []

    with patch(
        "backend.routes.admin.drivers.db_supabase.get_rows",
        AsyncMock(side_effect=get_rows_side),
    ):
        asyncio.run(admin_drivers.admin_get_drivers(search="Nighil"))

    assert "$and" not in captured_user_filters
    assert "$or" in captured_user_filters


def test_drivers_name_mirror_is_searched():
    """A driver whose users row is missing/stale must still be findable via the
    denormalized drivers.name column."""
    filters = _run_search("Nighil")
    assert "Nighil" in _regex_for(filters, "name")


def test_identifier_columns_still_match_the_whole_term():
    filters = _run_search("ABC-123")
    for col in ("license_plate", "driver_code"):
        assert "ABC-123" in _regex_for(filters, col), f"{col} no longer searched"


def test_pasted_id_still_matches_the_id_columns():
    """A pasted driver-row or user UUID must resolve the driver even when it
    matches nothing else — codex review r3548013237."""
    filters = _run_search("uid-driver-target")
    for col in ("id", "user_id"):
        assert "uid-driver-target" in _regex_for(filters, col), f"{col} no longer searched"


def test_short_terms_do_not_substring_match_opaque_ids():
    """`id`/`user_id` hold long opaque IDs, so a short term matches a large
    share of them by coincidence and buries the real result."""
    filters = _run_search("ab")
    assert _regex_for(filters, "id") == []
    assert not any("$regex" in c.get("user_id", {}) for c in _or_clauses(filters))


def test_multi_token_terms_do_not_search_id_columns():
    """A name with a space is never a pasted ID."""
    filters = _run_search("Nighil Kumar")
    assert _regex_for(filters, "id") == []


def test_no_user_match_still_searches_driver_columns():
    """An empty users pre-query must not produce an empty $or (which the query
    layer now rejects rather than running unfiltered)."""
    filters = _run_search("ABC-123", matching_users=[])
    assert _or_clauses(filters), "$or must not be empty"
    assert not any("$in" in c.get("user_id", {}) for c in _or_clauses(filters))


# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term,expected_digits",
    [
        ("(306) 555-1234", "3065551234"),
        ("306-555-1234", "3065551234"),
        ("+1 306 555 1234", "13065551234"),
    ],
)
def test_formatted_phone_also_matches_digits_only(term, expected_digits):
    """Stored numbers are E.164 ("+13065551234"), so the formatted term the
    admin pastes never substring-matches on its own."""
    filters = _run_search(term)
    assert expected_digits in _regex_for(filters, "phone")


@pytest.mark.parametrize("term", ["Nighil", "Nighil Kumar", "ABC-123", "12"])
def test_non_phone_terms_are_not_digit_normalized(term):
    """A name must never be mangled into a digits-only clause."""
    assert admin_drivers._phone_digits(term) is None


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_term_is_length_capped():
    filters = _run_search("N" * 500)
    for regex in _regex_for(filters, "name"):
        assert len(regex) <= admin_drivers._DRIVER_SEARCH_MAX_TERM


def test_token_count_is_capped():
    assert len(admin_drivers._driver_search_tokens(" ".join(["tok"] * 50))) == admin_drivers._DRIVER_SEARCH_MAX_TOKENS


def test_whitespace_only_search_applies_no_filter():
    filters = _run_search("   ")
    assert "$or" not in filters


def test_users_prequery_truncation_is_logged(caplog):
    """Hitting the cap can drop the very driver being searched for, so it must
    not pass silently."""
    hits = [{"id": f"uid-{i}"} for i in range(admin_drivers._DRIVER_SEARCH_USER_LIMIT)]
    with caplog.at_level("WARNING"):
        _run_search("Kumar", matching_users=hits)
    assert any("truncated" in r.message or "cap" in r.message for r in caplog.records)


def test_truncation_log_does_not_leak_the_search_term(caplog):
    """PIPEDA: search terms are names/phones/emails and must stay out of logs."""
    hits = [{"id": f"uid-{i}"} for i in range(admin_drivers._DRIVER_SEARCH_USER_LIMIT)]
    with caplog.at_level("WARNING"):
        _run_search("Nighil", matching_users=hits)
    for record in caplog.records:
        assert "Nighil" not in record.getMessage()
