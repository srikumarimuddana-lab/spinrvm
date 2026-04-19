# P3 — Rider App Hardening: Fix Before Scale

These items improve reliability, maintainability, and performance as the rider base grows.
None are launch blockers, but all should be resolved in the first month post-launch.

**Estimated total effort:** ~ongoing / 2–3 week sprint

---

## R-P3-1 · Rate Limiting on Rider-Specific Endpoints

- `POST /rides` — max 5 ride creations per minute per user
- `POST /rides/{id}/cancel` — max 10 cancellations per hour per user
- `GET /promo/available` — max 20 calls per minute (promo enumeration vector)
- `POST /promo/validate` — max 10 per minute (brute-force promo codes)

**File to fix:** `backend/routes/rides.py`, `backend/routes/promo.py`

---

## R-P3-2 · FCM Token Rotation Not Handled

**What's wrong:** FCM tokens can be rotated by the platform. The app registers the
token once per auth session but has no `onTokenRefresh` listener. After rotation,
push notifications stop until the user logs out and back in.

**File to fix:** `rider-app/app/_layout.tsx` + `shared/services/`

**How to fix:** Add `messaging().onTokenRefresh(token => registerFCMToken(token))`
alongside the initial registration.

---

## R-P3-3 · Driver Location Marker Not Memoized

**What's wrong:** Every WebSocket `driver_location_update` message causes a full
re-render of the map screen because the driver position is stored in top-level state.
The `CarMarker` should be wrapped in `React.memo` and only re-render when lat/lng changes.

**File to fix:** `rider-app/app/driver-arriving.tsx`, `rider-app/app/ride-in-progress.tsx`,
`shared/components/CarMarker.tsx`

---

## R-P3-4 · Activity FlatList Not Paginated

**What's wrong:** `activity.tsx` loads the full ride history in one request. After
100+ rides, this is a noticeable load time and memory issue.

**File to fix:** `rider-app/app/(tabs)/activity.tsx` + `backend/routes/rides.py`

**How to fix:** Use cursor-based pagination (`?limit=20&before=<ride_id>`).
Implement `onEndReached` in FlatList to load more.

---

## R-P3-5 · Home Screen Fires Too Many Requests on Mount

**What's wrong:** `/(tabs)/index.tsx` fires: GPS request, weather API, saved places
fetch, and nearby drivers fetch — all simultaneously on every mount (not just first mount).
Tab switches re-trigger all of these.

**File to fix:** `rider-app/app/(tabs)/index.tsx`

**How to fix:** Cache results in the store with a TTL. Only refetch if data is stale
(> 5 minutes old). Use `useFocusEffect` with a staleness check instead of `useEffect`.

---

## R-P3-6 · Scheduled Ride — No Past-Time Validation in UI

**What's wrong:** The date/time picker in `ride-options.tsx` for scheduling does not
prevent the user from selecting a past time. Backend should also reject it, but the
UX is poor if users can "select" an invalid time.

**File to fix:** `rider-app/app/ride-options.tsx`

**How to fix:**
```typescript
minimumDate={new Date(Date.now() + 15 * 60 * 1000)} // at least 15 min from now
```

---

## R-P3-7 · Test Coverage: Add Missing Store Action Tests

Add unit tests for:
- `createRide()` — double-booking guard path
- `cancelRide()` — after driver_arrived (should throw)
- `hydrateActiveRide()` — stale ride (404 from backend)
- `syncOfflineRequests()` — queue replay + max retry
- `triggerEmergency()` — network failure path (user alert shown)
- `walletStore.payWithWallet()` — insufficient balance rejection

---

## R-P3-8 · Add ErrorBoundary to Each Major Screen

Currently only the root `_layout.tsx` has an ErrorBoundary. Add per-screen boundaries
to prevent a single screen crash from killing the whole session.

**Files to wrap:** `driver-arriving.tsx`, `ride-in-progress.tsx`, `ride-completed.tsx`,
`ride-options.tsx`, `payment-confirm.tsx`

---

## Checklist

- [ ] R-P3-1 Rate limits on rides, cancellation, promo endpoints
- [ ] R-P3-2 FCM onTokenRefresh listener added
- [ ] R-P3-3 CarMarker memoized — only re-renders on lat/lng change
- [ ] R-P3-4 Activity FlatList paginated (cursor-based)
- [ ] R-P3-5 Home screen requests cached with TTL, not fired on every tab switch
- [ ] R-P3-6 Scheduled ride date picker enforces minimum future time
- [ ] R-P3-7 Unit tests for all missing store action paths
- [ ] R-P3-8 ErrorBoundary on each major ride-flow screen
