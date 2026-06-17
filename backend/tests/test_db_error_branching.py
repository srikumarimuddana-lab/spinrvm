"""Tests for pg_error_code / db_error_text (T4).

run_sync wraps PostgREST errors in DatabaseError/DuplicateRecordError whose
str() is a generic sentinel; the real SQLSTATE + constraint name live in
details['original'] and the __cause__ chain. Branching on str(e) (as the
ride-create / completion fallbacks did) silently missed every constraint-
specific case. These pin that the helpers surface the real signal.
"""

import pytest

from utils.error_handling import DatabaseError, DuplicateRecordError, db_error_text, pg_error_code

pytestmark = pytest.mark.unit


def test_str_of_wrapped_error_is_only_the_sentinel():
    # This is exactly why matching str(e) was broken.
    e = DuplicateRecordError(
        details={"original": 'duplicate key value violates unique constraint "rides_one_active_per_rider"'}
    )
    assert str(e).lower() == "record already exists"
    assert "rides_one_active_per_rider" not in str(e).lower()


def test_db_error_text_surfaces_original_constraint_name():
    e = DuplicateRecordError(
        details={"original": 'duplicate key value violates unique constraint "rides_one_active_per_rider"'}
    )
    text = db_error_text(e)
    assert "record already exists" in text  # the sentinel is included...
    assert "rides_one_active_per_rider" in text  # ...and so is the REAL constraint


def test_db_error_text_includes_cause_chain():
    cause = ValueError("PGRST204: could not find the 'foo' column")
    try:
        raise DatabaseError(details={}) from cause
    except DatabaseError as e:
        assert "pgrst204" in db_error_text(e)


def test_pg_error_code_reads_cause_code():
    class FakeAPIError(Exception):
        code = "23505"

    try:
        raise DatabaseError() from FakeAPIError("dup")
    except DatabaseError as e:
        assert pg_error_code(e) == "23505"


def test_pg_error_code_empty_when_absent():
    assert pg_error_code(ValueError("no code here")) == ""
