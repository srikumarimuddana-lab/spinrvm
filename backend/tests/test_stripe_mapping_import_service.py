"""Unit tests for services/stripe_mapping_import_service.py.

The service talks to Supabase through its own module-level ``supabase`` plus
``driver_import_service._select_in``, so the fake is patched onto BOTH modules.
Stripe is exercised only in the live-validation tests, via patched
``stripe.Account.retrieve`` / ``stripe.Customer.retrieve``.
"""

import pytest

from backend.services import driver_import_service as driver_svc
from backend.services import stripe_mapping_import_service as svc


class _FakeExecute:
    def __init__(self, data):
        self.data = data


def _resolve(row, col):
    """Support PostgREST JSONB arrow syntax (``meta->>key``) like the real API."""
    if "->>" in col:
        base, key = col.split("->>", 1)
        value = (row.get(base) or {}).get(key)
        return None if value is None else str(value)
    return row.get(col)


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._update = None

    def select(self, *_a, **_k):
        return self

    def update(self, fields):
        self._update = dict(fields)
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

    def limit(self, _n):
        return self

    def execute(self):
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if _resolve(r, col) == val]
            elif op == "in":
                allowed = {str(v) for v in val}
                rows = [r for r in rows if str(_resolve(r, col)) in allowed]
            elif op == "is":
                assert val == "null"
                rows = [r for r in rows if _resolve(r, col) is None]
        if self._update is not None:
            for r in rows:
                r.update(self._update)
        return _FakeExecute(rows)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _FakeQuery(name, self.store)


def _install_fake(monkeypatch, *, drivers=None, users=None):
    store = {"drivers": drivers or [], "users": users or []}
    fake = _FakeSupabase(store)
    monkeypatch.setattr(svc, "supabase", fake)
    monkeypatch.setattr(driver_svc, "supabase", fake)
    return store


def _driver(**overrides):
    row = {
        "id": "drv-1",
        "phone": "+13065551234",
        "stripe_account_id": None,
        "legacy_import_metadata": {"source": svc.DRIVER_IMPORT_SOURCE, "old_driver_id": "OLD-1"},
    }
    row.update(overrides)
    return row


