"""SQL contract for the Period-1 distance accumulator (migration 250).

Scalar-only, privacy-minimizing: a running kilometre total + span start on the
drivers row, plus an off-by-default settings flag. No coordinate columns.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "250_period1_distance_accumulator.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_adds_scalar_accumulator_columns_no_coordinates() -> None:
    sql = _sql()
    assert "ADD COLUMN IF NOT EXISTS period1_accum_km    numeric(10, 3) NOT NULL DEFAULT 0" in sql
    assert "ADD COLUMN IF NOT EXISTS period1_accum_since timestamptz" in sql
    # Privacy: scalar total + span start only — no coordinate/geometry column.
    lower = sql.lower()
    assert "geometry" not in lower
    assert "geography" not in lower
    assert " point" not in lower


def test_flag_defaults_off() -> None:
    sql = _sql()
    assert "period1_distance_tracking_enabled boolean NOT NULL DEFAULT false" in sql


def test_reversible_on_paper() -> None:
    assert "-- Rollback:" in _sql()
