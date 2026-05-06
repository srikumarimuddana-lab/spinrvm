# P2 — Medium Priority: Fix Before Public Launch (Backend API v1)

These 21 items are medium-severity findings from the 2026-04-26 backend API
audit. They cover financial rounding errors, missing input bounds, silent error
swallowing, PIPEDA accountability gaps, N+1 query patterns, and two incomplete
features (fare-split refunds, GeoJSON validation) that will cause support
tickets or incorrect billing at scale.

**Source audit:** `reports/audits/2026-04-26-backend-api-v1.txt`
**Estimated total effort:** ~4–6 days

---

## P2-1 · Dispute Refund Amount Not Bounded by Original Charge

**What's wrong:** The `refund_amount` field in `ResolveDisputeRequest` has no
upper-bound validation. An admin can submit a refund larger than the original
ride fare. Stripe rejects over-refunds at the API layer, but only after the
application has already set the dispute to "resolved" — leaving it in a broken
state with no refund issued.

**File to fix:** `backend/routes/disputes.py` — `admin_resolve_dispute`.
After fetching the ride, add:
```python
ride_fare = Decimal(str(ride.get("total_fare") or ride.get("fare_amount") or 0))
if req.refund_amount and req.refund_amount > ride_fare:
    raise HTTPException(400, "Refund cannot exceed original fare")
```

**Effort:** 30 minutes

---

## P2-2 · Corporate Wallet Top-Up — No Stripe Idempotency Key

**What's wrong:** `stripe.PaymentIntent.create()` in the corporate wallet
top-up endpoint is called without an `idempotency_key`. A network timeout after
Stripe processes the charge but before the server receives the response will
produce a duplicate `PaymentIntent` on retry. The caller-side
`@idempotent_endpoint` decorator does not prevent Stripe-side duplication.

**File to fix:** `backend/routes/corporate_wallet.py` — `topup` endpoint.
Add a timestamp-bucketed idempotency key:
```python
import time
bucket = int(time.time() // 60)  # 1-minute window
stripe.PaymentIntent.create(
    ...,
    idempotency_key=f"corp-topup-{wallet_id}-{bucket}",
)
```

**Effort:** 30 minutes

---

## P2-3 · Loyalty Redemption — Float Used in Dollar Conversion

**What's wrong:** The loyalty redemption endpoint used Python's built-in
`round(req.points / REDEMPTION_RATE, 2)`, which employs banker's rounding
(round-to-even). Over thousands of redemptions this introduces a systematic
bias that violates Canadian financial rounding conventions. All other monetary
arithmetic in the codebase uses `Decimal` with `quantize(ROUND_HALF_UP)`.

**File to fix:** `backend/routes/loyalty.py` — `redeem_points`. Replace with:
```python
credit_amount = (Decimal(req.points) / Decimal(REDEMPTION_RATE)).quantize(
    Decimal("0.01"), rounding=ROUND_HALF_UP
)
```

**Effort:** 15 minutes

---

## P2-4 · fares.py — lat/lng Query Params Lack Bounds

**What's wrong:** The `lat` and `lng` query parameters in `GET /fares/fares`
accept any float, including `float("nan")`, `float("inf")`, or out-of-range
values like `lat=999`. These flow into `resolve_service_area_for_point()` and
produce confusing 500 errors rather than a clean 422.

**File to fix:** `backend/routes/fares.py` — fare estimate endpoint. Change:
```python
lat: float = Query(..., ge=-90.0, le=90.0)
lng: float = Query(..., ge=-180.0, le=180.0)
```

**Effort:** 15 minutes

---

## P2-5 · favorites.py — GPS Coordinate Bounds Not Validated

**What's wrong:** `pickup_lat`, `pickup_lng`, `dropoff_lat`, and `dropoff_lng`
in `SaveFavoriteRequest` are unbound floats. A user can store
`pickup_lat=9999.0` in their favorites; this breaks downstream route-finding
calls when the favorite is used to pre-fill a ride request.

