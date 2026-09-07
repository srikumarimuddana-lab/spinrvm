# Change Impact & Risk Log — brand splash (rider-app + driver-app)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-07 |
| Author | Nighil (with Claude Code) |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | n/a — presentational launch surface. The one event this adds is a `warning` breadcrumb when the native splash has to be force-hidden. |
| PR / commit link | branch `claude/rider-brand-splash-premium-n7l85n` |
| Related issue or gap ID | none open. Adjacent: `ACTION_ITEMS.md` N18 (light-on-dark logo — untouched, this stays on white), `docs/audit/2026-09-03-engineering-director-teardown-round2.md` L204 (BrandSplash duplicated per app — still duplicated, see §4). |

## 1. Issue / gap identified

The launch experience read as a template app, not a premium one: the native splash was a
deliberately blank white image, so the first ~300–800 ms of every cold start was unbranded;
the JS splash then popped a 260 dp logo (upscaled ~3× from a 384 px PNG) with a spring
bounce, a grey subtitle and an OS `ActivityIndicator`, and finally hard-cut into the login
screen. Raised by the product owner during live app testing.

## 2. Root cause

Not a bug — an unowned surface. `expo-splash-screen` was configured to show
`splash-blank.png` and its JS API was never imported, so nothing controlled the handoff
between the native frame and the React tree. The gate in each `app/_layout.tsx` returned
*either* the splash *or* the app tree, which makes a cross-fade structurally impossible: the
two are different branches, so the splash is unmounted in the same frame the app mounts.

## 3. Fix / remediation

The mark is now the wordmark's own bullseye "o", cut out of the committed 2× logo art. The
native splash shows that mark (plus a soft brand-red halo) rotated half a turn; `BrandSplash`
paints the identical picture as its first JS frame, so the handoff is invisible, then unwinds
the rotation while the mark travels and shrinks until it *is* the "o", and the rest of the
wordmark rises in around it. Tagline and a new "Proudly Canadian" line arrive last. The
spinner is gone — a hairline appears only if boot passes 2.5 s. The splash now renders as an
overlay above the app tree and fades out over the first real route.

