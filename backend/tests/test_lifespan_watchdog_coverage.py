"""Regression guard for ranked audit blocker #27: every background loop
spawned in core/lifespan.py must be registered with the loop watchdog
(_WATCHDOG_LOOP_NAMES), so a silently-dead/hung loop is actually detected.

13 loops were spawned via `_spawn(name, coro_factory)` but never added to
`_WATCHDOG_LOOP_NAMES`:
    preauth_capture (5min), referral_payout (5min), driver_claim_reaper (60s),
    kyb_reverification (24h), route_finalizer (15s), route_gap_monitor (15s),
    safety_checkin (30s), reconciliation (daily 02:00 UTC),
    distance_reconciliation (daily 04:00 UTC),
    period1_distance_finalizer (5min), orphaned_hold_reconciler (15m),
    suspension_reactivation (10min), zoho_desk_sync (10min)

This file source-parses core/lifespan.py with `ast` (rather than importing
and running `lifespan()`, which would require a live/mocked DB + would spawn
real asyncio tasks under a non-ENV=test app) so the test is a live drift
detector: any future loop added to one list without the other fails CI
immediately, regardless of what today's actual loop set happens to be.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_LIFESPAN_PATH = Path(__file__).resolve().parents[1] / "core" / "lifespan.py"


def _parse_lifespan_module() -> ast.Module:
    source = _LIFESPAN_PATH.read_text()
    return ast.parse(source, filename=str(_LIFESPAN_PATH))


def _find_lifespan_function(tree: ast.Module) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            return node
    raise AssertionError("could not find `async def lifespan(...)` in core/lifespan.py")


def _spawned_loop_names(fn: ast.AsyncFunctionDef) -> list[str]:
    """Every string literal passed as the first positional arg to a call
    named `_spawn(...)` inside the lifespan() function body."""
    names: list[str] = []
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_spawn"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.append(node.args[0].value)
    return names


def _watchdog_loop_names(fn: ast.AsyncFunctionDef) -> list[str]:
    """All-role watchdog inventory from the placement registry and live spawns."""
    from backend.core.background_loop_registry import LOOP_WATCHDOG_NAME, active_api_loop_names

    spawned = set(_spawned_loop_names(fn))
    return [name for name in active_api_loop_names("all") if name in spawned and name != LOOP_WATCHDOG_NAME]


@pytest.fixture(scope="module")
def _lifespan_fn() -> ast.AsyncFunctionDef:
    return _find_lifespan_function(_parse_lifespan_module())


class TestWatchdogCoversEverySpawnedLoop:
    def test_every_spawned_loop_is_watched(self, _lifespan_fn):
        """The core regression guard: spawned-loop-names minus the watchdog
        itself must exactly equal the watched-loop-names set. A future loop
        added via _spawn() without a matching _WATCHDOG_LOOP_NAMES entry
        fails this test immediately — this is what should have caught the
        original 13-loop gap (ranked audit blocker #27)."""
        spawned = _spawned_loop_names(_lifespan_fn)
        watched = _watchdog_loop_names(_lifespan_fn)

        # loop_watchdog watches the other loops; it does not watch itself.
        watchable = {n for n in spawned if n != "loop_watchdog (5min)"}

        missing = sorted(watchable - set(watched))
        assert missing == [], f"loops spawned but NOT registered with the watchdog: {missing}"

        extra = sorted(set(watched) - watchable)
        assert extra == [], f"watchdog names with no matching _spawn() call (stale/typo'd): {extra}"

    def test_every_spawned_loop_has_a_role_and_h3_stays_dormant(self, _lifespan_fn):
        from backend.core.background_loop_registry import LOOP_PLACEMENT

        spawned = set(_spawned_loop_names(_lifespan_fn))
        assert spawned <= set(LOOP_PLACEMENT)
        assert "h3_index_reconciler (2min)" in LOOP_PLACEMENT
        assert "h3_index_reconciler (2min)" not in spawned

    def test_watchdog_list_has_no_duplicate_names(self, _lifespan_fn):
        """A duplicate entry is a silent naming collision — one loop's
        heartbeat would be shadowed under another's threshold config with no
        visible error, so the list itself must be duplicate-free."""
        watched = _watchdog_loop_names(_lifespan_fn)
        assert len(watched) == len(set(watched)), (
            f"duplicate entries in _WATCHDOG_LOOP_NAMES: {sorted({n for n in watched if watched.count(n) > 1})}"
        )

    def test_spawn_call_has_no_duplicate_names(self, _lifespan_fn):
        """Two loops spawned under the same name would also collide inside
        loop_monitor's heartbeat dict (last writer wins) — guard against
        that independently of the watchdog registration list."""
        spawned = _spawned_loop_names(_lifespan_fn)
        assert len(spawned) == len(set(spawned)), (
            f"duplicate _spawn() names in lifespan(): {sorted({n for n in spawned if spawned.count(n) > 1})}"
        )

    def test_watched_loop_count_matches_spawned_loop_count(self, _lifespan_fn):
        """Explicit count assertion (not just set-equality) — 24 previously
        watched + the 13 loops the watchdog-coverage fix added = 37, plus the
        2 tracking-overhaul loops (stale_p3_closer, driver_daily_rollup) = 39,
        plus support_sla_breach_sweep (ACTION_ITEMS.md G8) = 40, plus
        insurance_period_reconciler (ACTION_ITEMS.md C55, WS-12 §3) = 41,
        plus route_deviation_alerter (live route-deviation safety alert,
        domain-safety.md's "Intended, not built" gap) = 42 (43 total
        _spawn() calls, including loop_watchdog itself, which does not
        watch itself)."""
        spawned = _spawned_loop_names(_lifespan_fn)
        watched = _watchdog_loop_names(_lifespan_fn)

        assert len(spawned) == 43, (
            f"expected 43 total _spawn() calls (42 loops + loop_watchdog itself), got {len(spawned)} — "
            "update this test's expected count deliberately if a loop was intentionally added/removed, "
            "and update _WATCHDOG_LOOP_NAMES in the same change."
        )
        assert len(watched) == 42, f"expected 42 watched loop names, got {len(watched)}"

    def test_previously_missing_13_loops_are_now_registered(self, _lifespan_fn):
        """Names the audit found spawned-but-unwatched (ranked blocker #27).
        Pinned explicitly so a future refactor can't silently drop one back
        out while keeping the set-equality checks above green by coincidence
        (e.g. if it were removed from both lists together)."""
        watched = set(_watchdog_loop_names(_lifespan_fn))
        previously_missing = {
            "preauth_capture (5min)",
            "referral_payout (5min)",
            "driver_claim_reaper (60s)",
            "kyb_reverification (24h)",
            "route_finalizer (15s)",
            "route_gap_monitor (15s)",
            "safety_checkin (30s)",
            "reconciliation (daily 02:00 UTC)",
            "distance_reconciliation (daily 04:00 UTC)",
            "period1_distance_finalizer (5min)",
            "orphaned_hold_reconciler (15m)",
            "suspension_reactivation (10min)",
            "zoho_desk_sync (10min)",
        }
        assert len(previously_missing) == 13
        assert previously_missing <= watched, previously_missing - watched


class TestProcessRoleLoopOwnership:
    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "role,env",
        [("all", "development"), ("api", "development"), ("worker", "development"), ("all", "test")],
    )
    async def test_lifespan_spawns_and_watches_only_role_owned_loops(self, role, env, monkeypatch):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from fastapi import FastAPI

        from backend.core import lifespan as lifespan_module
        from backend.core.background_loop_registry import (
            LOOP_WATCHDOG_NAME,
            WORKER_WAVE1_LOOP_NAMES,
            active_api_loop_names,
        )
        from backend.utils import loop_alert
        import ai.mcp_server as mcp_server

        settings = MagicMock(
            ENV=env,
            DISPATCH_POOL_DSN="",
            REDIS_URL="",
            RATE_LIMIT_REDIS_URL="",
            WS_REDIS_URL="",
            ALERT_WEBHOOK_URL="",
        )
        monkeypatch.setattr(lifespan_module, "settings", settings)
        monkeypatch.setattr(lifespan_module, "init_database", AsyncMock(return_value=None))
        monkeypatch.setenv("SPINR_PROCESS_ROLE", role)
        monkeypatch.setattr(mcp_server, "start_mcp", AsyncMock())
        monkeypatch.setattr(mcp_server, "stop_mcp", AsyncMock())

        from utils import ws_pubsub

        monkeypatch.setattr(ws_pubsub.pubsub, "start", AsyncMock(return_value=True))
        monkeypatch.setattr(ws_pubsub.pubsub, "stop", AsyncMock())

        created_names = []
        watchdog_registrations = []
        real_create_task = asyncio.create_task
        loop = asyncio.get_running_loop()

        async def watchdog_once(factory):
            try:
                await factory()
            except asyncio.CancelledError:
                pass

        async def stop_after_watchdog_check(**kwargs):
            watchdog_registrations.append(kwargs["registered_names"])
            raise asyncio.CancelledError

        monkeypatch.setattr(loop_alert, "check_and_alert", stop_after_watchdog_check)

        def fake_create_task(coro, *, name=None):
            created_names.append(name)
            if name == LOOP_WATCHDOG_NAME:
                factory = coro.cr_frame.f_locals["coro_factory"]
                coro.close()
                return real_create_task(watchdog_once(factory), name=name)
            coro.close()
            future = loop.create_future()
            future.set_result(None)
            return future

        monkeypatch.setattr(asyncio, "create_task", fake_create_task)

        async with lifespan_module.lifespan(FastAPI()):
            await asyncio.sleep(0)

        spawned_names = set(_spawned_loop_names(_find_lifespan_function(_parse_lifespan_module())))
        selected = {
            name for name in spawned_names
            if name != LOOP_WATCHDOG_NAME and (role == "all" or (role == "api" and name not in WORKER_WAVE1_LOOP_NAMES))
        }
        if role == "worker":
            selected = set()
        expected_tasks = selected | ({LOOP_WATCHDOG_NAME} if role != "worker" else set())
        if env == "test":
            expected_tasks = set()
        assert {name for name in created_names if name in spawned_names} == expected_tasks
        assert "h3_index_reconciler (2min)" not in created_names
        assert "redis_startup_diagnosis" in created_names
        if role == "worker" or env == "test":
            assert watchdog_registrations == []
        else:
            assert len(watchdog_registrations) == 1
            assert set(watchdog_registrations[0]) == set(active_api_loop_names(role)) & spawned_names


