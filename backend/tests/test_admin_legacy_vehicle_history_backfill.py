"""Endpoint tests for the admin legacy vehicle-history backfill routes
(routes/admin/legacy_vehicle_history_backfill.py, Phase 2 of the
2026-08-27 migration plan).

Mirrors test_admin_legacy_driver_import.py's structure/fake-Supabase harness
for a two-CSV input instead of one. The service layer
(services/driver_import_service.py) talks to Supabase, so we patch its
module-level ``supabase`` with an in-memory fake. log_admin_action is
stubbed to avoid a real audit write.

The matching/diffing logic itself (plan_legacy_vehicle_history_backfill,
apply_legacy_vehicle_history_backfill) is exercised elsewhere at the service
layer -- these tests cover the route wrapping: two-file upload, the
combined-hash commit-token binding, and the validate/commit contract.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def super_admin_override():
    """Admin routes here sit behind require_module("drivers"); a super_admin
    passes that gate regardless of the modules claim."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


# vehicle_details.csv shape: _id (old_vehicle_id), driver_id (Mongo ObjectId
# crosswalk key), then the TRACKED_VEHICLE_FIELDS source columns, then
# created_at (epoch ms).
VEHICLE_HEADER = "_id,driver_id,name,model,color,year,number,vin,created_at"
GOOD_VEHICLE_ROW = "OV-1,OD-1,Toyota,Corolla,Red,2020,ABC123,1HGCM82633A004352,1700000000000"
UNPARSEABLE_CREATED_AT_ROW = "OV-2,OD-1,Toyota,Corolla,Red,2020,ABC123,1HGCM82633A004352,not-a-timestamp"

# drivers.csv shape: the raw Mongo-export crosswalk, same as Phase 1's own
# test fixture -- only used here to resolve driver_id -> phone.
DRIVERS_HEADER = "_id,name,phone,email,ratings,created_at,is_deleted,is_block,set_up_profile"
GOOD_DRIVERS_ROW = "OD-1,Jane Doe,3065551234,jane@example.com,4.5,1700000000000,false,false,true"
OTHER_DRIVERS_ROW = "OD-2,John Roe,3065559999,john@example.com,4.0,1700000000000,false,false,true"


def _vehicle_csv(*rows: str) -> bytes:
    return ("\n".join([VEHICLE_HEADER, *rows]) + "\n").encode()


def _drivers_csv(*rows: str) -> bytes:
    return ("\n".join([DRIVERS_HEADER, *rows]) + "\n").encode()


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert = None
        self._update = None

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
            return _Result(list(self._insert))
        if self._update is not None:
            matched = self._matched()
            for row in matched:
                row.update(self._update)
            return _Result(matched)
        return _Result(self._matched())


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _fresh_store():
    return {
        "drivers": [
            {
                "id": "driver-1",
                "phone": "+13065551234",
                "legacy_import_metadata": {"source": "legacy_saskatoon_driver_import"},
            }
        ],
        "driver_vehicle_history": [],
    }


