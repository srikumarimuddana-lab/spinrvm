# Driver-app GPS capture hardening (single-writer, capture-before-filter, backoff, retention)

**Date:** 2026-08-18
**Surface:** driver-app location capture (rides/insurance/safety-critical), 14 commits
**Trigger:** ride SPR-PE7TTB lost 83% of its trip GPS while the phone's GPS was healthy (Android Auto displayed the car live the whole time).

## Issue/gap identified
Five compounding client-side loss modes: (1) the Android Auto display task and the dispatch tracking task cannibalized each other at the native layer — expo-location serves ALL location tasks from ONE shared Android service (stopping either strips the other's foreground promotion; the AA task's `killServiceOnDestroy:true` contaminated the shared instance so swipe-from-recents killed on-trip tracking) and a static native dedup silently discards one consumer's copy of every fix while two tasks are registered; (2) client integrity checks dropped fixes BEFORE the durable outbox, and their shared mutable state let the 2 s AA watcher falsely teleport-reject legitimate trip fixes; (3) no flush backoff — the background task hammered a down backend every ~4 s; (4) sign-out destroyed unacknowledged points (SGI/billing evidence); (5) the outbox had no TTL/cap and its quarantine was write-only.

## Root cause
The Aug 17 Android Auto feature introduced a second expo-location task, triggering long-standing library behavior (single shared `LocationTaskService`; companion-object `sLastTimestamp` dedup across consumers). The mutual-exclusion "piggyback" guard was racy against the 60 s car-session tick. Independently, capture paths were filtered client-side before persistence, inverting the capture-before-filter principle.

## Fix/remediation (by commit)
1. Per-producer integrity checkers (`createLocationIntegrityChecker`), registry-wide reset.
2–4. Capture-before-filter in the background handler, dashboard watcher + GPS heartbeat, and per-producer instance in the AA task (whose display-only gate correctly remains).
5. Sign-out sweeps every producer's integrity baseline.
6. Exponential flush backoff (5 s ×2 → 5 min, ±20% jitter); only the ride-completion flush bypasses; health banner stays degraded through the window.
7–9. FIFO `locationTaskArbiter` serializing all task start/stops; stop-car-BEFORE-promote order fix; `reassertDispatchTask` re-promotes the shared service after every car-task stop while dispatch runs; `killServiceOnDestroy` removed; factually-wrong header comment rewritten.
10. 60 s in-handler self-heal re-asserts dispatch options (cross-context backstop).
11. `purgeAll` preserves un-flushed points into quarantine (`signout_unflushed`) instead of deleting.
12–13. Retention prune: quarantine TTL 14 d + cap 5 k; sessions closed >7 d expire (points preserved as `expired_unflushed`); outbox soft cap 50 k evicting oldest CLOSED-session rows only.
14. Capture-drop telemetry (counts only) in RecorderHealth + one non-fatal per affected ride.

## Risk & impact on existing functionality
- **Server ingestion blast radius** (`/drivers/location-batch`, `utils/breadcrumbs.py`, `utils/trip_distance.py`): spoof-suspect points (impossible speed/teleport/mocked) now reach the server instead of being client-dropped. The v2 point already carries `mocked`; settlement's segment caps (≤5 km, ≤150 km/h, ≤300 s gaps) and the route finalizer's rejection ladder own billing/display quality — billed distance for a mocked-fix trip changes only if those server filters accept it, same as any WS-path point today. Before: a client false-positive silently deleted route history; after: the evidence uploads and the server decides.
- **AA display consumers** (`carFixChannel`, carSession, register): unchanged interfaces; the car marker now updates from whichever single task is running — zero edits needed in those call sites (verified by their untouched test suites).
- **Battery/network**: backoff strictly reduces outage traffic; self-heal adds one probe + setOptions per minute while backgrounded; AA-only swipe-away can leave the display-only notification until disconnect/OS reclaim (no egress — accepted residual).
- **Privacy (PIPEDA gate 9 — explicit owner sign-off in this run)**: sign-out retention reverses the 2026-07-29 immediate-deletion decision into bounded (14 d/5 k) on-device retention. Quarantine is never uploaded and never read by any UI, so no cross-account egress path exists. Owner's directive: location evidence must never be silently destroyed.

## User experience effect
No visible change for a healthy driver. On-trip drivers using Android Auto keep recording (previously died silently); mock-location users' markers stop moving while their points still upload flagged; the degraded-upload banner no longer flickers off mid-outage.

## Files modified
| file | what changed |
|---|---|
| `driver-app/utils/locationIntegrity.ts` | factory + registry reset |
| `driver-app/utils/backgroundLocation.ts` | capture-first handler, arbiter wiring, order fix, reassert, self-heal |
| `driver-app/hooks/useDriverDashboard.ts` | watcher/heartbeat capture-first, fgIntegrity |
| `driver-app/lib/androidAuto/carLocationTask.ts` | arbiter wiring, repairDispatch, killServiceOnDestroy removal, header rewrite |
| `driver-app/utils/sessionTeardown.ts` | integrity reset step |
| `driver-app/utils/tripLocationRecorder.ts` | backoff, telemetry |
| `driver-app/utils/tripLocationOutbox.ts` | purge preservation, retention prune |
| `driver-app/utils/locationTaskArbiter.ts` | new FIFO mutex |
| tests alongside each | contract pins per commit |

## Before/after snippet
Before (`backgroundLocation.ts` handler — drop before persist):
```ts
const integrity = checkLocationIntegrity(location);
if (!integrity.trusted) { console.warn(...); continue; }
publishCarFix({...});
await tripLocationRecorder.recordNativeFix(location, 'background');
```
After (persist first; integrity gates display only):
```ts
await tripLocationRecorder.recordNativeFix(location, 'background');
const integrity = bgIntegrity.check(location);
if (!integrity.trusted) { console.warn(...); continue; }
publishCarFix({...});
```

## Rollback plan
All client-side, no live-data mutation: `git revert` per commit is valid. Revert commits 11–13 (purge preservation + prune) together — quarantine rows written by 11 age out only via 12–13's TTL. Ships in the next Expo build (`[build]` commit tag); no server flag needed.

## Verification performed
- Full driver-app jest suite + `npx tsc --noEmit` green at every commit (final counts in the PR).
- Sequencing pins: car-stop before dispatch-promote; dispatch repair on car-stop-while-running; self-heal throttle + session-ended skip; backoff respected by force / bypassed by completion; purge→quarantine; prune TTL/expiry/caps incl. never-evict-open-sessions.
- **No production build was run in this session** (remote container, no EAS credentials) — `npx tsc --noEmit` and jest only; the next `[build]` EAS run is the compile gate.

## What was NOT verified
Native Android service behavior is jest-invisible. Device release gate before fleet rollout: (1) on-trip + AA unplug → notification persists, fixes keep landing; (2) AA connected ≥5 min → one notification, no flap; (3) on-trip swipe-from-recents → tracking survives; (4) AA-only swipe+unplug → notification clears on disconnect; (5) mock-location app → `mocked=true` server-side, markers frozen; (6) 10 min airplane mode → backoff to 5 min cap, full drain on reconnect, completion flush immediate; (7) offline sign-out mid-trip → `signout_unflushed` rows present, never uploaded, pruned at TTL. No iOS Android-Auto surface exists; iOS impact limited to the shared JS paths (suites green).
