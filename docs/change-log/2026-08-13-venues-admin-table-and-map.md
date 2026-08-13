# Change Impact & Risk Log — Pickup Venues admin table + map editor

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code (session 01BCcM6c) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | PR #3883 |
| Related issue or gap ID | follow-on to `2026-08-13-saskatoon-pickup-venues.md` |

## 1. Issue / gap identified

The Pickup Venues page was a flat, unsorted, unpaginated list with status shown
only as a small "inactive" chip and no way to filter by it. That was fine for 4
venues; the Saskatoon seed takes it to ~42, all shipped dark, and the admin's job
is now to verify and activate them one at a time. There was also no map — venue
centres and pickup points could only be edited as raw latitude/longitude numbers,
which is not a workable way to confirm that a coordinate is the right door.

## 2. Root cause

The page was built when the feature had a handful of hand-entered venues, so it
never needed list management or spatial editing. Nothing was wrong with it; it
simply does not scale to the verification workload the seed created.

## 3. Fix / remediation

- **Table** replacing the list: status column with an Active/Inactive badge, a
  status filter defaulting to *All statuses* (with per-option counts), name and
  service-area search, sortable columns via the existing `useTableSort`, and
  pagination via the existing `Pagination` primitive. Clicking a row opens it.
- **Map editor** (`components/venue-map.tsx`): draggable centre marker, the
  detection radius drawn as a metre-accurate circle, numbered draggable markers
  for every pickup point, click-to-place for whichever target is selected, plus
  zoom / fit-to-venue controls.
- **Per-point distance readout**, flagged red when a point falls outside the
  detection radius — such a point can never be offered, because the rider only
  sees the chooser when their pin is already inside that radius.
- **Degraded-map disclosure**: the radius circle is a style layer, so it vanishes
  if the basemap style fails to load. The layer is now added idempotently on both
  `load` and `styledata`, and a style error surfaces a visible warning instead of
  leaving a plausible-looking map with no circle.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface, admin only.**

| Consumer | Interaction | Affected? |
|---|---|---|
| `backend/routes/admin/venues.py` | the CRUD this page calls | No — request/response shapes unchanged |
| `lib/api/pricing.ts` (`getVenues`/`create`/`update`/`delete`) | unchanged call sites | No |
| `/maps/pickup-points` → `rider-app/confirm-pickup.tsx` | reads the same table | No — no rider code changed |
| `components/geofence-map.tsx` | separate component, shares only `lib/map/maplibre-base` helpers | No — helpers are read-only imports, none modified |
| `components/sidebar.tsx` | links the page, gated by the `service_areas` module | No |

`venue-map.tsx` is new and imported by exactly one page (grepped). No shared
component was modified, so there is no 3+-page blast radius. No backend, ride
state, money, or background-loop interaction.

The real risk is **misplaced trust in the map**: an admin activating a venue
based on a map that failed to draw its radius circle. That is precisely what the
degraded-map warning and the out-of-radius flag exist to prevent, and both are
covered by e2e tests.

## 5. User-experience effect

**Internal admin only.** No rider, driver, or corporate-admin surface changes,
and nothing is visible mid-session to anyone using the apps.

