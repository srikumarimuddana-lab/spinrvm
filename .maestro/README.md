# Maestro E2E Flows

Native-build mobile E2E tests using [Maestro](https://maestro.mobile.dev/) — the
only suite in this repo that drives a real native build as an actual user would.
`rider-app/e2e/` and `driver-app/e2e/` (Playwright) only exercise the Expo **web
export** (react-native-web) with backend/WebSocket/Maps/Firebase mocked — they
cannot reproduce a native-module-only bug (see `.github/workflows/maestro-e2e.yml`'s
header comment for the motivating example, #3174).

This doc covers running the flows **locally**, for free, against a simulator/
emulator or a device connected to your machine — no Maestro Cloud account, no
`MAESTRO_CLOUD_API_KEY`, no billed device-farm minutes. See "CI vs. local" below
for how this relates to the existing `maestro-e2e.yml` CI workflow.

## Prerequisites

1. **Maestro CLI** — free, open-source, no account needed for local runs:
   ```bash
   curl -Ls "https://get.maestro.mobile.dev" | bash
   ```
   Verify: `maestro --version`.

2. **A simulator/emulator, or a physical device connected via USB with debugging
   enabled.**
   - **Android**: Android Studio + an AVD (Android Virtual Device) — any recent
     API level works, since the flows target JS-level UI, not OS-specific
     behavior. `emulator -avd <name>` or launch from Android Studio's Device
     Manager, then confirm `adb devices` shows it.
   - **iOS**: **macOS only** — Xcode + iOS Simulator (`open -a Simulator`, or
     `xcrun simctl boot "iPhone 15"`). Maestro's iOS support does not work on
     Windows/Linux, locally or otherwise; there is currently no CI lane for iOS
     either (`maestro-e2e.yml`'s own header — no Apple Developer credentials are
     provisioned in EAS yet).

3. **The backend running locally** (`cd backend && python3 -m backend.server` —
   see root `CLAUDE.md`'s Commands section). Flows that touch dispatch, chat, or
   payouts need a live backend + Supabase connection; login/UI-only flows
   technically don't, but there's no harness here to fake the backend out from
   under the app, so just run it.

4. **The app installed on the simulator/emulator/device**, built locally as a
   dev client (these are managed Expo apps — no `ios/`/`android/` folders are
   checked in, so this is a real (pre)build, not just bundling JS):
   ```bash
   cd rider-app   # or driver-app
   yarn install
   npx expo run:android   # or: npx expo run:ios  (macOS only)
   ```
   First run does a native prebuild and can take a while; subsequent runs are
   fast incremental builds. This produces the same shape of build
   (`developmentClient: true`) as EAS's `"test"` profile that CI uses — see
   `rider-app/eas.json`/`driver-app/eas.json`'s `test` block — just built and
   installed locally instead of via EAS Build + Maestro Cloud's device farm.

5. **Backend URL**: in dev, `EXPO_PUBLIC_BACKEND_URL` is auto-detected from the
   Metro dev server host (see `rider-app/.env.example`/`driver-app/.env.example`)
   — this works out of the box for an emulator/simulator on the same machine.
   If auto-detect doesn't reach your backend (a physical device on a different
   network segment, some Android emulator networking edge cases), set it
   explicitly in `rider-app/.env`/`driver-app/.env`:
   ```
   EXPO_PUBLIC_BACKEND_URL=http://<your-machine-lan-ip>:8000
   ```

6. **Dev-mode OTP bypass**: every flow's login step enters `1234` as the OTP —
   this only works when the backend's `ENV != production` (`CLAUDE.md`'s OTP
   security section), which a local dev backend already satisfies. No real
   Twilio SMS is sent or needed.

## Running flows

```bash
# Run a single flow
maestro test .maestro/rider/01_login.yaml

# Run all rider flows (in filename order — 01, 02, 03...)
maestro test .maestro/rider/

# Run all driver flows
maestro test .maestro/driver/

# Run everything
maestro test .maestro/

# Interactive mode — step through a flow, inspect the view hierarchy live
# (the fastest way to debug a selector that isn't matching)
maestro studio
```

Each flow file's `appId:` header (`com.spinr.user` for rider, `com.spinr.driver`
for driver) tells Maestro which installed app to launch — you don't need to
`cd` into either app's directory or pass `--app-id` yourself for local runs.

## Flow inventory

