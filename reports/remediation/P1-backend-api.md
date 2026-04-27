# P1 — High Priority: Fix Before Beta Launch (Backend API v1)

These 10 items must be resolved before sending the app to real beta testers.
They include race conditions that allow double-spending of loyalty points and
promo codes, Stripe refund errors that silently close disputes without paying
the rider, and IDOR vulnerabilities on corporate admin endpoints.

**Source audit:** `reports/audits/2026-04-26-backend-api-v1.txt`
**Estimated total effort:** ~3–4 days

---

## P1-1 · Loyalty Points Can Be Double-Awarded for the Same Ride

**What's wrong:** The idempotency check queries for an existing loyalty
transaction, and if absent, awards points and inserts a new row — in two
separate steps. Two concurrent requests for the same ride both see no existing
row and both award points.

**File to fix:** `backend/routes/loyalty.py` — `earn_points_for_ride`.
1. Add a database-level UNIQUE constraint:
   ```sql
   ALTER TABLE loyalty_transactions ADD CONSTRAINT uq_loyalty_user_ride
   UNIQUE (user_id, ride_id);
   ```
2. Catch the unique-constraint violation on INSERT and return the existing
   transaction as a successful no-op.

**Effort:** 2–3 hours

---

## P1-2 · Promo Code Can Be Redeemed More Times Than max_uses Allows

**What's wrong:** The apply_promo flow validates the promo (checks uses < max_uses),
inserts an application, then increments uses in a separate UPDATE. Two concurrent
requests for a max_uses=1 promo both see uses=0, both insert applications,
then both increment — final uses=2 when max_uses=1.

**File to fix:** `backend/routes/promotions.py` — `apply_promo`. Replace the
validate-then-increment pattern with a single atomic SQL:
```sql
UPDATE promotions SET uses = uses + 1
WHERE id = $id AND uses < max_uses
RETURNING id
```
Zero rows returned → promo exhausted → raise HTTPException(409).

**Effort:** 2–3 hours

---

## P1-3 · Dispute Refund — No Stripe Idempotency Key

**What's wrong:** `stripe.Refund.create()` is called without an
`idempotency_key`. If the connection drops after Stripe processes the refund,
a retry creates a second refund against the same PaymentIntent.

**File to fix:** `backend/routes/disputes.py` — `admin_resolve_dispute`. Add:
```python
stripe.Refund.create(
    payment_intent=ride.get("stripe_payment_intent_id"),
    amount=refund_amount_cents,
    idempotency_key=f"refund-dispute-{dispute_id}",
)
```

**Effort:** 15 minutes

---

## P1-4 · Promo Discount Value Has No Maximum — Arbitrary Financial Loss

**What's wrong:** The `discount_value` field on a promo code has no upper bound.
An admin (or, until P0-2 is fixed, any unauthenticated user) can create a promo
with `discount_value=999999` and `type="flat"`, making every ride effectively
free and costing the platform money on every redemption.

**File to fix:** `backend/routes/promotions.py` — `CreatePromoCodeRequest` model.
Add:
```python
discount_value: Decimal = Field(..., gt=0, le=500)  # flat: max $500 off
# Add a validator: if type == "percentage", le=100
```

**Effort:** 1 hour

---

## P1-5 · Dispute Resolution — Stripe Failure Leaves Dispute "Resolved" Without Refund

**What's wrong:** The dispute status is set to "resolved" in the DB first, then
`stripe.Refund.create()` is called. If Stripe fails, the exception is swallowed
with a bare `except Exception` and execution continues. The dispute is closed
but no money was returned to the rider.

**File to fix:** `backend/routes/disputes.py` — `admin_resolve_dispute`. Reorder:
1. Call `stripe.Refund.create()` first (with idempotency key from P1-3)
2. Only update the dispute status to "resolved" if Stripe returns success
3. On Stripe failure, return HTTPException(502) so the admin knows to retry

**Effort:** 2–3 hours

---

## P1-6 · Loyalty Redemption — Points Deducted But Wallet Never Credited

**What's wrong:** The redemption endpoint deducts loyalty points first, then
credits the rider's wallet. If the wallet credit throws, the exception is caught
and logged but the loyalty deduction is not reversed. The rider silently loses
their points with no financial benefit.

**File to fix:** `backend/routes/loyalty.py` — `redeem_points`. Reverse the
operation order:
1. Credit the wallet first
2. If the wallet credit succeeds, deduct the points
3. If the points deduction then fails, issue a compensating wallet debit and
   return HTTPException(503, "Redemption failed — please retry")

