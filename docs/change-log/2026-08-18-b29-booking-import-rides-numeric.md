# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md B29 (spun off B28) |

## 1. Issue / gap identified

`backend/services/booking_import_service.py` builds a raw `rides` insert
payload (`plan.rides_to_insert`, written directly via
`supabase.table("rides").insert(...)`, bypassing the generic
`db_supabase.insert_one` serializer). Four of the scalar columns in that
payload — `grand_total`, `tax_amount`, `area_fees_total`,
`discount_amount` — are `NUMERIC`/`DECIMAL` at the DB level, but the code
wrote them with `float(decimal_value)` instead of `str(decimal_value)`.
CLAUDE.md's Decimal-only rule forbids this: a `float()` cast on a `Decimal`
reintroduces binary floating-point rounding error immediately before the
value reaches Postgres, exactly the landmine class B28 already closed for
`payouts.amount`.

## 2. Root cause

B29's own filing (spun off by `spinr-money-auditor` while reviewing B28)
assumed *every* untouched `float()` call in this file targeting a
`rides`-shaped candidate list was the same bug. That assumption was only
partially correct — see verification below.

## 3. Fix / remediation

**Verified every candidate column's real Postgres type first** (read-only
`information_schema.columns` query against project `soavhtdhefowwvforzwb`,
2026-08-18) before changing anything, per this repo's precedent from B28:

| Column | Actual type | Action |
|---|---|---|
| `grand_total` | `numeric(10,2)` | **Fixed**: `float()` → `str()` |
| `tax_amount` | `numeric(8,2)` | **Fixed**: `float()` → `str()` |
| `area_fees_total` | `numeric(8,2)` | **Fixed**: `float()` → `str()` |
| `discount_amount` | `numeric(10,2)` | **Fixed**: `float()` → `str()` |
| `base_fare` | `double precision` | Left alone — genuinely FLOAT8 |
| `distance_km` | `double precision` | Left alone — genuinely FLOAT8 |
| `total_fare` | `double precision` | Left alone — genuinely FLOAT8 |
| `tip_amount` | `double precision` | Left alone — genuinely FLOAT8 |
| `driver_earnings` | `double precision` | Left alone — genuinely FLOAT8 |
| `admin_earnings` | `double precision` | Left alone — genuinely FLOAT8 |
| `distance_fare`, `time_fare`, `booking_fee`, `airport_fee`, `surge_multiplier` | `double precision` | Left alone — genuinely FLOAT8 (written as literals, e.g. `0.0`, not casts) |
| `pickup_lat/lng`, `dropoff_lat/lng` | `double precision` | Left alone — genuinely FLOAT8, and not money anyway (coordinates) |

**B29's own filing text was wrong about `base_fare`, `distance_km`,
`total_fare`, `tip_amount`, `driver_earnings`** — it listed these as
candidates alongside the four genuinely-NUMERIC columns, but
`information_schema` confirms all five are `double precision`. This
diff does **not** touch them — `float()` is the correct cast for a
FLOAT8 column, and B29's acceptance criteria says exactly this: "every
`rides` column ... that is `NUMERIC`/`DECIMAL` at the DB level" — a
column that isn't NUMERIC is out of scope by the item's own wording, not
merely deprioritized.

