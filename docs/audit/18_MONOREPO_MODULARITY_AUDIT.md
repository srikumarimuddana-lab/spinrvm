# Spinr Monorepo Modularity Audit

**Date:** 2026-04-13
**Scope:** Full monorepo (rider-app, driver-app, admin-dashboard, backend, shared, frontend)

---

## Verdict: Modularity is Poorly Maintained

The project has the **structure** of a modular monorepo but not the **discipline**. Code duplication is the norm, backend routes are god files, and the `shared/` package is architectural theater — zero imports across all consumers.

---

## Scorecard

| Dimension | Grade | Reality |
|-----------|-------|---------|
| Cross-package code reuse | **C** | `shared/` used by mobile apps (189 imports) but not admin; malformed as a package |
| Backend layering | **D** | No service layer, god routes (2000+ LOC) |
| Component extraction (mobile) | **D** | Screens are kitchen sinks |
| Hook extraction (mobile) | **D** | 1 hook file per app |
| Type sharing | **F** | Types duplicated per package |
| Dead code management | **D** | `/frontend` is half-migrated legacy |
| Dependency direction | **C** | No circular deps, but also no layering |

---

## Critical Findings

### MOD-C1: `shared/` Package is Malformed

**Correction from earlier analysis:** `@shared/*` imports ARE used extensively via TypeScript path aliases:

- rider-app: **97 imports across 35 files**
- driver-app: **92 imports across 39 files**
- admin-dashboard: **0 imports** (not integrated)
- frontend (legacy): 0 imports

However, the `shared/` package is **not a proper npm package** — it's just a folder used via path aliases:

**File:** `shared/package.json`
```json
{
  "dependencies": {
    "@react-native-community/netinfo": "^11.4.1",
    "react-native-worklets": "^0.5.1"
  },
  "devDependencies": { "@types/node": "^25.3.0" }
}
```

Missing fields:
- No `name` — cannot be referenced as a workspace package
- No `version` — no versioning discipline
- No `exports` — no public API contract; any file in `shared/` is importable (leaky)
- No `main` / `types` — no canonical entry point
- No `private` flag

**Secondary issue:** `shared/tsconfig.json` extends `../driver-app/tsconfig.json` — the shared package depends on a specific consumer's config. This is a reversed dependency direction.

**Impact:**
- No enforced public API — any internal file in `shared/` can be imported
- Breaking changes can't be versioned or detected
- Admin dashboard can't use it (needs explicit workspace package)
- Path alias-based sharing works but isn't portable (won't work if one app is extracted from monorepo)

**Fix:**
1. Add proper `package.json` with `name: "@spinr/shared"`, version, and `exports` map
2. Break circular tsconfig dependency (`shared/tsconfig.json` should not extend from an app)
3. Migrate admin-dashboard to use shared
4. Define explicit public API via `exports` — e.g., only expose `@spinr/shared/api`, `@spinr/shared/store`, `@spinr/shared/config`

### MOD-C2: `/frontend` is Legacy but Still Wired Up

