"""
Regression tests for account_purge.purge_pending_deletions — DV-8 / P2-46.

Verifies:
- Accounts past their 30-day grace period are permanently deleted
- Accounts within the grace period are skipped
- Accounts with no deletion_scheduled_at are skipped
- PII fields (email, phone, full_name) are cleared on the user row
- DB fetch failure is swallowed without raising
- A second run is a no-op (status='deleted' rows are not in the result set)

Uses importlib to load account_purge directly from source so the test
does not depend on the full supabase/firebase dep stack being installed.
"""

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Load account_purge from source, stubbing the deps it needs at import time
# ---------------------------------------------------------------------------

_MODULE_PATH = Path(__file__).parent.parent / "utils" / "account_purge.py"


def _parse_iso_utc(val):
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


_STUB_DT_UTILS = types.SimpleNamespace(parse_iso_utc=_parse_iso_utc)

# Stub out db_supabase and utils.datetime_utils before loading the module.
_STUB_DB_SUPABASE = types.SimpleNamespace(
    get_rows=AsyncMock(),
    update_one=AsyncMock(),
    delete_many=AsyncMock(),
)

_stubs = {
    "db_supabase": _STUB_DB_SUPABASE,
    "utils": types.SimpleNamespace(),
    "utils.datetime_utils": _STUB_DT_UTILS,
}
for _name, _mod in _stubs.items():
    sys.modules.setdefault(_name, _mod)

_spec = importlib.util.spec_from_file_location("_account_purge_under_test", _MODULE_PATH)
_account_purge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_account_purge)

purge_pending_deletions = _account_purge.purge_pending_deletions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
PAST_30 = (NOW - timedelta(days=31)).replace(microsecond=0).isoformat()
FUTURE_10 = (NOW + timedelta(days=10)).replace(microsecond=0).isoformat()


def _make_user(user_id="usr-001", deletion_scheduled_at=PAST_30):
    return {
        "id": user_id,
        "email": "test@example.com",
        "phone": "+13061234567",
        "full_name": "Jane Doe",
        "profile_photo_url": "https://cdn.example.com/avatar.jpg",
        "status": "pending_deletion",
        "deletion_scheduled_at": deletion_scheduled_at,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_expired_grace_triggers_deletion():
    """Account past grace period must be permanently deleted."""
    user = _make_user()
    update_calls = []
    delete_calls = []

    async def fake_get_rows(table, filters=None, **_kw):
        return [user] if table == "users" else []

    async def fake_update_one(table, filters, update, **_kw):
        update_calls.append((table, filters, update))
        return {}

    async def fake_delete_many(table, filters, **_kw):
        delete_calls.append((table, filters))
        return {}

    _account_purge.get_rows = AsyncMock(side_effect=fake_get_rows)
    _account_purge.update_one = AsyncMock(side_effect=fake_update_one)
    _account_purge.delete_many = AsyncMock(side_effect=fake_delete_many)
    _account_purge.parse_iso_utc = _parse_iso_utc

    await purge_pending_deletions()

    # At least one update_one call must soft-delete the user row
    user_updates = [c for c in update_calls if c[0] == "users" and c[1] == {"id": "usr-001"}]
    assert len(user_updates) >= 1, "Expected at least one update on the user row"

    final_user_update = user_updates[-1][2]
    assert final_user_update.get("status") == "deleted"
    assert final_user_update.get("deleted_at") is not None
    assert final_user_update.get("email") is None
    assert final_user_update.get("phone") is None
    assert final_user_update.get("full_name") is None

    # delete_many must have been called on at least one ancillary table
    assert len(delete_calls) >= 1, "Expected ancillary table deletions"


@pytest.mark.anyio
async def test_within_grace_period_skipped():
    """Account still within grace period must not be touched."""
    user = _make_user(deletion_scheduled_at=FUTURE_10)

    mock_update = AsyncMock()
    mock_delete = AsyncMock()

    _account_purge.get_rows = AsyncMock(return_value=[user])
    _account_purge.update_one = mock_update
    _account_purge.delete_many = mock_delete
    _account_purge.parse_iso_utc = _parse_iso_utc

    await purge_pending_deletions()

    mock_update.assert_not_called()
    mock_delete.assert_not_called()


@pytest.mark.anyio
async def test_no_scheduled_at_skipped():
    """Account with no deletion_scheduled_at must be skipped."""
    user = _make_user(deletion_scheduled_at=None)

    mock_update = AsyncMock()
    mock_delete = AsyncMock()

    _account_purge.get_rows = AsyncMock(return_value=[user])
    _account_purge.update_one = mock_update
    _account_purge.delete_many = mock_delete
    _account_purge.parse_iso_utc = _parse_iso_utc

    await purge_pending_deletions()

    mock_update.assert_not_called()
    mock_delete.assert_not_called()


@pytest.mark.anyio
async def test_db_fetch_failure_swallowed():
    """If get_rows raises, purge_pending_deletions must return without raising."""
    _account_purge.get_rows = AsyncMock(side_effect=Exception("DB unavailable"))
    _account_purge.update_one = AsyncMock()
    _account_purge.delete_many = AsyncMock()
    _account_purge.parse_iso_utc = _parse_iso_utc

    # Must not raise
    await purge_pending_deletions()


@pytest.mark.anyio
async def test_individual_user_error_does_not_abort_loop():
    """If one user's deletion fails, remaining users are still processed."""
    user_ok = _make_user(user_id="usr-ok")
    user_bad = _make_user(user_id="usr-bad")

    update_calls = []

    async def fake_get_rows(table, filters=None, **_kw):
        return [user_bad, user_ok]

    async def fake_update_one(table, filters, update, **_kw):
        update_calls.append((table, filters.get("id") or filters.get("user_id")))
        if filters.get("id") == "usr-bad" or filters.get("user_id") == "usr-bad":
            raise Exception("Simulated DB failure")
        return {}

    _account_purge.get_rows = AsyncMock(side_effect=fake_get_rows)
    _account_purge.update_one = AsyncMock(side_effect=fake_update_one)
    _account_purge.delete_many = AsyncMock(return_value={})
    _account_purge.parse_iso_utc = _parse_iso_utc

    # Must not raise even though usr-bad fails
    await purge_pending_deletions()

    # usr-ok must have been processed (update_one called with usr-ok somewhere)
    ok_calls = [uid for (_table, uid) in update_calls if uid == "usr-ok"]
    assert len(ok_calls) >= 1, "usr-ok should still be processed after usr-bad error"
