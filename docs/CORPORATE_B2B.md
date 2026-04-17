# Corporate B2B — Architecture Reference

> Status: live. Audience: new engineers joining the B2B stream and anyone changing corporate flows.
> Authoritative code paths are cited inline as `path:line`; if this doc drifts from code, trust the code.

---

## 1. Purpose

Spinr's B2B product lets companies pre-fund a master wallet, onboard employees with per-employee spending allowances, and have employee rides billed to the company instead of the rider's personal card. This layer sits **on top of** the consumer ride product — riders, drivers, dispatch, and fare engines are unchanged; the corporate code only adds a billing source.

Key product pieces:

| Piece | What it is |
|---|---|
| **Corporate account** | The company record (legal name, CRA business number, billing email, KYB status). |
| **Master wallet** | One per company, pre-funded in CAD, debited by employee rides and topped up by Stripe. |
| **Members** | Employees attached to a company, with roles (owner / admin / member) and optional allowances. |
| **Allowances** | Per-employee spend limits (fixed recurring, one-time, unlimited). |
| **Policies** | Company-wide guardrails: max fare, geofence, time windows, tip billing, payment source. |
| **Payment source** | Per-ride decision: rider wallet / rider card / company allowance / master fallback. |

---

## 2. High-Level System Map

```
 ┌─────────────────────┐        ┌──────────────────────┐        ┌───────────────────┐
 │  Admin Dashboard    │ ─────► │  FastAPI backend     │ ─────► │  Supabase (Postgres│
 │  (Next.js)          │  HTTP  │  routes/corporate_*  │        │  + Storage + RLS)  │
 │                     │        │  services/corporate_*│        │                    │
 └─────────────────────┘        └──────────┬───────────┘        └───────────────────┘
                                            │
                                            │ Stripe API + webhook
                                            ▼
                                 ┌──────────────────────┐
                                 │  Stripe              │
                                 │  - Customer per co.  │
                                 │  - PaymentIntents    │
                                 │  - Webhook dispatcher│
                                 └──────────────────────┘
```

- **Admin dashboard** drives every B2B write until self-serve onboarding lands.
- **FastAPI** hosts the routes, services, schemas, and two scheduled loops (auto-topup, low-balance).
- **Postgres** holds all state; every wallet delta runs through a Postgres function (`corporate_wallet_apply_delta`) for row-level locking + idempotency.
- **Stripe** is the charging layer only — Stripe never holds source-of-truth balance.

---

## 3. Data Model (nine tables)

Migration sources:
- `backend/migrations/05_corporate_accounts.sql` (initial table)
- `backend/migrations/17_corporate_accounts_fk.sql` (FKs from users/rides)
- `backend/migrations/27_corporate_b2b_v1.sql` (the other eight tables + RLS)
- `backend/migrations/28_corporate_wallet_apply_delta.sql` (the RPC)

| Table | Role | Key columns |
|---|---|---|
| `corporate_accounts` | Company record | `id`, `status` (pending_verification/active/suspended/closed), `stripe_customer_id` (UNIQUE), `size_tier`, `kyb_document_url`, `kyb_reviewed_at`, `billing_email` |
| `corporate_wallets` | Master wallet (1:1 with company) | `company_id` (UNIQUE), `balance` NUMERIC(12,2), `auto_topup_enabled/threshold/amount/daily_cap`, `low_balance_notified_at`, `soft_negative_floor` |
| `corporate_wallet_transactions` | Append-only ledger | `wallet_id`, `scope` (`master` or `member:{uuid}`), `type` (topup/allowance_grant/allowance_reset/allowance_rollback/ride_debit/refund/adjustment), `amount`, `balance_after`, `stripe_payment_intent_id` (UNIQUE WHERE NOT NULL), `ride_id`, `member_id`, `actor_user_id`, `notes` |
| `corporate_members` | Employee roster | `company_id`, `user_id` (NULL while invited), `role`, `status` (invited/active/suspended/removed), `invite_token`, `policy_override` |
| `corporate_member_allowances` | Per-employee spend cap | `member_id` UNIQUE, `type` (fixed_recurring/one_time/unlimited), `amount`, `used`, `period_start/end`, `rollover`, `auto_approve_topup_amount`, `auto_approve_monthly_count`, `status` |
| `corporate_allowance_requests` | "Ask for more" | `member_id`, `amount`, `reason`, `status` (pending/approved/denied/auto_approved), UNIQUE(member_id) WHERE status='pending' |
| `corporate_policies` | Company-wide rules | `company_id` UNIQUE, `max_fare_per_ride`, `allowed_geofence` (JSONB GeoJSON), `allowed_time_windows` (JSONB), `allowed_payment_source`, `tip_billed_to` |
| `corporate_allowed_domains` | Auto-match signups by domain | `company_id`, `domain` (lowercased, UNIQUE per company) |
| `ride_payment_sources` | Per-ride audit | `ride_id` PK, `source_type`, `company_id`, `member_id`, `allowance_debit_amount`, `master_fallback_amount`, `policy_check_result`, `policy_failed_rules` |
| `corporate_policy_evaluations` | Policy audit log | `ride_id`, `phase` (booking/completion), `result`, `failed_rules`, `bypassed_rules` |

