# Dependency Upgrade Runbook

Spinr mobile apps (rider-app, driver-app) have native modules with hard OS-version
floors and SDK compatibility matrices. Upgrading dependencies without a structured
process has historically caused cascading Gradle/Xcode failures, broken screens, and
wasted EAS build credits. This runbook is the required process for any dependency change.

---

## Expo SDK Compatibility Matrix

| Expo SDK | React Native | Min iOS | Min Android API | Notes |
|----------|-------------|---------|-----------------|-------|
| 57 (current) | 0.86 | **16.4** (expo-build-properties 57.x hard-validates deploymentTarget ≥ 16.4) | 24 (apps pin minSdk 25 for LogRocket) | React 19.2; New Architecture only for our stack (reanimated 4 / worklets) |
| 55 | 0.85 | 16.0 | 24 | New Architecture stable |
| 54 | 0.74 | 13.0 | 23 | |
| 53 | 0.73 | 13.0 | 23 | |
| 52 | 0.72 | 13.0 | 23 | |

**Key lesson from SDK 54→55 migration:** iOS minimum floor jumped from 13→16, silently
dropping ~5% of iPhones. Always pin `minimumOsVersion` explicitly in `app.config.ts`
so future SDK bumps require an intentional edit, not an accidental default change.

---

## Step 0 — Before Touching Any Version (Mandatory)

Run this audit before editing any `package.json`:

```bash
# Baseline — note ALL mismatches before changing anything
cd rider-app && npx expo install --check
cd driver-app && npx expo install --check

# Doctor check — catches broader config problems
cd rider-app && npx expo doctor
cd driver-app && npx expo doctor

# Identify native-module packages in the planned upgrade
# (these require a full EAS build to validate, not just yarn install)
grep -E "react-native-|expo-" rider-app/package.json
```

Check the Expo SDK changelog for the target version:
- Breaking changes section
- Removed APIs
- Minimum OS target changes
- New Architecture implications

---

## Step 1 — Risk-Triage Every Changed Package

Classify each package before upgrading:

| Risk | Criteria | Validation required |
|------|----------|---------------------|
| 🔴 High | Native module (Kotlin/Swift code), payment, auth, maps, camera, notifications | EAS build + manual smoke test of affected screens |
| 🟡 Medium | Navigation, UI component library, state management | Jest test suite + screen walkthrough |
| 🟢 Low | TypeScript types, pure-JS formatters, test utilities | `yarn install` + `tsc --noEmit` sufficient |

**Native module indicators** — a package is native if it has any of:
- `android/` or `ios/` directory in its npm package
- `app.plugin.js` at its root
- Listed under `expo.plugins` in `app.config.ts`

---

## Step 2 — Sequencing Rule (Never Batch High-Risk)

Upgrade in this order, committing and building after each group:

```
1. Core SDK bump alone
   → yarn install → expo install --check → EAS build → ✅ green → commit

2. High-risk native modules (one logical group at a time)
   → yarn install → expo install --check → EAS build per group → commit

3. Medium-risk JS packages (can batch within same category)
   → yarn install → tsc --noEmit → Jest → commit

4. Low-risk / types-only (bulk)
   → yarn install → tsc --noEmit → commit

5. Payment packages (always isolated, always last)
   → yarn install → EAS build → full payment flow smoke test → commit
```

**Why not batch?** The SDK 54→55 migration took 4+ failed EAS builds because
async-storage, reanimated, worklets, and expo-modules-core were all changed at once.
When Gradle failed, there was no way to isolate which package caused it.

---

## Step 3 — Screen Impact Map

Before upgrading, identify which Spinr screens are affected. Use this map:

| Package | Spinr screens at risk |
|---------|----------------------|
| `expo-location` / `react-native-maps` | Ride booking map, driver nav, live ETA |
| `@stripe/stripe-react-native` | Add card, payment sheet, manage-cards |
| `expo-notifications` | Ride offer push (driver), ride status (rider) |
| `react-navigation` / `expo-router` | Every screen transition, deep links, back stack |
| `react-native-reanimated` | Ride progress animation, map markers, bottom sheet |
| `expo-camera` / `expo-image-picker` | Driver document upload, profile photo |
| `@react-native-async-storage` | Auth token persistence, onboarding state |
| `expo-secure-store` | JWT storage |
| `react-native-maps` | Pickup/dropoff pin, driver location dot |
| `expo-modules-core` | Foundation for all Expo packages — always high-risk |

