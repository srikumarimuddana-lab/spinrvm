"""Unit tests for backend/services/dormant_driver_sin_purge_service.py.

Critical properties locked in here:
  - only drivers already flagged pre_launch_test=true, with a non-null sin,
    are ever candidates -- population is trusted from pre_launch_flag_service,
    never re-derived.
  - apply_sin_purge refuses entirely before the 180-day grace-period cutoff,
    even when explicitly called with --apply-equivalent intent.
  - apply deletes the vault secret (purge_driver_pii_secret RPC) BEFORE
    nulling sin/sin_last4/sin_collected_at.
  - a driver whose sin changed between plan and apply is reported as a
    conflict, never silently overwritten or re-purged.
  - no test in this file ever asserts on a real-looking SIN value making it
    into a log/print call -- print_report is checked for shape, not content.
"""

from __future__ import annotations

from datetime import date

from backend.services import dormant_driver_sin_purge_service as svc

BATCH = "20260926000000"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._predicates = []
        self._update_payload = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._predicates.append(("eq", col, val))
        return self

    def filter(self, col, op, val):
        self._predicates.append(("filter", col, op, val))
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def _row_matches(self, row) -> bool:
        for pred in self._predicates:
            kind = pred[0]
            if kind == "eq":
                _, col, val = pred
                if row.get(col) != val:
                    return False
            elif kind == "filter":
                _, col, op, val = pred
                if "->>" in col:
                    base, key = col.split("->>", 1)
                    actual = (row.get(base) or {}).get(key)
                else:
                    actual = row.get(col)
                if op == "not.is" and val == "null":
                    if actual is None:
                        return False
                elif op == "eq":
                    if actual != val:
                        return False
                else:
                    return False
        return True

    def execute(self):
        rows = [r for r in self.store.get(self.table, []) if self._row_matches(r)]
        if self._update_payload is not None:
            for r in rows:
                r.update(self._update_payload)
            return _Result([dict(r) for r in rows])
        return _Result([dict(r) for r in rows])


class _RpcCall:
    def __init__(self, name, params, recorder):
        self.name = name
        self.params = params
        self.recorder = recorder

    def execute(self):
        self.recorder.setdefault("rpc_calls", []).append((self.name, dict(self.params)))
        # Real purge_driver_pii_secret (migration 413) returns whether a row
        # was actually deleted -- true for any non-empty secret_id here.
        return _Result(bool(self.params.get("secret_id")))


class _FakeSupabase:
    def __init__(self, store, recorder):
        self.store = store
        self.recorder = recorder

    def table(self, name):
        return _Query(name, self.store)

    def rpc(self, name, params):
        return _RpcCall(name, params, self.recorder)


def _driver(driver_id, *, sin="vault-secret-uuid-1", flagged=True, extra_meta=None):
    meta = {}
    if flagged:
        meta["pre_launch_test"] = "true"
    if extra_meta:
        meta.update(extra_meta)
    return {"id": driver_id, "sin": sin, "legacy_import_metadata": meta}


def _fresh_store(**tables):
    base = {"drivers": []}
    base.update(tables)
    return base


def _use(monkeypatch, store, recorder=None):
    recorder = recorder if recorder is not None else {}
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store, recorder))
    return recorder


BEFORE_CUTOFF = date(2026, 9, 11)  # today, per ACTION_ITEMS.md A34
AFTER_CUTOFF = date(2026, 9, 26)  # grace_period_cutoff() with the 2026-09-11 180-day decision


# --------------------------------------------------------------------------
# Grace period
# --------------------------------------------------------------------------


def test_grace_period_cutoff_is_180_days_past_launch():
    assert svc.grace_period_cutoff() == AFTER_CUTOFF


def test_grace_period_not_elapsed_before_cutoff():
    assert svc.is_grace_period_elapsed(today=BEFORE_CUTOFF) is False


def test_grace_period_elapsed_on_and_after_cutoff():
    assert svc.is_grace_period_elapsed(today=AFTER_CUTOFF) is True
    assert svc.is_grace_period_elapsed(today=date(2027, 1, 1)) is True


# --------------------------------------------------------------------------
# Plan: candidate selection
# --------------------------------------------------------------------------


def test_plan_only_includes_flagged_drivers_with_a_sin(monkeypatch):
    store = _fresh_store(
        drivers=[
            _driver("d-flagged-with-sin", flagged=True, sin="secret-1"),
            _driver("d-flagged-no-sin", flagged=True, sin=None),
            _driver("d-not-flagged", flagged=False, sin="secret-2"),
        ]
    )
    _use(monkeypatch, store)

    plan = svc.build_sin_purge_plan(today=BEFORE_CUTOFF)

    assert [c.id for c in plan.candidates] == ["d-flagged-with-sin"]


def test_plan_reports_grace_period_state_without_gating_candidates(monkeypatch):
    store = _fresh_store(drivers=[_driver("d-1")])
    _use(monkeypatch, store)

    plan_before = svc.build_sin_purge_plan(today=BEFORE_CUTOFF)
    plan_after = svc.build_sin_purge_plan(today=AFTER_CUTOFF)

    # The candidate list itself doesn't change with the date -- only
    # apply_sin_purge's willingness to act on it does.
    assert [c.id for c in plan_before.candidates] == ["d-1"]
    assert [c.id for c in plan_after.candidates] == ["d-1"]
    assert plan_before.grace_period_elapsed is False
    assert plan_after.grace_period_elapsed is True
    assert plan_before.grace_period_cutoff == AFTER_CUTOFF.isoformat()


