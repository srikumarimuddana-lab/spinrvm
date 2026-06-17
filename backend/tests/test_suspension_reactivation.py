"""Auto-reactivation loop for expired temporary rider suspensions."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.anyio
async def test_reactivate_tick_flips_expired_suspension():
    from utils.suspension_reactivation import _reactivate_tick

    captured: dict = {}

    async def _update(table, filt, fields):
        captured["table"] = table
        captured["filt"] = filt
        captured["fields"] = fields
        return [{"id": "u1"}]  # truthy → the conditional update "stuck"

    audited: list = []

    async def _insert(table, doc):
        audited.append((table, doc))
        return doc

    with (
        patch(
            "db_supabase.get_rows",
            new_callable=AsyncMock,
            return_value=[{"id": "u1", "suspended_until": "2000-01-01T00:00:00+00:00"}],
        ),
        patch("db_supabase.update_one", new=AsyncMock(side_effect=_update)),
        patch("db_supabase.insert_one", new=AsyncMock(side_effect=_insert)),
    ):
        await _reactivate_tick()

    # Conditional, replay-safe filter + the active flip clearing the timer.
    assert captured["filt"] == {"id": "u1", "status": "suspended"}
    assert captured["fields"]["status"] == "active"
    assert captured["fields"]["suspended_until"] is None
    assert captured["fields"]["status_reason"] is None
    assert captured["fields"]["status_changed_by"] == "system:auto_reactivation"
    # Audited exactly once (only because the update returned a row).
    assert [t for t, _ in audited] == ["audit_logs"]
    assert audited[0][1]["details"]["new_status"] == "active"


@pytest.mark.anyio
async def test_reactivate_tick_skips_audit_when_update_lost_race():
    from utils.suspension_reactivation import _reactivate_tick

    with (
        patch(
            "db_supabase.get_rows",
            new_callable=AsyncMock,
            return_value=[{"id": "u1", "suspended_until": "2000-01-01T00:00:00+00:00"}],
        ),
        # Another replica already flipped it → conditional update matches nothing.
        patch("db_supabase.update_one", new_callable=AsyncMock, return_value=None),
        patch("db_supabase.insert_one", new_callable=AsyncMock) as mock_insert,
    ):
        await _reactivate_tick()

    mock_insert.assert_not_called()
