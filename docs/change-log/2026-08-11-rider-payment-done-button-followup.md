# Change Impact & Risk Log — Rider payment-sheet "Done" button re-reported dead (follow-up)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (session on `claude/rider-payment-done-button-gqpvj8`) |
| Surface(s) | rider-app (test-only change) |
| Domain (Sentry tag) | payments |
| Related | 5f18a92 / PR #3655 (the fix), ACTION_ITEMS C17/C19 (the OTA pipeline outage), `docs/change-log/2026-08-11-rider-payment-sheet-done-button.md` (original log) |

## 1. Issue / gap identified

Live tester re-reported "the Done button for payment selection is dead" —
the same symptom already fixed earlier today in `5f18a92` (merged to `main`
17:20 UTC via PR #3655).

## 2. Root cause (of the re-report)

The code fix was correct but **could not reach any device when it merged**:
the "EAS Mobile Update" workflow run for merge `f011ff3` **failed** at
17:20 UTC (both attempts) — the then-live C19 `eas update`
fingerprint-computation bug. The pipeline was repaired at 18:45 UTC
(`945f49e`), after which rider OTA publishes to the `preview` branch/channel
succeeded at **19:00 UTC** and again at **19:56 UTC** (runs 31524403194,
31529849214 — rider "Publish OTA update" step green in both). Any test
session on a bundle fetched before ~19:00 UTC still had the broken modal.

Delivery mechanics: expo-updates downloads the update in the background on
one launch and applies it on the next, so a tester must fully close and
relaunch the rider app **twice** after 19:00 UTC to be on the fixed bundle.
`runtimeVersion` is `2.0.0` on both the published update and the New-Arch
binaries the bug manifests on, so there is no runtime-version mismatch
blocking delivery.

Re-verification of the fix itself found no residual press-blocker: backdrop
is a sibling `Pressable` under a plain-`View` sheet, `modalOverlay` is
`flex: 1` (no zero-height/outside-parent-bounds hit-testing trap), no
open/close state loop, and a sweep of every remaining `activeOpacity={1}`
in rider-app (`otp`, `login`, `verify-email`, `ride-status`, `account`)
found only focus-forwarders and image-viewer backdrops with no interactive
children — no other live instance of the nested-Touchable class.

## 3. Fix / remediation

No production-code change (none is warranted — the shipped fix is
structurally correct). Added a regression guard so the pattern cannot be
silently reintroduced a third time (it has now bitten twice: payment sheet
`5f18a92`, language picker `2e4826b`):

- `rider-app/__tests__/ride-options-payment-sheet.test.tsx` — source-contract
  test (same pattern as `ride-completed-route.test.tsx`) pinning: sibling
  `Pressable` backdrop, plain-`View` sheet container, Done button wired to
  `setShowPaymentSheet(false)`, `onRequestClose` retained, and **no**
  Touchable carrying the `modalOverlay`/`paymentModal` styles.

## 4. Risk & impact on existing functionality

Test-only + docs-only diff; zero runtime blast radius. The contract test
reads `app/ride-options.tsx` as text — no imports of the screen, no new
mocks. Risk is limited to future false-positive test failures if the modal
is legitimately restructured; the assertions are scoped to the region
between the `Payment method modal` and `Promo selection sheet` markers to
keep them from tripping on unrelated edits elsewhere in the file.

## 5. User experience effect

None from this diff. For the reporter: the already-shipped fix restores the
Done button after the app is fully closed and relaunched twice (OTA applies
on second launch). No mid-session behavior change beyond the bug going away.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `rider-app/__tests__/ride-options-payment-sheet.test.tsx` | New source-contract regression test | Pin the fixed modal structure; the nested-Touchable press-loss class has recurred twice |
| `docs/change-log/2026-08-11-rider-payment-done-button-followup.md` | This log | Record why the fixed bug was re-reported (OTA delivery gap), so the next re-report isn't re-diagnosed from scratch |

## 7. Before/after snippet

Not applicable — no behavior-changing diff (additive test + docs only).

## 8. Rollback plan

Delete the test file (or `it.skip`) — no data, config, or runtime state
involved.

## 9. Verification performed

- `jest __tests__/ride-options-payment-sheet.test.tsx` — see commit for result.
- GitHub Actions evidence read directly (run 31517080902 failed on
  `f011ff3`; runs 31524403194 / 31529849214 rider jobs succeeded, "Publish
  OTA update" step green, publishing `--branch preview`).
- No production build run — the diff contains no shippable code.

## 10. What was NOT verified

- **On-device retest of the fixed bundle** — no emulator/device in this
  environment; the reporter (or any preview-channel tester) closing and
  relaunching the app twice after 19:00 UTC is the confirming step.
- Whether the reporter's device is on the `preview` channel (assumed — it is
  the channel push-triggered OTAs publish to and the one live-test builds in
  `eas.json` use). If they run a `test`-channel build, that channel received
  no OTA today; a `workflow_dispatch` of `eas-build.yml` with profile
  `test` would be needed.
