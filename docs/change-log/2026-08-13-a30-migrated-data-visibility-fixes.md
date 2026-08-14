# Change Impact & Risk Log — A30: migrated-data visibility fixes (Findings 2/3/4)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code (agent-assisted) |
| Surface(s) | backend, rider-app, driver-app, admin-dashboard |
| Domain (Sentry tag) | rides, admin |
| PR / commit link | branch `claude/post-migration-data-audit-dtbeg8` |
| Related issue or gap ID | `ACTION_ITEMS.md` A30, `docs/audit/2026-08-13-migrated-data-visibility-audit.md` Findings 2/3/4 |

## 1. Issue / gap identified

Three display-layer gaps left over after A30's Finding 0/1 were resolved (the legacy ride import did run in production; 224 rides exist, 100%/94.2% rider/driver phone-match): (a) two admin-dashboard detail panels silently cap ride history at 10/50 rows with no "more exists" signal; (b) driver-app earnings totals correctly exclude legacy-ride dollars with no on-screen explanation for the resulting mismatch against the trip list; (c) no screen anywhere marks a ride as "imported from the previous app."

## 2. Root cause

(a) The rider-detail panel never exposed the query-time cap; the driver-detail tab's own client-side pagination UI operated over an already-capped fetch with no accurate total to compare against. (b) `/drivers/earnings` deliberately applies `EXCLUDE_LEGACY_RIDES` (correct — avoids double-counting money the previous app already paid out) but nothing downstream of that filter communicates *why* to the driver. (c) `legacy_import_metadata` was already returned by every ride-history-reading endpoint except one (the rider-detail panel's column allowlist), but no frontend ever read it.

## 3. Fix / remediation

- **Backend:** `routes/admin/users.py`'s `_DETAIL_RIDE_COLUMNS` now includes `legacy_import_metadata` (non-PII JSONB import provenance, additive column). `routes/admin/drivers.py`'s `admin_get_driver_rides` now returns a `total_count` (via `count_documents()`) alongside the existing fetch-capped `total`.
- **admin-dashboard:** rider "Recent rides" panel shows "Showing N of Total — view all" (deep-links to the main rides list, prefilled search) whenever `total_rides` exceeds the fetched rows. Driver "Rides" tab now fetches up to 500 rows (was the backend's 50-row default) and shows a note only if even that's not enough. Both panels, plus the main rides list, badge a row "Imported" when `legacy_import_metadata` is non-empty.
- **driver-app:** `ActivityView.tsx` badges a legacy ride card and shows a one-line note above the earnings breakdown when the currently-filtered trip list contains legacy rides, computed from data already in memory.
- **rider-app:** `activity.tsx` badges a legacy ride card "Imported from your previous account."

## 4. Risk & impact on existing functionality

- **`_DETAIL_RIDE_COLUMNS`** (`routes/admin/users.py`): grepped — its only consumer is `admin_get_user_details`, itself only rendered by the rider-detail panel in `users/page.tsx`. Adding a column is additive; no existing reader is affected.
- **`admin_get_driver_rides`** (`routes/admin/drivers.py`): grepped for callers — `drivers/page.tsx`'s `loadDriverRides` and `monitoring/driver-panel.tsx`. Both destructure `res.rides`; neither reads `res.total` in a way `total_count`'s addition changes. `driver-panel.tsx` only slices the first 10 of the (already `created_at desc`-ordered) rows, so the `getDriverRides` default-limit bump from 50→500 doesn't change what it renders, only how much is fetched underneath.
- **`getDriverRides(id, limit=500)`**: signature change is backward compatible (new param has a default); the one call site with no explicit limit (`driver-panel.tsx`) now fetches more rows per call — negligible cost difference for a side panel, no behavior change to what's rendered.
- **`rides/page.tsx`'s `search` state**: moved from `useState("")` to a lazy initializer reading `?search=` from the URL. The only behavior change is that a URL carrying `?search=<term>` now pre-filters the list on load — no existing link into this page passes `?search=`, so no existing flow is affected; the one caller of this new capability (`users/page.tsx`'s new "view all" link) was added in the same change.
- **Frontend badge/note additions**: all four are purely additive conditionals (`legacy_import_metadata` non-empty / `total_count > fetched` / legacy rides present in the current filter) — a row/screen with none of those conditions true renders byte-identical to before.
- No ride state machine, money computation, or WebSocket event path is touched anywhere in this change.

## 5. User-experience effect

