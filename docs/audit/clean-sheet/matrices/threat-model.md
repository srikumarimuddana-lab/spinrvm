# Threat model matrix — per surface, STRIDE-style (R10, 2026-09-24)

**Status:** COMPLETE (v2.0 candidate for `docs/threat-model/*.md`, all of which are v1.0 dated 2026-04-24 — SEC-R10-013). Seeded from `audit-framework/dimensions/21-threat-model.md` and the four `docs/threat-model/{backend,rider-app,driver-app,admin-panel}.md`; every inherited row is re-scored against HEAD `332de89` and, where marked, the live Supabase project (VERIFIED-LIVE 2026-09-24, read-only). New rows are prefixed `N-`. Columns: id · threat · STRIDE · status (**Mitigated / Partial / Open**) · evidence · label · finding. No PII, no exploitation steps. Repo is public: rows describe the control gap, not a procedure.

## 0. How to read / what changed vs `docs/threat-model/*`

| Change class | Rows | Why |
|---|---|---|
| Was OPEN, now Mitigated | S-2, DS-5, DE-4, RE-4 (DV-10 audience binding); T-6 (DV-2); I-3 (redactor exists); AI-3 (dual-approval exports live); AE-2/E-3 (admin JWT *is* DB-checked) | fixes landed May–Sept, model never updated |
| Was LOW/Mitigated, now Partial/Open | S-3/DS-3/RS-2 (App Check over-credited); DI-4 (LogRocket, not Crashlytics, and unmasked); S-5 (refresh grace ordering); I-4 (key in public history, rotation unconfirmed) | new evidence |
| New rows | N-1..N-14 | LogRocket, env admin, reviewer OTP, agent control plane, migration drift, pgaudit, `http` ext, pepper fallback, Zoho egress, LLM keys in `settings`, mutable search_path, unregistered forks, SBOM/provenance, CF-IP trust |

Score = severity(1–5) × blast(1–5) × likelihood(1–5), same scale as the finding cards.

## 1. Backend (FastAPI on Fly/Railway, Supabase, Redis)

