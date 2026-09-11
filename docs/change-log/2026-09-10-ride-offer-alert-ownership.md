# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (session `01Ro32rhyxmV2ws4MaPhPUSX`) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | _pending — user is pushing this themselves_ |
| Related issue or gap ID | User report: two overlapping ride-offer ringtones when the driver opens the app from the launcher instead of tapping the push notification. Third attempt at this defect — supersedes `2026-09-04-ride-offer-ringtone-resume-dedup.md` and `2026-09-04-ride-offer-ringtone-self-review-fixes.md`. Full plan: Phase 1 of the Ride-Offer Alert Ownership plan. |

## 1. Issue / gap identified

A ride offer arrives while the driver app is backgrounded or killed; the OS notification rings correctly. If the driver opens the app **from the launcher** rather than tapping the notification, the app starts its own looping alert tone while the notification is still looping its channel ringtone. Both sound together for the rest of the ~15 s offer window, and `ongoing: true` + `autoCancel: false` means the driver cannot swipe the notification away to stop it.

## 2. Root cause

Two independent audio sources exist and nothing arbitrates between them:

1. **OS notification ringtone** — `notifeeService.ts` posts the offer with `loopSound: !muted`, which Notifee maps to Android's `FLAG_INSISTENT`, documented in the installed library (`src/types/NotificationAndroid.ts`, `AndroidFlags`) as repeating *"until the notification is cancelled or the notification window is opened"*.
2. **In-app MP3 loop** — `useRideOfferSound.ts` re-plays every 2500 ms while the offer panel is up.

The handover between them was implemented as a **re-post of the same notification id onto the silent channel** — an *update*, not a cancel. Three distinct failures followed:

- **The handover was unreachable on the most common path.** `consumePendingOffer` owned the only handover, but it runs it only when `consumePendingRideOffer()` resolves `true`. That function deletes `PENDING_OFFER_KEY` and *then* returns `false` whenever `rideState !== 'idle'` (`services/pendingRideOffer.ts`) — which is exactly the state after the WS handler claimed the offer while the app was backgrounded-but-alive. So on that path the silent re-post never ran and **nothing could cancel the loud looping notification** until it expired. The `rideState !== 'ride_offered'` dismiss effect cannot help either, because the state *is* `ride_offered`.
- **Even on the path that did run, the ordering was inverted.** `offerSound.play()` fired *before* the surface call, and that call's first statement awaits `ensureNotifeeReady()` (two `createChannel`s, N `deleteChannel`s, `requestPermission`). Both sources therefore sounded for the length of that round-trip — seconds on a cold start.
- **An update is not a documented way to stop an insistent ring.** Stock AOSP's `buzzBeepBlinkLocked` may clear it via `clearSoundLocked()`, but that is an undocumented implementation detail and OEM-dependent. The previous fix's change-log asserted the re-post "always collapses to exactly one audible source"; that assertion is the defect.

Compounding: `expo-audio` pauses players on the background transition and **auto-resumes them on foreground** (`AudioModule.kt` `OnActivityEntersBackground`/`OnActivityEntersForeground`; `shouldPlayInBackground` is never set and defaults `false`). That is why the second sound begins precisely at app-open — and why the reverse transition left the driver with **no** audible alert at all, since the notification had already been handed down to the silent channel.

## 3. Fix / remediation

Replaces the mechanism rather than patching the same inference a third time. The invariant is now explicit in the code comments: **one audio authority at a time; the authority is whoever the driver can actually hear; every handover is cancel → post → ring; a failed handover fails toward noise, never toward silence.**

