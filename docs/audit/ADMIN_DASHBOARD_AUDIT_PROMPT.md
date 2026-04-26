# Admin Dashboard — Comprehensive Audit Prompt

**Target:** `admin-dashboard/` (Next.js 16 / React 19) + `backend/routes/admin/**` + related Supabase/Redis surfaces
**Branch for this prompt:** `claude/admin-dashboard-audit-prompt-8AGYR`
**Framework alignment:** `audit-framework/modules/admin-panel.md`, `audit-framework/dimensions/*`, `audit-framework/ground-rules.md`
**Authoring context:** Canadian ride-sharing platform (Spinr). Admin panel is the highest-blast-radius surface — compromise of one admin account = complete PII/financial breach.

---

## Table of Contents

1. [Scope — What Is Considered (and Why Each Item Is In Scope)](#1-scope)
2. [Rationale — Why This Audit Is Necessary Now](#2-rationale)
3. [Benefits — What The Organization Gets Out Of It](#3-benefits)
4. [Methodology — How The Audit Is Performed](#4-methodology)
5. [Dimensions Checklist — The 16 Axes Applied To Admin](#5-dimensions)
6. [Enhancements — Making The Admin "Perfect, Functional, Secure"](#6-enhancements)
7. [Deliverables, Evidence Format, Commands](#7-deliverables)
8. [The Ready-to-Run Audit Prompt (copy/paste into a new Claude session)](#8-ready-to-run-prompt)

---

## 1. Scope

### 1.1 In-scope surfaces

| # | Surface | Path | Why in scope |
|---|---|---|---|
| A | Admin Next.js app | `admin-dashboard/` | The UI attack surface: auth flow, route guards, XSS sinks, CSRF, session storage, CSP. |
| B | Admin API routes | `backend/routes/admin/*.py` (analytics, auth, documents, drivers, faqs, maintenance, messaging, monitoring, promotions, rides, service_areas, settings, staff, subscriptions, support, users, vehicle_fleet, wallet) | Every destructive/PII-reading action lives here. |
| C | Admin auth & RBAC | `backend/routes/admin/auth.py`, `backend/routes/admin/staff.py`, JWT claims (`role`, `modules`), `backend/core/config.py` (`ADMIN_PASSWORD`, `JWT_SECRET`) | Admin JWT is **fully trusted** per CLAUDE.md — claims never re-verified against DB. Needs hardening. |
| D | Supabase RLS for admin reads | `backend/db_supabase.py`, migrations under `backend/migrations/` | Service-role key bypasses RLS; any SQL-injection-ish string interpolation is catastrophic. |
| E | Admin-facing app settings | `app_settings` table (Stripe/Twilio/Google Maps keys managed from admin UI) | Rotation-without-redeploy is a feature — must be gated, audited, encrypted at rest. |
| F | WebSocket monitoring | `admin-dashboard/src/hooks/use-monitoring-socket.ts`, `backend/socket_manager.py`, `spinr:ws:dispatch` channel | Real-time PII (rider locations, phone numbers) pushed to admins. |
| G | Shared client | `shared/` (`@spinr/shared`) insofar as admin imports it | Type drift between admin expectations and backend reality. |
| H | Build/deploy pipeline | `admin-dashboard/next.config.ts`, `sentry.{client,server}.config.ts`, Vercel deployment, `.env` handling | Source maps, secret leakage, preview-deploy auth. |
| I | E2E + unit tests | `admin-dashboard/e2e/`, `src/__tests__/`, `playwright.config.ts`, `vitest.config.ts` | Regression safety net for every finding. |
| J | Supporting infra | `backend/utils/audit_logger.py`, `backend/utils/rate_limiter.py`, `backend/utils/redis_client.py` | Admin actions must be audit-logged, rate-limited, lockable. |

### 1.2 Out-of-scope (explicitly)

- Rider-app / driver-app UI internals (already covered by `audit-framework/modules/{rider,driver}-app.md`).
- Third-party vendors' own security posture (Stripe, Twilio, Firebase) — only our integration boundary.
- Load/stress testing (covered separately by performance workstream).

### 1.3 Functional scope — every dashboard feature must be exercised

All 25 dashboard routes under `admin-dashboard/src/app/dashboard/`:
`analytics`, `audit-logs`, `cloud-messaging`, `corporate-accounts`, `disputes`, `documents`, `drivers`, `earnings`, `forecast`, `heatmap`, `monitoring`, `notifications`, `pricing`, `promotions`, `quests`, `rides`, `service-areas`, `settings`, `staff`, `subscriptions`, `support`, `surge`, `users`, `vehicle-types`, plus `/company-portal/[id]`, `/track/[rideId]`, `/login`, `/register`.

For each route the audit must answer: **Does it work end-to-end? Who can access it? What does it mutate? Is every mutation audit-logged, idempotent, reversible, and rate-limited?**

---

## 2. Rationale

### 2.1 Why the admin dashboard specifically

1. **Highest blast radius.** A single compromised admin can read every rider/driver's PII, impersonate any user, zero out wallets, push malicious FCM payloads to every device, and rotate third-party API keys. Every other surface is bounded by per-user scope; admin is not.
2. **Trusted JWT claims.** Per `CLAUDE.md`: *"admin JWTs are fully trusted (role+email+modules in claims). Rider/driver role is always re-read from the `users` table on every request; never trust the JWT role claim for non-admin tokens."* This means a leaked/forged admin token is as good as the private key — there is no DB re-check. That is a deliberate performance trade-off that must be balanced with MFA, short TTLs, IP binding, and audit logging.
3. **Settings-in-DB risk.** `app_settings` table holds Stripe secret keys, Twilio tokens, Google Maps API keys. The admin UI can rotate these without redeploy — meaning a SQL write bug or a UI CSRF becomes a full credential compromise.
4. **Bulk & irreversible operations.** Suspend-all-drivers, refund-all-rides, delete-user, approve-document, override-fare — all are one click away. A single missing confirmation/undo path can produce irreversible financial or safety incidents.
5. **Background loops share the blast.** `backend/core/lifespan.py` spawns 7 loops (surge, scheduled dispatch, payment retry, document expiry, corporate auto-topup, low-balance nudge, allowance reset). Admin settings tables feed these loops; a bad admin write propagates silently across all replicas.
6. **PII & PIPEDA (Canada).** Canadian privacy law (PIPEDA, plus Quebec Law 25) requires access logging, breach notification, data minimisation, and consent management. Admin reads of rider home addresses, phone numbers, payment methods, and ride history must all be auditable.
7. **Weak-secret fail-fast exists but isn't enough.** `backend/core/config.py` blocks `ADMIN_PASSWORD=admin123` and short `JWT_SECRET` in production, but there is no enforced MFA, no IP allowlist, no per-action step-up auth, no session revocation list.
8. **UI-layer only auth guard.** `admin-dashboard/src/app/dashboard/layout.tsx` gates access via a client-side `useAuthStore` check. There is no Next.js middleware (verified — none exists). A deep-linked dashboard route renders HTML before the redirect fires; sensitive content can leak via SSR/prefetch if any route ever switches away from pure CSR.
9. **Existing framework says it's unaudited.** `audit-framework/modules/admin-panel.md` currently reads *"Status: Not yet audited."* This prompt is the mandate to change that.

### 2.2 Threat model (one-page summary)

| Actor | Capability | Mitigations we expect the audit to validate |
|---|---|---|
| External attacker, unauthenticated | Credential stuffing on `/login`, CSRF, XSS, SSRF via admin-triggered fetches | Rate limits, CSP, SameSite cookies (or token-in-memory only), CAPTCHA, account lockout, strong password policy. |
| External attacker with phished admin password | Full takeover if no MFA | MFA (TOTP/WebAuthn) enforced; IP/device binding; email-on-new-login; anomaly alerts. |
| Low-privilege staff (support / ops) | Escalation via IDOR, module flag tampering, mass export | Module-scoped RBAC enforced **server-side per route**, not just via sidebar visibility. |
| Malicious insider | Bulk PII export, wallet drain, key rotation | Two-person rule on destructive ops, append-only audit log in separate DB/region, anomaly alerts (K > threshold / hour). |
| Compromised developer laptop | Source-map leakage, `.env` exfiltration, Sentry PII | Sourcemaps uploaded to Sentry and **not** served publicly, scrubbed Sentry `beforeSend`, `.env` gitignore lint. |
| Supply chain | Malicious npm package | `npm ci` reproducibility, lockfile review, Dependabot/`npm audit`, provenance checks on Radix/leaflet/maplibre. |

### 2.3 Why now

- Project is approaching production launch (see `READINESS_REPORT.md`, sprint completion docs 03–12 in `docs/audit/`).
- Corporate B2B layer (`CORPORATE_B2B.md`) adds real money flows managed from admin.
- Next 16 + React 19 + Tailwind 4 are all recent majors; regressions/CVEs land quickly.
- Admin panel has grown to 25+ routes without a consolidated security review.

---

## 3. Benefits

### 3.1 Security & compliance

- **Reduce breach probability.** Catch credential, CSRF, XSS, IDOR, and key-rotation-path bugs **before** go-live. Historical industry data: admin-panel compromise is involved in ≥30% of SaaS breaches disclosed 2019–2025.
- **PIPEDA / Quebec Law 25 readiness.** Produce the access-log, data-flow, and retention evidence regulators ask for after an incident.
- **PCI DSS adjacency.** Even as a merchant-of-record offloading to Stripe, admin surfaces that can read last-4 / trigger refunds still fall under SAQ-A and require logging.
- **SOC 2 Type II posture.** Produces the "change management + logical access + monitoring" artifacts auditors sample.

### 3.2 Reliability & operability

- **Zero-surprise deploys.** A green audit (+ E2E coverage it forces into existence) means the admin UI keeps working across Next/Sentry/Supabase upgrades.
- **Incident response drops from hours to minutes.** A complete, append-only admin audit log is the single biggest accelerator of forensics.
- **Fewer support tickets.** Most "the admin UI froze / I can't refund / search is broken" tickets trace to the gaps this audit surfaces (pagination, optimistic-UI drift, stale JWTs, missing error toasts).

### 3.3 Product & revenue

- **Enterprise trust.** Corporate B2B customers ask for a security summary before signing; this audit yields that summary.
- **Faster feature velocity.** A known-good baseline + E2E coverage lets new admin features ship without per-feature security debates.
- **Lower insurance premium.** Cyber-liability underwriters discount materially for documented admin-panel reviews with remediation evidence.

### 3.4 Engineering health

- **Reduces "god-route" growth.** Forces ownership boundaries between `backend/routes/admin/*.py` modules.
- **Kills dead code.** 124 TS files in `admin-dashboard/src` — the audit surfaces orphaned components, unused API helpers, stale feature flags.
- **Aligns with graphify.** Feeds the knowledge graph at `graphify-out/` with a fresh god-node report.

### 3.5 Concrete, measurable outcomes (success metrics)

| Metric | Baseline (to be filled in by audit) | Target post-remediation |
|---|---|---|
| Admin routes with server-side RBAC enforcement | ? / 25+ | 100% |
| Mutating endpoints covered by audit log | ? | 100% |
| E2E coverage of dashboard routes | 2 specs today (`login`, `dashboard`) | ≥1 happy-path spec per route + RBAC-denial spec per module |
| `npm audit` high/critical | ? | 0 |
| Lighthouse-a11y score (admin) | ? | ≥95 |
| Median login-to-first-paint | ? | <1.5s p75 |
| MFA enrolment rate on admin accounts | 0% (no MFA today) | 100% |
| CSP header strict-dynamic + nonce | Not set | Set; report-only → enforce |
| Secret-scan false positives in repo | ? | 0 |

---

## 4. Methodology

The audit is executed in **7 sequential phases**. Each phase has a gate: the next phase only starts when the previous phase's deliverables exist under `docs/audit/admin-dashboard/<phase>/`.

### Phase 0 — Bootstrap & inventory (≤ 30 min)

1. Read `CLAUDE.md`, `audit-framework/ground-rules.md`, `audit-framework/modules/admin-panel.md`, and `graphify-out/GRAPH_REPORT.md`.
2. Run the inventory commands in §7.3 and write `docs/audit/admin-dashboard/00-inventory.md`:
   - Every route under `admin-dashboard/src/app/` with its file path, auth guard, data-fetching pattern (CSR/SSR/SSG).
   - Every backend endpoint under `backend/routes/admin/` with HTTP verb, path, auth decorator, RBAC module, audit-log call (yes/no), rate-limit decorator (yes/no).
   - Dependency tree: `npm ls --prod --depth=0` for admin, `pip list` for backend.
3. Produce a matrix: **UI route × Backend endpoint × Audit-log × Rate-limit × RBAC-module × Test coverage**. Gaps on this matrix drive every later phase.

### Phase 1 — Static analysis & SAST (≤ 2 h)

Run, and archive outputs:

- `cd admin-dashboard && npm run lint -- --max-warnings 0` (strict)
- `cd admin-dashboard && npx tsc --noEmit`
- `cd admin-dashboard && npm audit --production --json`
- `cd admin-dashboard && npx depcheck`
- `cd admin-dashboard && npx licensee --production` (license sanity)
- `cd backend && ruff check . && ruff format --check .`
- `cd backend && pip-audit` (or `safety check`)
- `semgrep --config p/owasp-top-ten --config p/react --config p/typescript admin-dashboard/src`
- `semgrep --config p/python --config p/fastapi backend/routes/admin`
- Custom grep sweeps (see §7.3): `dangerouslySetInnerHTML`, `eval(`, `new Function(`, `localStorage.*token`, `document.cookie`, direct `fetch(` bypassing `api.ts`, missing `useAuthStore` checks, plain `console.log` of PII.

### Phase 2 — Authentication, session, RBAC (≤ 3 h)

- Trace the login → JWT → refresh → logout lifecycle end-to-end. Confirm:
  - Access token TTL = 12 h (per CLAUDE.md admin spec). Refresh rotation enforced; old refresh invalidated server-side.
  - Token storage: Zustand persisted in `localStorage`? If yes, document XSS impact and propose migration to an httpOnly cookie or in-memory + short TTL.
  - Logout revokes server-side session and clears all stores.
  - `dashboard/layout.tsx` client gate — add Next.js `middleware.ts` to **also** verify server-side on every request.
- Role/claim handling: every `/api/admin/*` handler must call the admin-JWT verifier **and** check the `modules` claim against the module required by that endpoint (e.g. `promotions.write`). Missing module → 403.
- Enumerate admin roles/modules used in the sidebar vs those enforced in backend. Reconcile.
- MFA: propose TOTP (RFC 6238) enrolment flow; optional WebAuthn. Provide migration plan.

### Phase 3 — DAST / functional walkthrough (≤ 4 h)

- Spin up the stack: `python -m backend.server` + `cd admin-dashboard && npm run dev`.
- Log in as each admin role. For **every** dashboard route:
  1. Happy path: does the page load, render data, and complete its mutating actions?
  2. Empty-state: zero rows, offline, timeout.
  3. Error-state: 401/403/500 from backend — does the UI show a real error, or silently blank?
  4. RBAC denial: log in as a role **without** the module — URL typed directly must 403 (not 200 with empty data).
  5. Destructive action: does it require confirmation? Is there an undo / audit entry / success toast?
- Run Playwright E2E (`npm run test:e2e`) and record failures. Expand specs where coverage is missing.
- Run axe-core (`@axe-core/playwright`) against every route; log violations.

### Phase 4 — Backend security deep-dive (≤ 3 h)

- Per endpoint in `backend/routes/admin/*.py`:
  - Input validation via Pydantic models (not raw dicts).
  - `Depends(get_current_admin)` present.
  - `audit_logger.log(...)` present on every mutation with actor, action, target, before/after.
  - `rate_limiter` decorator present (per-admin + per-IP).
  - No raw SQL string interpolation — use parameterised queries / Supabase client.
  - Money paths use `_d() / _round() / _f()` helpers; no `float` in any fare/wallet code path.
  - Idempotency: destructive endpoints accept or generate an `Idempotency-Key`.
- Trace `settings` write paths (`app_settings` table): encrypted-at-rest? Who can rotate? Is there a two-person rule?
- Supabase RLS: even for service-role paths, document which tables the admin can reach and whether RLS is "off" (service role bypasses). Ensure no admin endpoint accidentally runs under the anon key.

### Phase 5 — Data protection, privacy, logging (≤ 2 h)

- PII inventory: every field the admin can read/export across users, drivers, rides, wallet, documents.
- Export endpoints (`export-csv.ts`, jspdf): confirm they are gated, rate-limited, and logged with row counts.
- Sentry config: confirm `beforeSend` scrubs PII (phone, email, addresses, JWTs). Verify `sentry.client.config.ts` and `sentry.server.config.ts`.
- Log redaction: no JWTs, passwords, or OTPs written to stdout/Sentry/Railway logs.
- Retention: admin audit log retained ≥ 1 year, append-only, separate table from application log.
- Canadian data residency: confirm Supabase region, Redis region, Vercel region.

### Phase 6 — Performance, UX, accessibility, i18n (≤ 3 h)

- Lighthouse run against each route; capture LCP, CLS, TBT.
- Bundle analysis: `next build` + `@next/bundle-analyzer`. Flag any chunk > 250 KB gz.
- Heatmap / maplibre / leaflet pages: confirm tiles, heatmap, geofence work on slow 3G and offline.
- Recharts: confirm no PII in tooltips that could leak via screenshots.
- Accessibility: axe clean, keyboard-only walkthrough, focus ring visible, dark/light contrast ≥ 4.5:1.
- i18n readiness: English-only today — document the string-extraction plan (French is mandatory for Quebec).

### Phase 7 — Remediation plan & report (≤ 2 h)

- Consolidate findings into `docs/audit/admin-dashboard/REPORT.md` using the format in §7.1.
- Every finding: severity (CRITICAL/HIGH/MEDIUM/LOW/INFO), evidence file:line, reproducer, suggested fix, estimated effort.
- Group remediations into sprints using `audit-framework/templates/remediation-group.md`.
- Update `audit-framework/modules/admin-panel.md` status from "Not yet audited" → "Audited YYYY-MM-DD".
- Rebuild graphify: `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"`.

### Phase gate rule

Do **not** proceed past any phase until its artifact is committed. If a gate blocks, **surface the blocker to the user** rather than skipping — per `CLAUDE.md`: *"Before silencing or softening any error during development, STOP and ask the user."*

---

## 5. Dimensions

The 16 dimensions from `audit-framework/dimensions/*` applied to admin. Each has a **pass criterion** the audit must verify with code references.

| # | Dimension | Pass criterion for admin dashboard |
|---|---|---|
| 01 | Feature completeness | Every sidebar link resolves to a working page. Every documented admin feature in `docs/API_REFERENCE.md` has a UI entry point. Orphaned routes (in code but not in sidebar) are either wired up or deleted. |
| 02 | Authentication | MFA enforced on all admin accounts; access token 12 h / refresh 30 d rotated; logout revokes server-side; 5-failure lockout; strong-password policy; no `admin123`-class defaults; login page rate-limited. |
| 03 | Encryption & secrets | `JWT_SECRET` ≥ 32 chars, env-provided; `app_settings` secrets encrypted at rest (Supabase pgcrypto or KMS); TLS 1.2+ everywhere; no secret in Next public env (`NEXT_PUBLIC_*`). |
| 04 | Input validation | Every admin endpoint uses Pydantic models; UI forms validate before submit **and** backend re-validates; bulk CSV imports schema-checked; geofence GeoJSON parsed with a real parser not `eval`. |
| 05 | UI/UX | Loading / empty / error / success states exist on every page; destructive actions require typed confirmation (e.g. type "DELETE"); undo window on soft-delete; consistent toast/alert system; dark/light parity. |
| 06 | Real-time | `use-monitoring-socket.ts` reconnects with backoff; survives replica rollover via `spinr:ws:dispatch`; first message is `{"type":"auth","token":...}`; heartbeat pings; 30 msg/s rate limit; 64 KB max. |
| 07 | State machine | Ride/driver/document state changes from admin follow the same state machine as driver/rider; `_require_ride_in_state()` guard used; cancellation only before `TRIP_STARTED`; each change emits a WS event. |
| 08 | Payments | Refunds/adjustments from admin go through Stripe with idempotency keys and `stripe_events` claim; `_d()/_round()/_f()` used; no float arithmetic; dual-entry ledger for wallet deltas via `corporate_wallet_apply_delta`. |
| 09 | Test coverage | ≥1 Vitest unit per non-trivial util; ≥1 Playwright happy-path per route; ≥1 Playwright RBAC-denial per module; backend pytest ≥ 80% branch on `routes/admin/*`. |
| 10 | Error handling | No swallowed errors (CLAUDE.md rule); 503 on DB outage, 502 on upstream (Stripe/Twilio) outage; UI displays real messages; Sentry captures unhandled; no PII in error payloads. |
| 11 | Security headers & CORS | Strict CSP (nonce + strict-dynamic); `Strict-Transport-Security`; `X-Frame-Options: DENY` or CSP `frame-ancestors 'none'`; `Referrer-Policy: no-referrer`; `Permissions-Policy` deny list; admin origin is explicit, not `*`. |
| 12 | Compliance / PII / PCI | PII access logged; admin export actions logged with row counts; no card PAN stored; only last-4 shown; data residency Canadian; PIPEDA/Law 25 ROPA filled; retention policy documented. |
| 13 | Notifications / AI / FAQ | Cloud-messaging page cannot send to "all users" without two-person approval; FAQ edits go through review workflow; AI-generated suggestions labelled clearly; no silent auto-posting. |
| 14 | Performance & scalability | Admin list views paginate server-side; no N+1; slow queries indexed; bulk operations chunked; Lighthouse perf ≥ 85 on all pages; WS scales via Redis pub/sub; background loops idempotent across replicas. |
| 15 | Accessibility (WCAG 2.1 AA) | Axe clean on every route; keyboard nav works end-to-end; focus indicators visible; contrast ≥ 4.5:1; forms have labels & aria-describedby for errors; semantic landmarks used. |
| 16 | i18n & localisation | English-only today — plan & scaffolding for French (Canada) must be present; dates/currency locale-aware; RTL-safe layouts where feasible. |

Each dimension becomes a section in the final report with Status (Pass/Fail/Partial), evidence, gap list, and remediation.

---

## 6. Enhancements

Opinionated list of changes that would move the admin dashboard from "works" to "perfect, functional, secure". Each item includes **why**, **effort** (S/M/L), and **impact** (🔴 security / 🟠 reliability / 🟡 UX / 🟢 DX).

### 6.1 Authentication & session (highest priority)

1. 🔴 **Enforce MFA (TOTP) for every admin account.** `otplib` on the backend, QR enrolment page in settings. Gate login on `mfa_verified` claim. **Effort: M.**
2. 🔴 **Optional WebAuthn / passkeys** as a stronger MFA factor. **Effort: M.**
3. 🔴 **Move admin token off localStorage.** Today `authStore.ts` uses Zustand; if persisted via `zustand/middleware`'s `persist` it lives in `localStorage` → XSS reachable. Move to httpOnly cookie (SameSite=Strict, Secure) for long-lived refresh, keep access token in memory only. **Effort: M.**
4. 🔴 **Add Next.js `middleware.ts`** at `admin-dashboard/src/middleware.ts` to redirect unauthenticated users server-side for every `/dashboard/*` path before the SPA boots. **Effort: S.**
5. 🔴 **Server-side session revocation list** (Redis set). Logout and "revoke all sessions" flush it; every admin request checks membership. Defeats stolen-token replay. **Effort: M.**
6. 🔴 **IP allowlist / geofence** for admin logins (Canada + configured country codes). Allow exceptions with step-up auth. **Effort: M.**
7. 🔴 **Device binding**: fingerprint hash in refresh token; mismatch forces re-auth. **Effort: M.**
8. 🔴 **Step-up auth** for destructive actions (delete user, rotate Stripe key, mass refund): re-prompt MFA within last 5 min. **Effort: M.**
9. 🟠 **Email-on-new-login + Slack alert on admin login from new IP/device.** **Effort: S.**

### 6.2 RBAC & least privilege

10. 🔴 **Server-enforced module RBAC.** Every route under `backend/routes/admin/*.py` declares `required_module = "wallet.write"`; a single dependency checks the JWT `modules` claim. Today the sidebar hides items but the backend may not enforce. **Effort: M.**
11. 🔴 **Two-person rule** on: mass push notifications, bulk refunds > $CAD 1000 aggregate, API-key rotation, admin account creation, PII bulk export. Use a `pending_approvals` table with a second-admin approve step. **Effort: L.**
12. 🟠 **Role templates**: Support, Ops, Finance, Security, Super — documented module set per template. **Effort: S.**
13. 🟠 **Break-glass admin** procedure + runbook in `docs/runbooks/`. **Effort: S.**

### 6.3 Audit logging & monitoring

14. 🔴 **Append-only audit log** in a separate Postgres schema with `INSERT`-only grants. Every admin mutation writes `{actor, ts, action, target, before_json, after_json, ip, ua, request_id}`. **Effort: M.**
15. 🔴 **Admin-side anomaly alerts**: >K PII reads per hour, >K user suspensions per day, first-time rotation of a Stripe key. Wire to Slack/PagerDuty. **Effort: M.**
16. 🟠 **Viewer UI for the audit log** (it already exists at `dashboard/audit-logs` — verify it covers *all* admin actions, not just a subset). **Effort: S.**
17. 🟠 **Client-side telemetry** (Sentry + custom event) for every admin button click, gated behind a feature flag for privacy. **Effort: S.**

### 6.4 UI/UX hardening

18. 🟡 **Destructive-action confirmation pattern**: typed "DELETE <name>" or "REFUND $amount" before the primary button enables. Build one `<ConfirmDestructive>` component and use everywhere. **Effort: S.**
19. 🟡 **Undo window** (soft-delete + 30-day purge) for user/driver/promo deletions. **Effort: M.**
20. 🟡 **Consistent error boundaries.** Wrap every route in `error.tsx` (Next 16) with a Sentry-reported fallback. **Effort: S.**
21. 🟡 **Global command palette** (⌘K) for jump-to-user / jump-to-ride / jump-to-driver. **Effort: M.**
22. 🟡 **Table pattern**: shared data table with server pagination, sort, filter, column-visibility persistence. Today each page re-rolls. **Effort: L.**
23. 🟡 **Optimistic UI rollback**: when a mutation fails, UI must revert and toast — not leave stale state. **Effort: S per page.**
24. 🟡 **Empty states with next-action CTAs** on every list view. **Effort: S.**
25. 🟡 **Dark-mode parity audit**. `next-themes` is installed — confirm every surface renders in both. **Effort: S.**

### 6.5 Security headers, CSP, transport

26. 🔴 **Strict CSP with nonce + strict-dynamic** on admin routes; block inline scripts outside the nonce. Start in report-only for 1 week. **Effort: M.**
27. 🔴 **HSTS with preload**, `frame-ancestors 'none'`, `Referrer-Policy: no-referrer`, `Permissions-Policy` deny list, `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`. Configure in `next.config.ts` `headers()`. **Effort: S.**
28. 🔴 **CORS**: admin origin pinned in backend `CORSMiddleware`; no wildcard. **Effort: S.**
29. 🔴 **CSRF defence** for cookie-auth flows: double-submit token or SameSite=Strict. **Effort: M.**

### 6.6 Input validation & data integrity

30. 🔴 **Pydantic on every admin route** (not raw `dict`). Validate GeoJSON, phone (E.164), currency codes, date ranges, numeric bounds. **Effort: M.**
31. 🔴 **Idempotency-Key** header required on every destructive POST/PATCH/DELETE; dedup via Redis for 24 h. **Effort: M.**
32. 🔴 **Fare / wallet writes**: lint rule + test that blocks `float` in any code path admin can trigger. Extend existing pre-commit hook. **Effort: S.**
33. 🟠 **CSV import hardening**: size limit, header whitelist, row-count cap, virus scan for uploaded docs. **Effort: M.**

### 6.7 PII, privacy, compliance

34. 🔴 **Field-level masking**: phone/email show last-4 by default; reveal requires a justification text + audit entry. **Effort: M.**
35. 🔴 **PIPEDA data-subject export/erase** tooling in admin (subject request workflow). **Effort: L.**
36. 🔴 **Sentry `beforeSend` PII scrubber**: redact phone, email, token, address, lat/long. Unit-test it. **Effort: S.**
37. 🟠 **Data Residency doc**: confirm Supabase (Canada region), Redis (Canada), Vercel (Canada edge). Pin in deployment config. **Effort: S.**
38. 🟠 **Retention job** for audit log (e.g., 13 months) with legal-hold override. **Effort: M.**

### 6.8 Performance, observability, resilience

39. 🟠 **Server-side pagination + cursor-based lists** for users/rides/drivers — Railway memory spikes on 10k-row client paging today. **Effort: M.**
40. 🟠 **Database index review** for every `ORDER BY` / `WHERE` the admin UI runs. Produce `EXPLAIN ANALYZE` evidence. **Effort: M.**
41. 🟠 **Health dashboard route** (`/dashboard/monitoring` — verify it surfaces queue depth, WS connection count, Redis fallback mode, background-loop last-run time). **Effort: M.**
42. 🟠 **Graceful degradation** when Redis is unavailable (rate-limit + OTP lockout become in-process — the admin UI must show a banner). **Effort: S.**
43. 🟢 **Bundle budget**: fail CI if admin JS gz > 350 KB. **Effort: S.**
44. 🟢 **Preload strategies**: prefetch the 3 most-used admin routes after login. **Effort: S.**

### 6.9 Accessibility & i18n

45. 🟡 **Axe-core in CI**: fail the Playwright job on any WCAG AA violation. `@axe-core/playwright` is already a dep. **Effort: S.**
46. 🟡 **Keyboard-only pass**: Tab through every page, verify focus ring & skip-link. **Effort: S.**
47. 🟡 **French locale scaffolding** (Canada). Next.js `i18n` config + `next-intl` or similar; extract strings. **Effort: L.**
48. 🟡 **Currency/date formatting** via `Intl`, not ad-hoc. **Effort: S.**

### 6.10 Testing & CI

49. 🟢 **Playwright coverage floor**: 1 happy-path + 1 RBAC-denial per dashboard route (50+ tests). **Effort: L.**
50. 🟢 **Vitest coverage floor**: 80% branch on `src/lib/**` and `src/store/**`. **Effort: M.**
51. 🟢 **Contract tests** between `src/lib/api.ts` and `backend/routes/admin/*` via OpenAPI/typed-fetch (e.g. `openapi-typescript`). **Effort: M.**
52. 🟢 **Secrets scan** (`gitleaks`) + **SBOM** (`syft`) + **SCA** (`npm audit`, `pip-audit`) in CI. **Effort: S.**
53. 🟢 **Renovate/Dependabot** with auto-merge for patch upgrades. **Effort: S.**
54. 🟢 **PR checks**: type-check, lint, unit, E2E smoke, axe, bundle-size. **Effort: S.**

### 6.11 Operational & DX

55. 🟢 **Runbooks** in `docs/runbooks/admin-*.md` for: revoke admin, rotate Stripe key, restore from audit log, incident response. **Effort: M.**
56. 🟢 **Feature flags** for risky admin features (LaunchDarkly or home-grown `feature_flags` table). **Effort: M.**
57. 🟢 **Preview-environment auth**: Vercel preview URLs for the admin are password-gated (Vercel Access or Cloudflare Access). **Effort: S.**
58. 🟢 **CODEOWNERS** for `admin-dashboard/` and `backend/routes/admin/`. **Effort: S.**

### 6.12 Quick wins (< 1 hour each) to start immediately

- Add `headers()` block in `next.config.ts` with HSTS, X-Frame-Options, Referrer-Policy.
- Remove `console.log` of API responses in `src/lib/api.ts` even under `NODE_ENV=development` for endpoints that return PII.
- Add `.env.example` entries for every var the admin reads; fail build if any missing in prod.
- Add `CODEOWNERS` file.
- Add `middleware.ts` for server-side auth redirect.
- Add `app/dashboard/error.tsx` and `app/dashboard/not-found.tsx`.
- Wire `@axe-core/playwright` into the existing E2E run.
- Turn on `--max-warnings 0` (from 600) after one-pass cleanup.
- Pin Node version in `.nvmrc` and `package.json engines`.
- Pin Next patch version (currently `^16.2.3` — tighten to `16.2.x`).

---

## 7. Deliverables

### 7.1 Directory layout (all artifacts committed)

```
docs/audit/admin-dashboard/
├── 00-inventory.md                 # Route × endpoint × RBAC × audit-log matrix
├── 01-static-analysis/
│   ├── eslint.json
│   ├── tsc.log
│   ├── npm-audit.json
│   ├── semgrep.sarif
│   └── depcheck.txt
├── 02-auth-rbac.md
├── 03-dast-walkthrough.md          # One subsection per route with screenshots
├── 04-backend-deepdive.md
├── 05-privacy-compliance.md
├── 06-perf-a11y-i18n.md
├── screenshots/
├── REPORT.md                       # Executive summary + findings
└── REMEDIATION_PLAN.md             # Sprint-grouped backlog
```

### 7.2 Finding template (use in REPORT.md)

```markdown
### FND-<NN>: <short title>
- **Severity:** CRITICAL | HIGH | MEDIUM | LOW | INFO
- **Dimension:** 02 Authentication
- **Surface:** admin-dashboard / backend / infra
- **Evidence:** `admin-dashboard/src/lib/api.ts:28-36`
- **Description:** What is wrong, observed behaviour.
- **Impact:** What an attacker/operator can do.
- **Reproduction:** Minimal steps / curl / Playwright snippet.
- **Suggested fix:** Concrete change with file references.
- **Effort:** S / M / L
- **References:** OWASP ASVS V2.x, CWE-###, relevant RFC.
```

### 7.3 Reference commands the audit agent will run

**Inventory / search**

```bash
# Every admin route
find admin-dashboard/src/app -type f \( -name "page.tsx" -o -name "layout.tsx" \) | sort

# Every admin backend endpoint
rg -n "^@(router|app)\.(get|post|put|patch|delete)" backend/routes/admin/

# Audit-log coverage on admin mutations
rg -n "audit_logger\." backend/routes/admin/ | sort
rg -nL "audit_logger\." backend/routes/admin/ | sort  # files MISSING audit log

# Rate-limit decorator coverage
rg -n "rate_limit|limiter\." backend/routes/admin/

# Dangerous sinks on the frontend
rg -n "dangerouslySetInnerHTML|eval\(|new Function\(|document\.cookie" admin-dashboard/src
rg -n "localStorage\.(get|set)Item.*(token|jwt|secret)" admin-dashboard/src

# Direct fetch bypassing api.ts
rg -n "fetch\(\"/api" admin-dashboard/src | rg -v "src/lib/api\.ts"

# Float-arithmetic in money paths (should be empty)
rg -n "float\(|\* *1\.0|/ *100\.0" backend/routes/admin backend/services

# Secrets / keys accidentally in source
rg -n "sk_live_|pk_live_|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}" .
```

**Static analysis**

```bash
# Frontend
(cd admin-dashboard && npm ci)
(cd admin-dashboard && npm run lint -- --max-warnings 0)
(cd admin-dashboard && npx tsc --noEmit)
(cd admin-dashboard && npm audit --omit=dev --json > ../docs/audit/admin-dashboard/01-static-analysis/npm-audit.json)
(cd admin-dashboard && npx depcheck > ../docs/audit/admin-dashboard/01-static-analysis/depcheck.txt)

# Semgrep (install once: pipx install semgrep)
semgrep --config p/owasp-top-ten --config p/react --config p/typescript \
        --sarif --output docs/audit/admin-dashboard/01-static-analysis/semgrep.sarif \
        admin-dashboard/src
semgrep --config p/python --config p/fastapi backend/routes/admin

# Backend
(cd backend && ruff check . && ruff format --check .)
(cd backend && pip-audit)
```

**Dynamic / functional**

```bash
# Start stack
(cd backend && python -m backend.server &)
(cd admin-dashboard && npm run dev &)

# E2E + a11y
(cd admin-dashboard && npm run test:e2e)
```

**Graph refresh (per CLAUDE.md)**

```bash
python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"
```

### 7.4 Severity rubric

| Severity | Definition | Examples |
|---|---|---|
| CRITICAL | Unauthenticated or single-admin can cause company-ending loss | Stripe key exfil; arbitrary wallet credit; auth bypass. |
| HIGH | Authenticated-but-unauthorised admin can cause major harm | Module-RBAC bypass; missing audit on delete-user; PII export without log. |
| MEDIUM | Requires unlikely combination, or limited blast | Missing CSP; stale session after password reset; client-side-only guard. |
| LOW | Best-practice deviation with clear fix | Weak ESLint rule; console.log of non-PII; missing alt text. |
| INFO | Future-proofing or nice-to-have | i18n scaffold; bundle-size budget; preview-env auth. |

### 7.5 Ground rules (from `audit-framework/ground-rules.md`, condensed)

- **Evidence, not opinion.** Every finding links to `file:line` or a reproducible command.
- **No destructive tests against prod or shared staging without written go-ahead.**
- **Do not silently fix.** Report first; fix in a separate PR with the finding ID in the title.
- **No secrets in findings.** Redact tokens/keys in screenshots and log snippets.
- **Stop on unexpected state.** If a branch, file, or table surprises you, ask the user before deleting/overwriting.

---

## 8. Ready-to-Run Prompt

> Copy everything between the `BEGIN PROMPT` / `END PROMPT` markers into a fresh Claude Code session at the repo root. It is self-contained and references only files already in this repo.

```
===== BEGIN PROMPT =====

ROLE
You are a senior application-security engineer + staff full-stack engineer auditing the
Spinr admin dashboard. Your deliverable is a complete, evidence-backed audit with a
remediation plan. Follow the plan in docs/audit/ADMIN_DASHBOARD_AUDIT_PROMPT.md
exactly. Do not skip phase gates.

CONTEXT YOU MUST READ FIRST (in this order)
1. CLAUDE.md                                   — critical conventions
2. docs/audit/ADMIN_DASHBOARD_AUDIT_PROMPT.md  — this audit's plan (scope, phases, §5 dimensions, §6 enhancements, §7 deliverables)
3. audit-framework/ground-rules.md
4. audit-framework/modules/admin-panel.md
5. audit-framework/dimensions/*.md
6. graphify-out/GRAPH_REPORT.md                — god nodes & communities

SCOPE (summary — full list in §1 of the plan)
• admin-dashboard/ (Next.js 16, React 19, Tailwind 4)
• backend/routes/admin/*.py
• Admin auth (backend/routes/admin/auth.py, authStore.ts)
• app_settings table (Stripe/Twilio/Maps key rotation)
• WS monitoring (use-monitoring-socket.ts, socket_manager.py)
• Build & deploy config (next.config.ts, sentry.*.config.ts, Vercel)
Out of scope: rider/driver app internals, vendor posture, load testing.

THREAT MODEL
Admin compromise = complete PII/financial breach. Admin JWT is fully trusted with
in-token role+modules (no per-request DB check). app_settings can rotate Stripe/Twilio
keys from the UI. Assume attacker goals: credential stuffing, MFA bypass, IDOR,
module-RBAC bypass, CSRF on settings, XSS via localStorage token, bulk PII export.

RULES (non-negotiable)
• Evidence or it didn't happen — every finding cites file:line.
• Never silently fix a finding; report first, fix in a follow-up PR with the FND-ID.
• No destructive test against staging/prod without explicit user approval.
• No secrets in artifacts — redact tokens, keys, phone/email.
• Respect CLAUDE.md rules: Decimal for money, dual import pattern, ride state
  machine, Stripe idempotency, do-not-silently-swallow-errors.
• If a phase gate blocks, STOP and ask the user — don't soft-handle.
• Use TodoWrite to track phase progress; one in_progress task at a time.
• After any code change, refresh the graph:
    python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"

EXECUTION
Run phases 0–7 from §4 of the plan in order. For each phase:
  1. Create docs/audit/admin-dashboard/<phase-folder>/.
  2. Run the §7.3 commands for that phase; archive raw output.
  3. Write the phase artifact (markdown), link evidence.
  4. Commit: "audit(admin): phase <N> — <title>".
  5. Only then start the next phase.

For Phase 3 (DAST) you must actually spin up the backend and the Next.js dev server
(see §7.3). For each of the 25+ dashboard routes walk through: happy path, empty,
error (401/403/500), RBAC denial (URL-typed access without the module claim),
destructive-action confirmation. Capture screenshots into
docs/audit/admin-dashboard/screenshots/.

REPORT
Final output is docs/audit/admin-dashboard/REPORT.md using §7.2's finding template,
and docs/audit/admin-dashboard/REMEDIATION_PLAN.md grouping findings into sprints
per audit-framework/templates/remediation-group.md. Update
audit-framework/modules/admin-panel.md status from "Not yet audited" to
"Audited YYYY-MM-DD (see docs/audit/admin-dashboard/REPORT.md)".

ENHANCEMENTS
After the audit, propose PRs for the §6 enhancements in priority order: 6.1
(Auth/MFA) → 6.2 (RBAC) → 6.3 (Audit logs) → 6.5 (Headers/CSP) → 6.6 (Validation &
idempotency) → 6.7 (PII/PIPEDA) → 6.4 (UX) → rest. Do NOT implement any of them
without explicit user approval per PR.

SUCCESS CRITERIA
• 00-inventory.md matrix covers 100% of /dashboard routes and /api/admin endpoints.
• Every CRITICAL/HIGH finding has a reproducer and a proposed fix.
• E2E + axe passes on all routes OR each failure has a FND- entry.
• REPORT.md + REMEDIATION_PLAN.md committed on the working branch.
• Graph rebuilt; audit-framework/modules/admin-panel.md status updated.

FIRST ACTIONS
1. Read the files in the CONTEXT list.
2. Create a TodoWrite list from the 8 phases (0–7) + final report.
3. Run Phase 0 inventory. Pause for user review before Phase 1.

===== END PROMPT =====
```

### 8.1 How to use this prompt

- **One-shot audit:** paste the prompt above into a fresh session at repo root.
- **Iterative audit:** run phase by phase, reviewing each artifact before continuing. The phase gate rule in §4 is there precisely to keep scope manageable and context windows small.
- **As a RFP / vendor brief:** Sections 1–3 are the executive brief; Sections 4–7 are the SoW.

### 8.2 Maintenance

- Re-run the audit when any of these change: Next.js major, React major, Supabase schema touching `users` / `admin_users` / `app_settings`, any file under `backend/routes/admin/`, or the admin JWT/claims format.
- Update this document when new dashboard routes are added or when `audit-framework/dimensions/*` gets new dimensions.
- Track progress by flipping checkboxes in a matching `docs/audit/admin-dashboard/STATUS.md` generated from this prompt.

---

*End of prompt. Findings and remediations belong under `docs/audit/admin-dashboard/`, not in this file.*
