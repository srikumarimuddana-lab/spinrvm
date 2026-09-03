"""Tests for backend/repositories/dispatch_pool.py (C50 Phase 1, T9).

The single most important test in this file is
``test_lifespan_startup_byte_identical_when_flag_off`` — with
DISPATCH_POOL_DSN unset (T8's default everywhere until a human sets the Fly
secret), lifespan.init_database's new C50 block must be a true no-op: no
pool import, no app_settings read, no log line beyond what existed before
this change. See docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _settings_mock(env="test", **overrides):
    s = MagicMock()
    s.ENV = env
    s.DISPATCH_POOL_DSN = overrides.get("DISPATCH_POOL_DSN", "")
    s.DISPATCH_POOL_MIN_SIZE = overrides.get("DISPATCH_POOL_MIN_SIZE", 1)
    s.DISPATCH_POOL_MAX_SIZE = overrides.get("DISPATCH_POOL_MAX_SIZE", 8)
    return s


# ── Critical acceptance check: flag off => byte-identical startup ─────────


class TestLifespanByteIdenticalWhenFlagOff:
    @pytest.mark.anyio
    async def test_init_database_does_not_touch_app_settings_when_dsn_unset(self, monkeypatch):
        """DISPATCH_POOL_DSN empty (the real default, T8) must short-circuit
        BEFORE settings_loader.get_app_settings() is ever called — so a flag
        read never happens on the hot boot path when the DSN secret is
        unset, which is every environment today."""
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test", DISPATCH_POOL_DSN=""))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))

        with patch("backend.settings_loader.get_app_settings", AsyncMock()) as mock_get_settings:
            result = await lifespan.init_database()

        assert result is fake_supabase
        mock_get_settings.assert_not_called()

    @pytest.mark.anyio
    async def test_init_database_does_not_import_dispatch_pool_when_dsn_unset(self, monkeypatch):
        """init_pool() is never called when the DSN is unset. (Review note,
        2026-09-03: the patch below imports dispatch_pool itself, so this
        test cannot prove the module is never imported — only that the pool
        is never opened. The lifespan branch's local import sits behind the
        DSN check, so the import is skipped too, but that is not what is
        asserted here.)"""
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test", DISPATCH_POOL_DSN=""))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))

        with patch("backend.repositories.dispatch_pool.init_pool", AsyncMock()) as mock_init_pool:
            await lifespan.init_database()

        mock_init_pool.assert_not_called()

    @pytest.mark.anyio
    async def test_init_database_emits_no_new_log_lines_when_dsn_unset(self, monkeypatch):
        """No new logger.info/error/warning calls beyond the pre-existing
        health-check lines — a log-line count regression would mean the new
        block executed something even though the DSN is unset."""
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test", DISPATCH_POOL_DSN=""))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))

        with patch.object(lifespan.logger, "info") as mock_info, patch.object(lifespan.logger, "error") as mock_error:
            await lifespan.init_database()

        dispatch_pool_lines = [
            call.args[0]
            for call in (mock_info.call_args_list + mock_error.call_args_list)
            if call.args and "dispatch_pool" in str(call.args[0])
        ]
        assert dispatch_pool_lines == [], f"unexpected dispatch_pool log lines: {dispatch_pool_lines}"

    @pytest.mark.anyio
    async def test_real_settings_default_dispatch_pool_dsn_is_falsy(self):
        """Not a mock: proves the REAL Settings model's default keeps the
        lifespan branch closed, so this isn't just testing the test double."""
        from backend.core.config import Settings

        assert Settings.model_fields["DISPATCH_POOL_DSN"].default == ""

    @pytest.mark.anyio
    async def test_cleanup_database_close_pool_is_a_noop_when_never_opened(self, monkeypatch):
        """cleanup_database's new close_pool() call must not raise or do
        anything observable when init_pool() was never called (flag/DSN
        off) — dispatch_pool.close_pool() guards on _pool is None."""
        from backend.core import lifespan
        from backend.repositories import dispatch_pool

        # Ensure module-global pool state starts clean regardless of test order.
        monkeypatch.setattr(dispatch_pool, "_pool", None)

        await lifespan.cleanup_database(MagicMock())  # must not raise
        assert dispatch_pool.is_open() is False


# ── DSN-set branch: the actual code path once a human sets the Fly secret ─
#
# Previously entirely untested (Tara's review): every test above proves the
# flag/DSN-OFF no-op path. These prove the ON path behaves as documented --
# app_settings IS read exactly once the DSN is set, the flag value gates
# whether init_pool() is called, and an app_settings read failure fails
# closed (logs at ERROR, treats the flag as off) rather than either
# crashing boot or silently defaulting to "on".


