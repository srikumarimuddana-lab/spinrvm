"""F01 — Firebase customer-eligibility policy.

Source finding: the AI security assessment on PR #5138, F01
("anonymous Firebase identities are not explicitly prohibited"). The
assessment's own offline probe supplied an already-verified anonymous
Firebase payload and observed both token issuance at /auth/firebase and
acceptance by the shared AI authentication dependency.

These tests pin all three Firebase entry points, because the verification is
duplicated in each rather than delegated to one place:
  1. POST /auth/firebase          -> routes/auth.py::firebase_auth_login
  2. every authenticated request  -> dependencies/__init__.py::get_current_user
  3. the WebSocket handshake      -> routes/websocket.py

A gate on only one of the three would leave the others open, so "all three
reject it" is the actual acceptance criterion, not an incidental extra.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.firebase_identity import (
    ANONYMOUS_PROVIDER,
    FirebaseIdentityRejected,
    allowed_sign_in_providers,
    enforce_customer_eligibility,
    sign_in_provider,
    verified_contact,
)

pytestmark = pytest.mark.unit


def _payload(provider="phone", **overrides):
    p = {
        "uid": "fb-uid-1",
        "aud": "rider-app",
        "phone_number": "+13065550001",
        "firebase": {"sign_in_provider": provider},
    }
    p.update(overrides)
    return p


# ─────────────────────────────────────────────────────────────────────────────
# The policy helper itself
# ─────────────────────────────────────────────────────────────────────────────


class TestEligibilityPolicy:
    def test_anonymous_provider_is_rejected(self):
        with pytest.raises(FirebaseIdentityRejected) as exc:
            enforce_customer_eligibility(_payload(ANONYMOUS_PROVIDER), surface="t")
        assert exc.value.reason == "anonymous_provider"

    def test_anonymous_is_rejected_even_carrying_a_phone_claim(self):
        """The provider check runs first and is not redeemable by a contact
        claim — an anonymous session that later collected a phone number is
        still not a verified customer."""
        with pytest.raises(FirebaseIdentityRejected) as exc:
            enforce_customer_eligibility(_payload(ANONYMOUS_PROVIDER, phone_number="+13065550001"), surface="t")
        assert exc.value.reason == "anonymous_provider"

    def test_missing_firebase_claim_is_rejected_not_exempted(self):
        """A real Firebase ID token always carries firebase.sign_in_provider.
        Absence means the payload is not one, so it fails the allowlist — the
        one behaviour that must never be 'allow' by default."""
        p = _payload()
        del p["firebase"]
        with pytest.raises(FirebaseIdentityRejected) as exc:
            enforce_customer_eligibility(p, surface="t")
        assert exc.value.reason == "provider_not_allowed"

    def test_unlisted_provider_is_rejected(self):
        for provider in ("custom", "google.com", "apple.com", "password"):
            with pytest.raises(FirebaseIdentityRejected) as exc:
                enforce_customer_eligibility(_payload(provider), surface="t")
            assert exc.value.reason == "provider_not_allowed", provider

    def test_allowed_provider_without_any_contact_claim_is_rejected(self):
        p = _payload()
        del p["phone_number"]
        with pytest.raises(FirebaseIdentityRejected) as exc:
            enforce_customer_eligibility(p, surface="t")
        assert exc.value.reason == "no_verified_contact"

    def test_unverified_email_does_not_count_as_a_contact(self):
        with patch(
            "backend.utils.firebase_identity.settings.FIREBASE_ALLOWED_SIGN_IN_PROVIDERS",
            "phone,password",
        ):
            p = _payload("password", email="rider@example.ca", email_verified=False)
            del p["phone_number"]
            with pytest.raises(FirebaseIdentityRejected) as exc:
                enforce_customer_eligibility(p, surface="t")
            assert exc.value.reason == "no_verified_contact"

    def test_verified_email_counts_as_a_contact(self):
        """The contact rule itself accepts a verified email — asserted with the
        allowlist widened, since `password` is deliberately NOT on the default
        (see test_default_allowlist_admits_only_what_the_apps_use)."""
        with patch(
            "backend.utils.firebase_identity.settings.FIREBASE_ALLOWED_SIGN_IN_PROVIDERS",
            "phone,password",
        ):
            p = _payload("password", email="rider@example.ca", email_verified=True)
            del p["phone_number"]
            enforce_customer_eligibility(p, surface="t")  # must not raise

    def test_default_allowlist_admits_only_what_the_apps_use(self):
        """A provider on the allowlist that cannot satisfy the verified-contact
        rule turns a clean sign-in rejection into a silent, permanent 401 with
        no self-service recovery: `password` with an unverified email passes the
        provider check and then fails the contact check, on every request and
        every socket, after the account already exists. Only phone sign-in is
        implemented (signInWithCredential over a phone credential), so that is
        the whole default."""
        assert allowed_sign_in_providers() == frozenset({"phone"})
        with pytest.raises(FirebaseIdentityRejected) as exc:
            enforce_customer_eligibility(_payload("password", email="a@b.ca", email_verified=False), surface="t")
        # Rejected as an unlisted PROVIDER, not as a missing contact — so the
        # rejection happens before provisioning, not after.
        assert exc.value.reason == "provider_not_allowed"

    def test_phone_provider_with_phone_claim_is_accepted(self):
        enforce_customer_eligibility(_payload("phone"), surface="t")

    def test_provider_casing_and_whitespace_are_normalised(self):
        enforce_customer_eligibility(_payload("  PHONE  "), surface="t")

    def test_surface_label_cannot_weaken_the_gate(self):
        """`surface` is a log label only. No value of it admits an anonymous
        identity — otherwise a new call site could silently opt out."""
        for surface in ("auth_exchange", "get_current_user", "websocket", "", "admin"):
            with pytest.raises(FirebaseIdentityRejected):
                enforce_customer_eligibility(_payload(ANONYMOUS_PROVIDER), surface=surface)


class TestAllowlistCannotReopenTheFinding:
    def test_anonymous_is_stripped_from_a_configured_allowlist(self):
        with patch(
            "backend.utils.firebase_identity.settings.FIREBASE_ALLOWED_SIGN_IN_PROVIDERS",
            "phone,anonymous",
        ):
            assert ANONYMOUS_PROVIDER not in allowed_sign_in_providers()
            with pytest.raises(FirebaseIdentityRejected) as exc:
                enforce_customer_eligibility(_payload(ANONYMOUS_PROVIDER), surface="t")
            assert exc.value.reason == "anonymous_provider"

    def test_empty_allowlist_falls_back_closed_not_open(self):
        """An empty setting must not mean 'allow everything' — that would be
        the whole finding again, reachable by a config typo."""
        with patch("backend.utils.firebase_identity.settings.FIREBASE_ALLOWED_SIGN_IN_PROVIDERS", "   "):
            providers = allowed_sign_in_providers()
            assert providers  # non-empty secure default
            with pytest.raises(FirebaseIdentityRejected):
                enforce_customer_eligibility(_payload("github.com"), surface="t")

    def test_kill_switch_restores_pre_f01_behaviour(self):
        """CLAUDE.md pre-merge gate 7 — the documented rollback lever, which
        must actually work, and must be off by default (asserted below)."""
        with patch("backend.utils.firebase_identity.settings.FIREBASE_IDENTITY_POLICY_ENFORCED", False):
            enforce_customer_eligibility(_payload(ANONYMOUS_PROVIDER), surface="t")

    def test_policy_is_enforced_by_default(self):
        from backend.core.config import Settings

        assert Settings.model_fields["FIREBASE_IDENTITY_POLICY_ENFORCED"].default is True
        assert ANONYMOUS_PROVIDER not in Settings.model_fields["FIREBASE_ALLOWED_SIGN_IN_PROVIDERS"].default


class TestClaimExtraction:
    def test_sign_in_provider_absent_or_malformed_returns_empty(self):
        assert sign_in_provider({}) == ""
        assert sign_in_provider({"firebase": None}) == ""
        assert sign_in_provider({"firebase": "not-a-dict"}) == ""
        assert sign_in_provider({"firebase": {}}) == ""

    def test_verified_contact_drops_unverified_email(self):
        assert verified_contact({"email": "a@b.ca"}) == (None, None)
        assert verified_contact({"email": "a@b.ca", "email_verified": True}) == (None, "a@b.ca")
        assert verified_contact({"phone_number": "+1306"}) == ("+1306", None)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point 1 — POST /auth/firebase
# ─────────────────────────────────────────────────────────────────────────────


def _request():
    req = MagicMock()
    req.headers = {"user-agent": "test-agent"}
    req.client = MagicMock(host="127.0.0.1")
    return req


class TestAuthExchangeRejectsAnonymous:
    @pytest.mark.asyncio
    async def test_anonymous_exchange_is_401_and_provisions_nothing(self):
        """The assessment's exact repro: an anonymous payload previously
        created a rider row with an empty phone and returned a usable token
        pair. Both must now not happen — asserting no create_user call is the
        point, since a provisioned row outlives the rejected request."""
        import sys

        from backend.routes.auth import FirebaseAuthRequest, firebase_auth_login
        from backend.utils.error_handling import SpinrException

        payload = {
            "uid": "anon-uid",
            "aud": "driver-app",
            "firebase": {"sign_in_provider": ANONYMOUS_PROVIDER},
        }
        fb_stub = MagicMock()
        fb_stub.verify_id_token.return_value = payload
        create_user = AsyncMock()
        issue_refresh = AsyncMock()

        with (
            patch.dict("sys.modules", {"firebase_admin.auth": fb_stub}),
            patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", "driver-app"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.auth.db_supabase.get_user_by_phone", AsyncMock(return_value=None)),
            patch("backend.routes.auth.db_supabase.create_user", create_user),
            patch("backend.routes.auth.issue_refresh_token", issue_refresh),
        ):
            sys.modules["firebase_admin"].auth = fb_stub
            with pytest.raises(SpinrException) as exc:
                await firebase_auth_login(_request(), MagicMock(), FirebaseAuthRequest(firebase_token="tok"))

        assert exc.value.status_code == 401
        create_user.assert_not_awaited()
        issue_refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_provider_with_no_contact_claim_is_401(self):
        import sys

        from backend.routes.auth import FirebaseAuthRequest, firebase_auth_login
        from backend.utils.error_handling import SpinrException

        payload = {"uid": "u", "aud": "driver-app", "firebase": {"sign_in_provider": "phone"}}
        fb_stub = MagicMock()
        fb_stub.verify_id_token.return_value = payload
        create_user = AsyncMock()

        with (
            patch.dict("sys.modules", {"firebase_admin.auth": fb_stub}),
            patch("backend.routes.auth.settings.FIREBASE_DRIVER_APP_ID", "driver-app"),
            patch("backend.routes.auth.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
            patch("backend.routes.auth.db_supabase.get_user_by_phone", AsyncMock(return_value=None)),
            patch("backend.routes.auth.db_supabase.create_user", create_user),
        ):
            sys.modules["firebase_admin"].auth = fb_stub
            with pytest.raises(SpinrException) as exc:
                await firebase_auth_login(_request(), MagicMock(), FirebaseAuthRequest(firebase_token="tok"))

        assert exc.value.status_code == 401
        create_user.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point 2 — get_current_user (the dependency the AI routes share)
# ─────────────────────────────────────────────────────────────────────────────


class TestGetCurrentUserRejectsAnonymous:
    @pytest.mark.asyncio
    async def test_anonymous_firebase_token_is_401_without_a_db_read(self):
        """Rejecting before the user lookup matters twice: an ineligible token
        costs no Supabase read, and an anonymous UID that already has a row
        (provisioned before this fix) still cannot authenticate."""
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from backend.dependencies import get_current_user

        payload = {
            "uid": "anon-uid",
            "aud": "rider-app",
            "firebase": {"sign_in_provider": ANONYMOUS_PROVIDER},
        }
        lookup = AsyncMock(return_value={"id": "anon-uid", "phone": ""})

        with (
            patch("backend.dependencies.firebase_auth.verify_id_token", return_value=payload),
            patch("backend.dependencies.settings.FIREBASE_RIDER_APP_ID", "rider-app"),
            patch("backend.dependencies.db_supabase.get_user_by_id", lookup),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok"))

        assert exc.value.status_code == 401
        assert exc.value.detail == "ERR_IDENTITY_INELIGIBLE"
        lookup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_eligible_firebase_token_still_authenticates(self):
        """The negative case is only half the acceptance evidence — a real
        phone-verified customer must keep working."""
        from fastapi.security import HTTPAuthorizationCredentials

        from backend.dependencies import get_current_user

        with (
            patch("backend.dependencies.firebase_auth.verify_id_token", return_value=_payload()),
            patch("backend.dependencies.settings.FIREBASE_RIDER_APP_ID", "rider-app"),
            patch(
                "backend.dependencies.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": "fb-uid-1", "sessions_invalid_before": None}),
            ),
            patch(
                "backend.dependencies.db_supabase.get_driver_by_user_id_cached",
                AsyncMock(return_value=None),
            ),
        ):
            user = await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok"))

        assert user["id"] == "fb-uid-1"


# ─────────────────────────────────────────────────────────────────────────────
# Entry point 3 — the WebSocket handshake
# ─────────────────────────────────────────────────────────────────────────────


class TestWebSocketHandshakeGate:
    def test_handshake_calls_the_shared_eligibility_gate(self):
        """Static assertion, matching the style test_websocket_live_location.py
        already uses for is_session_revoked: the WS handshake duplicates
        Firebase verification instead of delegating to get_current_user, so
        the gate has to be spelled out in this file and must not be dropped."""
        import inspect

        from backend.routes import websocket as ws

        src = inspect.getsource(ws.websocket_endpoint)
        assert 'enforce_customer_eligibility(payload, surface="websocket")' in src
        assert "ERR_IDENTITY_INELIGIBLE" in src
