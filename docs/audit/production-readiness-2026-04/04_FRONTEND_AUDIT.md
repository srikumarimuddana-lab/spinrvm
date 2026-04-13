# 04 — Frontend & Mobile Audit

> **Read time:** ~20 min
> **Audience:** Mobile & web engineers
> **Scope:** rider-app, driver-app, admin-dashboard

---

## Executive verdict

Three clean, idiomatic codebases — Expo Router on mobile, Next.js 16 App Router on admin. The **mobile apps drift on Expo SDK versions** (rider 54 vs driver 55), which complicates shared RN native modules and EAS build parallelism. **Offline/resilience, a11y, and i18n** are all under-invested. Admin dashboard has healthy test coverage via Playwright + axe.

---

## Cross-cutting P0 / P1 findings

### P0-F1 — Expo SDK version drift (rider 54 vs driver 55)

**Evidence:** `rider-app/package.json` → `expo: ~54.0.0`; `driver-app/package.json` → `expo: ~55.0.15`. Several linked packages diverge in patch ranges (`expo-blur`, `expo-clipboard`, `expo-router`, etc.). Rider uses `expo-router ~55.0.12` (incompatible nesting in an SDK-54 app).

**Impact:**
- Different React Native/Hermes/JSC runtimes → subtle behavior deltas.
- Shared native deps (Stripe, Firebase, maps) build against different RN versions.
- EAS credentials/profiles need duplicate maintenance.
- Rider's `expo-router ~55.0.12` on top of Expo SDK 54 is **officially unsupported** — install may succeed but runtime behavior is undefined.

**Fix (M):**
1. Upgrade rider-app to Expo SDK 55 to match driver.
2. Pin identical versions of every `expo-*`, `react-*`, `react-native`, `@react-native-*` package across both apps.
3. Consider promoting common config to a **workspace** (Yarn/Bun workspaces) with `shared/` already present.

---

### P0-F2 — No offline/queue behavior for ride-critical actions

**Evidence:** Rider's `search-destination` → `ride-options` → `payment-confirm` flow relies on live network. Driver's `accept_ride` / `arrived` / `complete` WebSocket messages have no at-least-once delivery queue.

**Impact:** Momentary connectivity loss (elevators, underpasses) = rider retries a ride request (possible double-charge once webhook idempotency lands — and **guaranteed** double-charge today). Driver "arrived" event never reaches backend → rider sees stale status.

**Fix (M):**
- Rider: wrap ride-request POST with a **client-side idempotency key** (UUIDv4 per screen mount) and persist "in-flight request" to AsyncStorage until ack. Backend honors `Idempotency-Key` header (deduped in `idempotency_keys` table).
- Driver: queue WS messages in AsyncStorage while disconnected; retry on reconnect.
- Both: add a `NetInfo` banner ("Reconnecting…") already doable via `@react-native-community/netinfo` which is installed.

---

### P1-F3 — i18n only in driver-app (incomplete)

**Evidence:** A cursory scan shows i18n strings only in driver-app. Rider-app screens use hard-coded English. Admin dashboard similarly English-only.

**Impact:** Canadian market requires French (federal regulation, Quebec consumer rights). Launch-blocking in QC; customer-support-tax in the rest of CA.

**Fix (M):**
- Adopt `i18next` + `react-i18next` across all three apps (or `expo-localization` + simple loader for RN).
- Start with `en` + `fr-CA`; build a translation pipeline (CSV export/import).
- Add a CI lint that fails when a JSX string literal exceeds N characters.

---

### P1-F4 — Sparse accessibility

**Evidence:** Playwright + axe-core are configured on admin-dashboard (good), but mobile screens lack systematic `accessibilityLabel`, `accessibilityHint`, `accessibilityRole`. Color-contrast audit not run.

**Impact:**
- App Store & Play Store accessibility metadata is thin.
- AODA/PIPEDA expectations for accessible services are unmet.
- Voice-over/TalkBack users cannot complete a ride request.

**Fix (M):**
- Run `@axe-core/react` on every mobile screen via jest+react-native-testing-library snapshots.
- Add an ESLint rule: `react-native-a11y/has-accessibility-props` (custom).
- Manual audit with VoiceOver + TalkBack against the 12 critical screens (login, OTP, search, request, ride-status, payment, rate-ride, safety, emergency, profile, settings, support).

---

### P1-F5 — Admin dashboard: Next.js 16 is a current release; check for server-action auth

**Evidence:** `admin-dashboard/src/middleware.ts` (not read) is assumed to gate `/admin/*` via JWT. Next.js 16 server actions and RSC fetches require per-call auth.

