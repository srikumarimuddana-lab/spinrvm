# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session `session_01173usfHtfdzMMzYpWeWmVm`) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Closes the "still owed" list from `docs/change-log/2026-09-14-webgl-stub-detection-and-blocked-tile-notice.md` (`live-map.tsx`, `driver-map.tsx`, `geofence-map.tsx` all named there) and `docs/change-log/2026-09-14-monitoring-map-webgl-stub-guard.md`. `live-map.tsx`/`driver-map.tsx` were closed by `docs/change-log/2026-09-14-live-driver-map-webgl-stub-guard.md`; this is the fourth and last file. User asked explicitly to "pick up geofence-map.tsx (same pattern, would close out the full set)." |

## 1. Issue / gap identified

`geofence-map.tsx` — the polygon draw/edit map used by the service-areas
admin pages (general service-area boundary, airport zones) — had the same
gap already fixed three times this week elsewhere: no WebGL-capability
check, so a browser whose WebGL context is a non-drawing stub gets a
MapLibre map that never paints a pixel, with nothing on screen explaining
why.

## 2. Root cause

Same root cause as the three prior fixes this week (see
`docs/change-log/2026-09-14-webgl-stub-detection-and-blocked-tile-notice.md`
for the full writeup) — not re-investigated here, only ported.

## 3. Fix / remediation

Same guard as the other three files: a lazy
`const [webglOk] = useState<boolean>(() => hasRenderingWebGL());`
initializer (computed once, no `setState`-in-effect lint warning), an
`if (!webglOk) return;` in the mount effect before
`new maplibregl.Map(...)` is ever called, and a new render branch showing
a plain-language message instead of a blank canvas.

**Whole-component replacement, not a partial swap.** Unlike
`driver-map.tsx` (which keeps a legend bar visible above the map), this
component's entire visible surface when not readonly — the Draw / Redraw
/ Finish / Cancel / Clear toolbar and the drawing-instructions banner — is
only meaningful with a working map, so the `!webglOk` branch replaces the
whole render output rather than one region. The bare `[]` dependency array
on the mount effect is unchanged (it already carries a pre-existing
`// eslint-disable-next-line react-hooks/exhaustive-deps`) — `webglOk` was
deliberately **not** added to it, following `monitoring-map.tsx`'s
precedent (its effect also has a bare `[]`), not `live-map.tsx`/
`driver-map.tsx`'s precedent (which added it because those effects already
had real, non-empty dependency arrays).

**Alternative considered:** mirror the message to reference the "N points
defined" count the way `driver-map.tsx`'s legend bar does. **Rejected for
this round** — all three real consumers (`general-tab-form.tsx`,
`airport-zones-tab.tsx`, `page.tsx`) already render that count as a
sibling *outside* `<GeofenceMap>`/`<Suspense>`, driven by the parent's own
polygon state, so it keeps showing correctly regardless of what
`GeofenceMap` itself returns — there is no "still live" data to point at
*inside* this component the way `driver-map.tsx`'s legend bar is. The
reviewer (see below) confirmed this reasoning but flagged that the message
*could* still optionally mirror the parent's count for consistency of
tone across the four ports. Left as-is: explicitly a conscious call, not a
silent omission — the message stands alone without a trailing "X is still
live" clause, because nothing else inside this component persists
meaningfully without the map.

## 4. Risk & impact on existing functionality

- **Blast radius: one component's mount path, isolated.**
  `hasRenderingWebGL()` itself is unmodified and already shipped with its
  own test coverage; this diff adds the fourth and final call site.
  `GeofenceMap`'s real consumers, confirmed by grep:
  `general-tab-form.tsx`, `service-area-shared.tsx`,
  `airport-zones-tab.tsx`, and `page.tsx`, all under
  `admin-dashboard/src/app/dashboard/service-areas/_components/`.
- **Behavior when `webglOk` is true (every real admin today) is
  byte-identical to before** — confirmed by the "constructs a MapLibre map
  as before" regression test.
- **No visual-regression coverage exists for this page** (same disclosure
  every admin-dashboard map change this week has carried) —
  `/dashboard/service-areas` is not in the seeded Playwright baseline set.

## 5. User-experience effect

Internal-admin-facing only. An admin editing or viewing a service-area
boundary or airport zone whose browser can't render WebGL now sees a clear
explanatory message instead of a blank map with an invisible, non-working
toolbar. The parent pages' own "N points defined" text is unaffected
either way, since it lives outside this component. Every other admin: no
change whatsoever.

