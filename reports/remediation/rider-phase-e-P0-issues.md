# Rider Phase E — P0 Issues: Fix Before Launch

**Source:** `reports/audits/2026-04-23-rider-app-v1-phase-e.txt` (2026-04-24)
**Branch:** `claude/review-pending-audits-Pu1aP`
**Total items:** 6 · **Estimated total effort:** ~20 h
**Owners:** backend (×5), legal + infra (×1)
**Launch gate:** all six resolved before public launch

These are bugs discovered during rider-app Phase E (D17–D22) audit that have
CRITICAL severity and/or `risk_score ≥ 64`. They are distinct from the 92
pre-existing rider-P0..P4 remediation items and from the 17 new driver
issues (DV-1..DV-17); those live in separate files and are tracked
separately.

---

## 19-1 · Rating field-name mismatch — drivers silently unrated

**Source finding:** `[19-1]` CRITICAL · `risk_score=64`

**What's wrong:** `backend/routes/rides.py` writes rider's rating to
field `rider_rating` at line 1773, but the driver-average aggregator reads
`driver_rating` at line 1793. Every ride rating is saved but never counted.
Driver `average_rating` column never updates regardless of how many riders
rate them.

**Why it matters:** Dispatch, safety screening, and fraud signals (RT-3)
all consume `driver_rating` / `average_rating`. A rider's actual rating
has no effect on any downstream behaviour. Silent data-quality failure —
hard to detect without an audit because no error fires.

**Blast radius:** org — every driver's rating is affected

**File to fix:** `backend/routes/rides.py` around line 1773

**How to fix:**
```python
# Before (line 1773):
await db_update("rides", {"id": ride_id}, {
    "rider_rating": rating_data.rating,
    "tip_amount": new_tip,
    ...
})

# After — unify on `driver_rating` (semantically: the rating the driver
# received from the rider):
await db_update("rides", {"id": ride_id}, {
    "driver_rating": rating_data.rating,
    "tip_amount": new_tip,
    ...
})
```

Also grep for every read/write of `rider_rating` and decide whether any
should be `driver_rating` (most likely all of them).

**Effort:** 1 h (fix + test + data-backfill plan for existing stale rows)
**Owner:** `backend`
**Regulations:** SK-CPPA
**Regression test:** Rate a ride; verify `driver.average_rating` moves on
next GET `/drivers/{id}`.

---

## 20-1 · Wallet UPDATE bypasses atomicity RPC + uses float + $set wrapper

**Source finding:** `[20-1]` CRITICAL · `risk_score=128`

**What's wrong:** Three compounding issues in one call-site:
1. `backend/routes/wallet.py:125-129` uses raw `db.update_one("wallets",
   {...}, {"$set": {...}})` instead of routing through the
   `corporate_wallet_apply_delta` Postgres RPC. No row-level lock, no
   idempotency, no audit trail.
2. The payload passes `float(new_balance)` — violates the CLAUDE.md rule
   that money arithmetic must use `Decimal` only.
3. The `{"$set": ...}` wrapper is a MongoDB-style pattern on a Supabase
   client that doesn't interpret it — same bug family as DV-2, different
   table.

**Why it matters:** Concurrent debits can silently lose data (no lock).
Float loses cents on large balances. `$set` wrapper silently writes
nothing if the Supabase client interprets it as a column name.

**Blast radius:** org — every wallet operation

**File to fix:** `backend/routes/wallet.py:125-129` and every other
wallet-balance mutation (`top_up`, `pay`, `transfer` paths, plus
`fare_split.py::pay_split_share`).

**How to fix:**
```python
# Before:
await db.update_one("wallets", {"id": wallet_id}, {"$set": {
    "balance": float(new_balance),
    ...
}})

# After:
from decimal import Decimal
# Route through the RPC that handles row-level lock + idempotency + audit
await db.rpc("corporate_wallet_apply_delta", {
    "p_wallet_id": wallet_id,
    "p_delta_cents": int((new_balance - current_balance) * 100),
    "p_idempotency_key": request_idempotency_key,
    "p_reason": "top_up",
})
```

Also sweep the codebase for `{"$set":` patterns in other Supabase updates
(grep `$set` repo-wide) — DV-2 covered `document_expiry.py`, 20-1 covers
`wallet.py`, and there may be more.

**Effort:** 6 h (fix + sweep + tests)
**Owner:** `backend`
**Regulations:** CRA, PCI-DSS, SOC2
**Regression test:** Concurrent debit test (no lost writes); ledger
re-derivation equals stored balance; grep confirms no `float()` in money
paths; grep confirms no `{"$set"` on Supabase updates.

---

## 20-2 · Daily Stripe ↔ DB ↔ wallet reconciliation cron missing

**Source finding:** `[20-2]` CRITICAL · `risk_score=128`

