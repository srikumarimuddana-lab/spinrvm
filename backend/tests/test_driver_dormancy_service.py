"""Unit tests for backend/services/driver_dormancy_service.py.

The critical correctness properties this file locks in:
- idle-time is computed from the *later* of went_online_at/went_offline_at,
  falling back to created_at only when the driver has never toggled at all.
- a currently-online driver is never a candidate, no matter how stale its
  historical timestamps look.
- statuses whose inactivity is already explained elsewhere (suspended,
  banned, rejected, needs_review) are never candidates, regardless of idle
  time -- needs_review in particular is the abandoned-onboarding-shell
  population driver_import_service already tracks under a different label,
  and must not be double-counted here.
"""

import json
from datetime import datetime, timedelta, timezone

from backend.services import driver_dormancy_service as svc

BATCH = "20260911000000"
NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _days_ago(days: int) -> str:
    return _iso(NOW - timedelta(days=days))


class _Result:
    def __init__(self, data):
        self.data = data


class _NotProxy:
    """Minimal stand-in for supabase-py's query.not_ proxy -- only the one
    method this service actually calls."""

    def __init__(self, query):
        self._query = query

    def in_(self, col, vals):
        self._query._predicates.append(("not_in", col, list(vals)))
        return self._query


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._predicates = []
        self._update_payload = None

    @property
    def not_(self):
        return _NotProxy(self)

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
            elif kind == "not_in":
                _, col, vals = pred
                if row.get(col) in vals:
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
                    if actual is not None:
                        actual = str(actual).lower() if isinstance(actual, bool) else str(actual)
                else:
                    actual = row.get(col)
                if op == "is" and val == "null":
                    if actual is not None:
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


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _driver(
    driver_id,
    *,
    is_online=False,
    is_suspended=False,
    status="active",
    created_at=None,
    went_online_at=None,
    went_offline_at=None,
    extra_meta=None,
):
    return {
        "id": driver_id,
        "is_online": is_online,
        "is_suspended": is_suspended,
        "status": status,
        "created_at": created_at or _days_ago(500),
        "went_online_at": went_online_at,
        "went_offline_at": went_offline_at,
        "legacy_import_metadata": extra_meta or {},
    }


def _fresh_store(**tables):
    base = {"drivers": []}
    base.update(tables)
    return base


def _use(monkeypatch, store):
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store))
    monkeypatch.setattr(
        svc, "datetime", type("_FixedDateTime", (datetime,), {"now": classmethod(lambda cls, tz=None: NOW)})
    )


# --------------------------------------------------------------------------
# Idle-time computation and tier boundaries
# --------------------------------------------------------------------------


