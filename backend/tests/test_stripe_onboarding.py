"""Tests for the Stripe Connect payout onboarding link/return/refresh wiring.

Root cause this guards against: the AccountLink refresh_url/return_url were
built as http://localhost:8000/api/drivers/... — localhost (unreachable from a
phone), the wrong /api/ prefix, and pointing at endpoints that didn't exist —
so Stripe's hosted onboarding buffered at "Verify Identity" when it hit
refresh_url mid-flow.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_base_url_never_localhost():
    from backend.routes.drivers import _onboarding_base_url

    # app_settings localhost / missing → falls back to the public API origin.
    assert not _onboarding_base_url({"base_url": "http://localhost:8000"}).startswith("http://localhost")
    assert _onboarding_base_url({}).startswith("https://")
    # operator override with a real public URL is honoured (trailing slash trimmed)
    assert _onboarding_base_url({"base_url": "https://api.example.com/"}) == "https://api.example.com"


def test_onboarding_link_uses_correct_public_urls():
    from backend.routes import drivers as drv

    link = MagicMock(url="https://connect.stripe.com/setup/acct_X/abc")
    with patch.object(drv.stripe.AccountLink, "create", return_value=link) as create:
        out = drv._create_onboarding_link("acct_X", {"base_url": "https://api.spinr.test"}, "sk_test")

    assert out.url.startswith("https://connect.stripe.com/")
    kw = create.call_args.kwargs
    assert kw["account"] == "acct_X"
    assert kw["type"] == "account_onboarding"
    # correct /api/v1/ prefix, real endpoints, account_id on refresh for the
    # unauthenticated refresh handler to identify the driver.
    assert kw["refresh_url"] == "https://api.spinr.test/api/v1/drivers/stripe-refresh?account_id=acct_X"
    assert kw["return_url"] == "https://api.spinr.test/api/v1/drivers/stripe-return"
    assert "localhost" not in kw["refresh_url"]


@pytest.mark.anyio
async def test_refresh_endpoint_redirects_to_fresh_link():
    from backend.routes import drivers as drv

    link = MagicMock(url="https://connect.stripe.com/setup/acct_X/fresh")
    with (
        patch.object(
            drv.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "d1", "stripe_account_id": "acct_X"}])
        ),
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test"})),
        patch.object(drv.stripe.AccountLink, "create", return_value=link),
    ):
        resp = await drv.stripe_onboard_refresh(account_id="acct_X")

    assert resp.status_code == 303
    assert resp.headers["location"] == "https://connect.stripe.com/setup/acct_X/fresh"


@pytest.mark.anyio
async def test_refresh_unknown_account_404():
    from fastapi import HTTPException

    from backend.routes import drivers as drv

    with patch.object(drv.db_supabase, "get_rows", AsyncMock(return_value=[])):
        with pytest.raises(HTTPException) as exc:
            await drv.stripe_onboard_refresh(account_id="acct_unknown")
    assert exc.value.status_code == 404
