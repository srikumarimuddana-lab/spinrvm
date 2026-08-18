# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code (session) |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | rides (safety-hub, ride-options), payments (payment-confirm), corporate (work-profile — untouched), drivers (appeal, legal, dashboard heatmap) |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md C20 (mobile lint debt) |

## 1. Issue / gap identified

Picked up C20's "mobile lint debt" item, whose own text (rounds 1–4,
2026-08-12) already closed nearly the entire original backlog for both
apps: `no-restricted-syntax` to 0/0, `set-state-in-effect` to
near-0/0, `exhaustive-deps` to 1(deliberately deferred)/0. Ran a fresh
`eslint --no-cache` on both apps anyway (per this task's own instruction
not to trust a stale count) rather than assuming the document's rounds
were still current.

**Result confirmed the document is accurate for everything it covers —
the small number of live findings today are NOT leftover backlog, they're
new findings from code written after 2026-08-12** (verified via `git log`
on each flagged file):

- `rider-app/app/safety-hub.tsx` — new screen, added 2026-08-17 (F2 Safety
  Hub) — 5 `react-hooks/static-components` findings (a rule C20's rounds
  never encountered, since this file didn't exist yet).
- `driver-app/components/dashboard/HeatmapCells.tsx` — new file, added
  2026-08-13 (HM-03/04/05 heatmap rendering) — 1 `react/display-name`.
- `driver-app/app/legal.tsx` — modified 2026-08-17 (#4042, audience-split
  legal-documents endpoint) — 1 `exhaustive-deps`, on a new closure
  introduced by that change.
- `driver-app/app/appeal.tsx` — new screen, added 2026-08-17 (#4050,
  CRC/VSC deactivation appeals) — 2 `react/no-unescaped-entities`.
- `rider-app/app/payment-confirm.tsx`, `rider-app/app/ride-options.tsx` —
  both modified 2026-08-16/17 by unrelated payment-flow fixes — 1 stale
  `eslint-disable-next-line react-hooks/set-state-in-effect` each, left
  over from a code shape the intervening edits changed.
- `rider-app/app/work-profile.tsx` — the one pre-existing, already-
  documented exception: 2 findings a prior round (2026-08-12,
  `docs/change-log/2026-08-12-c20-lint-tier3-rider-app.md` /
  `-tier4-rider-app.md`) investigated in depth and deliberately left
  unfixed pending a human decision about a genuine redundant-fetch race —
  re-verified that reasoning still applies and left untouched.

## 2. Root cause

New code, not drift: every fixed file was authored or touched after the
2026-08-12 rounds closed C20's original backlog. This is the expected,
healthy pattern — lint debt in shipping code accrues faster than any one
cleanup round removes it — not a sign the earlier rounds' claims were
wrong. (My own first read of this task under-scoped how much of the
document to check before concluding it was stale; corrected before
writing this log — see §9.)

## 3. Fix / remediation

- **`rider-app/app/safety-hub.tsx`** (`react-hooks/static-components` ×5):
  `Row` was declared inside `SafetyHubScreen`'s render body, so a new `Row`
  component was created every render — the anti-pattern this rule
  detects, on the panic-adjacent Safety Hub screen. Hoisted `Row` to
  module scope, passing the two values it previously closed over
  (`styles`, `colors.textDim`) as explicit props. Same render output at
  every one of the 5 call sites, zero behavior change.
- **`rider-app/app/payment-confirm.tsx`, `rider-app/app/ride-options.tsx`**
  (`react-hooks/set-state-in-effect`, "unused eslint-disable directive"
  ×1 each): removed both stale suppression comments — re-ran
  `eslint --no-cache` on each file to confirm the rule genuinely doesn't
  fire there anymore before removing.
- **`rider-app/app/work-profile.tsx`** (`exhaustive-deps` ×1,
  `set-state-in-effect` ×1): **left as-is, not fixed.** Confirmed the
  prior round's documented reasoning (two effects racing on the same
  fetch, needs a human decision on which should own it) still applies to
  today's code before leaving it untouched.
- **`driver-app/app/appeal.tsx`** (`react/no-unescaped-entities` ×2):
  escaped two raw apostrophes to `&apos;`, matching this app's own
  existing convention (`login.tsx`, `profile-setup.tsx`,
  `vehicle-info.tsx`). Text-only change.
- **`driver-app/components/dashboard/HeatmapCells.tsx`**
  (`react/display-name` ×1): added
  `HeatmapCells.displayName = 'HeatmapCells'` to the anonymous
  `React.memo(...)`-wrapped component — standard fix, no behavior change.
- **`driver-app/app/legal.tsx`** (`exhaustive-deps` ×1): `fetchLegalContent`
  was a plain closure redeclared every render; adding it to the effect's
  deps naively would have made the effect re-run every render. Everything
  it closes over besides `singleDocType` (`SpinrConfig`,
  `legalDocFallbackText`, the `set*` setters) is stable, so wrapped it in
  `useCallback(fn, [singleDocType])` and added it to the effect's deps —
  closes the finding honestly; the effect's actual re-run condition is
  unchanged (`fetchLegalContent`'s identity only changes when
  `singleDocType` does).

## 4. Risk & impact on existing functionality

- **`safety-hub.tsx`**: blast radius is this one screen; `Row` was
  module-private (not exported), grepped for other importers — none. All
  5 call sites pass identical props plus the two newly-threaded ones.
- **`payment-confirm.tsx` / `ride-options.tsx`**: `eslint-disable` comments
  are compile-time-inert — removing one cannot change runtime behavior.
  Re-lint confirms the rule doesn't fire, so nothing was masked.
