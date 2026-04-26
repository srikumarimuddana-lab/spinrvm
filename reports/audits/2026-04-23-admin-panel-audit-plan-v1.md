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
| **E** | **17, 18, 19, 20, 21, 22** | **Observability · DR/BCP · Fraud ops · Reconciliation tools · Threat model · Third-party (admin view)** | **7–10 h** |
| **Total** | **16** | | **~29–42 h** |

### Phase A sub-tasks (D02–D04)
- A.1 Admin auth: MFA enforcement on `routes/admin/auth.py`; password policy (≥16 char, bcrypt cost ≥12); session idle ≤30 min; IP-allowlist middleware for admin routes; failed-login alerting.
- A.2 Secrets: separate admin JWT secret from rider/driver; admin API keys rotated ≥annually; NO `SUPABASE_SERVICE_ROLE_KEY` embedded in `admin-dashboard/` (must call backend).
- A.3 Input validation: every POST/PUT/PATCH/DELETE in `routes/admin/*` has a Pydantic schema; bulk operation bodies have explicit list caps; CSV/file imports have magic-byte + size caps.

### Phase B sub-tasks (D01, D11, D12)
- B.1 Feature completeness: inventory every `routes/admin/*.py` (19 files); confirm each has role gate (not just `is_admin=True`), input schema, audit-log write, error-sanitised responses.
- B.2 Security headers + CORS: Next.js `next.config.ts` HSTS + CSP + X-Frame-Options DENY + X-Content-Type-Options nosniff + Referrer-Policy; admin backend CORS allow-list enumerated; cookies HttpOnly/Secure/SameSite=Strict; session not in localStorage.
- B.3 Compliance: every admin mutation writes an append-only audit row (who / what / when / which record / before+after); audit log has no DELETE grant; view-as-user writes a consent + audit row; PII REVEAL actions audit-logged; DSAR fulfillment UI; 72 h breach-notification runbook linked from admin.

### Phase C sub-tasks (D09–D10, D14)
- C.1 Test coverage: Playwright E2E for login + MFA + at least one destructive action (driver suspend, promo create, bulk message); Vitest for admin UI components + role gating; backend `pytest` on `routes/admin/*` with bulk-input edge cases.
- C.2 Error handling: `routes/admin/*` never leaks stack traces; 500s carry `request_id` only; failed-login errors don't disclose whether email exists.
- C.3 Performance: every list endpoint paginated (no "export all users" without approval); bulk-mutation endpoints EXPLAIN ANALYZEd; admin-dashboard bundle size budget; data-grid virtualisation for >1000 rows.

### Phase D sub-task (D15)
- D.1 Accessibility: WCAG 2.1 AA on admin-dashboard — contrast AA 4.5:1; keyboard nav end-to-end; screen-reader labels on all interactive elements; focus order; accessible-name for every icon button; modals trap focus; forms announce errors; Dynamic Type not broken by CSS.

### Phase E sub-tasks (D17–D22) — NEW

Admin panel is the highest-blast-radius attack surface. Every E-phase
finding is evaluated at **one step up** from the consumer-side equivalent.

- E.1 **Observability (D17)**
  - Every admin action emits a structured log with `admin_id`, `action`, `target_record_id`, `before`, `after`, `request_id`.
  - Audit log + application log are separate pipelines (admin cannot tamper with their own trail).
  - Alert on: admin login from new device / new country / outside business hours; bulk-mutation fire; PII REVEAL above a threshold.
  - Admin dashboard has its own SLI — approval latency, support TTFR, data-export queue depth.
  - Output: findings per bullet in `dimensions/17-observability.md`.

- E.2 **DR / BCP (D18)**
  - Admin-originated actions (suspend driver, refund ride, create promo) work during partial outage.
  - Rollback procedure for bulk actions (e.g. "undo last bulk suspend") documented + tested.
  - Break-glass admin account (hardware key only, dual-authorisation, paged on use).
  - Admin password-reset path audited for social-engineering resistance.
  - Output: findings per bullet in `dimensions/18-dr-bcp.md`.

- E.3 **Fraud (D19) — admin side**
  - Admin-fraud analyst role + review queues exist as separate UI surface.
  - Every freeze/suspend/refund has an appeal path + audit row.
  - "View as user" impersonation is consent-gated + audit-logged + watermarked + time-limited.
  - No admin action can bypass fraud controls silently (e.g. admin cannot promo-bomb their own test accounts without a trail).
  - Output: findings per bullet in `dimensions/19-fraud.md`.

- E.4 **Financial Reconciliation (D20) — admin view**
  - Admin dashboard has a "reconciliation status" widget showing daily delta.
  - Manual adjustments by admin write to the same append-only `financial_events` table; no parallel admin-only ledger.
  - CRA / FINTRAC export endpoints produce regulator-format files (not ad-hoc CSV).
  - Segregation of duties: finance role can adjust; operations role cannot.
  - Output: findings per bullet in `dimensions/20-financial-reconciliation.md`.

- E.5 **Threat Model (D21) — admin-panel specific**
  - Scenarios to rule in or out:
    - Compromised admin token → blast radius
    - Malicious admin insider exfiltrating PII via data-export or "view-as-user"
    - Bulk-mutation abuse (suspend all drivers in a city)
    - CSRF / ClickJack on admin UI
    - Admin UI XSS → session theft
    - Broadcast-messaging abuse (CASL violation via compromised admin)
  - Each scenario: mitigation + detection + blast-radius tag.
  - Output: `docs/threat-model/admin-panel.md` + findings per bullet in `dimensions/21-threat-model.md`.

