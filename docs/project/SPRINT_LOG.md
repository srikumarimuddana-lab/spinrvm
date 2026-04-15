# Spinr — Sprint Log
<!-- Running decision log. Append entries; never delete. Newest at top. -->

---

## SPR-00 — Foundation
**Date:** 2026-04-14  
**Branch:** `claude/complete-product-streamline-Zj6lW`  
**Status:** ✅ Complete

### Context
New engagement to complete the Spinr product end-to-end. Previous sessions fixed
critical bugs (CORS, JWT, OTP, push notifications, WebSocket reconnect, race conditions,
admin auth, test suite). This session's goal: ship everything remaining.

### Audit Findings (3 parallel agents)

**Frontend Web App (`frontend/`):**
- STATUS: 42% complete (11 of 22 rider-app screens)
- Unique value vs rider-app: NONE
- Tech debt: duplicated stores, local API client, `(driver)/` directory missing
- DECISION D-003/D-004: Kill as standalone; add web build to rider-app

**Backend Feature Modules:**
- loyalty.py: MOUNTED, 4 endpoints, COMPLETE
- quests.py: MOUNTED, 8 endpoints, COMPLETE
- wallet.py: MOUNTED, 5 endpoints, COMPLETE
- favorites.py: MOUNTED, 5 endpoints, COMPLETE
- fare_split.py: MOUNTED, 6 endpoints, COMPLETE
- All mobile screens (wallet, fare-split, scheduled-rides) call real endpoints ✅

**Chat Real-Time Status:**
- Rider side: COMPLETE — REST send → WS push; `useRiderSocket` receives chat_message
- Driver side: PARTIALLY BROKEN — `chat.tsx` polls every 10s AND has a syntax bug
  (broken useEffect hook at line 95). `useDriverDashboard` receives chat_message
  via WS but only vibrates, never pushes to screen.
- DECISION D-005/D-006: Fix driver chat with driverStore subscription

**CI State:**
- 9 CI jobs: backend-test, frontend-test, driver-app-test, rider-app-test,
  admin-test, e2e-test, deploy-*, mobile-build, security-scan
- Branch `claude/complete-product-streamline-Zj6lW` is clean; no conflicts
- Backend test suite: 281 passed, 1 skipped

### Decisions Made
| ID | Decision |
|---|---|
| D-001 | Ship ALL features before first APK (no partial release train) |
| D-002 | First audience: internal only (Firebase can be mock) |
| D-003 | Kill `frontend/` as standalone app |
| D-004 | Merge web target into rider-app |
| D-005 | Rider chat real-time is already working |
| D-006 | Driver chat needs driverStore WS wiring (replace polling) |
| D-007 | Adopt Conventional Commits + SPR-nn branch prefix |
| D-008 | CalVer for apps, SemVer for API |

### Deliverables
- [x] `docs/project/MASTER_PLAN.md`
- [x] `docs/project/NAMING_CONVENTIONS.md`
- [x] `docs/project/SPRINT_LOG.md` (this file)
- [x] `docs/project/OPERATOR_TASKS.md`

---

## SPR-01 — Feature Completeness
**Date:** 2026-04-14 (started)  
**Branch:** `feat/SPR-01-feature-completeness` (to be created)  
**Status:** ✅ Complete

### Tasks
| ID | Task | Status | Notes |
|---|---|---|---|
| 1a | Fix driver chat.tsx (syntax bug + polling → driverStore WS) | ✅ | commit 36b8a66; driverStore WS subscription replaces 10s poll |
| 1b | Deprecate frontend/; configure rider-app web target | ✅ | frontend/DEPRECATED.md written; app.config.ts has web:{bundler:metro}; CI deploy-frontend builds from rider-app/ |
| 1c | Port 8 missing screens to rider-app | ✅ | All 8 screens already present with useTheme() + real API calls |
| 1d | Dark mode (ThemeContext + all screens) | ✅ | shared/theme/; all ~70 screens migrated |
| 1e | Offline mode (AsyncStorage persistence) | ✅ | rideStore + driverStore write-through; hydrateActiveRide / hydrateDriverRideState on mount |
| 1f | Legal text wire-up | ✅ | settings_router was never mounted in server.py; fixed + dual-mount at root + /api/v1 |