- `displayRideOfferNotification` takes a new `reclaim` option and, for `silent` or `reclaim`, **awaits `notifee.cancelNotification` before posting**. The cancel is deliberately hoisted *above* `await ensureNotifeeReady()` — cancelling needs no channel, so a slow or failed channel setup can no longer hold the ring open. The cancel's failure is swallowed so the post still runs.
- `_surfaceOfferNotification` now **returns a promise** (it still never rejects), so callers can await the handover before ringing.
- `consumePendingOffer` awaits the handover, then rings.
- **New `AppState` effect re-elects the audio owner on every transition while `rideState === 'ride_offered'`**, keyed on "an offer is live" rather than "we just hydrated one from storage". Foreground → cancel the OS ring and ring in-app; background → stop the tone and re-post **loud** (`reclaim`) so the ring follows the driver. Guarded by a per-ride `audioOwnerRef` so a repeated state (iOS `inactive`→`active`) cannot re-run a handover and blink the card.
- `reclaim` posts loud **without** `fullScreenAction`, so backgrounding mid-offer does not slam the activity back (plan decision 4, built up front rather than left as a fallback).
- New silent channel **`ride-offers-fg-v2`** at `AndroidImportance.DEFAULT` with `vibration: false`; `ride-offers-fg-v1` added to `STALE_CHANNEL_IDS`. v1 was `HIGH` with a vibration pattern, harmless while only ever reached by an update, but now that the handover posts a *new* notification it would peek a heads-up banner over the panel the driver is looking at and re-buzz. Channel config is immutable once created, so this required a new id.
- `getRideOfferTimeoutMs` **pins an absolute deadline per ride**. Previously a `countdown_seconds`-only payload recomputed a full fresh countdown on every re-post, so a handover could push the card's dismissal past the backend's actual offer expiry.
- `ensureNotifeeReady` **no longer caches a rejection**. A single transient `createChannel`/`requestPermission` failure used to disable every later notification for the process lifetime; now that going *silent* depends on this function being reachable, a poisoned cache would mean a ringtone nothing can stop.
- A handover that lands after the offer died now dismisses instead of resurrecting a card with stale Accept/Decline buttons.

## 4. Risk & impact on existing functionality

**Blast radius — every consumer enumerated, not assumed:**

| Consumer | Effect of this change |
|---|---|
| `services/backgroundMessaging.ts` (headless FCM, the killed-app path) | **Unchanged.** Passes neither `silent` nor `reclaim`, so it never cancels and still posts the loud, looping, full-screen-intent notification. A regression test now pins this. |
| `useDriverDashboard.ts` WS handler | First-delivery post unchanged (still loud while backgrounded). The new effect takes the ring off it when the driver foregrounds. |
| `useDriverDashboard.ts` FCM-foreground handler | Same as above. |
| `useDriverDashboard.ts` `consumePendingOffer` | Ordering changed (handover awaited, then ring). |
| `app/_layout.tsx:307` foreground Notifee action | Calls `dismissRideOfferNotification` only — unaffected, except the deadline is now also cleared (correct). |
| `lib/androidAuto/carSession.ts` | **Untouched.** Never mounts `useRideOfferSound`, never posts a notification. Note the pre-existing hazard (unchanged, tracked separately): a car-only launch consumes `PENDING_OFFER_KEY`, so the phone's loud notification is the only alert — this change deliberately does not weaken that notification. |
| `useRideOfferSound` | **Implementation untouched.** Only new call ordering. Single consumer, confirmed. |
| Notification id `ride-offer-current` | Unchanged, so all existing cancel/parse paths still match. |
| iOS | The cancel is Android-gated. iOS behaviour is unchanged by this phase — the duplicate-card fix is Phase 2, deliberately a separate change. |

**What could regress:**
- A cancel-then-post is a new notification, so a shade flicker or heads-up re-peek is possible. Mitigated by the DEFAULT-importance, vibration-free `fg-v2` channel — but this is the single most likely visible side effect and it is **not** verifiable without a device.
- There is a brief window between cancel and post with no card. Bounded by one native round-trip; the in-app tone is already playing in every case that reaches the `silent` branch.
- **The dangerous direction is silence**, because a missed offer increments the miss streak and 3 misses auto-offline the driver (`matching.py`). This change was designed so every failure lands on noise instead: the cancel only runs on an explicit handover, its failure is swallowed, the post still runs, and `.finally` starts the tone even if the handover threw.
- Drivers who customised the "Ride Offers (in-app)" channel in OS settings lose that customisation when `fg-v2` replaces `fg-v1`.

## 5. User-experience effect

Driver-facing, and visible mid-session to a driver who is online:

1. Opening the app on a live offer now produces **one** tone instead of two overlapping ones.
2. **New behaviour, not just a bug fix:** backgrounding the app mid-offer now re-arms the loud notification instead of leaving the driver in silence. Strictly louder than before.
3. The in-app offer panel no longer gets a heads-up banner peeking over it, and the second vibration on foreground is gone.
4. No reduction in reach for killed / locked / DND — that path is byte-for-byte unchanged.

