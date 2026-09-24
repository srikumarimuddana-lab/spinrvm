# A4 — Dispatch, state machine & realtime

**Lane:** A4 · **Model:** sonnet / spinr-dispatch-reviewer · **Returned:** 2026-09-24 ~14:26 UTC (partial, 25-minute time box) · **Orchestrator note:** lane output pasted verbatim below; only this header was added. Only 5 of the 42 background loops were read in full (see DISPATCH-003) — the other 37 are UNKNOWN from this run, not attested safe.

---

SPINR DISPATCH & STATE-MACHINE AUDIT — Lane A4 (partial, time-boxed ~25min)
Repo: /home/user/spinrvm · Read-only, no files modified.

## (a) TABLES

### 1. State-guard implementation count

Contrary to a "N ad-hoc copies" risk, there are exactly **two canonical guard functions** plus a consistent atomic-CAS-filter pattern used at every other status-mutating write site I checked. Not one unified state-machine module, but not scattered duplication either — a documented two-guard model with a shared `_RIDE_STATE_PHRASE` table.

| # | Guard | Path:line | Scope | Mechanism |
|---|---|---|---|---|
| 1 | `_require_ride_in_state` | `backend/routes/drivers/_shared.py:885` | driver-initiated transitions (arrive/start/complete/cancel) | read-then-check (`status IN allowed`) — TOCTOU-safe here because only the owning driver can act, not a multi-actor race |
| 2 | `_require_ride_in_state_rider` | `backend/routes/rides/_shared.py:433` | rider-initiated transitions (cancel) | same shape, rider-scoped |
| 3 | accept_ride CAS filter | `backend/routes/drivers/ride_flow.py:383-409` | driver accept (offer→accepted, or broadcast searching→accepted) | atomic `update_one` filtered on `{status, driver_id}` — stronger than CLAUDE.md's generic `{'status':'searching'}` description (per-driver_id scoped) |
| 4 | offer-timeout CAS | `backend/routes/rides/matching.py:1794-1805` | driver_assigned→searching on 15s timeout | atomic CAS filtered on `{status: driver_assigned, driver_id}` |
| 5 | search-timeout auto-cancel CAS | `backend/routes/rides/matching.py:2230-2252` | searching→cancelled (~5min no drivers) | atomic CAS `{status: searching}`, single-attempt write policy |
| 6 | scheduled-dispatch claim | `backend/utils/scheduled_rides.py:448-463` | scheduled→searching | atomic CAS `{status: scheduled}` |
| 7 | admin cancel/complete CAS | `backend/routes/admin/rides.py:654-656, 868-870` | admin-initiated cancel/complete | atomic CAS `{id, status: status_from}` |
| 8 | v2/v3 batch-offer accept/decline | `backend/services/driver_offer_service.py` + a Postgres RPC (`claim_driver_atomic`/v3 claim) | batch dispatch accept/decline/preempt | single-transaction DB RPC, not app-level CAS |

VERDICT rule #1: not a violation — every write site I found guards on current status atomically or (for single-actor-owned transitions) via a defensible read-then-check. No bare "just write status" call sites found.

### 2. Transition → WS emit coverage

