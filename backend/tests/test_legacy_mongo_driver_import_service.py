"""Unit tests for backend/services/driver_import_service.py's Mongo
driver-profile import section (Phase 1, docs/migration/2026-08-27-legacy-
data-full-migration-approach.md).

Companion to test_driver_import_service.py / test_driver_import_service_
coverage.py, which cover the Saskatoon-CSV build_plan()/commit_plan() this
section deliberately does NOT touch. Fakes are a local, minimal copy of the
same in-memory Supabase pattern those files use (select/eq/in_/insert/rpc),
not shared — matches this repo's existing per-test-file fake convention.
"""

from __future__ import annotations

from backend.services import driver_import_service as svc

MONGO_IMPORT_SOURCE = svc.MONGO_IMPORT_SOURCE

SERVICE_AREA = {"id": "sa-1", "name": "Saskatoon", "province": "SK"}


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert = None
        self._update = None
        self._range = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def limit(self, _n):
        return self

    def range(self, start, end):
        # Inclusive-end, matching PostgREST/supabase-py semantics.
        self._range = (start, end)
        return self

    def insert(self, rows):
        self._insert = rows if isinstance(rows, list) else [rows]
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
        return rows

    def execute(self):
        if self._insert is not None:
            self.store.setdefault(self.table, []).extend(self._insert)
            return _FakeExecute(list(self._insert))
        if self._update is not None:
            matched = self._matched()
            for row in matched:
                row.update(self._update)
            return _FakeExecute(matched)
        rows = self._matched()
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        return _FakeExecute(rows)


class _FakeRpc:
    def __init__(self, name, params, recorder):
        self.name = name
        self.params = params
        self.recorder = recorder

    def execute(self):
        self.recorder.setdefault("rpc_calls", []).append((self.name, self.params))
        return _FakeExecute(f"enc::{self.params.get('plaintext')}")


class _FakeSupabase:
    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.recorder: dict = {}

    def table(self, name):
        return _FakeQuery(name, self.store)

    def rpc(self, name, params):
        return _FakeRpc(name, params, self.recorder)


def _install(monkeypatch, **kwargs):
    fake = _FakeSupabase(**kwargs)
    monkeypatch.setattr(svc, "supabase", fake)
    return fake


def _mongo_row(**overrides):
    row = {
        "_id": "6923ea32d1bde481895439f4",
        "name": "Jane Doe",
        "phone": "3065551234",
        "email": "jane@example.com",
        "license_number": "41626417",
        "ratings": "4.5",
        "created_at": "1700000000000",
        "is_deleted": "false",
        "is_block": "false",
        "status": "offline",
        "set_up_profile": "true",
    }
    row.update(overrides)
    return row


# ── validate_required_mongo_driver_columns ──────────────────────────────


def test_validate_required_columns_empty_rows():
    plan = svc.MongoDriverImportPlan()
    svc.validate_required_mongo_driver_columns([], plan)
    assert len(plan.errors) == 1
    assert "empty" in plan.errors[0].message


def test_validate_required_columns_missing_column():
    plan = svc.MongoDriverImportPlan()
    svc.validate_required_mongo_driver_columns([{"_id": "x", "name": "y"}], plan)
    assert any(e.field == "phone" for e in plan.errors)


# ── build_mongo_driver_import_plan: happy path ──────────────────────────


