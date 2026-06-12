# Spinr — Full Security Audit & SOC 2 Readiness Assessment

**Date:** 2026-06-12
**Branch:** `claude/security-audit-soc2-b6kzr5`
**Scope:** entire repo at HEAD — `backend/` (FastAPI + migrations), `rider-app/`, `driver-app/`, `admin-dashboard/`, `shared/`, `.github/workflows/`, `docs/`, `reports/`
**Method:** four parallel specialist audits (backend security, money flows, client surfaces, SOC 2 controls) with independent verification of every P0 claim against source before inclusion. Findings below cite `file:line` at this commit.

---

## Executive summary

The codebase shows an unusually mature security posture for its stage — memory-only mobile tokens with SecureStore refresh tokens, HttpOnly-cookie admin auth with double-submit CSRF, append-only trigger-protected audit logs, automated PII retention purge, daily Stripe reconciliation, and 19 CI workflows including migration safety gates. **No P0 was found on any client surface.**

However, the audit found **5 P0 findings** that must be fixed before production traffic, the most severe being a **ledger inversion that credits the corporate master wallet on every allowance-paid ride** and an **unclosed April 2026 service-role-key breach** whose key rotation, history scrub, and PIPEDA notification decision have been pending for ~7 weeks.

On SOC 2: **Processing Integrity is audit-ready; every other criterion is Partial.** The single biggest Type I blockers are the open breach, advisory (non-blocking) SAST/secret gates, unevidenced branch protection, optional admin MFA, and missing consent versioning.

---

## P0 — Critical (fix before any production traffic)

### P0-1 · Corporate allowance settlement corrupts the ledger (money created)
`backend/services/payment_service.py:280` · **verified directly**

`settle_corporate` debits a member's allowance at ride settlement by calling `corporate_allowance_service.apply_rollback(...)`. The underlying RPC (`backend/migrations/29_corporate_allowance_rpc.sql:69-78`) maps the three delta types as:

| type | master wallet | `used` counter |
|---|---|---|
| `allowance_grant` | −X | −X |
| `allowance_reset` | 0 | reset |
| `allowance_rollback` | **+X** | +X |
| *(correct ride spend)* | **−X** | **+X** — **no type provides this** |

So every allowance-paid corporate ride **adds the spend amount to the master wallet balance** while depleting the allowance. Note the obvious-looking fix (swap to `apply_grant`) is **also wrong**: it debits master correctly but *decreases* `used`, so allowances would never deplete and `allowance_only` policy violations would never trigger.

**Fix:** add an `allowance_spend` type to `corporate_allowance_apply_delta` (master −X, used +X) in a new migration, expose it via `corporate_allowance_service.apply_spend()`, and use it at `payment_service.py:280`. The compensation path at line 301 needs the matching inverse. Add regression tests asserting both ledger directions.

### P0-2 · April 2026 service-role-key breach still open — rotation, scrub, and PIPEDA decision pending
`reports/compliance/2026-04-26-supabase-service-role-key-breach-assessment.md`

A live Supabase **service-role JWT** (RLS-bypassing, ~2036 expiry) plus `SUPABASE_URL` was committed in `backend/.env.example` (sanitized in commit `7a793ca`, 2026-04-20; HEAD verified clean). The breach doc's Action Log (§7) still shows, as of today:

1. **Key rotation** across Railway/Render/CI/local — `TBD — pending`
2. **Git-history scrub assessment** — `TBD — pending` (local history appears truncated/grafted; absence locally does not prove the remote history is clean)
3. **PIPEDA breach-notification decision** — `TBD — pending`, and the 72-hour assessment window lapsed in April

Tracker drift compounds it: `reports/remediation/rider-P0-critical-fix-now.md:276` marks the rotation `[x]` done while the breach doc says pending, and the root `OPEN-ITEMS-TRACKER.md` claims "0 CRITICAL open."

**Fix:** rotate the key and verify the old key returns 401; run trufflehog over the authoritative remote history; write the PIPEDA "real risk of significant harm" determination memo (even if the conclusion is no-notify: private repo, no anomalous access); reconcile the trackers. *This is also SOC 2 gap #1 — an auditor will treat it as a CC6/CC7 control failure.*

