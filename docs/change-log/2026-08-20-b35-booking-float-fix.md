# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides / payments |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md B35 (filed as a follow-up to B28/B29/B30; note below on filing status) |

**Filing note:** at the time this diff was written, no `### B35` section actually
existed in `ACTION_ITEMS.md` (checked directly — highest entry present was
B34). The task that produced this diff described B35's content inline
(candidate line numbers/fields in `backend/routes/rides/booking.py`,
grep-derived and explicitly **not** independently verified against the
schema). This log documents the investigation and fix performed against
that inline brief; it is not a summary of a pre-existing filed item.
Flagging here rather than fabricating a filed-entry citation, and rather
than silently proceeding as if B35 were on record — the user should decide
whether to add a real B35 entry to `ACTION_ITEMS.md` to match.

## 1. Issue / gap identified

`backend/routes/rides/booking.py` — the primary ride-creation write path —
writes several `rides` table fields via `_f(...)` (`_shared.py`'s
`_f = float`) immediately before a raw dict `$set`/insert, instead of a
Decimal-safe string. This is the same landmine class B28 closed for
`payouts.amount`, B29 closed for `booking_import_service.py`, and B30
closed for `_shared.py`'s `_reestimate_fare_for_stops` — a `float()` cast
on a `NUMERIC`/`DECIMAL` column reintroduces binary floating-point
imprecision at the DB-write boundary and violates CLAUDE.md's Decimal-only
money-arithmetic convention.

## 2. Root cause — and schema re-verification (do not trust the filed candidate list)

The task's inline brief listed 8 fields around `ride = Ride(...)` (lines
~1007–1024) and `airport_fee` (~1052) as candidates, plus `subtotal_fare`/
`discount_amount`/`grand_total` in the promo-application branch
(~1282–1293), and separately asked for `authorized_amount` (~226, 299,
345) to be triaged as possibly out-of-scope (a Stripe-only value).

**Live schema check performed before writing any fix** (read-only
`mcp__Supabase__execute_sql` against `information_schema.columns`, project
`soavhtdhefowwvforzwb`, 2026-08-20):

| Column | Live type | Verdict |
|---|---|---|
| `base_fare` | `double precision` | **Not a bug.** `_f()` is correct — left untouched. |
| `distance_fare` | `double precision` | **Not a bug.** Left untouched. |
| `time_fare` | `double precision` | **Not a bug.** Left untouched. |
| `booking_fee` | `double precision` | **Not a bug.** Left untouched. |
| `total_fare` | `double precision` | **Not a bug.** Left untouched. |
| `driver_earnings` | `double precision` | **Not a bug.** Left untouched. Confirmed independently by pre-existing code comments in `backend/migrations/159_payouts_overview_aggregates_fn.sql` and `303_payouts_overview_ytd_exclude_legacy.sql`: *"driver_earnings is FLOAT in the DB; cast via text so the value matches"*. |
| `admin_earnings` | `double precision` | **Not a bug.** Left untouched. |
| `airport_fee` | `double precision` | **Not a bug.** Left untouched. |
| `subtotal_fare` | `numeric(10,2)` | **Bug — fixed.** |
| `discount_amount` | `numeric(10,2)` | **Bug — fixed.** |
| `grand_total` | `numeric(10,2)` | **Bug — fixed** (promo-branch write site only). |
| `authorized_amount` | `numeric(12,2)` | **Bug — fixed.** Confirmed IN scope, not out of scope (§3 below). |

So **8 of the 9 originally-named fare/earnings fields were false positives**
— all genuinely `double precision`, because they pass through the
Pydantic `Ride` model (`schemas.py`), whose corresponding fields
(`base_fare`, `distance_fare`, `time_fare`, `booking_fee`, `total_fare`,
`driver_earnings`, `admin_earnings`) are typed `DecimalStr` in the model
but back genuinely FLOAT8 columns at the DB level — `_f()` (float) is the
correct serialization here, matching `_shared.py`'s own docstring for
`_f()`: *"Reserved for legacy callers that genuinely need a float"*. The
model coerces the float back through `Decimal(str(x))` internally
(verified empirically — see §9) and its `PlainSerializer` re-stringifies
it at `.dict()` time regardless, so passing a float here is redundant but
not wrong, and definitely not a NUMERIC-column bug. `airport_fee` — added
directly to the raw `ride_data` dict, not through the `Ride` model — is
independently confirmed `double precision` too (migration
`46_rides_fees_and_taxes.sql` labels it `DECIMAL(10,2)` in a comment, but
the live column is `double precision`; trusting the live schema query over
the migration file's stated intent, per this item's own instruction to
verify against the actual schema, not assume).