Not addressed in this phase, and still true for drivers: the notification remains un-swipeable (the Silence affordance is Phase 3), and iOS drivers on silent/DND remain under-alerted (Phase 4, blocked on an Apple entitlement).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/services/notifeeService.ts` | `reclaim` option; awaited cancel before a `silent`/`reclaim` post, hoisted above `ensureNotifeeReady`; `fg-v2` channel at DEFAULT importance without vibration; `fg-v1` marked stale; absolute per-ride deadline; `ensureNotifeeReady` rejection no longer cached; `fullScreenAction` suppressed on `reclaim` | Make the handover a real cancel, and stop the re-post from extending the card or jolting the driver |
| `driver-app/hooks/useDriverDashboard.ts` | `_surfaceOfferNotification` returns a promise and accepts `reclaim`, with a post-handover liveness check; `consumePendingOffer` awaits the handover before ringing; new AppState audio-authority effect with `audioOwnerRef` | Make the handover reachable from every path and correctly ordered |
| `driver-app/__tests__/services/notifeeService.test.ts` | Channel assertions updated to `fg-v2`; 6 new tests (cancel-before-display ordering, no-cancel on first delivery, no-cancel when merely muted, post survives a rejected cancel, `reclaim` shape, deadline not extended across a handover) | Pin the regression and the no-silence guarantees |

## 7. Before / after

```ts
// BEFORE — useDriverDashboard.ts, consumePendingOffer
offerSound.play();                            // ring first
const offer = useDriverStore.getState().incomingRide;
if (offer) _surfaceOfferNotification(offer, true);   // fire-and-forget UPDATE

// AFTER
const offer = useDriverStore.getState().incomingRide;
if (offer) {
  await _surfaceOfferNotification(offer, true);      // cancel + re-post, awaited
  audioOwnerRef.current = { rideId: offer.ride_id, owner: 'app' };
}
offerSound.play();                            // ring only once the OS ring is gone
```

```ts
// BEFORE — notifeeService.ts
await ensureNotifeeReady();
const silent = opts?.silent === true;
// ... then post to the silent channel and hope the loop stops

