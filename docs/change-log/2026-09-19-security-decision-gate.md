# Change Impact & Risk Log — Security Decision Gate (Phase 5)

**Date:** 2026-09-19
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** scripts/ (new, standalone), CI/CD (new workflow)
**Domain:** security automation, decision governance
**Related:** `docs/audit/2026-09-19-security-automation-roadmap.md` Phase 5 (final phase)

## Issue/gap identified
Phase 4a built a tracker (`docs/security/tracked-advisories.json`) and
decision-request documents, but nothing surfaces an open decision where a
reviewer will actually see it, and nothing gives a clear go/no-go signal.

## Root cause
Never built — this is the final, closing piece of the 5-phase program.

## Fix/remediation
- `scripts/security/decision_gate.py` — reads the tracker, computes a
  GO/NO_GO verdict (fails closed: any entry missing a status, or still
  `needs_decision`, is NO_GO), renders a Markdown PR-comment summary
  linking each open decision-request doc.
- `.github/workflows/decision-gate.yml` — posts (or idempotently updates,
  via a marker comment) that summary as a PR comment whenever
  `tracked-advisories.json` changes. **PR-comment delivery only**, per
  explicit instruction — no Slack/email dispatch built. **Not** wired as a
  required, merge-blocking branch-protection check — that's a repo-settings
  change only a human with admin access can make (same class of step as
  the DAST/Sentry blockers this whole roadmap has repeated). The job does
  fail its own status (visible red X) on NO_GO for signal value, but that
  cannot block a merge without a human making it required.
- Real dry-run: ran the script against PR #5512's own actual tracker (the
  real `maplibre-gl` `needs_decision` entry from Phase 4a) and posted the
  real output as a PR comment — not synthetic demo data.

## Risk & impact on existing functionality
- **Blast radius: isolated.** New standalone script + new workflow, zero
  application-code changes. Only reacts to changes in one specific file
  (`docs/security/tracked-advisories.json`).
- `spinr-cicd-infra-reviewer` review: **SAFE TO MERGE**, no blockers.
  Explicitly re-verified the shell-injection lesson from the earlier
  Phase 2 fix was applied correctly from the start this time — confirmed
  no `${{ github.* }}` interpolation inside any `run:` block anywhere in
  this file (all such values go through `env:` first). Found two low-
  severity, non-blocking issues, both fixed:
  1. `_load_json_safe` silently treated a malformed/non-list tracker file
     as an empty (GO) tracker — contradicted the module's own fail-closed
     posture. Fixed to raise loudly instead, per CLAUDE.md's "don't
     silently swallow errors."
  2. The workflow's header comment claimed a fork PR "gracefully no-ops"
     on the comment-posting step; actually `gh api` hard-fails (403) with
     no `continue-on-error`, so a fork PR touching the tracked file gets a
     red X regardless of verdict. Corrected the comment to say so
     honestly rather than leave a misleading claim in the code.

## User experience effect
None — no rider/driver/corporate-admin-facing change. Internal-admin-
facing only in the sense that a PR reviewer now sees a decision-gate
comment on relevant PRs.

## Files modified
| File | What changed | Why |
|---|---|---|
| `scripts/security/decision_gate.py` | New file | Verdict logic + comment rendering |
| `scripts/security/test_decision_gate.py` | New file, 14 tests | Coverage incl. fail-closed regression tests |
| `.github/workflows/decision-gate.yml` | New file | Idempotent PR-comment posting |

## Before/after snippet
Before: no signal existed for a pending security decision.
After (the fail-closed fix that was the review's main finding):
```python
except (json.JSONDecodeError, OSError) as exc:
    raise ValueError(f"decision_gate: {path} exists but is not valid JSON -- "
                      f"refusing to silently treat this as an empty (GO) tracker: {exc}") from exc
```

## Rollback plan
`git revert` is a complete rollback. The workflow posts comments only — no
data mutation, no merge-blocking behavior to undo.

## Verification performed
- `pytest scripts/security/test_decision_gate.py -v` — 14/14 pass.
- Manual CLI dry-run against the real `docs/security/tracked-advisories.json`
  (not synthetic) — confirmed correct NO_GO verdict and correctly-linked
  decision-request doc.
- Posted the real rendered output as an actual PR comment on #5512,
  end-to-end proof this works against real data, not just tests.
- `spinr-cicd-infra-reviewer` agent run: **SAFE TO MERGE**. Specifically
  re-verified the shell-injection class of bug from the earlier Phase 2
  fix does not recur here. Two low-severity findings, both fixed.

## What was NOT verified
- The workflow itself has **not** been exercised in real GitHub Actions
  (no way to trigger a real `pull_request` event from this session) —
  verified by careful reading and reviewer sign-off, plus the underlying
  script's real dry-run, not an actual workflow run.
- Making this check "required" in branch protection was **not** done —
  that needs a human with repo admin access (Settings → Branches). Until
  then, a NO_GO verdict is visible but cannot block a merge.
- No `npm run build` — no frontend code touched.