- **Riders:** a legacy-imported ride in the Activity tab now shows a small "Imported from your previous account" label. No other change.
- **Drivers:** same badge on legacy trip-history rows; a one-line note above the earnings breakdown explains why the trip-count and earnings total can disagree, only when at least one legacy ride is in view. Visible mid-session on next screen focus, same as any other Activity tab refresh.
- **Internal admin:** rider-detail and driver-detail panels now show a "Showing N of Total" note (only when the cap is actually hit) plus an "Imported" chip on legacy rows across all rides views. No behavior change to accounts under the caps — which, per the two live accounts spot-checked during this audit, is the common case.
- **Corporate admin:** no effect — none of the touched surfaces are corporate-scoped.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/users.py` | `_DETAIL_RIDE_COLUMNS` +`legacy_import_metadata` | Badge data for rider-detail panel |
| `backend/routes/admin/drivers.py` | `admin_get_driver_rides` +`total_count` field | Accurate cap-vs-total signal |
| `backend/tests/test_admin_extended.py` | Patched `count_documents` on existing test; +1 new test | Cover the new field |
| `rider-app/app/(tabs)/activity.tsx` | +badge on legacy ride cards | Finding 4 |
| `driver-app/components/activity/ActivityView.tsx` | +badge on legacy ride cards; +earnings-exclusion note | Findings 3 and 4 |
| `driver-app/__tests__/components/ActivityView.test.tsx` | +2 tests | Cover badge + note |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-list.tsx` | +"Imported" chip | Finding 4 |
| `admin-dashboard/src/app/dashboard/users/page.tsx` | +"Showing N of Total" note; +per-row badge | Finding 2 (rider panel) + Finding 4 |
| `admin-dashboard/src/app/dashboard/rides/page.tsx` | `search` state reads `?search=` on first render | Makes the rider panel's "view all" link actually filter |
| `admin-dashboard/src/lib/api/drivers.ts` | `getDriverRides` default limit 50→500 | Finding 2 (driver tab) |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | +`total_count` state/prop threading; +"Showing 500 of Total" note; +per-row badge | Finding 2 (driver tab) + Finding 4 |

## 7. Before / after

Driver-rides response shape (`backend/routes/admin/drivers.py`):

```python
# Before
return {"rides": enriched, "total": len(rides), "offset": offset, "limit": limit}
```

```python
# After
total_count = await db_supabase.count_documents("rides", {"driver_id": driver_id})
return {
    "rides": enriched,
    "total": len(rides),
    "total_count": total_count,
    "offset": offset,
    "limit": limit,
}
```

`rides/page.tsx` search initialization (behavior-changing: a URL with `?search=` now pre-filters on load, previously ignored):

```tsx
// Before
const [search, setSearch] = useState("");
```

```tsx
// After
const [search, setSearch] = useState(() =>
    typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("search") || ""
);
```

## 8. Rollback plan

All six changes are additive UI/response-shape changes with no data mutation, migration, or feature flag involved — `git revert` of these commits is a complete, sufficient rollback (no Stripe charges, wallet deltas, or ride state touched by any of them). No second deploy step or data-level remediation is needed.

## 9. Verification performed

- [x] Automated tests run: backend `pytest backend/tests/test_admin_extended.py -k DriverRides` (2/2 pass); driver-app `npx jest __tests__/components/ActivityView.test.tsx` (8/8 pass, including 2 new)
- [ ] Manual repro steps followed in staging — not performed; no staging environment available in this session
- [x] Blast-radius grep performed: `_DETAIL_RIDE_COLUMNS`, `getDriverRides` callers, `admin_get_driver_rides` callers, `EXCLUDE_LEGACY_RIDES`/`legacy_import_metadata` consumers — all listed in section 4
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA (new column is import-provenance JSON, not PII), additive-over-destructive (all six changes are additive)
- [ ] Feature-flagged — not flagged; all changes are additive UI-only with an explicit "renders identically when the condition is false" property, judged not to need one per CLAUDE.md's own guidance ("safe to ship without a flag since it's purely visual and only ever adds context to a state that's otherwise unexplained" — the audit doc's own recommendation for Finding 4, extended here to Findings 2/3)
- [x] `tsc --noEmit` clean on rider-app, driver-app, and admin-dashboard after every frontend commit
- [x] Real `npm run build` (admin-dashboard) run twice — after the rides-list/users-page/ride-list commit and again after the drivers-page commit — both succeeded end to end, not just `tsc`/dev server

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level step)
- [x] Blast radius is stated, not assumed (section 4 lists every other caller found by grep)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — section 5 covers rider/driver/admin effects; the one genuine behavior change (`?search=` now pre-filtering on load) is called out explicitly in section 7

## What was NOT verified

- None of this was checked against the actual rendered screens — no screenshot/visual-regression tooling exists in this repo for any of the three frontends (a standing gap noted elsewhere in `ACTION_ITEMS.md`), so this was reasoned about from source, not screenshotted.
- The rider-panel's `?search=` deep link was verified by reading the resulting `loadRides` options object, not by loading the admin dashboard in a browser and clicking through it end to end.
- Whether any of the specific accounts affected by Finding 1 (4 riders with a driver-unmatched leg) or Finding 2 (any account whose ride count actually exceeds these panels' caps) render correctly in production — the two live accounts spot-checked during the earlier live-verification pass were both well under every cap these fixes address, so they don't exercise the new "cap hit" UI paths.
