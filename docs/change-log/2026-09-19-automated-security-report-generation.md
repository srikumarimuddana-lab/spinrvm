# Change Impact & Risk Log — Automated Security Report Generation (Phase 2)

**Date:** 2026-09-19
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** CI/CD (`.github/workflows/`), scripts
**Domain:** infra / security observability (no rides/payments/auth code touched)
**Related:** `docs/audit/2026-09-19-security-automation-roadmap.md` Phase 2, PR #5512 (Phase 1)

## Issue/gap identified
`security-gates.yml`'s "Security gates summary" job prints static boilerplate
text on every run — no actual finding counts, no per-tool breakdown. The
~60 hand-written docs under `docs/audit/` show no automated pipeline turns
scan output into a report; it's all manual.

## Root cause
Never built, and most gates don't even publish their JSON output as a
downloadable artifact — only `bandit-report` was uploaded before this change.

## Fix/remediation
1. `security-gates.yml`: added two `continue-on-error: true`, `if: always()`
   artifact-upload steps (`spinr-rules-report` from the semgrep job,
   `npm-audit-admin-report` from the npm-audit-admin job), matching the
   existing `bandit-report` pattern.
2. New `scripts/security/generate_security_summary.py`: parses the three
   JSON artifacts defensively (missing/malformed file → "not available",
   never a crash, never conflated with a clean scan), aggregates finding
   counts by severity, renders Markdown. Deliberately excludes free-text
   finding fields (bandit `issue_text`, semgrep `extra.message`) — both can
   echo a literal matched secret value for a hardcoded-credential finding,
   which must never be copied into a permanently-committed file.
3. New `.github/workflows/security-report.yml`: triggers on `workflow_run`
   after "Security Gates" completes, gated to only real push/schedule/
   workflow_dispatch runs of this repo's own `main` (explicitly excludes
   `pull_request`/`merge_group` and any run whose `head_repository` isn't
   this repo — closes a fork-PR-could-execute-with-write-access vector
   found during review), downloads the three artifacts, runs the generator,
   and opens a **draft PR** with the report under `docs/audit/`.

## Risk & impact on existing functionality
- **Blast radius: isolated to CI.** No application code (backend/rider-app/
  driver-app/admin-dashboard) touched.
- The two new artifact-upload steps in `security-gates.yml` are additive and
  `continue-on-error: true` — confirmed by `spinr-cicd-infra-reviewer` that
  they cannot flip an existing blocking gate (SR-03 money-safety check,
  npm-audit-admin's allowlist check) from pass to fail, since both existing
  checks exit before the new upload step runs.
- `security-report.yml` never blocks or gates `security-gates.yml` — it only
  reacts after that workflow completes, on any conclusion, and cannot affect
  its required-check status.
- Other consumers of the two artifact names (`spinr-rules-report`,
  `npm-audit-admin-report`): none yet — brand-new names, no collision with
  the existing `bandit-report` name.
- **Security review found and required fixing two real issues before merge**
  (see Verification below): a fork-PR trust gap (an attacker-controlled
  `head_sha` could otherwise have been checked out and executed with
  `contents: write`/`pull-requests: write` and a live token) and a
  secret-leakage gap (raw scanner finding text could have reached a
  permanently-committed file). Both closed; regression tests added for the
  second.

## User experience effect
None — no rider/driver/corporate-admin/internal-admin-facing change. Purely
an internal CI/reporting pipeline; its only visible output is a draft PR
containing a Markdown report, reviewed like any other PR before merging.

## Files modified
| File | What changed | Why |
|---|---|---|
| `.github/workflows/security-gates.yml` | Added 2 additive, non-blocking artifact-upload steps | Feed real scan data to the report generator |
| `scripts/security/generate_security_summary.py` | New file | Parse artifacts, render Markdown, redact secret-bearing free text |
| `scripts/security/test_generate_security_summary.py` | New file, 17 tests | Coverage incl. 3 explicit anti-leak regression tests |
| `.github/workflows/security-report.yml` | New file | Trigger, download, generate, open draft PR |

## Before/after snippet
Before: no artifact existed for semgrep/npm-audit-admin findings; the only
"summary" was static text with no real counts.
After (fork-trust gate, the security-critical piece of this change):
```bash
case "$TRIGGER_EVENT" in
  push|schedule|workflow_dispatch) TRUSTED_EVENT=true ;;
  *) TRUSTED_EVENT=false ;;
esac
if [ "$BRANCH" = "main" ] && [ "$TRUSTED_EVENT" = "true" ] && [ "$HEAD_REPO" = "${{ github.repository }}" ]; then
  echo "should_run=true" >> "$GITHUB_OUTPUT"
```

## Rollback plan
`git revert` is a complete rollback here — this is CI/tooling code with no
persisted data of its own. Deleting `security-report.yml` (or disabling it
via `enabled: false` in repo settings) immediately stops any further draft
PRs; the two additive artifact-upload steps in `security-gates.yml` can be
reverted independently with zero effect on any other job.

## Verification performed
- `python3 -c "import yaml; yaml.safe_load(...)"` on both modified/new
  workflow YAML files — both parse cleanly.
- `pytest scripts/security/test_generate_security_summary.py -v` — 17/17
  pass, including 3 tests that explicitly assert secret-bearing free text
  never survives into the rendered report.
- Manual CLI smoke test of `generate_security_summary.py` end-to-end against
  a synthetic bandit JSON file — confirmed correct Markdown output including
  the "no data available" vs. "clean scan" distinction for missing inputs.
- `spinr-cicd-infra-reviewer` agent run (round 1) against the initial diff:
  found 2 BLOCKERS (fork-PR trust gap, secret-leakage-into-permanent-docs
  gap) and 2 WARNINGS (unscoped `check-trigger` permissions,
  download-artifact/upload-artifact version-pairing question).
- Both fixes applied; `spinr-cicd-infra-reviewer` agent run (round 2)
  verifying the fixes specifically: **SAFE TO COMMIT**, both blockers
  confirmed closed, version-pairing warning accepted as documented risk
  (same SHA pair already used elsewhere in this repo for same-run
  downloads/uploads).

## What was NOT verified
- The workflow has **not** been exercised end-to-end in real GitHub Actions
  (no way to trigger a real `workflow_run` chain from this session) — the
  fork-trust gate logic was verified by careful reading and reviewer
  sign-off, not by an actual fork-PR test run. Recommend watching the first
  few real triggers after merge to confirm `should_run` evaluates as
  expected on a real nightly `schedule` run.
- `actions/download-artifact`'s cross-run (`run-id`/`github-token`) behavior
  at the exact pinned SHA used here was not independently verified against
  upstream release notes — accepted as a documented risk per the reviewer's
  round-2 sign-off, since the same SHA pair is already proven in same-run
  use elsewhere in this repo.
- No `npm run build` — this change touches no admin-dashboard/rider-app/
  driver-app frontend code.
