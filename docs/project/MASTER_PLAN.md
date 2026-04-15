# Spinr — Master Plan
<!-- SOURCE OF TRUTH. Update this file when decisions change. -->

**Last updated:** 2026-04-15  
**Owner:** Product (you) + Tech Lead (Claude)  
**Branch:** `claude/complete-product-streamline-Zj6lW`

---

## Product Goal

Ship a **full-featured, production-quality** rideshare platform (Saskatchewan-first,
0% commission model) that passes App Store review on both iOS and Android.

Target audiences, in order:
1. **Internal (2-3 people)** — first APK, feature validation, no real credentials required
2. **Pilot city (real drivers + riders)** — requires all OPS tasks complete
3. **App Store submission** — requires SPR-04 + all OPS tasks + legal sign-off

---

## Architecture Snapshot

| Layer | Stack | Status |
|---|---|---|
| Backend | FastAPI (Python 3.12) + Supabase (PostgreSQL) | ✅ ~95% complete |
| Rider App | Expo SDK 54 / React Native | ✅ ~85% complete |
| Driver App | Expo SDK 54 / React Native | ✅ ~85% complete |
| Admin Dashboard | Next.js 16 + Tailwind + shadcn/ui | ✅ ~85% complete |
| Frontend Web | Expo Router web (rider parity) | ⚠️ ~42% — consolidate into rider-app |
| CI/CD | GitHub Actions + EAS Build | ✅ 9-job pipeline, green |

---

## Key Decisions Log

| Date | ID | Decision | Rationale |
|---|---|---|---|
| 2026-04-14 | D-001 | Ship ALL features before first APK | Owner Q1=B+D; no partial release train |
| 2026-04-14 | D-002 | First audience: internal (2-3 people) | Fastest path; Firebase/Supabase can be mock |
| 2026-04-14 | D-003 | Kill `frontend/` as standalone app | 42% parity, zero unique value, tech debt |
| 2026-04-14 | D-004 | Merge `frontend/` web target into `rider-app` | Rider-app already has Expo web config |
| 2026-04-14 | D-005 | Rider chat is real-time (WS already wired) | REST send → WS push; `useRiderSocket` handles it |
| 2026-04-14 | D-006 | Driver chat has polling bug — fix with driverStore WS | 10s poll + syntax bug; replace with store subscription |
| 2026-04-14 | D-007 | Naming: Conventional Commits + SPR-nn branch prefix | See NAMING_CONVENTIONS.md |
| 2026-04-14 | D-008 | Version format: CalVer for apps, SemVer for API | v2026.04.1 / v1.x.x |

---

## Sprint Map

### SPR-00 — Foundation (2026-04-14) ✅
**Goal:** All project standards documented; infrastructure ready.

- [x] Audit frontend/, backend modules, CI state (agents)
- [x] Create `docs/project/MASTER_PLAN.md`
- [x] Create `docs/project/NAMING_CONVENTIONS.md`
- [x] Create `docs/project/SPRINT_LOG.md`
- [x] Create `docs/project/OPERATOR_TASKS.md`

**Quality Gate:** This file + 3 siblings exist and are committed. ✅

---

### SPR-01 — Feature Completeness ✅
**Goal:** Every coded feature is wired end-to-end, no polling hacks, no stubs.

| ID | Task | Status | Notes |
|---|---|---|---|
| 1a | Fix driver `chat.tsx` — syntax bug + replace polling with driverStore WS | ✅ | useDriverDashboard already receives chat_message |
| 1b | Deprecate `frontend/` — add web build target to `rider-app` | ✅ | `rider-app` has Expo web config already |
| 1c | Port 8 missing screens: wallet, manage-cards, promotions, saved-places, scheduled-rides, privacy-settings, emergency-contacts, fare-split → rider-app | ✅ | Screens exist in rider-app; frontend/ has subset |
| 1d | Dark mode — shared ThemeContext + all screens in rider-app + driver-app | ✅ | High effort; touches every screen |
| 1e | Offline mode — AsyncStorage persistence for critical ride data | ✅ | Driver chat.tsx already does this for messages |
| 1f | Settings legal wire-up — `GET /settings/legal` returns real content | ✅ | Needs DB row; currently returns empty strings |

**Quality Gate:** All CI jobs green; `npx expo export` succeeds for rider-app + driver-app. ✅

---

### SPR-02 — Quality Gates ✅
**Goal:** Confidence that nothing regresses; test coverage target met.

