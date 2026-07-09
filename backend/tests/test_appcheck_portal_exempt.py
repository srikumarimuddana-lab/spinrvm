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


def test_mobile_auth_stays_appcheck_enforced():
    # The hybrid keeps mobile's own auth namespace enforced — only the browser
    # portal's /api/portal/auth/* is exempt.
    assert not _appcheck_exempt("/api/v1/auth/verify-otp")
    assert not _appcheck_exempt("/api/v1/auth/refresh")
    assert not _appcheck_exempt("/api/v1/auth/logout")
    assert not _appcheck_exempt("/api/v1/rides")


def test_portal_preauth_is_csrf_exempt():
    # send-otp is a browser-Origin POST with no csrf cookie yet.
    assert "/api/portal/auth/send-otp" in _CSRF_EXEMPT_EXACT
    assert "/api/portal/auth/verify-otp" in _CSRF_EXEMPT_EXACT
    assert "/api/portal/auth/send-email-otp" in _CSRF_EXEMPT_EXACT
    assert "/api/portal/auth/verify-email-otp" in _CSRF_EXEMPT_EXACT