**What's wrong:** `docs/runbooks/stripe-reconciliation.md` specifies a
daily 02:00 UTC cron comparing Stripe `PaymentIntent` totals against
`financial_events` and wallet balance deltas. No such loop is spawned in
`backend/core/lifespan.py` (which already has 7 background loops). Any
drift between Stripe and DB is only detected when a rider escalates.

**Why it matters:** PCI-DSS, SOC2 CC9.1, CRA record-keeping all expect a
daily reconciliation process. Drift caused by webhook misses, idempotency
collisions, or partial failures surfaces days or weeks later as
chargebacks.

**Blast radius:** org — every payment

**File to fix:** `backend/utils/reconciliation.py` (create) +
`backend/core/lifespan.py` (spawn)

**How to fix:**
Follow the template in `docs/runbooks/stripe-reconciliation.md`. Specifically:
```python
# backend/utils/reconciliation.py
async def daily_reconciliation():
    yesterday = (datetime.utcnow() - timedelta(days=1)).date()
    stripe_total = sum_stripe_intents(yesterday)
    db_total = sum_financial_events(yesterday, event_type='stripe_charge')
    wallet_delta = wallet_balance_delta(yesterday)
    discrepancy = abs(stripe_total - db_total)
    if discrepancy > 1:
        alert_finance(level='HIGH', ...)
    else:
        emit_heartbeat('reconciliation.ok', stripe_total=stripe_total)
    archive_report(yesterday, stripe_total, db_total, wallet_delta)

# backend/core/lifespan.py — add to startup:
_spawn("reconciliation (daily 02:00 UTC)", daily_reconciliation, 86400)
```

**Blocked by:** 20-3 (`financial_events` table must exist first).

**Effort:** 6 h (implementation + test harness with mocked Stripe)
**Owner:** `backend`
**Regulations:** PCI-DSS, SOC2, CRA
**Regression test:** Mock Stripe, inject a $0.02 delta, verify alert fires
and an OPEN-ITEMS-TRACKER row is created within the cron window.

---

## 20-3 · `financial_events` append-only ledger table does not exist

**Source finding:** `[20-3]` CRITICAL · `risk_score=96`

**What's wrong:** D20 requires an append-only ledger for every money
event (ride charge, refund, wallet top-up, chargeback). The repo has
`stripe_events` (webhook dedup) but no `financial_events` table with RLS
blocking `UPDATE` / `DELETE`. Without it, reconciliation has nothing to
reconcile against, and CRA / SOC2 cannot prove the money trail.

**Why it matters:** Blocks 20-2. Also required independently by CRA
record-keeping and SOC2 CC9.1. An auditor asking "show me every charge
this rider made in June 2024" has no query target.

**Blast radius:** org

**File to fix:** `backend/migrations/NNN_financial_events.sql` (new)

**How to fix:**
```sql
CREATE TABLE financial_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type text NOT NULL CHECK (event_type IN (
        'stripe_charge', 'stripe_refund', 'stripe_dispute',
        'wallet_topup', 'wallet_debit', 'fare_settle',
        'fare_split_debit', 'driver_payout', 'tax_adjust'
    )),
    user_id uuid NOT NULL REFERENCES users(id),
    ride_id uuid REFERENCES rides(id),
    delta_cents bigint NOT NULL,
    ref text,  -- e.g. Stripe PaymentIntent ID
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX financial_events_user_created ON financial_events (user_id, created_at);
CREATE INDEX financial_events_type_created ON financial_events (event_type, created_at);

-- Append-only RLS: no role may UPDATE or DELETE
ALTER TABLE financial_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY insert_only ON financial_events FOR INSERT WITH CHECK (true);
CREATE POLICY select_own ON financial_events FOR SELECT
    USING (user_id = auth.uid() OR auth.role() = 'admin');
-- no UPDATE or DELETE policy → default deny
```

Wire a helper `emit_financial_event(event_type, user_id, ride_id,
delta_cents, ref, metadata)` and call it at every money transition
(complete-ride, tip, refund, wallet top-up, fare-split settle).

**Effort:** 4 h (migration + helper + wire-up in 6–8 call-sites)
**Owner:** `backend` + `data`
**Regulations:** CRA, SOC2
**Regression test:** Insert a ride → `financial_events` row created;
attempt `UPDATE financial_events` → denied by RLS.

---

## 21-2 · Rider pickup/dropoff addresses retained in driver response
after ride completion

**Source finding:** `[21-2]` CRITICAL · `risk_score=128`

**What's wrong:** `backend/routes/rides.py:1129,1136` — the driver-facing
GET `/rides/{id}` response for a completed ride still includes
`pickup_address` and `dropoff_address` fields. These should be scoped to
the ride window: drivers need the pickup before pickup and the dropoff
during the trip; after `status == 'completed'`, the addresses should be
stripped from the driver-side response.

