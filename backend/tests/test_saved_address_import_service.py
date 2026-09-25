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


class _UniqueViolationError(Exception):
    """Shaped like the postgrest-py exception commit_saved_address_import_plan's
    _is_unique_violation detects: a `.code` attribute (as supabase-py surfaces
    a PostgREST error) is enough, same as the message-substring path."""

    code = "23505"


class _FakeQuery:
    def __init__(self, table, store, fake):
        self.table = table
        self.store = store
        self.fake = fake
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
        # The service inserts a whole chunk (list) on the happy path and a
        # single row (dict) on the per-row fallback -- normalize both.
        self._insert_rows = rows if isinstance(rows, list) else [rows]
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
            for row in self._insert_rows:
                if (row.get("user_id"), row.get("icon")) in self.fake.fail_pairs:
                    raise _UniqueViolationError("duplicate key value violates unique constraint")
            self.store.setdefault(self.table, []).extend(self._insert_rows)
            return _FakeExecute(list(self._insert_rows))
        return _FakeExecute(self._matched())


class _FakeSupabase:
    def __init__(self, store=None):
        self.store = store if store is not None else {}
        # (user_id, icon) pairs whose insert should look like a migration
        # 485 unique-index violation -- simulates a concurrent write that
        # claimed that Home/Work between validate and commit.
        self.fail_pairs: set[tuple[str, str]] = set()

    def table(self, name):
        return _FakeQuery(name, self.store, self)


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


# ── one Home / one Work per user (migration 485) ────────────────────────


def test_existing_home_in_db_downgrades_legacy_home_to_location(monkeypatch):
    _install(
        monkeypatch,
        store={
            "users": [_rider()],
            "saved_addresses": [{"user_id": "rider-1", "address": "An existing home address", "icon": "home"}],
        },
    )
    plan = svc.build_saved_address_import_plan([_address_row()], [_customer_row()], batch="b1")
    assert len(plan.rows_to_insert) == 1
    row = plan.rows_to_insert[0]
    assert row["icon"] == "location"
    # Label is kept ("Home") -- only the icon is downgraded, so the row still
    # reads as the rider's old home address, just not the DB's one Home slot.
    assert row["name"] == "Home"
    assert plan.downgraded_duplicate_home_work == 1
    assert plan.skipped_already_imported == 0


def test_two_home_rows_in_one_batch_only_first_stays_home(monkeypatch):
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    rows = [
        _address_row(_id="addr-1", name="111 First Street, Saskatoon, SK"),
        _address_row(_id="addr-2", name="222 Second Street, Saskatoon, SK"),
    ]
    plan = svc.build_saved_address_import_plan(rows, [_customer_row()], batch="b1")
    assert len(plan.rows_to_insert) == 2
    icons_by_old_id = {r["legacy_import_metadata"]["old_address_id"]: r["icon"] for r in plan.rows_to_insert}
    assert icons_by_old_id == {"addr-1": "home", "addr-2": "location"}
    assert plan.downgraded_duplicate_home_work == 1


def test_existing_home_does_not_affect_work_import(monkeypatch):
    _install(
        monkeypatch,
        store={
            "users": [_rider()],
            "saved_addresses": [{"user_id": "rider-1", "address": "An existing home address", "icon": "home"}],
        },
    )
    plan = svc.build_saved_address_import_plan([_address_row(type="work")], [_customer_row()], batch="b1")
    row = plan.rows_to_insert[0]
    assert row["icon"] == "work"
    assert plan.downgraded_duplicate_home_work == 0


def test_existing_work_downgrades_legacy_work_but_leaves_home_alone(monkeypatch):
    _install(
        monkeypatch,
        store={
            "users": [_rider()],
            "saved_addresses": [{"user_id": "rider-1", "address": "An existing work address", "icon": "work"}],
        },
    )
    rows = [
        _address_row(_id="addr-1", name="111 First Street, Saskatoon, SK", type="home"),
        _address_row(_id="addr-2", name="222 Second Street, Saskatoon, SK", type="work"),
    ]
    plan = svc.build_saved_address_import_plan(rows, [_customer_row()], batch="b1")
    icons_by_old_id = {r["legacy_import_metadata"]["old_address_id"]: r["icon"] for r in plan.rows_to_insert}
    assert icons_by_old_id == {"addr-1": "home", "addr-2": "location"}
    assert plan.downgraded_duplicate_home_work == 1


def test_non_home_work_icon_unaffected_by_existing_home(monkeypatch):
    _install(
        monkeypatch,
        store={
            "users": [_rider()],
            "saved_addresses": [{"user_id": "rider-1", "address": "An existing home address", "icon": "home"}],
        },
    )
    plan = svc.build_saved_address_import_plan([_address_row(type="")], [_customer_row()], batch="b1")
    row = plan.rows_to_insert[0]
    assert row["icon"] == "location"
    assert plan.downgraded_duplicate_home_work == 0