class TestLifespanDsnSetBranch:
    @pytest.mark.anyio
    async def test_dsn_set_app_settings_read_is_bounded_and_timeout_fails_closed(self, monkeypatch):
        """Review fix (2026-09-03): the boot-time flag read runs before any
        deadline middleware exists, so it must carry its own timeout; a
        timeout is treated like any other read failure (flag off, ERROR)."""
        import asyncio

        from backend.core import lifespan

        monkeypatch.setattr(lifespan, "supabase", MagicMock())
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test", DISPATCH_POOL_DSN="postgresql://x"))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))

        captured = {}
        real_wait_for = asyncio.wait_for

        async def _spy_wait_for(aw, timeout=None):
            captured["timeout"] = timeout
            aw.close()
            raise asyncio.TimeoutError()

        monkeypatch.setattr(lifespan.asyncio, "wait_for", _spy_wait_for)
        with (
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
            patch("backend.repositories.dispatch_pool.init_pool", AsyncMock()) as mock_init_pool,
            patch.object(lifespan.logger, "error") as mock_error,
        ):
            await lifespan.init_database()
        monkeypatch.setattr(lifespan.asyncio, "wait_for", real_wait_for)

        assert captured["timeout"] == 10.0
        mock_init_pool.assert_not_called()
        assert any("dispatch_direct_pool_enabled" in str(c.args[0]) for c in mock_error.call_args_list)

    @pytest.mark.anyio
    async def test_dsn_set_flag_true_calls_init_pool(self, monkeypatch):
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test", DISPATCH_POOL_DSN="postgresql://real-dsn"))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))

        with (
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"dispatch_direct_pool_enabled": True}),
            ) as mock_get_settings,
            patch("backend.repositories.dispatch_pool.init_pool", AsyncMock(return_value=MagicMock())) as mock_init,
        ):
            result = await lifespan.init_database()

        assert result is fake_supabase
        mock_get_settings.assert_awaited_once()
        mock_init.assert_awaited_once_with(True)

    @pytest.mark.anyio
    async def test_dsn_set_flag_false_reads_settings_but_does_not_call_init_pool(self, monkeypatch):
        """DSN set (a human configured the Fly secret) but the app_settings
        rollback switch is still off -- app_settings IS read (that's the
        whole point of a runtime kill switch), but init_pool() must not be
        called."""
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test", DISPATCH_POOL_DSN="postgresql://real-dsn"))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))

        with (
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"dispatch_direct_pool_enabled": False}),
            ) as mock_get_settings,
            patch("backend.repositories.dispatch_pool.init_pool", AsyncMock()) as mock_init,
        ):
            await lifespan.init_database()

        mock_get_settings.assert_awaited_once()
        mock_init.assert_not_called()

    @pytest.mark.anyio
    async def test_dsn_set_app_settings_read_failure_fails_closed(self, monkeypatch):
        """An unreadable app_settings row at boot must not crash the API
        (matches every other flag reader's tolerance) but must be logged
        loudly (AGENTS.md: DB errors must surface, never be silently
        swallowed) and must leave the pool CLOSED -- 'off' is the safe
        default for an unreadable rollback switch, not 'on'."""
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(lifespan, "settings", _settings_mock(env="test", DISPATCH_POOL_DSN="postgresql://real-dsn"))
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))

        with (
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(side_effect=RuntimeError("app_settings table unreachable")),
            ),
            patch("backend.repositories.dispatch_pool.init_pool", AsyncMock()) as mock_init,
            patch.object(lifespan.logger, "error") as mock_error,
        ):
            result = await lifespan.init_database()

        assert result is fake_supabase  # boot does not crash
        mock_init.assert_not_called()  # fails closed, not open
        dispatch_pool_errors = [
            call.args[0] for call in mock_error.call_args_list if call.args and "dispatch_pool" in str(call.args[0])
        ]
        assert len(dispatch_pool_errors) == 1, dispatch_pool_errors
        assert "dispatch_direct_pool_enabled" in dispatch_pool_errors[0]

    @pytest.mark.anyio
    async def test_dsn_set_init_pool_failure_in_production_propagates(self, monkeypatch):
        """init_pool() itself raises in production on open failure (its own
        documented contract) -- this must propagate all the way up through
        init_database() and fail boot, not be swallowed by the surrounding
        try/except (which only wraps the app_settings read, not the
        init_pool() call itself)."""
        from backend.core import lifespan

        fake_supabase = MagicMock()
        monkeypatch.setattr(lifespan, "supabase", fake_supabase)
        monkeypatch.setattr(
            lifespan, "settings", _settings_mock(env="production", DISPATCH_POOL_DSN="postgresql://real-dsn")
        )
        monkeypatch.setattr(lifespan, "run_sync", AsyncMock(return_value=MagicMock()))

        with (
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"dispatch_direct_pool_enabled": True}),
            ),
            patch(
                "backend.repositories.dispatch_pool.init_pool",
                AsyncMock(side_effect=RuntimeError("dispatch direct pool failed to open: connection refused")),
            ),
        ):
            with pytest.raises(RuntimeError, match="failed to open"):
                await lifespan.init_database()