def _patches(store):
    return (
        patch("services.driver_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.legacy_vehicle_history_backfill.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def _files(vehicle_bytes: bytes, drivers_bytes: bytes):
    return {
        "vehicle_details_csv": ("vehicle_details.csv", vehicle_bytes, "text/csv"),
        "drivers_csv": ("drivers.csv", drivers_bytes, "text/csv"),
    }


def _post(test_client, path, vehicle_bytes, drivers_bytes, data=None):
    return test_client.post(path, files=_files(vehicle_bytes, drivers_bytes), data=data or {})


def _validate_then_commit(test_client, vehicle_bytes, drivers_bytes):
    validate_resp = _post(
        test_client, "/api/admin/legacy-drivers/vehicle-history-backfill/validate", vehicle_bytes, drivers_bytes
    )
    assert validate_resp.status_code == 200, validate_resp.text
    report = validate_resp.json()
    return test_client.post(
        "/api/admin/legacy-drivers/vehicle-history-backfill/commit",
        files=_files(vehicle_bytes, drivers_bytes),
        data={"batch": report["batch"], "validation_token": report["validation_token"]},
    )


def test_validate_clean_csvs_no_writes(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/legacy-drivers/vehicle-history-backfill/validate",
            _vehicle_csv(GOOD_VEHICLE_ROW),
            _drivers_csv(GOOD_DRIVERS_ROW),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    # All 6 TRACKED_VEHICLE_FIELDS are non-blank on the one legacy row, and
    # there's no prior known value, so every field is a "change" worth logging.
    assert body["counts"]["history_rows_to_insert"] == 6
    assert body["counts"]["vehicle_rows"] == 1
    assert body["errors"] == []
    assert "validation_token" in body
    # Validate must not write anything.
    assert store["driver_vehicle_history"] == []


def test_commit_creates_history_rows(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(test_client, _vehicle_csv(GOOD_VEHICLE_ROW), _drivers_csv(GOOD_DRIVERS_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["history_rows_inserted"] == 6
    assert len(store["driver_vehicle_history"]) == 6
    for row in store["driver_vehicle_history"]:
        assert row["driver_id"] == "driver-1"
        assert row["changed_by_role"] == "system"
        # Append-only report row never carries a raw plate/VIN key -- only
        # driver_id/field/old_value/new_value/created_at/changed_by_*.
        assert set(row.keys()) == {
            "driver_id",
            "changed_by_user_id",
            "changed_by_role",
            "field",
            "old_value",
            "new_value",
            "created_at",
        }


def test_commit_skips_non_legacy_driver_as_warning(test_client, super_admin_override):
    """A phone match on a driver never tagged as legacy-imported is skipped
    with a warning, not written -- a phone coincidence must never touch an
    organic driver's vehicle history."""
    store = _fresh_store()
    store["drivers"][0]["legacy_import_metadata"] = {}
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(test_client, _vehicle_csv(GOOD_VEHICLE_ROW), _drivers_csv(GOOD_DRIVERS_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["history_rows_inserted"] == 0
    assert store["driver_vehicle_history"] == []


def test_commit_refuses_on_unparseable_created_at(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(
            test_client, _vehicle_csv(UNPARSEABLE_CREATED_AT_ROW), _drivers_csv(GOOD_DRIVERS_ROW)
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is False
    assert body["errors"]
    assert store["driver_vehicle_history"] == []


def test_commit_without_validation_token_is_422(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/legacy-drivers/vehicle-history-backfill/commit",
            _vehicle_csv(GOOD_VEHICLE_ROW),
            _drivers_csv(GOOD_DRIVERS_ROW),
        )
    assert resp.status_code == 422, resp.text


def test_commit_with_swapped_vehicle_csv_is_400(test_client, super_admin_override):
    """The commit token binds sha256(vehicle_bytes + '|' + drivers_bytes) --
    swapping just the vehicle_details.csv between validate and commit must
    still be caught, not only a swapped drivers.csv."""
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        validate_resp = _post(
            test_client,
            "/api/admin/legacy-drivers/vehicle-history-backfill/validate",
            _vehicle_csv(GOOD_VEHICLE_ROW),
            _drivers_csv(GOOD_DRIVERS_ROW),
        )
        assert validate_resp.status_code == 200, validate_resp.text
        report = validate_resp.json()
        resp = test_client.post(
            "/api/admin/legacy-drivers/vehicle-history-backfill/commit",
            files=_files(_vehicle_csv(UNPARSEABLE_CREATED_AT_ROW), _drivers_csv(GOOD_DRIVERS_ROW)),
            data={"batch": report["batch"], "validation_token": report["validation_token"]},
        )
    assert resp.status_code == 400, resp.text
    assert store["driver_vehicle_history"] == []


def test_commit_with_swapped_drivers_csv_is_400(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        validate_resp = _post(
            test_client,
            "/api/admin/legacy-drivers/vehicle-history-backfill/validate",
            _vehicle_csv(GOOD_VEHICLE_ROW),
            _drivers_csv(GOOD_DRIVERS_ROW),
        )
        assert validate_resp.status_code == 200, validate_resp.text
        report = validate_resp.json()
        resp = test_client.post(
            "/api/admin/legacy-drivers/vehicle-history-backfill/commit",
            files=_files(_vehicle_csv(GOOD_VEHICLE_ROW), _drivers_csv(OTHER_DRIVERS_ROW)),
            data={"batch": report["batch"], "validation_token": report["validation_token"]},
        )
    assert resp.status_code == 400, resp.text
    assert store["driver_vehicle_history"] == []


def test_row_limit_enforced_on_vehicle_details_csv(test_client, super_admin_override):
    store = _fresh_store()
    rows = [f"OV-{i},OD-1,Toyota,Corolla,Red,2020,ABC{i:03d},VIN{i:013d},1700000000000" for i in range(5001)]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/legacy-drivers/vehicle-history-backfill/validate",
            _vehicle_csv(*rows),
            _drivers_csv(GOOD_DRIVERS_ROW),
        )
    assert resp.status_code == 422, resp.text


def test_requires_admin_auth(test_client):
    # No admin_override fixture -> the router-level get_admin_user gate rejects.
    resp = _post(
        test_client,
        "/api/admin/legacy-drivers/vehicle-history-backfill/validate",
        _vehicle_csv(GOOD_VEHICLE_ROW),
        _drivers_csv(GOOD_DRIVERS_ROW),
    )
    assert resp.status_code in (401, 403)
