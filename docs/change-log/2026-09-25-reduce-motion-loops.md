# Change Impact & Risk Log — Reduce Motion for decorative animation loops

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | rider-app / driver-app (via `shared/`) |
| Domain (Sentry tag) | rides / drivers / safety |
| PR / commit link | branch `claude/spinr-animations-admin-ux-x7nl5x`, commits `54ed05f`..`61762d1` |
| Related issue or gap ID | `docs/audit/2026-09-25-ux-motion-admin-website-research.md` M1/M2 (sequencing item 1) |

## 1. Issue / gap identified

Four infinite decorative animation loops ignored the OS Reduce Motion setting:
- rider skeleton pulse
- driver GO-button pulse
- the map car's presence ring (both forks)
- the Safety overlay's live-location dot

Riders and drivers who turned Reduce Motion on still saw constant motion. The gap was found by a code-read audit on 2026-09-25.

## 2. Root cause

There was no shared way to read the setting. Each screen that honoured it (ride-status, driver-arriving, AiWelcomeOrb, BrandSplash) made its own one-shot `AccessibilityInfo.isReduceMotionEnabled()` call. Components written later simply didn't, and nothing in the design-system doc required it.

## 3. Fix / remediation

- **New hook:** `shared/hooks/useReduceMotion.ts` reads the setting and also subscribes to `reduceMotionChanged`, so a mid-session change applies without restarting the app.
- **Four loops gated.** Each one checks the hook. Under Reduce Motion the loop doesn't start, and the value rests at a static state that still conveys meaning:

  | Loop | Resting state |
  |---|---|
  | Skeleton | opacity 0.7 |
  | GO button | scale 1 |
  | Car presence ring | first frame (opacity 0.4, scale 1) |
  | Location dot | fully visible, next to its "LIVE" label |

- **Design-system doc:** new "Motion" section making Reduce Motion mandatory for loops.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, visual only.** No state, network, data, timing or ride-state-machine change. Default behaviour (Reduce Motion off) is unchanged: the hook returns `false` until the OS read resolves, so the first frame is identical to before.

Consumers checked by grep:

| Component | Used by |
|---|---|
| `SkeletonBox` | `rider-app/app/ride-options.tsx`, `rider-app/app/(tabs)/activity.tsx` |
| `DriverIdlePanel` | driver dashboard only |
| `CarMarker` `ring` prop, shared copy | `rider-app/app/ride-in-progress.tsx` (static), `driver-arriving.tsx` and `driver-arrived.tsx` (pulsing) |
| `CarMarker` `ring` prop, driver-app copy | `driver-app/app/driver/(tabs)/index.tsx` (`ownMarkerRing`) |
| `SafetyOverlay` | driver dashboard (and shared Safety components) |

Risks:

- **CarMarker is a registered fork** (`docs/known-forks.md`). Both copies were changed identically in one commit.
  - Android `tracksViewChanges` is deliberately untouched: it stays `true` while `ring.pulsing`, exactly as before.
  - The CarMarker parity test still passes.
- **SafetyOverlay is a safety surface.** Only the dot's opacity loop changed. The hold-to-confirm alert, 911, share-link and close actions, and their timing are untouched.
- **SOS hold pulse deliberately not changed.** It is the only hold-progress feedback and needs a substitute first.
- **Extra renders:** one extra render per mounted consumer, only for users who have Reduce Motion on (when the async read resolves `true`), or when the setting changes.

## 5. User-experience effect

