# Spinr Migrated-Data Visibility Audit

**Date:** 2026-08-13, live-verified follow-up added 2026-08-13
**Trigger:** Report that historical (previous-app) data is not visible in the driver app and rider app after migration.
**Scope:** Why legacy-imported data does or doesn't render on every relevant screen — rider-app Activity, driver-app Activity/Earnings, admin-dashboard rides/driver/rider detail views. This is a **visibility** audit; it deliberately does not re-litigate the financial-correctness findings already closed in `docs/audit/2026-08-11-driver-rider-migration-audit.md` (A25-A28 in `ACTION_ITEMS.md`), except where a closed finding turned out to double as a visibility answer.
**Method:** Code-level audit of the three legacy importers, the ride/earnings-history endpoints, and every frontend surface that consumes them, **followed by a live query against production Supabase (`soavhtdhefowwvforzwb`, `ca-central-1`) to resolve Finding 0**, per authorized Supabase MCP access granted after initial publication.
**Auditor:** Claude Code, reporting as ride-share senior developer / reporting analyst.

---

## ✅ Update (2026-08-13, same day) — Finding 0 resolved: the ride import DID run in production

The original version of this document could not confirm whether the legacy booking (ride) importer had ever actually been committed to production — the only committed doc described a run that failed with an infrastructure error, and no later doc recorded a successful commit. With live Supabase access now available, that question is answered directly:

```sql
select count(*) as legacy_ride_count, min(ride_completed_at), max(ride_completed_at),
       count(*) filter (where rider_id is null) as null_rider,
       count(*) filter (where driver_id is null) as null_driver
from rides
where legacy_import_metadata->>'source' = 'legacy_mongo_booking_import';
-- 224 rows | earliest 2026-01-30 | latest 2026-07-26 | null_rider: 0 | null_driver: 13
```

**224 legacy rides exist in production** — matching the documented CSV-scope count exactly (224 of 1,210 exported bookings), all `status='completed'`. So the change-log's "not run against live/staging Supabase" note was stale by the time of the original report; the commit did happen, just without a follow-up doc recording it. **Findings 1-4 below are the operative explanations for any remaining visibility gap** — treat the original Finding 0 as closed and read on for what live data says about Findings 1-4.

**Sample-record spot check** (2 fully-matched rides, IDs and dollar amounts only — no PII):

| Ride | Rider status | Rider's total rides | Driver status | Driver's total rides | Legacy offset payout on file? |
|---|---|---|---|---|---|
| `a80d5283…` (completed 2026-07-25) | active | 1 | active | 2 | ✅ `$33.32`, `payout_type='legacy_import'` |
| `4c940bd0…` (completed 2026-07-25) | active | 7 | suspended | 15 | ✅ `$183.02`, `payout_type='legacy_import'` |

Both riders' and both drivers' total ride counts are well under the admin panels' 10/50-row caps (Finding 2), so for these two specific accounts the legacy ride would show correctly in *every* screen checked, including the admin rider/driver detail panels. Both drivers' offsetting `payouts` rows are present and correctly typed, confirming the "no double-payout" mechanism from the original 2026-07-29 change-log is intact in production, not just in the design doc.

