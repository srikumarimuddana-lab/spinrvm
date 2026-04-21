# Rider App — Production-Readiness Audit Plan v1

**Date:** 2026-04-19
**Branch:** `claude/rider-app-audit-iVxpH`
**Scope:** `rider-app/` · `shared/` · related `backend/routes/` (auth, rides, payments, addresses, wallet, fare-split, promo, notifications)
**Framework:** `audit-framework/` — 16 dimensions, ground-rules.md applies
**Follows:** Driver App Audit v4 (2026-04-18) — same methodology, rider-specific risks

---

## Ground Rules (read before starting any phase)

- OTP is **4 digits by design** — compensating controls must be confirmed, NOT reflagged
- Hard-coded dev values (`"1234"`, `sk_test_*`) → severity LOW/RECOMMENDATION only
- Severity scale: CRITICAL · HIGH · MEDIUM · LOW · PASS · RECOMMENDATION
- Canadian market: PIPEDA, AODA, Official Languages Act (French required), PCI-DSS
- Full rules: `audit-framework/ground-rules.md`

---

## Branch Strategy

```bash
# ── AUDIT BRANCH (already created) ───────────────────────────────────────────
# All audit findings, plan files, and remediation specs live here
git checkout claude/rider-app-audit-iVxpH
git pull origin claude/rider-app-audit-iVxpH

# ── REMEDIATION BRANCHES (create one per sprint) ─────────────────────────────
# Sprint P0 — critical blockers
git checkout -b fix/rider-p0-critical-blockers
git push -u origin fix/rider-p0-critical-blockers

# Sprint P1 — before beta
git checkout -b fix/rider-p1-before-beta
git push -u origin fix/rider-p1-before-beta

# Sprint P2 — before public launch
git checkout -b fix/rider-p2-before-launch
git push -u origin fix/rider-p2-before-launch

# Sprint P3 — hardening
git checkout -b fix/rider-p3-hardening
git push -u origin fix/rider-p3-hardening

# Sprint P4 — future features
git checkout -b fix/rider-p4-future
git push -u origin fix/rider-p4-future

# ── AFTER EACH SPRINT ─────────────────────────────────────────────────────────
# Tag the sprint end for rollback reference
git tag rider-p0-complete
git push origin rider-p0-complete
```

---

## Pre-Audit Scans (run before Phase A)

```bash
# 1. Dependency vulnerabilities
cd /home/user/spinrvm/rider-app && npm audit 2>&1 | tail -10

# 2. Python backend deps
cd /home/user/spinrvm/backend && pip-audit 2>&1 | tail -10

# 3. Live secrets scan
grep -rn "sk_live_\|supabase\.co\|AAAA[A-Za-z]" \
  rider-app/ shared/ \
  --include="*.ts" --include="*.tsx" --include="*.js" -l

# 4. TypeScript errors baseline
cd /home/user/spinrvm/rider-app && npx tsc --noEmit 2>&1 | tail -20

# 5. Any/unknown type count (baseline)
grep -rn ": any\b" rider-app/app/ rider-app/store/ rider-app/hooks/ \
  --include="*.ts" --include="*.tsx" | wc -l

# 6. Magic status strings (should be centralized constants)
grep -rn "'searching'\|'driver_assigned'\|'driver_arrived'\|'in_progress'\|'completed'\|'cancelled'" \
  rider-app/app/ rider-app/store/ \
  --include="*.ts" --include="*.tsx" | wc -l

# 7. Console.log / debug artifacts
grep -rn "console\.log\|console\.warn\|debugger" \
  rider-app/app/ rider-app/store/ rider-app/hooks/ \
  --include="*.ts" --include="*.tsx" | wc -l
```

Record all results in `reports/audits/2026-04-19-rider-app-v1.txt` before reading any code.

---

## Audit Phases Overview

| Phase | Dimensions | Focus | Estimated Time |
|-------|-----------|-------|---------------|
| A | 01–04 | Feature completeness, Auth, Encryption, Input validation | 8–11 hours |
| B | 05–08 | UI/UX, Real-time, State machine, Payments | 11–16 hours |
| C | 09–12 | Tests, Error handling, Security headers, Compliance | 8–12 hours |
| D | 13–16 | Notifications, Performance, Accessibility, i18n | 9–13 hours |
| **Total** | **16** | | **~35–52 hours** |

---

## Remediation Sprints Overview

| Sprint | Priority | When | Estimated Effort |
|--------|----------|------|-----------------|
| P0 | CRITICAL | Before device testing | ~1 day |
| P1 | HIGH | Before beta launch | ~3–4 days |
| P2 | MEDIUM | Before public launch | ~5–7 days |
| P3 | LOW/HARDENING | Before scale | ~ongoing |
| P4 | RECOMMENDATION | Future roadmap | TBD |
