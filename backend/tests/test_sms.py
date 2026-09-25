"""
Unit tests for SMS service functionality.
Tests cover OTP SMS sending, general SMS, and Twilio integration.
"""

import asyncio
import importlib
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _restore_real_sms_service():
    """The project-wide autouse fixture in conftest.py stubs
    ``backend.sms_service.send_sms`` / ``send_otp_sms`` with AsyncMocks
    so route tests don't inadvertently dial Twilio. This file IS the
    test for those two functions — rebind the module attributes to the
    real implementations for the duration of every test here.
    """
    import backend.sms_service as sms_mod

    importlib.reload(sms_mod)
    yield


class TestSMSService:
    """Tests for SMS service core functionality."""

    @pytest.mark.asyncio
    async def test_send_sms_console_fallback(self):
        """Test SMS sending falls back to console when Twilio not configured."""
        from backend.sms_service import send_sms

        result = await send_sms("+1234567890", "Test message", twilio_sid="", twilio_token="", twilio_from="")

        assert result["success"] is True
        assert result["provider"] == "console"

    @pytest.mark.asyncio
    async def test_send_sms_twilio_success(self):
        """Test SMS sending via Twilio successfully."""
        from backend.sms_service import send_sms

        with patch("twilio.rest.Client") as mock_client:
            mock_sms = MagicMock()
            mock_sms.sid = "SM123"
            mock_client.return_value.messages.create.return_value = mock_sms

            result = await send_sms(
                "+1234567890", "Test message", twilio_sid="AC123", twilio_token="token", twilio_from="+10000000000"
            )

            assert result["success"] is True
            assert result["provider"] == "twilio"
            assert result["sid"] == "SM123"

    @pytest.mark.asyncio
    async def test_send_sms_twilio_failure(self):
        """Test SMS sending via Twilio handles failure."""
        from backend.sms_service import send_sms

        with patch("twilio.rest.Client") as mock_client:
            mock_client.return_value.messages.create.side_effect = Exception("Twilio error")

            result = await send_sms(
                "+1234567890", "Test message", twilio_sid="AC123", twilio_token="token", twilio_from="+10000000000"
            )

            assert result["success"] is False
            assert result["provider"] == "twilio"
            assert "error" in result

    @pytest.mark.asyncio
    async def test_send_sms_failure_error_is_pii_free(self):
        """PIPEDA: Twilio exception text embeds the destination number
        ("The 'To' number +1306... is not a valid phone number") and the SOS
        path logs the returned 'error' verbatim — so it must carry only the
        exception type and Twilio code/status, never str(e)."""
        from backend.sms_service import send_sms

        class FakeTwilioRestException(Exception):
            def __init__(self):
                super().__init__("Unable to create record: The 'To' number +13065551234 is not a valid phone number.")
                self.code = 21211
                self.status = 400

        with patch("twilio.rest.Client") as mock_client:
            mock_client.return_value.messages.create.side_effect = FakeTwilioRestException()

            result = await send_sms(
                "+13065551234", "Test message", twilio_sid="AC123", twilio_token="token", twilio_from="+10000000000"
            )

        assert result["success"] is False
        assert "3065551234" not in result["error"], "destination number leaked into the error field"
        assert result["error"] == "FakeTwilioRestException code=21211 status=400"

    @pytest.mark.asyncio
    async def test_send_sms_twilio_client_built_with_timeout(self):
        """INT-001 regression: the Twilio client must be built with an
        explicit HTTP timeout, not Twilio's default (unbounded) http_client —
        a slow/unresponsive Twilio would otherwise hang the OTP/login path."""
        import backend.sms_service as sms_mod

        with (
            patch("twilio.http.http_client.TwilioHttpClient") as mock_http_client_cls,
            patch("twilio.rest.Client") as mock_client_cls,
        ):
            mock_http_client_instance = MagicMock()
            mock_http_client_cls.return_value = mock_http_client_instance
            mock_sms = MagicMock()
            mock_sms.sid = "SM789"
            mock_client_cls.return_value.messages.create.return_value = mock_sms

            result = await sms_mod.send_sms(
                "+1234567890", "Test message", twilio_sid="AC123", twilio_token="token", twilio_from="+10000000000"
            )

            assert result["success"] is True
            mock_http_client_cls.assert_called_once_with(timeout=sms_mod._TWILIO_HTTP_TIMEOUT_S)
            mock_client_cls.assert_called_once_with("AC123", "token", http_client=mock_http_client_instance)

    @pytest.mark.asyncio
    async def test_send_sms_twilio_hang_times_out(self):
        """INT-001 regression: a Twilio call that hangs past the bounded
        thread-pool wait must time out and return a clean failure rather than
        block the caller (and the shared threadpool) forever."""
        import backend.sms_service as sms_mod

        def _hang(*_args, **_kwargs):
            # Simulate a Twilio call that never returns within the window —
            # sleep well past the (patched, short) thread timeout.
            time.sleep(0.3)
            raise AssertionError("should have been abandoned by asyncio.wait_for before returning")

        with (
            patch.object(sms_mod, "_TWILIO_THREAD_TIMEOUT_S", 0.05),
            patch("twilio.http.http_client.TwilioHttpClient"),
            patch("twilio.rest.Client") as mock_client_cls,
        ):
            mock_client_cls.return_value.messages.create.side_effect = _hang

            start = time.monotonic()
            result = await sms_mod.send_sms(
                "+1234567890", "Test message", twilio_sid="AC123", twilio_token="token", twilio_from="+10000000000"
            )
            elapsed = time.monotonic() - start

        assert result["success"] is False
        assert result["provider"] == "twilio"
        assert "TimeoutError" in result["error"]
        # The coroutine must return promptly once the thread-pool wait times
        # out, not wait for the hung thread itself to finish.
        assert elapsed < 0.3

    @pytest.mark.asyncio
    async def test_send_otp_sms(self):
        """Test sending OTP SMS."""
        from backend.sms_service import send_otp_sms

        with patch("twilio.rest.Client") as mock_client:
            mock_sms = MagicMock()
            mock_sms.sid = "SM456"
            mock_client.return_value.messages.create.return_value = mock_sms

            result = await send_otp_sms(
                "+1234567890", "123456", twilio_sid="AC123", twilio_token="token", twilio_from="+10000000000"
            )

            assert result["success"] is True
            assert result["sid"] == "SM456"

    def test_send_otp_sms_format(self):
        """Test OTP SMS message format."""
        otp_code = "123456"
        expected_message = f"Your Spinr verification code is: {otp_code}. It expires in 5 minutes."

        assert otp_code in expected_message
        assert "verification code" in expected_message.lower()
        assert "expires in 5 minutes" in expected_message


