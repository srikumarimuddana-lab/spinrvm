# React Native patch regeneration — handoff (2026-09-10)

**Status: CLOSED / FALSE ALARM — see correction below.** Tracked as `ACTION_ITEMS.md` C98
(now closed with a same-day correction). The rest of this document is kept as-written for the
record, but its central claim — that the Android crash workaround is "currently inactive" in
both apps — turned out to be wrong, caused by this session's own broken sandbox environment.
**Read this correction before acting on anything below.**

## Correction (same day, 2026-09-10)

On the user's real Windows machine, `yarn install` completed for both apps and `patch-package`
reported **both patches applied successfully** — `driver-app`'s with zero warnings even under
`--error-on-warn` (an exact-context match, no fuzz needed), `rider-app`'s with only the routine
filename-vs-installed-version notice patch-package always prints on a version bump (that
notice compares version **strings**, not diff content — it does not imply the underlying patch
needed fuzzy matching or is broken).

This directly contradicts the "Failed to apply patch" error documented below, which was
reproduced only inside this Claude Code cloud sandbox. The explanation is the sandbox's own
install, not the patches: as this doc's "Why this can't be finished here" section already
found, this environment's `node_modules/react-native` contains **zero real `.js` source
files** (only `.d.ts` type stubs) — so *any* patch-package run here fails, regardless of
whether the real patch is broken. It wasn't broken. The Android crash workaround has very
likely been active and correctly applied in every real build of both apps (EAS, prior local
installs, CI) all along.

**What's actually still worth doing** (low priority, cosmetic only): rename/regenerate
`rider-app/patches/react-native+0.86.2.patch` to `react-native+0.86.3.patch` to match the
currently installed version and silence the harmless mismatch warning. Nothing else below
needs acting on. See `ACTION_ITEMS.md` C98 for the full correction writeup.

---

*Everything below this line is the original, since-corrected investigation — kept unedited for
the record.*

## What's broken

Both mobile apps carry a `patch-package` patch against `react-native` that
works around a real Android crash. Neither patch applies anymore:

- `rider-app/patches/react-native+0.86.2.patch` — filename says 0.86.2,
  installed `react-native` is 0.86.3. `patch-package` warns about the
  mismatch and then fails to apply.
- `driver-app/patches/react-native+0.86.3.patch` — filename already matches
  the installed 0.86.3, but the patch **still fails to apply** — the
  underlying files changed enough within the 0.86.x line that the diff no
  longer matches.

Observed failure (from a `SessionStart` hook run in this sandbox):

```
**ERROR** Failed to apply patch for package react-native at path
  node_modules/react-native
This error was caused because react-native has changed since you
made the patch file for it.
```

**Net effect: the Android crash workaround is currently NOT active in either
app's installed `node_modules`.** This is not cosmetic — confirmed by
inspecting `node_modules/react-native` directly in both apps: none of the
patch's marker comments (`PATCH (spinr rider-app/driver-app):`) are present
in any of the 8 target files. If either app ships a build against
unpatched `react-native` 0.86.3, the original crash this patch exists to
prevent is a live regression risk.

## What the patch actually does

Both patches are the same fix (confirmed identical in content, just filed
under two different version-suffixed names), touching **8 files**, not just
one:

1. `Libraries/Components/ActivityIndicator/ActivityIndicator.js`
2. `Libraries/Components/RefreshControl/RefreshControl.js`
3. `Libraries/Components/Switch/Switch.js`
4. `Libraries/Debugging/DebuggingOverlay.js`
5. `Libraries/Modal/Modal.js`
6. `src/private/components/scrollview/HScrollViewNativeComponents.js`
7. `src/private/components/virtualview/VirtualViewExperimentalNativeComponent.js`
8. `src/private/components/virtualview/VirtualViewNativeComponent.js`

The documented root cause (from the `ActivityIndicator.js` hunk's own
in-patch comment — the most legible of the 8):

> RN 0.85.2's Android ActivityIndicator path imports `ProgressBarAndroid`,
> whose `codegenNativeComponent('AndroidProgressBar', { interfaceOnly: true })`
> returns a non-renderable object under the New Architecture (Bridgeless),
> throwing `"Element type is invalid... got: object"` at render time.