**File to fix:** `backend/routes/favorites.py` — `SaveFavoriteRequest`. Apply
`ge`/`le` field constraints:
```python
pickup_lat: float = Field(..., ge=-90.0, le=90.0)
pickup_lng: float = Field(..., ge=-180.0, le=180.0)
dropoff_lat: float = Field(..., ge=-90.0, le=90.0)
dropoff_lng: float = Field(..., ge=-180.0, le=180.0)
```

**Effort:** 30 minutes

---

## P2-6 · favorites.py — name Field Has No max_length

**What's wrong:** The `name` field for a saved favorite has no length limit.
A user can submit a multi-megabyte string that is stored and returned on every
`GET /favorites`. With 20 favorites each carrying a 1 MB name, a single API
response can be 20 MB.

**File to fix:** `backend/routes/favorites.py` — `SaveFavoriteRequest`. Add:
```python
name: str = Field(..., min_length=1, max_length=100)
```

**Effort:** 5 minutes

---

## P2-7 · wallet.py — recipient_phone Not Validated in Transfer

**What's wrong:** `recipient_phone` in `TransferRequest` is an unvalidated
plain string. All other phone fields in the codebase enforce E.164 `+1` format.
An invalid phone string fails to match any user and returns a misleading 404
rather than a descriptive 422.

**File to fix:** `backend/routes/wallet.py` — `TransferRequest`. Add:
```python
recipient_phone: str = Field(..., pattern=r'^\+1\d{10}$')
```

**Effort:** 5 minutes

---

## P2-8 · corporate_wallet.py — AdjustRequest.amount Has No Bounds

**What's wrong:** The `amount` field on the wallet adjustment request accepts
any float (positive or negative) with no magnitude limit. An admin can credit
or debit a corporate wallet by $1,000,000+ in a single call, with only a
free-text `notes` field as a guardrail.

**File to fix:** `backend/routes/corporate_wallet.py` — `AdjustRequest`. Add:
```python
amount: float = Field(..., ge=-100000.0, le=100000.0)
```

**Effort:** 30 minutes

---

## P2-9 · corporate_company.py — Allowance Float Instead of Decimal

**What's wrong:** Allowance amounts accepted from request bodies are `float`
and passed directly to the grant service without `Decimal` conversion. The
service forwards them to a Postgres RPC as-is. Floating-point arithmetic on
monetary values violates the Spinr coding standard (Decimal-only for money).

**File to fix:** `backend/routes/corporate_company.py` — allowance grant
endpoints. At the route layer before calling the service:
```python
amount = Decimal(str(req.amount)).quantize(Decimal("0.01"), ROUND_HALF_UP)
```

**Effort:** 30 minutes

---

## P2-10 · Corporate Account — Wallet Creation Failure Not Handled on KYB Approval

**What's wrong:** `ensure_corporate_wallet()` is called when KYB status is set
to "approved" but exceptions are not caught. If wallet creation fails (e.g., DB
timeout), the company's KYB status becomes "approved" but no wallet exists. The
next ride billed to the company will produce a confusing 500.

**File to fix:** `backend/routes/corporate_accounts.py` — `kyb_review`.
Wrap in try/except; on failure return 503 and log for manual recovery:
```python
try:
    await ensure_corporate_wallet(company_id)
except Exception as e:
    logger.error(f"Wallet provisioning failed for company {company_id}: {e}")
    raise HTTPException(503, "KYB approved but wallet provisioning failed — retry")
```

**Effort:** 1–2 hours

---

## P2-11 · /wallet/transactions — DB Error Silently Returns Empty List

**What's wrong:** `db.get_rows()` in `get_transactions` is wrapped in a
`try/except` that catches any exception and returns an empty list with HTTP 200.
A rider with 50 transactions sees an empty history on any DB error, with no
indication of the problem.

**File to fix:** `backend/routes/wallet.py` — `get_transactions`. Remove the
swallow or re-raise:
```python
# Before (wrong):
try:
    txns = await db.get_rows(...)
except Exception:
    return []

# After (correct):
txns = await db.get_rows(...)
```

**Effort:** 15 minutes

---

## P2-12 · promotions.py — Bare except in get_available_promos

**What's wrong:** A bare `except Exception` in `get_available_promos` silently
drops any promo that raises during per-promo validation. A promo with corrupted
data is invisibly skipped — admins never know why a promo stopped appearing in
the app.

