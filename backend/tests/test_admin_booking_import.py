"""Endpoint tests for the admin legacy-booking import routes.

The service layer (services/booking_import_service.py) talks to Supabase, so we
patch its module-level ``supabase`` with an in-memory fake. log_admin_action is
stubbed to avoid a real audit write.

The money contract itself is covered in test_booking_import_service.py; these
tests cover the HTTP layer: four-file upload handling, the caps, the
super-admin boundary, refuse-without-4xx on a dirty plan, and that validate
never writes.
"""

import csv
import io
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def super_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


@pytest.fixture
def staff_admin_override():
    """A non-super_admin who has somehow passed the router gate.

    Exercises the per-handler re-check, which exists so the guard survives a
    future re-mount under a weaker dependency.
    """
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin_2",
        "role": "admin",
        "modules": ["rides", "users", "drivers"],
    }
    yield
    app.dependency_overrides.pop(get_admin_user, None)


# --- fixtures mirroring the real legacy export shape -----------------------

BOOKING = {
    "_id": "bk-1",
    "booking_id": "CB1234567",
    "booking_status": "completed",
    "customer_id": "cus-1",
    "driver_id": "drv-legacy-1",
    "created_at": "1770197933837",
    "start_ride_at": "1770198000000",
    "complete_delivery_at": "1770198600000",
    "arrived_pickup_loc_at": "1770197950000",
    "pickup_address": "123 Main St, Regina, SK",
    "pickup_lat": "50.4099027",
    "pickup_long": "-104.6554126",
    "drop_address": "456 Broad St, Regina, SK",
    "drop_lat": "50.4059968",
    "drop_long": "-104.6377971",
    "distance_in_km": "2.4",
    "total_amount": "20.53",
    "gst": "0.98",
    "city_fees": "0.30",
    "infrastructure_fees": "0.20",
    "insurance_fees": "0.10",
    "coupon_discount": "0",
    "tip_driver": "0",
    "you_earn": "16.72",
    "payment_method": "card",
}
CUSTOMER = {"_id": "cus-1", "country_code": "1", "phone": "3065551111", "name": "Legacy Rider"}
DRIVER = {"_id": "drv-legacy-1", "country_code": "1", "phone": "3065552222", "name": "Legacy Driver"}
EARNING = {
    "_id": "earn-1",
    "booking_id": "bk-1",
    "amount": "16.72",
    "admin_comission_amount": "3.09",
    "booking_amount": "20.53",
    "earning_type": "salary",
}


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


# --- in-memory Supabase fake ----------------------------------------------


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert = None
        self._update = None
        self._count = None

    def select(self, *_a, **kw):
        self._count = kw.get("count")
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def ilike(self, col, val):
        self._filters.append(("ilike", col, val))
        return self

    def filter(self, col, _op, val):
        self._filters.append(("eq", col, val))
        return self

    def limit(self, _n):
        return self

    def insert(self, rows):
        self._insert = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, values):
        self._update = values
        return self

    @staticmethod
    def _get(row: dict, col: str):
        """Resolve a column, including PostgREST JSONB paths.

        The resume lookup filters on ``legacy_import_metadata->>source``; a
        fake that only does flat key access silently matches nothing, which
        would make a re-import look idempotent-by-accident here while
        duplicating against a real database.
        """
        if "->>" in col:
            base, _, key = col.partition("->>")
            blob = row.get(base.strip()) or {}
            return blob.get(key.strip().strip("'\"")) if isinstance(blob, dict) else None
        return row.get(col)

    def _matching(self):
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if str(self._get(r, col) or "") == str(val)]
            elif op == "in":
                allowed = {str(v) for v in val}
                rows = [r for r in rows if str(self._get(r, col) or "") in allowed]
            elif op == "ilike":
                needle = str(val).strip("%").lower()
                rows = [r for r in rows if needle in str(self._get(r, col) or "").lower()]
        return rows

    def execute(self):
        if self._insert is not None:
            self.store.setdefault(self.table, []).extend(self._insert)
            return _Result(list(self._insert))
        rows = self._matching()
        if self._update is not None:
            for r in rows:
                r.update(self._update)
            return _Result(rows)
        return _Result(rows, count=len(rows) if self._count == "exact" else None)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)

    def rpc(self, _name, params):
        ids = set(params.get("p_driver_ids") or [])
        for drv in self.store.get("drivers", []):
            if drv["id"] in ids:
                drv["total_rides"] = sum(
                    1
                    for r in self.store.get("rides", [])
                    if r.get("driver_id") == drv["id"] and r.get("status") == "completed"
                )
        return _Result([])


def _fresh_store():
    return {
        "service_areas": [{"id": "sa-1", "name": "Saskatoon", "province": "SK"}],
        "vehicle_types": [{"id": "vt-economy", "name": "Economy", "is_active": True}],
        "users": [{"id": "user-1", "phone": "+13065551111"}],
        "drivers": [{"id": "drv-1", "phone": "+13065552222", "total_rides": 0}],
        "rides": [],
        "payouts": [],
    }


