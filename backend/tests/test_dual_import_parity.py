"""Dual-import branch parity — fallback branches must mirror the try branch.

Every backend module uses the intentional dual import pattern
(CLAUDE.md § Critical Conventions):

    try:
        from ..utils.foo import bar      # package import mode
    except ImportError:
        from utils.foo import bar       # top-level import mode

A name bound only in the ``try`` branch is a latent NameError in the import
mode that takes the fallback. Two independent regressions proved the class:
a formatter stripped ``build_earnings_snapshot`` / ``dollars_to_cents`` from
fallback branches (PR #1757 Codex P1s), and a settlement metrics call could
500 a successful payment (PR #1758 Codex finding).

This file unifies both PRs' guards: it parses every module under
``backend/routes/`` plus the observability-touched modules, and asserts each
module-level ``try/except ImportError`` block binds the same names in both
branches. Imports, function/class defs, assignments, and nested try/except
fallbacks (websocket.py falls back to stub ``async def``s) all count as
bindings.

``KNOWN_LEGACY_VIOLATIONS`` is a ratchet baseline of pre-existing gaps.
Do not add to it — fix the fallback branch instead. Remove entries as the
gaps are fixed (the stale-baseline test enforces removal).
"""

from __future__ import annotations

import ast
import glob
import os

import pytest

pytestmark = pytest.mark.unit

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Non-routes modules guarded since PR #1758's observability work.
EXTRA_GUARDED_MODULES = [
    "services/dispatch_service.py",
    "socket_manager.py",
    "utils/breadcrumbs.py",
    "utils/breadcrumb_buffer.py",
    # AI assistant modules — same hazard class hit PR #1843 (Codex P2s:
    # a formatter stripped _metric_timed / promo_available_limit from
    # fallback branches mid-refactor).
    "ai/conversations.py",
    "ai/orchestrator.py",
    "ai/tools.py",
    "ai/tools_account.py",
    "ai/tools_booking.py",
    "ai/tools_rides.py",
    "ai/tools_support.py",
]

# Pre-existing fallback gaps (file → names missing from the except branch).
# Each is a latent NameError in top-level import mode; fix in scoped changes.
KNOWN_LEGACY_VIOLATIONS = {
    "routes/admin/maintenance.py": {"_run_sync", "_supabase_client"},
}


def _bound_names(stmts) -> set[str]:
    """Names a statement list binds: imports, defs, assigns, and nested
    try/except fallbacks (stub defs count as bindings)."""
    names: set[str] = set()
    for node in stmts:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Try):
            names |= _bound_names(node.body)
            for h in node.handlers:
                names |= _bound_names(h.body)
    return names


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


def _guarded_files() -> list[str]:
    routes = sorted(glob.glob(os.path.join(_BACKEND_DIR, "routes", "**", "*.py"), recursive=True))
    extras = [os.path.join(_BACKEND_DIR, rel) for rel in EXTRA_GUARDED_MODULES]
    return routes + [p for p in extras if os.path.exists(p)]


def test_fallback_import_branches_mirror_try_branches():
    failures = {}
    for path in _guarded_files():
        rel = os.path.relpath(path, _BACKEND_DIR).replace(os.sep, "/")
        missing = _violations(path) - KNOWN_LEGACY_VIOLATIONS.get(rel, set())
        if missing:
            failures[rel] = sorted(missing)
    assert not failures, (
        "Names bound in the try branch but missing from the except ImportError "
        f"fallback (latent NameError in top-level import mode): {failures}"
    )


def test_extra_guarded_modules_exist():
    """If an observability module is renamed, move the guard with it."""
    gone = [rel for rel in EXTRA_GUARDED_MODULES if not os.path.exists(os.path.join(_BACKEND_DIR, rel))]
    assert not gone, f"Guarded modules missing — update EXTRA_GUARDED_MODULES: {gone}"


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
