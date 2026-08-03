"""Coverage top-up for utils/meta_capi.py.

test_meta_conversions.py already covers normalization, hashing, and the
send_meta_event retry/backoff machinery in depth. This file fills the
remaining gaps reported at 72% (152 stmts / 42 missing: lines 134, 174, 176,
215-216, 232, 347-400):

  - hash_external_id's falsy-input short circuit (134)
  - build_user_data's client_ip_address / client_user_agent passthrough
    (174, 176)
  - get_config's exception-swallowing fallback to a disabled config (215-216)
  - _should_retry called directly, including its False branch (232)
  - send_events end-to-end: the empty-list short circuit, the
    unconfigured-credentials no-op, success, retryable failure -> success,
    non-retryable fail-fast, and total-exhaustion paths (347-400), mirroring
    the send_meta_event coverage in test_meta_conversions.py.

Test-only change — no application code modified.

Privacy review (CLAUDE.md PIPEDA "never send unhashed PII" concern): read the
whole module for this task. build_user_data (meta_capi.py:139-178) hashes
email/phone/user_id via SHA-256 before they ever touch the payload, and
fbp/fbc/client_ip_address/client_user_agent are sent unhashed — which matches
Meta's own Conversions API spec (fbp/fbc are opaque Meta-issued tokens, and
Meta requires client_ip_address/client_user_agent in plaintext to do its own
matching; Meta hashes IP on receipt). No raw email/phone/user_id leak was
found in this module. NOT FLAGGING A BUG here — noting the review happened
per the task's instructions, since silence would be indistinguishable from
"didn't check."
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import meta_capi

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_backoff_sleep():
    """Collapse retry backoff so tests don't actually wait."""
    with patch.object(meta_capi.asyncio, "sleep", new=AsyncMock()):
        yield


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


# ── hash_external_id falsy short circuit (line 134) ─────────────────────────


class TestHashExternalIdFalsy:
    @pytest.mark.parametrize("junk", [None, "", 0])
    def test_falsy_user_id_yields_no_hash(self, junk):
        assert meta_capi.hash_external_id(junk) is None

    def test_whitespace_only_user_id_yields_no_hash(self):
        """Passes the `not user_id` guard (non-empty string) but strips to
        empty, exercising the second falsy check on the cleaned value."""
        assert meta_capi.hash_external_id("   ") is None


# ── build_user_data: client_ip / client_user_agent passthrough (174, 176) ──


class TestBuildUserDataClientMeta:
    def test_client_ip_and_user_agent_are_included_unhashed_when_present(self):
        data = meta_capi.build_user_data(
            user_id="u1",
            client_ip="203.0.113.5",
            client_user_agent="Mozilla/5.0 SpinrRider/1.0",
        )
        assert data["client_ip_address"] == "203.0.113.5"
        assert data["client_user_agent"] == "Mozilla/5.0 SpinrRider/1.0"

    def test_client_ip_and_user_agent_omitted_when_absent(self):
        data = meta_capi.build_user_data(user_id="u1")
        assert "client_ip_address" not in data
        assert "client_user_agent" not in data


# ── get_config: settings-read failure disables tracking (215-216) ──────────


class TestGetConfig:
    @pytest.mark.anyio
    async def test_settings_load_failure_returns_disabled_config(self):
        with patch.object(meta_capi, "get_app_settings", AsyncMock(side_effect=RuntimeError("db down"))):
            config = await meta_capi.get_config()
        assert config.access_token == ""
        assert config.rider_dataset_id == ""
        assert config.driver_dataset_id == ""
        assert config.test_event_code == ""

    @pytest.mark.anyio
    async def test_settings_load_success_populates_config(self):
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


# ── _should_retry direct coverage, including the False branch (232) ────────


