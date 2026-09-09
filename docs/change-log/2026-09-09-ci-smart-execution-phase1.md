# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code session |
| Surface(s) | backend (CI/CD config only — 9 `.github/workflows/*.yml` files); `CLAUDE.md` (doc-only) |
| Domain (Sentry tag) | admin (closest fit — deploy-pipeline/CI change, not application code) |
| PR / commit link | (filled in on push) |
| Related issue or gap ID | User request: "why did all CI fail after going private, and design a risk-based smart-execution strategy" — findings folded into `ACTION_ITEMS.md` C93–C96 context, prior `docs/audit/2026-08-27-cicd-gates-guardrails-audit.md` |

## 1. Issue / gap identified

Two related asks: (a) diagnose why every CI check failed after the repo went private, and
(b) reduce Actions cost/turnaround by running only the CI gates a given PR's diff actually
needs. (a) was root-caused entirely by reading existing `ACTION_ITEMS.md` C93–C96 evidence
and live workflow-run data — no code changes were the fix for that (see the published audit
artifact for the full diagnosis: three of the four items are already fixed or mitigated;
C96, an Actions spend-limit/concurrency-cap hit, needs a human with GitHub billing access,
not code). This entry covers (b) — the zero-risk, additive execution-strategy items the user
approved implementing now — plus one stale-doc fix a fresh gap-audit surfaced along the way.

Specifically: 8 of the repo's PR-triggered workflows had no `concurrency:` group, so a stale
run from an earlier push kept running (and billing Actions minutes) instead of being
superseded by the latest commit's run — the same class of waste `ci.yml` itself was already
patched for today as a direct C96 mitigation (see that file's own comment, added by a
different session earlier today). Separately, `ci.yml` — which carries the genuinely
expensive full-matrix jobs (4x Playwright E2E, Docker build + Trivy image scan) — had no
`schedule:` trigger, so those jobs' only "everything, unfiltered" run was per-PR/per-push,
with no periodic safety-net run independent of PR volume the way `security-gates.yml`
already has (`schedule: cron: '0 2 * * *'`).

## 2. Root cause

- **Concurrency gap**: these 8 workflows were written independently over time; `cancel-in-progress`
  was added to the repo's heavier workflows (`ci.yml`, `ci-guardrails.yml`, `security-gates.yml`,
  `pr-checks.yml`, `migration-check.yml`, `maestro-e2e.yml`, `label-run-maestro.yml`,
  `claude-review.yml`, `dast-zap-baseline.yml`) as each was tuned for cost over time, but never
  swept across the remaining lighter ones. Confirmed via a fresh `spinr-cicd-infra-reviewer`
  pass (2026-09-09) that grepped every workflow file for a `concurrency:` block.
- **No nightly consolidation on `ci.yml`**: `security-gates.yml` picked up a daily schedule at
  some point; `ci.yml` never did. Not a defect exactly — just an inconsistency between two
  workflows that should follow the same pattern given they're both "heavy, full-matrix, PR +
  push + merge_group" workflows.
- **CLAUDE.md:381 doc drift**: said corporate coverage is "not yet enforced by a
  `--cov-fail-under` gate" — true as of the 2026-08-27 audit, but the 2026-08-27 audit's own
  Phase 3 recommendation (add that gate) has since shipped (`corporate-coverage-floor-gate` in
  `ci-guardrails.yml`, confirmed present at ~line 445 by the same fresh audit pass). The doc
  was never updated after the gate landed.

## 3. Fix / remediation

