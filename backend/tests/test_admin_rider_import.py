"""Endpoint tests for the admin bulk rider-import routes.

Mirrors test_admin_driver_import.py's pattern: the service layer
(services/rider_import_service.py) talks to Supabase directly, so we patch
its module-level ``supabase`` with an in-memory fake. log_admin_action is
stubbed to avoid a real audit write.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def super_admin_override():
    """rider_import router sits behind require_module("users")."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


CSV_HEADER = "phone,email,customer_id,gender,ratings,name"
GOOD_ROW = "3065551234,jane@example.com,cus_abc123,female,5,Jane Doe"


def _csv(*rows: str) -> bytes:
    return ("\n".join([CSV_HEADER, *rows]) + "\n").encode()


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

    def insert(self, rows):
        self._insert = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, fields):
        self._update = fields
        return self

    def execute(self):
        if self._insert is not None:
            self.store.setdefault(self.table, []).extend(self._insert)
            return _Result(list(self._insert))
        if self._update is not None:
            rows = self.store.get(self.table, [])
            for op, col, val in self._filters:
                if op == "eq":
                    for r in rows:
                        if r.get(col) == val:
                            r.update(self._update)
            return _Result([])
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "in":
                allowed = set(val)
                rows = [r for r in rows if r.get(col) in allowed]
        return _Result(rows)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _fresh_store():
    return {"users": [], "drivers": []}


