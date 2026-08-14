"""Regression checks (migration 295): corrects migration 294 (ACTION_ITEMS.md
B17, PR #3510) — its `ON DELETE SET NULL` FK action on
`financial_events.ride_id` cannot actually fire, because
`_financial_events_immutable()` (migration 58, untouched by 294)
unconditionally raises on any UPDATE. PostgreSQL implements FK referential
actions as an internal UPDATE against the referencing table that goes
through the normal trigger machinery, so the SET NULL action trips the same
BEFORE UPDATE trigger a direct UPDATE would. Step B would still abort on the
first paid ride to cross 7 years — just with a trigger-raised error instead
of a raw foreign_key_violation.

CI has no Postgres, so these checks pin the SQL contract textually — same
convention as every other migration test in this repo
(test_deletion_hard_delete_migration.py, test_financial_events_ride_id_fk_
contract.py).
"""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
SQL_294 = (MIGRATIONS / "294_financial_events_ride_id_set_null.sql").read_text()
SQL_295 = (MIGRATIONS / "295_financial_events_immutable_allows_fk_setnull.sql").read_text()

TRIGGER_FN = SQL_295.split("CREATE OR REPLACE FUNCTION _financial_events_immutable")[1]


class TestPrecedingMigrationUnchanged:
    def test_294_still_adds_on_delete_set_null(self):
        # 294 itself is correct and untouched — this migration only fixes
        # what stops it from working, it doesn't redo 294's job.
        assert "ON DELETE SET NULL" in SQL_294

    def test_295_does_not_redefine_the_fk_constraint(self):
        assert "ADD CONSTRAINT financial_events_ride_id_fkey" not in SQL_295


class TestDeleteGateUnchanged:
    def test_delete_path_still_gated_by_289s_guc(self):
        assert "spinr.financial_events.allow_delete" in TRIGGER_FN
        assert "TG_OP = 'DELETE'" in TRIGGER_FN


class TestUpdateAllowanceForFkSetNull:
    def test_permits_nulling_ride_id_unconditionally(self):
        # No GUC gate for this branch — see migration 295's header for why
        # one isn't possible (Postgres's own FK machinery issues this
        # UPDATE internally, with no chance for application code to set a
        # session GUC first).
        assert "NEW.ride_id IS NULL" in TRIGGER_FN
        assert "OLD.ride_id IS NOT NULL" in TRIGGER_FN

    def test_pins_every_other_column_against_tampering(self):
        for col in ("id", "event_type", "user_id", "delta_cents", "ref", "metadata", "created_at"):
            assert f"NEW.{col}" in TRIGGER_FN, f"trigger does not pin {col} against tampering"

    def test_no_guc_check_gates_the_ride_id_null_branch(self):
        # Unlike the DELETE branch, this UPDATE allowance must not depend on
        # any current_setting()/GUC check — it has to work unconditionally
        # since the FK action can't set one first.
        update_branch = TRIGGER_FN.split("IF NEW.ride_id IS NULL")[1].split("RAISE EXCEPTION")[0]
        assert "current_setting" not in update_branch

    def test_unconditional_raise_survives_as_fallback_for_other_shapes(self):
        assert "financial_events rows are append-only and cannot be modified" in TRIGGER_FN


class TestRollback:
    def test_rollback_plan_documented(self):
        assert "Rollback:" in SQL_295
