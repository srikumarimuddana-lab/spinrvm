# Spinr Driver & Rider Migrated-Data Audit

**Date:** 2026-08-11
**Scope:** Legacy-migrated Driver and Rider data — completeness, validity, financial integrity, and cross-surface consistency across admin dashboard, driver-app, and rider-app.
**Method:** Code-level / static audit of import services, migrations, aggregation logic, and every surface that reads the same underlying data.
**Auditors:** `spinr-migration-reviewer` (Phase 0), `spinr-money-auditor` (Phase 1), `spinr-regulatory-compliance-checker` (Phase 2), general-purpose (Phase 3), synthesized by Claude.

---

## ⚠️ Coverage limitation — read this first

This sandbox has **no live Supabase/Postgres credentials** (`backend/.env` absent, no `SUPABASE_URL`/`PG_CONNECTION_STRING` in the environment). Every finding below comes from reading the actual import services, migrations, and query logic — not from querying production rows. Concretely:

- **Verified**: whether the code *can* produce correct/complete/consistent data (logic, filters, idempotency, provenance).
- **NOT verified**: actual row counts, how many drivers/riders exist in a bad state today, the real dollar magnitude of any financial discrepancy, or whether any of the theoretical failure modes below have actually fired in production.

Every finding states which category it's in. Treat "code-certain" findings as needing a fix regardless of scale; treat "requires live DB" findings as needing a one-off SQL query against production/staging before you can size the blast radius.

## Cutover date — corrected

**There is no recorded "March 2025" cutover anywhere in the repo.** The only citable date anchor is `docs/change-log/2026-07-29-legacy-booking-import.md`: the legacy MongoDB booking export runs through **2026-07-26**, and that's scoped only to the one-time booking import (224 of 1,210 exported bookings imported, `booking_status='completed'` + Canadian country code). Driver imports (`driver_import_service.py`) and rider imports (`rider_import_service.py`) carry no system-wide "as-of" date — batches are timestamped by when the operator ran the CLI, not by a legacy-system export date. **Recommendation:** if "March 2025" is a real date someone gave you, it describes something outside this codebase (e.g. when the old Saskatoon app was decommissioned) — confirm with whoever supplied it before using it as an audit boundary; don't let a report cite it as fact.

---

## Executive summary

| Severity | Count | Theme |
|---|---|---|
| **P0** | 3 | Rider importer has zero provenance; rider importer can silently overwrite PIPEDA-scrubbed PII on deletion-pending accounts with no audit trail; three admin financial-dashboard surfaces double-count legacy-imported ride earnings (no `legacy_import_metadata` exclusion) |
| **P1** | 3 | `drivers.total_earnings` fleet-wide stat is dead code (always $0); legacy PST likely folded silently into fare line on old receipts; multiple admin/driver earnings surfaces bucket by different date fields or apply the legacy-exclusion inconsistently |
| **P2** | 4 | Driver import accepts unvalidated VIN/email/phone/expired-approved-docs; rider import writes no `legacy_import_metadata`; `/balance` vs `/earnings` composition can diverge; admin "total rides" and driver-app "total rides" use different definitions by design but aren't reconciled anywhere |

**What's confirmed clean:** the driver-facing balance/statement/T4A pipeline (`routes/drivers/earnings.py`, `utils/driver_statement.py`, `utils/t4a_annual_job.py`, `routes/drivers/tax_exports.py`) consistently applies the legacy-ride exclusion and agrees with itself. The booking-duplicate-prevention mechanism (unique DB index on `old_booking_id`) is real and DB-enforced, not just application-level. Rider wallet balance is a single source of truth read identically by admin and rider-app — no divergence risk there. The `driver_insurance_periods` regulatory ledger is correctly never touched by either import path (deliberate, not an oversight, but undocumented as such).

---

## Phase 0 — Provenance & migration inventory

**Importer provenance summary:**

| Importer | Writes `legacy_import_metadata`? | Idempotent? |
|---|---|---|
| `driver_import_service.py` | ✅ `{batch, old_driver_id, source, address_present, drivers_abstract_status}` | ✅ matches on `old_driver_id`+`source` |
| `rider_import_service.py` | ❌ **nothing written, ever** | Partial — matches by phone/email, but no batch-scoped rollback possible |
| `booking_import_service.py` | ✅ `{batch, source, old_booking_id, old_booking_code, old_customer_id, old_driver_id, imported_at}` | ✅ DB-enforced via unique index (migration 268) |
| `stripe_mapping_import_service.py` | ✅ `legacy_import_metadata.stripe_migration = {...}` | ✅ only fills NULL columns, re-runs converge |

