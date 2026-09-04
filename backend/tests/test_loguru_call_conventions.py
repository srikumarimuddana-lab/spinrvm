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

3. **`extra=`.** loguru also has no `extra` parameter; the stdlib spelling is
   ``logger.bind(**kwargs)``. Passing ``extra={...}`` hands a dict to
   ``str.format`` as a single keyword no message field ever references, so it
   is silently dropped — the structured context (e.g. an undercharge amount,
   a Stripe event id) never reaches the log line or the loguru → Sentry
   bridge. 6 call sites had it (ACTION_ITEMS.md C69).

All three are asserted statically over the source: neither defect is observable
at runtime without asserting on log *text*, and a unit test per call site would
be absurd. Only loguru modules are inspected — stdlib ``logging`` uses %-style,
``exc_info``, and ``extra=`` correctly, so it must not be caught here.

**Selecting those modules is the hard part, and getting it wrong is silent.**
This scan originally picked files with the substring ``from loguru import
logger``. That missed every module which takes ``logger`` from a package-local
re-export, and ``backend/routes/rides/*`` does exactly that — 12 modules and 221
call sites, including the whole dispatch path, were never scanned. 161 of those
calls carried one of the two defects above while the suite stayed green.

The two ``_deps`` modules are also not interchangeable, so "scan anything that
isn't obviously stdlib" would be wrong in the other direction:

    routes/rides/_deps.py     from loguru import logger        -> loguru
    routes/drivers/_deps.py   logger = logging.getLogger(...)  -> stdlib

``routes/drivers/*`` is therefore stdlib, and its ``exc_info=``/``%s`` calls are
correct. So the selector below *resolves* each module's ``logger`` binding,
following ``from ._deps import logger`` to the module that defines it, and
``test_every_logger_binding_resolves`` fails on any binding it cannot classify —
the point being that the next re-export cannot quietly slip out of the scan the
way this one did.
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


def _read(rel: str) -> tuple[Path, str]:
    path = BACKEND / rel
    return path, path.read_text()


def _backend_modules() -> list[tuple[Path, str]]:
    out = []
    for path in sorted(BACKEND.rglob("*.py")):
        parts = path.parts
        if ".venv" in parts or "tests" in parts:
            continue
        out.append((path, path.read_text()))
    return out


def _resolve_module(path: Path, level: int, module: str | None) -> Path | None:
    """Map an `from ... import` target to a file inside BACKEND, or None."""
    parts = (module or "").split(".") if module else []
    if level:
        # level 1 == the importing module's own package directory
        try:
            base = path.parents[level - 1]
        except IndexError:
            return None
        bases = [base]
    else:
        # Absolute, in both spellings the dual-import pattern produces:
        # `backend.routes.rides._deps` and `routes.rides._deps`.
        bases = [BACKEND.parent, BACKEND]
    for base in bases:
        cand = base.joinpath(*parts)
        for f in (cand.with_suffix(".py"), cand / "__init__.py"):
            if f.is_file() and BACKEND in f.parents:
                return f
    return None


def _logger_flavor(path: Path, src: str, seen: frozenset[Path] = frozenset()) -> str:
    """'loguru' | 'stdlib' | 'unknown' for the module-level `logger` name.

    Follows re-exports (`from ._deps import logger`) to the defining module.
    `ast.walk` rather than top-level-only, so the dual-import try/except that
    every module in this codebase uses is covered.
    """
    if path in seen:
        return "unknown"  # import cycle — refuse to guess
    seen = seen | {path}
    tree = ast.parse(src)

    # Local names bound to loguru's logger under *any* alias, so that
    # `from loguru import logger as _raw` + `logger = _raw.bind(...)` resolves.
    loguru_aliases = {
        a.asname or a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "loguru"
        for a in node.names
        if a.name == "logger"
    }

    flavors = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            bound = [a for a in node.names if (a.asname or a.name) == "logger"]
            if not bound:
                continue
            if any(a.name != "logger" for a in bound):
                flavors.add("unknown")  # `from x import something as logger` — don't guess
                continue
            if node.level == 0 and node.module == "loguru":
                flavors.add("loguru")
                continue
            target = _resolve_module(path, node.level, node.module)
            if target is None:
                flavors.add("unknown")
            else:
                flavors.add(_logger_flavor(target, target.read_text(), seen))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(t, ast.Name) and t.id == "logger" for t in targets):
                continue
            value = node.value
            if value is None:
                continue  # bare `logger: Logger` annotation binds nothing
            if any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "getLogger"
                for n in ast.walk(value)
            ):
                flavors.add("stdlib")
                continue
            root = value  # unwrap `.bind(...).opt(...)` chains back to their receiver
            while isinstance(root, ast.Call):
                root = root.func
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in loguru_aliases:
                flavors.add("loguru")
            elif isinstance(root, ast.Name) and root.id == "logger":
                pass  # `logger = logger` alias; the real binding is elsewhere
            else:
                flavors.add("unknown")
    if flavors == {"loguru"}:
        return "loguru"
    if flavors == {"stdlib"}:
        return "stdlib"
    return "unknown"


def _loguru_modules() -> list[tuple[Path, str]]:
    return [
        (path, src)
        for path, src in _backend_modules()
        if _logger_flavor(path, src) == "loguru" and any(True for _ in _logger_calls(ast.parse(src)))
    ]


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


def test_no_extra_kwarg_in_loguru_calls():
    offenders = []
    for path, src in _loguru_modules():
        for call, _func in _logger_calls(ast.parse(src)):
            for kw in call.keywords:
                if kw.arg == "extra":
                    rel = path.relative_to(BACKEND)
                    offenders.append(f"{rel}:{call.lineno}")

    assert not offenders, (
        "loguru has no extra parameter — it is swallowed as a str.format keyword and the "
        "structured context is silently dropped. Use logger.bind(**kwargs) instead:\n" + "\n".join(offenders)
    )


def test_scan_actually_sees_the_backend():
    """Non-vacuity guard: the two tests above pass trivially on an empty scan."""
    modules = _loguru_modules()
    assert len(modules) > 40, f"only {len(modules)} loguru modules found — scan is broken"
    total_calls = sum(len(list(_logger_calls(ast.parse(src)))) for _p, src in modules)
    assert total_calls > 700, f"only {total_calls} logger calls found — call detection is broken"


def test_scan_covers_reexported_loggers_and_only_those():
    """Pins both edges of the selector — the blind spot, and its overcorrection.

    ``routes/rides/*`` re-exports loguru's logger and must be scanned; the
    original substring selector missed it. ``routes/drivers/*`` re-exports a
    *stdlib* logger and must not be, or the scan would demand loguru spellings
    for calls where ``exc_info=``/``%s`` are correct.
    """
    scanned = {p.relative_to(BACKEND).as_posix() for p, _s in _loguru_modules()}
    assert "routes/rides/matching.py" in scanned
    assert "routes/rides/booking.py" in scanned
    assert "routes/drivers/ride_flow.py" not in scanned
    assert "routes/drivers/subscriptions.py" not in scanned

    assert _logger_flavor(*_read("routes/rides/_deps.py")) == "loguru"
    assert _logger_flavor(*_read("routes/drivers/_deps.py")) == "stdlib"


def test_every_logger_binding_resolves():
    """No module may call `logger.*` with a binding the selector cannot classify.

    This is the guard the original scan lacked: an unrecognised binding used to
    mean "silently not scanned", which is how 161 defective calls stayed green.
    It now means a failing test, and the fix is to teach `_logger_flavor` the new
    shape — never to leave the module unclassified.
    """
    unresolved = [
        p.relative_to(BACKEND).as_posix()
        for p, s in _backend_modules()
        if any(True for _ in _logger_calls(ast.parse(s))) and _logger_flavor(p, s) == "unknown"
    ]
    assert not unresolved, (
        "these modules call logger.* but their `logger` binding could not be traced to "
        "loguru or stdlib logging, so they are in neither scan:\n" + "\n".join(unresolved)
    )


def test_detectors_catch_the_original_defects():
    """All three detectors must fire on the exact code that shipped."""
    sample = ast.parse(
        'logger.warning("CSRF token mismatch: %s %s origin=%s", method, path, origin)\n'
        'logger.error("[PAYMENT] settle failed for ride %s", ride_id, exc_info=True)\n'
        'logger.error("[estimate] billing haversine (undercharge)", extra={"haversine_km": km})\n'
    )
    calls = list(_logger_calls(sample))
    assert len(calls) == 3
    assert PLACEHOLDER.findall(calls[0][0].args[0].value) == ["%s", "%s", "%s"]
    assert any(kw.arg == "exc_info" for kw in calls[1][0].keywords)
    assert any(kw.arg == "extra" for kw in calls[2][0].keywords)


def test_percent_without_conversion_is_not_flagged():
    """A bare '%' is prose, not a placeholder — the scan must not fail on it."""
    sample = ast.parse('logger.info("surge at 150% of base for area {}", area_id)\n')
    call, _func = next(iter(_logger_calls(sample)))
    assert PLACEHOLDER.findall(call.args[0].value) == []
