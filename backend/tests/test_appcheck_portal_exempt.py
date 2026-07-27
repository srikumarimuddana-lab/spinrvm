"""H2: the browser company portal (which can't attach an X-Firebase-AppCheck
header) must be App-Check-exempt on its own surfaces. Mobile's
/api/v1/auth/* namespace stays enforced except OTP login
(send-otp/verify-otp), which is deliberately exempt so login stays
reachable when App Check native modules aren't available/attested yet."""

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
    # The hybrid keeps mobile's own auth namespace enforced -- EXCEPT OTP
    # login (send-otp/verify-otp), which core/middleware.py deliberately
    # exempts so login stays reachable when App Check native modules are
    # unavailable/not-yet-attested (Expo Go/debug builds, newly registered
    # devices). Those two handlers keep their own OTP throttling/lockout
    # and expose nothing without possession of the SMS code. refresh/logout
    # and the rest of the API stay enforced.
    assert _appcheck_exempt("/api/v1/auth/send-otp")
    assert _appcheck_exempt("/api/v1/auth/verify-otp")
    assert not _appcheck_exempt("/api/v1/auth/refresh")
    assert not _appcheck_exempt("/api/v1/auth/logout")
    assert not _appcheck_exempt("/api/v1/rides")


def test_portal_preauth_is_csrf_exempt():
    # send-otp is a browser-Origin POST with no csrf cookie yet.
    assert "/api/portal/auth/send-otp" in _CSRF_EXEMPT_EXACT
    assert "/api/portal/auth/verify-otp" in _CSRF_EXEMPT_EXACT
    assert "/api/portal/auth/send-email-otp" in _CSRF_EXEMPT_EXACT
    assert "/api/portal/auth/verify-email-otp" in _CSRF_EXEMPT_EXACT
