"""Endpoint tests for the admin legacy SIN/DOB backfill routes
(routes/admin/legacy_sin_dob_backfill.py, Phase 2 of the 2026-08-27
migration plan).

Mirrors test_admin_legacy_driver_import.py's structure for a two-CSV-upload
flow (banks.csv + drivers.csv) instead of one. The service layer
(services/driver_import_service.py) talks to Supabase, so its module-level
``supabase`` is patched with an in-memory fake. log_admin_action is stubbed
to avoid a real audit write.
"""

from unittest.mock import AsyncMock, patch

import pytest

from utils.sin import validate_sin


@pytest.fixture
def super_admin_override():
    """Admin routes here sit behind require_module("drivers"); a super_admin
    passes that gate regardless of the modules claim."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def _mk_valid_sin(prefix: str = "12345678") -> str:
    """Brute-force the Luhn check digit so no real SIN appears in the repo."""
    for d in "0123456789":
        try:
            return validate_sin(prefix + d)
        except ValueError:
            continue
    raise AssertionError("unreachable")


VALID_SIN = _mk_valid_sin()

BANKS_HEADER = "driver_id,sin,date_of_birth"
DRIVERS_HEADER = "_id,name,phone"

OLD_DRIVER_ID = "6923ea32d1bde481895439f4"
PHONE_RAW = "3065551234"
PHONE_NORMALIZED = "+13065551234"


def _banks_csv(*rows: str) -> bytes:
    return ("\n".join([BANKS_HEADER, *rows]) + "\n").encode()


def _drivers_csv(*rows: str) -> bytes:
    return ("\n".join([DRIVERS_HEADER, *rows]) + "\n").encode()


def _good_bank_row(sin: str = VALID_SIN, dob: str = "1992-08-03T00:00:00.000") -> str:
    return f"{OLD_DRIVER_ID},{sin},{dob}"


def _good_driver_row() -> str:
    return f"{OLD_DRIVER_ID},Jane Doe,{PHONE_RAW}"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._is_filters = []
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

    def is_(self, col, _val):
        # apply_legacy_sin_dob_import guards each write with
        # .is_(<column>, "null") -- only rows where that column is still
        # unset match, mirroring the real "IS NULL" semantics.
        self._is_filters.append(col)
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
        for col in self._is_filters:
            rows = [r for r in rows if r.get(col) is None]
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


class _Rpc:
    def __init__(self, params):
        self.params = params

    def execute(self):
        return _Result(f"enc::{self.params.get('plaintext')}")


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)

    def rpc(self, _name, params):
        return _Rpc(params)


def _fresh_store():
    return {"drivers": []}


def _driver(**extra) -> dict:
    return {
        "id": "drv-1",
        "phone": PHONE_NORMALIZED,
        "sin": None,
        "date_of_birth": None,
        "legacy_import_metadata": {"source": "legacy_saskatoon_driver_import"},
        **extra,
    }


def _patches(store):
    return (
        patch("services.driver_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.legacy_sin_dob_backfill.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def _post(test_client, path, banks_bytes, drivers_bytes, **extra_data):
    return test_client.post(
        path,
        files={
            "banks_csv": ("banks.csv", banks_bytes, "text/csv"),
            "drivers_csv": ("drivers.csv", drivers_bytes, "text/csv"),
        },
        data=extra_data,
    )


def _validate_then_commit(test_client, banks_bytes, drivers_bytes):
    validate_resp = _post(
        test_client, "/api/admin/legacy-drivers/sin-dob-backfill/validate", banks_bytes, drivers_bytes
    )
    assert validate_resp.status_code == 200, validate_resp.text
    report = validate_resp.json()
    return test_client.post(
        "/api/admin/legacy-drivers/sin-dob-backfill/commit",
        files={
            "banks_csv": ("banks.csv", banks_bytes, "text/csv"),
            "drivers_csv": ("drivers.csv", drivers_bytes, "text/csv"),
        },
        data={"batch": report["batch"], "validation_token": report["validation_token"]},
    )


def test_validate_matches_and_reports_no_sin_or_dob_in_response(test_client, super_admin_override):
    store = _fresh_store()
    store["drivers"] = [_driver()]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/legacy-drivers/sin-dob-backfill/validate",
            _banks_csv(_good_bank_row()),
            _drivers_csv(_good_driver_row()),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["counts"]["to_update"] == 1
    assert body["errors"] == []
    # PIPEDA: never echo the raw SIN or DOB anywhere in the response.
    assert VALID_SIN not in resp.text
    assert "1992-08-03" not in resp.text
    # Validate must not write anything.
    assert store["drivers"][0]["sin"] is None


def test_commit_writes_encrypted_sin_and_dob(test_client, super_admin_override):
    store = _fresh_store()
    store["drivers"] = [_driver()]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(
            test_client,
            _banks_csv(_good_bank_row()),
            _drivers_csv(_good_driver_row()),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["updated"] == 1
    assert body["conflicts"] == []
    # Never echoed in the commit response either.
    assert VALID_SIN not in resp.text
    assert "1992-08-03" not in resp.text
    stored = store["drivers"][0]
    assert stored["sin"] == f"enc::{VALID_SIN}"  # vault-encrypted, not plaintext
    assert stored["sin_last4"] == VALID_SIN[-4:]
    assert stored["date_of_birth"] == "1992-08-03"
    assert stored["legacy_import_metadata"]["legacy_mongo_banks_sin_dob_import"]["sin_written"] is True


def test_commit_never_clobbers_existing_sin(test_client, super_admin_override):
    """A driver's self-entered SIN always wins over the legacy import — even
    though this row's DOB is still blank and gets backfilled."""
    store = _fresh_store()
    store["drivers"] = [_driver(sin="already-on-file")]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(
            test_client,
            _banks_csv(_good_bank_row()),
            _drivers_csv(_good_driver_row()),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["updated"] == 1  # dob backfilled; sin left alone
    stored = store["drivers"][0]
    assert stored["sin"] == "already-on-file"  # never clobbered
    assert stored["date_of_birth"] == "1992-08-03"


def test_validate_unmatched_phone_is_warning_not_error(test_client, super_admin_override):
    store = _fresh_store()
    store["drivers"] = []  # no matching phone at all
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/legacy-drivers/sin-dob-backfill/validate",
            _banks_csv(_good_bank_row()),
            _drivers_csv(_good_driver_row()),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["counts"]["to_update"] == 0
    assert body["counts"]["skipped_unmatched"] == 1
    assert any(w["old_driver_id"] == OLD_DRIVER_ID for w in body["warnings"])


def test_validate_skips_non_legacy_driver(test_client, super_admin_override):
    """A phone match on a driver with no legacy-import provenance is never
    touched -- a phone coincidence must not leak SIN/DOB onto an organic
    driver."""
    store = _fresh_store()
    store["drivers"] = [_driver(legacy_import_metadata={})]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/legacy-drivers/sin-dob-backfill/validate",
            _banks_csv(_good_bank_row()),
            _drivers_csv(_good_driver_row()),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["to_update"] == 0
    assert body["counts"]["skipped_not_legacy_driver"] == 1


def test_commit_without_validation_token_is_422(test_client, super_admin_override):
    store = _fresh_store()
    store["drivers"] = [_driver()]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/legacy-drivers/sin-dob-backfill/commit",
            _banks_csv(_good_bank_row()),
            _drivers_csv(_good_driver_row()),
        )
    assert resp.status_code == 422, resp.text


