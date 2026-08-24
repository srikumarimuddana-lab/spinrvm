"""The admin Settings page must actually be able to save
company_app_download_url — the link behind the rider welcome email's
"Book your first ride" CTA.

Mirrors test_admin_settings_company_logo.py: the update endpoint takes an
explicit Pydantic field list, not a passthrough dict, so a field present in
the UI but missing from that model is silently dropped on save — indistinguishable
from a working control that never persists.
"""

import pytest

from routes.admin.settings import SettingsUpdateRequest as SettingsUpdate
from schemas import AppSettings

pytestmark = [pytest.mark.unit]


def test_request_model_accepts_the_app_download_url():
    parsed = SettingsUpdate(company_app_download_url="https://spinr.onelink.me/book")
    assert parsed.model_dump(exclude_none=True)["company_app_download_url"] == "https://spinr.onelink.me/book"


def test_request_model_omits_it_when_untouched():
    # exclude_none=True is how a partial save avoids clobbering other fields.
    assert "company_app_download_url" not in SettingsUpdate().model_dump(exclude_none=True)


def test_clearing_it_back_to_blank_is_savable():
    # Blank is the normal unconfigured state — it means "no CTA button" — so
    # an admin must be able to get back to it, not just set a URL once.
    assert SettingsUpdate(company_app_download_url="").model_dump(exclude_none=True)["company_app_download_url"] == ""


def test_app_settings_defaults_it_blank():
    assert AppSettings().company_app_download_url == ""
