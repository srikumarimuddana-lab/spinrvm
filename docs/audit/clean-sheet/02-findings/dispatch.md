# R8 — Dispatch & State-Machine Audit (Wave W2)

Status: COMPLETE (all 10 sections written). Written incrementally per session-limit
resilience rule; each section was appended to disk as soon as it was finished.

Repo: /home/user/spinrvm. Read-only audit; no files edited besides this one.
Builds on `docs/audit/clean-sheet/rapid-baseline-2026-09-24/A4-dispatch-realtime.md`
(cites DISPATCH-001..004) and re-verifies/deepens those findings with direct code reads.

## Contents
1. Steelman
2. Attack (adversary passes)
3. Finding cards (DISPATCH-00x)
4. State-write-site table (rule #8 item)
5. Scenario cards §3.1/3.2/3.3
6. Rebuild Delta card
7. Top 5 findings
8. NOT verified
9. Open questions for a human
10. Escalations

---

## 1. Steelman — what's done right (re-verified, direct code reads)

- **Two canonical state guards + a consistent atomic-CAS pattern everywhere else** —
  re-confirmed. `_require_ride_in_state` (`backend/routes/drivers/_shared.py:885`) and
  `_require_ride_in_state_rider` (`backend/routes/rides/_shared.py:433`) cover
  single-actor-owned transitions; every multi-actor transition (accept, offer-timeout,
  search-timeout, scheduled-dispatch claim, admin cancel/complete, v2/v3 offer
  resolution) uses an atomic `{status: X, ...}` CAS filter or a single-transaction
  Postgres RPC. VERIFIED — no bare "just write status" call site found in
  `matching.py`, `ride_flow.py`, `ride_complete.py`, `cancellation.py`,
  `scheduled_rides.py`, `admin/rides.py`.
- **Insurance-period consolidation is real, not aspirational.**
  `release_driver_and_close_period()` (`backend/utils/insurance_periods.py:272-410`)
  is now the single implementation for "release a driver from an offer/assignment and
  close their open Period 2 with whatever they actually are now," replacing 5
  hand-mirrored copies (`_release_loser`, `decline_ride` in `ride_flow.py`,
  `cancellation.py`, `_offer_timeout_handler` and `process_expired_offer` in
  `matching.py`). The docstring explicitly cites CLAUDE.md's 2026-09-12 "fix proven in
  one copy, never reached its sibling" finding as the reason it was centralized —
  this closes HIST item 6 ("two insurance-period implementations") for the
  release-path specifically. VERIFIED at all 5 call sites via grep
  (`release_driver_and_close_period(` appears in `ride_flow.py` ×2,
  `cancellation.py`, `matching.py` ×2).
- **Offer-timeout release ordering is deliberately correct.** In
  `_offer_timeout_handler` (`matching.py:1794-1858`), the atomic CAS revert to
  `searching` happens *before* the miss-streak/auto-offline/
  `release_driver_and_close_period` logic, with the comment explaining why:
  "acting on a driver who just became correctly obligated to this ride ... would be
  wrong." This directly satisfies rule #4's "verify the timeout releases the driver
  before or atomically with re-entering searching, not after." VERIFIED.
- **Batch-claim RPC (migrations 402/403/448) has a real DB-level durable backstop for
  re-offer prevention that the rapid baseline did not credit.** `ride_offers` carries a
  table-wide `UNIQUE (ride_id, driver_id)` constraint (migration 100). The batch-claim
  RPC's offer insert uses `ON CONFLICT ON CONSTRAINT ride_offers_ride_driver_uq DO
  NOTHING` (migration 402 lines 394-402): if a candidate already has ANY prior
  `ride_offers` row for this ride (pending/declined/expired/accepted/cancelled,
  regardless of status), the RPC silently fails to claim them, releases them
  (`is_available = true`), and reports them unclaimed — and `matching.py` only appends
  a driver to `claimed_drivers` (which drives the WS/FCM notify loop) `if
  entry.get("claimed")` (line 1127) / `if not _row.get("claimed"): continue` (line
  1275). So on the flagged-on batch/v3 path, a declined/expired driver who slips past
  the Redis `offer_skip` pre-filter (DISPATCH-002) still cannot be silently
  re-notified — the DB constraint catches it as a second, independent layer. This
  matters because it means DISPATCH-002's real-world blast radius is narrower than
  "Redis down ⇒ silent re-offer" on this path. See DISPATCH-002 (updated) below for why
  this backstop does **not** cover the default legacy path.
- **Driver-online re-check at acceptance time (rule #10) is real, not just
  documented.** `accept_ride`'s CAS filter is scoped to `{status, driver_id}` (stronger
  than the CLAUDE.md-documented minimum), and on a lost race it re-reads to distinguish
  "this driver's own duplicate request won" from "genuinely taken" before returning 409
  (avoiding a false `ride_taken` on a client retry/double-tap). Confirmed via rapid
  baseline DISPATCH-004 and re-verified in this pass at `ride_flow.py:395-436`.
- **`scheduled → searching` DOES emit a rider WS event** — this closes rapid-baseline
  gap #3 (previously UNKNOWN/"not confirmed"). `utils/scheduled_rides.py:616`:
  `manager.broadcast_ride_status(ride_id, "searching", rider_id=rider_id,
  is_scheduled=True)`, guarded to run only after a final re-read confirms the ride is
  still `searching` (protects against a cancellation winning the race between the
  claim and the broadcast). Also mirrors the transition to admin monitoring as a new
  row (`ride_requested` broadcast) since the admin dashboard's `ride_status_changed`
  handler only patches existing rows. VERIFIED.
- **`driver_arrived → in_progress` not reaching the driver's own WS socket is a
  documented product decision, not a gap** — closes rapid-baseline's "NOT VERIFIED"
  item #4. `ConnectionManager.broadcast_ride_status()` (`backend/socket_manager.py:
  484-529`) takes an explicit `driver_user_id` parameter and its docstring says: "pass
  None for connections that shouldn't receive the event (e.g. driver_user_id=None for
  transitions the driver triggers themselves and already knows about)." Both the
  OTP-gated production `start` path (`ride_flow.py:1263-1348`, `verify_pickup_otp`) and
  the `arrive` path pass `driver_user_id` as unset/None when the driver is the actor —
  deliberate, documented, and consistent. VERIFIED.
- **7-item non-atomic-CAS recurrence family (HIST, `00-history.md:208`) — the 4 items
  actually inside dispatch scope are all closed.** C54 (batch-claim loop stranding
  claimed drivers on exception) closed 2026-09-04; C56 (`claim_driver_atomic`'s
  `run_sync` read-vs-write retry policy) closed 2026-09-03; C66 (`admin_complete_ride`
  no optimistic lock) closed 2026-09-04; C133 (`cancel_ride_rider` reading stale
  `auth_status` before its own claim) closed 2026-09-23 — all VERIFIED via
  `ACTION_ITEMS.md` entries with dated fixes and named regression tests. (B19, C77,
  C104 are payments-domain, not re-audited here — R9's scope.)
- **FCM/push is never inline-awaited on the dispatch hot path.** The offer notify loop
  collects `_dispatch_pushes` and fires one grouped `_deps.spawn(...)` batch call
  (`matching.py:1712-1719`) after building all payloads — satisfies rule #8's "never
  await Twilio/FCM inline" and explicitly documents the N+1-avoidance rationale (one
  grouped FCM send instead of N independent round-trips). VERIFIED.
- **Quest-progress enrichment was moved off the hot path's N+1 pattern.** Comment at
  `matching.py:1483-1499` explains this used to be a serial per-driver embedded-join
  query (up to `max_offers`=10 round-trips) on the P95<2s path and was batched into one
  `.in_()` query. VERIFIED — satisfies rule #8's N+1 flag proactively, already fixed.


---

## 2. Attack — adversary passes

- **Flaky network (Redis outage)**: DISPATCH-002 below — the offer-skip exclusion set
  is Redis-primary with no DB-level pre-filter (only a post-hoc DB catch on the
  flagged-off-by-default batch path; the live default legacy path fails worse — see
  DISPATCH-002 update). A sustained Redis outage degrades this specific guarantee
  (rule #9) while most other dispatch paths fail closed/slow instead of repeating an
  already-rejected match.
- **Regulator/auditor**: `.claude/context/domain-dispatch.md:40` documents a
  `driver_assigned` WS event to the rider that the code does not send (DISPATCH-001).
  An auditor comparing the doc contract to the wire protocol finds a real drift. Not a
  safety/compliance issue (insurance-period logging is unaffected — Period 2 opens
  correctly regardless of whether the rider is told), but a documented-contract
  violation that a careful plaintiff's-lawyer read could frame as "the rider had no way
  to know a driver was ever offered this ride" in a dispute about wait-time
  transparency.
- **Colluding/gaming driver**: attempted a plausible attack of "decline repeatedly to
  game position/ordering" — not exploitable via DISPATCH-002, because the effect (a
  driver re-offered a ride they declined) only manifests when Redis is degraded, not
  under attacker control, and it re-offers the *same* ride to the *same* driver rather
  than manipulating ranking order for other rides.
- **Hostile network/device (driver)**: offer timeout while phone backgrounded/locked —
  the 30s authoritative server-side timeout (`_offer_timeout_handler`,
  `timeout_seconds=30` = 15s client countdown + 15s grace) exists specifically so a
  crashed/offline device does not strand the ride; confirmed in code comment
  (`matching.py:1750-1754`). Handled.
- **Hostile network/device (rider)**: rider cancels during the 15s offer window — the
  offer-timeout and accept-path CAS filters both re-read current ride state before
  acting (`ride.get("status") != RideStatus.DRIVER_ASSIGNED` pre-checks, then the CAS
  itself as the real guard), so a rider cancellation racing an in-flight offer cannot
  produce a state where both a cancellation and an acceptance apply. Handled per
  `domain-dispatch.md`'s own "Rider cancelling during offer" guard.
- **Malicious insider / auditor on retention**: insurance-period rows are append-only
  (`release_driver_and_close_period` and `record_period_transition` only ever insert a
  new row + set `ended_at` on the prior open row — no UPDATE-in-place of `period`
  found in `insurance_periods.py`). Not independently re-verified against every write
  path in this pass (see NOT VERIFIED).

---

## 3. Finding cards (DISPATCH-00x)

### DISPATCH-001 — Rider gets no WS event when a driver is offered the ride (re-confirmed, unchanged from rapid baseline)
- Hierarchy: L2 Dispatch › L3 Matching & Offers › L4 Offer-sent notification › L5 Rider UI state during 15s offer window
- Severity: MEDIUM   Priority score: 2×3×3 = 18
- Status: VERIFIED (re-verified by direct read, not just re-cited)   Existing item: rapid-baseline DISPATCH-001; grep of `ACTION_ITEMS.md` for "driver_assigned" + WS keywords: no hits — still not filed as a tracked item.
- Adversary: regulator/auditor (doc-vs-code contract drift), flaky network (rider left with no intermediate feedback)
- Evidence: `backend/routes/rides/matching.py` — full offer-notify loop (lines 1531-1719) sends `dispatch_payload` (`type: "new_ride_assignment"`) only to `f"driver_{driver['user_id']}"` (line 1621). Grepped `f"rider_{` across `matching.py`, `services/driver_offer_service.py`, `services/dispatch_service.py`: only 3 hits total, all `driver_timeout` (line 1868) or `ride_cancelled` (lines 2168, 2335) — none at the offer-sent moment. Documented contract: `.claude/context/domain-dispatch.md:40` (`driver_assigned | backend → rider | ride_id, driver_id, eta, vehicle`) and CLAUDE.md's "every state change must emit a WebSocket event keyed to both the rider and the driver connection."
- What happens (plain language): between the rider's initial `ride_requested` event and either `driver_accepted` or `driver_timeout`, the rider's app receives no signal that a driver was found and an offer is in flight. Not a stranding bug — the ride genuinely is being worked — but a missed "driver found, confirming..." UX moment the client contract promises and the driver side gets.
- Root cause: the offer-sent notify loop was built driver-first (it needs per-driver enrichment: ETA, ride card, push); no symmetric rider-facing broadcast was added when the single- and batch-offer protocols were built.
- Recommendation: (a) add a lightweight rider WS event (e.g. reuse `broadcast_ride_status(ride_id, "driver_assigned", rider_id=...)` — the helper already exists and is used elsewhere for exactly this shape) at the same call site as the driver's `new_ride_assignment` send, or (b) if intentional (avoid showing a driver who might still decline), correct `domain-dispatch.md`'s event table instead of leaving code and doc to silently disagree.   Alternative considered: leave as-is, relying on `driver_accepted`/`driver_timeout` as the only rider-visible states post-request — rejected as the default outcome because it leaves an unreviewed doc/code mismatch rather than a reviewed decision either way.
- Blast radius: rider-app WS client only (grep confirms no other reader of this specific gap). `broadcast_ride_status` is already used in 6+ other transition sites in this codebase, so wiring it in here is a pattern match, not a new mechanism.
- Rollout: additive (new WS send using an existing helper), no schema/migration, if (a). Doc-only edit if (b).
- Verification to close: confirm with rider-app/product whether a "driver found, awaiting confirmation" screen exists client-side and expects this event; if yes, add the send + a WS integration test asserting `rider_{id}` receives a `driver_assigned`-typed message within the same dispatch attempt that sends `new_ride_assignment` to the driver; if no, edit the doc and close as a documentation fix.

### DISPATCH-002 — Declined/timed-out driver exclusion is Redis-primary; the DB-level backstop that exists only covers the flagged-off-by-default dispatch path (deepened from rapid baseline)
- Hierarchy: L2 Dispatch › L3 Matching & Offers › L4 Re-matching after decline/timeout › L5 Redis-degraded and default-path behavior
- Severity: MEDIUM-HIGH (raised from rapid baseline's MEDIUM — see the new sub-finding below)   Priority score: 2×3×2 = 12
- Status: VERIFIED (both the original Redis-fail-open behavior and the NEW default-path sub-finding are directly read, not inferred)   Existing item: new — no ACTION_ITEMS hit for "offer_skip" or "ride_offers_ride_driver_uq" as a tracked risk.
- Adversary: flaky network (Redis outage); also a benign timing race independent of any outage (see sub-finding)
- Evidence:
  - Redis-primary filter: `matching.py:924-940` builds `_skip_keys` from `spinr:offer_skip:{ride_id}:{driver_id}` and MGETs them; on `_skip_exc` the code explicitly **fails open** — comment: "Redis configured-but-unavailable: redis_mget re-raises. Fail open (no skips) so an outage can't halt dispatch here" (line ~928-933). The cascade/fallback pool path (`matching.py:927-933`) has the identical fail-open MGET pattern.
  - `_set_offer_skip` writes are Redis-only in both the legacy timeout handler (`matching.py:1907-1916`, `ttl=300`) and the v2 decision service (`driver_offer_service.py:50-54`).
  - **DB-level backstop exists, but only on the flagged-off-by-default paths.** `ride_offers` has a table-wide `UNIQUE (ride_id, driver_id)` constraint (migration 100). The batch-claim RPC (migrations 402/403/448) inserts with `ON CONFLICT ON CONSTRAINT ride_offers_ride_driver_uq DO NOTHING` (`402_dispatch_claim_batch.sql:394-402`), explicitly documented as catching "a re-offer after decline/expiry that the Redis offer_skip guard did not catch" — on conflict it releases the driver and reports them unclaimed, and `matching.py` only notifies drivers where `entry.get("claimed")` is true (lines 1124-1131, 1274-1277). This path is gated behind `dispatch_direct_pool_enabled` (`401_settings_dispatch_direct_pool_enabled.sql:28`, `DEFAULT FALSE`) and `driver_availability_v2_enabled` (`457_driver_availability_epoch.sql:11`, `DEFAULT FALSE`) — **both default off**, confirmed via migration DDL.
  - **NEW sub-finding: on the default (legacy PostgREST) path, the same stale-row conflict does NOT gracefully skip one driver — it aborts the whole dispatch attempt.** The legacy path's offer-insert (`matching.py:1387-1406`) is a single plain multi-row `.insert(offer_rows)` call with no `ON CONFLICT` clause. If any one claimed candidate already has a `ride_offers` row for this ride (from an earlier decline/expiry that the Redis skip-key missed — the same trigger the RPC's comment describes), the whole insert raises a Postgres unique-violation exception. The `except` block (line 1399-1406) releases **every** driver claimed in that attempt (not just the offending one) and re-raises, which `_dispatch_retry` (line 139) catches and retries with backoff. Net effect: one stale `ride_offers` row for one candidate delays the offer for every OTHER legitimately-claimed driver in that attempt too, on the path that is actually live in production today.
- What happens (plain language): under the default configuration, a Redis outage (or a tight benign timing race around when the skip key is set vs. read) that lets a previously-declined driver back into the candidate pool doesn't just risk re-offering that one driver — it can abort and retry the *entire* offer batch for that dispatch attempt, adding latency for every driver who would otherwise have been correctly offered the ride in that round. This risks the P95<2s dispatch SLA under exactly the failure condition (Redis degraded) most likely to already be stressing other parts of dispatch (presence filtering also fails open per `spinr_dispatch_presence_filter_failed_total`).
- Root cause: the DB-level protection (`ON CONFLICT DO NOTHING`, single-driver-scoped) was only built into the newer batch-claim RPC (migrations 402/403), which itself is dark-flagged off by default; the older/default PostgREST insert path was never given the equivalent per-row-conflict tolerance.
- Recommendation: either (a) add a DB-level pre-filter to the legacy candidate query itself — `NOT IN (SELECT driver_id FROM ride_offers WHERE ride_id = :ride_id)` — so a stale row is excluded before claim rather than discovered at insert time, matching rule #9's intent directly; or (b) change the legacy path's offer-row insert to per-row upsert/`ON CONFLICT DO NOTHING` (mirroring the RPC) so a single stale row degrades gracefully instead of aborting the batch; or (c) accelerate turning on `dispatch_direct_pool_enabled`, which already has the correct behavior, after verifying its own rollout readiness (out of this lane's scope to assess).   Alternative considered: leave Redis as the sole fast-path and accept the residual risk — rejected as the standalone fix specifically because the *legacy* failure mode (whole-batch abort) is worse than a narrow re-offer, and is the one actually live in production.
- Blast radius: `backend/routes/rides/matching.py`'s legacy claim/insert path (`_v3_claimed`/`_direct_pool_enabled` both False branch, lines ~1330-1406) and `_dispatch_retry`. No other reader of the `spinr:offer_skip:*` Redis namespace found.
- Rollout: additive (new WHERE-NOT-IN subquery, or `ON CONFLICT DO NOTHING` on the existing insert) — no migration needed, `ride_offers_ride_driver_uq` already exists (migration 100).
- Verification to close: a test that (1) simulates `redis_mget` raising and asserts a previously-declined driver is excluded from the next legacy-path candidate list via the DB fallback, and (2) asserts that a stale-row conflict on the legacy insert releases only the offending driver, not the whole claimed batch.

### DISPATCH-003 — `is_available` release is read-then-write, not atomic; the atomic replacement exists but is dark-flagged off by default (NEW finding this pass)
- Hierarchy: L2 Dispatch › L3 Driver availability › L4 `is_available ⇒ is_online` invariant maintenance › L5 Legacy (default) availability-write path
- Severity: LOW-MEDIUM   Priority score: 2×2×1 = 4 (narrow window, real invariant risk)
- Status: VERIFIED   Existing item: new — not filed; adjacent to but distinct from the `driver_availability_v2_enabled` rollout already tracked in migrations 457-464.
- Adversary: hostile network/device (driver taps "go offline" at exactly the wrong instant), flaky network
- Evidence: `backend/repositories/driver_repo.py:186-226` (`set_driver_available`). When `available=True`, the function reads `is_online` in one Supabase call (`cur = supabase.table("drivers").select("total_rides, is_online")...`, line 213) and clamps `payload["is_available"] = False` only `if available and not row.get("is_online", False)` (line 217) — then writes `payload` in a separate call. Between the read (line 213) and the write, nothing prevents the driver from toggling `is_online = False` (e.g. via `POST /me/availability`) in that window; the write would then set `is_available = True` on a now-offline driver, violating the invariant CLAUDE.md and rule #5 both require to hold at all times. No DB-level `CHECK` constraint enforces `is_available ⇒ is_online` as a backstop (grepped `backend/migrations/*.sql` for `CHECK.*is_avail` — no hits). **The atomic fix already exists**: migrations 457-464 build `transition_driver_availability()`, an epoch-fenced single-transaction RPC that closes exactly this class of race, but it's gated behind `driver_availability_v2_enabled` (`settings` table, `DEFAULT FALSE`, migration 457 line 11) — confirmed still false-by-default via DDL, no override found in any later migration.
- What happens (plain language): in a narrow (sub-second, two-network-round-trip) window, a driver who taps "go offline" at nearly the same moment their prior offer/ride is being released back to the pool could end up marked `is_available = true` while `is_online = false` — meaning dispatch (which reads `is_available`, never `is_online`, per rule #5) could offer them a ride they've just said they don't want, and the offer would then also fail non-obviously downstream (the driver-app is offline and won't act on it, burning an offer slot and the 15s timeout before self-correcting).
- Root cause: `set_driver_available`'s release path predates the epoch-fenced availability RPC; it was never migrated onto the same transaction-scoped pattern, and the newer pattern that would fix it is still dark.
- Recommendation: either replace the read-then-write in `set_driver_available` with a single atomic UPDATE using a `WHERE` clause that reads `is_online` in the same statement (e.g. `UPDATE drivers SET is_available = (is_online AND $1) WHERE id = $2` via a raw SQL call, avoiding the two-round-trip window entirely) as a low-risk, always-on fix; or treat this as one more reason to accelerate the `driver_availability_v2_enabled` rollout, since it already solves this. A `CHECK (NOT is_available OR is_online)` constraint would also catch and reject any future write that violates the invariant, as defense in depth, regardless of which app-level fix ships.   Alternative considered: leave as-is since the window is sub-second and self-heals on the driver's next heartbeat/presence check — rejected as a standalone answer because rule #5 treats this invariant as a hard non-negotiable, not a best-effort one, and a CHECK constraint costs little.
- Blast radius: `set_driver_available` is called from `release_driver_and_close_period`, `_offer_timeout_handler`, `_legacy_resolve_batch_offers`'s loser release, `cancellation.py`'s driver release paths, and `go_online`/`go_offline` in `routes/drivers/status.py` — i.e., every driver-release call site in the app funnels through this one function, so a fix here is a single-point fix, not a scattered one.
- Rollout: additive if implemented as a raw atomic UPDATE (no schema change); a `CHECK` constraint needs a migration but is non-breaking (existing rows already satisfy it in the steady state).
- Verification to close: a test that concurrently (a) calls `set_driver_available(id, True)` and (b) flips `is_online=False` for the same driver mid-call, asserting the driver never ends up `is_available=True, is_online=False`.

### DISPATCH-004 — Positive control: three previously-hand-mirrored race classes are now single canonical implementations (re-confirmed from rapid baseline, additional evidence)
- Severity: PASS (informational)
- Status: VERIFIED
- Evidence: `backend/routes/drivers/ride_flow.py:395-436` (driver-online re-check + own-duplicate-vs-genuine-race distinction at accept time — rule #10); `backend/utils/insurance_periods.py:272-410` (`release_driver_and_close_period`, single implementation replacing 5 hand-mirrored copies, explicitly citing the CLAUDE.md 2026-09-12 gate); `backend/routes/drivers/status.py` (go-online / dispatch-claim TOCTOU closed, post-write claim re-check, per rapid baseline — not independently re-read this pass, carried forward as VERIFIED per rapid baseline's own direct citation).
- Why flagged again: a future audit should not re-flag this pattern as new without first reading these sites; re-confirmed with a fresh, independent code read in this pass (not a copy of the prior finding) at `insurance_periods.py` and `ride_flow.py`.


---

## 4. `rides.status` write-site table (rule #8: guard / WS emit / metric / insurance-period call)

Every production-reachable write site found by grepping `RideStatus\.` / `"status":` in
`backend/routes/rides/`, `backend/routes/drivers/`, `backend/routes/admin/rides.py`,
`backend/utils/scheduled_rides.py`. Metric column only expects an entry for the 4
values CLAUDE.md's own metric spec covers (`driver_arrived|in_progress|completed|
cancelled`) — absence of a metric on `driver_assigned`/`driver_accepted`/`searching`
transitions is by design (covered by `spinr_dispatch_offer_*` counters instead), not a
gap.

| Transition | Site | Guard | WS emit (rider) | WS emit (driver) | Metric | Insurance period |
|---|---|---|---|---|---|---|
| `searching`→`driver_assigned` (offer sent) | `matching.py` claim (v3/direct/legacy, ~1090-1406) | atomic CAS / RPC (3 variants, see DISPATCH-002) | **missing** (DISPATCH-001) | yes (`new_ride_assignment`, :1621) | `spinr_dispatch_offer_sent_total` (:1435) | Period 2 written at claim (:1433, or in-RPC for v3/direct) |
| `driver_assigned`→`driver_accepted` (accept) | `ride_flow.py:398-409` (legacy) / v2 RPC | atomic CAS `{status,driver_id}` / RPC | yes (`driver_accepted`, :608) | n/a (self-triggered, by design) | `spinr_dispatch_offer_accepted_total` (:493) | Period 2 no-op safety net (:476) |
| `driver_assigned`→`searching` (offer timeout) | `matching.py:1794-1805` | atomic CAS `{status,driver_id}` | yes (`driver_timeout`, :1868) | yes (`ride_offer_expired`/`auto_offline`, :1878-1903) | n/a (not in the 4-value spec) | `release_driver_and_close_period` (:1858) or Period 0 if auto-offlined (:1852) |
| two drivers race accept | `ride_flow.py:398` (0 rows) | CAS (same as above) | n/a | yes (`ride_taken`, legacy :555 / v2 `driver_offer_service.py:218`) | n/a | released via `release_driver_and_close_period` (`_release_loser`, :550) |
| `driver_accepted`→`driver_arrived` | `ride_flow.py:1075-1098` | `_require_ride_in_state` (driver-owned) | yes (`broadcast_ride_status`, :1128) | n/a (self-triggered) | `spinr_rides_state_transition_total{to_status=driver_arrived}` (:1098) | n/a (still Period 2) |
| `driver_arrived`→`in_progress` (verify-otp, production) | `ride_flow.py:1220-1240` | atomic CAS `{id,driver_id,status}` | yes (`ride_started` + `broadcast_ride_status`, :1234-1252) | n/a (self-triggered, documented) | `spinr_rides_state_transition_total{to_status=in_progress}` (:1240) | Period 3 (:1239) |
| `driver_arrived`→`in_progress` (`/start`, dev-only, 410 in prod) | `ride_flow.py:1295-1327` | atomic CAS (same shape) | yes | n/a | **missing** (not gated as production-unreachable in code the way `/start`'s 410 makes it look — see NOT VERIFIED) | Period 3 (:1314) |
| `in_progress`→`completed` | `ride_complete.py:559,726` | not independently re-read this pass (carried from rapid baseline: guard present) | yes (`ride_completed`, per rapid baseline :926/:978) | not confirmed to driver's own WS key (push only) — **carried forward as NOT VERIFIED**, same as rapid baseline | `spinr_rides_state_transition_total{to_status=completed}` (:726) | closes Period 3 (not re-read this pass) |
| any pre-trip active→`cancelled` (rider) | `cancellation.py:76-106` | atomic CAS scoped to active statuses (:76-95) | yes | yes (per rapid baseline, not re-verified per-branch this pass) | `spinr_rides_state_transition_total{to_status=cancelled}` (:106) | `release_driver_and_close_period` (assumed, not re-read this specific line) |
| any pre-trip active→`cancelled` (driver/system, alt path) | `cancellation.py:997-1043` | atomic CAS | yes | yes | `spinr_rides_state_transition_total{to_status=cancelled}` (:1031) | not re-read |
| `searching`→`cancelled` (auto, 5min no drivers) | `matching.py:2230-2252` | atomic CAS `{status:searching}`, single-attempt write policy | yes (`ride_cancelled`, `is_auto:true`, :2335) | n/a (no driver assigned) | `spinr_rides_state_transition_total{to_status=cancelled}` (:2328) | n/a |
| `scheduled`→`searching` | `scheduled_rides.py:448-463` (claim), :616 (WS) | atomic CAS `{status:scheduled}` | yes (`broadcast_ride_status`, :616) — **closes rapid-baseline gap** | n/a (no driver yet) | n/a (not in the 4-value spec) | n/a |
| admin cancel/complete | `admin/rides.py:654-656,868-870` | atomic CAS `{id,status:status_from}` | per rapid baseline, not re-verified this pass | per rapid baseline, not re-verified this pass | not re-verified this pass | `record_period_transition` present at :1371 for admin direct-assignment (Period 2), completion path not re-read |
| v2/v3 batch-offer accept/decline/expire/preempt | `driver_offer_service.py` + `resolve_driver_offer` RPC (migration 460+) | single-transaction RPC, idempotent via `request_id` (never repeats a side effect on replay — `_won()` checks `not result.get("replayed")`) | via `_notify_driver`/`broadcast_ride_status` call sites in the service | via `_notify_driver` (`ride_taken`, `ride_offer_expired`, `availability_changed`, `auto_offline`) | `spinr_dispatch_offer_accepted_total` (:147), `spinr_dispatch_offer_terminal_total{outcome}` (:234) | delegated to the RPC (not re-read line-by-line this pass) |

**Status-value sweep result (rule #8 "any status value outside the allowed set"):**
Grepped `ride.status`/`ride_status`/`.get("status")` comparisons against string
literals across `backend/` (all `*.py`, excluding `backend/tests/`) and did not find
any comparison of a *ride's* `status` column to a value outside
`{scheduled, searching, driver_assigned, driver_accepted, driver_arrived, in_progress,
completed, cancelled}`. Values like `pending`/`accepted`/`declined`/`expired`/
`preempted` that appear near ride code all belong to the **separate** `ride_offers.status`
column (a different, RPC-managed enum — see migration 460's `ride_offers_outcome_check`
CHECK constraint for its own closed value set), not `rides.status`. One test file
(`backend/tests/test_b_p0_2.py`) documents a **historical, already-fixed** bug where a
prior version of the code compared against the literal `"trip_in_progress"` instead of
`"in_progress"` — kept as a regression test, not a live bug. `backend/models/
ride_status.py`'s `RideStatus` enum is the single canonical definition and nothing
found subclasses or extends it with additional values. **Not exhaustively swept**:
`rider-app/`, `driver-app/`, `admin-dashboard/` TypeScript status literals were sampled
via grep (58 distinct quoted string matches near `status` comparisons, dominated by
unrelated domains — subscription/document/payment status) but not individually traced
back to confirm each ride-status literal on the frontend matches the backend enum
1:1 — see NOT VERIFIED.


---

## 5. Scenario cards — sweep-catalog §3.1 (Booking & matching)

| # | Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status | Dispute risk |
|---|---|---|---|---|---|---|---|
| 1 | Double-tap "Request" → two rides/holds? | rider | rapid double submit | one ride created, one hold | `@idempotent_endpoint(scope="ride_create")` (`booking.py:382`) + client `Idempotency-Key` header + DB unique index `idx_rides_rider_idempotency_key` (`booking.py:1662-1672`); a concurrent duplicate loses the DB race and the code re-fetches and returns the original row (`_insert_ride_with_code`, `idempotent_reuse=True`, :1335-1337) rather than erroring | **Handled** | Low |
| 2 | Rider books while a previous ride is active | rider | book during active ride | reject with clear reason | `rides_one_active_per_rider` partial unique index (migration 53) over the active-status set; violation surfaces as a specific caught exception (`"rides_one_active_per_rider" in msg`, :1660) rather than a raw 500 | **Handled** | Low |
| 3 | No drivers for 5 min → auto-cancel, hold released, rider told why | system | search timeout | auto-cancel + refund/release hold + explain | `matching.py:2177-2252` (`ride_search_timeout`), atomic CAS `{status:searching}`→`cancelled`, `is_auto:true` in the `ride_cancelled` WS payload (:2335) so the client can render an explanation rather than a generic cancellation. Hold-release: not re-traced to the Stripe auth-release call in this pass — **NOT VERIFIED** whether the pre-authorized card hold (placed at booking or scheduled-dispatch time per `scheduled_rides.py`) is compensated in the same code path or a separate payment-domain job. | **Partial** (state machine confirmed; payment-hold compensation not traced — flag for R9) | Medium if hold isn't released promptly |
| 4 | Driver accepts at the same instant rider cancels | rider+driver | race | exactly one outcome wins, no stranded state | Both accept (`ride_flow.py:398-436`) and cancel (`cancellation.py:76-95`) paths use atomic CAS filters scoped to the current status; whichever write lands first wins the DB race, the other's CAS matches 0 rows and takes its own "lost the race" branch (re-read + appropriate response) rather than silently overwriting. Not independently re-tested under actual concurrency in this pass (code-level read only). | **Handled** (by code inspection; no concurrency test cited) | Low |
| 5 | Two drivers accept the same ride | 2× driver | race | 409 + `ride_taken` to loser | CAS filter `{status:'driver_assigned', driver_id:<offered driver>}` (legacy) or epoch-fenced RPC (v2); loser gets `SpinrException(RESOURCE_CONFLICT, 409, RIDE_TAKEN)` + `ride_taken` WS event (`ride_flow.py:550-559` legacy, `driver_offer_service.py:218` v2) | **Handled** | Low |
| 6 | Offer timeout while phone backgrounded/locked | driver | offer expires unacknowledged | server-authoritative timeout still fires | `_offer_timeout_handler` sleeps 30s server-side (15s client + 15s grace), independent of client countdown — "fires even if the device crashes or loses network" (code comment, `matching.py:1750-1754`) | **Handled** | Low |
| 7 | Pickup pin wrong side of divided road / airport / gated community | rider | pin placement | driver nav guidance / road-snap | `pickup_nav_lat`/`pickup_nav_lng` fields exist and are sent in the offer payload as a distinct "road-snapped pickup for driver navigation" (`matching.py:1563-1565`) — confirms the mechanism exists; accuracy/coverage of the snapping itself (e.g. airport/gated-community specific handling) not traced — **UNKNOWN**, out of this lane's grep depth | **Partial** | Medium (industry-wide problem) |
| 8 | Scheduled ride: driver cancels 10 min before; surge active at dispatch but not at booking | rider | timing mismatch | rider not silently re-priced; re-dispatch attempted | Re-dispatch: `_dispatch_scheduled_ride` re-runs `match_driver_to_ride` after transitioning to `searching` (:663), so a pre-dispatch driver cancellation is a non-issue (driver isn't assigned until match succeeds). Surge-at-dispatch-vs-booking: CLAUDE.md states "never apply surge to scheduled rides booked outside the surge window" as a rule but this pass did not trace `estimates.py`/`fare_service.py` surge-lock-in logic for scheduled rides — **NOT VERIFIED**, R9's domain | **Partial** | Medium |
| 9 | Price changes between quote and confirm | rider | route re-price | disclosed before charge | Out of this lane's scope — CLAUDE.md documents this as an **accepted, permanent SLA exception** (the 3.5s Directions-wait fix after the 12.12→16.46km incident) — not re-audited here, carried forward as a documented decision | **Handled** (documented exception, R9-owned) | Low (already fixed + documented) |
| 10 | Rider books for someone else (guest) | rider | guest booking | correct party gets notifications/receipt | `ride.get("guest_booking")` branch exists in the accept-notify path (`ride_flow.py:628-630`, "Corporate guest customer (no app): driver + vehicle + pickup OTP + live tracking link by SMS") — confirms a guest-booking notification path exists distinct from the normal in-app WS/push flow | **Handled** (mechanism confirmed; full guest journey not traced end-to-end) | Low-Medium |
| 11 | WAV/service-animal request, no eligible driver online | rider | accessibility need | clear "no WAV driver available" messaging, not silent no-match | `requires_wav`/`service_animal` booleans are read into the offer payload (`matching.py:1574-1575`) confirming candidate filtering exists, but this lane did not trace the candidate-ranking code far enough to confirm what message the rider receives specifically when zero WAV-capable drivers are online vs. the generic 5-min auto-cancel — **NOT VERIFIED**; flagged per greenfield-extensions §12 "no agent owns Maps & Routing" gap (WAV dispatch is gated by `service_areas.py`/`h3_heatmap.py`, an admittedly unowned surface per W0-SUMMARY) | **Partial / UNKNOWN** | High (accessibility law + regulatory-sk.md WAV requirement) |


## 5b. Scenario cards — sweep-catalog §3.2 (En route & pickup)

| # | Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status | Dispute risk |
|---|---|---|---|---|---|---|---|
| 12 | Driver GPS frozen/drifting/teleporting | driver | GPS fault | stale-location detection, client shows a warning | CLAUDE.md's own dispatch pitfalls list ("don't count a driver as available if last heartbeat > 90s old") implies a freshness check exists; this lane did not trace `routes/drivers/location.py`'s staleness logic directly — **NOT VERIFIED**, `location.py` is R13/observability-adjacent territory more than dispatch-state-machine territory | **UNKNOWN** (out of this lane's direct-read depth) | Medium |
| 13 | Driver going wrong way; rider wants to cancel — who pays? | rider | driver navigation error | fault-based fee waiver | `cancellation.py` has a `_was_scheduled`-style fault-tracking pattern (per rapid baseline/comment cross-refs) but this lane did not trace the specific "wrong-way driver → fee waiver" business logic branch — **NOT VERIFIED** | **UNKNOWN** | High (fee dispute) |
| 14 | Rider no-show: wait timer, fee, evidence | driver | rider absent at pickup | configurable wait window, fee, location evidence | `routes/drivers/ride_cancel.py:491-559` (`mark_rider_noshow`) — configurable `noshow_wait_seconds` (service-area override or `app_settings` default 300s), atomic CAS claim (`driver_arrived → cancelled`) placed **before** side effects specifically to prevent a race where the ride transitions out of `driver_arrived` mid-fee-collection producing a double charge (comment at :553-559), idempotent no-show call | **Handled** | Low (well-guarded) |
| 15 | Wrong rider gets in (verification PIN?) | driver/rider | mistaken pickup | PIN verification before trip starts | Production `driver_arrived → in_progress` is **exclusively** gated by `POST /rides/{id}/verify-otp` (`ride_flow.py:1164`); the no-OTP `/start` endpoint returns HTTP 410 in production (`ENV.lower() == "production"` check, :1275-1278). Brute-force lockout on the OTP itself (`check_pickup_otp_lockout`, `_PICKUP_OTP_MAX_FAILURES`) prevents a driver from guessing the 4-digit code | **Handled** | Low |
| 16 | Rider changes pickup after acceptance | rider | mid-offer pickup edit | re-route driver, re-disclose ETA/fare if it changes materially | Not traced in this pass — no `update_pickup`/similar endpoint found via the greps run in this lane; **UNKNOWN**, likely R4 (rider journey) territory more than dispatch state-machine | **UNKNOWN** | Medium |
| 17 | Rider or driver phone dies before pickup | either | device failure | offer/ride doesn't silently strand | Covered by scenario #6's server-authoritative 30s offer timeout (driver side) and by the no-show mechanism (#14, rider side, once driver has arrived); the gap is specifically pre-arrival rider-phone-death with no no-show timer yet running — not separately traced | **Partial** (post-arrival case Handled via #14; pre-arrival rider-side case UNKNOWN) | Medium |

## 5c. Scenario cards — sweep-catalog §3.3 (In trip & completion)

| # | Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status | Dispute risk |
|---|---|---|---|---|---|---|---|
| 18 | Phone dies/app killed/no connectivity mid-trip (rural SK) | either | connectivity loss | trip completes on last-known distance, reconciled later | `backend/routes/websocket.py:960-968`'s sequence-numbered outbox/replay mechanism (rapid-baseline DISPATCH steelman item, re-cited not re-read this pass) addresses reconnect-and-resync, but the specific "driver never reconnects, who completes the trip and on what distance" recovery path is the **stuck-ride sweeper**'s job (`utils/stuck_ride_sweeper.py`, confirmed atomic-claim-safe by rapid baseline) — this pass did not trace what distance/fare basis the sweeper uses to close an abandoned `in_progress` ride | **Partial** (safety-net mechanism exists per stuck-ride sweeper; fare/distance basis on forced-close NOT VERIFIED this pass) | High (rural connectivity is a named CLAUDE.md/greenfield-extensions gap, §12) |
| 19 | Driver forgets to end the trip; rider already out | driver | forgotten completion | auto-detect via GPS/timeout, prompt or auto-complete | Not traced this pass — likely overlaps with the stuck-ride sweeper (#18) but no distinct "driver forgot" detection (e.g. GPS stationary at destination for N minutes) found via the greps run in this lane | **UNKNOWN** | Medium |
| 20 | Driver ends trip early/far from destination | driver | premature completion | fare adjustment / flag for review | Not traced this pass; `ride_complete.py` (726 lines not deeply read beyond the guard/metric/WS grep) may contain distance-at-completion validation — **UNKNOWN**, flag for a deeper `ride_complete.py` read in a follow-up pass | **UNKNOWN** | High (fare dispute) |
| 21 | Route deviation / long-hauling; safety alert vs fare adjustment | driver | off-route driving | alert without over-triggering; distinguish safety concern from fare inflation | `backend/utils/route_deviation_alerter.py` exists, runs every 30s, flag-gated (`route_deviation_alert_enabled`, `app_settings`), uses Redis `SET NX` claim keys (`first_seen`/`escalated`) — CLAUDE.md's own Background-task-safety section already flags this loop's `SET NX` claims as unguarded (fails per-tick on a Redis error, not fail-open) — confirms the mechanism is real and its failure mode is understood/documented, not re-verified line-by-line this pass | **Handled** (mechanism confirmed; fare-adjustment-vs-safety-alert distinction not traced) | Medium |
| 22 | Stops added/removed mid-trip; re-pricing disclosure | rider | mid-trip stop edit | disclosed re-price before/at completion, not silently at receipt | `ride.get("stops")` is read into the offer payload at dispatch time (`matching.py:1576`), confirming stops exist as a first-class concept, but mid-trip stop mutation + its re-pricing disclosure was not traced in this pass — **UNKNOWN**, R9 (fare) territory | **UNKNOWN** | High |
| 23 | SOS pressed; false SOS; SOS with no data connection | rider/driver | emergency | reliable trigger even under poor connectivity, false-positive handling | Out of this lane's scope — R11 (Trust/Safety) owns SOS end-to-end; `.claude/context/domain-safety.md` exists and was not loaded by this lane (not required by the R8 role card) | **Out of scope for R8** | N/A here |
| 24 | Vehicle breakdown/collision mid-trip — insurance Period 3 evidence | driver | collision | Period 3 row remains intact, incident linked | Insurance-period rows are append-only by design (`release_driver_and_close_period`/`record_period_transition` insert-and-close-prior, no in-place UPDATE of `period` found) — a collision mid-trip doesn't itself need a *dispatch*-side state change beyond however the ride eventually completes/cancels, which is guarded per the write-site table above | **Handled** (structurally, via append-only Period 3 log) | Medium (R11/R12 own the actual FNOL workflow — see greenfield-extensions §12) |
| 25-27 | Cleaning fee evidence, minor riding unaccompanied, extreme cold pickup tolerance | rider/driver | various | evidence standard, age verification, winter ops mode | Out of this lane's direct scope (R7/R4/R5/R17 territory per sweep-catalog's own lane assignment for most of §3.3's tail) — not traced | **Out of scope for R8** | N/A here |


---

## 6. Rebuild Delta card — Epic: Ride Booking & Matching / Ride Fulfillment (state machine)

Testing R19's hypothesis: "one ride state-machine module with an append-only event log."

- **Verdict per inherited pattern**: MODIFY (not REPLACE). The current pattern — 2
  canonical Python guard functions (`_require_ride_in_state`,
  `_require_ride_in_state_rider`) plus a consistent atomic-CAS-filter convention at
  every other multi-actor write site — is **VERIFIED working correctly today** (rule
  #1 verdict: no bare-write violation found across ~13 write sites). A from-scratch
  rewrite of a correctly-functioning, live-tested state machine is the wrong move per
  CLAUDE.md's tie-breaker ("incremental on the live product over rewrite"). The
  evidence for consolidating is about **consistency of side effects** (WS/metric/
  insurance-period calls bundled with the status write), not about the CAS
  correctness itself.
- **Keep (already best-in-class)**: the atomic-CAS-per-write-site discipline itself;
  the append-only insurance-period log (already exists, already correctly append-only
  per `insurance_periods.py`); the idempotent-RPC pattern (`request_id`-keyed replay
  safety in `resolve_driver_offer`, "can this run twice?" already answered "yes,
  safely" for the v2/v3 offer domain).
- **Uber/Lyft do**: both run marketplace dispatch as an internally event-sourced state
  machine (public engineering blog material, not independently verified against a
  primary source in this pass — ASSUMED, flagged per ground rule on regulatory/
  competitive claims needing citation) where every transition emits a durable event
  that downstream consumers (rider app, driver app, ops dashboards, analytics) fan out
  from, rather than each consumer polling/deriving state independently.
  **Spinr today**: functionally similar in spirit (CAS write → immediate WS fan-out
  via `broadcast_ride_status`/`send_personal_message` in the same request/task), but
  the "event" is implicit (a status column value plus whatever side effects that call
  site remembered to also do) rather than an explicit, replayable row. This is exactly
  why DISPATCH-001 (a forgotten rider WS emit) was possible: nothing enforces that
  every `rides.status` write also produces every required side effect — it's
  convention + review, not a structural guarantee.
- **Clean-sheet Spinr would**: introduce a single `transition_ride_status(ride_id,
  expected_status, to_status, *, actor, reason, ...)` helper that wraps (a) the atomic
  CAS write, (b) an INSERT into a new append-only `ride_status_events` table (mirrors
  the existing `driver_insurance_periods` append-only pattern this codebase already
  trusts), (c) the `broadcast_ride_status` call to both rider and driver keys, and (d)
  the `spinr_rides_state_transition_total` metric increment — as one call, so a future
  write site cannot add a transition without also getting the event log row and the
  WS emit for free. Insurance-period calls stay a separate, explicit call (they're not
  1:1 with every ride-status transition — e.g. Period 2 opens at *offer*, not at
  `driver_assigned` write time, per CLAUDE.md's own documented nuance) rather than
  folded into the same helper, to avoid conflating two different domains that happen
  to share a trigger.
- **Why (the edge it creates)**: closes the exact class of bug DISPATCH-001 is (a
  transition that silently drops one required side effect) structurally rather than
  by review discipline; gives the regulator/auditor persona a queryable, replayable
  event log for "what happened to this ride and when" without reconstructing it from
  scattered WS-log lines; makes the write-site table in §4 of this report
  self-verifying (a missing WS emit becomes a query: "transitions with no
  corresponding `ride_status_events` row that also has a WS ack" instead of a manual
  grep-and-read audit like this one).
- **How (architecture/pattern)**: mirror the driver-availability v2 rollout's own
  playbook almost exactly — it already proved this shape works for a adjacent,
  harder domain (epoch-fenced, idempotent, dark-flagged). Concretely: (1) additive
  migration for `ride_status_events` (append-only, no FK cascade risk); (2) new Python
  helper function, initially called *alongside* existing call sites in shadow mode
  (dual-write, compare, no behavior change) — this codebase already has a proven
  precedent for exactly this de-risking technique (`matching.py`'s `_shadow_ids`/
  `_shadow_v2_ids`/`spinr_dispatch_admission_shadow_total` comparison for the v2
  admission-filter rollout, lines ~630-649); (3) once shadow-mode shows 1:1 parity for
  N days, migrate call sites to route status writes *through* the helper one at a time
  (CLAUDE.md's ≤3-files-per-subtask rule applies directly — there are ~13 write
  sites, so ~13 small PRs, not one big-bang migration); (4) only after all sites are
  migrated, consider whether the CAS itself should move into a Postgres RPC (closing
  DISPATCH-002's legacy-path batch-abort sub-finding as a side benefit, since an RPC
  can do per-row conflict tolerance the way migration 402's batch-claim RPC already
  does).
- **Who**: R8/dispatch-owning engineer + a `spinr-realtime-reliability-reviewer` pass
  per subtask (per CLAUDE.md gate 10's mandatory adversarial-review-before-commit).
- **When**: Now/Next for the additive event-log table + shadow-mode helper (low risk,
  no behavior change); Next/Later for migrating all ~13 call sites through it
  (medium effort, must be surgical per CLAUDE.md); the RPC-ification of the CAS itself
  is Later — it's a bigger architectural change and the current CAS-per-site pattern
  is not itself broken (rule #1 verdict), so there's no urgency independent of
  DISPATCH-002's narrower, already-actionable fix.
- **Incremental path from today (no big-bang)**: step 1 — additive
  `ride_status_events` migration, no code changes yet, verify: table exists, no writes
  yet. step 2 — build `transition_ride_status()` helper, call it in shadow mode from
  ONE low-traffic transition first (e.g. `scheduled→searching`, already the newest/
  best-documented call site), verify: shadow rows match legacy behavior for N days,
  zero behavior change. step 3 — migrate remaining ~12 call sites one per PR, each
  with its own before/after Change Impact Log entry per CLAUDE.md, verify: each
  migrated site's existing tests still pass plus a new event-log assertion. step 4 —
  once all sites route through the helper, evaluate RPC-ification as a separate,
  later decision.
- **Cost/effort**: M (spread across ~13 small, individually low-risk PRs over
  multiple sprints, not a single large change). **Risk**: Low if shadow-mode-first is
  followed (matches the codebase's own precedent); Medium if any step skips the
  shadow-mode parity check. **Reversibility**: High — dark-flagged per the
  `driver_availability_v2_enabled` precedent, and each site migrates independently so
  a bad migration is a single-site revert, not a system-wide one. **Build**, not
  buy/partner — this is domain-specific state-machine logic tightly coupled to
  `RideStatus`, insurance periods, and the existing WS fan-out; no third-party
  ride-dispatch state-machine product would fit without a bigger rewrite than the
  problem justifies.
- **Advantage type**: operational (fewer missed-side-effect bugs, faster incident
  triage via a queryable event log) and trust (regulator/auditor can be shown a
  literal audit trail). Not a rider/driver-facing competitive differentiator on its
  own — Uber/Lyft riders don't see this layer — so it should not be marketed, but it
  reduces the exact class of bug (DISPATCH-001) that erodes trust when found by an
  auditor or a plaintiff's lawyer instead of a dev.
- **"Why not?"**: could a simpler fix get 80% of the value? Yes, partially —
  DISPATCH-001 alone can be closed today by adding one WS send at one call site
  without building the whole event-log system (see DISPATCH-001's own recommendation).
  The event-log/single-helper investment is justified specifically because this audit
  found the pattern (missed side effect at one call site among many) is a *class* of
  risk, not a one-off — the same review discipline that lets a state machine be this
  well-guarded today (rule #1's clean verdict) can still miss one WS emit among ~13
  call sites, and did.


---

## 7. Top 5 findings

1. **DISPATCH-002 (deepened)** — the live default (legacy PostgREST) dispatch path
   aborts the *entire* offer batch on a single stale `ride_offers` row conflict,
   rather than gracefully skipping the one offending driver the way the dark-flagged
   batch/v3 RPC path already does. This is a real P95-SLA and re-offer risk under the
   exact Redis-degraded conditions rule #9 exists to guard against, and it's worse
   than the rapid baseline characterized it (a single-driver correctness gap vs. a
   whole-attempt availability gap). Actionable today, additive fix, no migration
   needed (constraint already exists).
2. **DISPATCH-001** — no rider-facing WS event at `searching→driver_assigned`, despite
   `domain-dispatch.md`'s own documented contract. Re-verified by direct grep, not
   just re-cited. Low technical risk, real doc/code drift a regulator/auditor persona
   would flag.
3. **DISPATCH-003 (new)** — `set_driver_available`'s read-then-write is not atomic,
   creating a narrow but real window where `is_available=True, is_online=False` can
   occur, violating rule #5's invariant. The atomic fix already exists in the
   codebase (migrations 457-464) but is dark-flagged off by default — this is a case
   where accelerating an existing rollout (or a small always-on atomic-UPDATE fix)
   closes a real gap cheaply.
4. **Positive finding**: the 4 dispatch-relevant items of the HIST "7 non-atomic CAS"
   recurrence family (C54, C56, C66, C133) are all closed with dated fixes and named
   regression tests — this domain is not where that recurrence family's residual risk
   lives (it's now concentrated in the 3 payments-domain items: B19, C77, C104,
   R9's territory).
5. **Coverage gap, not a defect**: several §3.2/§3.3 edge cases (driver GPS
   freeze/drift, wrong-way-driver fee dispute, rider changes pickup after acceptance,
   driver forgets to end trip, driver ends trip early/far, mid-trip stop re-pricing)
   were not traceable within this lane's grep-driven pass and are marked UNKNOWN
   rather than assumed handled — each is a candidate for a follow-up direct read of
   `ride_complete.py` (726 lines, only ~15% read this pass) and `location.py`.

## 8. NOT verified

- `ride_complete.py`'s full `in_progress → completed` guard/WS/insurance-close logic
  — only the guard-existence, metric line, and rapid-baseline's prior WS citation were
  confirmed; the file's 726 lines were not read beyond targeted grep hits.
- Whether `driver_arrived → in_progress` via the dev-only `/start` endpoint (410 in
  production) increments `spinr_rides_state_transition_total` — grep found no
  `_metric_inc` call in that specific function; not confirmed whether this is
  intentional (dev-path, no prod metric needed) or an oversight, since the endpoint
  does still write to the same `rides` table a real test/staging environment would
  query.
- Admin cancel/complete (`admin/rides.py:654-656,868-870`) WS-emit and metric coverage
  — carried forward from rapid baseline as unverified in this pass too; not
  independently re-read.
- Full body-read of the ~37 non-dispatch-critical background loops (surge, corporate,
  payments, safety, statements, etc.) — same limitation the rapid baseline flagged
  (DISPATCH-003 in that document); this pass added no new loop-body reads beyond what
  the rapid baseline already covered (`scheduled_dispatcher`, `driver_claim_reaper`,
  `stuck_ride_sweeper`, `offer_expiry_reaper`, `driver_readiness_reconciler` — all 5
  confirmed atomic-claim-based).
- `insurance_period_reconciler`, `stale_p3_closer`, `route_deviation_alerter`'s full
  internal logic (only its flag/Redis-key naming was read, not its full escalation
  logic), `safety_checkin`, `capacity_watchdog` — placement confirmed via registry
  citation, internal claim logic not directly read in this pass either.
- Rider-app / driver-app / admin-dashboard TypeScript status-literal sweep — sampled,
  not exhaustively traced 1:1 against `backend/models/ride_status.py`'s enum.
- Payment-hold compensation on the 5-minute auto-cancel path (scenario #3) — state
  machine confirmed, Stripe-hold-release call site not traced (R9 territory).
- Scheduled-ride surge-lock-in-at-booking-not-dispatch-time behavior (scenario #8) —
  not traced (R9 territory, though it's dispatch-adjacent via `scheduled_rides.py`).
- WAV/service-animal candidate filtering's actual behavior when zero eligible drivers
  are online (scenario #11) — payload fields confirmed to exist, the candidate-filter
  logic itself and the rider-facing message on a WAV-specific no-match were not
  traced this pass.
- Whether the `set_driver_available` read-then-write race (DISPATCH-003) has ever
  actually fired in production — no incident evidence sought or found; flagged
  purely from code inspection, consistent with "found by review, not by a real
  incident" framing this codebase uses elsewhere (see C133's own "why not urgent"
  section for the same honest framing this report is borrowing the convention from).

## 9. Open questions only a human can answer

- Is DISPATCH-001 (no rider WS event at offer-sent time) a deliberate product
  decision (avoid showing a driver who might still decline) or an unnoticed gap? This
  determines whether the fix is a WS send or a doc correction — genuinely open, not
  answerable from code alone (carried forward from rapid baseline, still unresolved).
- What is the actual production rollout plan/timeline for `driver_availability_v2_enabled`
  and `dispatch_direct_pool_enabled` (both still `DEFAULT FALSE` as of this audit)?
  Both flags already contain fixes for real gaps this audit found (DISPATCH-002's
  worst sub-case, DISPATCH-003's race) — is there a blocker to turning them on, or is
  this simply not yet scheduled? Only the owning engineer/team can answer whether
  accelerating this rollout is safe given whatever validation is still pending.
- Is the ~13-call-site "single state-machine module + append-only event log"
  investment (§6) actually wanted, or is the current CAS-per-site + code-review
  discipline considered sufficient given it has a clean rule-#1 verdict today? This
  is a genuine cost/benefit call for whoever owns the dispatch roadmap, not something
  this audit can decide unilaterally.
- For scenario #11 (WAV/service-animal, no eligible driver): what is the actual
  intended rider-facing message today, and is it distinguishable from a generic
  no-match? Regulatory-sk.md treats WAV support as mandatory when a WAV driver is
  online in the service area — worth a direct product/ops confirmation given this
  lane could not verify it from code alone in the time available.

## 10. Escalations

- **DISPATCH-002 (deepened)** should be escalated to whoever owns the
  `dispatch_direct_pool_enabled`/`driver_availability_v2_enabled` rollout — the fix
  for the *worse* (legacy, default-path) failure mode found in this pass is small and
  additive (§3's recommendation (a)/(b)), and does not require waiting on the larger
  flag rollout to ship, but the fact that the flagged-off path already has better
  behavior than the live default path is worth flagging to that rollout's owner
  directly, in case it changes their prioritization.
- **DISPATCH-003** (is_available/is_online TOCTOU) is low-severity but touches a
  CLAUDE.md non-negotiable invariant (rule #5) with a narrow, real production window —
  escalate to confirm whether a lightweight always-on atomic-UPDATE fix is preferred
  over waiting on the larger v2 rollout, since the two are independent decisions.
- No CRITICAL or blocker-class finding in this pass requires an immediate escalation
  outside the normal backlog process — consistent with the rapid baseline's own
  verdict that the core state-machine, race-guard, and replay-safety mechanisms
  actually inspected are mature.

---

## Audit metadata

- Sections above are the complete brief: Steelman, Attack, Finding cards
  (DISPATCH-001..004), write-site table, Scenario cards §3.1/§3.2/§3.3, Rebuild Delta
  card, Top 5, NOT verified, Open questions, Escalations.
- Time-boxed within the 60-120 minute budget; prioritized re-verifying and deepening
  the rapid baseline's DISPATCH-001/002 findings (both re-confirmed, DISPATCH-002
  substantially deepened with a new sub-finding) and closing its explicitly-flagged
  UNKNOWNs (scheduled→searching WS emit: closed, confirmed Handled;
  driver_arrived→in_progress driver-self-WS: closed, confirmed deliberate) over
  breadth across all 30 sweep-catalog §3.1-3.3 items — several §3.2/§3.3 items
  outside dispatch's core state-machine concern (GPS drift, stop editing, cleaning
  fees) are marked UNKNOWN rather than guessed at.

VERDICT: **FIX BLOCKERS-LITE / NEEDS DISPATCH-TEAM REVIEW** — no CRITICAL/blocker-class
defect found (the state machine, race guards, and replay-safety mechanisms actually
inspected are mature, consistent with the rapid baseline). Two MEDIUM-class,
actionable, additive fixes (DISPATCH-001, DISPATCH-002) and one LOW-MEDIUM
invariant-hardening item (DISPATCH-003) should go on the backlog; DISPATCH-002's
deepened default-path finding is the one worth prioritizing soonest given it's a live,
default-path SLA/availability risk rather than a flagged-off edge case.
