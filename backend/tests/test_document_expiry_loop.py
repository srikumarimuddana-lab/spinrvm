"""
Regression tests for document_expiry.check_expiring_documents — P0-5.

Verifies:
- Drivers with already-expired documents are suspended (past-expiry loop bound)
- Suspension update is sent WITHOUT a {"$set": ...} wrapper
- Drivers with documents expiring soon receive a warning notification
- Drivers with valid documents are skipped entirely
- DB failure on driver fetch is swallowed gracefully

Uses importlib to load document_expiry directly from source so the test
does not depend on the full supabase/firebase dep stack being installed.
"""

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load document_expiry from source, stubbing the deps it needs at import time
# ---------------------------------------------------------------------------

_MODULE_PATH = Path(__file__).parent.parent / "utils" / "document_expiry.py"

_STUB_DB = MagicMock()
_STUB_PUSH = AsyncMock()
_STUB_MANAGER = MagicMock()
_STUB_PRESENCE = AsyncMock()
_STUB_DT_UTILS = types.SimpleNamespace(parse_iso_utc=None)  # filled in below


def _parse_iso_utc(val):
    """Minimal ISO-8601 UTC parser (replaces utils.datetime_utils.parse_iso_utc)."""
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


_STUB_DT_UTILS.parse_iso_utc = _parse_iso_utc

# Pre-populate sys.modules with stubs so the module-level `from ..db import db`
# fallback path (`from db import db`) can resolve without the real packages.
_stubs = {
    "db": types.SimpleNamespace(db=_STUB_DB),
    "features": types.SimpleNamespace(send_push_notification=_STUB_PUSH),
    "socket_manager": types.SimpleNamespace(manager=_STUB_MANAGER),
    "utils": types.SimpleNamespace(),
    "utils.datetime_utils": _STUB_DT_UTILS,
    "utils.driver_presence": types.SimpleNamespace(clear_presence=_STUB_PRESENCE),
}
for _name, _mod in _stubs.items():
    sys.modules.setdefault(_name, _mod)

# Load the module fresh into a local name so we can re-patch its globals.
_spec = importlib.util.spec_from_file_location("_doc_expiry_under_test", _MODULE_PATH)
_doc_expiry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_doc_expiry)

check_expiring_documents = _doc_expiry.check_expiring_documents

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
SOON = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
VALID = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()


def _make_driver(license_expiry=None, insurance_expiry=None):
    return {
        "id": "drv-001",
        "user_id": "usr-001",
        "license_expiry_date": license_expiry,
        "insurance_expiry_date": insurance_expiry,
        "vehicle_inspection_expiry_date": None,
        "background_check_expiry_date": None,
        "work_eligibility_expiry_date": None,
        "doc_expiry_warned_at": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_expired_doc_triggers_suspension():
    """P0-5: driver with an already-expired license must be suspended."""
    driver = _make_driver(license_expiry=PAST)
    update_calls = []

    async def fake_get_rows(table, filters=None, **_kw):
        return [driver] if table == "drivers" else []

    async def fake_update_one(table, filters, update, **_kw):
        update_calls.append((table, filters, update))
        return {"id": "drv-001"}

    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(side_effect=fake_get_rows)
    mock_db.update_one = AsyncMock(side_effect=fake_update_one)

    _doc_expiry.db = mock_db
    _doc_expiry.send_push_notification = AsyncMock()
    _doc_expiry.clear_presence = AsyncMock()
    _doc_expiry.manager = MagicMock()
    _doc_expiry.parse_iso_utc = _parse_iso_utc

    await check_expiring_documents()

    suspension_calls = [
        c for c in update_calls if c[0] == "drivers" and c[1] == {"id": "drv-001"} and c[2].get("status") == "suspended"
    ]
    assert len(suspension_calls) >= 1, "Expected a suspension update"

    payload = suspension_calls[0][2]
    # P0-5: payload must NOT be wrapped in {"$set": ...}
    assert "$set" not in payload, "update payload must not use $set wrapper"
    assert payload.get("is_online") is False
    assert payload.get("is_available") is False


@pytest.mark.anyio
async def test_soon_expiring_doc_sends_warning_not_suspension():
    """Driver with doc expiring in 3 days gets a warning, not a suspension."""
    driver = _make_driver(license_expiry=SOON)
    update_calls = []
    notif_calls = []

    async def fake_get_rows(table, filters=None, **_kw):
        return [driver] if table == "drivers" else []

    async def fake_update_one(table, filters, update, **_kw):
        update_calls.append((table, filters, update))
        return {}

    async def fake_push(user_id, title, body, data=None):
        notif_calls.append({"user_id": user_id, "title": title})

    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(side_effect=fake_get_rows)
    mock_db.update_one = AsyncMock(side_effect=fake_update_one)

    _doc_expiry.db = mock_db
    _doc_expiry.send_push_notification = fake_push
    _doc_expiry.clear_presence = AsyncMock()
    _doc_expiry.manager = MagicMock()
    _doc_expiry.parse_iso_utc = _parse_iso_utc

    await check_expiring_documents()

    suspended = [c for c in update_calls if c[2].get("status") == "suspended"]
    assert len(suspended) == 0, "Should not suspend driver with soon-expiring doc"
    assert len(notif_calls) >= 1, "Expected a warning notification"
    assert notif_calls[0]["user_id"] == "usr-001"


@pytest.mark.anyio
async def test_valid_doc_skipped():
    """Driver with all valid docs must not trigger any update or notification."""
    driver = _make_driver(license_expiry=VALID, insurance_expiry=VALID)

    mock_push = AsyncMock()
    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(side_effect=lambda t, **_: [driver] if t == "drivers" else [])
    mock_db.update_one = AsyncMock(return_value={})

    _doc_expiry.db = mock_db
    _doc_expiry.send_push_notification = mock_push
    _doc_expiry.clear_presence = AsyncMock()
    _doc_expiry.manager = MagicMock()
    _doc_expiry.parse_iso_utc = _parse_iso_utc

    await check_expiring_documents()

    assert mock_push.call_count == 0, "Should not notify for valid docs"
    assert mock_db.update_one.call_count == 0, "Should not update for valid docs"


@pytest.mark.anyio
async def test_db_failure_on_fetch_is_swallowed():
    """If the initial DB fetch raises, check_expiring_documents returns without raising."""
    mock_db = MagicMock()
    mock_db.get_rows = AsyncMock(side_effect=Exception("DB down"))

    _doc_expiry.db = mock_db
    _doc_expiry.send_push_notification = AsyncMock()
    _doc_expiry.clear_presence = AsyncMock()
    _doc_expiry.manager = MagicMock()
    _doc_expiry.parse_iso_utc = _parse_iso_utc

    # Must not raise
    await check_expiring_documents()
