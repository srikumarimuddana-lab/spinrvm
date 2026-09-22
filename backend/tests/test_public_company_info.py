"""Tests for GET /company-info (routes/settings.py::get_company_info).

No dedicated test file existed for this route before — `test_server_coverage.py`
only asserts the deprecation header on the legacy root path, never the payload.

The rule pinned here: **every field reports what is configured, or ""** — the
endpoint never invents a value. `name` used to be the exception, defaulting to
"Spinr" while its four siblings defaulted to "". That made a blanked-out
Company Name indistinguishable from one deliberately set to "Spinr", so the
rider/driver apps could not omit the field and rendered a company name nobody
had entered. Found by a design review of the Help-screen fix that removed the
equivalent client-side fallbacks (docs/change-log/2026-09-22-*).

`utils/company_details.py` deliberately keeps its own "Spinr" default for email
and PDF footers — a different risk class (documents already filed with SGI),
and out of scope here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_FIELDS = ("name", "address", "phone", "email", "website")


async def _company_info(settings: dict) -> dict:
    from backend.routes import settings as settings_mod

    with patch.object(settings_mod, "get_app_settings", AsyncMock(return_value=settings)):
        return await settings_mod.get_company_info()


@pytest.mark.anyio
async def test_all_fields_empty_when_nothing_configured():
    result = await _company_info({})

    assert result == {field: "" for field in _FIELDS}


@pytest.mark.anyio
@pytest.mark.parametrize("missing", [{}, {"company_name": ""}, {"company_name": None}])
async def test_name_is_empty_not_spinr_when_unset(missing):
    """The regression this file exists for.

    Covers all three shapes a blank column reaches the handler as: key absent,
    empty string, and NULL.
    """
    result = await _company_info(missing)

    assert result["name"] == ""


@pytest.mark.anyio
async def test_configured_values_pass_through():
    result = await _company_info(
        {
            "company_name": "Acme Rides Ltd.",
            "company_address": "1 Main St, Saskatoon, SK",
            "company_phone": "+1 306 555 0100",
            "company_email": "help@example.ca",
            "company_website": "https://example.ca",
        }
    )

    assert result == {
        "name": "Acme Rides Ltd.",
        "address": "1 Main St, Saskatoon, SK",
        "phone": "+1 306 555 0100",
        "email": "help@example.ca",
        "website": "https://example.ca",
    }


@pytest.mark.anyio
async def test_partial_configuration_leaves_the_rest_empty():
    """A half-filled Company Info section must not backfill the gaps."""
    result = await _company_info({"company_email": "help@example.ca"})

    assert result["email"] == "help@example.ca"
    assert result["name"] == ""
    assert result["address"] == ""
    assert result["phone"] == ""
    assert result["website"] == ""