**Not feature-flagged**, same reasoning as the three prior fixes this
week: the only behavioral effect is replacing a silent failure with an
explanation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/geofence-map.tsx` | Adds `webglOk` guard + whole-component render branch | Close the last of the four "still owed" admin maps |
| `admin-dashboard/src/components/geofence-map.render.test.tsx` | New file, 3 tests | First render test of this component |

## 7. Before / after

Before: mount effect always called `new maplibregl.Map(...)` once
`containerRef.current` existed, regardless of whether the browser's WebGL
context could actually draw. A stub context produced a permanently blank
map with a fully non-functional (but visible-looking) drawing toolbar.

After:

```tsx
const [webglOk] = useState<boolean>(() => hasRenderingWebGL());

// mount effect
if (!containerRef.current || mapRef.current) return;
if (!webglOk) return;
// ...new maplibregl.Map(...) unchanged below this line

// render
if (!webglOk) {
    return (
        <div role="status" className="flex items-center justify-center bg-muted px-6 text-center" style={{ height, width: "100%", borderRadius: "8px" }}>
            <p className="text-sm text-muted-foreground">
                Geofence map can&apos;t render in this browser — often an ad or
                privacy blocker. Try disabling it for this site, or use a
                different browser.
            </p>
        </div>
    );
}
// ...existing toolbar + map JSX unchanged below this line
```

## 8. Rollback plan

`git-revert-safe` — client-side render logic only, no migration, no
persisted state, no live-data mutation, no config.

## 9. Verification performed

- [x] `npx eslint` on both files: 0 errors, 7 pre-existing warnings
      (identical count to `main`, confirmed via `git stash` diff).
- [x] `npx tsc --noEmit -p .`: clean.
- [x] `npx vitest run` on the new test file plus `webgl-support.test.ts`,
      `pages.smoke.test.tsx`, `monitoring-map.render.test.tsx`,
      `live-map.render.test.tsx`, `driver-map.render.test.tsx`: all
      passing, 3/3 new, 0 regressions.
- [x] **A real `npm run build`**: `✓ Compiled successfully`, TypeScript
      clean, all pages generated including `/dashboard/service-areas`. Run
      against the main checkout after syncing it to this worktree's exact
      base commit.
- [x] Blast-radius grep: every importer of `src/lib/map/webgl-support`
      (now all five map components: `ride-route-map.tsx`,
      `monitoring-map.tsx`, `live-map.tsx`, `driver-map.tsx`,
      `geofence-map.tsx`); every real consumer of `GeofenceMap` itself
      re-read to confirm the "N points defined" siblings live outside this
      component.
- [x] Reviewed by `spinr-design-consistency-reviewer` (CLAUDE.md gate 10) —
      see findings below.

## Findings from `spinr-design-consistency-reviewer`

**Verdict: SAFE TO MERGE.** Independently re-ran `eslint` (0 errors, 7
pre-existing warnings, identical to `main`), `tsc --noEmit` (clean), and
`vitest run` on the new test file (3/3 pass) — all claims verified, not
just trusted.

- Confirmed the guard executes before the map is constructed
  (`geofence-map.tsx:136`'s `if (!webglOk) return;` precedes line 138's
  `new maplibregl.Map(...)` in the same effect body).
- Confirmed the whole-component-replacement choice is the right call, with
  one correction to the stated rationale: all three real consumers *do*
  render "N points defined"/"N boundary points" reassurance, just as a
  parent-owned sibling outside `<GeofenceMap>`/`<Suspense>` rather than
  inside it. This doesn't make the swap wrong — staying agnostic of markup
  it doesn't own is arguably the better boundary — but flagged as
  **non-blocking, optional copy polish**: the message could mirror that
  count if a future pass wants tonal consistency with `driver-map.tsx`'s
  in-component legend bar. Deliberately left as-is this round (see
  section 3 above) rather than silently ignored.
- Confirmed the bare `[]` dependency array is correct and matches
  `monitoring-map.tsx`'s own precedent (pre-existing `eslint-disable`
  comment) — not an inconsistency against `live-map.tsx`/`driver-map.tsx`,
  which added `webglOk` to their *different*, already-non-empty dependency
  arrays.
- Confirmed the message text and hidden-toolbar behavior work correctly
  for both `readonly` and editable (`readonly={false}`) use.
- Confirmed no new color literals were introduced; the file's pre-existing
  hardcoded hex colors are untouched, out of this diff's scope, flagged
  INFO-only.

## 10. What was NOT verified

- Not tested in a real browser (no browser available in this
  environment).
- This closes the full four-map "still owed" list
  (`monitoring-map.tsx`, `live-map.tsx`, `driver-map.tsx`,
  `geofence-map.tsx`) — no further admin map is currently known to be
  missing this guard, but that has not been re-swept exhaustively beyond
  the five files that already import `hasRenderingWebGL`/`maplibregl`
  together.

## 11. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`.
- [x] Blast radius stated: one component's mount path, four confirmed
      real consumers, all unaffected when `webglOk` is true.
- [x] No silent behavior change to a working flow — every admin whose
      browser already worked sees byte-identical behavior.
