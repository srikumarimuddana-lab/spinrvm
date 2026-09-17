# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Author | Cursor Grok |
| Surface(s) | backend / rider-app / driver-app / admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | uncommitted |
| Related issue or gap ID | PostHog session replay dark-launch beside LogRocket (Android LogRocket remains default-off) |

## 1. Issue / gap identified

LogRocket session replay is default-off on Android (Android 16 hidden-API hang). There is no screenshot-based replay for those devices, and no admin kill switch for a second replay vendor. PostHog was requested as an add, not a replacement.

## 2. Root cause

Replay was a single vendor (`LogRocket.init('gfuign/spinr')`) with no `app_settings` gate. Adding PostHog without a fail-closed flag would start recording on the next native build as soon as a project key is compiled in.

## 3. Fix / remediation

Ship PostHog session replay **dark**: new `settings` columns default off/empty; public `GET /settings` returns the flag, project key (`phc_…`), and host; both apps init only when the flag is on **and** the key is non-empty. LogRocket is unchanged. Existing `useLogRocketPrivacyScreen` call sites also pause PostHog. Admin Integrations card is the write UI.

Alternative considered: two PostHog Cloud projects (one per app). Rejected for now — org plan `organizations_projects` limit is 1. Rider vs driver are split in the existing **Spinr** project by person property `role`.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same table:** `public.settings` (`id='app_settings'`). PUT `/api/admin/settings` upserts the whole row via `SettingsUpdateRequest`; omitted fields stay unchanged (`exclude_none=True`). Public `GET /settings` is an explicit allowlist — new keys are added there so the apps can read them. `get_app_settings()` 60s TTL means a flip takes effect within a minute on the backend; mobile applies on **next launch**.
- **Blast-radius grep:** `posthog_session_replay_enabled` / `posthog_api_key` / `posthogReplay` / `initPostHogReplayFromSettings` / `useLogRocketPrivacyScreen`. Readers: `backend/routes/settings.py`, `backend/routes/admin/settings.py`, `backend/schemas.py`, admin Settings Integrations card, `rider-app/app/_layout.tsx`, `driver-app/app/_layout.tsx`, `shared/hooks/useLogRocketPrivacyScreen.ts` (11 existing pause sites). LogRocket init/identify lines were not removed.
- **Could this regress a working flow?** A routine Settings **Save** that includes the new fields only after they exist on `SettingsUpdateRequest` **and** migration 428 is applied. If the migration is not applied, PUT 500s the entire save (`PGRST204`) — same class of bug as 313/415. Do not merge without applying 428 to the environment that serves admin Settings.
- **Ride state / money / loops:** none.
- **PIPEDA:** identify is `user_id` + `role` only. Mask all text inputs and images; console logs and network bodies off. GeoIP, app-lifecycle events, remote feature flags, surveys, and push-token capture are constructor-off. Payments, cards, SOS, documents, Stripe onboarding, support, ride-tracking webview already pause via the privacy hook. Ride maps and addresses are **not** paused (pre-existing LogRocket gap). Logout calls `reset()` so a shared device does not stitch rider A → rider B. PostHog Cloud is **US**, not Canada — flag stays off until Legal sign-off.

## 5. User-experience effect

