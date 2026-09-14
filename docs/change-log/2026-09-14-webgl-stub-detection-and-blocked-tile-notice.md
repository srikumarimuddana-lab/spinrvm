# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude (session requested by mkkreddy52@gmail.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | "STILL THE MAPS ARE NOT VISIBLE" — third report of the same blank ride map. Follows `2026-09-12-admin-ride-map-basemap-fallback.md` and `2026-09-13-self-hosted-basemap-and-osrm-regions.md`, **neither of which fixed it**. |

## 1. Issue / gap identified

The ride-detail Route Views map still renders as an empty box for the reporting
admin, after two prior changes aimed at it.

Two defects, both of which independently produce "empty box, no explanation":

1. **The WebGL stub probe could not detect a stub.** `hasWebGL()` returned
   `Boolean(gl.getParameter(gl.VERSION))`. Answering `VERSION` plausibly is
   exactly what a convincing stub does, so the check passed on precisely the
   machines it was written to protect, MapLibre was used, and the canvas never
   painted.
2. **The raster fallback hides total failure.** `static-route-map.tsx` sets
   `visibility: hidden` on any tile that fails to load, with no affordance at
   any threshold. One tile 404ing at the edge of coverage and *the entire tile
   source being unreachable* render identically: an empty box that says nothing.

## 2. Root cause

**Confirmed this time, not theorised.** The prior two entries both recorded an
unproven ad-blocker/WebGL-stub theory. A console probe run on the reporting
admin's own browser on 2026-09-14 returned:

- `VERDICT: STUBBED/BROKEN WebGL` — a `clearColor` → `clear` → `readPixels`
  round-trip did not return the colour it was told to paint, while
  `gl.getParameter(gl.VERSION)` answered normally. **That combination is the
  direct evidence the previous two sessions lacked**, and it proves defect (1):
  the old probe's chosen signal is the one signal a stub fakes.
- `mapCanvas: NO maplibre canvas` — there is no `.maplibregl-canvas` in the DOM,
  so `hasWebGL()` had *already* returned false and `StaticRouteMap` was in use.

The second reading is the important one, and it reframes the fix: **the raster
renderer was already running and the box was still empty.** So the Carto raster
tiles are failing too — same blocker, different asset type. Nothing in the UI
said so, which is how this survived two rounds of diagnosis.

**Still not confirmed:** *why* the raster tiles fail. Blocked by the extension,
network policy, or something else — the notice added here is what will finally
surface it. This change does not claim to fix that; it makes it visible and
stops the misdetection that put a dead MapLibre canvas on screen for other
users in the same position.

## 3. Fix / remediation

- **`hasWebGL()` → `hasRenderingWebGL()`**, extracted to
  `src/lib/map/webgl-support.ts`. Clears a 2×2 context to pure green and reads
  the pixel back; a real context returns green, a stub returns zeros, another
  colour, or throws. Fails toward `false` on anything it cannot positively
  verify — a plainer raster map costs far less than an empty panel on the
  SGI/dispute-review screen. Extracted rather than fixed in place so it is
  testable without importing maplibre-gl and its stylesheet, and so the three
  admin maps that still lack a fallback can adopt it.
- **`static-route-map.tsx` surfaces total tile failure.** Failed tile ids
  accumulate in a `Set`; when every tile in the current viewport has failed it
  renders a `role="status"` notice. A single failure stays silent — shouting
  about an ordinary edge-of-coverage 404 would train admins to ignore the
  notice that matters. The record resets when the tile set changes, since
  previous failures say nothing about different URLs.
- **First rendered tests of either admin ride map** (see §9).

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** Two components, one new module, no shared
component, no backend, no money, no ride state.

- **`hasWebGL()` had exactly one caller** and was module-private to
  `ride-route-map.tsx`; grepped to confirm. `hasRenderingWebGL` has the same
  single caller. No other admin map probes WebGL at all today.
