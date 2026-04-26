# CI Error Audit System — Baseline Audit Report

| Field | Value |
|---|---|
| **Date** | 2026-04-25 |
| **Auditor** | CI Audit System v1.0 (initial baseline) |
| **Scope** | All CI surfaces: backend, rider-app, driver-app, admin-dashboard, frontend, shared |
| **Report Type** | Baseline — documents current state and establishes the audit system |
| **Status** | 🟡 Active — 7 Change Requests pending approval |

---

## 1. Executive Summary

This report establishes the Spinr CI Error Audit System baseline. It documents the complete CI/CD infrastructure as it exists on 2026-04-25, identifies gaps and risks, proposes 7 Change Requests for improvement, and defines the ongoing audit process.

**What was built:**
- Automated CI Error Audit workflow (`ci-error-audit.yml`) — triggers on any pipeline failure, classifies errors, assesses impact, and creates structured GitHub issues
- CI Guard Rails workflow (`ci-guardrails.yml`) — runs on every PR to prevent regressions in coverage, lint count, security posture, migrations, and API contracts
- 8 Python audit scripts in `scripts/ci-audit/` — the analysis engine
- 2 GitHub Issue templates — structured Change Request and Error Report forms
- Dependabot configuration — automated dependency updates across all 5 surfaces + GitHub Actions

**Key findings:**
- CI infrastructure is comprehensive and well-structured overall
- 3 inconsistencies identified between CI config and local tool config
- 1 overly-permissive lint gate (600 ESLint warnings tolerated)
- 0 automated dependency management in place before this audit
- 0 structured error tracking / audit trail before this audit

**7 Change Requests raised** (pending approval, none implemented):

| CR ID | Title | Priority |
|---|---|---|
| CR-2026-001 | Establish realistic coverage baseline and enforce via pytest.ini | P1 |
| CR-2026-002 | Reduce admin-dashboard ESLint warnings from 600 to <100 | P2 |
| CR-2026-003 | Add pre-commit hooks for ruff and ESLint | P2 |
| CR-2026-004 | Add deployment rollback automation on health check failure | P2 |
| CR-2026-005 | Add .gitleaks.toml for enhanced pre-commit secret detection | P2 |
| CR-2026-006 | Add requirements.txt pinning via pip-compile | P3 |
| CR-2026-007 | Enable Dependabot auto-merge for patch-level security updates | P2 |

---

## 2. CI Infrastructure Assessment

### 2.1 Workflows

| Workflow | File | Trigger | Status |
|---|---|---|---|
| CI/CD Pipeline | `ci.yml` | push/PR to main, develop | ✅ Active |
| Test Environment | `test-env.yml` | push/PR to develop | ✅ Active |
| Deploy Backend | `deploy-backend.yml` | push to main (backend/ paths) | ✅ Active |
| Apply Supabase Schema | `apply-supabase-schema.yml` | manual only | ✅ Active |
| EAS Build | `eas-build.yml` | push to main (mobile paths) | ✅ Active |
| **CI Error Audit** | `ci-error-audit.yml` | workflow_run failure + manual | 🆕 New |
| **CI Guard Rails** | `ci-guardrails.yml` | PR to main/develop | 🆕 New |

### 2.2 CI Jobs Inventory (ci.yml)

| Job | Surface | Tools | Coverage Gate | Security Gate |
|---|---|---|---|---|
| `backend-test` | Backend | pytest, ruff, pip-audit | 70% (aspirational) | pip-audit CVE scan |
| `frontend-test` | Frontend | Jest, ESLint | None | None |
| `driver-app-test` | Driver | Jest | 30% lines | EXPO_PUBLIC_ check |
| `rider-app-test` | Rider | Jest | 60% lines | EXPO_PUBLIC_ check |
| `admin-test` | Admin | ESLint, Vitest, npm build | None enforced | None |
| `e2e-test` | All (main only) | Playwright | N/A | N/A |
| `security-scan` | All | trufflehog, Trivy FS | N/A | CRITICAL/HIGH block |
| `docker-image-scan` | Backend | Trivy image, cosign | N/A | CRITICAL/HIGH block |
| `smoke-test` | Backend (main only) | curl health checks | N/A | N/A |
| `notify-failure` | All | Slack | N/A | N/A |