### Decisions
_(to be recorded as sprint progresses)_

---

## SPR-03 — Polish + Accessibility
**Date:** 2026-04-15
**Branch:** `claude/complete-product-streamline-Zj6lW`
**Status:** ✅ Complete

### Tasks
| ID | Task | Status | Notes |
|---|---|---|---|
| 3a | Accessibility: fix critical violations on login + home + booking CTA | ✅ | accessibilityLabel/Role/State/Hint on phone input, send-OTP button, "Where to?" search bar, SOS, notifications — rider-app + driver-app |
| 3b | Sentry DSN wired | ✅ | shared/services/errorReporting.ts (Crashlytics facade); ErrorBoundary wired; admin-dashboard: @sentry/nextjs + sentry.client/server.config.ts; setUser on auth |
| 3c | Analytics: ride funnel events | ✅ | shared/analytics/index.ts; otp_verified, login, ride_requested, payment_initiated/completed, ride_completed; Firebase Analytics (web) + @react-native-firebase/analytics (native) |
| 3d | OTA update strategy — EAS preview channel CI | ✅ | CI job `ota-update` runs `eas update --channel preview` for rider+driver on every main merge; production channel is manual (SPR-04) |

### Decisions
| ID | Decision |
|---|---|
| D-012 | Use Firebase Crashlytics (already native dep) as Sentry compat facade for mobile; swap to @sentry/react-native at SPR-04 with EAS build |
| D-013 | Analytics uses firebase/analytics on web, @react-native-firebase/analytics on native; both silently no-op if Firebase not configured |
| D-014 | OTA channel strategy: test (internal APK) → preview (CI auto-push on main) → production (manual SPR-04 release) |

### Deliverables
- [x] `shared/services/errorReporting.ts` (Crashlytics facade)
- [x] `shared/analytics/index.ts` (typed event catalog)
- [x] `shared/components/ErrorBoundary.tsx` — wired to captureException
- [x] `rider-app/app/_layout.tsx` — errorReporting + analytics init
- [x] `driver-app/app/_layout.tsx` — errorReporting + analytics init
- [x] `rider-app/app/otp.tsx` — otpVerified + login events
- [x] `rider-app/app/payment-confirm.tsx` — rideRequested + paymentInitiated
- [x] `rider-app/app/ride-completed.tsx` — rideCompleted + paymentCompleted
- [x] `admin-dashboard/sentry.client.config.ts` + `sentry.server.config.ts`
- [x] `admin-dashboard/next.config.ts` — withSentryConfig
- [x] `admin-dashboard/package.json` — @sentry/nextjs added
- [x] Accessibility: login.tsx (both apps), (tabs)/index.tsx
- [x] CI job `ota-update` in `.github/workflows/ci.yml`

---

## SPR-02 — Quality Gates
**Date:** 2026-04-15 (started)
**Branch:** `claude/complete-product-streamline-Zj6lW`
**Status:** ✅ Complete