- **`legal.tsx`**: `fetchLegalContent` is not exported/imported elsewhere
  (grepped `driver-app/`). The `useCallback` wrap doesn't change when the
  effect fires, so the route-param-switch refetch behavior is unchanged.
- **`HeatmapCells.tsx`**: `displayName` is DevTools/error-boundary/lint
  metadata only, never read by application logic. Only render site is
  `driver-app/app/driver/(tabs)/index.tsx` — unaffected.
- **`appeal.tsx`**: text-only entity escaping; rendered string is
  character-for-character identical.
- No ride state machine, dispatch, payment, or corporate billing logic
  touched in any of these six files.

## 5. User-experience effect

None of these are user-visible changes — every fix is either a pure
render-timing correctness fix with identical output (safety-hub,
legal.tsx), dead-code/metadata cleanup (payment-confirm, ride-options,
HeatmapCells), or a text-encoding fix rendering identically (appeal.tsx).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/safety-hub.tsx` | Hoisted `Row` component out of render body to module scope, threaded `styles`/`chevronColor` as props | Fix `react-hooks/static-components` |
| `rider-app/app/payment-confirm.tsx` | Removed stale `eslint-disable-next-line react-hooks/set-state-in-effect` | Rule no longer fires there after later edits |
| `rider-app/app/ride-options.tsx` | Removed stale `eslint-disable-next-line react-hooks/set-state-in-effect` | Same |
| `driver-app/app/appeal.tsx` | Escaped 2 raw apostrophes to `&apos;` | Fix `react/no-unescaped-entities` |
| `driver-app/components/dashboard/HeatmapCells.tsx` | Added `HeatmapCells.displayName = 'HeatmapCells'` | Fix `react/display-name` |
| `driver-app/app/legal.tsx` | Wrapped `fetchLegalContent` in `useCallback([singleDocType])`, added it to the fetch effect's deps | Fix `exhaustive-deps` without a per-render re-fetch loop |

**Deliberately left untouched**: `rider-app/app/work-profile.tsx` (2
pre-existing findings, already investigated by a prior round, needs a
human product decision).

## 7. Before / after

```tsx
// Before (rider-app/app/safety-hub.tsx) — redeclared every render
export default function SafetyHubScreen() {
  ...
  const Row = ({ icon, color, title, subtitle, onPress }: {...}) => (
    <Pressable style={styles.row} onPress={onPress} ...>
      <Ionicons name="chevron-forward" size={16} color={colors.textDim} />
    </Pressable>
  );
  return (...);
}
```

```tsx
// After — hoisted, deps passed explicitly
function Row({ icon, color, title, subtitle, onPress, styles, chevronColor }: {...}) {
  return (
    <Pressable style={styles.row} onPress={onPress} ...>
      <Ionicons name="chevron-forward" size={16} color={chevronColor} />
    </Pressable>
  );
}
export default function SafetyHubScreen() {
  ...
  return (<Row ... styles={styles} chevronColor={colors.textDim} />);
}
```

```tsx
// Before (driver-app/app/legal.tsx) — new identity every render
const fetchLegalContent = async () => { ... };
useEffect(() => {
  // eslint-disable-next-line react-hooks/set-state-in-effect
  fetchLegalContent();
}, [singleDocType]);
```

```tsx
// After — stable identity across renders unless singleDocType changes
const fetchLegalContent = useCallback(async () => { ... }, [singleDocType]);
useEffect(() => {
  // eslint-disable-next-line react-hooks/set-state-in-effect
  fetchLegalContent();
}, [singleDocType, fetchLegalContent]);
```

## 8. Rollback plan

`git revert` is sufficient and safe for every file — all six changes are
additive/refactor-only with zero stored-data or state-machine interaction.
No feature flag needed; these are lint-debt fixes, not behavior changes.

## 9. Verification performed

- [x] `eslint --no-cache` on both apps' `app/` + `components/`: rider-app
  9 → 2 (the two pre-existing, deliberately-deferred work-profile.tsx
  findings); driver-app 4 → 0
- [x] `tsc --noEmit` on both apps: clean (exit 0)
- [x] Real production build (`yarn build:web`, i.e. `expo export --platform web`)
  run on **both** apps, not just a dev server or `tsc` check — both
  completed successfully (rider-app 55.6s, driver-app 50.9s)
- [x] Targeted `jest` runs on the only pre-existing test files touching the
  changed screens/components: `ride-options-payment-sheet.test.tsx` (7
  passed), `heatmapCellGeometry.test.ts` / `demandHeatmapShared.test.ts` /
  `useDemandHeatmap.test.ts` (42 passed) — no existing test file covers
  `safety-hub.tsx`, `payment-confirm.tsx`'s touched block, `appeal.tsx`,
  or `legal.tsx` directly
- [x] Grepped for every other consumer of each touched symbol (`Row`,
  `fetchLegalContent`, `HeatmapCells`) — confirmed isolated / no other
  callers, per §4
- [x] `git log` on every flagged file, confirming each is new/modified
  after the 2026-08-12 C20 rounds closed the original backlog — this
  pass is closing fresh debt, not re-litigating already-closed work

## 10. What was NOT verified

- **No new tests added.** Every fix is either a pure refactor with
  identical output or dead-comment removal — none introduces a new logic
  branch needing new coverage.
- **Not run on a real device/simulator.** `expo export --platform web`
  proves the web bundle builds; it doesn't exercise the native
  `react-native-maps` `Polygon` path in `HeatmapCells.tsx` or native
  `Pressable`/`ScrollView` behavior in `safety-hub.tsx`. No
  device/simulator harness exists in this environment.
- **No visual/snapshot regression tooling exists for RN screens** in this
  repo — "identical render output" claims above were verified by reading
  the JSX diff, not a screenshot comparison. Flagging per the standing gap
  rather than re-discovering it.