def test_commit_with_wrong_token_is_400(test_client, super_admin_override):
    """A token minted for one file pair does not authorize a commit against
    different file contents -- catches a file swap between validate/commit."""
    store = _fresh_store()
    store["drivers"] = [_driver()]
    other_bank_row = _good_bank_row(sin=VALID_SIN, dob="1985-01-01T00:00:00.000")
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        validate_resp = _post(
            test_client,
            "/api/admin/legacy-drivers/sin-dob-backfill/validate",
            _banks_csv(other_bank_row),
            _drivers_csv(_good_driver_row()),
        )
        assert validate_resp.status_code == 200, validate_resp.text
        report = validate_resp.json()
        resp = test_client.post(
            "/api/admin/legacy-drivers/sin-dob-backfill/commit",
            files={
                "banks_csv": ("banks.csv", _banks_csv(_good_bank_row()), "text/csv"),
                "drivers_csv": ("drivers.csv", _drivers_csv(_good_driver_row()), "text/csv"),
            },
            data={"batch": report["batch"], "validation_token": report["validation_token"]},
        )
    assert resp.status_code == 400, resp.text
    assert store["drivers"][0]["sin"] is None


def test_row_limit_enforced(test_client, super_admin_override):
    store = _fresh_store()
    rows = [f"id-{i:06d},{VALID_SIN},1990-01-01T00:00:00.000" for i in range(2001)]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/legacy-drivers/sin-dob-backfill/validate",
            _banks_csv(*rows),
            _drivers_csv(_good_driver_row()),
        )
    assert resp.status_code == 422, resp.text


def test_requires_admin_auth(test_client):
    # No admin_override fixture -> the router-level get_admin_user gate rejects.
    resp = _post(
        test_client,
        "/api/admin/legacy-drivers/sin-dob-backfill/validate",
        _banks_csv(_good_bank_row()),
        _drivers_csv(_good_driver_row()),
    )
    assert resp.status_code in (401, 403)