### P0-3 · SOS endpoint rejects expired JWTs — violates the project's own safety contract
`backend/routes/rides.py:4148` vs `.claude/context/domain-safety.md:29` · **verified directly**

`trigger_emergency` uses `Depends(get_current_user)`, which raises 401 on an expired token (`backend/dependencies/__init__.py:121`). The safety contract is explicit: *"Never gate SOS behind auth refresh. If the JWT is expired, still accept the request with the user_id claim and flag for review."* A rider whose 15-minute token expired mid-ride — precisely the degraded-connectivity scenario where SOS matters — cannot send an alert.

**Fix:** dedicated `get_current_user_allow_expired` dependency for this endpoint only (`jwt.decode(..., options={"verify_exp": False})`, signature still verified), re-validate `ride.rider_id == user_id`, and flag the incident row `expired_token=true` for review.

### P0-4 · `/auth/refresh` advertises a 30-day access-token expiry; the JWT dies in 15 minutes
`backend/routes/auth.py:1011` vs `backend/dependencies/__init__.py:101` · **verified directly**

`access_expires_at = now + timedelta(days=settings.ACCESS_TOKEN_TTL_DAYS)` is returned to clients, but `create_jwt_token` embeds `exp = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)` (15 min). Clients trusting `access_expires_at` won't refresh until the 30-day wall, so every refreshed session silently breaks 15 minutes later — stalling location updates and ride-status polling, with no clean "session expired" signal.

**Fix:** `timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)` at line 1011 (the CSRF cookie at line 1024 already uses the minutes value, confirming intent).

### P0-5 · Receipt bundles base/distance/time into one "Ride fare" line
`backend/services/fare_service.py:242-248`

`build_fare_breakdown_lines` emits a single consolidated `"Ride fare (X km)"` line. The receipt-transparency rule (CLAUDE.md "What Spinr Is NOT" / SK tax rules) requires base fare, distance, and time as separately disclosed line items. GST/PST are correctly separate (`rides.py:463-467`); the fare components are not.

**Fix:** emit three lines (Base fare / Distance fare / Time fare) and extend the receipt tests.

---

## P1 — High

| # | Finding | Location | Notes / fix |
|---|---|---|---|
| P1-1 | `verify_jwt_token` skips audience verification (`options={"verify_aud": False}`) — rider/staging tokens can be presented across token-type boundaries; role check becomes the only gate | `backend/dependencies/__init__.py:118` *(verified)* | Verify `aud=JWT_AUD_MOBILE` on the mobile branch; re-decode with admin audience where applicable |
| P1-2 | Full admin email logged in plaintext on Redis lockout failures — PIPEDA prohibits emails in logs | `backend/routes/admin/auth.py:85,96,103` | Log a SHA-256 prefix or user id |
| P1-3 | `GET /rides/{id}/call` returns the counterparty's **raw phone number** in the response body (comment admits Twilio Proxy not yet built) | `backend/routes/rides.py:4343-4348` | Return masked only until Proxy sessions exist |
| P1-4 | `_sum_fare_breakdown` accumulates fare lines with a `float` accumulator feeding `fare_breakdown_snapshot.grand_total` — systematic 1¢ drift at scale | `backend/routes/rides.py:422-438` | Accumulate with `Decimal` + `_round()` |
| P1-5 | `allowance_only` corporate policy not enforced: overage is flagged (`flag_violation`) but the master wallet is **charged anyway** | `backend/services/payment_service.py:276-297` | Fall through to rider card per billing priority, or decline; never over-spend the master wallet |
| P1-6 | `admin_router` mounted twice (`/api/v1/admin/*` and `/api/admin/*`) — slowapi tracks the two login paths separately, **doubling the effective brute-force budget** on admin login | `backend/server.py:256,300` | Remove the `v1_api_router` include |
| P1-7 | Firebase/Google API keys committed in `google-services.json` / `GoogleService-Info.plist` (both apps). Normal for mobile, but quota/billing abuse if unrestricted — restriction state not verifiable from the repo | `rider-app/google-services.json:18`, `driver-app/google-services.json` | Verify package-name + SHA-1 / bundle-ID restrictions in Google Cloud Console; rotate if unrestricted |
| P1-8 | `/wallet/pay` route-level guard compares against `total_fare`, but the RPC charges `COALESCE(grand_total, total_fare)` — small fee/tax shortfalls inside the RPC's $0.02 tolerance can slip through | `backend/routes/wallet.py:242-247` | Guard against `grand_total` too |
| P1-9 | Core SAST/secret CI gates are advisory: bandit, semgrep, pip-audit, eslint-security, gitleaks-history all `continue-on-error: true` | `.github/workflows/security-gates.yml` (OI-003–OI-006 in `OPEN-ITEMS-TRACKER.md:38-42`) | Flip to blocking — also SOC 2 CC8 gap #4 |
| P1-10 | Admin MFA (TOTP) exists but is **opt-in** per staff row (`mfa_enabled` default FALSE); super-admin `admin-001` cannot enroll at all | `backend/routes/admin/auth.py:756-873`, `backend/migrations/52_admin_staff_mfa.sql` | Enforce MFA for all module-privileged accounts |

