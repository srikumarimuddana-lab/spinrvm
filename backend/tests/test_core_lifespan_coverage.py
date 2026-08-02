"""Coverage for core/lifespan.py (A1c, Sub-tier B).

The central startup/shutdown module: DB health check + spawns 17 background
asyncio loops (per CLAUDE.md's Key Backend Files section). Had no dedicated
test file; only 58.52% coverage.

The critical regression to lock in is the `ENV=="test"` no-op guard from
issue #2981 (see the long comment above `_skip_background_loops` in the
source): under a real `TestClient(app)` lifespan, spawning all 17 loops for
real would fire them concurrently against whatever narrow per-test mock is
active, causing nondeterministic full-suite pollution — cancelling on
shutdown doesn't help because `task.cancel()` doesn't stop an
already-started `run_in_executor()` OS thread. `tests/conftest.py` sets
`ENV=test` by default (line 44), so entering `lifespan()` under pytest is
safe: `_skip_background_loops` is True and every `_spawn()` call becomes a
no-op log line instead of a real `asyncio.create_task()`.

This file focuses on: `init_database()` (module-level, directly testable
in isolation), `cleanup_database()`, and `lifespan()`'s ENV=test no-op
guard plus its DB-init/Stripe-config/SGI-template-check gating logic
(production raises, non-production logs and continues). It does not
attempt to individually test each of the 17 loops' own import-try/except
block — those loops each have their own dedicated direct-call unit tests
elsewhere (per the source's own comment), and under ENV=test none of them
actually spawn here regardless of which import succeeds.

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

pytestmark = pytest.mark.unit


def _settings_mock(env="test", **overrides):
    s = MagicMock()
    s.ENV = env
    s.REDIS_URL = overrides.get("REDIS_URL", "redis://test")
    s.RATE_LIMIT_REDIS_URL = overrides.get("RATE_LIMIT_REDIS_URL", "")
    s.WS_REDIS_URL = overrides.get("WS_REDIS_URL", "")
    s.SUPABASE_REGION = overrides.get("SUPABASE_REGION", "ca-central-1")
    return s


# ── init_database ────────────────────────────────────────────────────────


class TestInitDatabase:
    @pytest.mark.anyio
    async def test_unconfigured_supabase_raises_in_production(self, monkeypatch):
        from backend.core import lifespan

        monkeypatch.setattr(lifespan, "supabase", None)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="production"))
        with pytest.raises(RuntimeError):
            await lifespan.init_database()

    @pytest.mark.anyio
    async def test_unconfigured_supabase_warns_and_returns_none_outside_production(self, monkeypatch):
        from backend.core import lifespan

        monkeypatch.setattr(lifespan, "supabase", None)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="development"))
        result = await lifespan.init_database()
        assert result is None

    @pytest.mark.anyio
    async def test_missing_region_logs_error_in_production_but_does_not_raise(self, monkeypatch):
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="production", SUPABASE_REGION=""))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))
        result = await lifespan.init_database()
        assert result is fake_supabase

    @pytest.mark.anyio
    async def test_wrong_region_logs_error_in_production_but_does_not_raise(self, monkeypatch):
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(
            lifespan, "settings", _settings_mock(env="production", SUPABASE_REGION="us-east-1")
        )
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))
        result = await lifespan.init_database()
        assert result is fake_supabase

    @pytest.mark.anyio
    async def test_correct_region_in_production_succeeds(self, monkeypatch):
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(
            lifespan, "settings", _settings_mock(env="production", SUPABASE_REGION="ca-central-1")
        )
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))
        result = await lifespan.init_database()
        assert result is fake_supabase

    @pytest.mark.anyio
    async def test_health_check_success_returns_supabase(self, monkeypatch):
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test"))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))
        result = await lifespan.init_database()
        assert result is fake_supabase

    @pytest.mark.anyio
    async def test_health_check_failure_raises_in_production(self, monkeypatch):
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="production"))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(side_effect=ConnectionError("db down")))
        with pytest.raises(ConnectionError):
            await lifespan.init_database()

    @pytest.mark.anyio
    async def test_health_check_failure_warns_and_still_returns_supabase_outside_production(self, monkeypatch):
        """A health-check failure outside production is non-fatal — the
        function still returns the (possibly-degraded) supabase client
        rather than None, so local dev without full Supabase setup can
        still boot."""
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="development"))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(side_effect=ConnectionError("db down")))
        result = await lifespan.init_database()
        assert result is fake_supabase


# ── cleanup_database ─────────────────────────────────────────────────────


class TestCleanupDatabase:
    @pytest.mark.anyio
    async def test_completes_without_raising(self):
        from backend.core.lifespan import cleanup_database

        await cleanup_database(MagicMock())  # must not raise


# ── lifespan() — ENV=test no-op guard + gating logic ────────────────────


class TestLifespanEnvTestGuard:
    @pytest.mark.anyio
    async def test_env_test_never_spawns_real_background_tasks(self, monkeypatch):
        """The core regression this file must lock in (issue #2981): under
        ENV=test, asyncio.create_task must never be called for any of the
        17 background loops — every _spawn() call degrades to a log line."""
        from backend.core import lifespan

        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test"))
        monkeypatch.setattr(lifespan, "init_database", AsyncMock(return_value=MagicMock()))

        app = FastAPI()
        create_task_calls = []
        real_create_task = __import__("asyncio").create_task

        def _spy_create_task(coro, **kwargs):
            create_task_calls.append(kwargs.get("name"))
            # Actually create the task so `_restartable`'s wrapper doesn't
            # leak an un-awaited coroutine warning if ever called for real —
            # but under ENV=test this spy should never be invoked for any
            # of the 17 loop names, only possibly for internal helper tasks.
            return real_create_task(coro, **kwargs)

        # `lifespan()` does `import asyncio` LOCALLY inside its own body
        # (no module-level `asyncio` attribute on `core.lifespan` exists
        # until the function actually runs), so there is no
        # "backend.core.lifespan.asyncio" string path to patch before that
        # point. Patch the real stdlib `asyncio` module directly instead —
        # the local `import asyncio` re-binds to the same module object
        # regardless of where the import statement lives.
        import asyncio as real_asyncio_module

        with patch.object(real_asyncio_module, "create_task", side_effect=_spy_create_task):
            async with lifespan.lifespan(app):
                pass

        # None of the well-known loop names should have been spawned.
        loop_like_names = [n for n in create_task_calls if n and "(" in n]
        assert loop_like_names == []

    @pytest.mark.anyio
    async def test_lifespan_completes_full_startup_and_shutdown_under_env_test(self, monkeypatch):
        from backend.core import lifespan

        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test"))
        monkeypatch.setattr(lifespan, "init_database", AsyncMock(return_value=MagicMock()))

        app = FastAPI()
        async with lifespan.lifespan(app):
            assert app.state.db is not None
        # Reaching here means startup, yield, and shutdown all completed
        # without raising — including the background_tasks cancel/gather
        # tail (empty list under ENV=test, so it's a no-op).

    @pytest.mark.anyio
    async def test_db_init_failure_propagates_and_aborts_startup(self, monkeypatch):
        from backend.core import lifespan

        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test"))
        monkeypatch.setattr(lifespan, "init_database", AsyncMock(side_effect=RuntimeError("db unreachable")))

        app = FastAPI()
        with pytest.raises(RuntimeError):
            async with lifespan.lifespan(app):
                pass  # pragma: no cover - should never reach the yield

    # NOTE: a "missing Redis warns in production" test was deliberately NOT
    # added here. Testing that branch requires ENV="production", under which
    # `_skip_background_loops` is False — entering the full `lifespan()`
    # context manager would then attempt to really `asyncio.create_task()`
    # all 17 background loops (imports permitting), which is exactly the
    # full-suite-pollution failure mode issue #2981 fixed and this file's
    # own module docstring warns against. Not worth the risk for one log
    # line; the missing-Redis warning's condition (`not any([REDIS_URL,
    # RATE_LIMIT_REDIS_URL, WS_REDIS_URL])`) is simple enough to read-review
    # directly instead.

    @pytest.mark.anyio
    async def test_stripe_config_failure_raises_in_production(self, monkeypatch):
        from backend.core import lifespan

        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="production"))
        monkeypatch.setattr(lifespan, "init_database", AsyncMock(return_value=MagicMock()))

        app = FastAPI()
        with patch("utils.stripe_config.configure_stripe", side_effect=RuntimeError("stripe misconfigured")):
            with pytest.raises(RuntimeError):
                async with lifespan.lifespan(app):
                    pass  # pragma: no cover

    @pytest.mark.anyio
    async def test_stripe_config_failure_outside_production_logs_and_continues(self, monkeypatch):
        from backend.core import lifespan

        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test"))
        monkeypatch.setattr(lifespan, "init_database", AsyncMock(return_value=MagicMock()))

        app = FastAPI()
        with patch("utils.stripe_config.configure_stripe", side_effect=RuntimeError("stripe misconfigured")):
            async with lifespan.lifespan(app):
                pass  # must not raise — ENV=test tolerates the failure


# ── _restartable / _spawn behavior (exercised indirectly via lifespan) ──


class TestSpawnGuardLogsInsteadOfSpawning:
    @pytest.mark.anyio
    async def test_spawn_skip_message_logged_for_env_test(self, monkeypatch):
        """Confirms the log-based signal an operator/CI would see confirming
        the skip actually happened, not just that no task object exists."""
        from backend.core import lifespan

        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test"))
        monkeypatch.setattr(lifespan, "init_database", AsyncMock(return_value=MagicMock()))

        app = FastAPI()
        with patch.object(lifespan.logger, "info") as mock_log_info:
            async with lifespan.lifespan(app):
                pass

        skip_messages = [
            call.args[0] for call in mock_log_info.call_args_list if call.args and "Skipped background task" in call.args[0]
        ]
        assert skip_messages, "expected at least one 'Skipped background task in ENV=test' log line"