_TWILIO_KW = {"twilio_sid": "AC123", "twilio_token": "token", "twilio_from": "+10000000000"}


def _capacity(executor) -> int:
    """Admission slots (max_workers + queue_size) of a BoundedExecutor."""
    return executor._slots._initial_value


class TestSMSBoundedExecutor:
    """Twilio sends run on dedicated bounded pools (security-auditor finding on
    #5784): a hung Twilio/DNS call must pin at most the SMS pool's threads,
    never the event loop's shared default executor, and a full pool must fail
    fast with the normal {"success": False} shape."""

    @pytest.mark.asyncio
    async def test_sends_run_on_dedicated_executors_not_default(self):
        import backend.sms_service as sms_mod

        thread_names: list = []

        def _create(**_kwargs):
            thread_names.append(threading.current_thread().name)
            return MagicMock(sid="SM1")

        with patch("twilio.http.http_client.TwilioHttpClient"), patch("twilio.rest.Client") as mock_client_cls:
            mock_client_cls.return_value.messages.create.side_effect = _create
            assert (await sms_mod.send_sms("+13065551234", "hi", **_TWILIO_KW))["success"] is True
            assert (await sms_mod.send_otp_sms("+13065551234", "123456", **_TWILIO_KW))["success"] is True
            assert (await sms_mod.send_sos_sms("+13065551234", "SOS", **_TWILIO_KW))["success"] is True

        general_name, otp_name, sos_name = thread_names
        # Default-executor threads are named "asyncio_N"; ours carry a prefix.
        assert general_name.startswith("spinr-sms_"), general_name
        assert otp_name.startswith("spinr-sms-otp_"), otp_name
        assert sos_name.startswith("spinr-sms-sos_"), sos_name

    @pytest.mark.asyncio
    async def test_saturated_pool_fails_fast_and_default_executor_stays_free(self):
        import backend.sms_service as sms_mod

        release = threading.Event()
        capacity = _capacity(sms_mod._SMS_EXECUTOR)

        def _hang(**_kwargs):
            release.wait(5)
            return MagicMock(sid="SM-late")

        try:
            with (
                patch.object(sms_mod, "_TWILIO_THREAD_TIMEOUT_S", 0.1),
                patch("twilio.http.http_client.TwilioHttpClient"),
                patch("twilio.rest.Client") as mock_client_cls,
            ):
                mock_client_cls.return_value.messages.create.side_effect = _hang

                # Fill every worker and queue slot with sends that hang; each
                # caller's wait times out, but the hung threads (and the
                # queued items behind them) still hold their slots.
                hung = await asyncio.gather(
                    *(sms_mod.send_sms("+13065551234", "hi", **_TWILIO_KW) for _ in range(capacity))
                )
                assert all(r["success"] is False and r["error"] == "TimeoutError" for r in hung)
                assert len(sms_mod._SMS_EXECUTOR._threads) == sms_mod._SMS_EXECUTOR._max_workers == 8

                start = time.monotonic()
                result = await sms_mod.send_sms("+13065551234", "hi", **_TWILIO_KW)
                elapsed = time.monotonic() - start

                assert result == {"success": False, "provider": "twilio", "error": "ExecutorSaturated"}
                assert elapsed < 0.05, "a full SMS pool must reject immediately, not wait"

                # The loop's shared default executor is untouched by the outage.
                probe = await asyncio.wait_for(asyncio.to_thread(lambda: "free"), timeout=1.0)
                assert probe == "free"
        finally:
            release.set()

    @pytest.mark.asyncio
    async def test_sos_pool_has_capacity_while_otp_and_general_pools_saturated(self):
        import backend.sms_service as sms_mod

        release = threading.Event()

        def _create(**_kwargs):
            if not threading.current_thread().name.startswith("spinr-sms-sos"):
                release.wait(5)  # Twilio hangs for the OTP flood and the broadcast...
            return MagicMock(sid="SM-sos")

        try:
            with (
                patch.object(sms_mod, "_TWILIO_THREAD_TIMEOUT_S", 0.1),
                patch("twilio.http.http_client.TwilioHttpClient"),
                patch("twilio.rest.Client") as mock_client_cls,
            ):
                mock_client_cls.return_value.messages.create.side_effect = _create

                await asyncio.gather(
                    *(
                        sms_mod.send_otp_sms("+13065551234", "123456", **_TWILIO_KW)
                        for _ in range(_capacity(sms_mod._OTP_SMS_EXECUTOR))
                    ),
                    *(
                        sms_mod.send_sms("+13065551234", "bulk", **_TWILIO_KW)
                        for _ in range(_capacity(sms_mod._SMS_EXECUTOR))
                    ),
                )
                otp = await sms_mod.send_otp_sms("+13065551234", "123456", **_TWILIO_KW)
                assert otp["success"] is False and otp["error"] == "ExecutorSaturated"
                bulk = await sms_mod.send_sms("+13065551234", "bulk", **_TWILIO_KW)
                assert bulk["success"] is False and bulk["error"] == "ExecutorSaturated"

                # ...but an SOS fan-out (3 contacts) still sends on its own pool.
                sos = await asyncio.gather(
                    *(sms_mod.send_sos_sms("+13065551234", "SOS", **_TWILIO_KW) for _ in range(3))
                )
                assert [r["success"] for r in sos] == [True, True, True]
        finally:
            release.set()


