# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "check the settings page for any other bugs" |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Follow-up to the settings state-wipe fix (`2026-09-03-admin-settings-save-state-wipe-fix.md`) found while re-auditing the same page/endpoint pair for more bugs |

## 1. Issue / gap identified

On `/dashboard/settings` → Operations tab, the "Heat Map Configuration" card (Enable Heat Map, Default Time Range, Heat Intensity, Radius, Blur, Show Pickups, Show Dropoffs, Corporate Heat Map, Regular Rider Heat Map — 9 controls) never persisted any change. Toggling a switch or editing a value, then clicking the page's "Save Changes" button, showed the normal "Settings saved" success toast, but a reload always showed the pre-change values. Silent, on every single save, for every field in the card — not intermittent.

## 2. Root cause

This card's 9 fields (`heat_map_enabled`, `heat_map_default_range`, `heat_map_intensity`, `heat_map_radius`, `heat_map_blur`, `heat_map_show_pickups`, `heat_map_show_dropoffs`, `corporate_heat_map_enabled`, `regular_rider_heat_map_enabled`) were bound directly into the page's shared `settings` state (the same state backing every other card) and saved through the same generic `handleSave` → `updateSettings()` → `PUT /api/admin/settings` path.

That path is backed by `backend/routes/admin/settings.py`'s `SettingsUpdateRequest` (`extra="ignore"`) writing to the single-row `app_settings` table. These 9 fields have never been declared on that Pydantic model, so every one was silently stripped by Pydantic validation before `admin_update_settings` even ran — `model_dump(exclude_none=True)` never saw them, so they were never part of what got persisted, yet the endpoint still returned its normal 200/success response.

The actual backing store for these fields is a completely different table row: `settings` (a separate table from `app_settings`) with `id="heatmap_settings"` (added by `backend/migrations/03_corporate_accounts_heatmap.sql`), read and written by its own dedicated `GET`/`PUT /api/admin/settings/heatmap` endpoints (`admin_get_heatmap_settings` / `admin_update_heatmap_settings`, backed by `HeatmapSettingsRequest`). The frontend already has a correct, working API wrapper for this — `getHeatMapSettings()` / `updateHeatMapSettings()` in `admin-dashboard/src/lib/api/heatmap.ts` — and `dashboard/heatmap/page.tsx` already uses it correctly to *read* these same settings for the live heat map. The Settings page's card was simply never wired to it; `git log -S` shows this card was added complete-but-broken (its introducing commit is the only commit touching the "Heat Map Configuration" string in this file) — a long-standing bug, not a regression from any of today's earlier PageHeader/state-wipe work.

## 3. Fix / remediation

