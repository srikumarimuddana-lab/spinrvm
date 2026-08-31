# Migration Data-Quality Strategy — Runbook

**Audience:** anyone reviewing legacy-imported ride/driver/rider data on the admin dashboard,
or running another batch through the Bulk Import tools.

**Why this exists:** 2026-08-31 spot-check of Admin → Rides surfaced completed rides with a
missing Driver or Rider column, and a request to know whether the 910 imported drivers /
1,938 imported riders are trustworthy counts. This runbook is the investigation's findings,
made permanent — the categories of "wrong-looking" data a legacy import produces, how to tell
which ones are real defects vs. expected artifacts of importing an incomplete old-system
export, and the concrete detection query + handling policy for each. Companion to
`migration-tool-order.md` (which tool to run, in what order) — this doc is about what to do
when a tool's output looks off.

---

## 0. Verified findings (2026-08-31, production `soavhtdhefowwvforzwb`)

Everything below was checked directly against live Supabase, not inferred from the UI.

| Check | Result |
|---|---|
| Completed rides missing `driver_id` | 7 of 261 (2.7%) |
| Completed rides missing `rider_id` | 4 of 261 (1.5%) |
| Completed rides missing **both** | 0 |
| Completed rides with `grand_total` = 0 | 7 of 261 (2.7%) — **a different 7 rows**, not the same ones as the missing-driver set; all 7 have both driver and rider matched, real distance, clustered May 13–18, 2026 |
| Rides with the "Address unavailable (imported ride)" placeholder | 6, all on pickup, 0 on dropoff |
| Rides with a genuinely blank/NULL address string | 0 (the importer never leaves this NULL — see §1.C) |
| Cancelled rides missing `driver_id` | 17 of 17 (100%) — expected, see §1.F |
| Pre-launch (created before 2026-03-30) completed rides flagged `pre_launch_test` | 25 of 25 (100%) — already fully covered, no action needed |
| Duplicate `old_driver_id` across all 910 drivers | 0 |
| Duplicate phone across all 910 drivers / all 1,938 users | 0 |
| Duplicate SIN / Stripe Connect account ID / GST-BN across drivers | 0 |
| Existing "No Driver Found" admin filter tab | **Non-functional** — see §3 |

Bottom line: the driver/rider counts are not duplicated (checked three independent keys).
The 11 completed rides with a missing side, and the 7 completed rides at $0.00, are real and
are two *unrelated* issues — don't assume one explains the other.

---

## 1. Root-cause categories and handling policy

Each category below is something a legacy import — this one or the next one — can produce.
For each: how to detect it, why it happens, and what to do about it.

### A. Completed ride, one side (driver *or* rider) unmatched

**Detect:**
```sql
select * from rides
where status = 'completed'
  and ((driver_id is null or driver_id = '') or (rider_id is null or rider_id = ''));
```

**Why it happens:** `booking_import_service.py`'s `_match_rider_driver` resolves both sides by
**phone number** against `users`/`drivers` at import time. If a booking's driver-phone or
rider-phone doesn't match anyone already in Spinr, that side is left `NULL` — the row is still
imported (only a row missing *both* sides is dropped, since nobody could ever see it). This is
almost always an **ordering bug at import time, not a data bug**: the counterpart was never
imported because the driver or rider CSV/Mongo import hadn't been run yet, or that specific
person's row was skipped by an earlier importer (e.g. rejected for a non-Canada country code,
or a parse error). This is exactly why `migration-tool-order.md`'s sequence puts driver import
and rider import **before** booking import — running booking import first guarantees some
percentage of one-sided matches.

**Fix:** re-run driver import and rider import for the batch(es) that produced the unmatched
phones, *then* re-run booking import's matching pass (not a full re-import — see whether
`booking_import_service.py` exposes a match-only repair path before writing a new one). If the
counterpart genuinely never existed in the old export (a booking referencing a since-deleted
driver, for example), it's unrecoverable — tag it (see §2) rather than leaving it
indistinguishable from a real gap.

**Never do:** silently fabricate a driver_id/rider_id to "complete" the row. A fabricated
identity on a financial/insurance record is worse than a visibly-flagged gap.

### B. Completed ride at $0.00 (`grand_total` = 0)

**Detect:**
```sql
select * from rides where status = 'completed' and (grand_total is null or grand_total = 0);
```

**Why it happens:** the old app allowed free/comped/test bookings — `total_amount` in the
source CSV was genuinely 0. `booking_import_service.py` computes `grand_total` from the
booking's own `total_amount`/`gst`/fees/tip columns; it is never zeroed by a matching failure
(these 7 rows have both driver and rider matched). The May 13–18 clustering is a strong signal
this is one batch of old-system test/promo rides, not scattered organic $0 fares.

**Fix:** none required for correctness — the number is accurate to the source. Tag as
suspected-test (§2) so revenue/fare-per-ride analytics can exclude it without deleting the
row (7-year trip-record retention applies regardless of whether the ride was free).

### C. Placeholder / missing address

**Detect:**
```sql
select * from rides where pickup_address = 'Address unavailable (imported ride)'
   or dropoff_address = 'Address unavailable (imported ride)';
```

**Why it happens:** `pickup_address`/`dropoff_address` are `NOT NULL` columns, so when the
source CSV's address field was blank, the importer substitutes this literal string (and logs
an import-time warning) rather than fail the whole row — lat/lng are still real. This is
**already self-flagging** by design; there is no silent-NULL case to worry about.

**Fix:** none required. Just needs a filter to find them (§3).

### D. Pre-launch / dormant test-era rows

