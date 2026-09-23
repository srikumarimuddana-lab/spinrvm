# Spinr Driver Availability Implementation Plan

> **For agentic workers:** Implement task by task using `superpowers:subagent-driven-development` or `superpowers:executing-plans`. Luna workers can own bounded tasks; the architect owns contracts, dependency order, and review of auth, dispatch, and database races.

**Goal:** Prevent unattended drivers receiving new offers, recover cleanly from backgrounding and token renewal, and give drivers an accurate, actionable availability state.

**Architecture:** Retain FastAPI, Postgres, Redis and the native mobile tracking pipeline. Make Postgres authoritative for driver intent, the controlling session, offer ownership and transitions; use Redis for short reachability leases. Add a server-enforced readiness deadline and a versioned mobile reconciliation contract.

**Tech stack:** Python/FastAPI, Supabase/Postgres, Redis, React Native/Expo, TypeScript, native background location, WebSockets and FCM/APNs.

**Spec:** Sections 1–7 of this document are the design specification; sections 8–11 are its implementation and verification plan.

**Evidence date:** 23 September 2026. Code reviewed at `4fcb9d7703b96bc65b779e73169f8b583ce421c5` in `srikumarimuddana-lab/spinrvm`. Three GPT-6 Luna workers reviewed backend, mobile, and authentication; the architect cross-checked findings, corrected stale-document interpretations, inspected live schema/settings read-only, and selected the design.

**Status:** Analysis and plan only. No product code, migrations, settings, deployment, or driver records were changed. No incident-specific device trace or authenticated driver identifier was supplied.

## Global constraints

- Keep `is_online` and `is_available` meanings compatible during rollout; `is_available` implies `is_online`.
- A connection loss, expired offer, or routine access-token renewal must not cancel an active trip or end its insurance period.
- Preserve durable trip GPS through recoverable auth/network failures. Explicit logout/revocation must fence further authenticated uploads; do not extend a revoked session for trip convenience.
- Keep the online epoch, auth generation, client session generation, and offer claim ID separate.
- Existing insurance transitions and ride guards remain mandatory. New availability transitions and their audit/insurance effects must commit atomically where applicable.
- No raw tokens, refresh credentials, precise coordinates, phone numbers, or addresses in diagnostics.
- Use append-only migrations, backend-only mutating RPC permissions, pinned function search paths, indexes for new scans, and RLS for new user-data tables.
- Each implementation commit is one logical change touching at most three files and approximately 200 changed lines. Split further when needed. Put its Change Impact & Risk entry in the PR body to preserve that file limit.
- Ship new behaviour behind default-off settings; enforce capabilities on the server. A mobile release and a backend main-branch deployment are separate release events.

## Review focus

These cases must have explicit tests in the owning tasks:

1. An offline request races a heartbeat and an offer claim on different API replicas.
2. A phone remains connected overnight but no person confirms readiness.
3. A refresh response is lost after the server rotates the credential, followed by a cold start.
4. An offer acceptance reaches Postgres at the same time as expiry or session displacement.
5. Redis fails while a driver is mid-trip, or the OS stops scheduling idle background work.

## 1. What is happening today

### Current rules, verified in code

| Mechanism | Current behaviour | Consequence |
|---|---|---|
| Driver intent | `drivers.is_online` persists in Postgres; explicit status changes update it. | Closing the UI is not equivalent to going offline. |
| Assignment availability | `is_available` represents online with no conflicting trip/offer. | An online driver can correctly be unavailable. |
| Reachability | Redis presence TTL is 90 seconds, renewed by accepted location/WS activity. | A functioning background task can keep renewing presence while the person is asleep. |
| Long unreachable cleanup | `stale_intent_reconciler.py` scans about every 15 minutes; default and current setting are four hours. Requires stale `drivers.updated_at`, absent presence with healthy Redis, and no active ride. | It does not detect a reachable but unattended phone. Generic row updates can also obscure the meaning of activity. |
| Missed offers | Default threshold is three misses; Redis miss-streak TTL is 30 minutes. Redis failures can disable the count by returning zero. | This is an offer-response mechanism, not an overnight availability limit. The threshold is a code default; its runtime override was not inspected. |
| Token refresh | Renews/rotates authentication credentials. Foreground and background paths already coordinate through a session lock. | Refresh does not prove willingness to drive and should not reset readiness or missed-offer counts. |
| Live-location 409 | The inspected route explicitly returns `409 "Driver is not online"` when the driver row is offline. | Continuing native callbacks can generate recurring 409s after a server-side offline transition. |
| Batch-location 409 | Multiple conflict reasons exist, including offline idle upload and ride-state conflicts; the client currently classifies broad terminal HTTP statuses. | A blanket “discard every 409 batch” rule risks losing retryable history. |
| App online state | Dashboard adopts profile online state once, then local toggle/auto-offline events own it. | Missed notifications and background/resume races can leave the UI inconsistent with the server. |
| Grey GO | Current `DriverIdlePanel` blocks offline GO when driver status is missing or not `active`; online STOP remains allowed. | Missing profile data can look like confirmed ineligibility. The earlier grey-STOP bug is already addressed in this commit. |
| Relaunch authentication | Startup distinguishes unreadable storage/transient refresh failure from absent or rejected refresh credentials. | Some recovery protections already exist. A logout still requires evidence of the exact failure branch. |

### Important deployment finding

The pasted 16:50 error proves that the reaper could not read `drivers.availability_claim_id` at that time. A fresh read-only check during this review found the claim-ID columns and v2 claim/reaper functions **present**. Do not continue treating the column as currently missing or blindly rerun every historical migration.

Current inspected settings:

| Setting | Value |
|---|---|
| `dispatch_claim_identity_enabled` | `false` |
| `driver_single_session_enabled` | `false` |
| `stale_intent_offline_hours` | `4.0` |

The schema exists, but these two new behaviours remain disabled. Runtime callers for `begin_driver_session` do exist at the reviewed commit; earlier docs saying there were no callers are stale. When the flag is false, authentication uses the legacy session-ID path. That path attempts displacement, but lacks the enabled RPC's complete generation fencing. Do not switch either flag on without its staging and all-replica compatibility checks.

### What the reported incident does and does not establish

The overnight case is consistent with background GPS keeping reachability alive while intent stayed online. A Redis outage can also let dispatch fall back to DB-online drivers; the actual incident's Redis health is unknown. An offer can arrive or be opened after its server deadline, and subsequent missed offers can take a driver offline. The grey GO control could then reflect missing/non-active eligibility data. Relaunch could expose a separate authentication failure.

