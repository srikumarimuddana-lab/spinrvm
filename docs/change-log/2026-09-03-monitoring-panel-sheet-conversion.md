# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "simplify and rearrange the side panel", scoped after an inventory pass and confirmed via AskUserQuestion: convert Monitoring's detail panel to match the slide-over pattern already used on Drivers/Safety, rather than leaving it as a permanently-reserved 3rd column |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | commit on branch `claude/admin-monitoring-map-legibility` (stacked on the map-legibility fix, same PR) |
| Related issue or gap ID | None filed — follow-on scoping from the same conversation as the map-legibility fix |

## 1. Issue / gap identified

Across the admin dashboard, "click a row/marker to see detail" is implemented three different ways: Drivers and Safety both use the shared `Sheet` slide-over component (with a consistent inline close button in the panel's own header); Monitoring used a bespoke, permanently-reserved `w-72` third column that stayed on screen (with its own "No selection" filler state) whether or not anything was selected, and closed via a floating `absolute`-positioned X button — a pattern found nowhere else in the app.

## 2. Root cause

The Monitoring page's 3-column layout (list | map | detail) predates the `Sheet`-based pattern Drivers/Safety later standardized on, and was never migrated when that convention was established.

## 3. Fix / remediation

- **Monitoring's detail panel is now a `Sheet` slide-over** (`side="right"`, `sm:w-[420px]`), matching the same `showCloseButton={false}` + `sr-only` `SheetTitle`/`SheetDescription` pattern already used by Drivers (`w-[90vw]…w-[70vw]`) and Safety (`w-[640px]`) — sized narrower than those two since `DriverPanel`/`RidePanel` are read-only detail views, not edit forms.
- The map column is no longer squeezed by a permanently-reserved 3rd column — it now gets the full remaining width by default, and the panel only appears (overlaying the map) once a driver/ride is actually selected. The "No selection — click a driver marker or a ride in the list…" filler state is gone entirely, since there's nothing to fill when the Sheet is simply closed.
- **Close-button consistency**: added an optional `onClose?: () => void` prop to `DriverPanel` and `RidePanel`, rendered as an inline `<Button variant="ghost" size="icon-sm">` in each panel's own header row — the exact convention Drivers/Safety already use — replacing the old floating `absolute right-2 top-2` X button that page.tsx used to render on top of each panel.

## 4. Risk & impact on existing functionality

- **Blast radius**: `driver-panel.tsx` and `ride-panel.tsx` are each used in exactly one place (Monitoring's `page.tsx`) — grepped to confirm, no other importers. The new `onClose` prop is optional and additive; nothing else changes if a future caller omits it.
- **Behavior change (intentional, confirmed with the user before implementing)**: selecting a driver/ride now overlays the map with a backdrop instead of sitting beside it in a fixed column. All existing interactions are unchanged — clicking a driver marker, a ride-list row, or an activity-feed event still selects it and pans the map (`panTo`); "follow mode" still works; cancel/complete-ride actions on the ride panel are untouched, just relocated into the Sheet.
- **Accessibility**: Radix's `Dialog.Content` (which `Sheet` wraps) requires an accessible title — provided via `sr-only` `SheetTitle`/`SheetDescription`, matching Drivers/Safety's existing pattern exactly rather than inventing a new one.
- Nothing here touches ride state, dispatch, payments, or data fetching — purely a presentation-layer change to an already-selected item's display.

## 5. User-experience effect

Admin-facing only (Live Monitoring screen). The map now uses the screen's full width by default instead of permanently losing a 288px column to an often-empty panel. Clicking a driver or ride now opens a slide-over (Escape, backdrop click, or the header's close button all dismiss it) instead of a panel that was always present but empty. The panel's own close button moved from a floating icon in the corner to an inline button in the header, next to the panel's other content — visually consistent with how Drivers and Safety already behave.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | 3rd column → `Sheet` slide-over; removed "No selection" filler and floating close buttons; unused `X` import dropped | Match the app's established detail-panel pattern |
| `admin-dashboard/src/app/dashboard/monitoring/driver-panel.tsx` | Added optional `onClose` prop + inline header close button | Consistency with Drivers/Safety's close-button convention |
| `admin-dashboard/src/app/dashboard/monitoring/ride-panel.tsx` | Added optional `onClose` prop + inline header close button | Same as above |

## 7. Before / after

```tsx
// Before — page.tsx, always-reserved 3rd column
<div className="w-72 shrink-0 overflow-hidden border-l border-border">
  {selected === null ? (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
      <p className="text-sm font-medium">No selection</p>
      ...
    </div>
  ) : selected.type === "driver" && selectedDriver ? (
    <div className="relative h-full">
      <button className="absolute right-2 top-2 z-10 ..." aria-label="Close driver panel">
        <X className="h-3.5 w-3.5" />
      </button>
      <DriverPanel driver={selectedDriver} onRideClick={handleSelectRide} />
    </div>
  ) : ...}
</div>

// After
<Sheet open={selected !== null} onOpenChange={(open) => { if (!open) { setSelected(null); ... } }}>
  <SheetContent side="right" showCloseButton={false} className="... sm:w-[420px] ...">
    <SheetTitle className="sr-only">...</SheetTitle>
    {selected?.type === "driver" && selectedDriver ? (
      <DriverPanel driver={selectedDriver} onRideClick={handleSelectRide} onClose={() => { setSelected(null); ... }} />
    ) : ...}
  </SheetContent>
</Sheet>
```

## 8. Rollback plan

Pure `git revert` — no data, no migration, no feature flag. Shipped unconditionally, same as the map-legibility fix in this PR: this is a presentation-consistency fix bringing one page in line with an already-established app pattern, not a new style opinion needing a flag.

## 9. Verification performed

- [x] `tsc --noEmit` — no new errors across all three changed files.
- [x] `eslint` — 0 errors on all three changed files (same pre-existing eslint 10.9.1/eslint-plugin-react workaround as the map-legibility fix in this same PR: linted with a local unsaved `eslint@9.39.5`, then restored the pinned version). All 15 warnings present are pre-existing and at line numbers untouched by this diff (confirmed by inspection — e.g. `ride-panel.tsx`'s `Date.now()` purity warning at line 56 is nowhere near the header/close-button edit at ~line 88).
- [x] Grepped for other importers of `DriverPanel`/`RidePanel` before adding the `onClose` prop — confirmed single call site each, so the additive prop can't affect anything else.
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error", not a truncated tail.

## What was NOT verified

- **No live browser check.** Same standing gap as the rest of this PR — admin-dashboard has no visual-regression tooling, and this sandbox couldn't reach a running instance of the app. Recommend clicking through Monitoring (select a driver, select a ride, close via the header button, via Escape, and via backdrop click) before merging.
- **The rides live-tracking info panel** (`rides/live/[id]/page.tsx`) was reviewed as part of the same "every side panel" scope but deliberately left unchanged — it's already using a consistent card layout, and its one hardcoded color (a "LIVE" pulsing badge) is a previously-reviewed, explicitly-commented exception (`#2816`), not an oversight. Noted here so it's clear this was a decision, not a skip.
