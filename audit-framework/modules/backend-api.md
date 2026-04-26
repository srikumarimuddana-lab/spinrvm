# Module: Backend API

**Status:** Partially audited (as part of driver-app v4 audit)
**Tech stack:** FastAPI Python 3.12, Supabase (PostgreSQL + RLS), Redis, Stripe
**Root folder:** `backend/`

---

## Applicable Dimensions

| # | Dimension | Priority | Notes |
|---|---|---|---|
| 01 | Feature completeness | Partial | Check admin routes, corporate routes |
| 02 | Authentication | Required | JWT, OTP, Firebase, refresh tokens |
| 03 | Encryption & secrets | Required | Config validation, Vault encryption |
| 04 | Input validation | Required | All Pydantic models and validators |
| 07 | State machine | Required | Ride lifecycle is pure backend |
| 08 | Payments | Required | Stripe, webhooks |
| 09 | Test coverage | Required | pytest suite |
| 10 | Error handling | Required | Exception hierarchy, error handlers |
| 11 | Security headers | Required | CORS, HSTS, rate limiting, CI |
| 12 | Compliance | Required | RLS, data retention, PIPEDA |
| 14 | Performance | Required | DB queries, pagination, indexes |

*Dimensions 05 (UI/UX), 06 (GPS), 13 (notifications UI), 15 (accessibility), 16 (i18n) — not applicable to the API itself.*

---

## Routes Not Yet Fully Audited

| Route file | Status |
|---|---|
| `backend/routes/admin/` | Not audited — admin panel has separate security concerns |

## Audited Routes

| Route file | Audit report | Key findings |
|---|---|---|
| `backend/routes/disputes.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | CRITICAL: admin endpoints unprotected (no auth); PIPEDA: phone exposure; HIGH: N+1 queries; HIGH: Stripe refund non-rollback |
| `backend/routes/fare_split.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | MEDIUM: missing ride ownership check; MEDIUM: participant phone exposure; MEDIUM: non-atomic wallet+status |
| `backend/routes/fares.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | MEDIUM: lat/lng params lack bounds; PASS: Redis cache implemented |
| `backend/routes/favorites.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | MEDIUM: no GPS bounds validation; MEDIUM: name field unbounded |
| `backend/routes/loyalty.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | HIGH: non-atomic idempotency check (double-award race); MEDIUM: redemption non-rollback |
| `backend/routes/promotions.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | CRITICAL: 4 admin endpoints unprotected; HIGH: promo exhaustion race; HIGH: no discount upper bound |
| `backend/routes/corporate_accounts.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | HIGH: IDOR on all record-level endpoints; MEDIUM: silent wallet creation failure |
| `backend/routes/corporate_company.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | MEDIUM: float instead of Decimal for allowances; MEDIUM: unbounded billing queries |
| `backend/routes/corporate_rider.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | CRITICAL: join-domain authorization bypass (no domain ownership check) |
| `backend/routes/corporate_wallet.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | HIGH: IDOR on all endpoints; MEDIUM: unbounded adjustment amount |
| `backend/routes/wallet.py` | `reports/audits/2026-04-26-backend-api-v1.txt` | CRITICAL: 3× TOCTOU race conditions (top-up, pay, transfer) — balances corruptible |

---

## Key Files

| File | Purpose |
|---|---|
| `backend/routes/auth.py` | OTP + JWT + refresh tokens |
| `backend/routes/rides.py` | Full ride lifecycle |
| `backend/routes/drivers.py` | Driver profile + status |
| `backend/routes/payments.py` | Stripe PaymentIntent + SetupIntent |
| `backend/routes/webhooks.py` | Stripe webhook verification |
| `backend/routes/websocket.py` | WebSocket endpoint |
| `backend/socket_manager.py` | WebSocket connection management |
| `backend/utils/ws_pubsub.py` | Redis pub/sub for multi-server WS |
| `backend/services/dispatch_service.py` | Driver matching algorithm |
| `backend/utils/error_handling.py` | Exception hierarchy |
| `backend/validators.py` | Input validation functions |
| `backend/schemas.py` | Pydantic request/response models |
| `backend/core/config.py` | Secret loading + startup validation |
| `backend/core/middleware.py` | Security headers + CORS + App Check |
| `backend/utils/rate_limiter.py` | SlowAPI rate limits |
| `backend/db_supabase.py` | DB abstraction + retry logic |
| `backend/documents.py` | File upload + magic byte validation |
| `backend/utils/document_expiry.py` | Background compliance check |
| `backend/utils/crypto.py` | OTP hashing |
| `backend/utils/password.py` | bcrypt password hashing |
| `backend/migrations/` | Schema + RLS policies |