| id | Threat | STRIDE | Status | Evidence | Label | Finding / score |
|---|---|---|---|---|---|---|
| S-1 | SIM-swap takeover of rider/driver account | S | Partial | OTP + fail-closed lockout (`routes/auth.py:250-291`); 4-digit code (`dependencies/__init__.py:59-62`); no step-up on sensitive actions (payment-method change, phone change `users.py:574` re-OTPs — met) | VERIFIED | SEC-R10-009 · 24 |
| S-2 | Firebase token used cross-app | S | **Mitigated** | `dependencies/__init__.py:461-471`, `routes/auth.py:1566-1580`, `websocket.py:664-676`; production fails fast without app ids (`config.py:405-412`) | VERIFIED | closed (was DV-10) |
| S-3 | Cloned app bypasses App Check | S | Partial | middleware on by default in prod (`config.py:459-468`) but 15 exempt prefixes incl. OTP endpoints (`middleware.py:86-181`); console enforcement UNKNOWN (C3); replay protection unavailable outside Node | VERIFIED / UNKNOWN | SEC-R10-015 · 12 |
| S-4 | JWT forgery via weak/shared secret | S | Partial | ≥32-char startup guard (`config.py:385-391`); `algorithms=[HS256]` pinned; **but** one shared secret mints every class, no `kid`, same secret is OTP pepper fallback | VERIFIED | SEC-A2-001 · 40; SEC-R10-005 · 18 |
| S-5 | Refresh-token replay after rotation | S | Partial | rotation + cascade (`utils/refresh_tokens.py:300-345`) — met; 600 s benign window is ordering-blind | VERIFIED | SEC-R10-004 · 24 |
| S-6 | Forged bearer poisons log correlation | R/S | Partial | `middleware.py:496-508` unverified decode for `user_id`; rate limiter fixed the same class (`rate_limiter.py:10-33`) | VERIFIED | SEC-A2-006 · 8 |
| N-1 | Env super-admin (password only, no MFA) | S/E | **Open** | `config.py:146-153` exemption; `dependencies/__init__.py:330-352` | VERIFIED | SEC-R10-006 · 32 |
| N-2 | Static reviewer OTP in production | S | Open (accepted, unbounded) | `routes/auth.py:436-451`, audit tag `:539` | VERIFIED | SEC-R10-006 |
| T-1 | Ride state-machine bypass | T | Mitigated (R8 owns) | `_require_ride_in_state()` — not re-audited here | INFERRED | — |
| T-2 | Two drivers accept same ride | T | Mitigated | `{'status':'searching'}` CAS — R8 | INFERRED | — |
| T-3 | Client-supplied fare/ride id trusted on payment | T | Mitigated | `payments.py:64` `_authoritative_ride_charge(ride_id, rider_id)`; `:632`; `wallet.py:240`; `promotions.py:205,418` | VERIFIED | §3 sweep |
| T-4 | Fabricated GPS trajectory | T | Open (heuristic only) | `mocked` client-reported (`drivers/location.py`), server anomaly filters — R11 | VERIFIED (per A2) | SEC-A2-005 · 24 |
| T-6 | `$set` wrapper no-op (DV-2) | T | **Mitigated** | change-log per W0; not re-verified in code here | INFERRED | closed |
| N-3 | Untracked manual DDL on production | T | **Open** | RLS enabled live on `settings`/`document_files` while repo says 379 "NOT applied" (`ACTION_ITEMS.md:21388,:28800`) | VERIFIED-LIVE | SEC-R10-002 · 48 |
| N-4 | Mutable `search_path` on trigger/guard functions; unapplied 450 | E/T | Open (dormant) | `get_advisors`: 16 `function_search_path_mutable`, 1 `authenticated_security_definer_function_executable` | VERIFIED-LIVE | SEC-R10-008 · 12 |
| R-1 | Payment event not logged | R | Partial | `audit_logger`, `financial_events` append-only (live comment) — R9 owns | INFERRED | — |
| R-3 | Admin action not attributable | R | Partial | `audit_logs` 8,660 rows live, append-only trigger; env admin + break-glass are shared principals | VERIFIED-LIVE | SEC-R10-006 |
| N-5 | "Was the leaked key used?" is unanswerable | R | **Open** | `pgaudit` not installed (live); incident §5 item 3 never done | VERIFIED-LIVE | SEC-R10-001 · 75 |
| I-1 | Rider PII to driver via API after ride | I | Partial | `drivers/status.py:268-300` allow-listed projection; post-ride scope-strip not audited here (R7/R11) | INFERRED | — |
| I-2 | Stack/SQL/PII in error bodies | I | Mitigated | C78 (`SpinrException.details` redaction), `redact_error_detail` (`dependencies/__init__.py:625`) | VERIFIED | — |
| I-3 | Raw PII in logs | I | **Mitigated (grep-scope)** | `utils/pii.py`, `log_guard.py`, pre-commit check; loguru static test | VERIFIED | — |
| I-4 | Service-role key exposure | I | **Open** | public git history since 2026-04-12; rotation unconfirmed; chained `settings` secrets incl. `ai_api_key_*` | VERIFIED / UNKNOWN | SEC-R10-001 · 75 |
| I-5 | PII to LLM providers | I | Mitigated | scrub at 4 boundaries (`orchestrator.py:393,491,643`, `mcp_server.py:126-137`); names unmitigable (documented) | VERIFIED | DPA gap → N-6 |
| I-6 | Stripe error metadata to user | I | Partial | not re-verified here (R9) | INFERRED | — |
| N-6 | Sub-processors undocumented (LogRocket, Zoho, PostHog, Anthropic, OpenAI, SES) | I | **Open** | `docs/dpa-register.md:28-42`; `zoho_desk_integration.py:68-71` | VERIFIED | SEC-R10-014 · 18 |
| N-7 | LLM/Stripe/Twilio/Maps keys in one `settings` row readable by the service role | I | Open | `ai/providers/__init__.py:62`; `settings_loader.py:45`; plaintext-vs-Vault UNKNOWN | VERIFIED / UNKNOWN | SEC-R10-002 |
| D-1 | OTP SMS flood / Twilio spend | D | Mitigated | per-phone send cap fail-closed (`routes/auth.py:213-247`) | VERIFIED | — |
| D-3 | WS connection flood | D | Mitigated | 30 s auth deadline, 30 msg/s, 64 KB (`websocket.py:290-291,617-626`) | VERIFIED | SEC-R10-019 |
| D-4 | Redis in-process fallback → per-replica limiter drift | D | Partial | `redis_client.py` fallback documented; OTP paths fail closed on *error* but not on *fallback* | VERIFIED (per CLAUDE.md) | DV-6 (R13) |
| N-8 | Per-IP throttle keyed on spoofable `CF-Connecting-IP`, no origin lock | D/S | **Open** | `rate_limiter.py:87-107`; `fly.toml` no allowlist; C131 | VERIFIED | SEC-R10-009 · 24 |
| N-9 | OTP lockout as DoS against a legitimate user | D | Open | 5 fails → 24 h phone lock; runbook `otp-lockout-false-positive.md` | VERIFIED | SEC-R10-009 |
| E-1 | Rider token on driver-only endpoint | E | Mitigated | `is_driver` from `drivers` row per request (`dependencies/__init__.py:708-712`) | VERIFIED | — |
| E-2 | IDOR rider→rider | E | Mitigated | §3 sweep 102/102 | VERIFIED | SEC-R10-011 (enforcement gap) |
| E-3 | Admin role claim trusted without DB | E | **Mitigated** | `_verify_admin_payload` DB re-read `:355-360`; `staff.py:369-373` bump on change | VERIFIED | SEC-A2-008 narrowed |
| E-4 | Corporate wallet siphon | E | Partial | `require_company_admin`/`member` on every `{company_id}` route (§3); `corporate_wallet_apply_delta` — R9 | VERIFIED (authz) | — |
| E-5 | Direct PostgREST bypasses API | E | Mitigated (dormant) | RLS on 131/131 tables, 61 deny-all; no client principal | VERIFIED-LIVE | SEC-R10-008 |
| N-10 | Admin single-session logout fail-open on Redis error | E | Partial (documented) | `dependencies/__init__.py:283-306` | VERIFIED | SEC-R10-007 · 18 |
| N-11 | `http` extension installed, unused | E/I | Open (informational) | `list_extensions` `http 1.6 installed`; no migration references it | VERIFIED-LIVE | SEC-R10-008 |