# ── init_pool / close_pool ──────────────────────────────────────────────


class TestInitPool:
    @pytest.mark.anyio
    async def test_flag_off_does_not_open_pool(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_DSN", "postgresql://x", raising=False)

        result = await dispatch_pool.init_pool(dispatch_direct_pool_enabled=False)

        assert result is None
        assert dispatch_pool.is_open() is False

    @pytest.mark.anyio
    async def test_dsn_unset_does_not_open_pool_even_if_flag_on(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_DSN", "", raising=False)

        result = await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)

        assert result is None
        assert dispatch_pool.is_open() is False

    @pytest.mark.anyio
    async def test_open_failure_raises_in_production(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_DSN", "postgresql://x", raising=False)
        monkeypatch.setattr(dispatch_pool.settings, "ENV", "production", raising=False)
        monkeypatch.setattr(dispatch_pool, "_PSYCOPG_AVAILABLE", True)

        broken_pool_cls = MagicMock()
        broken_pool_cls.return_value.open = AsyncMock(side_effect=RuntimeError("connection refused"))
        monkeypatch.setattr(dispatch_pool, "AsyncConnectionPool", broken_pool_cls)

        with pytest.raises(RuntimeError):
            await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)
        assert dispatch_pool.is_open() is False

    @pytest.mark.anyio
    async def test_open_failure_outside_production_logs_and_returns_none(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_DSN", "postgresql://x", raising=False)
        monkeypatch.setattr(dispatch_pool.settings, "ENV", "development", raising=False)
        monkeypatch.setattr(dispatch_pool, "_PSYCOPG_AVAILABLE", True)

        broken_pool_cls = MagicMock()
        broken_pool_cls.return_value.open = AsyncMock(side_effect=RuntimeError("connection refused"))
        monkeypatch.setattr(dispatch_pool, "AsyncConnectionPool", broken_pool_cls)

        with patch.object(dispatch_pool.logger, "error") as mock_error:
            result = await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)

        assert result is None
        assert dispatch_pool.is_open() is False
        # Tara's review flagged this test's docstring/name promised a log
        # assertion it never made -- fixed: without this, deleting the
        # logger.error() call in init_pool()'s except branch would still
        # pass every other assertion here, silently regressing the
        # fail-loud-not-silent guarantee AGENTS.md and the module docstring
        # both require.
        mock_error.assert_called_once()
        assert "failed to open direct pool" in mock_error.call_args.args[0]

    @pytest.mark.anyio
    async def test_missing_psycopg_dependency_raises_in_production(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_DSN", "postgresql://x", raising=False)
        monkeypatch.setattr(dispatch_pool.settings, "ENV", "production", raising=False)
        monkeypatch.setattr(dispatch_pool, "_PSYCOPG_AVAILABLE", False)

        with pytest.raises(RuntimeError):
            await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)

    @pytest.mark.anyio
    async def test_missing_psycopg_dependency_outside_production_logs_and_returns_none(self, monkeypatch):
        """The non-raising sibling of the production case above -- previously
        untested (Tara's review): dependency missing + flag on, but ENV is
        not production, must log at ERROR (not silently swallow) and return
        None rather than raising."""
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_DSN", "postgresql://x", raising=False)
        monkeypatch.setattr(dispatch_pool.settings, "ENV", "staging", raising=False)
        monkeypatch.setattr(dispatch_pool, "_PSYCOPG_AVAILABLE", False)

        with patch.object(dispatch_pool.logger, "error") as mock_error:
            result = await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)

        assert result is None
        assert dispatch_pool.is_open() is False
        mock_error.assert_called_once()
        assert "psycopg[binary,pool] is not installed" in mock_error.call_args.args[0]

    @pytest.mark.anyio
    async def test_open_failure_never_echoes_dsn_credentials(self, monkeypatch):
        """Review fix (2026-09-03): libpq repeats conninfo fragments in
        malformed-DSN errors; the log line and the production RuntimeError
        must never carry the password."""
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        monkeypatch.setattr(
            dispatch_pool.settings, "DISPATCH_POOL_DSN", "postgresql://spinr:s3cr3t@db.example/postgres", raising=False
        )
        monkeypatch.setattr(dispatch_pool.settings, "ENV", "production", raising=False)
        monkeypatch.setattr(dispatch_pool, "_PSYCOPG_AVAILABLE", True)

        broken = MagicMock()
        broken.open = AsyncMock(
            side_effect=RuntimeError('invalid connection option in "postgresql://spinr:s3cr3t@db.example/postgres"')
        )
        broken.close = AsyncMock()
        monkeypatch.setattr(dispatch_pool, "AsyncConnectionPool", MagicMock(return_value=broken))

        with patch.object(dispatch_pool.logger, "error") as mock_error:
            with pytest.raises(RuntimeError) as excinfo:
                await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)

        assert str(excinfo.value) == "dispatch direct pool failed to open"
        logged = " ".join(str(c.args[0]) for c in mock_error.call_args_list)
        assert "s3cr3t" not in logged and "spinr:" not in logged
        assert "<redacted>@" in logged
        broken.close.assert_awaited_once()  # no leaked pool on the failure path

    @pytest.mark.anyio
    async def test_open_passes_liveness_check_and_prepare_threshold(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_DSN", "postgresql://x", raising=False)
        monkeypatch.setattr(dispatch_pool.settings, "ENV", "test", raising=False)
        monkeypatch.setattr(dispatch_pool, "_PSYCOPG_AVAILABLE", True)
        fake_pool_instance = MagicMock()
        fake_pool_instance.open = AsyncMock()
        pool_cls = MagicMock(return_value=fake_pool_instance)
        monkeypatch.setattr(dispatch_pool, "AsyncConnectionPool", pool_cls)

        with patch("backend.repositories.dispatch_pool._metric_gauge"):
            await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)

        kwargs = pool_cls.call_args.kwargs
        # Supavisor recycles server connections; probe on checkout.
        assert kwargs["check"] is pool_cls.check_connection
        assert kwargs["kwargs"]["prepare_threshold"] is None
        monkeypatch.setattr(dispatch_pool, "_pool", None)

    @pytest.mark.anyio
    async def test_successful_open_sets_pool_and_gauge(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        monkeypatch.setattr(dispatch_pool.settings, "DISPATCH_POOL_DSN", "postgresql://x", raising=False)
        monkeypatch.setattr(dispatch_pool.settings, "ENV", "test", raising=False)
        monkeypatch.setattr(dispatch_pool, "_PSYCOPG_AVAILABLE", True)

        fake_pool_instance = MagicMock()
        fake_pool_instance.open = AsyncMock()
        pool_cls = MagicMock(return_value=fake_pool_instance)
        monkeypatch.setattr(dispatch_pool, "AsyncConnectionPool", pool_cls)

        with patch("backend.repositories.dispatch_pool._metric_gauge") as mock_gauge:
            result = await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)

        assert result is fake_pool_instance
        assert dispatch_pool.is_open() is True
        mock_gauge.assert_any_call("spinr_db_direct_pool_in_use", 0.0)
        # prepare_threshold=None is the non-negotiable Supavisor transaction-
        # mode requirement documented in the module docstring.
        _, kwargs = pool_cls.call_args
        assert kwargs["kwargs"]["prepare_threshold"] is None

        # cleanup
        await dispatch_pool.close_pool()

    @pytest.mark.anyio
    async def test_idempotent_second_call_returns_existing_pool(self, monkeypatch):
        from backend.repositories import dispatch_pool

        sentinel_pool = MagicMock()
        monkeypatch.setattr(dispatch_pool, "_pool", sentinel_pool)

        result = await dispatch_pool.init_pool(dispatch_direct_pool_enabled=True)
        assert result is sentinel_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)