- **Who notices:** only riders and drivers with OS Reduce Motion turned on. For them the four loops become static. Everyone else sees no change.
- **Mid-session:** a user who toggles Reduce Motion while on one of these screens sees the loop stop or start on the next render.
- **Copy:** no copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/hooks/useReduceMotion.ts` | New live Reduce Motion hook | Single shared source; picks up mid-session changes |
| `shared/hooks/__tests__/useReduceMotion.test.ts` | 5 tests | Default, resolved, live change, rejected read, cleanup |
| `rider-app/components/SkeletonBox.tsx` | Gate pulse; static 0.7 opacity | Reduce Motion |
| `rider-app/components/__tests__/SkeletonBox.test.tsx` | 2 tests | The screen tests stub SkeletonBox, so they gave zero coverage |
| `driver-app/components/dashboard/DriverIdlePanel.tsx` | Gate GO pulse | Reduce Motion |
| `driver-app/__tests__/components/DriverIdlePanel.reduceMotion.test.tsx` | 3 tests | Includes the mid-session flip |
| `shared/components/CarMarker.tsx` | Gate ring loop | Reduce Motion (fork A) |
| `driver-app/components/CarMarker.tsx` | Same change | Reduce Motion (fork B) |
| `driver-app/__tests__/components/CarMarker.test.tsx` | Hook mock (default off) + 2 tests | Covers fork B |
| `rider-app/__tests__/carMarkerReduceMotion.test.tsx` | 2 tests | Covers fork A |
| `shared/components/SafetyOverlay.tsx` | Gate location-dot loop | Reduce Motion |
| `driver-app/__tests__/components/SafetyOverlay.test.tsx` | Hook mock (default off) + 2 tests | Covers the change |
| `docs/design/rider-driver-app-design-system.md` | New "Motion" section | Makes the rule discoverable |

## 7. Before / after

```tsx
// Before (shared/components/CarMarker.tsx, same in driver-app copy)
if (!ring?.pulsing) {
    ringPulseAnim.setValue(0);
    return;
}
// ...Animated.loop(...).start()
}, [ring?.pulsing, ringPulseAnim]);
```

```tsx
// After
const reduceMotion = useReduceMotion();
...
if (!ring?.pulsing || reduceMotion) {
    ringPulseAnim.setValue(0);
    return;
}
// ...Animated.loop(...).start()
}, [ring?.pulsing, reduceMotion, ringPulseAnim]);
```

The other three consumers follow the same pattern: early-return to a static value when `reduceMotion` is set, with `reduceMotion` added to the effect's deps.

## 8. Rollback plan

**No feature flag.** This follows the precedent of the existing reduce-motion gating (#4607 finding 5 in `ride-status.tsx` / `driver-arriving.tsx`), which also shipped unflagged. The change:
- only takes effect for users who opted into Reduce Motion at the OS level
- is purely visual
- writes no data

The alternative, an `app_settings` flag, was rejected: it would add remote-config plumbing to four components in order to override an explicit OS accessibility preference.

**Rollback:** revert the commits and ship through the normal mobile release or OTA path. No live data is touched, so no data-level remediation is needed.

## 9. Verification performed

**Tests**
- [x] New tests: 20 across 6 files. Every consumer has both a "Reduce Motion on at mount" test and a "turned on mid-session stops the running loop" test.
- [x] Every "Reduce Motion on" and mid-session test was run against the pre-change code and failed, then passes with the change. So they test the change, not just the mocks.
- [x] The CarMarker mid-session tests use a stateful hook mock, because `CarMarker` is `React.memo`-wrapped. A plain-value mock plus `rerender` with equal props is skipped by `memo` and never reaches the effect. The real hook updates via internal state, which `memo` cannot skip; the stateful mock follows that same path.
- [x] Existing suites re-run and passing:

  | App | Suites |
  |---|---|
  | rider-app | activityScreen, rideOptionsScreen, carMarkerPositionChange, CarMarkerParity, driverArrivingScreen, driverArrivedScreen, rideInProgressScreen |
  | driver-app | DriverIdlePanel (×2), driverDashboardScreen, CarMarker, SafetyOverlay |

**Typecheck and lint**
- [x] `tsc --noEmit` passes in rider-app and driver-app, which both compile the touched `shared/` files.
- [x] ESLint on touched files adds no new warnings or errors. driver-app `CarMarker.tsx` has 2 lint errors on lines this change did not touch, present identically on the old code.
- [x] Standalone `shared/` `tsc` fails with 1,072 errors both before and after this change; the environment can't resolve its dependencies. The shared files are typechecked through the apps instead.

**Review and conventions**
- [x] Blast-radius grep done (consumers listed in §4).
- [x] Fork registry (`docs/known-forks.md`) checked: both CarMarker copies changed in one commit.
- [x] `spinr-accessibility-reviewer` run against the diff (code-read only). Outcome:
  - **Blocker:** none.
  - **Should-fix:** mid-session tests were missing for 3 of 4 consumers. Fixed: now present for all 4.
  - **Should-fix, accepted as a documented tradeoff:** see §10 (WCAG 2.2.2 and the first-frame flash).
  - **Nits left as is:**
    - `AiAuroraBackground.tsx` still carries its own inline copy of the hook logic. Migrating it is DRY-only and outside this PR's surgical scope.
    - `SafetyOverlay`'s Reduce Motion branch returns no cleanup. There is nothing to clean up.
- [x] Feature flag: not used; justification in §8.

## 10. What was NOT verified

- **No device testing.** Nothing was run on a real iOS or Android device or simulator with Reduce Motion enabled; every visual claim is from reading code.
- **No visual regression tooling.** rider-app and driver-app have none, so the resting states (e.g. the CarMarker pulse layer at opacity 0.4 over the static ring) were not screenshotted.
- **iOS vs Android setting.** On Android the OS "Remove animations" setting feeds `isReduceMotionEnabled`. Whether a given Android OEM build fires `reduceMotionChanged` live was not verified. If it doesn't, the change still applies on the next mount.
- **No production build.** No EAS/native build was run. The change adds no native dependency; it uses RN's built-in `AccessibilityInfo`.
- **WCAG 2.1 SC 2.2.2 (Pause, Stop, Hide).** The SC asks for an in-content way to pause auto-moving content that runs longer than 5 s. This change relies on the OS Reduce Motion setting, the accepted mobile-platform substitute, rather than adding an in-app pause control. It is a documented tradeoff, not an assumed pass.
- **First-frame flash.** A user who already has Reduce Motion on may see each pulse start briefly on mount, because the hook returns `false` until the async OS read resolves. The previous one-shot gating had the same tradeoff; this change does not make it worse.