## 2. Rider app (Expo/RN)

| id | Threat | STRIDE | Status | Evidence | Label | Finding |
|---|---|---|---|---|---|---|
| RS-1 | SIM-swap registration | S | Partial | as S-1 | VERIFIED | SEC-R10-009 |
| RS-2 | Cloned bundle | S | Partial | App Check exempt on OTP; enforcement UNKNOWN | VERIFIED / UNKNOWN | SEC-R10-015 |
| RS-3 | Rogue driver impersonates support in chat | S | Mitigated | chat scoped to ride participants (`rides/chat.py:77-79,136-137`) | VERIFIED | — |
| RS-4 | MITM / no pinning | S | Open (undecided) | no pinning; no ADR; standards doc says deliberate | VERIFIED | SEC-A2-004 · 9 |
| RT-1 | Tampered fare estimate | T | Mitigated | server-authoritative (`payments.py:64`) | VERIFIED | — |
| RT-2 | Rider GPS spoof to game pricing | T | Open | as T-4 (R11) | INFERRED | — |
| RT-3 | Multiple ratings per ride | T | Mitigated | `@idempotent_endpoint(scope="ride_rate")` (`rides/rating.py:45`) | VERIFIED | — |
| RT-4 | AsyncStorage token impersonation | T | Mitigated | `expo-secure-store` (`shared/store/authStore.ts:2,78-79`) | VERIFIED | — |
| RI-1 | Driver PII persists on device after ride | I | Partial | not audited here (R7) | INFERRED | — |
| RI-2 | Home/work address leaked to driver after ride | I | Partial | as I-1 | INFERRED | — |
| RI-3 | PII to console in release | I | Partial | not audited (R7); LogRocket supersedes as the bigger issue | INFERRED | — |
| **RI-4** | Screenshot/app-switcher cache | I | **Open** | no `expo-screen-capture`/`FLAG_SECURE` (grep 0) | VERIFIED | SEC-R10-018 · 12 |
| **RI-6** | Rooted device reads storage | I | **Open** | no root/jailbreak signal (grep 0) | VERIFIED | SEC-R10-018 |
| **N-12** | Session replay to LogRocket, unmasked, iOS default on | I | **Open** | `rider-app/app/_layout.tsx:246-250,388,543` | VERIFIED | SEC-R10-003 · 64 |
| RD-1/2 | Booking spam / cancel exhaustion | D | Partial | rate limits + cancellation policy (R8/R11) | INFERRED | — |
| RE-1 | Rider → driver role escalation | E | Mitigated | role from DB | VERIFIED | — |
| RE-2 | `/rides/{id}` IDOR | E | Mitigated | `rides/queries.py:369-377` | VERIFIED | §3 |
| RE-3 | Corporate wallet of another company | E | Mitigated | `_ensure_member` (`corporate_rider.py:196,223,321,339,375,430`) | VERIFIED | §3 |
| RE-4 | Cross-app token | E | **Mitigated** | as S-2 | VERIFIED | closed |