class TestShouldRetryDirect:
    def test_none_status_retries(self):
        assert meta_capi._should_retry(None) is True

    def test_429_retries(self):
        assert meta_capi._should_retry(429) is True

    def test_5xx_retries(self):
        assert meta_capi._should_retry(500) is True
        assert meta_capi._should_retry(503) is True

    def test_4xx_other_than_429_does_not_retry(self):
        assert meta_capi._should_retry(400) is False
        assert meta_capi._should_retry(404) is False

    def test_2xx_does_not_retry(self):
        assert meta_capi._should_retry(200) is False


# ── send_events (347-400) ───────────────────────────────────────────────────


class TestSendEvents:
    async def _send(self, responses, **overrides):
        client = _FakeClient(responses)
        with patch.object(meta_capi.httpx, "AsyncClient", return_value=client):
            kwargs = dict(
                dataset_id="ds-1",
                events=[
                    {"event_name": "Purchase", "event_id": "e1"},
                    {"event_name": "FirstRide", "event_id": "e2"},
                ],
                access_token="token",
            )
            kwargs.update(overrides)
            result = await meta_capi.send_events(**kwargs)
        return result, client

    @pytest.mark.anyio
    async def test_empty_events_short_circuits_true_without_a_request(self):
        with patch.object(meta_capi.httpx, "AsyncClient") as client_cls:
            assert await meta_capi.send_events(dataset_id="ds-1", events=[], access_token="token") is True
            client_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_unconfigured_credentials_is_a_silent_no_op(self):
        with patch.object(meta_capi.httpx, "AsyncClient") as client_cls:
            result = await meta_capi.send_events(
                dataset_id="ds-1",
                events=[{"event_name": "Purchase", "event_id": "e1"}],
                access_token="",
            )
        assert result is False
        client_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_missing_dataset_id_is_a_silent_no_op(self):
        with patch.object(meta_capi.httpx, "AsyncClient") as client_cls:
            result = await meta_capi.send_events(
                dataset_id="",
                events=[{"event_name": "Purchase", "event_id": "e1"}],
                access_token="token",
            )
        assert result is False
        client_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_success_on_first_attempt_sends_a_single_batched_request(self):
        result, client = await self._send([_response(200)])
        assert result is True
        assert len(client.calls) == 1
        payload = client.calls[0][1]["json"]
        assert [e["event_name"] for e in payload["data"]] == ["Purchase", "FirstRide"]

    @pytest.mark.anyio
    async def test_test_event_code_included_only_when_set(self):
        _, client = await self._send([_response(200)], test_event_code="TESTBATCH")
        assert client.calls[0][1]["json"]["test_event_code"] == "TESTBATCH"

        _, client = await self._send([_response(200)])
        assert "test_event_code" not in client.calls[0][1]["json"]

    @pytest.mark.anyio
    async def test_retries_transient_5xx_then_succeeds(self):
        result, client = await self._send([_response(503), _response(200)])
        assert result is True
        assert len(client.calls) == 2

    @pytest.mark.anyio
    async def test_retries_429_then_succeeds(self):
        result, client = await self._send([_response(429), _response(200)])
        assert result is True
        assert len(client.calls) == 2

    @pytest.mark.anyio
    async def test_does_not_retry_4xx(self):
        result, client = await self._send([_response(400, "Invalid parameter")])
        assert result is False
        assert len(client.calls) == 1

    @pytest.mark.anyio
    async def test_gives_up_after_max_attempts(self):
        result, client = await self._send([_response(500), _response(500), _response(500)])
        assert result is False
        assert len(client.calls) == meta_capi._MAX_ATTEMPTS

    @pytest.mark.anyio
    async def test_network_error_is_retried_and_never_raises(self):
        result, client = await self._send([OSError("connection reset"), _response(200)])
        assert result is True
        assert len(client.calls) == 2

    @pytest.mark.anyio
    async def test_total_transport_failure_returns_false_without_raising(self):
        result, _ = await self._send([OSError("down"), OSError("down"), OSError("down")])
        assert result is False

    @pytest.mark.anyio
    async def test_access_token_sent_as_param_not_in_url(self):
        _, client = await self._send([_response(200)])
        url, kwargs = client.calls[0]
        assert "token" not in url
        assert kwargs["params"]["access_token"] == "token"
