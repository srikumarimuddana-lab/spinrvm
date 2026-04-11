# Sprint 5 Completion Report

**Date:** 2026-04-09  
**Sprint:** 5 — Production Hardening (Part II)  
**Status:** ✅ Complete — 4 branches merged / ready for PR

---

## Branches Delivered

| # | Branch | PR | Description |
|---|--------|----|-------------|
| 1 | `sprint5/scheduled-rides` | #15 | Advance ride booking + dispatcher |
| 2 | `sprint5/ride-receipts` | #16 | Post-trip email receipt endpoint + UI |
| 3 | `sprint5/admin-fleet-map` | #17 | Real-time fleet map in admin dashboard |
| 4 | `sprint5/docker-security` | #18 | Docker hardening + Trivy CI image scan |

---

## Branch 1 — Scheduled Rides (`sprint5/scheduled-rides`)

### Problem
Riders could not book rides in advance. The background scheduler import in `lifespan.py` pointed to a nonexistent `features` module — the task was silently disabled.

### Solution

**Backend**
- `backend/features/__init__.py` — new package exposing `check_scheduled_rides`
- `backend/features/scheduled_rides.py` — asyncio background loop:
  - Polls every 60 s for rides where `is_scheduled=True`, `status=searching`, and `scheduled_time` is within the next 15 minutes
  - Atomic CAS (`searching → dispatching`) prevents double-dispatch across replicas
  - Haversine proximity filter (8 km radius) selects nearby drivers
  - FCM push notification sent to each candidate driver
- `backend/core/lifespan.py` — enabled `asyncio.create_task(check_scheduled_rides())` with graceful `CancelledError` cleanup on shutdown
- `backend/routes/rides.py` — validation: scheduled rides require `scheduled_time`, must be 30 min – 7 days in the future

**Rider App**
- `rider-app/app/schedule-ride.tsx` — date/time picker screen (iOS: inline; Android: tap pickers); `POST /rides` with `is_scheduled=true`
- `rider-app/app/_layout.tsx` — route registered in ride flow

---

## Branch 2 — Ride Receipts (`sprint5/ride-receipts`)

### Problem
There was no way for riders to receive an email copy of their receipt after a trip. The `GET /rides/{id}/receipt` endpoint existed but email delivery was absent.

### Solution

**Backend**
- `backend/routes/rides.py` — `POST /rides/{id}/receipt/email`:
  - Validates ride ownership and `completed` status
  - Rate-limited to 3 resend attempts (counter stored in ride record)
  - Requires a verified email address on the user profile
  - Calls `send_receipt_email()` helper; returns `{success, message, sends_remaining}`

**Rider App**
- `rider-app/app/ride-completed.tsx` — split invoice button into Share + Email Receipt pair
- `rider-app/app/ride-details.tsx` — "Email receipt to my account" button for completed rides; loading state + error alert

---

## Branch 3 — Admin Fleet Map (`sprint5/admin-fleet-map`)

### Problem
The admin dashboard had no live view of where drivers physically were on the network.

### Solution

**Admin Dashboard**
- `admin-dashboard/src/app/dashboard/fleet/page.tsx` — Next.js page:
  - Dynamically imports the existing `DriverMap` Leaflet component (SSR disabled)
  - Polls `GET /api/v1/admin/drivers?is_online=true` every **10 seconds** via `setInterval`
  - Stat cards: Online Drivers / On Trip / Idle + utilisation %
  - Manual Refresh button; animated "live" pulsing indicator
  - Error state with retry; loading skeleton while first fetch completes
- `admin-dashboard/src/components/sidebar.tsx` — "Fleet Map" entry added to Operations group (`icon: MapPin`, `module: "rides"` for RBAC)

---

## Branch 4 — Docker Security (`sprint5/docker-security`)

### Problem
The backend `Dockerfile` ran as root, had no health check, no `.dockerignore`, and the base image tag was unpinned (`:slim` floats). The existing Trivy scan only checked the filesystem — the built image itself was never scanned.

### Solution

**`backend/Dockerfile`** — multi-stage hardened build:
- Pinned `python:3.12.9-slim` on both stages
- Builder stage compiles wheels; runtime stage copies `/install` only (no `gcc` in prod)
- Non-root user `spinr` (uid/gid 1001) created and set as `USER` before `EXPOSE`
- All source files `COPY --chown=spinr:spinr` to prevent root-owned files
- `HEALTHCHECK` polls `http://localhost:$PORT/health` every 30 s; marks unhealthy after 3 failures

**`backend/.dockerignore`**:
- Excludes: `.git`, `.venv`, `__pycache__`, `*.pyc`, pytest/mypy caches, all `.env*` files, Firebase credential files (`google-services.json`, `GoogleService-Info.plist`), `docs/`, `tests/`, `README*`, `Dockerfile*`, `docker-compose*`
- Keeps image surface minimal; prevents secrets baking into layers

**`.github/workflows/ci.yml`** — new `docker-image-scan` job:
- Builds the image (`docker build backend/`) after `backend-test` passes
- Runs `aquasecurity/trivy-action@0.28.0` with `exit-code: 1` on `CRITICAL,HIGH`
- `ignore-unfixed: true` — suppresses noise from vulnerabilities without an available fix
- Second Trivy pass emits SARIF uploaded to GitHub Security tab
- Pinned the existing `security-scan` job to `trivy-action@0.28.0` (was `@master`)

---

## Issues Resolved This Sprint

| Issue | Resolution |
|-------|-----------|
| Scheduled rides dispatcher disabled (ImportError) | `features/` package created; task enabled in lifespan |
| No email receipt flow | `POST /{id}/receipt/email` + rate limiting + UI buttons |
| Admin has no live driver visibility | Fleet map page with 10 s polling |
| Docker image runs as root, unpinned, no healthcheck | Multi-stage build, non-root user, HEALTHCHECK, pinned digest |
| Trivy only scanned filesystem — image never checked | `docker-image-scan` CI job blocks on CRITICAL/HIGH |

---

## Next Sprint Candidates (Sprint 6)

- Stripe payment integration (replace `payment_status=paid` placeholder)
- Driver earnings statement PDF
- Supabase Row-Level Security policy audit
- E2E test suite (Detox) — currently a CI placeholder
- Playwright smoke tests for admin dashboard
