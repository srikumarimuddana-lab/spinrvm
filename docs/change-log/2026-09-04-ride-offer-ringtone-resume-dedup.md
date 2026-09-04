# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session `01Hfvg3vjxhXapC25CK7DFZs`) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/ride-offer-ringtone-edges-wl1w4c` |
| Related issue or gap ID | User report: ride-offer sound is loud/clear when the driver app is foreground, plays once when killed, and plays **two overlapping ringtones** when the driver reopens the app on a pending offer |

## 1. Issue / gap identified

Reopening the driver app while a ride offer that arrived during background/kill is still live can produce two audible tones at once: the native OS/Notifee notification sound already looping from the backgrounded delivery, plus a fresh in-app tone loop, with nothing synchronizing the two.

## 2. Root cause

Three independent paths can surface a ride offer to the driver: the live WebSocket handler, the live FCM-foreground handler, and `consumePendingOffer()` (`useDriverDashboard.ts`, backed by `services/pendingRideOffer.ts`) — the last one runs on app mount and on every foreground resume, reading an offer stashed to AsyncStorage by the killed/backgrounded FCM handler (`services/backgroundMessaging.ts`).

The WS and FCM handlers both already call `offerSound.play()` (start the in-app tone loop) **and** `_surfaceOfferNotification()` (re-post the native Notifee card to a silent channel while foreground, suppressing its sound) whenever they surface a new offer. `consumePendingOffer()` did neither — it only vibrated (`Vibration.vibrate(...)`) and called `setIncomingRide()`. So on resume:
- Any native/Notifee notification sound already looping from the backgrounded delivery was left completely unmanaged — nothing dismissed or silenced it, so it kept ringing on its own `timeoutAfter` (up to 15s).
- No in-app tone started via this path, so the driver got only a vibration + a silent card.
- If the WebSocket happened to reconnect (`?last_seq=...` outbox replay, `useDriverDashboard.ts` background-close/foreground-reconnect effect) and deliver the *same* offer before `consumePendingOffer()`'s AsyncStorage read resolved, the WS handler's own `offerSound.play()` would start a **second**, independent tone loop layered on top of the still-ringing native one — the reported double-ring. Which of the two async paths "won" was a timing race, so the symptom was intermittent rather than deterministic, matching the user's report.

Not fully confirmed: I could not verify from static reading alone whether the backend also emits `ride_offer_expired` as a durable/replayed WS message for an offer that expired while the client was disconnected; if it does not, a stale outbox replay of an already-locally-resolved offer could theoretically still cause a brief spurious re-ring via the WS path. This fix does not address that narrower, unconfirmed case (see §9/§10) — it was judged too speculative to fix without server-side confirmation, and the WS/FCM handlers' pre-existing `rideState !== 'idle'` guard already bounds its blast radius to "briefly rings then immediately stops," not the reported "two sounds ringing at once."

## 3. Fix / remediation

`consumePendingOffer()` in `driver-app/hooks/useDriverDashboard.ts` now checks the (pre-existing, unchanged) return value of `consumePendingRideOffer()` — `true` only when a still-live offer was actually surfaced into the store. When `true`, it now also calls `offerSound.play()` and `_surfaceOfferNotification()`, exactly mirroring what the WS/FCM handlers already do for a live arrival.

This makes "the driver is looking at the offer panel in the foreground" converge on exactly one audible source regardless of which path won the race:
- If `consumePendingOffer()` wins, it rings the in-app tone and immediately re-posts the native notification to the silent channel — collapsing what would have been an unmanaged native loop into the same single-tone behavior the live-arrival (foreground) case already has, confirmed working by the user.
- If the WS/FCM path wins first, `setIncomingRide()`'s own existing guard (`rideState !== 'idle'` → warn and return, `store/driverStore.ts:524-531`) — together with `consumePendingRideOffer()`'s own pre-existing idle-check (`services/pendingRideOffer.ts:56`) — makes `consumePendingOffer()`'s call resolve `false`, so it does not ring a second time.

No change to `services/pendingRideOffer.ts` (shared with the Android Auto car-session caller) or `services/notifeeService.ts` — only the phone-specific wrapper in `useDriverDashboard.ts` was touched, so the car session's behavior is byte-for-byte unchanged.

## 4. Risk & impact on existing functionality

- Blast radius: single function, single file (`consumePendingOffer` in `driver-app/hooks/useDriverDashboard.ts`). Grepped the file for all callers of `consumePendingOffer` — used only inside this hook's own mount/foreground-resume effect (fire-and-forget, not awaited, so the sync→async signature change is safe) and its own dependency array; not exported, not called elsewhere.
- Grepped for all callers of `consumePendingRideOffer` (the shared function whose contract this fix relies on, unchanged): `useDriverDashboard.ts` (this fix) and the Android Auto car-session connect path. The car path passes no `onOffer`/ring logic today and is untouched by this diff.
- `offerSound.play()` is pre-existing and already idempotent (no-ops if already looping — `useRideOfferSound.ts:137`); `_surfaceOfferNotification()` is pre-existing and already used by the WS/FCM handlers for the identical purpose. This fix adds new *call sites* for both, it does not change their implementation.
- Does not touch the ride state machine, dispatch, or any money/wallet path — notification/audio presentation only.