1. Added `concurrency: { group: <workflow-slug>-${{ github.ref }}, cancel-in-progress: true }`
   to: `claude-audit.yml`, `dependabot-auto-merge.yml`, `mobile-bundle-smoke.yml`,
   `mobile-dep-check.yml`, `pip-compile-check.yml`, `sync-mobile-lockfiles.yml`,
   `sync-pip-lockfile.yml`, `test-env.yml`. Unconditional (not gated to `pull_request` the way
   `ci.yml`'s is) because none of these 8 has a push-triggered deploy step to protect from
   mid-flight cancellation — verified by reading each file's full job list before editing.
2. Added `schedule: - cron: '0 8 * * *'` (08:00 UTC = 2:00 AM Saskatchewan CST — no DST there,
   so this doesn't drift seasonally) to `ci.yml`'s existing `on:` block, mirroring
   `security-gates.yml`'s already-proven pattern. `schedule` is never the `pull_request` event,
   so every `detect-changes`-gated job in `ci.yml` runs unfiltered on it automatically, the same
   way `push`/`merge_group` already do — no other job logic needed to change.
   - **Considered and rejected**: a separate new workflow file that fires `ci.yml`'s
     `workflow_dispatch` trigger via the GitHub API/CLI on a schedule (would have kept `ci.yml`
     itself completely untouched). Rejected after confirming the default `GITHUB_TOKEN` does not
     reliably create a new workflow run when used to call another workflow's `workflow_dispatch`
     endpoint from within a running workflow — this is GitHub's documented recursive-run
     prevention guard, and would have made that approach a silent no-op (the API call succeeds,
     no run is created) rather than a working nightly trigger. A working, in-repo-proven pattern
     beats a novel one with an unverified failure mode.
3. Fixed `CLAUDE.md`'s stale "not yet enforced" line for corporate coverage (see Root cause).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to trigger/concurrency config on 9 workflow files, plus one doc
  line.** No job's internal steps, no application code, no runtime behavior, no data path is
  touched.
- **What else reads/writes the same jobs?** Each of the 8 concurrency additions was scoped
  after reading that file's complete `on:`/`jobs:` structure — none has a second workflow
  `uses:` it (`workflow_call`), so no other file's behavior changes. `ci.yml`'s schedule
  addition doesn't touch its `detect-changes` job's own logic or any downstream job's `if:`
  condition — those already treat every non-`pull_request` event as "run everything."
- **Could this regress a working flow?**
  - Concurrency additions: the only behavior change is that a superseded run on the same ref
    now gets cancelled instead of running to completion. For `sync-mobile-lockfiles.yml` /
    `sync-pip-lockfile.yml` (which commit a lockfile fix back to the PR branch), a cancelled
    run mid-push is not a partial-state risk — each step either completes atomically or the
    whole job is killed before it starts the next one; a superseding run picks up the latest
    commit and does the same work again if still needed.
  - `ci.yml`'s new `schedule` trigger: additive only. A scheduled run shares `ci.yml`'s
    existing concurrency group (`ci-${{ github.ref }}`, which resolves to `ci-refs/heads/main`
    for a schedule event) with any concurrent push-triggered run on `main` — but since neither
    a scheduled run (`cancel-in-progress: false`, `event_name != 'pull_request'`) nor a
    push-triggered run cancels the other, they queue rather than clobber each other, consistent
    with `ci.yml`'s existing "never cancel a push-triggered run" intent (see that file's own
    C96-mitigation comment).
  - New recurring cost: one extra full `ci.yml` run per day (~16 jobs) against `main`. This is
    the intended trade — trading a small, fixed, predictable nightly cost for not needing to
    run the full E2E/Docker matrix on every single PR — but it is a real, non-zero addition to
    monthly Actions-minute consumption, worth naming given C96 (a spend-limit/minutes-cap
    hit) is the reason this whole audit started.
- **Interaction with background loops / ride state machine / money paths:** none — CI/CD
  config and one doc line only.

## 5. User-experience effect

None — internal CI/CD pipeline and internal documentation only. No rider/driver/admin-facing
surface. No change to what any PR author sees on their own PR's checks today (the nightly run
is a separate, `main`-targeted run, not a PR check).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/claude-audit.yml` | Added `concurrency:` block | Cancel superseded runs |
| `.github/workflows/dependabot-auto-merge.yml` | Added `concurrency:` block | Cancel superseded runs |
| `.github/workflows/mobile-bundle-smoke.yml` | Added `concurrency:` block | Cancel superseded runs |
| `.github/workflows/mobile-dep-check.yml` | Added `concurrency:` block | Cancel superseded runs |
| `.github/workflows/pip-compile-check.yml` | Added `concurrency:` block | Cancel superseded runs |
| `.github/workflows/sync-mobile-lockfiles.yml` | Added `concurrency:` block | Cancel superseded runs |
| `.github/workflows/sync-pip-lockfile.yml` | Added `concurrency:` block | Cancel superseded runs |
| `.github/workflows/test-env.yml` | Added `concurrency:` block | Cancel superseded runs |
| `.github/workflows/ci.yml` | Added `schedule: cron: '0 8 * * *'` to existing `on:` block | Nightly consolidated safety-net run for the full-matrix suites, independent of PR volume |
| `CLAUDE.md` | Corrected stale "not yet enforced" corporate-coverage line | Doc drift — the gate has existed since the 2026-08-27 audit's Phase 3 |

## 7. Before / after

```yaml
# Before (each of the 8 files, e.g. mobile-dep-check.yml)
on:
  pull_request:
    paths: [...]
  push:
    branches: [main]
    paths: [...]

env:
  NODE_VERSION: '22'
```

```yaml
# After
on:
  pull_request:
    paths: [...]
  push:
    branches: [main]
    paths: [...]

concurrency:
  group: mobile-dep-check-${{ github.ref }}
  cancel-in-progress: true

env:
  NODE_VERSION: '22'
```

```yaml
# Before (ci.yml on: block, tail)
  workflow_dispatch:

# After
  workflow_dispatch:
  schedule:
    - cron: '0 8 * * *'
