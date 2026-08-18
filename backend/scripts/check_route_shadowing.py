#!/usr/bin/env python3
"""Assert no literal route is shadowed by an earlier route with the same
method — either a parameterized route (``/{id}``) or an exact duplicate
literal registered by a *different* router mounted at the same parent.

FastAPI/Starlette match routes in registration order, so two kinds of
mistake are both silent at import time and only visible by reading the
route table:

1. A router that registers ``GET /{driver_id}`` before ``GET /leaderboard``
   swallows the literal path.
2. Two *separate* routers both registering ``GET /faqs`` and both mounted
   into the same parent router (e.g. ``v1_api_router``) — the
   later-registered one is permanently dead code. This is the bug class
   that let ``backend/routes/faqs.py`` sit shadowed by
   ``backend/features.py``'s ``get_faqs`` for months (see
   docs/change-log/2026-08-18-retire-duplicate-faqs-route.md) — case 1's
   check alone does not catch it, since neither path contains ``{param}``.

Two usage modes:

    # Original: check literal-vs-param shadowing within one routes package
    # or module's own registration order.
    python scripts/check_route_shadowing.py routes/rides routes/drivers

    # New: check every router server.py mounts into v1_api_router/app,
    # grouped by (parent router variable, mount-time prefix) — the same
    # grouping FastAPI itself uses to build the final route table. Catches
    # both shadow classes across router boundaries, not just within one
    # router's own file.
    python scripts/check_route_shadowing.py --server-mounts server.py

Known limitation of --server-mounts: routers imported from a *package*
(a directory with its own __init__.py, e.g. ``routes.admin`` — a directory,
not a single module) are skipped, not silently passed as clean — each skip
is printed. Resolving a package's own internal aggregation order correctly
would need to recurse into its __init__.py the same way `registration_order`
already does for the single-package CLI mode above; the router variables
server.py imports directly from packages (currently only `admin_router` and
`admin_auth_router`, both from `routes.admin`) are few enough to review by
hand, and admin routes carry their own dedicated auth-gating review surface
(see `spinr-admin-rbac-reviewer`) — so this is a deliberate scope line, not
an oversight.
"""

import ast
import re
import sys
from pathlib import Path

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


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
                    and d.func.attr in _HTTP_METHODS
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


def _shadow_violations(routes):
    """routes: list of (method, path, name, source_label). Yields
    (kind, i, j) index pairs for a later literal route (i) shadowed by an
    earlier route (j) — either an earlier {param} route matching the same
    literal, or an earlier route with the exact same literal path."""
    for i, (method, path, _name, _src) in enumerate(routes):
        if "{" in path:
            continue
        for j in range(i):
            m2, p2, _n2, _s2 = routes[j]
            if m2 != method:
                continue
            if p2 == path:
                yield ("duplicate", i, j)
                break
            if "{" in p2 and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", p2), path):
                yield ("param-shadow", i, j)
                break


def main(targets):
    if targets and targets[0] == "--server-mounts":
        server_path = Path(targets[1]) if len(targets) > 1 else Path("server.py")
        return check_server_mounts(server_path)

    bad = 0
    for t in targets:
        routes = registration_order(Path(t))
        labeled = [(m, p, n, t) for m, p, n in routes]
        for kind, i, j in _shadow_violations(labeled):
            m, p, n, _ = labeled[i]
            m2, p2, n2, _ = labeled[j]
            if kind == "param-shadow":
                print(f"{t}: {m} {p} ({n}) shadowed by {p2} ({n2})")
            else:
                print(f"{t}: {m} {p} ({n}) is a duplicate of {p2} ({n2})")
            bad += 1
        print(f"{t}: {len(routes)} routes checked")
    return 1 if bad else 0


# --------------------------------------------------------------------------- #
# --server-mounts mode: walk server.py's own include_router() call graph
# --------------------------------------------------------------------------- #


def _parse_imports(server_src: str) -> dict:
    """local_alias -> (dotted_module, attr_name_in_that_module), for every
    top-level `from X import Y [as Z]` in server.py."""
    imports = {}
    for node in ast.parse(server_src).body:
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                local = alias.asname or alias.name
                imports[local] = (node.module, alias.name)
    return imports


def _parse_include_router_calls(server_src: str):
    """(parent_var, router_local_alias, prefix_or_None, resolvable) in
    source order, for every top-level `PARENT.include_router(ROUTER, ...)`
    statement. resolvable is False when the prefix kwarg is present but not
    a literal string (so grouping can't be trusted) — such calls are
    dropped from the check rather than mis-grouped."""
    calls = []
    for node in ast.parse(server_src).body:
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        if not (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "include_router"
            and isinstance(call.func.value, ast.Name)
        ):
            continue
        parent_var = call.func.value.id
        if not call.args or not isinstance(call.args[0], ast.Name):
            continue
        router_alias = call.args[0].id
        prefix = ""
        resolvable = True
        for kw in call.keywords:
            if kw.arg == "prefix":
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    prefix = kw.value.value
                else:
                    resolvable = False
        calls.append((parent_var, router_alias, prefix, resolvable))
    return calls


