# Maestro E2E Flows

Mobile E2E tests using [Maestro](https://maestro.mobile.dev/).

## Prerequisites

Install Maestro CLI:
```bash
curl -Ls "https://get.maestro.mobile.dev" | bash
```

## Running flows

```bash
# Run a single flow
maestro test .maestro/rider/01_login.yaml

# Run all rider flows
maestro test .maestro/rider/

# Run all flows
maestro test .maestro/
```

## Requirements

- iOS Simulator or Android Emulator running
- App built and installed on the simulator (`npx expo run:ios` or `npx expo run:android`)
- Dev mode active (OTP bypass "1234" works without real Twilio)

## Flows

### Driver Flows
- `01_login.yaml` — Login + OTP verification
- `02_go_online.yaml` — Toggle online status
- `03_accept_ride.yaml` — Accept ride offer from dashboard
- `04_verify_otp.yaml` — OTP entry during ride lifecycle
- `05_complete_trip.yaml` — Complete a ride (arrive → dropoff → complete)
- `06_payout.yaml` — View and request payout
- `07_in_trip_chat.yaml` — Send/receive in-trip chat messages
- `08_background_location.yaml` — **P3-20**: Location permission grant/deny/revoke; continuity under background task
  - Requires Maestro Cloud with device permissions API or manual simulator setup
  - Real-device continuity test (app backgrounded, updates continue) is xfail—see backend/tests/test_p3_background_location.py for manual runbook

### Rider Flows
- `01_login.yaml` — Login + OTP verification
- `02_request_and_cancel_ride.yaml` — Request a ride and cancel before driver accepts
- `03_schedule_and_cancel_ride.yaml` — Schedule a ride and cancel it
- `04_mid_trip_chat.yaml` — Send/receive in-trip messages
- `05_sos_button.yaml` — Trigger SOS alert

## CI Integration

Maestro flows run in CI via Maestro Cloud or a self-hosted runner with a simulator.
See `.github/workflows/ci.yml` for the planned integration (currently requires
simulator infrastructure — activate when available).
