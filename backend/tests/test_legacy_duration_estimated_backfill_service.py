"""Tests for booking_import_service.py's historical duration_estimated
backfill (docs/change-log/2026-08-19-legacy-migration-transparency-backend.md
§3's deferred follow-up).

Companion to test_legacy_sin_dob_import_service.py — mirrors its fake-
supabase harness (this module reads `svc.supabase` directly, not through
`db_supabase`/`repositories`, so the shared `mock_supabase_client` conftest
fixture does not intercept it; a local fake is the established pattern here).

Covers: the same estimation-detection condition build_plan() uses at import
time, the already-legacy-only scan scope, never-clobber semantics for a row
already carrying the duration_estimated key (from the importer itself or a
prior run of this backfill), idempotency across repeated dry runs, and the
apply path's write-time guard against a concurrent double-stamp.
"""

from __future__ import annotations

import copy
import json

from backend.services import booking_import_service as svc

IMPORT_SOURCE = svc.IMPORT_SOURCE
MARKER = svc.DURATION_ESTIMATED_BACKFILL_MARKER


def _legacy_ride(**overrides):
    """An already-imported legacy ride with a real measured duration
    (ride_started_at present) and no duration_estimated marker yet."""
    row = {
        "id": "ride-1",
        "ride_started_at": "2026-07-01T12:00:00+00:00",
        "legacy_import_metadata": {
            "source": IMPORT_SOURCE,
            "old_booking_id": "CB1234567",
        },
    }
    row.update(overrides)
    return row


# ── fake supabase (table only — this module never touches storage/rpc) ────


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store, supa=None):
        self.table = table
        self.store = store
        self.supa = supa
        self._filters = []
        self._update = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def filter(self, col, op, val):
        self._filters.append((op, col, val))
        return self

    def update(self, fields):
        self._update = fields
        return self

    def _matched(self):
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if "->>" in col:
                base, key = col.split("->>", 1)
                if op == "eq":
                    rows = [r for r in rows if (r.get(base) or {}).get(key) == val]
                elif op == "is":
                    # PostgREST's ...->>key=is.null — true when the key is
                    # absent (or literally None), which is all this module
                    # ever sends.
                    assert val == "null"
                    rows = [r for r in rows if (r.get(base) or {}).get(key) is None]
                else:  # pragma: no cover - defensive, no other op used here
                    raise AssertionError(f"unhandled JSON-path filter op: {op}")
            elif op == "eq" and isinstance(val, str) and val.lstrip()[:1] in "{[":
                # Whole-column JSONB equality guard (the concurrent-writer
                # hardening in apply_duration_estimated_backfill passes a
                # json.dumps()'d snapshot as `val`, matching how PostgREST's
                # `eq.<json>` filter is built for real). Compare by parsed
                # value, not string equality, mirroring Postgres jsonb `=`
                # (order-independent) rather than a literal text match.
                target = json.loads(val)
                rows = [r for r in rows if (r.get(col) or {}) == target]
            elif op == "eq":
                rows = [r for r in rows if r.get(col) == val]
        return rows

    def execute(self):
        if self._update is not None:
            matched = self._matched()
            for row in matched:
                row.update(self._update)
            return _FakeExecute(matched)
        # A real PostgREST SELECT returns a materialized JSON snapshot, not a
        # live reference into server-side state — deep-copy so a later
        # mutation of the fake store (e.g. the on_next_select hook below)
        # can't retroactively change data the caller already "received over
        # the wire".
        result = _FakeExecute(copy.deepcopy(self._matched()))
        # One-shot hook: fires right after a plain SELECT's execute() returns,
        # so a test can simulate another process's write landing in the real
        # race window this fix protects — between apply()'s own read of a
        # row and its own write, not before either happens.
        if self.supa is not None and self.supa.on_next_select is not None:
            callback = self.supa.on_next_select
            self.supa.on_next_select = None
            callback()
        return result


class _FakeSupabase:
    def __init__(self, rides=None):
        self.store = {"rides": rides if rides is not None else []}
        # One-shot callback a test can set to simulate a concurrent writer's
        # update landing right after the next plain SELECT executes. See
        # _FakeQuery.execute().
        self.on_next_select = None

    def table(self, name):
        return _FakeQuery(name, self.store, self)


def _install(monkeypatch, rides=None):
    fake = _FakeSupabase(rides=rides)
    monkeypatch.setattr(svc, "supabase", fake)
    return fake


# ── plan_duration_estimated_backfill ───────────────────────────────────


