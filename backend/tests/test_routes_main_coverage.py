"""Coverage-closure tests for routes/main.py (A1c Sub-tier B).

IMPORTANT CONTEXT: routes/main.py's ``api_router`` (defining ``GET /`` and
``GET /health``) is never imported or ``include_router``'d by server.py.
server.py implements its own independent ``/health`` directly on ``app``
(server.py:204) and has no ``/`` route at all. Confirmed via:

    grep -n "routes\\.main\\|main_router\\|import main" server.py   -> no hits
    grep -n '@app.get("/")' server.py                                -> no hits
    grep -n '"/health"' server.py routes/*.py
        server.py:204:@app.get("/health")
        routes/main.py:23:@api_router.get("/health")

`git log --oneline -- routes/main.py` shows only a single unrelated docs
commit touching it in passing -- no evidence of prior active use either.
This module is DEAD CODE: an unreferenced, unmounted duplicate of the real
health check. It is not deleted here (out of scope for a coverage pass --
that's a repo-owner call), but these tests exercise its two handler
functions directly (bypassing the app entirely, since there is no route to
hit through a TestClient) purely to close the coverage gap on functions
that do still get imported/loaded whenever ``routes.main`` is imported.

Patch targets: ``health_check`` does its own internal dual-import of
``db_supabase`` (bare, then ``from .. import db_supabase``) and of
``utils.loop_monitor.get_loop_status`` (bare, then relative).

FINDING re: the relative-import fallback branches specifically -- this
repo's tests/conftest.py deliberately aliases every "backend.<bare>" import
to the SAME module object as the bare "<bare>" one for modules under its
``_MIRRORED_BARE_ROOTS`` set (which includes "routes"), so that patches
applied to one spelling are visible through the other. A side effect:
``routes/main.py``'s module object keeps ``__name__="routes.main"`` /
``__package__="routes"`` (a single, dot-free segment) no matter which
spelling it's imported under in this test session, once the bare one has
been established as canonical -- which happens unconditionally the moment
any test in this file does ``import routes.main``. Since ``from ..
import X`` requires the importing package to have at least 2 dotted
segments, this makes the *success* path of both relative-import fallbacks
(``from .. import db_supabase`` and ``from ..utils.loop_monitor import
get_loop_status``) structurally unreachable via a normal import here --
attempting it (e.g. via ``from backend.routes import main``) yields the
exact same object and the exact same "attempted relative import beyond
top-level package" ImportError. Where a test below needs to reach the
success branch of one of those fallbacks for coverage, it does so by
patching ``builtins.__import__`` to intercept that one specific call
signature (matched on level/name/fromlist) and hand back a fake module,
leaving every other import untouched. This is noted inline on each such
test.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


# ============================================================
# root()
# ============================================================


class TestRoot:
    async def test_root_returns_service_banner(self):
        import routes.main as m

        result = await m.root()
        assert result == {"message": "Spinr API", "version": "1.0.0"}


# ============================================================
# health_check() -- bare import (routes.main), happy path
# ============================================================


class TestHealthCheckBareHappyPath:
    async def test_healthy_with_no_request(self):
        """Default mock_supabase_client (autouse) makes db_supabase.ping()
        succeed; no background_tasks registered -> health_check falls back
        to whatever's already in utils.loop_monitor's process-global
        ``_heartbeats`` dict (``registered_names or list(snapshot.keys())``).
        That dict is shared across the WHOLE test session -- other test
        modules exercise the real background loops and call
        ``record_heartbeat`` for their real names, so asserting an exact
        empty ``{}`` here broke when this file ran after those tests in the
        full suite (caught during this session's own full-suite run, see
        the Change Impact Log). Pinning ``_heartbeats`` to an explicit
        empty dict for the duration of this test makes it deterministic
        regardless of run order, matching what a fresh-process /health
        probe would actually see."""
        import routes.main as m

        with patch("utils.loop_monitor._heartbeats", {}):
            result = await m.health_check(request=None)

        assert result["status"] == "healthy"
        assert result["db"]["status"] == "ok"
        assert result["loops"] == {}

    async def test_healthy_with_request_and_registered_loops(self):
        """request.app.state.background_tasks supplies loop names; loops
        that have never ticked are 'never_ticked' but don't flip health.
        Uses a made-up loop name (not one of the real production loop
        names in utils/loop_monitor.LOOP_THRESHOLDS) specifically so this
        assertion can never collide with a real heartbeat some other test
        in the full suite happens to have recorded for a real loop name in
        the process-global ``_heartbeats`` dict (see the
        test_healthy_with_no_request docstring for the full explanation of
        that shared-state hazard)."""
        import routes.main as m

        task = SimpleNamespace(done=lambda: False, get_name=lambda: "not_a_real_loop_name (test)")
        done_task = SimpleNamespace(done=lambda: True, get_name=lambda: "ignored_done_task")
        fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(background_tasks=[task, done_task])))

        with patch("utils.loop_monitor._heartbeats", {}):
            result = await m.health_check(request=fake_request)

        assert result["status"] == "healthy"
        assert "not_a_real_loop_name (test)" in result["loops"]
        assert result["loops"]["not_a_real_loop_name (test)"]["status"] == "never_ticked"
        # the done() task must be excluded from the registered set
        assert "ignored_done_task" not in result["loops"]

    async def test_healthy_with_request_missing_app_state(self):
        """request present but request.app.state has no background_tasks
        attribute -> registered stays [] -> falls back to whatever has
        already ticked. Pins _heartbeats to empty for determinism (see
        test_healthy_with_no_request)."""
        import routes.main as m

        fake_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

        with patch("utils.loop_monitor._heartbeats", {}):
            result = await m.health_check(request=fake_request)

        assert result["status"] == "healthy"


# ============================================================
# health_check() -- DB failure branches
# ============================================================


class TestHealthCheckDbFailure:
    async def test_bare_db_ping_fails_then_relative_import_unavailable(self):
        """Bare ``import db_supabase`` succeeds as a module import, but
        ping() raising sends us into the first except (logged, swallowed --
        db_ok stays False). The subsequent relative-import fallback
        (``from .. import db_supabase``) fails for the bare module (routes
        has no parent package), landing in the second except with
        db_error set. Overall payload must be 'degraded' + HTTP 503."""
        import routes.main as m

        with patch("db_supabase.ping", new=AsyncMock(side_effect=RuntimeError("db down"))):
            response = await m.health_check(request=None)

        # relative import fails for the bare module -> JSONResponse(503)
        from starlette.responses import JSONResponse

        assert isinstance(response, JSONResponse)
        assert response.status_code == 503
        import json as _json

        body = _json.loads(response.body)
        assert body["status"] == "degraded"
        assert body["db"]["status"] == "error"

    async def test_relative_import_structurally_unreachable_in_this_harness(self):
        """FINDING: this test harness's conftest.py deliberately aliases
        every "backend.<bare>" import to the SAME module object as the bare
        "<bare>" one (see conftest.py's _BareModuleAliasFinder /
        _MIRRORED_BARE_ROOTS, which includes "routes"). That means
        routes/main.py's module object keeps __name__="routes.main" /
        __package__="routes" (a single, dot-free segment) no matter which
        spelling you import it under -- so ``from .. import db_supabase``
        (level=2) always raises "attempted relative import beyond
        top-level package" once the bare module is canonical, both here in
        tests AND in this repo's actual "cd backend && python server.py"
        bare run mode (only "python3 -m backend.server" gives it a real
        two-segment package where the fallback could resolve). This is
        exercised (not mocked away) below to document the real, reachable
        behaviour of the second except branch."""
        import routes.main as m

        with patch("db_supabase.ping", new=AsyncMock(side_effect=RuntimeError("bare db down"))):
            response = await m.health_check(request=None)

        from starlette.responses import JSONResponse

        assert isinstance(response, JSONResponse)
        assert response.status_code == 503
        import json as _json

        body = _json.loads(response.body)
        assert body["status"] == "degraded"
        assert body["db"]["status"] == "error"
        assert "attempted relative import" in body["db"]["error"]

    async def test_relative_import_success_path_via_import_patch(self):
        """Forces the ``from .. import db_supabase as _db`` statement itself
        to succeed by patching ``builtins.__import__`` to intercept exactly
        that call signature (level=2, name="", fromlist=("db_supabase",))
        and hand back a fake package exposing a working ``db_supabase.ping``
        -- everything else still goes through the real import machinery.
        This is the only reliable way to exercise db_ok flipping True via
        the fallback path given the harness constraint above."""
        import builtins

        import routes.main as m

        fake_backend_pkg = SimpleNamespace(db_supabase=SimpleNamespace(ping=AsyncMock(return_value={"ping_ms": 1.2})))
        real_import = builtins.__import__

        def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 2 and name == "" and fromlist == ("db_supabase",):
                return fake_backend_pkg
            return real_import(name, globals, locals, fromlist, level)

        with (
            patch("db_supabase.ping", new=AsyncMock(side_effect=RuntimeError("bare db down"))),
            patch("builtins.__import__", side_effect=_fake_import),
        ):
            result = await m.health_check(request=None)

        assert result["status"] == "healthy"
        assert result["db"]["status"] == "ok"
        assert result["db"]["ping_ms"] == 1.2

    async def test_db_error_with_details_attribute(self):
        """Exercises the ``if hasattr(exc, 'details')`` branch: an exception
        carrying a .details dict merges it into db_info. Same __import__
        patch technique as above, but the fake ping() raises instead of
        succeeding."""
        import routes.main as m

        class _DbErrorWithDetails(RuntimeError):
            def __init__(self):
                super().__init__("qualified db down")
                self.details = {"original": "connection refused"}

        async def _raising_ping():
            raise _DbErrorWithDetails()

        import builtins

        fake_backend_pkg = SimpleNamespace(db_supabase=SimpleNamespace(ping=_raising_ping))
        real_import = builtins.__import__

        def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 2 and name == "" and fromlist == ("db_supabase",):
                return fake_backend_pkg
            return real_import(name, globals, locals, fromlist, level)

        with (
            patch("db_supabase.ping", new=AsyncMock(side_effect=RuntimeError("bare db down"))),
            patch("builtins.__import__", side_effect=_fake_import),
        ):
            response = await m.health_check(request=None)

        import json as _json

        body = _json.loads(response.body)
        assert body["db"]["error"] == "qualified db down"
        assert body["db"]["original"] == "connection refused"


# ============================================================
# health_check() -- loop_monitor branches
# ============================================================


class TestHealthCheckLoopMonitor:
    async def test_loop_status_unhealthy_flips_overall_status(self):
        """get_loop_status() reporting healthy=False must flip the overall
        payload to 'degraded' + 503 even when the DB is fine."""
        import routes.main as m

        with patch(
            "utils.loop_monitor.get_loop_status",
            return_value={"healthy": False, "loops": {"stale_loop": {"status": "stale"}}},
        ):
            response = await m.health_check(request=None)

        from starlette.responses import JSONResponse

        assert isinstance(response, JSONResponse)
        assert response.status_code == 503
        import json as _json

        body = _json.loads(response.body)
        assert body["status"] == "degraded"
        assert body["loops"]["stale_loop"]["status"] == "stale"

    async def test_bare_loop_monitor_import_error_falls_back_to_relative_success(self):
        """Blocks the bare ``from utils.loop_monitor import
        get_loop_status`` with an ImportError, which sends execution into
        the ``except ImportError:`` handler's own
        ``from ..utils.loop_monitor import get_loop_status as _gls`` --
        SAME harness constraint as the db_supabase case above means that
        relative import also structurally fails on its own (routes.main's
        __package__ is the single dot-free segment "routes"), so it must
        be forced to succeed the same way: intercept the exact relative
        import call signature (level=2, name="utils.loop_monitor",
        fromlist=("get_loop_status",)) via a patched ``builtins.__import__``
        and hand back a fake ``get_loop_status``. This is what actually
        exercises line 73 (``loop_status = _gls(None)``)."""
        import builtins

        import routes.main as m

        def _fake_gls(_registered):
            return {"healthy": True, "loops": {"fake_relative_loop": {"status": "ok"}}}

        fake_utils_pkg = SimpleNamespace(get_loop_status=_fake_gls)
        real_import = builtins.__import__

        def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name == "utils.loop_monitor" and fromlist == ("get_loop_status",):
                raise ImportError("blocked bare for test")
            if level == 2 and name == "utils.loop_monitor" and fromlist == ("get_loop_status",):
                return fake_utils_pkg
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=_fake_import):
            result = await m.health_check(request=None)

        assert result["status"] == "healthy"
        assert result["loops"] == {"fake_relative_loop": {"status": "ok"}}

    async def test_bare_loop_monitor_import_error_relative_also_fails(self):
        """Blocks only the bare import with ImportError and otherwise lets
        the real import machinery run -- the relative fallback then fails
        on its own (same structural constraint as the db_supabase case),
        landing in the inner ``except Exception:`` (lines 74-78: logs
        'loop_monitor relative import failed; loops field omitted'). This
        is the natural (unpatched) behaviour of that inner except."""
        import builtins

        import routes.main as m

        real_import = builtins.__import__

        def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name == "utils.loop_monitor" and fromlist == ("get_loop_status",):
                raise ImportError("blocked bare for test")
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=_fake_import):
            result = await m.health_check(request=None)

        # both imports failed -> falls back to the pre-seeded default
        assert result["status"] == "healthy"
        assert result["loops"] == {}

    async def test_loop_monitor_import_completely_unavailable(self):
        """Both the bare and relative loop_monitor imports raising sends
        execution into the outer ``except Exception`` -- health still
        reports DB status with the pre-seeded default loop_status."""
        import builtins

        import routes.main as m

        real_import = builtins.__import__

        def _blocking_import(name, *args, **kwargs):
            if name == "utils.loop_monitor" or name.endswith("loop_monitor"):
                raise RuntimeError("totally unavailable")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_blocking_import):
            result = await m.health_check(request=None)

        # Falls back to the pre-seeded default: healthy, empty loops.
        assert result["status"] == "healthy"
        assert result["loops"] == {}

    async def test_loop_monitor_call_raises_after_successful_bare_import(self):
        """get_loop_status callable resolves via the bare import but raises
        when called -- this is NOT an ImportError, so it must be caught by
        the outer ``except Exception`` (not the ImportError branch)."""
        import routes.main as m

        with patch("utils.loop_monitor.get_loop_status", side_effect=RuntimeError("boom")):
            result = await m.health_check(request=None)

        assert result["status"] == "healthy"
        assert result["loops"] == {}
