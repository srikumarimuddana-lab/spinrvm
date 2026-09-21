# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session `session_01173usfHtfdzMMzYpWeWmVm`) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | User report: "Live monitoring map does not show anything ?" (screenshot: the Live Ride Monitoring page's map panel entirely blank, no error, no banner). Follows `docs/change-log/2026-09-13-self-hosted-basemap-and-osrm-regions.md` and `docs/change-log/2026-09-14-webgl-stub-detection-and-blocked-tile-notice.md`, both of which fixed the identical root cause on a *different* admin map (the ride-detail Route Views map) and explicitly flagged `monitoring-map.tsx`/`live-map.tsx`/`driver-map.tsx` as "still owed." |

## 1. Issue / gap identified

The Live Ride Monitoring page's map (`monitoring-map.tsx`) renders as an
empty box with zero explanation, on the same class of browser that made the
ride-detail map blank twice this week. Confirmed by reading the code, not
assumed: this file has no WebGL-capability probe at all and constructs a
`maplibregl.Map` unconditionally.

## 2. Root cause

Same root cause already confirmed (console probe, 2026-09-14) for the
ride-detail map: some browsers — commonly due to a privacy or ad-blocking
extension — hand back a **stubbed WebGL context** that answers every
capability query plausibly but never actually paints a pixel. MapLibre's own
`"load"` event only reflects style/tile JSON reaching the page over the
network, not a GPU drawing anything, so this component's `isLoaded`/
`basemapStatus`/`loadError` states all report success while the canvas stays
blank. The fix for this exact failure mode (`hasRenderingWebGL()` in
`src/lib/map/webgl-support.ts`) already exists and already shipped — it was
applied only to the ride-detail map, not this one. This is a gap-closing
change, not a new investigation.

## 3. Fix / remediation

- Added `const [webglOk] = useState<boolean>(() => hasRenderingWebGL());` —
  a lazy initializer computed once, the same idiom `ride-route-map.tsx`
  already uses for this exact probe (checked directly against that file
  rather than assumed).
- The mount effect now returns before ever calling `basemapChain()`/
  `buildMap()` when `!webglOk` — no MapLibre map is constructed at all in
  that case, so there is nothing left to silently fail to paint.
- A new render branch (checked before the existing `loadError` branch, since
  a WebGL-stub failure is detected earlier in the lifecycle than a
  tile-load failure) shows a plain-language message instead of the map:
  *"Live map can't render in this browser — often an ad or privacy blocker.
  Try disabling it for this site, or use a different browser. Driver and
  ride data elsewhere on this page is still live."*, `role="status"`.

**Alternative considered:** build a full raster/DOM fallback renderer for
this map, mirroring `static-route-map.tsx`'s approach for the (much
simpler) ride-detail case. **Rejected** — `monitoring-map.tsx` is a live,
WebSocket-driven fleet map with many moving driver markers, ride pins, a
demand overlay, and service-area polygons; a non-WebGL equivalent that kept
all of that live-updating would be a materially larger, separate feature
with its own design and testing needs, not a same-day port. The actionable
outcome for an affected admin is the same either way — fix their browser
environment — so a clear, honest message is the right-sized fix; CLAUDE.md's
simplicity-first principle argues against building new rendering
infrastructure to solve what is fundamentally a "tell the admin what's
wrong" problem.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one component's mount path.**
  `hasRenderingWebGL()` itself is unmodified, already shipped, and already
  has its own 12/12 test coverage from the ride-detail fix — this diff only
  adds a new call site. Grepped every other importer of
  `src/lib/map/webgl-support`: only `ride-route-map.tsx` (existing) and now
  `monitoring-map.tsx` (this diff).
- **Behavior when `webglOk` is true (the overwhelming majority of real
  admins) is byte-identical to before** — the new code only ever executes
  an early `return` when the probe is false; nothing on the true path
  changed. Confirmed by the "constructs a MapLibre map as before" regression
  test in the new render-test file.
- **`monitoring-map-demand-fill.test.ts` unaffected** — it only imports the
  two pure exports (`areaFillColor`/`areaFillOpacity`), neither of which
  this diff touches.
- **`pages.smoke.test.tsx`'s stub of this component is unaffected** — it
  replaces the whole component with a bare stub before any of this file's
  logic runs, confirmed by running that suite (27/27 still pass).
- **Sibling page UI (stats row, active-rides list, filters, toolbar) lives
  in `page.tsx`, not `monitoring-map.tsx`** — confirmed by reading
  `page.tsx` directly; none of it depends on whether the map canvas paints,
  so the message's "still live" claim is accurate, not just plausible.
