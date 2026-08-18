"""Unit tests for the driver CRC/VSC consent service (migration 319).

Mirrors tests/test_admin_legal_documents.py's mocking style — direct
unittest.mock.patch on the db_supabase functions the service calls, no real
DB. See services/driver_crc_consent.py for the PIPEDA rationale.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from services.driver_crc_consent import (
        get_consent_status,
        is_consent_current,
        record_consent,
    )
except ImportError:
    from backend.services.driver_crc_consent import (  # type: ignore[no-redef]
        get_consent_status,
        is_consent_current,
        record_consent,
    )


@pytest.mark.anyio
async def test_get_consent_status_defaults_to_not_consented_when_no_row():
    with patch("services.driver_crc_consent.db_supabase.find_one", AsyncMock(return_value=None)):
        out = await get_consent_status("driver-1")
    assert out == {"driver_id": "driver-1", "consented": False, "consent_version": None, "consented_at": None}


@pytest.mark.anyio
async def test_get_consent_status_returns_existing_row():
    row = {"driver_id": "driver-1", "consented": True, "consent_version": 2, "consented_at": "2026-08-17"}
    with patch("services.driver_crc_consent.db_supabase.find_one", AsyncMock(return_value=row)):
        out = await get_consent_status("driver-1")
    assert out == row


@pytest.mark.anyio
async def test_is_consent_current_true_when_version_matches():
    row = {"driver_id": "driver-1", "consented": True, "consent_version": 3}
    with patch("services.driver_crc_consent.db_supabase.find_one", AsyncMock(return_value=row)):
        assert await is_consent_current("driver-1", 3) is True


@pytest.mark.anyio
async def test_is_consent_current_false_when_version_stale():
    """A material change to the consent text (version bump) must require
    re-consent — an old version on file is not current."""
    row = {"driver_id": "driver-1", "consented": True, "consent_version": 2}
    with patch("services.driver_crc_consent.db_supabase.find_one", AsyncMock(return_value=row)):
        assert await is_consent_current("driver-1", 3) is False


@pytest.mark.anyio
async def test_is_consent_current_false_when_never_consented():
    with patch("services.driver_crc_consent.db_supabase.find_one", AsyncMock(return_value=None)):
        assert await is_consent_current("driver-1", 1) is False


@pytest.mark.anyio
async def test_record_consent_first_time_writes_consent_action():
    update_mock = AsyncMock(return_value=None)
    insert_mock = AsyncMock(return_value=None)
    with (
        patch("services.driver_crc_consent.db_supabase.find_one", AsyncMock(return_value=None)),
        patch("services.driver_crc_consent.db_supabase.update_one", update_mock),
        patch("services.driver_crc_consent.db_supabase.insert_one", insert_mock),
    ):
        await record_consent("driver-1", consent_version=1, source="driver_app")

    update_args = update_mock.await_args
    assert update_args.args[0] == "driver_crc_consents"
    assert update_args.args[1] == {"driver_id": "driver-1"}
    assert update_args.args[2]["consented"] is True
    assert update_args.args[2]["consent_version"] == 1
    assert update_args.kwargs.get("upsert") is True

    insert_args = insert_mock.await_args
    assert insert_args.args[0] == "driver_crc_consent_events"
    event = insert_args.args[1]
    assert event == {
        "driver_id": "driver-1",
        "action": "consent",
        "consent_version": 1,
        "source": "driver_app",
    }


@pytest.mark.anyio
async def test_record_consent_when_already_consented_logs_renew():
    """A driver re-confirming after a consent-version bump is a 'renew'
    event in the audit trail, not a fresh 'consent'."""
    existing = {"driver_id": "driver-1", "consented": True, "consent_version": 1}
    insert_mock = AsyncMock(return_value=None)
    with (
        patch("services.driver_crc_consent.db_supabase.find_one", AsyncMock(return_value=existing)),
        patch("services.driver_crc_consent.db_supabase.update_one", AsyncMock(return_value=None)),
        patch("services.driver_crc_consent.db_supabase.insert_one", insert_mock),
    ):
        await record_consent("driver-1", consent_version=2, source="driver_app")

    assert insert_mock.await_args.args[1]["action"] == "renew"
    assert insert_mock.await_args.args[1]["consent_version"] == 2


@pytest.mark.anyio
async def test_record_consent_rejects_invalid_source():
    with pytest.raises(ValueError, match="invalid source"):
        await record_consent("driver-1", consent_version=1, source="rider_app")
