# Change Impact & Risk Log — driver ride-offer card showed no ride details

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session), for @mkkreddy52 |
| Surface(s) | driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/ride-offer-details-change-3ig29p` (regression introduced by #5324 / `ce88857`) |
| Related issue or gap ID | Driver report + screenshot, 2026-09-14: "the ride offer was different till afternoon, now it should show details" |

## 1. Issue / gap identified

Since #5324 landed (`ce88857`, 2026-09-13 03:28 MDT), the driver ride-offer card renders only its
header (NEW RIDE / rider name / countdown ring) and its Accept/Decline bar. Everything in between —
the `YOUR EARNINGS` hero, the km / min / $-per-km metrics, the "100% yours — $0 commission" badge,
the surge / WAV / quiet / cash / pre-booked badges, incentives, quest hint, and **both the pickup and
drop-off addresses** — is absent. Reported by a driver with a screenshot showing a live offer
(`Accept $5.06`, 12 s left) carrying no trip information at all.

The data is fine: the panel renders placeholders (`--` km, `Pickup location`) when fields are missing,
and none of those placeholders appear either. The content is being rendered and then given zero height.

## 2. Root cause

#5324 wrapped the informational body in `<ScrollView style={{ flex: 1 }}>`. In React Native, `flex: 1`
expands to `flexGrow: 1, flexShrink: 1, flexBasis: 0`. Its parent (`styles.card`) is **auto-height with
only a `maxHeight` cap** — there is no definite height for the flex line to distribute. So Yoga
measures the ScrollView's hypothetical main size as its flexBasis, `0`; the card's height resolves to
timer + header + action bar; free space is `0`; `flexGrow` has nothing to hand back. The body stays at
0 px, and `overflow: hidden` on the card clips it out of sight.

The commit's own comment asserted the opposite ("`flex: 1` … is required here"), and cited
`ActiveRidePanel.tsx` as precedent — but that file caps with `maxHeight` and uses a **plain ScrollView
with no flex property**, which is the idiom that actually works.

Why no gate caught it: jest/react-test-renderer performs no layout, so #5324's regression test
(Accept/Decline present and enabled) passed against a zero-height body. driver-app has no visual or
snapshot regression tooling at all.

## 3. Fix / remediation

Size the body **shrink-only** — `styles.scrollBody = { flexShrink: 1 }` — instead of `flex: 1`:

- Normal offer: `flexBasis: auto` lets the ScrollView lay out at its content height, so the card sizes
  to its content exactly as it did before #5324. All details visible, no scrolling.
- Worst-case stack: the card clamps at `maxHeight` (88% of screen) and the ScrollView is the **only**
  shrinkable child — React Native's `flexShrink` default is `0`, so the timer, header and action bar
  cannot shrink — so it absorbs the overflow and scrolls internally. The action bar stays inside the
  clip. #5324's original goal is preserved.

Alternatives considered (CLAUDE.md pre-merge gate 10):
- *Revert #5324's ScrollView entirely* — rejected: reintroduces the real off-screen-Accept risk it fixed.
- *Give the card `height: cardMaxHeight`* — rejected: every offer card would become 88% of the screen
  tall regardless of content, burying the map and changing the design for all offers.
- *Chosen:* `flexShrink: 1` — smallest diff, restores exact pre-#5324 rendering for ordinary offers,
  keeps the height cap, and matches the in-repo `ActiveRidePanel.tsx` precedent.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated — one component, one consumer.** `RideOfferPanel` is imported only by
  `driver-app/app/driver/(tabs)/index.tsx` (the driver dashboard) and re-exported by
  `driver-app/components/index.ts`. No other importer.
- No sibling fork: `docs/known-forks.md` does not list this file. The Android Auto offer surface
  (`driver-app/lib/androidAuto/CarOfferPanel.tsx`) is an independently-designed horizontal panel with
  no ScrollView and no height cap; it was never touched by #5324 and needs no port.
- Repo-wide sweep for the same collapsing pattern (a flex-sized ScrollView under a `maxHeight`-only
  auto-height parent) found none: the three other flex-sized ScrollViews
  (`driver-app/app/otp.tsx`, `driver-app/app/driver/(tabs)/profile.tsx`,
  `rider-app/app/(tabs)/account.tsx`) all sit under full-screen `flex: 1` roots, where `flex: 1` is correct.
- No backend, dispatch, state-machine, money, or insurance-period code is touched. Accept/Decline
  handlers, the countdown timer and its animation, the offer-timeout path, and the
  `claim_driver_atomic` / Period 2 chain are all untouched — this is a layout-only change.
- Regression risk of the fix itself: if a future offer stack were tall enough to exceed the 88% cap,
  the body scrolls rather than growing — same as #5324 intended. The residual risk is the inverse of
  the bug (a body that grows too tall), which the cap still prevents.

## 5. User-experience effect

- **Driver-facing, and visible mid-session** — any driver currently online sees it on their next offer.
- Restores information a driver needs to price the decision inside a ~15 s countdown: earnings, trip
  distance and duration, $/km, pickup distance and address, drop-off address, and the surge / WAV /
  quiet / cash / pre-booked badges. WAV and quiet-mode badges in particular are accessibility-relevant —
  a driver has been unable to see an accessibility requirement before accepting since yesterday afternoon.
- No copy changes, no new UI. This restores the pre-#5324 appearance; it does not introduce a new one.
- Riders, corporate admins and internal admins see no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/panels/RideOfferPanel.tsx` | Body ScrollView sized by `styles.scrollBody` (`flexShrink: 1`) instead of inline `{ flex: 1 }`; new `scrollBody` style; call-site comment corrected to state why `flex: 1` collapses here | `flex: 1` measures as 0 under an auto-height, `maxHeight`-capped parent |
| `driver-app/__tests__/components/RideOfferPanel.test.tsx` | Two regression tests: the body's flattened style must be shrink-only (`flexShrink: 1`, no `flex`/`flexGrow`/`flexBasis`), and an ordinary offer must render earnings, both metrics and both addresses | jest does no layout, so the collapse is only catchable as a style-shape assertion |

