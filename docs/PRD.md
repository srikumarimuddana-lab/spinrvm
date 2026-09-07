# Spinr — Product Requirements Document

> **Canonical as of 2026-09-07.** Consolidated from `.planning/REQUIREMENTS.md` (GSD-generated
> requirements doc, 2026-05-02) and `docs/framework/02-product-requirements.md` (Pillar 2 of the
> engineering framework). Both source files remain in the repo for history but are superseded by
> this document — see the banners at the top of each. No requirement here is new; this is a
> reorganization/merge of what those two files already stated.

## Vision

Spinr is the driver-first alternative to existing Canadian ride-sharing platforms. Drivers keep
100% of every fare. Riders get transparent pricing, reliable service, and genuine safety features.
Saskatchewan goes first; the model scales nationally.

## Competitive thesis

Spinr does not out-scale incumbents; it out-positions them on five axes they structurally cannot follow:

1. **0% driver commission.** Monetization is SaaS corporate accounts, premium rider features, and
   partner referrals — never per-trip cuts. Incumbents' 25–40% take rate is core to their P&L, so
   they cannot match this.
2. **Bounded, honest pricing.** Hard 2.5× auto-surge cap, always visible before booking, never
   retroactive, never on corporate rides; every receipt line maps to a disclosed item. The cap is
   both a marketing claim and a code invariant (`SURGE_CAP` clamped at every fare-calc call site).
3. **Regulatory-native, not regulation-fighting.** PIPEDA, the Saskatchewan Transportation Act, SGI
   insurance periods, WCAG 2.1 AA, WAV support, and T4A tax artifacts are design inputs, not
   retrofits.
4. **Community-scale trust.** Saskatchewan-first community presence, a real safety surface (SOS,
   check-in loop, emergency contacts), and a no-data-harvesting stance (no ad SDKs, no behavioral
   retargeting) that incumbents cannot credibly claim.
5. **Corporate B2B as the profit engine.** The corporate layer (accounts, memberships, allowances,
   master-wallet fallback, KYB) sits on top of the consumer product without touching ride/driver
   logic — this is the revenue that funds the 0% consumer promise. See `docs/CORPORATE_B2B.md` and
   `docs/CORPORATE_B2B_GTM.md`.

Every one of these claims is enforced by a code invariant, a test, or an audit row, so the
marketing can never drift from the product.

## Goals

| # | Goal | Horizon |
|---|---|---|
| G1 | Close all P0 security/safety findings; achieve green CI | Device testing gate |
| G2 | WAV dispatch operational — satisfy Saskatchewan legal requirement | Device testing gate |
| G3 | Golden-path device testing verified on all 5 surfaces | Pre-production gate |
| G4 | Security pipeline flipped from advisory to blocking | Production gate |
| G5 | First production deployment: zero surprises, all monitors green | Launch |
| G6 | Corporate billing: companies can onboard, fund allowances, report | Post-launch |

## Personas and non-negotiables

| Persona | Non-negotiables every feature must preserve |
|---|---|
| Rider | Fare known before booking; surge visible pre-booking; one active ride; SOS reachable in-ride; PIPEDA rights (export/correct/delete) |
| Driver | 100% of fare; insurance period correctness (a liability, not a UX detail); contractor autonomy (no forced shifts/uniforms); document-expiry blocks going online, never silent lapse |
| Corporate admin | Spend visibility scoped to their company only; allowance caps enforced atomically; exports match receipts/tax rules |
| Internal admin | Module-gated RBAC (`require_module`); every action audit-logged; JWTs trusted only for admin tokens |

## Functional requirements

### Backend (FastAPI)

**Auth** (`routes/auth.py`)
- Issue access + refresh tokens as HttpOnly cookies on login
- `silentRefresh` returns new access token via cookie; 401 = no RT (no logout); 403/5xx = logout
- Admin JWT contains role + email + modules in claims; trusted fully
- Rider/driver role always re-read from `users` table on every request
- OTP: 6-digit, SHA-256 hashed at rest, 5-failure lockout, 24 hr Redis ban; dev bypass `"1234"` blocked when `ENV = production`
- Refresh token rotation on every use; SHA-256 stored hash

**Rides** (`routes/rides.py`)
- State machine guards via `_require_ride_in_state()`; every transition emits a WS event
- Valid states: `scheduled → searching → driver_assigned → driver_accepted → driver_arrived → in_progress → completed` (or `cancelled` pre-trip)
- Race guard: Supabase UPDATE filters `{status: 'searching'}`; 0 rows → 409 + `ride_taken` WS event
- At most one active ride per rider at any time
- `in_progress` transitions only to `completed`; never `cancelled` after trip start
- Every state change emits a WS event to both rider and assigned driver connection

**Fares** (`routes/fares.py`, `services/fare_service.py`)
- Decimal-only arithmetic; `_d()`, `_round()`, `_f()` before every DB write or API response
- Surge multiplier shown to rider before booking; never applied retroactively
- Surge hard cap 2.5×; auto-mode tiers as defined in `CLAUDE.md`
- Receipt shows: base fare, distance, time, booking fee, surge, GST (5%), PST (6% where applicable), tip
- Corporate rides: allowance → rider wallet → card fallback, via the `corporate_wallet_apply_delta` Postgres function for row-level locking
- Surge never applied to corporate account-paid rides

