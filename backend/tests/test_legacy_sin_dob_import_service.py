"""Tests for driver_import_service.py's legacy SIN/DOB backfill (banks.csv).

Companion to test_driver_import_service_coverage.py — mirrors its fake-
supabase harness (this module reads `svc.supabase` directly, not through
`db_supabase`/`repositories`, so the shared `mock_supabase_client` conftest
fixture does not intercept it; a local fake is the established pattern here).

Covers: the banks.csv -> drivers.csv -> phone crosswalk, the legacy-driver-
only safety gate, never-clobber semantics for both sin and date_of_birth
independently, SIN validation, duplicate-phone-in-batch handling, and the
apply path's encrypt-RPC + legacy_import_metadata-merge behavior.
"""

from __future__ import annotations

from backend.services import driver_import_service as svc

IMPORT_SOURCE = svc.IMPORT_SOURCE

VALID_SIN = "130692544"  # passes Luhn; not a real person's number


def _bank_row(**overrides):
    row = {
        "_id": "bank-1",
        "driver_id": "mongo-driver-1",
        "sin": VALID_SIN,
        "date_of_birth": "1992-08-03T00:00:00.000",
        "account_number": "5102421",
        "transit_number": "07418",
        "institute_number": "003",
    }
    row.update(overrides)
    return row


def _mongo_driver_row(**overrides):
    row = {"_id": "mongo-driver-1", "phone": "3065551234"}
    row.update(overrides)
    return row


def _spinr_driver(**overrides):
    row = {
        "id": "spinr-driver-1",
        "phone": "+13065551234",
        "sin": None,
        "date_of_birth": None,
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "42"},
    }
    row.update(overrides)
    return row


# ── fake supabase (table + rpc only — this module never touches storage) ──


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._update = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    def update(self, fields):
        self._update = fields
        return self

    def _matched(self):
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "in":
                allowed = set(val)
                rows = [r for r in rows if r.get(col) in allowed]
            elif op == "is":
                # PostgREST's .is_(col, "null") — the fake only needs the
                # null case, which is all this module ever sends.
                rows = [r for r in rows if r.get(col) is None]
        return rows

    def execute(self):
        if self._update is not None:
            matched = self._matched()
            for row in matched:
                row.update(self._update)
            return _FakeExecute(matched)
        return _FakeExecute(self._matched())


class _FakeRpc:
    def __init__(self, name, params, recorder):
        self.name = name
        self.params = params
        self.recorder = recorder

    def execute(self):
        self.recorder.setdefault("rpc_calls", []).append((self.name, self.params))
        return _FakeExecute(f"enc::{self.params.get('plaintext')}")


class _FakeSupabase:
    def __init__(self, drivers=None):
        self.store = {"drivers": drivers if drivers is not None else []}
        self.recorder: dict = {}

    def table(self, name):
        return _FakeQuery(name, self.store)

    def rpc(self, name, params):
        return _FakeRpc(name, params, self.recorder)


def _install(monkeypatch, drivers=None):
    fake = _FakeSupabase(drivers=drivers)
    monkeypatch.setattr(svc, "supabase", fake)
    return fake


# ── join_legacy_bank_sin_dob ───────────────────────────────────────────


def test_join_resolves_phone_via_mongo_driver_id():
    joined = svc.join_legacy_bank_sin_dob([_bank_row()], [_mongo_driver_row()])
    assert joined[0]["phone"] == "+13065551234"
    assert joined[0]["old_driver_id"] == "mongo-driver-1"
    assert joined[0]["sin_raw"] == VALID_SIN


def test_join_unmatched_driver_id_yields_none_phone():
    joined = svc.join_legacy_bank_sin_dob([_bank_row(driver_id="no-such-mongo-id")], [_mongo_driver_row()])
    assert joined[0]["phone"] is None


# ── plan_legacy_sin_dob_import ─────────────────────────────────────────


def test_plan_matches_legacy_driver_and_stages_sin_and_dob(monkeypatch):
    _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.plan_legacy_sin_dob_import([_bank_row()], [_mongo_driver_row()])

    assert len(plan.updates) == 1
    upd = plan.updates[0]
    assert upd["id"] == "spinr-driver-1"
    assert upd["_plain_sin"] == VALID_SIN
    assert upd["date_of_birth"] == "1992-08-03"
    assert not plan.warnings
    assert not plan.errors


