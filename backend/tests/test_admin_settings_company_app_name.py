"""The admin Settings page must be able to save `company_app_name`.

`company_app_name` is the product/brand name used in email BODY copy ("Open
the {app_name} driver app"), deliberately separate from `company_name` (the
legal entity, e.g. "Spinr Technologies Inc.") which drives the footer,
mailing address, and logo alt text — see `utils/company_details.py` and
ACTION_ITEMS.md N17.

Mirrors `test_admin_settings_company_logo.py`'s schema/request-model/loader
wiring check so the same silent-drop failure (a field present in the UI but
missing from the update request model) cannot recur for this field.
"""

import pytest

from routes.admin.settings import SettingsUpdateRequest as SettingsUpdate
from schemas import AppSettings

pytestmark = [pytest.mark.unit]


def test_request_model_accepts_the_app_name():
    parsed = SettingsUpdate(company_app_name="Northern Rides")
    assert parsed.model_dump(exclude_none=True)["company_app_name"] == "Northern Rides"


def test_request_model_omits_it_when_untouched():
    # exclude_none=True is how a partial save avoids clobbering other fields.
    assert "company_app_name" not in SettingsUpdate().model_dump(exclude_none=True)


def test_clearing_it_back_to_blank_is_savable():
    assert SettingsUpdate(company_app_name="").model_dump(exclude_none=True)["company_app_name"] == ""


def test_app_settings_defaults_to_spinr():
    # Every field falls back to the previously-hardcoded value: an
    # unconfigured setting must reproduce today's literal "Spinr" output
    # byte-for-byte.
    assert AppSettings().company_app_name == "Spinr"


def test_app_name_is_a_distinct_field_from_the_legal_entity_name():
    parsed = SettingsUpdate(company_name="Northern Rides Inc.", company_app_name="Northern Rides")
    dumped = parsed.model_dump(exclude_none=True)
    assert dumped["company_name"] == "Northern Rides Inc."
    assert dumped["company_app_name"] == "Northern Rides"
