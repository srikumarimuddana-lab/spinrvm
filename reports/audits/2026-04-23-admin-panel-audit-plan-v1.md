# Admin Panel — Production-Readiness Audit Plan v1

**Date:** 2026-04-23
**Branch:** `claude/review-pending-audits-Pu1aP`
**Scope:** `backend/routes/admin/` (API) + `admin-dashboard/` (Next.js 16 frontend)
**Framework:** `audit-framework/` — ground-rules.md applies
**Module scope file:** `audit-framework/modules/admin-panel.md`

> **Why this audit is separate:** admin panels are the highest-blast-radius
> attack surface. Weak admin auth = full data breach. Bulk operations are
> irreversible. Treat every finding here with one severity step above its
> consumer-side equivalent.

---

## Prompt (paste into a fresh agent call)

```
You are running a production-readiness audit of the Spinr admin panel —
BOTH the backend admin API (backend/routes/admin/) AND the Next.js
admin-dashboard/ frontend.

CONTEXT
- Read CLAUDE.md for the admin-specific JWT trust model: admin JWTs are
  FULLY TRUSTED (role+email+modules in claims). This is the opposite of
  rider/driver tokens. Compromise of the signing key or a single admin
  session = complete breach. Audit accordingly.
- Read audit-framework/ground-rules.md and audit-framework/modules/admin-panel.md.
- The admin-panel module file contains an explicit security checklist
  (MFA, IP allowlist, audit logging, RBAC, PII gating) — use it verbatim.

SCOPE
Backend API:
  backend/routes/admin/__init__.py
  backend/routes/admin/auth.py           ← admin login, MFA, session
  backend/routes/admin/analytics.py
  backend/routes/admin/documents.py      ← driver doc review (PII-heavy)
  backend/routes/admin/drivers.py        ← approve/suspend — IRREVERSIBLE
  backend/routes/admin/faqs.py
  backend/routes/admin/maintenance.py
  backend/routes/admin/messaging.py      ← bulk broadcast risk
  backend/routes/admin/monitoring.py
  backend/routes/admin/promotions.py
  backend/routes/admin/rides.py          ← refunds, cancellations
  backend/routes/admin/service_areas.py
  backend/routes/admin/settings.py       ← Stripe/Twilio/Maps key rotation
  backend/routes/admin/staff.py          ← admin account management
  backend/routes/admin/subscriptions.py
  backend/routes/admin/support.py
  backend/routes/admin/users.py          ← rider/driver CRUD (PII-heavy)
  backend/routes/admin/vehicle_fleet.py
  backend/routes/admin/wallet.py         ← $$$ moves

Frontend:
  admin-dashboard/src/        ← Next.js 16 app
  admin-dashboard/playwright/ ← E2E specs
  admin-dashboard/package.json

DIMENSIONS TO RUN
  01 Feature completeness     — every admin page navigable, no stubs
  02 Authentication           — CRITICAL priority; MFA required
  03 Encryption & secrets
  04 Input validation         — bulk ops with bad input = disaster
  09 Test coverage            — admin actions rarely tested
  10 Error handling           — never leak stack traces
  11 Security headers / CORS  — dashboard should be IP-restricted
  12 Compliance (PII/PCI)
  14 Performance              — bulk queries can down the DB
  15 Accessibility            — web-based, so WCAG 2.1 applies
  (Skip 05 mobile UX, 06 GPS, 07 consumer state machine, 08 consumer payments,
   13 mobile push, 16 i18n — unless admin panel is localised.)

SEVERITY SCALE
  CRITICAL | HIGH | MEDIUM | LOW | PASS | RECOMMENDATION
  Apply one severity step UP from the equivalent consumer finding because
  blast radius is organisation-wide.

DELIVERABLE
  reports/audits/2026-04-23-admin-panel-v1.txt

GROUND RULES specific to admin
- MFA NOT enforced on admin login = CRITICAL
- Admin session > 30 min idle timeout = HIGH
- Any admin route that only checks `is_admin=True` without role-level gating = HIGH
- Admin action without an immutable audit-log row (who/what/when/record) = HIGH
- Bulk mutation (e.g. suspend-all-drivers-in-city) without a second-factor
  confirmation = CRITICAL
- Admin can delete own audit log entries = CRITICAL
- PII export endpoint without rate limit + approval flag = HIGH
- Stack trace leaked to admin UI on 500 = MEDIUM
- Next.js API route proxying directly to Supabase with anon key = CRITICAL
- CORS on backend/routes/admin/* allowing non-admin origins = CRITICAL
```