## 7. Before / after

```jsx
// Before (#5324) — flexBasis 0 under an auto-height parent ⇒ body measures 0 px
<ScrollView style={{ flex: 1 }} showsVerticalScrollIndicator={false}>
```

```jsx
// After — flexBasis auto ⇒ lays out at content height; shrinks only when the card hits its cap
<ScrollView style={styles.scrollBody} showsVerticalScrollIndicator={false}>

// styles
scrollBody: {
    flexShrink: 1,
},
```

## 8. Rollback plan

No feature flag, config value, or migration is involved — this is a pure layout change in one
component, with no persisted state and no live-data side effects, so `git revert` **is** a complete
rollback here (the CLAUDE.md carve-out for genuinely isolated, low-risk changes). Reverting restores
the current zero-height body; it cannot corrupt or strand anything.

Delivery caveat: like any driver-app change, this reaches drivers via an EAS update/build, not a
backend deploy — so both the fix and any rollback are gated on that channel's release cadence
(`driver-app/eas.json`; mobile builds trigger only on a `[build]` commit marker).

## 9. Verification performed

- [x] Blast-radius grep performed — searched for: every importer of `RideOfferPanel`; every
      `<ScrollView>` with a `flex`/`flexGrow` style across driver-app, rider-app, admin-dashboard and
      shared; every file combining `maxHeight` with a `ScrollView` in driver-app/rider-app;
      `docs/known-forks.md` for a registered sibling; `CarOfferPanel.tsx` for the Android Auto surface.
- [x] Reviewed against CLAUDE.md conventions — no ride-state, money, insurance-period, PIPEDA or
      observability surface is touched; the ride state machine and the Accept/Decline handlers are
      byte-identical.
- [x] Syntax-checked both changed files with a standalone `tsc` parse (zero `TS1xxx` syntax errors;
      the remaining diagnostics are all missing-module/missing-global errors caused by the absent
      `node_modules`, not by the diff).
- [x] Reasoned against the in-repo precedent (`ActiveRidePanel.tsx`: `maxHeight` cap + plain
      ScrollView) and the Yoga flexBasis/flexGrow semantics that produce the collapse.
- [x] Reviewed with `spinr-design-consistency-reviewer` against the actual diff (pre-merge gate 10).
- [ ] Not feature-flagged — justification: this is a revert-shaped restoration of the pre-#5324
      appearance on a single component, and flagging it would leave drivers on the broken variant
      for longer than shipping it does.

**Not run, and why:**
- **No test suite was executed.** `driver-app/node_modules` is absent in this environment and
  `npm install` fails with `403 Forbidden` from the egress policy for `registry.npmjs.org` (confirmed
  both through and around the agent proxy; the local npm cache is empty, so `--offline` also fails).
  So jest — including the two regression tests added here — and a project-wide `tsc --noEmit`
  **have not been run**. They must be run by CI or by a human with a working install before merge.
- **No production build was run.** CLAUDE.md asks for a real production build on any driver-app
  change; an EAS build is not possible here for the same reason. Not run.
- **No device, simulator, or staging check.** The fix's actual on-screen effect is reasoned about from
  Yoga's layout rules, not observed. This is the same class of gap that let #5324 ship.
- **No visual regression coverage exists for this surface at all** — driver-app has no visual or
  snapshot tooling (CLAUDE.md gate 6), so the "reasoned about, not screenshotted" disclosure applies
  in full. **This change should be eyeballed on a real offer before it is considered confirmed.**

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
