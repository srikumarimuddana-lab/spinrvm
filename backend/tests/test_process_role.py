"""WS-A / audit C2: PROCESS_ROLE splits the API process from the loop process.

Today every replica runs the HTTP service AND all ~40 background loops, so an
eight-machine scale-out means eight copies of every batch loop contending on
best-effort Redis leader locks that fail OPEN. `PROCESS_ROLE` lets one machine
own the loops.

The property that matters most here is the DEFAULT: `all` must be
byte-for-byte today's behaviour, because Railway standby, local dev and the
whole test suite run on it. A regression that made `all` skip a loop would be
a silent outage of every reminder, retry and reconciliation job, so the
"default is unchanged" cases below are as load-bearing as the split itself.

Scoped to the role plumbing. The loop bodies are not touched by WS-A and are
not re-tested here.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.unit

_PROD_BASE = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test_key",
    "JWT_SECRET": "a" * 32,
    "ADMIN_PASSWORD": "StrongPass123!ExtraLong",
    "FIREBASE_DRIVER_APP_ID": "driver-app-id",
    "FIREBASE_RIDER_APP_ID": "rider-app-id",
    "SUPABASE_REGION": "ca-central-1",
    "ENV": "production",
    "REDIS_URL": "redis://localhost:6379/0",
}

_DEV_BASE = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test_key",
    "JWT_SECRET": "dev-secret",
    "ADMIN_PASSWORD": "anything",
    "ENV": "development",
}


def _make_settings(base: dict, **overrides):
    """Fresh Settings() under the given env, environment restored after.

    Same reload-avoidance rule as test_core_config_coverage.py: construct the
    CLASS, never `importlib.reload(core.config)` — that swaps the singleton
    under one of the two dual-import module names and leaves the other stale.
    """
    from backend.core.config import Settings

    merged = dict(base)
    for k, v in overrides.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v

    saved = {k: os.environ.get(k) for k in set(merged) | set(overrides)}
    try:
        for k in saved:
            os.environ.pop(k, None)
        for k, v in merged.items():
            os.environ[k] = str(v)
        return Settings(_env_file=None)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestProcessRoleValidation:
    def test_defaults_to_all(self):
        assert _make_settings(_DEV_BASE).PROCESS_ROLE == "all"

    @pytest.mark.parametrize("role", ["all", "web", "worker"])
    def test_accepts_each_known_role(self, role):
        assert _make_settings(_DEV_BASE, PROCESS_ROLE=role).PROCESS_ROLE == role

    @pytest.mark.parametrize("role", ["ALL", "Web", "WORKER"])
    def test_normalises_case(self, role):
        # Readers compare against lowercase literals; normalising once here
        # keeps every call site from having to remember to `.lower()`.
        assert _make_settings(_DEV_BASE, PROCESS_ROLE=role).PROCESS_ROLE == role.lower()

    @pytest.mark.parametrize("role", ["wroker", "api", "", "web,worker"])
    def test_rejects_unknown_role(self, role):
        # A typo must not fall through to a default that runs every loop on a
        # machine meant to run none — that failure is invisible until two
        # replicas double-charge something.
        with pytest.raises(Exception) as ei:
            _make_settings(_DEV_BASE, PROCESS_ROLE=role)
        assert "PROCESS_ROLE" in str(ei.value)


class TestRedisRequiredForSplitRoles:
    @pytest.mark.parametrize("role", ["web", "worker"])
    def test_production_split_role_requires_redis(self, role):
        with pytest.raises(Exception) as ei:
            _make_settings(_PROD_BASE, PROCESS_ROLE=role, REDIS_URL="")
        assert "REDIS_URL" in str(ei.value)

    def test_production_role_all_still_boots_without_redis(self):
        # Deliberately unchanged: extending the fail-fast to `all` is the
        # audit's recommendation but it changes DEFAULT production boot
        # behaviour, so it is a separate, explicit decision. If this test is
        # ever flipped, that decision is what changed — not a bug fix.
        s = _make_settings(_PROD_BASE, PROCESS_ROLE="all", REDIS_URL="")
        assert s.PROCESS_ROLE == "all"

    @pytest.mark.parametrize("role", ["web", "worker"])
    def test_non_production_split_role_does_not_require_redis(self, role):
        # Local dev must be able to exercise the split without running Redis.
        s = _make_settings(_DEV_BASE, PROCESS_ROLE=role, REDIS_URL="")
        assert s.PROCESS_ROLE == role


class TestSpawnGate:
    """The `_spawn` role gate, exercised through its own logic.

    `lifespan()` is an async context manager that opens DB connections and
    spawns 40 tasks; test_core_lifespan_coverage.py already owns driving it.
    What WS-A adds is a decision — "does this role spawn this name?" — and
    that decision is what is pinned here.
    """

    @staticmethod
    def _should_spawn(role: str, name: str, *, env_is_test: bool = False) -> bool:
        always_on = frozenset({"capacity_watchdog (60s)"})
        if env_is_test:
            return False
        if role == "web" and name not in always_on:
            return False
        return True

    @pytest.mark.parametrize("role", ["all", "worker"])
    @pytest.mark.parametrize(
        "name",
        ["payment_retry (5min)", "auto_payout (1h)", "loop_watchdog (5min)", "capacity_watchdog (60s)"],
    )
    def test_all_and_worker_spawn_everything(self, role, name):
        assert self._should_spawn(role, name) is True

    @pytest.mark.parametrize("name", ["payment_retry (5min)", "auto_payout (1h)", "loop_watchdog (5min)"])
    def test_web_spawns_no_batch_loops(self, name):
        assert self._should_spawn("web", name) is False

    def test_web_still_spawns_the_capacity_watchdog(self):
        # A web machine with no capacity watchdog is one whose thread-pool
        # saturation is invisible — the opposite of what the split is for.
        assert self._should_spawn("web", "capacity_watchdog (60s)") is True

    def test_env_test_skip_still_wins_over_role(self):
        assert self._should_spawn("worker", "auto_payout (1h)", env_is_test=True) is False


class TestSpawnGateMatchesSource:
    """Pin the assumptions above to the real `core/lifespan.py`.

    Without this, the hand-rolled `_should_spawn` could drift from the shipped
    gate and the class above would be testing a fiction.
    """

    @staticmethod
    def _src() -> str:
        import pathlib

        return (pathlib.Path(__file__).resolve().parents[1] / "core" / "lifespan.py").read_text()

    def test_names_are_recorded_before_the_role_skip(self):
        src = self._src()
        record = src.index("_spawned_loop_names.append(name)")
        skip = src.index("if _skip_for_role and name not in _ALWAYS_ON_LOOPS:")
        # Ordering is load-bearing: the watchdog-coverage self-check asserts
        # every loop is registered, and that must hold on a `web` machine that
        # spawns almost none of them.
        assert record < skip

    def test_capacity_watchdog_is_the_always_on_set(self):
        assert '_ALWAYS_ON_LOOPS = frozenset({"capacity_watchdog (60s)"})' in self._src()

    def test_only_web_skips(self):
        assert '_skip_for_role = _process_role == "web"' in self._src()

    def test_startup_line_reports_the_role(self):
        assert "role={_process_role}" in self._src()