- E.6 **Third-Party Risk (D22) — admin-specific**
  - Admin-dashboard dependencies (Next.js 16, Playwright, Sentry, any UI library) — lockfile pinned, npm audit clean.
  - Admin-facing vendors (Sentry collects admin PII on errors? confirm PII scrub).
  - Admin-dashboard Docker image (if containerised) pinned + scanned.
  - Admin UI communicates with backend via authenticated API only — never embeds `SUPABASE_SERVICE_ROLE_KEY`.
  - Output: findings per bullet in `dimensions/22-third-party.md`.

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

## Regulatory Matrix (REQUIRED tagging)

Canonical reference: `audit-framework/regulatory-matrix.md`. Admin panel
findings carry amplified regulatory exposure — a single misconfigured admin
route can be the breach vector for every Canadian regulation in this table.

| ID | Where it bites in admin panel | Concrete check |
|---|---|---|
| PIPEDA | `routes/admin/users.py`, `routes/admin/documents.py`, dashboard | DSAR fulfillment UI · audit-logged PII reveal · 72 h breach-notification runbook accessible from admin |
| CPPA | Surge / promo admin, algorithm settings | Algorithmic Impact Assessment linked from admin UI · override log |
| CASL | `routes/admin/messaging.py`, broadcast tool | Consent-audience filter cannot be overridden · unsubscribe list honoured · sender-ID template locked |
| OLA | admin-dashboard i18n, admin-generated notices | Every admin-created customer-facing notice requires EN+FR before publish |
| ACA | admin-dashboard UX | WCAG 2.1 AA on admin web; accessibility feedback mechanism linked |
| AML | `routes/admin/wallet.py`, corporate admin | Threshold + suspicious-txn dashboards · FINTRAC export format · STR approval workflow |
| CRA | `routes/admin/*` payout / tax | T4A generator UI · GST/HST registration field required on corporate onboarding |
| SK-TNC | `routes/admin/drivers.py`, `service_areas.py` | Permit # CRUD · per-municipality service-area gating · accessibility-fleet % report |
| SGI | `routes/admin/documents.py` | Insurance expiry override requires dual approval + audit row |
| SK-PST | `routes/admin/settings.py`, reporting | Tax rate configurable by region · change requires finance role + audit row |
| SK-HRC | `routes/admin/support.py`, dispute | Service-animal + WAV complaint intake flow · driver-training compliance tracker |
| PCI-DSS | admin-dashboard payment screens | No PAN displayed · last-4 only · reveal button forbidden or dual-control |
| SOC2 | admin-dashboard, `routes/admin/staff.py` | Quarterly access review · MFA enforced · audit log immutable · privileged JIT |
| WCAG | admin-dashboard | Keyboard nav · screen-reader labels · contrast AA · focus order |
| SAFE-CRC/DRV/VEH | `routes/admin/drivers.py`, `documents.py` | Override of expired credential requires dual approval + justification + audit |

Untagged CRITICAL/HIGH findings will be rejected during self-review.
Admin audit must also produce a **"regulator-ready export" inventory** —
list every endpoint that produces CRA / FINTRAC / OPC data in the required
format.

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

### Worked examples (gold standard — match this exact format)

One CRITICAL finding:
```yaml
- id: A-02-1
  severity: CRITICAL
  dimension: 02
  title: "Admin login issues JWT after password-only challenge — no MFA gate"
  evidence:
    file: backend/routes/admin/auth.py
    lines: [88, 124]
    snippet: |
      if bcrypt.checkpw(password.encode(), admin_row["password_hash"].encode()):
          token = issue_admin_jwt(admin_row)
          return {"access_token": token}
  root_cause: "Password verification directly issues JWT; no TOTP/hardware challenge step."
  impact: "Single-factor compromise of any admin account = full-tenant breach (every rider, driver, payment record, promo config)."
  blast_radius: org-wide
  fix:
    - "Insert TOTP/WebAuthn challenge between password verify and JWT issue."
    - "Require `mfa_verified: true` claim in admin JWT; middleware rejects tokens without it."
    - "Add Playwright test for MFA-required login at admin-dashboard/playwright/admin-mfa.spec.ts."
  effort_hours: 16
  regression_test: "admin-dashboard/playwright/admin-mfa.spec.ts"
  sprint: P0
  owners: [backend, admin]
  regulations: [PIPEDA, PCI-DSS, SOC2]
  confidence: high
  duplicate_of: null
```

One PASS finding:
```yaml
- id: A-12-1
  severity: PASS
  dimension: 12
  title: "Driver PII column revoked from anon/authenticated roles"
  evidence:
    file: backend/migrations/32_encrypt_sensitive_fields.sql
    lines: [99]
    snippet: "REVOKE SELECT (license_number) ON TABLE drivers FROM anon, authenticated;"
  root_cause: null
  impact: null
  fix: []
  effort_hours: 0
  blast_radius: self
  regression_test: null
  sprint: null
  owners: []
  regulations: [PIPEDA, SAFE-DRV]
  confidence: high
  duplicate_of: null
```

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
