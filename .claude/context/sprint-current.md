# Sprint — Current

_Update this file at the start of every sprint. Claude loads it via `@.claude/context/sprint-current.md` referenced from CLAUDE.md._

## Sprint goal

Close all P0 security/safety findings across backend, admin, and rider surfaces — HttpOnly token storage, admin TTL reduction, first-rating crash, fare-collection state mismatch, GPS OOM, and SOS silent failure.

**Status (2026-04-27):** 5 of 6 sprint P0s shipped via PRs #95/#97 (admin auth + Sentry), #105 (backend group 4), #106 (P1 column + ratings/payment fixes), and `f274816` (SOS UX). Only **A-P0-3 (GPS OOM)** remains and is **PARTIAL** — the limit was reduced but the architectural fix (Supabase-side aggregation) has not been done. See *Recently shipped* below for evidence.

## In-flight

| Ticket | Owner | State | Notes |
|---|---|---|---|
| A-P0-3 | — | **partial** | Per-driver GPS query at `routes/admin/maintenance.py:195-280` reduced from `limit=1000000` to `limit=100000`, but still pulls raw points + aggregates in Python (haversine in a loop). The audit's recommended fix — Supabase-side aggregation via a Postgres function or materialized view — is not yet done. Real fix touches a new SQL migration + `routes/admin/maintenance.py` + tests; ~3–5 h scoped work. |

## Blocked

| Ticket | Blocker | Unblock action |
|---|---|---|
| — | — | — |

## P0 incidents open

None currently open in production (pre-launch).

## Do not touch this sprint

- `backend/routes/rides.py` ride-state machine paths other than the rating endpoint and payment guard — active area, coordinate before touching
- `admin-dashboard/src/store/authStore.ts` — A-P0-1 has shipped; further changes to token storage need a fresh ticket and broader review

## Recently shipped

| PR / Commit | What | Why |
|---|---|---|
| `f274816` | **B-P0-2** RESOLVED — `process_payment` 409 fixed: state strings now consistent (`"completed"` matches between `routes/rides.py:1329` and `routes/drivers.py:2133`); plus double-booking race + SOS UX + dev-endpoint guard | Verified in `reports/audits/2026-04-26-backend-verification-rollup.md` lines 47–55 |
| `f274816` | **R-P0-1** RESOLVED — SOSButton: never green-checks until backend 200; amber "Sending…" state; one auto-retry on network failure; "Alert Not Sent + Call 911 + Retry" path | Verified in `shared/components/SOSButton.tsx:20,58,77,134` (4-state UX wired) |
| #105 | Group 4 backend misc fixes — `datetime.utcnow()` → `datetime.now(timezone.utc)`, scheduled-time validator | unblocked timezone correctness |
| #106 | **B-P0-1** RESOLVED + P1 auth/session/error column (B-P1-3,4,8,11,12,13 + B-P2-1 + B-P3-leak-cleanup) | `rides.py:1888` correctly guards `average_rating` inside `if rated_rides:`; verified in audit rollup line 33 |
| #113 | Restored B-P1-11/12 lost in #106 merge resolver | WS session-revocation + per-user rate-limit |
| #118 | **`pyotp` production bug fix** — admin MFA was importing pyotp without it being in requirements (would `ModuleNotFoundError` at runtime) | Surfaced during PR-watch on #113 CI failures; pip-compile drift check now green |
| #95/#97 | **A-P0-1** + **A-P0-2** RESOLVED — admin JWT moved to HttpOnly cookie + access TTL 12 h → 1 h with silent refresh | `authStore.ts:8,19-23,32,99,226-232` (HttpOnly via `/api/auth/set-cookie`); `core/config.py:40` (`ADMIN_ACCESS_TOKEN_TTL_HOURS: int = 1`) |
| #108 | CI error audit system + safeguards (`ci-error-audit.yml`, `ci-guardrails.yml`, `security-gates.yml`, `dependabot-auto-merge.yml`, `pip-compile-check.yml`) | Full audit + block pipeline |
| #109 | Fix invalid `yyz1` Vercel region → `iad1` | Unblocked Vercel deployments |

## Next sprint candidates (post-A-P0-3)

These are pulled from the 2026-04-26 backend verification rollup and are tracked as P1/P2 NOT FIXED. They do not gate the current P0 sprint but should seed the next.

- **B-P1-1** — Firebase audience binding (`auth.py:401` `verify_id_token` missing `audience=` kwarg)
- **B-P1-2** — `JWT_SECRET` length ≥32 enforcement (`core/config.py:85-101` only blocks placeholders)
- **B-P1-5** — `logger.warning` on auth/DB errors (5+ sites in `auth.py`); CLAUDE.md violation
- **B-P1-6** — Retention purge cron (PIPEDA 2 y / CRA 7 y soft-deleted rows persist indefinitely)
- **B-P2-2** — Stripe webhook event allowlist (`webhooks.py:216-221` swallows unknown events)
- **B-P2-1** — Corporate `float()` → `Decimal` (`corporate_company.py:270, 273`)

## Sentry alert wiring (post-sprint, high-leverage)

`B-P1-3` (refresh-token reuse cascade) emits a `[REFRESH TOKEN REUSE DETECTED]` ERROR-level log line on every cascade fire — this is the only real-time signal Sentry/PagerDuty can hook into. The cascade itself shipped in #106 but the alert rule has not been verified to be wired. Audit Sentry rules to confirm coverage; estimated ~30 min.
