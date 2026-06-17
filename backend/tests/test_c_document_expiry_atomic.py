"""Replay-safety tests for document_expiry (F6): atomic suspension + warn claims.

The suspension UPDATE and the warning throttle were unconditional / read-then-
write, so on multiple replicas every replica re-suspended and re-sent the
"account suspended" push each 12h tick, and warning pushes duplicated. The
suspension is now its own atomic claim (status != 'suspended') and the warning
is a compare-and-swap on doc_expiry_warned_at; only the winning replica notifies.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _run_with(driver, update_one_return):
    """Run check_expiring_documents with one driver; return the patched mocks."""
    from backend.utils import document_expiry as de

    def _get_rows(table, filters=None, limit=None, offset=0, **kw):
        if table == "drivers":
            return [driver] if offset == 0 else []
        return []  # driver_documents

    push = AsyncMock()
    clearp = AsyncMock()
    upd = AsyncMock(return_value=update_one_return)
    mgr = MagicMock()

    with (
        patch("backend.utils.document_expiry.db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.utils.document_expiry.db.update_one", upd),
        patch("backend.utils.document_expiry.send_push_notification", push),
        patch("backend.utils.document_expiry.clear_presence", clearp),
        patch("backend.utils.document_expiry.manager", mgr),
    ):
        asyncio.run(de.check_expiring_documents())

    return push, clearp, upd, mgr


def test_expired_docs_suspend_and_notify_once_when_claim_won():
    driver = {"id": "d1", "user_id": "u1", "license_expiry_date": "2020-01-01T00:00:00+00:00"}
    push, clearp, upd, mgr = _run_with(driver, update_one_return={"id": "d1", "status": "suspended"})

    upd.assert_awaited_once()  # the status-guarded suspension claim
    push.assert_awaited_once()  # exactly one suspension push
    clearp.assert_awaited_once()
    mgr.disconnect.assert_called_once_with("driver_u1")


def test_expired_docs_do_not_renotify_when_claim_lost():
    driver = {"id": "d1", "user_id": "u1", "license_expiry_date": "2020-01-01T00:00:00+00:00"}
    push, clearp, upd, mgr = _run_with(driver, update_one_return=None)

    upd.assert_awaited_once()  # claim attempted...
    push.assert_not_awaited()  # ...but lost -> no duplicate suspension push
    clearp.assert_not_awaited()
    mgr.disconnect.assert_not_called()


def test_expiring_warning_not_sent_when_claim_lost():
    soon = (datetime.now(timezone.utc) + timedelta(days=4, hours=1)).isoformat()
    driver = {"id": "d2", "user_id": "u2", "license_expiry_date": soon}
    push, _clearp, upd, _mgr = _run_with(driver, update_one_return=None)

    upd.assert_awaited_once()  # the doc_expiry_warned_at CAS
    push.assert_not_awaited()  # another replica already notified this window