- 39 TypeScript files mirroring `rider-app` structure
- 0 imports to/from other packages (it's standalone)
- CI comment in `.github/workflows/ci.yml:161` says: `"frontend/ is the legacy combined app, still tested by frontend-test"`
- Still has its own test/build jobs in CI (`ci.yml:99-120`)
- Still triggers EAS builds (`eas-build.yml:7`)
- README.md still documents it as THE frontend at line 12
- `package.json` shows `android:driver` and `ios:driver` variants — it was the combined rider+driver app before the split

**This is a partially-completed migration.** The team split the legacy `frontend/` into `rider-app/` + `driver-app/` but never:
- Removed `/frontend` from CI
- Updated README to reflect new structure
- Migrated `/frontend`-specific features (e.g., `AppMap.web.tsx`) to the new apps

**Recommendation:** Complete the migration:
1. Diff `/frontend` vs `rider-app` + `driver-app` to find unmigrated features
2. Port missing pieces to new apps
3. Remove `/frontend` CI jobs, EAS triggers, and README references
4. Delete `/frontend` directory

### MOD-C3: 13+ Duplicated Screens Between Rider & Driver Apps

Identical filenames exist independently in both apps:
```
become-driver.tsx, profile-setup.tsx, otp.tsx, login.tsx,
emergency-contacts.tsx, legal.tsx, report-safety.tsx,
settings.tsx, support.tsx, saved-places.tsx, ...
```

Estimated ~2,000+ LOC of near-identical auth/onboarding logic duplicated.

---

## High Findings

### MOD-H1: Backend Has No Service/Domain Layer

Routes contain business logic directly:

| File | LOC | Business Logic Inline |
|------|-----|----------------------|
| `routes/drivers.py` | **2,230** | 30+ handlers, config, location, push registration |
| `routes/rides.py` | **1,501** | `match_driver_to_ride()` dispatch algorithm |
| `features.py` | **1,112** | Push notifications + airport fees + pricing calcs |
| `db_supabase.py` | **1,014** | Direct DB queries routes import and call |

Routes import `db` directly (`from ..db import db`) and embed:
- Driver dispatch algorithms
- Fare/fee calculations
- State machine transitions
- Push notification logic

**Fix:** Create `backend/services/` with `RideService`, `DriverService`, `PaymentService`, `DispatchService`. Cap routes at ~300 LOC.

### MOD-H2: Rider App Has Monolithic Screens

| Screen | LOC | Concerns Mixed |
|--------|-----|---------------|
| `app/driver-arriving.tsx` | **1,200** | Map + WebSocket + UI state + navigation + alerts |
| `app/ride-options.tsx` | **1,041** | Vehicle selection + pricing + booking + promos |
| `app/search-destination.tsx` | **820** | Autocomplete + location + GPS |
| `app/payment-confirm.tsx` | **661** | Payment UI + booking + validation |
| `app/(tabs)/index.tsx` | **647** | Home + map + ride status |

Only **1 file** in `/components` and **1 file** in `/hooks`. Everything else is inline in screens.

### MOD-H3: Driver App Has Same Issues

| File | LOC |
|------|-----|
| `driver/profile.tsx` | 1,233 |
| `driver/index.tsx` | 743 |
| `driver/earnings.tsx` | 731 |
| `driver/payout.tsx` | 729 |
| `hooks/useDriverDashboard.ts` | 649 (god hook) |

### MOD-H4: Admin Dashboard is an Island

- `dashboard/service-areas/page.tsx`: **1,328 LOC**
- `lib/api.ts`: **667 LOC** (own API client, not shared)
- `dashboard/pricing/_tabs/fare-config.tsx`: **788 LOC**
- No shared types with backend (Pydantic models not exported)

---

## Medium Findings

### MOD-M1: Stores Mix Concerns
- `rider-app/store/rideStore.ts` (528 LOC) — Zustand + API calls + WebSocket triggers
- `driver-app/store/driverStore.ts` (642 LOC) — same pattern, independently implemented
- Both contain Haversine distance calculations

### MOD-M2: No Shared Validation Library
Each app implements its own phone/email validators. Backend has `backend/validators.py` (544 LOC) not mirrored on client.

### MOD-M3: No Shared Error Handling Pattern
`shared/services/firebase.ts:recordError()` is exported but never imported.

### MOD-M4: Utility Modules Lack Cohesion
- `backend/utils/error_handling.py` (574 LOC)
- `backend/utils/analytics.py` (388 LOC)
- `backend/utils/rate_limiter.py` (341 LOC)
- Validators scattered, not consistently applied in routes

---

## Concrete Metrics

| Metric | Value |
|--------|-------|
| `@shared/*` imports (rider-app) | 97 across 35 files |
| `@shared/*` imports (driver-app) | 92 across 39 files |
| `@shared/*` imports (admin-dashboard) | 0 (not integrated) |
| Duplicate screen files (rider vs driver) | 13+ |
| API client implementations | 3 (shared/api used by mobile, admin has its own, backend has utils) |
| Backend routes over 1000 LOC | 3 |
| Rider app screens over 500 LOC | 5 |
| Reusable components in rider-app | 1 |
| Custom hooks in rider-app | 1 |
| Legacy packages still in CI | 1 (`/frontend`) |

---

## Remediation Plan

### Phase 1: Foundation (Week 1)
1. **Fix `shared/package.json`** — add `name`, `version`, `exports`, `main`, TypeScript `types`
2. **Complete `/frontend` → `rider-app`/`driver-app` migration** — diff to find unmigrated features, port them, remove CI jobs, update README, delete
3. **Audit `@shared/*` path aliases** — ensure they resolve consistently across apps
4. **Document `shared/` contract** — what belongs here vs app-specific

### Phase 2: Consolidation (Week 2-3)
5. **Consolidate API clients** — delete app-specific clients, import from `shared/api/client`
6. **Extract shared validators** — create `shared/validators/` with phone, email, coords
7. **Consolidate auth store** — move rider/driver auth state to `shared/store/authStore`
8. **Extract shared error handler** — consistent error shape and logging

### Phase 3: Backend Service Layer (Week 3-4)
9. **Create `backend/services/`** directory structure
10. **Extract `RideService`** — matching, state transitions, fare calculation
11. **Extract `DriverService`** — availability, earnings, location
12. **Extract `PaymentService`** — Stripe integration, webhook handling, refunds
13. **Extract `DispatchService`** — driver matching algorithm from `routes/rides.py:78-170`
14. **Refactor routes** to thin controllers — delegate to services

### Phase 4: Mobile Decomposition (Week 4-5)
15. **Break up rider-app god screens** — extract hooks (`useDriverArrival`, `useRideEstimate`) and components (`VehicleSelector`, `DriverArrivingMap`, `PaymentMethodList`)
16. **Break up driver-app god screens** — same treatment
17. **Split `useDriverDashboard`** into focused hooks (`useActiveRide`, `useEarnings`, `useDriverStatus`)
18. **Extract common ride polling** to shared hook

### Phase 5: Admin Integration (Week 5-6)
19. **Share types with backend** — generate TypeScript types from Pydantic schemas
20. **Decompose admin pages** — extract components from 1000+ LOC pages
21. **Unify API client** across admin and mobile apps

---

## Quick Wins (Can Do Today)

1. Fix `shared/package.json` (30 min) — add proper `name`, `exports`, `main`
2. Update README.md to reflect split-app structure (`rider-app` + `driver-app`, not `frontend`)
3. Add `.eslintrc` rule to cap file size at 500 LOC (warn) and 800 LOC (error)
4. Add `madge` or `dependency-cruiser` to CI to detect circular deps
5. Add a CI check ensuring new screens under 400 LOC

---

## What Good Looks Like

```
shared/
├── api/          (single API client, used by all)
├── store/        (auth, location - shared state)
├── validators/   (phone, email, coordinates, monetary)
├── types/        (generated from backend Pydantic)
├── utils/        (logger, formatters, Haversine)
└── components/   (cross-platform - SOSButton, ErrorBoundary)

backend/
├── routes/       (< 300 LOC each, thin controllers)
├── services/     (business logic, domain rules)
├── repositories/ (DB access, wraps db_supabase)
├── schemas/      (Pydantic request/response)
└── utils/        (cross-cutting only)

rider-app/
├── app/          (screens < 400 LOC, mostly composition)
├── components/   (ride-specific UI, reusable)
├── hooks/        (useRidePolling, useDriverTracking, useRideEstimate)
└── store/        (rider-specific state only)
```

---

## Conclusion

The monorepo architecture is correct in shape but requires roughly **4-6 weeks** of disciplined refactoring to become truly modular. The biggest unlock is **fixing the `shared/` package first** — without a working shared layer, all duplication will continue. Backend service-layer extraction is the next-biggest win since `drivers.py` at 2,230 LOC is the single largest code smell.
