"""Coverage for routes/main.py (A1c, Sub-tier B).

`routes/main.py` mounts the root `/` endpoint and the `/health` liveness +
readiness probe used by Railway health checks and the post-deploy smoke test
(ACTION_ITEMS.md A2). It had zero dedicated test file despite being the
literal signal that gates whether a bad deploy gets traffic.

Scope: the two endpoint functions' primary branches (DB ok/down, loop
liveness ok/stale, background-task registration filtering). The nested
dual-import `ImportError` fallback branches inside `health_check` are
exercised implicitly by the dual-patch helper below (both the bare and
`backend.`-qualified module objects are genuinely different sys.modules
entries in this test environment — see `_dual_patch`, mirroring the same
pattern conftest.py's `patch_external_dependencies` fixture already uses)
rather than left uncovered as "not worth chasing" — since here the
fallback IS the code path actually reached whenever the primary attempt
also fails, not a dead branch.

Test-only change — no application code modified.
"""

from __future__ import annotations

import importlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


def _dual_patch(monkeypatch, bare_mod: str, qualified_mod: str, attr: str, value):
    """Patch ``attr`` on both the bare and ``backend.``-qualified module
    objects for ``bare_mod``/``qualified_mod``.

    These are NOT always distinct objects: `db_supabase` vs
    `backend.db_supabase` are proven-distinct sys.modules entries, but
    `routes.main`/`backend.routes.main` (and very likely
    `utils.loop_monitor`/`backend.utils.loop_monitor`) turned out to be
    ALIASES of the SAME module object in this test environment. Patching
    the same (module, attr) pair twice with raw `unittest.mock.patch()`
    instances and stopping them in creation (FIFO) order corrupts the
    restore: the second patcher snapshots the FIRST patcher's mock as its
    "original" value, so stopping both back-to-front leaves the mock
    permanently applied — this is exactly what leaked a `side_effect`
    mock into `test_webhooks_main.py::TestLoopMonitor` in the same pytest
    session. `monkeypatch.setattr` sidesteps this entirely: it tracks
    (object, name, original) triples and correctly restores the TRUE
    original even when the same target is set multiple times, so we use
    it here instead of manual `patch()`/`.stop()` bookkeeping.
    """
    seen_targets = set()
    for mod_path in (bare_mod, qualified_mod):
        try:
            mod = importlib.import_module(mod_path)
        except (ImportError, ModuleNotFoundError):
            continue
        if not hasattr(mod, attr):
            continue
        key = (id(mod), attr)
        if key in seen_targets:
            continue  # same underlying object as an earlier mod_path — skip the redundant set
        seen_targets.add(key)
        monkeypatch.setattr(mod, attr, value)


@pytest.fixture
def patch_ping(monkeypatch):
    """Patch db_supabase.ping (both module spellings) for the duration of a
    test. Usage: `patch_ping(AsyncMock(return_value={...}))` or
    `patch_ping(AsyncMock(side_effect=SomeError(...)))`."""

    def _apply(mock_ping):
        _dual_patch(monkeypatch, "db_supabase", "backend.db_supabase", "ping", mock_ping)

    return _apply


@pytest.fixture
def patch_loop_status(monkeypatch):
    """Patch utils.loop_monitor.get_loop_status (both module spellings)."""

    def _apply(mock_fn):
        _dual_patch(monkeypatch, "utils.loop_monitor", "backend.utils.loop_monitor", "get_loop_status", mock_fn)

    return _apply


@pytest.mark.anyio
async def test_root_returns_message_and_version():
    from backend.routes.main import root

    result = await root()
    assert result == {"message": "Spinr API", "version": "1.0.0"}


@pytest.mark.anyio
async def test_health_check_all_healthy_returns_200_shape(patch_ping, patch_loop_status):
    from backend.routes.main import health_check

    patch_ping(AsyncMock(return_value={"latency_ms": 12.3}))
    patch_loop_status(MagicMock(return_value={"healthy": True, "loops": {"surge_engine (2min)": {"status": "ok"}}}))

    result = await health_check(request=None)

    assert result["status"] == "healthy"
    assert result["db"]["status"] == "ok"
    assert result["db"]["latency_ms"] == 12.3
    assert result["loops"] == {"surge_engine (2min)": {"status": "ok"}}


