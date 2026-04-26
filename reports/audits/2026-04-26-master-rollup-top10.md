# Spinr Master Rollup — Top-10 Launch-Blocking Critical Paths

**Date:** 2026-04-26
**Branch:** `claude/audit-continuation-batch-2`
**Companion to:** `2026-04-26-master-rollup.md`
**Audience:** Eng leads, ops, exec — sprint-planning input

These are the 10 cross-module items that, if any one is unfixed, materially endanger production launch — by safety, regulatory, financial, or reputational measure. Each is sourced from its respective module audit; none can be deferred to "after launch" without a documented mitigation.

Items are ordered by **blast radius × urgency**, not by module.

---

## 1. Pickup OTP stored as plaintext (cross-module)

- **Source:** Driver P0-4 · Rider P0-3
- **Modules:** Backend (issuance) · Rider (display) · Driver (verify)
- **Why blocking:** Anyone with read access to `rides.pickup_code` (debug logs, replicated DB snapshot, support tool) can impersonate a rider. Compromises the only mechanism that ties the right rider to the right driver.
- **Fix path:** SHA-256 hash at issue time; verify by hash compare; never log raw. Rotate any rides with leaked codes. Driver remediation already touched this; verify rider rides are covered.
- **Effort:** ~3–4 h backend + 1 h rider
- **Owner:** backend (lead) + rider/driver

---

## 2. Admin JWT in `sessionStorage` — XSS-stealable for 12 hours

- **Source:** Admin P0-1 + P0-2 (admin-v1 [02-2]; admin-v2 [18-1/2/3] reinforce)
- **Modules:** Admin
- **Why blocking:** Admin tokens are unconditionally trusted (`role`, `email`, `modules` are read straight from the JWT). One XSS → 12-hour session theft → full admin abuse window. Refresh token also stealable, extending to 30 days.
- **Fix path:** Move access token to memory-only (already partially done); move refresh to HttpOnly + Secure + SameSite=Strict cookie scoped to `/admin/auth/refresh`; cut access TTL to 30–60 min; add CSRF double-submit when cookies move (admin-v2 [23-6]).
- **Effort:** 6–10 h backend + 2 h admin frontend
- **Owner:** backend + admin-dashboard

---

## 3. GPS query with `limit=1000000` — OOM in production

- **Source:** Admin P0-3
- **Modules:** Admin (initiator) · Backend (executor)
- **Why blocking:** A single admin tap on the heatmap loads up to 1M `gps_points` rows into the FastAPI process. At 200 bytes/row that's 200 MB on a 512 MB Railway worker — OOM, replica restart, dispatch outage. Operationally catastrophic and trivially triggered.
- **Fix path:** Cap at 5,000 rows; require time-window + bounding-box; cursor-paginate the rest. Same pattern as A-P4-4 (pending docs cursor).
- **Effort:** 2–3 h
- **Owner:** backend (with admin UI follow-up)

---

## 4. Rider can create two active rides (double-booking race)

- **Source:** Rider P0-5
- **Modules:** Rider · Backend
- **Why blocking:** Rider taps "Book" twice in 200 ms → two `searching` rides with different drivers → both drivers en route → cancellation cascade, double charge risk if either reaches `in_progress`. Violates the "rider may have at most one active ride" invariant from CLAUDE.md.
- **Fix path:** Backend `INSERT` with `WHERE NOT EXISTS (active ride)` guard via Postgres function; rider client disables button on first request and only re-enables on response.
- **Effort:** 3–4 h
- **Owner:** backend + rider

---

## 5. Driver with expired licence keeps driving — no auto-suspension

- **Source:** Driver P0-5
- **Modules:** Driver · Backend
- **Why blocking:** Document-expiry checks gate `go_online` but a driver already online when expiry happens stays online. A trip on an expired licence breaks the ride-share endorsement (insurance gap) and is a regulatory event — SGI can claw back coverage retroactively.
- **Fix path:** Background loop scans `drivers.documents` daily at 00:00 SK time; force-offline (and notify) any driver whose licence/insurance/registration crosses today. Idempotent via `auto_offlined_for_doc_expiry_at` flag.
- **Effort:** 4–5 h
- **Owner:** backend (driver UI gets notification only)

---

## 6. Emergency SOS silently fails on network error

- **Source:** Rider P0-1 · Rider P0-6 (button confirmation w/ no backend call)
- **Modules:** Rider · Backend
- **Why blocking:** SOS is the platform's safety lifeline. Today the button shows "Help is on the way" optimistically even if the backend POST fails. A scared rider in a real incident can be staring at a green checkmark while no contact and no safety team has been alerted. Reputational and human-safety risk.
- **Fix path:** Hard-fail UI on network error; queue-and-retry with exponential backoff *while* showing "Sending… retrying"; never green-check until 200 OK + audit log row written. Add periodic health-check from rider device every 60 s when SOS is armed.
- **Effort:** 3–4 h rider + 1 h backend
- **Owner:** rider (lead) + backend