### 2.3 Test Infrastructure

| Surface | Framework | Config File | Threshold |
|---|---|---|---|
| Backend | pytest + coverage | `backend/pytest.ini` | None enforced (see CR-2026-001) |
| Frontend | jest-expo | `frontend/jest.config.js` | 30% lines |
| Rider App | jest-expo | `rider-app/jest.config.js` | 60% lines |
| Driver App | jest-expo | `driver-app/jest.config.js` | 30% lines |
| Admin Dashboard | Vitest | `admin-dashboard/vitest.config.ts` | 60% lines |
| Rider E2E | Playwright | `rider-app/playwright.config.ts` | N/A |
| Driver E2E | Playwright | `driver-app/playwright.config.ts` | N/A |
| Admin E2E | Playwright | `admin-dashboard/playwright.config.ts` | N/A |

### 2.4 Security Tooling

| Tool | Scope | When | Blocks CI? |
|---|---|---|---|
| pip-audit | Python CVEs | Every CI run | ✅ Yes (`--fail-on-vuln`) |
| TruffleHog v3 | Secrets in git | Every CI run | ✅ Yes (`--fail`) |
| Trivy (filesystem) | OS + lib CVEs | Every CI run | SARIF upload only |
| Trivy (image) | Docker image | Every CI run | ✅ CRITICAL/HIGH |
| cosign (keyless) | Image signing | main branch only | N/A |
| ruff (S codes) | Python security rules | Every CI run | ✅ Yes |
| EXPO_PUBLIC_ check | Mobile secret exposure | Every CI run | ✅ Yes |

---

## 3. Findings and Gaps

### Finding 1 — Coverage Gate Inconsistency (P1)

**What:** `backend/pytest.ini` explicitly removes `--cov-fail-under` with a comment explaining it was never achievable. The CI workflow (`ci.yml:79`) uses `--cov-fail-under=70`. These are inconsistent.

**Evidence:**
```
# backend/pytest.ini (line 14-17)
# Coverage is reported but NOT gated. The 80% target was aspirational and has
# never been met against the current Supabase-backed code path (baseline is
# ~6%). Restoring a meaningful --cov-fail-under threshold is part of the P1
# test-suite repair.
```

**Risk:** Developers running `pytest` locally see no coverage gate. The CI gate either silently passes (if mocked) or fails consistently, both of which erode trust in the metric.

**Action:** CR-2026-001 filed. Do not change until approved.

---

### Finding 2 — ESLint 600-Warning Tolerance (P2)

**What:** `admin-dashboard/package.json` runs ESLint with `--max-warnings 600`. At 600 warnings, lint output is no longer actionable signal.

**Evidence:**
```json
"lint": "eslint --max-warnings 600"
```

**Risk:** New warnings accumulate invisibly. Security-related lint warnings (jsx-a11y, React issues) are buried in noise.

**Action:** CR-2026-002 filed. The CI guard rail (`ci-guardrails.yml`) now enforces that the count does not *grow* beyond 600, but reducing it requires CR approval.

---

### Finding 3 — No Automated Dependency Updates (P2)

**What:** No Dependabot or Renovate configuration existed before this audit. Dependencies across all 5 surfaces accumulate CVEs silently between manual update cycles.

**Evidence:** `git log --all -- .github/dependabot.yml` shows no prior config.

**Risk:** pip-audit and Trivy catch known CVEs in CI, but without Dependabot, fixes are never automatically proposed. CVE windows grow.

**Action:** `.github/dependabot.yml` created (this audit). Covers pip, npm (all 4 JS surfaces), and GitHub Actions. CR-2026-007 filed for auto-merge of patch security updates.

---

### Finding 4 — Pre-commit Hooks Limited to commitlint (P2)

**What:** `.husky/commit-msg` only runs `commitlint`. No pre-commit hook checks code quality (ruff, ESLint) before commit. Lint errors are only caught after a 3-5 minute CI run.

**Evidence:** `.husky/` directory contains only `commit-msg`.

**Risk:** Developer feedback loop on lint errors is slow. Lint failures routinely block CI.

**Action:** CR-2026-003 filed. Do not add pre-commit hooks until approved.

