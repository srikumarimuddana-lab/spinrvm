# CI/CD Gates, Guardrails & Release-Management Audit

**Date:** 2026-08-27
**Author:** Claude (session audit, requested by vikas@ngitservices.com)
**Scope:** every `.github/workflows/*.yml` (31 files), CODEOWNERS, PR template, Dependabot config, branch-protection posture, and how they interact with `CLAUDE.md`'s own mandatory release-gate policy.
**Method:** full-content read of the 12 highest-leverage workflow files (`ci.yml`, `ci-guardrails.yml`, `security-gates.yml`, `migration-check.yml`, `pr-checks.yml`, `claude-review.yml`, `dast-zap-baseline.yml`, `deploy-fly.yml`, `deploy-backend.yml`, `ci-error-audit.yml`, `ci-audit-autoclose.yml`, `eas-build.yml`) plus a structured inventory pass over the remaining 19; cross-referenced against `ACTION_ITEMS.md`'s existing CI/CD-tagged findings (C-prefixed and E-prefixed items) so this audit builds on, rather than rediscovers, what the team already knows.

---

## 0. Top-line verdict

Spinr's CI/CD is **not thin**. It is one of the more mature setups I've reviewed: 31 workflows, ~7,000 lines, a numbered security-gate system (G1–G7b), two independent coverage-floor gates that are genuinely scoped and blocking, a migration-safety checker that validates RLS/rollback/append-only/collision, a PR-hygiene bot that enforces a tiered template and auto-expands domain-specific checklists, and a self-auditing meta-layer (`ci-error-audit.yml` files P0/P1 issues on real CI failures and auto-closes them once recovered). Nearly every gate's own comments document *why* it is or isn't blocking, with dates, PR numbers, and measured baselines — a level of institutional memory most repos don't have.

The gaps that remain are not "you have no gates." They are:

1. **A governance gap that undermines everything below it**: there is concrete, already-documented evidence (`ACTION_ITEMS.md` C21) that PRs have merged to `main` via GitHub's native auto-merge while required checks were still `queued`/`in_progress`, and that `main`'s required-status-checks list has never been audited against the ~57 checks that actually run. This is a repo-admin-only fix no engineering session has been able to make. **Until this is fixed, every gate documented below is advisory in practice, not because the gate is weak, but because merge doesn't have to wait for it.**
2. **Zero blast-radius-aware triggering on the four heaviest workflows** (`ci.yml`, `ci-guardrails.yml`, `security-gates.yml`, `pr-checks.yml`) — every job runs on every PR regardless of what changed, while the *deploy* workflows and a handful of mobile-specific checks already do exactly this correctly. This is the direct, unaddressed cause of slow, expensive turnaround time.
3. **A handful of gates that look enforced but are structurally inert** (coverage-regression-gate has no real baseline to compare against; two secret-scanning jobs are advisory by design; CODEOWNERS routes to placeholder team handles GitHub can't resolve).
4. **One real, still-open live secret leak** (a valid Supabase `service_role` key in git history) that is the *reason* one of those gates is advisory — this predates this audit and is already tracked, but is worth restating plainly because it's a live risk, not a process nit.

Sections 1–4 detail each; Section 5 is the phased implementation plan with what I can do myself (workflow YAML) versus what needs a human with repo-admin GitHub access.

---

## 1. Governance gap: gates exist, but merge doesn't reliably wait for them

**Finding (verified, not new — `ACTION_ITEMS.md` C21, C13, E8):**

- No session, including this one, has ever had read/write access to **Settings → Branches → main → required status checks**. `ToolSearch` in this session for branch-protection/ruleset tools returned nothing; the GitHub MCP server exposes PRs, commits, files, labels, and Actions runs, but not repository branch-protection settings.
- **C21 (open):** two real PRs (#3719, #3728) merged via GitHub's native per-PR auto-merge toggle — not this repo's own `dependabot-auto-merge.yml` (grepped: hard-gated to `github.actor == 'dependabot[bot]'`, ruled out) — while `CI/CD Pipeline`, `CI Guard Rails`, and `Security Gates` were still in flight, and in one case with a check (`maestro-e2e.yml`) already failed, in another with 2 of 57 checks (`G4b · yarn audit`) red at merge time. GitHub's native auto-merge only waits for whatever branch protection lists as *required*, not for every check that actually runs — which means the required list is very likely a small, stale subset of the ~57 checks now firing per PR.
- **C13 (open, non-reproducing as of last check):** on at least one PR, every `pull_request`-triggered workflow (`ci.yml`, `security-gates.yml`, `ci-guardrails.yml`, `pr-checks.yml`) fired **zero runs** against two consecutive commits, confirmed against the Actions API directly (not just the UI, which can lag). Root cause was never identified — an Actions-policy or webhook-delivery issue, both of which need Settings access this session doesn't have. It hasn't reproduced since, but "hasn't reproduced" is not "fixed" — there's no monitoring that would catch it happening again except a human noticing an unusually quiet PR.
- **E8 (open):** `.github/CODEOWNERS` routes money/schema/auth/dispatch/safety paths to `@spinr-org/TBD-*` placeholder handles that GitHub cannot resolve. Even if "Require review from Code Owners" were enabled in branch protection, it currently enforces nothing, because there is nothing real to resolve against.

**Why this is the #1 finding, not a footnote:** every other section of this audit — coverage floors, migration safety, security gates, the Change Impact Log requirement — assumes that a red or in-progress check blocks merge. C21 is direct evidence that assumption is currently false for at least some PRs. A perfectly-designed gate that merge doesn't have to wait for provides exactly the same protection as no gate at all: none, on the PR that happens to race it. This also means `CLAUDE.md`'s own "Pre-merge release gates (mandatory while live app testing is active)" section — item 9, "Escalate, don't silently ship, when in doubt" — has no automated backstop today; it currently depends entirely on every contributor manually honoring it.

**This cannot be fixed by editing workflow YAML.** It requires a repo admin to:

1. Open **Settings → Branches → main** and compare the required-status-checks list against the full set of checks that actually run (`CI Guard Rails` summary, `Security Gates` summary, `backend-test`, `admin-test`, `rider-app-test`, `driver-app-test`, `Migration Safety Check`, `mobile-bundle-smoke`, and — once the fixes in Section 5 land — the path-filtered equivalents of all of those). Add whatever's missing.
2. Decide whether **Settings → General → Allow auto-merge** should stay enabled repo-wide for non-Dependabot actors, given there is currently no functioning human-review gate (CODEOWNERS is inert per E8) to catch anything the required-checks list itself misses. My recommendation: disable it for everyone except the Dependabot patch-only path (`dependabot-auto-merge.yml` already scopes itself correctly), until CODEOWNERS has real teams and required-checks is audited. Auto-merge without a working review gate is strictly a race condition waiting to be lost.
3. Fill real GitHub team slugs into `CODEOWNERS` and enable "Require review from Code Owners." I can't do this myself — I don't know your org's actual team structure — but the file is otherwise ready; it's a find-and-replace once you tell me the real team handles.
4. Optionally investigate C13's silent-non-trigger via **Settings → Actions → General** and **Settings → Webhooks** — lower urgency since it hasn't reproduced, but worth a five-minute look given how corrosive a silently-skipped required check is.

I'm flagging this at the top and will not silently proceed past it — this is exactly the kind of "escalate, don't silently ship" situation `CLAUDE.md` calls for, because everything else in this audit is only as good as this layer.

---

## 2. Headline gap: no blast-radius-aware triggering (the turnaround-time ask)

**Current state, verified file-by-file:**

| Workflow | Path-filtered? | What it runs on *every* PR regardless of diff |
|---|---|---|
| `ci.yml` | **No** | Full backend pytest+coverage (~20 min w/ Postgres service), all 3 frontend test suites (~15 min each), 4 Playwright E2E suites (~20 min each), Docker build + Trivy scan + diagnostic, trufflehog+Trivy filesystem scan |
| `ci-guardrails.yml` | **No** | Full backend pytest+coverage (shared run), 2 coverage-floor checks, lint-trend (ruff + admin ESLint), security-posture (lockfile hash + pip-audit + trufflehog + EXPO_PUBLIC scan), migration-safety, breaking-change, mobile-test-placement, Change Impact Log check — 9 jobs + summary |
| `security-gates.yml` | **No** | Bandit, ESLint-security ×3 modules, Semgrep, pip-audit, yarn-audit ×4 modules, npm-audit-admin, gitleaks ×2, Trivy container scan, license-check ×2 — 11 jobs |
| `pr-checks.yml` | **No** | Auto-label, size-advisory, required-fields, merge-conflict-detect, expand-sections, auto-summary — cheap individually, but always-on |
| `test-env.yml` (`develop` lane) | **No** | Same shape as `ci.yml`, duplicated for the `develop` branch |
| `claude-review.yml` | **No** (path-*ignore* on docs only) | 3-agent AI review pipeline (when the API key is configured) |

Compare against what the repo **already does correctly** elsewhere — this isn't a new pattern I'm introducing, it's an existing one I'm asking to be applied consistently:

- `migration-check.yml` — native `paths: backend/migrations/*.sql`.
- `mobile-bundle-smoke.yml`, `mobile-dep-check.yml`, `pip-compile-check.yml`, `sync-mobile-lockfiles.yml` — native `paths:` scoped to their surface.
- Every `deploy-*.yml` — scoped to the surface it deploys (`backend/**`, `driver-app/**`, `metrics-agent/**`).
- `eas-build.yml` — uses `dorny/paths-filter` (the real GitHub Action for this, not just native `paths:`) to route OTA publishes to only the app(s) that changed. **This is the proven, working precedent** for the pattern I'm recommending below.

**Reasoning for why this matters, concretely, not abstractly:** a PR that only touches `docs/`, `admin-dashboard/`, or `.github/ISSUE_TEMPLATE/` today still pays for: a full backend test suite against a live Postgres container, three frontend test suites, four E2E Playwright runs, a Docker image build, Bandit, Semgrep, pip-audit, three yarn-audit legs, an npm audit, two license scans, and two coverage-floor computations that will trivially no-op because no tracked file was touched — anywhere from 15 to 30+ minutes of wall-clock and a non-trivial multiple of that in billed Actions minutes, to validate a change that could not possibly have broken any of it. This is exactly the "optimum and reasonable and logical" turnaround-time problem in the request: the fix isn't "run fewer checks," it's "run the checks that could actually catch something for *this* diff."

### Recommended design

A **single reusable "detect changed surfaces" job**, added once and referenced by the three heavy workflows, using `dorny/paths-filter` (already vendored/proven via `eas-build.yml` — no new dependency, no new trust surface):

```yaml
# .github/workflows/detect-changes.yml (new, workflow_call)
on:
  workflow_call:
    outputs:
      backend: { value: ${{ jobs.filter.outputs.backend }} }
      rider:   { value: ${{ jobs.filter.outputs.rider }} }
      driver:  { value: ${{ jobs.filter.outputs.driver }} }
      admin:   { value: ${{ jobs.filter.outputs.admin }} }
      shared:  { value: ${{ jobs.filter.outputs.shared }} }
      migrations: { value: ${{ jobs.filter.outputs.migrations }} }
      workflows:  { value: ${{ jobs.filter.outputs.workflows }} }
jobs:
  filter:
    runs-on: ubuntu-latest
    outputs:
      backend: ${{ steps.f.outputs.backend }}
      # ... one output per surface
    steps:
      - uses: actions/checkout@<pinned>
      - uses: dorny/paths-filter@<pinned>
        id: f
        with:
          filters: |
            backend: ['backend/**']
            rider:   ['rider-app/**', 'shared/**']
            driver:  ['driver-app/**', 'shared/**']
            admin:   ['admin-dashboard/**']
            shared:  ['shared/**']
            migrations: ['backend/migrations/**']
            workflows:  ['.github/workflows/**']
```

Then each existing job in `ci.yml` / `ci-guardrails.yml` / `security-gates.yml` gains one line:

```yaml
backend-test:
  needs: [detect-changes]
  if: needs.detect-changes.outputs.backend == 'true' || needs.detect-changes.outputs.workflows == 'true' || github.ref == 'refs/heads/main' || github.event_name == 'merge_group'
```

**Critical design choices, and why each one is load-bearing, not optional:**

1. **`shared/` fans out to both mobile apps.** A change to `shared/` (used by both rider-app and driver-app per the labeler config already in this repo) must trigger both, not neither — this is exactly the kind of blast-radius mapping the request asked for, and getting it wrong (under-triggering) is worse than over-triggering. `admin-dashboard` has no `shared/` dependency per the existing labeler rules, so it's excluded from that fan-out.
2. **`workflows` (a change to `.github/workflows/**` itself) always triggers everything.** A CI/CD change is exactly the case where you cannot trust path-filtering to know it's safe — that would be the audit's own recommendation defeating itself the first time someone edits a workflow file.
3. **`main` push and `merge_group` always run everything, unconditionally**, ignoring the filter outputs entirely. Path-filtering is a PR-time optimization for fast feedback on the *branch*; the merge-queue/post-merge run against `main` is the last line of defense and must never skip a surface, because that's the run branch protection should actually be keyed off of for the trunk's own health. This also directly answers a risk a reviewer would otherwise raise: "what if the filter itself has a bug and silently skips something that mattered" — on `main` it can't, because filtering is bypassed there.
4. **A manual override stays available**: a PR label (e.g. `ci:full-run`) or the existing `workflow_dispatch` path lets an author force the full matrix if they believe the filter under-scoped their change (e.g. a change to a shared Python utility whose blast radius isn't captured by a top-level directory boundary). This is the same "escalate, don't silently ship" philosophy applied to the gate design itself — a false negative from path-filtering should be a one-label override away, not a fight.
5. **A skipped job still reports a conclusion GitHub's required-checks mechanism accepts.** A job skipped via `if:` reports `skipped`, and GitHub treats a `skipped` required check as satisfying "all required checks passed" for merge purposes — this is standard, documented GitHub Actions behavior, not a gap I'm introducing. Combined with point 1 (Section 1), once branch protection's required list is fixed to reference the right check names, this pattern is safe to rely on.

**Estimated turnaround-time impact:** for the common case (a PR touching exactly one surface — the majority of PRs, per the existing `risk-label`/labeler path-glob evidence that most changes are single-surface), this removes 3 of 4 frontend test suites, 3 of 4 E2E suites, most of `security-gates.yml`'s per-module matrix legs, and both coverage-floor jobs' pytest runs (already no-ops today, but currently still pay the full-suite-run cost to determine that). A rough estimate from the job list above: a single-surface PR today pays for roughly 25 jobs; after filtering, it pays for the ~6–8 that can actually be affected, plus the always-on governance jobs (Change Impact Log, required-fields, migration-safety-gate's own no-op check, license/CVE audits scoped to the touched ecosystem). That's a 60–70% reduction in per-PR compute and, more importantly, wall-clock time to green for the majority of PRs — without reducing coverage for the PRs that do touch multiple surfaces, which still run everything relevant to what they touched.

**This is additive and reversible**: nothing is deleted, every job keeps its existing logic, only the `if:` condition changes, and the override label is the rollback path if a specific mapping proves wrong in practice. Per `CLAUDE.md`'s own pre-merge release gates policy ("prefer a new column/field/flag over mutating... Feature-flag anything user-visible and non-trivial"), I'd land this incrementally: first the `detect-changes` job and wiring it into the lower-risk, purely-advisory jobs (lint-trend, breaking-change-gate), observe a few PRs, then extend to the blocking jobs once the filter mappings are confirmed correct in practice.

---

## 3. Gates that look enforced but are structurally inert

| Gate | File | Why it can't actually catch anything today | Fix |
|---|---|---|---|
| `coverage-regression-gate` | `ci-guardrails.yml` | No `CODECOV_TOKEN` secret exists (no one has Codecov account access to create one — `ACTION_ITEMS.md` C12). Base-branch coverage always resolves to 0, so the gate correctly (as of C24's fix) reports "not verified" rather than a false pass — but it still never actually blocks a real regression. | Stop depending on Codecov entirely. Checkout the PR's merge-base commit in a second step, run the same `pytest --cov` against it, and diff locally — this removes the third-party dependency altogether and fits the "no one has Codecov access" reality already on record, rather than waiting on a token that may never arrive. Cost: one more full pytest run per PR unless folded into the same `shared-coverage-run` pattern already used for the other two floor gates (recommended — see Section 2's dedup note below). |
| CODEOWNERS review routing | `.github/CODEOWNERS` | Every handle is `@spinr-org/TBD-*` — GitHub can't resolve a non-existent team, so "Require review from Code Owners" enforces nothing even if enabled. | Needs the user to supply real GitHub team slugs (or usernames) per domain (payments, migrations, security, dispatch, safety) — I can wire the file the moment I have real names; I can't invent your org's team structure. |
| `gitleaks` (G5a, git-history) | `security-gates.yml` | `continue-on-error: true` **by design**, not oversight — a real, still-valid Supabase `service_role` key is sitting in git history and hasn't been rotated. Flipping to blocking today would make `main` permanently red. | See the standalone callout below — this is the actual blocker, not the gate's design. |
| `gitleaks` (G5b, admin bundle) | `security-gates.yml` | `continue-on-error: true` with **no known finding** — it's been green every time it's run; it was just never promoted, apparently inherited G5a's caution by file-proximity rather than its own reason. | Flip to `continue-on-error: false` now — this one has no blocker, just inertia. Low-risk, immediate win. |
| Admin routes / utilities coverage (≥70% per `CLAUDE.md`) | *(no gate exists)* | Unlike `corporate_*.py` and the 5 money-path files, there is no `check_admin_coverage_floor.py` — this tier of the CLAUDE.md policy is unenforced, policy-only. | Add a third scoped floor-gate script mirroring `check_money_path_coverage_floor.py`'s pattern (touched-files-only, blocking, fails closed on a missing coverage report) for `routes/admin/**` and `utils/**`. |

### Standalone callout: the leaked Supabase key is a live risk, not a CI-process nit

`security-gates.yml`'s own comment on G5a states plainly that a full history scan (once the ruleset was actually loaded — an earlier version had a config bug that made it scan with zero rules) found "a real, still-valid Supabase `service_role` key in git history." This predates this audit — it's already tracked in `docs/change-log/2026-07-30-secret-scanning-gate-was-vacuous.md` — but I'm restating it here because **a live service-role key sitting in git history is a P0-class exposure under `CLAUDE.md`'s own breach-protocol section** ("Any suspected PII exposure... is a P0 incident"), and a service-role key is a superset risk of PII exposure — it's full database access. I have Supabase MCP tools available in this session and could check whether that key is still active and rotate it, but **credential rotation is exactly the kind of hard-to-reverse, outward-facing action I won't take without explicit confirmation** — it can break any service still using the old key, and it's a decision with production blast radius that deserves a deliberate go-ahead, not something bundled silently into a CI-gates audit. Flagging it here as its own line item for your decision, separate from everything else in this document.

---

## 4. Smaller, concrete findings worth fixing alongside the above

- **`test-env.yml` (the `develop`-branch lane) has `continue-on-error: true` on its own backend test job** — a documented blind spot (tracked as needing CR-2026-009 approval before touching, since it's gated behind this repo's own Change Request process for a reason: the workflow comment notes this suppression is why `ci-error-audit.yml`'s `workflow_run` trigger can never fire for a genuine `test-env.yml` failure — the meta-audit layer has a hole exactly where this one lane is concerned). Fixing this needs a `[CR]` per the repo's own process, not a silent edit.
- **Duplicate/triplicate full backend-suite runs across workflow boundaries.** `ci.yml`'s `backend-test`, `ci-guardrails.yml`'s `shared-coverage-run`, and (historically) `test-env.yml` on `develop` each run the full suite independently. `ci-guardrails.yml`'s own comments record that cross-workflow artifact-sharing was "rejected as too complex for a first version" when the *within*-workflow version of this exact problem was fixed for C47. Worth revisiting now, specifically because it compounds with the path-filtering work in Section 2: once `ci-guardrails.yml`'s coverage gates only run for backend-touching PRs, the remaining duplication (`ci.yml` vs. `ci-guardrails.yml`, both still full-suite for a backend PR) is the next biggest lever on turnaround time. A `workflow_call` reusable job that both workflows invoke once, sharing one coverage artifact the way `shared-coverage-run` already does internally, would remove the second full pytest run entirely for the common case.
- **No CodeQL, no SBOM publication.** `codeql-action/upload-sarif` is used only as a SARIF *uploader* for Semgrep/Trivy — no actual CodeQL analysis engine runs anywhere. Existing SAST coverage (Bandit + Semgrep + ESLint-security) is already reasonably strong, so this is a defense-in-depth enhancement, not a critical gap — lower priority than everything above.
- **DAST (`dast-zap-baseline.yml`) is fully inert** — correctly documented as scaffolding blocked on no staging environment existing (`ACTION_ITEMS.md` E1). Not a CI-process gap; an infrastructure dependency. No action from this audit beyond noting it's still blocked.
- **Migration-safety gate does not validate reversibility, index-fit, or forward-compatibility** — only that a rollback *comment exists* (not that it's correct or executable), append-only, naming, RLS presence, and numeric-prefix collisions. This is a reasonable, deliberate scope boundary (semantic SQL correctness isn't something a regex-based CI check can safely claim to verify), but worth stating explicitly rather than letting "migration safety check passed" imply more than it does.

---

## 5. Phased implementation plan

Ranked by risk/reversibility, per `CLAUDE.md`'s own pre-merge release-gate discipline (additive over destructive, feature-flagged rollout, escalate when blast radius is unclear) — applied here to the gates themselves, since gating logic is exactly the kind of change whose blast radius is "every future PR."

| Phase | What | Who can do it | Risk if wrong |
|---|---|---|---|
| **0 — immediate, standalone** | Decide whether to rotate the leaked Supabase `service_role` key (Section 3 callout) | User decision; I can execute via Supabase MCP tools once confirmed | High if mishandled (service disruption) — must not be bundled into this change silently |
| **1 — safe, additive, code-only** | Add `detect-changes` reusable job; wire it into `lint-trend-gate` and `breaking-change-gate` first (both already advisory, zero merge risk if the filter is imperfect) | Me, in this session, once you confirm scope | Low — worst case, a job runs when it didn't need to; nothing under-tests during this trial phase |
| **2 — extend once Phase 1 is observed clean** | Wire `detect-changes` into the blocking jobs in `ci.yml`, `ci-guardrails.yml`, `security-gates.yml`; add the `ci:full-run` override label | Me | Medium — an under-scoped filter could theoretically skip a real check; mitigated by the `main`/`merge_group`-always-full-run rule and the override label |
| **3 — fix structurally inert gates** | Self-computed coverage baseline (drop Codecov dependency); flip G5b to blocking; add admin-routes coverage floor gate | Me | Low — each is scoped and additive |
| **4 — governance (repo-admin only)** | Audit & fix `main`'s required-status-checks list (C21); decide on repo-wide auto-merge policy; fill real CODEOWNERS handles once provided | **You** — no session has ever had this access | N/A — I cannot do this regardless of risk tolerance |
| **5 — lower priority / defer** | Cross-workflow coverage-run dedup; CodeQL addition; SBOM publication; `test-env.yml`'s `[CR]`-gated fix | Me, on request | Low, but real engineering time — sequence after 1–4 |

I've written this audit and stopped before touching any workflow file. Phase 1 is safe enough that I'd normally just proceed, but because this whole document is about *changing what gates every future PR*, and because Phase 2 explicitly is not low-risk, I want your explicit sign-off on scope before I start editing `.github/workflows/*.yml` — including which phases to do now versus later, and whether Phase 0 (the leaked key) should be handled in this session or handed off separately.
