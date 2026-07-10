"""Section-wallets migration contract (226 / M5.1).

CI has no Postgres; pin the SQL contract textually (same pattern as
test_ai_retention_migration.py / test_deletion_hard_delete_migration.py).
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL = (MIGRATIONS / "226_corporate_section_wallets.sql").read_text(encoding="utf-8")


class TestSectionWallets:
    def test_section_id_added_nullable_with_fk(self):
        assert "ADD COLUMN IF NOT EXISTS section_id UUID REFERENCES corporate_sections(id)" in SQL
        # Money safety: never cascade-delete a wallet row with a section.
        assert "section_id UUID REFERENCES corporate_sections(id) ON DELETE CASCADE" not in SQL

    def test_head_member_id_added_set_null(self):
        assert "ADD COLUMN IF NOT EXISTS head_member_id UUID REFERENCES corporate_members(id) ON DELETE SET NULL" in SQL

    def test_old_unique_dropped_by_lookup_not_hardcoded_name(self):
        # The inline UNIQUE from migration 27 is auto-named; dropping a guessed
        # name would silently no-op on environments where it differs.
        assert "pg_constraint" in SQL
        assert "DROP CONSTRAINT %I" in SQL

    def test_partial_unique_indexes(self):
        assert "corp_wallets_master_unique" in SQL
        assert "WHERE section_id IS NULL" in SQL
        assert "corp_wallets_section_unique" in SQL
        assert "WHERE section_id IS NOT NULL" in SQL

    def test_rollback_documented(self):
        assert "Rollback" in SQL
