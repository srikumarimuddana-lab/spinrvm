# Change Impact & Risk Log — Android R8 minify on for production (rider + driver)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Author | Cursor Grok (operator request after internal-testing sign-off) |
| Surface(s) | rider-app, driver-app (Android production-profile EAS builds only) |
| Domain (Sentry tag) | drivers / rides (build-config; no runtime domain owns it) |
| PR / commit link | follow-on to PR #5418 |
| Related issue or gap ID | Play Console App optimization Low; staged R8 rollout |

## 1. Issue / gap identified

PR #5418 staged R8 behind `SPINR_ANDROID_MINIFY`, with production explicitly `0`. The operator
validated the current Play internal-testing apps and asked to turn minify **on** for the
production profile of **both** rider and driver so the next store AAB is minified.

## 2. Root cause

Production was left off on purpose until a human signed off. That sign-off is this request.
The AAB already on internal testing was built with minify `0`; promoting it would not minify.

## 3. Fix / remediation

Set `build.production.env.SPINR_ANDROID_MINIFY` to `"1"` in both `rider-app/eas.json` and
`driver-app/eas.json`. Next production-profile Android builds run R8. Resource shrinking
stays off. iOS is unchanged.

**Adversarial alternative:** promote the current internal AAB and skip this flag flip.
Rejected — that binary is unminified. There is no way to minify an already-built AAB.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, native modules in both Android apps.** Same table as
`docs/change-log/2026-09-14-android-r8-minification-staged.md` §4 (Nitro/Android Auto,
Skia, background location, Notifee ride-offer sound, Stripe, Firebase App Check/FCM).

Installed apps do not change until testers (then store users) install the **new** minified
AAB. OTA cannot add or remove R8.

**Assumption (stated):** the internal-testing sign-off was on the unminified production
AAB, not a minified preview/android-auto binary. The new minified internal build must be
installed and re-checked before promoting.

**Sentry:** Java/Kotlin frames will be obfuscated until ProGuard mapping upload is wired.
JS Sentry and Crashlytics are unaffected.

## 5. User-experience effect

Nobody until the new AAB is installed. Then: smaller download / slightly faster cold start,
or a release-only native crash if R8 breaks a module. No copy change. Not visible mid-session
on an already-installed unminified binary.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/eas.json` | production `SPINR_ANDROID_MINIFY` `0` → `1` | Minify the next rider store AAB |
| `driver-app/eas.json` | production `SPINR_ANDROID_MINIFY` `0` → `1` | Minify the next driver store AAB |
| `rider-app/app.config.ts` / `driver-app/app.config.ts` | Comments | Stop saying production is off |
| `docs/android-build-strategy.md` | Production row on | Match the flag |
| `docs/carplay-android-auto.md` | Nitro keep-rule profiles | Include production |

## 7. Before / after

```json
"SPINR_ANDROID_MINIFY": "0"   // production profile, both apps
```

```json
"SPINR_ANDROID_MINIFY": "1"   // production profile, both apps
```

## 8. Rollback plan

Set production back to `"0"`, rebuild, submit a **new** AAB with a higher versionCode to
the same Play track, and have testers/users install it. OTA cannot undo R8. No DB/Stripe/
ride-state rollback. If the minified internal build is bad, do **not** promote it.

## 9. Verification performed

- [x] Blast-radius: flag is only read via `SPINR_ANDROID_MINIFY === '1'` in both `app.config.ts` files feeding `enableMinifyInReleaseBuilds`.
- [x] Both production profiles now `1`; development/test remain `0`; rider preview and driver preview/android-auto remain `1`.
- [ ] Native build / `:app:minifyReleaseWithR8` log — will be the queued EAS production builds.
- [ ] Device re-test of the **new minified** internal AAB (operator).
- [ ] Sentry mapping upload — still not wired.

## 9b. What was NOT verified

No minified production-profile AAB existed at the time of this flag flip. Android Auto
head-unit proof of the Nitro keep rule is still outstanding. No visual-regression tooling
for rider-app or driver-app.
