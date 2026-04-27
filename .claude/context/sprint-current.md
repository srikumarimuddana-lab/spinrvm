# Sprint — Current

_Update this file at the start of every sprint. Claude loads it via `@.claude/context/sprint-current.md` referenced from CLAUDE.md._

## Sprint goal

Close all P0 security/safety findings across backend, admin, and rider surfaces — HttpOnly token storage, admin TTL reduction, first-rating crash, fare-collection state mismatch, GPS OOM, and SOS silent failure.

## In-flight

| Ticket | Owner | State | Notes |
|---|---|---|---|
| A-P0-1 | — | shipped (PR #117) | Admin JWT → HttpOnly cookie; CSRF double-submit; remove sessionStorage persist |
| A-P0-2 | — | shipped (PR #117) | Admin access token TTL already 1 h in config; confirmed end-to-end |
| B-P0-1 | — | shipped (PR #117) | Fixed `rider_rating` column name + rolling-average rating; no more wrong-column miss |
| B-P0-2 | — | shipped (PR #117) | Replaced MongoDB `$nin` atomic guard with Supabase `update_one` filter on `payment_status="pending"` |
| A-P0-3 | — | shipped (PR #117) | GPS breadcrumb cap: `limit=10000` → `limit=1000, order="timestamp"` in `complete_ride()` |
| R-P0-1 | — | shipped (PR #117) | `triggerEmergency` retries 3× (1 s / 2 s backoff) before showing 911 Alert |

## Blocked

| Ticket | Blocker | Unblock action |
|---|---|---|
| — | — | — |

## P0 incidents open

None currently open in production (pre-launch).

## Do not touch this sprint

- `backend/routes/rides.py` ride-state machine paths other than the rating endpoint and payment guard — active area, coordinate before touching
- PR #117 is the landing zone for all P0 fixes; don't re-open any of these tickets without verifying the fix is on that branch

## Recently shipped

| PR | What | Why |
|---|---|---|
| #106 | Security: P1 auth/session/error hardening (B-P1-3,4,8,11,12,13 + B-P2-1 + B-P3-leak-cleanup) | 96 new tests; refresh-token reuse cascade, sign-out-everywhere, WS token revocation, typed RateLimitError, per-user WS rate-limit, sanitised 5xx + request_id, scrubbed 13 `detail=str(e)` sites |
| #108 | CI error audit system and safeguards | `ci-error-audit.yml`, `ci-guardrails.yml`, `security-gates.yml`, `dependabot-auto-merge.yml`, `pip-compile-check.yml` — full audit + block pipeline |
| #109 | Fix invalid `yyz1` Vercel region → `iad1` | Unblocked Vercel deployments (vercel.json + 3 API route `preferredRegion` exports) |
| #105 | Backend misc fixes + test cleanup (group 4) | `datetime.utcnow()` → `datetime.now(timezone.utc)`, scheduled-time validator, minor route fixes |
| #95/#97 | Admin audit batch 1+2: Sentry PIPEDA cookie filtering, MFA flow, locked requirements | Sentry no longer captures raw cookies; admin login handles TOTP challenge; pip-compile two-file strategy |