**P0-A — Rider importer has no provenance trail.** `rider_import_service.py:150` accepts a `batch` parameter that is never persisted anywhere (`commit_plan`, lines 276-291). Every other importer stamps `legacy_import_metadata` on the rows it touches; riders alone have no way to answer "which rows did import batch X create/modify," no batch-scoped rollback (contrast `scripts/import_legacy_bookings.py:154-159`, which supports exactly that for bookings), and a weaker PIPEDA access/export answer ("show me everything imported about this user and when").

**Migration inventory** (tables touched: `drivers`, `users`, `rides`, `driver_insurance_periods`, `payouts`, `bank_accounts`, `driver_statements`, `wallets`, `wallet_transactions`, `financial_events`, `driver_documents`):

| Migration | Change | Additive/Destructive | RLS+Index |
|---|---|---|---|
| `221_drivers_bulk_import_fields.sql` | Adds driver PII/import columns + `legacy_import_metadata` | Additive | ✅ partial indexes |
| `256_users_legacy_import_metadata.sql` | Adds `legacy_import_metadata` on `users` | Additive | No index (documented as intentional — rare batch-scoped queries only) |
| `268_rides_legacy_import_metadata.sql` | Adds `legacy_import_metadata` + **unique partial index** on `old_booking_id` | Additive | ✅ `CREATE UNIQUE INDEX CONCURRENTLY` |
| `271_recount_driver_total_rides_fn.sql` | New RPC, avoids N+1 writes on import | Additive | N/A |
| `272/273_driver_statements*.sql` | Creates `driver_statements`; 273 changes FK to `ON DELETE CASCADE` | 273 is a documented, justified FK behavior change (statements are regenerable, not retention-sensitive) | N/A |
| `216_deletion_hard_delete_no_anonymize.sql` | Hard-deletes at 7y retention boundary | Destructive by design, fully documented, `SECURITY DEFINER` + pinned `search_path` | N/A |
| `294_financial_events_ride_id_set_null.sql` | FK → `ON DELETE SET NULL` | Justified (avoids purge blocking while preserving the 7-year CRA ledger row) | N/A |

No append-only violations found. `driver_insurance_periods` is explicitly excluded from every cascade/delete path.

**P2-B — No Change Impact Log exists for the driver or rider bulk-import paths themselves** (only the booking import and Stripe-mapping migration have runbooks/change-logs), despite both writing directly to `auth`/`users`/`drivers`. Per CLAUDE.md this is a documentation gap on a live-tested surface, even though the underlying code looks sound.

---

## Phase 1 — Driver data: completeness, validity, financial integrity

### P0-B — Three admin financial-dashboard surfaces double-count legacy-imported ride earnings

Every driver-facing earnings/payout/T4A endpoint correctly applies `utils/legacy_rides.py`'s `EXCLUDE_LEGACY_RIDES` filter. Three **admin-facing aggregate** surfaces do not:

1. **`backend/migrations/161_ride_money_rollup_fn.sql`** (`admin_ride_money_rollup`, powers `GET /admin/stats` and `GET /admin/earnings`) — sums `total_fare`/`driver_earnings`/`admin_earnings`/`tip_amount` over all `status='completed'` rides, no `legacy_import_metadata IS NULL` predicate.
2. **`backend/migrations/159_payouts_overview_aggregates_fn.sql`** (`admin_payouts_overview_aggregates`, powers `GET /admin/payouts/overview`) — `scoped_rides` CTE sums `driver_earnings` unfiltered; the outstanding-payable figure is *arithmetically* still correct (legacy rides and their offsetting `payouts` rows are both included together), **but** the T4A YTD snapshot fields (`t4a_ytd_gross`, `t4a_under_500` … `t4a_over_30k`) sum the same unfiltered set — meaning this dashboard's T4A snapshot can disagree with the actual T4A slips issued by `utils/t4a_annual_job.py` (which is correct).
3. **`routes/admin/rides.py:2145-2151`**, `/admin/rides/earnings/rides` — the per-ride CSV export finance uses to reconcile against Stripe/bank ledger — has no legacy exclusion at all. A legacy-imported row with no real Stripe charge appears in the reconciliation export indistinguishable from a real one unless finance separately knows to filter it.

