# Change Impact & Risk Log — retarget CI branch filters from `develop` to `staging`

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | mkkreddy52@gmail.com (via Claude Code) |
| Surface(s) | CI/CD only — no application code touched |
| Domain (Sentry tag) | n/a (pipeline config, not runtime code) |
| PR / commit link | direct commit to `staging` |
| Related issue or gap ID | `ACTION_ITEMS.md` E1 (staging environment) |

## 1. Issue / gap identified

The `staging` branch was created on 2026-08-28, but only three workflows fire on
a push to it: `test-env.yml` (Staging CI/CD), `deploy-backend-staging.yml`, and
the repo-wide-broken `maestro-e2e.yml`. Every real gate — the CI test matrix,
security gates, guard rails, and the mobile checks — was filtered to
`branches: [main, develop]` or `[main]` and so never ran against staging.

The most serious consequence: **code could land on `staging` with no security
gate at all**, and staging's only backend signal was `test-env.yml`'s
`backend-check`, which carries `continue-on-error: true` and therefore cannot
report failure.

## 2. Root cause

`develop` **was never created** in this repository. `test-env.yml` was
originally pointed at it and had never run once; it was retargeted to `staging`
earlier on 2026-08-28. That retarget covered only `test-env.yml` — the other
six workflows carrying a `[main, develop]` filter were left untouched, so the
filter has always silently resolved to "main only." `security-gates.yml` is a
separate case: it was never `develop`-aware at all, only `[main]`.

## 3. Fix / remediation

Replaced the dead `develop` entry with `staging` in six workflows, and added
`staging` alongside `main` in `security-gates.yml` (an addition, not a swap,
since it had no `develop` entry to replace). Also corrected one line of
guard-rails PR comment copy that told reviewers the gates run on
`main`/`develop`.

Deliberately **not** changed in this commit (see §11):

- `mobile-dep-check.yml`'s `push: [main]` trigger — its `pull_request` filter
  now covers staging; the push-side check stays main-only.
- `pip-compile-check.yml`'s `push: [main]` — its `pull_request` trigger has no
  branch filter and already fires on staging PRs.
- `test-env.yml`'s `continue-on-error: true` on `backend-check`.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to CI/CD triggers. No application code, no database,
no runtime behavior.** Nothing in `backend/`, `rider-app/`, `driver-app/`,
`admin-dashboard/`, or `shared/` is touched, so no ride state, money path,
insurance-period row, or background loop is affected.

Every changed line only *widens* which refs a workflow observes; no existing
`main` trigger was removed or narrowed. `main`'s gating is byte-for-byte
unchanged.

Blast-radius check performed on the newly-reachable jobs — every side-effecting
job in the widened workflows was read to confirm it stays gated away from a
staging push:

| Job | Gate | Fires on staging push? |
|---|---|---|
| `ci.yml` → `deploy-admin` (Vercel) | `github.event_name == 'workflow_dispatch'` | No |
| `ci.yml` → `mobile-build` (EAS build) | `github.ref == 'refs/heads/main' && [build]` | No |
| `ci.yml` → `smoke-test` (post-deploy) | `github.ref == 'refs/heads/main'` | No |
| `ci.yml` → `visual-regression-test` | `main` push or any PR | PR only; self-skips, no baselines (B38) |
| `ci.yml` → `notify-failure` (Slack) | `failure()` | **Yes — see below** |
| `claude-audit.yml` → PR comment | `github.event_name == 'pull_request'` | No |
| `ci-guardrails.yml` → PR comment | PR-triggered only | PR only (intended) |
| `sync-mobile-lockfiles.yml` → `git push` | pushes to `github.head_ref` | Writes to the PR's *head* branch, never to `staging` |
| `security-gates.yml` | no write steps found | n/a |

**One genuine new behavior:** `ci.yml`'s `notify-failure` job posts to
`SLACK_WEBHOOK` on any CI failure, with the branch-agnostic text
`CI/CD Pipeline Failed`. A red staging build will now page Slack with a message
indistinguishable from a production `main` failure. This is arguably desirable
(staging breakage should be visible) but the copy is now ambiguous. Flagged as
follow-up, not fixed here, to keep this diff to one logical change.

**Redundancy, not a regression:** `test-env.yml` already runs backend pytest and
both apps' `tsc --noEmit` on staging pushes. `ci.yml` now runs its own fuller
matrix on the same push. Staging CI wall-clock roughly doubles, and two backend
signals appear per push — one that can fail (`ci.yml`) and one that cannot
(`test-env.yml`). `ci.yml` should be treated as the authoritative one.
Consolidating them is follow-up work.

## 5. User-experience effect

**Nobody rider-, driver-, corporate-admin-, or internal-admin-facing.** This is
pipeline configuration; no shipped screen, copy, notification, or API response
changes. Nothing is visible mid-session to a rider mid-ride or a driver online.

The only human-visible change is for contributors: PRs based on `staging` now
receive guard-rail and security-gate check runs (and the guard-rails summary
comment) that previously did not appear.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | `push` + `pull_request` branches `[main, develop]` → `[main, staging]` | Brings the real test matrix onto the staging lane |
| `.github/workflows/security-gates.yml` | `push` + `pull_request` branches `[main]` → `[main, staging]` | Closes the "no security gate on staging" gap |
| `.github/workflows/ci-guardrails.yml` | `pull_request` branches `[main, develop]` → `[main, staging]`; PR comment copy `main`/`develop` → `main`/`staging` | Guard rails on staging PRs; comment text was made wrong by this change |
| `.github/workflows/mobile-bundle-smoke.yml` | `pull_request` branches → `[main, staging]` | Staging is the branch publishing OTA updates; bundle smoke matters most here |
| `.github/workflows/mobile-dep-check.yml` | `pull_request` branches → `[main, staging]` (push left `[main]`) | Dependency health on staging PRs |
| `.github/workflows/sync-mobile-lockfiles.yml` | `pull_request` branches → `[main, staging]` | Lockfile drift caught before it reaches staging |
| `.github/workflows/claude-audit.yml` | `push` branches → `[main, staging]` | `CLAUDE.md` / `.claude/**` audit on staging pushes |