---

## 7. Refresh-token brute-force / OTP lockout silently bypassed when Redis is down

- **Source:** Rider P0-7 (OTP) · Backend hardening
- **Modules:** Backend
- **Why blocking:** OTP failure counter lives in Redis. Per CLAUDE.md, when `REDIS_URL` is unset the rate-limiter falls back to in-memory dict — single-replica only. In multi-replica prod when Redis dies, lockout state effectively resets per-request and an attacker can brute-force a 4-digit OTP unimpeded.
- **Fix path:** When the Redis client errors, fail closed for OTP verification (return 503 "auth temporarily unavailable") rather than fail open. Document the trade-off in the SLA. Add Sentry alert when fallback path activates.
- **Effort:** 2 h
- **Owner:** backend

---

## 8. Real Supabase service-role key in `backend/.env.example`

- **Source:** Rider P0-8
- **Modules:** Backend (file location) · org-wide secret-rotation
- **Why blocking:** A live key in a tracked file = compromise. Once acknowledged, the rotation must happen before any other audit work proceeds because the key controls the database that holds rider PII, driver documents, and Stripe customer references.
- **Fix path:** (1) Confirm leak; (2) rotate the Supabase service-role key in console; (3) replace example with placeholder; (4) audit history for any external clones; (5) PIPEDA breach assessment per CLAUDE.md ("Within 24h: scope assessment").
- **Effort:** 1 h technical + ≥ 4 h legal/breach review
- **Owner:** ops + security + legal

---

## 9. Vercel admin SSR runs in US region — Canadian PII crosses border

- **Source:** Admin v2 [22-1]
- **Modules:** Admin (deploy config)
- **Why blocking:** PIPEDA + CLAUDE.md "Data residency": Supabase is locked to a Canadian region; admin SSR fetches from it. Without `vercel.json` `regions: ["yyz1"]` or per-route `preferredRegion`, every admin page renders in `iad1` (US) and pulls Canadian PII through US infra. Unmitigated cross-border processing of personal data is a regulatory event.
- **Fix path:** Add `vercel.json` with `regions: ["yyz1"]`; add `export const preferredRegion = 'yyz1'` to PII-touching server components/route handlers; verify via Vercel dashboard post-deploy.
- **Effort:** 1–2 h + verification window
- **Owner:** admin-dashboard + ops

---

## 10. No CSP / HSTS / X-Frame-Options on admin

- **Source:** Admin v1 [07-2] · Admin v2 [23-1/2/3/7]
- **Modules:** Admin (Next.js header config)
- **Why blocking:** Admin is the highest-privilege surface. With zero security headers it's exposed to: clickjacking (frame-ancestors), TLS-downgrade on first visit (no HSTS), content sniffing, and any reflected XSS bypassing dompurify (no CSP backstop — see [19-1]). Stacks of risk that compound rather than add.
- **Fix path:** Add `headers()` to `next.config.ts` returning the full bundle: CSP (Report-Only first → enforce), `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Permissions-Policy`. One PR; lands together. Submit `*.spinr.ca` to `hstspreload.org` after a clean week.
- **Effort:** 4–6 h (initial CSP tuning is the bulk of it)
- **Owner:** admin-dashboard

---

## Summary — sprint allocation

| Sprint week | Items |
|---|---|
| Week 1 (P0 freeze)         | #1 (OTP plaintext), #4 (double-booking), #6 (SOS fail), #8 (leaked key — emergency rotation) |
| Week 2                      | #2 (admin token), #3 (GPS limit), #5 (driver doc auto-suspend) |
| Week 3 (pre-launch hardening) | #7 (Redis fail-closed), #9 (Vercel region pin), #10 (admin headers) |

These are the **10**; another **~12 P0 items** (rider Android back, offline banner, etc.) are tier-2 launch blockers tracked in their respective `*-P0-*.md` files. Items #1–#10 are the ones that, if visible in a launch retro, would headline it.

---

## What this list intentionally excludes

- **MEDIUM-severity items**, however common (e.g., 204 across modules) — the rollup file has them for sprint planning. They're hardening, not launch blockers.
- **Phase E observability gaps** (Sentry PII, vendor DPAs) — important, but mitigations exist (manual log review, vendor-by-vendor DPA chase) without blocking traffic.
- **Backlog features (P4)** — by definition, not launch blockers.
- **Items with verified ✅ closure at HEAD** — e.g., several driver P0 items the rollup confirms as fixed.

---

## Definition of done for this list

- All 10 items have a merged PR linked from their remediation file.
- Each PR has automated regression coverage in the test plan.
- The rotation in #8 has a written breach-assessment outcome (even if "no harm").
- A re-run of the relevant audit confirms the finding flips to PASS.
