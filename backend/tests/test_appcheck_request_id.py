"""
B-P2-3: tests for App Check log lines including request_id.

Contract:
  - When App Check rejects (missing/invalid token) under enforcement, the
    log line includes `req_id=<id>` so the rejection can be correlated with
    the X-Request-ID echoed back to the client.
  - A missing token is logged at DEBUG, not WARNING: it's an expected,
    high-volume rejection (e.g. a client polling before Firebase App Check
    has minted a token) -- CLAUDE.md's observability convention reserves
    WARNING for recoverable anomalies, not expected/degraded paths. The 401
    access-log line still captures volume; a real attack pattern surfaces
    via the 401 rate, not this per-request line. An INVALID (present but
    unverifiable) token is the anomalous case and still logs at WARNING.
  - request.state.request_id is the source of truth (set by
    RequestIDMiddleware); App Check reads it directly because it dispatches
    BEFORE call_next can establish the loguru contextualize block in
    RequestIDMiddleware.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.anyio
async def test_missing_token_log_includes_request_id(caplog):
    from core.middleware import FirebaseAppCheckMiddleware

    middleware = FirebaseAppCheckMiddleware(app=MagicMock(), enforcement_enabled=True)

    request = MagicMock()
    request.url.path = "/api/rides"
    request.headers = {}
    request.state.request_id = "req-correlate-abc-123"

    async def call_next(_):
        return MagicMock(status_code=200)

    captured_messages: list[str] = []

    def _capture(msg, *args, **kwargs):
        # loguru's logger.warning("...{}...", v) — render the message.
        try:
            captured_messages.append(msg.format(*args))
        except Exception:
            captured_messages.append(str(msg))

    with patch("core.middleware.logger") as mock_logger:
        # Missing token is an expected, high-volume rejection -> DEBUG, not
        # WARNING (see module docstring).
        mock_logger.debug.side_effect = _capture
        response = await middleware.dispatch(request, call_next)

    assert response.status_code == 401
    # The log line must include the request_id so on-call can correlate.
    assert any("req-correlate-abc-123" in m for m in captured_messages), (
        f"App Check log line didn't include request_id; got {captured_messages!r}"
    )


@pytest.mark.anyio
async def test_missing_request_id_falls_back_to_dash():
    """If request.state.request_id isn't set (e.g. middleware ordering bug),
    the log line still uses '-' rather than crashing or leaking 'unknown'."""
    from core.middleware import FirebaseAppCheckMiddleware

    middleware = FirebaseAppCheckMiddleware(app=MagicMock(), enforcement_enabled=True)

    request = MagicMock()
    request.url.path = "/api/rides"
    request.headers = {}
    # Don't set request.state.request_id — getattr should return "-"
    type(request).state = MagicMock()
    request.state = MagicMock(spec=[])  # no attributes

    captured_messages: list[str] = []

    def _capture(msg, *args, **kwargs):
        try:
            captured_messages.append(msg.format(*args))
        except Exception:
            captured_messages.append(str(msg))

    async def call_next(_):
        return MagicMock(status_code=200)

    with patch("core.middleware.logger") as mock_logger:
        mock_logger.debug.side_effect = _capture
        await middleware.dispatch(request, call_next)

    assert any("req_id=-" in m for m in captured_messages), (
        f"missing request_id should render as 'req_id=-'; got {captured_messages!r}"
    )
