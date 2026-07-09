"""Regression: the [DEPRECATED_ROUTE] middleware must NOT flag App-Check-exempt
/api/admin/* paths.

Their /api/v1/admin twin is App-Check-ENFORCED (core/middleware.py), so a
browser client (the admin dashboard) cannot migrate to it — flagging them is
pure log noise, not an actionable deprecation signal. See server.py's
DeprecatedRootPathMiddleware and the /api/admin/ guard added alongside these
tests.
"""

import asyncio

from starlette.responses import Response

from backend.server import DeprecatedRootPathMiddleware


class _FakeURL:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeRequest:
    def __init__(self, path: str) -> None:
        self.url = _FakeURL(path)


async def _call_next(_request):
    return Response(status_code=200)


def _dispatch(path: str) -> Response:
    # BaseHTTPMiddleware needs an app arg; our dispatch override never touches it.
    mw = DeprecatedRootPathMiddleware(app=lambda scope, receive, send: None)
    return asyncio.run(mw.dispatch(_FakeRequest(path), _call_next))


def test_admin_corporate_accounts_not_flagged_deprecated():
    # Exact prefix and a nested path both stay unflagged.
    assert "X-Spinr-Deprecated" not in _dispatch("/api/admin/corporate-accounts").headers
    assert "X-Spinr-Deprecated" not in _dispatch("/api/admin/corporate-accounts/abc123/wallet").headers


def test_other_admin_paths_not_flagged_deprecated():
    # Any /api/admin/* path is exempt from the deprecation signal.
    assert "X-Spinr-Deprecated" not in _dispatch("/api/admin/service-areas").headers


def test_non_admin_deprecated_paths_still_flagged():
    # Regression guard: genuinely deprecated non-admin /api paths are still flagged.
    assert _dispatch("/api/documents/xyz").headers.get("X-Spinr-Deprecated") == "true"
    assert _dispatch("/api/auth/verify-otp").headers.get("X-Spinr-Deprecated") == "true"