class TestWatchdogFlagsAHungNewlyAddedLoop:
    """End-to-end (loop_monitor + loop_alert) proof that one of the 13 newly
    registered loops is actually detected as stale — not just present in a
    name list. Uses route_finalizer (15s) as the representative case."""

    @pytest.mark.anyio
    async def test_hung_route_finalizer_is_flagged_stale_and_alerted(self, monkeypatch):

        from backend.utils import loop_alert, loop_monitor

        name = "route_finalizer (15s)"
        # Simulate the loop's last successful heartbeat being far in the past
        # (it hung / its task died silently) relative to "now".
        with loop_monitor._lock:
            loop_monitor._heartbeats[name] = 0.0

        try:
            monkeypatch.setattr(loop_monitor.time, "monotonic", lambda: 999_999.0)

            status = loop_monitor.get_loop_status(registered_names=[name])
            assert status["healthy"] is False
            assert status["loops"][name]["status"] == "stale"

            # And check_and_alert actually posts for it once registered.
            from unittest.mock import AsyncMock, MagicMock, patch

            loop_alert._last_alerted.clear()
            inner = AsyncMock()
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            inner.post.return_value = resp
            cm_mock = AsyncMock()
            cm_mock.__aenter__.return_value = inner

            with patch.object(loop_alert, "get_loop_status", return_value=status):
                with patch.object(loop_alert, "time") as mock_time:
                    mock_time.monotonic.return_value = 999_999.0
                    with patch("httpx.AsyncClient", return_value=cm_mock):
                        await loop_alert.check_and_alert(
                            registered_names=[name],
                            webhook_url="https://hooks.example.com/T/B/x",
                        )

            inner.post.assert_called_once()
            _, kwargs = inner.post.call_args
            assert "route_finalizer" in kwargs["json"]["text"]
        finally:
            with loop_monitor._lock:
                loop_monitor._heartbeats.pop(name, None)
            loop_alert._last_alerted.clear()

    def test_never_ticked_loop_is_not_flagged_stale_on_fresh_boot(self):
        """Sanity check the never-ticked path still applies to a newly
        registered loop too — a loop that hasn't had its first scheduled
        window yet (e.g. distance_reconciliation, which only runs at
        04:00 UTC) must not false-positive as stale immediately on boot."""
        from backend.utils import loop_monitor

        name = "distance_reconciliation (daily 04:00 UTC)"
        with loop_monitor._lock:
            loop_monitor._heartbeats.pop(name, None)

        status = loop_monitor.get_loop_status(registered_names=[name])
        assert status["healthy"] is True
        assert status["loops"][name]["status"] == "never_ticked"