- **Who sees it:** internal admin sees a new Integrations card. Riders/drivers see nothing while the flag is off. With flag on + native build, sessions can be recorded in PostHog (masked).
- **Mid-session:** no. Init is on boot from `GET /settings`. Turning the flag off does not stop an already-running session until the next launch.
- **Copy:** admin helper text only. No rider/driver notification.
- **Visual:** `/dashboard/settings` is a seeded Playwright baseline (`dashboard-settings`). The new Integrations card **will** fail visual-regression until a human re-runs `update-visual-baselines.yml`. rider-app and driver-app have no visual tooling — replay UI is none (SDK only), reasoned about not screenshotted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/428_posthog_session_replay.sql` | Additive columns, default off/empty/US host | Flat `settings` table has no JSON catch-all |
| `backend/schemas.py` | `AppSettings` fields | Schema defaults fail closed |
| `backend/routes/admin/settings.py` | `SettingsUpdateRequest` fields; HTTPS `posthog_host`; `phc_` key prefix; host+key on `_SUPER_ADMIN_ONLY_FIELDS` | Write allowlist; destination cannot be silently repointed |
| `backend/routes/settings.py` | Public GET returns the three fields | Apps read the gate on boot |
| `backend/tests/test_posthog_settings_flag.py` | Defaults, round-trip, public GET, kill-switch vs destination privilege | Pin fail-closed + super-admin host/key |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | `KNOWN_SETTINGS_COLUMNS` | Drift gate |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | Integrations card | Admin enable/disable + key + host |
| `admin-dashboard/e2e/settings.spec.ts` | Toggle present/clickable | Same pattern as AI assistant |
| `shared/services/posthogReplay.ts` | Fail-closed init, identify, pause/resume, logout `reset()` | Shared client without putting the native SDK in `shared/package.json` |
| `shared/hooks/useLogRocketPrivacyScreen.ts` | Also pause/resume PostHog | Cover all 11 existing sites without renaming them |
| `shared/services/__tests__/posthogReplay.test.ts` | Gate + masking + GeoIP/lifecycle/push/flags off | Real coverage of fail-closed |
| `shared/hooks/__tests__/useLogRocketPrivacyScreen.test.ts` | Pause both SDKs | First unit test of the privacy hook |
| `rider-app/app/_layout.tsx` | Init + identify next to LogRocket; logout reset | Parallel, not a replacement |
| `driver-app/app/_layout.tsx` | Same; boot `GET /settings`; logout reset | Driver had no app-wide settings fetch |
| `rider-app/app.config.ts` / `driver-app/app.config.ts` | `posthog-react-native/expo` plugin | Native replay (Expo Go unsupported) |
| `rider-app/jest.config.js` / `driver-app/jest.config.js` | Native mock + real `posthogReplay` mapper | Tests must not load the native module |
| `rider-app/__mocks__/posthog-react-native.js` / `driver-app/__mocks__/posthog-react-native.js` | Jest stub | |
| `rider-app/package.json` / `driver-app/package.json` | Native deps via `expo install` | Native modules cannot live only in shared |
| `docs/vendor-register.md` | PostHog row + DPA open item | Required before merge for a PII-processing vendor |
| `docs/legal/subprocessor-list.md` | Draft PostHog row | Keep the public draft in sync with the register |
| `docs/change-log/2026-09-16-posthog-session-replay.md` | This log | Live-tested surface |

## 7. Before / after

```
# Before — rider/driver boot had no PostHog; privacy hook paused LogRocket only
LogRocket.init('gfuign/spinr')
LogRocket.pauseViewCapture()
```

```
# After — PostHog inits only when flag AND phc_ key are set; privacy hook pauses both
if (shouldInitPostHogReplay(settings)) { await initPostHogReplayFromSettings(...) }
pausePostHogReplay()  // plus existing LogRocket pause
resumePostHogReplay() // startSessionRecording(true) — same session
```

## 8. Rollback plan

Without a second deploy: `PUT /api/admin/settings` `{"posthog_session_replay_enabled": false}`. Backend cache ≤60s; already-open app sessions keep recording until next launch. Empty `posthog_api_key` is an equivalent fail-closed. Do not DROP the columns while readers exist. `git revert` is not sufficient if the flag was turned on in production — turn the flag off first.

## 9. Verification performed

- [x] Backend: `pytest --override-ini addopts= --tb=short -q tests/test_posthog_settings_flag.py tests/test_admin_settings_write_allowlist_drift.py` — 19 passed (parity coverage is `test_admin_settings_write_allowlist_drift.py`; there is no `test_settings_column_parity.py`)
- [x] Shared/mobile Jest: `yarn test --watchman=false --coverage=false ../shared/services/__tests__/posthogReplay.test.ts ../shared/hooks/__tests__/useLogRocketPrivacyScreen.test.ts` from `rider-app/` — 19 passed
- [x] Blast-radius grep: `posthog_session_replay_enabled`, `posthogReplay`, `useLogRocketPrivacyScreen`
- [x] PIPEDA: identify user_id+role; mask inputs/images; no log/network capture
- [x] Feature-flagged: `posthog_session_replay_enabled` default false
- [x] Adversarial alternative: two PostHog projects — blocked by plan limit 1; role property used instead
- [ ] `npm run build` / EAS native build — not run (plugin added; Expo Go cannot verify replay)
- [ ] Admin Playwright visual baseline — needs human recapture of `dashboard-settings`

## 10. What was NOT verified

- Migration 428 not applied to live Supabase
- No native iOS/Android device recording
- No PostHog EU org (current org is US Cloud)
- Replay on Android API 25 (`minSdkVersion` left at 25 for LogRocket; PostHog replay wants API 26+)
- Privacy policy / published subprocessor page not updated for production enablement
- Visual regression baseline not recaptured
- Admin E2E not run in this session unless Playwright is invoked separately
- rider-app / driver-app: no visual-regression tooling; pause coverage is unit-tested, not screenshotted
