"""Unit tests for the authenticated marketing-preferences endpoints
(routes/marketing.py): partial update writes per-channel consent + audit, and
an opt-out also suppresses the contact.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from routes import marketing as mod
    from routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences
except ImportError:  # pragma: no cover
    from backend.routes import marketing as mod  # type: ignore
    from backend.routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences  # type: ignore

pytestmark = pytest.mark.anyio

_USER = {"id": "66666666-6666-6666-6666-666666666666", "email": "a@b.com", "phone": "+13065551234"}


async def test_partial_update_only_touches_provided_channels():
    with (
        patch("services.marketing_consent.set_consent", AsyncMock(return_value=None)) as setc,
        patch("services.marketing_consent.add_marketing_suppression", AsyncMock(return_value=True)),
        patch("services.marketing_consent.get_preferences", AsyncMock(return_value={"email_opt_in": True})),
    ):
        body = MarketingPreferencesUpdate(email_opt_in=True, source="rider_app")
        await update_marketing_preferences(body, current_user=_USER)

    # Only email was provided → exactly one set_consent call, opting in.
    setc.assert_awaited_once()
    args, kwargs = setc.call_args
    assert args[1] == "email" and args[2] is True
    assert kwargs["source"] == "rider_app"
    assert kwargs["consent_version"] == mod.CONSENT_VERSION


async def test_opt_out_also_suppresses_contact():
    with (
        patch("services.marketing_consent.set_consent", AsyncMock(return_value=None)),
        patch("services.marketing_consent.add_marketing_suppression", AsyncMock(return_value=True)) as supp,
        patch("services.marketing_consent.get_preferences", AsyncMock(return_value={})),
    ):
        body = MarketingPreferencesUpdate(email_opt_in=False, sms_opt_in=False, source="rider_app")
        await update_marketing_preferences(body, current_user=_USER)

    # Both channels opted out → both contacts suppressed.
    channels = {c.args[0] for c in supp.call_args_list}
    targets = {c.args[1] for c in supp.call_args_list}
    assert channels == {"email", "sms"}
    assert targets == {"a@b.com", "+13065551234"}