def _patches(store):
    return (
        patch("services.rider_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.rider_import.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def _post(test_client, path, csv_bytes):
    return test_client.post(
        path,
        files={"riders_csv": ("riders.csv", csv_bytes, "text/csv")},
        data={"batch": "batch-1"},
    )


def test_validate_clean_csv(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["counts"]["to_create"] == 1
    assert body["counts"]["to_update"] == 0
    assert body["errors"] == []
    # Validate must not write anything.
    assert store["users"] == []


def test_validate_reports_missing_phone_column(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    bad_csv = b"email,name\njane@example.com,Jane Doe\n"
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", bad_csv)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is False
    assert any(e["field"] == "phone" for e in body["errors"])


def test_validate_reports_invalid_phone_format(test_client, super_admin_override):
    store = _fresh_store()
    bad = "12345,jane@example.com,cus_abc123,female,5,Jane Doe"
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", _csv(bad))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is False
    assert any(e["field"] == "phone" for e in body["errors"])


def test_validate_reports_duplicate_phone_within_csv(test_client, super_admin_override):
    store = _fresh_store()
    dupe = "3065551234,jane2@example.com,,,,Jane Two"
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", _csv(GOOD_ROW, dupe))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is False
    assert any(e["field"] == "phone" and "duplicate" in e["message"] for e in body["errors"])


def test_validate_warns_on_bad_stripe_customer_id_format(test_client, super_admin_override):
    store = _fresh_store()
    bad_cus = "3065551234,jane@example.com,not-a-stripe-id,female,5,Jane Doe"
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", _csv(bad_cus))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert any(w["field"] == "customer_id" for w in body["warnings"])


def test_validate_flags_existing_user_as_duplicate(test_client, super_admin_override):
    store = _fresh_store()
    store["users"].append(
        {
            "id": "existing-1",
            "phone": "+13065551234",
            "email": "old@example.com",
            "is_rider": True,
            "is_driver": False,
            "stripe_customer_id": None,
        }
    )
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["to_create"] == 0
    assert body["counts"]["to_update"] == 1
    assert body["counts"]["duplicates"] == 1
    assert body["duplicates"][0]["match_type"] == "rider"


def test_validate_flags_existing_driver_as_duplicate_driver_match(test_client, super_admin_override):
    store = _fresh_store()
    store["users"].append(
        {
            "id": "existing-2",
            "phone": "+13065551234",
            "email": None,
            "is_rider": False,
            "is_driver": True,
            "stripe_customer_id": None,
        }
    )
    store["drivers"].append({"id": "drv-1", "phone": "+13065551234", "name": "Jane Driver", "user_id": "existing-2"})
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["duplicate_drivers"] == 1
    assert body["duplicates"][0]["match_type"] == "driver"


def test_commit_creates_rows(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/commit", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["created_users"] == 1
    assert body["updated_users"] == 0
    assert len(store["users"]) == 1
    assert store["users"][0]["phone"] == "+13065551234"
    assert store["users"][0]["is_rider"] is True


def test_commit_stamps_provenance_on_created_user(test_client, super_admin_override):
    """P0-A (docs/audit/2026-08-11-driver-rider-migration-audit.md): every
    other importer stamps legacy_import_metadata on rows it touches; the
    rider CSV importer must too, so an admin can answer "which rows did
    import batch X create."""
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/commit", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    meta = store["users"][0]["legacy_import_metadata"]["rider_csv_import"]
    assert meta["batch"] == "batch-1"
    assert meta["source"] == "legacy_rider_csv_import"
    assert meta["imported_at"]


def test_commit_stamps_provenance_on_updated_user_without_clobbering_other_metadata(test_client, super_admin_override):
    """The same users.legacy_import_metadata column is shared with
    stripe_mapping_import_service (a `stripe_migration` sub-key on the same
    row) — writing rider-import provenance must merge, not overwrite."""
    store = _fresh_store()
    store["users"].append(
        {
            "id": "existing-1",
            "phone": "+13065551234",
            "email": None,
            "is_rider": False,
            "is_driver": False,
            "stripe_customer_id": None,
            "legacy_import_metadata": {"stripe_migration": {"batch": "stripe-batch-9", "old_stripe_customer_id": "x"}},
        }
    )
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/commit", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    meta = store["users"][0]["legacy_import_metadata"]
    assert meta["stripe_migration"] == {"batch": "stripe-batch-9", "old_stripe_customer_id": "x"}
    assert meta["rider_csv_import"]["batch"] == "batch-1"
    assert meta["rider_csv_import"]["source"] == "legacy_rider_csv_import"


def test_commit_preserves_ratings_temp_email_timezone_as_history(test_client, super_admin_override):
    """ratings/temp_email/timezone have no live `users` column (checked:
    only `service_areas`/corporate tables have `timezone`, only `drivers`
    has a ratings-shaped column) -- they must not be silently parsed and
    dropped. Preserved read-only under legacy_import_metadata.rider_csv_import,
    same "history, not a live field" pattern as driver_import_service.py's
    was_deleted_in_source/was_blocked_in_source."""
    header = "phone,email,customer_id,gender,ratings,temp_email,timezone,name"
    row = "3065551234,jane@example.com,cus_abc123,female,5,old@example.com,America/Regina,Jane Doe"
    csv_bytes = f"{header}\n{row}\n".encode()
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post(
            "/api/admin/riders/import/commit",
            files={"riders_csv": ("riders.csv", csv_bytes, "text/csv")},
            data={"batch": "batch-1"},
        )
    assert resp.status_code == 200, resp.text
    created = store["users"][0]
    meta = created["legacy_import_metadata"]["rider_csv_import"]
    assert meta["ratings_raw"] == "5"
    assert meta["temp_email"] == "old@example.com"
    assert meta["timezone"] == "America/Regina"
    # Never promoted to a live column -- no such column exists on `users`.
    assert "ratings" not in created
    assert "timezone" not in created
    assert "temp_email" not in created


def test_commit_update_triggers_on_history_only_change(test_client, super_admin_override):
    """An existing user with everything else already set (email present,
    already a rider, no customer_id change) would otherwise have nothing to
    update -- but new temp_email/timezone history is still real information
    worth recording, not silently dropped just because no live field moved."""
    # gender/ratings left BLANK -- gender, if present, always lands in
    # update_fields regardless of whether it changed (see build_plan), which
    # would trigger an update on its own and defeat the point of this test.
    header = "phone,email,customer_id,gender,ratings,temp_email,timezone,name"
    row = "3065551234,jane@example.com,cus_abc123,,,old@example.com,America/Regina,Jane Doe"
    csv_bytes = f"{header}\n{row}\n".encode()
    store = _fresh_store()
    store["users"].append(
        {
            "id": "existing-1",
            "phone": "+13065551234",
            "email": "jane@example.com",
            "is_rider": True,
            "is_driver": False,
            "stripe_customer_id": "cus_abc123",
            "legacy_import_metadata": {},
        }
    )
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post(
            "/api/admin/riders/import/commit",
            files={"riders_csv": ("riders.csv", csv_bytes, "text/csv")},
            data={"batch": "batch-1"},
        )
    assert resp.status_code == 200, resp.text
    meta = store["users"][0]["legacy_import_metadata"]["rider_csv_import"]
    assert meta["temp_email"] == "old@example.com"
    assert meta["timezone"] == "America/Regina"
    assert "ratings_raw" not in meta  # blank in the CSV -- not written


def test_validate_skips_pii_update_for_pending_deletion_account(test_client, super_admin_override):
    """P0-C (docs/audit/2026-08-11-driver-rider-migration-audit.md): a
    phone-matched account mid-PIPEDA-deletion must not have its falsy
    (scrubbed) email/PII fields silently repopulated by a bulk import."""
    store = _fresh_store()
    store["users"].append(
        {
            "id": "existing-pending-del",
            "phone": "+13065551234",
            "email": None,  # scrubbed by migration 296's 30-day PIPEDA purge
            "is_rider": True,
            "is_driver": False,
            "stripe_customer_id": None,
            "status": "pending_deletion",
        }
    )
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["to_update"] == 0
    assert body["counts"]["protected_skips"] == 1
    assert body["duplicates"][0]["match_type"] == "protected_skip"
    assert any("pending_deletion" in w["message"] for w in body["warnings"])


def test_commit_does_not_modify_pii_for_deleted_account(test_client, super_admin_override):
    store = _fresh_store()
    store["users"].append(
        {
            "id": "existing-deleted",
            "phone": "+13065551234",
            "email": None,
            "is_rider": False,
            "is_driver": False,
            "stripe_customer_id": None,
            "status": "deleted",
        }
    )
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/commit", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["updated_users"] == 0
    # No field on the existing row was touched — the account's PII stays
    # exactly as scrubbed.
    assert store["users"][0] == {
        "id": "existing-deleted",
        "phone": "+13065551234",
        "email": None,
        "is_rider": False,
        "is_driver": False,
        "stripe_customer_id": None,
        "status": "deleted",
    }


def test_commit_updates_existing_user(test_client, super_admin_override):
    store = _fresh_store()
    store["users"].append(
        {
            "id": "existing-1",
            "phone": "+13065551234",
            "email": None,
            "is_rider": False,
            "is_driver": False,
            "stripe_customer_id": None,
        }
    )
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/commit", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["updated_users"] == 1
    assert store["users"][0]["is_rider"] is True
    assert store["users"][0]["stripe_customer_id"] == "cus_abc123"


def test_commit_refuses_on_errors_and_writes_nothing(test_client, super_admin_override):
    store = _fresh_store()
    bad_csv = b"email,name\njane@example.com,Jane Doe\n"
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/commit", bad_csv)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is False
    assert body["errors"]
    assert store["users"] == []


def test_commit_maps_service_exception_to_502(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with (
        p_sb,
        p_audit,
        patch("routes.admin.rider_import.import_svc.commit_plan", side_effect=RuntimeError("db write failed")),
    ):
        resp = _post(test_client, "/api/admin/riders/import/commit", _csv(GOOD_ROW))
    assert resp.status_code == 502, resp.text


def test_row_limit_enforced(test_client, super_admin_override):
    store = _fresh_store()
    rows = [f"306555{i:04d},u{i}@example.com,,,,'Name {i}'" for i in range(501)]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", _csv(*rows))
    assert resp.status_code == 422, resp.text


def test_csv_size_limit_enforced(test_client, super_admin_override):
    store = _fresh_store()
    big_row = "3065551234,jane@example.com,,,," + ("x" * 1_000_100)
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", _csv(big_row))
    assert resp.status_code == 413, resp.text


def test_requires_admin_auth(test_client):
    # No admin override → the router-level get_admin_user gate rejects.
    resp = _post(test_client, "/api/admin/riders/import/validate", _csv(GOOD_ROW))
    assert resp.status_code in (401, 403)


def test_empty_csv_reports_error(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/validate", b"phone\n")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is False
    assert body["counts"]["rows"] == 0
    assert any(e["field"] == "csv" for e in body["errors"])


# --- 2026-08-30: created_at legacy-date fix + backfill -----------------
#
# build_plan() previously hardcoded new users' created_at to import time
# instead of the CSV's own legacy value, so migrated riders showed the
# import date as their "Joined" date on the admin Users page. See
# docs/change-log/2026-08-30-rider-created-at-legacy-date-fix.md.

CREATED_AT_HEADER = "phone,email,customer_id,gender,ratings,name,created_at"
# 1700000000000 ms -> 2023-11-14T22:13:20+00:00
LEGACY_EPOCH_MS = "1700000000000"
LEGACY_ISO = "2023-11-14T22:13:20+00:00"


def _csv_with_created_at(*rows: str) -> bytes:
    return ("\n".join([CREATED_AT_HEADER, *rows]) + "\n").encode()


def test_commit_uses_legacy_created_at_when_csv_has_one(test_client, super_admin_override):
    store = _fresh_store()
    row = f"3065551234,jane@example.com,cus_abc123,female,5,Jane Doe,{LEGACY_EPOCH_MS}"
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post(
            "/api/admin/riders/import/commit",
            files={"riders_csv": ("riders.csv", _csv_with_created_at(row), "text/csv")},
            data={"batch": "batch-1"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is True
    assert len(store["users"]) == 1
    assert store["users"][0]["created_at"] == LEGACY_ISO


def test_commit_falls_back_to_now_without_created_at_column(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/riders/import/commit", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is True
    created_at = store["users"][0]["created_at"]
    # Not the fixed legacy date -- genuinely import-time, just asserting it's
    # a real recent ISO timestamp rather than pinning an exact "now" value.
    assert created_at != LEGACY_ISO
    assert created_at.startswith("20")


def test_created_at_backfill_dry_run_reports_without_writing(test_client, super_admin_override):
    store = _fresh_store()
    store["users"] = [
        {"id": "u-1", "phone": "+13065551234", "created_at": "2026-08-30T00:00:00+00:00", "is_rider": True},
    ]
    p_sb, p_audit = _patches(store)
    row = f"3065551234,,,,,,{LEGACY_EPOCH_MS}"
    with p_sb, p_audit:
        resp = test_client.post(
            "/api/admin/riders/created-at-backfill",
            files={"riders_csv": ("riders.csv", _csv_with_created_at(row), "text/csv")},
            data={"apply": "false"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] is False
    assert body["fixed"] == 1
    # Dry run must not write.
    assert store["users"][0]["created_at"] == "2026-08-30T00:00:00+00:00"


def test_created_at_backfill_apply_fixes_mismatch(test_client, super_admin_override):
    store = _fresh_store()
    store["users"] = [
        {"id": "u-1", "phone": "+13065551234", "created_at": "2026-08-30T00:00:00+00:00", "is_rider": True},
    ]
    p_sb, p_audit = _patches(store)
    row = f"3065551234,,,,,,{LEGACY_EPOCH_MS}"
    with p_sb, p_audit:
        resp = test_client.post(
            "/api/admin/riders/created-at-backfill",
            files={"riders_csv": ("riders.csv", _csv_with_created_at(row), "text/csv")},
            data={"apply": "true"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] is True
    assert body["fixed"] == 1
    assert store["users"][0]["created_at"] == LEGACY_ISO


def test_created_at_backfill_no_mismatch_is_a_noop(test_client, super_admin_override):
    store = _fresh_store()
    store["users"] = [
        {"id": "u-1", "phone": "+13065551234", "created_at": LEGACY_ISO, "is_rider": True},
    ]
    p_sb, p_audit = _patches(store)
    row = f"3065551234,,,,,,{LEGACY_EPOCH_MS}"
    with p_sb, p_audit:
        resp = test_client.post(
            "/api/admin/riders/created-at-backfill",
            files={"riders_csv": ("riders.csv", _csv_with_created_at(row), "text/csv")},
            data={"apply": "true"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["fixed"] == 0


def test_created_at_backfill_skips_non_riders(test_client, super_admin_override):
    store = _fresh_store()
    store["users"] = [
        {"id": "u-1", "phone": "+13065551234", "created_at": "2026-08-30T00:00:00+00:00", "is_rider": False},
    ]
    p_sb, p_audit = _patches(store)
    row = f"3065551234,,,,,,{LEGACY_EPOCH_MS}"
    with p_sb, p_audit:
        resp = test_client.post(
            "/api/admin/riders/created-at-backfill",
            files={"riders_csv": ("riders.csv", _csv_with_created_at(row), "text/csv")},
            data={"apply": "true"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["fixed"] == 0
    assert store["users"][0]["created_at"] == "2026-08-30T00:00:00+00:00"


def test_created_at_backfill_requires_admin_auth(test_client):
    row = f"3065551234,,,,,,{LEGACY_EPOCH_MS}"
    resp = test_client.post(
        "/api/admin/riders/created-at-backfill",
        files={"riders_csv": ("riders.csv", _csv_with_created_at(row), "text/csv")},
        data={"apply": "false"},
    )
    assert resp.status_code in (401, 403)
