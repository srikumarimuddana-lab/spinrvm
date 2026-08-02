# CI Gate Health Audit — 2026-08

**Date:** 2026-08-02 · **Trigger:** PR #3037 (2026-08-01) — a single markdown-only PR
went red on 5 checks (`backend-test`, `driver-app-test`, `G6 · Trivy container scan`,
`G4b · yarn audit (shared)`, `G4c · npm audit (admin-dashboard)`), none caused by the
diff, while all 7 `ci-guardrails.yml` gates passed. When most non-guardrail gates are
red by default, red stops carrying information.

**Scope:** every job across all 22 workflow files in `.github/workflows/` that
produces a check run. Blocking/advisory read from the literal `continue-on-error`
value (job and step level) in each file — never inferred from job names or
comments. Live status pulled from actual GitHub Actions runs on `main`
(`push` events), not from any PR branch, since `pull_request` runs test the
merge ref, not the branch head (see #2861/C8 lesson: "CI disagrees with my
local run" is not evidence of a CI defect until both are known to test the
same tree).

**This is an audit.** No underlying test/build failures were fixed here.
Two exceptions, both "documentation of the gate's own contract": corrected a
stale header comment in `security-gates.yml` that mis-described the
non-blocking gates, and reopened-then-reclosed
[#3048](https://github.com/srikumarimuddana-lab/spinrvm/issues/3048) with
fresh evidence after a commit that landed *during this audit* fixed the
G6/`docker-image-scan` Trivy findings it tracked.

> **Correction (added after original publication):** this doc originally
> called the #3048/CR-2026-002 msgpack/setuptools findings a "Trivy false
> positive." That's wrong, per #3048's own later comment thread
> (2026-08-02T18:48Z, after a diagnostic step landed in #3113 and pip was
> removed from the runtime image in #3246): the flagged versions **were
> genuinely present** in the built image, inside `pip`'s own vendored
> dependency tree (`pip/_vendor/vendor.txt` + `pip/_vendor/bom.cdx.json`,
> both listing `msgpack==1.1.2`/`setuptools==70.3.0` — exactly what Trivy
> reported). Trivy reached them via that third-party SBOM instead of its own
> live filesystem scan, which is what looked like a contradiction, but the
> versions really were there. `skip-files` alone (what this doc originally
> described as "the fix") would have been a **suppression** — it hides the
> file from the scanner without removing the vulnerable vendored code from
> the image. What actually closed the finding was **#3246** (pip removed
> entirely from the runtime image, so `bom.cdx.json` and the vendored copies
> are gone) with `skip-files` kept as a belt-and-braces guard on top.
> Every "false positive" reference below is left in place for the historical
> record of what this audit believed at publication time, but should be read
> with this correction — do not use `skip-files` alone as a template for
> other noisy scanner findings without first checking whether the flagged
> version genuinely exists somewhere in the image.

---

## The single most important finding: is `backend-test` green on `main`?

**Yes — confirmed, and consistently so.** Checked the `backend-test` job on
every `push`-to-`main` run of `ci.yml` since 2026-08-01T23:29Z (7+
consecutive runs through 2026-08-02T02:30Z): `success` on every one, "Run
backend tests" step included. 501 backend test files are providing real
merge protection on `main` today.

The PR #3037 red on `backend-test` was a `pull_request`-vs-merge-ref
artifact (same class as the already-closed #2861/C8), not a `main`-branch
gate defect. Anyone re-checking this: always confirm against `push`-to-`main`
runs, never a PR run, and check job-level status, not workflow-level —
`ci.yml`'s workflow-level conclusion has been `failure` on nearly every
recent `main` push, but that's driven by `deploy-backend` (Railway paused,
C5) and `docker-image-scan`/`driver-app-e2e` (below), not by `backend-test`.

---

## Gate-by-gate table

Legend: **B** = blocking (`continue-on-error: false` or unset), **A** =
advisory (`continue-on-error: true`). Status is the live `main`-branch state
as of 2026-08-02T02:41Z UTC unless noted.

### `ci.yml` (CI/CD Pipeline) — `push`/`pull_request` to `main`/`develop`

| Gate | Job id | B/A | Status on `main` | Notes |
|---|---|---|---|---|
| backend-test | `backend-test` | B (lint/codecov-upload steps: A) | 🟢 green | Confirmed green on 7+ consecutive `main` pushes — see above. |
| python-dependency-audit | `python-dependency-audit` | B | 🟢 green | Split from backend-test in 2026-06 specifically so a new advisory doesn't gate the test suite. |
| frontend-test | `frontend-test` | n/a — `if: false` | ⚫ disabled | `frontend/` deprecated 2026-04-14 (D-003/D-004). Never runs, never posts a check. |
| driver-app-test | `driver-app-test` | B (coverage-upload step: A) | 🟢 green | |
| rider-app-test | `rider-app-test` | B (coverage-upload step: A) | 🟢 green | |
| admin-test | `admin-test` | B | 🟢 green | Includes `npm run build`. |
| E2E tests (Playwright) | `e2e-test` | PR: A (`continue-on-error: ${{ event == pull_request }}`); main push: B | 🟢 green | |
| Visual regression (Playwright) | `visual-regression-test` | A unconditionally | ⚪ self-skips | No committed baselines; job detects this and skips its own steps rather than failing. See `update-visual-baselines.yml`. |
| Rider app E2E (Playwright) | `rider-app-e2e` | PR: A; main push: B | 🟢 green | |
| Driver app E2E (Playwright) | `driver-app-e2e` | PR: A; main push: **B** | 🔴 **red on main**, blocking | Failed on both `main`-push runs checked (`0c58c3dc`, `24b3e49f`); a closed CR exists for a related driver-app E2E failure (#3247, "createPermissionHook is not a function") but its "Run E2E tests" step failure wasn't diagnosed line-by-line in this audit. **Needs a fresh CR** — see FIX list below; unlike PRs (where this is advisory), a push to `main` treats it as blocking, so this job is a real red gate on `main` right now. |
| deploy-backend | `deploy-backend` | B, main-only | 🔴 red, but **expected** | Railway (primary step) and Render (fallback step) both fail — documented, ACTION_ITEMS C5: Railway standby deploys deliberately paused via a GitHub Environment protection rule. Not a code defect. |
| deploy-frontend | `deploy-frontend` | n/a — `if: false` | ⚫ disabled | Same deprecation as frontend-test. |
| deploy-admin | `deploy-admin` | B, main-only | 🟢 green | |
| mobile-build | `mobile-build` | B, main-only + `[build]` commit-message gate | ⚪ rarely runs | Gated on commit message; not a PR-blocking gate. |
| security-scan (trufflehog + Trivy fs) | `security-scan` | B | 🟢 green | |
| docker-image-scan | `docker-image-scan` | B (diagnostic step: A) | 🟢 green (as of commit `00d1d9f5`, 2026-08-02T02:30Z) | Was red on the prior two `main` pushes (`0c58c3dc`, `24b3e49f`) with the CR-2026-002 msgpack/setuptools finding — **real, not a false positive** (see correction at top of this doc); `skip-files` alone was a suppression, actually closed by #3246 removing pip from the runtime image. |
| Post-deploy smoke test | `smoke-test` | B, main-only | 🟢 green | |
| notify-failure | `notify-failure` | Slack step: A | n/a | Only runs `if: failure()`. |

### `security-gates.yml` (Security Gates, G1–G7) — `pull_request`/`push` to `main` + daily schedule

| Gate | Job id | B/A | Status on `main` | Notes |
|---|---|---|---|---|
| G1 · Bandit (Python SAST) | `bandit` | **B** | 🟢 green | |
| G2 · ESLint security (rider-app) | `eslint-security` (matrix) | **B** | 🟢 green | |
| G2 · ESLint security (admin-dashboard) | `eslint-security` (matrix) | **B** | 🟢 green | |
| G2 · ESLint security (driver-app) | `eslint-security` (matrix) | **B** | 🟢 green (as of commit `00d1d9f5`) | Was red on 2 prior `main` pushes: `yarn install` failed — `@testing-library/jest-dom@6.10.0` requires Node ≥22, workflow pins Node 20.20.2. **Root cause identical across ≥2 runs spanning 2026-08-01T23:30Z→2026-08-02T02:08Z** (not a flake). CR-2026-003 / [#3240](https://github.com/srikumarimuddana-lab/spinrvm/issues/3240) was filed and closed "completed" for this same day; confirmed green on the current head commit. |
| G3 · Semgrep (Spinr rules + public) | `semgrep` | **B** (findings themselves currently soft-pass — see below) | 🟢 green | Job-level blocking; internal script only fails on a broken run, not on findings (`rc==1` → `::warning::` only, pending rule tuning). This is a real gap: a genuine Semgrep finding today would not fail the gate. |
| G4a · pip-audit (Python deps) | `pip-audit` | **B** | 🟢 green | |
| G4b · yarn audit (rider-app) | `yarn-audit` (matrix) | **B** | 🟢 green | |
| G4b · yarn audit (driver-app) | `yarn-audit` (matrix) | **B** | 🟢 green | |
| G4b · yarn audit (admin-dashboard) | `yarn-audit` (matrix) | **B** | 🟢 green | |
| G4b · yarn audit (shared) | `yarn-audit` (matrix) | **B** | 🟢 green | `fail-fast: false` — a prior masking incident (one leg cancelling the others) was fixed 2026-07-29. |
| G4c · npm audit (admin-dashboard) | `npm-audit-admin` | **B** | 🟢 green | |
| G5a · Gitleaks (git history) | `gitleaks` | **A** (`continue-on-error: true`) | 🟢 green | Deliberately advisory: a real, still-valid leaked Supabase `service_role` key exists in git history pending rotation; flipping to blocking before rotation would be dishonest enforcement, not real enforcement. **Documented, dated, has an owner action** — not a stale baselining artifact. |
| G5b · Gitleaks (admin bundle) | `bundle-secrets` | **A** (`continue-on-error: true`) | 🟢 green | No dated justification comment in the file (unlike G5a). **Needs documentation** — see DOCUMENT list. |
| G6 · Trivy container scan | `container-scan` | **B** | 🟢 green (as of `00d1d9f5`) | Same CR-2026-002 finding/timeline as `docker-image-scan` above — real vulnerable code (pip's vendored msgpack/setuptools), closed by #3246 (pip removed from runtime image) with `skip-files` as a belt-and-braces guard, not by `skip-files` alone. |
| G7 · pip-licenses | `license-check` | **B** | 🟢 green | |
| Security gates summary | `summary` | n/a (`if: always()`) | 🟢 green | Fixed in this PR: previously claimed "Gates G1–G7 ... are blocking" including gitleaks, contradicting G5a/G5b's literal `continue-on-error: true`. |

**Header comment finding:** the top-of-file comment claimed "Non-blocking for
the first 2 weeks — treat as baselining window," undated. **Fixed in this PR**
— replaced with an accurate, dated statement: 8 of 10 jobs are blocking; only
G5a/G5b remain advisory, each for its own documented reason, not a blanket
window.

### `ci-guardrails.yml` (CI Guard Rails) — `pull_request` only (no `push` trigger)

Not directly comparable to "status on `main`" since it only fires on PR
events (opened/synchronize/reopened) against a PR's head, never on a push to
`main` itself. Checked the last 30+ completed runs (most recent: 8 in a row,
2026-08-02): **all `success`**.

| Gate | Job id | B/A | Notes |
|---|---|---|---|
| Coverage regression check | `coverage-regression-gate` | **A** (`continue-on-error: true`, job + a deps-install step) | Confirmed literal, matches CLAUDE.md's own description. |
| Lint warning trend check | `lint-trend-gate` | **A** | Ratchet-only budget (admin-dashboard: 600 warnings), CR-2026-001 tracks lowering it. |
| Security posture check | `security-posture-gate` | **B** | pip-audit step inside is deliberately non-blocking by design (CVE enforcement lives in `security-gates.yml` G4a instead) — script always exits 0. |
| Migration safety check | `migration-safety-gate` | **B** | |
| Breaking change detection | `breaking-change-gate` | **A** | "Warning only — does not block PR" per its own script comment. |
| Mobile test placement check | `mobile-test-placement-gate` | **B** | |
| Change Impact & Risk Log check | `change-impact-log-gate` | **B** | Confirmed literal, matches CLAUDE.md. Checks presence/shape only, not content quality. |
| Apply risk label | `risk-label` | **A**, informational only | Never intended to block; labels the PR `risk:high/medium/low`. |
| Post guard rail summary | `guardrail-summary` | n/a | Posts a PR comment; doesn't itself gate. |

`coverage-regression-gate` (advisory) and `change-impact-log-gate` (blocking)
look identical in the PR checks UI — both just show as a named check with no
visual distinction of blocking status. This is a real usability gap (not
fixed here — UI-level, not a workflow-file fix) worth flagging to whoever
owns branch-protection-required-checks configuration.

### Everything else — not reachable on a normal PR to `main`

| Workflow | Trigger | Reachable on a normal PR? |
|---|---|---|
| `apply-supabase-schema.yml` | `workflow_dispatch` only | No — manual only |
| `bootstrap-fly.yml` | `workflow_dispatch` only | No — one-time/manual per its own header |
| `ci-error-audit.yml` | `workflow_run` (reacts to ci.yml/test-env.yml/deploy-backend.yml/eas-build.yml) + `workflow_dispatch` | No — reactive, never a direct PR check. Has a known blind spot: can't detect `test-env.yml` failures because that workflow suppresses its own failures with `|| true`/`|| echo` (CR-2026-008, approval-gated). |
| `claude-audit.yml` | `pull_request`/`push` path-filtered to `.claude/**`, `CLAUDE.md`, `.agents/**`, `.github/workflows/claude.yml` | Only if the PR touches those paths |
| `claude-review.yml` | `pull_request` (opened/reopened/ready_for_review) + `/claude review` comment | Yes, but skips cleanly (green, no review) when `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN` are both unset — deliberate per C7, no cost incurred |
| `dependabot-auto-merge.yml` | `pull_request`, `if: github.actor == 'dependabot[bot]'` | No — never for a human-authored PR |
| `deploy-backend.yml` | `push` to `main`, path-filtered to `backend/**`/`railway.json` | No — main-only |
| `deploy-fly.yml` | `push` to `main`, path-filtered to `backend/**`/`fly.toml` | No — main-only |
| `eas-build.yml` | `push` to `main`, path-filtered to app dirs, or `workflow_dispatch` | No — main-only or manual |
| `eas-native-build.yml` | `workflow_dispatch` only | No — fully manual |
| `migration-check.yml` | `pull_request`, path-filtered to `backend/migrations/*.sql` | Only if the PR touches a migration file |
| `mobile-dep-check.yml` | `pull_request`/`push`, path-filtered to rider/driver `package.json`/`yarn.lock` | Only if the PR touches those files |
| `pip-compile-check.yml` | `pull_request`/`push`, path-filtered to `backend/requirements.in`/`.txt` | Only if the PR touches those files |
| `subprocessor-audit.yml` | quarterly `schedule` + `workflow_dispatch` | No |
| `subprocessor-monitor.yml` | weekly `schedule` + `workflow_dispatch` | No |
| `sync-mobile-lockfiles.yml` | `pull_request`, path-filtered | Only if the PR touches rider/driver `package.json` |
| `test-env.yml` | `push`/`pull_request` to **`develop`** only | No — never fires against `main` |
| `update-visual-baselines.yml` | `workflow_dispatch` only | No — manual baseline generator |

`pr-checks.yml` runs on every PR (no path filter) but produces mostly
advisory/informational checks by construction (no `setFailed`/non-zero
exit), not by `continue-on-error`: `auto-label`, `size-advisory`,
`merge-conflict-detect`, `expand-sections`, `auto-summary` never fail.
`required-fields` is the one job that can go red (calls `core.setFailed`
when required PR template fields are missing).

---

## FIX — red, blocking, no documented accepted-risk entry

1. **`driver-app-e2e` (ci.yml) red on `main`-push** (blocking there, though
   advisory on PRs). Failed on both `main` pushes checked in this audit. A
   related closed CR (#3247) exists but doesn't obviously match the current
   failure signature — needs a fresh diagnosis and either a fix or a new
   `[CR]` documenting why it's an accepted, known-flaky risk. **Filed as
   [CR-2026-006 / #3256](https://github.com/srikumarimuddana-lab/spinrvm/issues/3256).**
2. **G3 · Semgrep findings soft-pass** (`security-gates.yml`) — the job is
   `continue-on-error: false` (blocking) but its own script only fails on a
   broken run, not on an actual Semgrep finding (`::warning::` only). This
   means a real new Semgrep finding today would not turn this gate red. Not
   an emergency (findings are visible in the Security tab), but it's a real
   coverage gap masquerading as "blocking," which is exactly the kind of
   mismatch this audit was commissioned to surface. Recommend either a
   `[CR]` to formally accept this as a phased rollout (rule-tuning in
   progress) or a plan to flip it to hard-fail findings.

## DOCUMENT — red/advisory for a plausible reason, but no CR/ACTION_ITEMS entry found

1. **G5b · Gitleaks (admin bundle)** — advisory (`continue-on-error: true`)
   with no dated justification comment in the file, unlike its sibling G5a
   (which explains the leaked-key situation in detail). Either the same
   leaked-key rationale applies here too (in which case say so inline) or
   there's a separate reason that should be written down. **Filed as
   [CR-2026-007 / #3257](https://github.com/srikumarimuddana-lab/spinrvm/issues/3257).**
2. **`ci-guardrails.yml`'s `coverage-regression-gate` vs. `change-impact-log-gate`
   UI ambiguity** — both blocking-looking checks render identically in the
   GitHub PR checks list despite opposite `continue-on-error` semantics.
   Not a workflow-file bug (GitHub's UI doesn't expose this distinction),
   but worth a short doc note in `ci-guardrails.yml`'s header pointing
   contributors at this audit doc for the authoritative blocking/advisory
   list, since the UI won't tell them.

## RETIRE — never runs, permanently advisory with nobody reading it, or superseded

1. **`frontend-test` / `deploy-frontend` (ci.yml)** — both hard-disabled via
   `if: false` since the `frontend/` surface was deprecated 2026-04-14
   (D-003/D-004). These are dead YAML: no check ever posts, nothing to
   retire from branch protection (they were presumably already removed from
   required checks when disabled — verify), but the job definitions
   themselves could be deleted rather than left as permanently-skipped
   placeholders. Low priority, purely a repo-hygiene item.
2. **`visual-regression-test` (ci.yml)** — currently self-skips every run
   (no committed baselines) rather than providing coverage. Not literally
   "nobody reading it" (it's clearly documented and has a companion
   `update-visual-baselines.yml` workflow to seed it), but until baselines
   are committed it provides zero signal. Tracked already via #2829 — no
   new action needed here, just confirming it's the same gap this audit
   would otherwise flag as new.
3. **`ci-error-audit.yml`'s blind spot on `test-env.yml`** — cannot detect
   `develop`-branch CI failures because `test-env.yml` suppresses its own
   failures with `|| true`/`|| echo`. Already tracked (CR-2026-008,
   approval-gated to modify `test-env.yml`). Since `test-env.yml` doesn't
   even run against `main` (see table above), this is lower urgency than it
   looks — flagging only so it isn't rediscovered as "new."

---

## Recommendations, impact, and next steps

**Immediate (this week):**
- `driver-app-e2e` (#3256) and G5b documentation (#3257) are now filed —
  triage and close them; both are cheap (a diagnosis + a paragraph) and each
  closes a real "why is this red/advisory and is that OK" question a future
  contributor will otherwise re-ask, as PR #3037's review already had to.
- Decide on G3 Semgrep (FIX #2): either accept the soft-pass phase formally
  with a target date to flip it hard, or flip it now if the current finding
  count is already at/near zero (the same "ratchet, don't big-bang" pattern
  already used for G2's ESLint budgets and `lint-trend-gate`).

**Value of this audit, concretely:** two of the five checks that were red on
PR #3037 (`G6 · Trivy container scan`, and its `ci.yml` twin
`docker-image-scan`) had a documented CR (#3048/CR-2026-002) whose fix landed
and was **verified green during this session** — confirmed on the very next
`main`-branch commit, not assumed from the CR's own "done" claim. That
verification step was real and valuable; the *conclusion drawn from it*
wasn't — this doc, and #3048 itself at the time, wrongly attributed the
green result to a "false positive" fix (`skip-files` alone). A later
diagnostic (#3113) and fix (#3246, removing pip from the runtime image)
established the findings were real vulnerable code, not a scanner artifact —
see the correction at the top of this doc. The lesson from this session's
own mistake: confirming a check went green is necessary but not sufficient —
also confirm *why* it went green before writing up the fix as correct. The
other two checks that were red on PR #3037 (`G4b`/`G4c` audits) were already
green on `main` by the time this audit ran — they were real findings on
2026-08-01 that had already been fixed by 2026-08-02, underscoring that a
snapshot from one day earlier was stale, not that the gates themselves are
unreliable.

**Impact if the FIX items are left undone:** `driver-app-e2e` staying red on
`main` means every future `main` push shows workflow-level `failure` in
`ci.yml` for a reason unrelated to that push's own diff — exactly the
"red stops carrying information" failure mode this audit exists to prevent,
now with one fewer excuse (Trivy/G6, C6's other long-standing offender, is
resolved). G3 Semgrep's soft-pass is a quieter but real risk: a
money/auth/dispatch-path Semgrep rule could fire today and nobody would see
red for it.

**Benefit of what's already correct:** `backend-test` — the gate 501 test
files depend on for real merge protection — is confirmed green and stable
across the full window checked. `ci-guardrails.yml`'s 7 gates are all
healthy and, per this audit, correctly documented (the one confirmed
discrepancy, `coverage-regression-gate` vs. `change-impact-log-gate`'s
identical-looking UI, is a GitHub UI limitation, not a workflow-config bug).
`security-gates.yml`'s "2-week baselining window" framing — which materially
understated how much real enforcement is already live — is now corrected in
the file itself, so the next person reading it starts from an accurate
picture instead of re-deriving it the way this audit had to.

**What this audit did NOT do (explicitly out of scope, per the task):**
root-cause or fix `driver-app-e2e`'s actual test failure, flip G3 Semgrep to
hard-fail, bump Trivy from 0.70.0 to 0.72.0 (noted, not touched — a version
bump needs its own build/lint/test verification pass per CLAUDE.md), or
change any branch-protection *required-checks* configuration in GitHub
settings (that's a repo-settings change, not a workflow-file change, and
wasn't requested).
