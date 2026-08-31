"""
Fail on HIGH/CRITICAL npm-audit findings, except a small, explicitly scoped
allowlist — the same `image-size` advisory already allowlisted for the mobile
yarn-audit gate (CR #4548, see check_yarn_audit_allowlist.py), now also
reached via admin-dashboard's Storybook devDependency.

`image-size` is a direct dependency of both `@storybook/nextjs-vite` and
`@storybook/nextjs` (Storybook's Next.js framework integration, used for
local component-dev/build only — never part of the built `admin-dashboard`
app bundle Vercel serves) and carries 2 HIGH advisories with no patched
version at all (`patched_versions: "<0.0.0"` on every published image-size
release, so no dependency bump can fix this today).

npm's audit report reflects this up the whole dependency chain: `image-size`
itself carries the real advisory objects, but every ancestor that pulls it in
(`vite-plugin-storybook-nextjs`, `@storybook/nextjs-vite`, ...) is *also*
reported as its own HIGH-severity "vulnerability", whose `via` list is just
the child package's *name* (a string, not an advisory object) rather than a
duplicate of the advisory. This walks each entry's `via` chain down to real
advisories (recursively, since an ancestor's `via` string points at another
key in the same report) and only allowlists an entry when EVERY advisory
reachable from it is the allowlisted `image-size` one. Any entry that reaches
even one non-allowlisted advisory still fails the gate.

Re-audit periodically and drop this allowlist once Storybook ships a real
fix upstream (tracked alongside CR #4548, same root cause).

Usage: python3 check_npm_audit_allowlist.py <path-to-npm-audit.json>
Reads a single `npm audit --json` object (not line-delimited, unlike yarn's
format) and exits 1 if any non-allowlisted HIGH/CRITICAL advisory is present.
"""
from __future__ import annotations

import json
import sys

ALLOWLISTED_GHSA = {"GHSA-w3rx-r6r6-pgpr", "GHSA-5p2g-fcmc-qvqq"}
ALLOWLISTED_MODULE = "image-size"
BLOCKING_SEVERITIES = {"high", "critical"}


def _leaf_advisories(module: str, vulns: dict, seen: set[str]) -> list[tuple[str, str]]:
    """Return every (module, url) advisory reachable from `module`'s `via` chain."""
    if module in seen:
        return []
    seen.add(module)

    vuln = vulns.get(module)
    if vuln is None:
        return [(module, "(unknown — not in this report)")]

    found: list[tuple[str, str]] = []
    for via in vuln.get("via", []):
        if isinstance(via, dict):
            found.append((module, via.get("url") or via.get("title", "")))
        else:
            # A dependency-name string — resolve it to its own entry.
            found.extend(_leaf_advisories(via, vulns, seen))
    return found or [(module, "(no advisory detail)")]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_npm_audit_allowlist.py <npm-audit.json>", file=sys.stderr)
        return 2

    with open(sys.argv[1]) as f:
        data = json.load(f)

    vulns = data.get("vulnerabilities", {})
    blocking: list[tuple[str, str, str]] = []

    for module, vuln in vulns.items():
        severity = vuln.get("severity", "")
        if severity not in BLOCKING_SEVERITIES:
            continue

        leaves = _leaf_advisories(module, vulns, set())
        non_allowlisted = [
            (leaf_module, url)
            for leaf_module, url in leaves
            if not (leaf_module == ALLOWLISTED_MODULE and any(g in url for g in ALLOWLISTED_GHSA))
        ]
        if non_allowlisted:
            for leaf_module, url in non_allowlisted:
                blocking.append((f"{module} -> {leaf_module}" if leaf_module != module else module, severity, url))
        else:
            print(f"Allowlisted (no patch exists, see script docstring): {module} [{severity}] via {leaves}")

    if blocking:
        print("Blocking HIGH/CRITICAL advisories (not covered by the allowlist):")
        for module, severity, url in blocking:
            print(f"  - {module} [{severity}] {url}")
        return 1

    print("No blocking HIGH/CRITICAL advisories after the allowlist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
