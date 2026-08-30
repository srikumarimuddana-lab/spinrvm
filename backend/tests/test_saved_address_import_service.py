"""Tests for saved_address_import_service.py's legacy rider saved-address
backfill (Phase 4 of the 2026-08-27 migration plan).

Companion pattern to test_legacy_vehicle_history_backfill.py — a local fake
Supabase (this module reads `svc.supabase` directly, not through
`db_supabase`/`repositories`, so the shared `mock_supabase_client` conftest
fixture does not intercept it).

Covers: the customer_addresses.csv -> customers.csv -> phone crosswalk (with
the customer_id-is-actually-a-Mongo-_id gotcha), the Saskatchewan bounding-
box filter, the rider-match requirement, type->name/icon mapping, idempotency
against already-saved addresses, and the required-columns hard-error path.
"""

from __future__ import annotations

from backend.services import saved_address_import_service as svc

IMPORT_SOURCE = svc.IMPORT_SOURCE

# A real Saskatoon coordinate, well inside the bounding box.
SK_LAT, SK_LNG = "52.1332", "-106.6700"
# A real Chandigarh, India coordinate, well outside it -- same class of
# junk/test data already found in the rider CSV import.
INDIA_LAT, INDIA_LNG = "30.7190586", "76.7487044"


def _address_row(**overrides):
    row = {
        "_id": "addr-1",
        "customer_id": "mongo-cust-1",
        "name": "123 Main Street, Saskatoon, SK S7K 0J5",
        "lat": SK_LAT,
        "long": SK_LNG,
        "type": "home",
        "created_at": "1700000000000",
        "country": "",
        "state": "",
    }
    row.update(overrides)
    return row


def _customer_row(**overrides):
    row = {"_id": "mongo-cust-1", "phone": "3065551234"}
    row.update(overrides)
    return row


def _rider(**overrides):
    row = {"id": "rider-1", "phone": "+13065551234", "is_rider": True}
    row.update(overrides)
    return row


# ── fake supabase ────────────────────────────────────────────────────────


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert_rows = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def insert(self, rows):
        self._insert_rows = rows
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
        if self._insert_rows is not None:
            self.store.setdefault(self.table, []).extend(self._insert_rows)
            return _FakeExecute(list(self._insert_rows))
        return _FakeExecute(self._matched())


class _FakeSupabase:
    def __init__(self, store=None):
        self.store = store if store is not None else {}

    def table(self, name):
        return _FakeQuery(name, self.store)


def _install(monkeypatch, **kwargs):
    fake = _FakeSupabase(**kwargs)
    monkeypatch.setattr(svc, "supabase", fake)
    return fake


# ── validate_required_columns ───────────────────────────────────────────


def test_validate_required_columns_empty_rows():
    plan = svc.SavedAddressImportPlan()
    svc.validate_required_columns([], plan)
    assert len(plan.errors) == 1
    assert "empty" in plan.errors[0].message


def test_validate_required_columns_missing_column():
    plan = svc.SavedAddressImportPlan()
    svc.validate_required_columns([{"_id": "x"}], plan)
    assert {e.field for e in plan.errors} == {"customer_id", "lat", "long", "name"}


def test_build_plan_returns_early_on_missing_columns(monkeypatch):
    _install(monkeypatch)
    plan = svc.build_saved_address_import_plan([{"_id": "x"}], [], batch="b1")
    assert plan.errors
    assert plan.rows_to_insert == []


# ── happy path ───────────────────────────────────────────────────────────


