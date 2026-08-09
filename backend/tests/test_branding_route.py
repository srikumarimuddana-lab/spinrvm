"""Tests for the public email-logo endpoint (routes/branding.py).

Exercises the inner ``_build_logo_response`` helper directly so we skip the
SlowAPI/request plumbing, plus two guarantees that are easy to regress:

  - the route is App-Check exempt (a mail client cannot send that header), and
  - exempting it did NOT expose the rest of ``backend/static`` — the SGI
    regulator form templates live there too.
"""

from unittest.mock import MagicMock, patch

import pytest

import routes.branding as branding
from core.middleware import _APP_CHECK_EXEMPT_PREFIXES

pytestmark = [pytest.mark.unit]

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _clear_logo_cache():
    """The module memoises the bytes; reset between tests so each one is honest."""
    branding._logo_bytes = None
    yield
    branding._logo_bytes = None


def test_serves_the_real_png_asset():
    resp = branding._build_logo_response()
    assert resp.status_code == 200
    assert resp.media_type == "image/png"
    assert resp.body[:8] == _PNG_MAGIC, "must be a real PNG, not a placeholder"


def test_sets_immutable_cache_header():
    # Mail clients and Gmail's image proxy re-fetch on every open otherwise.
    resp = branding._build_logo_response()
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_missing_asset_404s_rather_than_raising():
    # Mirrors report_branding.has_logo_asset()'s degrade-don't-raise contract:
    # a missing logo must leave the email readable via its alt text, not 500.
    with patch.object(branding, "has_logo_asset", return_value=False):
        resp = branding._build_logo_response()
    assert resp.status_code == 404


def test_unreadable_asset_404s_rather_than_raising():
    stub = MagicMock()
    stub.read_bytes.side_effect = OSError("disk gone")
    with (
        patch.object(branding, "has_logo_asset", return_value=True),
        patch.object(branding, "LOGO_PATH", stub),
    ):
        resp = branding._build_logo_response()
    assert resp.status_code == 404


def test_bytes_are_memoised_after_first_read():
    stub = MagicMock()
    stub.read_bytes.return_value = b"\x89PNG-stub"
    with (
        patch.object(branding, "has_logo_asset", return_value=True),
        patch.object(branding, "LOGO_PATH", stub),
    ):
        branding._build_logo_response()
        branding._build_logo_response()
    assert stub.read_bytes.call_count == 1


def test_route_is_app_check_exempt():
    # A mail client cannot attach X-Firebase-AppCheck. Without this the logo
    # 401s and every branded email renders with a broken image.
    assert "/api/v1/branding/" in _APP_CHECK_EXEMPT_PREFIXES


def test_exemption_does_not_cover_sgi_regulator_forms():
    """The reason this is a single-file route, not app.mount('/static').

    backend/static/ holds the SGI D00032/D00033 form templates. If anyone ever
    swaps this for a directory mount, the prefix below would publish them.
    """
    assert "/static" not in _APP_CHECK_EXEMPT_PREFIXES
    assert not any(p.rstrip("/").endswith("static") for p in _APP_CHECK_EXEMPT_PREFIXES)