def test_plan_skips_unmatched_phone(monkeypatch):
    _install(monkeypatch, drivers=[])  # no Spinr driver with that phone
    plan = svc.plan_legacy_sin_dob_import([_bank_row()], [_mongo_driver_row()])

    assert plan.updates == []
    assert plan.skipped_unmatched == 1
    assert plan.warnings[0].field == "phone"
    # never leaks the phone value itself
    assert "3065551234" not in plan.warnings[0].message


def test_plan_skips_unresolvable_driver_id(monkeypatch):
    _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.plan_legacy_sin_dob_import([_bank_row(driver_id="ghost")], [_mongo_driver_row()])

    assert plan.updates == []
    assert plan.skipped_unmatched == 1


def test_plan_skips_driver_without_legacy_import_source(monkeypatch):
    organic_driver = _spinr_driver(legacy_import_metadata={})
    _install(monkeypatch, drivers=[organic_driver])
    plan = svc.plan_legacy_sin_dob_import([_bank_row()], [_mongo_driver_row()])

    assert plan.updates == []
    assert plan.skipped_not_legacy_driver == 1


def test_plan_never_clobbers_existing_sin_or_dob(monkeypatch):
    already_set = _spinr_driver(sin="vault-uuid-existing", date_of_birth="1990-01-01")
    _install(monkeypatch, drivers=[already_set])
    plan = svc.plan_legacy_sin_dob_import([_bank_row()], [_mongo_driver_row()])

    assert plan.updates == []  # nothing new to write
    assert plan.skipped_already_on_file == 1


def test_plan_backfills_only_the_missing_half(monkeypatch):
    # sin already on file, dob missing -> update should carry dob only
    partial = _spinr_driver(sin="vault-uuid-existing", date_of_birth=None)
    _install(monkeypatch, drivers=[partial])
    plan = svc.plan_legacy_sin_dob_import([_bank_row()], [_mongo_driver_row()])

    assert len(plan.updates) == 1
    assert "_plain_sin" not in plan.updates[0]
    assert plan.updates[0]["date_of_birth"] == "1992-08-03"
    assert plan.skipped_already_on_file == 1


def test_plan_invalid_sin_warns_and_does_not_stage(monkeypatch):
    _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.plan_legacy_sin_dob_import([_bank_row(sin="000000001")], [_mongo_driver_row()])

    assert len(plan.updates) == 1
    assert "_plain_sin" not in plan.updates[0]  # dob still staged
    assert any(w.field == "sin" for w in plan.warnings)


def test_plan_unparseable_dob_warns_and_does_not_stage(monkeypatch):
    _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.plan_legacy_sin_dob_import([_bank_row(date_of_birth="not-a-date")], [_mongo_driver_row()])

    assert "date_of_birth" not in plan.updates[0]
    assert any(w.field == "date_of_birth" for w in plan.warnings)


def test_plan_duplicate_phone_in_batch_first_row_wins(monkeypatch):
    _install(monkeypatch, drivers=[_spinr_driver()])
    dup_bank_row = _bank_row(_id="bank-2")  # same driver_id -> same phone
    plan = svc.plan_legacy_sin_dob_import([_bank_row(), dup_bank_row], [_mongo_driver_row()])

    assert len(plan.updates) == 1
    assert plan.skipped_duplicate_match == 1


# ── apply_legacy_sin_dob_import ────────────────────────────────────────


def test_apply_encrypts_sin_and_merges_metadata_without_clobbering(monkeypatch):
    driver = _spinr_driver()
    fake = _install(monkeypatch, drivers=[driver])
    plan = svc.plan_legacy_sin_dob_import([_bank_row()], [_mongo_driver_row()])

    conflicts = svc.apply_legacy_sin_dob_import(plan, batch="test-batch-1")
    assert conflicts == []

    assert fake.recorder["rpc_calls"][0][0] == "encrypt_driver_pii"
    assert fake.recorder["rpc_calls"][0][1] == {"plaintext": VALID_SIN}

    updated = fake.store["drivers"][0]
    assert updated["sin"] == f"enc::{VALID_SIN}"
    assert updated["sin_last4"] == VALID_SIN[-4:]
    assert updated["date_of_birth"] == "1992-08-03"
    # original metadata key survives; new key is added alongside it
    assert updated["legacy_import_metadata"]["source"] == IMPORT_SOURCE
    assert updated["legacy_import_metadata"]["old_driver_id"] == "42"
    marker = updated["legacy_import_metadata"][svc.LEGACY_BANK_SIN_DOB_SOURCE]
    assert marker["batch"] == "test-batch-1"
    assert marker["sin_written"] is True
    assert marker["dob_written"] is True


