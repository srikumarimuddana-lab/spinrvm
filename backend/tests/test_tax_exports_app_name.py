"""N17: driver tax/earnings/DSAR email copy uses the `company_app_name`
setting, not a literal "Spinr" — see routes/drivers/tax_exports.py.

Companion to test_t4a_email.py / test_dsar_export.py, which cover delivery
and attachment shape; this pins the fallback (unconfigured -> "Spinr",
byte-for-byte) and the configured-value path for the T4A, earnings-CSV,
earnings-statement, and DSAR-export sender copy.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils.company_details import CompanyDetails

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

DRIVER_USER_ID = "driver_user_app_name"
DRIVER_EMAIL = "driver@example.com"


def _company(app_name):
    return CompanyDetails(
        name="Spinr Mobility Inc.",
        app_name=app_name,
        identity_line="Spinr Mobility Inc. - Saskatoon, SK",
        address="",
        contact_line="support@spinr.ca - www.spinr.ca",
        support_email="support@spinr.ca",
        logo_url="https://example.test/logo.png",
    )


def _patched(app_name):
    return patch("backend.routes.drivers.tax_exports.load_company_details", AsyncMock(return_value=_company(app_name)))


# --- T4A summary -------------------------------------------------------------


async def test_t4a_unconfigured_app_name_reproduces_the_literal_subject():
    from backend.routes.drivers import _email_t4a_document

    with (
        _patched("Spinr"),
        patch("backend.routes.drivers._deps.generate_t4a_pdf", return_value=b"%PDF-1.4 fake"),
        patch("backend.routes.drivers._deps.send_email", AsyncMock(return_value=True)) as send,
    ):
        await _email_t4a_document(DRIVER_USER_ID, DRIVER_EMAIL, 2025, {"year": 2025})
    assert send.await_args.kwargs["subject"] == "Your Spinr T4A summary for 2025"
    assert "— The Spinr Team" in send.await_args.kwargs["body"]


async def test_t4a_configured_app_name_replaces_the_literal():
    from backend.routes.drivers import _email_t4a_document

    with (
        _patched("Northern Rides"),
        patch("backend.routes.drivers._deps.generate_t4a_pdf", return_value=b"%PDF-1.4 fake"),
        patch("backend.routes.drivers._deps.send_email", AsyncMock(return_value=True)) as send,
    ):
        await _email_t4a_document(DRIVER_USER_ID, DRIVER_EMAIL, 2025, {"year": 2025})
    assert send.await_args.kwargs["subject"] == "Your Northern Rides T4A summary for 2025"
    assert "— The Northern Rides Team" in send.await_args.kwargs["body"]
    assert "Spinr" not in send.await_args.kwargs["subject"]


# --- Earnings CSV --------------------------------------------------------------


async def test_earnings_csv_configured_app_name_replaces_the_literal():
    from backend.routes.drivers import _email_earnings_csv

    with (
        _patched("Northern Rides"),
        patch("backend.routes.drivers._deps.send_email", AsyncMock(return_value=True)) as send,
    ):
        await _email_earnings_csv(DRIVER_USER_ID, DRIVER_EMAIL, 2025, "Year\n2025")
    assert send.await_args.kwargs["subject"] == "Your Northern Rides earnings export for 2025"
    assert "— The Northern Rides Team" in send.await_args.kwargs["body"]


# --- Earnings statement --------------------------------------------------------


async def test_earnings_statement_configured_app_name_replaces_the_literal():
    from backend.routes.drivers.tax_exports import _email_statement_document

    statement = {
        "period_type": "monthly",
        "period_start": "2026-07-01",
        "period_label": "July 2026",
        "earnings": {"total": "500.00"},
        "trips": 42,
        "payouts_total": "480.00",
    }
    with (
        _patched("Northern Rides"),
        patch("backend.utils.driver_statement_pdf.generate_driver_statement_pdf", return_value=b"%PDF-1.4 fake"),
        patch("backend.routes.drivers._deps.send_email", AsyncMock(return_value=True)) as send,
    ):
        await _email_statement_document(DRIVER_USER_ID, DRIVER_EMAIL, statement)
    assert send.await_args.kwargs["subject"] == "Your Northern Rides earnings statement — July 2026"
    assert "— The Northern Rides Team" in send.await_args.kwargs["body"]


# --- DSAR data export ------------------------------------------------------


async def test_dsar_export_link_email_uses_the_configured_app_name():
    from backend.routes.drivers import _build_export_link_email

    with _patched("Northern Rides"):
        rendered = await _build_export_link_email("https://example.test/export.zip", "August 18, 2026")
    assert "personal data held by Northern Rides" in rendered.text


async def test_dsar_export_readme_uses_the_configured_app_name():
    from backend.routes.drivers import _build_export_readme

    readme = _build_export_readme({"account": {"first_name": "Sam"}}, "2026-08-11", "Northern Rides")
    assert readme.startswith("Northern Rides — Personal Data Export")
    assert "personal data Northern Rides holds about you" in readme


async def test_dsar_export_readme_defaults_to_spinr_when_unconfigured():
    from backend.routes.drivers import _build_export_readme

    readme = _build_export_readme({"account": {}}, "2026-08-11")
    assert readme.startswith("Spinr — Personal Data Export")


async def test_dsar_export_html_uses_the_configured_app_name():
    from backend.routes.drivers import _build_export_email_html

    html = _build_export_email_html("export.zip", "Northern Rides")
    assert "personal data held by Northern Rides" in html
    assert "— The Northern Rides Team" in html