For an internal admin the page changes noticeably: the list becomes a paginated,
sortable table and defaults to showing **all** statuses, so no venue disappears
from view by default. The editor gains a map alongside the existing number
fields, which still work exactly as before — the map is additive, not a
replacement, so an admin who prefers typing coordinates is unaffected. No copy
or notification changes reach customers.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/venue-map.tsx` | New. MapLibre editor for centre + pickup points | Coordinates cannot be verified as numbers |
| `admin-dashboard/src/app/dashboard/venues/page.tsx` | List → table with status column, filters, sort, pagination; map wired into the editor; null-coordinate guard | ~42 venues need list management and spatial editing |
| `admin-dashboard/e2e/venues.spec.ts` | New. Filter, search, sort, out-of-radius flag, degraded-map disclosure | None of this behaviour was covered |

## 7. Before / after

The behaviour-changing part is when the radius layer is created.

```ts
// Before — added only on `load`; if the style never loads, the circle never
// exists and the admin sees markers with no radius, with no indication why.
map.on("load", () => {
  map.addSource(CIRCLE_SRC, { type: "geojson", data: circlePolygon(...) });
  map.addLayer({ id: CIRCLE_FILL, ... });
});
```

```ts
// After — idempotent, retried on every styledata, and a style failure is
// surfaced to the admin rather than silently degrading the view.
const ensureRadiusLayer = () => {
  if (!map.isStyleLoaded() || map.getSource(CIRCLE_SRC)) return;
  map.addSource(CIRCLE_SRC, { type: "geojson", data: circlePolygon(...) });
  map.addLayer({ id: CIRCLE_FILL, ... });
  setStyleFailed(false);
};
map.on("load", ensureRadiusLayer);
map.on("styledata", ensureRadiusLayer);
map.on("error", (e) => { if (!map.isStyleLoaded()) setStyleFailed(true); });
```

## 8. Rollback plan

Frontend-only and stateless — no data is written by this change beyond the
existing venue CRUD, whose payload shape is unchanged.

- **Revert:** `git revert` of the two commits restores the previous list UI.
  Safe, because no migration, no persisted state, and no API contract changed —
  a venue saved through the new editor is byte-identical to one saved through
  the old form.
- **No feature flag.** Justified rather than assumed: this is an internal admin
  screen with no customer exposure, the previous editing path (number fields) is
  retained intact alongside the map, and Vercel keeps the prior deployment one
  click away. Flagging would have added a branch to a surface with no
  mid-session users to protect.

## 9. Verification performed

- [x] **Real production build run** — `npm run build` in `admin-dashboard/`
      succeeded and `/dashboard/venues` is present in the route manifest. Not a
      dev server, and not `tsc` alone (`tsc --noEmit` was also clean).
- [x] **e2e run against the built app in a real browser** — new
      `e2e/venues.spec.ts`: 6/6 passing (status filter narrows both ways, search,
      sort ascending and descending, out-of-radius flag, degraded-map warning).
- [x] **Pre-existing tests re-run after the change** — `misc-admin-2.spec.ts`
      venues block 2/2, and `crawl-audit.spec.ts` for `/dashboard/venues` passes,
      holding its **0-violation a11y baseline** (`e2e/a11y-baseline.json`) even
      with the new banner, badges, and table markup.
- [x] **Screenshotted, not reasoned about.** CLAUDE.md flags the absence of
      visual-regression tooling as a standing gap; both the table and the editor
      were captured and inspected, which is how the missing radius circle was
      caught in the first place.
- [x] **Lint reduced, not deferred** — this page goes 6 warnings → 1 (the
      remaining one is the pre-existing `load()`-in-effect data fetch). The
      `set-state-in-effect` and `refs` rules the repo has been burning down were
      fixed properly rather than suppressed.
- [x] **Blast-radius grep performed** — searched every importer of `venue-map`
      and every reader of the venues API; results tabled in §4.

## 10. What was **not** verified

- **The basemap has never been seen rendering.** This container blocks
  `tiles.openfreemap.org` (`ERR_TUNNEL_CONNECTION_FAILED`), so every screenshot
  shows markers and controls over an empty canvas. Marker placement, the
  distance maths, and the failure banner are all verified; **tile imagery, the
  drawn radius circle, and drag-on-a-real-basemap are not.** The tile host is an
  established production dependency (`geofence-map.tsx` already ships against
  it), so this is an environment limit rather than a known defect — but it does
  mean the circle rendering is unproven end-to-end.
- **Marker dragging was not exercised** — click-to-place, selection, and the
  distance readout were tested; `dragend` was not simulated.
- **No test covers save round-tripping through the real backend.** The e2e mocks
  the venues API, so `updateVenue` with map-edited coordinates has not been run
  against a live Supabase.
- **Not viewed on a narrow viewport.** Layout is responsive by construction
  (`lg:grid-cols-2`, `overflow-x-auto` on the table), but only 1440px was
  screenshotted.
- **Light theme not captured** — screenshots are dark theme only.

## 11. Sign-off

- [x] Rollback plan is concrete and testable — pure frontend revert, no data or
      API-contract change.
- [x] Blast radius is stated from a grep, not assumed — table in §4.
- [x] No silent behavior change to an already-shipped flow: the change is
      admin-only, additive to the existing number-field editing path, and the
      UX effect is described in §5.
