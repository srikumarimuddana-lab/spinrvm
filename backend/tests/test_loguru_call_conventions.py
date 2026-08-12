"""loguru log calls must use {}-style formatting and must not pass `exc_info`.

Two silent-failure modes, both of which shipped to production and neither of
which raises, lints, or shows up in a test — the log line just quietly loses
information:

1. **`%s` placeholders.** loguru formats with ``str.format``. ``"%s"`` is not a
   format field, so ``logger.error("failed for ride %s", ride_id)`` emits the
   literal ``%s`` and discards ``ride_id``. Found in production as::

       WARNING core.middleware | CSRF token mismatch: %s %s origin=%s

   55 call sites had it, including the ERROR/CRITICAL lines an operator is
   meant to act on (stuck Stripe events, a charge confirmed while the ride
   update failed).

2. **`exc_info=`.** loguru has no such parameter; the stdlib spelling is
   ``logger.opt(exception=True)``. Passing ``exc_info=True`` hands a keyword to
   ``str.format``, so no traceback is ever attached — and, because the loguru →
   Sentry bridge in ``server.py`` branches on ``record["exception"]``, those
   errors reached Sentry as a bare ``capture_message`` with no stack. 112 call
   sites had it.

Both are asserted statically over the source: neither defect is observable at
runtime without asserting on log *text*, and a unit test per call site would be
absurd. The scan only inspects modules that do ``from loguru import logger`` —
stdlib ``logging`` uses %-style correctly and ``exc_info`` legitimately, so it
must not be caught here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parent.parent

# Conversion specifiers only — `%` alone (e.g. "150% of base") is not a
# placeholder, and `%%` is an escaped literal.
#
# The space flag (`% d`) is deliberately NOT accepted. It is legal printf but
# never used in this codebase, and allowing it makes prose match: "surge at
# 150% of base" contains "% o", where `o` is the octal conversion. That false
# positive is how this line was first written, and the test below pins it.
PLACEHOLDER = re.compile(r"%[-+#0]*[\d.]*[hlL]?[diouxXeEfFgGcrsa]")
LEVELS = {
    "trace",
    "debug",
    "info",
    "success",
    "warning",
    "error",
    "critical",
    "exception",
    "log",
}


def _loguru_modules() -> list[tuple[Path, str]]:
    out = []
    for path in sorted(BACKEND.rglob("*.py")):
        parts = path.parts
        if ".venv" in parts or "tests" in parts:
            continue
        src = path.read_text()
        if "from loguru import logger" in src:
            out.append((path, src))
    return out


def _logger_calls(tree: ast.AST):
    """Yield (call, func) for calls on a `logger` receiver, through .opt()/.bind()."""
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(func, ast.Attribute)):
            continue
        if func.attr not in LEVELS:
            continue
        root = func.value
        while isinstance(root, ast.Call):
            root = root.func
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id == "logger":
            yield node, func


def test_no_percent_style_placeholders_in_loguru_calls():
    offenders = []
    for path, src in _loguru_modules():
        for call, func in _logger_calls(ast.parse(src)):
            args = call.args[1:] if func.attr == "log" else call.args
            if len(args) < 2:
                # No format arguments — loguru does not call .format() at all,
                # so a bare "%" in the text is harmless.
                continue
            msg = args[0]
            if not (isinstance(msg, ast.Constant) and isinstance(msg.value, str)):
                continue
            found = PLACEHOLDER.findall(msg.value)
            if found:
                rel = path.relative_to(BACKEND)
                offenders.append(f"{rel}:{call.lineno}: {found} in {msg.value[:60]!r}")

    assert not offenders, (
        "loguru formats with str.format ({}), not %-style — these calls emit their "
        "placeholders literally and silently drop every argument:\n" + "\n".join(offenders)
    )


def test_no_exc_info_kwarg_in_loguru_calls():
    offenders = []
    for path, src in _loguru_modules():
        for call, _func in _logger_calls(ast.parse(src)):
            for kw in call.keywords:
                if kw.arg == "exc_info":
                    rel = path.relative_to(BACKEND)
                    offenders.append(f"{rel}:{call.lineno}")

    assert not offenders, (
        "loguru has no exc_info parameter — it is swallowed as a str.format keyword "
        "and no traceback is captured. Use logger.opt(exception=True) (or "
        "logger.exception(...)) instead:\n" + "\n".join(offenders)
    )


def test_scan_actually_sees_the_backend():
    """Non-vacuity guard: the two tests above pass trivially on an empty scan."""
    modules = _loguru_modules()
    assert len(modules) > 20, f"only {len(modules)} loguru modules found — scan is broken"
    total_calls = sum(len(list(_logger_calls(ast.parse(src)))) for _p, src in modules)
    assert total_calls > 200, f"only {total_calls} logger calls found — call detection is broken"


def test_detectors_catch_the_original_defects():
    """Both detectors must fire on the exact code that shipped."""
    sample = ast.parse(
        'logger.warning("CSRF token mismatch: %s %s origin=%s", method, path, origin)\n'
        'logger.error("[PAYMENT] settle failed for ride %s", ride_id, exc_info=True)\n'
    )
    calls = list(_logger_calls(sample))
    assert len(calls) == 2
    assert PLACEHOLDER.findall(calls[0][0].args[0].value) == ["%s", "%s", "%s"]
    assert any(kw.arg == "exc_info" for kw in calls[1][0].keywords)


def test_percent_without_conversion_is_not_flagged():
    """A bare '%' is prose, not a placeholder — the scan must not fail on it."""
    sample = ast.parse('logger.info("surge at 150% of base for area {}", area_id)\n')
    call, _func = next(iter(_logger_calls(sample)))
    assert PLACEHOLDER.findall(call.args[0].value) == []
