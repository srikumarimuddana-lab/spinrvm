# Pillar 5 — Security, Compliance & Safety

> Security posture, privacy law, provincial regulation, and the passenger
> safety surface are one pillar because at Spinr they share a property: each
> is enforced by layered machinery (code invariant → hook → CI gate →
> reviewer agent → audit doc), not by policy documents alone. Threat models:
> `docs/threat-model/` (backend, rider-app, driver-app, admin-panel).

## Security model

**Identity & sessions**
- Dual auth: Firebase ID token exchange + short-lived HS256 JWT (ADR-005).
  Access tokens 15 min (rider/driver) / 1 h (admin); refresh tokens 30 days,
  SHA-256 at rest, rotated on every use.
- Trust is asymmetric by design: admin JWTs carry role/email/modules and are
  trusted; rider/driver role is re-read from the `users` table on every
  request — never from the token.
- Admin RBAC is module-granted (`require_module` / `require_super_admin`,
  `AVAILABLE_MODULES`/`ROLE_PRESETS` in `backend/routes/admin/staff.py`),
  with a dedicated reviewer agent (`spinr-admin-rbac-reviewer`) auditing the
  grant graph on every change.
- OTPs: SHA-256 at rest, 5 failures/hour → 24 h Redis lockout, dev bypass
  dead in production.

**Defense in depth, concretely**
1. Code invariants — parameterized query building with layer-owned escaping
   (`repositories/_base.py`); an inexpressible OR-predicate raises rather
   than widening a filter shared with update/delete.
2. RLS in Supabase under the API as an independent enforcement layer.
3. Pre-commit hook — secrets scan, forbidden files, PII-in-logs check,
   branch check, float-money check on every commit.
4. CI — `security-gates.yml`, gitleaks rules (incl. the SIN/bank-PII rule),
   `migration-check.yml`, dependency and lockfile drift checks.
5. Reviewer agents — `spinr-security-auditor` (OWASP + Spinr failure modes),
   plus domain auditors for money, migrations, fraud, AI guardrails.
6. Standing artifacts — threat models, `SECURITY.md`, incident reports
   (`docs/incidents/`), and the `audit-framework/` dimension checklists.

**Known-degraded controls (state them, don't imply otherwise)**
- DAST (`dast-zap-baseline.yml`) is inert scaffolding until `STAGING_URL`
  exists; pen-test posture lives in `docs/runbooks/dast-and-pentest.md`.
- No automated PR review is currently running (ACTION_ITEMS C7/C9) — manual
  auditor-agent passes are the compensating control.
- Refresh-token-theft Sentry tripwire not yet configured (C2); staff MFA
  shipped in code but rollout comms pending (C4).

## PIPEDA (federal privacy)

- **Minimization**: every field ties to a stated purpose; rider addresses
  stored only as user-saved favorites; driver address encrypted, never
  shared with riders.
- **The never-log list** (enforced by hook + reviewer agents): raw GPS
  (geohash at most), full phone numbers (`phone_last4` at most), names,
  emails (user_id instead), PANs, government IDs, exact addresses.
- **Residency**: Canadian-region Supabase; region change = compliance event
  requiring legal sign-off (runbook exists for the migration itself).
- **User rights**: export (background job, deliberately unredacted —
  ADR-009 — with the dual-approval gate tracked open as B11), self-serve
  correction, deletion via `purge_pii_retention()` scrubbing profile fields
  within 30 days while ride records stay attributable for the regulatory
  window (the recorded B18/B23 decision — no phantom "2-year hashing").
- **Breach protocol**: P0 incident, 24 h scope / 72 h Commissioner
  notification when there is real risk of significant harm
  (`docs/runbooks/data-breach.md`); the 2026-07-30 service-role-key
  exposure incident write-up in `docs/incidents/` is the house example of
  doing this honestly.

## Saskatchewan Transportation Act / SGI

- **Driver eligibility** enforced at onboarding *and every `go_online`*:
  Class 5 license, 3 years experience, clean abstract, vehicle age < 10 y +
  annual inspection, ride-share insurance endorsement, CRC + Vulnerable
  Sector Check renewed annually. Document expiry blocks Period 1+.
- **Retention floors** (override PIPEDA deletion): trip records and
  driver/vehicle linkage 7 y; pickup/dropoff GPS (not full route) 3 y;
  insurance-period transitions 7 y.
- **Tax**: GST 5% / PST 6% as separate receipt lines; T4A-compatible annual
  driver earnings (background jobs exist for both); PST determination
  recorded in `docs/compliance/`.
- **Insurance periods are the sharpest liability edge**: Period 0–3
  classification derives from ride state; transitions append-only in
  `driver_insurance_periods`; Period 2 begins at `driver_accepted` (not
  `driver_assigned` — batch offers), Period 3 requires an `in_progress`
  ride_id. `spinr-insurance-period-auditor` reviews every change touching
  this; SGI quarterly reporting mappings live in `docs/reporting/`.
- **Accessibility & dignity**: WAV support when a WAV driver is online,
  mandatory service-animal accommodation, WCAG 2.1 AA on customer surfaces
  (`spinr-accessibility-reviewer` on every UI diff).
- **Contractor classification**: control-of-work language (shifts, uniforms,
  penalties for going offline) is a re-classification risk — legal review
  before shipping anything that nudges that way.

## Passenger & driver safety surface

- SOS notifies emergency contacts and the safety team and *offers* one-tap
  911 — it never auto-dials and never claims to replace 911.
- The safety check-in background loop, emergency-contact consent
  (`sos_contact_consent`), chat moderation, share-trip, and incident
  reporting are covered in `.claude/context/domain-safety.md`; SOS incidents
  have their own runbook (`docs/runbooks/sos-incident.md`).
- `spinr-safety-sos-reviewer` audits the UX/data surface;
  `spinr-fraud-auditor` covers the abuse side (referral velocity, promo
  stacking, signup device/phone reuse, GPS plausibility).
- KPI: safety incident rate < 1/10k rides, with every incident investigated
  individually — a statistical target never closes an individual case.

## Compliance-change protocol

Any change touching driver eligibility, retention, receipts/tax, WAV or
service-animal handling, logging, or deletion flows gets
`spinr-regulatory-compliance-checker` before merge; legal-document drafts
are gated by `spinr-legal-readiness-reviewer` against real code, not
placeholder text (`/legal-check`). Where compliance and product speed
conflict, compliance wins and the product finds another path — that is the
brand promise (Pillar 2) expressed as an engineering rule.