Cross-table references:
- `users.corporate_account_id` FK → `corporate_accounts(id)` ON DELETE SET NULL
- `rides.corporate_account_id` FK → `corporate_accounts(id)` ON DELETE SET NULL

**RLS**: every B2B table has `ENABLE ROW LEVEL SECURITY` with an admin-full-access policy (`EXISTS … users.role='admin'`). No employee-scoped policies yet — enforcement is still in Python for non-admin callers.

---

## 4. Backend Modules

### Routes

| File | Endpoints | Auth |
|---|---|---|
| `backend/routes/corporate_accounts.py` | CRUD, KYB upload URL, KYB review, status transition | `get_admin_user` |
| `backend/routes/corporate_wallet.py` | Wallet fetch, manual topup, manual adjust, config PUT | `get_admin_user` |
| `backend/routes/webhooks.py` (corporate branch, L94–113) | Stripe `payment_intent.succeeded` with `metadata.scope == 'corporate_topup'` | Stripe signature |

Endpoint reference:

```
GET    /admin/corporate-accounts                        list (filters: status, size_tier, search, is_active, skip, limit)
GET    /admin/corporate-accounts/{id}                   detail
POST   /admin/corporate-accounts                        create (super-admin)
PUT    /admin/corporate-accounts/{id}                   update
DELETE /admin/corporate-accounts/{id}                   delete
POST   /admin/corporate-accounts/{id}/kyb-upload-url    signed Supabase Storage URL
POST   /admin/corporate-accounts/{id}/kyb-review        approve → active + wallet + Stripe customer; reject → suspended
POST   /admin/corporate-accounts/{id}/status            transition active↔suspended, anything → closed (terminal)

GET    /admin/corporate-accounts/{id}/wallet            balance + paginated ledger
POST   /admin/corporate-accounts/{id}/wallet/topup      Stripe PaymentIntent (off_session=True, confirm=True)
POST   /admin/corporate-accounts/{id}/wallet/adjust     signed delta; enforces soft_negative_floor
PUT    /admin/corporate-accounts/{id}/wallet/config     auto_topup_{enabled,threshold,amount,daily_cap}
```

### Services

- **`backend/services/corporate_wallet_service.py`** — the only caller of the Postgres RPC.
  - `apply_topup(wallet_id, amount>0, stripe_pi, actor_user_id?, notes?)` — credit from Stripe.
  - `apply_adjustment(wallet_id, amount≠0, notes required, actor_user_id, floor?)` — signed manual correction.
  - `apply_refund(wallet_id, amount>0, ride_id, …)` — refund on cancelled/disputed ride.
  - All four thread-hop into a sync executor (`run_sync`) because Supabase client is sync; all delegate to RPC `corporate_wallet_apply_delta` which:
    1. Row-locks the wallet.
    2. Returns no-op if `stripe_payment_intent_id` already recorded (idempotency).
    3. Updates `balance` and appends a ledger row atomically.
    4. Enforces `soft_negative_floor` only for `master` scope.

### Schemas

- **`backend/schemas/corporate.py`** — Pydantic v2.
  - Enums: `CompanyStatus`, `SizeTier`, `Locale` (`en-CA` / `fr-CA`).
  - `CorporateAccountBase` / `Create` / `Update` / `Response` — base has CRA BN + Canadian tax-region validators, EmailStr on `billing_email` + `contact_email`, CAD/Canada defaults.
  - `KYBReviewDecision { approve: bool, note?: str ≤500 }`.
  - `CompanyStatusTransition { status: CompanyStatus, reason?: str ≤500 }`.
  - Wallet bodies live in `backend/schemas.py`: `TopUpRequest` (100–10000), `AdjustRequest` (signed), `WalletConfigPatch`.

