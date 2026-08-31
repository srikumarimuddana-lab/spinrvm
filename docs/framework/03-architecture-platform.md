# Pillar 3 — Architecture & Platform

> The system shape, the invariants that hold it together, and the rules for
> changing it. Source of truth for topology is `CLAUDE.md` → Architecture;
> decisions live in `docs/adr/`; this pillar states the *principles* that
> keep the architecture coherent as it grows.

## Topology

```
Rider App ──┐
Driver App ─┤── REST + WebSocket ──► FastAPI (Fly.io primary / Railway standby)
Admin ───────┘                            │
                             Supabase(Postgres+RLS)  Redis  Stripe
                             Firebase  Twilio  FCM
```

- One horizontally-scalable backend process; ~25 domain routers; thin service
  layer; repositories own DB access and query-filter escaping.
- All durable state in Supabase (Canadian region — moving it is a compliance
  event); ephemeral cache/pub-sub in Redis; WS fan-out across replicas via
  the `spinr:ws:dispatch` Redis channel.
- 18 background asyncio loops spawned at startup run on **every** replica
  concurrently — replay safety is an admission requirement, not an
  optimization (atomic DB claims, `reminder_sent` flags, idempotency keys;
  recipe: `spinr-background-loop` skill).

## Architectural principles

### 1. State machines over statuses
`rides.status` is a real state machine with an enumerated transition graph,
guarded by `_require_ride_in_state()`, raced-checked at acceptance
(`{'status': 'searching'}` filter → 0 rows → 409 + `ride_taken`), and
observable (every transition emits a WS event and increments
`spinr_rides_state_transition_total`). Any value outside the graph is a
contract violation surfaced loudly. New lifecycle features extend the graph
explicitly; they never bolt on side-channel flags that let state drift.

### 2. Money is Decimal, once-written, idempotent
- `Decimal` only (`_d()`, `_round()`, `_f()`); a pre-commit hook blocks float
  arithmetic in fare code.
- Stripe webhooks claim `stripe_events` rows before processing; corporate
  wallet deltas go through the `corporate_wallet_apply_delta` Postgres
  function for row-locking and idempotency. There is exactly one way to move
  money in each direction — new features call it, never reimplement it.

### 3. Layered trust, re-verified at the boundary
- Admin JWTs are trusted (role/email/modules in claims, 1 h lifetime);
  rider/driver role is **re-read from the DB on every request** — the JWT
  role claim is never trusted for non-admin tokens.
- Refresh tokens: 30 days, SHA-256 at rest, rotated on every use.
- WS connections authenticate in-band (first message), keyed
  `"driver_{id}"`/`"rider_{id}"`, heartbeat + rate-limited + size-capped.
- RLS in Supabase is the second, independent enforcement layer under the API.

### 4. Derived truth over duplicated truth
- `is_available` is computed (`is_online AND no active ride AND no pending
  offer`), never set independently; the invariant `is_available ⇒ is_online`
  must hold.
- Insurance periods derive from ride state, not driver UI — Period 2 starts
  at `driver_accepted` (batch-offer model), Period 3 requires a `ride_id` on
  an `in_progress` ride. Period rows are append-only regulatory audit data.
- When a fact can be derived, derive it; when it must be denormalized for
  performance, name the reconciliation job that heals divergence.

### 5. Degrade loudly, never silently
- Redis absent → in-process fallback, with the documented consequence
  (rate-limit/OTP state lost on restart) stated, not hidden.
- DB/auth/payment/dispatch errors surface as `logger.error` + clean
  `HTTPException` (503/502) — never a warning-and-continue, never a fallback
  path that masks the symptom (the duplicate-account incident rule).
- Google Directions slow → deliberate 3.5 s wait then haversine fallback: a
  *recorded* SLA exception (ADR'd, audit-tracked) because undercharging
  riders is worse than a slow quote. Exceptions to targets are decisions
  with writeups, not drift.

### 6. Config over deploys
Rotatable secrets and operational toggles (Stripe/Twilio/Maps keys, feature
flags) live in the `app_settings` table, managed from the admin dashboard —
rotation and rollout without redeploy. `.env` holds only bootstrap secrets,
with production startup failing fast on weak values.

### 7. Additive schema evolution
Migrations are append-only, filename-keyed, ordered, applied by
`run_migrations.py` in a < 30 s window. Prefer a new column/flag over
repurposing one; repurposing requires a migration plus a dual-read window.
Full conventions: `backend/migrations/CLAUDE.md`.

### 8. The corporate layer is a lamination, not a fork
Corporate billing sits on top of the consumer ride product; payment-source
selection happens at fare settlement without modifying ride/driver logic.
This pattern generalizes: new business models attach at settlement,
notification, or reporting seams — they do not fork the core lifecycle.

## Change rules

- Structural decisions get an ADR (`docs/adr/`, `/adr` skill) — deployment
  topology, trust-model changes, new external dependencies, new background
  loops, anything a future engineer would ask "why is it like this?" about.
- The dual-import pattern (`try: from .x import … except ImportError:`) is
  load-bearing for both run modes; never "simplify" it away.
- New external calls in request handlers must not block the hot path —
  queue via `asyncio.create_task` or a background loop (see the SLA
  anti-pattern list).
- Frontend surfaces (Expo rider/driver, Next.js admin) consume the same
  REST/WS contract; shared logic belongs in `shared/`, and a change to a
  shared component names every importer in its blast radius before merge.

## What we deliberately do not build (yet)

Microservices, Kubernetes, event-sourcing, multi-region active-active, and
service meshes are all *scale problems Spinr does not have*. The monolith
with disciplined seams (routers → services → repositories) plus DNS-level
fail-over is simpler, cheaper, and faster to reason about at
Saskatchewan-launch scale. The scorecard (Pillar 7) names the triggers that
would justify revisiting this — sustained replica count, team size, or a
domain whose deploy cadence diverges — so the decision is made by evidence,
not fashion.