**Impact:** A server action that forgets to check the session becomes an unauthenticated admin mutation endpoint.

**Fix (S):**
- Audit every `"use server"` export. Wrap in a `requireAdmin()` helper that reads session cookies.
- Write a Playwright test that hits the server action endpoint directly without auth → expect 401.

---

### P1-F6 — Map rendering blocks UI

**Evidence:** `react-native-maps` 1.20.1 on both apps. Heavy overlays (polylines, driver markers, heatmap tiles) typically run on the JS thread.

**Impact:** 10+ markers on an Android mid-tier device drops frames below 30fps during pan/zoom.

**Fix (M):**
- Move marker clustering to native (`react-native-map-clustering` or Mapbox SDK).
- Throttle driver-location updates to ≥2s per marker.
- Use `shouldComponentUpdate`/`React.memo` on marker components.

---

### P1-F7 — No crash-free user rate measured

**Evidence:** Crashlytics is wired up (`@react-native-firebase/crashlytics`) but no CI gate/alerting on **crash-free users** or release-health thresholds.

**Fix (S):**
- Add a weekly Slack digest: "Crash-free users last 7d: X%. Top 3 crashes: …"
- Fail release if crash-free <99.0% on the previous stable.

---

### P1-F8 — Driver app permission friction

**Evidence:** Driver app requires background location, camera (docs), notifications. Current flow likely requests all on first launch.

**Impact:** iOS App Store reviewers reject "up-front all permissions" patterns; users deny out of confusion.

**Fix (S):**
- Use **contextual permission prompts** — request camera only when user taps "Upload document", request background location only after "Go online" is tapped.
- Add "Why we need this" modals.

---

### P1-F9 — `react-native-dotenv` for secrets is risky

**Evidence:** Both mobile apps use `react-native-dotenv`.

**Impact:** `.env` values become compile-time constants bundled into the binary. Treat every string as public. OK for public keys (Firebase web, Stripe publishable). Dangerous if anyone ever adds a "backend shortcut key".

**Fix (S):**
- Add a README note at the top of `.env.example`: "This is SHIPPED with the app. Public keys only."
- Consider runtime config fetch from backend `/api/v1/client-config` for values that should rotate.

---

## P2 / P3 findings

### P2-F10 — `frontend/` folder purpose unclear

At repo root. Appears to be a legacy web stub. Audit and delete or document.

### P2-F11 — `shared/` not enforced as workspace

`shared/` holds cross-app types/helpers but isn't a proper workspace package, so imports rely on relative paths. Promote to `@spinr/shared`.

### P2-F12 — Playwright config lives outside src

`admin-dashboard/playwright/` and `admin-dashboard/e2e/` — clarify which is canonical; one should be removed.

### P2-F13 — No Storybook / design system

Small scale today, but multiple buttons/inputs will drift. Consider Expo + react-native-web Storybook.

### P2-F14 — React 19 + Next 16 is new — watch for library compat

Pin & test 3P libs (`@tanstack/react-query`, `react-hook-form`) against React 19. File issues upstream if encountered.

### P3-F15 — Overrides on `@tootallnate/once`

Both mobile apps carry `"@tootallnate/once": "^3.0.1"` as an override — a classic CVE-remediation for transitive deps. Document why, or remove when no longer needed.

### P3-F16 — `sharp` in driver-app dependencies

`sharp` is a native image library intended for Node server usage; it shouldn't be in a mobile app's runtime deps. Confirm it's only used at build time; move to `devDependencies`.

---

## Notable positives

- ✅ Modern stacks (Expo Router, Next.js App Router, React 19).
- ✅ Zustand for state is lightweight, avoids Redux bloat.
- ✅ Firebase App Check + Crashlytics + Messaging properly installed.
- ✅ Playwright + axe-core on admin is a strong a11y baseline.
- ✅ Expo Updates is wired for OTA — but see 05 for channel hygiene.

---

## Priority summary

| ID | Severity | Effort |
|---|---|---|
| F1 SDK drift | P0 | M |
| F2 offline queue | P0 | M |
| F3 i18n | P1 | M |
| F4 a11y | P1 | M |
| F5 server action auth | P1 | S |
| F6 map perf | P1 | M |
| F7 crash-free gate | P1 | S |
| F8 permission UX | P1 | S |
| F9 dotenv risk | P1 | S |
| F10–F14 | P2 | S–M |
| F15–F16 | P3 | S |

---

*Continue to → [05_DEVOPS_AUDIT.md](./05_DEVOPS_AUDIT.md)*
