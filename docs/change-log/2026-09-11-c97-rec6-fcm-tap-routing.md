# Change Impact & Risk Log — C97 rec #6: wire Firebase's own notification-tap detection into driver-app routing

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | driver-app, shared |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `mvapps/blissful-thompson-u14frt` |
| Related issue or gap ID | ACTION_ITEMS.md C97, recommendation #6 |

## 1. Issue / gap identified

The original audit (`docs/audit/2026-09-10-driver-app-notification-delivery-audit.md`) flagged
`expo-notifications`' tap-handling code in `driver-app/app/_layout.tsx` as "misleading dead code,
low priority, cleanup." Going deeper on it found that's an understatement: the *display* handler
(`setNotificationHandler`) is dead for FCM messages but still live for one local notification; the
*tap-routing* logic (`addNotificationResponseReceivedListener` /
`getInitialNotificationResponseAsync`) — written to deep-link `chat_message`, `lost_and_found(_message)`,
and `license_backfill_prompt` push taps to their specific screens — can only fire for notifications
`expo-notifications` itself posted, and those three push types are never posted that way.

## 2. Root cause

On Android, `@react-native-firebase/messaging`'s manifest service (no explicit `android:priority`,
defaults to 0) takes priority over `expo-notifications`' service (`android:priority="-1"`) for the
same `com.google.firebase.MESSAGING_EVENT` intent-filter — confirmed by directly reading both
packages' `AndroidManifest.xml`. RNFirebase, not expo-notifications, is what actually receives and
(for a `notification`-block message) gets the OS to auto-display these pushes. `chat_message`,
`lost_and_found`/`lost_and_found_message`, and `license_backfill_prompt` are not sent data-only
(`backend/features.py`'s `is_data_only` gate covers only `new_ride_assignment`/`live_activity`), so
they carry a real FCM `notification` block. A driver tapping one of those from the lock screen or
notification tray while the app is backgrounded or killed generates a tap event that
expo-notifications' listeners are structurally unable to see — no equivalent RNFirebase-side tap
listener (`onNotificationOpenedApp` / `getInitialNotification`) existed anywhere in the codebase
before this change (confirmed via repo-wide grep — zero hits before, one hit after: the log line
this fix adds).

Net effect (plausible, not device-confirmed): a driver who isn't looking at the app and taps one of
these three push types most likely lands on a generic screen instead of the intended
chat/case/profile screen.

## 3. Fix / remediation

- Added `onNotificationOpenedApp(handler)` and `getInitialNotification()` to
  `shared/services/firebase.ts`, wrapping RNFirebase's own v22+ modular tap-detection APIs (same
  file convention as the existing `onForegroundMessage`/`onTokenRefresh` exports).
- Wired both into `driver-app/app/_layout.tsx`'s `usePushNotificationRouter`:
  - A new effect calling `onNotificationOpenedApp` for the backgrounded-tap case (app already
    running, brought to foreground by the tap).
  - The existing killed-state effect now checks Firebase's `getInitialNotification()` first (the
    common real-world case for any push type except the local welcome nudge), falling back to
    expo-notifications' own `getInitialNotificationResponseAsync()` for the one notification that
    library still posts itself.
- Extracted the shared 4-branch routing switch (previously duplicated inline in two places, about
  to become four) into `driver-app/utils/pushNotificationRouting.ts` — a small, dependency-free,
  directly unit-tested module — rather than quadruplicating the same if/else chain.
