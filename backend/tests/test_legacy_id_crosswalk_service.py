"""Unit tests for backend/services/legacy_id_crosswalk_service.py.

Critical correctness properties locked in here:
- a Saskatoon-sourced driver contributes old_numeric_driver_id only
- a Mongo-sourced (top-level or mongo_driver_history) driver contributes
  old_mongo_object_id only
- an enriched driver (both linkage shapes at once) contributes BOTH ids in
  one candidate
- an old id already present in legacy_id_crosswalk is never re-proposed
  (idempotent re-run)
- a rider whose own rides disagree on old_customer_id is excluded as
  ambiguous, never guessed
- apply writes one row per candidate and never lets one failed insert take
  down the rest of the batch
"""

from __future__ import annotations

from backend.services import legacy_id_crosswalk_service as svc

BATCH = "20260908000000"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._predicates = []
        self._insert_rows = None

    def select(self, *_a, **_k):
        return self

    def filter(self, col, op, val):
        self._predicates.append(("filter", col, op, val))
        return self

    def insert(self, rows):
        self._insert_rows = rows if isinstance(rows, list) else [rows]
        return self

    def _get_path(self, row: dict, col: str):
        if "->" not in col:
            return row.get(col)
        parts = col.replace("->>", "->").split("->")
        value = row.get(parts[0]) or {}
        for key in parts[1:]:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    def _row_matches(self, row) -> bool:
        for kind, col, op, val in self._predicates:
            actual = self._get_path(row, col)
            if op == "not.is" and val == "null":
                if actual is None:
                    return False
            else:  # pragma: no cover - no other operator used by this service
                raise NotImplementedError(op)
        return True

    def execute(self):
        if self._insert_rows is not None:
            if self.store.get("__raise_on_insert__") == self.table:
                raise RuntimeError("simulated insert failure")
            self.store.setdefault(self.table, []).extend(dict(r) for r in self._insert_rows)
            return _Result([dict(r) for r in self._insert_rows])
        rows = [r for r in self.store.get(self.table, []) if self._row_matches(r)]
        return _Result([dict(r) for r in rows])


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _store(**tables):
    base = {"drivers": [], "rides": [], "legacy_id_crosswalk": []}
    base.update(tables)
    return base


def _use(monkeypatch, store):
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store))


def _driver(driver_id, *, source=None, top_old_id=None, mongo_history=None):
    meta: dict = {}
    if source:
        meta["source"] = source
    if top_old_id:
        meta["old_driver_id"] = top_old_id
    if mongo_history:
        meta["mongo_driver_history"] = [{"old_driver_id": oid} for oid in mongo_history]
    return {"id": driver_id, "legacy_import_metadata": meta}


def _ride(rider_id, old_customer_id):
    return {"rider_id": rider_id, "legacy_import_metadata": {"old_customer_id": old_customer_id}}


class TestBuildDriverCrosswalkPlan:
    def test_saskatoon_driver_gets_numeric_id_only(self, monkeypatch):
        store = _store(drivers=[_driver("d1", source=svc.IMPORT_SOURCE, top_old_id="1234567890")])
        _use(monkeypatch, store)
        plan = svc.build_driver_crosswalk_plan()
        assert len(plan.candidates) == 1
        c = plan.candidates[0]
        assert c.entity_type == "driver"
        assert c.spinr_user_id == "d1"
        assert c.old_numeric_driver_id == "1234567890"
        assert c.old_mongo_object_id is None

    def test_mongo_created_driver_gets_mongo_id_only(self, monkeypatch):
        store = _store(drivers=[_driver("d1", source=svc.MONGO_IMPORT_SOURCE, top_old_id="507f1f77bcf86cd799439011")])
        _use(monkeypatch, store)
        plan = svc.build_driver_crosswalk_plan()
        c = plan.candidates[0]
        assert c.old_mongo_object_id == "507f1f77bcf86cd799439011"
        assert c.old_numeric_driver_id is None

    def test_enriched_driver_gets_both_ids_in_one_candidate(self, monkeypatch):
        """A Saskatoon-created driver later enriched from the Mongo export
        carries both linkage shapes at once -- one crosswalk row, both
        columns populated, not two separate rows."""
        store = _store(
            drivers=[
                _driver(
                    "d1",
                    source=svc.IMPORT_SOURCE,
                    top_old_id="1234567890",
                    mongo_history=["507f1f77bcf86cd799439011"],
                )
            ]
        )
        _use(monkeypatch, store)
        plan = svc.build_driver_crosswalk_plan()
        assert len(plan.candidates) == 1
        c = plan.candidates[0]
        assert c.old_numeric_driver_id == "1234567890"
        assert c.old_mongo_object_id == "507f1f77bcf86cd799439011"

    def test_multiple_mongo_history_entries_uses_most_recent(self, monkeypatch):
        store = _store(drivers=[_driver("d1", mongo_history=["old-1", "old-2"])])
        _use(monkeypatch, store)
        plan = svc.build_driver_crosswalk_plan()
        assert plan.candidates[0].old_mongo_object_id == "old-2"

    def test_organic_driver_with_no_legacy_metadata_is_excluded(self, monkeypatch):
        store = _store(drivers=[_driver("d1")])
        _use(monkeypatch, store)
        plan = svc.build_driver_crosswalk_plan()
        assert plan.candidates == []
        assert plan.stats["eligible_drivers_found"] == 0

    def test_already_recorded_old_id_is_not_re_proposed(self, monkeypatch):
        store = _store(
            drivers=[_driver("d1", source=svc.IMPORT_SOURCE, top_old_id="1234567890")],
            legacy_id_crosswalk=[{"old_mongo_object_id": None, "old_numeric_driver_id": "1234567890"}],
        )
        _use(monkeypatch, store)
        plan = svc.build_driver_crosswalk_plan()
        assert plan.candidates == []
        assert plan.stats["already_recorded"] == 1
        assert plan.stats["new_rows_to_write"] == 0

    def test_re_run_only_fills_in_the_missing_half_of_an_enriched_driver(self, monkeypatch):
        """The numeric id was already recorded by an earlier run; the mongo
        id (added later via enrichment) is new -- the candidate should only
        carry the new half."""
        store = _store(
            drivers=[
                _driver(
                    "d1", source=svc.IMPORT_SOURCE, top_old_id="1234567890", mongo_history=["507f1f77bcf86cd799439011"]
                )
            ],
            legacy_id_crosswalk=[{"old_mongo_object_id": None, "old_numeric_driver_id": "1234567890"}],
        )
        _use(monkeypatch, store)
        plan = svc.build_driver_crosswalk_plan()
        assert len(plan.candidates) == 1
        c = plan.candidates[0]
        assert c.old_numeric_driver_id is None
        assert c.old_mongo_object_id == "507f1f77bcf86cd799439011"


