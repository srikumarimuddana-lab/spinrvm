# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code (session) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this change) |
| Related issue or gap ID | User-reported: "Route Map Snapshots" / "Route Backfill" tools on Bulk Operations always process exactly 200 rides regardless of how many rides actually need it |

## 1. Issue / gap identified

The admin Bulk Operations page's "Route Map Snapshots" and "Route Backfill" tools always asked the backend for exactly 200 rides per click, no matter how many rides were actually eligible or what the "Re-generate all" checkbox was set to. With "Re-generate all" (force=true) checked and more rides than the per-call cap, repeated clicks reprocessed the same first batch forever — rows past the cap were never reachable.

## 2. Root cause

Two separate issues, both in `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx`:

1. Both `SnapshotRegenerateSection` and `RouteRegenerateSection` hardcoded `200` as the `limit` argument on every Preview and every Regenerate call, rather than reading the real eligible count. The backend itself allows up to 500 per call (`RegenerateSnapshotsRequest.limit`/`RegenerateRoutesRequest.limit`, both `Field(..., le=500)` in `backend/routes/admin/rides.py`), so 200 was an arbitrary under-count, not a real ceiling.
2. Neither backend endpoint had an `offset` parameter, so there was no way for a second "Regenerate" click to advance past the first `limit`-sized window. In force=false mode this only matters at the margins (already-written rows drop out of the eligibility filter on their own, so repeat clicks naturally advance); in force=true mode ("re-generate everything") it's fatal — every call re-matches the exact same rows, so a fleet larger than the per-call cap could never be fully force-regenerated.

## 3. Fix / remediation

**Backend** (`backend/routes/admin/rides.py`): added `offset: int = Field(0, ge=0)` to both `RegenerateSnapshotsRequest` and `RegenerateRoutesRequest`, and threaded it through to the underlying `db.get_rows(...)` calls along with an explicit `order="id"` (offset-based paging is only meaningful with a deterministic sort — these endpoints also write to the same table they read from). For the routes endpoint, `offset` applies to the initial 500-row candidate fetch (the layer that actually bounds visibility), not the later `_needs_route()`/`limit` slice.

