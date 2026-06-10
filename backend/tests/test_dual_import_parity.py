"""Dual-import parity guard.

Every backend module uses the try/except ImportError dual-import pattern
(`python -m backend.server` vs top-level). A name imported only in the try
branch silently NameErrors in the other import mode — which is exactly how
a settlement metrics call could 500 a successful payment (PR #1758 Codex
finding). This test parses the modules touched by the observability work
and asserts both branches bind the same names.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

GUARDED_MODULES = [
    "routes/rides.py",
    "routes/drivers.py",
    "routes/websocket.py",
    "services/dispatch_service.py",
    "socket_manager.py",
    "utils/breadcrumbs.py",
    "utils/breadcrumb_buffer.py",
]


def _bound_names(stmts) -> set[str]:
    """Names bound by imports, stub defs, assignments, or nested try/except
    fallbacks (websocket.py falls back to stub `async def`s, not imports)."""
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


def _dual_import_blocks(tree: ast.Module):
    """Yield (lineno, try_names, except_names) for module-level
    try/except-ImportError blocks consisting of imports."""
    for node in tree.body:
        if not isinstance(node, ast.Try):
            continue
        if not any(
            isinstance(h.type, ast.Name) and h.type.id == "ImportError" for h in node.handlers if h.type is not None
        ):
            continue
        try_names = _bound_names(node.body)
        if not try_names:
            continue
        except_names: set[str] = set()
        for h in node.handlers:
            except_names |= _bound_names(h.body)
        yield node.lineno, try_names, except_names


@pytest.mark.parametrize("rel_path", GUARDED_MODULES)
def test_both_import_branches_bind_the_same_names(rel_path: str):
    source = (BACKEND / rel_path).read_text()
    tree = ast.parse(source)
    problems = []
    for lineno, try_names, except_names in _dual_import_blocks(tree):
        missing = try_names - except_names
        if missing:
            problems.append(f"{rel_path}:{lineno} fallback branch missing: {sorted(missing)}")
    assert not problems, "Dual-import fallback drift (NameError in top-level import mode):\n  " + "\n  ".join(problems)