class TestClosePool:
    @pytest.mark.anyio
    async def test_close_when_never_opened_is_a_noop(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)
        await dispatch_pool.close_pool()  # must not raise
        assert dispatch_pool.is_open() is False

    @pytest.mark.anyio
    async def test_close_calls_pool_close_and_clears_module_state(self, monkeypatch):
        from backend.repositories import dispatch_pool

        fake_pool = MagicMock()
        fake_pool.close = AsyncMock()
        monkeypatch.setattr(dispatch_pool, "_pool", fake_pool)

        await dispatch_pool.close_pool()

        fake_pool.close.assert_awaited_once()
        assert dispatch_pool.is_open() is False

    @pytest.mark.anyio
    async def test_close_error_is_logged_not_raised(self, monkeypatch):
        """Fail-loud-but-not-crash-on-shutdown: a close() error must be
        logged (not swallowed silently) but must not prevent the rest of
        shutdown from completing — matches cleanup_database's own
        try/except-log pattern for its other cleanup step."""
        from backend.repositories import dispatch_pool

        fake_pool = MagicMock()
        fake_pool.close = AsyncMock(side_effect=RuntimeError("already closed"))
        monkeypatch.setattr(dispatch_pool, "_pool", fake_pool)

        with patch.object(dispatch_pool.logger, "error") as mock_error:
            await dispatch_pool.close_pool()  # must not raise

        assert dispatch_pool.is_open() is False
        # Tara's review: this test previously only checked the non-raising
        # outcome, not that the error was actually logged -- deleting the
        # logger.error() call on line ~200 of dispatch_pool.py would have
        # passed silently before this fix.
        mock_error.assert_called_once()
        assert "error closing direct pool" in mock_error.call_args.args[0]


