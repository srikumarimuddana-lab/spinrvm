# Change Impact & Risk Log — SDK 57 dependency alignment (rider-app + driver-app)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (session on `claude/sdk-55-57-upgrade-jrz0iv`) |
| Surface(s) | rider-app, driver-app (build pipeline + client dependency layer; one driver runtime fix) |
| Domain (Sentry tag) | rides (app-wide client deps); the held Stripe bump would be payments |
| PR / commit link | branch `claude/sdk-55-57-upgrade-jrz0iv` |
| Related issue or gap ID | Completes the SDK 55→57 upgrade (Dependabot #605 + follow-ups #607/#609); supersedes the RNGH metro hotfix (`2026-08-11-metro-rngh-renderer-shim.md`) |

## 1. Issue / gap identified

Both apps were upgraded to the SDK 57 core (expo ~57.0.9 / RN 0.86.2 / React 19.2.3)
but the upgrade was left ~90% done: five libraries sat below SDK 57's expected
versions (`bundledNativeModules.json`, expo/expo sdk-57), a delivery-blocking Metro
workaround masked one of them, driver app-start metrics were silently OFF due to a
renamed expo-observe export, and comments/docs still described the SDK 55 world.

## 2. Root cause

Partial upgrade: the core bump auto-updated `expo-*` packages, but packages listed in
`expo.install.exclude` and pinned in yarn `resolutions` are deliberately fenced off
from `expo install` — and nobody did the manual alignment pass those fences exist to
force. The expo-observe breakage was invisible because the guarded require fails soft
(metrics off, no error). Docs staleness: same reason — the SDK 57 bump was a
Dependabot event, not an audited migration.

## 3. Fix / remediation

Per-package result vs the authoritative SDK 57 manifest:

| Package | Before | After | Note |
|---|---|---|---|
| react-native-gesture-handler | ~2.31.1 | **~2.32.0** (both apps) | SDK 57's version; drops the import of the RN-0.86-removed renderer shim |
| Metro renderer-shim redirect | active (both metro.config.js) | **removed** | RNGH 2.32 makes it dead; was the d4b573c hotfix |
| react-native-safe-area-context | ~5.6.2 | **~5.7.0** (both apps) | SDK 57's version |
| @react-native-community/netinfo | 11.5.2 (deps + resolutions) | **12.0.1** (both apps, both places) | SDK 57's version; consumers use only stable `addEventListener`/`fetch` |
| @react-native-community/datetimepicker | 8.6.0 | **9.1.0** (driver) / **removed** (rider) | Rider had ZERO imports (verified repo-wide grep) — removed together with equally-unused react-native-modal-datetime-picker ^18.0.0 |
| eslint-config-expo | ~10.0.0 (rider) | **~57.0.1** | Driver already ~57.0.0; also removed from rider's expo.install.exclude |
| expo-observe API in driver `_layout.tsx` | `_observe.AppMetricsRoot` (null on 57) | **`ObserveRoot ?? AppMetricsRoot`** | Behavior fix: restores app-start metrics; fail-soft unchanged |
| @stripe/stripe-react-native | 0.63.0 | **HELD at 0.63.0** | User decision 2026-08-11: payments surface mid-live-testing + Kotlin-toolchain entanglement (Option C); bump criteria: EAS Android+iOS build + spinr-money-auditor review + payment smoke. SDK 57 expects 0.64.0 |
| react-native-worklets | ^0.11.3 | **unchanged (ahead)** | Deliberately ahead of bundled 0.10.1 (reanimated 4.5.3 pairing); in expo.install.exclude |
| expo-speech-recognition | ^56.0.1 | **unchanged** | No 57.x exists on npm (verified: latest is 56.0.1). Community package; re-check each SDK cycle |

Confirmed already-correct (no change): react-native-web ~0.21.0 (SDK 57 bundles exactly
this), maps 1.27.2, webview 13.16.1, svg 15.15.4, screens ~4.26.2, reanimated ^4.5.3,
async-storage 2.2.0, @sentry/react-native ^7 (→7.11+), all expo-* ~57.x, both
`+0.86.2` patches (still required for New-Arch codegen breakage).

Stale-artifact cleanup: rider tsconfig duplicate path keys deduped; rider/driver
app.config.ts stale SDK 55 comments rewritten (and driver's blanket `as any` on the
ios block narrowed to one targeted `@ts-expect-error` — everything else in the block
typechecks on SDK 57; the `@ts-expect-error` on `newArchEnabled` was removed as SDK 57
types it); metro comments retagged with a packageExports re-test recipe;
`docs/android-build-strategy.md` retitled to SDK 57 with real patch filenames
(`expo-modules-core+55.0.25.patch` marked retired); `docs/dependency-upgrade-runbook.md`
gained the SDK 57 matrix + history rows. `expo-env.d.ts` absence was investigated and
is by design (gitignored, generated per checkout).

runtimeVersion ship-gate: rider `2.0.0 → 2.1.0`, driver `2.5.0 → 2.6.0` — every
native-module bump above makes new JS bundles OTA-incompatible with binaries built
before this branch; the literal-string runtimeVersion is this repo's documented fence.

## 4. Risk & impact on existing functionality

- Blast radius: **cross-surface (both mobile apps), client dependency layer only.** No
  backend, admin, API, DB, background-loop, ride-state-machine, or money-path
  interaction.
- Per-package consumers (grepped, not assumed):
  - RNGH: `GestureHandlerRootView` wraps both apps' `_layout.tsx`; `@gorhom/bottom-sheet`
    (rider); `shared/hooks/useHoldToConfirm.ts` (hold-to-confirm on SOS/ride actions —
    safety-adjacent surface). Regression mode would be app-wide gesture breakage — loud,
    immediate in any smoke test, not subtle.
  - safe-area-context: every screen (expo-router/react-navigation dependency).
  - netinfo: `shared/components/OfflineBanner.tsx` (both apps), rider `_layout.tsx`
    connectivity gating, `driver-app/hooks/useDriverDashboard.ts` (driver online flow —
    dispatch-adjacent).
  - datetimepicker: driver `become-driver.tsx` only (onboarding document expiry).
  - expo-observe: metrics-only; fail-soft guard unchanged.
- Metro redirect removal: build-time module resolution only; verified by production
  exports of both apps (the exact failure mode it was added for).
- Lockfile side effects: rider's eslint upgrade tripped yarn 1's duplicate-eslint link
  invariant; fixed by deduping eslint to a single 9.39.5 in yarn.lock. The rebuild also
  re-resolved floating transitive ranges (babel/hermes/zod — all in-range, all
  verification-green) and — a strict improvement — split the semver-impossible
  `brace-expansion` lock entry that ACTION_ITEMS C19 identified (`^2.x/^5.x` ranges
  previously mapped to 1.1.18; now correctly 1.1.18 / 2.1.4 / 5.0.9 in BOTH apps),
  further hardening the `eas update` fingerprint path.

## 5. User-experience effect

- No intended visible change for riders/drivers. Native-module bumps can shift
  gesture/inset/connectivity edge behavior; nothing observable until a **new binary**
  ships — none of this is OTA-deliverable to current live-tester builds, and the
  runtimeVersion bumps make that fence explicit.
- Driver metrics fix is telemetry-only (EAS Observe app-start metrics resume).
- No copy/notification changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| rider-app/package.json (+yarn.lock) | RNGH 2.32, safe-area 5.7, netinfo 12.0.1 (deps+resolutions), eslint-config-expo 57.0.1 (+exclude removal), datetimepicker×2 removed | SDK 57 alignment |
| driver-app/package.json (+yarn.lock) | RNGH 2.32, safe-area 5.7, netinfo 12.0.1 (deps+resolutions), datetimepicker 9.1.0 | SDK 57 alignment |
| rider-app/metro.config.js, driver-app/metro.config.js | Renderer-shim redirect removed; stale comments refreshed + packageExports re-test recipe | Redirect dead after RNGH 2.32 |
| rider-app/tsconfig.json | 4 duplicate path keys deduped | Behavior-neutral hygiene |
| rider-app/app.config.ts | Stale SDK 55 comments rewritten (values untouched) | Accuracy |
| driver-app/app.config.ts | `@ts-expect-error` on newArchEnabled removed; ios `as any` narrowed to targeted suppression; stale comments rewritten | SDK 57 types allow real checking |
| driver-app/app/_layout.tsx | ObserveRoot fallback in guarded expo-observe require | Restore metrics under renamed 57 API |
| rider-app+driver-app app.config.ts | runtimeVersion 2.0.0→2.1.0 / 2.5.0→2.6.0 | OTA fence for the native bumps |
| docs/android-build-strategy.md, docs/dependency-upgrade-runbook.md | Retitled/retabled to SDK 57 reality | Docs cited ghost patches and "55 (current)" |
| ACTION_ITEMS.md | EAS-gated follow-ups + mobile-lint-debt item added | Standing gaps tracked, not silently dropped |

## 7. Before / after

The one behavior-changing diff a bundler consumes (both metro.config.js):

```js
# Before
if (moduleName === 'react-native/Libraries/Renderer/shims/ReactNative') {
  return context.resolveRequest(
    context, 'react-native/Libraries/Renderer/shims/ReactFabric', platform);
}
```

```js
# After
(removed — react-native-gesture-handler 2.32.0 no longer imports the missing
shim; verified by grep of its published lib/ output and by production exports
of both apps)
```

Driver metrics restoration (`driver-app/app/_layout.tsx`):

```ts
# Before — silently null on expo-observe ~57 (export renamed)
ObserveMetricsRoot = _observe.AppMetricsRoot ?? null;
```

```ts
# After
ObserveMetricsRoot = _observe.ObserveRoot ?? _observe.AppMetricsRoot ?? null;
```

## 8. Rollback plan

- Nothing here touches live data; nothing ships until a new EAS binary is cut. The
  unshipped-binary state is itself the rollback boundary.
- Per-commit `git revert` + `yarn install` is a complete rollback for any individual
  bump (each is an isolated 2-file commit). The metro-redirect removal commit is
  independently revertible: re-adding the redirect restores bundling even if RNGH 2.32
  stays.
- If a binary built from this branch misbehaves in the field: `eas update:republish`
  the previous update group / previous build — standard store-less rollback; the
  runtimeVersion bumps guarantee old binaries never receive new-JS OTAs.

## 9. Verification performed

- [x] Baseline captured BEFORE any change (tsc, jest, production export, evaluated
  prebuild config — both apps) and re-run green after every commit touched an app.
- [x] Automated tests: rider 55 suites / 461 tests, driver 53 suites / 379 tests —
  pass at baseline and at final sweep.
- [x] `npx tsc --noEmit` clean in both apps at final sweep (driver additionally
  gained real type coverage of its ios config block).
- [x] Production JS bundles: `CI=1 npx expo export --platform android` exit 0 for both
  apps at baseline, after the redirect removal, and at final sweep. **This is a Metro
  production bundle — no native Android/iOS compile was run in this environment.**
- [x] Evaluated prebuild config (`expo config --type prebuild`) byte-identical to
  baseline for both apps' comment-only app.config edits.
- [x] Blast-radius greps for every bumped package (consumers listed in §4); repo-wide
  import grep proving rider's datetimepicker packages unused; full-tree grep proving no
  other package imports the removed renderer shim.
- [x] yarn.lock diff audited vs main: driver scoped exactly to the 4 intended packages
  (+brace-expansion split); rider additionally carries the eslint-ecosystem tree and
  in-range transitive refreshes from the dedupe rebuild (enumerated in §4).
- [x] Version targets validated against expo/expo sdk-57 `bundledNativeModules.json`
  (fetched from the sdk-57 branch) — the authoritative source `expo install --check`
  reads.
- [ ] `expo install --check` / `expo-doctor` could NOT run: this environment's egress
  proxy denies `api.expo.dev` (CONNECT 403, verified in proxy status). The manifest
  comparison above substitutes. CI's `mobile-dep-check.yml` will run the real thing.
- [x] Reviewed against CLAUDE.md conventions: no state-machine/money/RLS surface
  touched; pre-merge gates 1 (blast radius), 2 (additive-over-destructive: holds
  chosen over risky bumps), 7 (rollback stated), 9 (escalated: Stripe/major-bumps/EAS
  decisions were put to the user, 2026-08-11).

## 10. What was NOT verified

- **No EAS/native build**: Kotlin/Gradle/pod outcomes of the native-module bumps are
  unproven (gesture-handler, safe-area, netinfo, datetimepicker all compile native
  code). The C17 lesson is acknowledged: a green `expo export` is NOT a green
  `eas build`/`eas update`. First EAS Android + iOS build off this branch is the real
  gate — commit messages deliberately avoid the EAS trigger tag (mobile builds
  auto-trigger only on tagged commit messages).
- **No on-device smoke**: gesture surfaces (bottom sheets, map pan, hold-to-confirm
  SOS), safe-area insets on notched devices, offline-banner behavior, driver
  onboarding date picker, and the restored Observe metrics all need a device pass on
  the next dev build.
- **No release-build Hermes check**: the Sentry packageExports workaround stays in
  place untouched (its crash was release-only); flipping it remains a separately-gated
  follow-up.
- **No visual regression tooling exists** for these apps (standing gap, already noted
  in ACTION_ITEMS) — "no visible diff" for the comment/typing commits is reasoned, not
  screenshotted.
- Driver jest suite does not exercise the `_layout.tsx` observe branch (guarded
  require is test-mocked); the ObserveRoot fix is verified against the installed
  package's type declarations, not a booted app.
- Environment note: this container's yarn cache had a corrupted `firebase` entry
  (missing package.json files) that broke driver tsc/jest at baseline; purged and
  reinstalled from registry — worth knowing if CI images show the same symptom, but
  not a repo defect (no repo change involved).
