"""H2: the browser company portal (which can't attach an X-Firebase-AppCheck
header) must be App-Check-exempt on its own surfaces, while the MOBILE
/api/v1/auth/* endpoints stay enforced."""

from backend.core.middleware import _APP_CHECK_EXEMPT_PREFIXES, _CSRF_EXEMPT_EXACT


def _appcheck_exempt(path: str) -> bool:
    return any(path.startswith(p) for p in _APP_CHECK_EXEMPT_PREFIXES)


def test_portal_surfaces_are_appcheck_exempt():
    assert _appcheck_exempt("/api/portal/auth/send-otp")
    assert _appcheck_exempt("/api/portal/auth/verify-otp")
    assert _appcheck_exempt("/api/portal/auth/refresh")
    assert _appcheck_exempt("/api/portal/auth/logout")
    assert _appcheck_exempt("/api/company/abc123/bookings")
    assert _appcheck_exempt("/api/rider/work-profile")
    assert _appcheck_exempt("/api/rider/work-profile/accept-invite")
    assert _appcheck_exempt("/api/v1/vehicle-types")
    assert _appcheck_exempt("/api/v1/maps/places/autocomplete")


def test_mobile_auth_session_endpoints_stay_appcheck_enforced():
    # Mobile session management stays enforced — browser clients use the
    # /api/portal/auth/* mirror instead. (send-otp/verify-otp are exempt by
    # design: OTP possession is the auth factor and they carry their own
    # throttling/lockout.)
    assert not _appcheck_exempt("/api/v1/auth/refresh")
    assert not _appcheck_exempt("/api/v1/auth/logout")
    assert not _appcheck_exempt("/api/v1/auth/logout-all")


def test_marketing_website_surfaces_are_appcheck_exempt():
    # The marketing website (spinr.ca) drives driver signup and rider web
    # booking from the browser — same class of exemption as the company
    # portal. All JWT-gated except fares/service-areas (public reads).
    assert _appcheck_exempt("/api/v1/users/profile")
    assert _appcheck_exempt("/api/v1/drivers/register")
    assert _appcheck_exempt("/api/v1/drivers/requirements")
    assert _appcheck_exempt("/api/v1/drivers/documents")
    assert _appcheck_exempt("/api/v1/drivers/documents/upload")
    assert _appcheck_exempt("/api/v1/drivers/me")
    assert _appcheck_exempt("/api/v1/rides")
    assert _appcheck_exempt("/api/v1/rides/estimate")
    assert _appcheck_exempt("/api/v1/rides/active")
    assert _appcheck_exempt("/api/v1/rides/some-ride-id/cancel")
    assert _appcheck_exempt("/api/v1/payments/setup-intent")
    assert _appcheck_exempt("/api/v1/payments/cards")
    assert _appcheck_exempt("/api/v1/fares")
    assert _appcheck_exempt("/api/v1/service-areas")


def test_driver_sensitive_surfaces_stay_appcheck_enforced():
    # The website exemptions must not leak onto driver-app-only or other
    # mobile-only surfaces.
    assert not _appcheck_exempt("/api/v1/drivers/location-batch")
    assert not _appcheck_exempt("/api/v1/drivers/destination")
    assert not _appcheck_exempt("/api/v1/payments/create-intent")
    assert not _appcheck_exempt("/api/v1/users/emergency-contacts")
    assert not _appcheck_exempt("/api/v1/wallet/top-up")


def test_portal_preauth_is_csrf_exempt():
    # send-otp is a browser-Origin POST with no csrf cookie yet.
    assert "/api/portal/auth/send-otp" in _CSRF_EXEMPT_EXACT
    assert "/api/portal/auth/verify-otp" in _CSRF_EXEMPT_EXACT
    assert "/api/portal/auth/send-email-otp" in _CSRF_EXEMPT_EXACT
    assert "/api/portal/auth/verify-email-otp" in _CSRF_EXEMPT_EXACT