def test_happy_path_creates_needs_review_offline_driver(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert len(plan.users_to_insert) == 1
    assert len(plan.drivers_to_insert) == 1

    user = plan.users_to_insert[0]
    assert user["phone"] == "+13065551234"
    assert user["first_name"] == "Jane"
    assert user["last_name"] == "Doe"
    assert user["role"] == "driver"
    assert user["is_driver"] is True

    driver = plan.drivers_to_insert[0]
    # The core safety invariant of this section: no CSV field can ever
    # promote a row past needs_review/offline/unverified.
    assert driver["status"] == "needs_review"
    assert driver["is_verified"] is False
    assert driver["is_online"] is False
    assert driver["is_available"] is False
    assert driver["service_area_id"] == "sa-1"
    assert driver["legacy_import_metadata"]["source"] == MONGO_IMPORT_SOURCE
    assert driver["legacy_import_metadata"]["old_driver_id"] == "6923ea32d1bde481895439f4"
    assert driver["legacy_import_metadata"]["was_deleted_in_source"] is False
    assert driver["legacy_import_metadata"]["was_blocked_in_source"] is False
    assert driver["legacy_import_metadata"]["incomplete_profile_in_source"] is False
    assert driver["rating"] == 4.5
    assert driver["_plain_license_number"] == "41626417"
    # No vehicle data in Phase 1 -- that's vehicle_details.csv's job.
    assert "vehicle_make" not in driver
    assert driver.get("vehicle_type_id") is None or "vehicle_type_id" not in driver


def test_created_at_backdated_from_csv_epoch_ms(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(created_at="1700000000000")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    # 1700000000000 ms -> 2023-11-14T22:13:20+00:00
    assert plan.drivers_to_insert[0]["created_at"].startswith("2023-11-14")
    assert plan.users_to_insert[0]["created_at"].startswith("2023-11-14")


def test_created_at_falls_back_to_now_when_unparseable(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(created_at="not-a-timestamp")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    # Just assert it's a real ISO string, not the literal garbage input.
    assert "not-a-timestamp" not in plan.drivers_to_insert[0]["created_at"]


# ── build_mongo_driver_import_plan: rejections ──────────────────────────


def test_missing_id_is_error(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row(_id="")], service_area=SERVICE_AREA, import_batch="b1")
    assert any(e.field == "_id" for e in plan.errors)
    assert not plan.drivers_to_insert


def test_duplicate_id_in_same_batch_is_error(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(), _mongo_row(phone="3065559999")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert any(e.field == "_id" and "duplicate" in e.message for e in plan.errors)
    # First occurrence still processed once (the error is on the second).
    assert len(plan.drivers_to_insert) == 1


def test_invalid_phone_is_error(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row(phone="123")], service_area=SERVICE_AREA, import_batch="b1")
    assert any(e.field == "phone" for e in plan.errors)
    assert not plan.drivers_to_insert


def test_missing_name_is_warning_with_placeholder_not_error(monkeypatch):
    # Decided 2026-08-27 (option b): confirmed root cause is abandoned
    # onboarding (set_up_profile=false in source, never referenced by any
    # booking) — a blank name imports with a placeholder rather than
    # blocking the whole batch's commit. See docs/migration/2026-08-27-
    # legacy-driver-blank-name-root-cause.md.
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(name="", set_up_profile="false")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    assert any(w.field == "name" for w in plan.warnings)
    assert len(plan.drivers_to_insert) == 1

    driver = plan.drivers_to_insert[0]
    assert driver["name"] == "Unnamed Legacy Driver 5439f4"  # last 6 chars of the _id fixture
    assert driver["legacy_import_metadata"]["incomplete_profile_in_source"] is True
    # Still forced needs_review/unverified/offline like every other row.
    assert driver["status"] == "needs_review"
    assert driver["is_verified"] is False

    user = plan.users_to_insert[0]
    assert user["first_name"] == "Unnamed"
    assert user["last_name"] == "Legacy Driver 5439f4"


def test_missing_name_with_set_up_profile_true_still_imports_but_flag_false(monkeypatch):
    # A blank name with set_up_profile=true has not been observed in the
    # real export, but the flag must reflect the source field truthfully
    # rather than assume every blank name is an abandoned signup.
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(name="", set_up_profile="true")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    assert plan.drivers_to_insert[0]["legacy_import_metadata"]["incomplete_profile_in_source"] is False


def test_malformed_email_is_warning_not_error_row_still_imports(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(email="not-an-email")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    assert any(w.field == "email" for w in plan.warnings)
    assert len(plan.drivers_to_insert) == 1
    assert plan.users_to_insert[0]["email"] is None


# ── build_mongo_driver_import_plan: existing-match linking (2026-08-27) ─
#
# Decided: an existing-account match is no longer a hard error (see
# docs/migration/2026-08-27-legacy-driver-blank-name-root-cause.md §3/§6).


def test_existing_user_match_links_new_driver_and_sets_is_driver(monkeypatch):
    """Sub-population 1: phone matches an existing account with no driver
    row yet -- link a NEW driver row to it, no duplicate user."""
    fake = _install(
        monkeypatch,
        store={"users": [{"id": "u-organic", "phone": "+13065551234", "email": None, "is_driver": False}]},
    )
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert not plan.users_to_insert  # no duplicate user created
    assert len(plan.drivers_to_insert) == 1
    assert plan.drivers_to_insert[0]["user_id"] == "u-organic"

    assert len(plan.users_to_update) == 1
    upd = plan.users_to_update[0]
    assert upd["id"] == "u-organic"
    assert upd["is_driver"] is True
    history = upd["legacy_import_metadata"]["mongo_driver_history"]
    assert len(history) == 1
    assert history[0]["old_driver_id"] == "6923ea32d1bde481895439f4"

    svc.commit_mongo_driver_import_plan(plan)
    stored_user = next(u for u in fake.store["users"] if u["id"] == "u-organic")
    assert stored_user["is_driver"] is True
    assert stored_user["phone"] == "+13065551234"  # unchanged -- update never touched it


def test_existing_user_match_does_not_reflip_is_driver_if_already_true(monkeypatch):
    _install(
        monkeypatch,
        store={"users": [{"id": "u-organic", "phone": "+13065551234", "email": None, "is_driver": True}]},
    )
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    upd = plan.users_to_update[0]
    assert "is_driver" not in upd  # already true -- don't rewrite what isn't changing
    assert "legacy_import_metadata" in upd  # history is still recorded


class _FailingInsertQuery(_FakeQuery):
    """Same as _FakeQuery, but raises on .execute() for a bulk insert --
    simulates the drivers-insert bulk statement failing partway through a
    real commit (a UNIQUE(phone) violation, an encryption RPC error,
    anything). Reads/updates on other tables are untouched."""

    def execute(self):
        if self._insert is not None:
            raise RuntimeError("simulated drivers insert failure")
        return super().execute()


class _FailingDriversInsertSupabase(_FakeSupabase):
    def table(self, name):
        if name == "drivers":
            return _FailingInsertQuery(name, self.store)
        return super().table(name)


def test_commit_never_flags_existing_account_as_driver_if_drivers_insert_fails(monkeypatch):
    """The exact production bug found 2026-08-29 (692 real rows): sub-
    population 1 (existing account, no driver row yet) must never end up
    with is_driver=True unless its new drivers row actually landed. Before
    the fix, plan.users_to_update ran BEFORE the drivers insert, so a
    failure here would have durably left is_driver=True with no driver."""
    fake = _FailingDriversInsertSupabase(
        store={"users": [{"id": "u-organic", "phone": "+13065551234", "email": None, "is_driver": False}]}
    )
    monkeypatch.setattr(svc, "supabase", fake)
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert len(plan.users_to_update) == 1

    try:
        svc.commit_mongo_driver_import_plan(plan)
        raise AssertionError("expected the simulated drivers-insert failure to propagate")
    except RuntimeError as exc:
        assert "simulated drivers insert failure" in str(exc)

    stored_user = next(u for u in fake.store["users"] if u["id"] == "u-organic")
    assert stored_user["is_driver"] is False  # never flipped -- driver row never landed
    assert fake.store.get("drivers", []) == []


def test_commit_never_flags_new_user_as_driver_if_drivers_insert_fails(monkeypatch):
    """Same invariant for the brand-new-user population: the user row may
    still get created (harmless -- a plain account), but is_driver must
    stay False until its driver row is confirmed written."""
    fake = _FailingDriversInsertSupabase()
    monkeypatch.setattr(svc, "supabase", fake)
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert len(plan.users_to_insert) == 1

    try:
        svc.commit_mongo_driver_import_plan(plan)
        raise AssertionError("expected the simulated drivers-insert failure to propagate")
    except RuntimeError as exc:
        assert "simulated drivers insert failure" in str(exc)

    assert fake.store.get("drivers", []) == []
    users = fake.store.get("users", [])
    assert all(u.get("is_driver") is not True for u in users)


def test_existing_driver_match_enriches_history_no_new_row(monkeypatch):
    """Sub-population 2: phone matches an existing REAL driver -- enrich
    that driver's history, never create a competing needs_review row."""
    fake = _install(
        monkeypatch,
        store={
            "drivers": [
                {
                    "id": "drv-organic",
                    "phone": "+13065551234",
                    "legacy_import_metadata": {"source": "legacy_saskatoon_driver_import"},
                    "name": "Real Driver",
                    "status": "active",
                }
            ]
        },
    )
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert not plan.drivers_to_insert  # no duplicate driver row
    assert not plan.users_to_insert

    assert len(plan.drivers_to_enrich) == 1
    enrich = plan.drivers_to_enrich[0]
    assert enrich["id"] == "drv-organic"
    meta = enrich["legacy_import_metadata"]
    # The driver's OWN prior source is preserved, not clobbered.
    assert meta["source"] == "legacy_saskatoon_driver_import"
    assert len(meta["mongo_driver_history"]) == 1
    assert meta["mongo_driver_history"][0]["old_driver_id"] == "6923ea32d1bde481895439f4"

    svc.commit_mongo_driver_import_plan(plan)
    stored_driver = next(d for d in fake.store["drivers"] if d["id"] == "drv-organic")
    # Live fields never touched.
    assert stored_driver["name"] == "Real Driver"
    assert stored_driver["status"] == "active"
    assert stored_driver["legacy_import_metadata"]["mongo_driver_history"]


def test_resume_path_via_direct_creation_shape_skips_with_warning(monkeypatch):
    """A driver already CREATED by a previous run of THIS importer for the
    same old_driver_id is a resume, not new work -- the original top-level
    source/old_driver_id shape."""
    _install(
        monkeypatch,
        store={
            "drivers": [
                {
                    "id": "drv-1",
                    "phone": "+13065551234",
                    "legacy_import_metadata": {
                        "source": MONGO_IMPORT_SOURCE,
                        "old_driver_id": "6923ea32d1bde481895439f4",
                    },
                }
            ]
        },
    )
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert any(w.field == "resume" for w in plan.warnings)
    assert not plan.drivers_to_insert
    assert not plan.users_to_insert
    assert not plan.drivers_to_enrich


def test_resume_path_via_enrichment_history_shape_skips_with_warning(monkeypatch):
    """A driver already ENRICHED by a previous run of THIS importer for the
    same old_driver_id -- the mongo_driver_history list shape -- is also a
    resume, not a second history entry."""
    _install(
        monkeypatch,
        store={
            "drivers": [
                {
                    "id": "drv-organic",
                    "phone": "+13065551234",
                    "legacy_import_metadata": {
                        "source": "legacy_saskatoon_driver_import",
                        "mongo_driver_history": [{"old_driver_id": "6923ea32d1bde481895439f4", "batch": "prior"}],
                    },
                }
            ]
        },
    )
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert any(w.field == "resume" for w in plan.warnings)
    assert not plan.drivers_to_enrich
    assert not plan.drivers_to_insert


def test_matching_driver_from_a_different_old_id_is_enriched_not_treated_as_resume(monkeypatch):
    """Same phone, but the existing driver's history points at a DIFFERENT
    old_driver_id -- a second real history entry, not a false resume."""
    _install(
        monkeypatch,
        store={
            "drivers": [
                {
                    "id": "drv-1",
                    "phone": "+13065551234",
                    "legacy_import_metadata": {"source": MONGO_IMPORT_SOURCE, "old_driver_id": "some-other-id"},
                }
            ]
        },
    )
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert len(plan.drivers_to_enrich) == 1
    assert plan.drivers_to_enrich[0]["legacy_import_metadata"]["mongo_driver_history"][0]["old_driver_id"] == (
        "6923ea32d1bde481895439f4"
    )


# ── build_mongo_driver_import_plan: rating parsing ──────────────────────


def test_rating_zero_is_treated_as_no_data_not_written(monkeypatch):
    """The old app's '0' means 'never rated', not a real 0-star rating --
    must not overwrite the drivers table's own 5.0 default."""
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row(ratings="0")], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors
    assert "rating" not in plan.drivers_to_insert[0]


def test_rating_out_of_range_is_not_written(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row(ratings="7.2")], service_area=SERVICE_AREA, import_batch="b1")
    assert "rating" not in plan.drivers_to_insert[0]


def test_rating_unparseable_is_not_written(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(ratings="not-a-number")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert "rating" not in plan.drivers_to_insert[0]


# ── build_mongo_driver_import_plan: deleted/blocked history preserved ──


def test_deleted_and_blocked_flags_preserved_but_never_block_import(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(is_deleted="true", is_block="true")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors
    meta = plan.drivers_to_insert[0]["legacy_import_metadata"]
    assert meta["was_deleted_in_source"] is True
    assert meta["was_blocked_in_source"] is True
    # Still lands needs_review/offline like every other row -- deletion/block
    # state is preserved as history, not an import-time rejection.
    assert plan.drivers_to_insert[0]["status"] == "needs_review"


# ── commit_mongo_driver_import_plan ─────────────────────────────────────


def test_commit_refuses_with_errors(monkeypatch):
    _install(monkeypatch)
    plan = svc.MongoDriverImportPlan()
    plan.errors.append(svc.ImportErrorItem("OLD-1", "x", "boom"))
    try:
        svc.commit_mongo_driver_import_plan(plan)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "refusing to commit" in str(exc)


def test_commit_inserts_users_then_drivers_and_encrypts_license_number(monkeypatch):
    fake = _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan([_mongo_row()], service_area=SERVICE_AREA, import_batch="b1")
    assert not plan.errors

    svc.commit_mongo_driver_import_plan(plan)

    assert len(fake.store["users"]) == 1
    inserted_driver = fake.store["drivers"][0]
    assert inserted_driver["license_number"] == "enc::41626417"
    assert "_plain_license_number" not in inserted_driver


def test_commit_with_no_license_number_encrypts_none(monkeypatch):
    fake = _install(monkeypatch)
    plan = svc.build_mongo_driver_import_plan(
        [_mongo_row(license_number="")], service_area=SERVICE_AREA, import_batch="b1"
    )
    assert not plan.errors

    svc.commit_mongo_driver_import_plan(plan)

    inserted_driver = fake.store["drivers"][0]
    assert inserted_driver["license_number"] is None


# ── orphaned-driver backfill (2026-08-29 data-repair) ───────────────────

_ORPHAN_HISTORY = [
    {
        "batch": "20260828205731",
        "old_driver_id": "697cb9d28cd7f3775ff4fe6e",
        "source": MONGO_IMPORT_SOURCE,
        "was_deleted_in_source": False,
        "was_blocked_in_source": False,
        "incomplete_profile_in_source": True,
        "linked_at": "2026-08-28T20:57:37.794441+00:00",
    }
]


def test_find_orphaned_returns_only_history_users_missing_a_driver_row(monkeypatch):
    _install(
        monkeypatch,
        store={
            "users": [
                # Orphan: is_driver + history, no drivers row for u-1.
                {
                    "id": "u-1",
                    "first_name": "Unnamed",
                    "last_name": "Legacy Driver fe6e",
                    "phone": "+16393188526",
                    "is_driver": True,
                    "legacy_import_metadata": {"mongo_driver_history": _ORPHAN_HISTORY},
                },
                # Not an orphan: has history, but a drivers row exists.
                {
                    "id": "u-2",
                    "first_name": "Real",
                    "last_name": "Driver",
                    "phone": "+13065551111",
                    "is_driver": True,
                    "legacy_import_metadata": {"mongo_driver_history": _ORPHAN_HISTORY},
                },
                # Not an orphan: is_driver=True but no mongo_driver_history at all
                # (an ordinary, unrelated driver -- never touched by this import).
                {
                    "id": "u-3",
                    "first_name": "Ordinary",
                    "last_name": "Driver",
                    "phone": "+13065552222",
                    "is_driver": True,
                    "legacy_import_metadata": {},
                },
            ],
            "drivers": [{"id": "d-2", "user_id": "u-2"}],
        },
    )
    orphans = svc.find_orphaned_legacy_driver_users()
    assert [u["id"] for u in orphans] == ["u-1"]


class _RejectLargeInQuery(_FakeQuery):
    """Fails any .in_() call carrying more values than a single PostgREST
    request should reasonably take -- simulating the URL-length limit an
    unchunked .in_() over hundreds of UUIDs can hit in production."""

    def in_(self, col, vals):
        if len(vals) > 200:
            raise RuntimeError(f"simulated URL-too-long rejection: {len(vals)} values in .in_({col!r})")
        return super().in_(col, vals)


class _RejectLargeInSupabase(_FakeSupabase):
    def table(self, name):
        return _RejectLargeInQuery(name, self.store)


def test_find_orphaned_batches_in_query_at_production_scale(monkeypatch):
    """2026-08-30 production incident: find_orphaned_legacy_driver_users()
    ran its drivers lookup as one raw .in_("user_id", candidate_ids) instead
    of this file's own established _select_in(chunk=200) convention (used by
    every other .in_() lookup here -- see _prefetch_existing). At real scale
    (697 orphan candidates the first time this path ever ran against
    production), that surfaced to the operator as "Scan failed: an
    unexpected error occurred" with no useful detail. Reproduces at 250
    candidates against a fake that rejects any .in_() over 200 values."""
    users = [
        {
            "id": f"u-{i}",
            "first_name": "Legacy",
            "last_name": f"Driver {i}",
            "phone": f"+1306555{i:04d}",
            "is_driver": True,
            "legacy_import_metadata": {"mongo_driver_history": _ORPHAN_HISTORY},
        }
        for i in range(250)
    ]
    fake = _RejectLargeInSupabase(store={"users": users, "drivers": []})
    monkeypatch.setattr(svc, "supabase", fake)

    orphans = svc.find_orphaned_legacy_driver_users()
    assert len(orphans) == 250


def test_backfill_dry_run_reports_without_writing(monkeypatch):
    fake = _install(
        monkeypatch,
        store={
            "users": [
                {
                    "id": "u-1",
                    "first_name": "Unnamed",
                    "last_name": "Legacy Driver fe6e",
                    "phone": "+16393188526",
                    "is_driver": True,
                    "legacy_import_metadata": {"mongo_driver_history": _ORPHAN_HISTORY},
                }
            ]
        },
    )
    result = svc.backfill_orphaned_legacy_driver_rows(service_area=SERVICE_AREA, apply=False)
    assert result == {"scanned": 1, "applied": False, "fixed": 1}
    assert fake.store.get("drivers", []) == []  # nothing written


def test_backfill_apply_writes_driver_row_from_history(monkeypatch):
    fake = _install(
        monkeypatch,
        store={
            "users": [
                {
                    "id": "u-1",
                    "first_name": "Unnamed",
                    "last_name": "Legacy Driver fe6e",
                    "phone": "+16393188526",
                    "is_driver": True,
                    "legacy_import_metadata": {"mongo_driver_history": _ORPHAN_HISTORY},
                }
            ]
        },
    )
    result = svc.backfill_orphaned_legacy_driver_rows(service_area=SERVICE_AREA, apply=True)
    assert result == {"scanned": 1, "applied": True, "fixed": 1}

    assert len(fake.store["drivers"]) == 1
    driver = fake.store["drivers"][0]
    assert driver["user_id"] == "u-1"
    assert driver["phone"] == "+16393188526"
    assert driver["name"] == "Unnamed Legacy Driver fe6e"
    assert driver["service_area_id"] == "sa-1"
    # Never promoted past the safety floor, same as a normal import row.
    assert driver["status"] == "needs_review"
    assert driver["is_verified"] is False
    assert driver["is_online"] is False
    assert driver["is_available"] is False
    # No license_number/rating fields -- never recoverable, not fabricated.
    assert "license_number" not in driver
    assert "rating" not in driver
    meta = driver["legacy_import_metadata"]
    assert meta["old_driver_id"] == "697cb9d28cd7f3775ff4fe6e"
    assert meta["source"] == MONGO_IMPORT_SOURCE
    assert meta["incomplete_profile_in_source"] is True
    assert meta["backfill_reason"] == "orphaned_by_2026-08-29_commit_atomicity_bug"
    assert "backfilled_at" in meta


def test_backfill_no_orphans_returns_zero_without_error(monkeypatch):
    _install(monkeypatch, store={"users": []})
    result = svc.backfill_orphaned_legacy_driver_rows(service_area=SERVICE_AREA, apply=True)
    assert result == {"scanned": 0, "applied": True, "fixed": 0}


# ── created_at legacy-date backfill (2026-08-30) ────────────────────────
# backfill_orphaned_legacy_driver_rows() stamps the repaired drivers row's
# created_at as the repair run's own time; the driver's real join date is
# already correct on their linked users.created_at. See docs/change-log/
# 2026-08-30-rider-created-at-legacy-date-fix.md.


def test_find_backfilled_driver_created_at_mismatches_finds_mismatch(monkeypatch):
    fake = _install(
        monkeypatch,
        store={
            "users": [{"id": "u-1", "created_at": "2026-02-17T04:53:20+00:00"}],
            "drivers": [
                {
                    "id": "d-1",
                    "user_id": "u-1",
                    "created_at": "2026-08-30T01:58:38+00:00",
                    "legacy_import_metadata": {"backfill_reason": "orphaned_by_2026-08-29_commit_atomicity_bug"},
                }
            ],
        },
    )
    mismatches = svc.find_backfilled_driver_created_at_mismatches()
    assert mismatches == [
        {
            "driver_id": "d-1",
            "old_created_at": "2026-08-30T01:58:38+00:00",
            "new_created_at": "2026-02-17T04:53:20+00:00",
        }
    ]
    # Read-only: no write happened.
    assert fake.store["drivers"][0]["created_at"] == "2026-08-30T01:58:38+00:00"


def test_find_backfilled_driver_created_at_mismatches_skips_already_correct(monkeypatch):
    _install(
        monkeypatch,
        store={
            "users": [{"id": "u-1", "created_at": "2026-02-17T04:53:20+00:00"}],
            "drivers": [
                {
                    "id": "d-1",
                    "user_id": "u-1",
                    "created_at": "2026-02-17T04:53:20+00:00",
                    "legacy_import_metadata": {"backfill_reason": "orphaned_by_2026-08-29_commit_atomicity_bug"},
                }
            ],
        },
    )
    assert svc.find_backfilled_driver_created_at_mismatches() == []


def test_find_backfilled_driver_created_at_mismatches_ignores_non_backfilled_drivers(monkeypatch):
    _install(
        monkeypatch,
        store={
            "users": [{"id": "u-1", "created_at": "2026-02-17T04:53:20+00:00"}],
            "drivers": [
                {
                    "id": "d-1",
                    "user_id": "u-1",
                    "created_at": "2026-08-30T01:58:38+00:00",
                    "legacy_import_metadata": {"source": "legacy_mongo_driver_import"},
                }
            ],
        },
    )
    # Not stamped with the orphan-backfill reason -- an ordinary driver row
    # whose created_at legitimately differs from its user's for some other
    # reason, must never be touched by this repair.
    assert svc.find_backfilled_driver_created_at_mismatches() == []


def test_apply_driver_created_at_corrections_writes(monkeypatch):
    fake = _install(monkeypatch, store={"drivers": [{"id": "d-1", "created_at": "2026-08-30T01:58:38+00:00"}]})
    svc.apply_driver_created_at_corrections(
        [
            {
                "driver_id": "d-1",
                "old_created_at": "2026-08-30T01:58:38+00:00",
                "new_created_at": "2026-02-17T04:53:20+00:00",
            }
        ]
    )
    assert fake.store["drivers"][0]["created_at"] == "2026-02-17T04:53:20+00:00"


# ── print_mongo_driver_import_report ────────────────────────────────────


def test_print_report_dry_run_and_commit_modes(capsys):
    plan = svc.MongoDriverImportPlan()
    plan.warnings.append(svc.ImportErrorItem("OLD-1", "email", "bad format"))
    plan.errors.append(svc.ImportErrorItem("OLD-2", "phone", "invalid"))

    svc.print_mongo_driver_import_report(plan, dry_run=True)
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "WARNING old_driver_id=OLD-1" in out
    assert "ERROR old_driver_id=OLD-2" in out

    svc.print_mongo_driver_import_report(plan, dry_run=False)
    assert "COMMIT" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Abandoned-onboarding classification (2026-08-31 driver-count segmentation)
# ---------------------------------------------------------------------------


class TestIsIncompleteOnboardingRow:
    """The importer stamps two independent markers on a shell row, and
    neither alone covers the population: in the real 2026-08-22 batch 16 rows
    had a placeholder name with incomplete_profile_in_source=false, and 1 had
    the flag set with a real name. Both must classify as incomplete."""

    def test_flag_true_is_incomplete(self):
        row = {"name": "Jane Doe", "legacy_import_metadata": {svc.INCOMPLETE_PROFILE_KEY: True}}
        assert svc.is_incomplete_onboarding_row(row) is True

    def test_placeholder_name_alone_is_incomplete(self):
        row = {
            "name": f"{svc.LEGACY_PLACEHOLDER_NAME_PREFIX} 5439f4",
            "legacy_import_metadata": {svc.INCOMPLETE_PROFILE_KEY: False},
        }
        assert svc.is_incomplete_onboarding_row(row) is True

    def test_bare_placeholder_name_is_incomplete(self):
        """backfill_orphaned_legacy_driver_rows writes the prefix with no
        id suffix -- a prefix match must still catch it."""
        row = {"name": svc.LEGACY_PLACEHOLDER_NAME_PREFIX, "legacy_import_metadata": {}}
        assert svc.is_incomplete_onboarding_row(row) is True

    def test_real_driver_with_flag_false_is_complete(self):
        row = {"name": "Jane Doe", "legacy_import_metadata": {svc.INCOMPLETE_PROFILE_KEY: False}}
        assert svc.is_incomplete_onboarding_row(row) is False

    def test_row_without_either_marker_is_complete(self):
        """An organic (never-imported) driver, and the shape most existing
        test fixtures use -- must not be misclassified as a shell."""
        assert svc.is_incomplete_onboarding_row({"id": "d1", "user_id": "u1"}) is False

    def test_null_name_is_complete(self):
        assert svc.is_incomplete_onboarding_row({"name": None, "legacy_import_metadata": {}}) is False

    def test_flag_must_be_true_not_merely_truthy(self):
        """`"false"` from a stringly-typed source must not read as flagged."""
        row = {"name": "Jane Doe", "legacy_import_metadata": {svc.INCOMPLETE_PROFILE_KEY: "false"}}
        assert svc.is_incomplete_onboarding_row(row) is False


class TestFetchIncompleteOnboardingDriverIds:
    def test_returns_union_of_both_markers(self, monkeypatch):
        _install(
            monkeypatch,
            store={
                "drivers": [
                    {"id": "flagged", "name": "Jane Doe", "legacy_import_metadata": {svc.INCOMPLETE_PROFILE_KEY: True}},
                    {
                        "id": "placeholder",
                        "name": f"{svc.LEGACY_PLACEHOLDER_NAME_PREFIX} abc123",
                        "legacy_import_metadata": {},
                    },
                    {"id": "real", "name": "Real Driver", "legacy_import_metadata": {}},
                ]
            },
        )
        assert svc.fetch_incomplete_onboarding_driver_ids() == {"flagged", "placeholder"}

    def test_returns_empty_set_when_nothing_incomplete(self, monkeypatch):
        _install(monkeypatch, store={"drivers": [{"id": "real", "name": "Real Driver", "legacy_import_metadata": {}}]})
        assert svc.fetch_incomplete_onboarding_driver_ids() == set()

    def test_paginates_past_a_single_page(self, monkeypatch):
        """PostgREST caps an unpaginated select at 1000 rows. This population
        is 600 today and grows with every legacy batch, so a single-page
        fetch would silently start dropping ids."""
        rows = [
            {
                "id": f"shell-{i}",
                "name": f"{svc.LEGACY_PLACEHOLDER_NAME_PREFIX} {i:06d}",
                "legacy_import_metadata": {},
            }
            for i in range(1200)
        ]
        _install(monkeypatch, store={"drivers": rows})
        found = svc.fetch_incomplete_onboarding_driver_ids()
        assert len(found) == 1200
        assert "shell-1199" in found

    def test_skips_rows_with_no_id(self, monkeypatch):
        _install(
            monkeypatch,
            store={"drivers": [{"name": f"{svc.LEGACY_PLACEHOLDER_NAME_PREFIX} x", "legacy_import_metadata": {}}]},
        )
        assert svc.fetch_incomplete_onboarding_driver_ids() == set()


class TestPlaceholderNameConstantIsUsedAtWriteSite:
    def test_blank_name_row_gets_a_name_matching_the_shared_prefix(self, monkeypatch):
        """Ties the write site to the constant the read side classifies on --
        a hand-copied literal at either end would silently stop matching."""
        _install(monkeypatch)
        plan = svc.build_mongo_driver_import_plan(
            [_mongo_row(name="", set_up_profile="false")],
            service_area=SERVICE_AREA,
            import_batch="batch-1",
        )
        assert not plan.errors, plan.errors
        driver = plan.drivers_to_insert[0]
        assert driver["name"].startswith(svc.LEGACY_PLACEHOLDER_NAME_PREFIX)
        assert svc.is_incomplete_onboarding_row(driver) is True


# ---------------------------------------------------------------------------
# Suspect-legacy-number classification (2026-08-31 review queue)
# ---------------------------------------------------------------------------


class TestIsSuspectLegacyImportRow:
    """Flags a legacy-imported row whose phone is not a Canadian number. A
    review signal only — it gates nothing. The critical property is the last
    two cases: an ORGANIC signup is never flagged, whatever its number."""

    def test_saskatchewan_legacy_row_is_not_flagged(self):
        for ac in ("306", "639"):
            row = {"phone": f"+1{ac}5551234", "legacy_import_metadata": {"source": svc.MONGO_IMPORT_SOURCE}}
            assert svc.is_suspect_legacy_import_row(row) is False

    def test_other_canadian_area_codes_are_not_flagged(self):
        # 250 (BC) and 368 (AB) are real Canadian NPAs — both were missing from
        # an earlier draft of the list, which would have flagged real drivers.
        for ac in ("250", "368", "604", "416", "902", "867"):
            row = {"phone": f"+1{ac}5551234", "legacy_import_metadata": {"source": "x"}}
            assert svc.is_suspect_legacy_import_row(row) is False, ac

    def test_us_area_code_on_a_legacy_row_is_flagged(self):
        row = {"phone": "+13345551234", "legacy_import_metadata": {"source": "x"}}
        assert svc.is_suspect_legacy_import_row(row) is True

    def test_unassigned_area_code_is_flagged(self):
        """700/981/991 are not assignable NANP codes — the shape of a foreign
        number squeezed into ten digits."""
        for ac in ("700", "981", "991"):
            row = {"phone": f"+1{ac}5551234", "legacy_import_metadata": {"source": "x"}}
            assert svc.is_suspect_legacy_import_row(row) is True, ac

    def test_organic_signup_is_never_flagged(self):
        """The heuristic must not reach a real Spinr driver who happens to have
        a foreign mobile. `legacy_import_metadata` is JSONB NOT NULL DEFAULT
        '{}', so 'not imported' is the empty dict, not NULL."""
        for phone in ("+13345551234", "+19915551234", "+13065551234"):
            assert svc.is_suspect_legacy_import_row({"phone": phone, "legacy_import_metadata": {}}) is False

    def test_row_with_no_metadata_key_at_all_is_not_flagged(self):
        assert svc.is_suspect_legacy_import_row({"id": "d1", "phone": "+13345551234"}) is False

    def test_missing_or_malformed_phone_on_a_legacy_row_is_flagged(self):
        for phone in (None, "", "+1", "12345", "whatever"):
            row = {"phone": phone, "legacy_import_metadata": {"source": "x"}}
            assert svc.is_suspect_legacy_import_row(row) is True, repr(phone)

    def test_missing_phone_on_an_organic_row_is_not_flagged(self):
        assert svc.is_suspect_legacy_import_row({"phone": None, "legacy_import_metadata": {}}) is False


class TestFetchSuspectLegacyDriverIds:
    def test_returns_only_flagged_legacy_rows(self, monkeypatch):
        _install(
            monkeypatch,
            store={
                "drivers": [
                    {"id": "sk", "phone": "+13065551234", "legacy_import_metadata": {"source": "x"}},
                    {"id": "us", "phone": "+13345551234", "legacy_import_metadata": {"source": "x"}},
                    {"id": "organic-us", "phone": "+13345559999", "legacy_import_metadata": {}},
                ]
            },
        )
        assert svc.fetch_suspect_legacy_driver_ids() == {"us"}

    def test_paginates_past_a_single_page(self, monkeypatch):
        rows = [
            {"id": f"sus-{i}", "phone": "+19915551234", "legacy_import_metadata": {"source": "x"}} for i in range(1200)
        ]
        _install(monkeypatch, store={"drivers": rows})
        found = svc.fetch_suspect_legacy_driver_ids()
        assert len(found) == 1200
        assert "sus-1199" in found

    def test_empty_when_nothing_flagged(self, monkeypatch):
        _install(
            monkeypatch,
            store={"drivers": [{"id": "sk", "phone": "+16395551234", "legacy_import_metadata": {"source": "x"}}]},
        )
        assert svc.fetch_suspect_legacy_driver_ids() == set()


class TestCanadianAreaCodeList:
    def test_contains_the_saskatchewan_codes_the_fleet_actually_uses(self):
        assert {"306", "639"} <= svc.CANADIAN_AREA_CODES

    def test_excludes_real_us_codes(self):
        assert not ({"334", "360", "858", "952"} & svc.CANADIAN_AREA_CODES)