**Why it matters:** Realizes threat-model `RI-2` (rank 128). A driver
can cache the response client-side, retaining the rider's home or work
address indefinitely. This is exactly the data-collection primitive
required for attack tree RAT-1 (stalking a specific rider via address
retention).

**Blast radius:** org — every rider over time

**File to fix:** `backend/routes/rides.py` around line 1129 (driver-facing
ride fetch) + any serializer that selects driver-visible fields.

**How to fix:**
```python
# In the driver-response serializer:
def driver_ride_view(ride: dict) -> dict:
    view = {
        "id": ride["id"],
        "status": ride["status"],
        "fare_amount": ride["fare_amount"],
        ...
    }
    # Only include addresses while the ride is active or before pickup
    if ride["status"] in ("searching", "driver_assigned",
                         "driver_arriving", "trip_in_progress"):
        view["pickup_address"] = ride["pickup_address"]
        view["dropoff_address"] = ride["dropoff_address"]
    # status in ('completed', 'cancelled') → addresses NOT included
    return view
```

Also: audit the rider-facing response to confirm riders still see their
own full history with addresses (they need this for their own records).

**Effort:** 2 h (serializer fix + test + driver-app side check that no
client breaks from missing fields)
**Owner:** `backend`
**Regulations:** PIPEDA, SK-HRC
**Regression test:** Driver API body for a ride with `status='completed'`
contains no `pickup_address` or `dropoff_address`; rider API still
returns them.

---

## 22-2 · Supabase Canadian region attestation not on file

**Source finding:** `[22-2]` CRITICAL · `risk_score=128` (×4 blast because
regulator)

**What's wrong:** Both `docs/vendor-inventory.md:35` and
`docs/dpa-register.md:28` flag the Supabase region as "⚠ VERIFY". Spinr
handles Canadian rider PII (C3–C4 per `docs/data-classification.md`).
PIPEDA s.4.1.3 requires documented evidence of where personal information
is processed, and the cross-border disclosure (or lack thereof) must be
stated in the privacy policy.

**Why it matters:** If Supabase is actually hosting in `us-east-1` (the
Supabase default for accounts that didn't explicitly select ca-central-1),
Spinr is in PIPEDA violation without having disclosed it. This is a
launch blocker.

**Blast radius:** regulator — affects compliance posture, not
individual users

**Action to fix (no code change):**
1. In the Supabase dashboard → Project Settings → verify region.
2. If Canadian: obtain written attestation from Supabase support
   confirming ca-central-1 is the primary DB region.
3. File as `reports/legal/dpa-supabase-2026.pdf`.
4. Update `docs/dpa-register.md:28` — flip the ⚠ to ✅ and link the PDF.
5. If NOT Canadian: product + legal decision whether to (a) migrate to
   ca-central-1, or (b) disclose cross-border transfer in privacy policy
   with rider consent flow.

**Effort:** 1 h (confirmation + filing) — up to 40 h if migration is required

**Owner:** `legal` + `infra`
**Regulations:** PIPEDA
**Regression test:** `docs/dpa-register.md` row for Supabase shows `✅`
with linked PDF; `reports/legal/dpa-supabase-2026.pdf` exists; privacy
policy matches disclosed region.

---

## Summary Checklist (pre-launch gate)

- [x] 19-1 rating field fix — ALREADY FIXED: aggregator reads `rider_rating` consistently (comment at rides.py:1881-1883 confirms the historic bug was resolved)
- [x] 20-1 wallet RPC + Decimal — ALREADY FIXED: wallet.py routes all balance mutations through atomic Postgres RPCs (`wallet_increment_balance`, `wallet_pay_for_ride`, `_wallet_transfer_rpc`)
- [x] 20-2 daily reconciliation cron — `backend/utils/reconciliation.py` created; spawned in `lifespan.py` as `reconciliation (daily 02:00 UTC)`; uses Redis leader lock for replay safety
- [x] 20-3 `financial_events` table + RLS — migration `58_financial_events.sql`: append-only with UPDATE/DELETE trigger + RLS; companion `59_reconciliation_discrepancies.sql` for discrepancy tracking
- [x] 21-2 driver response address scoping — `rides.py` `GET /{ride_id}`: strips `pickup_address`, `dropoff_address`, `pickup_lat/lng`, `dropoff_lat/lng` from driver view when `status in (completed, cancelled)`; riders retain full history
- [ ] 22-2 Supabase region attestation filed · legal + infra · 1 h — **open legal/infra action**: verify region in Supabase dashboard → obtain written attestation → file as `reports/legal/dpa-supabase-2026.pdf` → update `docs/vendor-register.md`

**Total engineering effort:** ~20 h · **Launch blocker:** all six  
**Engineering status:** 5/6 resolved. Item 22-2 is a legal/infra action with no code change.
