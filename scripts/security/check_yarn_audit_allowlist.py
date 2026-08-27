"""
Fail on HIGH/CRITICAL yarn-audit findings, except a small, explicitly scoped
allowlist (CR #4548).

`image-size` — pulled in transitively via
expo -> @expo/cli -> @expo/metro -> metro -> metro-config, Metro's own
dev-tooling, not code shipped in the built app bundle -- has 2 HIGH
advisories with no patched version at all (`patched_versions: "<0.0.0"`,
true of every published image-size release including latest, so no
dependency bump can fix this today). The allowlist below excludes only
these two specific GHSA IDs on the `image-size` module; any other
HIGH/CRITICAL finding -- on image-size or anything else -- still fails
the gate.

Re-audit periodically and drop this allowlist once Metro ships a real fix
upstream. See CR #4548 for the full writeup.

Usage: python3 check_yarn_audit_allowlist.py <path-to-yarn-audit-json>
Reads `yarn audit --json` output (one JSON object per line) and exits 1 if
any non-allowlisted HIGH/CRITICAL advisory is present.
"""
from __future__ import annotations

import json
import sys

ALLOWLISTED_GHSA = {"GHSA-w3rx-r6r6-pgpr", "GHSA-5p2g-fcmc-qvqq"}
ALLOWLISTED_MODULE = "image-size"
BLOCKING_SEVERITIES = {"high", "critical"}


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_yarn_audit_allowlist.py <yarn-audit.json>", file=sys.stderr)
        return 2

    blocking: list[tuple[str, str, str]] = []
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "auditAdvisory":
                continue
            adv = obj["data"]["advisory"]
            severity = adv.get("severity", "")
            if severity not in BLOCKING_SEVERITIES:
                continue
            url = adv.get("url") or ""
            module = adv.get("module_name", "")
            if module == ALLOWLISTED_MODULE and any(g in url for g in ALLOWLISTED_GHSA):
                print(f"Allowlisted (CR #4548, no patch exists): {module} [{severity}] {url}")
                continue
            blocking.append((module, severity, url))

    if blocking:
        print("Blocking HIGH/CRITICAL advisories (not covered by the CR #4548 allowlist):")
        for module, severity, url in blocking:
            print(f"  - {module} [{severity}] {url}")
        return 1

    print("No blocking HIGH/CRITICAL advisories after the CR #4548 allowlist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