- Corrected the misleading module-level comment on `setNotificationHandler` (it claimed to be
  necessary for FCM foreground display; it isn't — it's necessary for the one notification
  `expo-notifications` still posts itself, `DriverIdlePanel.tsx`'s one-time document-upload nudge).
- Did **not** remove the `expo-notifications` tap-routing code — it is still the only path for that
  one local notification, so removing it (the other half of the original recommendation's either/or)
  would have broken a genuinely-live, if narrow, case.

## 4. Risk & impact on existing functionality

- **Blast radius: `driver-app/app/_layout.tsx`, `shared/services/firebase.ts`, plus the two new
  files.** Grepped for every other consumer of `shared/services/firebase.ts`'s exports — rider-app
  imports the same file but only uses `onForegroundMessage`/`onTokenRefresh`/permission helpers,
  none of which changed; the two new exports are additive and unused by rider-app today.
- **Purely additive on the listener side**: the two new `useEffect`s in `_layout.tsx` are new
  registrations, not modifications to the existing expo-notifications listeners, which are
  untouched in behavior (only the shared routing logic they call was moved, not changed — the
  extracted `routePushNotificationTap` is byte-for-byte the same branches as before, now unit-tested
  directly instead of only reachable through the whole file).
- **No interaction with ride state, money, or insurance periods** — this is push-notification tap
  routing only. Ride-offer notifications are unaffected: they're data-only and handled entirely by
  a separate, confirmed-working system (Notifee), and the `new_ride_assignment` branch in the
  shared router is redundant-but-harmless for the Firebase listeners (that branch structurally
  can't fire there, since ride offers never get an OS-auto-displayed notification for RNFirebase's
  tap APIs to catch).
- **Failure mode if something is subtly wrong**: worst case, a driver taps a chat/lost-and-found/
  license-reminder notification from background or killed state and lands on the wrong screen (or
  the fallback `/driver/notifications` screen) — the same failure mode that already existed before
  this fix (a driver already couldn't reliably deep-link from these taps). This fix cannot make
  that case worse than it already was; it can only leave it unchanged (if the new listeners don't
  fire as expected) or fix it (if they do).
- **No feature flag.** No client-side remote-config mechanism exists in driver-app for gating a
  single notification listener's registration (the repo's `app_settings`-in-DB pattern is
  backend-read, not wired into driver-app's notification setup), and this change doesn't touch
  backend routes/config that could carry one. Given the additive-only blast radius above, a flag
  was judged unnecessary here rather than built new for this one change — flagged explicitly per
  CLAUDE.md's release-gate #3 rather than silently skipped.

## 5. User-experience effect

- **Driver-facing.** A driver who taps a chat-message, lost-and-found, or license-reminder push
  notification while the app is backgrounded or killed should now land on the specific
  chat/case/profile screen instead of a generic one. Not visible mid-session in any other way —
  this only fires on a notification tap, never during active in-app use.
- No copy or notification content changed — only where a tap on an existing notification routes to.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/services/firebase.ts` | New `onNotificationOpenedApp`/`getInitialNotification` exports wrapping RNFirebase's modular tap-detection APIs | Give the app a way to detect taps on FCM-auto-displayed notifications, which expo-notifications structurally cannot see |
| `driver-app/app/_layout.tsx` | New background-tap effect; killed-state effect now checks Firebase first; routing logic extracted to a shared helper; misleading comment corrected | Wire the new detection into the existing router; stop duplicating the routing switch a 3rd/4th time; fix the C97 rec #6 misleading comment |
| `driver-app/utils/pushNotificationRouting.ts` | New — extracted `routePushNotificationTap` | Dependency-free, directly unit-testable routing decision, reused by all 4 listeners |
| `driver-app/__tests__/utils/pushNotificationRouting.test.ts` | New — 9 tests covering every branch | Cover the routing decision now that it's isolated and testable |
| `driver-app/__mocks__/@shared/services/firebase.js` | Added mocks for the 2 new exports | Keep the existing Jest manual-mock complete so any test importing `_layout.tsx` doesn't hit `undefined is not a function` |
| `ACTION_ITEMS.md` | C97 addendum; recommendation #6 marked done | Record the finding and the fix |
| `docs/change-log/2026-09-11-c97-rec6-fcm-tap-routing.md` | New — this file | Change Impact Log |

## 7. Before / after

```tsx
// Before — killed-state effect only ever checked expo-notifications'
// own API, which is never populated by a real FCM push tap:
useEffect(() => {
  if (!canUseNotifications || !Notifications) return;
  let timer: ReturnType<typeof setTimeout>;
  (async () => {
    try {
      const response = await Notifications.getInitialNotificationResponseAsync?.();
      if (response?.notification?.request?.content?.data) {
        const data = response.notification.request.content.data;
        timer = setTimeout(() => { /* inline routing switch */ }, 100);
      }
    } catch (e) { /* ... */ }
  })();
  return () => clearTimeout(timer);
}, [router]);
```

```tsx
// After — Firebase's own API checked first (the common real-world case),
// falling back to expo-notifications' for the one notification it still posts:
useEffect(() => {
  let timer: ReturnType<typeof setTimeout>;
  let cancelled = false;
  (async () => {
    try {
      const fbMessage = await getInitialNotification();
      if (cancelled) return;
      if (fbMessage?.data) {
        timer = setTimeout(() => routePushNotificationTap(router, fbMessage.data), 100);
        return;
      }
    } catch (e) { /* ... */ }
    if (!canUseNotifications || !Notifications) return;
    try {
      const response = await Notifications.getInitialNotificationResponseAsync?.();
      if (cancelled) return;
      if (response?.notification?.request?.content?.data) {
        timer = setTimeout(() => routePushNotificationTap(router, response.notification.request.content.data), 100);
      }
    } catch (e) { /* ... */ }
  })();
  return () => { cancelled = true; clearTimeout(timer); };
}, [router]);
```

## 8. Rollback plan

`git-revert-safe` — every change is code-only (a new client listener registration, a moved-not-changed
routing helper, and doc updates); nothing persists data, calls a new backend endpoint, or writes to
any table. A plain `git revert` fully restores the prior behavior with no data cleanup needed. The
`shared/services/firebase.ts` additions are net-new exports with no existing caller to break if
reverted.

## 9. Verification performed

- [x] Automated tests run — unit only: `npx jest __tests__/utils/pushNotificationRouting.test.ts`
  (9/9 new tests passing), full driver-app suite `npx jest` (141 suites, 1592/1592 tests passing,
  no regressions). `npx tsc --noEmit` in `driver-app/` — clean, zero errors. `npx eslint` on all
  changed files — zero errors (one pre-existing, unrelated warning at an untouched line further
  down `_layout.tsx`, confirmed via `git diff` to predate this change).
- [x] Blast-radius grep performed — confirmed rider-app's only use of `shared/services/firebase.ts`
  is `onForegroundMessage`/`onTokenRefresh`/permission helpers, unaffected by the two new exports;
  confirmed zero pre-existing use of `onNotificationOpenedApp`/`getInitialNotification`/
  `NotificationForwarderActivity` anywhere in the codebase or its tests before this change.
- [x] Reviewed against relevant CLAUDE.md conventions — this is additive (release gate #2), not a
  ride-state/money change (gate #4 doesn't apply), and is a real, disclosed behavior change to an
  already-shipped screen's tap-routing (gate #5 — see "User-experience effect" above).
- [x] Escalated before building: since this grew from "cleanup" to new, untested, driver-facing
  notification code, `AskUserQuestion` was used to let the user choose between (a) a safe
  comment-only fix plus a sharper backlog item, (b) building the full fix now despite no device to
  test on, or (c) holding for a device pass. The user explicitly chose (b).

## What was NOT verified

- **Not tested on a real Android or iOS device.** This is the central, load-bearing caveat: the
  entire fix rests on RNFirebase's documented `onNotificationOpenedApp`/`getInitialNotification`
  behavior actually firing as expected for a real tapped notification, in this exact app's build
  configuration. This exact area of the codebase (Android manifest-priority interaction between
  two notification libraries) already produced one subtle, previously-undetected bug — the finding
  this whole investigation started from — so "should work per the platform docs" is not the same
  confidence level as a confirmed on-device test. No QA pass, emulator run, or TestFlight/internal-
  track build was available in this sandbox to close that gap.
- **`shared`'s own standalone `npx tsc --noEmit`** reports pre-existing type-resolution errors
  against `@react-native-firebase/messaging` for every method in `firebase.ts`, old and new alike
  (confirmed via `git stash`: 33 errors before this change, 37 after — the +4 are the two new
  exports' two errors each, following the exact same pre-existing pattern as every other method in
  the file). `driver-app`'s own `npx tsc --noEmit` — the one that reflects the real app build's
  module resolution — is clean. Not investigated further since it's pre-existing and out of this
  fix's scope.
- **iOS notification-tap behavior specifically** was reasoned about via the same RNFirebase
  modular API (which is cross-platform), not verified against iOS's own APNs/UNUserNotificationCenter
  tap-delivery semantics on a real device — a second, independent unverified assumption layered on
  top of the primary one above.
- **No visual/interaction regression tooling exists for driver-app at all** (per CLAUDE.md — this
  is one of the two surfaces, alongside rider-app, with zero automated coverage of this kind), so
  this disclosure is mandatory, not optional, for any driver-app change and doubly so for one this
  hard to verify locally.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data written anywhere)
- [x] Blast radius is stated, not assumed (additive-only; rider-app's use of the shared module
  confirmed unaffected)
- [x] No silent behavior change — the UX effect is named, and the untested-on-device status is
  disclosed as plainly as the fix itself, not smoothed over