### DB helpers (`backend/db_supabase.py`)

All corporate DB access goes through named helpers so the routes stay thin:

- `insert_corporate_account`, `get_corporate_account_by_id`, `list_corporate_accounts_filtered`, `update_corporate_account`, `delete_corporate_account`, `update_corporate_account_status`
- `record_kyb_decision` — flips status + stamps `kyb_reviewed_at/by`.
- `create_kyb_upload_url` — signed Supabase Storage URL into `kyb-documents` bucket, 1-hour TTL.
- `ensure_corporate_wallet`, `get_corporate_wallet_by_company`, `update_corporate_wallet_config`, `mark_low_balance_notified`
- `update_corporate_stripe_customer_id`
- `list_wallets_needing_autotopup`, `list_wallets_low_balance_no_autotopup`
- `get_default_payment_method`, `sum_autotopups_today`
- `get_corporate_members_for_user` (hot path — used on every ride)

### Background workers

Both workers run inside the FastAPI process (no external scheduler):

| File | Loop | Cadence | Job |
|---|---|---|---|
| `backend/utils/corporate_autotopup.py` | `corporate_autotopup_loop` | 600 s | For each wallet with `auto_topup_enabled=true` and `balance < threshold`: validate company active, fetch default payment method, check daily cap, create off-session Stripe PaymentIntent. Exceptions per-wallet are swallowed so one bad wallet doesn't stall the tick. |
| `backend/utils/corporate_low_balance.py` | `corporate_low_balance_loop` | 3600 s | For each wallet with `auto_topup_enabled=false` and `balance < threshold`: email `billing_email` via `send_email()`, rate-limited to one email per 12 h per wallet via `low_balance_notified_at`. |

---

## 5. State Machine — `corporate_accounts.status`

```
          ┌─────────────────────────┐
          │ pending_verification    │  ◄──  created via POST /admin/corporate-accounts
          └───────────┬─────────────┘
         KYB approve  │  KYB reject
                      ▼
          ┌─────────────────────────┐              ┌─────────────┐
          │ active                  │ ◄─reactivate─│ suspended   │
          │ (wallet provisioned,    │ ─suspend───► │ (autotopup  │
          │  Stripe customer set)   │              │  disabled)  │
          └───────────┬─────────────┘              └──────┬──────┘
                       │       close                       │ close
                       ▼                                   ▼
                   ┌───────────────── closed ─────────────────┐
                   │         (terminal — cannot reopen)        │
                   └───────────────────────────────────────────┘
```

Transition side-effects (in `routes/corporate_accounts.py`):
- **→ active from pending_verification** (KYB approve): idempotent `ensure_corporate_wallet` + `stripe.Customer.create(metadata={corporate_account_id: …})` + persist `stripe_customer_id`.
- **→ suspended or closed**: `update_corporate_wallet_config(auto_topup_enabled=False)` so we stop charging a frozen company.
- **closed is terminal**: any transition out of `closed` returns `409 Conflict`.

---

## 6. End-to-End Flows

### 6.1 Onboarding (company signup → bookable)

```
Admin                 Backend                     Stripe               Postgres
─────                 ───────                     ──────               ────────
POST /admin/corp-accts
                ───►  insert_corporate_account
                                                                      status=pending_verification

POST /admin/.../kyb-upload-url
                ───►  create_kyb_upload_url
                      signed URL ◄─ Supabase Storage
                ◄───

[admin uploads PDF directly to Storage via signed URL]

POST /admin/.../kyb-review {approve:true}
                ───►  record_kyb_decision                              status=active
                      ensure_corporate_wallet                          corporate_wallets row (balance 0)
                      stripe.Customer.create ─►
                                              ◄─ cus_...
                      update_corporate_stripe_customer_id              stripe_customer_id persisted
                ◄───  CorporateAccountResponse
```

After this, the company is `active` with a zero-balance wallet and a Stripe customer — but no payment method yet. An admin must attach a card in the Stripe Dashboard (or via a future self-serve flow) before top-ups can charge.

### 6.2 Manual top-up