The genuinely NUMERIC fields were correctly named:
`subtotal_fare`, `discount_amount`, and `grand_total` (in the promo branch
only — `grand_total` is written at several other sites in this codebase
too, all already correct per B30's prior investigation).

### `authorized_amount` triage (task step 3)

Traced all three `authorized_amount` write sites end-to-end rather than
assuming they were Stripe-only:

- Line 226 (`_attach_preauthorized_hold`) and lines 299/345
  (`_preauthorize_ride_card`) each build a `dict` (`fields`/return value)
  whose docstrings explicitly say *"return the ride fields to persist"* /
  *"fields are merged into `ride_data`"* (`_PreauthOutcome.fields`
  docstring, `booking.py:135`).
- `create_ride` calls `ride_data.update(_preauth.fields)` (line ~1191) and
  `ride_data.update(await _attach_preauthorized_hold(...))` (line ~1161)
  — both merge directly into `ride_data`, the same raw dict passed to
  `_insert_ride_with_code` → `db_supabase.insert_ride(ride_data)`.
- `outcome.charged_amount` / `hold_amount` are `Decimal` end-to-end
  (`utils/stripe_charge.py`'s `ChargeOutcome.charged_amount =
  to_decimal(amount)`).

**Conclusion: `authorized_amount` is NOT a Stripe-only value out of
scope — it IS a `rides.authorized_amount` (NUMERIC(12,2)) table write**,
contrary to what the task's speculative framing suggested it might be.
Fixed along with the other three confirmed NUMERIC columns.

## 3. Fix / remediation

Swapped `_f(...)` → `_money_str(...)` (imported from `._shared`, already
used elsewhere in this codebase for exactly this purpose — no new helper
invented) at the 6 confirmed-NUMERIC write sites:

1. `_attach_preauthorized_hold`'s `"authorized_amount"` (line ~226)
2. `_preauthorize_ride_card`'s initial-hold `"authorized_amount"` (~299)
3. `_preauthorize_ride_card`'s fare-only-retry `"authorized_amount"` (~345)
4. `create_ride`'s promo branch `discounted_grand` assignment feeding
   `rides.grand_total` (~1270)
5. `create_ride`'s promo branch `update_one(...)` dict —
   `"subtotal_fare"`/`"discount_amount"` (~1282–1283)
6. The in-memory `fresh_ride["subtotal_fare"]`/`fresh_ride["discount_amount"]`
   mirror assignments two lines below (~1290–1291) — **not** a DB write
   themselves, but the same values feed the admin-monitoring WebSocket
   broadcast (`build_monitoring_ride(fresh_ride, ...)`) a few lines later
   in the same function; fixed for consistency with the DB write and
   because `_money_str`'s own docstring names "WebSocket payload" as
   exactly its intended use. Flagging this as slightly beyond the literal
   "DB column write site" scope, done anyway because it is the same three
   variables, same root cause, and a one-line mechanical match — not a
   separate investigation.

The 8 confirmed-FLOAT8 fields (`base_fare`, `distance_fare`, `time_fare`,
`booking_fee`, `total_fare`, `driver_earnings`, `admin_earnings`,
`airport_fee`) were **deliberately left untouched** — `_f()` is correct
for them.

No arithmetic changed anywhere — every fixed value was already `Decimal`
up to the point of serialization; only the final `_f()` → `_money_str()`
call changed.

**Not split into multiple PRs.** The task flagged this as a possibility
given B35's 7+ field footprint vs. B30's 2, but once independently
verified against the schema the real fix surface is 4 columns across 6
write sites, all the same mechanical `_f(` → `_money_str(` swap with no
new investigation needed per site (unlike, say, `ride = Ride(...)` at
~1007–1024 vs. the promo-branch dict at ~1282–1293 needing genuinely
different reasoning to arrive at "leave alone" vs. "fix" — that
differentiation is exactly what this log's §2 already did). One commit,
~20 lines, is proportionate.

## 4. Risk & impact on existing functionality

**Write path.** `ride_data`/`update_one("rides", ...)` calls in
`create_ride`, `_attach_preauthorized_hold`, and `_preauthorize_ride_card`
are the only writers of `authorized_amount` on the *booking* path. Other
writers of `authorized_amount` exist elsewhere (e.g. scheduled-dispatch
preauth, `utils/scheduled_rides.py`) — out of this file's scope, not
touched, not audited here (see "What was NOT verified").

**Read side — grepped every backend reader of the 4 touched columns:**

- `authorized_amount`: `services/payment_service.py:1920`,
  `routes/rides/cancellation.py:159`, `utils/stripe_reconcile.py` (x2),
  `utils/orphaned_hold_reconciler.py` (x2) — all normalize via
  `_round(_d(ride.get("authorized_amount") or 0))` or equivalent before
  use; none assume the write-time Python type. No frontend
  (`admin-dashboard`/`rider-app`/`driver-app`) reader references
  `authorized_amount` at all (grepped, zero matches).
- `subtotal_fare`/`discount_amount`/`grand_total`: every backend reader
  found (`routes/rides/_shared.py`, `routes/rides/queries.py`,
  `services/payment_service.py`, `utils/ledger_projection.py`,
  `routes/admin/rides.py`, `routes/promotions.py`, `ai/tools_rides.py`)
  reads these from a **fresh DB fetch**, not from the in-memory
  `ride_data`/`fresh_ride` dict this diff touches — PostgREST/Supabase
  serializes a `NUMERIC` column as a JSON number on read regardless of
  whether it was written as a JSON string or JSON number (same point
  B30's log already established), so no reader anywhere sees a JSON-shape
  change on the next fetch.
  - Frontend: `rider-app/utils/fareBreakdown.ts`,
    `rider-app/app/(tabs)/activity.tsx`, `admin-dashboard/.../
    live-monitoring.ts` already type `discount_amount`/`subtotal_fare` as
    `number | string` and coerce via `parseFloat(String(...))`/`Number(...)`
    — unaffected. `rider-app/app/ride-details.tsx:202-204` calls
    `parseFloat(ride.discount_amount)` directly (no `String()` wrapper) —
    `parseFloat` accepts both a `number` and a `string` argument
    (coerces internally), so this is unaffected too.
- The one place this diff's fix IS newly visible pre-DB-round-trip is the
  admin-monitoring WS payload built from `fresh_ride` right after the
  promo block runs (`build_monitoring_ride(fresh_ride, ...)`) — the
  `subtotal_fare`/`discount_amount`/`grand_total` values in that one
  broadcast go from raw JSON floats to JSON strings. This is the intended
  outcome (Audit-17 P0-1: money crosses the wire as decimal strings), and
  matches every other money field already on that same payload.

**No interaction with the ride state machine, dispatch, or the 18
background loops.** All touched code runs synchronously inside
`create_ride`'s request path (preauth + promo application), before/around
the ride insert; nothing here changes which branch executes or what
numeric value is computed, dispatch behavior, or offer timing.

**Adjacent finding, NOT fixed here (out of scope — task limited this diff
to `booking.py`):** `backend/services/fare_service.py`'s
`recalculate_fare_for_distance` (called from
`routes/drivers/ride_complete.py` on ride completion, whenever the actual
GPS distance differs from the planned distance) returns
`"grand_total": _f(new_grand_total)`, which the caller spreads into a raw
`update_fields` dict written straight to the `rides` row. `grand_total` is
confirmed NUMERIC(10,2) — this is the identical bug class, on the
**ride-completion** path, likely higher-traffic than booking.py's promo
branch (any ride whose planned distance diverges from actual GPS
distance). `distance_fare`/`total_fare`/`driver_earnings` in that same
dict are correctly `_f()` (confirmed FLOAT8, same as this diff's
negative controls). Recommend a follow-up item; not added to
`ACTION_ITEMS.md` here since no B35 entry exists to append to (see the
filing note in the Summary table) and this diff's task scope was
`booking.py` only.

## 5. User-experience effect

No visible UX change. The HTTP response body from `create_ride`
(`updated_ride`) is built from a **fresh** `db_supabase.get_ride(ride.id)`
call after all writes complete, not from the in-memory `ride_data`/
`fresh_ride` dicts this diff touches — so the rider-facing numeric values
are identical before and after (PostgREST round-trips a NUMERIC column
the same way regardless of write-time Python type). Not visible mid-session
to a rider or driver. The one behavior change that IS visible (in a
non-user-facing channel) is the admin-monitoring WebSocket payload's
`subtotal_fare`/`discount_amount`/`grand_total` becoming JSON strings
instead of JSON numbers for rides booked with a promo code — internal
admin-dashboard-only, and the admin-dashboard's own consumer of that
payload already accepts `number | string` for these fields (see §4).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/booking.py` | `_f(...)` → `_money_str(...)` at 6 write sites (`authorized_amount` ×3, `discounted_grand`/`grand_total`, `subtotal_fare`, `discount_amount`); added `_money_str` to the `._shared` import | Close the float-on-NUMERIC landmine for the 4 columns confirmed genuinely NUMERIC in this file |
| `backend/tests/test_booking_rides_numeric_no_float_cast.py` | New | Static source-text regression test: positive fix pins for all 6 write sites + negative controls pinning the 8 confirmed-FLOAT8 fields to stay `_f(` |
| `backend/tests/test_ride_preauth_booking.py` | Updated 3 existing assertions (`"authorized_amount": 25.0` → `"25.00"`, `35.0` → `"35.00"`) | These tests hardcoded the pre-fix float value as the expected result; they were the only pre-existing tests broken by the fix, found by running the full booking/ride test surface |
| `docs/change-log/2026-08-20-b35-booking-float-fix.md` | New | This log |

## 7. Before / after

```python
# Before — _attach_preauthorized_hold
return {
    "payment_intent_id": outcome.payment_intent_id,
    "authorized_amount": _f(outcome.charged_amount),
    "auth_status": "authorized",
}
```

```python
# After
return {
    "payment_intent_id": outcome.payment_intent_id,
    "authorized_amount": _money_str(outcome.charged_amount),
    "auth_status": "authorized",
}
```

```python
# Before — create_ride promo branch
discounted_grand = _f(_round(_d(fresh_ride.get("grand_total", server_fare)) - discount))
...
{
    "subtotal_fare": _f(server_fare),
    "discount_amount": _f(discount),
    "promo_code": validation["code"],
    "promo_application_id": application_id,
    "grand_total": discounted_grand,
},
```

```python
# After
discounted_grand = _money_str(_round(_d(fresh_ride.get("grand_total", server_fare)) - discount))
...
{
    "subtotal_fare": _money_str(server_fare),
    "discount_amount": _money_str(discount),
    "promo_code": validation["code"],
    "promo_application_id": application_id,
    "grand_total": discounted_grand,
},
```

(`ride = Ride(...)`'s `base_fare=_f(base_fare)` etc., and
`ride_data["airport_fee"] = _f(airport_fee)`, are unchanged — confirmed
correct, not part of this diff.)

## 8. Rollback plan

- **Code only, no migration.** All 4 fixed columns (`authorized_amount`,
  `subtotal_fare`, `discount_amount`, `grand_total`) were already
  `NUMERIC` before this diff (verified live against
  `information_schema.columns`, project `soavhtdhefowwvforzwb`,
  2026-08-20). `str(Decimal)` and `float(Decimal)` both parse into a
  `NUMERIC` column via PostgREST without erroring, so `git revert` is a
  complete, sufficient rollback — it reintroduces the
  policy-noncompliant-but-not-currently-known-to-be-value-changing
  `_f()` cast, nothing more.
- No live data is touched by reverting: this changes serialization for
  *future* card preauthorizations and *future* promo-code applications
  only. No backfill or corrective migration needed for rows already
  written under the old code path.

## 9. Verification performed

- [x] New regression test:
  `pytest backend/tests/test_booking_rides_numeric_no_float_cast.py -v --no-cov`
  — **14 passed**.
- [x] Verified the new test actually catches the regression: `git stash`
  of `booking.py` (reverting to the pre-fix `_f()` calls) →
  `pytest tests/test_booking_rides_numeric_no_float_cast.py --no-cov -q`
  → **6 failed** (all 6 fix-pinning tests, each with a clear assertion
  message naming the exact write site and required fix), **8 passed**
  (all negative controls, correctly staying green since those sites
  weren't touched) → `git stash pop` restored the fix →
  re-ran → **14 passed**.
- [x] Full booking/ride test surface:
  `pytest backend/tests/ -k "booking or Booking or ride or Ride or stop" --no-cov -q`
  — first run (before updating the 3 stale assertions): **3 failed, 2820
  passed, 1 skipped** (the 3 failures were pre-existing
  `test_ride_preauth_booking.py` assertions hardcoding the old float
  value, e.g. `{'authorized_amount': '25.00'} != {'authorized_amount':
  25.0}` — confirms the fix changed real behavior, not just the new
  test). Updated those 3 assertions to the Decimal-safe string. Re-ran:
  **2823 passed, 1 skipped, 0 failed**.
- [x] Related surface: `pytest backend/tests/ -k "promo or Promo or preauth or Preauth or authorized_amount" --no-cov -q` — **358 passed, 1 skipped, 0 failed**.
- [x] `ruff check` clean on all 3 touched/added Python files.
- [x] `ruff format --check` clean on all 3 touched/added Python files.
- [x] Live schema query (read-only, `mcp__Supabase__execute_sql` against
  project `soavhtdhefowwvforzwb`) confirmed all 12 candidate columns'
  actual types (table in §2) before writing any fix — this is what
  overturned 8 of the 9 originally-named fare/earnings fields as false
  positives.
- [x] Dry run: called `_f()` vs. `_money_str()` directly on a
  representative fractional-input `Decimal` (`grand_total=12.005`,
  `buffer=2.00` → `hold_amount=14.01` after `_round`), then passed both
  through the real `repositories._base._serialize_for_api` used on every
  `insert_ride`/`update_one` call:
  - **Before:** `_f(hold_amount)` → Python `float(14.01)` → after
    `_serialize_for_api` → `{"authorized_amount": 14.01}` → `json.dumps`
    → `{"authorized_amount": 14.01}` (bare JSON number).
  - **After:** `_money_str(hold_amount)` → Python `'14.01'` (str) → after
    `_serialize_for_api` → `{"authorized_amount": "14.01"}` → `json.dumps`
    → `{"authorized_amount": "14.01"}` (JSON string, Decimal-safe).
- [ ] Not run against a real staging/live Supabase `rides` table — only
  `mock_supabase_client`/mocked `db.update_one`/`insert_ride` calls were
  exercised via the test suite; the real PostgREST round-trip was
  reasoned about from `_serialize_for_api`'s known behavior (str vs.
  float passthrough) and the live `information_schema.columns` read, not
  observed on a real write.
- [ ] No `admin-dashboard`/`rider-app`/`driver-app` build was run — this
  is a backend-only Python change. The frontend blast-radius check in §4
  was static code reading of existing TS/TSX consumers, not an executed
  build or test of those apps.

## 10. What was NOT verified

- **Not run end-to-end against a live or staging Supabase `rides`
  table.** Same boundary B30 documented: only mocked DB calls were
  exercised in the test suite; the actual on-the-wire PostgREST encoding
  was reasoned about, not observed on a real request.
- **`backend/services/fare_service.py`'s `recalculate_fare_for_distance`
  found but not fixed** (§4, "Adjacent finding") — same bug class, on the
  ride-completion path, out of this diff's file-boundary scope.
- **No independent (non-self) `spinr-money-auditor` subagent pass** —
  this investigation (schema re-verification, code-path tracing for
  `authorized_amount`, blast-radius grep) was performed directly.
- **The empirical claim in §2 that the `Ride` Pydantic model's `float →
  Decimal(str(x))` coercion is exact "at realistic magnitudes"** was
  checked with one representative value (`3.335`), matching the same
  spot-check depth B30 used for its own float-round-trip claim — not an
  exhaustive proof for arbitrary magnitudes.
- **No live production query was run to check whether any already-stored
  `authorized_amount`/`subtotal_fare`/`discount_amount`/`grand_total`
  value has actually drifted from float imprecision** — this diff fixes
  the write path going forward only; no backfill/audit of historical rows
  was performed or requested.
- **`ACTION_ITEMS.md` was not edited** — no B35 entry exists to update
  (see the filing note in the Summary table), and no other open item
  matched this fix closely enough to append to. The
  `recalculate_fare_for_distance` finding above was similarly not added
  as a new tracked item, per this diff's scope boundary.

## 11. Sign-off

- [x] Rollback plan is concrete and testable: plain `git revert`, no
  schema or live-data dependency.
- [x] Blast radius is stated, not assumed: grepped every backend reader
  of the 4 touched columns, the driver-app/rider-app/admin-dashboard
  consumers, traced `authorized_amount`'s actual write path end-to-end
  rather than assuming it was Stripe-only, and named the one adjacent
  finding (`fare_service.py`) left deliberately untouched.
- [x] No silent behavior change to an already-shipped flow — the
  rider-facing HTTP response and DB-stored numeric values are unchanged;
  only the admin-monitoring WS payload's JSON shape for 3 fields changes
  (number → string), on an internal, already-`number | string`-tolerant
  consumer.