### Tasks
| ID | Task | Status | Notes |
|---|---|---|---|
| 2a | Backend integration tests: loyalty, quests, wallet, fare-split | ✅ | 77 new tests; full suite 356 passing; uses TestClient + dependency_overrides + patch("routes.X.db") |
| 2b | E2E framework: Playwright for rider-app web | ✅ | rider-app/playwright.config.ts + e2e/fixtures.ts; mocks /api/v1/** + Google Maps + Firebase; CI job `rider-web-e2e` added |
| 2c | E2E: full ride cycle smoke test (mock backend) | ✅ | e2e/ride-booking.spec.ts walks 5 stages (searching → driver_assigned → driver_arrived → in_progress → completed) |
| 2d | Performance baseline: API P95 latency, WS round-trip | ✅ | perf_baseline.py: in-process ASGI bench; HTTP P95 ≤7ms, WS P95 ≤8ms; CI job `perf-baseline` saves artifact + regresses at +30% |

### Decisions
| ID | Decision |
|---|---|
| D-009 | Patch `routes.X.db` (not `backend.routes.X.db`) — server.py puts backend/ on sys.path |
| D-010 | Cross-module patches required when a route uses `get_or_create_wallet` (patch both modules' `db`) |
| D-011 | rider-app E2E runs against `expo export --platform web` output served by `npx serve` on :3002; no real network |

### Deliverables
- [x] `backend/tests/test_loyalty.py` (14 tests)
- [x] `backend/tests/test_wallet.py` (15 tests)
- [x] `backend/tests/test_quests.py` (25 tests)
- [x] `backend/tests/test_fare_split.py` (23 tests)
- [x] `rider-app/playwright.config.ts`
- [x] `rider-app/e2e/fixtures.ts`
- [x] `rider-app/e2e/smoke.spec.ts`
- [x] `rider-app/e2e/ride-booking.spec.ts`
- [x] CI job `rider-web-e2e` in `.github/workflows/ci.yml`
- [x] `backend/tests/perf_baseline.py` (HTTP P95 + WS round-trip; CI job `perf-baseline`)

---

## SPR-04 — App Store Submission
**Date:** 2026-04-15
**Branch:** `claude/complete-product-streamline-Zj6lW`
**Status:** ✅ Code-complete — blocking on OPS gates

### Context
All code and configuration required to ship production builds is committed.
The remaining steps are operator actions (provisioning accounts, uploading
credentials, running `eas build`, submitting to the stores). See
`docs/deploy/CHECKLIST.md` Phases A–I and `docs/deploy/04-mobile-eas.md`
for the full runbook.

### Tasks
| ID | Task | Status | Notes |
|---|---|---|---|
| 4a | EAS production build profile — rider-app | ✅ | `distribution:store`, `autoIncrement:true`, `submit.production` with env-var credentials |
| 4b | EAS production build profile — driver-app | ✅ | Same as 4a; separate `ASC_DRIVER_APP_ID` var |
| 4c | App Store Connect metadata | ✅ | `rider-app/store-assets/metadata.json` — name, description, keywords, review notes, screenshot manifest |
| 4d | Google Play metadata | ✅ | `driver-app/store-assets/metadata.json` — same structure for both apps |
| 4e | Final security scan | ✅ | `security-scan` CI job: full git-history Trufflehog on release tags/dispatch; Trivy blocks on CRITICAL/HIGH; working-tree filesystem scan added |
| 4f | `docs/deploy/CHECKLIST.md` Phase A–I | ⏳ | Checklist exists and is complete; human sign-off gates final launch |

### Decisions
| ID | Decision |
|---|---|
| D-015 | EAS submit credentials sourced from GitHub Actions secrets (`$APPLE_ID`, `$ASC_APP_ID`, `$ASC_DRIVER_APP_ID`, `$APPLE_TEAM_ID`); never hardcoded in eas.json |
| D-016 | `play-service-account.json` is gitignored; operator places it manually in each app dir before `eas submit` |
| D-017 | Trufflehog scan is incremental (HEAD~1) on every push for speed; switches to full-history on release tags and `workflow_dispatch` |
| D-018 | Trivy filesystem scan now blocks (exit-code 1) on CRITICAL or HIGH severity with `ignore-unfixed:true` |

### Deliverables
- [x] `rider-app/eas.json` — production profile with `distribution:store`, `autoIncrement`, `submit.production`
- [x] `driver-app/eas.json` — same
- [x] `rider-app/store-assets/metadata.json` — App Store Connect + Google Play copy
- [x] `driver-app/store-assets/metadata.json` — same
- [x] `.github/workflows/ci.yml` — `security-scan` job: full-history Trufflehog, filesystem scan, Trivy CRITICAL/HIGH blocking
- [x] `scripts/setup-eas-secrets.sh` — extended to register `EXPO_PUBLIC_BACKEND_URL` and `SENTRY_AUTH_TOKEN`
- [x] `docs/project/MASTER_PLAN.md` — SPR-00 through SPR-04 status updated
- [ ] OPS: Apple Developer + Google Play accounts active (human gate)
- [ ] OPS: Firebase `google-services.json` + `GoogleService-Info.plist` placed in app dirs
- [ ] OPS: `eas build --profile production --platform all` run for rider-app + driver-app
- [ ] OPS: `eas submit --profile production --platform all` run for both apps
- [ ] OPS: Store review approved (24-72h for new apps)

<!-- Add new sprints above this line, newest first -->

