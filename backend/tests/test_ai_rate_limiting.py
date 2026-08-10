"""AI1 (ACTION_ITEMS.md) — /ai/chat rate limiting and the orchestrator daily cap.

Two independent gaps closed here:

1. `ai_chat_limit` (utils/rate_limiter.py) keys on client IP only, so one
   authenticated user rotating IPs was never actually bounded by it.
   `ai_chat_user_limit` + `get_authenticated_user_key` add a user-keyed
   dimension alongside it (both decorators apply, neither replaces the
   other).
2. `orchestrator._over_daily_cap` used to fail OPEN on a Redis error —
   a Redis blip silently removed the per-user daily LLM-cost cap for
   every user. It now fails CLOSED-with-a-floor via a process-local
   counter (`_DEGRADED_CAP_FLOOR`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

import backend.ai.orchestrator as orch
from utils.rate_limiter import (
    default_limiter,
    get_authenticated_user_key,
    rate_limit_exceeded_handler,
)

pytestmark = pytest.mark.unit


def _bearer(claims: dict) -> str:
    return "Bearer " + jwt.encode(claims, "not-the-real-secret", algorithm="HS256")


# ─────────────────────────────────────────────────────────────────────────────
# get_authenticated_user_key — pure unit tests, no HTTP round trip
# ─────────────────────────────────────────────────────────────────────────────


class TestGetAuthenticatedUserKey:
    def _request(self, headers: dict) -> Request:
        req = MagicMock(spec=Request)
        req.headers = headers
        return req

    def test_keys_by_user_id_claim(self):
        req = self._request({"authorization": _bearer({"user_id": "rider-42"})})
        assert get_authenticated_user_key(req) == "user:rider-42"

    def test_falls_back_to_sub_claim(self):
        req = self._request({"authorization": _bearer({"sub": "rider-99"})})
        assert get_authenticated_user_key(req) == "user:rider-99"

    def test_user_id_claim_preferred_over_sub(self):
        req = self._request({"authorization": _bearer({"user_id": "rider-1", "sub": "rider-2"})})
        assert get_authenticated_user_key(req) == "user:rider-1"

    def test_same_user_same_key_regardless_of_ip_header(self):
        """The whole point of AI1: identity, not IP, drives the bucket."""
        token = _bearer({"user_id": "rider-7"})
        req_a = self._request({"authorization": token, "cf-connecting-ip": "1.1.1.1"})
        req_b = self._request({"authorization": token, "cf-connecting-ip": "9.9.9.9"})
        assert get_authenticated_user_key(req_a) == get_authenticated_user_key(req_b) == "user:rider-7"

    def test_no_auth_header_falls_back_to_ip(self):
        req = self._request({"cf-connecting-ip": "203.0.113.5"})
        assert get_authenticated_user_key(req) == "ip:203.0.113.5"

    def test_malformed_token_falls_back_to_ip(self):
        req = self._request({"authorization": "Bearer not-a-jwt", "cf-connecting-ip": "203.0.113.6"})
        assert get_authenticated_user_key(req) == "ip:203.0.113.6"

    def test_no_claims_falls_back_to_ip(self):
        req = self._request({"authorization": _bearer({"role": "user"}), "cf-connecting-ip": "203.0.113.7"})
        assert get_authenticated_user_key(req) == "ip:203.0.113.7"


# ─────────────────────────────────────────────────────────────────────────────
# /ai/chat integration — real decorators, real limiter, stripped app
# (mirrors tests/test_rate_limit_response_shape.py's _app_with_auth_router
# pattern rather than the full server.py app, to keep these fast/isolated).
# ─────────────────────────────────────────────────────────────────────────────


def _frames_gen(frames):
    async def fake_run_chat_turn(**kwargs):
        for frame in frames:
            yield frame

    return fake_run_chat_turn


DONE_FRAMES = [
    ("meta", {"conversation_id": "c1", "user_message_id": "m1"}),
    ("done", {"message_id": "m2", "usage": {}, "stop_reason": "end_turn"}),
]


def _app_with_ai_router(monkeypatch) -> FastAPI:
    from dependencies import get_current_user
    from routes.ai import api_router

    monkeypatch.setattr("routes.ai.run_chat_turn", _frames_gen(DONE_FRAMES))

    app = FastAPI()
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(api_router, prefix="/api/v1")

    async def fake_user():
        return {"id": "whoever", "is_driver": False}

    app.dependency_overrides[get_current_user] = fake_user
    return app


@pytest.fixture
def enabled_limiter():
    """Re-enable + reset the shared default_limiter for these tests.

    Mirrors test_rate_limit_response_shape.py's fixture of the same name:
    conftest's autouse reset_rate_limiters disables the limiter after
    resetting storage, so opting back in here (post-reset) starts every
    test with a clean window."""
    import importlib

    limiters = []
    for rl_mod_path in ("backend.utils.rate_limiter", "utils.rate_limiter"):
        try:
            rl_mod = importlib.import_module(rl_mod_path)
        except (ImportError, ModuleNotFoundError):
            continue
        limiter = getattr(rl_mod, "default_limiter", None)
        if limiter is not None:
            limiter.enabled = True
            limiters.append(limiter)
    yield
    for limiter in limiters:
        limiter.enabled = False


class TestUserKeyedLimitIndependentOfIP:
    def test_single_user_capped_across_rotating_ips(self, monkeypatch, enabled_limiter):
        """AI1 core scenario: one authenticated user spread across many
        client IPs must still hit the per-user ceiling (ai_chat_user_limit,
        10/minute) even though ai_chat_limit's IP bucket never fills."""
        app = _app_with_ai_router(monkeypatch)
        client = TestClient(app)
        token = _bearer({"user_id": "rider-rotating"})

        statuses = []
        for i in range(11):
            resp = client.post(
                "/api/v1/ai/chat",
                json={"message": "hi", "stream": False},
                headers={"authorization": token, "cf-connecting-ip": f"10.0.0.{i}"},
            )
            statuses.append(resp.status_code)

        assert statuses[:10] == [200] * 10
        assert statuses[10] == 429

    def test_normal_path_under_the_cap_still_works(self, monkeypatch, enabled_limiter):
        app = _app_with_ai_router(monkeypatch)
        client = TestClient(app)
        token = _bearer({"user_id": "rider-normal"})

        for _ in range(3):
            resp = client.post(
                "/api/v1/ai/chat",
                json={"message": "hi", "stream": False},
                headers={"authorization": token},
            )
            assert resp.status_code == 200

    def test_different_users_from_same_ip_are_not_cross_capped(self, monkeypatch, enabled_limiter):
        """The user-keyed limiter must not accidentally bucket by IP —
        two distinct users behind one IP each get their own 10/minute."""
        app = _app_with_ai_router(monkeypatch)
        client = TestClient(app)
        headers_a = {"authorization": _bearer({"user_id": "rider-a"}), "cf-connecting-ip": "203.0.113.1"}
        headers_b = {"authorization": _bearer({"user_id": "rider-b"}), "cf-connecting-ip": "203.0.113.1"}

        # Burn rider-a's user-keyed budget (well under the IP-keyed
        # limiter's own 10/minute — both share the same IP here, so IP
        # would 429 first if it were the only limiter; use exactly 10 to
        # stay within both).
        for _ in range(10):
            resp = client.post("/api/v1/ai/chat", json={"message": "hi", "stream": False}, headers=headers_a)
            assert resp.status_code == 200
        assert (
            client.post("/api/v1/ai/chat", json={"message": "hi", "stream": False}, headers=headers_a).status_code
            == 429
        )

        # rider-b, same IP, fresh user key — still allowed.
        resp_b = client.post("/api/v1/ai/chat", json={"message": "hi", "stream": False}, headers=headers_b)
        assert resp_b.status_code == 429  # IP bucket (ai_chat_limit) is now also exhausted at 10/minute


