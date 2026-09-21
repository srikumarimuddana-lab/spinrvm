"""
Cross-reference dependency-audit findings (currently: npm audit for
admin-dashboard) against a tracked-decisions file, and draft a
decision-request document for any HIGH/CRITICAL finding nobody has made a
call on yet.

Phase 4a of the 2026-09-19 security automation roadmap
(docs/audit/2026-09-19-security-automation-roadmap.md) -- the CVE/
dependency-advisory watcher half of "threat hunting." Formalizes what
security-gates.yml's own "Security gates summary" job has said to do since
it was written ("file P1 items for any new HIGH/CRITICAL results in
OPEN-ITEMS-TRACKER.md") but never actually does -- that file does not exist
in this repo (confirmed 2026-09-19). This script and
docs/security/tracked-advisories.json are that mechanism, made real.

Why a draft, not an automated verdict: this script can tell you a finding
exists and whether anyone has already made a call on it. It CANNOT tell you
whether a patched version exists, whether the vulnerable code path is
actually reachable in production, or what the right fix is -- those need a
human (or a dedicated research pass) to verify. Fabricating that judgment
here would be exactly the "soft-handling as a default" root CLAUDE.md
warns against for security findings. Every drafted decision-request
explicitly says what still needs research, and DECISION_REQUIRED status
tracked-advisories entries are never treated as "handled" by this script.

Tracked-advisories.json schema (list of objects):
    {
      "advisory_id": str,       # a GHSA id, or "(unknown)" if npm didn't supply one
      "module": str,            # the vulnerable package name
      "ecosystem": "npm" | "yarn" | "pip",
      "surface": str,           # e.g. "admin-dashboard"
      "severity": "low" | "moderate" | "high" | "critical",
      "status": "needs_decision" | "accepted_risk" | "fixed" | "wont_fix",
      "first_seen": "YYYY-MM-DD",
      "decision_owner": str | None,
      "reasoning": str          # required once status != "needs_decision"
    }

Usage:
    python3 threat_watch.py \\
        --npm-audit /tmp/npm-audit-admin.json \\
        --surface admin-dashboard \\
        --tracked-advisories docs/security/tracked-advisories.json \\
        --output-dir /tmp/decision-requests
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_npm_audit_allowlist import leaf_advisories  # noqa: E402

BLOCKING_SEVERITIES = {"high", "critical"}


def _load_json_safe(path: Optional[str], default: Any) -> Any:
    if not path:
        return default
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def find_new_npm_findings(
    npm_audit_data: Optional[dict[str, Any]],
    tracked: list[dict[str, Any]],
    surface: str,
) -> list[dict[str, Any]]:
    """Walk npm-audit's vulnerabilities, resolve each HIGH/CRITICAL module to
    its real leaf advisories (reusing check_npm_audit_allowlist's own walk,
    so the two scripts can never disagree about what a module's "real"
    advisory is), and return the ones with no matching tracked-advisories
    entry for this surface. A finding already tracked with ANY status
    (needs_decision, accepted_risk, fixed, wont_fix) is not "new" -- it's
    already on someone's radar; only status counts as unhandled, and that's
    reported separately (see find_pending_decisions), not duplicated here.
    """
    if not npm_audit_data:
        return []
    vulns = npm_audit_data.get("vulnerabilities") or {}
    tracked_ids = {(t.get("advisory_id"), t.get("surface")) for t in tracked}

    new_findings = []
    seen_advisory_ids: set[str] = set()
    for module, vuln in vulns.items():
        severity = str(vuln.get("severity", "")).lower()
        if severity not in BLOCKING_SEVERITIES:
            continue
        for leaf_module, url in leaf_advisories(module, vulns, set()):
            advisory_id = _extract_ghsa_id(url) or "(unknown)"
            key = (advisory_id, surface)
            if key in tracked_ids or advisory_id in seen_advisory_ids:
                continue
            seen_advisory_ids.add(advisory_id)
            new_findings.append(
                {
                    "advisory_id": advisory_id,
                    "module": leaf_module,
                    "reached_via": module if module != leaf_module else None,
                    "ecosystem": "npm",
                    "surface": surface,
                    "severity": severity,
                    "url": url,
                }
            )
    return new_findings


def find_pending_decisions(tracked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries already in the tracker but still awaiting a human call."""
    return [t for t in tracked if t.get("status") == "needs_decision"]


def _extract_ghsa_id(url: str) -> Optional[str]:
    if not isinstance(url, str):
        return None
    marker = "advisories/"
    idx = url.find(marker)
    if idx == -1:
        return None
    return url[idx + len(marker):].strip("/") or None