Driver-app gets the same entrance with the "Driver" descriptor from its app icon, and its
minimum hold drops from 3000 ms to the shared 1800 ms.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, two surfaces, presentational only.** No backend call, no store, no
  ride state, no money path, no `go_online`/insurance-period code is touched. Grep confirms
  `BrandSplash` has exactly one importer per app (that app's `app/_layout.tsx`), and
  `SPLASH_MIN_DISPLAY_MS` exactly one reader per app.
- **The one structural risk** is the gate rewrite in both `app/_layout.tsx`. The render
  condition is unchanged in meaning: the app subtree renders under `navReady` /
  `appInteractive`, which is the exact inverse of the old early-return condition, so
  `ThemeProvider`, `PersistQueryClientProvider`, `<Stack>` and every hook below them mount at
  precisely the same moment as before. `markInteractive()` still fires on the same boolean.
- **The splash is `pointerEvents="none"`** for its whole life, so the app underneath is
  interactive the instant it mounts — TTI is not regressed by the fade.
- `driver-app`'s `<Stack.Screen name="driver" options={{ animation: 'none' }}>` (which exists
  to stop the home screen's `SOSButton` flashing through a transition) is untouched; the
  cross-fade happens above that screen, not through it.
- **Not shared into `shared/`,** deliberately. `driver-app/jest.config.js` maps every
  `^@shared/(.*)$` to `<rootDir>/__mocks__/@shared/$1`, a 7-file mock directory; a shared
  component would need a new exception in that mapper, which cannot be exercised in the
  authoring environment (see §9). The per-app duplication the round-2 teardown flagged
  therefore remains, now as two near-identical files plus one shared generator. Extraction is
  a follow-up, to be done where the suites can actually be run.
- Dead after this change, left in place rather than deleted: `splash-blank.png` (both apps),
  `splash-wordmark.png`, `splash-mark.png` (both apps), `spinr-logo-dark.png` (driver).
  Nothing references them; removing them is a separate cleanup.

## 5. User-experience effect

Every rider and every driver sees a new launch on **every cold start**. It is never visible
mid-session — the splash only exists before the first route. Nobody mid-ride or online sees
anything change.

Copy: the tagline keeps its existing words ("Ride Local · Support Local") and is restyled as
tracked small caps; **"Proudly Canadian" is new copy** on both splashes. It is a provenance
line, not a claim about the service, and carries no regulatory meaning.

Dark-mode (opt-in) users previously got a white splash that cut to a dark app; they now get
the same white splash fading into it, which is strictly softer.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `scripts/brand/gen_splash_assets.py` | New. Stdlib-only generator: splits the wordmark into letter/mark layers by colour, crops the "o", renders the halo, composites the native frame, extracts the driver descriptor | The layers must come from the real logo art, reproducibly, not be redrawn |
| `rider-app/assets/images/splash/*`, `driver-app/assets/images/splash/*` | New generated PNGs (`mark`, `wordmark-letters`, `glow`, `native-splash`, + `descriptor-driver` for driver) | The animation needs the logo in pieces |
| `rider-app/constants/splash.ts`, `driver-app/constants/splash.ts` | New. Measured geometry + timings shared by the native config and the JS frame | The two must agree exactly or the handoff jumps |
| `rider-app/hooks/useSplashPhase.ts`, `driver-app/hooks/useSplashPhase.ts` | New. `intro → exit → done` | Keeps the exit-timing rule testable in isolation |
| `driver-app/hooks/useAnimatedValue.ts` | New (copy of rider's) | Lets the driver splash drop five `react-hooks/refs` suppressions |
| `rider-app/components/BrandSplash.tsx`, `driver-app/components/BrandSplash.tsx` | Rewritten | The entrance itself |
| `rider-app/app/_layout.tsx`, `driver-app/app/_layout.tsx` | Splash becomes an overlay; `preventAutoHideAsync` at module scope; once-guarded `hideNativeSplash` + 2.5 s watchdog | Enables the cross-fade and makes the native handoff explicit |
| `rider-app/app.config.ts`, `driver-app/app.config.ts` | Native splash points at `native-splash.png`, `imageWidth: 200` | Owns the first frame |
| `*/[__tests__]/BrandSplash.test.tsx`, `useSplashPhase.test.ts` (4 files) | New | Pin the contracts in §9 |
| `docs/android-build-strategy.md` | Corrected a section describing a `hideAsync()` and a 10 s watchdog that never existed | It now describes real code |
| `docs/runbooks/MOBILE_SMOKE.md` | Added launch-frame checks | The new failure modes are visual |
| `rider-app/e2e/smoke.spec.ts` | Comment only — cited "~1.5s splash", already stale before this change | Accuracy |

## 7. Before / after

```tsx
// Before — two branches, so the splash can only ever be swapped away in one frame
if (!fontsLoaded || fontError || !isAuthInitialized || !isLocationInitialized || !minSplashElapsed) {
  return (
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <BrandSplash onLayout={onLoadingLayout} />   // onLoadingLayout was `() => {}`
      </ErrorBoundary>
    </QueryClientProvider>
  );
}
return (<PersistQueryClientProvider …><ThemeProvider><RootLayoutInner … /></ThemeProvider></PersistQueryClientProvider>);
```

```tsx
// After — siblings, so the app mounts underneath and the splash fades off it
return (
  <View style={{ flex: 1 }}>
    {navReady ? (
      <PersistQueryClientProvider …><ThemeProvider><RootLayoutInner … /></ThemeProvider></PersistQueryClientProvider>
    ) : null}
    {splashPhase !== 'done' ? (
      <QueryClientProvider client={queryClient}>
        <ErrorBoundary onError={hideNativeSplashOnError}>
          <BrandSplash
            phase={splashPhase === 'exit' ? 'exit' : 'intro'}
            fontsReady={fontsLoaded}
            onNativeHideReady={hideNativeSplashReady}
            onExitComplete={onSplashExitComplete}
          />
        </ErrorBoundary>
      </QueryClientProvider>
    ) : null}
  </View>
);
```

## 8. Rollback plan

No feature flag is possible: this is the boot frame, and it runs before any `app_settings`
fetch. There is no data path, no migration and no live-data side effect, so there is nothing
to remediate — only pixels.

- **JS side (the choreography, the overlay, the assets): revert by OTA.** All of it lives in
  the JS bundle, `runtimeVersion` is unchanged in both apps, and `expo-splash-screen` was
  already in the shipped binaries. Publishing the previous bundle restores the old splash on
  every installed build with no store release.
- **Native side (the `app.config.ts` first frame): reverting needs a new EAS build,** because
  the image is baked into the iOS storyboard and Android resources at prebuild. Until such a
  build ships, the worst case is the *old* blank white native frame handing off to whatever
  JS bundle is live — which is exactly today's behaviour, so the two halves are safe to
  revert independently and in either order.

## 9. Verification performed

- [x] Asset generator is deterministic — ran twice, byte-identical output (`md5sum` diff clean).
- [x] Layer split proven lossless: all 97,720 ink pixels in the source wordmark land on
      exactly one layer, no pixel outside the source's own ink, no red left in the letters layer.
- [x] Geometry verified by reassembling the lockup from the generated layers using the exact
      constants the components use, with a crosshair on the computed "o" centre — the mark lands
      dead centre.
- [x] Full timeline rendered from the **committed** constants and **committed** assets for both
      apps (headless Chromium, 390×844) and compared against the approved design.
- [x] Static verification of both apps: every constant imported exists and is used, every
      `SPLASH_TIMING` key referenced is defined, every `require`d asset exists on disk, every prop
      passed to `<BrandSplash>` is declared, no `ActivityIndicator`, no stale `onLayout` /
      `onLoadingLayout`, no `splash-blank` reference, no early return reintroduced.
- [x] Blast-radius grep: `BrandSplash`, `useAnimatedValue`, `SPLASH_MIN_DISPLAY_MS`,
      `splash-blank`, `splash-mark`, `splash-wordmark`, plus both colour-guard tests
      (`off-brand-teal-color-fix.test.tsx`, scoped to named files — not these).
- [x] Tests written for both apps: renders the three layers with the right a11y label, no
      spinner, `onNativeHideReady` fires once and only after the mark paints, the exit always
      completes exactly once even if the animation callback is dropped, the slow-boot hairline
      only appears after the threshold, reduce-motion parks the mark, and the type waits for the font.
- [ ] **`yarn lint` / `tsc --noEmit` / `jest` / `expo export` were NOT run locally.** They cannot
      run in this environment: the npm registry is blocked by the egress proxy
      (`registry.npmjs.org` returns 403 through the CONNECT proxy), so `node_modules` cannot be
      installed for either app. **CI is the gate.**
- [ ] **Production build not run** for either app (same reason).

### CI round 1 (run 34145052729, head `2640a8e`) — three real defects, all fixed

Static analysis is not a typechecker, and CI proved it. On `main` (run 6317) `rider-app-test`,
`driver-app-test` and the driver E2E were all green, so all three were this PR's:

1. **`StyleSheet.absoluteFillObject` does not exist on this RN version's `StyleSheet` type**
   (TS2551, both apps). `styles.root` now spells the four edges out. The separate
   `StyleSheet.absoluteFill` used as a value in a style *array* was always fine.
2. **`renderHook` could not infer its `Props`** from a destructured, untyped callback parameter
   under `@testing-library/react-native@13` (driver-app), widening `result.current` to `unknown`
   (TS2345 + two TS18046). The parameter is annotated now, in both apps' copies.
3. **`driver-app/e2e/smoke.spec.ts`'s "authed verified driver lands on /driver dashboard" was
   passing vacuously.** It seeded a session and sampled the URL after a fixed 2.5s; the old 3000ms
   driver splash hold kept `<Stack>` unmounted past that window, so the URL was still `/` and the
   `not.toMatch(/\/login$/)` assertion never observed the routing outcome. Dropping the hold to
   1800ms surfaced the real result, `/login` — correct behaviour, because `fixtures.ts` documents
   that `authStore` keeps web sessions memory-only and `seedAuthedDriverSession`'s localStorage
   writes are never read on web. The test now drives the real login via `loginAsDriver`, the flow
   that doc names as the only working one, already used by `online-toggle`, `payout` and
   `complete-trip` — all of which passed in the same run this one failed in. A broken test was
   repaired, not skipped.

`G4b · yarn audit (JS deps)` was also red on both apps. That is the known permanently-red gate,
not this PR's: the fix already existed on `main` as #5078 (a dependency bump in both apps'
`package.json` + `yarn.lock`), and this branch was cut one commit before it. `origin/main` is
merged in rather than waited on; it no-ops for the gate once the base carries it.

## 10. What was NOT verified

- **No test, typecheck, lint or build was executed locally** — everything verified here is static
  analysis plus browser-rendered simulation of the same maths and the same image files. CI has
  since executed the typecheck (see round 1 above); at the time of writing, the jest suites and
  the E2E run against the fixed code have not yet reported. The components have still never been
  executed by a JavaScript engine in this environment.
- **Nothing was verified on a device or simulator.** In particular: the iOS storyboard frame,
  Android 12+ sizing of the splash icon inside its ~192 dp circular mask (the halo is designed
  to reach zero alpha at its rim so a clip is invisible, but this is reasoned, not observed),
  the real `preventAutoHideAsync → hideAsync` handoff, and native frame pacing. Expo Go and dev
  builds do **not** reproduce the configured native splash — this needs a preview/production
  EAS build on a physical Android 12+ device and an iPhone.
- **Neither rider-app nor driver-app has any visual-regression tooling**, so there is no
  automated check that the splash looks right; the renders in this change are hand-compared.
- The driver-app web export path is unexercised (it has a `playwright.config.ts` but no web
  stubs), so the browser-based check above was only meaningful for rider-app's asset set.