**Frontend** (`admin-dashboard/src/app/dashboard/bulk-operations/page.tsx`, `admin-dashboard/src/lib/api/imports.ts`):
- Preview now requests `limit=500` (the backend's real ceiling) instead of 200, so `result.total` reflects the true eligible count for the current window.
- Regenerate now uses `Math.min(result.total, 500)` as its limit — the exact count just previewed, capped defensively at the same ceiling — instead of a hardcoded 200.
- Added a local `offset` state per section, used only when "Re-generate all" is checked: it advances by `limit` after each successful (non-preview) Regenerate call, resets to 0 when the checkbox is toggled, and a small "Start over from the beginning" control resets it manually. force=false mode ignores it (always sends `offset=0`), matching its self-advancing filter.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Both `adminRegenerateImportedSnapshots`/`adminRegenerateImportedRoutes` (frontend) and the two backend routes they call have exactly one call site each — grepped the repo, confirmed via the same search used for the original tools' own Change Impact Logs (`docs/change-log/2026-08-29-regenerate-imported-snapshots-sequential-stall.md`). No other page or script calls these two backend endpoints.
- `offset` defaults to `0` on both request models, so any other caller (there are none) or a stale cached frontend bundle sending the old 3-argument request shape continues to behave exactly as before.
- Response shape unchanged for both endpoints — only the `limit`/`offset` values sent by the frontend changed, and a new optional request field was added additively.
- The `order="id"` addition changes row ordering within a single query's results but not which rows are eligible; UUID ordering has no semantic meaning to an operator, it exists purely so offset-based paging is deterministic across calls.

## 5. User-experience effect

Internal super_admin-only tool (both routes are already `require_super_admin`-gated). Before: Preview and Regenerate always operated on a fixed 200-row window with no way to see or reach more; force mode couldn't progress past that window at all across multiple clicks. After: Preview shows the true eligible count up to 500; Regenerate processes exactly that count; and with "Re-generate all" checked, each successful click visibly advances a "Next batch starts at row N" indicator so an operator with more than 500 imported rides can work through the full set click by click, with an explicit way to reset back to the start.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | Added `offset` field to `RegenerateSnapshotsRequest`/`RegenerateRoutesRequest`; passed `order="id", offset=body.offset` to both underlying `db.get_rows` calls | Let repeat force=true calls page past the per-call row cap instead of re-matching the same rows forever |
| `backend/tests/test_admin_rides_coverage.py` | Added one regression test per endpoint asserting `offset`/`order` are forwarded to the query | Lock in the paging fix |
| `admin-dashboard/src/lib/api/imports.ts` | Added `offset` parameter (default `0`) to both `adminRegenerateImportedSnapshots`/`adminRegenerateImportedRoutes` wrappers | Expose the new backend parameter to the UI |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Both regenerate sections: Preview now requests up to 500 (not 200); Regenerate now uses the previewed `result.total` (capped at 500) instead of a hardcoded 200; added per-section `offset` state that advances in force mode, resets on checkbox toggle, with a manual reset control | Fix the reported under-counting and the force-mode paging dead-end |

## 7. Before / after

```ts
// Before
const res = await adminRegenerateImportedSnapshots(force, 200, true);
// ...
const res = await adminRegenerateImportedSnapshots(force, 200);
```

```ts
// After
const res = await adminRegenerateImportedSnapshots(force, 500, true, force ? offset : 0);
// ...
const limit = Math.min(result?.total ?? 500, 500);
const res = await adminRegenerateImportedSnapshots(force, limit, false, force ? offset : 0);
if (force) setOffset(offset + limit);
```

```python
# Before
rides = await db.get_rows(
    "rides", filters,
    columns="id,pickup_lat,pickup_lng,dropoff_lat,dropoff_lng,planned_route_polyline",
    limit=body.limit,
)
```

```python
# After
rides = await db.get_rows(
    "rides", filters,
    columns="id,pickup_lat,pickup_lng,dropoff_lat,dropoff_lng,planned_route_polyline",
    order="id",
    limit=body.limit,
    offset=body.offset,
)
```

## 8. Rollback plan

`git-revert-safe` — no schema change, no data migration, `offset` is additive and defaults to `0` (identical behavior to before if omitted). A plain `git revert` fully restores the prior (under-capped) behavior with no data-level cleanup needed.

## 9. Verification performed

- [x] Backend: `pytest backend/tests/test_admin_rides_coverage.py -k "RegenerateImportedSnapshots or RegenerateImportedRoutes"` — 14 passed (12 pre-existing + 2 new offset/order regression tests).
- [x] `ruff check` / `ruff format --check` on both changed backend files — clean.
- [x] Frontend: `npx tsc --noEmit` — clean, no type errors.
- [x] Frontend: `npx eslint` on both changed files — clean.
- [x] Frontend: a real production build (`npm run build`, not just the dev server or `tsc --noEmit` alone) was run for this admin-dashboard change, per this repo's own release-gate requirement.
- [x] Blast-radius grep: confirmed single call site for each of the two frontend wrapper functions and each of the two backend routes.

## What was NOT verified

- Not exercised against a real Supabase instance or a real admin session in a browser — no live Supabase/browser access from this session. The backend logic is verified via the existing mocked-`get_rows` test harness (same pattern the pre-existing tests for these two endpoints already use), not a real query.
- This page (`bulk-operations`) has no existing automated UI test coverage at all (unlike the sibling `_components/*.test.tsx` files this session's earlier Migration Checklist work added) and is not one of the 6 pages covered by admin-dashboard's Playwright visual-regression suite. The two regenerate sections' state/paging logic (offset advancing, reset-on-toggle, the "Start over" control) was reasoned through and type-checked, not exercised via an automated component test or screenshotted — flagging this explicitly rather than implying UI-level coverage that doesn't exist.
- Did not manually click through the actual admin UI in a live browser (no browser/network access to production from this sandboxed session) — the operator's next real click-through is the first live exercise of this exact code path.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (single call site per function/route, grepped)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
