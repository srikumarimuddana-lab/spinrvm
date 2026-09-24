# A2 — Security, crypto & data protection

**Lane:** A2 · **Model:** fable / general-purpose · **Returned:** 2026-09-24 ~14:27 UTC · **Orchestrator note:** lane output pasted verbatim below; only this header was added. Vendor claims (pgsodium deprecation) come from search snippets, not fetched pages — INFERRED, as the lane itself labels them.

---

Read-only. All `path:line` references are from `/home/user/spinrvm`. Evidence labels per master §4. No PII in this output. Sentry/Stripe connectors not used. Web searches used: 5 of 6 (all primary sources; no WebFetch was blocked because none was attempted — search snippets only, so vendor claims below are cited but labelled INFERRED where the full page was not read).

## (a) Control mapping — OWASP ASVS L2 / API Top 10 / MASVS-L1

| Standard / control | Spinr control | Status | Evidence |
|---|---|---|---|
| ASVS V2.1 password storage (admin) | bcrypt primary, legacy SHA-256 path still accepted on change-password | partial | `backend/utils/password.py:4-58`; `backend/routes/admin/auth.py:790` |
| ASVS V2.2 anti-automation / OTP brute force | 5-fail/hour → 24 h Redis lockout, keyed-HMAC OTP hash, constant-time compare | met | `backend/routes/auth.py:196-315,967-997`; `backend/utils/crypto.py:11-56` |
| ASVS V2.8 / OTP dev bypass gating | `"1234"` only when ENV != production and no SMS provider configured | met (but forked) | `backend/routes/auth.py:439-463,780-803`; duplicate in `backend/routes/users.py:1230-1255` |
| ASVS V2.8 MFA for admin | TOTP enforced (`ADMIN_MFA_ENFORCED=True`), break-glass exempt | met (TOTP, not phishing-resistant) | `backend/core/config.py:146-153`; ACTION_ITEMS C4 |
| ASVS V3.2 session lifetimes | access 15 min rider/driver, 1 h admin, refresh 30 d — matches CLAUDE.md | met | `backend/core/config.py:143,146,155`; `backend/routes/admin/auth.py:239` |
| ASVS V3.3 refresh rotation + reuse detection | random token, SHA-256 at rest, revoked on rotation, reuse → theft cascade with a benign-replay grace window | met (grace window is deliberate) | `backend/utils/refresh_tokens.py:4-24,57-73,137-147`; `backend/routes/auth.py:739,1245,1514` |
| ASVS V3.5 token revocation (admin) | aud check, jti denylist (Redis), `admin_staff.is_active` + `token_version` re-read from DB per request, 30-min idle | partial — jti denylist fails OPEN on Redis error | `backend/dependencies/__init__.py:262-275,296-307,355-372` |
| ASVS V3.5 revocation (rider/driver) | `users.token_version` re-read per request; per-session tombstone only checked at opt-in ingest points, fail-open | partial (deliberate) | `backend/dependencies/__init__.py:192-201,618,704-730`; `backend/utils/session_revocation.py:114-134` |
| ASVS V3.5 / JWT algorithm pinning | `algorithms=[HS256]` on every verifying decode; no `alg:none` exposure | met | `backend/dependencies/__init__.py:128,179`; `backend/utils/rate_limiter.py:121,145` |
| ASVS V6.2 / V9 JWT signing model | HS256 shared secret; ES256 exists only for the APNs provider token; no `kid`, no JWKS, no rotation without full restart | partial | `backend/dependencies/__init__.py:50`; `backend/core/config.py:141`; `backend/utils/apns_client.py:99-108` |
| ASVS V14.3 weak-secret fail-fast | Production refuses placeholder JWT_SECRET/ADMIN_PASSWORD, JWT_SECRET < 32 chars, placeholder Supabase key/URL, wildcard CORS | met | `backend/core/config.py:367-399`; `backend/core/middleware.py:731-745,914-920` |
| ASVS V14.1 secrets not in repo | only `.env.example` files tracked; pattern grep for live Stripe/AWS/PEM/Supabase-JWT found no hits; Gitleaks in CI | met | `git ls-files` (5 `.env.example`); `.github/workflows/security-gates.yml`, `ci.yml` |
| ASVS V6.4 secret rotation cadence | secret-rotation-monitor workflow flags overdue credentials incl. JWT_SECRET | partial (monitor only; rotation itself requires restart, no dual-key window) | `.github/workflows/secret-rotation-monitor.yml:4,101-108,169` |
| ASVS V6.1 PII at rest | pgsodium/Vault deterministic AEAD via SECURITY DEFINER RPCs for driver SIN, emergency contacts | partial — extension pending deprecation (see SEC-A2-002) | `backend/utils/vault_pii.py:2,24,70`; `backend/migrations/289:26,80`, `357:50-74`, `413:27` |
| ASVS V7.1 no PII in logs | targeted greps for logger calls containing phone/email/lat/lng/address found only masked forms (`phone[-4:]`, `_log_safe_email`) | met (grep-scope only) | `backend/routes/auth.py:296,1085`; `backend/routes/admin/auth.py:149-167` |
| ASVS V7.1 log integrity | request-ID middleware reads `user_id` from an UNVERIFIED JWT decode for log correlation | partial | `backend/core/middleware.py:496-508` |
| API Top10 API1/API5 (BOLA/BFLA) | rider/driver role re-read from `users` per request (not JWT claim); admin `modules` list comes from the JWT until token_version bump | partial | `backend/dependencies/__init__.py:441,618`; `backend/routes/admin/auth.py:244-300` |
| API Top10 API4 rate limiting | SlowAPI + Redis, OTP send caps, WS 30 msg/s, 64 KB | met | `backend/core/middleware.py`; `backend/routes/auth.py:412` |
| API Top10 API8 misconfig (CORS, headers) | explicit origins; wildcard only non-prod; security-headers middleware | met | `backend/core/middleware.py:914-920,995-1006` |
| Supabase RLS (defense in depth) | backend uses service-role key exclusively → every RLS policy dormant; no anon client shipped | known, documented | `backend/supabase_client.py:11-20`; ACTION_ITEMS C108 (closed, doc-only) |
| Stripe webhook idempotency | `claim_stripe_event` before processing | met | `backend/routes/webhooks.py:11-30,741` |
| MASVS-STORAGE-1 token storage | native: `expo-secure-store` for access/refresh tokens (rider, driver, background); web: sessionStorage per ADR-015 | met (native) / accepted-risk (web) | `shared/store/authStore.ts:2,78-94`; `driver-app/utils/backgroundAuth.ts:75-77`; `docs/adr/015-web-token-storage.md` |
| MASVS-NETWORK-2 certificate pinning | none; export-compliance note says plain HTTPS; no ADR records the decision | missing (undecided) | `rider-app/app.config.ts:77-81`; `driver-app/app.config.ts:82-86`; no hits in `docs/adr/` |
| MASVS-RESILIENCE / app attestation | Firebase App Check middleware exists (default on in prod, exempt prefixes); production enforcement not confirmed; no Play Integrity / App Attest verdict use beyond App Check; GPS `mocked` flag is client-reported | partial | `backend/core/middleware.py:61-181,420-475,890,970`; ACTION_ITEMS C3 (~13797-13806); `backend/routes/drivers/location.py:239,390,678-690` |