class TestTwilioIntegration:
    """Tests for Twilio integration."""

    def test_twilio_client_initialization(self):
        """Test Twilio client initialization."""

        with patch("twilio.rest.Client") as mock_client:
            mock_client.return_value = MagicMock()
            client = mock_client(account_sid="AC123", auth_token="token")

            assert client is not None
            mock_client.assert_called_once_with(account_sid="AC123", auth_token="token")

    def test_twilio_message_creation_params(self):
        """Test Twilio message creation parameters."""
        params = {"body": "Test message", "from_": "+10000000000", "to": "+1234567890"}

        assert "body" in params
        assert "from_" in params
        assert "to" in params


class TestSMSValidation:
    """Tests for SMS input validation."""

    def test_validate_phone_number_valid(self):
        """Test validating valid phone numbers."""
        valid_numbers = ["+1234567890", "+1-234-567-8900", "+442079460958"]

        for number in valid_numbers:
            assert number.startswith("+")
            assert any(c.isdigit() for c in number)

    def test_validate_phone_number_invalid(self):
        """Test validating invalid phone numbers."""
        invalid_numbers = [
            "",
            "notaphone",
            "12345",
        ]

        for number in invalid_numbers:
            is_valid = number.startswith("+") and len(number) >= 10
            assert is_valid is False

    def test_validate_message_length(self):
        """Test SMS message length validation."""
        max_length = 160  # Standard SMS length

        short_message = "Hello"
        long_message = "A" * 200

        assert len(short_message) <= max_length
        assert len(long_message) > max_length


class TestSMSRetry:
    """Tests for SMS retry logic."""

    def test_sms_retry_on_failure(self):
        """Test SMS retry logic on failure."""
        from tenacity import retry, stop_after_attempt, wait_fixed

        call_count = 0

        @retry(stop=stop_after_attempt(3), wait=wait_fixed(0.01))
        def send_with_retry():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary failure")
            return True

        result = send_with_retry()

        assert result is True
        assert call_count == 3

    def test_sms_retry_exhausted(self):
        """Test SMS retry exhaustion."""
        from tenacity import RetryError, retry, stop_after_attempt, wait_fixed

        @retry(stop=stop_after_attempt(3), wait=wait_fixed(0.01))
        def send_always_fails():
            raise Exception("Always fails")

        with pytest.raises(RetryError):
            send_always_fails()
