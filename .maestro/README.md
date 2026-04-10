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

## CI Integration

Maestro flows run in CI via Maestro Cloud or a self-hosted runner with a simulator.
See `.github/workflows/ci.yml` for the planned integration (currently requires
simulator infrastructure — activate when available).
