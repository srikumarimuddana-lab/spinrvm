# Change Impact & Risk Log — heading readout on the car screen (production)

> **REVERSED BEFORE MERGE (2026-08-21).** The on-surface pill was removed at the
> product owner's call: a line of instrumentation on a dashboard is something a
> driver has to decide to ignore, and `271° gps · course-up` reads like a
> warning to someone who has no idea what it means. The plumbing it added —
> `getHeadingSource()` and `formatHeadingReadout()` — is kept and now feeds the
> **dev-only debug panel's** `heading` fact, which already existed but printed
> less. Net effect on a production build: nothing new is drawn. Hardware
> confirmation of the heading fix therefore needs a non-production build, as it
> did before. The entry below describes the reverted state and is left for the
> record.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | srikumarimuddana@gmail.com (Claude Code) |
| Surface(s) | driver-app (Android Auto surface only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | PR #4315, branch `claude/android-auto-earnings-privacy-2nzgpp` |
| Related issue or gap ID | Follow-up to the car-marker heading fix on this branch |

## 1. Issue / gap identified

The wrong-way car marker was reported from photographs, and a photograph cannot
say **why**: a bearing supplied by GPS, one derived from two positions, one
carried from an earlier reading, and none at all are indistinguishable on
screen. The head unit has no console, no red box and no Metro output, and the
on-surface debug panel that prints exactly these facts is compiled out of
production builds (`isCarDebugAvailable()` returns false when
`EXPO_PUBLIC_ENV === 'production'`). So a driver testing a production build had
nothing to send back but the picture — which is how the diagnosis ended up
resting on inference rather than observation.

## 2. Root cause

The debug panel is all-or-nothing and correctly excluded from production (a
40-line rolling log is not driver-facing UI). There was no middle ground: no
single always-available fact that says whether the marker has a real course.

## 3. Fix / remediation

- `carFixChannel` now records how the current bearing was arrived at
  (`lastHeadingSource`) and exposes `getHeadingSource()`, re-exported from
  `useCarLocation` alongside the fix itself.
- `formatHeadingReadout()` (pure, in `carCameraMath.ts`) renders one terse line:
  `271° gps · course-up`, `271° derived · course-up`, `271° held · north-up`, or
  `no course · north-up`.
- `carSurface` draws it as a small muted pill in the existing top-left pill row,
  **in every build including production**, behind a single constant
  `SHOW_HEADING_READOUT`.

`no course` is deliberately distinguishable from a real `0°` bearing: those two
states look identical on the map today and are the crux of the reported bug.

## 4. Risk & impact on existing functionality

Blast radius: **the Android Auto surface's chrome.** Display-only.

- `getHeadingSource()` is a read of a module variable set inside `adoptCarFix`;
  it changes nothing about how a bearing is resolved, stored or published.
- The readout renders inside `pillRow`, which is `pointerEvents="none"` — it
  cannot take a touch, and Android Auto forbids in-surface touch anyway.
- No new timer, subscription, network call or storage write. The pill re-renders
  with the fix updates the surface already re-renders on.
- Nothing else reads `lastHeadingSource`; the debug panel's own `heading` fact
  is unchanged and still production-gated.

## 5. User-experience effect

Driver-facing, visible mid-session on a connected head unit: a small muted line
(11px, `textDim`) appears left of the status pill. It is instrumentation, not a
control — nothing is tappable and nothing about the ride changes.

Deliberate trade: this is **temporary diagnostic chrome shipped to real
drivers**. It is one short line in a corner, and the alternative is another
round of guessing from photos. Flip `SHOW_HEADING_READOUT` to false to remove
it — one constant, no other change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/androidAuto/carFixChannel.ts` | Track + expose `getHeadingSource()` | The source is half the answer |
| `driver-app/lib/androidAuto/useCarLocation.ts` | Re-export it | Surface reads location from here |
| `driver-app/lib/androidAuto/carCameraMath.ts` | `formatHeadingReadout()` | Pure, so the wording is testable |
| `driver-app/lib/androidAuto/carSurface.tsx` | Render the pill; `SHOW_HEADING_READOUT` | Production needs one visible fact |
| `driver-app/lib/androidAuto/__tests__/carCameraMath.test.ts` | 4 cases | Lock the four states apart |

## 7. Before / after

```
Before (production build): status pill + earnings pill. Nothing about heading.
After:  [271° gps · course-up]  [● Trip in progress]  [TODAY ••••]
```

## 8. Rollback plan

`SHOW_HEADING_READOUT = false` removes it with a one-line edit, or revert the
commit. Display-only client code, nothing persisted, no server component. Takes
effect on the next driver-app build either way.

## 9. Verification performed

- [x] driver-app: **73 suites / 675 tests pass**, incl. 4 new `formatHeadingReadout` cases covering gps / derived / held / no-course and the `0°` vs `no course` distinction.
- [x] `tsc --noEmit` clean; eslint clean on the touched files (one pre-existing unused-import warning in `carSurface.tsx`).
- [x] Blast-radius grep — `getHeadingSource`, `lastHeadingSource`, `pillRow`, `formatHeadingReadout`.
- [ ] Manual/hardware check — not done.

## What was NOT verified

- **Not rendered anywhere.** The pill's size, contrast and position beside the
  existing pills are reasoned from the styles, not seen on a head unit or a
  simulator; there is no visual-regression tooling for this surface.
- Whether the line is legible at arm's length at 11px on a real dashboard is
  unknown — if it is not, the fix is to raise the font size, not to remove it.
- The readout reports what the app BELIEVES. If the underlying fix is wrong in
  some way the tests do not model, the readout will confidently report that
  wrong state — it is a diagnostic, not a check.
- No production/`eas` build was run.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] UX field filled in — new driver-visible chrome, and its temporary nature stated