## 3. Driver app (Expo/RN)

| id | Threat | STRIDE | Status | Evidence | Label | Finding |
|---|---|---|---|---|---|---|
| DS-1 | Forged licence onboarding | S | Partial | doc verification loop (R11/R12) | INFERRED | — |
| DS-2 | Driver SIM-swap | S | Partial | as S-1; Firebase phone auth path also accepts phone-match lookup (`routes/auth.py:1608-1612`) | VERIFIED | — |
| DS-3 | Cloned driver app receives offers | S | Partial | as S-3 | VERIFIED / UNKNOWN | SEC-R10-015 |
| DS-4 | Multi-device sessions | S | Accepted | multi-device deliberately allowed (`dependencies/__init__.py:672-680`); `logout-all` kills all | VERIFIED | — |
| DS-5 / DE-4 | Cross-app Firebase token | S/E | **Mitigated** | as S-2 | VERIFIED | closed |
| DT-1 | Fake trip via GPS mock | T | Open (heuristic) | as T-4 | VERIFIED (per A2) | SEC-A2-005 |
| DT-4 | Suspended driver stays available | T | Mitigated? | DV-1 fixed per change-log; R8 owns | INFERRED | — |
| DT-6 | `$set` no-op on expiry suspension | T | Mitigated | DV-2 | INFERRED | — |
| DR-1..3 | Repudiation of accept/earnings/SOS | R | Mitigated | `driver_offer_decisions`, `driver_insurance_periods` (append-only, live), `safety_incidents` | VERIFIED-LIVE (tables) | — |
| DI-1 | Rider PII retained post-ride | I | Partial | as I-1 | INFERRED | — |
| DI-2/5/6 | Screenshots / shared device / physical access to earnings | I | Open | no screen-capture guard; SecureStore tokens ✔ | VERIFIED | SEC-R10-018 |
| **DI-4** | PII in crash/replay tooling | I | **Open** | Crashlytics absent (grep 0); LogRocket unmasked (`driver-app/app/_layout.tsx:207-211,441,611` identify with `role: 'driver'`) | VERIFIED | SEC-R10-003 |
| DE-1 | Driver reads another driver | E | Mitigated | `drivers/status.py:268-300` self/admin/active-ride projection; every `/rides/{id}/*` driver route checks `driver_id` (§3) | VERIFIED | §3 |
| DE-2 | Driver hits admin endpoint | E | Mitigated | `_admin_verified` marker | VERIFIED | — |
| DE-3 | Stripe Connect ID rebind | E | Partial | R9 | INFERRED | — |
| N-13 | Pickup PIN plaintext at rest (by design) | I | Accepted | `rides.pickup_otp`; ride-scoped lockout (`ride_flow.py:1186-1210`) | VERIFIED | SEC-R10-016 |

## 4. Admin dashboard + company portal + track page (Next.js on Vercel)

