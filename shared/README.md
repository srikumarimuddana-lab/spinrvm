# @spinr/shared

Cross-surface TypeScript code shared by `rider-app`, `driver-app`, and (for a
narrow slice of modules) `admin-dashboard`. Ships as raw `.ts`/`.tsx` source —
there is no build step in the normal dev/build flow (`package.json` has no
`main`/`module`/`types` field; `exports` points straight at source files).
`npm run build` (`tsc --project tsconfig.build.json` → `dist/`) exists but
isn't what consumers resolve.

Package name: `@spinr/shared`. All three consuming apps depend on it as
`"@spinr/shared": "file:../shared"` in their own `package.json`.

## Directory structure

| Path | Purpose |
|---|---|
| `theme/` | Design tokens (`index.ts`: `lightColors`/`darkColors`/`ThemeColors`) and `ThemeContext.tsx` (`ThemeProvider`/`useTheme()`). Full writeup, including admin-dashboard's relationship to it, in `docs/design/rider-driver-app-design-system.md` — don't re-derive it here. |
| `store/` | Zustand stores: `authStore.ts` (auth/session state, token refresh, cached profile), `locationStore.ts` (current/last-known device location, persisted), `vehicleTypeStore.ts` (vehicle-type/marker config, persisted). |
| `api/` | `client.ts` (main fetch wrapper — auth headers, timeout/deadline propagation, refresh-on-401; RN-only, imports `Platform`), `cachedClient.ts` (cache-aware variant), `upload.ts` (multipart upload helper), `places.ts` (Google Places Autocomplete types, platform-agnostic), `queryClient.ts` (shared TanStack Query setup). |
| `hooks/queries/` | TanStack Query hooks (`driverQueries.ts`, `notificationQueries.ts`). |
| `hooks/` (top level) | Misc RN hooks: safety panel config, hold-to-confirm, emergency contacts, places autocomplete, LogRocket privacy screen, exit-on-back-press, completed-route refresh. |
| `components/` | Shared RN UI: `Button`, `Card`, `Input`, `FormScreen`, `ErrorBoundary`/`ErrorScreen`, `OfflineBanner`, `ForceUpdateOverlay`, `CustomAlert`, map-related (`AppMap`(+`.web`), `CarMarker`, `RouteLine`, `RoutePins`), safety UI (`SOSButton`, `SafetyOverlay`, `SafetySheet`, `SafetyShield`), `SupportScreen`, `AiAuroraBackground`. |
| `services/` | `firebase.ts` (FCM/Crashlytics/App Check via `@react-native-firebase` modular API, native-build only), `errorReporting.ts` (Sentry/Crashlytics/console facade), `logRocketInstance.ts` (holds each app's LogRocket handle). |
| `config/` | `spinr.config.ts` + `index.ts` (backend URL resolution, env-driven config), `firebaseConfig.ts`, `legalDocs.ts` (ToS/Privacy Policy links/versions). |
| `cache/` | `appCache` — TTL-based cache wrapper (`CACHE_CONFIG`, `CACHE_KEYS`) used by the API clients and stores. |
| `types/` | `types/api/` (`money.ts`, `errors.ts`, `ride.ts`, `route.ts`, `user.ts`, `wsEvents.ts`, `pagination.ts` — backend API contract types, platform-agnostic), plus `ai.ts` (AI chat/action types) and `safety.ts`. |
| `validators/` | Platform-agnostic input validators returning a `{valid: true} \| {valid: false, reason}` union — replaces logic that used to be duplicated per app. |
| `utils/` | Grab-bag: `logger.ts`, `pii.ts` (PII scrubbing for logs), `responsive.ts` (spacing/font scale), `routeSegments.ts` (route-quality/GeoJSON helpers, used by admin-dashboard's live maps too), `aiLocationMessages.ts`, `gpsSmoothing.ts`, `markerPlayback.ts`, `vehicleTracking.ts`, `sosLocation.ts`, `bookingDistanceGuard.ts`, `appRating.ts`, `otaVersion.ts`, `toastMessage.ts`, `placesSession.ts`, `fixFeed.ts`. |
| `errors/` | `errorPresentation.ts` — maps the backend's structured `error.code` to a toast title/severity. Pure, no RN/API-client dependency. |
| `analytics/` | Deliberate no-op stub (Firebase Analytics was removed from the mobile apps); preserves call signatures so screens don't need call-site changes if analytics is reintroduced. |
| `auth/` | `sessionMarker.ts` — dependency-free "session ended" disk marker read by the driver app's headless background-location task, which can't pull in the full auth store. |
| `constants/` | `routeMapStyle.ts` — map style constants, consumed by both the mobile apps and admin-dashboard's ride maps. |
| `assets/` | Car marker PNGs (`car_marker*.png`, @2x/@3x), `car-top.svg`, plus the generator scripts (`generate-car.js`, `generate_markers.py`) and `marker-src/` used to produce them. |
| `build-types/` | `peer-deps.d.ts` — ambient module stubs (e.g. `firebase/app`, `firebase/auth`) so `tsc --project tsconfig.build.json` can emit declarations without installing every RN/Expo peer as a devDependency. |
| `.npmrc` | `legacy-peer-deps=true` — deliberately keeps this package's declared `peerDependencies` (react-native, expo-image, netinfo, react) out of its own lockfile; see the comment in the file for why (npm 7+ auto-install would otherwise pull ~500 RN toolchain packages into a lockfile no build ever installs). |

Each major subdirectory with non-trivial logic has its own `__tests__/`
(`api/`, `components/`, `constants/`, `utils/`, `validators/`) run via each
consuming app's own test runner — there's no standalone `shared` test command
beyond `typecheck`.

## How consumers import it — two different mechanisms

**1. `@shared/*` path alias — rider-app and driver-app only.**
Both apps alias `@shared` → `../shared` at both the bundler and type-checker
level:
- `babel.config.js`: `module-resolver` plugin, `alias: { '@shared': '../shared' }`
- `metro.config.js`: `resolver.extraNodeModules['@shared']` + `watchFolders`
  includes `../shared` (so edits trigger a Metro rebuild in dev)
- `tsconfig.json`: `paths: { "@shared/*": ["../shared/*"] }`

This resolves directly into `shared/`'s raw source tree (not through the
`exports` map), so it can reach *anything* under `shared/`, not just what's
listed in `package.json`. In practice this is what rider-app/driver-app code
uses exclusively — e.g. `import api from '@shared/api/client'`,
`import { useTheme } from '@shared/theme'`,
`import SpinrConfig from '@shared/config/spinr.config'`. 169+ files in
driver-app alone import via `@shared/*`.

Both apps also list `@spinr/shared: file:../shared` in `package.json`
(creating a `node_modules/@spinr/shared` symlink), but grep found **no**
`@spinr/shared`-style imports anywhere in rider-app or driver-app source —
only the `@shared/*` alias is actually used there.

**2. `@spinr/shared` package import (the `exports` map) — admin-dashboard.**
admin-dashboard (Next.js, no Metro/Babel module-resolver) depends on
`@spinr/shared: file:../shared` and imports through the subpath `exports`
declared in `shared/package.json`, e.g.:
```ts
import { toGeoJsonMultiLineString } from '@spinr/shared/utils/routeSegments';
import type { AiAction } from '@spinr/shared/types/ai';
import { buildLocationChoiceMessage } from '@spinr/shared/utils/aiLocationMessages';
```
plus `@spinr/shared/constants/routeMapStyle`. This only reaches whatever's
listed in the `exports` map (not arbitrary files under `shared/`, unlike the
`@shared/*` alias). There's no `@shared/*`-style alias configured anywhere in
admin-dashboard.

## admin-dashboard's actual relationship to `shared/`

Partial, deliberately narrow — confirmed by grep, not assumed:

- **Imports directly** (via `@spinr/shared/...`): `utils/routeSegments`,
  `utils/aiLocationMessages`, `types/ai`, `constants/routeMapStyle`. These are
  the platform-agnostic pieces (no `react-native` import) used by admin's ride
  maps and AI console.
- **Does not import** — and has its own separate port/mirror instead —
  anything RN-specific:
  - `theme/` — admin's Tailwind `globals.css` has its own hardcoded copy of
    the color tokens (see `docs/design/rider-driver-app-design-system.md` and
    `docs/change-log/2026-07-29-admin-dashboard-brand-token-port.md`); a token
    edit in `shared/theme/` does **not** propagate to admin-dashboard.
  - `api/client.ts` — admin has its own `src/lib/api/client.ts`, whose
    `RateLimitError` is a deliberate mirror of `shared/api/client.ts`'s
    (comment at `admin-dashboard/src/lib/api/client.ts:14`), not an import —
    `shared/api/client.ts` imports RN's `Platform` and can't run in Next.js.
  - `components/` (`CarMarker`, etc.) — admin's vehicle-types page mirrors the
    same icon set by comment reference, not by importing the component.
  - `store/`, `cache/`, `services/`, `config/`, `hooks/` (RN hooks) — not
    referenced by admin-dashboard at all.

So: admin-dashboard is a real, direct `@spinr/shared` consumer for a handful
of pure-TS utility/type/constant modules, and a parallel-port ("mirrors ...")
of everything that's RN-coupled — it never imports `shared/` for anything
that touches `react-native`, Zustand, or Expo.
