# Spinr Migrated-Data Visibility Audit

**Date:** 2026-08-13
**Trigger:** Report that historical (previous-app) data is not visible in the driver app and rider app after migration.
**Scope:** Why legacy-imported data does or doesn't render on every relevant screen — rider-app Activity, driver-app Activity/Earnings, admin-dashboard rides/driver/rider detail views. This is a **visibility** audit; it deliberately does not re-litigate the financial-correctness findings already closed in `docs/audit/2026-08-11-driver-rider-migration-audit.md` (A25-A28 in `ACTION_ITEMS.md`), except where a closed finding turned out to double as a visibility answer.
**Method:** Code-level audit of the three legacy importers, the ride/earnings-history endpoints, and every frontend surface that consumes them. Synthesized from two parallel research passes plus direct file reads.
**Auditor:** Claude Code, reporting as ride-share senior developer / reporting analyst.

---

## ⚠️ Coverage limitation — read this first

Same constraint as the prior audit: **no live Supabase/production access in this sandbox** (the Supabase MCP tool is present but requires interactive auth this session doesn't have). Everything below is either (a) directly citable from committed docs/change-logs describing what was actually run against production, or (b) code-level analysis of what *would* happen given that data. Where the two diverge, it's called out explicitly. **The single highest-priority action coming out of this audit is a live query, not a code change** — see Finding 0.

---

## Executive summary

| # | Finding | Severity | Verified how |
|---|---|---|---|
| 0 | **The ride-history importer's production commit was never confirmed to have run.** The only documented run against real infrastructure failed (Cloudflare 1101, project unhealthy); no later doc records a successful `--commit`. If that's still true, `rides` contains **zero** legacy rows — which alone fully explains "no historical rides anywhere." | **P0 — blocking, needs live-DB confirmation before anything else in this report matters** | Code + change-log citation |
| 1 | The real phone-match rate between legacy bookings and existing Spinr riders/drivers was never measured against production data — only assumed 100% in a fake-DB dry run. A booking whose party doesn't match imports with a **NULL link**, which is invisible in that rider's/driver's own history (by design — it still shows in admin as an orphan row). | **P1 — needs a live query to size** | Code + change-log citation |
| 2 | Two admin-dashboard detail panels hard-cap ride history at a small page size with **no legacy exclusion but also no "more exists" signal**: rider-detail "Recent rides" (10 rows) and driver-detail "Rides" tab (50 rows, no pagination params sent to the backend). An active rider/driver with normal post-migration ride volume will never see their imported rows in these two panels once they've accumulated 10-50 newer rides. | **P1 — display-layer, admin-only** | Code read |
| 3 | Driver-app earnings totals (today/week/month/all, and the comparison endpoint) **deliberately and correctly exclude legacy-imported rides** (to avoid double-counting money already paid out in the old app) — but nothing on the driver-facing earnings screen tells the driver why. A driver can see 40 rides in their trip list and a lower "rides this period" earnings count with no explanation. | **P2 — correct behavior, missing UX affordance** | Code read, cross-referenced against `docs/change-log/2026-07-29-legacy-booking-import.md` |
| 4 | No screen in rider-app, driver-app, or admin-dashboard visually marks a ride as "imported from the previous app" — except one admin driver-summary stat card (`imported_rides_excluded`). Riders/drivers/support staff have no way to tell a legacy row apart from a normal one, which matters because legacy rows carry placeholder text (`"Address unavailable (imported ride)"`) when the source CSV had a blank address, with no context for why. | **P2 — UX/support gap, not a bug** | Repo-wide grep across all three frontends |

**What's confirmed clean:** once a legacy ride *is* imported with both `rider_id` and `driver_id` matched, it renders correctly and identically to a normal ride in the rider-app Activity tab and driver-app trip-history list — no client-side filter drops it, no required-field assumption throws on its (thinner) data shape. The admin main rides list is null-safe and unfiltered (shows every row, legacy or not, with a blank name where a party is unmatched). `EXCLUDE_LEGACY_RIDES`'s A26 production bug (closed 2026-08-11) never touched any ride-*history* endpoint — it only ever affected earnings/balance aggregation, and is unrelated to this report's symptom.

---

## Finding 0 (P0) — Was the ride import ever actually committed to production?

This is the finding that should be checked **first**, because it can make every other finding moot.

`docs/change-log/2026-07-29-legacy-booking-import.md:186` states, in its own verification section:

> "Not run against live or staging Supabase. All verification used an in-memory fake client and the real CSVs. The dry run against real infrastructure... has not been performed — that is step 2 of the runbook and must be done before committing. At time of writing the target Supabase project was returning Cloudflare 1101 (project paused/unhealthy), so no real-infrastructure call has succeeded yet."

No later change-log entry, ACTION_ITEMS.md entry, or PR reference in this repo records that the real `scripts/import_legacy_bookings.py --commit` (or its admin-dashboard equivalent, Bulk Operations → Legacy Booking Import) was subsequently run successfully against the production Supabase project. By contrast, the **driver** and **rider** profile importers are separately confirmed live in production — `docs/change-log/2026-08-12-driver-import-service-backfill.md:3-4` and `docs/change-log/2026-08-12-rider-import-service-backfill.md:3-4` both state their importers "have been live for some time (Saskatoon launch onboarding)."

That asymmetry — **driver/rider profiles migrated, but no confirmed ride-history migration** — matches the reported symptom precisely: users who existed in the previous app can log into Spinr today with a working profile, but see no trip history, because the trips themselves were likely never written to `rides`.

**Action (do this before anything else in this report):**
```sql
select count(*), min(created_at), max(created_at)
from rides
where legacy_import_metadata->>'source' = 'legacy_mongo_booking_import';
```
- **If this returns 0 rows:** the fix is not a display bug at all — it's that the import needs to actually be run (via the admin dashboard's Bulk Operations → Legacy Booking Import, or the CLI, following the documented dry-run → commit runbook in the change-log). Everything below (Findings 1-4) becomes prep work for *after* that commit, not independent bugs to chase now.
- **If this returns >0 rows:** the import did run at some point undocumented in this repo, and Findings 1-4 below are the operative explanations for any remaining visibility gap — go straight to Finding 1.