---

## Branch Strategy

```bash
git checkout claude/review-pending-audits-Pu1aP

# Remediation branches after findings are tallied
git checkout -b fix/admin-p0-critical
git checkout -b fix/admin-p1-before-beta
git checkout -b fix/admin-p2-before-launch
git checkout -b fix/admin-p3-hardening
```

---

## Pre-Audit Scans

```bash
# 1. Frontend dep vulnerabilities
cd admin-dashboard && npm audit 2>&1 | tail -20

# 2. Backend admin-route specific lint
cd backend && ruff check routes/admin/ 2>&1 | tail -20

# 3. TypeScript errors in dashboard
cd admin-dashboard && npx tsc --noEmit 2>&1 | tail -20

# 4. Secrets in admin-dashboard
grep -rn "sk_live_\|SUPABASE_SERVICE_ROLE\|NEXT_PUBLIC_.*KEY" admin-dashboard/ \
  --include="*.ts" --include="*.tsx" --include="*.mjs" -l

# 5. Admin routes missing @require_admin or equivalent guard
grep -rn "^@router\." backend/routes/admin/ --include="*.py" -A 1 | \
  grep -v "require_admin\|admin_only\|Depends"

# 6. Direct Supabase service-role usage from admin-dashboard (should be via API)
grep -rn "service_role\|SERVICE_ROLE" admin-dashboard/src/ \
  --include="*.ts" --include="*.tsx"

# 7. Bulk mutation endpoints (need second-factor)
grep -rn "bulk\|batch\|delete_many\|update_many" backend/routes/admin/ \
  --include="*.py"

# 8. Missing audit_logger calls on admin mutations
grep -rn "POST\|PUT\|PATCH\|DELETE" backend/routes/admin/ --include="*.py" | wc -l
grep -rn "audit_logger\|log_admin_action" backend/routes/admin/ \
  --include="*.py" | wc -l
# If the first number >> the second, audit logging is spotty — flag it.

# 9. Playwright E2E coverage of admin flows
ls admin-dashboard/playwright/ admin-dashboard/e2e/ 2>/dev/null
cd admin-dashboard && grep -rn "test(" playwright/ e2e/ 2>/dev/null | wc -l

# 10. Next.js security config
cat admin-dashboard/next.config.ts | head -60
grep -rn "Content-Security-Policy\|X-Frame-Options\|Strict-Transport" \
  admin-dashboard/ --include="*.ts" --include="*.mjs"
```

---

## Phase Breakdown

| Phase | Dimensions | Focus | Est. |
|-------|------------|-------|------|
| A | 02, 03, 04 | **Admin auth (MFA/session/IP)** + secrets + input validation | 7–10 h |
| B | 01, 11, 12 | Feature map + CORS/headers/CSP + PII gating + audit logging | 6–9 h |
| C | 09, 10, 14 | Playwright + Vitest coverage + error surfaces + bulk-query perf | 6–9 h |
| D | 15 | WCAG 2.1 AA pass on admin-dashboard (web) | 3–4 h |
| **Total** | **10** | | **~22–32 h** |

---

## Admin-Specific Security Checklist (from modules/admin-panel.md — must-pass)

### Authentication
- [ ] MFA required — TOTP or hardware key (SMS-only is NOT sufficient)
- [ ] Session idle timeout ≤ 30 minutes
- [ ] Admin login IP-restricted to office/VPN range (documented allow-list)
- [ ] Failed admin login generates an alert (Slack/email/PagerDuty)
- [ ] Password policy: ≥ 16 chars, bcrypt cost ≥ 12

### Authorisation (RBAC)
- [ ] Roles exist: super-admin / support / finance / operations
- [ ] Support role cannot read payment card data or payout bank account
- [ ] Finance cannot modify driver status
- [ ] Every mutation endpoint checks role — not just `is_admin=True`
- [ ] `modules` claim in admin JWT is enforced server-side, not just UI-hidden

### Audit Logging
- [ ] Every admin mutation writes a row: who / what / when / which record / before+after
- [ ] Audit rows are append-only (no UPDATE/DELETE permission for admin role)
- [ ] Bulk operations require second-factor confirmation
- [ ] Audit log exported to cold storage outside primary DB

