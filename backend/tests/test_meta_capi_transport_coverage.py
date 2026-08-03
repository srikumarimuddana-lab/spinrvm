"""Coverage-closure tests for utils/meta_capi.py (A1c Sub-tier C).

Complements the existing tests/test_meta_conversions.py, which already covers
normalization/hashing (TestNormalization), user_data assembly
(TestBuildUserData), and `send_meta_event`'s retry/fail-fast/isolation
behaviour end to end (TestTransport), plus the FirstRide gate and backend
event-id idempotency at the `services.meta_conversions_service` layer.

This file fills in the branches those don't reach, all within
`backend/utils/meta_capi.py` itself:
- `hash_external_id(None)` / falsy user_id -> None (line 134).
- `build_user_data`'s `client_ip`/`client_user_agent` passthrough fields
  (previously only fbp/fbc and the three hashed fields were exercised).
- `get_config`'s app_settings-read-failure branch, which returns a
  disabled (empty-token) config rather than raising (this module's
  documented exception to the repo's "never swallow errors" rule for
  marketing telemetry — see the module docstring).
- `_should_retry` as a standalone unit across all four branches (None,
  429, 5xx, plain 4xx).
- `send_events` (the batch-send sibling of `send_meta_event`) — previously
  entirely untested: empty-list no-op, not-configured no-op, success on
  first attempt, retry-then-succeed, and exhausted-retries-returns-False.

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import meta_capi

pytestmark = pytest.mark.unit


# ============================================================
# hash_external_id — falsy input
# ============================================================


class TestHashExternalIdFalsy:
    @pytest.mark.parametrize("junk", [None, "", 0])
    def test_falsy_user_id_yields_no_hash(self, junk):
        assert meta_capi.hash_external_id(junk) is None


# ============================================================
# build_user_data — client_ip / client_user_agent passthrough
# ============================================================


class TestBuildUserDataClientFields:
    def test_client_ip_and_user_agent_pass_through_unhashed(self):
        data = meta_capi.build_user_data(
            user_id="u",
            client_ip="203.0.113.9",
            client_user_agent="Mozilla/5.0 SpinrRiderApp",
        )
        assert data["client_ip_address"] == "203.0.113.9"
        assert data["client_user_agent"] == "Mozilla/5.0 SpinrRiderApp"

    def test_absent_client_fields_are_omitted(self):
        data = meta_capi.build_user_data(user_id="u")
        assert "client_ip_address" not in data
        assert "client_user_agent" not in data


# ============================================================
# get_config — app_settings read failure
# ============================================================


class TestGetConfig:
    @pytest.mark.anyio
    async def test_settings_read_failure_yields_disabled_config(self):
        with patch.object(meta_capi, "get_app_settings", AsyncMock(side_effect=RuntimeError("supabase down"))):
            config = await meta_capi.get_config()

        assert config.access_token == ""
        assert config.rider_dataset_id == ""
        assert config.driver_dataset_id == ""
        assert config.test_event_code == ""

    @pytest.mark.anyio
    async def test_settings_read_success_populates_config(self):
        settings = {
            "meta_capi_access_token": "  tok  ",
            "meta_rider_dataset_id": "ds-rider",
            "meta_driver_dataset_id": "ds-driver",
            "meta_test_event_code": "TEST1",
        }
        with patch.object(meta_capi, "get_app_settings", AsyncMock(return_value=settings)):
            config = await meta_capi.get_config()

        assert config.access_token == "tok"
        assert config.rider_dataset_id == "ds-rider"
        assert config.driver_dataset_id == "ds-driver"
        assert config.test_event_code == "TEST1"
        assert config.dataset_for("driver") == "ds-driver"
        assert config.dataset_for("rider") == "ds-rider"


# ============================================================
# _should_retry — standalone unit
# ============================================================


class TestShouldRetry:
    def test_network_error_none_is_retried(self):
        assert meta_capi._should_retry(None) is True

    def test_429_is_retried(self):
        assert meta_capi._should_retry(429) is True

    def test_5xx_is_retried(self):
        assert meta_capi._should_retry(503) is True

    def test_plain_4xx_is_not_retried(self):
        assert meta_capi._should_retry(400) is False
        assert meta_capi._should_retry(404) is False


# ============================================================
# send_events — batch send, previously entirely untested
# ============================================================


def _response(status_code: int, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = text
    return resp


class _FakeClient:
    """Stands in for httpx.AsyncClient as an async context manager."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _no_backoff_sleep():
    """Collapse retry backoff so tests don't actually wait."""
    with patch.object(meta_capi.asyncio, "sleep", new=AsyncMock()):
        yield


class TestSendEvents:
    _EVENTS = [
        {"event_name": "Purchase", "event_id": "evt-1"},
        {"event_name": "FirstRide", "event_id": "evt-2"},
    ]

    async def _send(self, responses, **overrides):
        client = _FakeClient(responses)
        with patch.object(meta_capi.httpx, "AsyncClient", return_value=client):
            kwargs = dict(dataset_id="ds-1", events=self._EVENTS, access_token="token")
            kwargs.update(overrides)
            result = await meta_capi.send_events(**kwargs)
        return result, client

    @pytest.mark.anyio
    async def test_empty_events_list_is_a_no_op_success(self):
        with patch.object(meta_capi.httpx, "AsyncClient") as client_cls:
            assert await meta_capi.send_events(dataset_id="ds-1", events=[], access_token="token") is True
            client_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_unconfigured_token_is_a_silent_no_op(self):
        with patch.object(meta_capi.httpx, "AsyncClient") as client_cls:
            result = await meta_capi.send_events(dataset_id="ds-1", events=self._EVENTS, access_token="")
        assert result is False
        client_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_unconfigured_dataset_is_a_silent_no_op(self):
        with patch.object(meta_capi.httpx, "AsyncClient") as client_cls:
            result = await meta_capi.send_events(dataset_id="", events=self._EVENTS, access_token="token")
        assert result is False
        client_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_success_on_first_attempt_sends_all_events_in_one_call(self):
        result, client = await self._send([_response(200)])
        assert result is True
        assert len(client.calls) == 1
        payload = client.calls[0][1]["json"]
        assert len(payload["data"]) == 2

    @pytest.mark.anyio
    async def test_retries_transient_5xx_then_succeeds(self):
        result, client = await self._send([_response(503), _response(200)])
        assert result is True
        assert len(client.calls) == 2

    @pytest.mark.anyio
    async def test_does_not_retry_4xx(self):
        result, client = await self._send([_response(400, "bad payload")])
        assert result is False
        assert len(client.calls) == 1

    @pytest.mark.anyio
    async def test_gives_up_after_max_attempts_returns_false(self):
        result, client = await self._send([_response(500), _response(500), _response(500)])
        assert result is False
        assert len(client.calls) == meta_capi._MAX_ATTEMPTS

    @pytest.mark.anyio
    async def test_network_error_is_retried_and_never_raises(self):
        result, client = await self._send([OSError("connection reset"), _response(200)])
        assert result is True
        assert len(client.calls) == 2

    @pytest.mark.anyio
    async def test_test_event_code_is_included_only_when_set(self):
        _, client = await self._send([_response(200)], test_event_code="TEST123")
        assert client.calls[0][1]["json"]["test_event_code"] == "TEST123"

        _, client = await self._send([_response(200)])
        assert "test_event_code" not in client.calls[0][1]["json"]
