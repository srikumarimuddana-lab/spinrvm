# Spinr TODO List

_Last audited: 2026-04-27. Items marked ✅ were verified implemented in the codebase._

---

## Critical Issues (Must Fix)

_No open critical issues. All previously listed items were resolved:_

- ✅ **Driver App: Push notifications** — FCM + `expo-notifications` fully wired; background handler persists offers to AsyncStorage; cold-start hydration on resume. (`_layout.tsx`, `useDriverDashboard.ts`)
- ✅ **Driver App: WebSocket reconnection** — Exponential backoff `[1s, 2s, 5s, 10s, 30s]` + ±500 ms jitter; AppState listener reconnects on foreground; proactive close on background. (`hooks/useDriverDashboard.ts`)
- ✅ **Driver App: Location batching** — Buffers up to 500 points; batch-uploads to `/drivers/location-batch` every 30 s; retries up to 3×; persists buffer to AsyncStorage across crashes. (`hooks/useDriverDashboard.ts`)

---

## High Priority

- [ ] **Backend: CORS wildcard blocked in prod but `ALLOWED_ORIGINS` not set by default**
  - `middleware.py:354` raises `RuntimeError` if `"*"` is in origins on production — correct guard exists.
  - Remaining gap: deployment docs / Railway env var checklist does not explicitly require `ALLOWED_ORIGINS` to be set before go-live.
  - Action: Add `ALLOWED_ORIGINS` to the Railway required-env-vars list in `docs/` and `.env.example`.
  - File: `backend/.env.example`, `docs/deploy.md`

- [ ] **Backend: `server.py` still mounts duplicate root + `/api/v1` routes**
  - `server.py:152-163` mounts routers at both `/api/v1` and `/` for backward compatibility.
  - Stale root mounts widen the attack surface and confuse rate-limit rules.
  - Action: Audit which root-mounted routes are still called by mobile clients; deprecate and remove after a version bump.
  - File: `backend/server.py`

- [ ] **4-digit OTP — deliberate product decision, not a bug**
  - `backend/dependencies/__init__.py:42-46` documents the trade-off (1/10,000 guess odds + 5-attempt lockout = acceptable).
  - If security posture changes before launch, raise `OTP_LENGTH` and `PICKUP_OTP_LENGTH` there in one place; both auth + pickup OTPs update automatically.
  - Action: No change needed unless security review mandates it. Remove from critical path.

---

## Medium Priority

- [ ] **Admin Dashboard: Auth exists but review coverage**
  - `admin-dashboard/src/app/login/` and `register/` routes exist.
  - Action: Verify JWT validation middleware is applied to all `/dashboard/*` routes; confirm no unauthenticated read endpoints on admin API.
  - File: `admin-dashboard/src/app/layout.tsx`, `backend/routes/admin/`

- [ ] **Driver App: External navigation (Google Maps / Apple Maps deep link)**
  - Currently opens native maps app for turn-by-turn, leaving Spinr.
  - Action: Evaluate in-app navigation via `react-native-maps-directions` polyline + maneuver steps. Low risk since route polyline is already rendered.
  - File: `driver-app/app/driver/(tabs)/index.tsx`

- [ ] **Backend: Duplicate route mounts increase surface area**
  - Same as High Priority item above — needs a cleanup pass before production.

---

## Low Priority

- [ ] **API versioning cleanup**
  - `/api/v1` prefix is already applied (`server.py:152`). Root-level duplicates are the remaining gap (see High Priority).
  - Action: Remove legacy root mounts after client version gate.

- [ ] **Dark mode — driver app**
  - Theme system detection exists (89 references to `colorScheme` / theme in driver-app) but not consistently applied across all screens.
  - Action: Audit screens without `useColorScheme()` and apply dark palette tokens.
  - File: `driver-app/` (multiple screens)

- [ ] **Error messages — rider app**
  - Generic "Something went wrong" fallbacks in several screens; could surface more actionable copy.
  - Action: Triage on a screen-by-screen basis during UX polish sprint.

---

## Open Branches (Unmerged Work)

These branches exist on the remote and need review/merge or close decision:

| Branch | Purpose | Action |
|---|---|---|
| `claude/fix-coverage-baseline-cr2026001` | Test coverage baseline fix | Review and merge |
| `claude/deploy-admin-panel-vercel-aXWm2` | Admin panel Vercel deploy config | Review and merge |
| `claude/staging-smoke-runbook` | Staging smoke test runbook | Review and merge |
| `fix/staging-smoke-ruff` | Ruff lint fix for staging | Review and merge |

---

## Completed / Verified Implemented

- ✅ Push notifications (FCM + expo-notifications, background + cold-start)
- ✅ WebSocket reconnection (exponential backoff + jitter + AppState integration)
- ✅ Location batching (30 s interval, 500-point buffer, AsyncStorage persistence)
- ✅ Geofence arrival verification (Haversine, configurable radius via `/drivers/config`)
- ✅ Ride offer countdown configurable via `/drivers/config` (not hardcoded)
- ✅ Race condition on ride acceptance (409 + `ride_taken` WS event handled in `driverStore.ts:404-415`)
- ✅ Earnings export (`/drivers/earnings/export` + `exportEarnings()` in store + UI in `payout.tsx`)
- ✅ JWT secret weakness guard (fails fast on known-weak placeholders in production, `config.py:87-97`)
- ✅ API versioned at `/api/v1` (`server.py:152`)
- ✅ Tip collection (WebSocket `tip_received` event + store + UI)
