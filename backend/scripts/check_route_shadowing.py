#!/usr/bin/env python3
"""Assert no literal route is shadowed by an earlier parameterized route.

FastAPI/Starlette match routes in registration order, so a router that
registers ``GET /{driver_id}`` before ``GET /leaderboard`` silently swallows
the literal path. This check walks a routes package (or single module) in
registration order and fails if any literal path would be captured by an
earlier ``{param}`` route with the same HTTP method.

Usage:
    python scripts/check_route_shadowing.py routes/rides routes/drivers
"""

import ast
import re
import sys
from pathlib import Path


def _routes_of(path: Path, router_name: str):
    src = path.read_text()
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and isinstance(d.func.value, ast.Name)
                    and d.func.value.id == router_name
                    and d.func.attr in {"get", "post", "put", "patch", "delete"}
                ):
                    p = d.args[0].value if d.args else ""
                    yield (d.func.attr.upper(), p, node.name)


def registration_order(target: Path):
    if target.is_file():
        return list(_routes_of(target, "api_router"))
    # package: replicate the facade's include order
    init = (target / "__init__.py").read_text()
    m = re.search(r"for _sub in \(([^)]*)\)", init)
    if not m:
        raise SystemExit(f"{target}: cannot find include order in __init__.py")
    routes = []
    for mod in [s.strip() for s in m.group(1).split(",") if s.strip()]:
        routes.extend(_routes_of(target / f"{mod}.py", "router"))
    return routes


def main(targets):
    bad = 0
    for t in targets:
        routes = registration_order(Path(t))
        for i, (method, path, name) in enumerate(routes):
            if "{" in path:
                continue
            for m2, p2, n2 in routes[:i]:
                if m2 == method and "{" in p2 and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", p2), path):
                    print(f"{t}: {method} {path} ({name}) shadowed by {p2} ({n2})")
                    bad += 1
        print(f"{t}: {len(routes)} routes checked")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
