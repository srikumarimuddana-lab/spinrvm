# Phase E · P2 — Admin Before-Launch (Phase E Findings)

These MEDIUM severity findings from the Phase E audit must be resolved before public launch. They cover CI gate enforcement, dependency hygiene, supply-chain controls, observability gaps, data-residency, and web platform hardening.

Source audit: `reports/audits/2026-04-26-admin-panel-v2-phase-e.txt`
Branch: `claude/plan-deferred-tasks-qtT8I`

**Estimated total effort:** ~18–28 hours.

---

## Already Implemented in This Session

| Finding | Title | Status |
|---------|-------|--------|
| [17-1] | E2E added to PRs as non-blocking | ✅ Fixed (continue-on-error on PR, blocking on main) |
| [17-3] | npm audit non-blocking → blocking | ✅ Fixed: `continue-on-error: false` in security-gates.yml |
| [19-1] | dompurify CVE via jspdf | ✅ Already resolved (dompurify 3.4.1 installed) |
| [19-2] | postcss XSS via Tailwind | ✅ Already resolved (postcss 8.5.10 installed) |
| [19-3] | hono JSX XSS | ✅ Already resolved (hono 4.12.15 installed) |
| [20-2] | .npmrc audit=true | ✅ Fixed: added audit=true, audit-level=high, registry pin |
| [21-1] | Sentry blockAllMedia | ✅ Fixed |
| [21-2] | Sentry tracesSampleRate | ✅ Fixed (raised to 1.0) |
| [21-4] | Sentry surface/domain tag | ✅ Fixed |
| [22-3] | Analytics URL ID scrubbing | ✅ Fixed: beforeSend in layout.tsx |
| [23-4] | Permissions-Policy expanded | ✅ Fixed: added payment=(), usb=(), interest-cohort=() |
| [23-5] | Referrer-Policy: same-origin | ✅ Fixed |
| [23-7] | X-XSS-Protection: 0 | ✅ Fixed |

---

## A-PE-P2-1 · Backend Must Set HttpOnly Cookie (No Client-Side Cookie Writes)

**What's wrong:** `admin-dashboard/src/store/authStore.ts:18-28` — JavaScript `document.cookie` cannot set `HttpOnly`. Today the admin cookie is JS-readable, making it stealable via XSS. Only the backend `Set-Cookie` response header can enforce HttpOnly.

**Finding:** `[18-2]` · Severity: MEDIUM · Risk score: 12 · Regulations: OWASP A02

**File to fix:** `backend/routes/admin/auth.py` (login + refresh response headers) + `admin-dashboard/src/store/authStore.ts`

**How to fix:**
1. Backend login/refresh set `Set-Cookie: admin_access=...; HttpOnly; Secure; SameSite=Strict; Path=/admin` in the HTTP response (not in JSON body for the cookie portion).
2. Frontend stops calling `document.cookie` entirely; reads the token from memory only.
3. Pairs with A-PE-P1-1 (refresh token cookie) and A-PE-P1-2 (SameSite=Strict) — ship together.

**Effort:** 1 h verification + backend wiring (covered by A-PE-P1-1) · **Audit ref:** 18-2

---

## A-PE-P2-2 · CSRF Protection on State-Changing Admin Endpoints

**What's wrong:** Admin uses bearer-token auth. Once SameSite=Strict lands (A-PE-P1-2), CSRF risk drops sharply but isn't zero — a compromised same-site subdomain can still cross. Defence-in-depth: double-submit CSRF token on POST/PUT/DELETE.

**Finding:** `[23-6]` · Severity: MEDIUM · Risk score: 9 · Regulations: OWASP A01

**File to fix:** `backend/routes/admin/auth.py` (issue CSRF cookie on login) + `admin-dashboard/src/lib/api.ts` (Axios interceptor)

**How to fix:**
1. Backend login sets a `Set-Cookie: admin_csrf=<random>; SameSite=Strict; Path=/admin` (NOT HttpOnly — JS must read it).
2. Axios interceptor in `api.ts` reads `admin_csrf` cookie and adds `X-CSRF-Token` header on every mutating request.
3. Backend dependency validates `X-CSRF-Token == admin_csrf cookie value`.

**Effort:** 4–6 h · **Audit ref:** 23-6

---

## A-PE-P2-3 · ESLint Major Version Upgrade (9 → 10)

**What's wrong:** ESLint 9.39.4 is current on the v9 track. ESLint 10 ships new security and regex-injection rule categories.

**Finding:** `[19-4]` · Severity: HIGH (tooling) · Risk score: 12

**File to fix:** `admin-dashboard/package.json`

**How to fix:** `npm install eslint@^10 --save-dev`; run `npm run lint`; triage new violations within the `--max-warnings 600` budget.

