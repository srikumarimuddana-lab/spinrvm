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


def _run_with(driver, update_one_return, *, availability_v2=False, pause_code="OK"):
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
    from backend.services import driver_availability_service
    pause = AsyncMock(return_value={"code": pause_code, "is_online": True, "accepting_requests": False,
                                   "has_trip": True})
    close_period = AsyncMock()

    with (
        patch("backend.utils.document_expiry.db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.utils.document_expiry.db.update_one", upd),
        patch("backend.utils.document_expiry.send_push_notification", push),
        patch("backend.utils.document_expiry.clear_presence", clearp),
        patch("backend.utils.document_expiry.manager", mgr),
        patch("backend.utils.document_expiry.close_period_for_forced_offline", close_period),
        patch.object(driver_availability_service, "driver_availability_v2_enabled", AsyncMock(return_value=availability_v2)),
        patch.object(driver_availability_service, "pause_driver_for_policy", pause),
    ):
        asyncio.run(de.check_expiring_documents())

    return push, clearp, upd, mgr, pause, close_period


def test_expired_docs_suspend_and_notify_once_when_claim_won():
    driver = {"id": "d1", "user_id": "u1", "license_expiry_date": "2020-01-01T00:00:00+00:00"}
    push, clearp, upd, mgr, _pause, close_period = _run_with(
        driver, update_one_return={"id": "d1", "status": "suspended"}
    )

    upd.assert_awaited_once()  # the status-guarded suspension claim
    assert upd.await_args.args[2] == {"status": "suspended", "is_online": False, "is_available": False}
    push.assert_awaited_once()  # exactly one suspension push
    clearp.assert_awaited_once()
    close_period.assert_awaited_once_with("d1", reason="document_expired")
    mgr.disconnect.assert_called_once_with("driver_u1")


def test_expired_docs_do_not_renotify_when_claim_lost():
    driver = {"id": "d1", "user_id": "u1", "license_expiry_date": "2020-01-01T00:00:00+00:00"}
    push, clearp, upd, mgr, _pause, _close = _run_with(driver, update_one_return=None)

    upd.assert_awaited_once()  # claim attempted...
    push.assert_not_awaited()  # ...but lost -> no duplicate suspension push
    clearp.assert_not_awaited()
    mgr.disconnect.assert_not_called()


def test_expiring_warning_not_sent_when_claim_lost():
    soon = (datetime.now(timezone.utc) + timedelta(days=4, hours=1)).isoformat()
    driver = {"id": "d2", "user_id": "u2", "license_expiry_date": soon}
    push, _clearp, upd, _mgr, _pause, _close = _run_with(driver, update_one_return=None)

    upd.assert_awaited_once()  # the doc_expiry_warned_at CAS
    push.assert_not_awaited()  # another replica already notified this window


def test_expired_docs_v2_keeps_intent_and_uses_scoped_policy_pause():
    driver = {"id": "d1", "user_id": "u1", "license_expiry_date": "2020-01-01T00:00:00+00:00"}
    push, clearp, upd, mgr, pause, close_period = _run_with(
        driver, update_one_return={"id": "d1", "status": "suspended"}, availability_v2=True
    )

    assert upd.await_args.args[2] == {"status": "suspended"}
    pause.assert_awaited_once()
    assert pause.await_args.args == ("u1",)
    assert pause.await_args.kwargs["blocking_statuses"] == {"suspended"}
    clearp.assert_not_awaited()
    close_period.assert_not_awaited()
    mgr.disconnect.assert_not_called()
    push.assert_awaited_once()  # truthful suspension notice remains
    assert pause.await_args.kwargs["request_id"].startswith("document-expiry-d1-")
    assert pause.return_value["has_trip"] is True  # obligation remains available to the socket/history path


def test_expired_docs_v2_suppresses_suspension_notice_if_policy_state_changed():
    driver = {"id": "d1", "user_id": "u1", "license_expiry_date": "2020-01-01T00:00:00+00:00"}
    push, _clearp, _upd, _mgr, pause, _close = _run_with(
        driver,
        update_one_return={"id": "d1", "status": "suspended"},
        availability_v2=True,
        pause_code="POLICY_STATE_CHANGED",
    )

    pause.assert_awaited_once()
    push.assert_not_awaited()