```

## 8. Rollback plan

`git revert`-safe for all 10 files, no live data or in-flight workflow run entanglement.

- Reverting the 8 `concurrency:` additions restores the prior (no-cancellation) behavior —
  strictly more Actions-minute usage, not unsafe.
- Reverting `ci.yml`'s `schedule:` addition simply stops the nightly run from firing again;
  it does not affect any already-completed run or any PR-time check.
- Reverting the `CLAUDE.md` line restores the stale text — a documentation-accuracy
  regression, not a functional one.
- No feature flag needed: every change here is either a pure trigger addition or a
  scheduling config, not a behavior toggle a live user could be mid-session on.

## 8a. Caught by proactive review before push, fixed same session

Ran `spinr-cicd-infra-reviewer` against this exact diff before pushing (per its own "use
proactively on any `.github/workflows` change" mandate). It found two real issues, both fixed
before push — recorded here rather than silently folded into the sections above, since this
is exactly the "escalate, don't silently ship" + "verify before shipping" discipline this repo
asks for, and worth being visible that it worked:

- **BLOCKER (fixed, commit after b3ad045):** `test-env.yml`'s new `cancel-in-progress: true`
  was unconditional, but this workflow's `push`-triggered path runs `eas build`/`eas update`
  against Expo — non-idempotent external state a mid-flight cancellation could orphan or race.
  Rescoped to `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`, matching
  `ci.yml`'s own conditional pattern in this same diff.
- **WARNING (fixed, commit after 3e5d3ff):** `ci.yml`'s 4 GHCR-push/cosign-sign steps gate only
  on `github.ref == 'refs/heads/main'`, which is also true for the new nightly `schedule` run —
  so every night was about to re-push and re-sign an unchanged image with no new commit behind
  it. Added `&& github.event_name != 'schedule'` to all 4; the Trivy scan itself (the actual
  nightly value) is unaffected.

Everything else the reviewer checked (deploy-admin's `workflow_dispatch` gate, mobile-build's
`[build]`-tag gate, `sync-mobile-lockfiles.yml`/`sync-pip-lockfile.yml`/`dependabot-auto-merge.yml`'s
idempotency under cancellation) came back confirmed-safe, consistent with Section 4's reasoning
above.

## 9. Verification performed

- [x] `yaml.safe_load` against all 9 edited workflow files post-edit — all parse clean.
- [x] Read each of the 8 concurrency-target files' complete `on:`/`permissions:`/`jobs:`
      structure before editing, to confirm none has a push-triggered deploy step that
      unconditional cancellation could interrupt mid-flight (the reason `ci.yml`'s own
      concurrency block is conditional on `pull_request`, unlike these 8).
  Do not treat "cancel-in-progress: true" here as identical to
      `migration-check.yml`'s by coincidence — it was reasoned about per-file, not copy-pasted.
- [x] Re-read `ci.yml`'s current `on:` block immediately before editing (not relying on an
      earlier read) to check for a same-day collision, since this file's own comment shows it
      was already edited today by a separate session for a C96 mitigation — confirmed unchanged
      between reads, safe to apply.
- [x] Confirmed via a fresh `spinr-cicd-infra-reviewer` subagent pass (2026-09-09, read-only)
      that `corporate-coverage-floor-gate` is real and blocking before rewriting the CLAUDE.md
      line, rather than trusting the original audit's Phase-3 recommendation as proof it shipped.
- [ ] **No real GitHub Actions run observed for any of these 9 files before this PR's own
      push** — not possible from this session; this PR's own CI run (and, for the schedule
      trigger specifically, its first 08:00 UTC firing) is the real-world test.
- [ ] `actionlint` — **not available in this session** (Docker CLI present but no daemon
      reachable at `/var/run/docker.sock`) — same disclosed gap as the
      `2026-09-08-c93-c94-ci-token-permissions.md` entry. Only generic YAML parsing was
      performed, which cannot catch GitHub-Actions-schema-specific errors.

## What was NOT verified

- **Whether the nightly `ci.yml` run's Actions-minute cost is acceptable given C96** — this
  entry adds a real, recurring cost at the exact moment the account's spend limit is under
  question. The audit artifact's P0 item (confirm/raise the spend limit) is a prerequisite
  this session cannot check itself (no billing-dashboard access) — flagging explicitly rather
  than assuming headroom exists.
- **Whether `github.ref` for a `schedule` event actually resolves to `refs/heads/main`** as
  reasoned in Section 4 — this is GitHub's documented behavior (scheduled workflows run
  against the repository's default branch), not independently confirmed against a live run in
  this session.
- **The two governance/security items surfaced by the same audit are deliberately NOT touched
  here**, per explicit user direction: the `main` required-status-checks audit / auto-merge
  policy decision (needs GitHub Settings access no session has), and the leaked Supabase
  `service_role` key rotation (held for a separate, focused session given its production
  blast radius).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data entanglement)
- [x] Blast radius is stated, not assumed (each file read in full before editing; `ci.yml`
      re-read fresh immediately before its edit to catch a possible same-day collision)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
      (none — Section 5 states plainly there is none)
