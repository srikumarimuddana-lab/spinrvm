# Cold storage: alternative Android build strategies (driver-app)

Same purpose and structure as `rider-app/build-options/`. See that directory's README.md
for the full decision tree and concept.

## Differences from rider-app

- **No Stripe.** Driver-app does not bundle `@stripe/stripe-react-native`. Any "Stripe pin"
  steps in Option A/B switching guides are skipped here.
- Otherwise identical strategies.

## Index

| Strategy | When to consider |
|---|---|
| **Active (Option C)** — Kotlin 2.2.21 + ksp 2.2.21-2.0.5 | (current — see `../plugins/`, `../patches/`) |
| `option-b-kotlin-2.1.20/` | Option C breaks on Expo SDK 55 modules |
| `option-a-kotlin-2.0.21/` | Catastrophic Option C failure |

See `rider-app/build-options/README.md` for the master decision tree.
