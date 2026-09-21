# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session `session_01173usfHtfdzMMzYpWeWmVm`) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/5425 |
| Related issue or gap ID | ACTION_ITEMS.md C115 — filed by `spinr-design-consistency-reviewer` while reviewing `docs/change-log/2026-09-14-monitoring-map-webgl-stub-guard.md`, deliberately not fixed there (see that entry's "Deliberately not fixed here" section). |

## 1. Issue / gap identified

On the Live Monitoring page, the service-area "jump" pill buttons and the
Follow toggle silently do nothing — no error, no visual change, anywhere —
when the centre map can't render (a stubbed/non-drawing WebGL context, the
same rare browser configuration `docs/change-log/2026-09-14-monitoring-map-webgl-stub-guard.md`
already fixed the blank-map symptom for).

## 2. Root cause

`MonitoringMap`'s `onReady` callback only ever fires from inside
`map.on("load", ...)`, which is unreachable once the WebGL guard bails out
(`if (!webglOk) return;` before the map is ever constructed). In that state
`page.tsx`'s `mapHandlesRef.current` stays `null` for the page's entire
life. Every other `mapHandlesRef.current?.foo()` call site in `page.tsx`
already tolerates that via optional chaining and has *some other* on-screen
feedback for the same user action (a detail panel opening, a marker
updating) — but the jump buttons (`handleAreaFit` →
`mapHandlesRef.current?.fitArea(...)`) and the Follow toggle's entire
purpose *is* the map-visual effect itself, so there was nothing else to
fall back on. `mapHandlesRef` is a plain ref, not React state, so `page.tsx`
had no way to reactively hide/disable that UI based on it.

## 3. Fix / remediation

- Added an optional `onCanRenderChange?: (canRender: boolean) => void` prop
  to `MonitoringMap`. It fires once, on mount, with the same `webglOk` value
  the component's own render branch already uses — `webglOk` is a
  `useState` lazy-initializer that never changes post-mount, so `[webglOk]`
  as the effect's dependency array fires this exactly once by construction,
  not something needing ongoing synchronization. Implemented with the same
  callback-ref idiom (`onCanRenderChangeRef`) `onSelectDriverRef`/
  `onSelectRideRef` already use in this file, so the effect doesn't need
  the callback prop itself in its dependency array.
