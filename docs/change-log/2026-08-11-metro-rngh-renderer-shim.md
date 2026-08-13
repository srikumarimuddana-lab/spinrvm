# Change Impact & Risk Log — Native JS bundling broken at HEAD (gesture-handler × RN 0.86 renderer shim)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (session on `claude/wallet-payment-vehicle-selection-70tjn0`) |
| Surface(s) | rider-app, driver-app (build pipeline only — no runtime code shipped by this diff itself) |
| Domain (Sentry tag) | rides (delivery-blocking for all mobile fixes) |
| PR / commit link | branch `claude/wallet-payment-vehicle-selection-70tjn0` |
| Related issue or gap ID | Discovered while verifying the payment-sheet Done-button fix (same date/log) |

## 1. Issue / gap identified

`npx expo export --platform android` fails at HEAD for **rider-app** (and by
inspection driver-app has the identical dependency set and import): Metro
cannot resolve `react-native/Libraries/Renderer/shims/ReactNative`, imported
unconditionally by `react-native-gesture-handler/src/RNRenderer.ts`. Every
native JS bundle — `expo export`, `eas build`, `eas update` — dies on this.
**Nothing (including hotfixes) can ship to phones from HEAD until it is
fixed.**

## 2. Root cause

- react-native 0.86 stopped shipping the old-architecture renderer shim
  `Libraries/Renderer/shims/ReactNative.js`. Verified against the **published
  0.86.2 registry tarball** (not just local node_modules): `shims/` contains
  only `ReactFabric.js`, `ReactFeatureFlags.js`, `ReactNativeTypes.js`,
  `ReactNativeViewConfigRegistry.js`, `createReactNativeComponentClass.js`.
- react-native-gesture-handler 2.31.x (`~2.31.1` pinned in both apps; 2.31.2
  installed per yarn.lock) still does
  `export { default as RNRenderer } from 'react-native/Libraries/Renderer/shims/ReactNative'`
  — byte-identical in 2.31.1 and 2.31.2, so no version within the pinned
  range avoids it.
- Not covered by any existing workaround: the repo's
  `patches/react-native+0.86.2.patch` does not touch `Renderer/shims`, the
  metro `resolveRequest` had no rule for it, and RN's `exports` map has only
  identity wildcards (and package-exports resolution is deliberately disabled
  in both metro configs for the Sentry/Hermes CJS issue).
- When it broke: not bisectable from this clone (truncated history, 179
  commits; file histories bottom out at the graft boundary). Most plausibly
  it has been broken since the RN 0.86 upgrade landed and simply not noticed
  because no EAS build/OTA has been cut from HEAD since. The installed
  live-tester builds predate it.

## 3. Fix / remediation

Added a targeted Metro `resolveRequest` redirect in **both**
`rider-app/metro.config.js` and `driver-app/metro.config.js`, following the
same file's established stub pattern: requests for
`react-native/Libraries/Renderer/shims/ReactNative` resolve to
`react-native/Libraries/Renderer/shims/ReactFabric` instead. Both apps run
the New Architecture (rider `runtimeVersion: '2.0.0'` was bumped for exactly
that), where Fabric **is** the live renderer and `ReactFabric.js` exposes the
same `findHostInstance_DEPRECATED` surface RNGH reads off `RNRenderer`. This
is the resolution upstream RNGH itself moved to for Fabric.

The durable fix is bumping `react-native-gesture-handler` to a release with
native RN 0.86 support and then deleting this redirect — noted for
`ACTION_ITEMS.md`. Per the repo's own pre-merge gate ("verify a newer
version actually works before pinning it"), that bump was **not** done
blind in this hotfix.

## 4. Risk & impact on existing functionality

- Blast radius: **build-time module resolution only**, both mobile apps. The
  redirect matches one exact module specifier; every other request falls
  through unchanged. No backend, admin, API, or data interaction.
- What consumes the redirected module: only
  `react-native-gesture-handler/src/RNRenderer.ts` (repo-wide grep — no other
  importer of `Renderer/shims/ReactNative`).
- What RNGH uses it for: `findHostInstance_DEPRECATED` when resolving host
  components for gesture attachment. On a New-Arch runtime the Fabric shim is
  the correct provider of that API; on an old-arch runtime this redirect
  would be wrong — but these apps are New-Arch-only builds
  (old-arch installs are fenced off by the `runtimeVersion` bump, per the
  comment in `rider-app/app.config.ts`).
- Regression risk concentrates in gesture handling (RNGH is the root wrapper
  of both apps): if the Fabric shim were an incompatible substitute the
  failure mode would be app-wide gesture breakage at launch — loud and
  immediate in smoke testing, not subtle.

## 5. User-experience effect

- Nobody sees this diff directly; it unblocks shipping. Riders/drivers see
  its effect only as "updates can reach phones again."

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/metro.config.js` | Added shim redirect in `resolveRequest` | Restore native bundling |
| `driver-app/metro.config.js` | Same redirect, mirrored comment | Same break, same stack |

## 7. Before / after

```
# Before (both apps)
expo export --platform android
→ Error: Unable to resolve module react-native/Libraries/Renderer/shims/ReactNative
  from …/react-native-gesture-handler/src/RNRenderer.ts   (exit 1)
```

```
# After (rider-app, verified this session)
expo export --platform android
→ bundle written to output dir                            (exit 0)
```

## 8. Rollback plan

- The change is inert config on disk until a build/update is cut. Rollback =
  revert the two-file commit; nothing live-data-touching to remediate.
- If a shipped OTA built on top of this redirect misbehaves at runtime:
  `eas update:republish` the previous update group on the channel (same
  single-step, no-store-review rollback as any OTA).

## 9. Verification performed

- [x] rider-app: `npx expo export --platform android` **fails at HEAD** with
  the resolution error (twice, full log captured), fails **identically with
  the Done-button fix stashed** (proves pre-existing), and **exits 0 with
  this redirect** (see companion log entry; result recorded in commit).
- [x] Published-tarball check: `npm pack react-native@0.86.2` → shims listing
  (rules out corrupted local install); RNGH 2.31.1 vs 2.31.2 `RNRenderer.ts`
  diffed (byte-identical).
- [x] Blast-radius grep: importers of `Renderer/shims/ReactNative` (RNGH
  only); existing `resolveRequest` rules read end-to-end in both apps to
  confirm no rule-ordering interaction (the new rule is exact-match,
  placed before platform-conditional rules that never match this specifier).
- [ ] driver-app export not run in this session (compute-time bound); the
  redirect is byte-equivalent to the rider-app one verified above. Run
  `expo export --platform android` in driver-app before its next build.

## 10. What was NOT verified

- **Runtime gesture behavior on a device** — no device/emulator in this
  environment. First smoke test on a build/OTA carrying this change should
  exercise scroll + tap + swipe surfaces (bottom sheets, map pan, sliders).
- Whether EAS's build environment differs in any way that masked/unmasked
  this locally — the registry-tarball check makes an environment-specific
  cause implausible, but the next `eas update`/`eas build` is the definitive
  proof.
- No automated CI job runs `expo export` for either app (that's why this
  could sit broken at HEAD unnoticed) — standing gap worth an
  `ACTION_ITEMS.md` entry: a bundle-smoke job would have caught this the day
  it landed.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to a shipped flow (build-time only)