**Dispatch** (`services/dispatch_service.py`)
- Reads `is_available` (system-computed), never `is_online` alone; invariant `is_available ⇒ is_online` must hold
- Offer timeout ~15 s; releases driver back to available on timeout
- WAV requests matched to WAV-equipped drivers only when a WAV driver is online in the area
- Dispatch loop is replay-safe (atomic DB claim prevents duplicate offers across replicas)
- KPI target: match rate ≥ 85%; dispatch latency P95 < 2 s

**Drivers** (`routes/drivers.py`)
- Document expiry (licence, insurance, vehicle registration) blocks Period 1+; checked on every `go_online`
- Insurance period transitions logged append-only to `driver_insurance_periods`
- Period 2 starts on `driver_assigned` (driver obligated to the ride before acceptance)
- Period 3 requires a `ride_id` linked to an `in_progress` ride

**Payments** (`routes/payments.py`, `utils/stripe_*.py`)
- `claim_stripe_event(event_id)` called before every webhook; silently skip if already claimed
- Payment success rate KPI target ≥ 99%
- Stripe keys live in `app_settings` DB table; rotatable without redeployment
- All Stripe calls idempotency-keyed

**Safety** (SOS, `utils/`)
- SOS notifies emergency contacts + safety team + offers one-tap 911; never auto-dials
- SOS failure must surface loudly — no silent swallow
- GPS trace logged at pickup and dropoff only (not full route), 3-year regulatory retention

**WebSocket** (`routes/websocket.py`)
- First message must be `{"type": "auth", "token": "<jwt>"}`
- Connection keys: `driver_{user_id}` / `rider_{user_id}`
- Rate limit 30 msg/s; 64 KB max message
- Cross-replica fan-out via `spinr:ws:dispatch` Redis pub/sub channel

**Migrations**
- Append-only; never edit a merged migration
- Every new user-data table ships with RLS policies in the same migration
- Money-touching Postgres functions: `SECURITY DEFINER` with explicit `search_path`

### Rider App (React Native / Expo)
- Auth tokens never stored in JS-accessible storage (HttpOnly cookies via BFF); Axios interceptor auto-retries 401 after `silentRefresh`
- Fare estimate displayed before booking confirmation; surge clearly marked
- Wallet: top-up, pay with wallet, transaction history
- Fare split: create split, cancel split, per-participant status
- Tip (`addTip`) is idempotent — duplicate tip rejected with 400
- `payWithWallet` insufficient-balance check returns 400 with balance unchanged
- SOS trigger available from in-ride screen; failure surfaces to rider
- WAV request option visible when a WAV driver is available
- Real-time driver location updates during `driver_accepted` + `driver_arrived`
- Rating prompt after ride completion

### Driver App (React Native / Expo)
- Online toggle sets `is_online`; system computes `is_available`
- Offer screen: 15 s accept/reject window with countdown
- Navigation integration: pickup → dropoff via Google Maps
- Earnings summary and trip history accessible
- Location updates rate-limited
- OTP screen for identity verification
- Document expiry warnings surface before going online

### Admin Dashboard (Next.js)
- HttpOnly cookie auth with CSRF token on all state-changing requests
- `silentRefresh`: 401 = no session (redirect to login, no logout loop); 403 = `logout()`
- `initAuth` runs once per page load (init guard prevents duplicate calls)
- Fleet ops: driver list, ride list, live map (typeahead search, session-based Maps billing)
- Analytics: match rate, cancellation rate, utilisation, revenue
- Corporate management: company onboarding, allowances, membership, member rides
- Surge override: manual 1.0–10.0× (values > 2.5× require documented justification; still clamped to 2.5× at fare-calc time)
- Driver document review and approval
- Settings page: Stripe/Twilio/Google Maps keys managed in DB (no redeploy needed)
- WAV fleet status visible; WAV driver availability by area

### Shared (`@spinr/shared`)
- API client wraps axios with base URL + auth interceptor
- Shared types and stores used across rider + driver apps

## Non-functional / SLA requirements

### Performance (P95 targets)

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

(See `CLAUDE.md` "Performance SLAs" for the current documented exception on fare-estimate latency.)

### Security
- All auth tokens in HttpOnly cookies; never in `localStorage`/`sessionStorage`
- Admin JWT and rider/driver token lifetimes per `CLAUDE.md` "Token lifetimes"
- OTP SHA-256 hashed at rest; 5-failure/hr Redis lockout; dev bypass gated by `ENV != production`
- No PII in logs, Sentry, or analytics (no GPS coords, full phone, full name, email, card data)
- Stripe idempotency enforced via `claim_stripe_event` before every webhook handler
- Settings (Stripe/Twilio keys) live in `app_settings` DB table — rotatable without redeploy
- Semgrep + OWASP Top Ten scanning on every PR; Gitleaks secret scanning with allowlist for dev-fixture false positives

