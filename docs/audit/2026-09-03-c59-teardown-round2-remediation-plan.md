# C59: Teardown Round 2 remediation plan (post-C50 review findings)

**Source:** Cursor "Engineering Director Teardown Round 2" review at commit `bf5d0ac`, closed 2026-09-03. Verdict: B- grade holds; several live defects confirmed real, a few of the original teardown's rankings/claims were wrong and corrected here. Read the source review before starting any item — do not re-derive scope from this doc alone if something's ambiguous.

**Owner:** Ravi (Engineering Manager, SDLC lead), orchestrating his team. Reports to Pandi.

**Ground rules for every item below:**
- Follow AGENTS.md conventions throughout: migrations are append-only + forward-compatible + RLS-first, money/insurance-period code uses `Decimal`, every new state transition gets a test, errors surface loudly (no silent `logger.warning`-and-continue on DB/auth/payment/dispatch paths).
- Every item ships behind existing conventions (flags in `app_settings`, not env vars) where it changes live behavior. Read-only/observability fixes don't need a flag.
- No item in this plan touches production directly. Land on a feature branch off `staging`, PR against `staging`, and get Pandi's sign-off before any prod deploy — same discipline as C50.
- If you hit something genuinely ambiguous or discover the finding doesn't hold up under your own investigation, STOP and report back rather than guessing or silently dropping it.

---

## P0 — Compliance-critical, do first

### T1: Admin force-cancel doesn't guard ride state or close the insurance period
**Finding:** `update_ride`'s admin force-cancel path has no status predicate — only `completed`/`cancelled` are rejected. An admin can force-cancel a ride that's `in_progress`. `record_period_transition` only fires on force-*complete*, never on force-*cancel*, so an insurance-period row started at `driver_assigned`/`in_progress` never gets its `ended_at` set when the ride is force-cancelled admin-side. Test suite has zero coverage of the `in_progress` force-cancel case.
**Why P0:** direct violation of AGENTS.md's insurance-period rules — "every period transition is logged... append-only" implies transitions must actually close, and per CLAUDE.md's ride state machine, `in_progress` should never legally reach `cancelled` at all (only `completed`). This finding suggests the admin path bypasses that invariant entirely.
**Scope:**
1. Add a `_require_ride_in_state()` guard (per CLAUDE.md convention) to the admin force-cancel handler — reject `in_progress` outright, matching the documented state machine (`in_progress` → `completed` only).
2. If product intent is that admin force-cancel of an in-progress ride is a legitimate emergency path (verify with Pandi/Lakshmi before assuming either way — this is a real product question, not just a bug), then instead: wire `record_period_transition` into the force-cancel handler so the open insurance period always closes, with `ended_at` and a reason code.
3. Add test coverage for both the `in_progress` guard-rejection case and (if applicable) the force-cancel-closes-period case, in `test_ride_state_machine.py` per CLAUDE.md's testing conventions.
**Owner suggestion:** Surya (backend) implements, Divya (Security & Compliance) reviews — this is exactly her charter.

---

## P1 — Real, ship soon

### T2: Dispatch claim-leak on mid-batch exception (overlaps backlog C51)
**Finding:** the batch-claim loop claims drivers one at a time with no release if a later claim in the same batch throws — earlier-claimed drivers stay stuck until the orphan-claim reaper cycles. Same root cause Ravi's team already filed as backlog C51 during the C50 Phase 0 review.
**Scope:** wrap the claim loop in proper cleanup — on any exception mid-batch, release already-claimed-but-not-yet-offered drivers back to available before re-raising or returning the partial result. Add a test that forces a mid-batch exception and asserts no driver is left claimed-but-orphaned.
**Owner suggestion:** Surya — he already has context on this from the C50 Phase 2 review.