# ── acquire() / run_query() ────────────────────────────────────────────


class TestAcquire:
    @pytest.mark.anyio
    async def test_acquire_raises_when_pool_not_open(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)

        with pytest.raises(RuntimeError, match="not open"):
            async with dispatch_pool.acquire():
                pass  # pragma: no cover

    @pytest.mark.anyio
    async def test_acquire_rejects_when_deadline_already_expired(self, monkeypatch):
        from backend.repositories import dispatch_pool

        fake_pool = MagicMock()
        monkeypatch.setattr(dispatch_pool, "_pool", fake_pool)
        monkeypatch.setattr(dispatch_pool, "_deadline_exhausted", lambda: True)
        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: -1.0)

        with pytest.raises(TimeoutError):
            async with dispatch_pool.acquire():
                pass  # pragma: no cover

        monkeypatch.setattr(dispatch_pool, "_pool", None)


class _FakeAsyncCM:
    """Minimal async context manager stand-in for psycopg_pool's
    AsyncConnectionPool.connection()/psycopg's conn.transaction()/
    conn.cursor() -- all three are async context managers in the real
    API (verified against installed psycopg==3.3.5 / psycopg_pool==3.3.1
    during Surya's review), so tests exercising acquire()/run_query()'s
    success paths need a stand-in that supports `async with`, not a bare
    AsyncMock (which is not itself an async context manager)."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc_info):
        return False


class TestAcquireSuccessPath:
    """Previously untested (Tara's review): acquire()'s happy path --
    actually entering the pool's connection() context manager and emitting
    the wait-time/in-use metrics -- had no coverage; only the two failure
    modes above did."""

    @pytest.mark.anyio
    async def test_acquire_yields_connection_and_emits_metrics(self, monkeypatch):
        from backend.repositories import dispatch_pool

        fake_conn = MagicMock(name="fake_psycopg_async_connection")
        fake_pool = MagicMock()
        fake_pool.connection = MagicMock(return_value=_FakeAsyncCM(fake_conn))
        # 3 managed, 1 idle -> 2 actually checked out.
        fake_pool.get_stats = MagicMock(return_value={"pool_size": 3, "pool_available": 1})
        monkeypatch.setattr(dispatch_pool, "_pool", fake_pool)
        monkeypatch.setattr(dispatch_pool, "_deadline_exhausted", lambda: False)
        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: 5.0)

        with (
            patch("backend.repositories.dispatch_pool._metric_observe") as mock_observe,
            patch("backend.repositories.dispatch_pool._metric_gauge") as mock_gauge,
        ):
            async with dispatch_pool.acquire() as conn:
                assert conn is fake_conn

        # wait-time histogram observed once on entry
        mock_observe.assert_called_once()
        assert mock_observe.call_args.args[0] == "spinr_db_direct_pool_wait_ms"
        # in-use gauge updated on entry AND again in the finally block
        gauge_calls = [c for c in mock_gauge.call_args_list if c.args[0] == "spinr_db_direct_pool_in_use"]
        assert len(gauge_calls) == 2
        # in-use is pool_size - pool_available (3 - 1), NOT pool_size: reading
        # pool_size alone would report idle connections as busy.
        assert all(c.args[1] == 2.0 for c in gauge_calls)
        fake_pool.connection.assert_called_once_with(timeout=5.0)

    @pytest.mark.anyio
    async def test_acquire_gauge_update_survives_exception_in_caller_block(self, monkeypatch):
        """The `finally` gauge update must run even when the caller's body
        raises -- proves the metric isn't only emitted on the clean path."""
        from backend.repositories import dispatch_pool

        fake_conn = MagicMock(name="fake_psycopg_async_connection")
        fake_pool = MagicMock()
        fake_pool.connection = MagicMock(return_value=_FakeAsyncCM(fake_conn))
        fake_pool.get_stats = MagicMock(return_value={"pool_size": 1, "pool_available": 0})
        monkeypatch.setattr(dispatch_pool, "_pool", fake_pool)
        monkeypatch.setattr(dispatch_pool, "_deadline_exhausted", lambda: False)
        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: None)

        with patch("backend.repositories.dispatch_pool._metric_gauge") as mock_gauge:
            with pytest.raises(ValueError):
                async with dispatch_pool.acquire():
                    raise ValueError("caller-side failure")

        gauge_calls = [c for c in mock_gauge.call_args_list if c.args[0] == "spinr_db_direct_pool_in_use"]
        assert len(gauge_calls) == 2
        fake_pool.connection.assert_called_once_with(timeout=None)