| Transition | Write path:line | WS emit path:line | rider? | driver? |
|---|---|---|---|---|
| scheduled → searching | `utils/scheduled_rides.py:450` | not confirmed in this pass (ride_requested-equivalent not traced) | UNKNOWN | UNKNOWN |
| searching → driver_assigned (offer sent) | `services/dispatch_service.py:613` / v3 RPC | `routes/rides/matching.py:1621` (`"type":"new_ride_assignment"`) | **MISSING** — no `"type":"driver_assigned"` (or equivalent) send to `rider_{id}` found anywhere in `matching.py`, `driver_offer_service.py` | yes |
| driver_assigned → driver_accepted | `routes/drivers/ride_flow.py:398-409` | `routes/drivers/ride_flow.py:614` (`"driver_accepted"` → rider) + `:624` (push, driver) | yes | yes (push only, no WS confirmed to driver's own connection) |
| driver_assigned → searching (offer timeout) | `routes/rides/matching.py:1794` | `:1862` (`driver_timeout` → rider), `:1900` (`ride_offer_expired` → driver) | yes | yes |
| two drivers race accept | `routes/drivers/ride_flow.py:398` (0 rows) | `services/driver_offer_service.py:218` (`ride_taken` → losing driver) | n/a | yes |
| driver_accepted → driver_arrived | `routes/drivers/ride_flow.py` (search hit `:1110`) | `:1110` (`driver_arrived` → rider) + `:1117` (push) | yes | n/a |
| in_progress → completed | `routes/drivers/ride_complete.py` | `:926` + `:978` (`ride_completed` → rider), `routes/rides/lifecycle.py:322/326/360` | yes | not confirmed to driver's own WS key (push at `:940`) |
| any active → cancelled (pre-trip only) | `routes/rides/cancellation.py`, `routes/admin/rides.py:654` | `ride_cancelled` (multiple sites, e.g. `matching.py:2331/2347/2359`) | yes | yes (`"type":"ride_cancelled", ...both}` per domain-dispatch.md — not individually re-verified per call site this pass) |
| searching → cancelled (auto, no drivers) | `routes/rides/matching.py:2242` | `:2329` (`ride_cancelled`, `is_auto:true`) | yes | n/a (no driver assigned yet) |

Real gap found: **no rider-facing WS event on `searching → driver_assigned`** (the offer-sent moment). `.claude/context/domain-dispatch.md`'s own event table documents this as `driver_assigned | backend → rider | ride_id, driver_id, eta, vehicle` — the code doesn't do it. Rider's client sees no update between the initial `ride_requested` and the eventual `driver_accepted` (or `driver_timeout` if the offer lapses). See DISPATCH-001 below.

### 3. Loop replay-safety (backend/core/lifespan.py / background_loop_registry.py)

Full 42-loop catalog placement is read directly from `backend/core/background_loop_registry.py:31-77` (VERIFIED — this file exists and is the source of truth; default `SPINR_PROCESS_ROLE=all` means every loop still runs on every replica today, matching CLAUDE.md's "runs on every replica" framing). I did **not** read the body of all 42 loops in this time box — only the dispatch-critical subset relevant to this lane. Full mechanism verified for these:

| Loop | Placement | Mechanism | Evidence |
|---|---|---|---|
| `scheduled_dispatcher (60s)` | api | atomic DB claim (`status='scheduled'` CAS) | `utils/scheduled_rides.py:448-463` VERIFIED |
| `driver_claim_reaper (60s)` | api | atomic claim (dispatch's own `claim_driver_atomic`) + best-effort Redis leader lock as throttle only | `utils/driver_claim_reaper.py:3-19,122-127` VERIFIED |
| `stuck_ride_sweeper (60s)` | api | atomic CAS write (`retry_policy="write"`) + best-effort leader lock throttle | `utils/stuck_ride_sweeper.py:157` + `routes/rides/matching.py:2230-2252` comment cross-reference VERIFIED |
| `offer_expiry_reaper (10s)` | api | atomic `pending→expired` claim + leader-lock throttle | `utils/offer_expiry_reaper.py:15,71,148` VERIFIED |
| `driver_readiness_reconciler (20s)` | api | best-effort leader lock; underlying reconciliation described as idempotent/duplicate-safe by design | `utils/driver_readiness_reconciler.py:3,16,197` VERIFIED (comment: "duplicate workers are therefore harmless") |
| `insurance_period_reconciler (10min)` | api | not read this pass | UNKNOWN |
| `stale_p3_closer (15min)`, `period1_distance_finalizer (5min)`, `route_deviation_alerter (30s)`, `safety_checkin (30s)`, `capacity_watchdog (60s)` | api | not read this pass | UNKNOWN — CLAUDE.md's Background-task-safety section already asserts route_deviation_alerter/safety_checkin have unguarded `SET NX` claims (fail-per-tick, not fail-open); take as ASSUMED per that doc, not independently re-verified |
| remaining ~27 non-dispatch loops (surge, corporate, T4A, statements, reconciliation, etc.) | mixed | not read | UNKNOWN — out of dispatch lane scope |

None of the dispatch-critical loops I actually read would double-act across replicas — all five use an atomic DB claim as the real safety mechanism, with the Redis leader lock explicitly documented as a best-effort throttle only (fails open, and the codebase says so in comments, consistent with CLAUDE.md's Background-task-safety section).

## (b) FINDING CARDS

### DISPATCH-001 — Rider gets no WS event when a driver is offered the ride (searching→driver_assigned is silent to the rider)
- Hierarchy: L2 Dispatch › L3 Matching & Offers › L4 Offer-sent notification › L5 Rider UI state during 15s offer window
- Severity: MEDIUM   Priority score: S×B×L ≈ 2×3×3 = 18 (not a strand/double-book — a contract/UX gap)
- Status: VERIFIED   Existing item: new (grep of ACTION_ITEMS.md for "driver_assigned" WS keyword returned no hits)
- Adversary: flaky network / regulator (as auditor: "does the client contract match the doc?")
- Evidence: `backend/routes/rides/matching.py:1621` (only `driver_{user_id}` gets `new_ride_assignment`); no `rider_{id}` send with a `driver_assigned`-equivalent type anywhere in `matching.py`, `services/driver_offer_service.py`, or `services/dispatch_service.py` (grepped `f"rider_{` across all three — only 3 hits, all `driver_timeout`/`ride_cancelled`). Documented contract: `.claude/context/domain-dispatch.md:40` and CLAUDE.md's "every state change must emit a WebSocket event keyed to both the rider and the driver connection (if assigned)".
- What happens (plain language): the rider's app has no signal that a driver has been found and an offer is out — the UI stays on whatever "searching" state it was already in for up to the full 15s offer window, then jumps straight to "driver accepted" (or "still searching" on timeout). Not a stranding bug (rider isn't misled about ride existing), but it's a documented-but-unimplemented event and a missed opportunity for "driver found, confirming..." UX.
- Root cause: the offer-sent path only notifies the driver being offered; no corresponding rider broadcast was added when the batch/v2 offer protocol was built.
- Recommendation: either (a) add a lightweight rider WS event (e.g. `driver_assigned` or `matching_in_progress`) at the same call site as the driver's `new_ride_assignment` send, or (b) if this is an intentional product decision (don't show the rider a driver who might still decline), correct `domain-dispatch.md`'s event table to remove the false contract rather than leave code and doc disagreeing.   Alternative considered: leave as-is and rely on `driver_accepted`/`driver_timeout` as the only rider-visible states — rejected as the fix-of-choice only if a human confirms it's deliberate; otherwise it's un-reviewed doc drift, which is worse than either option.
- Blast radius: rider-app WS client only; no driver-side or DB-side effect. No other consumer found.
- Rollout: additive (new WS send, no schema/migration) if choosing (a); doc-only if choosing (b).
- Verification to close: confirm with rider-app team whether a "driver found, awaiting confirmation" screen exists client-side and expects this event; if yes, add the send and a WS integration test; if no, edit the doc.

### DISPATCH-002 — Declined/timed-out-driver exclusion (offer_skip) is Redis-only; a Redis outage re-opens re-offering the same declining driver
- Hierarchy: L2 Dispatch › L3 Matching & Offers › L4 Re-matching after decline/timeout › L5 Redis degraded-mode fallback
- Severity: MEDIUM   Priority score: 2×2×2 = 8
- Status: VERIFIED   Existing item: new (this is documented as a known/accepted residual in the code itself, not previously filed as an ACTION_ITEMS entry — grepped for "offer_skip" and related keywords in `ACTION_ITEMS.md`, no hits)
- Adversary: flaky network (Redis outage) / colluding driver (repeatedly declining hoping to game position — not exploitable here since the effect only shows up when Redis is *down*, not attacker-controlled)
- Evidence: `backend/routes/rides/matching.py:1907-1916` (skip key set on timeout, `ttl=300`), `:661` and `:929` (candidate pool filtered against skip keys), `backend/services/driver_offer_service.py:50-54,187,238` (skip key set on decline). Explicit code-comment admission of the gap: `backend/migrations/402_dispatch_claim_batch.sql:125,390` and `403_dispatch_claim_batch_v2.sql:147,420` — "offer_skip guard in matching.py is skipped when Redis is down". `utils/redis_client.py` confirms Redis falls back to an in-process dict per replica in dev/outage — meaning in a true Redis-down production scenario the skip set is neither durable nor shared across replicas.
- What happens (plain language): under a real Redis outage (not the everyday case — Redis is otherwise required for presence/rate-limiting too), a driver who just declined or ignored an offer for a ride can be re-offered the exact same ride on the next dispatch tick, in violation of the rule-of-thumb in `domain-dispatch.md` step 6 and this audit's rule #9. Under normal operation (Redis up) the mechanism is real and works (VERIFIED, not just claimed).
- Root cause: no DB-durable/cross-replica fallback (e.g. a `declined_driver_ids` array column on `rides`, or writing to `ride_offers` and filtering candidates against `NOT IN (SELECT driver_id FROM ride_offers WHERE ride_id=... AND status IN ('declined','expired'))`) — the migration files acknowledge a `ride_offers` table exists and is the intended durable backstop but the comments say the current matching-candidate query doesn't consult it for this purpose.
- Recommendation: add the SQL-level exclusion against `ride_offers` (already the source of truth for offer status) as a durable fallback alongside the Redis fast-path, so Redis is a latency optimization, not the sole correctness mechanism for rule #9.   Alternative considered: leave as Redis-only, accept the residual risk since Redis-down already degrades many other dispatch paths — rejected as the standalone fix because this specific violation (re-offering a driver who explicitly said no) is a worse user experience than most other Redis-degraded behaviors, which tend to fail closed/slow rather than repeat an already-rejected match.
- Blast radius: `backend/routes/rides/matching.py` candidate-ranking query and `backend/services/dispatch_candidates.py`; grep shows no other reader of the `spinr:offer_skip:*` key namespace.
- Rollout: additive (new WHERE-NOT-IN subquery or `.in_()`-based exclude list), no migration needed if `ride_offers` already has the needed rows (it does, per the offer accept/decline code already writing to it).
- Verification to close: a test that simulates `redis_set` raising/unavailable and asserts a previously-declined driver is still excluded from the next `rank_candidates`/`admit_candidates_v2` call.

### DISPATCH-003 — Full 42-loop replay-safety survey incomplete this pass (audit scope limitation, not a code defect)
- Hierarchy: L2 Dispatch › L3 Background loops › L4 Cross-replica safety
- Severity: RECOMMENDATION   Priority score: n/a
- Status: INFERRED (registry placement VERIFIED via `background_loop_registry.py`; individual loop-body safety mechanism VERIFIED for only 5 of 42; the other 37 are UNKNOWN this pass, not attested)
- Adversary: auditor
- Evidence: `backend/core/background_loop_registry.py:31-77` (full catalog + placement), 5 loop bodies read directly (table above)
- What happens (plain language): this report's loop-safety table is only fully evidenced for the 5 dispatch-adjacent loops (`scheduled_dispatcher`, `driver_claim_reaper`, `stuck_ride_sweeper`, `offer_expiry_reaper`, `driver_readiness_reconciler`) — all 5 confirmed atomic-claim-based and safe. The remaining ~37 loops (surge, corporate, payments, safety, statements, etc.) were not re-read in this dispatch-scoped pass; CLAUDE.md's own Background-task-safety section already asserts most use idempotency flags or a documented atomic claim, but that's a project-level claim, not independently re-verified here.
- Root cause: n/a — scope/time-box, not a bug.
- Recommendation: if a full cross-domain loop audit is needed, dispatch this to the `spinr-realtime-reliability-reviewer` agent (or SRE/observability lane) against the full 42-entry catalog; this lane only owns the 5 dispatch-relevant ones.   Alternative: accept CLAUDE.md's existing per-loop mechanism claim as sufficient — reasonable for non-dispatch loops, but not a substitute for a direct read if a specific loop is under active change.
- Blast radius: none (informational).
- Rollout: n/a
- Verification to close: a future lane/pass reads the remaining 37 loop bodies directly.

### DISPATCH-004 — Positive control: rider/driver going-offline-mid-offer and offline-mid-accept races are both closed with a single canonical helper (not a finding, logged as VERIFIED-CLEAN to prevent re-litigation)
- Severity: PASS (informational, included per instructions to note steelman items with evidence)
- Status: VERIFIED
- Evidence: `backend/routes/drivers/ride_flow.py:107-130` (accept_ride re-reads `is_online` at accept time, not offer time — this audit's rule #10, done); `backend/utils/insurance_periods.py:272-311` (`release_driver_and_close_period` — single implementation replacing 5 previously-hand-mirrored copies, explicitly citing CLAUDE.md's 2026-09-12 "fix in one copy never reached its sibling" gate as the reason it was consolidated); `backend/routes/drivers/status.py:1297-1337` (post-write claim re-check closes the go-online / dispatch-claim TOCTOU window, with the residual narrow exposure explicitly documented rather than hidden).
- Why flagged as its own card: these are exactly the three race classes rule #4/#5/#10 ask about, and given how carefully self-documented the fixes already are (down to citing the CLAUDE.md gate that motivated them), a future audit should not re-flag this pattern as new without first reading these three sites.

## (c) STEELMAN — what's done well
- The `is_available ⇒ is_online` invariant isn't just checked at write time — `set_driver_available()` is a single canonical function whose callers (including the offer-release path) get the clamp for free, and CLAUDE.md's own retrospective on this exact bug class ("fix proven in one app, never reached the sibling") is cited in the code comment as the reason it was centralized (`backend/utils/insurance_periods.py:281-287`).
- Acceptance race guard is stronger than the minimum bar: the CAS filter is scoped to `{status, driver_id}` (not just `{status:'searching'}`), and on a lost race the handler re-reads to distinguish "this driver's own duplicate request won" from "genuinely taken by someone else" before returning 409 — avoiding a false "ride taken" to the same driver's retry/double-tap.
- Scheduled-dispatch double-fire (rule #6) is a real atomic DB claim (`status='scheduled'` CAS), not just a Redis lock — explicitly designed to survive the dev fallback where Redis is unavailable, and the code comment names the "Background Loop Recipe" contract it's satisfying.
- Offer-timeout driver release (rule #4) is ordered correctly and is atomic: the CAS revert to `searching` happens before the miss-streak/auto-offline/release-and-close-period logic, with an explicit comment ("acting on a driver who just became correctly obligated... would be wrong") showing the ordering was a deliberate decision, not luck.
- WebSocket reconnect has a real replay/resync mechanism (`backend/routes/websocket.py:960-968`, a sequence-numbered outbox replayed on reconnect), addressing rule #5's "what does a client miss during reconnect" concern — not just a bare heartbeat.

## (d) NOT VERIFIED
- Rider WS coverage for `driver_arrived`→`in_progress` transition specifically to the driver's own WS connection (push confirmed, WS-to-driver-self not traced).
- Full body-read of 37 of the 42 `core/lifespan.py` loops (see DISPATCH-003) — only the 5 dispatch-critical ones were read in full.
- `insurance_period_reconciler`, `stale_p3_closer`, `route_deviation_alerter`, `safety_checkin`, `capacity_watchdog` mechanism details — placement (api/deferred) confirmed via registry, internal claim logic not read this pass.
- Whether DISPATCH-001 (no rider WS event at driver_assigned) is a deliberate product decision or an unnoticed gap — flagged as VERIFIED code behavior, but the "is this intentional" question is genuinely open and needs a human/product answer, not assumed either way.
- No PR/diff context was supplied for this run — impact cross-check skipped (not applicable to a baseline audit).

VERDICT (lane): NEEDS DISPATCH-TEAM REVIEW on DISPATCH-001 (confirm intent) and DISPATCH-002 (add DB-durable fallback for the declined-driver exclusion) — neither is a strand/double-book-class CRITICAL, both are real, actionable gaps against the documented contract. No CRITICAL or HIGH findings surfaced in this pass; the core state-machine, race-guard, and replay-safety mechanisms actually inspected are mature and well self-documented.