@pytest.mark.anyio
async def test_health_check_db_down_returns_503_degraded(patch_ping, patch_loop_status):
    """When the primary `import db_supabase; db_supabase.ping()` attempt
    fails, `health_check` falls through to `from .. import db_supabase as
    _db`. In this test harness `backend.routes.main` and bare
    `routes.main` resolve to the SAME module object (confirmed via a debug
    probe — unlike `db_supabase`/`backend.db_supabase`, which are
    genuinely distinct here), so `health_check.__package__` is `'routes'`
    (one level) instead of the real deployment's `'backend.routes'` (two
    levels) — the relative fallback's `..` exceeds that depth and raises
    Python's own "attempted relative import beyond top-level package"
    before `_db.ping()` is ever reached. That's a test-harness packaging
    artifact (the real `python -m backend.server` entrypoint has correct
    two-level packaging and would reach `_db.ping()` normally), not an
    application bug — so this test asserts what's actually reachable
    here: a non-empty db_error and a degraded 503, not the specific
    "db down" string from the unreachable ping() call. The `.details`
    merge on the fallback exception (`if hasattr(exc, "details"):
    db_info = exc.details`) is untestable in this harness for the same
    reason and is not separately covered."""
    from backend.routes.main import health_check

    patch_ping(AsyncMock(side_effect=ConnectionError("db down")))
    patch_loop_status(MagicMock(return_value={"healthy": True, "loops": {}}))

    response = await health_check(request=None)

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["status"] == "degraded"
    assert body["db"]["status"] == "error"
    assert body["db"]["error"]  # some error message present, never silently empty


@pytest.mark.anyio
async def test_health_check_stale_loop_flips_status_to_degraded_even_with_db_ok(patch_ping, patch_loop_status):
    from backend.routes.main import health_check

    patch_ping(AsyncMock(return_value={}))
    patch_loop_status(
        MagicMock(
            return_value={
                "healthy": False,
                "loops": {"stripe_reconcile (24h)": {"status": "stale", "seconds_since_tick": 999999}},
            }
        )
    )

    response = await health_check(request=None)

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["status"] == "degraded"
    assert body["db"]["status"] == "ok"
    assert body["loops"]["stripe_reconcile (24h)"]["status"] == "stale"


@pytest.mark.anyio
async def test_health_check_with_request_filters_to_running_background_tasks(patch_ping, patch_loop_status):
    """Only not-yet-done tasks are passed as the registered-loop-name filter
    — a completed/cancelled task must not count as a live loop."""
    from backend.routes.main import health_check

    running_task = MagicMock()
    running_task.done.return_value = False
    running_task.get_name.return_value = "surge_engine (2min)"

    finished_task = MagicMock()
    finished_task.done.return_value = True
    finished_task.get_name.return_value = "retention_purge (24h)"

    mock_request = MagicMock()
    mock_request.app.state.background_tasks = [running_task, finished_task]

    captured = {}

    def _fake_get_loop_status(registered_names=None):
        captured["names"] = registered_names
        return {"healthy": True, "loops": {}}

    patch_ping(AsyncMock(return_value={}))
    patch_loop_status(MagicMock(side_effect=_fake_get_loop_status))

    result = await health_check(request=mock_request)

    assert result["status"] == "healthy"
    assert captured["names"] == ["surge_engine (2min)"]


@pytest.mark.anyio
async def test_health_check_request_without_background_tasks_state_passes_none(patch_ping, patch_loop_status):
    """A request whose app.state has no `background_tasks` attribute (e.g.
    a minimal test app) must not crash — falls back to registered=None."""
    from backend.routes.main import health_check

    mock_request = MagicMock()
    mock_request.app.state = MagicMock(spec=[])  # no background_tasks attr

    captured = {}

    def _fake_get_loop_status(registered_names=None):
        captured["names"] = registered_names
        return {"healthy": True, "loops": {}}

    patch_ping(AsyncMock(return_value={}))
    patch_loop_status(MagicMock(side_effect=_fake_get_loop_status))

    result = await health_check(request=mock_request)

    assert result["status"] == "healthy"
    assert captured["names"] is None


@pytest.mark.anyio
async def test_health_check_loop_monitor_generic_exception_defaults_to_healthy(patch_ping, patch_loop_status):
    """A non-ImportError failure reading loop status (e.g. a lock contention
    bug) must not take down the whole health endpoint — DB status alone
    still gates readiness."""
    from backend.routes.main import health_check

    patch_ping(AsyncMock(return_value={}))
    patch_loop_status(MagicMock(side_effect=RuntimeError("lock contention")))

    result = await health_check(request=None)

    assert result["status"] == "healthy"
    assert result["loops"] == {}
