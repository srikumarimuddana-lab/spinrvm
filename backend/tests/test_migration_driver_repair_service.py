"""Unit tests for backend/services/migration_driver_repair_service.py.

Critical correctness properties locked in here:
- only completed, driver_id-null, old_driver_id-bearing rides are candidates
- a re-matched driver is found via EITHER linkage shape (top-level
  legacy_import_metadata.old_driver_id or nested mongo_driver_history[])
- an old_driver_id claimed by more than one current driver is never guessed
  at -- excluded as ambiguous, not silently linked to either
- apply sets driver_id only when it's still null (race-safe), reconstructs
  Period 2/3 insurance rows, and writes exactly one offsetting payout per
  driver sized to cancel the newly-linked earnings (payable_balance-neutral)
"""

from decimal import Decimal

from backend.services import booking_import_service
from backend.services import migration_driver_repair_service as svc

BATCH = "20260831000000"


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._predicates = []
        self._update_payload = None
        self._insert_rows = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._predicates.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self._predicates.append(("is_", col, val))
        return self

    def in_(self, col, vals):
        self._predicates.append(("in_", col, set(vals)))
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def insert(self, rows):
        self._insert_rows = rows if isinstance(rows, list) else [rows]
        return self

    def _row_matches(self, row) -> bool:
        for pred in self._predicates:
            kind = pred[0]
            if kind == "eq":
                _, col, val = pred
                if row.get(col) != val:
                    return False
            elif kind == "is_":
                _, col, val = pred
                if val == "null" and row.get(col) is not None:
                    return False
            elif kind == "in_":
                _, col, vals = pred
                if row.get(col) not in vals:
                    return False
        return True

    def execute(self):
        if self._insert_rows is not None:
            self.store.setdefault(self.table, []).extend(dict(r) for r in self._insert_rows)
            return _Result([dict(r) for r in self._insert_rows])
        rows = [r for r in self.store.get(self.table, []) if self._row_matches(r)]
        if self._update_payload is not None:
            for r in rows:
                r.update(self._update_payload)
        return _Result([dict(r) for r in rows])


class _RpcResult:
    def execute(self):
        raise RuntimeError("no recount RPC in tests -- fall back to per-driver recount")


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)

    def rpc(self, *_a, **_k):
        return _RpcResult()


def _ride(ride_id, *, old_driver_id="old-d1", driver_earnings=25.0, **fields):
    row = {
        "id": ride_id,
        "status": "completed",
        "driver_id": None,
        "driver_earnings": driver_earnings,
        "driver_arrived_at": "2026-01-01T00:00:00Z",
        "ride_started_at": "2026-01-01T00:05:00Z",
        "ride_completed_at": "2026-01-01T00:30:00Z",
        "legacy_import_metadata": {"old_driver_id": old_driver_id} if old_driver_id else {},
    }
    row.update(fields)
    return row


def _driver(driver_id, *, top_level_old_id=None, mongo_history_old_ids=None, total_rides=0):
    meta: dict = {}
    if top_level_old_id:
        meta["old_driver_id"] = top_level_old_id
    if mongo_history_old_ids:
        meta["mongo_driver_history"] = [{"old_driver_id": oid} for oid in mongo_history_old_ids]
    return {"id": driver_id, "legacy_import_metadata": meta, "total_rides": total_rides}


def _store(rides=None, drivers=None, payouts=None):
    return {"rides": rides or [], "drivers": drivers or [], "payouts": payouts or [], "driver_insurance_periods": []}


def _patch(monkeypatch, store):
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store))
    # recount_drivers/payout_id_for are imported from booking_import_service
    # but recount_drivers reads booking_import_service's OWN module-level
    # `supabase` global (a function's globals follow its defining module,
    # not its importer) -- must patch both. Same reasoning CLAUDE.md's
    # testing conventions call out for db_supabase's re-exports.
    monkeypatch.setattr(booking_import_service, "supabase", _FakeSupabase(store))