def test_plan_marks_estimated_when_no_start_timestamp(monkeypatch):
    ride = _legacy_ride(ride_started_at=None)
    _install(monkeypatch, rides=[ride])

    plan = svc.plan_duration_estimated_backfill()

    assert plan.total_legacy_rides_scanned == 1
    assert len(plan.updates) == 1
    upd = plan.updates[0]
    assert upd.id == "ride-1"
    assert upd.old_booking_id == "CB1234567"
    assert upd.duration_estimated is True
    assert not plan.errors


def test_plan_marks_not_estimated_when_start_timestamp_present(monkeypatch):
    ride = _legacy_ride(ride_started_at="2026-07-01T12:00:00+00:00")
    _install(monkeypatch, rides=[ride])

    plan = svc.plan_duration_estimated_backfill()

    assert len(plan.updates) == 1
    assert plan.updates[0].duration_estimated is False


def test_plan_skips_ride_already_carrying_duration_estimated_key(monkeypatch):
    """A row the importer itself already stamped (post-fix import) must not
    be re-planned."""
    ride = _legacy_ride(ride_started_at=None)
    ride["legacy_import_metadata"]["duration_estimated"] = True
    _install(monkeypatch, rides=[ride])

    plan = svc.plan_duration_estimated_backfill()

    assert plan.updates == []
    assert plan.skipped_already_marked == 1
    assert plan.total_legacy_rides_scanned == 1


def test_plan_skips_ride_already_carrying_this_backfills_own_marker(monkeypatch):
    """A row stamped by a previous run of THIS script (marker present, e.g.
    duration_estimated somehow absent but the batch marker is there) is also
    skipped — the never-clobber guarantee is not solely keyed on the
    importer's own field name."""
    ride = _legacy_ride(ride_started_at=None)
    ride["legacy_import_metadata"][MARKER] = {"batch": "prior-run", "backfilled_at": "2026-08-01T00:00:00+00:00"}
    _install(monkeypatch, rides=[ride])

    plan = svc.plan_duration_estimated_backfill()

    assert plan.updates == []
    assert plan.skipped_already_marked == 1


def test_plan_only_scans_this_importers_legacy_rides(monkeypatch):
    """A non-legacy (organic) ride is never fetched in the first place — the
    fetch itself filters on legacy_import_metadata->>source, mirroring
    _fetch_already_imported()'s own filter."""
    organic_ride = {"id": "ride-organic", "ride_started_at": None, "legacy_import_metadata": {}}
    legacy_ride = _legacy_ride(ride_started_at=None)
    _install(monkeypatch, rides=[organic_ride, legacy_ride])

    plan = svc.plan_duration_estimated_backfill()

    assert plan.total_legacy_rides_scanned == 1
    assert len(plan.updates) == 1
    assert plan.updates[0].id == "ride-1"


def test_plan_is_idempotent_across_repeated_dry_runs(monkeypatch):
    ride = _legacy_ride(ride_started_at=None)
    _install(monkeypatch, rides=[ride])

    plan1 = svc.plan_duration_estimated_backfill()
    plan2 = svc.plan_duration_estimated_backfill()

    assert len(plan1.updates) == 1
    assert len(plan2.updates) == 1
    assert plan1.updates[0].duration_estimated == plan2.updates[0].duration_estimated


def test_plan_reports_error_for_row_missing_id(monkeypatch):
    ride = _legacy_ride(id=None)
    _install(monkeypatch, rides=[ride])

    plan = svc.plan_duration_estimated_backfill()

    assert plan.updates == []
    assert len(plan.errors) == 1


# ── apply_duration_estimated_backfill ───────────────────────────────────


def test_apply_stamps_marker_without_touching_duration_minutes(monkeypatch):
    ride = _legacy_ride(ride_started_at=None, duration_minutes=45)
    fake = _install(monkeypatch, rides=[ride])
    plan = svc.plan_duration_estimated_backfill()

    conflicts = svc.apply_duration_estimated_backfill(plan, batch="test-batch-1")
    assert conflicts == []

    updated = fake.store["rides"][0]
    assert updated["legacy_import_metadata"]["duration_estimated"] is True
    marker = updated["legacy_import_metadata"][MARKER]
    assert marker["batch"] == "test-batch-1"
    assert "backfilled_at" in marker
    # original metadata key survives
    assert updated["legacy_import_metadata"]["source"] == IMPORT_SOURCE
    assert updated["legacy_import_metadata"]["old_booking_id"] == "CB1234567"
    # duration_minutes is never written by this backfill
    assert updated["duration_minutes"] == 45


