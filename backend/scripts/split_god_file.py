#!/usr/bin/env python3
"""AST-driven splitter for Spinr god route files.

Splits a monolithic routes module into a package of domain submodules while
preserving:
  * runtime behaviour (pure code motion, no logic edits)
  * the dual-import pattern (relative import levels bumped by one)
  * test patchability (names patched wholesale in tests are accessed via
    module-attribute indirection: _deps.<name> / _shared.<name> / <mod>.<name>)
  * the external API surface (package __init__ re-exports every top-level name)
"""

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

# --------------------------------------------------------------------------- #
# scope analysis: is a Name at some position a module-global reference?
# --------------------------------------------------------------------------- #


def _collect_locals(node):
    """Names bound in a function scope (excluding nested function bodies)."""
    bound = set()
    args = node.args
    for a in (
        list(args.posonlyargs)
        + list(args.args)
        + list(args.kwonlyargs)
        + ([args.vararg] if args.vararg else [])
        + ([args.kwarg] if args.kwarg else [])
    ):
        bound.add(a.arg)

    forced_global = set()

    def _scan_stmt(st):
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(st.name)
            return  # don't descend into nested scopes
        if isinstance(st, ast.Global):
            forced_global.update(st.names)
            return
        if isinstance(st, ast.Nonlocal):
            forced_global.update(st.names)  # treat as non-local-to-this-scope
            return
        if isinstance(st, (ast.Import, ast.ImportFrom)):
            for al in st.names:
                bound.add((al.asname or al.name).split(".")[0])
            return
        # generic: find Name stores + exception names, skipping nested scopes
        for child in ast.iter_child_nodes(st):
            _scan_expr_or_stmt(child)
        if isinstance(st, ast.ExceptHandler) and st.name:
            bound.add(st.name)

    def _scan_expr_or_stmt(n):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if hasattr(n, "name"):
                bound.add(n.name)
            return
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return  # comprehension targets live in their own scope
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
            bound.add(n.target.id)
        if isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                bound.add((al.asname or al.name).split(".")[0])
        for child in ast.iter_child_nodes(n):
            _scan_expr_or_stmt(child)

    for st in node.body:
        _scan_stmt(st)
    return bound - forced_global


class GlobalRefCollector(ast.NodeVisitor):
    """Collect Name nodes that resolve to module globals (with positions)."""

    def __init__(self):
        self.scope_stack = []  # list of sets of local names (function scopes only)
        self.refs = []  # (name, lineno, col, end_col)

    def _is_local(self, name):
        return any(name in s for s in self.scope_stack)

    def visit_FunctionDef(self, node):
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_func(node)

    def _visit_func(self, node):
        for d in node.decorator_list:
            self.visit(d)
        for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
            self.visit(default)
        for a in (
            list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
            + ([node.args.vararg] if node.args.vararg else [])
            + ([node.args.kwarg] if node.args.kwarg else [])
        ):
            if a.annotation:
                self.visit(a.annotation)
        if node.returns:
            self.visit(node.returns)
        self.scope_stack.append(_collect_locals(node))
        for st in node.body:
            self.visit(st)
        self.scope_stack.pop()

    def visit_Lambda(self, node):
        for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
            self.visit(default)
        locs = set()
        a = node.args
        for arg in (
            list(a.posonlyargs)
            + list(a.args)
            + list(a.kwonlyargs)
            + ([a.vararg] if a.vararg else [])
            + ([a.kwarg] if a.kwarg else [])
        ):
            locs.add(arg.arg)
        self.scope_stack.append(locs)
        self.visit(node.body)
        self.scope_stack.pop()

    def _visit_comp(self, node):
        locs = set()
        for gen in node.generators:
            for n in ast.walk(gen.target):
                if isinstance(n, ast.Name):
                    locs.add(n.id)
        # first iterable evaluated in enclosing scope
        self.visit(node.generators[0].iter)
        self.scope_stack.append(locs)
        for i, gen in enumerate(node.generators):
            if i > 0:
                self.visit(gen.iter)
            for cond in gen.ifs:
                self.visit(cond)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.scope_stack.pop()

    visit_ListComp = _visit_comp
    visit_SetComp = _visit_comp
    visit_GeneratorExp = _visit_comp
    visit_DictComp = _visit_comp

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and not self._is_local(node.id):
            self.refs.append((node.id, node.lineno, node.col_offset, node.end_col_offset))


# --------------------------------------------------------------------------- #
# splitter
# --------------------------------------------------------------------------- #


def is_import_block(node):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return True
    if isinstance(node, ast.Try):
        stmts = list(node.body)
        for h in node.handlers:
            stmts.extend(h.body)
        stmts.extend(node.orelse)
        stmts.extend(node.finalbody)
        return all(isinstance(s, (ast.Import, ast.ImportFrom)) for s in stmts)
    return False


