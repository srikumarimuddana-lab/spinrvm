# Spinr — Production Readiness Report

_Consolidated from the full-platform audit of 2026-06-09 (architecture, security,
performance, production-readiness, and industry gap analysis). The actionable
backlog lives in [`ACTION_ITEMS.md`](../ACTION_ITEMS.md) at the repo root —
update that file, not this one, as items close. This document is the context._

## Verdict

**~90% production-ready.** Core platform (auth, dispatch, payments, ride state
machine, safety) is hardened and tested. The remaining gaps are concentrated:
test-coverage floors in money paths, a post-deploy smoke test, a handful of
PIPEDA loose ends, one perf hot path, and operational drills. Nothing
architectural is missing.

## Architecture snapshot

Five surfaces around one horizontally-scalable FastAPI backend:

| Surface | Tech | Notes |
|---|---|---|
| `backend/` | Python 3.12 + FastAPI | ~30 domain routers + 24 admin routers, thin service layer |
| `rider-app/`, `driver-app/` | Expo SDK 54 / RN new arch | Crashlytics, ErrorBoundary, OTA updates |
| `admin-dashboard/` | Next.js | HttpOnly cookie auth, 1h TTL, enforced TOTP MFA |
| `shared/` | TypeScript | API client (proactive refresh), stores, types |

State: Supabase (Postgres + RLS, Canadian region enforced at boot), Redis
(documented in-process fallback), Stripe, Twilio (SMS + Proxy number masking),
FCM/Expo push. Deploys: Fly.io Toronto primary + Railway warm standby, cutover
via single Cloudflare CNAME (`docs/runbooks/railway-fly-failover.md`, ADR-007).

Load-bearing design decisions (preserve these):
- **16 replay-safe background loops** (atomic claims / idempotency keys / claim
  flags) + watchdog + ordered graceful shutdown — see CLAUDE.md "Background Loop Recipe".
- **Ride state machine** centrally guarded; acceptance race solved by atomic
  `{'status':'searching'}` filter; insurance periods derived from ride state, append-only.
- **Decimal-only money** with pre-commit enforcement; `corporate_wallet_apply_delta`
  for locked wallet mutations; daily Stripe↔DB reconciliation cron.
- **Settings-in-DB** (`app_settings`, 60s TTL cache) for key rotation without redeploys.

## Security posture (post-remediation)

Closed during the 2026-06 hardening pass (see ACTION_ITEMS.md "Recently completed"
for commits): refresh-TTL reporting bug, SOS-on-expired-token, payment body
validation, admin JWT error-message oracle, MFA challenge audience pin, TOTP
secret leak in staff endpoints, enforced staff MFA with enrollment-scoped tokens,
super-admin MFA reset. Previously shipped: HttpOnly tokens, OTP hash+lockout,
refresh rotation with reuse detection, Stripe webhook signature + idempotency,
CSRF double-submit, Firebase App Check, rate limiting (OTP fail-closed), CORS
wildcard blocked in prod, weak-secret fail-fast, vault encryption fail-closed,
audit logs with delete lockdown, branch protection + security CI gates
(Bandit/Semgrep/ESLint + secret scanning).

Open security/PIPEDA items: `ACTION_ITEMS.md` B1 (analytics GPS interface),
B2 (disputes PII + RLS + rounding), B4 (WS rate limit per-replica).

## Performance vs SLA targets

| Path | Target P95 | Status |
|---|---|---|
| Dispatch offer → driver notification | < 2s | ✅ pushes off request path (`e9283fc`) |
| Fare estimate | < 300ms | ✅ polyline overlapped (`d322709`) + driver index (`5788367`) |
| Driver location write | < 150ms | ⚠️ open — ACTION_ITEMS B3 (per-ping ETA + ride query) |
| WS fan-out | < 100ms | ✅ targeted fan-out + 2s per-send timeout |
| Stripe webhook | < 500ms | ✅ idempotent claim, allowlisted types |

Frontend caching (TanStack staleTime/refetch), admin batch fetches, and the
settings cache were audited as already well-optimized — do not churn them.

## Industry gap analysis (vs Uber/Lyft baseline)

Present: tipping, multi-stop, scheduled rides, fare split, upfront pricing,
trip sharing, number masking, ride tiers, surge (2.5× hard cap), promos,
loyalty, quests, referrals, lost & found, in-ride chat, SOS + safety check-ins,
WAV dispatch, corporate billing, T4A/GST tax docs, demand forecasting, device
attestation, fr-CA i18n.

Real gaps (post-launch backlog, D-items): driver destination mode, driver
heatmap UI, in-app VoIP, real-time fraud scoring layer. **Intentionally absent**
(per "What Spinr Is NOT" — do not implement): pooling, unbounded surge, service
fees, per-trip commission, ad SDKs, behavioral profiling.

## Readiness scorecard

| Area | Status |
|---|---|
| Config safety (fail-fast, weak-secret checks, CA region) | ✅ Ready |
| Resilience (loops, shutdown, Redis fallback, watchdog) | ✅ Ready |
| Rate limiting / abuse protection | ✅ Ready |
| Deployment + 30 runbooks + 9 ADRs | ✅ Ready (drill pending — C1) |
| CI/CD (tests, security gates, migration safety) | ✅ Ready (smoke test pending — A2) |
| Mobile release (EAS, Crashlytics, privacy manifests) | ✅ Ready |
| Observability (Sentry wired + loud-if-missing, metrics, audit logs) | ✅ Ready (alert rule pending — C2) |
| Test coverage (60% floor vs 90% money-path mandate) | ⚠️ Gap — A1 |
| Migrations (178 files, historic duplicate prefixes, CI-gated) | ⚠️ Managed debt |
| PIPEDA (retention, DSAR, anonymization, breach runbook) | ⚠️ A3 + B1 + B2 remain |

## Where everything else lives

| Topic | Location |
|---|---|
| Working conventions, invariants, SLAs | `CLAUDE.md` |
| Actionable backlog (the to-do list) | `ACTION_ITEMS.md` |
| Sprint history + shipped-fix ledger | `.claude/context/sprint-current.md` |
| Incident/ops procedures | `docs/runbooks/` |
| Architecture decisions | `docs/adr/` |
| Historical audit & remediation archives | `docs/audit/`, `reports/` |
