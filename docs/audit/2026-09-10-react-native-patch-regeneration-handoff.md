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

**Correction (2026-09-10, later same day): the "8 files, identical between
apps" claim above was wrong on both counts — verified by reading the full
patch content, not just its file list.**

The two patches are **not identical**: `driver-app/patches/react-native+0.86.3.patch`
has 612 lines / 11 `PATCH (spinr...)` markers; `rider-app/patches/react-native+0.86.2.patch`
has 518 lines / 7 markers. Confirmed via `diff` on their `diff --git` header
lines: driver-app's patch touches a 9th file rider-app's doesn't —
`Libraries/Components/ScrollView/ScrollView.js` — and driver-app's
ActivityIndicator/Modal hunks carry two extra, driver-app-only iOS fixes
(see "Two distinct root causes" below) that rider-app's hunks for those same
files don't include. Whoever regenerates these should **not** assume porting
one patch and copying it to the other app is correct — diff the two
patches' shared files against each other first to see exactly where they
already diverge on purpose.

Driver-app's patch (the more complete of the two), 9 files:

1. `Libraries/Components/ActivityIndicator/ActivityIndicator.js`
2. `Libraries/Components/RefreshControl/RefreshControl.js`
3. `Libraries/Components/ScrollView/ScrollView.js` — **missing from this doc's original file list; also absent from rider-app's patch entirely**
4. `Libraries/Components/Switch/Switch.js`
5. `Libraries/Debugging/DebuggingOverlay.js`
6. `Libraries/Modal/Modal.js`
7. `src/private/components/scrollview/HScrollViewNativeComponents.js`
8. `src/private/components/virtualview/VirtualViewExperimentalNativeComponent.js`
9. `src/private/components/virtualview/VirtualViewNativeComponent.js`

rider-app's patch touches the same 9 minus `ScrollView.js` (8 files), and
its ActivityIndicator/Modal hunks are the Android-only halves of driver-app's
(no iOS extension — see below).

### Two distinct root causes, not one — correcting "very likely the same class of fix"

This doc originally guessed all 8 files shared one root cause (Bridgeless
runtime rendering) and flagged that as unconfirmed. Having now read every
hunk directly, there are **two unrelated bug classes**:

- **Runtime Bridgeless-rendering crash** (7 files: ActivityIndicator,
  RefreshControl, ScrollView, Switch, DebuggingOverlay, Modal,
  HScrollViewNativeComponents) — `codegenNativeComponent(..., {interfaceOnly:
  true})` resolves to a non-renderable object at render time, throwing
  `"Element type is invalid... got: object"`. Each of these files' fix
  detects the broken component and falls back to a JS-only implementation.
  `ScrollView.js`'s hunk is a defensive guard for `HScrollViewNativeComponents.js`'s
  fix specifically (falls back to plain `View` if the patched native
  component still isn't renderable for any reason), not an independent fix.
- **Build-time codegen parse failure** (2 files: both VirtualView native
  component specs) — unrelated to Bridgeless rendering. RN's codegen
  (`@react-native/babel-plugin-codegen`) can't resolve a *named type alias*
  (`NativeModeChangeEvent`) referenced inside `DirectEventHandler<...>`,
  throwing "Unable to determine event arguments for onModeChange" at build
  time. The fix inlines the event shape directly instead of referencing the
  alias. Per the patch's own comment, `VirtualView` isn't even used by
  Spinr's app code — this exists purely to stop the build from failing
  during RN's own internal codegen pass, not to fix app behavior.

Additionally, two files (`ActivityIndicator.js`, `Modal.js`) have a
**driver-app-only iOS extension** beyond the Android fix described above —
each citing a specific real crash already seen in production:
`ActivityIndicator`'s iOS fallback exists because a non-renderable native
component "crashed BrandSplash (and thus the whole app at first render) on
the driver app while rider was unaffected"; `Modal`'s iOS fallback exists
because the same failure "crashed CancelReasonSheet on ride cancel." Neither
extension is present in rider-app's patch for those files.

**The exact, complete original patch content for all 9 driver-app files (8
for rider-app) is still intact and version-controlled** — nothing was lost.
Read directly from:
- `rider-app/patches/react-native+0.86.2.patch`
- `driver-app/patches/react-native+0.86.3.patch`

Both files are confirmed unmodified against `git HEAD` as of this handoff.

### Checked whether RN 0.86.3 already fixed any of this upstream — no

The "Cross-check worth doing first" section below was written before this
check existed; running it now gives a clear negative answer, so the full
hand-port is confirmed necessary, not just recommended as a precaution.
Fetched the real RN 0.86.3 source (npm's `gitHead` for that exact version,
commit `95cffbff2e071e278987c7d7cd51fbc970dd5622`, `facebook/react-native`)
for 6 of the 9 files and compared each patch's stated "before" lines against
it verbatim:

| File | Root-cause line/import | Still present in 0.86.3? |
|---|---|---|
| `ActivityIndicator.js` | `require('../ProgressBarAndroid/ProgressBarAndroid').default` | Yes, byte-identical |
| `RefreshControl.js` | `import AndroidSwipeRefreshLayoutNativeComponent, {...} from './AndroidSwipeRefreshLayoutNativeComponent'` | Yes, byte-identical |
| `Switch.js` | `import AndroidSwitchNativeComponent, {...} from './AndroidSwitchNativeComponent'` | Yes, byte-identical |
| `Modal.js` | `import RCTModalHostView from './RCTModalHostViewNativeComponent'` | Yes, byte-identical |
| `HScrollViewNativeComponents.js` | Android branch still resolves to `AndroidHorizontalScrollViewNativeComponent`/`AndroidHorizontalScrollContentViewNativeComponent` | Yes, byte-identical |
| `VirtualViewNativeComponent.js` | `onModeChange` still typed via the named `NativeModeChangeEvent` alias | Yes, byte-identical |

Not independently re-fetched: `ProgressBarAndroid.js` itself (confirmed its
`ActivityIndicator.js` caller is unchanged, which is what actually matters —
the file that defines the codegen call may have moved to a platform-suffixed
variant, but the import path that triggers the crash hasn't), `DebuggingOverlay.js`,
`ScrollView.js`, `VirtualViewExperimentalNativeComponent.js` (its sibling
`VirtualViewNativeComponent.js` was checked and is unchanged; both files
carry the identical fix pattern per the patch itself). Given 6 of 6 checked
files are byte-identical to the pre-patch state, treat all 9 as unfixed
upstream unless a future check finds otherwise — there is no shortcut here;
every file needs the same hand-port this doc already recommended.

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
2. For each of the 9 driver-app files / 8 rider-app files listed above: open
   the original patch's hunk for that file (from the still-intact
   `patches/react-native+0.86.*.patch`) side by side with the **current**
   installed `node_modules/react-native/<path>`. Confirm what the current
   file's Android/native-component code path actually looks like now, then
   hand-port the same fix, adapted to the current code structure. Do not
   assume the old hunk's surrounding context still matches — RN's internals
   shift between patch versions, which is exactly why the old patch stopped
   applying. **The two VirtualView files need a different verification step
   than the other 7**: since their bug is a codegen build-time parse
   failure, not a runtime render crash, confirming the fix still applies
   means triggering RN's codegen pass (part of the normal build) and
   checking it doesn't throw "Unable to determine event arguments" — not
   watching for a render-time crash on device.
3. Once all files are edited in `node_modules`, regenerate the patch:
   `npx patch-package react-native` — this overwrites
   `patches/react-native+<installed-version>.patch` with a fresh diff. Do
   this once per app — don't copy one app's regenerated patch to the other;
   they're intentionally different (see "What the patch actually does").
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

## Cross-check worth doing first — DONE (2026-09-10), answer is no

Before hand-porting, check whether RN 0.86.3 already fixed the underlying
`ProgressBarAndroid`/Bridgeless issue upstream. **Checked directly against
real upstream RN 0.86.3 source (not the changelog — RN's changelog doesn't
cover internal fixes at this granularity, confirmed by searching it for
every relevant term with zero matches).** 6 of 9 files' exact root-cause
lines are byte-identical to the pre-patch state — see the table in "What the
patch actually does" above. None of these patches are deletable; every one
still needs the hand-port this doc recommends. This section is kept for the
record, not because it's still an open question.