- **`static-route-map.tsx` has one consumer**, `ride-route-map.tsx`.
- **The probe change moves users between two already-shipped code paths.** It
  cannot produce a state neither path handles. Its one behavioural effect is
  that a browser with a stubbed context now gets the raster renderer instead of
  a dead canvas — which is what the existing `useStatic` branch was built for.
- **Direction of risk is asymmetric and deliberate.** A false negative (real
  WebGL judged stubbed) costs a plainer but working raster map. A false
  positive costs a blank panel. The predicate is therefore strict: green
  channel > 200 **and** red < 50 **and** blue < 50.
- **Could a real GPU fail this?** The check is a clear-to-a-constant and a
  1-pixel read — no shaders, no extensions, no float precision. Colour-space
  rounding is tolerated by the ±50 margins. `readPixels` straight after `clear`
  needs no `preserveDrawingBuffer`, because the drawing buffer is not discarded
  until compositing.
- **CI visual regression unaffected** — `dashboard-rides` never opens the ride
  detail modal, so neither component renders during capture. jsdom returns no
  WebGL context, so the probe returns false in tests exactly as before.
- **Not touched:** `live-map.tsx`, `driver-map.tsx`, `geofence-map.tsx` still
  have no fallback chain and no WebGL probe — the same gap the previous two
  entries left open, still out of scope, still owed.

## 5. User-experience effect

Internal-admin-facing only.

- **Admin with a stubbed WebGL context** (the reporter): was a dead MapLibre
  canvas; now gets the raster renderer. If its tiles also fail they now get a
  one-line explanation instead of an unexplained empty box.
- **Admin with working WebGL:** no change whatsoever. The probe returns true
  and MapLibre is used exactly as before.
- **Any admin whose tiles are all blocked:** gains a notice that previously did
  not exist at any threshold.
- Not mid-session disruptive — affects initial map render only.

New copy, reviewed against the customer-centric tone standard (specific,
non-technical, actionable): *"Basemap tiles blocked — often an ad or privacy
blocker. The route and pins below are accurate."* It names the likeliest cause
because that is the actionable part, and reassures on the forensic content,
which is what an admin on this screen actually needs.

**Not feature-flagged.** Gate 3 asks for a flag on user-visible non-trivial
change. Both changes only ever replace a blank panel with something: a working
raster map, or an explanation. A flag defaulting off would ship the fix inert
for the exact user who reported it three times.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/map/webgl-support.ts` | **New.** `hasRenderingWebGL()` — clear→readPixels round-trip | The old `VERSION` probe is fooled by the stub it exists to catch |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-route-map.tsx` | Private `hasWebGL()` removed; imports `hasRenderingWebGL` | Same call site, correct implementation, now testable |
| `admin-dashboard/src/app/dashboard/rides/_components/static-route-map.tsx` | Tracks failed tile ids; renders a `role="status"` notice when all fail | A totally blocked basemap said nothing, which hid the real cause for two sessions |
| `admin-dashboard/src/lib/__tests__/webgl-support.test.ts` | **New** — 8 tests over the probe | The stub case had no coverage; that is why the bug shipped twice |
| `admin-dashboard/src/app/dashboard/rides/_components/static-route-map.render.test.tsx` | **New** — 8 rendered tests | First test at any tier that renders either admin ride map |

## 7. Before / after

```ts
// Before — a stub answers VERSION plausibly, so this returns true and the
// component builds a MapLibre map that never paints a pixel.
const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
if (!gl || typeof gl.getParameter !== "function") return false;
return Boolean(gl.getParameter(gl.VERSION));
```
```ts
// After — ask it to produce a pixel, which is the one thing a stub cannot fake.
const pixel = new Uint8Array(4);
gl.clearColor(0, 1, 0, 1);
gl.clear(gl.COLOR_BUFFER_BIT);
gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
return pixel[1] > 200 && pixel[0] < 50 && pixel[2] < 50;
```