## 7. Before / after

```yaml
# Before — ci.yml (and 5 others). `develop` has never existed in this repo,
# so this filter has always resolved to "main only".
on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]
```

```yaml
# After
on:
  push:
    branches: [main, staging]
  pull_request:
    branches: [main, staging]
```

```yaml
# Before — security-gates.yml (no `develop` entry to swap; staging is an addition)
  pull_request:
    branches: [main]
  push:
    branches: [main]
```

```yaml
# After
  pull_request:
    branches: [main, staging]
  push:
    branches: [main, staging]
```

## 8. Rollback plan

`git revert` is a complete and sufficient rollback here, which is normally not
acceptable — but it applies cleanly in this case because **the change touches no
live data whatsoever**. There are no Stripe charges, wallet deltas, ride-state
rows, or insurance-period records in scope; nothing is "already applied" that a
revert could leave inconsistent.

Reverting restores the exact prior `main`-only gating on the next push. No
redeploy of any service is required, because no service is deployed by this
change. If only the Slack noise is unwanted, the narrower fix is to add
`&& github.ref == 'refs/heads/main'` to `ci.yml`'s `notify-failure` job rather
than reverting the whole commit.

## 9. Verification performed

- [x] **All 32 workflow files parsed** with `yaml.safe_load` — 0 broken, before
      and after the edit.
- [x] **Resolved triggers asserted post-edit**, not just grepped. Confirmed:
      `ci.yml` push+PR `['main','staging']`; `security-gates.yml` push+PR
      `['main','staging']`; `ci-guardrails.yml` PR `['main','staging']`;
      `mobile-bundle-smoke.yml` PR `['main','staging']`;
      `mobile-dep-check.yml` PR `['main','staging']` / push `['main']`;
      `sync-mobile-lockfiles.yml` PR `['main','staging']`;
      `claude-audit.yml` push `['main','staging']`.
- [x] **Blast-radius grep performed.** Searched all seven changed workflows for
      `vercel`, `eas `, `npm publish`, `flyctl`, `railway`, `git push`,
      `createComment`, `issues.create`, `peter-evans`, `create-pull-request`,
      `gh pr`, `gh issue`, `slack`, `curl -X POST`, `--prod`. Every hit is
      tabulated in §4 with its gate.
- [x] **Residual `develop` sweep** across `.github/workflows/` — the four
      remaining hits are prose/comments that correctly describe the history, plus
      the one comment string fixed in this commit.
- [x] Reviewed against `CLAUDE.md` — "Pre-merge release gates" (blast radius
      first, additive over destructive, rollback before merge) and the
      surgical-changes rule.
- [ ] Feature flag — n/a. Workflow triggers cannot be flagged; the branch filter
      *is* the gate.

## 10. What was NOT verified

- **No workflow was executed.** Trigger correctness is proven by parsing the
  resolved YAML, not by observing a real GitHub Actions run. The first push to
  `staging` after this commit is the real test.
- **Whether `ci.yml`'s full matrix actually passes on `staging` is unknown.**
  Staging currently sits at the same SHA as `main`, and `main`'s own recent CI
  history is not clean (commit `6e2ed95` on this branch was itself a fix for 15
  backend test failures introduced on `main` by #4662). It is entirely possible
  the first `ci.yml` run on staging goes red — that would be the gate working,
  not this change failing.
- **Slack behavior not observed.** `notify-failure` firing on a staging failure
  is inferred from reading its `if: failure()` gate; no test webhook was fired.
- **Runner-cost impact not measured.** The roughly-doubled staging CI time is an
  estimate from job counts and the observed ~17-minute `test-env.yml` run, not a
  billing measurement.
- No automated visual/snapshot regression tooling exists for any surface here,
  but that is moot — this change renders nothing.

## 11. Follow-ups (not in this commit)

1. Remove `continue-on-error: true` from `test-env.yml`'s `backend-check`, or
   narrow it to `pytest -m unit` (needs no secrets). Until then that job's green
   is meaningless — `ci-error-audit.yml` blind spot M4.
2. Consolidate the now-duplicated backend/tsc jobs between `test-env.yml` and
   `ci.yml`.
3. Add branch context to `ci.yml`'s `notify-failure` Slack text, or gate it to
   `main`.
4. Fix `ci-error-audit.yml`'s `workflow_run` listeners: it watches
   `Deploy Backend` and `EAS Mobile (Build + Update)`, neither of which is a real
   workflow name (`Deploy Backend to Railway`, `Deploy Backend to Fly.io`,
   `EAS Mobile Update`), and it does not watch
   `Deploy Backend to Fly.io (Staging)` at all.
5. `maestro-e2e.yml` is an invalid workflow file failing instantly on every push
   repo-wide (2,234+ runs, `main` included). Pre-existing; unrelated to staging.
6. Add branch protection to `staging` once these checks have a green baseline.

## 12. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; justified in §8)
- [x] Blast radius is stated, not assumed (§4 table, per side-effecting job)
- [x] No silent behavior change to an already-shipped flow — the one real new
      behavior (Slack on staging failure) is called out in §4 and §11
