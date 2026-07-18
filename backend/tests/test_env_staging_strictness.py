"""ADR-008: staging must behave like production for auth strictness.

Covers the two auth gates that switched from ENV=="production" to
settings.is_production_like:
- /send-otp static-code fallback ("1234") is refused in staging.
- Admin login rate limit is strict in staging.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from backend.utils.error_handling import SpinrException
except ImportError:
    from utils.error_handling import SpinrException

PHONE = "+13065550142"


async def _call_send_otp_staging():
    from backend.routes.auth import send_otp
    from backend.schemas import SendOTPRequest
    from backend.tests.test_auth_send_otp import _resolve_inner

    body = SendOTPRequest(phone=PHONE)
    request = MagicMock()
    request.client = MagicMock(host="127.0.0.1")

    patches = [
        # No Twilio configured in the (mocked) app_settings row.
        patch("backend.routes.auth.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.auth.db_supabase.delete_many", AsyncMock()),
        patch("backend.routes.auth.db_supabase.insert_otp_record", AsyncMock()),
        patch("backend.routes.auth.settings.ENV", "staging"),
    ]
    for p in patches:
        p.start()
    try:
        inner = _resolve_inner(send_otp)
        return await inner(request, body)
    finally:
        for p in patches:
            p.stop()


class TestStagingOtpFallbackRefused:
    def test_staging_without_twilio_refuses_static_otp(self):
        """Staging + no Twilio must 503, never issue the fixed dev code."""
        with pytest.raises(SpinrException) as exc:
            asyncio.run(_call_send_otp_staging())
        assert exc.value.status_code == 503


class TestStagingRefusesUnconfiguredSettlement:
    """payment_service.settle_card: Stripe-unconfigured in staging must refuse
    settlement (mark failed, 503) — never mark the ride paid for free."""

    def test_staging_unconfigured_charge_refused(self):
        from decimal import Decimal

        from backend.services import payment_service as ps
        from backend.utils.stripe_charge import ChargeOutcome

        update_ride = AsyncMock()
        with (
            patch.object(ps.app_config, "ENV", "staging"),
            patch(
                "backend.services.payment_service.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": "r1", "stripe_customer_id": "cus_1", "default_payment_method": "pm_1"}),
            ),
            patch("backend.services.payment_service.db_supabase.update_ride", update_ride),
            patch(
                "backend.services.payment_service.charge_ride",
                AsyncMock(return_value=ChargeOutcome(status="unconfigured")),
            ),
            patch("backend.services.payment_service.record_payment_event", AsyncMock()),
            patch("backend.services.payment_service.send_push_notification", AsyncMock()),
        ):
            result = asyncio.run(
                ps.settle_card(
                    ride={"id": "ride1", "rider_id": "r1", "payment_method_id": "pm_1"},
                    ride_id="ride1",
                    rider_id="r1",
                    total_charge=Decimal("20.00"),
                    tip_amount=Decimal("0.00"),
                )
            )

        assert result.success is False
        assert result.error_code == "stripe_unconfigured"
        assert result.status_code == 503
        # The refusal path marks the ride failed (collectible) — never paid.
        statuses = [c.args[1].get("payment_status") for c in update_ride.call_args_list]
        assert "paid" not in statuses
        assert "failed" in statuses


class TestAdminLoginRateLimit:
    @pytest.mark.parametrize(
        "env,expected",
        [
            ("production", "3/30minutes"),
            ("staging", "3/30minutes"),
            ("development", "20/minute"),
            ("test", "20/minute"),
        ],
    )
    def test_rate_limit_strict_in_production_like(self, env, expected):
        from backend.routes.admin import auth as admin_auth

        with patch.object(admin_auth.settings, "ENV", env):
            assert admin_auth._admin_login_rate_limit() == expected
