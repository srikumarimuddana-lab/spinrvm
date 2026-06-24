"""Unit tests for services.marketing_consent — the CASL eligibility gate.

Covers:
- is_eligible: opted-in + not suppressed → True
- is_eligible: no preference row (default) → False (silence ≠ consent)
- is_eligible: opted out → False
- is_eligible: opted in but on marketing_suppressions → False
- is_eligible: email opted in but on transactional email_suppressions → False
- is_eligible: push gated purely on push_opt_in (no suppression lookup)
- set_consent: writes the preference (upsert) AND an append-only audit event
- add_marketing_suppression: idempotent (DuplicateRecordError → False)

Patching: the service does `import db_supabase` (fallback path) and calls
db_supabase.find_one / insert_one / update_one, so we patch those names.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from db_supabase import DuplicateRecordError
    from services import marketing_consent as mc
except ImportError:  # pragma: no cover
    from backend.db_supabase import DuplicateRecordError  # type: ignore
    from backend.services import marketing_consent as mc  # type: ignore

pytestmark = pytest.mark.anyio

_USER = "11111111-1111-1111-1111-111111111111"


def _find_one_for(rows: dict):
    """Build a find_one side-effect that returns rows[table] (or None)."""

    async def _fn(table, filters=None):
        return rows.get(table)

    return _fn


async def test_eligible_when_opted_in_and_not_suppressed():
    rows = {"marketing_preferences": {"user_id": _USER, "email_opt_in": True}}
    with patch("db_supabase.find_one", AsyncMock(side_effect=_find_one_for(rows))):
        assert await mc.is_eligible("email", user_id=_USER, target="a@b.com") is True


async def test_not_eligible_without_preference_row():
    with patch("db_supabase.find_one", AsyncMock(return_value=None)):
        # No row at all → default false → cannot send (CASL: silence ≠ consent).
        assert await mc.is_eligible("email", user_id=_USER, target="a@b.com") is False


async def test_not_eligible_when_opted_out():
    rows = {"marketing_preferences": {"user_id": _USER, "email_opt_in": False}}
    with patch("db_supabase.find_one", AsyncMock(side_effect=_find_one_for(rows))):
        assert await mc.is_eligible("email", user_id=_USER, target="a@b.com") is False


async def test_not_eligible_when_marketing_suppressed():
    rows = {
        "marketing_preferences": {"user_id": _USER, "email_opt_in": True},
        "marketing_suppressions": {"id": "x", "channel": "email", "target": "a@b.com"},
    }
    with patch("db_supabase.find_one", AsyncMock(side_effect=_find_one_for(rows))):
        assert await mc.is_eligible("email", user_id=_USER, target="a@b.com") is False


async def test_not_eligible_when_hard_bounce_on_transactional_list():
    # Opted in, not on marketing list, but a hard bounce/complaint on the
    # transactional email_suppressions list must block marketing too.
    rows = {
        "marketing_preferences": {"user_id": _USER, "email_opt_in": True},
        "email_suppressions": {"id": "y", "email": "a@b.com"},
    }
    with patch("db_supabase.find_one", AsyncMock(side_effect=_find_one_for(rows))):
        assert await mc.is_eligible("email", user_id=_USER, target="a@b.com") is False


async def test_push_eligible_ignores_suppression_lookup():
    rows = {"marketing_preferences": {"user_id": _USER, "push_opt_in": True}}
    with patch("db_supabase.find_one", AsyncMock(side_effect=_find_one_for(rows))):
        # No target needed; push is gated purely on push_opt_in.
        assert await mc.is_eligible("push", user_id=_USER) is True


async def test_set_consent_writes_preference_and_audit_event():
    with (
        patch("db_supabase.update_one", AsyncMock(return_value=None)) as upd,
        patch("db_supabase.insert_one", AsyncMock(return_value=None)) as ins,
    ):
        await mc.set_consent(_USER, "email", True, source="rider_app", consent_version="v3")

    # Preference upserted on user_id with the channel flag + version.
    upd.assert_awaited_once()
    args, kwargs = upd.call_args
    assert args[0] == "marketing_preferences"
    assert args[1] == {"user_id": _USER}
    assert args[2]["email_opt_in"] is True
    assert args[2]["consent_version"] == "v3"
    assert kwargs.get("upsert") is True

    # Append-only audit event recorded.
    ins.assert_awaited_once()
    iargs, _ = ins.call_args
    assert iargs[0] == "marketing_consent_events"
    assert iargs[1]["action"] == "opt_in"
    assert iargs[1]["channel"] == "email"
    assert iargs[1]["source"] == "rider_app"


async def test_opt_out_clears_opted_in_at():
    with (
        patch("db_supabase.update_one", AsyncMock(return_value=None)) as upd,
        patch("db_supabase.insert_one", AsyncMock(return_value=None)),
    ):
        await mc.set_consent(_USER, "sms", False, source="sms_stop")
    payload = upd.call_args[0][2]
    assert payload["sms_opt_in"] is False
    assert payload["sms_opted_in_at"] is None


async def test_add_marketing_suppression_idempotent():
    # First insert succeeds → True.
    with patch("db_supabase.insert_one", AsyncMock(return_value={"id": "1"})):
        assert await mc.add_marketing_suppression("email", "A@B.com", reason="bounce", source="ses") is True
    # Duplicate (unique index) → swallowed → False, never raises.
    with patch("db_supabase.insert_one", AsyncMock(side_effect=DuplicateRecordError("dupe"))):
        assert await mc.add_marketing_suppression("email", "A@B.com", reason="bounce", source="ses") is False