- **Not touched, still owed:** `live-map.tsx`
  (`app/dashboard/rides/live/[id]/live-map.tsx`) and `driver-map.tsx`
  (`components/driver-map.tsx`) have the identical gap — no probe, no
  fallback — and are deliberately out of scope for this diff, matching the
  task-decomposition precedent this session's C49 work already followed
  today (one themed slice at a time). Flagged as the next pickup, not
  silently left unmentioned.

## 5. User-experience effect

Internal-admin-facing only, Live Ride Monitoring page.

- **Admin with a stubbed/broken WebGL context:** was a dead, unexplained
  blank map; now sees a clear, actionable message and the rest of the page
  (driver/ride data, filters, stats) continues to work exactly as before.
- **Every other admin:** no change whatsoever — the probe returns true and
  the map builds exactly as it did before this diff.
- Not mid-session disruptive — the probe runs once at mount; there is no
  code path where a working map goes blank later in the same session (a
  genuine mid-session GPU/context-loss event is a separate, pre-existing gap
  this diff does not claim to cover — see §10).

**Not feature-flagged.** Gate 3 asks for a flag on user-visible non-trivial
change to a shared component used by 3+ pages — this is a single-page,
single-component change (not shared), and like the ride-detail fix it
precedent-sets from, the only behavioral effect is replacing a silent
failure with an explanation; a flag defaulting off would ship the fix inert
for the exact admin who reported it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.tsx` | Adds a `webglOk` lazy-initialized probe; mount effect returns before constructing a MapLibre map when false; new render branch shows a message instead | Close the exact gap the two prior sessions' fixes explicitly left open for this file |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.render.test.tsx` | New file, 2 tests | First rendered test of this component at any tier; proves the guard actually fires and that the pre-existing path is unaffected |

## 7. Before / after

```tsx
// Before — no capability check; a stubbed WebGL context still gets a
// MapLibre map built, which never paints a pixel and never says why.
useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    let detach: (() => void) | null = null;
    const chain = basemapChain(resolvedTheme);
    const buildMap = (attempt: number) => { /* ... */ };
    // ...
    buildMap(0);
}, []);
```
```tsx
// After — bail out before ever constructing the map.
const [webglOk] = useState<boolean>(() => hasRenderingWebGL());

useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    if (!webglOk) return;
    // ... unchanged from here
}, []);

// Render:
if (!webglOk) {
    return <div role="status" className="...">Live map can't render...</div>;
}
if (loadError) { /* unchanged */ }
```

## 8. Rollback plan

`git-revert-safe` — client-side render logic only, no migration, no feature
flag, no persisted state, no live-data mutation, no config. Reverting
restores the previous unconditional-build behavior (i.e. the blank panel for
affected browsers).

## 9. Verification performed

This session has real, working `node_modules` (symlinked from the main
checkout, which has them installed) — unlike the two sessions that authored
the underlying `hasRenderingWebGL()` fix, which could not reach the npm
registry at all and never ran a single check locally. Everything below was
actually executed, not reasoned about:

- [x] `npx eslint` on both changed files: **0 errors.** The file's
      pre-existing 8 warnings (ref-during-render × 3, one pre-existing
      `setState`-in-effect on the unrelated `setBasemapStatus("ok")` call,
      exhaustive-deps × 4) are unchanged in count — confirmed by running
      eslint on the unmodified file via `git stash` first and diffing the
      output.
- [x] `npx tsc --noEmit -p .`: clean, no errors.
- [x] `npx vitest run` on the new test file plus every related existing
      test file: **`webgl-support.test.ts`, `monitoring-map-demand-fill.test.ts`,
      `static-route-map.render.test.tsx`, `static-route-map.test.ts`,
      `maplibre-basemap-chain.test.ts`, `monitoring-toolbar.test.tsx`,
      `pages.smoke.test.tsx` — 108/108 passed, 0 failed, 0 regressions.**