---

## P2 — Medium

- **MFA challenge token has no `aud` claim** and is decoded without audience check — `backend/routes/admin/auth.py:661,826`. Add `aud="spinr:mfa_challenge"`.
- **`FOR ALL` RLS policy on `ride_messages`** — `backend/migrations/98_create_ride_messages.sql:32`; violates the enumerate-verbs convention; verify users can't INSERT directly past backend validation.
- **2-minute login lockout outside production** treats staging like local dev — `backend/routes/admin/auth.py:70-71`. Key on `development` only.
- **Rate limiter silently degrades to per-worker memory** if Redis dies post-startup; multi-replica deploys multiply OTP/login budgets by replica count — `backend/utils/rate_limiter.py:37-75`.
- **Exact GPS in `safety_incidents` rows is broadcast verbatim over the admin WS channel** (`backend/routes/rides.py:4180-4193`); document as a regulatory exception with retention, and keep it out of Sentry breadcrumbs.
- **No Sentry `before_send` PII scrubber on backend** — `backend/server.py:336-382` has `send_default_pii=False` but no last-line-of-defense scrub hook (the admin dashboard *does* have one).
- **Float time-bomb in `DEFAULT_FARE`** — `backend/services/fare_service.py:31-37`: constants are float literals, safe only because every current callsite wraps with `_d()`. Make them `Decimal(...)`.
- **Time-bucketed Stripe idempotency fallback** (`intent-{user}-{epoch//60}`) double-charges across the 60-second boundary for non-ride top-ups — `backend/routes/payments.py:201`, `backend/routes/wallet.py:189`.
- **`calculate_all_fees` returns floats** that get written to `rides.tax_amount`/`tax_breakdown` and summed with builtin `round()` in the estimate path — `backend/features.py:843-876,927`.
- **WS auth swallows `HTTPException`** (revoked JTI, inactive account) without logging the rejection reason — `backend/routes/websocket.py:380-406`.
- **Documented float pre-commit hook does not exist** — CLAUDE.md claims "a pre-commit hook blocks float arithmetic in fare code"; neither `.husky/pre-commit` nor `ruff.toml` implements it. Implement or correct the doc.
- **Consent versioning not implemented** — CLAUDE.md says consent language version is stored at signup; no `consent_version` field exists anywhere in `backend/`. (Also SOC 2 Privacy gap.)
- **Saga gap in `settle_corporate`**: allowance-debit and master-debit RPCs are not transactional, and the allowance leg has no idempotency key of its own — a crash between them (or a failed compensation, `payment_service.py:299-316`) needs manual ledger repair.

---

## P3 — Low / hygiene

