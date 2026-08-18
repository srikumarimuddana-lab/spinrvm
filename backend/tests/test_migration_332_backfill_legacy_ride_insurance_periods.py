"""Migration-SQL contract tests for
backend/migrations/332_backfill_legacy_ride_insurance_periods.sql.

Same style as test_migration_328_legacy_id_crosswalk.py: parses the
migration file directly (no DB involved) so a future edit can't silently
drop the `is_reconstructed` marker, weaken the append-only trigger's
column-lock, or widen the backfill's WHERE clause beyond the exact 182
rides verified against production before this migration was written
(see PR body for the verification queries and results — all read-only,
run before this file existed).

CR #4081: reconstruct-and-flag remediation for the 186 legacy-imported
rides with zero driver_insurance_periods rows. Decision recorded and
approved in issue #4081 (2026-08-18). 182 of the 186 get a clean two-row
(Period 2 + Period 3) reconstruction; 4 are deliberately excluded (3 with
no driver_id, 1 with no arrival/start timestamps) and documented in the
migration's own header comment, not silently dropped.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1] / "migrations" / "332_backfill_legacy_ride_insurance_periods.sql"
)


class TestMigration332Contract:
    @pytest.fixture(autouse=True)
    def _sql(self):
        self.sql = _MIGRATION.read_text()

    def test_adds_is_reconstructed_column_not_null_default_false(self):
        assert re.search(
            r"ALTER\s+TABLE\s+driver_insurance_periods\s+"
            r"ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+is_reconstructed\s+"
            r"boolean\s+NOT\s+NULL\s+DEFAULT\s+false",
            self.sql,
            re.IGNORECASE,
        )

    def test_column_comment_present(self):
        assert "COMMENT ON COLUMN driver_insurance_periods.is_reconstructed IS" in self.sql

    def test_immutability_trigger_protects_new_column(self):
        """The append-only trigger from migration 64 explicitly enumerates
        every immutable column. Adding a new column without extending that
        list would silently allow is_reconstructed to be flipped after
        insert — this migration must re-create the function with
        is_reconstructed added to the comparison."""
        assert "CREATE OR REPLACE FUNCTION _driver_insurance_periods_immutable()" in self.sql
        # The comparison block must include the new column alongside the
        # original set migration 64 established.
        for col in ("id", "driver_id", "period", "started_at", "ride_id", "created_at", "is_reconstructed"):
            assert re.search(
                rf"NEW\.{col}\s+IS\s+DISTINCT\s+FROM\s+OLD\.{col}",
                self.sql,
                re.IGNORECASE,
            ), f"immutability trigger must still guard {col}"

    def test_trigger_still_blocks_delete_and_reopen(self):
        # Must not have weakened the original migration-64 guarantees while
        # extending the function.
        assert "driver_insurance_periods rows are append-only and cannot be deleted" in self.sql
        assert "is already closed and cannot be modified" in self.sql
        assert "UPDATE must set ended_at to a non-NULL timestamp" in self.sql

    def test_period_2_backfill_marks_reconstructed_true(self):
        period_2_block = self.sql[self.sql.index("-- Period 2 (en route") : self.sql.index("-- Period 3 (passenger")]
        assert re.search(r"\btrue\b", period_2_block, re.IGNORECASE)
        assert "driver_arrived_at" in period_2_block
        assert "r.started_at" in period_2_block

    def test_period_3_backfill_carries_ride_id_and_marks_reconstructed_true(self):
        period_3_block = self.sql[self.sql.index("Period 3") :]
        assert re.search(r"\btrue\b", period_3_block, re.IGNORECASE)
        assert "r.id" in period_3_block or "ride_id" in period_3_block
        assert "ride_completed_at" in period_3_block

    def test_backfill_scoped_to_legacy_import_metadata(self):
        # Must never touch a native (non-imported) ride's rows.
        assert self.sql.count("legacy_import_metadata IS NOT NULL") == 2
        assert self.sql.count("legacy_import_metadata != '{}'::jsonb") == 2

    def test_backfill_excludes_rides_with_no_driver(self):
        assert self.sql.count("r.driver_id IS NOT NULL") == 2

    def test_backfill_idempotent_via_not_exists_guard(self):
        """Matches migration 65's own established idempotency pattern —
        re-running this file is a no-op the second time, on top of the
        migration runner's own schema_migrations tracking."""
        assert re.search(
            r"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+driver_insurance_periods\s+dip\s+"
            r"WHERE\s+dip\.ride_id\s*=\s*r\.id\s+AND\s+dip\.period\s*=\s*2\s*\)",
            self.sql,
            re.IGNORECASE,
        )
        assert re.search(
            r"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+driver_insurance_periods\s+dip\s+"
            r"WHERE\s+dip\.ride_id\s*=\s*r\.id\s+AND\s+dip\.period\s*=\s*3\s*\)",
            self.sql,
            re.IGNORECASE,
        )

    def test_no_row_ever_left_open(self):
        """Every INSERT in this migration must set ended_at explicitly —
        these are all closed, historical periods. Leaving one open would
        risk colliding with the driver_insurance_periods_open partial
        unique index (one open row per driver) against a driver's real,
        currently-open period."""
        insert_blocks = re.findall(
            r"INSERT INTO driver_insurance_periods\s*\([^)]*\)\s*SELECT.*?(?=INSERT INTO|\Z)",
            self.sql,
            re.IGNORECASE | re.DOTALL,
        )
        assert len(insert_blocks) == 2
        for block in insert_blocks:
            # Column list must include ended_at, and it must not be NULL —
            # both backfills pass a real timestamp column, never a literal NULL.
            assert "ended_at" in block
            assert not re.search(r"ended_at.{0,40}NULL", block.split("FROM")[0], re.IGNORECASE)

    def test_documents_the_four_excluded_rides(self):
        """CR-4081's whole point: excluded rides must be explicitly named
        and reasoned about, not silently absent from the WHERE clause with
        no record anyone considered them."""
        for ride_id in (
            "bda2a258-7987-4344-882e-ca202df17d43",
            "ab5c5f5b-4c3e-4989-90a8-8163b69b08b5",
            "ab0acdfc-46fd-430e-a6e2-502c1a2c7642",
            "e8c7f1b5-84f4-4a64-9f98-1b8ca70ba251",
        ):
            assert ride_id in self.sql, f"excluded ride {ride_id} must be documented in the migration"

    def test_no_write_to_rides_table(self):
        """Scope discipline: this migration only ever writes to
        driver_insurance_periods. Modifying `rides` (e.g. tagging the 4
        excluded rows) is explicitly out of scope for this file."""
        assert not re.search(r"UPDATE\s+rides\b", self.sql, re.IGNORECASE)
        assert not re.search(r"INSERT\s+INTO\s+rides\b", self.sql, re.IGNORECASE)

    def test_rollback_comment_present(self):
        assert re.search(r"^--\s+[Rr]ollback:", self.sql, re.MULTILINE)

    def test_rollback_deletes_only_reconstructed_rows(self):
        assert "DELETE FROM driver_insurance_periods WHERE is_reconstructed = true;" in self.sql