- `page.tsx` adds `const [mapCanRender, setMapCanRender] = useState(true)`
  (optimistic default — the overwhelming majority of sessions), passes
  `onCanRenderChange={setMapCanRender}` to `MonitoringMap`, and threads
  `mapCanRender` to both affected controls:
  - The service-area jump-pill buttons get `disabled={!mapCanRender}`, a
    `title` explaining why, dimmed/`cursor-not-allowed` styling, and — since
    a `title` tooltip doesn't reliably fire hover events on a `disabled`
    element in every browser — a small nearby text line ("Map can't render
    in this browser — jump buttons disabled.") shown only in that state,
    matching the "explain, don't just disable silently" tone
    `MonitoringMap`'s own WebGL-guard render branch already established.
  - `MonitoringToolbar` gets a new optional `mapCanRender?: boolean` prop
    (default `true`, so every existing/other caller is unaffected — there
    are none besides `page.tsx`, see §4). The Follow `Button` gets
    `disabled`, a `title`, and an adjacent "map unavailable" text label
    (same reasoning: a `title` on a disabled `<button>` isn't a reliable
    sole channel).

**Alternative considered:** poll `mapHandlesRef.current` on an interval or
via a `MutationObserver`-style check instead of a dedicated callback prop.
**Rejected** — `webglOk` is already known synchronously inside
`MonitoringMap` at mount; polling from the parent would be strictly more
code, a real (if small) perf/complexity cost, and a false economy since the
value never changes after mount anyway. A one-shot callback prop is the
smallest correct signal for a value that's set once and stays fixed for the
component's whole life — CLAUDE.md's simplicity-first principle and the
C115 entry's own recommendation both point the same way.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped every importer of both changed
  components:
  - `MonitoringMap` — production: `page.tsx` only. Tests:
    `monitoring-map.render.test.tsx`, the new
    `monitoring-jump-buttons-availability.render.test.tsx` (mocks it
    controllably), `pages.smoke.test.tsx` (stubs it),
    `monitoring-map-demand-fill.test.ts` (imports only its two pure helper
    exports, unaffected).
  - `MonitoringToolbar` — production: `page.tsx` only. Tests:
    `monitoring-toolbar.test.tsx`, `pages.smoke.test.tsx` (stubs it), the
    new `monitoring-toolbar-availability.render.test.tsx`.
  - No other page, component, or hook reads `MapHandles`, `onReady`, or
    `ToolbarProps` — this is a single production call site on both ends.
- **`onCanRenderChange` and `mapCanRender` are both optional** with a
  default of `true` — any caller (there are none today besides `page.tsx`)
  that doesn't pass them gets byte-identical behavior to before this diff.
- **Behavior when the map renders fine (the overwhelming majority of real
  admins) is unchanged**: `mapCanRender` starts `true` and `MonitoringMap`
  itself confirms it with `true` on mount, so the two controls render
  exactly as before — proven by the "regression: behaves as before" test
  cases in the new test file, not just reasoned about.
- **Does not touch `handleAreaFit`, `fitArea`, `onFollowToggle`, or any
  ride/dispatch/money code path** — this is purely a disabled-attribute +
  explanatory-copy change gated on a new, additive boolean signal. No
  existing state, table, or background loop is read or written differently.
- **`live-map.tsx` / `driver-map.tsx`** are unrelated files with their own
  separate WebGL-guard work (ACTION_ITEMS.md C116-adjacent) — not touched,
  not in scope here.

## 5. User-experience effect

Internal-admin-facing only, Live Monitoring page (`/dashboard/monitoring`).

- **Admin on a browser where the map renders fine (the vast majority):** no
  visible change whatsoever.
- **Admin on a browser whose WebGL context is a non-drawing stub (the same
  rare case the sibling WebGL-guard fix targets):** the service-area jump
  buttons and the Follow toggle are now visibly dimmed/disabled with an
  explanation, instead of looking clickable and silently doing nothing.
  This is a strict improvement — replacing an unexplained no-op with an
  explained, non-interactive state — not a change to any *working* flow.
- **Not visible mid-session to someone already using a working map** — the
  signal is computed once at mount from a value (`webglOk`) that itself
  never changes after mount; there is no code path where a working map's
  controls go from enabled to disabled later in the same session.

**Not feature-flagged.** Gate 3 calls for a flag on user-visible,
non-trivial changes to a shared component used by 3+ pages; both changed
components have exactly one production consumer (§4), and — like the
sibling WebGL-guard fix this closes the gap on — the only behavioral effect
for the overwhelming majority of admins is none at all; a flag defaulting
off would ship the fix inert for the exact admin it's meant to help.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.tsx` | New optional `onCanRenderChange` prop; fires once on mount with `webglOk` | The reactive signal `page.tsx` needs — this component is the only place that knows whether the map can render |
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | New `mapCanRender` state wired from `onCanRenderChange`; jump-pill buttons get `disabled` + `title` + nearby explanatory text when false; `mapCanRender` passed to `MonitoringToolbar` | Closes the actual gap: these controls' whole purpose is a map-visual effect with no other feedback |
| `admin-dashboard/src/app/dashboard/monitoring/toolbar.tsx` | New optional `mapCanRender` prop (default `true`); Follow `Button` gets `disabled` + `title` + adjacent "map unavailable" text when false | Same reasoning, for the Follow toggle |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.render.test.tsx` | 3 new tests: `onCanRenderChange` fires `false`/`true` once on mount, and is safely optional | Direct coverage of the new signal's source |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-toolbar-availability.render.test.tsx` | New file, 3 tests: `MonitoringToolbar`'s Follow button disabled+explained behavior (and regression: unchanged when `mapCanRender` is omitted/true) | Regression coverage for the Follow toggle, split into its own commit/file per CLAUDE.md's ~200-line batch-size rule (see "Findings" below) |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-jump-buttons-availability.render.test.tsx` | New file, 3 tests: the full `MonitoringPage`'s jump buttons disabled+explained behavior via a controllable `MonitoringMap` mock, plus the empty-`jumpableAreas` edge case below | Regression coverage for the jump buttons, matching the `*.render.test.tsx` convention `monitoring-map.render.test.tsx` established |
| `ACTION_ITEMS.md` | C115 marked closed | Tracking |

## 7. Before / after

```tsx
// Before — MonitoringToolbar's Follow button: always enabled, no way to
// know the map beneath it can't render.
<Button
    size="sm"
    variant={followMode ? "default" : "outline"}
    onClick={onFollowToggle}
    className="h-8 gap-1.5 text-xs"
>
    <Navigation className="h-3.5 w-3.5" />
    Follow {followMode ? "ON" : "OFF"}
</Button>
```

```tsx
// After — disabled + explained when the map can't render; byte-identical
// otherwise (mapCanRender defaults to true).
<Button
    size="sm"
    variant={followMode ? "default" : "outline"}
    onClick={onFollowToggle}
    disabled={!mapCanRender}
    title={mapCanRender ? undefined : "Map can't render in this browser, so Follow mode has no visible effect."}
    className="h-8 gap-1.5 text-xs"
>
    <Navigation className="h-3.5 w-3.5" />
    Follow {followMode ? "ON" : "OFF"}
</Button>
{!mapCanRender && (
    <span className="text-[10px] text-muted-foreground" title="...">
        map unavailable
    </span>
)}
```

The jump-pill buttons in `page.tsx` follow the identical
disabled+title+nearby-text pattern; see the file diff for the full snippet.

## 8. Rollback plan

`git-revert-safe` — client-side render logic only, no migration, no
feature flag, no persisted state, no live-data mutation, no config, no
Stripe/wallet/ride-state involvement whatsoever. Reverting restores the
previous (silently-no-op'ing-on-a-broken-map) behavior; every other admin
sees no change either way.

## 9. Verification performed

- [x] `npx tsc --noEmit -p .`: clean, no errors.
- [x] `npx eslint` on every changed/added file (`monitoring-map.tsx`,
      `page.tsx`, `toolbar.tsx`, `monitoring-map.render.test.tsx`,
      `monitoring-toolbar-availability.render.test.tsx`,
      `monitoring-jump-buttons-availability.render.test.tsx`): **0 errors,
      24 warnings.** Compared against the pre-change baseline (`git stash`
      the diff, re-run eslint on the unmodified files, restore) at the
      structured rule-id level, not just a raw count: baseline was 23
      warnings across the same 4 pre-existing files (the new test files
      didn't exist yet). The one new warning is
      `monitoring-map.tsx react-hooks/refs` ("Cannot access refs during
      render") on the new `onCanRenderChangeRef.current = onCanRenderChange;`
      line — this file already carries 3 warnings of that exact same rule
      for the identical, unsuppressed `onSelectDriverRef.current = ...` /
      `onSelectRideRef.current = ...` / `serviceAreasRef.current = ...`
      idiom already in the file (none of the three suppressed), so the new
      line is a 4th instance of an already-accepted local pattern, not a
      new category of problem — left unsuppressed for the same reason the
      other three are. Every other warning in the "after" list is
      identical (by rule + message, not just line number) to the baseline
      list. Zero new warnings in either test file.
- [x] `npx vitest run` on both new test files, the extended
      `monitoring-map.render.test.tsx`, `monitoring-toolbar.test.tsx`, and
      `pages.smoke.test.tsx` together: **53/53 passed, 0 regressions.**
- [x] **A real production build was run: `npm run build` (`next build`),
      not just `tsc --noEmit` or a dev server.** `✓ Compiled successfully
      in 880ms`, exit code 0, no TypeScript errors, all routes generated
      including `/dashboard/monitoring`. Run twice (once truncated to the
      tail for a quick check, once capturing full output) to confirm.
- [x] Blast-radius grep (CLAUDE.md gate 1): every importer of both changed
      components — `MonitoringMap` and `MonitoringToolbar` each have
      exactly one production consumer (`page.tsx`); full list in §4.
- [x] Reviewed against CLAUDE.md's admin-dashboard visual-regression
      disclosure rule (gate 6): **`/dashboard/monitoring` is one of the 6
      pages with an active, CI-wired Playwright visual-regression baseline**
      (`e2e/visual-regression.spec.ts`). This diff changes visible pixels
      only in the already-rare `!mapCanRender` state (the default
      `mapCanRender = true` path — what the seeded baseline actually
      captures — renders byte-identically to before), but any pixel-level
      diff the baseline does catch on this page needs a human to re-run
      `update-visual-baselines.yml`, which this agent cannot trigger — see
      §10.
- [x] Adversarial review (CLAUDE.md gate 10) — **the Agent tool was not
      available in this isolated worktree session, so `spinr-design-
      consistency-reviewer` could not be run directly; fell back to
      `/code-review` at high effort, as instructed for that case.** Result
      explicitly flagged as "single-pass review without the Agent tool (no
      multi-agent fan-out or subagent verify pass ran) — this is not the
      full audit." See "Findings" below for what it caught and how each
      was resolved.

## 10. What was NOT verified

- **Not tested in a real browser against a genuine WebGL-stub context** —
  the render tests exercise the logic via mocked `hasRenderingWebGL()` /
  `MonitoringMap` return values, not an actual ad-blocker-stubbed GPU
  context. Same disclosure the sibling WebGL-guard change-log already
  carries for the underlying probe.
- **No visual/screenshot confirmation of the disabled-state styling
  (dimmed opacity, `cursor-not-allowed`, the small explanatory text's
  layout) at real viewport sizes.** `/dashboard/monitoring` has an active,
  merge-blocking Playwright visual baseline (CLAUDE.md gate 6), but it was
  seeded against the default (`mapCanRender = true`) rendering, which this
  diff leaves pixel-identical — the new `!mapCanRender` branch is not part
  of the seeded comparison at all (that state depends on a WebGL-stub
  browser condition the CI runner doesn't naturally produce), so this
  diff's actual new pixels have no automated visual coverage either way.
  Reasoned about via the render tests' text/attribute assertions and by
  matching the established Tailwind disabled-state idiom already used
  elsewhere in admin-dashboard, not screenshotted.
- **The jump-pill buttons' inline JSX in `page.tsx` was not extracted into
  a separately-unit-testable component** — it's covered instead by
  mounting the full `MonitoringPage` with `MonitoringMap` swapped for a
  controllable mock (see the new test file), which is heavier than a
  small-component test but was chosen over extraction to avoid adding a
  single-use abstraction CLAUDE.md's simplicity-first principle argues
  against for a two-button JSX block.
- **`live-map.tsx` / `driver-map.tsx`** have their own, separate
  WebGL-guard-adjacent tracking (ACTION_ITEMS.md, near C116) — untouched,
  out of scope here.

## Findings from `/code-review` (high effort, Agent tool unavailable)

Three findings, all addressed before commit:

1. **ACTION_ITEMS.md wasn't actually updated yet at review time** — the
   draft change log claimed C115 was marked closed while the file was
   still untouched. **Fixed:** the ACTION_ITEMS.md edit (this diff's §6)
   was completed and verified present before committing; this log's own
   claim is now accurate.
2. **Real bug: the "jump buttons disabled" explanatory text could render
   with zero actual buttons on screen** — the outer wrapper was gated on
   `serviceAreas.length > 0`, but the buttons themselves are filtered
   further (`a.geojson || a.fallbackCenter`); an area with neither would
   leave the filtered list empty while the wrapper (and the new
   `!mapCanRender` text) still rendered, describing buttons that were
   never there. **Fixed:** extracted `jumpableAreas` once, gated both the
   wrapper and the buttons on that same list, so the explanation can never
   appear without at least one actual (disabled) button. Covered by a new
   regression test ("never shows the explanation when there are service
   areas but none are jumpable") in
   `monitoring-jump-buttons-availability.render.test.tsx`.
3. **Batch-size rule: the original single new test file (~224 lines) was
   over CLAUDE.md's ~200-line split threshold for one commit/one logical
   change.** **Fixed:** split into
   `monitoring-toolbar-availability.render.test.tsx` (Follow toggle,
   ~80 lines) and `monitoring-jump-buttons-availability.render.test.tsx`
   (jump buttons, ~165 lines after the edge-case test above), each its own
   commit.

All three re-verified after the fixes: `tsc --noEmit` clean, eslint
unchanged (still exactly the one expected new `react-hooks/refs` warning,
zero in either test file), 53/53 relevant tests passing (up from 25 — the
new edge-case test plus the toolbar suite running standalone), and a third
`npm run build` run (`✓ Compiled successfully in 1387ms`, exit 0).

## 11. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`.
- [x] Blast radius stated, not assumed: one production consumer per
      changed component, confirmed by grep.
- [x] No silent behavior change to a working flow — the default/majority
      path (`mapCanRender = true`) is byte-identical to before; the
      "User-experience effect" field above is filled in for the one case
      that does change (an already-broken, previously-unexplained no-op
      becomes an explained, disabled control).
