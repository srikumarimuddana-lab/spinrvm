# Change Impact & Risk Log — Threat Watch: Dependency-Advisory Decision Tracker (Phase 4a)

**Date:** 2026-09-19
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** scripts/ (new, standalone), docs/security/ (new)
**Domain:** security / dependency management
**Related:** `docs/audit/2026-09-19-security-automation-roadmap.md` Phase 4a, PR #5512's own CI (real `maplibre-gl` finding used as first dry-run)

## Issue/gap identified
`security-gates.yml`'s own "Security gates summary" job has said, since it
was written, to "file P1 items for any new HIGH/CRITICAL results in
`OPEN-ITEMS-TRACKER.md`" — that file has never existed in this repo. A real,
currently-unpatched **critical** `maplibre-gl` advisory (GHSA-jrc7-96c5-q579)
is failing CI right now (discovered while triaging PR #5512) with no
tracking record anywhere, risking a future "just widen the allowlist to
turn CI green" fix with no research trail.

## Root cause
Never built. The gap was aspirational in a code comment, not a real
mechanism.

## Fix/remediation
- `docs/security/tracked-advisories.json` — a structured record of every
  dependency advisory decision (status: `needs_decision`, `accepted_risk`,
  `fixed`, `wont_fix`; owner; reasoning).
- `scripts/security/threat_watch.py` — cross-references npm-audit output
  against the tracker (reusing `check_npm_audit_allowlist.py`'s dependency-
  chain walk, now renamed `leaf_advisories` — public, no longer
  underscore-prefixed — so the two scripts share one source of truth for
  "what a module's real advisory is" and can't silently disagree). For any
  untracked HIGH/CRITICAL finding, drafts a decision-request document
  containing only verifiable facts (module, severity, advisory URL,
  dependency chain) — explicitly with **no fabricated fix recommendation**,
  since the script cannot verify patch availability or production
  reachability on its own.
- Real dry-run performed against today's actual `maplibre-gl` CVE (not
  synthetic test data) — `docs/security/decision-requests/decision-request-
  GHSA-jrc7-96c5-q579.md` and the corresponding `tracked-advisories.json`
  entry are committed as genuine, currently-open tracking artifacts.

## Risk & impact on existing functionality
- **Blast radius:** the only change to an existing file is renaming
  `check_npm_audit_allowlist.py`'s private `_leaf_advisories` to public
  `leaf_advisories` (docstring + comment added explaining it's now a shared
  contract) — confirmed the existing allowlist enforcement still works
  identically (`python3 scripts/security/check_npm_audit_allowlist.py
  <same fixture>` still correctly exits 1 on the real maplibre-gl finding).
  No other file touched.
- Not wired into any CI workflow yet — deliberately scaffolding-first
  (matching the Phase 3 posture), given today's own lesson on this PR
  (a shell-injection bug shipped in a new workflow on the first pass) about
  rushing CI wiring without extra care.
- `spinr-security-auditor` review (see Verification) explicitly weighed
  whether committing a real, unpatched CRITICAL finding's existence to a
  permanent, potentially-public file is itself a disclosure risk — verdict:
  net-positive, since (a) the GHSA is already public, (b) the vulnerable
  lockfile is already in the repo (so `npm audit` already reproduces this
  for anyone with read access), (c) it's already visible in CI logs, and
  (d) an untracked finding risks a worse outcome (silent allowlist
  widening with no research trail). Conditional on the tracker being
  actively worked, not left to rot at `needs_decision` indefinitely.

## User experience effect
None — no rider/driver/corporate-admin/internal-admin-facing change.

## Files modified
| File | What changed | Why |
|---|---|---|
| `scripts/security/threat_watch.py` | New file | Watcher + decision-request generator |
| `scripts/security/test_threat_watch.py` | New file, 12 tests | Coverage incl. no-fabricated-recommendation regression test |
| `scripts/security/check_npm_audit_allowlist.py` | Renamed `_leaf_advisories` → `leaf_advisories`, added stability-contract docstring | Shared, stable helper between the two scripts |
| `docs/security/tracked-advisories.json` | New file, 1 real entry | Decision tracker |
| `docs/security/decision-requests/decision-request-GHSA-jrc7-96c5-q579.md` | New file | Real decision-request artifact |

## Before/after snippet
Before: `_leaf_advisories` (private, no stability contract) existed only in
`check_npm_audit_allowlist.py`.
After (now a documented, shared contract):
```python
def leaf_advisories(module: str, vulns: dict, seen: set[str]) -> list[tuple[str, str]]:
    """... Public (no leading underscore) and imported by scripts/security/
    threat_watch.py -- keep its name and signature stable, or update both
    call sites in the same change. ..."""
```

## Rollback plan
`git revert` is a complete rollback for the new files. For the rename in
`check_npm_audit_allowlist.py`, reverting restores the private name; nothing
else references it once `threat_watch.py` is also reverted.

## Verification performed
- `pytest scripts/security/test_threat_watch.py scripts/security/test_check_yarn_audit_allowlist.py -q` — 18/18 pass.
- Manual CLI dry-run against the real `maplibre-gl` finding (not synthetic):
  confirmed correct decision-request generation, correct dedup (re-running
  after adding the tracker entry reports it as "pending," not a duplicate),
  and confirmed `check_npm_audit_allowlist.py` still exits 1 identically
  after the rename.
- `spinr-security-auditor` agent run: **SAFE TO MERGE**. Checked (1) no
  field leakage beyond safe, already-public advisory metadata, (2) the
  disclosure-risk tradeoff of committing a real unpatched CVE record
  (judged net-positive, see above), (3) the "no fabricated recommendation"
  design holds up against the actual rendered document, (4) the
  `sys.path`-based import is safe (advisory ids are trusted CI-generated
  strings, and the output filename is sanitized) though coupling-fragile —
  addressed by making the helper a public, documented contract instead of
  a private one two files silently share.

## What was NOT verified
- `threat_watch.py` currently only watches npm-audit output. pip-audit and
  yarn-audit still don't publish a downloadable artifact at all (same gap
  Phase 2 already documented) — this tool can't watch what it can't read.
- No CI wiring — this is scaffolding, run manually today. Wiring it into a
  scheduled workflow is follow-up work, not done here.
- The `maplibre-gl` finding's actual research checklist (patch
  availability, production reachability) was **not** performed — that's
  explicitly left to a human, by design (see "Why a draft, not an
  automated verdict" in the script's docstring).