Flows are numbered and generally build on each other's state within an app —
run them in order, or read the flow's own header comment for what it assumes.

| # | Flow | Assumes | Notes |
|---|---|---|---|
| **Rider** | | | |
| 01 | `login.yaml` | Fresh app state (`clearState: true`) | Phone `3065550199`, OTP `1234` |
| 02 | `request_and_cancel_ride.yaml` | Logged in (run 01 first) | |
| 03 | `schedule_and_cancel_ride.yaml` | Logged in | Verifies a local notification/reminder is set and cleared |
| 04 | `mid_trip_chat.yaml` | **A trip already in progress** | Needs a real driver-side counterpart — see "Two-sided flows" below |
| 05 | `sos_button.yaml` | **A trip already in progress** | Same as above |
| **Driver** | | | |
| 01 | `login.yaml` | Fresh app state | Phone bypass, OTP `1234` |
| 02 | `go_online.yaml` | Logged in and verified | |
| 03 | `accept_ride.yaml` | Online, **a ride offer arrives via WebSocket** | Needs a real rider requesting a ride at the same time |
| 04 | `verify_otp.yaml` | Ride accepted, en route to pickup | 4-digit pickup OTP |
| 05 | `complete_trip.yaml` | OTP verified, trip in progress | |
| 06 | `payout.yaml` | Driver has an available balance **≥ $10** | Data precondition — a clean local DB won't have this without seeding |
| 07 | `in_trip_chat.yaml` | Trip in progress (run after 04) | |

### Two-sided flows — the real limitation of solo local testing

Driver `03_accept_ride.yaml` onward, and rider `04_mid_trip_chat.yaml`/
`05_sos_button.yaml`, all assume an actual live ride between a rider and a
driver — dispatch, WebSocket offers, and trip state are real backend behavior,
not stubbed. To exercise these locally you need **two app instances against the
same backend at once**: e.g. one Android emulator running rider-app requesting
a ride, one running driver-app (or a second emulator/device) online and
accepting it, timed by hand. This is the one genuine capability gap versus a
scripted CI run — it's possible on one laptop (two emulators, or one emulator +
one physical device) but it's manual coordination, not a single `maestro test`
invocation. 01/02 (login) and rider 03 (schedule/cancel) are fully single-device.

`06_payout.yaml` also needs backend data (an available balance) that a fresh
local database won't have — either complete a full ride cycle first (which
itself needs the two-sided setup above) or seed the balance directly.

## Troubleshooting

- **Element not found / flow times out on a `tapOn`/`assertVisible`**: run
  `maestro studio` against the running app to inspect the live view hierarchy
  and confirm the exact text/id Maestro sees — app copy or a `testID` may have
  drifted from the flow file.
- **Android permission dialogs (location, notifications) block a flow**: grant
  permissions ahead of time via `adb shell pm grant <package> <permission>`, or
  pre-accept them once manually on the emulator before running — none of the
  current flows script permission-dialog handling themselves.
- **`clearState: true` on rider `01_login.yaml`** wipes the app's local storage
  each run — expected, keeps login idempotent, but means any manually-seeded
  local session gets reset too.
- **iOS simulator flows fail immediately**: confirm you're on macOS and a
  simulator is actually booted (`xcrun simctl list devices booted`) — Maestro
  won't boot one for you.

## CI vs. local

`.github/workflows/maestro-e2e.yml` is the CI path — it EAS-builds a native
Android APK on the `test` profile and runs these same flows on **Maestro
Cloud's hosted device farm** (`maestro cloud`, billed, needs
`MAESTRO_CLOUD_API_KEY` + `EXPO_TOKEN`). As of 2026-09-08 that workflow exists
and is valid, but doesn't fire passively — it's gated behind
`workflow_dispatch` or a PR labeled `run-maestro`, by design, for billing
discipline, and the two required secrets' presence hasn't been confirmed. Full
status tracked in `ACTION_ITEMS.md` B25.

Running locally (this doc) is currently a **manual, on-demand check** — before
a release, after a native-module change, or whenever you want to verify what
Playwright's web-export suite can't reach — not something CI runs automatically
per PR. That's a deliberate cost tradeoff (Maestro CLI is free; Maestro Cloud
and any CI-hosted simulator/emulator infrastructure are not), not an oversight.
If that changes (e.g., a self-hosted runner with an attached
simulator/emulator is set up later), update this section.
