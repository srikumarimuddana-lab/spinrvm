# Spinr — Codebase Analysis & Observations

Date: 2026-02-11

This document captures observed mapping of rider/driver code, backend and frontend review notes, gaps, and prioritized actionable improvements with suggested code changes.

## 1) File mapping (rider vs driver)
- Backend core: backend/server.py — ride lifecycle, driver matching, WebSocket realtime messages, demo drivers, admin panel and settings.
- Frontend rider screens: `frontend/app/search-destination.tsx`, `frontend/app/ride-status.tsx`, `frontend/app/ride-in-progress.tsx`, `frontend/app/ride-completed.tsx`, `frontend/app/payment-confirm.tsx`, `frontend/app/otp.tsx`.
- Frontend driver-related screens: `frontend/app/driver-arriving.tsx`, `frontend/app/driver-arrived.tsx`, `frontend/app/chat-driver.tsx`.
- Stores: `store/rideStore.ts`, `store/authStore.ts`, `store/userStore.ts` — client-side state for rides, auth and user.

## 2) Backend observations (`backend/server.py`)
- Framework & infra: FastAPI + Motor (MongoDB). JWT-based auth, OTP login flow, WebSocket endpoint at `/ws/{client_type}/{client_id}`.
- Data models: `Driver`, `Ride`, `FareConfig`, `VehicleType`, `ServiceArea` implemented as Pydantic models and persisted to Mongo collections.
- Realtime behavior: drivers send `driver_location` messages; server updates `driver_locations` cache and notifies any rider with active ride. Riders can request `get_nearby_drivers` over WS.
- Matching: `match_driver_to_ride` supports `nearest`, `rating_based`, `round_robin`, `combined`. If no drivers exist, `create_demo_drivers` inserts demo drivers.
- Payments: Stripe integration present but guarded — if secret not present, APIs return mocked intents (safe for dev).

Backend risks and gaps:
- Authorization: critical operations like `/rides/{ride_id}/complete` and `/rides/{ride_id}/start` allow demo bypasses and rely on rider identity; driver-only actions are not enforced.
- WebSocket auth: `/ws/{client_type}/{client_id}` accepts connections without verifying JWT — a malicious client can impersonate driver/rider IDs.
- Input validation: several admin endpoints accept raw dicts (no Pydantic validation) which can introduce malformed data.
- Rate limits & abuse controls: OTP endpoints lack throttling; consider adding rate-limiting to prevent abuse.
- Concurrency: `match_driver_to_ride` does not use reservations/atomic updates to prevent double-assigning the same driver under high load.

## 3) Frontend observations (example: `frontend/app/driver-arrived.tsx`)
- Stack: React Native + Expo; UI components well-structured and themed via `SpinrConfig`.
- UX: `driver-arrived` screen includes OTP display, driver card, share/copy actions and a demo button to start ride; relies on `rideStore.fetchRide` to obtain `currentRide` and `currentDriver`.
- Realtime integration: frontend appears to rely on WebSocket flows (backend WS exists), but I did not find a centralized WS client in the sampled files — verify `rideStore` for WS handling.

Frontend risks and gaps:
- OTP handling: the screen displays OTP and allows demo 'Start Ride' — ensure OTP verification flow is enforced in production.
- Missing optimistic updates/ retry logic: if WS disconnects, client should gracefully reconnect and re-subscribe to necessary channels.
- Type safety & validation: some components assume presence of fields (e.g., `currentDriver.name`) without null checks; guard these in UI or types.

## 4) Prioritized recommendations (short-term → mid-term → long-term)

Short-term (high priority)
- Enforce WebSocket authentication: require JWT on WS connect and validate `client_id` matches token subject. (File: `backend/server.py`)
- Enforce role-based permissions: restrict driver-only endpoints and ensure riders cannot call driver actions. (File: `backend/server.py`)
- Add rate-limiting & basic abuse protection on OTP endpoints. (File: `backend/server.py`)
- Add atomic driver assignment to avoid race-conditions in `match_driver_to_ride` (use a DB transaction or conditional update). (File: `backend/server.py`)

Mid-term (medium priority)
- Move admin endpoints to Pydantic request models to validate inputs. (File: `backend/server.py`)
- Implement WS ping/pong and reconnection guidance on the client; centralize WS client in `store/rideStore.ts`. (File: `store/rideStore.ts` and `frontend/app/*`)
- Add server-side logging and metrics for matching latency and WebSocket errors (admin stats already exist; instrument further). (File: `backend/server.py`)

Long-term (low priority / strategic)
- Introduce worker queue for driver matching (e.g., Celery/RQ or background tasks in FastAPI) to scale matching and retry logic. (Backend architecture)
- Implement end-to-end tests for matching, cancellation fee flow, and payments.
- Add role-specific mobile apps or feature flags to cleanly separate driver vs rider UX flows.

## 5) Specific code-change suggestions (concrete)

1) WebSocket auth: require token on connect and decode before accepting.
   - Change: parse `Authorization` header or `token` query param in `websocket_endpoint` and call `verify_jwt_token`. Reject if invalid.
   - File: `backend/server.py` (function `websocket_endpoint`). Example change: validate token and set `user_id = payload['user_id']`; use `client_type_userid` as connection key.