### Data Access
- [ ] PII fields (licence#, bank, SIN if stored) require elevated permission to view
- [ ] "View as rider/driver" shows only the user's own view, never raw DB rows
- [ ] Search pagination enforced; no unbounded "export all users"
- [ ] Finance-gated reports (earnings, tax, T4A) behind finance role

### Dashboard Frontend (admin-dashboard/)
- [ ] Next.js API routes NEVER embed `SUPABASE_SERVICE_ROLE_KEY` — always call backend
- [ ] CSP present and strict (no `unsafe-inline` in production)
- [ ] HSTS, X-Frame-Options DENY, X-Content-Type-Options nosniff
- [ ] Cookies: HttpOnly, Secure, SameSite=Strict
- [ ] Session tokens NOT in localStorage (XSS exfiltration risk)
- [ ] Sentry PII scrubbing enabled (sentry.client.config.ts, sentry.server.config.ts)
- [ ] Playwright E2E covers login + MFA + at least one destructive action

---

## Output Schema (REQUIRED)

Every finding in `2026-04-23-admin-panel-v1.txt` MUST also appear in a
machine-readable block at the end of the file under a `===FINDINGS-YAML===`
fence. Admin-panel findings carry elevated blast radius — the schema captures
that explicitly.

```yaml
===FINDINGS-YAML===
- id: A-02-1                             # A = admin · <dim> · <seq>
  severity: CRITICAL                     # CRITICAL|HIGH|MEDIUM|LOW|PASS|RECOMMENDATION
  dimension: 02                          # 01..16 (+17..22 if adopted)
  title: "Admin login has no MFA enforcement"
  evidence:
    file: backend/routes/admin/auth.py
    lines: [88, 124]
    snippet: "if password_ok: issue_admin_jwt(...)"      # ≤5 lines
  root_cause: "MFA step absent; only password checked."
  impact: "Single-factor compromise = full-tenant breach (every rider/driver)."
  blast_radius: org-wide                 # self | user | org-wide
  fix:
    - "Add TOTP enrolment + challenge step post-password."
    - "Reject issued JWTs that lack an `mfa_verified` claim."
  effort_hours: 16
  regression_test: "admin-dashboard/playwright/admin-mfa.spec.ts"
  sprint: P0
  owners: [backend, admin]
  regulations: [PIPEDA, PCI-DSS, SOC2]
  confidence: high
  duplicate_of: null
===END-FINDINGS-YAML===
```

### Mandatory rules
- No `CRITICAL` or `HIGH` without `file` + `lines` + `snippet`. Otherwise
  downgrade to `RECOMMENDATION` and mark `confidence: low`.
- Admin severity ladder is **one step up** from consumer-side:
  - Consumer CRITICAL → Admin CRITICAL
  - Consumer HIGH → Admin CRITICAL if exploit yields bulk PII or bulk mutation
  - Consumer MEDIUM → Admin HIGH if it affects admin-only routes
- Live secrets found in admin code: redact in `snippet`; never paste the literal.
- Prior-audit dedup: grep
  `reports/audits/2026-04-18-driver-app-*.txt reports/audits/2026-04-19-rider-app-v1.txt`
  for same `file:line`; set `duplicate_of` if rediscovered.

### Done-sentinel
```
===AUDIT-COMPLETE=== dimensions_run=<N> findings=<N> critical=<N> high=<N>
```

### Self-review pass (run before emitting the sentinel)
1. Every mutation endpoint under `backend/routes/admin/*` → did you produce at
   least one `PASS` or finding on (a) role check (b) audit log row? If not, you
   haven't read it — go back.
2. Every destructive admin action → is `blast_radius: org-wide` tagged?
3. Every PII-viewing endpoint → does it appear in a `PIPEDA`-tagged finding?
4. `admin-dashboard/src/` direct-to-Supabase usage with service-role key →
   CRITICAL if any hit.

---

## Remediation Sprints

| Sprint | Priority | File to create |
|--------|----------|----------------|
| P0 | CRITICAL | `reports/remediation/admin-P0-critical-fix-now.md` |
| P1 | HIGH     | `reports/remediation/admin-P1-before-beta.md` |
| P2 | MEDIUM   | `reports/remediation/admin-P2-before-launch.md` |
| P3 | LOW      | `reports/remediation/admin-P3-hardening.md` |
| P4 | RECOMMEND | `reports/remediation/admin-P4-future-features.md` |

Use `audit-framework/templates/remediation-group.md` as skeleton.
