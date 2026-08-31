"""Unit tests for backend/services/pre_launch_flag_service.py.

The critical correctness property this file locks in: a legacy-imported
driver whose own created_at predates launch must NOT be flagged if they
have ever driven a real ride or have a real driver_insurance_periods row —
only a fully dormant (zero-activity) pre-launch driver is a candidate. This
was found mid-build: an earlier, looser date-only criterion would have
mislabeled 33 real, active drivers as pre-launch test data.
"""

import json

from backend.services import pre_launch_flag_service as svc

BATCH = "20260830000000"


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

    def lt(self, col, val):
        self._predicates.append(("lt", col, val))
        return self

    def in_(self, col, vals):
        self._predicates.append(("in", col, list(vals)))
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
            elif kind == "lt":
                _, col, val = pred
                actual = row.get(col)
                if actual is None or not (actual < val):
                    return False
            elif kind == "in":
                _, col, vals = pred
                if row.get(col) not in vals:
                    return False
            elif kind == "filter":
                _, col, op, val = pred
                if col == "legacy_import_metadata" and op == "eq":
                    if json.dumps(row.get(col) or {}, sort_keys=True, default=str) != val:
                        return False
                    continue
                if "->>" in col:
                    base, key = col.split("->>", 1)
                    actual = (row.get(base) or {}).get(key)
                else:
                    actual = row.get(col)
                if op == "not.is" and val == "null":
                    if actual is None:
                        return False
                elif op == "is" and val == "null":
                    if actual is not None:
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


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _driver(driver_id, *, source="legacy_mongo_driver_import", created_at="2026-02-01", extra_meta=None):
    meta = {"source": source}
    if extra_meta:
        meta.update(extra_meta)
    return {"id": driver_id, "created_at": created_at, "legacy_import_metadata": meta}


def _ride(ride_id, *, created_at="2026-02-01", extra_meta=None):
    return {"id": ride_id, "created_at": created_at, "legacy_import_metadata": extra_meta or {}}


def _fresh_store(**tables):
    base = {"drivers": [], "rides": [], "driver_insurance_periods": []}
    base.update(tables)
    return base


def _use(monkeypatch, store):
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store))


# --------------------------------------------------------------------------
# Driver candidacy: the dormant-only criterion
# --------------------------------------------------------------------------


