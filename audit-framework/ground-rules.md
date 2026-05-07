# Audit Ground Rules — Spinr

Read this before starting any audit. These rules override default security assumptions and prevent wasted findings.

---

## Non-Negotiable Rules

### 1. OTP Digit Count — 4 Digits Is Approved

The OTP is **4 digits by design**. Do NOT flag this as a security issue.

**Compensating controls that make this acceptable:**
- 3 OTP sends per minute (rate-limited via SlowAPI + Redis)
- 5 OTP verification attempts per minute
- 5 failed attempts → 24-hour brute-force lockout (Redis-backed)
- OTP expires after 5 minutes

**What to document instead:** Confirm these compensating controls are implemented and working.

---

### 2. Hard-Coded Dev Values — Intentional, Not a Bug

Hard-coded values that appear in the codebase are **intentional for the current testing phase**. These include:
- OTP bypass code `"1234"` (and `"123456"` fallback)
- Localhost URLs (`http://your-local-ip:8000`)
- Test API keys (Stripe test mode keys beginning with `sk_test_`)
- Supabase placeholder URLs in example files

**How to flag these:**
- Severity: **LOW** or **RECOMMENDATION** only — never CRITICAL or HIGH
- Always include a production-readiness migration path (e.g. "replace with environment variable at launch")

---

### 3. Scope Boundaries

Each audit is scoped to a specific module. Do not audit outside the declared scope unless a cross-module dependency is directly relevant to a finding.

| Module | Scope |
|---|---|
| driver-app | `driver-app/` · related `backend/` routes · `shared/` |
| rider-app | `rider-app/` · related `backend/` routes · `shared/` |
| backend-api | `backend/` · `shared/` |
| admin-panel | `backend/routes/admin/` · admin frontend if it exists |

---

### 4. Canadian Market Context

Spinr operates in Saskatchewan (initial market), Canada. These rules apply:

| Requirement | Detail |
|---|---|
| **PIPEDA** | Canadian federal privacy law — analogous to GDPR. Data export rights, breach notification, consent. |
| **AODA / Accessibility** | Accessibility for Ontarians with Disabilities Act — web and app accessibility. |
| **Official Languages Act** | Federal businesses must support both English and French. Flag missing French translations. |
| **PCI-DSS** | Payment Card Industry standards — no raw card data, Stripe tokenisation, HMAC webhook signatures. |
| **CRA (Canada Revenue Agency)** | T4A slips for drivers, GST/HST business number validation, BN-9 format. |
| **Phone numbers** | Must enforce `+1` country code (Canada/US) — not open to any country. |

---

### 5. Architecture Decisions — Do Not Reflag

These are known, accepted architectural decisions. Do not flag them as findings:

| Decision | Rationale |
|---|---|
| Supabase (not self-hosted Postgres) | Managed service — RLS, realtime, storage included |
| Stripe Connect (not direct payout) | PCI-DSS delegated to Stripe, driver onboarding handled |
| Firebase FCM (not direct APNS/FCM calls) | Cross-platform push, Crashlytics, App Check |
| Redis for rate limiting (in-process fallback) | Redis required in production; fallback is explicitly flagged in code |
| SlowAPI for rate limiting | FastAPI-native, Redis-backed — acceptable for current scale |
| Expo managed workflow (no bare ios/ checked in) | EAS Build generates native code; `PrivacyInfo.xcprivacy` declared via `app.config.ts` |

---

### 6. Severity Decision Guide

When uncertain about severity, ask these questions:

- **Can this crash the app or make a feature completely unusable?** → CRITICAL
- **Can this cause a data breach, financial loss, or security bypass in production?** → HIGH
- **Will a real user hit this under normal usage and be frustrated or confused?** → MEDIUM
- **Is this an inconvenience or a dev-only issue?** → LOW
- **Is this already working correctly?** → PASS
- **Is this a "nice to have" or future feature?** → RECOMMENDATION

For a quantitative score, multiply severity × blast × likelihood per the
formula in `reports/audits/OPEN-ITEMS-TRACKER.md`.

---

### 7. Incident → Audit Feedback Rule

Whenever a real-world incident (outage, near-miss, customer complaint with
security implications, vendor breach, regulatory inquiry) occurs:

1. Post-mortem author files a new row in `OPEN-ITEMS-TRACKER.md` tagged
   `source: incident-YYYY-MM-DD-slug`.
2. If the incident reveals a dimension gap (e.g., "monitoring didn't catch X"),
   append a checklist item to the relevant dimension doc within 7 days.
3. If three consecutive incidents trace back to the same dimension, that
   dimension is due for a focused re-audit (schedule within 30 days).
4. Post-mortems retained in `reports/postmortems/YYYY-MM-DD-slug.md` for ≥ 3
   years (SOC2 CC7.3).

**Why:** Audits are hypotheses about risk. Incidents are ground truth. The
framework must evolve from real events or it becomes stale.

---

### 8. Auditor Independence

The engineer or agent that wrote a remediation item must not be the sole
verifier. Rules:

- Verification runs produce a `reviewed_by` field in every YAML entry, naming a
  second party (different engineer, different agent call with independent inputs,
  or external reviewer).
- For CRITICAL/HIGH findings: the remediation PR and the verification run must
  be by different parties.
- External pen-test findings (see `docs/external-testing.md`) are always
  considered independent and may be used as the verification signal.