## (b) Finding cards (§7.1)

### SEC-A2-001 — Single HS256 shared secret is both the verify key and the mint key for every token class, and also the OTP pepper fallback
- Hierarchy: L2 Auth & identity › L3 Token issuance › L4 JWT signing › L5 secret compromise
- Severity: HIGH   Priority score: S×B×L = 4×5×2 = 40
- Status: VERIFIED   Existing item: new (flagged only as a candidate in `docs/audit/clean-sheet-prompt/standards-and-scale.md` §2.2; no ACTION_ITEMS entry for HS256/asymmetric/kid)
- Adversary: malicious insider / anyone with read access to Fly+Railway env, a heap dump, or a backup that includes env
- Evidence: `backend/dependencies/__init__.py:50` (`JWT_ALGORITHM = "HS256"`), `backend/core/config.py:136-141` (`JWT_SECRET`, `ALGORITHM`), `backend/utils/crypto.py:29` (pepper falls back to `JWT_SECRET`), `backend/routes/admin/auth.py:244-249` (admin claims incl. `modules`, `aud`, `jti` signed with same secret), `backend/utils/apns_client.py:107` (only ES256 use — APNs, unrelated)
- What happens: whoever holds `JWT_SECRET` can mint a super_admin token with `aud=admin`, all modules, a fresh `jti`, and `token_version` matching the DB; the only remaining guard is the `admin_staff` row lookup (needs a real staff id). The same secret, if `OTP_PEPPER` is unset, lets an attacker precompute OTP hashes for an `otp_records` leak.
- Root cause: symmetric signing chosen for simplicity (ADR-005); both deploy providers and any future partner/edge verifier must hold the mint key; no `kid` so rotation is a hard cutover that logs everyone out.
- Recommendation: move to ES256/EdDSA with a `kid` header and an in-process JWKS (private key only on the token-minting path; verifiers, rate-limiter and middleware use the public key), keep HS256 acceptance behind a flag for one access-token lifetime, and set `OTP_PEPPER` explicitly now (zero-code change). Alternative considered: keep HS256 and just rotate more often — rejected because rotation without `kid` forces a global logout and does not reduce the mint-anywhere blast radius.
- Blast radius: every `jwt.decode`/`jwt.encode` site — `backend/dependencies/__init__.py:128,179,769`, `backend/utils/rate_limiter.py:145`, `backend/routes/admin/auth.py:278,634,674,945,1027,1185`, `backend/core/middleware.py:501`, WS auth path, `backend/ai/mcp_server.py`; the admin dashboard and both mobile apps only consume tokens (no change).
- Rollout: additive — new key pair via env/`app_settings`, dual-verify window, flag `JWT_ASYMMETRIC_MINT`.   Rollback: flip mint flag back to HS256; verifiers keep accepting both during the window, so no forced logout.
- Verification to close: `pytest tests/test_auth.py tests/test_admin_security.py` extended with a "HS256-signed admin token rejected once flag on" case; manual: mint admin token with old secret after cutover → 401.

