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
