"""N17: document-expiry email "next step" copy uses the `company_app_name`
setting ("Upload ... in the {app_name} driver app"), not a literal "Spinr".

Companion to test_document_expiry_email.py, which covers the email
fan-out/replay-safety; this pins the fallback (unconfigured -> "Spinr",
byte-for-byte) and the configured-value path for both the expired-suspension
notice and the expiring-soon warning.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.company_details import CompanyDetails

pytestmark = pytest.mark.unit

_EXPIRED = {"id": "d1", "user_id": "u1", "license_expiry_date": "2020-01-01T00:00:00+00:00"}


def _soon(days):
    from datetime import datetime, timedelta, timezone

    return {
        "id": "d1",
        "user_id": "u1",
        "license_expiry_date": (datetime.now(timezone.utc) + timedelta(days=days, hours=1)).isoformat(),
    }


def _company(app_name):
    return CompanyDetails(
        name="Spinr Technologies Inc.",
        app_name=app_name,
        identity_line="Spinr Technologies Inc. - Saskatoon, SK",
        address="",
        contact_line="support@spinr.ca - www.spinr.ca",
        support_email="support@spinr.ca",
        logo_url="https://example.test/logo.png",
    )


def _run(driver, update_one_return, app_name):
    from backend.utils import document_expiry as de

    def _get_rows(table, filters=None, limit=None, offset=0, **kw):
        if table == "drivers":
            return [driver] if offset == 0 else []
        return []

    email = AsyncMock(return_value=True)

    with (
        patch("backend.utils.document_expiry.db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.utils.document_expiry.db.update_one", AsyncMock(return_value=update_one_return)),
        patch("backend.utils.document_expiry.send_push_notification", AsyncMock()),
        patch("backend.utils.document_expiry.send_lifecycle_email", email),
        patch(
            "backend.utils.document_expiry.resolve_recipient",
            AsyncMock(return_value={"id": "u1", "first_name": "Sam", "email": "sam@example.test"}),
        ),
        patch("backend.utils.document_expiry.clear_presence", AsyncMock()),
        patch("backend.utils.document_expiry.manager", MagicMock()),
        patch("backend.utils.document_expiry.load_company_details", AsyncMock(return_value=_company(app_name))),
    ):
        asyncio.run(de.check_expiring_documents())

    return email


def test_unconfigured_app_name_reproduces_the_literal_spinr_next_step():
    email = _run(_soon(1), update_one_return={"id": "d1"}, app_name="Spinr")
    body = email.await_args.kwargs["rendered"].text
    assert "Upload the renewed document in the Spinr driver app" in body


def test_configured_app_name_replaces_the_literal_in_the_warning_next_step():
    email = _run(_soon(1), update_one_return={"id": "d1"}, app_name="Northern Rides")
    body = email.await_args.kwargs["rendered"].text
    assert "Upload the renewed document in the Northern Rides driver app" in body
    assert "Spinr driver app" not in body


def test_configured_app_name_replaces_the_literal_in_the_suspension_next_step():
    email = _run(_EXPIRED, update_one_return={"id": "d1", "status": "suspended"}, app_name="Northern Rides")
    body = email.await_args.kwargs["rendered"].text
    assert "Upload a current copy in the Northern Rides driver app" in body
    assert "Spinr driver app" not in body