def test_apply_reports_conflict_and_does_not_clobber_a_concurrent_self_entry(monkeypatch):
    """A driver who self-enters their SIN via routes/drivers/profile.py
    between plan() and apply() must win — the write-time .is_(col, "null")
    guard, not just the plan-time snapshot, is what enforces this.

    The guard covers the whole row's update in one query (both sin's and
    date_of_birth's .is_(col, "null") checks are ANDed together when both
    are being written), so a sin conflict skips this row's date_of_birth
    write too this pass — safe, and self-healing: a re-plan next run sees
    sin now on file (skips it) and date_of_birth still null (stages it
    alone, no sin guard attached), so nothing is lost, just deferred one run."""
    driver = _spinr_driver()
    fake = _install(monkeypatch, drivers=[driver])
    plan = svc.plan_legacy_sin_dob_import([_bank_row()], [_mongo_driver_row()])

    # Simulate the race: the driver's own SIN entry lands after planning,
    # before this batch's apply loop reaches their row.
    driver["sin"] = "vault-uuid-self-entered"

    conflicts = svc.apply_legacy_sin_dob_import(plan, batch="test-batch-race")

    # old_driver_id here is banks.csv's driver_id (this module's own
    # old_driver_id convention throughout), not the Spinr driver's
    # legacy_import_metadata.old_driver_id (the numeric Saskatoon id) —
    # two different "old id" namespaces, see the crosswalk in join_legacy_bank_sin_dob.
    assert conflicts == ["mongo-driver-1"]
    # self-entered value survives untouched; nothing else on the row changed
    assert fake.store["drivers"][0]["sin"] == "vault-uuid-self-entered"
    assert fake.store["drivers"][0]["date_of_birth"] is None


def test_apply_refuses_when_plan_has_errors(monkeypatch):
    fake = _install(monkeypatch, drivers=[_spinr_driver()])
    plan = svc.SinDobImportPlan(errors=[svc.ImportErrorItem("x", "sin", "boom")])

    try:
        svc.apply_legacy_sin_dob_import(plan, batch="test-batch-2")
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    assert "rpc_calls" not in fake.recorder


def test_apply_is_a_noop_with_no_updates(monkeypatch):
    fake = _install(monkeypatch, drivers=[])
    svc.apply_legacy_sin_dob_import(svc.SinDobImportPlan(), batch="test-batch-3")
    assert fake.recorder == {}


def test_apply_dob_only_backfill_does_not_mark_sin_written(monkeypatch):
    """A driver whose SIN is already self-entered but whose DOB is missing
    gets a DOB-only backfill — the marker must record sin_written=False so
    sin_source() (below) never mislabels their self-entered SIN as
    legacy-imported."""
    driver = _spinr_driver(sin="vault-uuid-self-entered", date_of_birth=None)
    fake = _install(monkeypatch, drivers=[driver])
    plan = svc.plan_legacy_sin_dob_import([_bank_row()], [_mongo_driver_row()])

    conflicts = svc.apply_legacy_sin_dob_import(plan, batch="test-batch-dob-only")
    assert conflicts == []

    updated = fake.store["drivers"][0]
    assert updated["sin"] == "vault-uuid-self-entered"  # untouched
    assert updated["date_of_birth"] == "1992-08-03"
    marker = updated["legacy_import_metadata"][svc.LEGACY_BANK_SIN_DOB_SOURCE]
    assert marker["sin_written"] is False
    assert marker["dob_written"] is True


# ── print_sin_dob_report ────────────────────────────────────────────────


def test_print_sin_dob_report_smoke(capsys):
    plan = svc.SinDobImportPlan(
        updates=[{"id": "d1", "old_driver_id": "42"}],
        warnings=[svc.ImportErrorItem("43", "phone", "no Spinr driver with this phone number")],
    )
    svc.print_sin_dob_report(plan, dry_run=True)
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "drivers to update: 1" in out


# ── sin_source (finding #2, docs/audit/2026-08-19-legacy-migration-data-quality-audit.md) ──


def test_sin_source_none_driver_or_no_sin():
    assert svc.sin_source(None) is None
    assert svc.sin_source({}) is None
    assert svc.sin_source({"sin_collected_at": None, "legacy_import_metadata": {}}) is None


def test_sin_source_self_entry_when_no_legacy_marker():
    driver = {"sin_collected_at": "2026-08-01T00:00:00Z", "legacy_import_metadata": {}}
    assert svc.sin_source(driver) == "self_entry"


