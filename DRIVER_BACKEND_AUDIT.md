# Driver App ↔ Backend Integration Audit

Branch: `claude/audit-driver-backend-EiiRL`
Date: 2026-04-22
Scope: Cross-reference every HTTP/WS call in `driver-app/` against the FastAPI routers in `backend/`.

All driver-app HTTP calls go through `shared/api/client.ts`, which auto-prepends `/api/v1` to the URL and injects the bearer token. WebSocket URL is `wss://<host>/ws/driver/{user_id}`.

---

## P0 — Broken calls (HTTP 4xx / 5xx in production)

These are driver-app calls whose path does not exist on the backend, or that hit an admin-only route. They **will fail every time they run**.

### 1. `GET /admin/service-areas` — hit in `become-driver` + `profile-setup`
- Caller: `driver-app/app/become-driver.tsx:67`, `driver-app/app/profile-setup.tsx:107`
- Backend route: `backend/routes/admin/service_areas.py:22` — mounted under admin router with `dependencies=[Depends(get_admin_user)]` (`backend/routes/admin/__init__.py:55`).
- **Effect:** Driver onboarding calls an admin-gated endpoint. Non-admin JWTs → **403 Forbidden**. The service-area list silently fails to load; the dropdown stays empty, which blocks `PUT /drivers/me` (needs `service_area_id`).
- **Fix:** Add a public endpoint, e.g. `GET /service-areas` in `backend/routes/fares.py` (or a new `backend/routes/service_areas.py`) that returns `{id, name, is_active}` for active areas only. Update both screens.

### 2. `GET /faqs` — hit in driver FAQ screen
- Caller: `driver-app/app/driver/faq.tsx:60`
- Backend route: `backend/routes/admin/faqs.py:22` → mounted as `/admin/faqs`, admin-gated.
- **Effect:** `GET /api/v1/faqs` → **404**. FAQ screen shows empty state.
- **Fix:** Add a public `GET /faqs` (read-only, `is_published=true`) in a non-admin router; or proxy from settings.

### 3. `GET /legal/content` — hit in Legal screen
- Caller: `driver-app/app/legal.tsx:96` (raw `fetch`, not the shared client, so no `/api/v1` prefix applied)
- Backend route: `GET /settings/legal` and `GET /api/v1/settings/legal` (`backend/routes/settings.py:55`).
- **Effect:** **404**. Screen falls back to hard-coded legal text (which is fine as fallback, but the DB-backed text — admin-dashboard editable — never reaches the driver).
- **Fix:** Change the driver-app URL to `${SpinrConfig.backendUrl}/settings/legal` and map the response keys (`terms_of_service_text`, `privacy_policy_text`).

### 4. `POST /safety/report` — hit in Report Safety screen
- Caller: `driver-app/app/report-safety.tsx:89`
- Backend route: **does not exist.** Grep for `safety/report` and `@.*safety` returns zero matches.
- **Effect:** **404**. Driver-reported safety incidents are silently dropped. Major trust + liability issue.
- **Fix:** Add `POST /safety-incidents` (or `/safety/report`) that inserts into `safety_incidents`. There is already a `safety_incidents` surface in admin support, so the table/concept exists — just not the driver-facing submit route.

### 5. Payout screen: three wrong paths
- Caller: `driver-app/app/driver/payout.tsx:176` → `GET /payouts/t4a`
- Caller: `driver-app/app/driver/payout.tsx:193` → `GET /payouts/csv`
- Caller: `driver-app/app/driver/tax-documents.tsx:55` → `GET /payouts/tax-documents`
- Actual backend: `GET /drivers/t4a/{year}` (`backend/routes/drivers.py:1430`), `GET /drivers/earnings/export?year=` (`backend/routes/drivers.py:1455`). There is **no** `/payouts/tax-documents` at all.
- **Effect:** All three buttons 404. No T4A download, no earnings CSV export, no tax-documents list.
- **Fix:**
  - `payout.tsx:176` → `api.get(`/drivers/t4a/${year}`)`
  - `payout.tsx:193` → `api.get(`/drivers/earnings/export?year=${year}`)`
  - `tax-documents.tsx:55` → add backend endpoint (e.g. `GET /drivers/tax-documents` that lists available T4A years) or have client enumerate years locally and call `/drivers/t4a/{year}` per row.

