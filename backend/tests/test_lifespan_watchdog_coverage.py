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
    """The string literals inside the `_WATCHDOG_LOOP_NAMES = list([...])`
    assignment inside the lifespan() function body."""
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_WATCHDOG_LOOP_NAMES"
        ):
            # RHS is `list([...])` — a Call to list() with one list-literal arg.
            value = node.value
            assert isinstance(value, ast.Call), "_WATCHDOG_LOOP_NAMES must be assigned via list([...])"
            assert len(value.args) == 1 and isinstance(value.args[0], ast.List)
            elts = value.args[0].elts
            return [e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    raise AssertionError("could not find `_WATCHDOG_LOOP_NAMES = list([...])` in lifespan()")


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
        watched + the 13 loops this fix adds = 37 (38 total _spawn() calls,
        including loop_watchdog itself, which does not watch itself)."""
        spawned = _spawned_loop_names(_lifespan_fn)
        watched = _watchdog_loop_names(_lifespan_fn)

        assert len(spawned) == 38, (
            f"expected 38 total _spawn() calls (37 loops + loop_watchdog itself), got {len(spawned)} — "
            "update this test's expected count deliberately if a loop was intentionally added/removed, "
            "and update _WATCHDOG_LOOP_NAMES in the same change."
        )
        assert len(watched) == 37, f"expected 37 watched loop names, got {len(watched)}"

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