**File to fix:** `backend/routes/promotions.py` — `get_available_promos`. Log
at ERROR level with the promo ID before continuing:
```python
except Exception as e:
    logger.error(f"Error validating promo {promo.get('id')}: {e}")
    continue
```

**Effort:** 30 minutes

---

## P2-13 · No Audit Trail on Dispute Resolution (PIPEDA Accountability)

**What's wrong:** When an admin resolves a dispute — approving a refund or
rejecting a claim — there is no audit log entry. PIPEDA requires accountability
for decisions affecting personal information. Without a log there is no way to
answer a PIPEDA access request ("who decided my dispute, when, and on what
basis?").

**File to fix:** `backend/routes/disputes.py` — `admin_resolve_dispute`. After
the dispute is resolved, call:
```python
await log_audit(
    "dispute_resolved", "dispute", dispute_id, current_admin.get("email"),
    f"Resolution: {req.resolution}. Amount: {req.refund_amount}"
)
```

**Effort:** 30 minutes

---

## P2-14 · promotions.py — N+1 Per-Promo Usage Check in get_available_promos

**What's wrong:** After fetching up to 100 active promos, the endpoint makes an
additional `count_documents()` DB call per promo to check the current user's
usage. 100 promos → 100 extra DB calls per request, causing noticeable latency
on the booking screen.

**File to fix:** `backend/routes/promotions.py` — `get_available_promos`.
Batch-fetch all usage for the current user and join in memory:
```python
usage_rows = await db.get_rows(
    "promo_applications",
    {"user_id": current_user["id"], "promo_id": {"$in": promo_ids}},
)
used_counts = {}
for row in usage_rows:
    used_counts[row["promo_id"]] = used_counts.get(row["promo_id"], 0) + 1
```

**Effort:** 2–3 hours

---

## P2-15 · corporate_company.py — billing/summary Truncated at limit=1000

**What's wrong:** `list_company_ride_payment_sources()` was called with a
hardcoded `limit=1000`. For a corporate account with more than 1000 rides in
the period, the summary silently truncates and the totals displayed in the
dashboard are wrong.

**File to fix:** `backend/routes/corporate_company.py` — `billing_summary`.
Paginate through all rows with a `while True` loop:
```python
page_size = 1000
skip = 0
while True:
    page = await list_company_ride_payment_sources(..., limit=page_size, offset=skip)
    all_rows.extend(page)
    if len(page) < page_size:
        break
    skip += page_size
```

**Effort:** 2–3 hours

---

## P2-16 · corporate_company.py — billing/statements Truncated at limit=5000

**What's wrong:** Same pattern as P2-15 in the monthly statement endpoint
(`limit=5000`). Silently wrong totals for high-volume accounts.

**File to fix:** `backend/routes/corporate_company.py` — `billing_statement`.
Apply the same paginated fetch pattern as P2-15.

**Effort:** 2–3 hours

---

## P2-17 · corporate_rider.py — Allowance Requests Fetched for Entire Company

**What's wrong:** The rider's "my allowance requests" endpoint fetched all
allowance requests for the entire company, then filtered to the current rider
in Python. For a company with 500 riders making monthly requests, this pulls
thousands of rows just to return one rider's handful.

**File to fix:** `backend/routes/corporate_rider.py` — `my_requests`. Pass
`member_id` as a DB-level filter so the query is scoped at the database:
```python
return await list_company_allowance_requests(
    company_id, statuses=None, member_id=membership["id"]
)
```

**Effort:** 1 hour

---

## P2-18 · corporate_company.py — list_company_allowances Unbounded

**What's wrong:** No limit or pagination is applied to the allowances query.
A company with 10,000 members returns all allowance rows in a single response,
producing a very large payload and straining the DB.

**File to fix:** `backend/routes/corporate_company.py` — `list_allowances`
endpoint. Add `skip`/`limit` query parameters consistent with other list
endpoints and apply them to the DB query.

**Effort:** 1 hour

---

## P2-19 · Fare Split — No Refund on Cancellation or Participant Decline

**What's wrong:** When a split is cancelled by the requester, or when a
participant who has already paid declines their portion, no refund or wallet
credit is issued. The paid participant's balance is permanently reduced with
no compensation. This will generate support tickets at any meaningful scale.

**File to fix:** `backend/routes/fare_split.py` — `cancel_split` and
`participant_respond`. On cancellation, iterate `status="paid"` participants
and issue a compensating wallet credit equal to their share. On decline after
payment, issue a credit and recompute remaining shares.

**Effort:** 3–4 hours

---

## P2-20 · corporate_company.py — GeoJSON Geofence Not Schema-Validated

**What's wrong:** The `geofence` field of a company policy is accepted as a raw
dict and stored without GeoJSON schema validation. A malformed geofence (missing
`"features"` array, wrong coordinate order) passes the Pydantic layer but
breaks geofence evaluation at ride-request time, blocking all corporate rides
for that company.

**File to fix:** `backend/routes/corporate_company.py` — `set_policy` or the
`CorporatePolicy` schema. Add a `@field_validator` that checks:
```python
assert geofence.get("type") == "FeatureCollection", "type must be FeatureCollection"
assert isinstance(geofence.get("features"), list), "features must be a list"
for f in geofence["features"]:
    assert "geometry" in f, "each feature must have a geometry"
    assert f["geometry"].get("type") in {"Polygon", "MultiPolygon"}, "geometry type invalid"
    assert "coordinates" in f["geometry"], "geometry must have coordinates"
```

**Effort:** 1–2 hours

---

## P2-21 · Fare Split Payment — Non-Atomic Wallet Debit + Status Update

**What's wrong:** In `participant_pay`, the wallet debit and the participant
status update (`"paid"`) are two sequential DB calls with no transaction. If the
status update fails after the wallet has already been debited, the participant's
balance is reduced but they still appear to owe money. A retry will double-debit
them.

**File to fix:** `backend/routes/fare_split.py` — `participant_pay`. Wrap both
operations in a Postgres function called via RPC (added in migration 52,
`fare_split_pay_share`) so both succeed or both roll back atomically.

**Effort:** 2 hours (Postgres migration + Python call-site update)

---

## Checklist

- [x] P2-1  Cap `refund_amount` to original fare in `admin_resolve_dispute`
- [x] P2-2  Add Stripe idempotency key to corporate wallet top-up
- [x] P2-3  Replace `round()` with `Decimal.quantize(ROUND_HALF_UP)` in `redeem_points`
- [x] P2-4  Add `ge`/`le` bounds to `lat`/`lng` query params in `fares.py`
- [x] P2-5  Add GPS coordinate bounds to `SaveFavoriteRequest`
- [x] P2-6  Add `max_length=100` to `SaveFavoriteRequest.name`
- [x] P2-7  Add `+1XXXXXXXXXX` phone pattern to `TransferRequest.recipient_phone`
- [x] P2-8  Add magnitude bounds (`ge=-100000, le=100000`) to `AdjustRequest.amount`
- [x] P2-9  Convert allowance amounts to `Decimal` before calling the grant service
- [x] P2-10 Wrap `ensure_corporate_wallet()` in try/except; return 503 on failure
- [x] P2-11 Remove silent empty-list fallback in `/wallet/transactions`
- [x] P2-12 Log promo validation errors at `ERROR` level in `get_available_promos`
- [x] P2-13 Add `log_audit()` call on dispute resolution (PIPEDA accountability)
- [x] P2-14 Batch promo-usage check in `get_available_promos` (eliminate N+1)
- [x] P2-15 Paginate `billing/summary` to remove `limit=1000` truncation
- [x] P2-16 Paginate `billing/statements` to remove `limit=5000` truncation
- [x] P2-17 Scope `allowance-requests` to `member_id` at the DB level in `corporate_rider.py`
- [x] P2-18 Add `skip`/`limit` pagination to `list_company_allowances`
- [x] P2-19 Issue wallet refund on fare-split cancellation and participant decline
- [x] P2-20 Validate GeoJSON `FeatureCollection` schema in `set_policy`
- [x] P2-21 Make wallet debit + status update atomic via `fare_split_pay_share` RPC (migration 52)