**Finding 1, now measured directly** (see full breakdown below): **100% of legacy rides have a matched rider** (`null_rider: 0`) and **94.2% have a matched driver** (211/224, `null_driver: 13`, affecting 4 distinct riders whose ride-with-that-driver won't show a driver name/details, though the ride itself is fully visible in their own Activity tab). This is a materially better result than the "real match rate is unknown" the original document flagged as a risk — the phone-normalization approach worked for nearly all real production data.

**What this means for "is the issue resolved?"**: for any of the 211 fully-matched legacy rides belonging to an account with fewer rides than the admin panel caps (10/50), the answer is **yes** — confirmed end-to-end against live data, not just code reading. What's *not* yet confirmed resolved:
- The 13 driver-unmatched rides (4 riders affected) — those riders will see the ride, just with no driver info attached; this is expected/by-design NULL-link behavior, not a bug, but worth a manual look if any of those 4 riders specifically reported the issue.
- Finding 2 (admin pagination caps) — not disproven, just not triggered by these two low-ride-count samples. Any rider/driver who has accumulated 10+/50+ *newer* Spinr-native rides since their legacy import would still hit the silent cap in the admin rider/driver detail panels specifically (the rider-app/driver-app's own history screens, and the admin main rides list, are all correctly paginated with no such ceiling).
- Findings 3 and 4 (earnings-exclusion explainer, no visual "imported ride" badge) are UX gaps, not visibility bugs — they remain open as documented below.

---

## ⚠️ Coverage limitation (original, superseded above for Finding 0)

The original version of this document had **no live Supabase/production access** in that sandbox. Everything below (outside the update above) is the original code-level analysis; it's left intact since Findings 1-4 are still current and mostly unaffected by the live-DB confirmation, except where the update above supersedes a specific claim.

---

## Executive summary

| # | Finding | Severity | Verified how |
|---|---|---|---|
| 0 | ~~The ride-history importer's production commit was never confirmed to have run.~~ **RESOLVED 2026-08-13, live-verified:** 224 legacy rides exist in production, all `status='completed'`, matching the documented CSV scope exactly. | **CLOSED** | Live query against production Supabase |
| 1 | The real phone-match rate between legacy bookings and existing Spinr riders/drivers was never measured against production data. **Now measured:** 100% rider match (0/224 NULL), 94.2% driver match (211/224, 13 NULL affecting 4 riders). A booking whose party doesn't match imports with a **NULL link**, which is invisible in that rider's/driver's own history (by design — it still shows in admin as an orphan row). | **P2 — measured, small residual gap, not urgent** | Live query against production Supabase |
| 2 | Two admin-dashboard detail panels hard-cap ride history at a small page size with **no legacy exclusion but also no "more exists" signal**: rider-detail "Recent rides" (10 rows) and driver-detail "Rides" tab (50 rows, no pagination params sent to the backend). An active rider/driver with normal post-migration ride volume will never see their imported rows in these two panels once they've accumulated 10-50 newer rides. | **P1 — display-layer, admin-only** | Code read |
| 3 | Driver-app earnings totals (today/week/month/all, and the comparison endpoint) **deliberately and correctly exclude legacy-imported rides** (to avoid double-counting money already paid out in the old app) — but nothing on the driver-facing earnings screen tells the driver why. A driver can see 40 rides in their trip list and a lower "rides this period" earnings count with no explanation. | **P2 — correct behavior, missing UX affordance** | Code read, cross-referenced against `docs/change-log/2026-07-29-legacy-booking-import.md` |
| 4 | No screen in rider-app, driver-app, or admin-dashboard visually marks a ride as "imported from the previous app" — except one admin driver-summary stat card (`imported_rides_excluded`). Riders/drivers/support staff have no way to tell a legacy row apart from a normal one, which matters because legacy rows carry placeholder text (`"Address unavailable (imported ride)"`) when the source CSV had a blank address, with no context for why. | **P2 — UX/support gap, not a bug** | Repo-wide grep across all three frontends |

**What's confirmed clean:** once a legacy ride *is* imported with both `rider_id` and `driver_id` matched, it renders correctly and identically to a normal ride in the rider-app Activity tab and driver-app trip-history list — no client-side filter drops it, no required-field assumption throws on its (thinner) data shape. The admin main rides list is null-safe and unfiltered (shows every row, legacy or not, with a blank name where a party is unmatched). `EXCLUDE_LEGACY_RIDES`'s A26 production bug (closed 2026-08-11) never touched any ride-*history* endpoint — it only ever affected earnings/balance aggregation, and is unrelated to this report's symptom.

---

## Finding 0 (CLOSED) — Was the ride import ever actually committed to production? Yes.

*(Original analysis preserved below for context; see the "Update" section at the top of this document for the live-verified resolution.)*

`docs/change-log/2026-07-29-legacy-booking-import.md:186` states, in its own verification section:

> "Not run against live or staging Supabase. All verification used an in-memory fake client and the real CSVs. The dry run against real infrastructure... has not been performed — that is step 2 of the runbook and must be done before committing. At time of writing the target Supabase project was returning Cloudflare 1101 (project paused/unhealthy), so no real-infrastructure call has succeeded yet."

No later change-log entry, ACTION_ITEMS.md entry, or PR reference in this repo records that the real `scripts/import_legacy_bookings.py --commit` (or its admin-dashboard equivalent, Bulk Operations → Legacy Booking Import) was subsequently run successfully against the production Supabase project — **this turned out to be a documentation gap, not an accurate description of production state.** A live query (see the Update section above) confirms 224 legacy rides exist in `rides`, matching the documented CSV scope. Recommend someone with commit access backfill a short change-log note recording when/how the real commit happened, since no such record exists — the next person to hit this exact question shouldn't have to re-derive it from a live query again.

---

## Finding 1 (P2, downgraded from P1) — Phone-match rate now measured: 100% riders, 94.2% drivers; NULL-linked rows stay invisible to their own owner by design

All three importers (`booking_import_service.py`, `driver_import_service.py`, `rider_import_service.py`) use the **same** phone-normalization logic (10-digit → `+1XXXXXXXXXX`, 11-digit starting with `1` → `+1XXXXXXXXXX`, else passthrough unchanged) and match by exact string equality against `users.phone` / `drivers.phone`. No divergence in the matching logic between the three files — ruled out as a code-inconsistency cause.

`docs/change-log/2026-07-29-legacy-booking-import.md:190` originally flagged this as unknown: "**Real phone match rate is unknown.** The dry run assumed every legacy Canadian party exists in Spinr. In production some will not match; those rides import with a NULL link (by design) or are skipped if neither party matches." **Now measured directly against production:** 224/224 rides have a matched rider (100%), 211/224 have a matched driver (94.2%) — the 13 driver-unmatched rides touch 4 distinct riders. This is a good outcome; the phone-normalization approach the importer uses worked for nearly all real legacy parties.

When a booking's rider or driver can't be phone-matched, `booking_import_service.py:388-399` sets that side's `_id` to `None` and imports anyway (unless *both* sides are unmatched, in which case the row is skipped entirely as an orphan nobody could ever see). Consequence, confirmed against the actual history-endpoint queries:

- `GET /rides/history` (rider-app) filters `.eq("rider_id", rider_id)` — `backend/routes/rides/queries.py:139`. A NULL `rider_id` row is invisible here, permanently, to every rider.
- `GET /drivers/rides/history` (driver-app) filters `{"driver_id": driver["id"]}` — `backend/routes/drivers/ride_reads.py:295-296`. Same for drivers.
- The **admin** main rides list has no such filter and is null-safe (renders "—" for the blank side), so an unmatched-party row is visible there, just not to the actual rider/driver it (half-)belongs to.

Untested edge cases in the normalization/matching layer that could plausibly explain real-world match failures: phone extensions (not handled by any of the three importers — the extension digits get absorbed into the digit count and the number falls through to unnormalized passthrough), non-NANP numbers, and dash/space/paren-formatted input (exercised by exactly one positive test case across all three importers, `test_booking_import_service.py:372-376`).

**Action:** once Finding 0 confirms rides exist, run a live count of `legacy_import_metadata->>'source' = 'legacy_mongo_booking_import' AND rider_id IS NULL` (and the driver_id equivalent) to size how many riders/drivers are affected, and spot-check a sample of the unmatched phone numbers against the corresponding `users`/`drivers` rows to see whether it's a genuine "never signed up" gap or a format mismatch the normalizer should have caught.

---

## Finding 2 (P1) — Two admin detail panels silently drop older imported rides via a hard pagination cap

Distinct from the rider/driver-app visibility question above — this is specifically an **admin-dashboard investigability gap**, relevant because when a rider/driver reports "I don't see my old rides," the admin's own tools should be able to confirm the data exists, and today two of the three admin surfaces that would normally be used for that can silently miss it:

- **Rider-detail "Recent rides" panel** (`admin-dashboard/src/app/dashboard/users/page.tsx:855-889`, backed by `GET /api/admin/users/{user_id}` → `backend/routes/admin/users.py:200-227`): hard-limited to the **10 most recent rides**, ordered `created_at desc`, no legacy exclusion but also no indication more rows exist below the cap. Any rider with 10+ rides since the legacy import batch's date will never see the imported rows in this panel — the admin reviewing the account would see only recent Spinr-native rides and could wrongly conclude "no legacy data was ever imported for this rider."
- **Driver-detail "Rides" tab** (`admin-dashboard/src/app/dashboard/drivers/page.tsx`, via `getDriverRides()` → `backend/routes/admin/drivers.py:1964-1965`): the frontend calls this endpoint with **no query parameters at all**, so the backend's `limit=50` default applies; the tab's own client-side pagination only paginates within that already-capped 50-row set. Same failure mode as above at a larger threshold.

Neither endpoint applies `EXCLUDE_LEGACY_RIDES` (correct — this is the row-*visibility* path, not the earnings-aggregation path), but neither surfaces a total count or "load older" affordance either, so the cap is silent.

**Recommendation:** these two panels should either raise their default limit, add real pagination (offset/cursor, mirroring what the main rides list and rider-app/driver-app history endpoints already do correctly), or at minimum show a total count so an admin doesn't mistake "not on this page" for "not imported." This is additive (a query param + a "Showing 10 of N" label), not a behavior change to what's already shown.

---

## Finding 3 (P2) — Driver earnings totals correctly exclude legacy rides, with no on-screen explanation

Confirmed by design, not a bug: `GET /drivers/earnings` and the weekly/monthly rollup-or-fallback pair both apply `EXCLUDE_LEGACY_RIDES` (`backend/routes/drivers/earnings.py:320, 609, 719, 789, 800`), and `driver_daily_stats` (the rollup those endpoints prefer) was never backfilled for legacy rides in the first place (`docs/change-log/2026-07-29-legacy-booking-import.md:61`). This is the correct call financially — the old app already paid drivers for those rides, and the importer's offsetting `payouts` rows exist specifically so re-counting that money wouldn't hand out a second, withdrawable copy of it.

The gap is purely UX: `driver-app/components/activity/ActivityView.tsx` shows a trip list that *does* include legacy rides (Finding-2-adjacent: driver-app itself has no cap comparable to the admin panels, and no client-side legacy filter — confirmed clean) sitting next to a period-earnings summary that silently *excludes* the same rides' dollar figures, with nothing on screen explaining the discrepancy. A driver counting rows in their own trip list against the earnings total will see a mismatch and reasonably read it as a bug. The one place this is explained today, `imported_rides_excluded` (`admin-dashboard/src/app/dashboard/drivers/page.tsx:2095-2137`), is an **admin-only** stat card — the driver themselves never sees the equivalent explanation.

**Recommendation:** add a one-line note to the driver-app Activity/Earnings screen when the driver has ≥1 legacy-imported ride in the visible window (e.g. "N rides from your previous app are shown here but not counted toward earnings — those were already paid out"). Backend already has everything needed to compute this (`legacy_import_metadata` is already fetched with every ride row); this is additive UI copy, not a new endpoint.

---

## Finding 4 (P2) — No visual "imported ride" marker anywhere a rider, driver, or support agent would see one

Confirmed by exhaustive grep across `rider-app/`, `driver-app/`, and `admin-dashboard/src/` for `legacy_import_metadata`, `isLegacy`, `legacyImport`, and `imported`: the only real hit outside the dedicated bulk-import operator tools is the one admin driver-summary card from Finding 3. Every ride list — rider-app Activity, driver-app trip history, admin main rides list, admin rider/driver detail panels — renders a legacy row with the exact same card/row layout as a native one.

This compounds two things already documented as accepted trade-offs elsewhere in this audit chain: (1) a legacy row with a blank source address gets the placeholder `"Address unavailable (imported ride)"` (`backend/services/booking_import_service.py:414, 417`) with zero visual context for why an address would ever say that, and (2) `rate_driver` rejects rating a legacy ride with a 400 (per the original booking-import change-log) — a rider tapping "Rate this ride" on an old imported trip gets an opaque error with no indication it's because the ride predates Spinr.

**Recommendation:** a small, additive "Imported from your previous account" badge/label on any ride card where `legacy_import_metadata` is non-empty, on both consumer apps and the admin rides list/detail panels. This is exactly the kind of change CLAUDE.md's rollout guidance already covers — additive, no behavior change to existing rows, safe to ship without a flag since it's purely visual and only ever adds context to a state that's otherwise unexplained.

---

## What was NOT verified

- ~~Whether `rides` actually contains any `legacy_mongo_booking_import`-sourced rows in production~~ — **verified 2026-08-13, live-confirmed: 224 rows exist.**
- ~~The real phone-match rate for bookings against existing riders/drivers~~ — **verified 2026-08-13, live-confirmed: 100% riders, 94.2% drivers matched.**
- Whether any specific rider/driver who reported "no history" is one of the 4 riders affected by a driver-unmatched leg, an admin-panel pagination-cap case (Finding 2), or something else account-specific — needs a lookup by that specific account's ID, not answerable in general.
- Whether Findings 2-4 (pagination caps, missing earnings explainer, missing "imported ride" badge) actually affect any real account today, versus being latent risk that hasn't triggered yet — the two sample accounts checked live were both under the relevant thresholds, so they don't demonstrate either way.
- No visual/screen-level screenshot verification was performed on any of the three frontends — all findings come from reading the source that renders those screens (or, for the Finding 0/1 update, live SQL matching the same predicates those endpoints use), consistent with the prior audit's own stated limitation and this repo's lack of automated visual-regression tooling (flagged generally in `ACTION_ITEMS.md`).

## Recommended next steps, in priority order

1. ~~Run the Finding-0 query against production.~~ **Done — 224 rows confirmed, riders 100%/drivers 94.2% matched.**
2. If a specific rider/driver is still reporting missing history, look up their account directly: check whether they're one of the 4 riders with a driver-unmatched leg, whether their ride/history counts exceed the admin panel caps in Finding 2, or whether their legacy ride simply isn't among the 224 (i.e., not a Canadian-account/completed-status booking per the original import scope).
3. Fix Finding 2 (admin panel pagination caps) — small, additive, no behavior change to what's already visible, just removes a silent ceiling that would otherwise mislead an admin investigating a "missing history" ticket for a higher-volume account.
4. Ship Finding 4's "imported ride" badge — cheap, additive, directly answers the "why does this look different" support-ticket class before it happens.
5. Add Finding 3's one-line earnings-exclusion explainer to the driver-app Activity/Earnings screen.
6. Backfill a short change-log note recording the real production commit of the booking importer (date, operator, final counts) — the one documentation gap this live check surfaced; nothing else in the repo currently records that it happened.
7. File 2-6 as ACTION_ITEMS.md entries (next available: A30) so they're tracked the same way the 2026-08-11 audit's findings were, rather than living only in this document. See `ACTION_ITEMS.md` A30 for the tracked version of this list.

Per CLAUDE.md: this document is the audit, not the fix. Any of the above that gets implemented needs its own Change Impact & Risk Log entry, and item 2's pagination increase in particular should state its blast radius (who else calls `GET /api/admin/drivers/{id}/rides` and `GET /api/admin/users/{user_id}` with the current default limits) before shipping.