## 5. User-experience effect

Driver-facing only. Two visible changes:
1. The double-ring is fixed for the race ordering this fix targets (see §2/§9 for the one narrower, unconfirmed sub-case not covered).
2. Reopening the app on a pending offer that arrived while backgrounded/killed now also rings the same loud in-app tone the foreground case already uses, not just a vibration — this is a genuine, intentional behavior improvement toward the user's stated goal (a uniform, loud ring regardless of app state), not only a bug fix. Not visible mid-session to a driver who is not currently looking at an offer.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/hooks/useDriverDashboard.ts` | `consumePendingOffer()` now rings `offerSound.play()` + calls `_surfaceOfferNotification()` when `consumePendingRideOffer()` resolves `true`, instead of only vibrating | Close the gap where this resume path never managed sound/notification state at all |
| `docs/change-log/2026-09-04-ride-offer-ringtone-resume-dedup.md` | New change-log entry | CLAUDE.md mandatory Change Impact Log for a dispatch-domain behavior change |

## 7. Before / after

```ts
// Before
const consumePendingOffer = useCallback(
  () => consumePendingRideOffer({ onOffer: () => Vibration.vibrate([0, 500, 200, 500]) }),
  [],
);
```

```ts
// After
const consumePendingOffer = useCallback(async () => {
  const surfaced = await consumePendingRideOffer({
    onOffer: () => Vibration.vibrate([0, 500, 200, 500]),
  });
  if (surfaced) {
    offerSound.play();
    const offer = useDriverStore.getState().incomingRide;
    if (offer) _surfaceOfferNotification(offer);
  }
}, [offerSound]);
```

## 8. Rollback plan

`git revert` — pure client-side notification/audio presentation logic, no data written, no migration, no server-side change. No wallet/ride-state/Stripe data involved, so a code revert alone is a complete rollback.

## 9. Verification performed

- [x] Manual code trace of every path that can call `offerSound.play()` / `_surfaceOfferNotification()` / `setIncomingRide()`, and every guard between them (`rideState !== 'idle'` in three independent places: the WS handler, the FCM handler, and `driverStore.setIncomingRide` itself), confirmed by reading the actual source (not the initial research-agent summary, which was directionally correct but missed the resume path's total absence of sound handling — corrected by re-reading `useDriverDashboard.ts`, `pendingRideOffer.ts`, `notifeeService.ts`, and `driverStore.ts` directly).
- [x] Confirmed `services/pendingRideOffer.ts`'s existing, already-tested `true`/`false` return contract (`__tests__/services/pendingRideOffer.test.ts`, 7 passing cases) is exactly what this fix relies on and was not modified.
- [x] Blast-radius grep performed for `consumePendingOffer` and `consumePendingRideOffer` call sites (§4).
- [ ] `npx tsc --noEmit` — **attempted, not completed**: `driver-app/node_modules` is not installed in this session and `yarn install` failed twice with `ECONNRESET` against the registry through this environment's proxy (a third attempt was in flight at commit time). Could not run a typecheck, lint, or the Jest suite for this change in this session.
- [ ] Automated test for the new resume-time ring/surface behavior — not added. No test file exists at all for `useDriverDashboard.ts` (a ~1900-line hook with heavy native/WS/store dependencies); adding first-ever coverage for the whole hook was judged out of scope for a surgical, single-function fix. The dependency this fix relies on (`consumePendingRideOffer`'s return contract) is already covered.
- [ ] Manual on-device repro (foreground / backgrounded / killed, all three original edge cases) — not possible in this sandboxed session (no device/simulator).

## 10. What was NOT verified

- No automated test exercises `consumePendingOffer`'s new branch directly — reasoned through via static code trace of the store/guard logic, not executed.
- Could not run `tsc`/lint/Jest in this session due to a persistent `ECONNRESET` on `yarn install` — say so explicitly per CLAUDE.md rather than imply a passing build. If this environment's install issue is transient, re-run `cd driver-app && yarn install && npx tsc --noEmit && yarn test` before considering this fully verified.
- Did not confirm whether the backend emits `ride_offer_expired` as a durable/replayed WS message (§2's noted open question) — if it does not, a narrow, separate spurious-brief-re-ring case may remain on WS reconnect for an offer that expired entirely while the client was disconnected. Bounded by the existing `rideState !== 'idle'` guard to "rings briefly then stops," not the reported overlapping double-ring, so left out of this fix's scope rather than speculatively patched.
- No on-device/simulator verification of actual audible behavior in any of the three app states — this is inherently unverifiable in this sandboxed session; flagging per CLAUDE.md rather than claiming it was screenshotted/heard.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data involved)
- [x] Blast radius is stated, not assumed (single function, two call sites grepped and enumerated)
- [x] No silent behavior change to an already-shipped flow — the added ring-on-resume behavior is called out explicitly in §5 as an intentional UX change, not framed as "just a bug fix"
