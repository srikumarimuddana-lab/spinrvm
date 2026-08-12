"""The branded receipt/invoice shell is switchable without a redeploy.

Email rendering is the one thing in this change automated tests genuinely
cannot check — nobody here knows how it looks in Outlook. So the flag is the
real safety net, and these pin its behaviour: on by default, off restores the
previous shell, and a settings failure never costs a receipt.
"""

from unittest.mock import AsyncMock, patch

import pytest

import utils.email_receipt as er
from routes.admin.settings import SettingsUpdateRequest
from schemas import AppSettings

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


async def _company(settings=None, fail=False):
    loader = AsyncMock(side_effect=RuntimeError("db down")) if fail else AsyncMock(return_value=settings or {})
    with (
        patch("settings_loader.get_app_settings", loader),
        patch("utils.company_details.get_app_settings", AsyncMock(return_value=settings or {})),
    ):
        return await er._branded_company()


# --- Default and switching -------------------------------------------------


async def test_defaults_to_the_branded_shell():
    # Shipping dark would leave the known-wrong company details in front of
    # riders, which is the thing the retrofit exists to fix.
    assert AppSettings().branded_receipt_enabled is True
    assert await _company({}) is not None


async def test_false_restores_the_previous_shell():
    assert await _company({"branded_receipt_enabled": False}) is None


async def test_true_renders_branded():
    assert await _company({"branded_receipt_enabled": True}) is not None


# --- Failure behaviour -----------------------------------------------------


async def test_settings_failure_falls_back_to_the_legacy_shell():
    """Fails CLOSED, unlike the lifecycle-email kill switch which fails open.

    Different question: there the risk was silently muting a suspension notice,
    so erring towards sending is right. Here the receipt sends either way and
    the only choice is which shell — so on an unknown, use the one that has
    been in front of riders for months.
    """
    assert await _company(fail=True) is None


async def test_a_receipt_still_renders_with_no_company():
    # The failure path must produce a complete document, not a broken one.
    ride = {"id": "r1", "grand_total": "10.00", "tax_breakdown": {}}
    html = er.generate_receipt_html(ride, {"first_name": "Sam"}, None, company=None)
    assert "<!DOCTYPE html>" in html and "</html>" in html


# --- Admin surface ---------------------------------------------------------


def test_the_flag_is_savable_from_the_settings_page():
    # Same silent-drop failure mode as company_logo_url: absent from the
    # request model means the control exists but never persists.
    parsed = SettingsUpdateRequest(branded_receipt_enabled=False).model_dump(exclude_none=True)
    assert parsed["branded_receipt_enabled"] is False


def test_the_flag_is_omitted_when_untouched():
    assert "branded_receipt_enabled" not in SettingsUpdateRequest().model_dump(exclude_none=True)