**Fix direction:** add `legacy_import_metadata IS NULL` to the two SQL functions (new migrations, append-only) and `**EXCLUDE_LEGACY_RIDES` to the CSV export filter — mirroring the pattern already correct everywhere else in the codebase.
**Blast radius / triage note:** no driver is at risk of double-payout, and the CRA-facing T4A job itself is correct. This is an admin/CFO-dashboard-only overstatement. **Requires live DB** to know how much any given report window is currently skewed (depends on whether imported rides' original `ride_completed_at` values fall inside that window).

### P1-A — `drivers.total_earnings` is dead code

`routes/admin/drivers.py:715`'s fleet-wide "Total Earnings" card sums `d.get("total_earnings")` from the `drivers` table — a column **never written anywhere in the codebase** (confirmed by grep across `db_supabase.py`, `repositories/*.py`, and every migration). `admin_get_driver_payouts_summary`'s own docstring already says as much: "ignored on purpose — never maintained in production." Net effect: this stat card is always $0. Not migration-specific, but directly relevant since it's the number an auditor would otherwise use to sanity-check migrated-driver totals.

### P2 — Validity gaps in `driver_import_service.py`

- **VIN**: stored plaintext, no format/checksum validation — any string passes (`:655,804`).
- **Email**: required column, but no format check — `"notanemail"` is accepted as-is.
- **Phone**: `normalize_phone` only reformats digit count; doesn't reject placeholder ranges (`555-0100`, repeated digits).
- **Documents**: a row can be imported with `status="approved"` and an already-past `expiry_date` — the importer only checks the date *parses*, not that it's in the future (`:758-765`). Mitigated at runtime by `go_online`'s own expiry re-check (`routes/drivers/status.py:309-328`), so this is a defense-in-depth gap, not a live safety hole.
- **Compliance fields optional by design**: `sgi_approved`, `work_authorization_status`, `is_permanent_resident`/`is_citizen`, all three expiry dates, and `decals_sent` are all nullable on import with no `plan.errors` entry when blank — completeness is enforced by downstream `status`/`is_verified` gating, not by the importer itself. Confirm this is the intended model, not an oversight.
- **Float-on-money (adjacent finding)**: `routes/drivers/earnings.py` daily/trip aggregation sums money fields with raw `float()` in four places (`:405-409, 591-595, 689-693, 758-762`) instead of `_d()`/Decimal — a genuine CLAUDE.md Decimal-discipline violation, low blast radius (display path only) but flagged since it's inside the file this audit reviewed line-by-line anyway.

### Verified clean

- `driver_insurance_periods` backfill (migration 65) only opens periods reflecting *current* state at migration time — does not fabricate historical periods from imported ride timestamps.
- Neither import service touches `driver_insurance_periods` at all — deliberate and safe, but undocumented as intentional; a one-line comment would prevent a future "fix."
- `driver_import_service.commit_plan` refuses to write if `plan.errors` is non-empty — validate-then-commit is enforced.
- Admin commit endpoint requires a `validation_token` bound to `sha256(csv)+batch+admin_id` — a stale/tampered CSV can't be committed without re-validating.
- License numbers are encrypted via Postgres RPC before storage; VIN plaintext storage is a documented, intentional design choice (migration 244).

---

## Phase 2 — Rider data: completeness, validity, PIPEDA integrity

### P0-C — Rider importer can silently overwrite PIPEDA-scrubbed PII on deletion-pending accounts, with no audit trail

`rider_import_service.py`'s `_prefetch_existing` (lines 107-138) matches CSV rows to existing `users` purely by phone/email — it never reads `users.status`. `build_plan` then, for any match: repopulates `email` whenever the DB's current value is falsy (line 238-239), unconditionally sets `is_rider=True` (line 242-243), and writes `stripe_customer_id` (line 236-237).

Per migration 296 (PIPEDA 30-day scrub), a `pending_deletion` account keeps its `phone` live (so the user can reactivate) but has `email`/`first_name`/`last_name`/`profile_image` NULLed after 30 days — **exactly the falsy state the import path treats as "safe to repopulate."** Compare to the real reactivation flow (`routes/auth.py:1241-1287`, `POST /auth/reactivate`): OTP-gated, resets `status`/`deletion_requested_at`/`deletion_scheduled_at`, writes an audit-log entry (`dsar_reactivated`). The import path does none of that — no status check, no audit entry, PII silently restored, account left in an inconsistent state (rider-enabled again while still internally flagged `pending_deletion`).

**Fix direction:** `_prefetch_existing`/`build_plan` must select `status` and refuse to auto-update (or flag-for-review) any existing user whose `status IN ('pending_deletion', 'deleted')`.
**Requires live DB** to confirm whether any current production rider CSV has actually collided with a `pending_deletion` account — recommend a one-off cross-reference query (import batches vs. `users.status='pending_deletion'`) before treating this as purely theoretical. **This should block further rider-CSV imports against production until the status check is added.**

### P1-B — Legacy PST likely folded silently into the fare line on imported historical receipts

`booking_import_service.py` parses `gst` into a dedicated tax-breakdown line but has **no `pst` field parsed anywhere** in the module (confirmed via full read of `FEE_COLUMNS` and the money-parsing block, lines 71-84, 448-511). If the legacy export's `total_amount` included PST with no dedicated column, it's arithmetically absorbed into the `residual` "Ride fare" line — violating the CLAUDE.md/regulatory-sk.md rule that GST and PST must appear as separate line items. When `gst <= 0`, `tax_breakdown = {}` with no "no tax data available for this legacy ride" marker anywhere — an admin/rider viewing an old imported receipt can't distinguish "legitimately $0 tax" from "legacy import didn't carry a tax field." **Requires the legacy CSV schema** to confirm whether PST data exists to recover; regardless, the fix (explicit "tax breakdown unavailable" flag on `fare_breakdown_snapshot`) is actionable without live data.

### P2-C — Rider importer never writes `legacy_import_metadata`

Migration 256 added the column specifically for this purpose; `rider_import_service.py`'s `user_row` construction never sets it (same root cause as P0-A above). Fix: populate `{batch, source, imported_at}` on both create and update, matching the booking-import pattern.

### Verified clean

- **No inferred favorite addresses** — full read confirms `rider_import_service.py` has no address-handling code path at all; correctly avoids the CLAUDE.md "never inferred" rule. (Minor completeness note: `ratings_raw`, `temp_email`, `tz` are parsed from CSV but never stored — silently discarded, not a compliance issue.)
- **No wallet-ledger-bypass risk** — because no wallet migration exists at all. Neither import service touches `wallets.balance` via any path. This also means **any legacy wallet balance riders held in the old app is simply not migrated** — flag to product as a scope decision, not a code defect.
- **Duplicate-ride prevention is DB-enforced**, not just application-level — the unique partial index on `old_booking_id` (migration 268) is a real Postgres constraint; no bypass path exists regardless of retry/batch-name/concurrent-admin-reimport.
- **No corporate-membership risk** — neither import path touches `corporate_members`/`corporate_wallets` at all.

---

## Phase 3 — Cross-surface reflection: does the same data show consistently everywhere?

| # | Data point | Consistent across surfaces? | Root cause when not |
|---|---|---|---|
| 1 | Driver period earnings: driver-app vs. admin fleet dashboards | **No** | Admin's `admin_ride_money_rollup`/`admin_payouts_overview_aggregates` SQL fns don't exclude legacy rides; every driver-app query does |
| 2 | Same driver, same admin screen — "Earnings" header vs. "Payouts" tab | **No** | `admin_get_driver_live_stats` (header) has no legacy exclusion; `admin_get_driver_payouts_summary` (Payouts tab) does — two different numbers, one screen |
| 3 | Admin `/drivers/stats` fleet-wide total_earnings | **No — confirmed $0 always** | Reads the dead `drivers.total_earnings` column (see P1-A) |
| 4 | Admin `/drivers/stats` daily earnings chart vs. driver-app day/week/month | **No** | Buckets by `created_at` (ride requested) not `ride_completed_at`; no legacy exclusion |
| 5 | `driver_daily_stats` nightly rollup (feeds weekly/monthly earnings) vs. live-rides fallback on the *same* endpoint | **No** | Rollup writer has no legacy exclusion and buckets by `created_at`; can disagree with its own fallback path |
| 6 | `/balance` vs `/earnings` total composition | **Usually equal, can diverge** | One is a live sum of fare components, the other trusts the stored `driver_earnings` column directly — any future edit path touching only one of them breaks parity |
| 7 | `/balance` payable vs `/earnings`/statement total | **No, by apparent design gap** | `/balance` excludes `ride_incentive_claims` bonuses and cancellation fees that `/earnings` and driver statements include — undocumented |
| 8 | T4A bucketing vs. earnings-screen bucketing | **Different date field, by design (documented in code)** | T4A buckets by `created_at`, earnings screens by `ride_completed_at` — both correctly exclude legacy rides, so only a period-boundary cosmetic issue, not a legacy-exclusion issue |
| 9 | Admin payouts-summary vs. `/earnings`/T4A for rides predating the `driver_earnings` column | **No, edge case** | Admin path has no fare-component fallback when `driver_earnings` is NULL; driver-facing paths do |
| 10 | Rider "total rides": admin vs. rider-app | **No, by design, unreconciled** | Admin counts all-status lifetime rides; rider-app counts completed-only, period-scoped |
| 11 | Wallet balance: admin vs. rider-app | **Yes** | Both read the identical shared helper against the single `wallets.balance` column — no independent aggregation, no divergence risk |

**Headline:** the legacy-ride exclusion pattern is consistently correct **only** within the driver-facing earnings/statement/T4A pipeline and the one admin per-driver Payouts tab. It is **not** applied on any admin fleet-wide/platform-wide aggregate surface — those overstate driver earnings whenever legacy-imported rides fall inside the queried window. This is the same root defect as P0-B, confirmed independently from the cross-surface angle.

---

## Financial reconciliation summary

Per `docs/change-log/2026-07-29-legacy-booking-import.md`, the booking import itself was verified to have a **$0.00 net-payable-delta** at the individual-ride level (legacy rides paired with offsetting `payouts` rows). This audit did not find any evidence that this pairing is broken. The financial risk identified here is entirely in **downstream aggregation**: dashboards and CSV exports that sum `driver_earnings`/`total_fare` without applying the same legacy exclusion the pairing depends on will overstate gross revenue/earnings/T4A snapshot figures, even though the underlying ledger nets to zero. **No evidence of driver double-payout, wallet fund loss, or rider overcharge was found** — every finding here is a reporting/display-layer inconsistency, not a money-movement bug. This should still be fixed before it's relied on for a real financial close or T4A comparison.

---

## What was NOT verified

- Actual row-level completeness/validity of any driver or rider currently in the production `drivers`/`users` tables — no DB access.
- Whether any `pending_deletion` rider account has actually been touched by a past import run (P0-C blast radius).
- Whether the legacy MongoDB export ever carried a separate PST field (P1-B).
- The real dollar magnitude of the admin-dashboard earnings overstatement (P0-B) — depends on the actual distribution of imported rides' `ride_completed_at` values relative to report windows in use.
- Whether `driver_earnings` and the underlying fare-component columns have ever drifted apart in production (item #6).
- Whether `driver_daily_stats` has ever been backfilled for date ranges containing legacy-imported rides (item #5).
- No visual/screen-level verification was performed — all admin-dashboard findings are from reading the backend endpoints those screens call, not from loading the actual UI.

## Recommended next steps, in priority order

1. **Block further rider-CSV imports against production** until `rider_import_service.py` checks `users.status` before touching PII (P0-C).
2. Run a one-off SQL cross-reference: import batches vs. `users.status='pending_deletion'` to size P0-C's actual blast radius.
3. Add `legacy_import_metadata IS NULL` to `admin_ride_money_rollup` and `admin_payouts_overview_aggregates` (new append-only migrations), and `**EXCLUDE_LEGACY_RIDES` to the `/admin/rides/earnings/rides` CSV export (P0-B).
4. Add `legacy_import_metadata`/batch provenance to `rider_import_service.py` (P0-A / P2-C) — same pattern as `driver_import_service.py`.
5. Either wire up `drivers.total_earnings` or remove the dead-column read in `admin_get_driver_stats` so the fleet "Total Earnings" card stops silently showing $0 (P1-A).
6. Decide and document: is legacy PST truly absent from the source data, or does it need recovering (P1-B)?
7. File the remaining P2s (VIN/email/phone validation, `/balance` vs `/earnings` composition gap, rider total-rides definition mismatch) as backlog items — none are blocking, all are worth fixing before the next migration audit cycle.

Any of the above that gets implemented needs its own Change Impact & Risk Log entry per CLAUDE.md — this document is the audit, not the fix.
