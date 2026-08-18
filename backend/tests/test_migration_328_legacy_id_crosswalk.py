"""Migration-SQL contract tests for backend/migrations/328_legacy_id_crosswalk.sql.

Same style as test_migration_319_late_tip_types.py: parses the migration
file directly (no DB involved) so a future edit can't silently drop the
service-role-only RLS lockdown, widen entity_type, or swap in a native-UUID
spinr_user_id column that would break the FK-type parity with
users.id/drivers.id (both TEXT — see backend/supabase_schema.sql; this is
exactly the class of bug test_migration_fk_column_types.py exists to catch
for inline FKs, but this column deliberately has no inline FK since its
target table depends on entity_type).

CR #4106: schema-only migration, backfill explicitly deferred until a fresh
old-app export lands (ACTION_ITEMS.md A34) — no data-touching assertions
here, on purpose.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "328_legacy_id_crosswalk.sql"


class TestMigration328Contract:
    @pytest.fixture(autouse=True)
    def _sql(self):
        self.sql = _MIGRATION.read_text()

    def test_creates_legacy_id_crosswalk_table(self):
        assert re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+public\.legacy_id_crosswalk",
            self.sql,
            re.IGNORECASE,
        )

    def test_has_both_old_id_columns_nullable(self):
        # Neither old-ID column is NOT NULL: a driver may have one, both, or
        # (pre-backfill) neither; a rider only ever gets old_mongo_object_id.
        for col in ("old_mongo_object_id", "old_numeric_driver_id"):
            decl = re.search(rf"^\s*{col}\s+TEXT\s*,?\s*$", self.sql, re.IGNORECASE | re.MULTILINE)
            assert decl is not None, f"{col} should be a plain nullable TEXT column"

    def test_at_least_one_old_id_required(self):
        assert "CONSTRAINT legacy_id_crosswalk_has_an_old_id CHECK" in self.sql
        assert "old_mongo_object_id IS NOT NULL OR old_numeric_driver_id IS NOT NULL" in self.sql

    def test_spinr_user_id_is_text_not_null_no_fk(self):
        """Must match users.id/drivers.id's actual TEXT type (both TEXT
        PRIMARY KEY per backend/supabase_schema.sql, not native UUID) — a
        UUID-typed column here would hit the exact class of bug
        test_migration_fk_column_types.py's docstring describes for migration
        281 (FK constraint cannot be implemented across incompatible types).
        No REFERENCES clause: the target table (users vs. drivers) depends
        on entity_type, which a single declarative FK can't express."""
        decl = re.search(r"^\s*spinr_user_id\s+TEXT\s+NOT\s+NULL\s*,?\s*$", self.sql, re.IGNORECASE | re.MULTILINE)
        assert decl is not None
        assert "REFERENCES" not in decl.group(0).upper()

    def test_entity_type_restricted_to_driver_or_rider(self):
        assert re.search(
            r"entity_type\s+TEXT\s+NOT\s+NULL\s+CHECK\s*\(\s*entity_type\s+IN\s*\(\s*'driver'\s*,\s*'rider'\s*\)\s*\)",
            self.sql,
            re.IGNORECASE,
        )

    def test_batch_column_present_and_not_null(self):
        assert re.search(r"^\s*batch\s+TEXT\s+NOT\s+NULL\s*,?\s*$", self.sql, re.IGNORECASE | re.MULTILINE)

    def test_indexes_on_both_old_id_lookup_columns(self):
        # The table's whole reason to exist: look up by old ID to find the
        # new UUID. Partial indexes since both old-ID columns are often NULL.
        assert re.search(
            r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+legacy_id_crosswalk_mongo_object_id_idx\s+"
            r"ON\s+public\.legacy_id_crosswalk\s*\(\s*old_mongo_object_id\s*\)\s*"
            r"WHERE\s+old_mongo_object_id\s+IS\s+NOT\s+NULL",
            self.sql,
            re.IGNORECASE,
        )
        assert re.search(
            r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+legacy_id_crosswalk_numeric_driver_id_idx\s+"
            r"ON\s+public\.legacy_id_crosswalk\s*\(\s*old_numeric_driver_id\s*\)\s*"
            r"WHERE\s+old_numeric_driver_id\s+IS\s+NOT\s+NULL",
            self.sql,
            re.IGNORECASE,
        )

    def test_index_on_spinr_user_id_for_reverse_lookup(self):
        assert re.search(
            r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+legacy_id_crosswalk_spinr_user_id_idx\s+"
            r"ON\s+public\.legacy_id_crosswalk\s*\(\s*spinr_user_id\s*\)",
            self.sql,
            re.IGNORECASE,
        )

    def test_rls_enabled_with_no_policies(self):
        """Service-role-only pattern — same precedent as
        190_marketing_consents.sql. RLS enabled but zero CREATE POLICY
        statements: anon/authenticated access is denied entirely, only the
        backend service role (which bypasses RLS) can read/write."""
        assert re.search(
            r"ALTER\s+TABLE\s+public\.legacy_id_crosswalk\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
            self.sql,
            re.IGNORECASE,
        )
        assert "Intentionally no policies: service-role-only" in self.sql
        assert not re.search(r"CREATE\s+POLICY", self.sql, re.IGNORECASE)

    def test_no_backfill_statements(self):
        """CR #4106 scope: schema only. An INSERT here would mean someone
        smuggled in a backfill using only today's Supabase-side data, which
        the CR explicitly says not to do (it would just re-encode the
        existing phone-match, not add real cross-referencing value)."""
        assert not re.search(r"^\s*INSERT\s+INTO", self.sql, re.IGNORECASE | re.MULTILINE)

    def test_rollback_comment_present(self):
        assert re.search(r"^--\s+[Rr]ollback:", self.sql, re.MULTILINE)

    def test_rollback_is_plain_drop_table(self):
        # No rows exist yet, so the rollback should be a straightforward
        # DROP, not a data-preserving migration-down script.
        assert "DROP TABLE IF EXISTS public.legacy_id_crosswalk;" in self.sql