class TestAcquireTimeoutPath:
    @pytest.mark.anyio
    async def test_timed_out_wait_is_still_observed(self, monkeypatch):
        """Review fix (2026-09-03): a PoolTimeout raised by connection() used
        to skip the wait-time observe entirely, so the saturation histogram
        was blind exactly when the pool was saturated."""
        from backend.repositories import dispatch_pool

        class _RaisingCM:
            async def __aenter__(self):
                raise TimeoutError("couldn't get a connection after 5.0 sec")

            async def __aexit__(self, *exc_info):  # pragma: no cover - never reached
                return False

        fake_pool = MagicMock()
        fake_pool.connection = MagicMock(return_value=_RaisingCM())
        fake_pool.get_stats = MagicMock(return_value={"pool_size": 8, "pool_available": 0})
        monkeypatch.setattr(dispatch_pool, "_pool", fake_pool)
        monkeypatch.setattr(dispatch_pool, "_deadline_exhausted", lambda: False)
        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: 5.0)

        with (
            patch("backend.repositories.dispatch_pool._metric_observe") as mock_observe,
            patch("backend.repositories.dispatch_pool._metric_gauge"),
        ):
            with pytest.raises(TimeoutError):
                async with dispatch_pool.acquire():
                    pass  # pragma: no cover

        waits = [c for c in mock_observe.call_args_list if c.args[0] == "spinr_db_direct_pool_wait_ms"]
        assert len(waits) == 1
        assert waits[0].kwargs.get("labels") == {"outcome": "timeout"}


class TestRedactDsn:
    def test_masks_user_and_password(self):
        from backend.repositories.dispatch_pool import _redact_dsn

        msg = 'invalid connection option in "postgresql://spinr:s3cr3t@db.example:6543/postgres?sslmode=require"'
        out = _redact_dsn(msg)
        assert "s3cr3t" not in out and "spinr:" not in out
        assert "postgresql://<redacted>@db.example:6543/postgres?sslmode=require" in out

    def test_leaves_credential_free_text_alone(self):
        from backend.repositories.dispatch_pool import _redact_dsn

        assert _redact_dsn("connection refused") == "connection refused"


class TestStatementBudget:
    def test_defaults_when_no_deadline(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: None)
        assert dispatch_pool._statement_budget_s() == dispatch_pool.DEFAULT_STATEMENT_TIMEOUT_S

    def test_uses_remaining_when_smaller(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: 1.25)
        assert dispatch_pool._statement_budget_s() == 1.25

    def test_caps_at_default(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: 99.0)
        assert dispatch_pool._statement_budget_s() == dispatch_pool.DEFAULT_STATEMENT_TIMEOUT_S


class TestClaimBatchWrapper:
    """The psycopg3 binding of the RPC (previously untested at the Python
    level — the real-Postgres suite drives the SQL through psycopg2 and the
    parity test mocks claim_batch outright)."""

    @pytest.mark.anyio
    async def test_empty_driver_list_returns_without_touching_pool(self, monkeypatch):
        from backend.repositories import dispatch_pool

        monkeypatch.setattr(dispatch_pool, "_pool", None)  # acquire() would raise
        assert await dispatch_pool.claim_batch("r1", [], [], 3, None, None) == []

    @pytest.mark.anyio
    async def test_rpc_call_casts_every_parameter_and_sets_statement_timeout(self, monkeypatch):
        import sys
        import types

        from backend.repositories import dispatch_pool

        # psycopg may not be installed where the mocked suite runs; the
        # wrapper only needs `psycopg.rows.dict_row` as a row_factory token.
        if "psycopg" not in sys.modules:
            monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
        fake_rows = types.ModuleType("psycopg.rows")
        fake_rows.dict_row = object()
        monkeypatch.setitem(sys.modules, "psycopg.rows", fake_rows)

        fake_cur = MagicMock()
        fake_cur.execute = AsyncMock()
        fake_cur.fetchall = AsyncMock(return_value=[{"driver_id": "d1", "claimed": True}])
        fake_conn = MagicMock()
        fake_conn.transaction = MagicMock(return_value=_FakeAsyncCM(None))
        fake_conn.cursor = MagicMock(return_value=_FakeAsyncCM(fake_cur))
        fake_pool = MagicMock()
        fake_pool.connection = MagicMock(return_value=_FakeAsyncCM(fake_conn))
        fake_pool.get_stats = MagicMock(return_value={"pool_size": 1, "pool_available": 0})
        monkeypatch.setattr(dispatch_pool, "_pool", fake_pool)
        monkeypatch.setattr(dispatch_pool, "_deadline_exhausted", lambda: False)
        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: None)

        rows = await dispatch_pool.claim_batch("r1", ("d1", "d2"), (100.0, 200), "2", "t0", "t1")

        assert rows == [{"driver_id": "d1", "claimed": True}]
        awaited = [c.args for c in fake_cur.execute.await_args_list]
        assert awaited[0][0] == "SELECT set_config('statement_timeout', %s, true)"
        sql, params = awaited[-1]
        # Explicit casts: psycopg3 binds typed parameters and a Python
        # list[int] is not guaranteed to resolve to int4[].
        assert "%s::text, %s::text[], %s::int[], %s::int, %s::timestamptz, %s::timestamptz" in sql
        assert params == ("r1", ["d1", "d2"], [100, 200], 2, "t0", "t1")
        assert all(isinstance(e, int) for e in params[2])


