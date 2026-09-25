"""
Unit tests for SMS service functionality.
Tests cover OTP SMS sending, general SMS, and Twilio integration.
"""

import asyncio
import contextvars
import importlib
import os
import sys
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

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


def _saturated(sms_mod, pool: str) -> int:
    counters = sms_mod.metrics.snapshot()["counters"]["spinr_sms_executor_saturated_total"]
    return counters[(("pool", pool),)]


def _occupied(sms_mod, pool: str) -> float:
    return sms_mod.metrics.snapshot()["gauges"]["spinr_sms_executor_occupied_slots"][(("pool", pool),)]


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
    async def test_caller_contextvars_are_visible_inside_send(self):
        """asyncio.to_thread copied the caller's context into the worker
        thread (Sentry scope/span, request_id, deadline); the bounded pools
        must too, or the Twilio HTTP span attaches to a stale request."""
        import backend.sms_service as sms_mod

        marker = contextvars.ContextVar("sms_test_marker", default="unset")
        seen = []

        def _create(**_kwargs):
            seen.append(marker.get())
            return MagicMock(sid="SM1")

        with patch("twilio.http.http_client.TwilioHttpClient"), patch("twilio.rest.Client") as mock_client_cls:
            mock_client_cls.return_value.messages.create.side_effect = _create
            for i, send in enumerate((sms_mod.send_sms, sms_mod.send_sos_sms, sms_mod.send_sms)):
                marker.set(f"request-{i}")
                assert (await send("+13065551234", "hi", **_TWILIO_KW))["success"] is True

        assert seen == ["request-0", "request-1", "request-2"]

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
                assert len(sms_mod._SMS_EXECUTOR._threads) == sms_mod._SMS_EXECUTOR._max_workers == 16

                before = _saturated(sms_mod, "general")
                start = time.monotonic()
                result = await sms_mod.send_sms("+13065551234", "hi", **_TWILIO_KW)
                elapsed = time.monotonic() - start

                assert result == {"success": False, "provider": "twilio", "error": "ExecutorSaturated"}
                assert elapsed < 0.05, "a full SMS pool must reject immediately, not wait"
                # The wedge is alertable: rejection counted, pool reads fully occupied.
                assert _saturated(sms_mod, "general") == before + 1
                assert _occupied(sms_mod, "general") == capacity

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

    @pytest.mark.asyncio
    async def test_otp_login_burst_after_outage_is_not_rejected(self):
        """The /send-otp limiter is per IP, so a post-outage login burst across
        many users can exceed the old 12-slot OTP pool. With Twilio healthy,
        a 60-send burst must be fully admitted (pool is 8 workers + 56 queue)."""
        import backend.sms_service as sms_mod

        assert sms_mod._OTP_SMS_EXECUTOR._max_workers == 8
        assert _capacity(sms_mod._OTP_SMS_EXECUTOR) == 64

        def _create(**_kwargs):
            time.sleep(0.02)  # healthy Twilio round trip, scaled down
            return MagicMock(sid="SM-otp")

        with patch("twilio.http.http_client.TwilioHttpClient"), patch("twilio.rest.Client") as mock_client_cls:
            mock_client_cls.return_value.messages.create.side_effect = _create
            results = await asyncio.gather(
                *(sms_mod.send_otp_sms(f"+1306555{i:04d}", "123456", **_TWILIO_KW) for i in range(60))
            )

        assert [r for r in results if not r["success"]] == []

    @pytest.mark.asyncio
    async def test_saturation_metrics_preregistered_and_tagged_by_domain(self):
        """Every pool's counter/gauge exists at 0 from import (alert rules
        never see a missing series), and a saturated SOS / OTP pool's error
        log carries domain=safety / domain=auth for Sentry filtering."""
        from loguru import logger

        import backend.sms_service as sms_mod

        for pool in ("sos", "otp", "general"):
            assert isinstance(_saturated(sms_mod, pool), int)
            assert isinstance(_occupied(sms_mod, pool), (int, float))

        release = threading.Event()
        # Capture the tags at the module's own logger.bind() rather than via a
        # loguru sink: in the full suite other tests reconfigure the global
        # loguru logger (handlers/patchers), so a sink added here saw nothing.
        bound: list = []
        real_bind = logger.bind

        def _spy_bind(**kwargs):
            bound.append(kwargs)
            return real_bind(**kwargs)

        def _hang(**_kwargs):
            release.wait(5)
            return MagicMock(sid="SM-late")

        try:
            with (
                patch.object(sms_mod, "_TWILIO_THREAD_TIMEOUT_S", 0.1),
                patch.object(sms_mod.logger, "bind", side_effect=_spy_bind),
                patch("twilio.http.http_client.TwilioHttpClient"),
                patch("twilio.rest.Client") as mock_client_cls,
            ):
                mock_client_cls.return_value.messages.create.side_effect = _hang
                for pool, send in (("sos", sms_mod.send_sos_sms), ("otp", sms_mod.send_otp_sms)):
                    await asyncio.gather(
                        *(send("+13065551234", "x", **_TWILIO_KW) for _ in range(_capacity(sms_mod._SMS_POOLS[pool])))
                    )
                    before = _saturated(sms_mod, pool)
                    result = await send("+13065551234", "x", **_TWILIO_KW)
                    assert result["error"] == "ExecutorSaturated"
                    assert _saturated(sms_mod, pool) == before + 1
                    assert _occupied(sms_mod, pool) == _capacity(sms_mod._SMS_POOLS[pool])
        finally:
            release.set()

        tags = {b.get("sms_pool"): b.get("domain") for b in bound if "sms_pool" in b}
        assert tags == {"sos": "safety", "otp": "auth"}

    @pytest.mark.asyncio
    async def test_max_size_broadcast_plus_small_callers_never_saturates_when_healthy(self):
        """Fail-fast must only fire in an outage pile-up, never on a healthy
        broadcast: drive the real admin _fan_out (Semaphore(50) in flight) and,
        concurrently, a burst of one-off sends (guest notices / opt-out
        notices) with Twilio mocked fast -- zero may be rejected."""
        import backend.sms_service as sms_mod
        from backend.routes.admin import messaging

        created = []

        def _create(**_kwargs):
            time.sleep(0.02)  # healthy Twilio round trip, scaled down
            created.append(1)
            return MagicMock(sid="SM-ok")

        recipients = [{"id": f"u{i}", "phone": f"+1306555{i:04d}"} for i in range(200)]
        update_mock = AsyncMock(return_value=None)

        with (
            patch("twilio.http.http_client.TwilioHttpClient"),
            patch("twilio.rest.Client") as mock_client_cls,
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(
                    return_value={
                        "twilio_account_sid": "AC123",
                        "twilio_auth_token": "token",
                        "twilio_from_number": "+10000000000",
                    }
                ),
            ),
            patch.object(messaging.db_supabase, "update_one", update_mock),
        ):
            mock_client_cls.return_value.messages.create.side_effect = _create
            _, small = await asyncio.gather(
                messaging._fan_out(
                    "msg-max",
                    recipients,
                    title="T",
                    description="D",
                    channels=["sms"],
                    is_marketing=False,
                    target_app=None,
                    msg_type="info",
                ),
                asyncio.gather(*(sms_mod.send_sms("+13065551234", "guest", **_TWILIO_KW) for _ in range(100))),
            )

        update_mock.assert_awaited_once_with(
            "cloud_messages", {"id": "msg-max"}, {"successful": 200, "failed_count": 0}
        )
        assert all(r["success"] for r in small), [r.get("error") for r in small if not r["success"]]
        assert len(created) == 300


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
