"""Regression test for finding 14: notify_safety_team must be bound in BOTH
import branches of routes/safety.py.

server.py imports `from routes.safety import api_router` (top-level, with
backend/ on sys.path), so the relative `from ..features import ...` in the try
branch raises ImportError and the *except* branch is what actually runs in
production. That branch previously imported only db_supabase / get_current_user
/ create_ticket_for_safety — omitting notify_safety_team — so every
POST /safety/report hit `NameError` at the notify call. The broad except around
it logged and still returned success, so the WS broadcast, email fan-out, and
CRITICAL on-call paging silently never fired for any safety report.
"""

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

_SAFETY_SRC = pathlib.Path(__file__).resolve().parents[1] / "routes" / "safety.py"


def _import_names_by_branch():
    """Return (try_names, except_names) imported in the top-level try/except."""
    tree = ast.parse(_SAFETY_SRC.read_text())
    try_names: set[str] = set()
    except_names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.ImportFrom):
                try_names.update(a.name for a in stmt.names)
        for handler in node.handlers:
            for stmt in handler.body:
                if isinstance(stmt, ast.ImportFrom):
                    except_names.update(a.name for a in stmt.names)
    return try_names, except_names


def test_fallback_branch_imports_notify_safety_team():
    """The production (except ImportError) branch must import notify_safety_team."""
    try_names, except_names = _import_names_by_branch()
    assert "notify_safety_team" in try_names, "try branch lost notify_safety_team import"
    assert "notify_safety_team" in except_names, (
        "except-ImportError branch (runs in production) is missing notify_safety_team — "
        "POST /safety/report will NameError and the safety team is never paged"
    )


def test_notify_safety_team_bound_on_module():
    """Behavioral guard: however the module resolves, the symbol is bound."""
    try:
        from backend.routes import safety
    except Exception as exc:  # pragma: no cover - only if backend deps unavailable
        pytest.skip(f"backend not importable in this environment: {exc}")
    assert callable(getattr(safety, "notify_safety_team", None))