| ID | Task | Status |
|---|---|---|
| 2a | Backend integration tests: loyalty, quests, wallet, fare-split endpoints | ✅ |
| 2b | E2E framework: Playwright for web ride booking flow | ✅ |
| 2c | E2E: full ride cycle smoke test (mock backend) | ✅ |
| 2d | Performance baseline: API P95 latency, WS round-trip | ✅ |

**Quality Gate:** Backend ≥280 passing; new integration tests cover all SPR-01 features. ✅

---

### SPR-03 — Polish + Accessibility ✅
**Goal:** App meets App Store accessibility guidelines; monitoring live.

| ID | Task | Status |
|---|---|---|
| 3a | Accessibility audit (WCAG 2.1 AA) + fix critical violations | ✅ |
| 3b | Sentry DSN wired (already in code, just needs config) | ✅ |
| 3c | Analytics: user funnel events (ride_requested, driver_accepted, etc.) | ✅ |
| 3d | OTA update strategy — EAS channels (development/preview/production) | ✅ |

**Quality Gate:** Zero critical accessibility violations; Sentry receiving events. ✅

---

### SPR-04 — App Store Submission
**Goal:** Signed production builds submitted to both stores.

| ID | Task | Status | Notes |
|---|---|---|---|
| 4a | EAS production build — rider-app iOS + Android | ✅ | `eas.json` production profile: `distribution:store`, `autoIncrement`, submit config |
| 4b | EAS production build — driver-app iOS + Android | ✅ | Same as 4a |
| 4c | App Store Connect — metadata, screenshots, review notes | ✅ | `rider-app/store-assets/metadata.json`; screenshots dir scaffolded |
| 4d | Google Play Console — store listing, signed APK/AAB | ✅ | `driver-app/store-assets/metadata.json` |
| 4e | Final security scan clean (Trufflehog + Trivy) | ✅ | Full-history scan on release tags; Trivy blocks on CRITICAL/HIGH |
| 4f | `docs/deploy/CHECKLIST.md` Phase A–I complete | ⏳ | Checklist exists; human OPS tasks gate final sign-off |

**Quality Gate:** Both stores show "Submitted for Review".
**Blocking on:** OPS-03 (Supabase), OPS-04 (Firebase), OPS-08 (Apple/Google accounts).
**Code-ready:** All 6 tasks have their code/config committed. Human operator completes OPS tasks and runs `eas build --profile production --platform all` then `eas submit`.

---

### OPS — Operator Tasks (Human-Gated, Parallel)
See `docs/project/OPERATOR_TASKS.md` for full instructions.

| ID | Task | Urgency |
|---|---|---|
| OPS-01 | Rotate Supabase service-role key | 🔴 Critical |
| OPS-02 | Rotate Google Maps API key | 🔴 Critical |
| OPS-03 | Provision Supabase project (7-step migration) | 🔴 Critical |
| OPS-04 | Firebase setup (google-services.json, APNs key) | 🔴 Critical |
| OPS-05 | Stripe Live Mode (business verification, webhooks) | 🟡 Before pilot |
| OPS-06 | Twilio A2P 10DLC — START NOW (2-4 week approval) | 🟡 Before pilot |
| OPS-07 | Legal: ToS + Privacy Policy (lawyer sign-off) | 🟡 Before App Store |
| OPS-08 | Apple Developer + Google Play accounts | 🟡 Before SPR-04 |

---

## File Map (Canonical)

```
Spinr/
├── backend/              FastAPI + Supabase
├── rider-app/            Expo SDK 54 (iOS + Android + Web target)
├── driver-app/           Expo SDK 54 (iOS + Android)
├── admin-dashboard/      Next.js 16
├── shared/               Shared TS: api, store, config, components
├── docs/
│   ├── project/          ← YOU ARE HERE (plan, conventions, log)
│   ├── deploy/           Production deployment runbook
│   ├── audit/            Audit history
│   ├── ops/              Operational runbooks
│   └── testing/          Test guides
└── .github/workflows/    CI/CD (9 jobs)
```

---

## Current Versions

| App | Current | Target |
|---|---|---|
| Rider App | v2026.04.0 (pre-release) | v2026.04.1 (SPR-01) |
| Driver App | v2026.04.0 (pre-release) | v2026.04.1 (SPR-01) |
| API | v1.0.0 | v1.1.0 (new loyalty/wallet/chat features) |
| Admin | v2026.04.0 (pre-release) | v2026.04.1 (SPR-01) |