```tsx
// Before — every tile failure hidden, at every threshold, with no affordance.
onError={(e) => { e.currentTarget.style.visibility = "hidden"; }}
```
```tsx
// After — still hidden individually, but a fully-blocked source now says so.
onError={(e) => {
    e.currentTarget.style.visibility = "hidden";
    setFailedTiles((prev) => (prev.has(t.key) ? prev : new Set(prev).add(t.key)));
}}
// …
{allTilesBlocked && <div role="status">Basemap tiles blocked — …</div>}
```

## 8. Rollback plan

`git revert` is a complete rollback: client-side render logic only — no
migration, no feature flag, no persisted state, no live-data mutation, no
config. Reverting restores the previous probe and the silent-tile behaviour,
i.e. the blank panel.

There is no partial-rollback hazard: the probe change only selects between two
paths that both already existed and shipped.

## 9. Verification performed

**The environment still cannot run this project's checks.** npm registry
tarballs are 403, so `admin-dashboard/node_modules` cannot be installed and
**neither `vitest` nor `npm run build` was run locally.** CI is the first real
execution. Stating this plainly again rather than letting the new tests imply
they have passed.

What was actually executed:

- **The probe was run against every case its test asserts** — 12/12 — using a
  faithful mirror driven by fake contexts: a real one, the reported stub
  (`VERSION` answers, `readPixels` returns zeros), a wrong-colour stub, a
  rounding-tolerance case, a null context, a no-op `readPixels`, each of the
  four methods missing in turn, a throwing `clear()`, and `document`
  undefined. **The same harness run against the OLD implementation returns
  `true` for the reported stub and the new one returns `false`** — that
  contrast is the fix, demonstrated rather than asserted.
- **The tile-blocked accumulation was run through its full lifecycle** — 8/8:
  quiet at zero and one failure; still quiet after the same tile errors 21
  times (the `Set` keeps it at one entry, which is why a repeated `onError`
  cannot trip the notice early); notice at total failure; identical `Set`
  reference returned on a duplicate error and on a reset-when-empty, so React
  bails out rather than re-rendering.
- Blast-radius grep: every caller of `hasWebGL`, every consumer of
  `static-route-map`, every other WebGL probe in `admin-dashboard/src` (none).
- Reviewed by `spinr-design-consistency-reviewer` (CLAUDE.md gate 10).

## 10. What was NOT verified

- **No test in this change has been run by a test runner**, and `npm run build`
  was not run. `static-route-map.tsx` gained a hook and `ride-route-map.tsx` a
  new import — both plausible TS/lint break sites, unchecked locally.
- **The render test is the first of its kind here and is itself unproven.** It
  stubs `ResizeObserver` and `clientWidth`/`clientHeight` because jsdom
  provides neither, and the component lays out nothing without a measured box.
  If those stubs are wrong the tests could pass while asserting on an empty
  DOM. The assertions are written to fail loudly in that case
  (`expect(tiles().length).toBeGreaterThan(0)` before anything else), but this
  has not been observed running.
- **Nothing has been seen rendering in a browser.** The notice's wrap behaviour
  on a narrow modal, and whether the 52px top reservation still clears the pins
  if the copy wraps to two lines, are reasoned from the existing band's
  geometry, not measured. `dashboard-rides`' visual baseline does not open the
  detail modal, so a green visual-regression run is not evidence here.
- **The actual cause of the reporter's blank raster tiles is still unknown.**
  This change makes it visible; it does not fix it. Expect a follow-up once the
  notice appears and the Network tab names the blocked host.
- **The probe has not been run on real hardware** — no GPU, no browser in this
  environment. The false-negative risk (a genuine context judged stubbed) is
  argued from the operations used, not measured across drivers. If it ever
  misfires the symptom is a plainer raster map, not a broken one.
- **Nothing here addresses `live-map.tsx`, `driver-map.tsx` or
  `geofence-map.tsx`**, which have neither a fallback chain nor a WebGL probe.
  An admin with a stubbed context still gets a dead canvas on those three.