**Detect:**
```sql
select * from rides where created_at < '2026-03-30' and status = 'completed'
  and coalesce((legacy_import_metadata->>'pre_launch_test')::boolean, false) = false;
```
(An empty result is the desired state — verified empty as of this writing.)

**Why it happens:** old-system activity that predates Spinr's 2026-03-30 public launch is
owner-confirmed test data, not real ride history. The Pre-Launch Legacy Data Flagging tool
(`services/pre_launch_flag_service.py`) already tags qualifying drivers/rides with
`legacy_import_metadata.pre_launch_test = true`.

**Fix:** none — already fully applied. Re-run the detection query after every new booking
import batch to confirm it stays at zero; if a future batch reintroduces pre-launch rows,
re-run the flagging tool's Preview → Apply, don't hand-patch rows.

### E. Duplicate import across overlapping source extracts

**Detect:**
```sql
select legacy_import_metadata->>'old_booking_id' as old_id, count(*)
from rides
where legacy_import_metadata->>'old_booking_id' is not null
group by 1 having count(*) > 1;
```

**Why it happens:** when a newer cumulative extract (e.g. the 08-22 zip, which the operator
has confirmed already contains everything the 07-26 zip had) gets imported after an earlier
partial extract already landed, every booking that appears in both would double-import
without a guard. `booking_import_service.py` guards this at the code level — `old_id in
already_imported` is checked per row before any insert, keyed on the old system's own booking
`_id`, so re-running the same or an overlapping extract is a no-op for rows already present
regardless of which zip supplied them first. Confirmed empty as of this writing (0 duplicate
`old_booking_id` values across all 278 rides).

**Fix:** none required, mechanism already in place. Re-run this query after every batch as a
trust-but-verify step — it's cheap and catches a regression in the guard itself.

### F. Cancelled ride missing a driver

**Detect:** `status = 'cancelled' and driver_id is null`

**Why it happens:** this is **expected, not a defect** — for both organic and legacy rides, a
ride cancelled before a driver was ever assigned never had one to record. All 17 cancelled
rides in production fall in this bucket. Don't fold this into the same "needs review" bucket
as §A — a `cancelled` ride with no driver is normal; a `completed` ride with no driver is not.

---

## 2. How to tag a row without touching ride state

**Do not** reclassify `rides.status` for any row in §A/§B to signal "this one's suspect."
`status` is the guarded ride-state-machine column (`CLAUDE.md` §Critical Conventions) and by
the time a legacy row is `completed`, GST/PST figures, T4A-relevant driver earnings, and (for
organic equivalents) insurance-period rows may already assume that status. Mutating it is a
destructive edit to an already-audited financial/regulatory record for a data-quality
observation — exactly what `CLAUDE.md`'s "additive over destructive" release gate exists to
prevent.

Instead, stamp an **additive** flag onto the existing `legacy_import_metadata` JSONB column
(same convention this migration effort has used everywhere else — see the two-marker-key
driver-detection pattern, the `pre_launch_test` flag, and every importer's own provenance
entry). Proposed shape, merged onto whatever's already on the row:

```json
{
  "data_quality": {
    "issue": "missing_driver",           // missing_driver | missing_rider | placeholder_address | zero_fare
    "detected_at": "2026-08-31T18:00:00Z",
    "detected_by": "migration_data_quality_scan"
  }
}
```

This is read-only metadata a filter can query on (`legacy_import_metadata->'data_quality'->>'issue'`)
without any ride-state, insurance, or tax implication, and it's reversible: clearing the key
after a row gets repaired (§1.A's re-match fix) is a plain `UPDATE ... SET legacy_import_metadata = legacy_import_metadata - 'data_quality'`.

## 3. Admin UI: filter design

**The existing "No Driver Found" tab is currently dead code**, unrelated to migration:
`admin-dashboard/.../rides/page.tsx` builds `apiOpts` from the active tab, but explicitly
excludes `no_driver_found` from ever setting `apiOpts.status` (`opts.tab !== "no_driver_found"`
guards the only place `status` gets set) — and there's no client-side post-filter anywhere in
`ride-list.tsx`/`page.tsx` either. Selecting that tab today silently fetches the exact same
rows as "All". It also names a **different concept** from what this investigation found:
"No Driver Found" in the live dispatch sense means a `cancelled` ride whose dispatch loop
timed out with no driver ever offered — a *dispatch-quality* signal (§1.F, but for the failure
case). The gap in §A is an *import-quality* signal on `completed` rows; there's no live
equivalent to "No Rider Found" at all, since an organic ride can't exist without a rider.

Recommendation: don't literally clone "No Driver Found" three times. Fix it to mean what its
label says (a real dispatch-timeout filter on `cancelled` rides), and add one new **"Needs
Review"** tab that covers every import-quality category from §1 (A/B/C) via the
`legacy_import_metadata.data_quality` tag from §2 — with a sub-label per row (Missing driver /
Missing rider / Placeholder address / $0 fare) so an admin can tell at a glance which of the
four they're looking at, in one place, instead of four separate tabs to check every time.
This also means the next issue category this runbook adds a row for doesn't need a new tab —
just a new `issue` value.

## 4. Ongoing guardrail

Run §1's five detection queries (A–E) after every future booking/driver/rider import batch,
before calling that batch done. Numbers should either be zero or fully explained (like this
run's May 13–18 $0-fare cluster) — an unexplained non-zero count on a fresh batch is a signal
to stop and investigate the batch's source data before moving to the next migration-order
step, not something to wave through because a prior batch had a similar count.
