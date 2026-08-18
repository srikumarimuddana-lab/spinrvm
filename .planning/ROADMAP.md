# Spinr — Roadmap

*Generated: 2026-05-02 | Mode: YOLO | Granularity: Standard | Research-first: Yes*

---

## Current State

- P0 security sprint in flight on `claude/ci-error-audit-system-HPjKP` (now merged to `main` via PR #397)
- 15 backend CI failures from P0+P3 merge resolved and merged
- Admin authStore (silentRefresh) and rider walletStore test mismatches resolved
- Pre-existing CI bugs remain: G5b Gitleaks `args:`, claude-review `max_turns`, E2E auth mocking
- G4c npm-audit-admin is the only hard-blocking CI gate
- GSD planning structure initialised today (2026-05-02)
- Device testing has NOT commenced; pre-launch dev-only environment

---

## Phase 1 — P0 Security & Safety Hardening *(ACTIVE)*

**Goal**: Close all 6 P0 security/safety findings. No open P0 items when device testing commences.

**Why first**: P0 items are security and safety issues in a ride-sharing app. Shipping known vulnerabilities to device testers adds risk and creates misleading test results.

### Deliverables

| ID | Task | Area | Complexity |
|---|---|---|---|
| P0-1 | Resolve remaining HttpOnly backend cookie edge cases | Backend auth | Medium |
| P0-2 | Reduce admin JWT access token TTL to 12 hr | Backend auth | Small |
| P0-3 | **WAV dispatch** — wheelchair-accessible vehicle matching | Backend dispatch + DB + Rider UI | Large |
| P0-4 | First-rating crash fix (rider app post-ride rating) | Rider app | Small |
| P0-5 | Fare-collection state mismatch fix | Backend fares | Medium |
| P0-6 | GPS OOM fix (driver app location loop) | Driver app | Medium |
| P0-7 | SOS silent failure fix + rideStore.sos.test.ts passes | Rider app + Backend | Medium |

### WAV Dispatch (P0-3) — Must Plan First

WAV dispatch touches 5+ files and requires a DB migration. Run `/gsd-plan-phase` for this task before executing. Key files: `dispatch_service.py`, `rides.py`, `drivers.py`, `rideStore.ts`, a new migration `62_wav_dispatch.sql`.

### Success Criteria

- [ ] All 7 P0 items committed and tested
- [ ] Backend pytest full suite green (`pytest -m "not slow"`)
- [ ] `rideStore.sos.test.ts` passing
- [ ] Admin authStore tests passing
- [ ] Rider walletStore tests passing
- [ ] PR merged to `main`

---

## Phase 2 — CI Health & Dev Environment Setup *(NEXT)*

**Goal**: Achieve fully green CI on `main`. Establish the dev secrets pattern so device testers can start without rotating production keys.

**Why second**: Broken CI creates noise that masks real failures. Device testing needs a clean baseline.

### Deliverables

| ID | Task | Area |
|---|---|---|
| CI-1 | Fix G4c npm-audit-admin (pino lockfile drift) | Admin dashboard |
| CI-2 | Fix G5b Gitleaks `args:` → valid gitleaks-action input | `.github/workflows/security-gates.yml` |
| CI-3 | Fix claude-review `max_turns` invalid input | `.github/workflows/claude-review.yml` |
| CI-4 | E2E Playwright — mock admin auth (all 7 smoke tests fail due to redirect) | Admin dashboard E2E |
| ENV-1 | `.gitleaks.toml` allowlist in place (suppress dev-fixture false positives) | Repo root |
| ENV-2 | Document dev secrets setup: `.env.local` pattern + GitHub Actions dev-tier secrets | `docs/dev-setup.md` |
| ENV-3 | Verify dev backend reachable from physical devices (ngrok / Railway dev deploy) | Infrastructure |

### Success Criteria

- [ ] All CI jobs green on a fresh PR to `main`
- [ ] No red ❌ in the security-gates workflow
- [ ] E2E smoke tests green (7/7 pass)
- [ ] `docs/dev-setup.md` documents how to configure `.env.local` for device testing
- [ ] G4c security gate passes

---

## Phase 3 — Device Testing Preparation & Execution

**Goal**: Every golden path verified on physical iOS and Android devices. All 5 surfaces talk to the dev backend simultaneously.

**Why third**: Device testing is the last milestone before production. Done right, it finds real crashes and UX gaps that unit/integration tests miss.

### Deliverables

| ID | Task | Area |
|---|---|---|
| DT-1 | Seed dev database with realistic test data (riders, drivers, corporate accounts) | Backend / Supabase |
| DT-2 | Expo EAS dev build for rider + driver apps targeting dev backend | Mobile / EAS |
| DT-3 | Admin dashboard pointed at dev backend | Admin / Vercel preview |
| DT-4 | Golden path: full ride lifecycle (searching → completed) | All surfaces |
| DT-5 | Golden path: wallet top-up → pay with wallet → transaction history | Rider app |
| DT-6 | Golden path: fare split create → cancel | Rider app |
| DT-7 | Golden path: SOS trigger → emergency contact notification | Rider + Backend |
| DT-8 | Golden path: WAV ride request → WAV driver match → pickup | All surfaces |
| DT-9 | Golden path: corporate allowance ride (company pays) | Admin + Rider |
| DT-10 | Auth: login, session persist across restart, silent refresh, logout | Rider + Driver + Admin |
| DT-11 | Bug fix sprint: triage and resolve all device testing findings | All |

### Success Criteria

- [ ] All 10 golden paths verified on iOS + Android (rider) and iOS + Android (driver)
- [ ] Admin dashboard golden paths verified in browser
- [ ] Zero P0/P1 bugs open from device testing
- [ ] Test report written (device, OS version, findings, resolutions)

---

## Phase 4 — Type Safety & Code Quality

**Goal**: Eliminate `any` types from critical auth flows, complete tax compliance data, add monitoring alert.

**Why fourth**: These are P1 items — important but not blocking device testing. Addressing them after device testing ensures the surface area is stable before tightening types.

### Deliverables

| ID | Task | Area |
|---|---|---|
| Q-1 | Sentry alert: REFRESH TOKEN REUSE DETECTED → PagerDuty (~30 min, UI only) | Sentry |
| Q-2 | `shared/store/authStore.ts`: eliminate 16+ `any` types in auth flows | Shared TS |
| Q-3 | `backend/routes/drivers.py`: add `gst_registered` + `gst_bn` to T4A export | Backend |
| Q-4 | DB migration: add GST fields to `drivers` table | Backend migration |
| Q-5 | Dockerfile: pin base images with SHA256 digests | Docker |
| Q-6 | `OPEN-ITEMS-TRACKER.md`: log any HIGH/CRITICAL findings from security baselining | Repo root |

### Success Criteria

- [ ] `tsc --noEmit` passes in `shared/` with zero `any` on auth-critical paths
- [ ] T4A driver earnings export includes GST/BN fields
- [ ] PagerDuty alert fires on token reuse test event
- [ ] Dockerfiles use pinned digest images

---

## Phase 5 — Pre-Production Hardening

**Goal**: Flip security pipeline from advisory to blocking. Verify all SLAs. Complete compliance checklist.

**Why fifth**: This is the last gate before production. Everything that was advisory during development becomes a hard requirement here.

### Deliverables

| ID | Task | Area |
|---|---|---|
| SEC-1 | Flip all security gates from `continue-on-error: true` to blocking | `.github/workflows/security-gates.yml` |
| SEC-2 | Full `OPEN-ITEMS-TRACKER.md` review: resolve all HIGH/CRITICAL findings | Security |
| SEC-3 | `npm-audit` + `pip-audit` clean (zero HIGH/CRITICAL vulnerabilities) | Dependencies |
| SEC-4 | Performance baseline: run `perf_baseline.py`; compare against baselines | Backend |
| COMP-1 | Saskatchewan regulatory checklist: all items in `regulatory-sk.md` green | Compliance |
| COMP-2 | PIPEDA review: data flows, retention periods, consent text verified | Compliance |
| COMP-3 | `docs/runbooks/data-breach.md` created (required before launch per CLAUDE.md) | Documentation |
| COMP-4 | GST/PST visible on all rider receipts as separate line items | Rider app |
| OBS-1 | All Sentry domain alerts configured and tested | Observability |
| OBS-2 | Grafana/monitoring dashboard verified; oncall board active | Observability |

### Success Criteria

- [ ] All security gates blocking (no `continue-on-error: true` in security-gates.yml)
- [ ] Zero HIGH/CRITICAL security findings open
- [ ] Performance SLAs met at P95 (from REQUIREMENTS.md table)
- [ ] Saskatchewan regulatory checklist 100% green
- [ ] Data breach runbook exists
- [ ] All Sentry alerts firing correctly on test events

---

## Phase 6 — Production Deployment

**Goal**: First production deployment with zero surprises. Riders and drivers can complete full ride lifecycle in production.

**Why last**: All gates must be green before go-live. No half-deployed surfaces.

### Deliverables

| ID | Task | Area |
|---|---|---|
| REL-1 | Database migrations applied in order: `python -m backend.scripts.run_migrations` | Backend |
| REL-2 | Backend deployed to Railway from `main` | Infrastructure |
| REL-3 | Admin dashboard deployed to Vercel from `main` | Infrastructure |
| REL-4 | Expo EAS production builds for rider + driver apps (`[build]` commit) | Mobile |
| REL-5 | Production smoke test: complete one full ride lifecycle | QA |
| REL-6 | Monitor for 24 hr: Sentry zero new errors, all P95 SLAs met | Observability |
| REL-7 | Post-launch: enable branch protection requiring all security gates | GitHub |

### Success Criteria

- [ ] All 5 surfaces deployed and communicating
- [ ] Full ride lifecycle completed in production (searching → completed → receipt)
- [ ] Zero P0/P1 errors in Sentry for 24 hr after launch
- [ ] All P95 performance SLAs met in production
- [ ] Branch protection enabled on `main` requiring security gates

---

## KPI Targets at Launch

| Metric | Target |
|---|---|
| Match rate | ≥ 85% |
| Rider cancellation rate | ≤ 8% |
| Driver cancellation rate | ≤ 3% |
| Driver utilisation | ≥ 55% |
| Dispatch P95 latency | < 2 s |
| Payment success rate | ≥ 99% |
| Weekly active driver retention | ≥ 80% |
| Safety incident rate | < 1 / 10k rides |

---

## Dependency Graph

```
Phase 1 (P0) ──► Phase 2 (CI) ──► Phase 3 (Device Testing)
                                          │
                                          ▼
                                   Phase 4 (Quality)
                                          │
                                          ▼
                                   Phase 5 (Hardening)
                                          │
                                          ▼
                                   Phase 6 (Launch)
```

Phase 1 and Phase 2 can have some parallel work (CI fixes don't need P0 to close), but device testing (Phase 3) requires both P0 closed and CI green.