class TestBuildRiderCrosswalkPlan:
    def test_consistent_old_customer_id_across_rides_is_one_candidate(self, monkeypatch):
        store = _store(rides=[_ride("u1", "cust-1"), _ride("u1", "cust-1")])
        _use(monkeypatch, store)
        plan = svc.build_rider_crosswalk_plan()
        assert len(plan.candidates) == 1
        c = plan.candidates[0]
        assert c.entity_type == "rider"
        assert c.spinr_user_id == "u1"
        assert c.old_mongo_object_id == "cust-1"
        assert c.old_numeric_driver_id is None

    def test_conflicting_old_customer_id_is_ambiguous_not_guessed(self, monkeypatch):
        store = _store(rides=[_ride("u1", "cust-1"), _ride("u1", "cust-2")])
        _use(monkeypatch, store)
        plan = svc.build_rider_crosswalk_plan()
        assert plan.candidates == []
        assert plan.ambiguous == ["u1"]
        assert plan.stats["ambiguous_skipped"] == 1

    def test_ride_missing_rider_id_or_old_customer_id_is_excluded(self, monkeypatch):
        store = _store(
            rides=[
                {"rider_id": None, "legacy_import_metadata": {"old_customer_id": "cust-1"}},
                {"rider_id": "u2", "legacy_import_metadata": {}},
            ]
        )
        _use(monkeypatch, store)
        plan = svc.build_rider_crosswalk_plan()
        assert plan.candidates == []

    def test_already_recorded_old_customer_id_is_not_re_proposed(self, monkeypatch):
        store = _store(
            rides=[_ride("u1", "cust-1")],
            legacy_id_crosswalk=[{"old_mongo_object_id": "cust-1", "old_numeric_driver_id": None}],
        )
        _use(monkeypatch, store)
        plan = svc.build_rider_crosswalk_plan()
        assert plan.candidates == []
        assert plan.stats["already_recorded"] == 1


class TestApplyCrosswalkBackfill:
    def test_writes_one_row_per_candidate(self, monkeypatch):
        store = _store()
        _use(monkeypatch, store)
        candidates = [
            svc.CrosswalkCandidate("driver", "d1", "mongo-1", None),
            svc.CrosswalkCandidate("rider", "u1", "cust-1", None),
        ]
        written, failed = svc.apply_crosswalk_backfill(candidates, batch=BATCH)
        assert written == 2
        assert failed == []
        rows = store["legacy_id_crosswalk"]
        assert len(rows) == 2
        assert {r["spinr_user_id"] for r in rows} == {"d1", "u1"}
        assert all(r["batch"] == BATCH for r in rows)
        assert all(r["imported_at"] for r in rows)

    def test_empty_candidates_is_a_no_op(self, monkeypatch):
        store = _store()
        _use(monkeypatch, store)
        written, failed = svc.apply_crosswalk_backfill([], batch=BATCH)
        assert (written, failed) == (0, [])

    def test_one_failed_insert_does_not_abort_the_rest(self, monkeypatch):
        store = _store()
        store["__raise_on_insert__"] = "legacy_id_crosswalk"
        _use(monkeypatch, store)
        candidates = [svc.CrosswalkCandidate("driver", "d1", "mongo-1", None)]
        written, failed = svc.apply_crosswalk_backfill(candidates, batch=BATCH)
        assert written == 0
        assert failed == ["d1"]