**Effort:** 2–3 hours

---

## P1-7 · GET /fare-split/ride/{ride_id} — Any Authenticated User Can See Participant Phones

**What's wrong:** Any authenticated rider can call this endpoint with any
`ride_id` and receive the full fare-split record including participant phone
numbers. There is no check that the caller is the ride owner or a participant.

**File to fix:** `backend/routes/fare_split.py` — `get_fare_split_by_ride`.
After fetching the split, add:
```python
is_owner = (split.get("requester_id") == current_user["id"])
is_participant = any(
    p.get("user_id") == current_user["id"] for p in participants
)
if not (is_owner or is_participant):
    raise HTTPException(403, "Access denied")
```
Additionally remove `phone_number` from the participant response payload.

**Effort:** 1 hour

---

## P1-8 · IDOR on Corporate Wallet and Account Admin Endpoints

**What's wrong:** All corporate wallet endpoints (`GET /{company_id}/wallet`,
top-up, adjust, config) and all corporate account record endpoints (GET, PUT,
DELETE, status-change) authenticate via `get_current_admin` but perform no
ownership check. Any admin can read or modify any company's wallet or account
by changing the `company_id` path parameter.

**File to fix:** `backend/routes/corporate_wallet.py` and
`backend/routes/corporate_accounts.py`. After fetching the account, add:
```python
# If admins are org-scoped (not global Spinr staff):
if fetched_account.get("admin_email") != current_admin.get("email"):
    raise HTTPException(403, "Access denied")
```
If all admins are global Spinr staff, add a code comment documenting this
assumption so the next developer doesn't add per-org admin roles without
revisiting these endpoints.

**Effort:** 1–2 hours

---

## P1-9 · Strip Phone Numbers from Admin Dispute and Fare Split Responses

**What's wrong:** `GET /admin/disputes` returns rider phone numbers in the
enrichment payload (~line 137 of disputes.py). `GET /fare-split/ride/{ride_id}`
returns participant phone numbers. Both are unnecessary PII exposure.

**File to fix:**
- `backend/routes/disputes.py` — remove `phone_number` from the enriched
  dispute dict (or mask it: `"+1***" + phone[-4:]`)
- `backend/routes/fare_split.py` — remove `phone_number` from the participant
  response payload in `get_fare_split_by_ride`

**Effort:** 30 minutes

---

## P1-10 · Admin Disputes List — N+1 Query Pattern (100+ DB Calls Per Page Load)

**What's wrong:** After fetching 50 dispute rows, the endpoint makes two
additional DB calls per row (`get_user_by_id` + `get_ride`), producing up to
101 round-trips per request. At 5–15ms per Supabase call, this adds 0.5–1.5
seconds of pure DB latency to every admin dispute page load.

**File to fix:** `backend/routes/disputes.py` — `admin_get_disputes`. Replace
the per-dispute loop with batched fetches:
```python
user_ids = list({d["rider_id"] for d in disputes if d.get("rider_id")})
ride_ids = list({d["ride_id"] for d in disputes if d.get("ride_id")})
users = await db.get_rows("users", {"id": {"$in": user_ids}})
rides = await db.get_rows("rides", {"id": {"$in": ride_ids}})
users_by_id = {u["id"]: u for u in (users or [])}
rides_by_id = {r["id"]: r for r in (rides or [])}
# Then enrich each dispute from the dicts — 3 queries total
```

**Effort:** 2 hours

---

## Checklist

- [x] P1-1 Add UNIQUE(user_id, ride_id) to loyalty_transactions; handle conflict as no-op
- [x] P1-2 Replace promo uses increment with atomic conditional UPDATE
- [x] P1-3 Add idempotency_key=f"refund-dispute-{dispute_id}" to stripe.Refund.create()
- [x] P1-4 Add le=500 bound on promo discount_value (le=100 for percentage type)
- [x] P1-5 Move Stripe refund call before DB status update in admin_resolve_dispute
- [x] P1-6 Reverse loyalty redemption order; add compensating credit on failure
- [x] P1-7 Add ownership check to GET /fare-split/ride/{ride_id}; remove phone from response
- [x] P1-8 Add company ownership check or document global-admin assumption in corporate endpoints
- [x] P1-9 Remove/mask phone_number from dispute and fare-split response payloads
- [x] P1-10 Replace per-dispute DB loop with batch user + ride fetch in admin_get_disputes
