# Module: Backend API

**Status:** Plan v1 ready — Phase A–E execution pending (2026-04-23)
**Audit plan:** `reports/audits/2026-04-23-backend-api-audit-plan-v1.md`
**Applicable dimensions:** 17 (D01–D04, D07–D12, D14, D17–D22)
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

| 17 | Observability | Required | Structured logging, request_id, SLIs, heartbeats, PII redaction in logs |
| 18 | DR / BCP | Required | PITR config, Redis replica, graceful degradation, drill cadence |
| 19 | Fraud | Required | Velocity, impossible-travel, promo abuse, Stripe Radar, sanctions |
| 20 | Financial reconciliation | Required | Stripe↔DB delta cron, wallet function enforcement, T4A, GST/PST columns |
| 21 | Threat model / STRIDE | Required | Backend is the trust boundary; GPS spoof, token replay, corporate wallet siphon |
| 22 | Third-party risk | Required | Vendor inventory, DPAs, sub-processors, SBOM, Docker image scanning |

*Dimensions 05 (UI/UX), 06 (GPS), 13 (notifications UI), 15 (accessibility), 16 (i18n) — not applicable to the API itself.*

**Total applicable dimensions: 17** (D01–D04, D07–D12, D14, D17–D22)

---

## Routes Not Yet Fully Audited

| Route file | Status |
|---|---|
| `backend/routes/admin/` | Not audited — admin panel has separate security concerns |
| `backend/routes/disputes.py` | Discovered but not audited |
| `backend/routes/fare_split.py` | Discovered but not audited |
| `backend/routes/fares.py` | Discovered but not audited |
| `backend/routes/favorites.py` | Discovered but not audited |
| `backend/routes/loyalty.py` | Discovered but not audited |
| `backend/routes/promotions.py` | Discovered but not audited |
| `backend/routes/corporate_accounts.py` | Discovered but not audited |
| `backend/routes/corporate_company.py` | Discovered but not audited |
| `backend/routes/corporate_rider.py` | Discovered but not audited |
| `backend/routes/corporate_wallet.py` | Discovered but not audited |
| `backend/routes/wallet.py` | Discovered but not audited |

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
