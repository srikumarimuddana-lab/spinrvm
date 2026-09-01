# 2026-09-01 — iOS EAS builds broken: `cannot find 'SceneGeometry' in scope`

## Issue/gap identified
Both production iOS EAS builds from commit `ba913e9` failed (rider-app build 20 `b13d6da7`, driver-app build 21 `de00590d`) with `XCODE_BUILD_ERROR: cannot find 'SceneGeometry' in scope`; no store-submittable iOS artifact can currently be produced from `main`.

## Root cause
`SceneGeometry` is not an Apple API — it is an internal utility (`public enum SceneGeometry`, `expo-modules-core/ios/Utilities/SceneGeometry.swift`) **added in `expo-modules-core` 57.0.11** (2026-08-14, expo/expo#48168 + #48318). Both apps hard-pin `expo-modules-core` to **57.0.8** via yarn `resolutions` (pin predates the visible history window; its original rationale is not recoverable from this repo's grafted history). The dependency-bump wave that landed between the last green build (2026-08-20, commit `14102d7`) and `ba913e9` updated `expo-store-review` 57.0.1 → **57.0.2**, whose only change (expo/expo#48318 — "Present the review prompt from the foregrounded scene") is a call to `SceneGeometry.foregroundScene()` in `StoreReviewModule.swift`. New consumer + old pinned core = unresolvable symbol at compile time. The bump PR updated the adjacent `expo-constants` resolutions pin (57.0.8 → 57.0.14 in driver-app) but left the `expo-modules-core` pin untouched, which is what armed the trap.

## Fix/remediation
Revert `expo-store-review` to `~57.0.1` in both `rider-app` and `driver-app` (`package.json` spec + the exact `yarn.lock` stanza — version/resolved/integrity — taken verbatim from last-green commit `14102d7`). This restores the store-review/modules-core pair that compiled successfully in builds 19/20 while keeping every other package at its bumped version. Lockfiles were hand-edited with historically-known-good stanzas because this remote environment's egress policy blocks the npm/yarn registries (no `yarn install` possible here); the stanzas are byte-identical to what yarn itself previously generated.

## Risk & impact on existing functionality
- Blast radius: `expo-store-review` is the **only** installed consumer of `SceneGeometry` (verified by grepping the expo monorepo `sdk-57` branch: the sole other consumer, `expo-screen-capture`, appears in neither app's lockfile). No other package's resolution changes.
- The one behavior 57.0.2 added (presenting the App Store review prompt from the foregrounded scene rather than the key window) is given up until the pin is fixed — a cosmetic/edge-case regression only relevant in multi-scene situations.
- Residual trap: the `expo-modules-core: 57.0.8` resolutions pin remains. Any future bump of an expo package that adopts `SceneGeometry` (or any other post-57.0.8 core API) will re-break the iOS build the same way. Follow-up (needs a machine with npm registry access): update the pin to 57.0.14 in both apps' `resolutions`, run `yarn install` to regenerate both lockfiles, and restore `expo-store-review` `~57.0.2` in the same change — or document why 57.0.8 must be kept.

## User experience effect
None visible. Rider/driver apps regain the ability to ship iOS builds; the in-app "rate this app" prompt continues to work as it did in the currently-shipped builds (19/20), which run these exact versions.

## Files modified
| file path | what changed | why |
|---|---|---|
| `rider-app/package.json` | `expo-store-review` `~57.0.2` → `~57.0.1` | 57.0.2 requires `SceneGeometry` (expo-modules-core ≥ 57.0.11); app pins core 57.0.8 |
| `rider-app/yarn.lock` | store-review stanza 57.0.2 → 57.0.1 (verbatim from `14102d7`) | keep lockfile consistent with spec; registry unreachable from this environment |
| `driver-app/package.json` | same as rider-app | same |
| `driver-app/yarn.lock` | same as rider-app | same |

## Before/after snippet
```diff
-    "expo-store-review": "~57.0.2",
+    "expo-store-review": "~57.0.1",
```
```diff
-expo-store-review@~57.0.2:
-  version "57.0.2"
-  resolved "https://registry.yarnpkg.com/expo-store-review/-/expo-store-review-57.0.2.tgz#8035182aea0a8e7da1ce0fa80b9182c381a4864a"
-  integrity sha512-C/cMUe0blmdLOeuA/rlfKTIb6znlmSfdm1Rx+khCfRL2qbMEfY0jZkGeVv/H3xObo9AyoaKJF8Vc0J8+Vz8CWg==
+expo-store-review@~57.0.1:
+  version "57.0.1"
+  resolved "https://registry.yarnpkg.com/expo-store-review/-/expo-store-review-57.0.1.tgz#ead8367b2207e3d3b3ea3979fc4fb71c20422d4d"
+  integrity sha512-OktDBfIEe4DQXVxz7umFyg+slkZo/nrr4GfwiFJlNjOXl+F+vNsaGBdEn1aE4W5DAnSVpLQsjaZt1dts2HJXlQ==
```

## Rollback plan
`git revert` of this commit is a complete rollback — the change is dependency-manifest-only, touches no runtime data, and no build artifact produced from it has shipped. Reverting returns to the current (already-broken) state, no worse.

## Verification performed
- Traced the exact symbol: cloned expo/expo `sdk-57` (sparse) — `SceneGeometry` defined in `expo-modules-core/ios/Utilities/SceneGeometry.swift`, introduced in 57.0.11 per that package's CHANGELOG; consumed by `expo-store-review` (`StoreReviewModule.swift`) as of 57.0.2 per its CHANGELOG.
- Diffed the full resolved-version set of both yarn.locks between last-green (`14102d7`) and failing (`ba913e9`) commits (128 common changes) and confirmed `expo-modules-core` stayed 57.0.8 in both while `expo-store-review` moved to 57.0.2; confirmed `expo-screen-capture` absent from both lockfiles.
- JSON-validated both package.json files; grep-confirmed lockfiles contain exactly one `expo-store-review` stanza each, matching the new spec, byte-identical to the stanza yarn generated at `14102d7`.

## What was NOT verified
- **No iOS build was run** — this environment cannot run EAS/Xcode builds and its egress policy blocks npm registries, so `yarn install --frozen-lockfile` was not executed locally either. The real verification is the next EAS iOS build (triggered by a `[build]` commit on main after merge).
- The EAS error summary listed a single unique error; if the full (unretrieved — log endpoint timed out) Xcode log contains additional independent errors beyond `SceneGeometry`, they would surface on the next build.
- Android builds were not examined (no failure reported there; the changed package's delta is iOS-only).

---

# Round 2 (same day) — second latent error unmasked: `'getModule' is inaccessible due to 'internal' protection level`

## Issue/gap identified
The Round 1 rebuild on merge commit `1ed0adb` (rider build `55781f21`) cleared the `SceneGeometry` error but failed on the next one behind it: `'getModule' is inaccessible due to 'internal' protection level` ×2. EAS's error summary only surfaces the first failing batch, so this error was invisible until Round 1 removed the one in front of it.

## Root cause
Same mechanism, different consumer: `expo-image` (bumped 57.0.1 → **57.0.2+** in the same wave) calls `appContext?.moduleRegistry.getModule(implementing: ImageModule.self)` at exactly two sites (`ImageView.swift`, `ImageModule.swift` — matching the two reported errors) for its new `imageLoaded` event (expo/expo#47337, shipped 2026-08-04 in lockstep with core 57.0.9 which made `ModuleRegistry.getModule(implementing:)` public). The pinned core 57.0.8 still has it `internal`.

**Pin rationale recovered** (contradicts Round 1's "not recoverable" note): `docs/change-log/2026-08-02-rider-app-expo-sdk57-upgrade.md` documents `resolutions.expo-modules-core` as a *"supporting pin that must track the SDK line but isn't Expo's own submodule, so Dependabot's grouping never touches it."* It is a dedupe pin that is **supposed to move with every bump wave** — the wave moved every sibling and missed it. Round 1's one-package revert was therefore the wrong shape: any bumped sibling can depend on post-57.0.8 core APIs (two already did), so the pin itself is the fix.

## Fix/remediation
In both apps:
- `resolutions."expo-modules-core"`: `57.0.8` → **`57.0.14`** (latest published in the 57.0.x line; satisfies both the `getModule` publicization (≥57.0.9) and `SceneGeometry` (≥57.0.11); now also range-compatible with the `~57.0.13` spec the `expo` package declares).
- `yarn.lock`: core stanza rewritten to 57.0.14 with its real 57.0.14 dependency set (from the expo monorepo manifest at the 57.0.14 publish state: `@expo/expo-modules-macros-plugin 0.6.1`, `expo-modules-jsi ~57.0.6`, `invariant ^2.2.4`); `expo-modules-jsi` stanza moved `~57.0.4`/57.0.4 → `~57.0.6`/57.0.6 (zero-dependency leaf, published 2026-08-26 in lockstep with core 57.0.14).
- `expo-store-review` restored to `~57.0.2` (Round 1's revert is no longer needed once core has `SceneGeometry`), stanza restored byte-identical from git history.

## Risk & impact on existing functionality
- Core 57.0.9–57.0.14 delta is small per its CHANGELOG: the `SceneGeometry`/`getNativeRef` additions, a `matchContents` stale-size regression fix (57.0.13) *for* a regression introduced earlier in the line, measurement fixes for SwiftUI/Compose-hosted views, and an Android settled-promise guard. Every bumped expo sibling in the apps was published against this line, so raising the pin moves the tree *toward* the tested lockstep matrix, not away from it.
- **Known limitation of this change:** the two new lockfile stanzas (`expo-modules-core@57.0.14`, `expo-modules-jsi@57.0.6`) carry **no `integrity`/sha1 fields** — this environment's egress policy blocks every npm registry/CDN route to the published hashes. yarn v1 skips verification for entries with no recorded hash and will fetch them fine, but the first `yarn install` run on a machine with registry access (NOT `--frozen-lockfile`) should be allowed to rewrite the lockfile to backfill real `integrity` hashes, and that diff should be committed.

## User experience effect
None beyond restoring the ability to ship iOS builds. `expo-image`'s `imageLoaded` event and store-review's foreground-scene behavior activate as their bumped versions intended.

## Files modified
| file path | what changed | why |
|---|---|---|
| `rider-app/package.json`, `driver-app/package.json` | core resolution 57.0.8 → 57.0.14; store-review `~57.0.1` → `~57.0.2` | move the dedupe pin with the SDK line, as its documented purpose requires |
| `rider-app/yarn.lock`, `driver-app/yarn.lock` | core stanza → 57.0.14 (real 57.0.14 deps, no integrity — see limitation); jsi stanza → 57.0.6 (no integrity); store-review stanza → 57.0.2 (full integrity, from git) | keep lockfiles consistent with the resolutions change without registry access |

## Rollback plan
`git revert` of this commit — manifest-only, nothing shipped from the broken state. Reverting lands back on the Round 1 state (SceneGeometry fixed, getModule broken).

## Verification performed
- `yarn install --frozen-lockfile` executed locally in **both** apps: `[1/4] Resolving packages...` completes and installation advances to `[2/4] Fetching packages...` before dying on this sandbox's blocked egress — a lockfile inconsistent with package.json aborts in step 1 with "Your lockfile needs to be updated", so the edited lockfiles pass the exact gate EAS's install step enforces. Driver-app's resolution warnings (`@expo/log-box`, `expo-constants` pins lagging requested ranges) predate this change and are non-fatal; no warning exists for the new core pin.
- 57.0.14 dependency set and jsi 57.0.6 version/deps read from the expo/expo monorepo `sdk-57` branch manifests (tip package.json version field is exactly 57.0.14 / 57.0.6, i.e. the as-published state).
- `getModule` call sites and access level verified in monorepo source; version attribution via expo-image and expo-modules-core CHANGELOGs.

## What was NOT verified
- Same as Round 1: no local iOS build possible; the next EAS build is the real gate.
- The two integrity-less stanzas were validated against yarn's resolver locally but not against a real fetch (egress blocked); EAS's yarn will do the first real fetch of those two tarballs.
- Whether further latent Xcode errors hide behind `getModule` (EAS reveals errors in batches); each round has strictly progressed, and no other bumped package shows post-57.0.14 API usage by construction (57.0.14 is the newest published core).