def _user(**overrides):
    row = {
        "id": "usr-1",
        "phone": "+13065559999",
        "email": "rider@example.com",
        "stripe_customer_id": None,
        "legacy_import_metadata": {},
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------- parsing


def test_parse_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown kind"):
        svc.parse_mapping_rows("a,b\n1,2\n", "corporate")


def test_parse_requires_stripe_column_per_kind():
    with pytest.raises(ValueError, match="stripe_account_id"):
        svc.parse_mapping_rows("old_driver_id,phone\nOLD-1,3065551234\n", svc.KIND_DRIVERS)
    with pytest.raises(ValueError, match="stripe_customer_id"):
        svc.parse_mapping_rows("phone,email\n3065551234,a@b.c\n", svc.KIND_RIDERS)


def test_parse_requires_a_match_key_column():
    with pytest.raises(ValueError, match="old_driver_id or phone"):
        svc.parse_mapping_rows("stripe_account_id\nacct_1\n", svc.KIND_DRIVERS)
    with pytest.raises(ValueError, match="phone or email"):
        svc.parse_mapping_rows("stripe_customer_id\ncus_1\n", svc.KIND_RIDERS)


def test_parse_rejects_empty_csv():
    with pytest.raises(ValueError, match="no data rows"):
        svc.parse_mapping_rows("stripe_account_id,old_driver_id\n", svc.KIND_DRIVERS)


# ------------------------------------------------------- driver matching


def test_driver_matched_by_old_driver_id(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    rows = [{"old_driver_id": "OLD-1", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert not plan.errors
    assert [u["driver_id"] for u in plan.driver_updates] == ["drv-1"]
    assert plan.driver_updates[0]["stripe_account_id"] == "acct_A1"
    assert plan.driver_updates[0]["row_ref"] == "OLD-1"


def test_driver_old_id_requires_importer_source(monkeypatch):
    # Same old_driver_id but stamped by some other source — must NOT match.
    _install_fake(monkeypatch, drivers=[_driver(legacy_import_metadata={"source": "other", "old_driver_id": "OLD-1"})])
    rows = [{"old_driver_id": "OLD-1", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["no_match"]


def test_driver_phone_fallback(monkeypatch):
    meta = {"source": svc.DRIVER_IMPORT_SOURCE, "old_driver_id": "OLD-9"}
    _install_fake(monkeypatch, drivers=[_driver(legacy_import_metadata=meta)])
    rows = [{"phone": "3065551234", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert not plan.errors
    assert [u["driver_id"] for u in plan.driver_updates] == ["drv-1"]
    assert plan.driver_updates[0]["row_ref"] == "row-2"  # header is line 1


def test_driver_phone_match_never_targets_native_drivers(monkeypatch):
    # Payout-hijack guard: a driver who onboarded natively (no legacy import
    # provenance) must be unreachable by phone-matched mapping rows, even
    # with a NULL stripe_account_id.
    _install_fake(monkeypatch, drivers=[_driver(legacy_import_metadata={})])
    rows = [{"phone": "3065551234", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["no_match"]
    assert not plan.driver_updates


def test_driver_ambiguous_match(monkeypatch):
    other = _driver(
        id="drv-2",
        phone="+13065550000",
        legacy_import_metadata={"source": svc.DRIVER_IMPORT_SOURCE, "old_driver_id": "OLD-2"},
    )
    _install_fake(monkeypatch, drivers=[_driver(), other])
    rows = [{"old_driver_id": "OLD-1", "phone": "3065550000", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["ambiguous_match"]
    assert not plan.driver_updates


def test_driver_no_match(monkeypatch):
    _install_fake(monkeypatch)
    rows = [{"old_driver_id": "OLD-404", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["no_match"]


def test_driver_missing_match_key_row(monkeypatch):
    _install_fake(monkeypatch)
    rows = [{"old_driver_id": "", "phone": "", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["csv"]


# --------------------------------------------------------- local guards


def test_malformed_account_id_errors_without_stripe(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    rows = [{"old_driver_id": "OLD-1", "stripe_account_id": "ACCT-BAD"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["stripe_id_format"]
    assert not plan.driver_updates


def test_duplicate_stripe_id_in_csv(monkeypatch):
    two = _driver(
        id="drv-2",
        phone="+13065550000",
        legacy_import_metadata={"source": svc.DRIVER_IMPORT_SOURCE, "old_driver_id": "OLD-2"},
    )
    _install_fake(monkeypatch, drivers=[_driver(), two])
    rows = [
        {"old_driver_id": "OLD-1", "stripe_account_id": "acct_A1"},
        {"old_driver_id": "OLD-2", "stripe_account_id": "acct_A1"},
    ]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["duplicate_in_csv", "duplicate_in_csv"]
    assert not plan.driver_updates


def test_duplicate_target_rows(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    rows = [
        {"old_driver_id": "OLD-1", "stripe_account_id": "acct_A1"},
        {"old_driver_id": "OLD-1", "stripe_account_id": "acct_A2"},
    ]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["duplicate_target", "duplicate_target"]
    assert not plan.driver_updates


def test_already_mapped_is_warning_and_skipped(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver(stripe_account_id="acct_A1")])
    rows = [{"old_driver_id": "OLD-1", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert not plan.errors
    assert [w.field for w in plan.warnings] == ["already_mapped"]
    assert not plan.driver_updates


def test_conflict_existing_different_id(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver(stripe_account_id="acct_OTHER")])
    rows = [{"old_driver_id": "OLD-1", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["conflict_existing"]


def test_id_taken_by_other_driver(monkeypatch):
    holder = _driver(id="drv-2", phone="+13065550000", stripe_account_id="acct_A1", legacy_import_metadata={})
    _install_fake(monkeypatch, drivers=[_driver(), holder])
    rows = [{"old_driver_id": "OLD-1", "stripe_account_id": "acct_A1"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["id_taken"]


def test_bad_old_stripe_account_id_is_warning_only(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    rows = [{"old_driver_id": "OLD-1", "stripe_account_id": "acct_A1", "old_stripe_account_id": "junk"}]
    plan = svc._build_local_plan(svc.KIND_DRIVERS, rows, "b1")
    assert not plan.errors
    assert [w.field for w in plan.warnings] == ["stripe_id_format"]
    assert plan.driver_updates[0]["old_stripe_account_id"] is None


# -------------------------------------------------------- rider matching


def test_rider_matched_by_phone_then_email(monkeypatch):
    by_phone = _user()
    by_email = _user(id="usr-2", phone="+13065550001", email="other@example.com")
    _install_fake(monkeypatch, users=[by_phone, by_email])
    rows = [{"phone": "3065559999", "email": "other@example.com", "stripe_customer_id": "cus_C1"}]
    plan = svc._build_local_plan(svc.KIND_RIDERS, rows, "b1")
    # phone and email resolve to different users → ambiguous
    assert [e.field for e in plan.errors] == ["ambiguous_match"]

    rows = [{"phone": "", "email": "other@example.com", "stripe_customer_id": "cus_C1"}]
    plan = svc._build_local_plan(svc.KIND_RIDERS, rows, "b1")
    assert not plan.errors
    assert [u["user_id"] for u in plan.user_updates] == ["usr-2"]


def test_rider_conflict_existing_lazily_created_customer(monkeypatch):
    _install_fake(monkeypatch, users=[_user(stripe_customer_id="cus_NEWAPP")])
    rows = [{"phone": "3065559999", "stripe_customer_id": "cus_C1"}]
    plan = svc._build_local_plan(svc.KIND_RIDERS, rows, "b1")
    assert [e.field for e in plan.errors] == ["conflict_existing"]


def test_rider_row_ref_uses_old_user_id_not_pii(monkeypatch):
    _install_fake(monkeypatch, users=[_user()])
    rows = [{"phone": "3065559999", "stripe_customer_id": "cus_BAD!", "old_user_id": "U-77"}]
    plan = svc._build_local_plan(svc.KIND_RIDERS, rows, "b1")
    assert plan.errors[0].row_ref == "U-77"
    assert "3065559999" not in plan.errors[0].message


# --------------------------------------------------- live Stripe validation

SECRET = "sk_test_abc"  # test-mode key → payload livemode False matches
DRIVER_ROWS = [{"old_driver_id": "OLD-1", "stripe_account_id": "acct_A1"}]


def _acct_payload(**overrides):
    payload = {
        "id": "acct_A1",
        "type": "express",
        "country": "CA",
        "business_type": "individual",
        "details_submitted": True,
        "payouts_enabled": True,
        "livemode": False,
        "capabilities": {"transfers": "active"},
        "requirements": {"currently_due": [], "disabled_reason": None},
    }
    payload.update(overrides)
    return payload


def _patch_account_retrieve(monkeypatch, result):
    import stripe

    def fake(_id, api_key=None):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(stripe.Account, "retrieve", fake)


def _patch_customer_retrieve(monkeypatch, result):
    import stripe

    def fake(_id, api_key=None):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(stripe.Customer, "retrieve", fake)


@pytest.mark.anyio
async def test_build_plan_clean_account(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    _patch_account_retrieve(monkeypatch, _acct_payload())
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, SECRET, "b1")
    assert not plan.errors and not plan.warnings
    assert len(plan.driver_updates) == 1


@pytest.mark.anyio
async def test_build_plan_no_such_account(monkeypatch):
    import stripe

    _install_fake(monkeypatch, drivers=[_driver()])
    _patch_account_retrieve(monkeypatch, stripe.error.InvalidRequestError("No such account: acct_A1", None))
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, SECRET, "b1")
    assert [e.field for e in plan.errors] == ["not_accessible"]
    assert not plan.driver_updates


@pytest.mark.anyio
async def test_build_plan_permission_error(monkeypatch):
    import stripe

    _install_fake(monkeypatch, drivers=[_driver()])
    _patch_account_retrieve(monkeypatch, stripe.error.PermissionError("not authorized for acct_A1"))
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, SECRET, "b1")
    assert [e.field for e in plan.errors] == ["not_accessible"]


@pytest.mark.anyio
async def test_build_plan_rate_limit_is_transient_error(monkeypatch):
    import stripe

    _install_fake(monkeypatch, drivers=[_driver()])
    _patch_account_retrieve(monkeypatch, stripe.error.RateLimitError("slow down"))
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, SECRET, "b1")
    assert [e.field for e in plan.errors] == ["stripe_transient"]
    assert not plan.driver_updates


@pytest.mark.anyio
async def test_build_plan_wrong_country(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    _patch_account_retrieve(monkeypatch, _acct_payload(country="US"))
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, SECRET, "b1")
    assert [e.field for e in plan.errors] == ["country"]
    assert not plan.driver_updates


@pytest.mark.anyio
async def test_build_plan_transfers_never_requested(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    _patch_account_retrieve(monkeypatch, _acct_payload(capabilities={}))
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, SECRET, "b1")
    assert [e.field for e in plan.errors] == ["capability_transfers"]


@pytest.mark.anyio
async def test_build_plan_incomplete_onboarding_is_commitable(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    _patch_account_retrieve(
        monkeypatch,
        _acct_payload(
            details_submitted=False,
            payouts_enabled=False,
            capabilities={"transfers": "pending"},
            requirements={"currently_due": ["external_account"], "disabled_reason": None},
        ),
    )
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, SECRET, "b1")
    assert not plan.errors
    assert {w.field for w in plan.warnings} == {"capability_transfers", "onboarding_incomplete", "requirements_due"}
    assert len(plan.driver_updates) == 1  # the whole point: keep the account


@pytest.mark.anyio
async def test_build_plan_rejected_account(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    _patch_account_retrieve(
        monkeypatch, _acct_payload(requirements={"currently_due": [], "disabled_reason": "rejected.fraud"})
    )
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, SECRET, "b1")
    assert "account_rejected" in [e.field for e in plan.errors]


@pytest.mark.anyio
async def test_build_plan_deleted_customer(monkeypatch):
    _install_fake(monkeypatch, users=[_user()])
    _patch_customer_retrieve(monkeypatch, {"id": "cus_C1", "deleted": True, "livemode": False})
    rows = [{"phone": "3065559999", "stripe_customer_id": "cus_C1"}]
    plan = await svc.build_plan(svc.KIND_RIDERS, rows, SECRET, "b1")
    assert [e.field for e in plan.errors] == ["customer_deleted"]
    assert not plan.user_updates


@pytest.mark.anyio
async def test_build_plan_customer_metadata_mismatch_warns(monkeypatch):
    _install_fake(monkeypatch, users=[_user()])
    _patch_customer_retrieve(monkeypatch, {"id": "cus_C1", "livemode": False, "metadata": {"user_id": "someone-else"}})
    rows = [{"phone": "3065559999", "stripe_customer_id": "cus_C1"}]
    plan = await svc.build_plan(svc.KIND_RIDERS, rows, SECRET, "b1")
    assert not plan.errors
    assert [w.field for w in plan.warnings] == ["metadata_user_mismatch"]
    assert len(plan.user_updates) == 1


@pytest.mark.anyio
async def test_build_plan_livemode_mismatch_is_error(monkeypatch):
    # A test-mode object must never become a live payout destination.
    _install_fake(monkeypatch, drivers=[_driver()])
    _patch_account_retrieve(monkeypatch, _acct_payload(livemode=True))
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, SECRET, "b1")
    assert [e.field for e in plan.errors] == ["livemode"]
    assert not plan.driver_updates


@pytest.mark.anyio
async def test_build_plan_without_stripe_secret_refuses(monkeypatch):
    _install_fake(monkeypatch, drivers=[_driver()])
    plan = await svc.build_plan(svc.KIND_DRIVERS, DRIVER_ROWS, "", "b1")
    assert [e.field for e in plan.errors] == ["stripe_not_configured"]
    assert not plan.driver_updates


# ---------------------------------------------------------------- commit


def test_commit_plan_refuses_on_errors(monkeypatch):
    store = _install_fake(monkeypatch, drivers=[_driver()])
    plan = svc.StripeMappingPlan(kind=svc.KIND_DRIVERS, batch="b1")
    plan.errors.append(svc.StripeMappingErrorItem("OLD-1", "no_match", "boom"))
    plan.driver_updates.append(
        {"driver_id": "drv-1", "stripe_account_id": "acct_A1", "row_ref": "OLD-1", "existing_metadata": {}}
    )
    with pytest.raises(RuntimeError, match="refusing to commit"):
        svc.commit_plan(plan)
    assert store["drivers"][0]["stripe_account_id"] is None  # zero writes


def test_commit_plan_writes_id_and_merges_provenance(monkeypatch):
    existing_meta = {"source": svc.DRIVER_IMPORT_SOURCE, "old_driver_id": "OLD-1"}
    store = _install_fake(monkeypatch, drivers=[_driver(legacy_import_metadata=dict(existing_meta))])
    plan = svc.StripeMappingPlan(kind=svc.KIND_DRIVERS, batch="b1")
    plan.driver_updates.append(
        {
            "driver_id": "drv-1",
            "stripe_account_id": "acct_A1",
            "row_ref": "OLD-1",
            "old_stripe_account_id": "acct_OLDPLATFORM",
            "existing_metadata": dict(existing_meta),
        }
    )
    result = svc.commit_plan(plan)
    assert result == {"updated_drivers": 1, "updated_users": 0, "conflicts": []}
    row = store["drivers"][0]
    assert row["stripe_account_id"] == "acct_A1"
    meta = row["legacy_import_metadata"]
    # Driver-importer provenance keys survive the merge.
    assert meta["source"] == svc.DRIVER_IMPORT_SOURCE
    assert meta["old_driver_id"] == "OLD-1"
    migration = meta["stripe_migration"]
    assert migration["batch"] == "b1"
    assert migration["source"] == svc.IMPORT_SOURCE
    assert migration["old_stripe_account_id"] == "acct_OLDPLATFORM"
    assert migration["imported_at"]


def test_commit_plan_null_guard_reports_conflict(monkeypatch):
    # Column got filled between validate and commit → guarded update matches
    # zero rows → surfaced as a conflict, never clobbered.
    store = _install_fake(monkeypatch, drivers=[_driver(stripe_account_id="acct_RACED")])
    plan = svc.StripeMappingPlan(kind=svc.KIND_DRIVERS, batch="b1")
    plan.driver_updates.append(
        {"driver_id": "drv-1", "stripe_account_id": "acct_A1", "row_ref": "OLD-1", "existing_metadata": {}}
    )
    result = svc.commit_plan(plan)
    assert result["updated_drivers"] == 0
    assert result["conflicts"] == ["OLD-1"]
    assert store["drivers"][0]["stripe_account_id"] == "acct_RACED"


def test_commit_plan_unique_violation_is_conflict(monkeypatch):
    # Migration-257 unique index firing (same acct_ taken by an overlapping
    # commit) → reported as a conflict, never retried or propagated.
    class _UniqueViolation(Exception):
        code = "23505"

    class _RaisingQuery:
        def update(self, _fields):
            return self

        def eq(self, *_a):
            return self

        def is_(self, *_a):
            return self

        def execute(self):
            raise _UniqueViolation("duplicate key value violates unique constraint")

    class _RaisingSupabase:
        def table(self, _name):
            return _RaisingQuery()

    monkeypatch.setattr(svc, "supabase", _RaisingSupabase())
    plan = svc.StripeMappingPlan(kind=svc.KIND_DRIVERS, batch="b1")
    plan.driver_updates.append(
        {"driver_id": "drv-1", "stripe_account_id": "acct_A1", "row_ref": "OLD-1", "existing_metadata": {}}
    )
    result = svc.commit_plan(plan)
    assert result["updated_drivers"] == 0
    assert result["conflicts"] == ["OLD-1"]


def test_commit_plan_other_db_error_propagates(monkeypatch):
    # Non-unique DB errors must surface loudly (route turns them into a 502).
    class _DbError(Exception):
        pass

    class _RaisingQuery:
        def update(self, _fields):
            return self

        def eq(self, *_a):
            return self

        def is_(self, *_a):
            return self

        def execute(self):
            raise _DbError("connection reset")

    class _RaisingSupabase:
        def table(self, _name):
            return _RaisingQuery()

    monkeypatch.setattr(svc, "supabase", _RaisingSupabase())
    plan = svc.StripeMappingPlan(kind=svc.KIND_DRIVERS, batch="b1")
    plan.driver_updates.append(
        {"driver_id": "drv-1", "stripe_account_id": "acct_A1", "row_ref": "OLD-1", "existing_metadata": {}}
    )
    with pytest.raises(_DbError):
        svc.commit_plan(plan)


def test_commit_plan_writes_rider_customer(monkeypatch):
    store = _install_fake(monkeypatch, users=[_user()])
    plan = svc.StripeMappingPlan(kind=svc.KIND_RIDERS, batch="b2")
    plan.user_updates.append(
        {
            "user_id": "usr-1",
            "stripe_customer_id": "cus_C1",
            "row_ref": "U-77",
            "old_stripe_customer_id": None,
            "old_user_id": "U-77",
            "existing_metadata": {},
        }
    )
    result = svc.commit_plan(plan)
    assert result == {"updated_drivers": 0, "updated_users": 1, "conflicts": []}
    row = store["users"][0]
    assert row["stripe_customer_id"] == "cus_C1"
    assert row["legacy_import_metadata"]["stripe_migration"]["old_user_id"] == "U-77"


# ------------------------------------------------------------- KYC sync


@pytest.mark.anyio
async def test_sync_kyc_after_commit_stamps_status(monkeypatch):
    from unittest.mock import AsyncMock

    from backend import db_supabase
    from backend.services import stripe_kyc_sync

    driver = _driver(
        stripe_account_id="acct_A1",
        legacy_import_metadata={"stripe_migration": {"batch": "b1", "source": svc.IMPORT_SOURCE}},
    )
    monkeypatch.setattr(db_supabase, "get_rows", AsyncMock(return_value=[driver]))
    update_one = AsyncMock()
    monkeypatch.setattr(db_supabase, "update_one", update_one)
    monkeypatch.setattr(stripe_kyc_sync, "refresh_driver_kyc", AsyncMock(return_value={"status": "ok"}))

    result = await svc.sync_kyc_after_commit(["drv-1"], "b1")
    assert result == {"ok": 1, "failed": 0, "failed_driver_ids": []}
    _table, _filters, updates = update_one.call_args.args
    migration = updates["legacy_import_metadata"]["stripe_migration"]
    assert migration["kyc_sync"] == "ok"
    assert migration["kyc_synced_at"]
    assert migration["batch"] == "b1"  # existing provenance preserved


@pytest.mark.anyio
async def test_sync_kyc_one_failure_does_not_halt_batch(monkeypatch):
    from unittest.mock import AsyncMock

    from backend import db_supabase
    from backend.services import stripe_kyc_sync

    d1 = _driver(id="drv-1", stripe_account_id="acct_A1", legacy_import_metadata={})
    d2 = _driver(id="drv-2", stripe_account_id="acct_A2", legacy_import_metadata={})

    async def fake_get_rows(_table, filters, limit=1):
        return [d1] if filters["id"] == "drv-1" else [d2]

    async def fake_refresh(driver):
        if driver["id"] == "drv-1":
            raise RuntimeError("stripe exploded")
        return {"status": "ok"}

    monkeypatch.setattr(db_supabase, "get_rows", fake_get_rows)
    monkeypatch.setattr(db_supabase, "update_one", AsyncMock())
    monkeypatch.setattr(stripe_kyc_sync, "refresh_driver_kyc", fake_refresh)

    result = await svc.sync_kyc_after_commit(["drv-1", "drv-2"], "b1")
    assert result["ok"] == 1
    assert result["failed"] == 1
    assert result["failed_driver_ids"] == ["drv-1"]


@pytest.mark.anyio
async def test_sync_kyc_non_ok_status_recorded_as_failure(monkeypatch):
    from unittest.mock import AsyncMock

    from backend import db_supabase
    from backend.services import stripe_kyc_sync

    driver = _driver(stripe_account_id="acct_A1", legacy_import_metadata={})
    monkeypatch.setattr(db_supabase, "get_rows", AsyncMock(return_value=[driver]))
    update_one = AsyncMock()
    monkeypatch.setattr(db_supabase, "update_one", update_one)
    monkeypatch.setattr(stripe_kyc_sync, "refresh_driver_kyc", AsyncMock(return_value={"status": "stripe_error"}))

    result = await svc.sync_kyc_after_commit(["drv-1"], "b1")
    assert result["failed_driver_ids"] == ["drv-1"]
    # Status still stamped so the operator sees which drivers need a retry.
    _t, _f, updates = update_one.call_args.args
    assert updates["legacy_import_metadata"]["stripe_migration"]["kyc_sync"] == "stripe_error"
