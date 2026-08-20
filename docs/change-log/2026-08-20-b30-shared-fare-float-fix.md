# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md B30 (spun off by B29) |

## 1. Issue / gap identified

`backend/routes/rides/_shared.py`'s `_reestimate_fare_for_stops` — the
shared fare-recalculation helper the **live** mid-trip stop-editing
endpoints (`POST`/`DELETE /rides/{id}/stops` in `routes/rides/stops.py`)
call on every stop add/remove — wrote `tax_amount` and `area_fees_total`
into its `result` dict (spread directly into a `rides` `$set` update) via
`float(_round(...))` instead of `str(_round(...))`. Both columns are
`NUMERIC` at the DB level. This is the same landmine class B28 closed for
`payouts.amount` and B29 closed for `booking_import_service.py`'s `rides`
insert — CLAUDE.md's Decimal-only rule forbids a `float()` cast on money
data at a DB-write boundary — but this time on a live, rider/driver-facing
booking-adjacent path rather than an offline importer.

**Scope correction vs. B30's filing (verified before fixing, not
assumed):** B30 named four columns (`grand_total`, `tax_amount`,
`area_fees_total`, `discount_amount`). Reading the actual file showed only
two of those four are genuinely affected here:

| Column | Found in `_shared.py`? | Status |
|---|---|---|
| `tax_amount` | Yes, `float(_round(tax_amount))` | **Bug — fixed by this diff** |
| `area_fees_total` | Yes, `float(_round(fees_total))` | **Bug — fixed by this diff** |
| `grand_total` (scalar column) | Yes | Already correct — written via `_money_str(grand_total)` (`str(_round(...))`) before this diff. Not touched; a regression test now pins it. |
| `grand_total` (inside `fare_breakdown_snapshot`) | Yes, `float(_round(grand_total))` | **Not the scalar `rides.grand_total` column** — `fare_breakdown_snapshot` is `JSONB` (migration 90). Per the B28/B29 precedent for jsonb sub-fields (jsonb has no NUMERIC concept), this correctly stays `float()`. Not touched; a regression test's negative control pins it. |
| `discount_amount` | **No** — not written anywhere in this file | Nothing to fix. `discount_amount` only appears as a *read* in `_build_fare_breakdown` (building the display line items), never as a write. A regression test guards against silently reintroducing an unfixed write here later. |

Only `tax_amount` and `area_fees_total` needed the `float()` → `str()`
swap. This mirrors B29's own precedent of re-verifying rather than
copy-pasting a candidate list (B29 found 5 of its own 11 candidates were
genuinely FLOAT8, not NUMERIC; this diff similarly found 2 of B30's 4
candidates were not live bugs in this file).

## 2. Root cause

The values are `Decimal` end-to-end before this point:
`fees_result = await _deps.calculate_all_fees(...)` → `fees_total =
_d(fees_result.get("fees_total", 0))` / `tax_amount =
_d(fees_result.get("tax_amount", 0))` (both `_d()`-converted to `Decimal`)
→ `_round(...)` quantizes to 2dp. The bug was purely at the final
serialization step: `float(_round(tax_amount))` / `float(_round(fees_total))`
cast the already-correct `Decimal` back to a binary float immediately
before being spread into the `rides` `$set` update, which
`repositories/_base.py`'s `_serialize_for_api` only string-serializes for
values that are still `Decimal` at that point — a `float` passes through
unchanged into the PostgREST payload.

**Empirically verified, not assumed:** because Python's `float.__repr__`
uses the shortest-round-trip algorithm, an exhaustive check of every cent
value from $0.00–$100,000.00 (`Decimal(cents)/100`) shows `Decimal(str(float(d)))
== d` for all of them — i.e. for realistic ride-tax/fee magnitudes, this
specific bug does not currently produce a *visibly different* numeric value
on a single round trip. This does **not** make the `float()` cast
harmless: it is still a direct violation of the mandatory Decimal-only
convention (the same convention a pre-commit hook enforces in fare code),
it silently drops the exactness guarantee for any future code path that
reads this field expecting an exact Decimal string rather than a
binary-float approximation, and it is fragile against any future
downstream consumer that does naive float arithmetic on the field instead
of renormalizing through `_d()`. Stating this precisely rather than
claiming an unverified truncation, per CLAUDE.md's instruction to verify
before asserting a money-precision claim.

## 3. Fix / remediation

Swapped `float(_round(...))` → `str(_round(...))` for `tax_amount` and
`area_fees_total` only, at their single write site in
`_reestimate_fare_for_stops`. No arithmetic changed — both values were
already `Decimal` up to this point; only the final serialization changed.
Nothing else in the file was touched: `grand_total`'s scalar write was
already correct, the jsonb `fare_breakdown_snapshot.grand_total` correctly
stays `float()`, and `discount_amount` has no write site here to fix.

