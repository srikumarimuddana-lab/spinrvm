# ADR-002: Expo SDK for both mobile apps

**Date:** 2026-02-14
**Status:** Accepted

---

## Context

Spinr requires two distinct mobile apps — one for riders, one for drivers — targeting iOS and Android. The team is TypeScript-first with no native (Swift/Kotlin) experience. Key requirements:

- Shared business logic between the two apps (API client, auth flow, type definitions)
- OTA (over-the-air) update capability to ship hotfixes without App Store review
- Push notifications via FCM (Firebase Cloud Messaging)
- Background location tracking for drivers while a ride is active
- Google Maps integration for route display and ETA
- Fast iteration cycle for a small team

Alternatives considered:

| Option | Rejected because |
|--------|-----------------|
| React Native CLI (bare) | Full native toolchain required; no OTA updates without additional setup |
| Flutter | Dart; not TypeScript; smaller ecosystem for maps/payments integrations |
| Native iOS + Android | No shared code; requires two separate teams; out of scope for current headcount |
| Capacitor (Ionic) | Web-based rendering; inferior performance for real-time map updates |

---

## Decision

Use **Expo SDK 54** (managed workflow) for both `rider-app/` and `driver-app/`. Production builds are generated via **Expo EAS Build**, triggered only when a commit message contains `[build]` to avoid burning EAS build minutes on every push.

Key implementation details:
- Both apps share types and the API client via the `@spinr/shared` workspace package (`shared/`).
- Push notifications use `expo-notifications` backed by Expo's push gateway, which proxies to APNs/FCM. The backend sends notifications via Twilio (SMS) and Firebase Admin SDK (push) — Expo's push service is used for device token management only.
- Background location (`expo-location` with `BACKGROUND` permission) is used in the driver app only; the rider app requests `FOREGROUND` only.
- Google Maps is rendered via `react-native-maps` with the Google Maps provider on Android and Apple Maps fallback on iOS (Maps API key injected at build time via EAS secrets).
- OTA updates use `expo-updates`; the channel is `production` on main, `staging` on feature branches.

---

## Consequences

**Positive:**
- Single TypeScript codebase covers both platforms and both apps.
- EAS Build produces fully signed `.ipa` / `.aab` artifacts without a local Mac.
- `expo-updates` allows pushing JS-layer fixes to users within minutes, without an App Store release.
- The `@spinr/shared` package eliminates API contract drift between the two apps and the backend.

**Negative / trade-offs:**
- Managed workflow limits native module choices to those in the Expo SDK or compatible with `expo-modules-core`. Adding a native-only library requires ejecting to a bare workflow or using a Development Build.
- EAS Build minutes are a finite resource; the `[build]` gate is essential to avoid exhausting the free tier during active development.
- Expo SDK upgrades (typically every 6 months) require coordinating RN version, native dependency updates, and EAS Build image updates simultaneously — estimated 1–2 days of upgrade work per cycle.
- Background location on iOS requires `UIBackgroundModes: location` in `app.json`; Apple review scrutinises this. The driver app justification (active navigation during a ride) is straightforward, but must be clearly described in App Store review notes.