class TestInUseGauge:
    """_in_use() must report connections actually checked out.

    Regression: the gauge previously read psycopg_pool's `pool_size`, which
    counts every connection the pool manages including idle ones. With
    DISPATCH_POOL_MIN_SIZE=1 that made an idle pool report 1 connection in
    use permanently, so spinr_db_direct_pool_in_use could never reach 0 and
    was useless for the saturation signal Phase 2 needs it for.
    """

    def _pool(self, stats):
        fake_pool = MagicMock()
        fake_pool.get_stats = MagicMock(return_value=stats)
        return fake_pool

    def test_subtracts_idle_connections(self):
        from backend.repositories import dispatch_pool

        assert dispatch_pool._in_use(self._pool({"pool_size": 8, "pool_available": 3})) == 5.0

    def test_idle_pool_reports_zero_not_min_size(self):
        """The exact bug: a pool at min_size with nothing checked out."""
        from backend.repositories import dispatch_pool

        assert dispatch_pool._in_use(self._pool({"pool_size": 1, "pool_available": 1})) == 0.0

    def test_fully_saturated_pool_reports_max(self):
        from backend.repositories import dispatch_pool

        assert dispatch_pool._in_use(self._pool({"pool_size": 8, "pool_available": 0})) == 8.0

    def test_falls_back_to_pool_size_when_available_key_absent(self):
        """A stats-key change must degrade to the old behaviour, not raise on
        the dispatch path."""
        from backend.repositories import dispatch_pool

        assert dispatch_pool._in_use(self._pool({"pool_size": 4})) == 4.0

    def test_clamps_at_zero_when_keys_disagree(self):
        """pool_size and pool_available are sampled separately and can
        momentarily disagree under concurrency -- never emit a negative."""
        from backend.repositories import dispatch_pool

        assert dispatch_pool._in_use(self._pool({"pool_size": 2, "pool_available": 5})) == 0.0

    def test_none_pool_reports_zero(self):
        from backend.repositories import dispatch_pool

        assert dispatch_pool._in_use(None) == 0.0


