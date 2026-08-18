# Spinr — Requirements

*Generated: 2026-05-02 | Mode: YOLO | Granularity: Standard*

---

## Vision

Spinr is the driver-first alternative to existing Canadian ride-sharing platforms. Drivers keep 100% of every fare. Riders get transparent pricing, reliable service, and genuine safety features. Saskatchewan goes first; the model scales nationally.

## Goals

| # | Goal | Horizon |
|---|---|---|
| G1 | Close all P0 security/safety findings; achieve green CI | Device testing gate |
| G2 | WAV dispatch operational — satisfy Saskatchewan legal requirement | Device testing gate |
| G3 | Golden-path device testing verified on all 5 surfaces | Pre-production gate |
| G4 | Security pipeline flipped from advisory to blocking | Production gate |
| G5 | First production deployment: zero surprises, all monitors green | Launch |
| G6 | Corporate billing: companies can onboard, fund allowances, report | Post-launch |

---

## Non-Functional Requirements

### Performance SLAs (P95)

| Path | Target |
|---|---|
| Dispatch offer → driver phone notification | < 2 s |
| Fare estimate (rider tap → price shown) | < 300 ms |
| Fare settlement (trip end → receipt) | < 1 s |
| WebSocket event fan-out | < 100 ms |
| Driver location update (write) | < 150 ms |
| Auth token refresh | < 200 ms |
| Stripe webhook processing | < 500 ms |
| Migration apply (prod window) | < 30 s |

### Security

- All auth tokens in HttpOnly cookies; never in `localStorage` or `sessionStorage`
- Admin JWT: 12 hr access, 30-day refresh; rider/driver: 15 min access, 30-day refresh
- OTP SHA-256 hashed at rest; 5-failure/hr Redis lockout; dev bypass `"1234"` gated by `ENV != production`
- No PII in logs, Sentry, or analytics (no GPS coords, full phone, full name, email, card data)
- Stripe idempotency enforced via `claim_stripe_event` before every webhook handler
- Settings (Stripe/Twilio keys) live in `app_settings` DB table — rotatable without redeploy
- Semgrep custom ruleset + OWASP Top Ten scanning on every PR
- Gitleaks secret scanning with `.gitleaks.toml` allowlist for dev-fixture false positives

### Reliability

- Zero silent error swallowing: all DB/auth/payment/dispatch errors surface via `logger.error` + HTTP 5xx
- All 7 background loops are replay-safe (claim flags, idempotency keys, or atomic DB claims)
- WebSocket fan-out uses Redis pub/sub for cross-replica delivery
- Supabase run_sync() wraps all DB calls with one H2 GOAWAY retry

### Compliance

- PIPEDA: data residency ca-central-1, right-to-delete within 30 days, consent version stored at signup
- Saskatchewan Transportation Act: trip logs 7 yr, driver-vehicle linkage 7 yr, GPS at pickup/dropoff 3 yr, insurance period transitions 7 yr
- SGI insurance: 4 TNC periods logged and append-only for every driver session
- WCAG 2.1 AA: all customer-facing surfaces
- GST (5%) and PST (6%) shown as separate receipt line items; T4A-compatible driver earnings

### Observability

- Sentry domain tags: `dispatch`, `payments`, `auth`, `corporate`, `safety`, `drivers`, `rides`, `admin`
- Metrics: `spinr.<domain>.<metric>.<unit>` naming convention
- REFRESH TOKEN REUSE alert → PagerDuty (pending creation)
- State transitions: info log + metric; user-visible errors: Sentry + error log

---

## Functional Requirements

### Backend (FastAPI)

#### Auth (`/routes/auth.py`)

- REQ-AUTH-1: Issue access + refresh tokens as HttpOnly cookies on login
- REQ-AUTH-2: `silentRefresh` returns new access token via cookie; 401 = no RT (no logout); 403/5xx = logout
- REQ-AUTH-3: Admin JWT contains role + email + modules in claims; trusted fully
- REQ-AUTH-4: Rider/driver role always re-read from `users` table on every request
- REQ-AUTH-5: OTP 6-digit, SHA-256 hashed at rest, 5-failure lockout, 24 hr Redis ban
- REQ-AUTH-6: Dev OTP bypass `"1234"` blocked when `ENV = production`
- REQ-AUTH-7: Refresh token rotation on every use; SHA-256 stored hash

#### Rides (`/routes/rides.py`)

- REQ-RIDE-1: State machine guards via `_require_ride_in_state()`; every transition emits WS event
- REQ-RIDE-2: Valid states: `scheduled → searching → driver_assigned → driver_accepted → driver_arrived → in_progress → completed` (or `cancelled` pre-trip)
- REQ-RIDE-3: Race guard: Supabase UPDATE filters `{status: 'searching'}`; 0 rows → 409 + `ride_taken` WS event
- REQ-RIDE-4: At most one active ride per rider at any time
- REQ-RIDE-5: `in_progress` transitions only to `completed`; never `cancelled` after trip start
- REQ-RIDE-6: Every state change emits WS event to both rider and assigned driver connection

