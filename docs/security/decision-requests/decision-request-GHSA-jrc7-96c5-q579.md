# Decision Request: CRITICAL dependency advisory in `admin-dashboard`

**Date drafted:** 2026-09-19
**Status:** DRAFT — needs a human decision before this is actionable
**Drafted by:** `scripts/security/threat_watch.py` (automated, not a security verdict)

## In plain language

A dependency scan found a **critical**-severity security advisory
in a package used by `admin-dashboard`. No decision has been recorded for
it yet in this repo's tracker — whether it's fixed, accepted as a risk, or
something else is genuinely unknown at this point, not "unfixable." This
document exists so a person can look at it, decide what to do, and record
that decision so this doesn't get silently missed on the next scan.

## Technical details

- **Advisory:** GHSA-jrc7-96c5-q579 (https://github.com/advisories/GHSA-jrc7-96c5-q579)
- **Package:** `maplibre-gl`
- **Dependency chain:** `maplibre-gl`
- **Ecosystem:** npm
- **Surface:** `admin-dashboard`
- **Severity (as reported by the audit tool):** critical

## What still needs research (not yet known)

- [ ] Is a patched version of `maplibre-gl` available? (`npm view maplibre-gl versions`, or check the advisory page above)
- [ ] Is the vulnerable code path actually reachable in the shipped `admin-dashboard` bundle, or only in a dev/build-time dependency?
- [ ] If a patch exists, does upgrading break anything? (run the affected build/lint/tests before pinning, per this repo's CLAUDE.md)
- [ ] If no patch exists, is there a safe workaround (e.g. isolating the vulnerable code path, replacing the dependency)?

## Options to consider (fill in after research above)

| Option | Effort | Risk if chosen | Notes |
|---|---|---|---|
| Upgrade `maplibre-gl` to a patched version | ? | Low, if tests pass | Preferred if a patch exists and doesn't break the build |
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
explicitly allowlisted. An unreviewed `critical` finding sitting
in a blocking gate risks someone eventually "fixing" the red CI by adding a
blanket allowlist entry without the research above — this document exists to
force the research to happen first.

## Files likely impacted (once a decision is made)

- `admin-dashboard/package.json` / lockfile (if upgrading or replacing)
- `scripts/security/check_npm_audit_allowlist.py` or `check_yarn_audit_allowlist.py` (if allowlisting)
- `docs/security/tracked-advisories.json` (always — record the decision here)

## Rollback plan

N/A until a fix is chosen — this document itself makes no code change.