class TestRunQuery:
    """Previously entirely untested (Tara's review): run_query()'s
    fetch="all"/"one"/"none" branches, its transaction/cursor wrapping, and
    its exception-log-and-reraise path had zero coverage."""

    def _fake_pool_and_cursor(self, monkeypatch, fetchall_return=None, fetchone_return=None):
        from backend.repositories import dispatch_pool

        fake_cur = MagicMock()
        fake_cur.execute = AsyncMock()
        fake_cur.fetchall = AsyncMock(return_value=fetchall_return)
        fake_cur.fetchone = AsyncMock(return_value=fetchone_return)

        fake_conn = MagicMock()
        fake_conn.transaction = MagicMock(return_value=_FakeAsyncCM(None))
        fake_conn.cursor = MagicMock(return_value=_FakeAsyncCM(fake_cur))

        fake_pool = MagicMock()
        fake_pool.connection = MagicMock(return_value=_FakeAsyncCM(fake_conn))
        fake_pool.get_stats = MagicMock(return_value={"pool_size": 1, "pool_available": 0})
        monkeypatch.setattr(dispatch_pool, "_pool", fake_pool)
        monkeypatch.setattr(dispatch_pool, "_deadline_exhausted", lambda: False)
        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: None)
        return fake_cur

    @pytest.mark.anyio
    async def test_fetch_all_returns_fetchall_result(self, monkeypatch):
        from backend.repositories import dispatch_pool

        fake_cur = self._fake_pool_and_cursor(monkeypatch, fetchall_return=[("row1",), ("row2",)])

        result = await dispatch_pool.run_query("SELECT 1", ("p1",), fetch="all")

        assert result == [("row1",), ("row2",)]
        # Review fix (2026-09-03): every transaction opens with a
        # transaction-local statement_timeout before the query itself.
        awaited = [c.args for c in fake_cur.execute.await_args_list]
        assert awaited[0][0] == "SELECT set_config('statement_timeout', %s, true)"
        assert awaited[0][1] == ("10000",)  # no client deadline -> default 10 s cap
        assert awaited[-1] == ("SELECT 1", ("p1",))
        fake_cur.fetchall.assert_awaited_once()
        fake_cur.fetchone.assert_not_called()

    @pytest.mark.anyio
    async def test_statement_timeout_follows_remaining_client_deadline(self, monkeypatch):
        """The server-side budget is the remaining client deadline, capped at
        DEFAULT_STATEMENT_TIMEOUT_S — the fix for the unbounded-execute
        finding (client deadline used to bound acquisition only)."""
        from backend.repositories import dispatch_pool

        fake_cur = self._fake_pool_and_cursor(monkeypatch)
        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: 2.5)
        await dispatch_pool.run_query("SELECT 1", fetch="none")
        assert fake_cur.execute.await_args_list[0].args[1] == ("2500",)

        fake_cur = self._fake_pool_and_cursor(monkeypatch)
        monkeypatch.setattr(dispatch_pool, "_remaining_seconds", lambda: 60.0)
        await dispatch_pool.run_query("SELECT 1", fetch="none")
        assert fake_cur.execute.await_args_list[0].args[1] == ("10000",)

    @pytest.mark.anyio
    async def test_fetch_one_returns_fetchone_result(self, monkeypatch):
        from backend.repositories import dispatch_pool

        fake_cur = self._fake_pool_and_cursor(monkeypatch, fetchone_return=("only_row",))

        result = await dispatch_pool.run_query("SELECT 1", fetch="one")

        assert result == ("only_row",)
        fake_cur.fetchone.assert_awaited_once()
        fake_cur.fetchall.assert_not_called()

    @pytest.mark.anyio
    async def test_fetch_none_returns_none_without_calling_fetch_methods(self, monkeypatch):
        from backend.repositories import dispatch_pool

        fake_cur = self._fake_pool_and_cursor(monkeypatch)

        result = await dispatch_pool.run_query("UPDATE x SET y = 1", fetch="none")

        assert result is None
        fake_cur.fetchall.assert_not_called()
        fake_cur.fetchone.assert_not_called()

    @pytest.mark.anyio
    async def test_query_failure_is_logged_and_reraised(self, monkeypatch):
        """Fail-loud, per AGENTS.md: a DB error here must surface via a
        logger.error call AND propagate to the caller, not be swallowed."""
        from backend.repositories import dispatch_pool

        fake_cur = self._fake_pool_and_cursor(monkeypatch)
        fake_cur.execute = AsyncMock(side_effect=RuntimeError("syntax error at or near"))

        with patch.object(dispatch_pool.logger, "error") as mock_error:
            with pytest.raises(RuntimeError, match="syntax error"):
                await dispatch_pool.run_query("BAD SQL")

        mock_error.assert_called_once()
        assert "query failed" in mock_error.call_args.args[0]

    @pytest.mark.anyio
    async def test_query_duration_metric_always_observed_even_on_failure(self, monkeypatch):
        from backend.repositories import dispatch_pool

        fake_cur = self._fake_pool_and_cursor(monkeypatch)
        fake_cur.execute = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("backend.repositories.dispatch_pool._metric_observe") as mock_observe:
            with pytest.raises(RuntimeError):
                await dispatch_pool.run_query("BAD SQL")

        duration_calls = [c for c in mock_observe.call_args_list if c.args[0] == "spinr_db_direct_query_duration_ms"]
        assert len(duration_calls) == 1


def test_dispatch_pool_reuses_base_redact_pg_error():
    """Convention check: must reuse repositories/_base.py's redactor rather
    than reimplementing error-message scrubbing."""
    from backend.repositories import _base, dispatch_pool

    assert dispatch_pool._redact_pg_error is _base._redact_pg_error


class TestPoolSizeConfig:
    """Review fix (2026-09-03): pool size bounds are validated at config load
    in every environment, not discovered at open time in production only."""

    def test_min_greater_than_max_is_rejected(self):
        from types import SimpleNamespace

        from backend.core.config import Settings

        bad = SimpleNamespace(DISPATCH_POOL_MIN_SIZE=10, DISPATCH_POOL_MAX_SIZE=8)
        with pytest.raises(ValueError, match="DISPATCH_POOL_MIN_SIZE must be <= DISPATCH_POOL_MAX_SIZE"):
            Settings._validate_dispatch_pool_sizes(bad)

    def test_valid_bounds_pass_through(self):
        from types import SimpleNamespace

        from backend.core.config import Settings

        ok = SimpleNamespace(DISPATCH_POOL_MIN_SIZE=1, DISPATCH_POOL_MAX_SIZE=8)
        assert Settings._validate_dispatch_pool_sizes(ok) is ok

    def test_field_constraints_are_declared(self):
        from backend.core.config import Settings

        min_meta = Settings.model_fields["DISPATCH_POOL_MIN_SIZE"].metadata
        max_meta = Settings.model_fields["DISPATCH_POOL_MAX_SIZE"].metadata
        assert any(getattr(m, "ge", None) == 0 for m in min_meta)
        assert any(getattr(m, "ge", None) == 1 for m in max_meta)
