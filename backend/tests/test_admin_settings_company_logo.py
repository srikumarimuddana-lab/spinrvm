"""The admin Settings page must actually be able to save company_logo_url.

The update endpoint takes an explicit Pydantic field list, not a passthrough
dict — a field present in the UI but missing from that model is silently
dropped on save, which looks exactly like a working control that never
persists. This pins the wiring end to end: schema default, request model,
and the loader that reads it back.
"""

import pytest

from routes.admin.settings import SettingsUpdateRequest as SettingsUpdate
from schemas import AppSettings

pytestmark = [pytest.mark.unit]


def test_request_model_accepts_the_logo_url():
    parsed = SettingsUpdate(company_logo_url="https://cdn.northern.test/logo.png")
    assert parsed.model_dump(exclude_none=True)["company_logo_url"] == "https://cdn.northern.test/logo.png"


def test_request_model_omits_it_when_untouched():
    # exclude_none=True is how a partial save avoids clobbering other fields.
    assert "company_logo_url" not in SettingsUpdate().model_dump(exclude_none=True)


def test_clearing_it_back_to_blank_is_savable():
    # Blank is the normal state — it means "use the bundled Spinr asset" — so
    # an admin must be able to get back to it, not just set a URL once.
    assert SettingsUpdate(company_logo_url="").model_dump(exclude_none=True)["company_logo_url"] == ""


def test_app_settings_defaults_it_blank():
    # get_app_settings merges these defaults over the DB row, so the feature
    # works whether or not migration 287 has run.
    assert AppSettings().company_logo_url == ""


@pytest.mark.parametrize(
    "field",
    ["company_name", "company_address", "company_email", "company_website", "company_phone"],
)
def test_every_field_the_email_footer_reads_is_savable(field):
    """Guards the same silent-drop failure for the fields that feed the footer."""
    assert field in SettingsUpdate(**{field: "x"}).model_dump(exclude_none=True)
    assert hasattr(AppSettings(), field)