### 6. `POST /rides/{rideId}/tip` — called from `driverStore.submitTip`
- Caller: `driver-app/store/driverStore.ts:728`
- Backend route: `backend/routes/rides.py:1176` — **enforces `ride['rider_id'] == current_user['id']`**.
- **Effect:** A driver calling this endpoint always gets **403 Forbidden** ("Not authorized to tip this ride"). `submitTip` is dead code in the driver surface; tips are only placed by riders.
- **Fix:** Delete `submitTip` from `driverStore.ts` (lines 726–734) and its type declaration at line 313; or, if the intent was "driver can self-report cash tip received", add a new distinct endpoint and rename.

---

## P1 — Missing driver-facing endpoints (call site exists or is needed, route doesn't)

### 7. `GET /drivers/tax-documents` (listing)
Currently `tax-documents.tsx` displays whatever shape `/payouts/tax-documents` returned. There is no backend listing endpoint — only a single-year T4A generator. Add a listing route.

### 8. Public service areas endpoint (see #1)

### 9. Public FAQ endpoint (see #2)

### 10. Safety incident submission (see #4)

### 11. Subscription verify-session + cancel hooks vs Stripe
`backend/routes/drivers.py:2907` verify-session assumes Stripe Checkout flow. In dev/no-Stripe mode the first `/subscribe` call short-circuits to `{"success": True, "mode": "dev"}` and the client never needs verify-session, but **on a `402`-failed "go online" the subscribe screen immediately polls `verify-session` with no `session_id`** (`subscription.tsx:69`) — that call returns 422 "session_id required". Either gate the call on session existence or make `session_id` optional with a "no-op" response when absent.

### 12. Driver-side listing of safety/emergency history
The driver can trigger `POST /rides/{id}/emergency` but cannot view their own past reports. Admin-only table today. Low priority.

---

## P2 — Dual / redundant endpoints that cause confusion

### 13. Two push-token endpoints, only one used
- `POST /notifications/register-token` — writes `push_tokens` table + mirrors `users.fcm_token` (`backend/routes/notifications.py:48`). ← driver-app uses this (`_layout.tsx:175`).
- `POST /drivers/push-token` — writes only `users.fcm_token` (`backend/routes/drivers.py:489`). Documented as "dispatch push fallback" but **never called by driver-app**.
- **Risk:** the dispatch-path comment implies ops expect drivers to register here too. Today they don't — fallback push on stale WS works only because notifications.register-token happens to mirror the same column. Either (a) delete `POST /drivers/push-token` as dead code, or (b) make driver-app call both (with the driver variant being the authoritative Expo/platform track for driver-only targeting).

### 14. Two driver-status endpoints
- `POST /drivers/status` (self, body `{is_online, is_available}` — `backend/routes/drivers.py:523`)
- `PUT /drivers/{driver_id}/status` (by id, body `{is_online}` — `backend/routes/drivers.py:2402`)
- Driver-app calls the `PUT /{id}/status` variant via `authStore.updateDriverStatus` (`shared/store/authStore.ts:463`).
- `is_available` is therefore never toggled by the client — which is fine (dispatcher sets it) but leaves `POST /drivers/status` as dead code. Pick one and delete the other.

### 15. Two account-deletion endpoints — driver is using the wrong one (PIPEDA risk)
- `DELETE /users/account` — 30-day grace, PIPEDA-compliant (`backend/routes/users.py:88`)
- `DELETE /users/profile` — immediate hard delete (`backend/routes/users.py:111`)
- Driver Settings screen calls the **immediate** one: `driver-app/app/driver/settings.tsx:126`.
- **Impact:** For Canadian drivers this bypasses the PIPEDA grace period. Also there is no way to undo. The project claims PIPEDA compliance in the CLAUDE.md / `R-P1-6` comments.
- **Fix:** Change `settings.tsx:126` to `api.delete('/users/account')` and surface the 30-day reactivation window to the user in the confirmation modal copy.

---

## P3 — Driver-app features implemented but not fully wired

### 16. Driver destination zone (commute home)
Backend has `POST /drivers/destination`, `DELETE /drivers/destination`, `GET /drivers/destination` (`backend/routes/drivers.py:558–609`). No driver-app screen or store action calls any of them. Either build the UI or delete the endpoints.

### 17. Driver leaderboard
Backend has `GET /drivers/leaderboard` (`backend/routes/drivers.py:2311`). No call site in driver-app. Either build the UI or delete.

### 18. Driver document delete
`documentStore.ts:174` calls `DELETE /drivers/documents/{id}` but grep for `@documents_router.delete("/documents")` in `backend/documents.py` shows no such route. **Backend missing.** Driver cannot remove an uploaded document; they can only re-upload and overwrite the requirement link.

### 19. `is_available` toggle
Dispatcher flips `is_available` on accept/decline/complete, but there is no driver-visible break/pause toggle (Uber-style "pause requests"). Consider adding — backend already supports `POST /drivers/status` with `is_available`.