**Blocker (2026-04-27):** `eslint-config-next@16.2.4` bundles `eslint-plugin-react` that calls an internal ESLint API removed in v10 (`linter.js:497` / `createRuleListeners`). Installing ESLint 10 crashes the linter with an unhandled TypeError. Retry when `eslint-config-next` ships an ESLint-10-compatible release. Track: https://github.com/vercel/next.js/issues — search "eslint 10".

**Effort:** 2–3 h (after blocker clears) · **Audit ref:** 19-4

---

## A-PE-P2-4 · uuid Moderate Vulnerabilities

**What's wrong:** `uuid` 13.0.0 has 3 moderate-severity buffer-bounds CVEs in v3/v5/v6 (GHSA-w5hq-g745-h8pq). Admin uses uuid for request-correlation IDs.

**Finding:** `[19-5]` (moderate) · Severity: MEDIUM

**File to fix:** `admin-dashboard/package.json`

**How to fix:** `npm install uuid@^14` and update any `import { v4 }` call sites. Breaking changes: v7 API added, v3/v5/v6 generators changed internally.

**Effort:** 1 h · **Audit ref:** 19-5

---

## A-PE-P2-5 · Sentry DSN Region — US Ingestion for Canadian PII

**What's wrong:** Sentry SaaS default ingestion is US. Sending Canadian admin telemetry to US infrastructure is a PIPEDA residency question even when PII is scrubbed.

**Finding:** `[22-2]` · Severity: MEDIUM · Risk score: 12 · Regulations: PIPEDA, Data Residency

**File to fix:** Legal/ops decision: switch to Sentry EU + PII scrub (preferred), or file a DPA addendum.

**How to fix:** Verify the configured DSN's region (`o<org-id>` prefix). If US: either (a) switch to Sentry EU (`https://o<id>.ingest.de.sentry.io`), or (b) add a DPA addendum to `docs/vendor-register.md` confirming scrubbed data is residency-exempt.

**Effort:** 2–3 h (legal review + DSN swap) · **Audit ref:** 22-2

---

## A-PE-P2-6 · Structured Server-Side Logging (pino)

**What's wrong:** Admin uses bare `console.error` for server-side error paths. No structured JSON logger with correlation IDs.

**Finding:** `[21-5]` · Severity: MEDIUM · Risk score: 8 · Regulations: SOC2

**File to fix:** New `admin-dashboard/src/lib/logger.ts` + update server components / route handlers

**How to fix:** Adopt `pino` (or Vercel Edge Logger primitives). Emit structured JSON with `request_id`, `surface=admin`, and `domain` per CLAUDE.md observability conventions.

**Status:** Done. `pino@10` installed; `src/lib/logger.ts` created with `surface=admin`, `env`, `LOG_LEVEL` override. All three BFF auth routes (login, refresh, logout) now emit structured JSON with `request_id`, `domain=auth`, `duration_ms`, and appropriate log levels (`debug`/`info`/`warn`/`error`). No PII logged (only role-neutral status codes and boolean flags). Client-side `console.error` in `authStore.ts` is intentionally left as-is — it runs in the browser, outside server-side log collection.

**Effort:** 4–6 h · **Audit ref:** 21-5 ✅

---

## A-PE-P2-7 · Vercel PR Deploy Branch Protection

**What's wrong:** No documented rollback runbook and no branch protection requiring security-gates results before Vercel deploy.

**Finding:** `[17-4]` · Severity: MEDIUM · Risk score: 12 · Regulations: SOC2

**How to fix:** 
1. Add `docs/runbooks/admin-rollback.md` (one page: Vercel → Deployments → revert).
2. Enable GitHub branch protection on `main` requiring the `security-gates` summary check before merge.

**Effort:** 1–2 h · **Audit ref:** 17-4

---

## Checklist

- [x] A-PE-P2-1 Backend HttpOnly cookie (18-2) — done; access token set HttpOnly by BFF routes; `document.cookie` writes removed from authStore
- [x] A-PE-P2-2 CSRF double-submit token (23-6) — already implemented (refresh/route.ts validates csrfCookie === csrfHeader; api.ts sends X-CSRF-Token)
- [ ] A-PE-P2-3 ESLint 9 → 10 upgrade (19-4)
- [x] A-PE-P2-4 uuid 13 → 14 (19-5) — upgraded to ^14.0.0
- [ ] A-PE-P2-5 Sentry DSN region / DPA addendum (22-2) — tracked in docs/vendor-register.md
- [x] A-PE-P2-6 Structured server-side logging (21-5) — pino@10 + src/lib/logger.ts; all BFF auth routes emit structured JSON with request_id, domain, duration_ms
- [x] A-PE-P2-7 Branch protection + rollback runbook (17-4) — docs/runbooks/admin-rollback.md created; GitHub branch protection settings documented in §5