### SEC-A2-002 — Driver SIN and emergency-contact encryption depend on `pgsodium`, which Supabase documents as "pending deprecation"
- Hierarchy: L2 Data protection › L3 PII at rest › L4 driver SIN / emergency contacts › L5 platform extension removal
- Severity: HIGH   Priority score: 4×4×2 = 32
- Status: VERIFIED (deprecation notice) / INFERRED (impact on Spinr's direct `pgsodium.*` calls)   Existing item: new — `grep -i pgsodium ACTION_ITEMS.md` returns only the migration-359 ownership investigation (~line 8162), not a deprecation item
- Adversary: auditor / regulator (7-year retention obligation on driver records) and plaintiff's lawyer if a forced migration loses decryptability
- Evidence: Supabase doc titled "pgsodium (pending deprecation)" — https://supabase.com/docs/guides/database/extensions/pgsodium and https://github.com/orgs/supabase/discussions/27109 (search snippet: "Supabase does not recommend the usage of pgsodium as it will be deprecated… use Supabase Vault instead… The Vault extension won't be impacted; its internal implementation will shift away from pgsodium, but the interface and API will remain unchanged"). Spinr calls `pgsodium.valid_key`/`pgsodium.create_key`/`pgsodium.disable_key` directly: `backend/migrations/357_encrypt_emergency_contacts.sql:50-74`, `docs/runbooks/pii-key-rotation.md:31-60`; app layer uses Vault RPCs `backend/utils/vault_pii.py:24,70`.
- What happens: nothing today. When Supabase removes pgsodium, the key-rotation runbook and any migration that references `pgsodium.*` stop working; `vault.secrets`-based reads should survive per Supabase's statement, but that is INFERRED, not tested.
- Root cause: encryption design (migrations 32/137/289/357) predates the vendor's deprecation signal; the key-management surface is the raw extension, not the Vault API.
- Recommendation: (1) inventory every `pgsodium.*` reference and rewrite the rotation runbook to Vault-only primitives; (2) assess app-layer envelope encryption (AES-256-GCM data keys wrapped by a cloud KMS in ca-central) for SIN/licence so the crypto boundary is not a Postgres extension at all. Alternative considered: wait for Supabase's migration outreach — rejected because 7-year retention data cannot be allowed to become undecryptable on a vendor's timeline.
- Blast radius: `backend/utils/vault_pii.py`, `backend/routes/drivers/_shared.py`, `services/driver_import_service.py:361`, migrations 32/137/138/289/357/359/413, `docs/runbooks/pii-key-rotation.md`, retention purge (`backend/utils/retention_purge.py`).
- Rollout: additive dual-read (new envelope column beside Vault UUID column) with backfill.   Rollback: keep Vault column authoritative until dual-read verified; no destructive step until the old column is proven redundant.
- Verification to close: Supabase MCP `list_extensions` on prod to confirm pgsodium version/state (not run — read-only lane, connector not exercised); round-trip test of every encrypt/decrypt RPC on a branch after removing direct `pgsodium.*` calls.

### SEC-A2-003 — Admin jti revocation denylist and session tombstones fail OPEN on Redis error
- Hierarchy: L2 Auth › L3 Session revocation › L4 admin logout / staff deactivation › L5 Redis outage
- Severity: MEDIUM   Priority score: 3×3×2 = 18
- Status: VERIFIED (deliberate, documented)   Existing item: partially covered by test suites listed at ACTION_ITEMS ~1834-1846 (`test_admin_logout_revocation.py`); no open item for the fail-open posture
- Adversary: malicious insider (recently offboarded staff) during a Redis incident; flaky-network adversary
- Evidence: `backend/dependencies/__init__.py:296-307` (`failing OPEN for jti=…; DB token_version still enforced`), `backend/utils/session_revocation.py:114-134` (`allowing request (fail-open)`), contrast break-glass which fails CLOSED `:317-323`
- What happens: an admin who clicked Logout keeps a usable token for up to 1 h if Redis is down; a deactivated staff member is still blocked by the DB `is_active` check (`:355-358`), so the real exposure is logout-only, not deactivation.
- Root cause: performance decision to keep Redis off the per-request path for riders/drivers and to prefer availability over strict revocation for admin when Redis is degraded.
- Recommendation: for admin only (low QPS), fail CLOSED on denylist error, or bump `admin_staff.token_version` on every logout so the DB path (already fail-closed) carries revocation. Alternative considered: leave as is — acceptable for rider/driver, not for the admin surface that can move money.
- Blast radius: `_verify_admin_payload` is shared by HTTP and WebSocket admin auth (`:262-268`); `backend/routes/admin/auth.py` logout handler.
- Rollout: config flag `ADMIN_REVOCATION_FAIL_CLOSED` default off, then on.   Rollback: flip flag; no data change.
- Verification to close: extend `test_admin_logout_revocation.py` with a Redis-raises case asserting 401.

### SEC-A2-004 — No certificate pinning and no ADR recording that as a decision; threat model says P1 OPEN while the standards doc calls it a deliberate trade-off
- Hierarchy: L2 Mobile › L3 Transport › L4 MITM › L5 hostile Wi-Fi
- Severity: MEDIUM   Priority score: 3×3×1 = 9
- Status: VERIFIED (absence + contradiction)   Existing item: new — ACTION_ITEMS hits for "pinning" are only Docker/Actions/test pinning
- Adversary: abusive rider / fraudster on shared Wi-Fi; auditor (inconsistent records)
- Evidence: `rider-app/app.config.ts:77-81`, `driver-app/app.config.ts:82-86` (standard HTTPS only); no `ssl-pinning`/`trustkit` refs in either app; `docs/threat-model/rider-app.md:52,126,149` (RS-4 "OPEN… P1"); `docs/audit/clean-sheet-prompt/standards-and-scale.md` §2.7 ("deliberate trade-off"); `ls docs/adr/` has no pinning ADR
- What happens: nothing user-visible; TLS via Cloudflare edge is the only transport control. The organisational gap is that two artefacts disagree on whether this is accepted risk.
- Root cause: decision never written down.
- Recommendation: write ADR-017 "No certificate pinning; rely on TLS + App Check + short tokens" (or the reverse), and update RS-4 in the threat model to match. Alternative considered: implement pinning — rejected for now because Cloudflare fronting rotates leaf certs and pinning breaks fail-over (`docs/runbooks/railway-fly-failover.md`).
- Blast radius: docs only.
- Rollout: none.   Rollback: n/a.
- Verification to close: ADR merged; threat-model row updated.

### SEC-A2-005 — App attestation in production unconfirmed; GPS anti-spoof relies on a client-reported `mocked` flag
- Hierarchy: L2 Driver › L3 Location integrity › L4 fake-trip earnings › L5 rooted device / mock provider
- Severity: MEDIUM   Priority score: 3×4×2 = 24
- Status: INFERRED   Existing item: ACTION_ITEMS C3 (App Check enforcement "remains manual", ~13797-13806) — stuck because enforcement is a Firebase-console toggle, not code, and nobody has recorded doing it; `docs/threat-model/driver-app.md` DAT-2
- Adversary: colluding driver (fake trips), fraudster
- Evidence: `backend/core/middleware.py:420-475,890,970` (App Check middleware, default on in prod, `APP_CHECK_ENFORCEMENT` override); `backend/routes/drivers/location.py:239,390,678-690` (`mocked: bool = False` from client); `driver-app/hooks/useDriverDashboard.ts:738,835,960`
- What happens: a modified driver build or a Frida-hooked app can send `mocked=false` with spoofed coordinates; the server-side teleport/anomaly filters (`utils/trip_distance.py`, A40 #7) are the real control, and they are heuristic.
- Root cause: attestation delegated to Firebase App Check, whose enforcement state is outside the repo; no Play Integrity `deviceIntegrity` or App Attest assertion consumed at the location or settlement path.
- Recommendation: confirm App Check enforcement (Firebase console) and log it in C3; then trial Play Integrity `MEETS_DEVICE_INTEGRITY` + App Attest on go_online and settlement as a risk signal (not a hard block). Alternative considered: hard-block non-attested devices — rejected: false positives on older/custom Android devices would lock out legitimate drivers (independent contractors, no supplied hardware).
- Blast radius: `go_online`, location batch ingest, `utils/trip_distance.py` settlement filter, driver-app native modules.
- Rollout: flag `ATTESTATION_SIGNAL_ENABLED`, additive column on `driver_locations`/`rides` for verdict.   Rollback: flag off; verdict column ignored.
- Verification to close: Firebase console screenshot/record of enforcement state; test that a request without `X-Firebase-AppCheck` on a non-exempt path returns 401 in production-mode config (`tests/test_appcheck_*`).

### SEC-A2-006 — Request-ID middleware trusts an unverified JWT for `user_id` log correlation
- Hierarchy: L2 Observability › L3 Log integrity › L4 request correlation › L5 forged bearer
- Severity: LOW   Priority score: 2×2×2 = 8
- Status: VERIFIED   Existing item: new
- Adversary: fraudster / plaintiff's lawyer (attributes activity to another user in logs; poisons audit trail before auth rejects)
- Evidence: `backend/core/middleware.py:496-508` (`jwt.decode(token, options={"verify_signature": False})`, `payload.get("user_id")`)
- What happens: any client can put an arbitrary `user_id` into every log line for the request even though the request is then 401'd; investigations grepping by user_id see phantom activity.
- Root cause: correlation runs before auth and avoids a second verify for latency.
- Recommendation: tag the correlation value as `claimed_user_id` (rename field) or verify signature (HS256 verify is microseconds). Alternative considered: leave as is — rejected because audit/forensic consumers cannot distinguish claimed from verified.
- Blast radius: every log line's `user_id` binding; Sentry `rider_id/driver_id` tags if sourced from `request.state.user_id`.
- Rollout: rename is additive.   Rollback: revert field name.
- Verification to close: unit test that a tampered token yields no `user_id` binding.

### SEC-A2-007 — OTP dev-bypass logic is forked between `routes/auth.py` and `routes/users.py`
- Hierarchy: L2 Auth › L3 OTP › L4 dev bypass › L5 production gating drift
- Severity: LOW   Priority score: 2×3×1 = 6
- Status: VERIFIED   Existing item: new (check `docs/known-forks.md` — not verified whether listed)
- Adversary: malicious insider / careless refactor
- Evidence: `backend/routes/auth.py:439-463,780-803`; `backend/routes/users.py:1154-1255`
- What happens: nothing today (both gate on ENV != production); risk is a future edit tightening one copy and not the other — the exact failure pattern CLAUDE.md gate 10 cites.
- Root cause: OTP issuance duplicated for phone-change flow.
- Recommendation: single `issue_otp_code()` helper used by both; add a static test asserting only one `"1234"` literal exists outside tests. Alternative: register the pair in `docs/known-forks.md` — acceptable minimum.
- Blast radius: two handlers; tests `test_auth.py`.
- Rollout: pure refactor.   Rollback: git revert (no data).
- Verification to close: grep count of `"1234"` in `backend/routes` == 1.

### SEC-A2-008 — CLAUDE.md "admin JWT fully trusted" is stale; code re-reads `admin_staff` per request but still authorises `modules` from the claim
- Hierarchy: L2 Auth › L3 Admin authz › L4 module gating › L5 privilege change mid-session
- Severity: LOW   Priority score: 2×3×1 = 6
- Status: VERIFIED   Existing item: new (doc drift)
- Adversary: auditor; malicious insider whose modules were reduced
- Evidence: `backend/dependencies/__init__.py:355-360` (DB read of `is_active`, `token_version`); `backend/routes/admin/auth.py:244-300,463-482` (`modules` embedded in JWT and read back from payload)
- What happens: a module removed from a staff member takes effect only after token expiry (≤1 h) or a `token_version` bump; CLAUDE.md and `docs/threat-model/admin-panel.md:13,101` over-state the trust (they say no DB check at all).
- Root cause: ADR-005 wanted no DB lookup per admin request; later hardening added one but docs were not updated.
- Recommendation: bump `token_version` whenever `admin_staff.modules`/`role` changes (forces re-login), and correct CLAUDE.md + threat model. Alternative: read modules from the DB row already fetched — cheaper still, since the row is already in hand at `:355`.
- Blast radius: admin staff edit endpoints in `backend/routes/admin/`; admin dashboard module gating.
- Rollout: additive.   Rollback: n/a (no live-data change).
- Verification to close: test that changing modules invalidates the existing token.

Not re-filed (existing, why stuck): **RLS dormancy** — ACTION_ITEMS C108 closed as documentation-only; the underlying architecture question (will Supabase Auth ever issue end-user JWTs) is explicitly parked "for whoever owns the auth roadmap" — it is stuck because no owner is named. **Web token storage** — ADR-015 accepted sessionStorage short-term; long-term edge BFF is "Deferred" with no trigger condition.

## (c) Research table (§6 Security & crypto)

| Technique | Verdict | Reason | Source |
|---|---|---|---|
| Passkeys / WebAuthn for admin login | trial | OWASP classes FIDO2/WebAuthn as the most phishing-resistant factor; admin already has TOTP + MFA enforcement, so add passkeys as a second enrollment path for super_admin first | https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html ; https://cheatsheetseries.owasp.org/cheatsheets/Zero_Trust_Architecture_Cheat_Sheet.html |
| Asymmetric JWT (ES256/EdDSA) + `kid` + JWKS rotation | adopt (Next) | Removes mint-anywhere risk of SEC-A2-001; Spinr already runs ES256 for APNs so the library path exists (`backend/utils/apns_client.py:107`) | evidence in repo; ASVS V6/V9 |
| DPoP / refresh-token binding (RFC 9449) | assess | Sender-constrains tokens so a stolen refresh token from a device backup is useless; cost is a per-request proof JWT on mobile and a nonce store; only worth it after asymmetric keys land | https://datatracker.ietf.org/doc/html/rfc9449 ; https://www.rfc-editor.org/info/rfc9449/ |
| Play Integrity `deviceIntegrity` / App Attest as GPS-spoof signal | trial (signal, not block) | `MEETS_DEVICE_INTEGRITY` fails on rooted/hooked/uncertified emulators — directly targets DAT-2; App Attest gives a hardware-backed assertion per request on iOS | https://developer.android.com/google/play/integrity/verdicts ; https://developer.apple.com/documentation/devicecheck/establishing-your-app-s-integrity |
| Envelope encryption with cloud KMS (ca-central) | assess → likely adopt | pgsodium pending deprecation (SEC-A2-002); KMS-wrapped data keys keep the crypto boundary in app code and region-pinned for PIPEDA | https://supabase.com/docs/guides/database/extensions/pgsodium (INFERRED from snippet) |
| Field-level tokenization of SIN / licence numbers | hold | SIN is already stored only as a Vault UUID with no app read path (`migrations/289:80`); tokenization adds a vault service without removing a current exposure — revisit only if a read path is added | evidence in repo |

## (d) Steelman — what is already done well

- OTP design is better than the CLAUDE.md summary: keyed HMAC-SHA256 with a pepper (not plain SHA-256), constant-time compare, legacy fallback deliberately deleted with a dated rationale (`backend/utils/crypto.py:11-56`).
- Refresh-token handling is textbook: random bytes, SHA-256 at rest, rotate-on-use, reuse-as-theft cascade (token_version bump + revoke-all + WS kick) with a documented, bounded benign-replay grace window (`backend/utils/refresh_tokens.py:4-73`).
- Production fail-fast is broad and specific: weak JWT_SECRET, placeholder admin creds, placeholder Supabase key/URL, wildcard CORS, and region all refuse startup (`backend/core/config.py:367-432`, `backend/core/middleware.py:731-745,914-920`).
- Admin auth is layered beyond "trusted JWT": aud check, jti denylist, DB `is_active` + `token_version`, 30-min idle timeout, TOTP enforced by default, and a fail-CLOSED break-glass allowlist (`backend/dependencies/__init__.py:262-372`).
- Every verifying `jwt.decode` pins `algorithms=[HS256]`; PII-in-log greps found only masked forms; secrets are absent from the tree and Gitleaks/Semgrep/Bandit/ZAP gates run in CI.

## (e) NOT VERIFIED

- Supabase production extension state (pgsodium version, whether Vault-only migration has begun) — Supabase MCP not exercised; the deprecation claim is from search snippets of supabase.com, not a fetched page (INFERRED).
- Firebase App Check enforcement state in the Firebase console (outside repo).
- Whether `OTP_PEPPER` is set in production env (if unset, pepper == JWT_SECRET).
- Whether `docs/known-forks.md` already lists the `auth.py`/`users.py` OTP fork.
- PII-in-logs: greps were pattern-based on `logger.*(phone|email|lat|lng|address)` and `logger.bind(...)`; loguru `{}`-placeholder calls with positional args, dict dumps (`logger.info(f"... {payload}")`), and `print()` were not exhaustively checked.
- Mobile token storage in rider-app was verified only via `shared/store/authStore.ts` and driver `backgroundAuth.ts`; per-screen code (`rider-app/app/otp.tsx`) not opened.
- ASVS row-by-row beyond the ~24 controls above (no full V1–V14 sweep); MASVS-L1 mapped at headline level only.
- No dynamic testing performed; all statuses are from source reading.
- Web search budget: 5 used; `datatracker.ietf.org`, `developer.android.com`, `developer.apple.com`, `owasp.org`, `supabase.com` answered via search snippets — no host was blocked because no WebFetch was attempted.

Sources: [Supabase pgsodium (pending deprecation)](https://supabase.com/docs/guides/database/extensions/pgsodium), [Supabase discussion #27109](https://github.com/orgs/supabase/discussions/27109), [RFC 9449 DPoP](https://datatracker.ietf.org/doc/html/rfc9449), [Play Integrity verdicts](https://developer.android.com/google/play/integrity/verdicts), [Apple App Attest](https://developer.apple.com/documentation/devicecheck/establishing-your-app-s-integrity), [OWASP MFA Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html), [OWASP Zero Trust Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Zero_Trust_Architecture_Cheat_Sheet.html).