### Reliability
- Zero silent error swallowing: all DB/auth/payment/dispatch errors surface via `logger.error` + HTTP 5xx
- All background loops are replay-safe (claim flags, idempotency keys, or atomic DB claims)
- WebSocket fan-out uses Redis pub/sub for cross-replica delivery
- Supabase `run_sync()` wraps all DB calls with one retry on H2 GOAWAY

### Compliance
- PIPEDA: data residency ca-central-1, right-to-delete per `CLAUDE.md` "Compliance (PIPEDA)", consent version stored at signup
- Saskatchewan Transportation Act retention: trip logs 7 yr, driver-vehicle linkage 7 yr, GPS at pickup/dropoff 3 yr, insurance period transitions 7 yr
- SGI insurance: 4 TNC periods logged append-only for every driver session
- WCAG 2.1 AA on all customer-facing surfaces
- GST (5%) and PST (6%) shown as separate receipt line items; T4A-compatible driver earnings

### Observability
- Sentry domain tags: `dispatch`, `payments`, `auth`, `corporate`, `safety`, `drivers`, `rides`, `admin`
- Metrics use the `spinr_<domain>_<metric>_<unit>` naming convention (see `CLAUDE.md` "Observability Conventions" for the canonical list — supersedes any dotted spelling)
- State transitions: info log + metric; user-visible errors: Sentry + error log

## Testing requirements

Coverage minimums, required test cases (every ride state transition, every fare branch, every
auth/RLS policy, every Stripe webhook type), and device-testing acceptance criteria are as defined
in `CLAUDE.md` "Testing Conventions". Device testing acceptance covers: rider booking flow
end-to-end, driver offer-accept-navigate-complete, admin ride creation/live map/driver list,
wallet top-up/pay/history, auth login/persist/refresh/logout, SOS trigger + emergency contact
notification, and WAV request-to-match.

## CI/CD requirements
- Backend, admin (Vitest), rider-app (Jest), and driver-app (Jest) test suites green on every PR to main
- Security/audit gates (npm-audit, Gitleaks) valid and passing
- Playwright E2E: auth properly mocked; all smoke tests pass
- Pre-production: all security gates flip from `continue-on-error: true` to blocking

## Deployment requirements
- Dev tier keys in `.env.local` (gitignored); CI secrets point to dev-tier sandboxes
- Mobile builds via Expo EAS, triggered only when commit message contains `[build]`
- Database migrations applied via `run_migrations.py` before a new backend deploy
- Supabase region ca-central-1 (PIPEDA compliance; never change without legal sign-off)
- Monitoring active before first real user: Sentry domain alerts, refresh-token-reuse alerting
- Data-breach runbook (`docs/runbooks/data-breach.md`) in place before launch

Current backend/frontend deployment topology (Fly.io primary / Railway standby, Vercel for
admin/shared) is documented in `CLAUDE.md` "Deployment" — treat that as the live source of truth
over any older deployment target named in the superseded source files.

## Requirement process (how an idea becomes a checkable requirement)

Every requirement, before implementation starts, states:

| Field | Test of adequacy |
|---|---|
| Problem statement | One sentence; a stranger could tell whether it's solved |
| Affected persona(s) | rider / driver / corporate-admin / internal-admin |
| Surface(s) | backend, rider-app, driver-app, admin-dashboard, shared |
| Acceptance criteria | Each one checkable by a named test, command, or manual step |
| Regulated-surface flags | touches rides? money? auth? corporate? safety? PII? |
| Guardrail check | passes the "is NOT" filter (see Out of scope, below); names the closest guardrail |
| KPI linkage | which KPI/SLA row this moves or risks |

Rules of engagement:
- Ambiguity is named and asked about, never silently resolved — with extra force on the ride state
  machine, money paths, and insurance periods.
- Vague asks are converted to checkable ones ("fix the bug" → "a test that reproduces it, then passes").
- No speculative features or unasked-for configurability; ship the minimum change that solves the
  stated problem.

Deep domain rules live in versioned context docs and bind every requirement that enters their
area — a requirement that contradicts its domain contract is wrong, or the contract needs a
recorded amendment (ADR + doc update), never a silent divergence:
- `.claude/context/domain-dispatch.md`, `domain-payments.md`, `domain-corporate.md`,
  `domain-safety.md`, `regulatory-sk.md`, `brand-spinr.md`

## Prioritization
- `ACTION_ITEMS.md` is the single prioritized backlog (A/B/C bands); work is picked from open
  `[ ]` items, and every unfixable finding lands there.
- `docs/PRODUCTION_READINESS.md` holds full context behind backlog items; `.claude/context/sprint-current.md`
  holds the (currently stale — see its own banner) active-sprint slice.
- KPI/SLA breaches outrank feature work — see the KPI table in `CLAUDE.md` for targets and their
  below-target signals.

## Out of scope / What Spinr Is NOT

See `CLAUDE.md` → "What Spinr Is NOT" for the full guardrail list (no per-trip commission, no
unbounded/hidden surge, no driver-control patterns, no data harvesting, no hidden fees, no
country-agnostic genericization, no 911 replacement). Every feature proposal is checked against
that list before implementation, regardless of revenue upside.