def assigned_names(node):
    names = []
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                names.append(t.id)
            else:
                raise SystemExit(f"non-Name assign target at line {node.lineno}")
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def import_bound_names(node):
    out = []
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        for al in node.names:
            out.append((al.asname or al.name).split(".")[0])
    elif isinstance(node, ast.Try):
        for st in node.body:  # try-branch defines the canonical names
            out.extend(import_bound_names(st))
        for h in node.handlers:
            for st in h.body:
                out.extend(import_bound_names(st))
    return out


def split(cfg):
    source_path = Path(cfg["source"])
    src = source_path.read_text()
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    module_map = dict(cfg["module_map"])
    patched = set(cfg["patched_names"])
    pkg = cfg["package_name"]

    # ---- classify top-level nodes -------------------------------------- #
    node_module = []  # (node, module, start_line, end_line) 1-based inclusive
    name_owner = {}
    errors = []
    prev_end = 0
    for node in tree.body:
        if is_import_block(node):
            mod = "_deps"
            for n in import_bound_names(node):
                name_owner.setdefault(n, "_deps")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name not in module_map:
                errors.append(f"unmapped def: {node.name} (line {node.lineno})")
                mod = "_shared"
            else:
                mod = module_map[node.name]
            if node.name in name_owner and name_owner[node.name] != mod:
                errors.append(f"owner collision: {node.name}")
            name_owner[node.name] = mod
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = assigned_names(node)
            mods = {module_map.get(n, "_shared") for n in names}
            if len(mods) != 1:
                errors.append(f"assign split across modules at line {node.lineno}")
            mod = mods.pop()
            for n in names:
                if n in name_owner and name_owner[n] != mod:
                    errors.append(f"owner collision: {n} ({name_owner[n]} vs {mod})")
                name_owner[n] = mod
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            mod = "_deps"  # module docstring
        else:
            errors.append(f"unhandled top-level {type(node).__name__} at line {node.lineno}")
            mod = "_shared"
        start = prev_end + 1  # attach gap (comments/banners) to following node
        end = node.end_lineno
        node_module.append((node, mod, start, end))
        prev_end = end
    trailing = lines[prev_end:]  # anything after last node

    if "api_router" in name_owner:
        name_owner["api_router"] = "__init__"

    if errors:
        for e in errors:
            print("ERROR:", e)
        raise SystemExit(1)

    # ---- per-line rewrite plan ------------------------------------------ #
    # global refs across the whole file
    collector = GlobalRefCollector()
    for node, mod, _s, _e in node_module:
        if mod == "_deps":
            continue
        collector.visit(node)
    # map line -> owning module
    line_owner = {}
    for _node, mod, s, e in node_module:
        for ln in range(s, e + 1):
            line_owner[ln] = mod

    edits = defaultdict(list)  # lineno -> [(col, end_col, replacement)]
    needs = defaultdict(lambda: {"attr_mods": set(), "deps_from": set(), "shared_from": set()})
    unresolved = defaultdict(set)

    for name, ln, col, end_col in collector.refs:
        mod = line_owner.get(ln)
        if mod is None or mod == "_deps":
            continue
        owner = name_owner.get(name)
        if owner is None or owner == mod:
            continue
        if name == "api_router":
            edits[ln].append((col, end_col, "router"))
            needs[mod]["router"] = True
            continue
        if owner == "__init__":
            raise SystemExit(f"reference to __init__-owned {name} from {mod}")
        if name in patched or owner not in ("_deps", "_shared"):
            edits[ln].append((col, end_col, f"{owner}.{name}"))
            needs[mod]["attr_mods"].add(owner)
        elif owner == "_deps":
            needs[mod]["deps_from"].add(name)
        elif owner == "_shared":
            needs[mod]["shared_from"].add(name)

    if any(unresolved.values()):
        print("unresolved:", dict(unresolved))

    # _shared must not depend on submodules (import-order safety)
    bad = [m for m in needs.get("_shared", {"attr_mods": set()})["attr_mods"] if m not in ("_deps",)]
    if bad:
        raise SystemExit(f"_shared references submodules {bad}; adjust module_map")

    # ---- relative import level bump ------------------------------------- #
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.level and n.level >= 1:
            ln = n.lineno
            text = lines[ln - 1]
            m = re.search(r"(\bfrom\s+)(\.+)", text)
            if not m:
                raise SystemExit(f"cannot bump relative import at line {ln}: {text!r}")
            edits[ln].append((m.start(2), m.end(2), m.group(2) + "."))

    # ---- apply edits ------------------------------------------------------ #
    new_lines = list(lines)
    for ln, es in edits.items():
        text = new_lines[ln - 1]
        for col, end_col, repl in sorted(es, key=lambda x: -x[0]):
            text = text[:col] + repl + text[end_col:]
        new_lines[ln - 1] = text

    # ---- assemble chunks --------------------------------------------------- #
    chunks = defaultdict(list)
    module_names = defaultdict(list)  # module -> ordered defined names
    has_routes = defaultdict(bool)
    for node, mod, s, e in node_module:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_names[mod].append(node.name)
            for d in node.decorator_list:
                base = d
                while isinstance(base, ast.Call):
                    base = base.func
                if (
                    isinstance(base, ast.Attribute)
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "api_router"
                ):
                    has_routes[mod] = True
        else:
            for n in assigned_names(node) if isinstance(node, (ast.Assign, ast.AnnAssign)) else []:
                if n != "api_router":
                    module_names[mod].append(n)
        if isinstance(node, (ast.Assign,)) and "api_router" in assigned_names(node):
            continue  # api_router is recreated in __init__
        chunks[mod].append("".join(new_lines[s - 1 : e]))
    if trailing and "".join(trailing).strip():
        chunks["_shared"].append("".join(trailing))

    # ---- write files -------------------------------------------------------- #
    out_dir = Path(cfg["package_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    origin = source_path.name

    def header(mod, purpose):
        text = (
            f'"""{purpose}\n\n'
            f"Split from ``backend/routes/{origin}`` (god-file refactor). Pure code\n"
            f"motion — no behaviour changes. See docs/refactors/god-file-split.md.\n"
            f'"""\n\n'
        )
        if mod in ("_deps", "__init__"):
            # facade modules re-export by design; F401 is the point
            text += "# ruff: noqa: F401\n\n"
        return text

    written = {}

    deps_text = header("_deps", cfg["deps_doc"]) + "".join(chunks["_deps"])
    written["_deps"] = deps_text

    def import_block(mod):
        need = needs[mod]
        out = []
        attr_mods = sorted(need["attr_mods"])
        deps_from = set(need["deps_from"])
        if need.get("router"):
            deps_from.add("APIRouter")
        if attr_mods:
            out.append("from . import " + ", ".join(attr_mods) + "\n")
        if deps_from:
            out.append(
                "from ._deps import (  # noqa: F401\n" + "".join(f"    {n},\n" for n in sorted(deps_from)) + ")\n"
            )
        if need["shared_from"]:
            out.append(
                "from ._shared import (  # noqa: F401\n"
                + "".join(f"    {n},\n" for n in sorted(need["shared_from"]))
                + ")\n"
            )
        if need.get("router"):
            out.append("\nrouter = APIRouter()\n")
        return "".join(out) + "\n"

    shared_text = header("_shared", cfg["shared_doc"]) + import_block("_shared") + "".join(chunks["_shared"])
    written["_shared"] = shared_text

    for mod in cfg["module_order"]:
        if mod not in chunks:
            raise SystemExit(f"module {mod} has no content")
        written[mod] = header(mod, cfg["module_docs"][mod]) + import_block(mod) + "".join(chunks[mod])

    leftover = set(chunks) - set(written) - {"_deps", "_shared"}
    if leftover:
        raise SystemExit(f"modules missing from module_order: {leftover}")

    # __init__
    init = [header("__init__", cfg["init_doc"])]  # APIRouter arrives via _deps re-export

    def reexport(mod, names):
        if not names:
            return ""
        return f"from .{mod} import (  # noqa: F401\n" + "".join(f"    {n},\n" for n in names) + ")\n"

    deps_names = [n for n, o in name_owner.items() if o == "_deps"]
    init.append(reexport("_deps", deps_names))
    init.append(reexport("_shared", [n for n in module_names["_shared"]]))
    init.append("\nfrom . import " + ", ".join(cfg["module_order"]) + "  # noqa: E402\n\n")
    for mod in cfg["module_order"]:
        init.append(reexport(mod, module_names[mod]))
    init.append(f"\napi_router = APIRouter(tags={cfg['router_tags']!r})\n")
    routed = [m for m in cfg["module_order"] if has_routes[m]]
    init.append("for _sub in (" + ", ".join(routed) + "):\n")
    init.append(f'    api_router.include_router(_sub.router, prefix="{cfg["router_prefix"]}")\n')
    all_names = deps_names + [n for m in ["_shared"] + cfg["module_order"] for n in module_names[m]]
    dupes = {n for n in all_names if all_names.count(n) > 1}
    if dupes:
        raise SystemExit(f"duplicate top-level names: {dupes}")
    init.append("\n__all__ = [\n" + "".join(f'    "{n}",\n' for n in ["api_router"] + sorted(all_names)) + "]\n")
    written["__init__"] = "".join(init)

    for mod, text in written.items():
        (out_dir / f"{mod}.py").write_text(text)
    # report
    print(f"package {pkg}: wrote {len(written)} files")
    for mod in ["_deps", "_shared"] + cfg["module_order"] + ["__init__"]:
        n = written[mod].count("\n")
        print(f"  {mod:16s} {n:5d} lines  routes={'y' if has_routes.get(mod) else '-'}")
    return name_owner


if __name__ == "__main__":
    import importlib.util

    spec = importlib.util.spec_from_file_location("cfg", sys.argv[1])
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    split(m.CONFIG)
