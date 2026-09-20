"""Every dual-import block must bind the same names in both branches.

CLAUDE.md's dual-import pattern exists because modules are imported two ways
(``python -m backend.server`` vs. a bare top-level path)::

    try:
        from ..utils.thing import helper
    except ImportError:
        from utils.thing import helper   # type: ignore

A name present in only one branch does not fail at import time. It fails at
**call** time, with a bare ``NameError``, and only under whichever import mode
exercises the deficient branch — so it can pass locally, pass a targeted test
run, and still take out a production code path.

This has now happened twice:

* ``notify_safety_team`` was missing from the ``except ImportError`` branch,
  leaving the safety-team notification dead in production (prior audit
  finding #14). A bespoke regression test was added for that one symbol.
* ``payable_ride_filter`` was added to the ``try`` branch of
  ``utils/auto_payout.py`` and omitted from the fallback, producing 32 CI
  failures — ``NameError: name 'payable_ride_filter' is not defined`` raised
  from inside the weekly payout batch's balance computation (2026-09-20
  review, Phase 1).

Pinning one symbol at a time does not close the class, so this scans every
backend module instead. It is a static AST check: no imports are executed, so
it cannot be defeated by import side effects and costs nothing to run.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1]

# Directories with no runtime dual-import obligation.
_SKIP_PARTS = {"tests", "migrations", "__pycache__", "node_modules", "scripts", "evals"}


def _bound_names(body: list[ast.stmt]) -> set[str]:
    """Names a branch binds into the module namespace.

    Recurses into a nested ``try``/``except`` and counts ``def``/assignment
    fallbacks, because a fallback branch legitimately provides a name three
    ways — re-importing it, defining a no-op stub, or assigning a sentinel:

        except ImportError:
            from utils.loop_monitor import record_heartbeat as _record_heartbeat
        ...
            except ImportError:
                def _record_heartbeat(name): ...      # still bound

    Counting only top-level imports would report that as asymmetric.
    """
    names: set[str] = set()
    for node in body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    # A star-import binds an unknowable set; treat the block as
                    # opaque rather than guessing.
                    names.add("*")
                else:
                    names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Try):
            names |= _bound_names(node.body)
            for nested in node.handlers:
                names |= _bound_names(nested.body)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _python_files() -> list[pathlib.Path]:
    out = []
    for path in _BACKEND.rglob("*.py"):
        if _SKIP_PARTS & set(path.relative_to(_BACKEND).parts):
            continue
        out.append(path)
    return sorted(out)


def _asymmetric_blocks(path: pathlib.Path) -> list[str]:
    """Report every try/except-ImportError block whose branches disagree."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - a syntax error is another test's problem
        return []

    problems: list[str] = []
    # MODULE SCOPE ONLY. The dual-import convention is a module-level construct;
    # a function-local `try: from x import f / except ImportError: from y import f
    # as _f` is a deliberate lazy import that often aliases differently on
    # purpose (see routes/main.py's loop-status probe), and flagging those would
    # be noise that gets this whole check switched off.
    for node in tree.body:
        if not isinstance(node, ast.Try):
            continue
        try_names = _bound_names(node.body)
        if not try_names or "*" in try_names:
            continue
        for handler in node.handlers:
            # Only ImportError/ModuleNotFoundError handlers are the dual-import
            # pattern; a broad `except Exception` around an optional dependency
            # legitimately binds nothing.
            exc = handler.type
            exc_names: set[str] = set()
            if isinstance(exc, ast.Name):
                exc_names = {exc.id}
            elif isinstance(exc, ast.Tuple):
                exc_names = {e.id for e in exc.elts if isinstance(e, ast.Name)}
            if not exc_names & {"ImportError", "ModuleNotFoundError"}:
                continue

            except_names = _bound_names(handler.body)
            if not except_names or "*" in except_names:
                # A fallback that imports nothing is an optional-dependency
                # guard (e.g. `except ImportError: helper = None`), not a
                # dual-import block.
                continue

            missing_in_fallback = try_names - except_names
            missing_in_try = except_names - try_names
            if missing_in_fallback or missing_in_try:
                # Relative for readability when the file is under backend/;
                # the detector is also called on tmp files by its own tests.
                try:
                    rel: pathlib.Path | str = path.relative_to(_BACKEND)
                except ValueError:
                    rel = path
                problems.append(
                    f"{rel}:{node.lineno} — try-branch only: {sorted(missing_in_fallback)}; "
                    f"except-branch only: {sorted(missing_in_try)}"
                )
    return problems


@pytest.mark.unit
def test_every_dual_import_block_binds_the_same_names():
    offenders: list[str] = []
    for path in _python_files():
        offenders.extend(_asymmetric_blocks(path))

    assert not offenders, (
        "Dual-import block(s) bind different names in each branch. A name missing from one "
        "branch raises NameError at CALL time under that import mode — it will not fail at "
        "import, so it can reach production silently (see this module's docstring for two "
        "real instances). Add the missing import to the branch that lacks it:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_detector_flags_a_known_asymmetric_block(tmp_path):
    """Positive control: without this, a detector that silently matched nothing
    would leave the test above passing forever and proving nothing."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "try:\n    from ..utils.thing import helper, other\nexcept ImportError:\n    from utils.thing import helper\n",
        encoding="utf-8",
    )
    problems = _asymmetric_blocks(sample)
    assert problems, "the detector failed to flag a genuinely asymmetric block"
    assert "other" in problems[0]


@pytest.mark.unit
def test_detector_accepts_a_symmetric_block(tmp_path):
    sample = tmp_path / "sample_ok.py"
    sample.write_text(
        "try:\n"
        "    from ..utils.thing import helper, other\n"
        "except ImportError:\n"
        "    from utils.thing import helper, other  # type: ignore\n",
        encoding="utf-8",
    )
    assert _asymmetric_blocks(sample) == []


@pytest.mark.unit
def test_detector_ignores_optional_dependency_guards(tmp_path):
    """`except ImportError:` that assigns a sentinel instead of importing is a
    different pattern and must not be reported."""
    sample = tmp_path / "sample_optional.py"
    sample.write_text(
        "try:\n    import fancy_optional\nexcept ImportError:\n    fancy_optional = None\n",
        encoding="utf-8",
    )
    assert _asymmetric_blocks(sample) == []
