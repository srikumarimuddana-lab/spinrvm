# Spinr — Open Items Tracker

Living document for HIGH and CRITICAL findings from security baselining, audits, and phase reviews.
Updated at each phase gate and after any security scan run.

**Severity definitions:**
- `CRITICAL` — exploitable vulnerability or data-loss risk; blocks merge to main
- `HIGH` — significant security or quality issue; blocks phase sign-off
- `MEDIUM` — notable issue; should resolve before production
- `LOW` — advisory; log and review quarterly

---

## Baseline: Sprint 9 Audit (2026-04-09)

All 55 Fortune 100 production readiness findings resolved across 9 categories
(SEC, INF, CQ, TST, MOB, DOC, AI, COM, FEAT). Reference: `docs/audit/12_SPRINT_9_COMPLETION.md`.

**Current posture: 0 CRITICAL · 0 HIGH · 0 MEDIUM · 0 LOW open**

---

## Open — MEDIUM

_No open MEDIUM items._

---

## Open — Pre-Production Gate (Phase 5 SEC-1)

These advisory CI gates must be flipped to **blocking** before production. They are currently `continue-on-error: true` and will not fail a PR.

| ID | Severity | Workflow | Job | Line | Action required |
|----|----------|----------|-----|------|-----------------|
| OI-003 | HIGH | `security-gates.yml` | `bandit` (Python SAST) | 33 | Set `continue-on-error: false` |
| OI-004 | HIGH | `security-gates.yml` | `eslint-security` (JS SAST) | 55, 70 | Set `continue-on-error: false` |
| OI-005 | HIGH | `security-gates.yml` | `semgrep` | 90 | Set `continue-on-error: false` |
| OI-006 | HIGH | `security-gates.yml` | `pip-audit` | 115 | Set `continue-on-error: false` |
| OI-007 | MEDIUM | `ci-guardrails.yml` | Multiple jobs | 31, 49, 124, 200, 325, 543 | Review per-job; flip security-relevant ones to blocking |
| OI-008 | LOW | `claude-review.yml` | `review` | 47 | Keep `continue-on-error: true` — advisory by design |

**Note**: OI-003 through OI-006 are Phase 5 SEC-1 work. Do not flip them before device testing (Phase 3) completes — some scans produce noisy findings that need triage before becoming hard blocks.

---

## Open — Dependencies

| ID | Severity | Surface | Finding | Last checked | Notes |
|----|----------|---------|---------|--------------|-------|
| — | — | admin-dashboard | npm audit: 0 critical, 0 high, 0 moderate | 2026-05-02 | Clean |
| — | — | backend | pip-audit: not yet run against current requirements-locked.txt | — | Run in Phase 5 |
| — | — | rider-app | yarn audit: not yet run this sprint | — | Run in Phase 5 |
| — | — | driver-app | yarn audit: not yet run this sprint | — | Run in Phase 5 |

---

## Resolved

| ID | Severity | Finding | Resolved in | Date |
|----|----------|---------|-------------|------|
| SEC-001–SEC-011 | Various | Full set from initial audit | Sprints 1–9 | 2026-04-09 |
| INF-001–INF-008 | Various | Infrastructure findings | Sprints 1–9 | 2026-04-09 |
| CQ-001–CQ-006+ | Various | Code quality findings | Sprints 1–9 | 2026-04-09 |
| TST-001–TST-005 | Various | Test coverage gaps | Sprints 1–9 | 2026-04-09 |
| MOB-001–MOB-007 | Various | Mobile platform findings | Sprints 1–9 | 2026-04-09 |
| A-P0-1 | CRITICAL | Admin JWT tokens not HttpOnly (stored in localStorage) | PR #95 / #97 | 2026-04-27 |
| A-P0-2 | CRITICAL | Admin JWT TTL was 12h; reduced to 1h with silent refresh | PR #95 / #97 | 2026-04-27 |
| B-P0-1 | HIGH | First-rating crash — missing `rider_rating` column | PR #117 | 2026-04-27 |
| B-P0-2 | HIGH | Fare-collection state mismatch — `process_payment` 409 | PR #117 | 2026-04-27 |
| A-P0-3 | HIGH | GPS OOM — Python haversine loop replaced with Postgres function | PR #117 / #126 | 2026-04-27 |
| R-P0-1 | HIGH | SOS silent failure — no retry, no visual confirmation | PRs in P0 sprint | 2026-04-27 |
| L-P0-3 | HIGH | WAV dispatch missing — wheelchair-accessible vehicle matching | PR #240 | 2026-04-29 |
| B-P2-8 | MEDIUM | Docker base images unpinned — `backend/Dockerfile` | PR in P0 sprint | 2026-04-29 |
| Q-5 | MEDIUM | Root `Dockerfile` unpinned (dev/CI image) | 16606100 | 2026-05-06 |
| Q-2 | MEDIUM | `any` types in `shared/services/firebase.ts` and `shared/config/firebaseConfig.ts` | a7ae53eb | 2026-05-06 |
| OI-001 | MEDIUM | Driver-card `<div onClick>` — added `role="button"`, `tabIndex={0}`, `onKeyDown` in `monitoring/ride-panel.tsx` | claude/audit-expo-dependencies-Jt8BL | 2026-05-06 |
| OI-002 | MEDIUM | Ride-detail close button missing `aria-label` — added `aria-label="Close ride panel"` and `aria-hidden="true"` on icon in `monitoring/page.tsx` | claude/audit-expo-dependencies-Jt8BL | 2026-05-06 |

---

## How to Add a New Finding

1. Assign the next `OI-NNN` ID
2. Set severity (CRITICAL / HIGH / MEDIUM / LOW)
3. Add to the appropriate open section above
4. When resolved: move to the Resolved table with PR/date

## Phase 5 Checklist

Before production, all of the following must be true:

- [x] OI-001 resolved (driver card accessibility)
- [x] OI-002 resolved (close button aria-label)
- [ ] OI-003 through OI-006 flipped to blocking in `security-gates.yml`
- [ ] OI-007 jobs reviewed and appropriate ones made blocking
- [ ] pip-audit run against `requirements-locked.txt` — 0 HIGH/CRITICAL
- [ ] yarn audit run in rider-app and driver-app — 0 HIGH/CRITICAL
- [ ] All findings opened during Phase 3 device testing resolved or downgraded