def test_sin_source_legacy_import_when_marker_has_sin_written():
    driver = {
        "sin_collected_at": "2026-08-19T00:00:00Z",  # same-shaped stamp as self-entry
        "legacy_import_metadata": {
            svc.LEGACY_BANK_SIN_DOB_SOURCE: {"batch": "b1", "imported_at": "2026-08-19T00:00:00Z", "sin_written": True}
        },
    }
    assert svc.sin_source(driver) == "legacy_import"


def test_sin_source_dob_only_backfill_stays_self_entry():
    """Regression for the exact mislabeling bug this fix must avoid: a
    legacy_import_metadata marker exists (a DOB-only batch touched this
    driver) but sin_written is False, because the SIN itself was
    self-entered — must classify as self_entry, not legacy_import."""
    driver = {
        "sin_collected_at": "2026-01-01T00:00:00Z",  # self-entry, predates the DOB backfill
        "legacy_import_metadata": {
            svc.LEGACY_BANK_SIN_DOB_SOURCE: {
                "batch": "dob-only-batch",
                "imported_at": "2026-08-19T00:00:00Z",
                "sin_written": False,
                "dob_written": True,
            }
        },
    }
    assert svc.sin_source(driver) == "self_entry"


def test_sin_source_no_sin_collected_at_and_no_legacy_marker_is_none():
    driver = {"sin_collected_at": None, "legacy_import_metadata": {"source": IMPORT_SOURCE}}
    assert svc.sin_source(driver) is None


# ── dob_source (Oct 30 checklist item #7, docs/runbooks/legacy-migration-playbook.md) ──


def test_dob_source_none_driver_or_no_dob():
    assert svc.dob_source(None) is None
    assert svc.dob_source({}) is None
    assert svc.dob_source({"date_of_birth": None, "legacy_import_metadata": {}}) is None


def test_dob_source_legacy_import_when_original_csv_import_wrote_it():
    """The common case: DOB was set at initial driver creation by build_plan()
    (the Saskatoon CSV import) and never touched by the later banks.csv
    backfill, so there is no dob_written marker at all — must still classify
    as legacy_import via the source==IMPORT_SOURCE check, not self_entry."""
    driver = {
        "date_of_birth": "1990-01-01",
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_driver_id": "42"},
    }
    assert svc.dob_source(driver) == "legacy_import"


def test_dob_source_legacy_import_when_banks_csv_backfill_marker_present():
    driver = {
        "date_of_birth": "1990-01-01",
        "legacy_import_metadata": {
            svc.LEGACY_BANK_SIN_DOB_SOURCE: {"batch": "b1", "imported_at": "2026-08-19T00:00:00Z", "dob_written": True}
        },
    }
    assert svc.dob_source(driver) == "legacy_import"


def test_dob_source_self_entry_when_no_legacy_marker_and_not_csv_import():
    """DOB set on a driver with no legacy_import_metadata.source==IMPORT_SOURCE
    and no banks.csv marker — currently only reachable via an admin
    correction (no driver-facing route writes date_of_birth), but the
    contract stays 'self_entry' to match sin_source()'s three-value shape."""
    driver = {"date_of_birth": "1990-01-01", "legacy_import_metadata": {}}
    assert svc.dob_source(driver) == "self_entry"


def test_dob_source_sin_only_backfill_stays_legacy_import_via_csv_source():
    """Regression mirroring test_sin_source_dob_only_backfill_stays_self_entry,
    but for the opposite direction: a banks.csv batch that only backfilled
    SIN (dob_written False, because DOB was already on file from the
    original CSV import) must NOT be reported as self_entry — the CSV-import
    marker still correctly classifies it as legacy_import."""
    driver = {
        "date_of_birth": "1990-01-01",
        "legacy_import_metadata": {
            "source": IMPORT_SOURCE,
            "old_driver_id": "42",
            svc.LEGACY_BANK_SIN_DOB_SOURCE: {
                "batch": "sin-only-batch",
                "imported_at": "2026-08-19T00:00:00Z",
                "sin_written": True,
                "dob_written": False,
            },
        },
    }
    assert svc.dob_source(driver) == "legacy_import"


def test_dob_source_non_dict_legacy_import_metadata_is_self_entry():
    """Defensive guard: a truthy, non-dict legacy_import_metadata value (bad
    data) must not raise — falls through to self_entry rather than crashing
    the admin driver-detail read path."""
    driver = {"date_of_birth": "1990-01-01", "legacy_import_metadata": "corrupt"}
    assert svc.dob_source(driver) == "self_entry"
