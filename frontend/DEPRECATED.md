# ⚠️ DEPRECATED — Do Not Use

**This directory (`frontend/`) is deprecated as of 2026-04-14 (SPR-01, Decision D-003/D-004).**

## Why

`frontend/` was a combined web+mobile rider app prototype. It has:
- **42% screen parity** with `rider-app/` (11 of 22 screens)
- **Zero unique features** not already in `rider-app/`
- Duplicated stores, duplicated API client, incomplete state management
- No Firebase, no push notifications, no OTA updates

## Canonical Replacement

**Use `rider-app/` instead.** It is the complete, production-grade rider application with:
- All 22 screens (including wallet, fare-split, saved-places, promotions, chat, etc.)
- Real-time WebSocket via `useRiderSocket`
- Firebase FCM push notifications
- Stripe card management
- Full Expo web support (`npx expo export --platform web`)
- `react-native-maps` stubbed for web via `metro.config.js`

## Web Builds

CI now builds the web version from `rider-app/` and deploys to Vercel.
To build locally: `cd rider-app && npx expo export --platform web`

## Removal Timeline

This directory will be deleted in **SPR-02** once `rider-app` web deploy is
confirmed stable in production. Until then, `frontend-test` CI job continues
to run (with a deprecation warning) to avoid breaking downstream job dependencies.

## References

- Decision log: `docs/project/SPRINT_LOG.md` — SPR-00 / D-003, D-004
- Master plan: `docs/project/MASTER_PLAN.md`