#### Fares (`/routes/fares.py`, `/services/fare_service.py`)

- REQ-FARE-1: Decimal-only arithmetic; `_d()`, `_round()`, `_f()` before every DB write or API response
- REQ-FARE-2: Surge multiplier shown to rider before booking; never applied retroactively
- REQ-FARE-3: Surge hard cap 2.5×; auto-mode tiers as defined in CLAUDE.md
- REQ-FARE-4: Receipt shows: base fare, distance, time, booking fee, surge, GST (5%), PST (6% where applicable), tip
- REQ-FARE-5: Corporate rides: allowance → rider wallet → card fallback; `corporate_wallet_apply_delta` Postgres function for row-level locking
- REQ-FARE-6: Surge never applied to corporate account-paid rides

#### Dispatch (`/services/dispatch_service.py`)

- REQ-DISP-1: Dispatch reads `is_available` (system-computed); never `is_online` alone
- REQ-DISP-2: `is_available ⇒ is_online` invariant must hold
- REQ-DISP-3: Offer timeout ~15 s; releases driver back to available on timeout
- REQ-DISP-4: WAV requests matched to WAV-equipped drivers only when WAV driver is online in area
- REQ-DISP-5: Dispatch loop is replay-safe (atomic DB claim prevents duplicate offers across replicas)
- REQ-DISP-6: KPI target: match rate ≥ 85%; dispatch latency P95 < 2 s

#### Drivers (`/routes/drivers.py`)

- REQ-DRV-1: Document expiry (licence, insurance, vehicle registration) blocks Period 1+; checked on every `go_online`
- REQ-DRV-2: Insurance period transitions logged append-only to `driver_insurance_periods`
- REQ-DRV-3: Period 2 starts on `driver_assigned` (driver obligated to ride before acceptance)
- REQ-DRV-4: Period 3 requires a `ride_id` linked to an `in_progress` ride

#### Payments (`/routes/payments.py`, `/utils/stripe_*.py`)

- REQ-PAY-1: `claim_stripe_event(event_id)` called before every webhook; silently skip if already claimed
- REQ-PAY-2: Payment success rate KPI target ≥ 99%
- REQ-PAY-3: Stripe keys live in `app_settings` DB table; rotatable without redeployment
- REQ-PAY-4: All Stripe calls idempotency-keyed

#### Safety (`/routes/rides.py` SOS, `/utils/`)

- REQ-SOS-1: SOS notifies emergency contacts + safety team + offers one-tap 911; never auto-dials
- REQ-SOS-2: SOS failure must surface loudly — no silent swallow (current P0 bug to fix)
- REQ-SOS-3: GPS trace logged at pickup and dropoff (not full route) for 3-year regulatory retention

#### WebSocket (`/routes/websocket.py`)

- REQ-WS-1: First message must be `{"type": "auth", "token": "<jwt>"}` within 30 s
- REQ-WS-2: Connection keys: `driver_{user_id}` / `rider_{user_id}`
- REQ-WS-3: 30-second ping heartbeat; 30 msg/s rate limit; 64 KB max message
- REQ-WS-4: Cross-replica fan-out via `spinr:ws:dispatch` Redis pub/sub channel

#### Migrations

- REQ-MIG-1: Append-only; never edit a merged migration
- REQ-MIG-2: Every new user-data table ships with RLS policies in the same migration
- REQ-MIG-3: Money-touching Postgres functions: `SECURITY DEFINER` with explicit `search_path`
- REQ-MIG-4: Next migration slot: `62_*.sql` (verify before creating)

---

### Rider App (React Native / Expo SDK 54)

- REQ-RID-1: Auth tokens never stored in JS-accessible storage (HttpOnly cookies via BFF)
- REQ-RID-2: Axios interceptor auto-retries 401 after `silentRefresh`
- REQ-RID-3: Fare estimate displayed before booking confirmation; surge clearly marked
- REQ-RID-4: Wallet: top-up, pay with wallet, transaction history (20 default, configurable limit)
- REQ-RID-5: Fare split: create split, cancel split, per-participant status
- REQ-RID-6: Tip: `addTip` idempotent — duplicate tip rejected with 400 (R-P1-25)
- REQ-RID-7: `payWithWallet` balance check: insufficient balance returns 400; balance unchanged (R-P3-7)
- REQ-RID-8: SOS trigger available from in-ride screen; failure surfaces to rider
- REQ-RID-9: WAV request option visible when WAV driver is available
- REQ-RID-10: Real-time driver location updates during `driver_accepted` + `driver_arrived` states
- REQ-RID-11: Rating prompt after ride completion; first-rating crash eliminated (P0-4)

### Driver App (React Native / Expo SDK 54)

