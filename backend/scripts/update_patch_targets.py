#!/usr/bin/env python3
"""Rewrite test patch targets after a god-file split.

`patch("backend.routes.rides.<name>...")` becomes
`patch("backend.routes.rides.<owner>.<name>...")` where <owner> is the
submodule that now holds <name> (_deps for external imports, _shared for
shared helpers). Names whose owner is unknown are reported, not touched.
"""

import ast
import re
import sys
from pathlib import Path


def build_owner_map(pkg_dir, cfg_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("cfg", cfg_path)
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    owner = {n: m for n, m in cfg.CONFIG["module_map"].items()}
    # _deps: names bound by its import statements
    deps_src = (Path(pkg_dir) / "_deps.py").read_text()
    for node in ast.walk(ast.parse(deps_src)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                owner.setdefault((al.asname or al.name).split(".")[0], "_deps")
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    owner.setdefault(t.id, "_deps")
    return owner


def main(pkg, pkg_dir, cfg_path, tests_dir):
    owner = build_owner_map(pkg_dir, cfg_path)
    submods = {f.stem for f in Path(pkg_dir).glob("*.py")}
    rx = re.compile(rf"\b(backend\.)?routes\.{pkg}\.([A-Za-z_][A-Za-z0-9_]*)")
    unknown, changed = {}, 0

    def repl(m):
        nonlocal changed
        name = m.group(2)
        if name in submods:  # already rewritten / direct submodule reference
            return m.group(0)
        own = owner.get(name)
        if own is None or own == "__init__":
            if own is None:
                unknown.setdefault(name, 0)
                unknown[name] += 1
            return m.group(0)
        changed += 1
        return f"{m.group(1) or ''}routes.{pkg}.{own}.{name}"

    for f in sorted(Path(tests_dir).rglob("*.py")):
        src = f.read_text()
        new = rx.sub(repl, src)
        if new != src:
            f.write_text(new)
            print(f"updated {f}")
    print(f"\n{changed} references rewritten")
    if unknown:
        print("UNKNOWN names (left untouched):", unknown)


if __name__ == "__main__":
    main(*sys.argv[1:5])