def test_never_activated_past_threshold_is_dormant(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", created_at=_days_ago(91))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert len(plan.candidates) == 1
    assert plan.candidates[0].tier == svc.TIER_DORMANT
    assert plan.candidates[0].reason == svc.REASON_NEVER_ACTIVATED


def test_never_activated_within_grace_period_is_not_a_candidate(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", created_at=_days_ago(30))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert plan.candidates == []


def test_went_dark_uses_later_of_online_and_offline_timestamps(monkeypatch):
    """A driver who went online 200 days ago but offline only 10 days ago
    is NOT dormant -- the more recent toggle is what counts."""
    store = _fresh_store(drivers=[_driver("drv-1", went_online_at=_days_ago(200), went_offline_at=_days_ago(10))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert plan.candidates == []


def test_went_dark_past_threshold_is_dormant(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", went_online_at=_days_ago(200), went_offline_at=_days_ago(120))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert len(plan.candidates) == 1
    assert plan.candidates[0].tier == svc.TIER_DORMANT
    assert plan.candidates[0].reason == svc.REASON_WENT_DARK


def test_long_dormant_tier_at_one_year_idle(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", created_at=_days_ago(400))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert len(plan.candidates) == 1
    assert plan.candidates[0].tier == svc.TIER_LONG_DORMANT


def test_exactly_at_dormant_boundary_is_a_candidate(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", created_at=_days_ago(svc.DORMANT_THRESHOLD_DAYS))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert len(plan.candidates) == 1
    assert plan.candidates[0].tier == svc.TIER_DORMANT


def test_one_day_before_dormant_boundary_is_not_a_candidate(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", created_at=_days_ago(svc.DORMANT_THRESHOLD_DAYS - 1))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert plan.candidates == []


# --------------------------------------------------------------------------
# Exclusions: currently online, punitive/rejected status, needs_review
# --------------------------------------------------------------------------


def test_currently_online_driver_is_never_a_candidate(monkeypatch):
    """Query-layer exclusion: is_online=True is filtered out before
    classification runs at all, regardless of how stale went_offline_at
    looks."""
    store = _fresh_store(
        drivers=[_driver("drv-1", is_online=True, went_online_at=_days_ago(1), went_offline_at=_days_ago(400))]
    )
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert plan.candidates == []


def test_suspended_driver_is_excluded(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", is_suspended=True, created_at=_days_ago(400))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert plan.candidates == []


def test_status_suspended_banned_rejected_are_excluded(monkeypatch):
    store = _fresh_store(
        drivers=[
            _driver("drv-1", status="suspended", created_at=_days_ago(400)),
            _driver("drv-2", status="banned", created_at=_days_ago(400)),
            _driver("drv-3", status="rejected", created_at=_days_ago(400)),
        ]
    )
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert plan.candidates == []


def test_needs_review_driver_is_excluded_not_double_counted(monkeypatch):
    """The abandoned-onboarding-shell population is already classified and
    excluded from the default admin view by driver_import_service's own
    is_incomplete_onboarding_row -- dormancy must not re-flag the same
    population under a different label."""
    store = _fresh_store(drivers=[_driver("drv-1", status="needs_review", created_at=_days_ago(400))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert plan.candidates == []


def test_active_driver_past_threshold_is_still_a_candidate(monkeypatch):
    """Sanity check that exclusions are specific to the listed statuses,
    not a blanket "anything but pending" rule."""
    store = _fresh_store(drivers=[_driver("drv-1", status="active", created_at=_days_ago(400))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert len(plan.candidates) == 1


def test_already_flagged_driver_is_not_a_candidate_again(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", created_at=_days_ago(400), extra_meta={"dormant": True})])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    assert plan.candidates == []


# --------------------------------------------------------------------------
# apply_dormancy_flags: writes, merge behavior, idempotency
# --------------------------------------------------------------------------


def test_apply_flags_preserving_existing_metadata(monkeypatch):
    store = _fresh_store(
        drivers=[_driver("drv-1", created_at=_days_ago(400), extra_meta={"source": "legacy_saskatoon_driver_import"})]
    )
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()
    conflicts = svc.apply_dormancy_flags(plan, batch=BATCH)
    assert conflicts == []

    row = store["drivers"][0]
    meta = row["legacy_import_metadata"]
    assert meta["dormant"] is True
    assert meta["dormancy_tier"] == svc.TIER_LONG_DORMANT
    assert meta["dormancy_flag"]["batch"] == BATCH
    assert meta["dormancy_flag"]["reason"] == svc.REASON_NEVER_ACTIVATED
    # Existing key preserved, not clobbered by the merge.
    assert meta["source"] == "legacy_saskatoon_driver_import"


def test_apply_is_idempotent_on_rerun(monkeypatch):
    store = _fresh_store(drivers=[_driver("drv-1", created_at=_days_ago(400))])
    _use(monkeypatch, store)
    plan1 = svc.build_dormancy_plan()
    svc.apply_dormancy_flags(plan1, batch=BATCH)

    plan2 = svc.build_dormancy_plan()
    assert plan2.candidates == []  # already flagged, not re-offered


def test_apply_dormancy_flags_picks_up_a_pre_apply_change(monkeypatch):
    """apply_dormancy_flags re-reads each row immediately before writing
    (not the plan-time snapshot) -- a metadata change that landed before
    apply started is naturally picked up and preserved."""
    store = _fresh_store(drivers=[_driver("drv-1", created_at=_days_ago(400))])
    _use(monkeypatch, store)
    plan = svc.build_dormancy_plan()

    store["drivers"][0]["legacy_import_metadata"]["some_other_writer_key"] = "concurrent-value"

    conflicts = svc.apply_dormancy_flags(plan, batch=BATCH)
    assert conflicts == []
    meta = store["drivers"][0]["legacy_import_metadata"]
    assert meta["dormant"] is True
    assert meta["some_other_writer_key"] == "concurrent-value"


def test_apply_flag_to_row_guard_refuses_a_stale_snapshot(monkeypatch):
    """The narrow race _apply_flag_to_row itself guards against: a write
    that lands in the instant between its own read and its own write."""
    store = _fresh_store(drivers=[_driver("drv-1", created_at=_days_ago(400))])
    _use(monkeypatch, store)

    stale_read_meta = dict(store["drivers"][0]["legacy_import_metadata"])
    store["drivers"][0]["legacy_import_metadata"]["some_other_writer_key"] = "concurrent-value"

    cand = svc.DormancyCandidate(
        id="drv-1",
        tier=svc.TIER_LONG_DORMANT,
        reason=svc.REASON_NEVER_ACTIVATED,
        idle_days=400,
        legacy_import_metadata=stale_read_meta,
    )
    conflict = svc._apply_flag_to_row(cand, stale_read_meta, batch=BATCH, now_iso="2026-09-11T00:00:00Z")
    assert conflict == "drv-1"
    meta = store["drivers"][0]["legacy_import_metadata"]
    assert meta["some_other_writer_key"] == "concurrent-value"
    assert "dormant" not in meta


# --------------------------------------------------------------------------
# fetch_dormancy_flagged_ids
# --------------------------------------------------------------------------


def test_fetch_dormancy_flagged_ids_all_and_scoped_by_tier(monkeypatch):
    store = _fresh_store(
        drivers=[
            _driver("drv-1", extra_meta={"dormant": True, "dormancy_tier": "dormant"}),
            _driver("drv-2", extra_meta={"dormant": True, "dormancy_tier": "long_dormant"}),
            _driver("drv-3", extra_meta={}),
        ]
    )
    _use(monkeypatch, store)
    assert svc.fetch_dormancy_flagged_ids() == {"drv-1", "drv-2"}
    assert svc.fetch_dormancy_flagged_ids(tier="dormant") == {"drv-1"}
    assert svc.fetch_dormancy_flagged_ids(tier="long_dormant") == {"drv-2"}
