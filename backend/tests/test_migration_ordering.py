"""Migration runners must apply files in numeric-prefix order.

Regression: both runners used plain lexicographic sort, which runs
"224_…" before "48_…" (and "157_…" before "15_…") on a fresh
environment, so an ALTER TABLE could execute before the CREATE TABLE
it depends on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_migrations import _discover_migrations, migration_sort_key  # noqa: E402

pytestmark = pytest.mark.unit


def test_numeric_prefix_orders_before_lexicographic():
    names = [
        "224_notification_preferences_earnings_summary.sql",
        "48_notifications_tables.sql",
        "157_driver_vehicle_history.sql",
        "15_ride_aggregate_columns.sql",
        "00_schema_migrations_table.sql",
    ]
    ordered = sorted(names, key=migration_sort_key)
    assert ordered == [
        "00_schema_migrations_table.sql",
        "15_ride_aggregate_columns.sql",
        "48_notifications_tables.sql",
        "157_driver_vehicle_history.sql",
        "224_notification_preferences_earnings_summary.sql",
    ]


def test_duplicate_prefixes_tiebreak_on_full_filename():
    # Pre-existing duplicate prefixes (08, 28, …) are keyed by full filename.
    names = ["48_zebra.sql", "48_notifications_tables.sql"]
    assert sorted(names, key=migration_sort_key) == [
        "48_notifications_tables.sql",
        "48_zebra.sql",
    ]


def test_real_migrations_dir_is_monotonic_by_prefix():
    files = [p.name for p in _discover_migrations()]
    prefixes = [migration_sort_key(n)[0] for n in files]
    assert prefixes == sorted(prefixes)
    # The specific pair from the review finding:
    assert files.index("48_notifications_tables.sql") < files.index(
        "224_notification_preferences_earnings_summary.sql"
    )