```
POST /admin/.../wallet/topup {amount: 500}
   │
   ├─► assert company.status == 'active' && stripe_customer_id set
   ├─► assert wallet exists
   ├─► stripe.PaymentIntent.create(
   │       amount=50000, customer=cus_..., off_session=True, confirm=True,
   │       metadata={scope:'corporate_topup', company_id, wallet_id, initiated_by:<admin>})
   └─► return {payment_intent_id, client_secret}

[Stripe charges default PM, fires webhook]

POST /webhooks/stripe  (payment_intent.succeeded, scope=corporate_topup)
   │
   ├─► apply_topup(wallet_id, amount, stripe_pi, actor_user_id, notes)
   │       └─► RPC corporate_wallet_apply_delta
   │              • row-lock wallet
   │              • if stripe_pi already in ledger → no-op (idempotent)
   │              • balance += amount
   │              • INSERT corporate_wallet_transactions (type='topup')
   └─► mark_stripe_event_processed(event_id)
```

### 6.3 Auto-topup loop (every 10 min)

```
run_autotopup_tick
   │
   ├─► list_wallets_needing_autotopup
   │       (auto_topup_enabled=true AND balance < threshold)
   │
   └─► for each wallet:
          ├─ skip if company not active or no stripe_customer_id
          ├─ get_default_payment_method(customer) — card list, first one
          ├─ sum_autotopups_today(wallet_id) — already charged today
          ├─ skip if today_sum + amount > daily_cap
          └─ stripe.PaymentIntent.create(
                 off_session=True, confirm=True,
                 metadata={scope:'corporate_topup', initiated_by:'autotopup', ...})
             → webhook path identical to §6.2
```

### 6.4 Low-balance email (every hour)

```
run_low_balance_tick
   └─► for each wallet with auto_topup_enabled=false AND balance < threshold:
         ├─ skip if no billing_email
         ├─ skip if last notified < 12 h ago   ◄── rate limit via low_balance_notified_at
         ├─ send_email(billing_email, "…balance low…")
         └─ mark_low_balance_notified(wallet_id)
```

### 6.5 Suspend / close / reactivate

Single endpoint `POST /admin/corporate-accounts/{id}/status` with body `{status, reason?}`:

- Rejects any exit from `closed` (terminal).
- Updates `corporate_accounts.status`.
- If moving to `suspended` or `closed` and wallet has `auto_topup_enabled=true`, flips it to `false` in the same request. This is the single choke-point that keeps a frozen company from being silently charged.

---

## 7. Admin Dashboard (Next.js)

All under `admin-dashboard/src/app/dashboard/corporate-accounts/`:

| Page | Route | Purpose |
|---|---|---|
| `page.tsx` | `/corporate-accounts` | Company list; filters (status, size_tier, search); create/edit/delete modals. |
| `[id]/page.tsx` | `/corporate-accounts/{id}` | Detail: account fields, status transition buttons, wallet panel (balance, adjust, config), transaction history. |
| `kyb-queue/page.tsx` | `/corporate-accounts/kyb-queue` | Queue of `pending_verification` companies for KYB review. |

API client lives in `admin-dashboard/src/lib/` — it's a thin fetch wrapper shared with other admin panels.

---

## 8. Shared / Reused Components

The corporate feature deliberately stays on top of existing infra rather than forking it.

| Concern | Reused from | Notes |
|---|---|---|
| Admin auth | `backend/dependencies.py::get_admin_user` | Same dependency that protects other `/admin` routes. Role-check happens against `users.role`, never the JWT. |
| Stripe client | Single `stripe` SDK import; same secret as rider flows | Rider vs. corporate top-ups are distinguished by `metadata.scope` in the webhook dispatcher. |
| Stripe webhook | `backend/routes/webhooks.py` | One endpoint, one signature check, one `stripe_events` dedup table. Corporate is a branch inside the dispatch. |
| Email send | `features.send_email` | Same helper the rest of the product uses; low-balance emails go through it. |
| DB layer | `backend/db_supabase.py` | Same `run_sync` executor, `_rows_from_res`, `_single_row_from_res`, `_serialize_for_api` helpers. No new ORM. |
| Request schemas | Pydantic v2, `extra='forbid'` on bodies | Consistent with existing `/admin` routes. |
| Error handling | `HTTPException` + explicit status codes | No custom exception hierarchy was introduced. |
| Migrations | `backend/migrations/*.sql`, applied in order by the migration runner | Corporate migrations are 05, 17, 27, 28, 29. |

