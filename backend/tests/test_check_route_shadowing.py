"""Regression coverage for scripts/check_route_shadowing.py, run against the
REAL backend/server.py so this is an actual CI backstop, not just unit tests
of the checker's own logic — the whole point of fixing this checker (see
docs/change-log/2026-08-18-retire-duplicate-faqs-route.md's "flagged, not
fixed" section) was so this class of bug gets caught automatically going
forward. A checker nobody runs is exactly the illusion of coverage this
session's FAQ audit kept finding — don't repeat that here.

Known pre-existing issue (documented, not fixed by this test file): the
checker's new cross-router literal-duplicate check found that
`features.py`'s `admin_support_router.get("/tickets")` and `.get("/faqs")`
are shadowed by `support_router`'s identical, earlier-registered paths in
the same `v1_api_router` mount group — and, per the admin-dashboard source
(it calls `/api/admin/tickets` and `/api/admin/faqs`, served by the
`routes/admin` package, never `/api/v1/tickets` or `/api/v1/faqs`), these
two `admin_support_router` handlers appear to be fully dead code, not just
shadowed. This mirrors the `routes/faqs.py` dead-code pattern fixed in PR
#4199, but inside `features.py` this time. NOT fixed here — deliberately
out of scope for "fix the checker's detection gap" (this PR's actual ask);
flagged directly to the user as a separate follow-up. `_KNOWN_VIOLATIONS`
below is a closed allowlist (not a general suppression mechanism) so this
test still fails loudly on any NEW violation while not blocking CI on the
pre-existing one.
"""

from pathlib import Path

try:
    from backend.scripts.check_route_shadowing import (
        _parse_imports,
        _parse_include_router_calls,
        _router_own_prefix,
        _shadow_violations,
        find_server_mount_violations,
    )
except ImportError:
    from scripts.check_route_shadowing import (
        _parse_imports,
        _parse_include_router_calls,
        _router_own_prefix,
        _shadow_violations,
        find_server_mount_violations,
    )

SERVER_PY = Path(__file__).resolve().parent.parent / "server.py"

# (method, path, name) -> must exactly match a real, currently-open finding.
# Shrinking this set (by fixing the underlying dead code) is welcome; growing
# it silently is not — any new entry here should point at a tracked decision,
# not just be added to make this test pass.
_KNOWN_VIOLATIONS = {
    ("GET", "/tickets", "admin_get_tickets"),
    ("GET", "/faqs", "admin_get_faqs"),
}


def test_server_mounts_have_no_unexpected_route_shadowing():
    violations, _skipped, _group_sizes = find_server_mount_violations(SERVER_PY)

    unexpected = [v for v in violations if (v["method"], v["path"], v["name"]) not in _KNOWN_VIOLATIONS]
    assert not unexpected, (
        "New route-shadowing violation(s) found in backend/server.py's router "
        f"mounts (not in the known-issues allowlist): {unexpected}"
    )

    # If a known violation stops appearing, the allowlist is stale — someone
    # fixed the underlying dead code and forgot to shrink this set, or the
    # checker regressed and stopped detecting it. Either way, surface it.
    found = {(v["method"], v["path"], v["name"]) for v in violations}
    missing = _KNOWN_VIOLATIONS - found
    assert not missing, (
        f"Known violation(s) no longer detected: {missing}. If the underlying dead code was "
        "actually fixed, shrink _KNOWN_VIOLATIONS in this test. If not, the checker regressed."
    )


def test_server_mounts_walk_produces_the_expected_router_groups():
    """Sanity-check the walk itself isn't silently finding nothing — e.g. if
    server.py's import style changed and _parse_imports stopped resolving
    anything, the violation-count assertions above would trivially pass for
    the wrong reason (zero routes walked, not zero real duplicates)."""
    _violations, skipped, group_sizes = find_server_mount_violations(SERVER_PY)

    assert group_sizes, "expected at least one (parent_var, prefix) group to be walked"
    assert group_sizes.get("v1_api_router (prefix='')", 0) > 100, (
        "v1_api_router should resolve well over 100 routes across its ~40 mounted "
        "sub-routers — a much smaller number likely means the import/call parser "
        "stopped resolving most of them"
    )
    # Package-typed imports (routes.admin, routes.rides, routes.drivers) are a
    # documented, deliberate limitation (see the module docstring) — assert
    # they're still being skipped-with-a-note rather than silently dropped or
    # crashing.
    assert any("package" in note for note in skipped)


class TestShadowViolationsUnit:
    """Unit-level checks on the detection logic itself, independent of
    server.py's actual content (which will keep changing) — so a future
    edit to _shadow_violations can't silently break either check class
    while the two integration tests above still happen to pass."""

    def test_literal_shadowed_by_earlier_param_route(self):
        routes = [
            ("GET", "/{driver_id}", "get_driver", "a.py"),
            ("GET", "/leaderboard", "get_leaderboard", "a.py"),
        ]
        violations = list(_shadow_violations(routes))
        assert violations == [("param-shadow", 1, 0)]

    def test_exact_duplicate_literal_across_two_sources(self):
        routes = [
            ("GET", "/faqs", "get_faqs", "features.py"),
            ("GET", "/faqs", "get_public_faqs", "routes/faqs.py"),
        ]
        violations = list(_shadow_violations(routes))
        assert violations == [("duplicate", 1, 0)]

    def test_different_methods_on_the_same_literal_path_do_not_collide(self):
        routes = [
            ("GET", "/faqs", "get_faqs", "features.py"),
            ("POST", "/faqs", "create_faq", "routes/admin/faqs.py"),
        ]
        assert list(_shadow_violations(routes)) == []

    def test_no_false_positive_for_two_distinct_param_routes(self):
        """Two differently-named {param} routes on the same method are not
        flagged — that's a real ambiguity FastAPI allows (first wins), not
        the bug class this checker targets."""
        routes = [
            ("GET", "/{driver_id}", "get_driver", "a.py"),
            ("GET", "/{ride_id}", "get_ride", "b.py"),
        ]
        assert list(_shadow_violations(routes)) == []


class TestServerPyParsingUnit:
    def test_parse_imports_resolves_module_and_attr_with_and_without_alias(self):
        src = (
            "from routes.rides import api_router as rides_router\n"
            "from features import admin_support_router, pricing_router\n"
        )
        imports = _parse_imports(src)
        assert imports["rides_router"] == ("routes.rides", "api_router")
        assert imports["admin_support_router"] == ("features", "admin_support_router")
        assert imports["pricing_router"] == ("features", "pricing_router")

    def test_parse_include_router_calls_captures_parent_router_and_literal_prefix(self):
        src = (
            "v1_api_router.include_router(rides_router)\n"
            'app.include_router(auth_router, prefix="/api/v1")\n'
        )
        calls = _parse_include_router_calls(src)
        assert calls == [
            ("v1_api_router", "rides_router", "", True),
            ("app", "auth_router", "/api/v1", True),
        ]

    def test_parse_include_router_calls_flags_dynamic_prefix_as_unresolvable(self):
        src = "app.include_router(auth_router, prefix=some_variable)\n"
        calls = _parse_include_router_calls(src)
        assert calls == [("app", "auth_router", "", False)]

    def test_router_own_prefix_reads_literal_apirouter_prefix_kwarg(self):
        src = 'documents_router = APIRouter(prefix="/drivers", tags=["Driver Documents"])\n'
        assert _router_own_prefix(src, "documents_router") == "/drivers"

    def test_router_own_prefix_defaults_to_empty_when_absent(self):
        src = 'support_router = APIRouter(tags=["Support"])\n'
        assert _router_own_prefix(src, "support_router") == ""