class TestBuildPlanMatching:
    def test_matches_via_top_level_old_driver_id(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1")],
            drivers=[_driver("driver-1", top_level_old_id="old-d1")],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        assert plan.stats["repairable"] == 1
        assert plan.candidates[0].driver_id == "driver-1"

    def test_matches_via_nested_mongo_driver_history(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1")],
            drivers=[_driver("driver-1", mongo_history_old_ids=["old-d1"])],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        assert plan.stats["repairable"] == 1
        assert plan.candidates[0].driver_id == "driver-1"

    def test_ride_without_old_driver_id_excluded_entirely(self, monkeypatch):
        store = _store(rides=[_ride("r1", old_driver_id=None)], drivers=[])
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        assert plan.stats["rides_missing_driver_with_old_id"] == 0
        assert plan.candidates == []

    def test_ride_with_driver_already_set_excluded(self, monkeypatch):
        store = _store(rides=[_ride("r1", old_driver_id="old-d1", driver_id="already-set")], drivers=[])
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        assert plan.candidates == []

    def test_no_matching_driver_counts_as_still_unmatched(self, monkeypatch):
        store = _store(rides=[_ride("r1", old_driver_id="old-d1")], drivers=[])
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        assert plan.stats["still_unmatched"] == 1
        assert plan.candidates == []

    def test_ambiguous_old_driver_id_never_guessed(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1")],
            drivers=[
                _driver("driver-1", top_level_old_id="old-d1"),
                _driver("driver-2", top_level_old_id="old-d1"),
            ],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        assert plan.stats["ambiguous_old_driver_id_skipped"] == 1
        assert plan.candidates == []


class TestApplyDriverRepair:
    def test_apply_sets_driver_id(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1")],
            drivers=[_driver("driver-1", top_level_old_id="old-d1")],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        conflicts, recounted = svc.apply_driver_repair(plan, batch=BATCH)
        assert conflicts == []
        assert store["rides"][0]["driver_id"] == "driver-1"
        assert store["rides"][0]["legacy_import_metadata"]["driver_repair"]["batch"] == BATCH

    def test_apply_reconstructs_insurance_periods(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1")],
            drivers=[_driver("driver-1", top_level_old_id="old-d1")],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        svc.apply_driver_repair(plan, batch=BATCH)
        periods = store["driver_insurance_periods"]
        assert len(periods) == 2
        by_period = {p["period"]: p for p in periods}
        assert by_period[2]["started_at"] == "2026-01-01T00:00:00Z"
        assert by_period[2]["ended_at"] == "2026-01-01T00:05:00Z"
        assert by_period[3]["started_at"] == "2026-01-01T00:05:00Z"
        assert by_period[3]["ended_at"] == "2026-01-01T00:30:00Z"
        assert all(p["is_reconstructed"] is True for p in periods)
        assert all(p["driver_id"] == "driver-1" for p in periods)

    def test_apply_skips_insurance_period_missing_a_timestamp(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1", ride_started_at=None)],
            drivers=[_driver("driver-1", top_level_old_id="old-d1")],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        svc.apply_driver_repair(plan, batch=BATCH)
        # Neither period has both endpoints without ride_started_at.
        assert store["driver_insurance_periods"] == []

    def test_apply_writes_offsetting_payout_sized_to_earnings(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1", driver_earnings=25.0)],
            drivers=[_driver("driver-1", top_level_old_id="old-d1")],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        svc.apply_driver_repair(plan, batch=BATCH)
        payouts = store["payouts"]
        assert len(payouts) == 1
        assert payouts[0]["driver_id"] == "driver-1"
        assert Decimal(payouts[0]["amount"]) == Decimal("25.0")
        assert payouts[0]["status"] == "completed"
        assert payouts[0]["payout_type"] == "legacy_import"

    def test_apply_sums_payout_across_multiple_rides_for_same_driver(self, monkeypatch):
        store = _store(
            rides=[
                _ride("r1", old_driver_id="old-d1", driver_earnings=25.0),
                _ride("r2", old_driver_id="old-d1", driver_earnings=15.0),
            ],
            drivers=[_driver("driver-1", top_level_old_id="old-d1")],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        svc.apply_driver_repair(plan, batch=BATCH)
        payouts = store["payouts"]
        assert len(payouts) == 1
        assert Decimal(payouts[0]["amount"]) == Decimal("40.0")

    def test_apply_zero_earnings_ride_writes_no_payout(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1", driver_earnings=0)],
            drivers=[_driver("driver-1", top_level_old_id="old-d1")],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        svc.apply_driver_repair(plan, batch=BATCH)
        assert store["payouts"] == []

    def test_apply_payout_idempotent_on_rerun_same_batch(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1", driver_earnings=25.0)],
            drivers=[_driver("driver-1", top_level_old_id="old-d1")],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        svc.apply_driver_repair(plan, batch=BATCH)
        assert len(store["payouts"]) == 1
        # Re-run apply with the SAME already-applied plan/batch (simulates a
        # retried request) -- driver_id guard means no new candidates would
        # normally exist, but even if apply_driver_repair were invoked again
        # on the same candidates, the payout id is unchanged so it must not
        # duplicate.
        svc.apply_driver_repair(plan, batch=BATCH)
        assert len(store["payouts"]) == 1

    def test_apply_never_overwrites_a_driver_id_set_concurrently(self, monkeypatch):
        store = _store(
            rides=[_ride("r1", old_driver_id="old-d1")],
            drivers=[_driver("driver-1", top_level_old_id="old-d1")],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        # Simulate a concurrent writer beating this apply to the ride.
        store["rides"][0]["driver_id"] = "someone-else"
        conflicts, recounted = svc.apply_driver_repair(plan, batch=BATCH)
        assert conflicts == ["r1"]
        assert store["rides"][0]["driver_id"] == "someone-else"
        assert store["payouts"] == []
        assert store["driver_insurance_periods"] == []

    def test_apply_recounts_total_rides_via_fallback(self, monkeypatch):
        store = _store(
            rides=[
                _ride("r1", old_driver_id="old-d1"),
                {**_ride("r2", old_driver_id=None), "driver_id": "driver-1", "status": "completed"},
            ],
            drivers=[_driver("driver-1", top_level_old_id="old-d1", total_rides=0)],
        )
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        svc.apply_driver_repair(plan, batch=BATCH)
        driver_row = next(d for d in store["drivers"] if d["id"] == "driver-1")
        # r1 (just repaired) + r2 (already completed with this driver) = 2.
        assert driver_row["total_rides"] == 2

    def test_apply_with_no_candidates_is_a_noop(self, monkeypatch):
        store = _store(rides=[], drivers=[])
        _patch(monkeypatch, store)
        plan = svc.build_driver_repair_plan()
        conflicts, recounted = svc.apply_driver_repair(plan, batch=BATCH)
        assert conflicts == []
        assert recounted == 0