| id | Threat | STRIDE | Status | Evidence | Label | Finding |
|---|---|---|---|---|---|---|
| AS-1 | Admin phished (password + TOTP) | S | Partial | TOTP enforced + per-account and TOTP lockouts (`admin/auth.py:73-110`); no WebAuthn | VERIFIED | A2 research (passkeys trial) |
| AS-2 | Break-glass left enabled | S | Mitigated | Redis allowlist TTL = token life, fail-closed, jti-revocable (`dependencies/__init__.py:307-330`, `admin/auth.py:1395-1403`) | VERIFIED | closed |
| AS-3 | Admin JWT via XSS | S | Partial | CSP with nonce (`admin-dashboard/src/middleware.ts:37`, `next.config.ts:23-60`); tokens in sessionStorage per ADR-015 (accepted) | VERIFIED (config exists) | — |
| AS-4 | Service-role key via dashboard | I | Mitigated | key never leaves backend (`supabase_client.py`) | VERIFIED | — |
| N-1 | Env super-admin without MFA | S/E | **Open** | see §1 N-1 | VERIFIED | SEC-R10-006 |
| AT-1 | Admin edits audit log | T | Mitigated | append-only triggers live (`audit_logs` table comment; 8,660 rows) | VERIFIED-LIVE | — |
| AT-2 | Bulk op without dual approval | T | **Open** | no bulk-op gate found (exports have one, ops do not) | INFERRED (not exhaustively grepped) | R6 |
| AT-4 | Cross-tenant corporate mutation | T | Mitigated | `require_company_admin` on every `{company_id}` write (§3) | VERIFIED | — |
| AR-2 | Shared account not attributable | R | Partial | 2 named `admin_staff` rows (live) + env admin + break-glass | VERIFIED-LIVE | SEC-R10-006 |
| AI-2 | View-as-rider fidelity | I | Unverified | `view_as|impersonat` hits in `admin/__init__.py`, `ai_console.py`, `rides.py` — not audited | UNKNOWN | R6 |
| **AI-3** | Mass export without gate | I | **Mitigated** | migration 396 + `admin_export_approval_requests` live | VERIFIED-LIVE | closed |
| AI-4 | Dashboard logs PII | I | Partial | not audited here | INFERRED | R6 |
| AI-5 | Admin panel internet-exposed | I | Partial | rate-limited login, MFA, idle timeout; no IP allow-list found | VERIFIED (absence) | — |
| AE-1 | Support → super-admin escalation | E | Mitigated | `require_module`/`require_super_admin` + rbac reviewer agent | VERIFIED | — |
| **AE-2** | Admin JWT not re-read from DB | E | **Mitigated** | as E-3 | VERIFIED | doc drift |
| AE-3 | RBAC bypass via direct DB | E | Mitigated | service role backend-only; RLS deny-all | VERIFIED-LIVE | — |
| N-14 | Track page: public share token | I | Mitigated | 32-byte random, 24 h TTL, App Check exemption reasoned (`middleware.py:150-181`, `rides/sharing.py:229`) | VERIFIED | — |

## 5. AI assistant / MCP surface

| id | Threat | STRIDE | Status | Evidence | Label | Finding |
|---|---|---|---|---|---|---|
| A-1 | Prompt injection → act for another user | E | Mitigated | identity from token only; tools scoped by `user` arg (`ai/tools.py`, `mcp_server.py:197`); threat detection is visibility-only by design (`ai/threat.py:1-15`) | VERIFIED | — |
| A-2 | Booking/mutation without consent | T | Mitigated | `_client_action` proposal cards; app executes on tap (`tools_booking.py:4,1658`); only DB write in tool modules is an FAQ embedding cache (`tools_support.py:169`) | VERIFIED | — |
| A-3 | PII to provider | I | Mitigated | 4-boundary scrub; names by minimisation | VERIFIED | N-6 DPA gap |
| A-4 | Staff token on `/mcp` | E | Mitigated | marker + role backstop fail-closed (`mcp_server.py:92-113`) | VERIFIED | — |
| A-5 | Zombie session drives AI turns | E | Mitigated (fail-open by design) | `get_current_user_active_session`; tombstone check on `/mcp` (`:212-214`) | VERIFIED | — |
| A-6 | Cost/abuse | D | Mitigated | daily caps with bounded local fallback | VERIFIED | — |
| A-7 | Kill switch bypass | D | Mitigated | both switches required on every entry point (`mcp_server.py:170-178`) | VERIFIED | — |
| A-8 | Provider keys exposure | I | **Open** | keys in `settings` row (`ai/providers/__init__.py:62`) | VERIFIED | SEC-R10-002 |
| A-9 | Dev-agent over-privilege (push to main, write connector, no audit rows) | E/R | **Open** | `.claude/settings.json:23-34`; connector-scoping rows; `agent_action_log` 0 rows | VERIFIED / VERIFIED-LIVE | SEC-R10-012 · 32 |