---

## P4 — Payload / schema mismatches (runtime-safe but brittle)

### 20. `POST /drivers/location-batch` body shape
Driver sends `{ points: [...] }` (`useDriverDashboard.ts:259`). Backend accepts both `points` and `locations` (`drivers.py:1189`: `batch.get("locations") or batch.get("points")`). Works today but asymmetric — standardise to `points` (or `locations`) everywhere and delete the fallback.

### 21. `POST /drivers/rides/{id}/cancel` uses query-string for reason
`driverStore.ts:518` → `.../cancel?reason=foo`. Backend reads `reason: str = Query("")` (`drivers.py:2104`). Works but unusual — reason should be a JSON body, and the backend currently does NOT persist reason anywhere (the update_ride at line 2121 only writes `status`, `cancelled_at`, `updated_at`; the comment at 2117–2119 explicitly drops `cancelled_by` and `cancellation_reason` to avoid PGRST204). So the reason string is logged to nowhere and emitted only in the WS event to the rider. **Effect:** cancellation analytics / fraud review are blind. Add the columns and persist.

### 22. `POST /rides/{id}/emergency` body
Driver sends `{latitude, longitude}` (`index.tsx:569`). Backend's `EmergencyRequest` model accepts `{message?, latitude?, longitude?}` (`rides.py:1965`). The `message` is never sent from driver. The backend stores `request.message` as `None`. Consider attaching a free-text message (e.g. from the 3-tap SOS sheet) — or drop `message` from the model.

### 23. Notifications register-token body
Driver sends `{token, platform}` (`_layout.tsx:175`). Backend model `RegisterTokenRequest(token, platform)` matches. ✓ (This was initially reported as mismatched in an earlier audit; confirmed correct here.)

---

## P5 — Security / correctness concerns

### 24. Driver-app `profile.tsx` updates user profile via `POST /users/profile`
`driver-app/app/driver/profile.tsx:158` posts `{first_name, last_name, email, phone, ...}`. Backend `POST /users/profile` accepts `{first_name, last_name, email, gender, role}` — **`phone` is silently ignored**. Phone must go through `PATCH /users/profile/phone` (`users.py:142`). Either split the form or fix the call site to do two requests.

### 25. Driver-app `POST /users/profile` can set `role`
Same endpoint accepts `role` in body (`users.py:31`). If the backend doesn't gate role changes, a driver could potentially escalate to admin via this endpoint. **Verify:** open `users.py:31+` and confirm `role` is either dropped or restricted to `{rider,driver}`. If not gated, lock it down.

### 26. Document upload: dual paths
- Raw `POST /upload` (multipart) via `shared/api/upload.ts` → returns `{url}`. Used by `documents.tsx:132`.
- `POST /drivers/documents/upload` (multipart) → tied to a specific requirement_id (`backend/documents.py:493`). Used by `documentStore.ts:144`.
- Driver-app uses BOTH. The generic `/upload` produces URLs that then get linked by `POST /drivers/documents`. The driver-specific `/documents/upload` handles both in one call. Pick one flow and delete the other to avoid the "uploaded but not linked" orphan case.

### 27. `driverStore.rateRider` posts `{rating, comment}` — backend stores as `rider_comment`
Field-name OK (`backend/routes/drivers.py:2161`). No issue, but note the `rider_rating` is **not bounded** in the model (backend takes whatever `rating` is). Add `ge=1, le=5` on `RideRatingRequest.rating` to match rider-side rating validation.

---

## WebSocket audit

Driver WS URL: `wss://{host}/ws/driver/{user_id}`.

### Driver → Server
| Type | Used | Backend handler |
|---|---|---|
| `auth` | ✓ `useDriverDashboard.ts:475` | `websocket.py:68` |
| `driver_location` | ✓ sent every 5s + on reconnect | `websocket.py:181` (accepts `driver_location` or `location_update`) |
| `pong` | ✓ in response to `ping` | `websocket.py:177` |

### Server → Driver
| Type | Driver listens? | Backend emits? |
|---|---|---|
| `auth_success` | Treated as "first non-error msg = authed" (no explicit handler) — works | ✓ `websocket.py:131` |
| `new_ride_assignment` | ✓ `useDriverDashboard.ts:382` | ✓ `rides.py:327` |
| `ride_cancelled` | ✓ `useDriverDashboard.ts:399` | ✓ multiple (`rides.py:617/1879`, `drivers.py:2136`, `admin/rides.py:181+`) |
| `chat_message` | ✓ `useDriverDashboard.ts:421` | ✓ `websocket.py:397` |
| `ping` | ✓ responds with `pong` (`useDriverDashboard.ts:412`) | ✓ heartbeat_task |
| `error` | ✓ `useDriverDashboard.ts:500` | ✓ various |
| `driver_status_changed` | ✗ not handled | emitted on own login — benign (targets admins) |
| `driver_location_update` | ✗ not handled | emitted on own location — benign (targets rider+admins) |
| `ride_status_changed` | ✗ not handled | emitted by dispatch_service — **potential gap** if server pushes ride status while driver is away from that screen |