def render_decision_request(finding: dict[str, Any]) -> str:
    """Draft a decision-request document. Every fact here is something the
    script can actually verify from the audit JSON (module, severity,
    advisory id/url, dependency chain) -- it never fabricates a fix
    recommendation or a "no patch exists" claim without that having been
    researched. See the module docstring's "Why a draft, not an automated
    verdict" section.
    """
    today = date.today().isoformat()
    dep_chain = f"`{finding['reached_via']}` → `{finding['module']}`" if finding.get("reached_via") else f"`{finding['module']}`"

    return f"""# Decision Request: {finding['severity'].upper()} dependency advisory in `{finding['surface']}`

**Date drafted:** {today}
**Status:** DRAFT — needs a human decision before this is actionable
**Drafted by:** `scripts/security/threat_watch.py` (automated, not a security verdict)

## In plain language

A dependency scan found a **{finding['severity']}**-severity security advisory
in a package used by `{finding['surface']}`. No decision has been recorded for
it yet in this repo's tracker — whether it's fixed, accepted as a risk, or
something else is genuinely unknown at this point, not "unfixable." This
document exists so a person can look at it, decide what to do, and record
that decision so this doesn't get silently missed on the next scan.

## Technical details

- **Advisory:** {finding['advisory_id']} ({finding['url'] or '(no URL provided by the audit tool)'})
- **Package:** `{finding['module']}`
- **Dependency chain:** {dep_chain}
- **Ecosystem:** {finding['ecosystem']}
- **Surface:** `{finding['surface']}`
- **Severity (as reported by the audit tool):** {finding['severity']}

## What still needs research (not yet known)

- [ ] Is a patched version of `{finding['module']}` available? (`npm view {finding['module']} versions`, or check the advisory page above)
- [ ] Is the vulnerable code path actually reachable in the shipped `{finding['surface']}` bundle, or only in a dev/build-time dependency?
- [ ] If a patch exists, does upgrading break anything? (run the affected build/lint/tests before pinning, per this repo's CLAUDE.md)
- [ ] If no patch exists, is there a safe workaround (e.g. isolating the vulnerable code path, replacing the dependency)?

## Options to consider (fill in after research above)

| Option | Effort | Risk if chosen | Notes |
|---|---|---|---|
| Upgrade `{finding['module']}` to a patched version | ? | Low, if tests pass | Preferred if a patch exists and doesn't break the build |
| Allowlist as accepted risk (like the existing `image-size` entry in `scripts/security/check_npm_audit_allowlist.py`) | Low | Advisory stays live in the dependency tree | Only appropriate if no patch exists AND the vulnerable path is confirmed unreachable in production |
| Replace the dependency | High | Regression risk across every consumer | Last resort |

## Recommendation

**No default recommendation is given here** — this script can confirm the
advisory exists and is untracked, but cannot verify patch availability or
production reachability on its own. Whoever picks this up should complete
the research checklist above, then record the decision in
`docs/security/tracked-advisories.json` (status: `fixed`, `accepted_risk`,
or `wont_fix`, with `reasoning` filled in) so this same finding doesn't
generate a duplicate decision request on the next scan.

## Risk & impact if left undecided

This advisory will keep showing up in every `security-gates.yml` run
(`G4c`/`G4b` — npm/yarn audit) as a blocking failure until either fixed or
explicitly allowlisted. An unreviewed `{finding['severity']}` finding sitting
in a blocking gate risks someone eventually "fixing" the red CI by adding a
blanket allowlist entry without the research above — this document exists to
force the research to happen first.

## Files likely impacted (once a decision is made)

- `{finding['surface']}/package.json` / lockfile (if upgrading or replacing)
- `scripts/security/check_npm_audit_allowlist.py` or `check_yarn_audit_allowlist.py` (if allowlisting)
- `docs/security/tracked-advisories.json` (always — record the decision here)

## Rollback plan

N/A until a fix is chosen — this document itself makes no code change.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch dependency-audit output for untracked HIGH/CRITICAL advisories")
    parser.add_argument("--npm-audit", help="Path to npm-audit-admin.json")
    parser.add_argument("--surface", default="admin-dashboard", help="Which surface this audit JSON is for")
    parser.add_argument("--tracked-advisories", required=True, help="Path to tracked-advisories.json")
    parser.add_argument("--output-dir", required=True, help="Directory to write one decision-request .md per new finding")
    args = parser.parse_args()

    npm_audit_data = _load_json_safe(args.npm_audit, None)
    tracked = _load_json_safe(args.tracked_advisories, [])
    if not isinstance(tracked, list):
        print(f"WARNING: {args.tracked_advisories} is not a JSON list, treating as empty", file=sys.stderr)
        tracked = []

    new_findings = find_new_npm_findings(npm_audit_data, tracked, args.surface)
    pending = find_pending_decisions(tracked)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for finding in new_findings:
        doc = render_decision_request(finding)
        out_path = out_dir / f"decision-request-{finding['advisory_id'].replace('/', '_')}.md"
        out_path.write_text(doc)
        print(f"New untracked {finding['severity'].upper()} finding: {finding['advisory_id']} ({finding['module']}) -> {out_path}")

    if pending:
        print(f"\n{len(pending)} already-tracked finding(s) still awaiting a decision:")
        for p in pending:
            print(f"  - {p.get('advisory_id')} ({p.get('module')}) in {p.get('surface')}, first seen {p.get('first_seen')}")

    if not new_findings and not pending:
        print("No new or pending untracked advisories.")


if __name__ == "__main__":
    main()