- [x] **A real `npm run build` (production Next.js/Turbopack build) was
      run and passed** — `✓ Compiled successfully`, TypeScript finished
      with no errors, all 79 pages generated. Run from the main checkout's
      real `node_modules` (this worktree's is a symlink, which Turbopack's
      Rust resolver refuses to cross with a "points out of the filesystem
      root" error — an environment quirk unrelated to this diff) after
      temporarily copying the two changed files there and restoring the
      main checkout to clean immediately after.
- [x] Blast-radius grep: every importer of `src/lib/map/webgl-support`
      (two: `ride-route-map.tsx`, unmodified; this file); every consumer of
      `monitoring-map.tsx`'s exports (`page.tsx` only); confirmed the
      sibling stats/active-rides UI lives in `page.tsx`, not this component.
- [x] Reviewed by `spinr-design-consistency-reviewer` (CLAUDE.md gate 10) —
      see findings below.

## 10. What was NOT verified

- **Not tested in a real browser.** The render test proves the guard logic
  fires correctly against a mocked `maplibre-gl`; it does not prove the
  message's layout/wrap behavior at real viewport sizes, which
  `spinr-accessibility-reviewer`-style visual confirmation would need. No
  automated visual-regression tooling covers this page's map panel opening
  a live WebSocket-driven state (same disclosure every admin-dashboard map
  change in this area has carried this week).
- **A genuine mid-session WebGL context loss** (GPU driver crash, tab
  backgrounding on some platforms) is a different failure mode from a
  stubbed context at mount — this diff's probe runs once, at mount, and
  does not detect a context that later goes bad. Out of scope here; not
  claimed to be covered.
- **`live-map.tsx` and `driver-map.tsx`** have the identical gap and are
  explicitly not touched by this diff — see §4.
- **Whether the reporting admin's specific browser is actually a WebGL-stub
  case (vs. e.g. all tile hosts blocked, which this file's existing
  `loadError` path already handles) is not confirmed for this specific
  report** — the fix is applied because it closes a real, confirmed gap
  (this file had zero WebGL-capability handling of any kind), not because
  the reporting admin's browser was independently diagnosed the way the
  ride-detail reporter's was via a console probe.

## Findings from `spinr-design-consistency-reviewer`

**Verdict: ON-BRAND & COMPLETE — safe to merge.** Independently re-ran
`eslint`/`tsc`/the new render test/the two named adjacent suites rather than
trusting this session's report; confirmed identical results. Loaded brand
context and confirmed the diff introduces zero new color literals (only
pre-existing `bg-muted`/`text-muted-foreground` tokens already used
elsewhere in this file) — no brand-drift risk. Re-derived the control flow
independently: confirmed the `!webglOk` render branch sits after all hooks
(Rules-of-Hooks compliant), confirmed `containerRef.current` is `null` for
the component's whole life whenever that branch is taken, and confirmed
`loadError`/`webglOk` are mutually exclusive by construction (`loadError`
can only be set from inside `buildMap()`, which never runs when `webglOk`
is false) — so the branch ordering is correct and the outcome is
unambiguous either way. Confirmed the message copy's "still live" claim
directly against `page.tsx` (toolbar counts, Active Rides list, and Alert
Feed all derive from `driversMapRef`/`ridesMapRef` + the WS/REST feed, zero
dependency on the map object) and confirmed the tone/styling choice
(`text-muted-foreground`, not `text-destructive`) is the correct semantic
downgrade — a browser-configuration notice with a workaround is not the
same severity as a real system failure.

**One non-blocking WARNING, real but out of this diff's scope:** when
`webglOk` is false, `MonitoringMap`'s `onReady` callback never fires (it
only ever fires from inside `map.on("load", ...)`, now unreachable), so
`page.tsx`'s `mapHandlesRef.current` stays `null` for the page's whole
life. Every other call site in `page.tsx` already tolerates this via
optional chaining (`mapHandlesRef.current?.foo()`, the file's own
established, consistent pattern for "map not ready yet") — but the
service-area "jump" pill buttons (`page.tsx:797-809`) and the Follow toggle
(routed through `MonitoringToolbar`) are UI whose entire purpose *is* a
map-visual effect, with no other on-screen feedback substituting for it.
An admin whose browser can't render the map can click "Regina" repeatedly
and nothing will ever happen, anywhere, with zero indication why — unlike
every other silently-no-op'd handle call, which has some other UI reacting
to the same event. **Not introduced by a logic bug in this diff** — it's a
previously-impossible state this diff makes newly reachable, in a
different file (`page.tsx`) than the one this diff touches.

**Deliberately not fixed here.** Closing it properly needs a new reactive
readiness signal threaded from `MonitoringMap` to `page.tsx` (the existing
`mapHandlesRef` is a ref, not state, so `page.tsx` can't reactively hide/
disable UI based on it today) — a real, separable design change to a
second file, not a quick addition to this one. Bundling it in would widen
this diff past what CLAUDE.md's task-decomposition/surgical-changes rules
ask for, for a fix whose actual priority (stop showing a silently blank
map) is already fully addressed. Tracked as a follow-up instead, matching
how both change-logs this diff ports from tracked their own "still owed"
gaps rather than silently leaving them unstated.

Also confirmed by the reviewer: a genuine **mid-session WebGL context loss**
(as opposed to a stubbed context detected at mount) is unhandled here —
but it's equally unhandled in `ride-route-map.tsx`, the file this fix is
ported from, so this is a pre-existing gap in the underlying pattern, not
something this diff regresses.

## 11. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`.
- [x] Blast radius stated: one component's mount path, isolated.
- [x] No silent behavior change to a working flow — every admin whose
      browser already worked sees byte-identical behavior; only the
      previously-blank-and-unexplained case changes, to explained.
