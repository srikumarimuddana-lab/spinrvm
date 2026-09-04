# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session `01Hfvg3vjxhXapC25CK7DFZs`) |
| Surface(s) | driver-app, backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | [PR #4981](https://github.com/srikumarimuddana-lab/spinrvm/pull/4981), branch `claude/ride-offer-ringtone-edges-wl1w4c` |
| Related issue or gap ID | Self-review (Codex-style) pass over this branch's own two prior commits — findings P1/P2/P4/P5 |

## 1. Issue / gap identified

An adversarial re-read of this branch's own first commit (`c3551fe`) found two regressions it introduced and two adjacent defects:

- **P1 (regression):** the new resume path could re-arm the **loud** notification channel, reintroducing the exact double-ring the commit exists to fix.
- **P2 (regression):** the resume path rings the tone but never brings the offer panel to the front, unlike the two sibling paths that do.
- **P4:** `settings` imported unaliased into a module that already binds that name to a different object.
- **P5 (pre-existing, in the rewritten function):** resume-path vibration ignored the driver's vibration preference.

## 2. Root cause

**P1.** `_surfaceOfferNotification()` decides whether the Notifee card is silent by inferring from app state: `const silent = AppState.currentState === 'active'`. That inference is only sound for the two callers it was written for — the WS handler needs a live socket, and the FCM foreground listener only fires while foregrounded, so both are genuinely `'active'`. The resume path added in `c3551fe` is the first caller that can run **at mount**: its effect is gated only on `if (!user) return`, so it fires as soon as auth hydrates during app launch, when `AppState.currentState` is not yet `'active'` (iOS passes through `inactive` on launch; RN can report `unknown`/`null` early on both platforms). With `silent` false, `displayRideOfferNotification` posts to `RIDE_OFFER_CHANNEL_ID` with `sound: 'ride_offer'` and `loopSound: true` — a loud looping native notification starting at the same moment as the in-app tone. Worse in the cold-start-via-tap case: `backgroundMessaging.ts:320` already dismissed the card before launching, so this does not silence an existing notification, it creates a new loud one.

**P2.** Both sibling handlers call `router.replace('/driver/')` when surfacing an offer (`useDriverDashboard.ts:939` WS, `:1792` FCM); the resume path did not. `useDriverDashboard` is mounted only in `app/driver/(tabs)/index.tsx`, but a tab sibling stays mounted once visited, so a driver resuming on another tab could hear a looping tone with no offer panel in view. Tolerable when the path only vibrated; not once it rings.

**P4.** `features.py` already binds `settings` locally to the DB app_settings **dict** (`settings = await get_app_settings()` at `:1594` and `:1813`, used as `settings.get(...)`). Importing the pydantic `Settings` object under the same bare name puts two different APIs on one identifier. Inert today (both locals are assigned immediately before use, and Python's function-scoping makes them locals for their whole function regardless), but a foot-gun for the next edit. `services/payment_service.py:40` already established the convention for exactly this reason.

**P5.** The resume path's `onOffer` callback called `Vibration.vibrate(...)` unconditionally; both sibling handlers gate on `useAlertPrefsStore.getState().vibration`.

## 3. Fix / remediation

- **P1:** `_surfaceOfferNotification(data, forceSilent = false)` — `silent` is now `forceSilent || AppState.currentState === 'active'`. The resume path passes `true`, because the in-app tone is authoritative there by construction (`offerSound.play()` fires on the preceding line). The WS/FCM callers are unchanged and keep the AppState inference, which is correct for them.
- **P2:** resume path now calls `router.replace('/driver/' as any)`, matching both siblings.
- **P4:** `features.py` imports `settings as app_config` in **both** arms of the dual-import block; the one usage is now `app_config.IOS_CRITICAL_ALERTS_ENABLED`. `push_retry.py` has no such collision (verified by grep) and keeps the plain name.
- **P5:** resume-path vibration is now gated on the driver's vibration preference.

Also corrected in this commit: the sibling change-log `2026-09-04-ios-critical-alerts-scaffolding.md` overstated what the iOS scaffolding delivers (it omitted the missing runtime `criticalAlert` permission request). Both that entry and the flag's definition in `backend/core/config.py` now enumerate all three prerequisites.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated**. `_surfaceOfferNotification` is module-private to `useDriverDashboard.ts`; grepped all three of its call sites (`:938` WS, `:1791` FCM, and the resume path). The two pre-existing callers pass no second argument, so `forceSilent` defaults `false` and their behavior is **byte-for-byte unchanged** — the new parameter is purely additive.
- `router.replace('/driver/')` on the resume path is the same call the two sibling paths already make on the same event, so it introduces no navigation target that a live offer didn't already produce.
- P4 is a rename of a module-level binding with exactly one usage; the dual-import pattern is preserved in both arms (a formatter stripped the `except ImportError` line mid-edit — caught and restored, then re-verified: `grep -c` returns 2).
- No change to the ride state machine, dispatch, money, or any persisted state.

## 5. User-experience effect

Driver-facing, and all three are corrections to behavior this branch itself introduced hours earlier (nothing here has shipped to a driver yet):
1. The cold-start double-ring the branch was meant to fix can no longer be re-armed by launch-time app state.
2. Resuming on a pending offer now brings the offer panel to the front instead of ringing behind another tab.
3. A driver who disabled vibration is no longer buzzed on resume.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/hooks/useDriverDashboard.ts` | `_surfaceOfferNotification` gains `forceSilent`; resume path passes it, adds `router.replace`, gates vibration on the alert pref | P1, P2, P5 |
| `backend/features.py` | `settings` → `settings as app_config` in both dual-import arms + its one usage; comment notes the third iOS prerequisite | P4, P3 |
| `backend/core/config.py` | Flag comment now enumerates all three prerequisites, including the missing runtime `criticalAlert` request | P3 |
| `docs/change-log/2026-09-04-ios-critical-alerts-scaffolding.md` | Correction block appended | P3 |
| `docs/change-log/2026-09-04-ride-offer-ringtone-self-review-fixes.md` | This entry | — |

## 7. Before / after

```ts
// Before — silence inferred from app state even at mount
const silent = AppState.currentState === 'active';
...
if (offer) _surfaceOfferNotification(offer);
```

```ts
// After — the caller that knows the tone is playing says so
const silent = forceSilent || AppState.currentState === 'active';
...
if (offer) _surfaceOfferNotification(offer, true);
router.replace('/driver/' as any);
```

## 8. Rollback plan

`git revert` — client-side notification/audio presentation plus one backend identifier rename. No data written, no migration, no schema or wire-format change (`IOS_CRITICAL_ALERTS_ENABLED` still defaults `False`, so the APNs payload is still byte-identical to pre-branch).

## 9. Verification performed

- [x] Adversarial self-review of the branch's own diff (the pass that produced P1–P5), with every claim checked against source rather than memory — which is how P1 was found and how the initially-suspected "unbounded ringing" hypothesis was **disproved** (the centralized `if (!incomingRide) offerSound.stop()` effect at `useDriverDashboard.ts:1135` already covers accept/decline/expire/cancel).
- [x] Grepped all three `_surfaceOfferNotification` call sites to confirm the new parameter is additive and the two existing callers are unaffected.
- [x] Confirmed `useDriverDashboard` is mounted only in `app/driver/(tabs)/index.tsx` (P2's premise) and that `push_retry.py` has no `settings` collision (P4's scope).
- [x] `python3 -c "import ast; ast.parse(...)"` clean on all three edited backend files.
- [x] Re-verified the dual-import pattern survived the formatter (`grep -c "core.config import settings as app_config"` → 2, one per arm).
- [ ] `tsc` / `jest` / `pytest` — **still not runnable in this session**; `yarn install` fails with `ECONNRESET` and `pip install` with "No matching distribution found" (this environment's package-registry egress is blocked, confirmed across multiple attempts and both package managers). CI's own `driver-app-test` job (TypeScript check + jest) is the real gate for the TS change and runs on this push.

## 10. What was NOT verified

- P1's race is **not proven to fire** — I could not execute the app to observe `AppState.currentState` during launch. The fix is applied because it is deterministic and free (the caller that knows the tone is playing states it, rather than inferring), not because the race was reproduced. If it never fired, this change is a no-op; if it did, it was the reported bug.
- No automated test covers the resume path — `useDriverDashboard.ts` has no test file at all (pre-existing gap for a ~1900-line hook, not introduced here).
- No on-device verification of any of the three driver-visible behaviors, in any app state. Unchanged from the sibling entries: this session has no simulator or device.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data involved)
- [x] Blast radius is stated, not assumed (all three call sites of the changed function enumerated; default-argument behavior traced)
- [x] No silent behavior change — all three driver-visible effects are listed in §5, including the one (P5) that is a pre-existing bug rather than a regression from this branch