- Tracked CI debris: `CUsersTabUsrDskOff111AppDataLocalTempgitleaks-artifact.zip` at repo root (mangled Windows temp path, tracked in git). Contents verified: a gitleaks SARIF with **0 findings, no secrets**. Delete + gitignore.
- `backend/check_permissions.py` prints `SUPABASE_URL` + key prefix; `backend/make_admin.py` hardcodes a promotable UUID — remove or quarantine both scripts.
- Real-looking Protomaps key in `admin-dashboard/.env.example` (`NEXT_PUBLIC_PROTOMAPS_API_KEY=7ee89ce997b4ee94`) — replace with placeholder, rotate, delete the unused block.
- Admin refresh-cookie `maxAge` mismatch: login issues 7 days, refresh re-issues 30 — share one constant matching the backend RT TTL.
- Refund-push failure logged at `logger.debug` in the `charge.refunded` handler — `backend/routes/webhooks.py:375`; payment-path convention requires `logger.error`.
- No certificate pinning on any surface (HTTPS enforced; documented gap, optional hardening).
- Refresh-row `audience` trusted from the DB row rather than cryptographically bound — `backend/routes/auth.py:956-963` (defense-in-depth).
- `@expo/ngrok` listed as a runtime dependency in both mobile apps — move to devDependencies.
- Doc drift: `docs/vendor-inventory.md` calls Railway primary; CLAUDE.md/ADR-007 say Fly.io. Reconcile.

---

## Controls verified working (positive findings)

**Backend** — OTPs SHA-256-hashed with constant-time compare and 5/hr→24 h lockout, dev bypass prod-gated; rider/driver role always re-read from DB (JWT role claim never trusted, never even minted); admin JWTs re-validated per request (is_active, token_version, JTI revocation, idle timeout); refresh tokens 48-byte random, SHA-256 at rest, rotated on use with reuse-cascade revocation; Stripe webhook signature verified before `claim_stripe_event` idempotency gate; all `SECURITY DEFINER` functions pin `search_path`; CORS wildcard refused in production; full security-header set incl. HSTS; comprehensive weak-secret fail-fast at startup (JWT length, admin password strength, Supabase key shape, Redis URL); bcrypt cost 12 with transparent legacy upgrade; driver license/VIN vault-encrypted with fail-closed `_vault_encrypt`.

**Money** — Decimal discipline in `calculate_fare` end-to-end; `dollars_to_cents` via Decimal `to_integral_value` (no `int(x*100)`); SURGE_CAP 2.5 enforced in auto mode and re-capped at fare calc; surge correctly bypassed for corporate-paid and out-of-window scheduled rides; payment retry uses deterministic idempotency keys under a Redis leader lock; atomic `payment_status` claim prevents double-charge; `confirm_payment` binds intent metadata to the authenticated user; raw-card fields rejected at the API perimeter (PCI); 100%-to-driver fare split confirmed (no hidden platform share); daily Stripe↔DB reconciliation with four typed discrepancy classes.

**Clients** — access tokens memory-only on mobile, refresh tokens in expo-secure-store; admin HttpOnly + SameSite=Strict cookies, refresh token stripped from every JSON body, double-submit CSRF with `timingSafeEqual`; per-request-nonce CSP, no `dangerouslySetInnerHTML`; middleware enforces JWT `exp` on all non-public routes; the lone WebView is HTTPS + host-allowlisted; single-flight 401 refresh that cannot loop; GPS redacted from client log URLs; no third-party analytics SDK; no Supabase keys shipped client-side.

**Process** — append-only `audit_logs` (UPDATE/DELETE blocked by triggers, deletion only via session-flagged retention function); daily `purge_pii_retention` (90 d locations / 3 y GPS / 7 y rides+audit) with runbook; 30 runbooks incl. data-breach, PITR (RTO 4 h/RPO 5 min), failover, migration-conflict; quarterly sub-processor audit + weekly staleness monitor; hash-pinned Python deps verified in CI; migration append-only/RLS/sequence CI gates; threat models for all four surfaces; SHA-pinned Dockerfile.

---

## SOC 2 readiness