### Missing WS-level features on driver side
- No explicit handler for backend-sent **`ride_status_changed`**. If the server ever pushes "ride transitioned to `trip_in_progress`" to drivers who didn't initiate the transition locally, the UI won't pick it up. Currently the driver initiates every state transition, so this is latent.
- No `get_nearby_drivers` use from the driver app (rider-only feature, fine).
- `ride_offered` is used as UI phase state but is NOT a WS event name — avoid naming clash.

---

## Screen-by-screen call summary (driver-app/)

| Screen | Status |
|---|---|
| `login.tsx` | OK — `/auth/send-otp` ✓ |
| `otp.tsx` | OK — `/auth/verify-otp`, `/auth/send-otp` ✓ |
| `become-driver.tsx` | ⚠ `/admin/service-areas` — see #1 |
| `profile-setup.tsx` | ⚠ `/admin/service-areas` — see #1 |
| `vehicle-info.tsx` | OK — `/vehicle-types`, `PUT /drivers/me` ✓ |
| `documents.tsx` | OK — `/drivers/requirements`, `/drivers/documents`; document `DELETE` is broken (see #18) |
| `legal.tsx` | ⚠ wrong path — see #3 |
| `report-safety.tsx` | ✗ endpoint missing — see #4 |
| `driver/index.tsx` | OK — `/drivers/demand-heatmap`, `/rides/{id}/emergency` ✓ |
| `driver/ride-detail.tsx` | OK — `GET /rides/{id}` ✓ |
| `driver/chat.tsx` | OK — messages + call ✓ |
| `driver/earnings.tsx` | OK (via driverStore) ✓ |
| `driver/payout.tsx` | ⚠ T4A + CSV wrong paths — see #5; GST save via `PUT /drivers/me` ✓ |
| `driver/payout-history.tsx` | OK — `GET /drivers/payouts` ✓ |
| `driver/tax-documents.tsx` | ✗ path wrong — see #5 |
| `driver/subscription.tsx` | OK except verify-session edge case — see #11 |
| `driver/quests.tsx` | OK ✓ |
| `driver/referral.tsx` | OK ✓ |
| `driver/notifications.tsx` | OK ✓ |
| `driver/settings.tsx` | ⚠ wrong delete endpoint — see #15 |
| `driver/profile.tsx` | ⚠ phone ignored — see #24 |
| `driver/addresses.tsx` | OK ✓ |
| `driver/emergency-contacts.tsx` | OK ✓ |
| `driver/help.tsx` | OK — `/support/chat` ✓ |
| `driver/faq.tsx` | ✗ path wrong — see #2 |
| `driver/rides.tsx` | OK — `/drivers/rides/history` ✓ |

---

## Required-environment / runtime concerns (not strictly endpoint mapping)

- `SpinrConfig.backendUrl` is read once at module load for raw `fetch` calls (`legal.tsx`, `become-driver.tsx:205,221`). Anything that flips backend URL at runtime (dev → prod switch, staging) won't affect those calls. Prefer the shared `api` client everywhere.
- The driver app registers FCM tokens via `/notifications/register-token`, but pre-auth it silently 401s (now gated on `isAuthInitialized` — good, `_layout.tsx:168`). Watch for race where the WS connects before FCM registration completes — push fallback for the very first offer can be lost.

---

## Recommended immediate actions (in order)

1. **Fix #5 (payouts paths)** — low blast radius, removes 3 always-broken buttons.
2. **Fix #3 (legal path)** — one-line.
3. **Fix #15 (delete account endpoint)** — compliance.
4. **Fix #6 (remove driver submitTip)** — kill dead code that hides bugs.
5. **Add public service-areas (#1) + public FAQ (#2)** — unblocks onboarding and Help.
6. **Add `POST /safety-incidents` (#4)** — trust/safety gap.
7. **Fix #18 (document delete backend)**.
8. **Audit role field in `POST /users/profile` (#25)** — potential privilege escalation.
9. **Reconcile duplicate endpoints (#13, #14)** — pick one, delete the other.
10. **Persist cancellation reason + add rating bounds (#21, #27)**.