Gave the Heat Map Configuration card its own state (`heatMapSettings`, loaded via `getHeatMapSettings()` alongside the page's other initial fetches) and its own save action (`handleSaveHeatMap`, calling `updateHeatMapSettings()` against the endpoint that actually persists these fields), independent of the page's shared `settings` state and its generic "Save Changes" button — mirroring the precedent already set on this same page by the MFA card, which also has its own independent action buttons separate from the top-level save. Every input/switch in the card now reads/writes `heatMapSettings` via a new `updateHeatMap()` helper instead of the shared `update()`, and the card shows its own inline "Save" button (with the same saving/saved states as the page header's button) plus a loading spinner while `heatMapSettings` is still in flight.

## 4. Risk & impact on existing functionality

- **Blast radius**: isolated to this one card in this one file. `getHeatMapSettings`/`updateHeatMapSettings` are pre-existing, unmodified functions already exercised by `dashboard/heatmap/page.tsx` — grepped for other consumers of `HeatMapSettings`/`getHeatMapSettings`/`updateHeatMapSettings`; only `heatmap/page.tsx` (read-only usage) and this card (now read+write) use them. No shared component or backend endpoint was changed.
- The rest of the Settings page (`settings` state, `handleSave`, every other card) is untouched — this card no longer participates in the shared save flow at all, so it can no longer regress or be regressed by any other card's field.
- Because these fields were never actually being written through the generic endpoint, this fix does not change what's stored for any *other* setting — it only makes the 9 heat-map fields start persisting for the first time in this card's history.

## 5. User-experience effect

Admin-facing only. Before: every control in "Heat Map Configuration" appeared to work (toggle a switch, click the page's Save Changes, see "Settings saved") but silently did nothing — a materially misleading UX for a card an admin might reasonably use to actually tune the demand heat map. After: the card persists correctly, but now saves independently via its own "Save" button in the card header rather than the page-level "Save Changes" button. This is a visible behavior change (a second save action appears where there wasn't one before) but is required — these fields cannot go through the shared endpoint (it doesn't and, on current design, isn't meant to accept them; that's a separate, already-established table/endpoint). Not flag-gated: this is a bugfix making a currently-inert control set functional, not new product surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Heat Map Configuration card: new `heatMapSettings`/`heatMapSaving`/`heatMapSaved` state, `getHeatMapSettings()` fetch in the initial `useEffect`, new `updateHeatMap()`/`handleSaveHeatMap()`, card's 9 fields rebound from `settings`/`update()` to `heatMapSettings`/`updateHeatMap()`, new inline Save button in the card header | Root-cause fix for the card's fields never persisting (undeclared on the generic settings endpoint's model; real backing store is a separate table/endpoint) |

## 7. Before / after

```tsx
// Before — bound into the shared `settings` state, saved via the generic
// PUT /api/admin/settings, whose model never declared these fields
<Switch
    id="heat_map_enabled"
    checked={settings.heat_map_enabled ?? true}
    onCheckedChange={(v) => update("heat_map_enabled", v)}
/>
// ...only ever reachable via the page's single top-level "Save Changes"
// button → updateSettings(settings) → fields silently dropped, extra="ignore"

// After — its own state, its own save action against the endpoint that
// actually persists these fields
<Switch
    id="heat_map_enabled"
    checked={heatMapSettings.heat_map_enabled ?? true}
    onCheckedChange={(v) => updateHeatMap("heat_map_enabled", v)}
/>
// card header:
<Button size="sm" variant="outline" onClick={handleSaveHeatMap} disabled={heatMapSaving || !heatMapSettings}>
    {heatMapSaved ? "Saved!" : heatMapSaving ? "Saving..." : "Save"}
</Button>
```

## 8. Rollback plan

Plain `git revert` — no data, no migration, no schema change, no feature flag. Reverting restores the prior (silently broken) save path with no other side effects; the `settings` table's `heatmap_settings` row and the `/api/admin/settings/heatmap` endpoints are unmodified either way.

## 9. Verification performed

- [x] Read the full `SettingsUpdateRequest` Pydantic model in `backend/routes/admin/settings.py` (all ~340 declared fields, lines 158-499) and confirmed none of the 9 `heat_map_*`/`corporate_heat_map_enabled`/`regular_rider_heat_map_enabled` names appear anywhere on it.
- [x] Cross-checked every other `settings.<field>` reference on this page (68 distinct fields) against that same model — all 68 are declared; the heat-map card's 9 fields were the only mismatch found.
- [x] Read `admin_get_heatmap_settings`/`admin_update_heatmap_settings` and `HeatmapSettingsRequest` directly to confirm the real backing store (separate `settings` table row, `id="heatmap_settings"`) and its exact field names — matched 1:1 against the frontend's own `HeatMapSettings` TypeScript interface in `lib/api/heatmap.ts`.
- [x] Read `backend/migrations/03_corporate_accounts_heatmap.sql` to confirm these columns physically live on the `settings` table, not `app_settings`.
- [x] Confirmed `dashboard/heatmap/page.tsx` already correctly uses `getHeatMapSettings()`/`updateHeatMapSettings()` for the same fields (read-only there), proving the API pair works and is the intended integration point.
- [x] `git log -S"Heat Map Configuration" -- admin-dashboard/src/app/dashboard/settings/page.tsx` — one commit only, confirming this card was born already broken, not a regression from today's other changes.
- [x] `tsc --noEmit` — clean.
- [x] `eslint` on the changed file — 0 errors; 4 pre-existing warnings, all on untouched lines (1041, 1468, 1469 — `react/no-unescaped-entities`, unrelated to this change).
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error".
- [x] Ran the existing `/dashboard/settings` smoke test (`pages.smoke.test.tsx`) — passes.
- [x] Grepped for other consumers of `HeatMapSettings`/`getHeatMapSettings`/`updateHeatMapSettings` to confirm blast radius.

## What was NOT verified

- **No live browser reproduction.** Same standing gap as every other admin-dashboard change this session — no visual-regression tooling exists, and this sandbox cannot run the app against a real backend/Supabase instance. The root cause (undeclared Pydantic fields silently dropped by `extra="ignore"`) was confirmed by direct source reading of the backend model, not by reproducing the no-op live and then confirming the fix persists a real DB row.
- **The `heatmap/page.tsx` map itself was not re-tested** — this fix only adds a second *writer* of the same already-existing, already-correct `GET`/`PUT /api/admin/settings/heatmap` endpoints; `heatmap/page.tsx`'s own read path and rendering were not touched and were not re-verified beyond confirming by inspection that it already uses the same API functions correctly.
- **The card's 3 gradient-color fields** (`heat_map_gradient_start/mid/end`, present in the backend's `HeatmapSettingsRequest`/`_DEFAULT_HEATMAP_SETTINGS` and the frontend's `HeatMapSettings` type) were never part of this card's UI before or after this fix — not a regression, just noting they're a pre-existing gap in this specific card's coverage (out of scope for a bugfix; the card only edits the 9 fields it always displayed).
