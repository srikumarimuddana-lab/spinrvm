# Sprint — Current

_Update this file at the start of every sprint. Claude loads it via `@.claude/context/sprint-current.md` referenced from CLAUDE.md._

## Sprint goal

Close all P0 security/safety findings across backend, admin, and rider surfaces — HttpOnly token storage, admin TTL reduction, first-rating crash, fare-collection state mismatch, GPS OOM, and SOS silent failure.

## Status: GOAL MET ✓

All six P0 tickets are shipped on branch `claude/ci-error-audit-system-HPjKP`.

## In-flight

None — sprint P0 goal achieved.

## Blocked

| Ticket | Blocker | Unblock action |
|---|---|---|
| — | — | — |

## P0 incidents open

None currently open in production (pre-launch).

## Do not touch this sprint

- `backend/routes/rides.py` ride-state machine paths other than the rating endpoint and payment guard — active area, coordinate before touching

## Recently shipped (this sprint)

| Ticket | Commit | What |
|---|---|---|
| A-P0-1 | `0187917` + authStore | Admin JWT → HttpOnly cookie (`/api/auth/set-cookie` route sets `httpOnly: true`); access token in memory only; sessionStorage partializes only `user` + `isAuthenticated` |
| A-P0-2 | config.py | `ADMIN_ACCESS_TOKEN_TTL_HOURS: int = 1` (was 12 h); silent refresh fires 5 min before expiry |
| A-P1-7 | `0187917` | Admin logout blacklists JTI in Redis; `get_current_user` checks `admin:revoked:{jti}` |
| B-P0-1 | `c0ecf8b` | `rate_driver` aggregation used `driver_rating` (synthetic enrichment field, absent from raw rows) → changed to `rider_rating` (real DB column); 7 regression tests |
| B-P0-2 | `5d47261` | `cancel_ride` guard was `"trip_in_progress"` (GPS phase) not `"in_progress"` (ride status); `complete_ride` no longer writes `payment_status="completed"`; stale test fixed |
| A-P0-3 | `3929404` | GPS OOM: `count_documents` replaces `get_rows+len` in cleanup; `columns="driver_id"` on presence query; decline_logs capped at 10 k |
| R-P0-1 | `4c6eb9a` | `triggerEmergency` was swallowing errors; `SOSButton.triggerSOS` always saw success → always showed "Alert Sent"; fix: rethrow so SOSButton's retry detects failure |

## Previously shipped PRs

| PR | What | Why |
|---|---|---|
| #106 | Security: P1 auth/session/error hardening (B-P1-3,4,8,11,12,13 + B-P2-1 + B-P3-leak-cleanup) | 96 new tests; refresh-token reuse cascade, sign-out-everywhere, WS token revocation, typed RateLimitError, per-user WS rate-limit, sanitised 5xx + request_id, scrubbed 13 `detail=str(e)` sites |
| #108 | CI error audit system and safeguards | `ci-error-audit.yml`, `ci-guardrails.yml`, `security-gates.yml`, `dependabot-auto-merge.yml`, `pip-compile-check.yml` — full audit + block pipeline |
| #109 | Fix invalid `yyz1` Vercel region → `iad1` | Unblocked Vercel deployments (vercel.json + 3 API route `preferredRegion` exports) |
| #105 | Backend misc fixes + test cleanup (group 4) | `datetime.utcnow()` → `datetime.now(timezone.utc)`, scheduled-time validator, minor route fixes |
| #95/#97 | Admin audit batch 1+2: Sentry PIPEDA cookie filtering, MFA flow, locked requirements | Sentry no longer captures raw cookies; admin login handles TOTP challenge; pip-compile two-file strategy |