def test_happy_path_inserts_address_matched_to_real_rider(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan([_address_row()], [_customer_row()], batch="b1")
    assert not plan.errors
    assert len(plan.rows_to_insert) == 1

    row = plan.rows_to_insert[0]
    assert row["user_id"] == "rider-1"
    assert row["address"] == "123 Main Street, Saskatoon, SK S7K 0J5"
    assert row["lat"] == float(SK_LAT)
    assert row["lng"] == float(SK_LNG)
    assert row["name"] == "Home"
    assert row["icon"] == "home"
    assert row["place_id"] is None
    assert row["created_at"].startswith("2023-11-14")  # 1700000000000ms
    assert row["legacy_import_metadata"]["source"] == IMPORT_SOURCE
    assert row["legacy_import_metadata"]["old_address_id"] == "addr-1"
    assert row["legacy_import_metadata"]["batch"] == "b1"


def test_work_type_maps_to_work_label_and_icon(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan([_address_row(type="work")], [_customer_row()], batch="b1")
    row = plan.rows_to_insert[0]
    assert row["name"] == "Work"
    assert row["icon"] == "work"


def test_blank_type_falls_back_to_default_label_and_icon(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan([_address_row(type="")], [_customer_row()], batch="b1")
    row = plan.rows_to_insert[0]
    assert row["name"] == "Saved Address"
    assert row["icon"] == "location"


def test_missing_created_at_falls_back_to_now(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan(
        [_address_row(created_at="not-a-timestamp")], [_customer_row()], batch="b1"
    )
    row = plan.rows_to_insert[0]
    assert "not-a-timestamp" not in row["created_at"]


# ── filtering: Saskatchewan bounding box ────────────────────────────────


def test_row_outside_saskatchewan_is_skipped(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan(
        [_address_row(lat=INDIA_LAT, long=INDIA_LNG)], [_customer_row()], batch="b1"
    )
    assert plan.rows_to_insert == []
    assert plan.skipped_out_of_province == 1


def test_unparseable_lat_lng_is_treated_as_out_of_province(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan(
        [_address_row(lat="not-a-number", long="also-not-a-number")], [_customer_row()], batch="b1"
    )
    assert plan.skipped_out_of_province == 1


# ── crosswalk: customer_id is a Mongo _id, not the Stripe customer_id ───


def test_customer_id_is_matched_against_customers_csv_mongo_id(monkeypatch):
    """The address CSV's own `customer_id` column is the legacy customer's
    Mongo _id -- confirmed against the real export. A Stripe-shaped
    customer_id would never match here; that's intentional."""
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan(
        [_address_row(customer_id="cus_stripe_shaped_id")], [_customer_row()], batch="b1"
    )
    assert plan.rows_to_insert == []
    assert plan.skipped_unmatched_customer == 1


def test_unresolvable_customer_id_is_skipped(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan([_address_row()], [], batch="b1")
    assert plan.rows_to_insert == []
    assert plan.skipped_unmatched_customer == 1


# ── rider-match requirement ─────────────────────────────────────────────


def test_no_matching_spinr_account_is_skipped(monkeypatch):
    _install(monkeypatch, store={"users": [], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan([_address_row()], [_customer_row()], batch="b1")
    assert plan.rows_to_insert == []
    assert plan.skipped_no_rider == 1


def test_matched_account_that_is_not_a_rider_is_skipped(monkeypatch):
    _install(monkeypatch, store={"users": [_rider(is_rider=False)], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan([_address_row()], [_customer_row()], batch="b1")
    assert plan.rows_to_insert == []
    assert plan.skipped_no_rider == 1


# ── idempotency ──────────────────────────────────────────────────────────


def test_already_saved_identical_address_is_skipped(monkeypatch):
    fake = _install(
        monkeypatch,
        store={
            "users": [_rider()],
            "saved_addresses": [{"user_id": "rider-1", "address": "123 Main Street, Saskatoon, SK S7K 0J5"}],
        },
    )
    plan = svc.build_saved_address_import_plan([_address_row()], [_customer_row()], batch="b1")
    assert plan.rows_to_insert == []
    assert plan.skipped_already_imported == 1
    # No write happened -- this is the plan-building step, read-only.
    assert len(fake.store["saved_addresses"]) == 1


def test_different_address_for_same_rider_is_not_treated_as_duplicate(monkeypatch):
    _install(
        monkeypatch,
        store={
            "users": [_rider()],
            "saved_addresses": [{"user_id": "rider-1", "address": "A totally different street"}],
        },
    )
    plan = svc.build_saved_address_import_plan([_address_row()], [_customer_row()], batch="b1")
    assert len(plan.rows_to_insert) == 1


# ── malformed address text ──────────────────────────────────────────────


def test_implausibly_short_address_text_is_skipped(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan([_address_row(name="Hi")], [_customer_row()], batch="b1")
    assert plan.rows_to_insert == []
    assert any(w.field == "name" for w in plan.warnings)


def test_blank_address_text_is_skipped(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    plan = svc.build_saved_address_import_plan([_address_row(name="")], [_customer_row()], batch="b1")
    assert plan.rows_to_insert == []


# ── commit ───────────────────────────────────────────────────────────────


def test_commit_refuses_on_errors(monkeypatch):
    fake = _install(monkeypatch)
    plan = svc.SavedAddressImportPlan()
    plan.errors.append(svc.ImportReportItem(0, "x", "bad"))
    try:
        svc.commit_saved_address_import_plan(plan)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    assert fake.store == {}


def test_commit_inserts_planned_rows(monkeypatch):
    fake = _install(monkeypatch, store={"saved_addresses": []})
    plan = svc.SavedAddressImportPlan()
    plan.rows_to_insert.append(
        {
            "id": "new-addr-1",
            "user_id": "rider-1",
            "name": "Home",
            "address": "123 Main Street",
            "lat": 52.1,
            "lng": -106.6,
            "icon": "home",
            "place_id": None,
            "created_at": "2024-01-01T00:00:00+00:00",
            "legacy_import_metadata": {"source": IMPORT_SOURCE},
        }
    )
    svc.commit_saved_address_import_plan(plan)
    assert len(fake.store["saved_addresses"]) == 1
    assert fake.store["saved_addresses"][0]["user_id"] == "rider-1"


def test_commit_is_a_noop_with_nothing_to_insert(monkeypatch):
    fake = _install(monkeypatch, store={"saved_addresses": []})
    svc.commit_saved_address_import_plan(svc.SavedAddressImportPlan())
    assert fake.store["saved_addresses"] == []
