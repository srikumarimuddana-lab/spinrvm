"""Contract test for migration 294 (ACTION_ITEMS.md B17).

financial_events.ride_id must be ON DELETE SET NULL, not the default NO
ACTION migration 58 shipped it with. Without this, purge_pii_retention()
Step B's `DELETE FROM rides WHERE created_at < now() - 7y` raises
foreign_key_violation on the first paid ride to cross 7 years (every paid
ride has a retained financial_events row pointing at it), with no exception
handler on Step B — aborting the whole daily purge run, including Step A's
GPS anonymization, on every subsequent run.

No live database is available in this environment to apply the migration
end-to-end, so — same approach as test_wallet_apply_delta_contract.py and
test_allowance_rpc_sign_contract.py — these assertions read the migration
SQL directly and pin the invariants that matter.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[1] / "migrations"
_MIGRATION = _MIGRATIONS_DIR / "294_financial_events_ride_id_set_null.sql"
_SQL = _MIGRATION.read_text()

_ORIGINAL_MIGRATION = _MIGRATIONS_DIR / "58_financial_events.sql"
_ORIGINAL_SQL = _ORIGINAL_MIGRATION.read_text()


def test_migration_294_exists_and_is_next_available_number():
    """Guards against a future collision silently reusing 294 for something
    else — run_migrations.py keys idempotency off the full filename, so a
    rename would re-run this migration under a new name."""
    assert _MIGRATION.exists()


def test_original_migration_58_fk_has_no_delete_action():
    """Pins the bug this migration fixes: migration 58's inline FK has no
    ON DELETE clause at all (defaults to NO ACTION), so it must stay
    unmodified (append-only convention) while 294 supersedes it via ALTER."""
    assert "ride_id" in _ORIGINAL_SQL
    assert "REFERENCES rides(id)" in _ORIGINAL_SQL
    fk_line = next(line for line in _ORIGINAL_SQL.splitlines() if "REFERENCES rides(id)" in line)
    assert "ON DELETE" not in fk_line.upper()


def test_294_adds_on_delete_set_null_for_ride_id():
    assert "ON DELETE SET NULL" in _SQL
    # Must be the ride_id -> rides(id) FK specifically, not some other table's.
    add_at = _SQL.index("ADD CONSTRAINT financial_events_ride_id_fkey")
    # The header comment also says "ON DELETE SET NULL" in prose (explaining
    # the choice) — search from add_at so this pins the executable ALTER
    # TABLE statement, not the comment above it.
    references_at = _SQL.index("REFERENCES public.rides(id)", add_at)
    set_null_at = _SQL.index("ON DELETE SET NULL", add_at)
    assert add_at < references_at < set_null_at, (
        "ON DELETE SET NULL must apply to the ride_id -> rides(id) FK, "
        "not be a dangling clause on an unrelated statement"
    )


def test_294_does_not_cascade():
    """financial_events is the 7-year CRA/SOC2 money ledger (migration 58's
    own header comment) — it must survive its ride being purged. CASCADE
    would delete the tax record itself, which defeats the table's purpose."""
    assert "ON DELETE CASCADE" not in _SQL


def test_294_resolves_constraint_name_dynamically_not_hardcoded_drop():
    """Mirrors migration 273's precedent: look up the FK by column via
    pg_constraint rather than assuming Postgres' default constraint name, so
    this converges even if an environment's constraint was named differently."""
    assert "pg_constraint" in _SQL
    assert "DROP CONSTRAINT %I" in _SQL or "DROP CONSTRAINT IF EXISTS" in _SQL


def test_294_reloads_postgrest_schema_cache():
    assert "NOTIFY pgrst" in _SQL