// AFTER
const silent = opts?.silent === true;
const reclaim = opts?.reclaim === true;
if ((silent || reclaim) && Platform.OS === 'android') {
    try { await notifee.cancelNotification(RIDE_OFFER_NOTIFICATION_ID); } catch { /* post still runs */ }
}
await ensureNotifeeReady();
```

The behavioural addition with no "before" is the AppState re-election effect (see §3).

## 8. Rollback plan

driver-app ships **OTA** — `.github/workflows/eas-build.yml` publishes a JS-only update on every push to `main` touching `driver-app/**`. This change is JS-only (the `ride_offer` raw resource is already in the binary via `plugins/withRideOfferSound.js`), so **no EAS build is required and no store release is involved**.

- **Rollback:** `eas update:republish` the prior update group on `production` (and `android preview`). Reaches phones on next cold start, not mid-session. Precedent: `docs/change-log/2026-08-11-metro-rngh-renderer-shim.md`.
- **Canary:** publish via `workflow_dispatch` with `profile: preview` first — a push to `main` otherwise reaches 100% of live drivers on next launch, with no staged rollout configured in this repo.
- **Not available:** a server-side kill switch. The `offer_alert_audio_mode_android|ios` flag is specified in the plan but **not implemented in this phase**, so there is no way to revert this behaviour without republishing. That is a deliberate, stated gap — see §10.
- **Channel caveat:** `fg-v2` is created on first launch of the new bundle. A republish to the old bundle leaves the unused `fg-v2` channel on the device; harmless, but `fg-v1` will have been deleted, so the old bundle re-creates it.

## 9. Verification performed

- `npx jest __tests__/services/notifeeService.test.ts` — **25 passed** (19 pre-existing + 6 new).
- `npx jest __tests__/services/backgroundMessaging.android.test.ts __tests__/services/backgroundMessaging.test.ts` — **26 passed**, confirming the killed-app path is unchanged.
- `npx jest __tests__/app/driverDashboardScreen.test.tsx __tests__/hooks` — **7 suites, 94 passed**.
- `npx tsc --noEmit` — no errors in either changed file. Four errors are reported in `__tests__/components/CarMarker.test.tsx`; they belong to the concurrent Android car-marker change sitting in the same working tree (verified against `git diff -U0`: two of them are on lines that do not exist in `HEAD`), not to this change and not to `HEAD`. They will fail `mobile-dep-check.yml`'s `driver-app-dep-check` job, so they must be fixed on that change — not bundled into this commit.
- `npx eslint` on all three changed files — **no new errors**. The 6 errors reported are all at lines outside this change's hunks (verified against `git diff -U0`) and pre-date it.
- **No production build was run.** `driver-app` has no `build`/`typecheck` npm script; the OTA bundle is produced by the EAS workflow, not locally. `tsc --noEmit` + Jest is what was run, and it is explicitly *not* equivalent to a real bundle.

## 10. What was NOT verified

- **Nothing was run on a device or simulator.** This session has neither. Every claim about what is *audible* is derived from reading code, the Notifee AAR, and `expo-audio`'s Kotlin — not from hearing it. The plan's 19-cell device matrix is unrun and is the merge gate.
- **Whether the overlap is actually gone.** `FLAG_INSISTENT` is entirely OS-side; Jest can assert the notification *request* and the call ordering, never that audio started or stopped.
- **Whether the cancel-then-post flickers or re-peeks**, and whether DEFAULT importance fully suppresses it. Reasoned about, not screenshotted — driver-app has **no visual regression tooling at all** and this repo has **no audio regression tooling for any surface**.
- **OEM divergence.** If the original cause was OEM-specific, so is this fix's efficacy. Untested on Samsung One UI / Xiaomi / Oppo. Re-confirm on the exact handset where the double ring was observed.
- **The channel-immutability testing trap.** A device that already has `fg-v1` will keep showing the old behaviour until the new bundle creates `fg-v2`. A tester who does not clear app data (`adb shell pm clear com.spinr.driver`) may wrongly report the fix failed. At least one run must also be an *upgrade* over an existing install, since that is what live-test drivers experience.
- **Whether `USE_FULL_SCREEN_INTENT` is granted** on the test handsets (Android 14+ restricts it to calling/alarm apps), which changes how much the killed/locked case depends on the loop.
- **The new background `reclaim` post is the least-exercised new path** — it has unit coverage for its shape but no end-to-end exercise, and it is the one that could plausibly annoy a driver (a ring re-arming as they leave the app).
- **`useDriverDashboard` has no real behavioural coverage.** `__tests__/app/driverDashboardScreen.test.tsx` mocks the hook wholesale, which is CLAUDE.md's explicit "a stubbed-out component gives zero real coverage" case. The new AppState effect is therefore **untested by construction**; the plan's `offerAudioAuthority.test.ts` is not written yet.
- **No server-side kill switch exists for this behaviour** (see §8). Rollback requires a republish.
- **Unrelated in-flight work is present in the working tree.** `driver-app/components/CarMarker.tsx` and its test show modifications that are **not part of this change** and were not made by this session. They must not be staged with this commit.

## 11. Sign-off

- [x] Blast radius stated, not assumed — every caller of the changed functions enumerated in §4
- [x] Additive where it mattered — new `reclaim` option and new channel id rather than mutating existing behaviour or an immutable channel
- [x] Rollback plan is concrete (OTA republish) and stated before merge
- [x] Failure direction is explicitly toward noise, never silence
- [ ] **Device matrix unrun — this is the merge gate and it needs a human.** Three change-logs in this series shipped without it; this one must not.

---

## 12. Self-review (Codex-style) over this change's own diff

An adversarial re-read of everything written in this session, following the precedent of `2026-09-04-ride-offer-ringtone-self-review-fixes.md`. Four findings, all in code added above, all fixed in the same tree. **Two of them (F1, F3) were new silence/noise regressions introduced by this change** — the same class of defect this change exists to fix, which is exactly why the pass was worth doing.

### F1 — `reclaim` could produce total silence (introduced here, now fixed)

`displayRideOfferNotification` cancels before it posts. `await ensureNotifeeReady()` sat *between* the two, and it can reject (native error, permission race). On that path the loud notification was already cancelled and the function threw before posting, so nothing was on screen. For `silent` that degrades to "tone but no card" — tolerable. **For `reclaim` it is outright silence**: the caller has already run `offerSound.stop()`, so that post is the driver's only remaining alert.

Pre-existing code had the same `await` as its first statement, but with no cancel ahead of it there was nothing to lose — this change is what turned it into a silence path.

**Fix:** wrap it. Log loudly via `console.error` (never swallow, per CLAUDE.md) and attempt the post regardless. Android channels are created once and persist on the device, so a transient setup failure usually still leaves a postable channel, and the existing two-tier fallback around `displayNotification` covers a genuinely unpostable state. Pinned by a new test: a rejected `createChannel` still yields exactly one `displayNotification`.

### F2 — the pinned deadline outlived the offer (introduced here, now fixed)

The absolute-deadline fix in §3 cleared `rideOfferDeadline` in `dismissRideOfferNotification()` but **not** on the auto-dismiss path, where `scheduleRideOfferDismiss`'s timer calls `notifee.cancelNotification` directly (`notifeeService.ts:105`) and bypasses that function. So after an ignored offer timed out, an already-elapsed deadline survived for that `ride_id`. A later post for the **same ride** — which `utils/push_retry.py` does on a dispatch retry — then resolved `timeoutMs <= 0` and was dismissed before it ever rendered. The driver would never see the re-offer.

Only bites a payload with no `offer_expires_at`; the absolute branch recomputes and overwrites. **Fix:** clear the deadline in the timer callback too. Pinned by a test that fires the auto-dismiss and asserts a same-ride re-post still renders with a full timeout.

### F3 — iOS `inactive` was treated as a background handover (introduced here, now fixed)

The re-election effect derived `owner` as `next === 'active' ? 'app' : 'os'`, so **every** non-active state took the reclaim branch. iOS emits `'inactive'` for transient interruptions — pulling the notification shade down, a call banner, Control Centre — and RN can report `'unknown'` early. So on iOS, a driver pulling the shade down **to look at the offer they were deciding on** would have stopped the in-app tone and posted a loud local card. And because the reclaim cancel is Android-gated while the *post* was not, that card would duplicate the backend's already-delivered APNs alert **and chime a second time** — re-creating the iOS-1 defect this plan documents, on a new trigger.

**Fix:** two guards. Ignore any state that is not `'active'` or `'background'`, and gate the reclaim post to Android (iOS has no insistent loop to reclaim, and its remote alert is already the single source). iOS backgrounded therefore keeps exactly the behaviour it had before this effect existed.

### F4 — test-mock leak (hygiene)

The two ordering tests used `mockImplementation`, and `jest.clearAllMocks()` in `beforeEach` resets call records but **not** implementations. The stubs therefore persisted into every later test in the file, pushing into a dead closure array. Harmless today (they resolve `undefined`, same as the original `mockResolvedValue`), but a live trap for the next test added after them. Converted to `mockImplementationOnce`.

### Reviewed and found no fix needed

- **Double handover on foreground.** `consumePendingOffer`'s listener and the re-election effect both fire on `'active'`. Traced: if the WS path claimed the offer, the effect handles it and `consumePendingRideOffer()` returns `false` (`rideState !== 'idle'`), so it does not repeat; if the offer is only in storage, `rideState` is `'idle'` so the effect returns early and `consumePendingOffer` owns it. Exactly one handover on both paths.
- **`audioOwnerRef` set from `consumePendingOffer` at mount**, when `AppState.currentState` may not be `'active'` yet. Correct as written — that path really did perform the handover, so marking `'app'` and skipping the subsequent `'active'` is the intent, not a miss.
- **The post-handover liveness check** cannot strand a newly-arrived offer: a new offer leaves `rideState === 'ride_offered'`, so it does not dismiss, and the shared notification id means the new post overwrites.
- **The CarMarker prefetch probe** (§11 of the car-marker log): re-probing on remount is cache-warm and cheap; a cache-miss-while-offline failing to the bundled car matches the pre-existing intent documented at `:715-717`; the `http(s)` narrowing prevents a local-source false negative.

### Verification after the self-review fixes

- `npx jest __tests__/services/notifeeService.test.ts --no-coverage` — **27 passed** (2 new).
- `npx jest` across CarMarker, both backgroundMessaging suites, driverDashboardScreen and `__tests__/hooks` — **10 suites, 149 passed**.
- `npx tsc --noEmit` — **0 errors** project-wide.
- `npx eslint` over all five changed files — `notifeeService.ts` and both test files are error-free; the 11 errors reported in `CarMarker.tsx` and `useDriverDashboard.ts` are all pre-existing lines outside every hunk in this session's work.

### Still not verified

Unchanged from §10: none of this has run on a device. F1 and F2 in particular are failure-path fixes whose triggers (a rejecting `createChannel`, a `push_retry` re-offer of the same ride with no `offer_expires_at`) are pinned by unit tests but have never been observed in the field.