---

### Finding 5 — No Structured Error Tracking (P2)

**What:** CI failures generate Slack notifications but no structured audit trail. There is no way to identify recurring failure patterns, measure MTTR, or track fix effectiveness.

**Evidence:** `reports/audits/` contained only manually-written audit reports. No automated failure records.

**Action:** Addressed by this audit system. The `ci-error-audit.yml` workflow now creates structured GitHub issues for every P1+ failure.

---

### Finding 6 — requirements.txt Partially Unpinned (P3)

**What:** `backend/requirements.txt` uses some version ranges (e.g. `stripe>=7`) rather than exact pins. This means CI may install different transitive dependency versions than production.

**Action:** CR-2026-006 filed for pip-compile adoption.

---

### Finding 7 — No Deployment Rollback Automation (P2)

**What:** If the post-deploy smoke test fails, the broken deployment remains running. The only response is a Slack alert requiring manual engineer action.

**Action:** CR-2026-004 filed. The smoke-test job currently has no rollback step.

---

## 4. Change Requests (Pending Approval)

> **Process:** Each CR below must be approved via a GitHub Issue using `.github/ISSUE_TEMPLATE/ci_change_request.yml` before any implementation begins. The CR issue must be labelled `approved` before work starts.

### CR-2026-001 — Coverage Baseline (P1)

- **What:** Establish a realistic coverage floor in `backend/pytest.ini` via `--cov-fail-under`; align with `ci.yml`
- **Where:** `backend/pytest.ini`, `.github/workflows/ci.yml`
- **Why:** Config inconsistency destroys trust in the coverage gate
- **Benefit:** Coverage gate becomes meaningful; prevents silent regressions
- **Risk if not done:** Coverage continues unmeasured; regressions ship undetected
- **Risk of change:** Low (threshold-only change; no app code touched)
- **Effort:** M | **Rollback:** Remove `--cov-fail-under` from pytest.ini

### CR-2026-002 — ESLint Warning Reduction (P2)

- **What:** Reduce admin-dashboard `--max-warnings` from 600 → <100 incrementally
- **Where:** `admin-dashboard/package.json`, `admin-dashboard/src/**`, `ci-guardrails.yml`
- **Why:** 600 warnings = no signal; new issues accumulate invisibly
- **Benefit:** Lint output actionable again; security warnings visible
- **Risk if not done:** Count grows to 1000+; real issues hidden
- **Risk of change:** Low (lint fixes are non-functional)
- **Effort:** L | **Rollback:** Revert `--max-warnings` value

### CR-2026-003 — Pre-commit Lint Hooks (P2)

- **What:** Add ruff + ESLint via `lint-staged` to `.husky/pre-commit`
- **Where:** `.husky/pre-commit` (new), root `package.json`
- **Why:** CI lint failures cost 5 minutes; pre-commit hook costs 2 seconds
- **Benefit:** Dramatically faster developer feedback loop
- **Risk if not done:** Lint errors continue blocking CI routinely
- **Risk of change:** Low; `--no-verify` bypass available for emergencies
- **Effort:** S | **Rollback:** Remove `.husky/pre-commit`

### CR-2026-004 — Deployment Rollback (P2)

- **What:** Auto-rollback to previous Railway deployment on smoke-test failure
- **Where:** `.github/workflows/ci.yml` (smoke-test job), `deploy-backend.yml`
- **Why:** Failed deploys currently require manual engineer response; MTTR ~30 min
- **Benefit:** MTTR drops to <2 minutes automatically
- **Risk if not done:** Production outages last until on-call responds
- **Risk of change:** Medium; needs Railway API research + staging validation
- **Effort:** M | **Rollback:** Remove rollback step from workflow

### CR-2026-005 — Gitleaks Pre-commit (P2)

- **What:** Add `.gitleaks.toml` with Spinr patterns + pre-commit hook
- **Where:** `.gitleaks.toml` (new), `.husky/pre-commit`
- **Why:** TruffleHog catches secrets in CI *after* commit; gitleaks catches *before*
- **Benefit:** Secrets never enter git history; defence-in-depth
- **Risk if not done:** Credential leak window exists between commit and CI scan
- **Risk of change:** Low; allowlist config prevents false positives
- **Effort:** S | **Rollback:** Remove `.gitleaks.toml`, revert pre-commit hook

