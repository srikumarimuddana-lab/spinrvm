"""Dual-import branch parity — fallback branches must mirror the try branch.

Every backend route module uses the intentional dual import pattern
(CLAUDE.md § Critical Conventions):

    try:
        from ..utils.foo import bar      # package import mode
    except ImportError:
        from utils.foo import bar       # top-level import mode

A name imported only in the ``try`` branch is a latent NameError in the
import mode that takes the fallback. This is exactly how the Codex P1s on
PR #1757 happened: a formatter stripped ``build_earnings_snapshot`` and
``dollars_to_cents`` from the fallback branches, leaving the tip/rating/
completion and dispute-refund paths broken in top-level import mode.

This test parses every module under ``backend/routes/`` and asserts each
module-level ``try/except ImportError`` block binds the same names in both
branches (imports, function defs, or assignments all count as bindings).

``KNOWN_LEGACY_VIOLATIONS`` is a ratchet baseline of pre-existing gaps in
files outside PR #1757's scope. Do not add to it — fix the fallback branch
instead. Remove entries as the gaps are fixed.
"""

from __future__ import annotations

import ast
import glob
import os

import pytest

pytestmark = pytest.mark.unit

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pre-existing fallback gaps (file → names missing from the except branch).
# These predate the parity check; each is a latent NameError in top-level
# import mode and should be fixed in its own scoped change.
KNOWN_LEGACY_VIOLATIONS = {
    "routes/admin/maintenance.py": {"_run_sync", "_supabase_client"},
    "routes/safety.py": {"notify_safety_team"},
    "routes/websocket.py": {
        "get_app_settings",
        "get_ride_eta_seconds",
        "redis_expire",
        "redis_incr",
    },
}


def _bound_names(stmts: list[ast.stmt]) -> set[str]:
    """Names a statement list binds at module level (imports, defs, assigns)."""
    out: set[str] = set()
    for s in stmts:
        if isinstance(s, ast.ImportFrom):
            out |= {a.asname or a.name for a in s.names}
        elif isinstance(s, ast.Import):
            out |= {(a.asname or a.name).split(".")[0] for a in s.names}
        elif isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(s.name)
        elif isinstance(s, ast.Assign):
            out |= {t.id for t in s.targets if isinstance(t, ast.Name)}
    return out


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if isinstance(t, ast.Name):
        return t.id == "ImportError"
    if isinstance(t, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id == "ImportError" for e in t.elts)
    return False


def _violations(path: str) -> set[str]:
    tree = ast.parse(open(path).read())
    missing: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Try):
            continue
        if not any(_catches_import_error(h) for h in node.handlers if h.type is not None):
            continue
        body_names = _bound_names(node.body)
        handler_names: set[str] = set()
        for h in node.handlers:
            handler_names |= _bound_names(h.body)
        missing |= body_names - handler_names
    return missing


def _route_files() -> list[str]:
    return sorted(glob.glob(os.path.join(_BACKEND_DIR, "routes", "**", "*.py"), recursive=True))


def test_fallback_import_branches_mirror_try_branches():
    failures = {}
    for path in _route_files():
        rel = os.path.relpath(path, _BACKEND_DIR).replace(os.sep, "/")
        missing = _violations(path) - KNOWN_LEGACY_VIOLATIONS.get(rel, set())
        if missing:
            failures[rel] = sorted(missing)
    assert not failures, (
        "Names imported in the try branch but missing from the except ImportError "
        f"fallback (latent NameError in top-level import mode): {failures}"
    )


def test_legacy_baseline_is_not_stale():
    """Fail when a baselined violation gets fixed, so the entry is removed."""
    stale = {}
    for rel, names in KNOWN_LEGACY_VIOLATIONS.items():
        path = os.path.join(_BACKEND_DIR, rel)
        if not os.path.exists(path):
            stale[rel] = "file gone"
            continue
        fixed = names - _violations(path)
        if fixed:
            stale[rel] = sorted(fixed)
    assert not stale, f"Baseline entries no longer violating — remove them from KNOWN_LEGACY_VIOLATIONS: {stale}"