| Criterion | Status | Evidence highlights | Key gaps |
|---|---|---|---|
| CC1/CC2 Governance | **Partial** | `SECURITY.md`; vendor/DPA registers (`docs/vendor-inventory.md`, `docs/dpa-register.md`); `docs/incident-response.md`; `docs/data-classification.md` | No access-review cadence, no offboarding runbook, no CODEOWNERS, PGP key unpublished |
| CC3/CC4 Risk & monitoring | **Partial** | Threat models ×4; ~400 audit artifacts in `reports/`; Sentry; loop watchdog; sub-processor monitors | No external pen test; no formal risk register; Sentry alert rules not evidenced |
| CC5/CC6 Logical access | **Partial** | Module-level admin RBAC; TOTP MFA + hashed backup codes; append-only audit log; vault-encrypted driver PII; startup secret validation | **Open key-rotation from the breach**; MFA optional; TOTP secrets & `app_settings` keys plaintext-at-rest (RLS only); no key-rotation cadence doc |
| CC7 Operations | **Partial** | 30 runbooks incl. `data-breach.md`, `pitr-restore.md`, failover; retention purge loop | No DR-drill artifacts (runbook mandates quarterly); breach Action Log unclosed |
| CC8 Change mgmt | **Partial** | 19 workflows; migration safety gates (blocking); trufflehog diff scan (blocking); lockfile/hash verification; Dependabot grouped | SAST/secret gates advisory; branch protection referenced but not evidenced; Dependabot patch auto-merge without review; no CODEOWNERS |
| CC9 Vendor risk | **Partial** | Vendor inventory mapped to CC9.2 with quarterly cadence enforced by CI; DPA register; sub-processor baseline | Supabase region attestation unexecuted (`reports/legal/supabase-region-attestation-checklist.md`); Redis vendor/DPA "VERIFY"; Gemini DPA + privacy-policy disclosure open (DV-16) |
| Availability | **Partial** | `/health` + platform healthchecks; dual-region warm standby w/ DNS cutover (ADR-007); PITR runbook; SLO doc | Single Railway replica; no uptime monitoring/status page in repo; no executed DR drill |
| Confidentiality / Privacy | **Partial** | DSAR export + 30-day-grace deletion with SK-Act carve-outs; retention purge; `send_default_pii=False`; startup CA-region enforcement; PII redaction helpers | **Consent versioning missing**; PIPEDA decision for the breach undocumented; phone number exposed by call endpoint; no `before_send` scrubber |
| Processing Integrity | **Ready** | Stripe reconciliation + idempotency; ride-state guards with atomic claims; Decimal rules; corporate RPC row-locking | (P0-1 ledger inversion must land first; add human review loop on `STRIPE_ORPHAN`) |

### Top 10 gaps blocking a SOC 2 Type I

1. Close the breach: rotate the Supabase service-role key, verify old-key 401, update the Action Log (P0-2).
2. Write the PIPEDA notification decision memo for that breach — the lapsed 72 h window must be explained in writing.
3. Run the git-history scrub assessment against the authoritative remote.
4. Flip bandit / semgrep / pip-audit / eslint-security / gitleaks from advisory to blocking in `security-gates.yml`.
5. Evidence branch protection on `main` (required checks + review) and add `.github/CODEOWNERS` — sprint log [17-4] claims protection was enabled manually; confirm in GitHub settings and capture evidence, since repo docs still mark it TODO.
6. Implement consent versioning at signup (claimed in CLAUDE.md, absent in code).
7. Execute the Supabase ca-central-1 region attestation + DPA filing; resolve Redis/Gemini vendor "VERIFY" cells.
8. Enforce admin MFA for all privileged accounts.
9. Produce at least one DR/PITR drill record with artifacts (quarterly per runbook).
10. Document pgsodium PII-key rotation cadence; remove the tracked gitleaks zip; reconcile Railway-vs-Fly primary doc drift.

---

## Recommended remediation order

1. **Day 0:** P0-2 key rotation + PIPEDA memo (no code); P0-4 one-line TTL fix; P1-6 router de-dup.
2. **This week:** P0-1 `allowance_spend` RPC + service + settlement fix with regression tests; P0-3 SOS expired-JWT dependency; P0-5 receipt line split; P1-2/P1-3 PII fixes; P1-4 Decimal accumulator.
3. **Before launch:** P1-5 policy enforcement, P1-8 wallet guard, P1-9 blocking gates, P1-10 mandatory MFA, P1-7 key-restriction verification; then the P2 list.
4. **SOC 2 track:** gaps 5–10 above, then engage an auditor for Type I; Type II needs ≥3 months of operating evidence (DR drills, access reviews, closed-loop reconciliation) after that.