def test_earlier_created_at_wins_home_regardless_of_csv_row_order(monkeypatch):
    """The earliest-created legacy row keeps Home, not just whichever the
    CSV happens to list first -- same "oldest wins" rule routes/addresses.py
    uses for this table's own live replace-on-save logic."""
    _install(monkeypatch, store={"users": [_rider()], "saved_addresses": []})
    rows = [
        _address_row(_id="addr-later", name="111 First Street, Saskatoon, SK", created_at="1700000100000"),
        _address_row(_id="addr-earlier", name="222 Second Street, Saskatoon, SK", created_at="1700000000000"),
    ]
    plan = svc.build_saved_address_import_plan(rows, [_customer_row()], batch="b1")
    icons_by_old_id = {r["legacy_import_metadata"]["old_address_id"]: r["icon"] for r in plan.rows_to_insert}
    assert icons_by_old_id == {"addr-earlier": "home", "addr-later": "location"}


def test_two_home_rows_for_different_users_both_stay_home(monkeypatch):
    _install(
        monkeypatch, store={"users": [_rider(), _rider(id="rider-2", phone="+13065550102")], "saved_addresses": []}
    )
    rows = [
        _address_row(_id="addr-1", customer_id="mongo-cust-1", name="111 First Street, Saskatoon, SK"),
        _address_row(_id="addr-2", customer_id="mongo-cust-2", name="222 Second Street, Saskatoon, SK"),
    ]
    customers = [_customer_row(), _customer_row(_id="mongo-cust-2", phone="3065550102")]
    plan = svc.build_saved_address_import_plan(rows, customers, batch="b1")
    icons_by_user = {r["user_id"]: r["icon"] for r in plan.rows_to_insert}
    assert icons_by_user == {"rider-1": "home", "rider-2": "home"}
    assert plan.downgraded_duplicate_home_work == 0


# ── commit-time race (a write lands between validate and commit) ────────


def test_commit_falls_back_to_row_level_on_home_work_race(monkeypatch):
    """A concurrent write claims this rider's Home between validate and
    commit: the chunk-level insert 23505s, so commit falls back to
    row-by-row -- the other, unrelated row in the same chunk still lands
    instead of the whole chunk being lost."""
    fake = _install(monkeypatch, store={"saved_addresses": []})
    fake.fail_pairs = {("rider-1", "home")}
    plan = svc.SavedAddressImportPlan()
    plan.rows_to_insert.extend(
        [
            {
                "id": "new-addr-1",
                "user_id": "rider-1",
                "name": "Home",
                "address": "111 First Street",
                "lat": 52.1,
                "lng": -106.6,
                "icon": "home",
                "place_id": None,
                "created_at": "2024-01-01T00:00:00+00:00",
                "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_address_id": "addr-1"},
            },
            {
                "id": "new-addr-2",
                "user_id": "rider-1",
                "name": "Saved Address",
                "address": "222 Second Street",
                "lat": 52.2,
                "lng": -106.7,
                "icon": "location",
                "place_id": None,
                "created_at": "2024-01-01T00:00:00+00:00",
                "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_address_id": "addr-2"},
            },
        ]
    )
    result = svc.commit_saved_address_import_plan(plan)
    assert result.inserted == 1
    assert len(result.race_conflicts) == 1
    assert "rider-1" in result.race_conflicts[0].message
    stored = fake.store["saved_addresses"]
    assert len(stored) == 1
    assert stored[0]["id"] == "new-addr-2"


def test_commit_reraises_a_unique_violation_that_is_not_the_home_work_race(monkeypatch):
    """A 23505 on a non-home/work row isn't the race this fallback exists
    for (e.g. an id collision) -- it must surface, never be swallowed as a
    race_conflict."""
    fake = _install(monkeypatch, store={"saved_addresses": []})
    fake.fail_pairs = {("rider-1", "location")}
    plan = svc.SavedAddressImportPlan()
    plan.rows_to_insert.append(
        {
            "id": "dup-id",
            "user_id": "rider-1",
            "name": "Saved Address",
            "address": "999 Some Street",
            "lat": 52.1,
            "lng": -106.6,
            "icon": "location",
            "place_id": None,
            "created_at": "2024-01-01T00:00:00+00:00",
            "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_address_id": "addr-9"},
        }
    )
    raised = False
    try:
        svc.commit_saved_address_import_plan(plan)
    except _UniqueViolationError:
        raised = True
    assert raised, "expected the unique violation to propagate, not be swallowed"
    assert fake.store["saved_addresses"] == []


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