---

## Step 4 — Post-Upgrade Validation Checklist

After every upgrade, before committing:

```
□ expo install --check passes with 0 warnings
□ expo doctor passes (or known acceptable warnings documented)
□ tsc --noEmit passes (TypeScript clean)
□ yarn test passes (Jest unit suite)
□ No package-lock.json exists in rider-app/ or driver-app/ (yarn-only project)
□ minimumOsVersion is explicitly set in both app.config.ts files
□ expo.install.exclude is up to date (intentionally-pinned packages protected)
```

For any 🔴 High-risk package, also:

```
□ EAS Android build passes
□ EAS iOS build passes (or iOS smoke tested on device)
□ Affected screens manually verified (see screen impact map above)
```

---

## Step 5 — CI Gates (Automated)

The `mobile-dep-check.yml` workflow runs on every PR that touches `package.json` or
`yarn.lock` in either mobile app. It enforces:

1. `expo install --check` — zero version drift from SDK expectations
2. `tsc --noEmit` — TypeScript clean build
3. `minimumOsVersion` explicitly set in both `app.config.ts` files
4. No `package-lock.json` in yarn-managed apps

A PR that fails any of these gates must be fixed before merge.

---

## Expo SDK Upgrade Checklist (Full SDK Version Bump)

Use this when bumping the `expo` package itself (e.g. SDK 55 → 56):

```
□ Read the official SDK changelog: https://expo.dev/changelog
□ Note: minimum iOS change? minimum Android API change? New Architecture required?
□ Update minimumOsVersion in both app.config.ts files if iOS floor changed
□ Run: expo install (auto-resolves all SDK-managed packages to expected versions)
□ Run: expo install --check (verify alignment)
□ Run: expo doctor (catch config issues)
□ Check each package in expo.install.exclude — still intentionally pinned?
□ Update the compatibility matrix table at the top of this file
□ EAS build both apps before opening PR
□ Smoke test: ride booking flow (rider-app), offer accept + navigation (driver-app)
□ Smoke test: Stripe payment sheet (rider-app)
□ Smoke test: push notifications received (both apps)
```

---

## Payment Package Upgrade Protocol

`@stripe/stripe-react-native` upgrades require extra care — payment failures are
P0 incidents. Follow this protocol:

1. Check the [Stripe React Native changelog](https://github.com/stripe/stripe-react-native/blob/master/CHANGELOG.md) for breaking API changes
2. Verify these APIs still exist in the new version (all used in rider-app):
   - `StripeProvider` (root layout)
   - `useStripe` hook
   - `initPaymentSheet` / `presentPaymentSheet`
   - `confirmPayment`
   - `createPaymentMethod`
   - `CardField` component
   - `CardFieldInput.Details` type
3. EAS build → manual test of full payment flow with Stripe test card `4242 4242 4242 4242`
4. Test 3DS flow with `4000 0025 0000 3155`
5. Test decline with `4000 0000 0000 9995`

driver-app does NOT use the Stripe React Native SDK — its Stripe integration is
entirely through backend REST API + browser deep-link. Do not add the SDK there.

---

## Lockfile Hygiene

This project uses **yarn** for both mobile apps. npm must not be used.

Rules:
- Never run `npm install` in `rider-app/` or `driver-app/` — it creates `package-lock.json`
- If `package-lock.json` appears, delete it and commit the deletion immediately
- Always use `yarn install --frozen-lockfile` in CI
- Always use `yarn add` / `yarn remove` for dependency changes

The `os-target-check` CI job enforces the no-`package-lock.json` rule automatically.

---

## Version History

| Date | SDK | iOS min | Android min | PR |
|------|-----|---------|-------------|----|
| 2026-08 | 57 | 16.4 | 24 (app pins 25) | #605 (Dependabot expo-stack bump) + #607/#609 (app-level completion) + SDK 57 dependency-alignment branch, 2026-08-11 (see `docs/change-log/2026-08-11-sdk57-dependency-alignment.md`) |
| 2026-05-03 | 55 | 16.0 | 24 | #406 |
| (initial) | 54 | 13.0 | 23 | — |