def _patches(store):
    return (
        patch("services.booking_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.booking_import.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def _files(bookings=None, customers=None, drivers=None, earnings=None):
    return {
        "bookings_csv": ("bookings.csv", _csv_bytes(bookings or [BOOKING]), "text/csv"),
        "customers_csv": ("customers.csv", _csv_bytes(customers or [CUSTOMER]), "text/csv"),
        "drivers_csv": ("drivers.csv", _csv_bytes(drivers or [DRIVER]), "text/csv"),
        "earnings_csv": ("earnings.csv", _csv_bytes(earnings or [EARNING]), "text/csv"),
    }


def _post(test_client, path, files=None, data=None):
    return test_client.post(
        path,
        files=files if files is not None else _files(),
        data=data or {"service_area_name": "Saskatoon", "vehicle_type_name": "Economy"},
    )


# --- validate --------------------------------------------------------------


def test_validate_clean_export_reports_without_writing(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/validate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["counts"]["rides_planned"] == 1
    assert body["errors"] == []
    # A dry run must not write anything.
    assert store["rides"] == []
    assert store["payouts"] == []


def test_validate_report_carries_no_pii(test_client, super_admin_override):
    """Report items expose row_num/booking_code only — never names or phones."""
    bad = {**BOOKING, "total_amount": "1.00"}  # residual goes negative -> error
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/validate", files=_files(bookings=[bad]))
    body = resp.json()
    assert body["can_commit"] is False
    assert body["errors"], "expected a validation error"
    blob = resp.text
    for pii in ("Legacy Rider", "Legacy Driver", "3065551111", "3065552222", "123 Main St"):
        assert pii not in blob, f"PII leaked into report: {pii}"
    for item in body["errors"]:
        assert set(item) == {"row_num", "booking_code", "field", "message"}


def test_validate_rejects_non_utf8_and_names_the_file(test_client, super_admin_override):
    store = _fresh_store()
    files = _files()
    files["customers_csv"] = ("customers.csv", b"\xff\xfe_id,phone\n", "text/csv")
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/validate", files=files)
    assert resp.status_code == 422
    assert "customers" in resp.json()["detail"]


def test_validate_rejects_empty_file_and_names_it(test_client, super_admin_override):
    store = _fresh_store()
    files = _files()
    files["earnings_csv"] = ("earnings.csv", b"", "text/csv")
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/validate", files=files)
    assert resp.status_code == 422
    assert "driver earnings" in resp.json()["detail"]


def test_validate_rejects_oversize_file(test_client, super_admin_override):
    from routes.admin import booking_import as mod

    store = _fresh_store()
    files = _files()
    files["bookings_csv"] = ("bookings.csv", b"x" * (mod.MAX_CSV_BYTES + 1), "text/csv")
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/validate", files=files)
    assert resp.status_code == 413
    assert "bookings" in resp.json()["detail"]


def test_validate_requires_all_four_files(test_client, super_admin_override):
    store = _fresh_store()
    files = _files()
    del files["earnings_csv"]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/validate", files=files)
    assert resp.status_code == 422


def test_unknown_service_area_is_a_400_without_cli_flag_wording(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/bookings/import/validate",
            data={"service_area_name": "Nowhere", "vehicle_type_name": "Economy"},
        )
    assert resp.status_code == 400
    # An admin UI must not tell an operator to pass a CLI flag.
    assert "--service-area-id" not in resp.json()["detail"]


# --- authorization ---------------------------------------------------------


def test_non_super_admin_is_refused(test_client, staff_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/validate")
    assert resp.status_code == 403
    assert store["rides"] == []


def test_non_super_admin_cannot_commit(test_client, staff_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/commit")
    assert resp.status_code == 403
    assert store["rides"] == []
    assert store["payouts"] == []


# --- commit ----------------------------------------------------------------


def test_commit_writes_rides_payouts_and_recounts(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/commit")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["imported_rides"] == 1
    assert body["offset_payouts"] == 1
    assert len(store["rides"]) == 1
    assert len(store["payouts"]) == 1
    assert store["drivers"][0]["total_rides"] == 1


def test_commit_offset_payout_cancels_imported_earnings(test_client, super_admin_override):
    """The whole point: net payable delta is zero."""
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        _post(test_client, "/api/admin/bookings/import/commit")
    ride = store["rides"][0]
    payable = ride["base_fare"] + ride["distance_fare"] + ride["time_fare"] + ride["tip_amount"]
    # str(Decimal), not float -- payouts.amount is NUMERIC as of migration 331.
    assert round(payable, 2) == round(float(store["payouts"][0]["amount"]), 2)


def test_commit_refuses_dirty_plan_with_200_and_full_report(test_client, super_admin_override):
    """The shared api client discards non-2xx bodies, so a refusal is a 200."""
    bad = {**BOOKING, "total_amount": "1.00"}
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/bookings/import/commit", files=_files(bookings=[bad]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is False
    assert body["errors"]
    assert store["rides"] == []
    assert store["payouts"] == []


def test_commit_is_idempotent_across_reruns(test_client, super_admin_override):
    """Re-sending the same CSVs with the same batch must not duplicate."""
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    data = {
        "service_area_name": "Saskatoon",
        "vehicle_type_name": "Economy",
        "batch": "batch1",
    }
    with p_sb, p_audit:
        first = _post(test_client, "/api/admin/bookings/import/commit", data=data)
        second = _post(test_client, "/api/admin/bookings/import/commit", data=data)
    assert first.json()["committed"] is True
    # Second run finds nothing left to do and refuses rather than duplicating.
    assert second.json()["committed"] is False
    assert len(store["rides"]) == 1
    assert len(store["payouts"]) == 1
    assert store["drivers"][0]["total_rides"] == 1


def test_commit_audits_counts_only(test_client, super_admin_override):
    store = _fresh_store()
    audit = AsyncMock(return_value="audit-1")
    with (
        patch("services.booking_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.booking_import.log_admin_action", audit),
    ):
        _post(test_client, "/api/admin/bookings/import/commit")
    audit.assert_awaited_once()
    args = audit.await_args.args
    assert args[1] == "legacy_booking_import"
    assert args[2] == "rides"
    payload = args[4]
    assert payload["imported_rides"] == 1
    blob = str(payload)
    for pii in ("Legacy Rider", "3065551111", "123 Main St"):
        assert pii not in blob