This is a plausible chain, **not a proven single root cause**. The access logs contain proxy/private source addresses, not driver/session identity or response bodies. Mixed 200 and 409 entries cannot establish that the same driver toggled state. A batch 200 also does not prove that a live marker reached the rider.

Capture the incident's app build, OS, driver ID, request IDs, status-transition reasons, offer creation/delivery/expiry/accept timestamps, refresh outcomes, and Redis/reaper health. Inspect server state by IDs, without exporting credentials or location history. Correlate the 23 September 17:03–17:04 UTC window before making incident-specific claims.

## 2. Uber and Lyft comparison

Only documented product behaviour is compared. Public help pages do not establish either company's Redis TTLs, token rotation design, session fencing, or complete matching implementation. Behaviour also varies by market and release.

| Topic | Public evidence | Spinr decision |
|---|---|---|
| Explicit availability | Uber describes GO and Go Offline; Lyft's 2018 product explanation distinguishes offline, online and last-ride mode. | Give the driver explicit controls and visible state; never infer readiness from login alone. |
| Background operation | Uber documents background GPS and reminders when using another app without accepting a trip. | Preserve legitimate background trips and reachability; separately confirm long-idle readiness. |
| Offers | Uber help describes a short response window; Lyft describes requests disappearing and distinguishes technical misses. | Keep an absolute server deadline; discard late displays; distinguish delivery failure from an offer that was shown and ignored. |
| Connectivity | Lyft documents connectivity as a cause of frozen/unresponsive app behaviour. | Show reconnecting/recovery state, with a clear action, while preserving valid credentials and active-trip context. |

Sources, accessed 23 September 2026:

