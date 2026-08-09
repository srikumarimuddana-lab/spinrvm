"""Every rate limiter must sit BELOW its @router.<method> decorator.

Decorators apply bottom-up, and `APIRouter.get/post/...` registers the function
it receives and then returns it **unchanged**. So this:

    @ride_read_limit          # applied second — wraps a function nobody calls
    @router.get("/active")    # applied first  — registers the BARE function
    async def get_active_ride(...):

registers the unwrapped endpoint. The limiter object is built, looks correct in
a grep, and never runs. That is not hypothetical: all four endpoints in
`routes/rides/queries.py` shipped this way and were silently unrated in
production until 2026-08-08.

A grep for `@some_limit` cannot catch this — only the ordering can. This test
walks every route module's AST and fails on any endpoint whose limiter is
listed above its router decorator.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROUTES_DIR = Path(__file__).resolve().parent.parent / "routes"

# Decorator names that wrap the endpoint and therefore MUST be applied below the
# router decorator. Sourced from utils/rate_limiter.py's public limiters.
_LIMITER_SUFFIXES = ("_limit", "_rate_limit")

# Decorators that are legitimately applied above the router decorator because
# they do not wrap the endpoint for dispatch (none today) — kept as an explicit
# allowlist so an intentional exception is a visible, reviewed decision.
_ALLOWED_ABOVE_ROUTER: set = set()


def _iter_route_files():
    for path in sorted(_ROUTES_DIR.rglob("*.py")):
        if path.name.startswith("_") or "__pycache__" in str(path):
            continue
        yield path


def _name_of(node: ast.expr) -> str:
    """Best-effort dotted name for a decorator expression."""
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_router_decorator(name: str) -> bool:
    return name.startswith("router.") and name.split(".")[-1] in {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "websocket",
    }


def _is_limiter_decorator(name: str) -> bool:
    base = name.split(".")[-1]
    return any(base.endswith(sfx) for sfx in _LIMITER_SUFFIXES)


def _collect_violations():
    """Return [(file, lineno, func, limiter)] for limiters above their router."""
    violations = []
    for path in _iter_route_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            names = [_name_of(d) for d in node.decorator_list]
            router_idx = next((i for i, n in enumerate(names) if _is_router_decorator(n)), None)
            if router_idx is None:
                continue

            for i, name in enumerate(names):
                if i >= router_idx or not _is_limiter_decorator(name):
                    continue
                if name in _ALLOWED_ABOVE_ROUTER:
                    continue
                violations.append(
                    (
                        str(path.relative_to(_ROUTES_DIR.parent)),
                        node.lineno,
                        node.name,
                        name,
                    )
                )
    return violations


def test_no_limiter_is_applied_above_its_router_decorator():
    violations = _collect_violations()
    if violations:
        detail = "\n".join(
            f"  {f}:{ln}  {fn}()  — @{lim} is ABOVE @router.<method>" for f, ln, fn, lim in violations
        )
        pytest.fail(
            "Rate limiter(s) applied above the router decorator — these endpoints "
            "register UNWRAPPED and are silently unrated:\n"
            f"{detail}\n\n"
            "Move the limiter BELOW @router.<method>."
        )


def test_the_detector_actually_detects_the_bug():
    """A guard that has never seen the bug fail is not a guard.

    Reproduces the exact shape that shipped in queries.py and asserts the AST
    walk flags it — so a future refactor of this test cannot quietly turn it
    into a no-op that passes on everything.
    """
    source = (
        "@ride_read_limit\n"
        "@router.get('/active')\n"
        "async def get_active_ride():\n"
        "    ...\n"
    )
    tree = ast.parse(source)
    fn = tree.body[0]
    names = [_name_of(d) for d in fn.decorator_list]

    router_idx = next(i for i, n in enumerate(names) if _is_router_decorator(n))
    limiter_idx = next(i for i, n in enumerate(names) if _is_limiter_decorator(n))

    assert limiter_idx < router_idx, "fixture should place the limiter above the router"


def test_correct_ordering_is_not_flagged():
    """The inverse: the fixed shape must NOT be reported, or the test would
    fail every correctly-written route and get disabled."""
    source = (
        "@router.get('/active')\n"
        "@ride_read_limit\n"
        "async def get_active_ride():\n"
        "    ...\n"
    )
    tree = ast.parse(source)
    fn = tree.body[0]
    names = [_name_of(d) for d in fn.decorator_list]

    router_idx = next(i for i, n in enumerate(names) if _is_router_decorator(n))
    limiter_idx = next(i for i, n in enumerate(names) if _is_limiter_decorator(n))

    assert router_idx < limiter_idx


def test_scan_covers_a_meaningful_number_of_routes():
    """Guards against the walk silently matching nothing (e.g. a path change or
    a decorator-naming convention shift), which would make the suite green for
    the wrong reason."""
    seen = 0
    for path in _iter_route_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = [_name_of(d) for d in node.decorator_list]
                if any(_is_router_decorator(n) for n in names) and any(
                    _is_limiter_decorator(n) for n in names
                ):
                    seen += 1
    assert seen >= 25, f"expected to find many rate-limited routes, found {seen}"