def test_dormant_pre_launch_driver_is_a_candidate(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1")])
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    assert [c.id for c in plan.driver_candidates] == ["drv-1"]


def test_pre_launch_driver_with_a_ride_is_excluded(monkeypatch):
    """The critical regression test: a pre-launch-created driver who has
    driven a real ride is a real active driver, not test data."""
    store = _fresh_store(
        drivers=[_driver("drv-1")],
        rides=[{"id": "ride-1", "driver_id": "drv-1", "created_at": "2026-06-01"}],
    )
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    assert plan.driver_candidates == []


def test_pre_launch_driver_with_insurance_period_is_excluded(monkeypatch):
    store = _fresh_store(
        drivers=[_driver("drv-1")],
        driver_insurance_periods=[{"id": "dip-1", "driver_id": "drv-1"}],
    )
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    assert plan.driver_candidates == []


def test_post_launch_dormant_driver_is_not_a_candidate(monkeypatch):
    """A dormant driver whose OWN created_at is post-launch is out of scope
    for this tool entirely (not pre-launch data), even if never active."""
    store = _fresh_store(drivers=[_driver("drv-1", created_at="2026-05-01")])
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    assert plan.driver_candidates == []


def test_non_legacy_driver_is_never_a_candidate(monkeypatch):
    row = {"id": "drv-1", "created_at": "2026-02-01", "legacy_import_metadata": {}}
    store = _fresh_store(drivers=[row])
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    assert plan.driver_candidates == []


def test_already_flagged_driver_is_not_a_candidate_again(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", extra_meta={"pre_launch_test": True})])
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    assert plan.driver_candidates == []


# --------------------------------------------------------------------------
# Ride candidacy: no activity-based exclusion
# --------------------------------------------------------------------------


def test_pre_launch_ride_is_a_candidate(monkeypatch):
    store = _fresh_store(rides=[_ride("ride-1")])
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    assert [c.id for c in plan.ride_candidates] == ["ride-1"]


def test_post_launch_ride_is_not_a_candidate(monkeypatch):
    store = _fresh_store(rides=[_ride("ride-1", created_at="2026-06-01")])
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    assert plan.ride_candidates == []


def test_already_flagged_ride_is_not_a_candidate_again(monkeypatch):
    store = _fresh_store(rides=[_ride("ride-1", extra_meta={"pre_launch_test": True})])
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    assert plan.ride_candidates == []


# --------------------------------------------------------------------------
# apply_pre_launch_flags: writes, merge behavior, idempotency
# --------------------------------------------------------------------------


def test_apply_flags_driver_and_ride_preserving_existing_metadata(monkeypatch):
    store = _fresh_store(
        drivers=[_driver("drv-1", extra_meta={"mongo_driver_history": [{"old_driver_id": "abc"}]})],
        rides=[_ride("ride-1", extra_meta={"old_booking_id": "xyz"})],
    )
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()
    conflicts = svc.apply_pre_launch_flags(plan, batch=BATCH)
    assert conflicts == {"drivers": [], "rides": []}

    driver_row = next(r for r in store["drivers"] if r["id"] == "drv-1")
    assert driver_row["legacy_import_metadata"]["pre_launch_test"] is True
    assert driver_row["legacy_import_metadata"]["pre_launch_flag"]["batch"] == BATCH
    # Existing key preserved, not clobbered by the merge.
    assert driver_row["legacy_import_metadata"]["mongo_driver_history"] == [{"old_driver_id": "abc"}]

    ride_row = next(r for r in store["rides"] if r["id"] == "ride-1")
    assert ride_row["legacy_import_metadata"]["pre_launch_test"] is True
    assert ride_row["legacy_import_metadata"]["old_booking_id"] == "xyz"


def test_apply_is_idempotent_on_rerun(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1")])
    _use(monkeypatch, store)
    plan1 = svc.build_pre_launch_flag_plan()
    svc.apply_pre_launch_flags(plan1, batch=BATCH)

    plan2 = svc.build_pre_launch_flag_plan()
    assert plan2.driver_candidates == []  # already flagged, not re-offered


def test_apply_pre_launch_flags_picks_up_a_pre_apply_change(monkeypatch):
    """apply_pre_launch_flags re-reads each row immediately before writing
    (not the plan-time snapshot) -- a metadata change that landed before
    apply started is naturally picked up and preserved, not overwritten by
    a stale plan snapshot."""
    store = _fresh_store(drivers=[_driver("drv-1")])
    _use(monkeypatch, store)
    plan = svc.build_pre_launch_flag_plan()

    # A write that lands after planning but before apply runs.
    store["drivers"][0]["legacy_import_metadata"]["some_other_writer_key"] = "concurrent-value"

    conflicts = svc.apply_pre_launch_flags(plan, batch=BATCH)
    assert conflicts == {"drivers": [], "rides": []}
    meta = store["drivers"][0]["legacy_import_metadata"]
    assert meta["pre_launch_test"] is True
    assert meta["some_other_writer_key"] == "concurrent-value"


def test_apply_flag_to_row_guard_refuses_a_stale_snapshot(monkeypatch):
    """The narrow race _apply_flag_to_row itself guards against: a write
    that lands in the instant between its own read and its own write. Exercised
    directly here since that window can't be forced through the public
    apply_pre_launch_flags entry point (which always re-reads fresh
    immediately beforehand, by design -- see the test above)."""
    store = _fresh_store(drivers=[_driver("drv-1")])
    _use(monkeypatch, store)

    stale_read_meta = dict(store["drivers"][0]["legacy_import_metadata"])  # snapshot taken "earlier"

    # A write lands after the stale snapshot was taken, before our guarded
    # write executes.
    store["drivers"][0]["legacy_import_metadata"]["some_other_writer_key"] = "concurrent-value"

    conflict = svc._apply_flag_to_row("drivers", "drv-1", stale_read_meta, batch=BATCH, now_iso="2026-08-30T00:00:00Z")
    assert conflict == "drv-1"
    # The concurrent writer's key must survive untouched -- the stale
    # snapshot must never have overwritten it, and our flag must not have
    # been applied against a column state we didn't actually observe.
    meta = store["drivers"][0]["legacy_import_metadata"]
    assert meta["some_other_writer_key"] == "concurrent-value"
    assert "pre_launch_test" not in meta