Corporate-specific pieces (not shared):

- Postgres RPC `corporate_wallet_apply_delta` — row-locked ledger mutation; unique to corporate.
- `services/corporate_wallet_service.py` — thin wrapper over the RPC.
- Two scheduled loops in `backend/utils/corporate_*.py` — run in-process; nothing else in the backend does this.
- `corporate_wallet_transactions.stripe_payment_intent_id` partial unique index — the idempotency key for topups.

---

## 9. Correctness Invariants

Things that must stay true; breaking any of them is a bug:

1. **All wallet mutations go through `corporate_wallet_apply_delta`.** Never `UPDATE corporate_wallets SET balance = …` from application code. The RPC is the only thing that keeps balance and ledger atomic.
2. **Stripe PI is the idempotency key for top-ups.** A retried webhook must never double-credit. The partial unique index on `corporate_wallet_transactions.stripe_payment_intent_id` enforces this at the DB.
3. **`closed` is terminal.** Guard lives in `change_company_status`; don't add back-doors.
4. **Suspend / close disables auto-topup.** Prevents silently charging a frozen company. Guard lives in the same endpoint.
5. **`soft_negative_floor` applies only to `master` scope.** Enforced inside the RPC. Allowance and member scopes are allowed to go negative to represent debt.
6. **Role comes from DB, not JWT.** `get_admin_user` reads `users.role`; a forged `role: super_admin` claim must not grant access. This is a project-wide rule and corporate inherits it.
7. **RLS is on for every B2B table.** If you add a table, enable RLS in the same migration and add at least the admin-full-access policy.

---

## 10. Test Matrix

Under `backend/tests/`, the corporate suite is 19 files:

| Test | What it pins |
|---|---|
| `test_corporate_admin_routes.py` | list/filter/pagination of the admin endpoint |
| `test_corporate_autotopup.py` | scheduler tick: PM fallback, daily cap, PI creation |
| `test_corporate_b2b_schema.py` | migrations applied: tables + columns + constraints |
| `test_corporate_db_helpers.py` | DB helpers return shapes the routes expect |
| `test_corporate_e2e_foundation.py` | create → KYB approve → suspend → reactivate happy path |
| `test_corporate_e2e_wallet.py` | approve → Stripe customer → wallet → topup intent → webhook credit → suspend freezes autotopup |
| `test_corporate_kyb.py` | KYB decision recording + status flip |
| `test_corporate_kyb_upload.py` | signed upload URL generation + content-type validation |
| `test_corporate_low_balance.py` | hourly tick: email sent + 12 h rate limit |
| `test_corporate_schemas.py` | Pydantic validation (BN, tax region, emails) |
| `test_corporate_status.py` | status transition guard; closed-is-terminal |
| `test_corporate_stripe_customer.py` | Stripe customer created on KYB approve only |
| `test_corporate_validators.py` | CRA BN + Canadian tax region validators |
| `test_corporate_wallet_bootstrap.py` | `ensure_corporate_wallet` is idempotent, fires on approve only |
| `test_corporate_wallet_config.py` | config PUT validation (both threshold+amount required to enable) |
| `test_corporate_wallet_freeze.py` | suspend/close disables autotopup; reactivate doesn't re-enable |
| `test_corporate_wallet_routes.py` | topup + adjust endpoints; soft_negative_floor |
| `test_corporate_wallet_view.py` | GET wallet: balance + ledger + pagination |
| `test_corporate_webhook.py` | Stripe `payment_intent.succeeded` with `corporate_topup` scope → `apply_topup` |

---

## 11. Known Gaps / Follow-ups

Things the current implementation doesn't yet do:

- **Self-serve employee onboarding.** `corporate_allowed_domains` is populated but no rider-app sign-up flow matches it yet.
- **Ride-time policy enforcement.** `corporate_policies` + `corporate_policy_evaluations` tables exist but the ride booking path hasn't integrated the policy engine.
- **Row-scoped RLS** for members and wallet. Only admin access is policed by RLS today; employee reads still go through Python checks.
- **Retry / backfill** for Stripe webhook misses. Stripe's own retry is relied on; no reconciliation job exists.
- **Allowance resets.** `allowance_grant`, `allowance_reset`, and `allowance_rollback` ledger types are allocated but no scheduled job writes them yet.
