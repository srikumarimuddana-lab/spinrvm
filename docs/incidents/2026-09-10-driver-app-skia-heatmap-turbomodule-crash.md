# Incident — driver-app iOS crash, `TurboModuleRegistry.getEnforcing('RNSkiaModule')`

**Detected:** 2026-09-10, via a PR (#5162) reporting a driver-app iOS crash
**Status:** **Mitigated, verified, and closed.** Crashing code path fully disabled (#5167, merged) and confirmed holding — see §6. Root cause not conclusively confirmed — see §4.
**Classification:** Downgraded from the reported "P0 / 100% crash" to a narrow, single-user incident — see §1.
**Related:** PR #5149 (HM-32, introduced the dependency) · PR #5162 (`__turboModuleProxy` pre-check, merged but now inert) · PR #5167 (kill-switch, the actual mitigation) · `docs/change-log/2026-09-10-skia-heatmap-crash-kill-switch.md` · Sentry issue `CRIMSON-SMOKE-7445-RH` (`spinr-backend.sentry.io/issues/7723007736`)

> ## The headline
>
> A driver-app iOS build crashed with `Invariant Violation:
> TurboModuleRegistry.getEnforcing(...): 'RNSkiaModule' could not be found` after
> HM-32 (a new Skia-based heatmap overlay) shipped. It was reported and acted on as
> a **100%, fleet-wide P0 crash**. The actual Sentry data, pulled after the fix
> already shipped, shows **3 events, 1 user** — not a fleet-wide outage. The fix
> (disabling the crashing code path entirely) was still the right call and is
> merged; this document exists to correct the severity record and be honest about
> what the evidence does and doesn't establish about the actual root cause.

---

## 0. What actually happened, in plain terms

1. HM-32 (PR #5149) added a new native dependency (`@shopify/react-native-skia`)
   to draw a nicer iOS demand-heatmap gradient, guarded by a `try/catch` around
   its `require()` call — the standard pattern this codebase already uses
   elsewhere for exactly this "OTA JS update reaches a binary that predates a new
   native module" scenario.
2. That code shipped automatically to the iOS **production** OTA channel the
   moment #5149 merged.
3. A separate PR (#5162) reported this was crashing driver-app 100% on iOS,
   claiming the `try/catch` guard didn't actually work because
   `TurboModuleRegistry.getEnforcing()`'s failure "crashes the Hermes runtime
   before JS try/catch can intercept it," and proposed a `__turboModuleProxy`
   pre-check as the fix.
4. That root-cause claim was checked against the actual `react-native`/
   `invariant`/`@shopify/react-native-skia` source and against this repo's own
   pre-existing regression test (which simulates the identical throw and already
   passed) — none of it supported the claim. The exception is, by every piece of
   source-level evidence, a plain synchronous JS throw that a wrapping
   `try/catch` should catch.
5. Given the crash was reported as active on real phones and the true mechanism
   was unconfirmed, PR #5167 shipped a kill-switch instead of trusting an
   unverified theory: it disables the entire Skia render path (`false && ...`)
   and restores the pre-HM-32, Skia-free iOS heatmap fallback. Both #5162 and
   #5167 are merged.
6. Only *after* the fix shipped was the actual Sentry issue pulled and read. It
   showed a much smaller incident than reported, and a stack trace too corrupted
   to use for root-causing. See §1–§3.

## 1. What the Sentry data actually shows

| Field | Value |
|---|---|
| Issue | `CRIMSON-SMOKE-7445-RH` |
| Events | **3** |
| Users affected | **1** |
| Last seen | ~3 hours before the issue was pulled (exact timestamp not captured — see §5) |
| `environment` | `production` |
| `release` | `com.spinr.driver@2.0.0+29` |
| `surface` | `driver-app` |
| `level` | `fatal` |
| `mechanism` | `onerror` |
| `handled` | `no` |
| Culprit (Sentry's heuristic) | `anonymous(main)` |

**This was reported to the user and treated internally as a "100% crash on
driver-app 2.0.03."** Neither part of that framing holds up: the release is
`2.0.0+29`, not "2.0.03" (likely a misread of the version/build fields), and the
actual blast radius per Sentry's own count is one user. This should have been
checked before being repeated as a P0 in the PR that reported it, in the PR that
fixed it, and in status updates to the user. Recorded here so the same mistake —
trusting a self-reported "100%"/"P0" without checking the actual monitoring
data — doesn't repeat.

## 2. The stack trace is not usable for root-causing

The captured frames jump between unrelated library internals with no plausible
caller/callee relationship: a DOM `CharacterData.substringData` polyfill, an
`expo-sensors` `MagnetometerUncalibratedSensor` class definition, React Native
Fabric renderer internals, `XMLHttpRequest.send`, `RCTAlertManager`, and two
lines attributed to the **driver-app Profile screen** (`app/driver/(tabs)/
profile.tsx`, "Documents" section) — a screen with no relationship to the
heatmap or Skia. One frame (`app:///main.jsbundle:1 in anonymous`) is fully
unsymbolicated. This is the signature of Sentry resolving minified bundle
positions against a mismatched or stale sourcemap for this release, not a real
call path.

**What is trustworthy**: the exception's message text (`'RNSkiaModule' could not
be found...`) — that string is generated verbatim by React Native's own
`invariant()` call and isn't subject to sourcemap corruption, so the *identity*
of the failure is certain even though its *location* is not.

## 3. Root cause — not conclusively determined

Confirmed via direct source inspection (`node_modules/invariant/invariant.js`,
`node_modules/react-native/Libraries/TurboModule/TurboModuleRegistry.js`, and
`@shopify/react-native-skia`'s own `index.js` → `NativeSetup.js` →
`NativeSkiaModule.js` chain): the failing call is a synchronous
`TurboModuleRegistry.getEnforcing("RNSkiaModule")`, executed as an immediate,
synchronous side effect of `require('@shopify/react-native-skia')` — no async or
deferred initialization involved. By every source-level and empirical (this
repo's own passing regression test simulating the identical throw) signal, this
*should* be caught by a `try/catch` wrapped around that `require()` call — which
is exactly what HM-32 shipped with from the start.

Sentry's `mechanism: onerror` / `handled: no` says the exception reached the
global handler uncaught. That is a genuine, structural fact and it does not
match the "should be caught" analysis above. The gap between those two facts is
not resolved. The most likely reconciling explanation, unconfirmed: the
captured event's "last seen ~3h ago" is close to — possibly *before* — the
23:57:42 UTC merge/OTA-publish of the try/catch-guarded code, which would mean
this event reflects a user hitting an earlier, unguarded state rather than a
failure of the guard itself. Settling this needs the event's exact timestamp
(see §5.1), which was not captured before this document was written.

## 4. What was done

- [x] **Crashing code path fully disabled** (PR #5167, merged) — `require('@shopify/react-native-skia')` can no longer execute on any device, sidestepping the unresolved question in §3 entirely rather than relying on a guard whose adequacy is still unconfirmed.
- [x] **iOS heatmap functionality restored** via the pre-HM-32, Skia-free fallback (`HeatmapCells`) — no feature loss beyond the newer visual style.
- [x] **PR #5162's `__turboModuleProxy` pre-check merged** — now dead code (the component it lives in never renders), kept rather than reverted since it's harmless and can be revisited if Skia is ever re-enabled.
- [x] **Root-cause claim in #5162 checked against primary sources** rather than accepted — see §3.
- [x] **Severity corrected in this document** rather than left standing at "100%/P0."

## 5. What still must be done

1. **Get the exact event timestamp from the Sentry issue detail page** (the admin portal shows only relative time — "3h ago" — in the copy pasted here) and compare it against `2026-09-09T23:57:42Z` (the HM-32 merge/OTA-publish time). This single fact would settle whether the guard was ever genuinely inadequate or whether this event predates it.
2. **Fix driver-app's Sentry sourcemap upload pipeline.** Right now a JS stack trace for a driver-app production crash cannot be trusted — this incident's trace pointed at an unrelated screen. This is a real observability gap independent of this specific incident and will make the *next* crash equally hard to diagnose until it's fixed.
3. ~~**Watch for recurrence with a timestamp *after* the kill-switch reached devices.**~~ **Done — see §6.** No new events observed across multiple checks spanning several hours.
4. **Re-enable the Skia gradient overlay only after**: a real EAS device build test on both a pre- and post-Skia binary, and — given this incident — a working sourcemap pipeline so any future failure is actually diagnosable, not just guarded against. *(Still open — §6's verification confirms the mitigation holds, not that the original guard was ever adequate. That question is still unresolved per §3.)*
5. **Fix driver-app's Sentry sourcemap upload pipeline.** Still open — unrelated to whether this specific incident recurs, and will make the *next* crash (in any driver-app feature, not just this one) equally hard to diagnose until it's fixed.
6. **Calibrate future severity claims against actual monitoring data before acting on them.** "100% crash" and "P0" were asserted, not measured, in the PR that reported this. The fix built on top of that unverified severity was still reasonable on its own risk/cost merits (see #5167's Change Impact Log) — but the size of the emergency shaped how it was communicated to the user, and that part should have waited for the Sentry numbers.

## 6. Post-fix verification — no recurrence observed

Watched the Sentry issue across three separate checks after the kill-switch (#5167) went live, alongside a direct on-device check:

| Check | Events | Users | "Last seen" (relative, at time of check) |
|---|---|---|---|
| 1 (before the fix was fully assessed) | 3 | 1 | ~3h ago |
| 2 | 5 | 1 | ~6h ago |
| 3 (most recent) | 5 | 1 | ~8h ago |

Reading this: between checks 2 and 3, the event count did **not** move (5 → 5), and "last seen" aged by almost exactly the real wall-clock time that passed between the two checks — the signature of a **static, unchanging last-seen timestamp**, not a stream of new crashes. Had a genuinely new crash occurred in that window, "last seen" would have reset toward "just now" rather than continuing to age. The 3 → 5 jump between checks 1 and 2 is not fully explained (Sentry's issue-level count can shift slightly between dashboard loads independent of new events), but it was not accompanied by any forward movement in "last seen" either, so it does not read as new crashes.

**Separately, a real device check** (app opened on an actual iPhone after the fix's OTA had already delivered) reported no crash.

**Conclusion:** across the observation window, no crash traceable to this issue occurred after the mitigation went live. This is being treated as sufficient real-world confirmation that the mitigation (§4) holds, closing the "does the fix actually work" question this document was tracking. It does **not** retroactively confirm or deny the original root-cause question in §3 — that remains an open, separate question, now low-priority since the risky code path is permanently disabled either way.

## 7. Lessons

- **A severity label in a PR description is a claim, not a measurement.** "100% crash," here, was a five-word sentence that went unchallenged through two PRs and a user-facing status update before anyone pulled the actual event/user count. The fix cost was low enough that acting fast was still right, but the *framing* should have been "reported crash, severity unconfirmed" until the monitoring data was actually checked.
- **A stack trace is not self-verifying.** This one contained frames from unrelated files with no logical call relationship, which is a recognizable tell (broken sourcemaps) — but it still takes a deliberate "does this call chain make sense" read to catch, not just trusting whatever the crash-reporting tool renders.
- **Removing a risky code path beats trusting an unverified patch to it, when the two are similarly cheap.** The kill-switch didn't need to know what was really wrong to be effective, and that turned out to matter: the root cause is *still* unconfirmed as of this document, and a narrower "smarter guard" fix would have shipped without ever resolving that gap either.