### T3: Staging schema is scaffolding-only; Fly vs Railway rollback behavior is inconsistent
**Finding:** confirms what we already found in T16 (staging DB stuck at migration 371 vs repo's 40x). New info: Railway auto-rolls-back on a failed health probe; Fly does not. Given Railway is our documented standby/failover host (per memory: Kiran confirmed Railway + its own Redis is available for failover), this asymmetry means a bad deploy behaves differently depending on which host takes it.
**Scope:**
1. Get a staging `DATABASE_URL` (blocked on Pandi/Kiran — dashboard-sourced, not committed) and bring staging to migration parity.
2. Document the Fly-vs-Railway rollback asymmetry explicitly in `docs/runbooks/staging-environment.md` or a new failover runbook — at minimum, so it's a known gap, not a surprise during a real incident.
3. Scope (don't necessarily build yet) what closing that gap would take on Fly — health-check-triggered rollback isn't native to Fly the way it is to Railway; this may need `fly deploy --strategy canary` + a scripted health-check watchdog, or accepting the asymmetry and leaning on Railway as documented standby. Bring options back to Pandi, don't just implement one.
**Owner suggestion:** Vikram (DevOps/Infra) — this is squarely his charter.

---

## P2 — Real but smaller; batch together

### T4: Admin JWT middleware decodes but doesn't verify signature
**Finding:** admin routes' auth middleware decodes the JWT payload without verifying the signature — meaning a forged token with the right claims shape could pass. Per CLAUDE.md's JWT trust model, admin JWTs are "fully trusted" once past auth — but that trust model assumes the token was actually verified to get there.
**Scope:** add real signature verification (`jwtVerify` equivalent) as a gate in front of every admin route, not just decode-and-trust. Confirm this doesn't break the existing 12hr admin token lifetime or refresh-rotation behavior — test both a valid and a tampered token.
**Owner suggestion:** Divya (Security & Compliance) leads, Surya implements — this is a real auth vulnerability, treat it with matching urgency even though it's filed P2 here for sequencing, not severity.

### T5: Ride export endpoint has no audit trail
**Finding:** the ride export endpoint isn't logged anywhere. Per CLAUDE.md's PIPEDA section, user-rights data exports should be traceable, and per the Observability Conventions, "admin actions" are supposed to go to an audit table + info log.
**Scope:** add an audit-table write (`<entity>_audit` pattern per CLAUDE.md's table-naming convention) on every export call — who exported, what filter/scope, when. No new migration needed if a suitable admin-audit table already exists; check `backend/routes/admin/` for an existing pattern before creating a new table.
**Owner suggestion:** Surya.

### T6: LogRocket captures ride maps with no redaction
**Finding:** session replay tooling (LogRocket) is capturing rider/driver map views with no PII redaction — a real PIPEDA concern given CLAUDE.md explicitly forbids raw GPS coordinates, exact addresses, etc. from logs/analytics.
**Scope:** either (a) add LogRocket's redaction/masking config to blur or omit the map viewport entirely on ride-tracking screens, or (b) if redaction can't be done reliably, disable LogRocket capture on those specific screens via a flag. Divya should make the call on which is acceptable.
**Owner suggestion:** Anika (Frontend/Mobile) implements, Divya approves the approach before it ships.

### T7: `activeRide` persisted client-side via React Query; Redis-down fails open on offer cooldown; Maps API key bundled client-side; unset `REDIS_URL` fails open silently
**Finding:** four smaller items bundled together since they're all low-effort, real, and non-urgent:
- `activeRide` persisting client-side raises a staleness/privacy question (does it survive logout? get cleared on ride completion?)
- Redis-down currently fails *open* on the offer-cooldown check — meaning during a Redis outage, the same driver could get double-offered. Per AGENTS.md's Redis Transparency note, this needs to be a documented tradeoff, not a silent gap.
- Google Maps API key is bundled into the rider app client-side (standard practice for mobile Maps SDKs, but confirm it's properly domain/bundle-restricted in the Google Cloud Console — this may already be fine, verify rather than assume)
- `REDIS_URL` unset in production would silently fall back to an in-process dict (per AGENTS.md's documented behavior) rather than failing loud — confirm this is only ever true in dev, and that production has a startup check that fails loud if `REDIS_URL` is unset.
**Scope:** each is small — bundle into one PR per finding, don't need separate branches. Prioritize the Redis-fail-open one; it's the only one with a real (if narrow) production-safety edge case.
**Owner suggestion:** Anika for the client-side `activeRide` question, Vikram for the Redis/env-var checks, Surya for the Maps key verification.

### T8: Rider API needs `?last_seq=` pagination
**Finding:** an unspecified rider-facing list endpoint lacks pagination, risking the "reading full ride list on dashboards without pagination" anti-pattern flagged in AGENTS.md's Performance SLA section.
**Scope:** confirm which endpoint (the review didn't fully specify — Ravi's team should identify it, likely ride history or similar), add `last_seq`-based cursor pagination matching existing patterns elsewhere in the codebase if any exist.
**Owner suggestion:** Surya.

---

## Explicitly OUT OF SCOPE — do not implement, this teardown's own review already rejected these

- **Do NOT add a watchdog respawn for background loops.** `core/lifespan.py`'s `_restartable` wrapper already restarts crashed tasks — this would be redundant work chasing a non-problem.
- **Do NOT "fix" corporate billing fail-open behavior.** It is intentional, already documented, and already tested. Any change here requires an ADR and Pandi/Kiran's explicit sign-off — never a silent code change, even if it looks like a bug on first read.
- **Do NOT touch OTP bypass logic.** It's already correctly gated on `ENV.lower() == "production"` per AGENTS.md's OTP Security section — the review confirmed no actual gap here.
- One pure doc fix, not a code change: the architecture doc's pool-flag direction table had it backwards (direct pool is flag-**ON**, not flag-off) — just correct the doc, don't touch code for this.

---

## Sequencing recommendation (Pandi's call to finalize)

1. **T1 first, alone** — compliance-critical, small blast radius, needs Divya's review regardless.
2. **T2 + T4** in parallel — both are real correctness/security bugs, neither depends on the other.
3. **T3** — partially blocked on Pandi/Kiran (staging DATABASE_URL); Vikram can start the documentation/options-scoping half immediately.
4. **T5, T6, T7, T8** — batch as a P2 sweep once P0/P1 land; low individual risk, fine to parallelize across Surya/Anika/Vikram.

Do not start T3's "close the Fly/Railway gap" implementation sub-item without Pandi's explicit go — that's an infra-shape decision, not a bug fix.