The fix replaces the native `ProgressBarAndroid`-backed component with a
JS-only fallback `View` that honors `animating`, `hidesWhenStopped`, `color`,
and forwards sizing via `style`. The other 7 files are very likely the same
class of fix (native-component-returns-non-renderable-object under
Bridgeless) applied to `RefreshControl`, `Switch`, `Modal`,
`DebuggingOverlay`, and the two `VirtualView` native components — **this
needs confirming per-file**, not assumed, by whoever picks this up (see
Step 2 below).

**The exact, complete original patch content for all 8 files, both apps, is
still intact and version-controlled** — nothing was lost. Read directly from:
- `rider-app/patches/react-native+0.86.2.patch`
- `driver-app/patches/react-native+0.86.3.patch`

Both files are confirmed unmodified against `git HEAD` as of this handoff.

## Why this can't be finished in this sandbox

Regenerating a `patch-package` patch requires a diff against the **real**
`react-native` source. This Claude Code cloud environment's installed
`node_modules/react-native` (both apps) contains **no `.js` implementation
files at all** — only `.d.ts` type-declaration stubs:

```
$ find rider-app/node_modules/react-native/Libraries -name '*.js' | wc -l
0
$ find rider-app/node_modules/react-native/Libraries -name '*.d.ts' | wc -l
84
```

Total installed package size is ~16 MB (a real `react-native` install is
normally 150–300+ MB of source). `View.js` — the single most fundamental RN
component — doesn't exist either; only `View.d.ts`. This looks like a
deliberate, sandbox-specific minimization (this container has no
Android/iOS SDK or emulator to actually build/run a native mobile app
against anyway), not a broken install — but it means any patch generated
here would be a diff against stub files, not real RN source, and would be
silently wrong.

**Concretely: I attempted a diagnostic `patch-package` dry-run inside
`driver-app` and it overwrote `driver-app/patches/react-native+0.86.3.patch`
with a garbage 393,995-line diff** (diffing against something unrelated,
not the real source). This was caught immediately via `git status` /
`git diff` and reverted with `git checkout -- driver-app/patches/react-native+0.86.3.patch`
before anything was committed — the repo is clean, nothing was lost. This is
direct proof that patch regeneration must not be attempted in this
environment: the tooling runs without error and produces a plausible-looking
but wrong result.

## Recommended regeneration steps (for a real environment — local machine or CI)

Do this **once for each app** (`rider-app`, `driver-app`), on a machine (or
CI job) with a full, real `node_modules` install — i.e. NOT this cloud
sandbox:

1. `cd rider-app && yarn install --check-files` (or `driver-app` — this will
   report the same patch-apply failure locally; that's expected, continue).
2. For each of the 8 files listed above: open the original patch's hunk for
   that file (from the still-intact `patches/react-native+0.86.*.patch`) side
   by side with the **current** installed
   `node_modules/react-native/<path>`. Confirm what the current file's
   Android/native-component code path actually looks like now, then hand-port
   the same fallback-to-JS-View fix, adapted to the current code structure.
   Do not assume the old hunk's surrounding context still matches — RN's
   internals shift between patch versions, which is exactly why the old
   patch stopped applying.
3. Once all 8 files are edited in `node_modules`, regenerate the patch:
   `npx patch-package react-native` — this overwrites
   `patches/react-native+<installed-version>.patch` with a fresh diff.
4. Delete `node_modules`, reinstall (`yarn install`), and confirm
   `patch-package` reports success with **no** "Failed to apply" error for
   `react-native`.
5. Per this repo's mandatory pre-merge gate for a live-tested surface: run
   a real Android build/emulator (or physical device) check that actually
   exercises `ActivityIndicator`, `RefreshControl`, `Switch`, `Modal`, and a
   scroll view with pull-to-refresh — the crash this patch prevents is a
   render-time crash, so `tsc`/lint passing proves nothing about whether the
   fix still works.
6. Fill in a Change Impact & Risk Log entry per `CLAUDE.md` (this touches a
   customer-facing rendering path in both apps, in live app testing) before
   merging — use `docs/templates/CHANGE_IMPACT_LOG.md`.

## Cross-check worth doing first

Before hand-porting, check whether RN 0.86.3 already fixed the underlying
`ProgressBarAndroid`/Bridgeless issue upstream (RN's changelog /
GitHub issues for the 0.86.x line). If it's already fixed upstream, some or
all of these 8 patches may simply be deletable rather than needing a
rewrite — cheaper and lower-risk than porting old workarounds forward.
Confirm per-file, don't assume it covers all 8.
