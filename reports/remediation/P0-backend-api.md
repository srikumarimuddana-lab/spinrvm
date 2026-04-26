# P0 — Critical: Fix Before Any Beta Testing (Backend API v1)

These 6 items must be resolved immediately. They include unauthenticated admin
endpoints that expose rider PII, an authorization bypass that lets any rider
join any corporate account, and three TOCTOU race conditions that corrupt wallet
balances and enable fund loss.

**Source audit:** `reports/audits/2026-04-26-backend-api-v1.txt`
**Estimated total effort:** ~2 days

---

## P0-1 · Admin Dispute Endpoints Have No Authentication

**What's wrong:** `GET /admin/disputes` and `PUT /admin/disputes/{id}/resolve`
have no `get_current_admin` dependency. Any unauthenticated HTTP request can
list all disputes (including rider phone numbers) and approve Stripe refunds.

**File to fix:** `backend/routes/disputes.py` — `admin_get_disputes` and
`admin_resolve_dispute` endpoint functions. Add:
```python
current_admin: dict = Depends(get_current_admin)
```
to both function signatures.

**Effort:** 15 minutes

---

## P0-2 · Admin Promo Endpoints Have No Authentication

**What's wrong:** Four endpoints in `promotions.py`'s `admin_router` —
`GET /admin/promo-codes`, `POST /admin/promo-codes`,
`PUT /admin/promo-codes/{id}`, `DELETE /admin/promo-codes/{id}` — require
no authentication. Any caller can create unlimited-budget promo codes or
delete existing campaigns.

**File to fix:** `backend/routes/promotions.py` — apply a router-level
dependency to the `admin_router`:
```python
admin_router = APIRouter(dependencies=[Depends(get_current_admin)])
```

**Effort:** 15 minutes

---

## P0-3 · /rider/work-profile/join-domain Authorization Bypass

**What's wrong:** A rider can join any corporate account by supplying an
arbitrary `company_id`. The service (`corporate_membership_service.py`) does
not validate that the rider's email domain is in the company's allowed domain
list — it trusts the caller. Any authenticated rider can enroll in any company.

**File to fix:** `backend/routes/corporate_rider.py` — before calling
`join_via_domain()`, add:
```python
user_email = current_user["phone_or_email"]  # from JWT
domain = user_email.split("@")[-1].lower()
allowed = await db.get_rows(
    "corporate_allowed_domains",
    {"company_id": req.company_id, "domain": domain},
    limit=1
)
if not allowed:
    raise HTTPException(403, "Your email domain is not authorized for this company")
```

**Effort:** 2–3 hours

---

## P0-4 · /wallet/top-up — TOCTOU Race Condition (Balance Corruption)

**What's wrong:** The top-up reads the wallet balance into memory, computes
`new_balance = old_balance + amount`, then writes it back. Two concurrent
top-ups read the same `old_balance` and both write `old_balance + X`, so one
top-up is silently lost.

**File to fix:** `backend/routes/wallet.py` — `top_up` endpoint. Replace the
read-compute-write with an atomic Supabase RPC:
```python
result = await db_supabase.rpc(
    "wallet_increment_balance",
    {"p_wallet_id": wallet["id"], "p_amount": str(req.amount)}
)
# RPC executes: UPDATE wallets SET balance = balance + p_amount WHERE id = p_wallet_id RETURNING balance
```
Create migration `backend/migrations/XXXX_wallet_increment_rpc.sql`.

**Effort:** 3–4 hours

---

## P0-5 · /wallet/pay — TOCTOU Race + Non-Atomic Dual Update

**What's wrong:** Same TOCTOU race as P0-4 on the balance check. Additionally,
the wallet debit and the `ride.payment_status` update are two separate DB calls.
If the second fails, the rider's balance is debited but the ride is never marked
paid — funds disappear with no record.

**File to fix:** `backend/routes/wallet.py` — `pay` endpoint. Create a Postgres
function `wallet_pay_for_ride(p_wallet_id, p_ride_id, p_amount)` that:
1. Locks the wallet row with `SELECT FOR UPDATE`
2. Checks `balance >= p_amount`, raises if not
3. Debits the wallet: `UPDATE wallets SET balance = balance - p_amount`
4. Marks the ride paid: `UPDATE rides SET payment_status = 'paid'`
5. Returns the new balance

**Effort:** 4–5 hours

---

## P0-6 · /wallet/transfer — TOCTOU Race (Multi-Party, Money Disappears)

**What's wrong:** Sender balance is read without a lock. Two concurrent
transfers from the same sender both pass the sufficiency check → overdraft.
Additionally if the sender debit succeeds but the recipient credit fails,
the sender loses funds with no compensating record.

**File to fix:** `backend/routes/wallet.py` — `transfer` endpoint. Create a
Postgres function `wallet_transfer(p_sender_id, p_recipient_id, p_amount)` that:
1. Locks both wallet rows in ascending UUID order (deadlock prevention)
2. Checks sender `balance >= p_amount`
3. Debits sender and credits recipient in a single transaction
4. Returns both new balances

**Effort:** 5–6 hours

---

## Checklist

- [ ] P0-1 Add Depends(get_current_admin) to GET + PUT /admin/disputes endpoints
- [ ] P0-2 Add router-level Depends(get_current_admin) to promotions.py admin_router
- [ ] P0-3 Validate email domain against corporate_allowed_domains in join-domain endpoint
- [ ] P0-4 Replace top-up read-compute-write with atomic wallet_increment_balance RPC
- [ ] P0-5 Replace pay read-compute-write + dual update with wallet_pay_for_ride RPC
- [ ] P0-6 Replace transfer multi-step with atomic wallet_transfer RPC