## 6. Supply chain & CI/CD

| id | Threat | STRIDE | Status | Evidence | Label | Finding |
|---|---|---|---|---|---|---|
| SC-1 | Malicious/vulnerable dependency | T | Mitigated | hash-locked requirements + `--require-hashes` in `backend/Dockerfile:49`; pip-audit/yarn/npm audit blocking | VERIFIED | — |
| SC-2 | CI hash check silently bypassed | T | Partial | `ci-guardrails.yml:151-152,404-405,569-570` `\|\| pip install … \|\| true` | VERIFIED | SEC-R10-017 |
| SC-3 | Tampered Action | T | Mitigated | 246/253 SHA-pinned; 7 local | VERIFIED | C18 closed |
| SC-4 | Tampered image between build and deploy | T | Mitigated | cosign keyless sign (`ci.yml:1255-1262`) + verify before Fly deploy | VERIFIED | — |
| SC-5 | No provenance / SBOM for incident response | R | **Open** | no attestation; no SBOM artefact | VERIFIED | SEC-R10-017 · 16 |
| SC-6 | Secret in git history | I | **Open** | service-role key (07-30); driver-PII files rewritten, purge unsubmitted (COMP-013) | VERIFIED | SEC-R10-001 |
| SC-7 | Secret-scanning gate not blocking | T | Partial | G5a advisory (`security-gates.yml:596`) pending SC-6 | VERIFIED | SEC-R10-001 |
| SC-8 | Merge gate advisory in practice | T | Open | C21; PR #5048 (W0 §1) | VERIFIED (per W0) | R14 |
| SC-9 | Dead duplicate Dockerfile scanned instead of deployed one | T | Partial | root `Dockerfile` vs `backend/Dockerfile` (deployed via `railway.json:5`, `backend/fly.toml:24`) | VERIFIED | SEC-R10-017 |

## 7. Cross-surface open threats (ranked)

| Rank | Row(s) | Score | Owner | Priority |
|---|---|---|---|---|
| 1 | I-4 / N-5 / SC-6 — service-role key in public history, rotation unconfirmed, no DB audit | 75 | owner + backend | P0 |
| 2 | N-12 / DI-4 — LogRocket unmasked replay, undisclosed | 64 | mobile + privacy | P1 |
| 3 | N-3 — untracked production DDL / migration drift (379, 450, +8) | 48 | migration owner | P1 |
| 4 | S-4 — single HS256 secret for everything | 40 | backend | P1 (Next) |
| 5 | N-1 / N-2 / AR-2 — env admin without MFA, static reviewer OTP | 32 | owner | P1 |
| 6 | A-9 — dev-agent plane (push-to-main, write connector) | 32 | repo admin | P1 |
| 7 | S-5 — refresh grace ordering | 24 | backend | P2 |
| 8 | N-8 / N-9 / S-1 — CF-IP trust, OTP lockout DoS, 4-digit login OTP | 24 | backend + infra | P2 |
| 9 | T-4 / DT-1 — GPS spoof (heuristic only) | 24 | R11 | P2 |
| 10 | N-6 / N-7 — DPA gaps, keys in `settings` | 18 | privacy + backend | P2 |
| 11 | N-10 — admin jti fail-open | 18 | backend | P3 |
| 12 | SC-5 / SC-2 — SBOM/provenance, CI hash fallback | 16 | CI owner | P3 |
| 13 | RI-4 / RI-6 / DI-2 / RS-4 — mobile hardening + pinning ADR | 12 | mobile | P3 |
| 14 | S-3 / DS-3 / RS-2 — App Check over-credited | 12 | owner (C3) | P3 |