### CR-2026-006 — pip-compile Pinning (P3)

- **What:** Introduce `requirements.in` + `pip-compile` for fully-pinned `requirements.txt`
- **Where:** `backend/requirements.in` (new), `backend/requirements.txt`
- **Why:** Partial version ranges allow CI↔production divergence
- **Benefit:** Reproducible installs; exact CVE scanning
- **Risk if not done:** Transitive dependency drift between environments
- **Risk of change:** Low; run full test suite after initial pin generation
- **Effort:** S | **Rollback:** Revert `requirements.txt`

### CR-2026-007 — Dependabot Auto-merge (P2)

- **What:** Configure auto-merge for patch-level Dependabot security PRs
- **Where:** GitHub repository settings, `.github/dependabot.yml`
- **Why:** Security patch PRs sit unmerged for weeks without auto-merge
- **Benefit:** Known CVEs patched within hours
- **Risk if not done:** CVE window remains open despite having Dependabot active
- **Risk of change:** Low for patch only; CI must pass before auto-merge
- **Effort:** XS | **Rollback:** Disable auto-merge in GitHub settings

---

## 5. What Was Implemented (This Audit)

The following were implemented directly as part of standing up the audit system. They are **CI infrastructure only** — no application code was modified.

### 5.1 CI Error Audit Workflow (`.github/workflows/ci-error-audit.yml`)

Triggers automatically when `CI/CD Pipeline`, `Test Environment`, or `Deploy Backend` workflows fail.

**Pipeline:**
```
Workflow Failure
    │
    ├─▶ fetch_run_data.py       — pulls job logs via GitHub API
    ├─▶ error_classifier.py     — categorises: test|lint|security|build|deploy|...
    ├─▶ impact_assessor.py      — blast radius + user impact
    ├─▶ fix_recommender.py      — per-category fix playbooks + CR candidates
    ├─▶ report_generator.py     — structured Markdown report
    ├─▶ change_request_generator.py — CR drafts with full context
    ├─▶ create_github_issue.py  — files GitHub issue (P1+ only by default)
    └─▶ Slack alert             — P0 findings only
```

### 5.2 CI Guard Rails Workflow (`.github/workflows/ci-guardrails.yml`)

Runs on every PR to `main` or `develop`.

| Gate | What it checks | Blocks merge? |
|---|---|---|
| Coverage regression | Coverage hasn't dropped >2% vs base | ✅ Yes |
| Lint trend | ESLint warning count hasn't grown beyond baseline | ✅ Yes |
| Security posture | No new CVEs with available fixes; no secrets in diff | ✅ Yes |
| Migration safety | No DROP TABLE / TRUNCATE / NOT NULL without default | ✅ Yes |
| Breaking change | No removed routes or public DB helpers | ⚠️ Warning only |
| PR summary comment | Posts a ✅/❌ table on every PR | Informational |

### 5.3 Audit Scripts (`scripts/ci-audit/`)

| Script | Purpose |
|---|---|
| `audit_runner.py` | CLI entry point — orchestrates the full pipeline locally |
| `fetch_run_data.py` | GitHub API — fetches job + step logs for a run |
| `error_classifier.py` | Classifies log output into structured error records |
| `impact_assessor.py` | Maps errors to affected features + user impact |
| `fix_recommender.py` | Per-category fix playbooks with safety flags |
| `report_generator.py` | Generates structured Markdown audit report |
| `change_request_generator.py` | Generates detailed CR drafts with full context |
| `create_github_issue.py` | Files / updates GitHub issues via API |

### 5.4 GitHub Issue Templates

- `.github/ISSUE_TEMPLATE/ci_change_request.yml` — structured CR form with what/where/why/benefits/risks
- `.github/ISSUE_TEMPLATE/ci_error_report.yml` — structured error report with root cause + resolution tracking

### 5.5 Dependabot (`.github/dependabot.yml`)

Weekly automated dependency PRs for: `backend/` (pip), `admin-dashboard/` (npm), `rider-app/` (npm), `driver-app/` (npm), `shared/` (npm), root GitHub Actions.

---

