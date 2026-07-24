"""Regression tests for WS-12 (C3): atomic insurance-period transitions.

The two-step close+open in Python left a crash window where a driver could
end up with no open period row — a regulatory audit gap. The fix:
- Migration 253 adds record_insurance_period_transition RPC that does
  close+open in a single transaction, serialized by the partial unique index.
- insurance_periods.py calls the RPC instead of two separate queries.

This file validates the migration contract and the Python-side RPC call.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MIG_253 = (_ROOT / "migrations" / "253_insurance_period_transition_rpc.sql").read_text()
_INSURANCE_PY = (_ROOT / "utils" / "insurance_periods.py").read_text()


class TestMigration253:
    def test_creates_rpc_function(self):
        assert "record_insurance_period_transition" in _MIG_253

    def test_security_definer(self):
        assert "SECURITY DEFINER" in _MIG_253

    def test_pinned_search_path(self):
        assert "search_path" in _MIG_253

    def test_period_3_requires_ride_id(self):
        assert "p_new_period = 3" in _MIG_253
        assert "p_ride_id IS NULL" in _MIG_253

    def test_returns_status_discriminator(self):
        assert "'ok'" in _MIG_253
        assert "'noop'" in _MIG_253
        assert "'race'" in _MIG_253

    def test_handles_unique_violation(self):
        assert "unique_violation" in _MIG_253

    def test_has_rollback_marker(self):
        assert "rollback:" in _MIG_253.lower()

    def test_close_and_open_in_same_function(self):
        assert "UPDATE driver_insurance_periods" in _MIG_253
        assert "INSERT INTO driver_insurance_periods" in _MIG_253


class TestPythonUsesRpc:
    def test_calls_rpc(self):
        assert "record_insurance_period_transition" in _INSURANCE_PY
        assert ".rpc(" in _INSURANCE_PY

    def test_no_two_step_close_insert(self):
        """The old two-step pattern (separate close UPDATE + INSERT) must be gone."""
        lines = _INSURANCE_PY.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if '.table("driver_insurance_periods").update(' in stripped:
                pytest.fail("Python must not do a separate UPDATE on driver_insurance_periods — use the RPC")
            if '.table("driver_insurance_periods").insert(' in stripped:
                pytest.fail("Python must not do a separate INSERT on driver_insurance_periods — use the RPC")

    def test_handles_noop_status(self):
        assert '"noop"' in _INSURANCE_PY

    def test_handles_race_status(self):
        assert '"race"' in _INSURANCE_PY

    def test_still_swallows_exceptions(self):
        """Compliance-grade: failures must not block the state machine."""
        assert "except Exception:" in _INSURANCE_PY

    def test_emits_failure_metric(self):
        assert "spinr_insurance_period_write_failed_total" in _INSURANCE_PY
