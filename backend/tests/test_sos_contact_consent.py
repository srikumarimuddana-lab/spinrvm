"""Unit tests for services.sos_contact_consent — SOS emergency-contact
STOP-opt-out suppression (PIA finding R-002).

Covers:
- normalize_phone: NANP normalization (bare 10-digit and +1 forms agree)
- is_suppressed: found row -> True
- is_suppressed: no row -> False
- is_suppressed: DB error -> False (FAIL-OPEN — the most important test here:
  a bug that flips this to fail-closed would silently drop a real SOS alert)
- suppress: writes a row
- suppress: idempotent on DuplicateRecordError (no crash on repeat STOP)
- unsuppress: hard-deletes the row (matches marketing_suppressions' actual
  un-suppression behaviour, not an append-only audit pattern)

Patching: the service does `import db_supabase` (fallback path, same as
services.marketing_consent) and calls db_supabase.find_one / insert_one /
delete_many, so we patch those names directly — mirroring
test_marketing_consent.py's patch target exactly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from db_supabase import DuplicateRecordError
    from services import sos_contact_consent as sc
except ImportError:  # pragma: no cover
    from backend.db_supabase import DuplicateRecordError  # type: ignore
    from backend.services import sos_contact_consent as sc  # type: ignore

pytestmark = pytest.mark.anyio

_PHONE_BARE = "3065551234"
_PHONE_E164 = "+13065551234"


def test_normalize_phone_bare_and_e164_agree():
    assert sc.normalize_phone(_PHONE_BARE) == _PHONE_E164
    assert sc.normalize_phone(_PHONE_E164) == _PHONE_E164


async def test_is_suppressed_true_when_row_found():
    with patch("db_supabase.find_one", AsyncMock(return_value={"id": "x", "phone": _PHONE_E164})) as fo:
        assert await sc.is_suppressed(_PHONE_E164) is True
    fo.assert_awaited_once_with("sos_contact_suppressions", {"phone": _PHONE_E164})


async def test_is_suppressed_false_when_no_row():
    with patch("db_supabase.find_one", AsyncMock(return_value=None)):
        assert await sc.is_suppressed(_PHONE_E164) is False


async def test_is_suppressed_fails_open_on_db_error():
    """The single most important test in this module: a DB error must NEVER
    suppress a real SOS alert. Fail-open means False (not suppressed)."""
    with patch("db_supabase.find_one", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await sc.is_suppressed(_PHONE_E164) is False


async def test_suppress_writes_row():
    with patch("db_supabase.insert_one", AsyncMock(return_value=None)) as ins:
        await sc.suppress(_PHONE_BARE, reason="sms_stop", source="twilio")
    ins.assert_awaited_once()
    args, _ = ins.call_args
    assert args[0] == "sos_contact_suppressions"
    assert args[1]["phone"] == _PHONE_E164
    assert args[1]["reason"] == "sms_stop"
    assert args[1]["source"] == "twilio"


async def test_suppress_idempotent_on_duplicate():
    with patch("db_supabase.insert_one", AsyncMock(side_effect=DuplicateRecordError("dup"))):
        # Must not raise on a repeat STOP.
        await sc.suppress(_PHONE_E164, reason="sms_stop", source="twilio")


async def test_unsuppress_deletes_row():
    with patch("db_supabase.delete_many", AsyncMock(return_value=None)) as dm:
        await sc.unsuppress(_PHONE_BARE)
    dm.assert_awaited_once_with("sos_contact_suppressions", {"phone": _PHONE_E164})
