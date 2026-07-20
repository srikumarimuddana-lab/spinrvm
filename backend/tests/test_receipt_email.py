"""Unit tests for send_ride_receipt_email.

Covers:
- Early return when resend_api_key is missing (no HTTP call)
- Decimal arithmetic for GST and grand_total
- Surge line item hidden at 1.0x, shown above 1.0x
- HTTP errors are swallowed (fire-and-forget contract)

Patching notes:
  get_app_settings is imported inline inside the function as
  `from settings_loader import get_app_settings`, so we patch it at
  `settings_loader.get_app_settings`.

  httpx is imported inline as `import httpx`, so we patch
  `httpx.AsyncClient` directly.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from utils.receipt_email import _build_plain_text, send_ride_receipt_email
except ImportError:
    from backend.utils.receipt_email import (  # type: ignore[no-redef]
        _build_plain_text,
        send_ride_receipt_email,
    )


@pytest.fixture(autouse=True)
def _no_suppression_or_log():
    """Receipts now route through send_transactional_email, which checks the
    suppression list (db_supabase.find_one) and writes email_send_log
    (insert_one). Neutralise both so these provider-body tests are isolated
    from leaked global DB-mock state (otherwise a "suppressed" lookup skips
    the send and the assertions on the POST body fail under cross-test order).
    """
    with (
        patch("db_supabase.find_one", AsyncMock(return_value=None)),
        patch("db_supabase.insert_one", AsyncMock(return_value=None)),
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CENT = Decimal("0.01")

_BASE_RIDE = {
    "id": "ride-abc",
    "ride_code": "SPIN1234",
    "base_fare": "5.00",
    "distance_fare": "3.00",
    "time_fare": "2.00",
    "booking_fee": "1.00",
    "surge_multiplier": "1.0",
    "total_fare": "11.00",  # 5+3+2+1
    "grand_total": None,
    "distance_km": "10.5",
    "duration_minutes": 15,
    "pickup_address": "123 Main St",
    "dropoff_address": "456 Elm St",
}

_BASE_RIDER = {
    "id": "rider-xyz",
    "email": "test@example.com",
    "first_name": "Alice",
    "last_name": "Rider",
}


def _make_mock_response(status_code: int = 202) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = ""
    return resp


def _make_async_client_mock(post_side_effect=None) -> MagicMock:
    """Return a context-manager-compatible AsyncClient mock."""
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    if post_side_effect is not None:
        mock.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock.post = AsyncMock(return_value=_make_mock_response(202))
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPlainTextMinimumFare:
    """The plain-text body must disclose the minimum-fare uplift and airport
    surcharge so the itemised fare lines reconcile to the printed Subtotal."""

    _RIDER = {"first_name": "Alice", "last_name": "Rider"}

    def test_minimum_fare_adjustment_line_present(self):
        # base+dist+time+booking = 5.90, floored to 8.00.
        ride = {
            **_BASE_RIDE,
            "base_fare": "3.50",
            "distance_fare": "0.15",
            "time_fare": "0.25",
            "booking_fee": "2.00",
            "total_fare": "8.00",
            "grand_total": None,
        }
        body = _build_plain_text(ride, self._RIDER)
        assert "Minimum fare adjustment" in body
        assert "$2.10" in body  # 8.00 − 5.90

    def test_no_minimum_fare_line_when_not_clamped(self):
        # _BASE_RIDE: total_fare 11.00 == component sum → no adjustment line.
        body = _build_plain_text(_BASE_RIDE, self._RIDER)
        assert "Minimum fare adjustment" not in body

    def test_airport_surcharge_line_present(self):
        ride = {**_BASE_RIDE, "airport_fee": "4.00", "total_fare": "15.00"}
        body = _build_plain_text(ride, self._RIDER)
        assert "Airport surcharge" in body
        assert "$4.00" in body


@pytest.mark.anyio
async def test_receipt_email_no_resend_key():
    """When resend_api_key is absent, return without making any HTTP call."""
    mock_client = _make_async_client_mock()

    with (
        patch("settings_loader.get_app_settings", AsyncMock(return_value={})),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await send_ride_receipt_email(_BASE_RIDE, _BASE_RIDER)

    mock_client.post.assert_not_awaited()


@pytest.mark.anyio
async def test_receipt_email_decimal_math():
    """GST (5%) and grand_total in the POST body must use correct Decimal arithmetic."""
    captured: dict = {}

    async def capture_post(url, *, headers, json):
        captured.update(json)
        return _make_mock_response(202)

    mock_client = _make_async_client_mock(post_side_effect=capture_post)

    with (
        patch(
            "settings_loader.get_app_settings",
            AsyncMock(
                return_value={
                    "resend_api_key": "re_test-key",
                    "resend_from_email": "receipts@spinr.ca",
                }
            ),
        ),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await send_ride_receipt_email(_BASE_RIDE, _BASE_RIDER)

    assert captured, "Expected an HTTP POST to be made"
    content_value = captured["text"]

    # subtotal = 11.00; GST = 11.00 * 0.05 = 0.55; PST = 11.00 * 0.06 = 0.66
    subtotal = Decimal("11.00")
    expected_gst = (subtotal * Decimal("0.05")).quantize(_CENT, rounding=ROUND_HALF_UP)
    expected_pst = (subtotal * Decimal("0.06")).quantize(_CENT, rounding=ROUND_HALF_UP)
    expected_grand = subtotal + expected_gst + expected_pst  # 12.21

    assert f"{expected_gst:.2f}" in content_value, f"Expected GST {expected_gst} in receipt body; got:\n{content_value}"
    assert f"{expected_grand:.2f}" in content_value, (
        f"Expected grand total {expected_grand} in receipt body; got:\n{content_value}"
    )


@pytest.mark.anyio
async def test_receipt_email_surge_hidden_when_1x():
    """When surge_multiplier=1.0 the receipt body must NOT contain 'Surge'."""
    captured: dict = {}

    async def capture_post(url, *, headers, json):
        captured.update(json)
        return _make_mock_response(202)

    mock_client = _make_async_client_mock(post_side_effect=capture_post)
    ride = {**_BASE_RIDE, "surge_multiplier": "1.0"}

    with (
        patch(
            "settings_loader.get_app_settings",
            AsyncMock(
                return_value={
                    "resend_api_key": "re_test-key",
                    "resend_from_email": "receipts@spinr.ca",
                }
            ),
        ),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await send_ride_receipt_email(ride, _BASE_RIDER)

    assert captured, "Expected an HTTP POST to be made"
    content_value = captured["text"]
    assert "Surge" not in content_value, "Surge line item must not appear when surge_multiplier is 1.0"


@pytest.mark.anyio
async def test_receipt_email_surge_shown_when_above_1x():
    """When surge_multiplier=1.5 the receipt body MUST contain 'Surge'."""
    captured: dict = {}

    async def capture_post(url, *, headers, json):
        captured.update(json)
        return _make_mock_response(202)

    mock_client = _make_async_client_mock(post_side_effect=capture_post)
    ride = {**_BASE_RIDE, "surge_multiplier": "1.5"}

    with (
        patch(
            "settings_loader.get_app_settings",
            AsyncMock(
                return_value={
                    "resend_api_key": "re_test-key",
                    "resend_from_email": "receipts@spinr.ca",
                }
            ),
        ),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        await send_ride_receipt_email(ride, _BASE_RIDER)

    assert captured, "Expected an HTTP POST to be made"
    content_value = captured["text"]
    assert "Surge" in content_value, "Surge line item must appear when surge_multiplier is above 1.0"


@pytest.mark.anyio
async def test_receipt_email_http_error_does_not_raise():
    """An httpx.HTTPError during send must be swallowed — fire-and-forget contract."""
    import httpx as _httpx

    mock_client = _make_async_client_mock(post_side_effect=_httpx.HTTPError("connection refused"))

    with (
        patch(
            "settings_loader.get_app_settings",
            AsyncMock(
                return_value={
                    "resend_api_key": "re_test-key",
                    "resend_from_email": "receipts@spinr.ca",
                }
            ),
        ),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        # Must not propagate any exception
        await send_ride_receipt_email(_BASE_RIDE, _BASE_RIDER)