**`old_payout_gst_amount`** (also named in B29's filing) is a key inside
the `legacy_import_metadata` **jsonb** column, not a scalar `rides` column
at all — same category as the three jsonb fields (`fare_breakdown_snapshot`,
`tax_breakdown`, `area_fees_breakdown`) B28 already carved out as
correctly-still-`float()`. Left unchanged; jsonb has no Postgres NUMERIC
concept to violate.

No arithmetic changed — every fixed value (`total_amount - tip`, `gst`,
`fees_total`, `discount`) was already a `Decimal` before this fix and stays
`Decimal` all the way to the final `str()` call; only the serialization at
the write boundary changed. `fees_total` in particular accumulates via
`fees_total += amount` where `amount` is itself `parse_money(...)` (a
`Decimal`) — confirmed this summation happens entirely in `Decimal` before
the new `str()` cast, not in `float`.

New regression test:
`backend/tests/test_booking_import_rides_numeric_no_float_cast.py` — static
source-text scan (same technique as B28's
`test_payouts_amount_no_float_cast.py`) scoped to the `ride: dict[str, Any]
= {...}` literal. Unlike the payouts version, this dict has its own nested
jsonb sub-dicts (including a same-named-but-different `"grand_total"` key
inside `fare_breakdown_snapshot`), so the helper blanks out nested-brace
content rather than reusing the payouts test's flat block extractor — this
was necessary in practice: the first draft of the test false-positived on
that nested key before the depth-aware version was written. The test both:
- fails if any of the four NUMERIC columns is written with `float()`
  (verified this actually fires: temporarily reverted the `grand_total` fix
  and confirmed the test failed, then restored it), and
- fails (negative control) if any of the confirmed-FLOAT8 columns is ever
  wrapped in `str()` by a future well-meaning-but-wrong "fix".

## 4. Risk & impact on existing functionality

- **Blast radius: contained to `booking_import_service.py`'s own `rides`
  insert.** Grepped the whole backend for every writer that sets
  `"grand_total"`, `"tax_amount"`, `"area_fees_total"`, or
  `"discount_amount"` as a raw dict key destined for the `rides` table.
  Confirmed other writers already serialize correctly for this concern:
  - `backend/routes/rides/_shared.py` builds these same four keys via
    `float(_round(...))` in its own fare-snapshot builder — **this is a
    pre-existing, separate instance of the same bug class, not touched by
    this diff** (out of scope per this task's explicit file boundary: only
    `booking_import_service.py`, its tests, `ACTION_ITEMS.md`, and this log
    were authorized). Flagging it below as a new backlog item rather than
    scope-creeping this PR, following B28's own precedent of spinning off
    adjacent findings instead of fixing them inline.
  - `backend/routes/rides/booking.py`, `backend/routes/rides/estimates.py`,
    `backend/services/company_booking_service.py` assign these keys from
    `fare_service`/`fees_result` output directly (no `float()` cast visible
    at the assignment site) — not verified further, out of scope for this
    diff.
  - `backend/routes/admin/rides.py` writes `"discount_amount": str(...)`
    (already correct) in one place and reads with `float(Decimal(...))` in
    another (a *read*-path cast for an admin response payload, not a DB
    write — correct to stay `float()` there since API JSON has no NUMERIC
    concept).
  - `backend/services/payment_service.py` already uses
    `str(_round(_d(...)))` for `tax_amount`/`discount_amount`/`grand_total`
    — matches this fix's pattern, no change needed.
- **Read-side impact: none.** Same reasoning as B28 — PostgREST serializes
  NUMERIC as a JSON number either way, and every downstream reader
  (`fare_service.py`, `payment_service.py`, `email_receipt.py`,
  `receipt_pdf.py`, `stripe_reconcile.py`, `routes/rides/queries.py`, etc.)
  already normalizes via `_d()`/`Decimal(str(...))` before doing anything
  money-sensitive with these fields, per this repo's own convention. None
  of them assume the *write*-time Python type.
- No interaction with the ride state machine, dispatch, or any background
  loop — this is an offline CLI/admin-upload import path, not something a
  rider or driver hits mid-session.
- **spinr-money-auditor review: self-performed, not run via the Agent/Task
  tool.** This session's tool surface does not expose a generic
  subagent-spawning tool (checked via `ToolSearch` for "Agent", "Task",
  "spawn subagent" — nothing matched that would let this session invoke
  the `spinr-money-auditor` skill/agent as a subagent). Performed the
  equivalent review directly instead, per the task's own fallback
  instruction:
  - Decimal-only discipline: confirmed every fixed value was already a
    `Decimal` at every step from `parse_money()` through to the new
    `str()` call; no float ever enters the money path.
  - No float reintroduction: grepped the diff for any remaining
    `float(...)` on the four fixed columns — none found; verified via the
    new regression test's negative-control (`test_numeric_rides_column_not_
    float_cast`).
  - No summation of money before the final `str()` cast: `fees_total`'s
    accumulation loop (`fees_total += amount`) happens entirely over
    `Decimal` values before the single `str(fees_total)` call at the end;
    confirmed no intermediate `float()` round-trip.
  - This is a self-review, not an independent second pass — flagged
    explicitly per the task's instruction rather than silently presented
    as equivalent to a real subagent audit.

## 5. User-experience effect

- **Backend-only, no UI surface.** This is an offline legacy-data import
  script (CLI + admin-upload endpoint), not a live rider/driver/corporate-
  admin flow. No mid-session visibility to anyone already using the app.
  The only observable effect is that newly-imported legacy rides' `rides`
  row values for these four columns are now written as exact Decimal
  strings instead of binary-float approximations — a precision
  improvement, not a behavior change any user would notice.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/booking_import_service.py` | `float(...)` → `str(...)` for `grand_total`, `tax_amount`, `area_fees_total`, `discount_amount` in the `ride` insert dict; added type-verification comments | Close the float-on-NUMERIC landmine for these four confirmed columns |
| `backend/tests/test_booking_import_rides_numeric_no_float_cast.py` | New | Static regression test (positive + negative control), verified it catches the mistake |
| `backend/tests/test_booking_import_service.py` | Updated 3 assertions (`test_grand_total_is_bill_excluding_tip`, `test_fees_and_tax_land_in_their_own_columns`, `test_payout_gst_amount_preserved_raw_not_merged_into_tax`) from `pytest.approx(float)` to string-literal / `Decimal(...)` comparisons; added `from decimal import Decimal` import | Match the new string serialization |
| `ACTION_ITEMS.md` | B29 marked CLOSED | Record the fix and its scoping |

## 7. Before / after

```python
# Before
"grand_total": float(total_amount - tip),
"tax_amount": float(gst),
"area_fees_total": float(fees_total),
"discount_amount": float(discount),
```

```python
# After
"grand_total": str(total_amount - tip),
"tax_amount": str(gst),
"area_fees_total": str(fees_total),
"discount_amount": str(discount),
```

(`total_amount`, `tip`, `gst`, `fees_total`, `discount` are all `Decimal`
throughout — only the final serialization changed.)

## 8. Rollback plan

- **Code only, no migration involved** — these four columns were already
  `NUMERIC`/`DECIMAL` before this diff (no schema change here, unlike
  B28). `git revert` is a complete and sufficient rollback: `str(Decimal)`
  and `float(Decimal)` both parse correctly into a NUMERIC column via
  PostgREST, so reverting only reintroduces the (small, historically
  unobserved on this table, since this importer is not yet run in
  production) float-precision risk — it does not break inserts or change
  any already-written row.
- No live data was touched: this fix changes behavior for *future* import
  runs only. No re-backfill or corrective migration is needed for rows
  already imported by a prior run of this script, since (per B29's own
  filing) this bug has been present since the importer's introduction —
  any such correction is a separate decision or item, not part of this fix.

## 9. Verification performed

- [x] Automated tests: 82 tests pass across the affected surface (`pytest
  backend/tests/test_booking_import_rides_numeric_no_float_cast.py
  backend/tests/test_booking_import_service.py
  backend/tests/test_admin_booking_import.py
  backend/tests/test_payouts_amount_no_float_cast.py --no-cov`)
- [x] Verified the new regression test actually catches the regression it's
  meant to catch (temporarily reverted the `grand_total` fix, confirmed
  both the negative-cast test and the positive-str test failed with clear
  messages, then restored the fix and reconfirmed all pass)
- [x] `ruff check` clean on all three touched Python files
- [x] `ruff format --check` clean on all three touched Python files
- [x] Live production query (read-only, `mcp__Supabase__execute_sql`
  against project `soavhtdhefowwvforzwb`) confirmed the real column type of
  every candidate column named in B29's filing, before writing any code —
  this is what caught that 5 of the 11 originally-listed candidates
  (`base_fare`, `distance_km`, `total_fare`, `tip_amount`,
  `driver_earnings`) are actually FLOAT8, not NUMERIC, and correctly left
  unchanged
- [x] Money-auditor equivalent review: **self-performed**, not run via a
  spawned `spinr-money-auditor` subagent — this session's tool surface has
  no generic Agent/Task subagent-spawning tool available (confirmed via
  `ToolSearch`). See section 4 above for the review performed directly.
- [ ] No `admin-dashboard`/`rider-app`/`driver-app` build was run — this
  change is backend-only Python, no frontend surface touched
- [ ] Not exercised against a real staging environment or the real Supabase
  `rides` table with an actual insert — verification was static
  (source-text regression test) + unit-level (`build_plan` against the
  in-memory fake Supabase client in `test_booking_import_service.py`), not
  an end-to-end `commit_plan()` run against a live/staging database

## 10. What was NOT verified

- Not run end-to-end against a live or staging Supabase `rides` table —
  only the in-memory fake-client unit tests exercise `build_plan()`, and
  `commit_plan()` (the actual `supabase.table("rides").insert(...)` call)
  is not covered by any test in this repo for either the old or new code
  path, matching the same boundary B28 documented for its own writers.
- Did not audit or fix `backend/routes/rides/_shared.py`'s own
  `float(_round(...))` casts on these same four column names — found
  during the blast-radius grep, explicitly out of scope for this diff per
  the task's file-boundary instruction, and not part of B29's filing.
  Recommend a follow-up ACTION_ITEMS.md entry (see below).
- No independent (non-self) `spinr-money-auditor` pass — see section 4.

## 11. Sign-off

- [x] Rollback plan is concrete and testable: plain `git revert`, no schema
  or live-data dependency
- [x] Blast radius is stated, not assumed: grepped every writer of these
  four column names into a `rides`-shaped dict; named the one adjacent
  finding (`routes/rides/_shared.py`) left deliberately untouched
- [x] No silent behavior change to an already-shipped flow — this is an
  offline import script with no live-session UI; write-side precision only
  improves, never regresses