---

## Finding 1 (P1) — Phone-match rate against production was never measured, and NULL-linked rows are invisible to their own owner

All three importers (`booking_import_service.py`, `driver_import_service.py`, `rider_import_service.py`) use the **same** phone-normalization logic (10-digit → `+1XXXXXXXXXX`, 11-digit starting with `1` → `+1XXXXXXXXXX`, else passthrough unchanged) and match by exact string equality against `users.phone` / `drivers.phone`. No divergence in the matching logic between the three files — ruled out as a code-inconsistency cause.

What *is* unverified: `docs/change-log/2026-07-29-legacy-booking-import.md:190` says outright — "**Real phone match rate is unknown.** The dry run assumed every legacy Canadian party exists in Spinr. In production some will not match; those rides import with a NULL link (by design) or are skipped if neither party matches." The only match-rate number anywhere in the repo (224/224, 100%) comes from a synthetic fake-DB dry run, explicitly caveated as not representative.

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

- Whether `rides` actually contains any `legacy_mongo_booking_import`-sourced rows in production (Finding 0) — **this is the one fact that determines whether the rest of this report is the active plan or contingency documentation**, and it requires live DB/Supabase access this session doesn't have.
- The real phone-match rate for bookings against existing riders/drivers (Finding 1) — requires the same live access, once Finding 0 is confirmed positive.
- Whether any specific rider/driver who reported "no history" is a NULL-link case, a pagination-cap case (Finding 2), or simply predates any import ever running (Finding 0) — indistinguishable without a live account lookup.
- No visual/screen-level screenshot verification was performed on any of the three frontends — all findings come from reading the source that renders those screens, consistent with the prior audit's own stated limitation and this repo's lack of automated visual-regression tooling (flagged generally in `ACTION_ITEMS.md`).

## Recommended next steps, in priority order

1. **Run the Finding-0 query against production first.** Everything else is downstream of this answer.
2. If Finding 0 comes back empty: run the actual `--commit` import following the documented runbook (`docs/change-log/2026-07-29-legacy-booking-import.md`), on a project that's currently healthy (the original blocker was an unrelated Cloudflare/project-pause error, not a code defect) — with a fresh dry-run report reviewed before commit, per the existing safeguards (validation token, super-admin gate, rate limits already built).
3. If Finding 0 comes back populated: size Finding 1 (unmatched-party counts) with a live query, and decide whether a phone-normalization improvement (extension handling, broader format coverage) is worth a re-run for the unmatched subset.
4. Fix Finding 2 (admin panel pagination caps) — small, additive, no behavior change to what's already visible, just removes a silent ceiling.
5. Ship Finding 4's "imported ride" badge — cheap, additive, directly answers the "why does this look different" support-ticket class before it happens.
6. Add Finding 3's one-line earnings-exclusion explainer to the driver-app Activity/Earnings screen.
7. File 1-6 as ACTION_ITEMS.md entries (next available letter after A28) so they're tracked the same way the 2026-08-11 audit's findings were, rather than living only in this document.

Per CLAUDE.md: this document is the audit, not the fix. Any of the above that gets implemented needs its own Change Impact & Risk Log entry, and item 2's pagination increase in particular should state its blast radius (who else calls `GET /api/admin/drivers/{id}/rides` and `GET /api/admin/users/{user_id}` with the current default limits) before shipping.