def _router_own_prefix(module_src: str, router_name: str) -> str:
    """The `prefix="..."` a router was itself constructed with, e.g.
    `documents_router = APIRouter(prefix="/drivers", ...)`. Empty string if
    the router has no own prefix or it isn't a literal."""
    for node in ast.parse(module_src).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == router_name
            and isinstance(node.value, ast.Call)
        ):
            for kw in node.value.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value
    return ""


def _resolve_module_file(dotted_module: str, project_root: Path):
    """dotted_module like 'routes.rides' -> project_root/routes/rides.py.
    Returns None if that's a package (directory) rather than a single
    module — see the --server-mounts limitation documented in the module
    docstring."""
    candidate = project_root.joinpath(*dotted_module.split(".")).with_suffix(".py")
    if candidate.is_file():
        return candidate
    return None


def find_server_mount_violations(server_path: Path):
    """Returns (violations, skipped, group_sizes).

    violations: list of dicts — {kind, parent, prefix, method, path, name,
    source, shadowed_by_name, shadowed_by_source} — one per shadow/duplicate
    finding, in the same order the CLI prints them.
    skipped: list of human-readable notes for router aliases that could not
    be resolved (packages, dynamic prefixes) — see the module docstring's
    documented --server-mounts limitation.
    group_sizes: dict of {"parent (prefix=...)": route_count} for the CLI's
    "N routes checked" line.
    """
    project_root = server_path.parent
    src = server_path.read_text()
    imports = _parse_imports(src)
    calls = _parse_include_router_calls(src)

    groups: dict = {}  # (parent_var, prefix) -> list of (method, path, name, source_label)
    skipped = []
    for parent_var, router_alias, prefix, resolvable in calls:
        if not resolvable:
            skipped.append(f"{router_alias}: dynamic (non-literal) prefix, cannot group safely")
            continue
        if router_alias not in imports:
            # Locally-defined router (e.g. v1_api_router itself is an
            # APIRouter() built in server.py, never imported) — nothing to
            # resolve routes from at this call site.
            continue
        dotted_module, attr_name = imports[router_alias]
        module_file = _resolve_module_file(dotted_module, project_root)
        if module_file is None:
            skipped.append(f"{router_alias}: from package '{dotted_module}' (directory) — not walked, see docstring")
            continue
        module_src = module_file.read_text()
        own_prefix = _router_own_prefix(module_src, attr_name)
        source_label = f"{module_file.relative_to(project_root)}::{attr_name}"
        for method, path, name in _routes_of(module_file, attr_name):
            effective_path = (own_prefix + path) if own_prefix else path
            groups.setdefault((parent_var, prefix), []).append((method, effective_path, name, source_label))

    violations = []
    group_sizes = {}
    for (parent_var, prefix), routes in sorted(groups.items()):
        group_label = f"{parent_var} (prefix={prefix!r})"
        for kind, i, j in _shadow_violations(routes):
            m, p, n, src_i = routes[i]
            _m2, p2, n2, src_j = routes[j]
            violations.append(
                {
                    "kind": kind,
                    "group": group_label,
                    "method": m,
                    "path": p,
                    "name": n,
                    "source": src_i,
                    "shadowed_by_path": p2,
                    "shadowed_by_name": n2,
                    "shadowed_by_source": src_j,
                }
            )
        group_sizes[group_label] = len(routes)

    return violations, skipped, group_sizes


def check_server_mounts(server_path: Path) -> int:
    violations, skipped, group_sizes = find_server_mount_violations(server_path)

    for v in violations:
        if v["kind"] == "param-shadow":
            print(
                f"{v['group']}: {v['method']} {v['path']} ({v['name']} in {v['source']}) "
                f"shadowed by {v['shadowed_by_path']} ({v['shadowed_by_name']} in {v['shadowed_by_source']})"
            )
        else:
            print(
                f"{v['group']}: {v['method']} {v['path']} ({v['name']} in {v['source']}) is a "
                f"DUPLICATE of {v['shadowed_by_path']} ({v['shadowed_by_name']} in {v['shadowed_by_source']}) "
                "— the second registration is dead code, never reached"
            )
    for group_label, count in group_sizes.items():
        print(f"{group_label}: {count} routes checked")
    for note in skipped:
        print(f"skipped: {note}")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