- [Uber: taking trips](https://www.uber.com/ca/en/drive/basics/how-to-take-trips/)
- [Uber: using other apps while online](https://help.uber.com/driving-and-delivering/article/using-other-apps-or-getting-calls-while-online----?nodeId=f6ab4115-7fc7-48cd-87d7-dfbed561a364)
- [Uber: getting a trip request](https://help.uber.com/driving-and-delivering/article/getting-a-trip-request?nodeId=e7228ac8-7c7f-4ad6-b120-086d39f2c94c)
- [Lyft: acceptance rate and technical misses](https://help.lyft.com/hc/en-ca/all/articles/115013077708)
- [Lyft: availability controls — historical product explanation, February 2018](https://www.lyft.com/hub/posts/go-online)
- [Lyft: app and connectivity problems](https://help.lyft.com/hc/en-ca/all/articles/115013078688)

Do not adopt a claimed universal Uber/Lyft “three misses” rule or copy driving-hour restrictions from another market. Spinr's proposed readiness policy below is a product recommendation, separate from regulatory driving-time rules.

## 3. Architecture decision

### Alternatives

| Approach | Benefit | Limitation | Decision |
|---|---|---|---|
| Shorten Redis TTL and add an offline marker | Small patch; reduces some 409 loops. | Does not catch an unattended but connected phone, resolve offer races, or explain auth/UI failures. | Immediate containment only. |
| Strengthen the current DB/Redis model with versioned state | Reuses existing infrastructure and transactional claims; addresses all reported scenarios. | Requires coordinated backend/mobile rollout and careful race tests. | **Recommended.** |
| Rebuild presence and dispatch as separate event-stream services | Could serve a much larger fleet. | Adds operational burden and migration risk without evidence of need. | Defer. |

### Dispatch eligibility

For a **new idle-driver offer**, require every condition:

```text
valid controlling session
AND online intent
AND accepting_requests
AND current online_epoch
AND eligible account/documents/service area
AND no conflicting active ride or pending claim
AND fresh reachability lease
AND fresh, integrity-approved location
AND readiness deadline has not passed
```

Existing deliberately supported queued/back-to-back ride flows need their own explicit obligation checks. Do not accidentally disable or broaden them through the idle-driver predicate.

### Distinct identities and clocks

| Name | Purpose | Changes when |
|---|---|---|
| Auth token version | Revokes older auth generations. | Explicit login displacement/logout-all policy, not routine refresh. |
| Client session generation | Fences delayed native callbacks against a newer local login. | Local session replacement/teardown. |
| `online_epoch` (new integer) | Fences old online/offline/heartbeat requests. | A committed online/offline/pause/controller transition invalidates the old epoch. |
| `availability_claim_id` | Identifies a particular offer reservation. | A new dispatch claim is created. |
| `last_contact_at` | Dedicated server-received contact timestamp. | Valid current-session heartbeat; coalesce durable updates if needed. |
| `ready_until` | Limits how long an idle driver is assumed ready. | Explicit Go/Still ready, accepted offer, explicit decline, or authenticated trip completion action. GPS, WS pongs and token refresh never extend it. |

Reuse existing equivalents if implementation discovery finds them; do not create duplicate session abstractions. A controlling session ID comes from authenticated server claims/state, never trusted from a client-supplied driver ID.

### Proposed initial timing policy

These are canary defaults to validate on real devices and low-demand shifts, not measured industry standards.

| Event | Proposed behaviour |
|---|---|
| Healthy online phone | Aim for contact at most every 20–30 seconds when the OS schedules work. Existing more frequent GPS updates can serve this purpose. |
| No valid contact for 90 seconds | Immediately ineligible for new offers when the lease expires. UI shows reconnecting when it can run. |
| Short connectivity gap | Reconcile and resume the same online epoch only if the server confirms it is still valid and ready. |
| Contact gap over five minutes | Require explicit Go Online before new offers; invalidate the old epoch. Enforce on the request path as well as a worker. |
| No qualifying human activity for 60 minutes, no active trip | Show “Still available?” with a two-minute response window. If unconfirmed, pause new offers and require Go Online. Server enforces the 62-minute cutoff even if notification never arrives. |
| Active trip | Idle-readiness timeout is suspended; connection/auth problems do not cancel the trip. At completion reconcile account/session and establish the next readiness window only from an authenticated completion/confirmation. |
| Three foreground-presented but unanswered offers | Preserve the existing threshold concept, record each outcome once, and pause with an explanation. Presentation is a delivery signal, not proof that the person saw the offer. Explicit decline is a response, not evidence of an unattended phone. |
| Offer not delivered/acknowledged | Release according to its deadline/delivery policy; mark a delivery failure. Do not count it as a human miss. |

The five-minute gate uses a dedicated server timestamp, with its write-coalescing interval documented and tested. The 90-second lease remains the earlier dispatch gate. Do not use `updated_at` as proof of connectivity. A geofence, token refresh, restored UI, or incoming push cannot silently clear a pause.

The 60-minute rule may interrupt a legitimate long wait; provide clear notice and a one-tap renewal, measure false pauses, and tune only this product threshold. Do not impose penalties or infer readiness from device movement. If a driver leaves the app running all night, the phone may remain reachable but becomes ineligible after the readiness cutoff.

### Redis and Postgres consistency

Postgres owns the intent/epoch/session and claims. Redis contains `{driver_id, controller_session_id, online_epoch, received_at, location_valid_at}` with a TTL. This is a discovery filter, not permission to assign a ride.

1. A status transition locks the driver row and checks the expected epoch and obligations.
2. It updates intent/accepting state, increments the epoch, and writes related audit/insurance effects in one transaction.
3. After commit, invalidate Redis discovery data and publish the versioned snapshot. Failed publication is retried using the existing durable delivery pattern or a narrowly scoped outbox.
4. Heartbeat renewal validates the current session/epoch and location eligibility. A delayed renewal may leave an old Redis entry, but its old epoch cannot pass the final DB claim.
5. Dispatch reads fresh Redis evidence and passes the observed epoch/session to the transactional claim. The DB revalidates durable state and deadline before creating the offer.

There is no distributed transaction between Redis and Postgres. This design deliberately makes stale Redis evidence insufficient to revive an offline driver. Serialize online/offline, claims, acceptance, expiry and displacement through a documented consistent row-lock order. Keep migration 448's claim identity checks. Lock driver rows in sorted ID order, then ride rows in sorted ID order, then offer rows; no path may acquire another driver lock after locking a ride. Release another driver's losing claim in a separate idempotent operation if its driver lock was not acquired at the start. Audit every existing caller against this order before adopting the RPCs.

Use backend-generated lease evidence only: the claim receives a trusted `contact_valid_until` and `location_valid_until` derived from Redis/server validation, never raw client timestamps as authority. Recheck both against `clock_timestamp()` **after acquiring locks**, together with durable `last_contact_at` and `ready_until`. This prevents a candidate selected before lease/offer expiry from being committed after a long lock wait. Retain the existing live GPS age ceiling of 60 seconds and five-second future-skew allowance initially; a WS heartbeat cannot make old GPS fresh.

Pause semantics are explicit: if no live obligation exists, commit `is_online=false`, `is_available=false`, `accepting_requests=false`, a reason, and a newer online epoch. With an active trip, retain `is_online=true`, set `accepting_requests=false`, and keep the trip/insurance period. Resolve a pending unaccepted offer transactionally before closing its obligation. Re-Go after a pause uses the same authenticated controller and creates a new online epoch; it does not create a login or change auth generation. A controller replacement is a separate authenticated displacement operation.

The T1 transition RPC owns Go, Stop, pause and displacement of availability. T5 owns offer accept/decline/expire/cancel decisions; both use the same lock order and one owner for each insurance transition. Existing login displacement must invoke the shared availability transition inside its transaction or use a durable fenced handoff; there must be no window where the new auth generation leaves an old controller dispatchable.

**Redis unavailable:** continue recording authenticated durable contact when Postgres is healthy so the outage itself is not confused with a dead phone; never bulk flip all driver intent based only on missing Redis data. Pause new automatic offers and expose a temporary matching degradation. Preserve active trips, trip history, and auth recovery. Do not revert to every DB-online driver. This trades temporary matching capacity for avoiding ghost offers; instrument and canary it explicitly. If a future fallback is required, it must use independently bounded durable contact evidence and the same fences, not an unrestricted online flag. Production must not silently use per-process presence dictionaries across replicas.

## 4. Driver application behaviour

Maintain three separate state domains:

```text
auth: restoring | authenticated | recoverable | reauth_required
availability: unknown | offline | connecting | ready | reconnecting | paused | blocked
work: idle | offer_pending | on_trip
```

The server snapshot supplies `state_version`, `online_epoch`, `is_online`, `accepting_requests`, `availability_state`, `reason_code`, `controller_session_id`, `server_time`, `last_contact_at`, `ready_until`, `active_ride`, and `pending_offer`, and `snapshot_issued_at`. `state_version` advances on durable availability/offer/trip changes; same-version snapshots are ordered by database-issued snapshot time, and the client also rejects obsolete local reconciliation request generations. Serialize integer versions safely as decimal strings in JSON. Snapshot reads must be internally consistent for the driver/offer/trip state they report.

A signed-out/revoked app stops authenticated work. A merely offline app may still restore its account/profile; it must not refresh dispatch presence as a side effect.

On cold start, foreground resume, WS reconnect, auto-offline, status conflict, or notification tap:

1. Restore/refresh auth through the existing serialized coordinator.
2. Fetch the authoritative availability snapshot with a request deadline.
3. Apply it only if the local session generation still matches and its state version is not older.
4. Recover the active trip first. Expire invalid offers. Reconcile the native tracker with current work and intent.
5. Render a useful action. Reaching a ten-second reconciliation deadline shows Retry/connection guidance; it never leaves an unexplained disabled button.

| Situation | Driver sees | Allowed action |
|---|---|---|
| Offline and eligible | “You're offline” | Go Online |
| Restoring | “Checking your status…” | Retry after timeout; no silent online restoration |
| Ready | “You're online and available” | Stop requests |
| No contact/temporary auth failure | “Reconnecting — new requests paused” | Retry; preserve current trip |
| Idle/missed-offer pause | “Requests paused because you haven't responded” | Go Online |
| Expired offer | “This offer expired” | Dismiss; return to reconciled availability |
| Missing profile response | “We couldn't check your eligibility” | Retry; do not call the account pending |
| Confirmed account/document block | Specific reason from server | Open the relevant resolution screen |
| Confirmed displaced/revoked session | “Your session ended” with the actual reason | Sign in; preserve a safe trip-recovery route if work exists |

### Tracking and 409 handling

- Store a session-bound availability record containing epoch and intended idle tracking state. Persist a local stop request before awaiting native stop. Only a successful explicit online transition clears that stop for a newer/current epoch.
- Native recovery/geofence/FCM callbacks must read this state and cannot turn an offline driver online. Keep trip recording and idle dispatch tracking as separate decisions.
- Add structured backend codes: `DRIVER_OFFLINE`, `ONLINE_EPOCH_STALE`, `SESSION_SUPERSEDED`, `PRESENCE_UNAVAILABLE`, `OFFER_EXPIRED`, `RIDE_STATE_CONFLICT`, `ELIGIBILITY_UNAVAILABLE`.
- A live `DRIVER_OFFLINE`/stale-epoch response triggers one coalesced snapshot recovery and suppresses repeated idle uploads for that epoch. It does not log out the user or discard active-trip history.
- Return per-batch/per-point durable acknowledgement. Drain only acknowledged or explicitly permanent-invalid items with a recorded reason. An idle-offline conflict is not a reason to erase an unacknowledged trip batch.
- STOP while a trip is active means stop future requests; retain the current obligation. A pending offer is resolved transactionally before closing its claim/insurance period. A network failure shows stopping/reconnecting until server acknowledgement or lease expiry bounds new offers.

## 5. Authentication and controlling device

Extend the existing foreground/background refresh coordinator. Do not build a second one.

- Access-token expiry triggers refresh. It does not change online intent, increment online_epoch, reset readiness, or reset a miss counter.
- Preserve recoverable state on network errors, 5xx, or unreadable secure storage. Stop new offer eligibility when the contact lease cannot renew.
- Distinguish absent credentials from failed reads. A definitive invalid/revoked refresh response must be correlated to the credential generation that was actually sent before clearing anything.
- Preserve the existing rotation-race recovery; add the case where the server rotates but the response is lost before persistence. Evaluate the current benign-race handling with an actual restart test before changing replay policy.
- An old 401, logout completion, or native callback cannot wipe credentials from a newer login.
- Prefer one controlling driver device/session. Other device sign-in must have explicit displacement UX; an active-trip takeover requires a supported transfer/recovery flow. Ordinary refresh never takes control.
- Validate the already-written generation RPC and all OTP/Firebase/refresh/logout paths before enabling `driver_single_session_enabled`. Do not enable it solely because migration 452 exists.

A push registration token is only a delivery address. Its renewal is unrelated to being online. Likewise, background contact confirms a running authenticated phone, not that the driver is awake.

## 6. Offers and expiry

Retain the existing configured offer duration initially (batch default 15 seconds). Increasing the timer alone masks delivery problems.

Offer envelope:

```json
{
  "offer_id": "uuid",
  "claim_id": "uuid",
  "online_epoch": "42",
  "server_time": "2026-09-23T17:03:30Z",
  "expires_at": "2026-09-23T17:03:45Z"
}
```

- Use the same offer identity across push, WS and persisted recovery. Deduplicate all channels.
- Configure provider TTL/expiration from the remaining offer lifetime; delayed push is still possible, so the client checks expiry and authoritative state.
- Use server-time offset plus a monotonic local countdown. Do not start a fresh 15-second timer on app open. Reconcile after a clock change or resume.
- Record idempotent `received` and `presented` acknowledgements separately. Emit `presented` only while the offer UI is foreground-visible and interaction-eligible; include app state, channel and remaining deadline. Even this does not prove human attention. A transport ACK does not prove that a human saw the offer. Do not extend `expires_at` when either ACK arrives.
- Initially use ACKs for observability and fair miss classification. Introduce early undelivered-offer cancellation only after measuring locked-device ACK delays; keep the existing deadline as the hard bound.
- Acceptance and expiry contend on one transactionally protected decision. Check ownership, claim ID, online epoch, permitted session, pending state and DB time. `now >= expires_at` rejects acceptance.
- If accept commits first, expiry cannot penalize or release that claim. If expiry commits first, accept returns `OFFER_EXPIRED` with a fresh snapshot. Retries of the winner are idempotent. Preserve exactly one insurance transition and one miss outcome.
- Reapers recover work after process death; correctness must not depend on an in-process timer running exactly on time.

## 7. Expected outcome for the reported scenarios

| Scenario | Required result |
|---|---|
| Driver leaves a healthy phone online overnight | Readiness expires and new offers pause even though GPS continues. On return, driver stays signed in and must tap Go Online. |
| Phone dies or app is killed | Lease expires within 90 seconds of last valid contact; no new offer passes the eligibility gate. Longer disconnect requires explicit resume. |
| App wakes after an offer expires | Remove stale offer and notification; show expiry message if relevant; do not reopen or restart its countdown. |
| Three foreground-presented offers receive no response | Pause once, with an explanation and a working Go Online action. |
| Server is offline but native callbacks continue | First structured conflict reconciles/suppresses idle sends; no endless 409 loop. |
| Profile lookup temporarily fails | Recovery UI with Retry; no false account-ineligible classification. |
| Access token expires overnight | Refresh quietly when work runs; no availability counter resets. Temporary failure is recoverable. |
| App restarts during refresh rotation | Recover the persisted/current token generation or show recovery; no unrelated session wipe. |
| Session truly revoked/displaced | Stop new offers and uploads for that session; show explicit sign-in/transfer guidance. |
| Active ride loses network or Redis fails | Keep trip context and permitted local history; suppress new offers and recover uploads safely. |

## 8. Implementation map and dependency order

Keep the existing modules; introduce small focused helpers for the new state contract. New paths below are intentional proposed files. Migration numbers 455–459 are available at the reviewed snapshot; recheck main immediately before creating them and reserve the next free numbers if they have since been used. Never edit or rename an applied migration.

| Wave | Responsibility | Primary files |
|---|---|---|
| 0 | Capture evidence and confirm deployed capabilities | Existing migration/status tools, loop metrics, structured logs |
| 1 | Durable availability schema and transition contract | New migration 455, new repository/service, status route |
| 2 | Session/epoch presence and all dispatch admission paths | Presence utility, location route, matching/service/RPCs |
| 3 | Atomic offer decision and delivery outcomes | New migrations 457–458, ride flow, expiry/reaper/offer delivery |
| 4 | Mobile availability state, recovery, tracker fencing | New shared availability types/reducer; dashboard; native tracker; idle panel |
| 5 | Auth hardening, long-idle policy, rollout | Existing auth helpers, reconciler, new migration 459 and readiness worker |

Within each task: write the specified regression first, confirm it fails for the intended reason, implement the bounded change, rerun that targeted test, review the diff and commit. Do not represent the sample contract/test snippets as already-running code. Integration fixtures must exercise the actual route/RPC, not merely duplicate the predicate in the test.

### Task 0: Incident and deployment baseline

- [ ] Record deployed API SHA on every Fly/Railway replica and installed iOS/Android build.
- [ ] Inspect migration runner status and function signatures/permissions read-only. Confirm 448/452 capabilities, existing settings and loop health; reconcile the custom migration ledger with actual schema.
- [ ] Correlate the driver's status, offers, auth outcomes and reconnect events for the incident window. If the driver cannot be identified, explicitly retain the incident-root-cause uncertainty.
- [ ] Establish baseline location-409 reason counts, delivery/expiry latency, unexpected logout reasons, and availability disagreements. Add IDs/reason codes where absent before relying on aggregate HTTP status logs.

Deliverable: a read-only incident note and canary baseline. No mass driver reset, Redis flush, or blanket migration replay.

### Task 1: Availability schema and atomic transition

Files (3): create `backend/migrations/455_driver_availability_epoch.sql`; create `backend/repositories/driver_availability_repo.py`; create `backend/tests/direct_pool/test_driver_availability_epoch.py`.

- [ ] Add `online_epoch`, `controller_session_id`, `accepting_requests`, `last_contact_at`, `ready_until`, `availability_reason` to drivers, plus a monotonically increasing `state_version`. Backfill conservatively and keep new admission dark.
- [ ] Define `transition_driver_availability(driver_id, expected_epoch, authenticated_session_id, action, request_id)` returning a complete committed availability snapshot. Supported actions: `go_online`, `go_offline`, `stop_requests`, `pause_unreachable`, `pause_idle`, `pause_misses`, `displace_controller`. `go_offline` requires no remaining obligation; `stop_requests` schedules that outcome while preserving current work. Offer decision actions belong to T5.
- [ ] Lock the driver and affected obligations in one documented order; compare epoch, check eligibility/obligations, update fields, preserve an active trip, and write required audit/period changes. Use an idempotency request ID so a lost response does not toggle twice.

Contract example:

```python
# Two concurrent commands based on epoch 7 cannot both mutate epoch 7.
first = transition(driver_id, expected_epoch=7, action="stop_requests")
assert first.online_epoch == 8
assert first.accepting_requests is False
second = transition(driver_id, expected_epoch=7, action="go_online")
assert second.code == "ONLINE_EPOCH_STALE"
```

- [ ] Integration tests: simultaneous stop/claim; active-trip stop retains ride/period; stale pause cannot override a new Go; duplicate request ID returns same result; unauthorized session cannot mutate.
- [ ] Run direct-pool tests against disposable Postgres; inspect RPC permissions and indexes. Commit after review.

### Task 2: Snapshot API and status integration

Files (3): create `backend/services/driver_availability_service.py`; modify `backend/routes/drivers/status.py`; create `backend/tests/test_driver_availability_snapshot.py`.

- [ ] Expose `GET /api/v1/drivers/me/availability` and route existing status writes through T1 behind the availability flag.
- [ ] Define service `get_driver_availability(user_id)` and `change_driver_availability(user_id, command)`; return the fields and reason codes in section 4.
- [ ] Snapshot queries must agree on the driver epoch, active ride and pending offer; use a consistent DB snapshot/RPC, or retry if the version changed during assembly.

```python
assert snapshot["availability_state"] == "blocked"
assert snapshot["reason_code"] == "DOCUMENT_EXPIRED"
assert snapshot["pending_offer"] is None
# On lookup failure, return 503/ELIGIBILITY_UNAVAILABLE, not status=pending.
```

- [ ] Test old snapshot ordering, lookup failure, expired pending offer, eligible offline driver, and active trip during reconnect. Validate timeout/error contract and commit.

### Task 3: Session-bound presence renewal

Commit A (3 files): modify `backend/utils/driver_presence.py`; modify `backend/routes/drivers/location.py`; create `backend/tests/test_driver_presence_epoch.py`. Commit B (3 files): modify `backend/routes/websocket.py`, `backend/utils/driver_presence.py`, and `backend/tests/test_websocket_live_location.py` for authenticated WS connect/pong renewal. Inventory every `mark_present`/`clear_presence` caller; migrate each through the same fence in bounded follow-up commits, including status and admin paths. No legacy writer may overwrite a v2 presence value with an unscoped key.

- [ ] Define `renew_driver_presence(driver_id, session_id, online_epoch, received_at, location_valid_at)` returning `renewed | stale_epoch | offline | unavailable`.
- [ ] Validate current owner/epoch and GPS integrity before updating dispatch-location freshness. Track network contact separately so an invalid fix cannot make an old location dispatchable.
- [ ] Return structured conflict codes and current epoch. Coalesce durable `last_contact_at` writes with a documented maximum interval of 30 seconds; test five-minute gap behaviour at that boundary.

```python
await go_offline(epoch=12)  # Commits epoch 13.
result = await renew_driver_presence(epoch=12)
assert result == "stale_epoch"
assert await can_claim(epoch=12) is False
```

- [ ] Test delayed background callback, mocked/rejected GPS, old/future timestamps, Redis unavailable, and new-session isolation. Commit.

### Task 4: Dispatch eligibility and final claim fencing

Split into two commits, each at most three files:

1. Modify `backend/services/dispatch_service.py`, `backend/routes/rides/matching.py`, `backend/tests/test_dispatch_presence_failopen.py` to centralize admission and change new-offer outage policy behind the flag.
2. Create `backend/migrations/456_driver_claim_epoch_fence.sql`, create `backend/repositories/driver_offer_repo.py` for the new RPC wrappers, and create `backend/tests/direct_pool/test_driver_claim_epoch_fence.py`. This depends on 455 and precedes the offer-decision migration; do not edit 448.

- [ ] Inventory callers of driver claim functions, including scheduled, batch, sequential, retry, admin assignment and any queued-ride path. Each must either satisfy the contract or have a documented explicit admin override that cannot bypass session/obligation safety.
- [ ] Carry observed epoch/session and bounded contact evidence into the DB claim. Check `ready_until` using database time.

```python
assert eligible(intent=True, lease=False, ready=True) is False
assert eligible(intent=True, lease=True, ready=False) is False
assert eligible(intent=False, lease=True, ready=True) is False
# Final RPC must independently reject a candidate whose epoch changed.
```

- [ ] Verify healthy-empty Redis and unavailable Redis both prevent new automatic offers under v2, while active-trip APIs work. Shadow compare candidate sets before enforcement. Commit each bounded change with its tests.

### Task 5: Atomic acceptance/expiry

Files per commit (3 maximum): first create `backend/migrations/457_offer_decision_atomicity.sql`, extend `backend/repositories/driver_offer_repo.py`, and create `backend/tests/direct_pool/test_offer_decision_atomicity.py`; then integrate `backend/routes/drivers/ride_flow.py`, `backend/utils/offer_expiry_reaper.py`, and create `backend/tests/test_offer_decision_routes.py`. A separate matching timeout integration commit modifies `backend/routes/rides/matching.py` and creates `backend/tests/test_matching_offer_decisions.py`.

- [ ] Define backend-only `resolve_driver_offer(offer_id, claim_id, expected_epoch, actor_session_id, action, request_id)` for `accept`, `decline`, `expire`, and `cancel_unaccepted`. Use the T1 lock order.
- [ ] At database time, atomically decide winner, update offer/ride/availability, emit one durable outcome, and preserve insurance claim identity.

```python
results = await concurrently(accept_at_deadline(), expire_at_deadline())
assert committed_accepts(results) <= 1
assert not (offer_is_accepted() and miss_was_recorded())
assert number_of_open_claims_for_driver() <= 1
```

- [ ] Test both race orderings, lost-response retries, stale claim release after a newer offer, process restart, and displaced-session accept. Commit only after real transaction tests pass.

### Task 6: Offer presentation receipts and fair missed-offer handling

Commit A (3 files): create `backend/routes/drivers/offer_receipts.py`; register it in `backend/routes/drivers/__init__.py`; add `backend/tests/test_offer_receipts.py`. Commit B (3 files): create `backend/migrations/458_offer_delivery_receipts.sql`, modify `backend/routes/rides/matching.py`, and create `backend/tests/test_offer_miss_outcomes.py`. Commit C (3 files): update `backend/features.py`, `backend/routes/rides/matching.py`, and create `backend/tests/test_offer_notification_deadline.py`. Inspect the existing push provider helper called by `features.py`; if provider TTL needs a deeper helper change, put that helper and its targeted test in a separate commit. Apply receipt schema before enabling the route.

- [ ] Add idempotent receipt keyed by offer/session/event with `received` or `presented`, accepted only for the addressed authenticated driver.
- [ ] Count each expired, foreground-presented, unanswered offer once as a nonresponse outcome, without claiming the person saw or deliberately ignored it. Missing receipts are delivery-unknown; they are not proof of a human miss. The readiness deadline still catches unattended devices.
- [ ] Set offer notification expiration/TTL to the remaining lifetime and propagate the same offer envelope on all channels.

```python
record_presented(offer_id, session_id)
record_presented(offer_id, session_id)
expire(offer_id)
expire(offer_id)
assert miss_outcomes(offer_id) == 1
```

- [ ] Test duplicated channels, late ACK, no ACK, accepted winner, explicit decline, and threshold crossing only once. Keep timer extension disabled. Commit each unit.

### Task 7: Shared mobile state contract

Files (3): create `shared/types/driverAvailability.ts`; create `driver-app/utils/driverAvailabilityState.ts`; create `driver-app/__tests__/utils/driverAvailabilityState.test.ts`.

- [ ] Encode section 4 types, structured errors and a pure reducer. Network effects stay outside it.
- [ ] Implement `applyAvailabilitySnapshot(state, snapshot, localGeneration)` with monotonic version and session-generation checks.

```typescript
expect(applySnapshot(currentVersion8, staleVersion7)).toEqual(currentVersion8);
expect(applySnapshot(newLogin, oldLoginSnapshot)).toEqual(newLogin);
expect(profileReadFailed(readyState).eligibility).toBe('unknown');
```

- [ ] Test every state in section 4 and the ten-second recovery UI transition. Commit.

### Task 8: Dashboard reconciliation and explained controls

Commit A (3 files): create `shared/api/driverAvailability.ts` using the existing configured Axios client, modify `driver-app/hooks/useDriverDashboard.ts`, and create `driver-app/hooks/__tests__/useDriverDashboard.availability.test.ts`. Commit B (3 files): modify `driver-app/components/dashboard/DriverIdlePanel.tsx`, `driver-app/__tests__/components/DriverIdlePanel.test.tsx`, and `driver-app/app/driver/(tabs)/index.tsx` for props. The existing status mutation in `shared/store/authStore.ts` must delegate to the agreed contract in its own bounded integration commit, with `driver-app/__tests__/store/authStore.driverStatus.test.ts`.

- [ ] Replace one-time online hydration as the availability authority with T2/T7 reconciliation on the named lifecycle events. Coalesce concurrent resumes/conflicts into one request.
- [ ] Preserve current trip UI while checking availability. Never replay cached `is_online=true` as a Go command.
- [ ] Render blocked versus unknown eligibility distinctly. Keep stop-future-requests accessible for online/on-trip state. Expired offer is an outcome, not a disabled-control condition.

```typescript
await resumeWith({ serverState: 'paused', cachedOnline: true });
expect(screenState()).toMatchObject({ action: 'Go Online', online: false });
await profileFailure();
expect(screenState().action).toBe('Retry');
```

- [ ] Test deferred responses, missing status, concurrent auto-offline, current trip, timeout and older snapshot. Run Jest and a production mobile build; document that unit tests do not prove native background execution. Commit each unit.

### Task 9: Native tracking intent and response classification

Commit A (3 files): create `driver-app/utils/availabilityMarker.ts`, modify `driver-app/utils/backgroundLocation.ts`, add focused native-callback tests. Commit B (3 files): update `driver-app/hooks/useDriverDashboard.ts`, `driver-app/utils/backgroundLocation.ts` (geofence/recovery helpers live here), and `driver-app/utils/__tests__/backgroundLocation.test.ts`. Split additional callers found by the start/stop inventory into individual bounded commits. Commit C (3 files): update `driver-app/utils/tripLocationTransport.ts`, `driver-app/utils/tripLocationRecorder.ts`, and add `driver-app/utils/__tests__/tripLocationTransport.ack.test.ts` for explicit batch classification if the transport owns that path.

- [ ] Persist `{sessionGeneration, onlineEpoch, idleUploadsAllowed}`. A stop writes false before native stop; only an acknowledged online transition can set true.
- [ ] Every native/recovery/geofence start and upload checks the generation/epoch fence. Preserve separately authorized active-trip recording.
- [ ] On structured live conflict suppress idle retries and request one reconciliation. Replace blanket batch-status draining with explicit acknowledgement/permanent-rejection handling.

```typescript
await persistStopped({ epoch: '9', generation: 'A' });
await oldNativeCallback({ epoch: '8', generation: 'A' });
expect(liveUploads).toHaveLength(0);
expect(unacknowledgedTripHistory).toEqual(originalHistory);
```

- [ ] Test native stop failure, force-kill/relaunch, recovery callback after stop, logout/new login, offline idle batch, valid completed-trip retention, and every existing batch rejection code. Commit.

### Task 10: Offer UI and notification recovery

Use these bounded commits:

- A: `driver-app/services/pendingRideOffer.ts`, `driver-app/__tests__/services/pendingRideOffer.test.ts`, `driver-app/store/driverStore.ts` (offer types/outcome).
- B: `driver-app/hooks/useDriverDashboard.ts`, `driver-app/app/driver/(tabs)/index.tsx` (actual countdown), and new `driver-app/hooks/__tests__/useDriverDashboard.offerDeadline.test.ts`.
- C: `driver-app/services/backgroundMessaging.ts`, `driver-app/services/notifeeService.ts`, and new `driver-app/__tests__/services/offerDelivery.test.ts`.
- D: `driver-app/app/_layout.tsx` and new `driver-app/__tests__/notificationOfferResume.test.ts` for notification action reconciliation.
- E: `driver-app/lib/androidAuto/carSession.ts` and `driver-app/lib/androidAuto/__tests__/carSession.test.ts` for the second offer consumer.

Retain the existing wire name `offer_expires_at` during compatibility and map it explicitly to the v2 internal `expires_at`; support both through a typed adapter and compare consistent values if both are present. Stored-offer consumption currently deletes the record before checking expiry. Return a typed `expired` outcome with only offer ID/deadline for optional feedback, without retaining a stale actionable offer or reviving it. An app that has no surviving offer receipt cannot promise to explain every historical expiry.

- [ ] Deduplicate by offer ID, validate absolute expiry and epoch on every display/recovery path, send received/presented receipts, and reconcile on notification tap.
- [ ] Use monotonic countdown from server-time offset. Late accept consumes structured result and settles the UI; no modal may remain locked on an expired offer.

```typescript
deliverOffer({ expiresAt: serverNow - 1 });
expect(visibleOffer()).toBeNull();
expect(acceptEnabled()).toBe(false);
expect(notificationForOffer()).toBeNull();
```

- [ ] Test delayed push, duplicate WS/FCM, clock skew, suspend across deadline, accept/expiry race response, and lost accept response followed by snapshot. Commit.

### Task 11: Auth generation and restart reliability

Commit A (3 files): modify `shared/store/authStore.ts`, `shared/auth/sessionLock.ts` only if the tests require it, and `driver-app/__tests__/store/authStore.refreshRace.test.ts`. Commit B (2 files): modify `driver-app/utils/backgroundAuth.ts` and its existing tests. Commit C: any proven backend change stays within `backend/routes/auth.py`, `backend/utils/refresh_tokens.py`, and the relevant auth tests.

- [ ] Add regression coverage before changing the existing coordinator. Prove lost-response rotation recovery, secure-storage read failure, 503 versus definitive refresh rejection, stale-generation 401, and restart after background rotation.
- [ ] Audit all OTP/Firebase/session-cleanup paths and active-trip displacement behaviour; reuse generation RPC 452. Only implement remaining verified gaps.

```typescript
await receiveOldRefresh401AfterNewLogin();
expect(currentCredentials()).toEqual(newLoginCredentials);
await initializeWithStorageReadError();
expect(authState()).toBe('recoverable');
expect(credentialsWereDeleted()).toBe(false);
```

- [ ] Run shared and rider auth regression tests as well as driver tests, because authStore is shared. Enable the single-session flag only after the all-replica and active-trip takeover tests pass. Commit each fix independently.

### Task 12: Long-idle readiness and durable pause recovery

Commit A (3 files): create `backend/migrations/459_driver_readiness_policy.sql`, create `backend/utils/driver_readiness_reconciler.py`, create `backend/tests/test_driver_readiness_reconciler.py`. Commit B: register worker in `backend/core/lifespan.py`, update `backend/utils/stale_intent_reconciler.py` to use T1, and update its tests. Commit C (3 files): modify `driver-app/hooks/useDriverDashboard.ts`, `driver-app/components/dashboard/DriverIdlePanel.tsx`, and create `driver-app/__tests__/components/DriverReadinessPrompt.test.tsx`.

- [ ] Add default-off policy settings with the section 3 defaults. The worker scans indexed due rows and attempts an idempotent T1 pause. Never infer inactivity from generic `updated_at`.
- [ ] Check readiness and long-disconnect expiry on claims and heartbeat recovery even if the worker is delayed. Background heartbeats must not change `ready_until`.
- [ ] Acknowledge readiness only from an explicit authenticated action on the current epoch. Suppress prompts during active trips; reconcile immediately after work ends.

```python
for minute in range(63):
    heartbeat(current_epoch, server_time=start + minutes(minute))
assert ready_until == start + minutes(62)
assert can_receive_new_offer(at=start + minutes(63)) is False
assert authentication_is_valid() is True
```

- [ ] Test overnight live GPS, missed prompt delivery, disconnected device, stationary real driver renewal, worker restart/duplicate workers, settings/Redis failures, active trip across deadline and stale worker after a newer Go. Commit each unit.

## 9. Verification and release gates

### Automated

- Run each task's named tests while implementing. Backend commands run from `backend/`: `pytest tests/<task_test>.py`; existing `test_dispatch_presence_failopen.py`, `test_stale_intent_reconciler.py`, `test_offer_expiry_reaper.py` and auth lifecycle tests remain regression targets.
- Run direct-pool tests using the repository's existing isolated Postgres harness. Inspect `backend/tests/direct_pool/conftest.py` for the required invocation/environment; a skipped integration suite is not evidence of transaction correctness.
- Driver commands run from `driver-app/`: `yarn test <test-path> --runInBand`; include shared/rider auth suites when shared auth changes.
- Before release run the relevant lint/type/production build and repository-required CI gates. Record exact commands, results, skipped tests, and platform build IDs in the Change Impact & Risk log.

### Real devices and failure injection

| Test | Required evidence |
|---|---|
| iOS and Android locked overnight | Both no-contact and still-contact cases stop new offers by their respective bounds; restart restores a clear state. |
| Stationary driver waiting | Valid foreground/background contact does not spuriously disappear because location did not move; readiness renewal works. |
| Force-stop, process kill, battery saver/Doze | No assumption that JS intervals or push execute; server deadline remains authoritative. |
| Switch Wi-Fi/cellular, airplane mode, packet loss | Short recoverable gap resumes only under valid epoch; long gap requires Go. |
| Token expires; rotation response dropped | No unrelated logout; definitive revocation still wins. |
| Two devices and two API replicas | One controller, no stale epoch resurrection, no cross-session credential wipe. |
| Redis failure/recovery | New offers pause visibly; active trip completion/history/auth recovery still work. |
| Stop/Go/claim/accept/expire concurrency | Exactly one valid outcome, no stranded reservation, correct insurance transitions. |
| Late FCM/APNs and expired local storage | No actionable expired offer or restarted countdown. |

### Operational measurements

Use bounded reason-code labels, never driver IDs as metric labels. Add counters/histograms for `spinr_driver_availability_transition_total`, `spinr_driver_location_conflict_total`, `spinr_driver_reconcile_duration_ms`, `spinr_auth_recovery_total`, `spinr_dispatch_offer_delivery_duration_ms`, and `spinr_dispatch_offer_terminal_total`; align registration with the existing metrics utility. Correlate pseudonymous driver/session/offer IDs in structured logs. Alert on stalled reapers, schema-capability failure, Redis admission failure, unexpected logout reasons, or growth in expired-before-presented offers. Measure batch acknowledgements separately from live-marker delivery.

Keep one coalesced availability reconciliation per app/session, reuse GPS/WS activity for contact, batch Redis candidate reads, and index readiness scans. Do not add a database write for every UI render or a new independent mobile polling loop. Benchmark claim lock contention, DB contact write rate and battery usage in the canary; use the repo's existing latency targets rather than claiming the new design is faster without measurements.

### Acceptance criteria

1. No new claim commits for offline, expired-readiness, stale-epoch or conflicting-session drivers in race tests.
2. After last valid contact, new-offer eligibility ends at the 90-second lease boundary; already issued offers still follow their bounded deadline.
3. Connected unattended idle drivers cannot remain eligible beyond the configured readiness deadline.
4. After an authoritative idle-offline response, repeated native callbacks do not continue live uploads for that epoch; durable trip history is retained.
5. Reconciliation resolves or exposes Retry within ten seconds; every blocked GO has a specific reason.
6. Expired offers never become actionable on resume; accept and expiry cannot both produce winning side effects.
7. Temporary storage/network/5xx failures do not delete credentials. Genuine revocation still prevents further authenticated work.
8. No active trip is cancelled or its insurance period closed by readiness/presence loss.
9. Controlled canary has no unexplained auth logout or incorrect insurance transition; offer transport delivery P95 target remains under two seconds. Measure transport presentation separately from driver decision time.

## 10. Rollout and rollback

1. **Evidence/containment:** confirm current schema and worker health; add reason-coded diagnostics; fix verified 409/UI recovery defects without enabling the new policy globally.
2. **Schema first:** apply reviewed additive migrations with capability checks. Keep both existing dark flags and new availability/readiness enforcement off until dependencies are proven. Validate every replica and standby.
3. **Shadow mode:** calculate v2 eligibility beside the existing result without sending duplicate offers. Inspect disagreements, especially low-demand stationary drivers and Redis degradation.
4. **Mobile capability rollout:** ship iOS/Android builds; include a capability/version handshake. Clients lacking epoch/receipt support remain in a bounded compatibility cohort. A stale client cannot claim v2 support by omitting fields.
5. **Canary:** internal drivers on both OSes, then a small identified cohort. Run at least one overnight cycle and one active-trip outage cycle before expanding. Enable claim identity, session generation, availability fencing and readiness as separately observable gates in dependency order.
6. **Expansion:** increase only after criteria above pass. Log adoption and remaining legacy clients. Set a supported-version cutoff before applying the full contract fleet-wide.

Rollback disables the new idle prompt/policy first while retaining epoch/session safety and expired-offer rejection for already transitioned sessions. If an API release must be reverted, stop admitting new offers for affected v2 sessions until a compatible release or explicit re-Go transition is available. Do not deploy an old writer that ignores new epochs over live v2 sessions. Leave additive columns in place, preserve audit/insurance history, and never resurrect old offers or bulk set drivers online. Rollback must be rehearsed with an active trip and a pending claim.

## 11. Execution ownership and priority

**Architect:** owns API/schema contracts, lock ordering, state semantics, incident-evidence claims and release gates. Reviews every auth/dispatch/insurance change.

**Luna backend worker:** T1–T6, with schema/API work sequential until contracts are stable. **Luna mobile worker:** T7–T10 against agreed fixtures. **Luna auth worker:** T11. **Luna reliability worker:** T12 and device/runbook matrix after T1–T3 are stable. Use fresh review passes for actual diffs; a planning review does not replace implementation review.

Highest priority is: explain and contain current failures; make dispatch eligibility authoritative and fenced; reconcile mobile state; close offer decision races; prove auth recovery; then enable the human-readiness policy. The plan intentionally reuses protections already present rather than assuming every reported symptom requires a new subsystem.

### Code evidence index

All links are pinned to the reviewed commit.

- [Presence and counters](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/utils/driver_presence.py)
- [Location routes and explicit live 409](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/routes/drivers/location.py#L762-L810)
- [Status transitions](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/routes/drivers/status.py)
- [Dispatch presence fallback](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/routes/rides/matching.py#L580-L627)
- [Long-unreachable reconciler](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/utils/stale_intent_reconciler.py)
- [Durable offer expiry](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/utils/offer_expiry_reaper.py)
- [Claim identity migration 448](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/migrations/448_durable_dispatch_claim_identity.sql)
- [Driver session generation migration 452](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/migrations/452_driver_session_generation.sql)
- [Auth runtime](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/routes/auth.py)
- [Refresh token lifecycle](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/backend/utils/refresh_tokens.py)
- [Mobile dashboard](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/driver-app/hooks/useDriverDashboard.ts)
- [GO/STOP eligibility](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/driver-app/components/dashboard/DriverIdlePanel.tsx)
- [Native background tracking](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/driver-app/utils/backgroundLocation.ts)
- [Stored offer expiry](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/driver-app/services/pendingRideOffer.ts)
- [Shared auth store](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/shared/store/authStore.ts)
- [Background auth](https://github.com/srikumarimuddana-lab/spinrvm/blob/4fcb9d7703b96bc65b779e73169f8b583ce421c5/driver-app/utils/backgroundAuth.ts)