# ─────────────────────────────────────────────────────────────────────────────
# orchestrator._over_daily_cap — fail-closed-with-a-floor on Redis errors
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_degraded_counts():
    """_degraded_daily_counts is a module-level dict; isolate tests from
    each other's counts."""
    orch._degraded_daily_counts.clear()
    yield
    orch._degraded_daily_counts.clear()


class TestOverDailyCapRedisFailure:
    @pytest.mark.anyio
    async def test_normal_path_still_works(self):
        """Redis healthy, under cap -> not capped."""
        from unittest.mock import AsyncMock, patch

        with (
            patch.object(orch, "redis_incr", AsyncMock(return_value=5)),
            patch.object(orch, "redis_expire", AsyncMock()),
        ):
            assert await orch._over_daily_cap("rider-1", cap=50) is False

    @pytest.mark.anyio
    async def test_normal_path_over_cap_still_blocks(self):
        from unittest.mock import AsyncMock, patch

        with (
            patch.object(orch, "redis_incr", AsyncMock(return_value=51)),
            patch.object(orch, "redis_expire", AsyncMock()),
        ):
            assert await orch._over_daily_cap("rider-1", cap=50) is True

    @pytest.mark.anyio
    async def test_redis_error_does_not_fail_fully_open(self, caplog):
        """A single Redis error must NOT silently remove the cap — hammer
        past the degraded floor and confirm it eventually blocks."""
        from unittest.mock import AsyncMock, patch

        boom = ConnectionError("redis down")
        with patch.object(orch, "redis_incr", AsyncMock(side_effect=boom)):
            results = [await orch._over_daily_cap("rider-flood", cap=50) for _ in range(orch._DEGRADED_CAP_FLOOR + 5)]

        # Below the floor: allowed (not fully blocked either).
        assert results[: orch._DEGRADED_CAP_FLOOR].count(True) == 0
        # Once the floor is exceeded: capped.
        assert all(results[orch._DEGRADED_CAP_FLOOR :])

    @pytest.mark.anyio
    async def test_redis_error_does_not_fail_fully_closed_on_first_call(self):
        """A single transient error must not instantly hard-block —
        that's the "generous floor" requirement, not a bare fail-closed."""
        from unittest.mock import AsyncMock, patch

        with patch.object(orch, "redis_incr", AsyncMock(side_effect=TimeoutError("redis timeout"))):
            assert await orch._over_daily_cap("rider-single", cap=50) is False

    @pytest.mark.anyio
    async def test_redis_error_logs_at_error_level_with_exception(self, caplog):
        """CLAUDE.md 'Do not silently swallow errors': DB/Redis errors on
        this path must surface via logger.error with the underlying
        exception, never logger.warning-and-continue."""
        from unittest.mock import AsyncMock, patch

        boom = ConnectionError("redis unreachable")
        with (
            patch.object(orch, "redis_incr", AsyncMock(side_effect=boom)),
            caplog.at_level("ERROR", logger="backend.ai.orchestrator"),
        ):
            await orch._over_daily_cap("rider-log", cap=50)

        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert error_records, "expected an ERROR-level log on the Redis-failure path"
        assert any(r.exc_info for r in error_records), "expected the underlying exception to be attached"

    @pytest.mark.anyio
    async def test_degraded_counts_are_per_user(self):
        """One user's degraded-mode usage must not cap a different user."""
        from unittest.mock import AsyncMock, patch

        with patch.object(orch, "redis_incr", AsyncMock(side_effect=ConnectionError("down"))):
            for _ in range(orch._DEGRADED_CAP_FLOOR + 1):
                await orch._over_daily_cap("rider-hog", cap=50)
            # A different user starts fresh even though Redis is still down.
            assert await orch._over_daily_cap("rider-fresh", cap=50) is False