2) Driver assignment atomic update: after selecting `selected_driver`, use `update_one` with a predicate that `is_available: True` to claim driver; only proceed if the update modified a document.
   - File: `backend/server.py` (function `match_driver_to_ride`). This avoids double assignment under concurrency.

3) Role enforcement: add simple check in endpoints that currently can be misused.
   - Example: in `/rides/{ride_id}/complete` require `current_user['role'] == 'driver'` or check driver_id matches current_user.
   - File: `backend/server.py` (routes `start`, `complete`, `simulate-arrival`).

4) Centralize WS client on frontend: create or update `store/rideStore.ts` to manage a single WebSocket connection, subscribe to `rider_{userId}` channel, and expose live driver location updates.
   - Files: `store/rideStore.ts`, `frontend/app/ride-in-progress.tsx`, `frontend/app/driver-arrived.tsx` to read from store.

5) Admin input validation: replace raw `Dict[str, Any]` request params with Pydantic models for `admin_update_settings`, `admin_create_service_area`, `admin_create_vehicle_type`, `admin_create_fare_config`.
   - File: `backend/server.py` (admin routes)

## 6) Quick security checklist
- Secure JWT secret and rotate in production; require HTTPS for mobile app endpoints.
- Add CSRF protections for admin web panel if exposed publicly and restrict admin panel access.
- Add rate limiting for auth and OTP routes.

## 7) Next steps I can take (pick one)
- Implement WebSocket authentication (small, high impact) and open a PR.
- Add atomic driver assignment changes to `match_driver_to_ride` and test with concurrent requests.
- Centralize WebSocket client in `store/rideStore.ts` and patch relevant screens to consume it.

---

## 8) Implementation status (what I changed now)
- Added `firebase-admin` dependency to `backend/requirements.txt`.
- Implemented Firebase ID token verification and a robust `get_current_user` that prefers Firebase tokens and falls back to legacy JWTs.
- Updated WebSocket connection flow (`/ws/{client_type}/{client_id}`) to require an initial `auth` message with a token and to register connections server-side as `{client_type}_{user_id}` (prevents impersonation).
- Enforced role checks on `start` and `complete` ride endpoints so only authorized drivers (or owning riders where allowed) can trigger those actions.
- Implemented atomic driver claiming in `match_driver_to_ride` to reduce race conditions and prevent double assignments.
- Added basic rate-limiting using `slowapi` for `/auth/send-otp` (5/min) and `/auth/verify-otp` (10/min) to mitigate abuse.
- CI now generates `junit.xml` (pytest `--junitxml`) and uploads it as a workflow artifact for test reporting.

> ⚠️ Note: these changes require Firebase credentials and some behavior changes on the mobile app (send an `auth` message with a token upon opening the WebSocket). I recommend deploying to a staging environment and testing mobile clients against it.

## 9) Required environment variables & setup
- `FIREBASE_SERVICE_ACCOUNT_JSON` — the JSON service account (must be valid JSON). Set it in your environment or container secrets (do NOT commit to git). If the JSON contains newlines or special chars, use a single-line value or proper secret manager.
- `SUPABASE_URL` — your Supabase project URL (if you plan to migrate DB to Supabase).
- `SUPABASE_SERVICE_ROLE_KEY` — service role key for migrations and server-side operations (store securely).
- `SUPABASE_ANON_KEY` — public key for client usage (optional).

Commands to install the new dependency and run the backend locally (example):

```bash
# from repo root
cd backend
python -m pip install -r requirements.txt
# set env variables e.g. in a .env file or secrets manager
# then run
uvicorn server:app --reload --port 8000
```

---
## CI usage: test annotation & failure threshold 📋

You can control which tests are annotated in PRs and optionally fail the CI job when test failures exceed a threshold using two env variables in the workflow or when triggering the job:

- **ANNOTATE_TESTS_PATTERN** (string)
  - If non-empty, the workflow runs a filtered test pass (`pytest -k "<pattern>"`) and annotates only those results (produced in `annotate_junit.xml`).
  - Example: `ANNOTATE_TESTS_PATTERN="rate_limit or smoke"`
  - Use this to focus PR annotations on a subset of tests (slow/flaky groups) without annotating the entire test suite.

- **FAIL_THRESHOLD** (integer, default `-1` = disabled)
  - If set to a non-negative integer, CI will fail when total failures+errors in the main `junit.xml` exceed this value.
  - Example: `FAIL_THRESHOLD=2` — job fails when >2 failures are detected.

How to set these for a specific run:
- Add them to your workflow dispatch inputs or temporarily set them in the workflow job environment for a branch.
- For quick local testing (not recommended for PR annotations), export them in the environment when running the workflow runner.

These variables give fine-grained control over PR noise and enable stricter gating in CI when needed.

---
Report generated automatically from quick repo inspection. If you'd like, I can continue with the next prioritized tasks (Supabase migration, FCM integration, rate-limiting) and open a PR with tests and documentation for each change. Tell me which to start next.
