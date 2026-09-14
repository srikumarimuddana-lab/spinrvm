# Android Auto marker position and direction — Change Impact Log

Date: 2026-09-14. Author: Codex. Domain: drivers. Branch: `codex/android-auto-heading-road-fix`.

## Issue and evidence

User reports east/west sideways motion and northbound travel with the vehicle facing south, including active-trip photos. Current-code diagnostics reproduced a south-directed route overriding northbound movement, and an old fix rewinding the location channel. The photographs do not identify the installed APK/OTA revision.

## Execution checklist

The user requested coding only and will perform device testing to conserve usage. No broad test suites or native builds are run during this implementation. Local static validation and focused review remain necessary. Each code batch is committed before the next starts; this checklist is the task tracker because TodoWrite is unavailable.

- [ ] Foreground measurement metadata and chronological acceptance.
- [ ] Background producer metadata.
- [ ] Direction-aware matching and route animation geometry.
- [ ] Marker integration on both tracked forks.
- [ ] Android Auto camera and playback route continuity.
- [ ] Route response ordering and final static review.

## Changes and blast radius

| Files | Change | Effect / risk |
|---|---|---|
| `driver-app/lib/androidAuto/carFixChannel.ts`, `useCarLocation.ts` | Retain native capture time, accuracy and speed; reject stale/older/invalid display updates; bound watchdog concurrency | All car display producers share the channel. `register.ts` also reads its last fix for SOS; rejected display fixes leave the last valid position available. Timestamped cache entries retain capture time. |

Trip recorder persistence, billing, ride state transitions, and backend writes are unchanged. Display-only filtering is downstream of raw trip recording. Heading/route changes will be scoped to Android Auto through opt-in marker props; common metadata plumbing also affects phone marker inputs. Check both marker forks listed in `docs/known-forks.md`.

## Before / after

Before: every callback overwrites position and derives heading in arrival order; camera uses current GPS while the icon uses delayed playback. A route segment can determine heading against observed travel.

After (as each checklist item lands): measurement-order acceptance; marker and camera share display state; route alignment respects movement direction and retained geometry covers playback.

## UX, alternative, and rollout

The driver should see the car aligned with travel on the head unit through all four compass headings and turns. No icon-art rotation is applied: rotating the bitmap by 180 degrees would also reverse currently correct southbound travel. Replacing the navigation SDK was considered; correcting the existing data/timing faults has a smaller blast radius and no added routing service cost.

No deployment, update publication or merge is authorized by this coding-only task. Device validation on the actual APK/update remains the release gate. A bundle switch for new rendering behavior is recorded with the final integration; it requires an update/restart and is not an instantaneous server kill switch.

## Verification and rollback

Static checks and review results are recorded at completion. Automated runtime tests, native build, Android Auto DHU and physical road tests are not performed in this implementation per user instruction. Neither app has active automated native visual regression coverage.

Rollback after a release requires republishing the preceding compatible bundle or disabling the rendering switch in a replacement update, followed by app restart/update adoption. No DB rollback applies. Source commits alone do not roll back an installed app.