- REQ-DRV-APP-1: Online toggle sets `is_online`; system computes `is_available`
- REQ-DRV-APP-2: Offer screen: 15 s accept/reject window with countdown
- REQ-DRV-APP-3: Navigation integration: pickup → dropoff via Google Maps
- REQ-DRV-APP-4: Earnings summary and trip history accessible
- REQ-DRV-APP-5: GPS OOM eliminated (P0-6); location updates rate-limited
- REQ-DRV-APP-6: OTP screen for identity verification (driver-app/app/otp.tsx)
- REQ-DRV-APP-7: Document expiry warnings surface before going online

### Admin Dashboard (Next.js 16)

- REQ-ADM-1: HttpOnly cookie auth with CSRF token; CSRF header sent on all state-changing requests
- REQ-ADM-2: `silentRefresh`: 401 = no session (redirect to login, no logout loop); 403 = call `logout()`
- REQ-ADM-3: `initAuth` runs once per page load (init guard prevents duplicate calls)
- REQ-ADM-4: Fleet ops: driver list, ride list, live map (typeahead search, session-based Maps billing)
- REQ-ADM-5: Analytics: match rate, cancellation rate, utilisation, revenue
- REQ-ADM-6: Corporate management: company onboarding, allowances, membership, member rides
- REQ-ADM-7: Surge override: manual 1.0–10.0× (values > 2.5× require documented justification)
- REQ-ADM-8: Driver document review and approval
- REQ-ADM-9: Settings page: Stripe/Twilio/Google Maps keys managed in DB (no redeploy needed)
- REQ-ADM-10: WAV fleet status visible; WAV driver availability by area

### Shared (`@spinr/shared`)

- REQ-SHR-1: API client (`@shared/api/client`) wraps axios with base URL + auth interceptor
- REQ-SHR-2: Shared types and stores used across rider + driver apps

---

## Testing Requirements

### Coverage Minimums

| Surface | Target |
|---|---|
| `routes/payments.py`, `services/fare_service.py`, `utils/crypto.py` | ≥ 90% |
| `routes/rides.py`, `services/dispatch_service.py` | ≥ 80% |
| Admin routes, other utilities | ≥ 70% |
| Frontend stores (Zustand/Vitest) | ≥ 80% |

### Required Test Cases

- Every ride state transition (one case each in `test_ride_state_machine.py`)
- Every fare calculation branch: tiers, surge, corporate, promo
- Every auth/RLS policy: allowed and denied paths
- Every Stripe webhook type (before production)
- P0 regression tests: SOS, GPS OOM, fare mismatch, first-rating crash

### Device Testing Acceptance Criteria

- [ ] Rider app: booking flow end-to-end on physical iOS + Android
- [ ] Driver app: accept offer, navigate to pickup, complete ride
- [ ] Admin dashboard: create ride, view on live map, view in driver list
- [ ] Wallet: top-up, pay with wallet, view transactions
- [ ] Auth: login, session persist across app restart, silent refresh, logout
- [ ] SOS: trigger, verify emergency contact notification (test contacts)
- [ ] WAV: request WAV ride, matched to WAV driver

---

## CI / CD Requirements

- REQ-CI-1: `backend-test` green on every PR to main
- REQ-CI-2: `admin-test` (Vitest) green on every PR
- REQ-CI-3: `rider-app-test` (Jest) green on every PR
- REQ-CI-4: `driver-app-test` (Jest) green on every PR
- REQ-CI-5: G4c `npm-audit-admin` passes (only hard-blocking security gate)
- REQ-CI-6: G5b Gitleaks valid config (`.gitleaks.toml` allowlist in place)
- REQ-CI-7: `claude-review` workflow valid inputs (no deprecated `max_turns`)
- REQ-CI-8: Playwright E2E: auth properly mocked; all smoke tests pass
- REQ-CI-9 (pre-production): Flip all security gates from `continue-on-error: true` to blocking

---

## Deployment Requirements

### Dev / Staging

- REQ-DEPL-1: Dev tier keys in `.env.local` (gitignored); never committed
- REQ-DEPL-2: GitHub Actions secrets point to dev-tier Stripe/Twilio/Supabase sandboxes
- REQ-DEPL-3: Expo dev build deployable to physical devices via EAS dev build

### Production

- REQ-DEPL-4: Backend: Railway (auto-deploy from `main`; Render fallback)
- REQ-DEPL-5: Admin + shared: Vercel (auto-deploy from `main`)
- REQ-DEPL-6: Mobile builds: Expo EAS — triggered only when commit message contains `[build]`
- REQ-DEPL-7: Database migrations applied via `python -m backend.scripts.run_migrations` before new backend deploy
- REQ-DEPL-8: Supabase region: ca-central-1 (PIPEDA compliance; never change without legal sign-off)
- REQ-DEPL-9: Monitoring active before first real user: Sentry domain alerts, REFRESH TOKEN REUSE → PagerDuty
- REQ-DEPL-10: Runbook for data breach (docs/runbooks/data-breach.md) created before launch