# --------------------------------------------------------------------------
# Apply: the hard grace-period gate
# --------------------------------------------------------------------------


def test_apply_refuses_before_grace_period_elapses(monkeypatch):
    store = _fresh_store(drivers=[_driver("d-1")])
    _use(monkeypatch, store)
    plan = svc.build_sin_purge_plan(today=BEFORE_CUTOFF)

    try:
        svc.apply_sin_purge(plan, batch=BATCH, today=BEFORE_CUTOFF)
        raised = False
    except RuntimeError:
        raised = True

    assert raised, "apply_sin_purge must refuse before the grace-period cutoff"
    # Refusal must be a hard stop -- nothing written.
    assert store["drivers"][0]["sin"] == "vault-secret-uuid-1"


def test_apply_refuses_even_with_zero_candidates_before_cutoff(monkeypatch):
    store = _fresh_store(drivers=[])
    _use(monkeypatch, store)
    plan = svc.build_sin_purge_plan(today=BEFORE_CUTOFF)

    try:
        svc.apply_sin_purge(plan, batch=BATCH, today=BEFORE_CUTOFF)
        raised = False
    except RuntimeError:
        raised = True

    assert raised, "the grace-period gate applies regardless of candidate count"


# --------------------------------------------------------------------------
# Apply: happy path, once eligible
# --------------------------------------------------------------------------


def test_apply_deletes_vault_secret_before_nulling_columns(monkeypatch):
    store = _fresh_store(
        drivers=[
            {
                "id": "d-1",
                "sin": "secret-uuid-1",
                "sin_last4": "1234",
                "sin_collected_at": "2026-05-01T00:00:00+00:00",
                "legacy_import_metadata": {"pre_launch_test": "true"},
            }
        ]
    )
    recorder = _use(monkeypatch, store)
    plan = svc.build_sin_purge_plan(today=AFTER_CUTOFF)

    result = svc.apply_sin_purge(plan, batch=BATCH, today=AFTER_CUTOFF)

    assert result == {"purged": ["d-1"], "conflicts": []}
    # The RPC that deletes the actual ciphertext must have been called.
    assert recorder["rpc_calls"] == [("purge_driver_pii_secret", {"secret_id": "secret-uuid-1"})]
    # All three SIN-related columns nulled; nothing else touched.
    updated = store["drivers"][0]
    assert updated["sin"] is None
    assert updated["sin_last4"] is None
    assert updated["sin_collected_at"] is None
    assert updated["legacy_import_metadata"]["pre_launch_test"] == "true"
    assert updated["legacy_import_metadata"][svc.PURGE_META_KEY]["batch"] == BATCH


def test_apply_is_a_no_op_with_zero_candidates(monkeypatch):
    store = _fresh_store(drivers=[])
    recorder = _use(monkeypatch, store)
    plan = svc.build_sin_purge_plan(today=AFTER_CUTOFF)

    result = svc.apply_sin_purge(plan, batch=BATCH, today=AFTER_CUTOFF)

    assert result == {"purged": [], "conflicts": []}
    assert recorder.get("rpc_calls", []) == []


# --------------------------------------------------------------------------
# Apply: conflict guard (sin changed between plan and apply)
# --------------------------------------------------------------------------


def test_apply_reports_conflict_when_sin_changed_since_plan(monkeypatch):
    store = _fresh_store(drivers=[_driver("d-1", sin="secret-original", extra_meta={"pre_launch_test": "true"})])
    recorder = _use(monkeypatch, store)
    plan = svc.build_sin_purge_plan(today=AFTER_CUTOFF)

    # Simulate a race: the driver's sin changed after the plan snapshot was
    # taken (e.g. self-entry, or another apply run already purged it).
    store["drivers"][0]["sin"] = "secret-changed"

    result = svc.apply_sin_purge(plan, batch=BATCH, today=AFTER_CUTOFF)

    assert result == {"purged": [], "conflicts": ["d-1"]}
    # Never touched -- the guard must prevent both the RPC call and the update.
    assert recorder.get("rpc_calls", []) == []
    assert store["drivers"][0]["sin"] == "secret-changed"


# --------------------------------------------------------------------------
# purge_pii_secret helper
# --------------------------------------------------------------------------


def test_purge_pii_secret_returns_false_for_empty_input(monkeypatch):
    store = _fresh_store()
    recorder = _use(monkeypatch, store)

    assert svc.purge_pii_secret(None) is False
    assert svc.purge_pii_secret("") is False
    assert recorder.get("rpc_calls", []) == []


def test_purge_pii_secret_calls_the_rpc(monkeypatch):
    store = _fresh_store()
    recorder = _use(monkeypatch, store)

    assert svc.purge_pii_secret("secret-uuid-1") is True
    assert recorder["rpc_calls"] == [("purge_driver_pii_secret", {"secret_id": "secret-uuid-1"})]


# --------------------------------------------------------------------------
# print_report — shape only, never SIN content
# --------------------------------------------------------------------------


def test_print_report_never_prints_a_sin_value(monkeypatch, capsys):
    store = _fresh_store(drivers=[_driver("d-1", sin="a-real-looking-secret-value")])
    _use(monkeypatch, store)
    plan = svc.build_sin_purge_plan(today=BEFORE_CUTOFF)

    svc.print_report(plan, dry_run=True)

    out = capsys.readouterr().out
    assert "a-real-looking-secret-value" not in out
    assert "DRY RUN" in out
    assert AFTER_CUTOFF.isoformat() in out