def test_apply_reports_conflict_and_does_not_clobber_a_concurrent_stamp(monkeypatch):
    """A row stamped by a concurrent run of this same script (or by the
    importer itself re-importing) between plan() and apply() must win — the
    write-time re-read + IS NULL guard, not just the plan-time snapshot, is
    what enforces this."""
    ride = _legacy_ride(ride_started_at=None)
    fake = _install(monkeypatch, rides=[ride])
    plan = svc.plan_duration_estimated_backfill()

    # Simulate the race: another process stamps this row after planning,
    # before this run's apply loop reaches it.
    ride["legacy_import_metadata"] = dict(ride["legacy_import_metadata"])
    ride["legacy_import_metadata"]["duration_estimated"] = False
    ride["legacy_import_metadata"][MARKER] = {"batch": "other-run", "backfilled_at": "2026-08-01T00:00:00+00:00"}

    conflicts = svc.apply_duration_estimated_backfill(plan, batch="test-batch-race")

    assert conflicts == ["ride-1"]
    # the concurrent stamp survives untouched
    assert fake.store["rides"][0]["legacy_import_metadata"]["duration_estimated"] is False
    assert fake.store["rides"][0]["legacy_import_metadata"][MARKER]["batch"] == "other-run"


def test_apply_does_not_clobber_an_unrelated_key_added_by_a_concurrent_writer(monkeypatch):
    """Simulates the concurrent-writer risk with legacy_gst_backfill_service.py
    (docs/change-log/2026-08-19-legacy-backfill-concurrent-writer-fix.md):
    some OTHER script read-merge-writes a different key
    (`old_payout_gst_amount`) into the same row's legacy_import_metadata
    between our plan() and apply(), without ever touching
    `duration_estimated`. The `duration_estimated IS NULL` guard alone would
    still pass here (that key is untouched) and, before this fix, would have
    written back this function's stale `meta` snapshot — silently dropping
    the concurrently-added key. The whole-column equality guard must instead
    treat this as a conflict and leave the concurrent write intact."""
    ride = _legacy_ride(ride_started_at=None)
    fake = _install(monkeypatch, rides=[ride])
    plan = svc.plan_duration_estimated_backfill()

    # Simulate the race landing in the actual protected window: right after
    # apply()'s own read of this row (which still sees no
    # old_payout_gst_amount) but before its write, a different backfill
    # (e.g. legacy_gst_backfill_service.py, once it grows a commit path)
    # writes an unrelated key to the same row.
    def _concurrent_write():
        ride["legacy_import_metadata"] = dict(ride["legacy_import_metadata"])
        ride["legacy_import_metadata"]["old_payout_gst_amount"] = "1.23"

    fake.on_next_select = _concurrent_write

    conflicts = svc.apply_duration_estimated_backfill(plan, batch="test-batch-gst-race")

    assert conflicts == ["ride-1"]
    meta = fake.store["rides"][0]["legacy_import_metadata"]
    # the concurrently-written key survives untouched
    assert meta["old_payout_gst_amount"] == "1.23"
    # and this backfill did NOT get to stamp its own key over the stale read
    assert "duration_estimated" not in meta
    assert MARKER not in meta


def test_apply_refuses_when_plan_has_errors(monkeypatch):
    fake = _install(monkeypatch, rides=[_legacy_ride()])

    try:
        svc.apply_duration_estimated_backfill(svc.DurationEstimatedBackfillPlan(errors=["boom"]), batch="test-batch-2")
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    # no write attempted
    assert "duration_estimated" not in fake.store["rides"][0]["legacy_import_metadata"]


def test_apply_is_a_noop_with_no_updates(monkeypatch):
    fake = _install(monkeypatch, rides=[])
    conflicts = svc.apply_duration_estimated_backfill(svc.DurationEstimatedBackfillPlan(), batch="test-batch-3")
    assert conflicts == []
    assert fake.store["rides"] == []


def test_apply_is_idempotent_on_rerun_after_success(monkeypatch):
    """Re-running apply (e.g. operator error, re-invoking the CLI) after a
    clean apply must not re-plan or re-touch an already-stamped row."""
    ride = _legacy_ride(ride_started_at=None)
    _install(monkeypatch, rides=[ride])
    plan = svc.plan_duration_estimated_backfill()
    svc.apply_duration_estimated_backfill(plan, batch="test-batch-4")

    replan = svc.plan_duration_estimated_backfill()
    assert replan.updates == []
    assert replan.skipped_already_marked == 1