New regression test:
`backend/tests/test_shared_rides_numeric_no_float_cast.py` — depth-aware
static source-text scan (sibling of B29's
`test_booking_import_rides_numeric_no_float_cast.py`), scoped to the
`result = {...}` dict inside `_reestimate_fare_for_stops`. Covers:
positive fix pin for `tax_amount`/`area_fees_total` (must be `str(`, must
not be `float(`); a pin that the scalar `grand_total` write stays
`str(`/`_money_str(`; a guard that `discount_amount` is not silently
written here without updating this test; and a negative control that the
separate jsonb `fare_breakdown_snapshot.grand_total` stays `float(`.
Verified the test actually catches the regression by temporarily
reverting the `tax_amount` fix and confirming
`test_numeric_rides_column_not_float_cast[tax_amount]` and
`test_numeric_rides_column_uses_str[tax_amount]` both failed with clear
messages, then restoring the fix and reconfirming all pass.

## 4. Risk & impact on existing functionality

- **Write path (this diff's actual change):** the only writer of these two
  fields via this function is `routes/rides/stops.py`'s
  `add_stop_mid_trip` / `remove_stop_mid_trip`, which spreads
  `_reestimate_fare_for_stops`'s return dict straight into
  `db.update_one("rides", {"id": ride_id}, {"$set": {**fare_update, ...}})`.
  No other caller of `_reestimate_fare_for_stops` exists (grepped
  `backend/` for the symbol — only `routes/rides/stops.py` and test files
  import it).
- **Read side: none.** Grepped the whole backend and both mobile
  surfaces for readers of `tax_amount`/`area_fees_total`:
  - Backend readers (`fare_service.py`, `payment_service.py`,
    `email_receipt.py`, `receipt_pdf.py`, `routes/rides/queries.py`,
    `stripe_reconcile.py`, and others) already normalize via
    `_d()`/`Decimal(str(...))` before doing anything money-sensitive —
    same convention B29 documented, still holds; none assume the
    write-time Python type.
  - `driver-app/app/driver/ride-detail.tsx` reads `ride.tax_amount` via
    `parseFloat(ride.tax_amount || '0')` — string-safe, unaffected by the
    type change.
  - `admin-dashboard/.../ride-detail-modal.tsx` reads both fields via a
    local `n(v) => parseFloat(String(v ?? 0)) || 0` helper — explicitly
    `String()`-coerces first, unaffected by the type change.
  - PostgREST/Supabase serialize a `NUMERIC` column as a JSON number on
    read regardless of whether it was written as a JSON string or a JSON
    number, so no reader anywhere sees a JSON-shape change from this fix.
- **No interaction with the ride state machine, dispatch, or any
  background loop** — this only runs inside the mid-trip stop-edit
  handlers, gated to rides already in `driver_accepted`/`driver_arrived`/
  `in_progress`. The fix changes serialization only, not which branch
  executes or what numeric value is computed.
- **Found during this fix's blast-radius grep, NOT fixed here (out of
  file-boundary scope for this diff — `routes/rides/_shared.py` only):**
  `backend/routes/rides/booking.py` (~line 1278-1288), inside the
  promo-code-application branch of `create_ride`, writes `subtotal_fare`,
  `discount_amount`, and `grand_total` into the same `rides` table via
  `_f(...)` (`_shared.py`'s `_f = float`) — the identical bug class, on
  the ride-creation/promo path, in a third file. All three of those
  columns are `NUMERIC`/`DECIMAL` per the same migration 82/46. Not
  touched by this diff (different file, different task boundary);
  flagging for a follow-up backlog item rather than scope-creeping this
  fix, matching this repo's own B29→B30 precedent of naming an adjacent
  finding instead of silently fixing or silently dropping it. Not added to
  `ACTION_ITEMS.md` in this commit — the task instruction for this diff
  scoped the commit to `_shared.py` + its test + this log only.

## 5. User-experience effect

- **No visible UX change.** The rider- and driver-facing responses from
  `add_stop_mid_trip`/`remove_stop_mid_trip` (HTTP response body and the
  `stops_updated` WebSocket push to the driver) carry the same numeric
  value before and after this fix — only the Python/wire *type*
  (`float` → `str`) changed at the DB-write boundary, and PostgREST
  round-trips a `NUMERIC` column as a JSON number either way on the next
  read. Not visible mid-session to a rider or driver already on an active
  ride with a stop being added/removed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/_shared.py` | `float(_round(tax_amount))` → `str(_round(tax_amount))` and `float(_round(fees_total))` → `str(_round(fees_total))` in `_reestimate_fare_for_stops`'s `result` dict | Close the float-on-NUMERIC landmine for the two columns actually affected in this file |
| `backend/tests/test_shared_rides_numeric_no_float_cast.py` | New | Static regression test (positive fix pin, jsonb negative control, scope-correction guards), verified it catches the mistake |
| `docs/change-log/2026-08-20-b30-shared-fare-float-fix.md` | New | This log |

## 7. Before / after

```python
# Before
"grand_total": _money_str(grand_total),
"tax_amount": float(_round(tax_amount)),
"tax_breakdown": fees_result.get("tax_breakdown", {}),
"area_fees_total": float(_round(fees_total)),
"area_fees_breakdown": fees_result.get("fees", []),
```

```python
# After
"grand_total": _money_str(grand_total),
"tax_amount": str(_round(tax_amount)),
"tax_breakdown": fees_result.get("tax_breakdown", {}),
"area_fees_total": str(_round(fees_total)),
"area_fees_breakdown": fees_result.get("fees", []),
```

(`tax_amount` and `fees_total` are `Decimal` throughout — only the final
serialization changed. `grand_total` on this line was already correct and
is unchanged.)

## 8. Rollback plan

- **Code only, no migration** — `tax_amount`/`area_fees_total` were
  already `NUMERIC` before this diff (verified live against
  `information_schema.columns`, project `soavhtdhefowwvforzwb`,
  2026-08-20; no schema change here). `git revert` is a complete and
  sufficient rollback: `str(Decimal)` and `float(Decimal)` both parse into
  a `NUMERIC` column via PostgREST without erroring, so reverting only
  reintroduces the (empirically-verified-safe-at-typical-magnitudes, but
  policy-noncompliant) `float()` cast — it does not break the stop-edit
  endpoints or corrupt any already-written row.
- No live data was touched: this changes serialization for *future*
  stop-add/stop-remove fare recalculations only. No backfill or corrective
  migration needed for rides whose stops were already edited under the old
  code path.

## 9. Verification performed

- [x] New regression test: `pytest
  backend/tests/test_shared_rides_numeric_no_float_cast.py --no-cov` — all
  cases pass (see exact command/output in the PR/session report).
- [x] Verified the new test actually catches the regression: temporarily
  reverted the `tax_amount` fix, confirmed both the positive-`str()` test
  and the negative-`float()` test failed with clear messages, restored the
  fix, reconfirmed all pass.
- [x] Existing test suites covering this file's callers re-run:
  `test_stop_fare_integrity.py`, `test_coverage_rides.py`
  (`test_reestimate_fare_for_stops_returns_updated_fare`), and the broader
  `routes/rides` test files — see exact pass/fail counts in the session
  report.
- [x] `ruff check` clean on both touched Python files.
- [x] `ruff format --check` clean on both touched Python files.
- [x] Live production query (read-only, `mcp__Supabase__execute_sql`
  against project `soavhtdhefowwvforzwb`) confirmed `tax_amount`,
  `area_fees_total`, `grand_total`, and `discount_amount` are all
  `NUMERIC` on the live `rides` table before writing any code.
- [x] Dry run against `mock_supabase_client`/a direct call to
  `_reestimate_fare_for_stops` with a `calculate_all_fees` mock returning
  fractional-cent `tax_amount`/`fees_total` values, capturing the
  pre-fix vs. post-fix write payload's Python types — see the dry-run
  scenario in the session report for the concrete before/after.
- [ ] Not run against a real staging/live Supabase `rides` table — the
  actual `supabase.table("rides").update(...)` call is exercised only
  through `mock_supabase_client`/mocked `db.update_one`, not a live
  Postgres round-trip.
- [ ] No `admin-dashboard`/`rider-app`/`driver-app` build was run — this
  is a backend-only Python change; the frontend blast-radius check above
  was static code reading, not an executed build/test of those apps.

## 10. What was NOT verified

- **Not run end-to-end against a live or staging Supabase `rides` table.**
  Only `mock_supabase_client`/mocked `db.update_one` calls were exercised
  — the real `supabase-py`/PostgREST JSON encoding of the resulting
  payload (which is what ultimately determines the on-the-wire
  representation) was reasoned about from CPython's `json.dumps` +
  `float.__repr__` behavior and an exhaustive cent-value check, not
  observed on a real request.
- **Did not audit or fix `backend/routes/rides/booking.py`'s** own
  `_f(...)` (float) casts on `subtotal_fare`/`discount_amount`/
  `grand_total` in its promo-application `rides` update — found during
  this diff's blast-radius grep, explicitly out of scope for this file
  boundary. Recommend a follow-up `ACTION_ITEMS.md` entry (not added in
  this commit per the task's explicit 3-file scope).
- **No independent (non-self) `spinr-money-auditor` subagent pass** — this
  session's investigation (Decimal-chain verification, live
  `information_schema` check, blast-radius grep, frontend consumer check)
  was performed directly rather than via a spawned
  `spinr-money-auditor` agent/skill invocation.
- **The empirical "no value-changing truncation at realistic magnitudes"
  finding in §2 was checked only up to $100,000.00 at cent granularity**
  — not proven for arbitrary magnitudes, and not a claim that the
  `float()` cast was ever safe in principle, only that no currently-stored
  row's value is known/expected to have visibly drifted from this
  specific bug.

## 11. Sign-off

- [x] Rollback plan is concrete and testable: plain `git revert`, no
  schema or live-data dependency.
- [x] Blast radius is stated, not assumed: grepped every backend reader,
  the driver-app and admin-dashboard consumers of these two fields, and
  every other writer of the four originally-named columns; named the one
  adjacent finding (`routes/rides/booking.py`) left deliberately
  untouched.
- [x] No silent behavior change to an already-shipped flow — the response
  shape and numeric value shown to riders/drivers are unchanged; only the
  DB-write-time Python type improved.