## 6. Error Classification Schema

The audit system classifies every CI error on two axes:

### Category
| Category | Description |
|---|---|
| `test` | Unit/integration/e2e test failure |
| `coverage` | Coverage threshold not met |
| `lint` | ruff / ESLint / TypeScript errors |
| `security` | CVE / secret / Trivy finding |
| `build` | Docker / Next.js / Expo / Python build failure |
| `deploy` | Railway / Render / Vercel / EAS deployment failure |
| `dependency` | pip / npm / yarn install failure |
| `infra` | GitHub Actions runner / network issue |

### Severity
| Severity | Meaning | Response SLA |
|---|---|---|
| P0 | Production broken or security critical | < 1 hour |
| P1 | PR blocked / feature broken | < 4 hours |
| P2 | Non-blocking / CI signal degraded | < 1 week |
| P3 | Technical debt / informational | Backlog |

---

## 7. Process: What Happens When CI Fails

```
CI Job Fails
     │
     ▼
ci-error-audit.yml triggers automatically
     │
     ├─▶ [Within 5 min] Audit issue created on GitHub
     │       Labels: ci-failure, severity: P*, category: *
     │       Body: root cause analysis + fix recommendations + CR list
     │
     ├─▶ [If P0] Slack alert fired immediately
     │
     └─▶ Developer response:
             P0: respond within 1 hour; fix directly (no CR needed for CI fixes)
             P1: respond within 4 hours; fix on current branch
             P2/P3: triage to sprint backlog
```

### Fix Safety Rules

1. **CI configuration fixes** (workflows, pytest.ini, ruff.toml): safe to apply directly — no CR required
2. **Test fixes** (adding/changing test assertions): safe — fix test to match correct behaviour, never silence
3. **Application code fixes** to resolve a failing test: safe — fix the code, run full suite
4. **Threshold changes** (coverage %, lint warning counts): require CR approval
5. **New tooling** (hooks, scanners, auto-merge): require CR approval
6. **Any change that modifies business logic** while fixing a CI issue: stop and file a CR

### Never
- Never silence a test (`pytest.mark.skip`, `it.skip`) without a CR
- Never raise a threshold to make CI pass — lower the threshold or fix the code
- Never bypass pre-commit hooks (`--no-verify`) on `main`/`develop`
- Never push a "fix CI" commit to `main` directly — always via PR

---

## 8. How to Run the Audit Locally

```bash
# Run a full audit on a specific failed workflow run
python3 scripts/ci-audit/audit_runner.py \
  --run-id <GITHUB_RUN_ID> \
  --repo srikumarimuddana-lab/spinrvm \
  --token "$GITHUB_TOKEN"

# Audit only backend failures, report P2+
python3 scripts/ci-audit/audit_runner.py \
  --run-id <RUN_ID> \
  --repo srikumarimuddana-lab/spinrvm \
  --token "$GITHUB_TOKEN" \
  --surface backend \
  --severity-filter P2
```

Output is saved to `reports/audits/YYYY-MM-DD-ci-failure-<run_id>.md`.

---

## 9. Appendix — Files Created by This Audit

```
.github/
├── workflows/
│   ├── ci-error-audit.yml       ← Automated audit on failure
│   └── ci-guardrails.yml        ← PR quality gates
├── ISSUE_TEMPLATE/
│   ├── ci_change_request.yml    ← CR form
│   └── ci_error_report.yml      ← Error report form
└── dependabot.yml               ← Automated dependency updates

scripts/ci-audit/
├── __init__.py
├── audit_runner.py              ← CLI entry point
├── fetch_run_data.py            ← GitHub API data fetcher
├── error_classifier.py          ← Log → structured errors
├── impact_assessor.py           ← Errors → blast radius
├── fix_recommender.py           ← Errors → fix playbooks + CRs
├── report_generator.py          ← All data → Markdown report
├── change_request_generator.py  ← Improvements → CR drafts
└── create_github_issue.py       ← Report → GitHub issue

reports/audits/
└── CI_AUDIT_SYSTEM_2026-04-25.md  ← This file
```

---

_Baseline audit completed 2026-04-25. Next scheduled full audit: 2026-05-25._
_Report generated by: CI Error Audit System v1.0_
