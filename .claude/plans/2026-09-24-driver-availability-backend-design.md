<!--
Provenance: architect design pass for PR #5727's remaining backend work
(Task 3 close-out, F-series fixes to the published Tasks 1-3, Tasks 4, 5, 6 and
the backend half of Task 12), produced 2026-09-24 against commit fb1777b by a
read-only architect agent. Master spec: docs/superpowers/plans/2026-09-23-driver-availability.md.
Mobile counterpart: .claude/plans/2026-09-24-driver-availability-mobile-auth-design.md.
Where this file and .claude/plans/2026-09-24-driver-availability-contract-decisions.md
disagree, the contract-decisions file wins.
-->

# PR #5727: backend architecture for the remaining work (T3 close-out, T4, T5, T6, T12-backend)

Every file:line below is from HEAD fb1777b. The work goes in this order: **F-series fixes, then T4 (458), T5 (459), T6 (460), T12 (461).** Each commit changes at most 3 files and about 200 lines. Put each commit's Change Impact & Risk entry in the PR body.

---

## 1. Findings

### 1.1 Existing code to reuse (do not duplicate)
- **448 claim identity.** Columns `drivers.availability_claim_id/availability_claimed_at` and `ride_offers.claim_id`. Triggers: `ride_offers_0_validate_claim_identity` (448:67-91; it locks the driver row when a pending offer is inserted), `driver_insurance_periods_stamp_claim_id` (448:39-61) and `drivers_clear_availability_claim_on_release` (448:99-115). Reapers: `reap_stale_driver_claim_v2` / `_legacy_` (448:228-408). The best-effort Period-2 sub-block pattern is at 448:640-648.
- **442.** `record_insurance_period_transition` locks the ride `FOR UPDATE` for Period 2 (442:13-99). The trigger `ride_offers_pending_ride_searching` also locks the ride (442:108-131).
- **457 (this PR).** T1 `transition_driver_availability` (457:48-236), `renew_driver_presence` (241-347), `update_live_driver_marker_fenced` (352-392), `get_driver_availability_snapshot` (397-448), and the `driver_availability_requests` idempotency table (29-46).
- **Python modules:**
  - `utils/driver_presence.scoped_driver_presence_evidence` (386-444): returns the evidence dict plus a reachable flag.
  - `driver_availability_repo`, `driver_presence_repo`, `driver_availability_service`.
  - `insurance_periods.release_driver_and_close_period / close_period_after_release` (272 / 343).
  - `process_expired_offer` (matching.py:1774) and `_batch_offer_timeout_handler` (1935).
  - `offer_expiry_reaper` (67-158).
  - `features._build_fcm_message` (1318) and `send_dispatch_offer_pushes_batch` (1480); `push_retry._process_row / _send_fcm_push` (133 / 211).
  - `core/background_loop_registry.LOOP_CATALOG`: watchdog names are derived from it, and `test_lifespan_watchdog_coverage.py` checks coverage by parsing the code.
- **Claim call sites (complete list).**
  - Automatic claims happen only in the matching.py claim phase: the PostgREST loop via `claim_driver_atomic` (1172-1247) and the direct pool via `dispatch_pool.claim_batch` (1040-1171).
  - Scheduled, retry, cascade, decline re-dispatch and timeout re-dispatch all go through `match_driver_to_ride`.
  - Admin direct assignment is admin/rides.py:1287-1367. It calls `set_driver_available(driver, False)` unconditionally, then records Period 2 and starts `_offer_timeout_handler`.
  - Dead or test-only, leave untouched: `DispatchService.find_candidate_drivers / claim_driver / claim_any_driver` (dispatch_service.py:412-667), `driver_repo.claim_ride_atomic` (385), `match_and_claim_driver` (245).

### 1.2 Flaws in the published T1–T3 code to fix first
457 has not been applied anywhere and is not merged (PR body), so **amend it in place**. If anyone has applied 457 to a shared database, stop and escalate instead.

- **F1 — structured 503 codes never reach the client.**
  - `http_exception_handler` replaces every non-string 5xx detail with a generic sentence (error_handling.py:49-65, 796-806).
  - So `{"code":"PRESENCE_UNAVAILABLE"}` (location.py:76, 170), `ELIGIBILITY_UNAVAILABLE` (status.py:58-61, 80-83, 189-192) and `SESSION_AUTHORITY_UNAVAILABLE` (location.py:103) all go out as that sentence.
  - The tests only check the raised exception (test_driver_availability_snapshot.py:267-291), not what goes over the wire.
  - **Fix:** pass a 5xx dict through only when all of these hold:
    - its keys are all in {code, reason_code, online_epoch, state_version, retry_after_ms};
    - `code` and `reason_code` match `^[A-Z][A-Z0-9_]{2,63}$`;
    - epoch and version match `^[0-9]{1,20}$`;
    - `retry_after_ms` is an int.
  - Emit only those keys. Everything else is still sanitized, so test_error_response_sanitisation.py:73-81 keeps passing.
- **F2 — gaps in T1's authority rules.**
  - **(a) The system cannot pause a driver when the current session differs from the controller or is NULL.** T1 requires the caller's session to be current (457:101-104) and, for the idle/unreachable/missed-offer pauses, to equal the controller (457:119-125). `pause_driver_for_policy` borrows `users.current_session_id` (service:336-347) and returns SESSION_RECONCILE_REQUIRED when it is NULL; the admin suspend/ban/reject route (admin/drivers.py in this PR's diff) then returns 503 after the status change is already saved. T5, T12 and stale-intent all need a trusted system actor.
  - **(b) After any re-login, a new session can never go online.** `go_online` from a session that is not the controller returns CONTROLLER_SESSION_MISMATCH (457:119-125). Only `displace_controller` rebinds the controller, and nothing calls it.
  - **(c) A system action would stamp `last_contact_at`** (457:210). That field must mean device contact only.
  - **(d) Readiness is hard-coded and the snapshot contradicts dispatch.** `ready_until` is hard-coded to +62 min (457:211). The snapshot reports `paused/READY_TIMEOUT` whenever v2 is on and `ready_until` has passed (service:206-210), but nothing enforces or refreshes readiness yet. So after 62 minutes the app says "paused" while offers keep arriving.
- **F3 — inconsistent session codes.** Command endpoints return the raw T1 codes UNAUTHORIZED_SESSION and CONTROLLER_SESSION_MISMATCH (status.py:89-106). The presence paths map the same cases to SESSION_SUPERSEDED (driver_presence.py:238-245).
- **F5 — v2 batch conflicts are unstructured.** Two v2 batch 409s are plain strings: the idle "Driver is not online" (location.py:499-500) and the trip "Ride cannot accept location points…" (617-618). The app cannot classify them, which breaks the "no blanket 409 drain" contract.
- **F7 — WebSocket epoch binding is expensive and fragile.** The WS handshake binds the epoch through the full eligibility snapshot (websocket.py:846-857), which reads service areas, driver documents and Redis on every connect. Any read error leaves the socket unbound (859-863).
- **Optional:**
  - **F4:** v2 flag reads fetch the whole settings row, secrets included, on every location, status and WS request (location.py:73, status.py:55, websocket.py:842).
  - **F6:** `KNOWN_KEY_PREFIXES` (redis_client.py:477-495) lacks `spinr:presence:v2:driver:`.

---

## 2. Presence writer/reader inventory (final Task 3)

| Location | Operation | Classification | Action |
|---|---|---|---|
| driver_presence.py:152/172/181 | `renew_ws_presence`, `renew_ws_batch_location`, `clear_ws_presence` | Fenced under v2 (exact scoped key; legacy clear only when `availability_v2` is false) | none |
| driver_presence.py:216 `renew_driver_presence` → `_merge_scoped_presence` (190) | scoped Lua write | fenced | F2-5 adds code mapping |
| driver_presence.py:285 `clear_scoped_driver_presence` | exact-key delete | fenced | none |
| driver_presence.py:293 `mark_present`, 315 `clear_presence` (plus H3 purge) | legacy key | legacy only | none |
| location.py:288, 503, 550, 1007, 1162, 1174 | scoped renew | fenced | F2-6 adds READINESS_EXPIRED mapping |
| location.py:372, 570, 701, 1017, 1325 | `mark_present` | legacy only (each guarded by `not availability_v2`) | none |
| location.py:792 | nearby-map reader (scoped under v2) | read only; falls back to DB state when Redis is unreachable (map only) | non-goal |
| status.py:137 renew, 165 clear_scoped | v2 go/offline | fenced | none |
| status.py:1222 `mark_present`, 1225 `clear_presence` | legacy branch (v2 returns at 344/965) | legacy only | none |
| websocket.py:549, 920, 1064, 1115, 1364, 1413 | scoped | fenced | F7 (binding) |
| websocket.py:932, 1076, 1167, 1447 | `mark_present` | legacy only | none |
| document_expiry.py:311; spinr_pass.py:647; subscriptions.py:1846 | `clear_presence` | legacy only (v2 paths return or continue first) | none |
| **matching.py:1696** (`_offer_timeout_handler`, raw `is_online=false` at 1691) | clear plus raw offline | **needs change** | T5-9 |
| **matching.py:1907** (`process_expired_offer`, raw offline at 1898-1906) | clear plus raw offline | **needs change** | T5-7 (replaced by RPC) |
| **matching.py:598 (primary), 879 (cascade)** | readers that fail open | **needs change** | T4-4b |
| **stale_intent_reconciler.py:148, 195** (raw flip at 210-228) | reader plus raw offline | **needs change** | T12-7 |
| **auth.py:2396** (`_offline_driver_for_logout_all`, raw offline + Period 0) | not gated | needs change; **owned by T11** | Interim safe: v2 readers ignore the legacy key and the claim RPC rechecks `is_online` |
| dispatch_service.py:504 (dead in production), estimates.py:116, surge_engine.py:136/174, admin/monitoring.py:183, diagnose_nearby_drivers.py:129 | readers (scoped under v2 via the central reader) | not dispatch paths | none (non-goal) |
| driver_availability_service.py:75 | snapshot scoped read | fenced | none |
| presence_sweeper.py | retired, not spawned | — | none |
| H3 purge (`on_driver_offline` only via `clear_presence`) | not triggered by v2 offline | H3 provider is dormant; reconciler rebuilds from the DB | note only |

Other raw availability writers that are safe under v2 because the claim RPC rechecks: users.py:420 (account deletion), driver_claim_reaper (release does not look at `accepting_requests`), admin/rides.py:1289 (replaced in T4-6).

---

## 3. Lock order and flag matrix

**Rule.** Waiting locks are always taken in this order: `drivers` (ascending id) → `rides` (ascending id) → `ride_offers` (ascending id) → the open `driver_insurance_periods` row. No path takes a *waiting* driver lock after a ride lock. The one deliberate exception is the automatic v3 claim: it takes candidate driver locks with `FOR UPDATE SKIP LOCKED` in rank order. A lock that never waits cannot join a deadlock cycle, and all of them are taken before the ride lock (403/448 already rely on this). Other drivers' claims are released in separate transactions.

| Path | Lock sequence | Flag off | v2 on | v2 + readiness |
|---|---|---|---|---|
| Automatic claim | v3: drivers SKIP LOCKED → ride → offer insert (triggers re-enter held locks) → period | legacy: `claim_driver_atomic` or direct-pool batch, unchanged | `dispatch_claim_offers_v3` | same, plus the READINESS_EXPIRED check via seam |
| Admin direct | driver (waits) → ride → period | `set_driver_available(False)`, unchanged | v3 `admin_direct` | readiness bypassed (documented override) |
| Accept (batch) | acceptor driver → ride → all offers of the ride (id order) → period. Losers released afterwards, each via `cancel_unaccepted`: that driver → ride → offer | legacy ride CAS | `resolve_driver_offer('accept')` | also refreshes `ready_until` |
| Decline / expire / cancel_unaccepted | driver → ride → offer → period (→ T1 inline: driver lock already held, no new rides locked) | legacy | RPC | decline refreshes readiness |
| T1 transitions | driver → rides (id) → offers (ride_id) → period | `AVAILABILITY_V2_DISABLED` | as in 457 | window from seam |
| Heartbeat renew | driver → (T1 inline) | disabled | contact gap → `pause_unreachable` | + expired readiness → `pause_idle` |
| Fenced marker write | driver → users | disabled | as in 457 | same |
| Receipts | none (insert only) | route rejects legacy offers | recorded | same |
| confirm_ready / reconcile / finalize | driver (reconcile uses SKIP LOCKED) → T1 | `AVAILABILITY_V2_DISABLED` / no-op | active | active |
| Rider cancel | ride CAS (own transaction) → bulk offer UPDATE (own transaction) → per offer RPC: driver → ride → offer | legacy release RPC | RPC `cancel_unaccepted` | same |
| Claim reaper (448) | driver → period | unchanged | unchanged | unchanged |

---

## 4. Designs

### 4.0 Rules for all new SQL (enforced by CI, or needed by the test fixture)
- **CHECK E** fails on `ALTER TABLE…NOT NULL` when both are on the same line. Put `ADD COLUMN … NOT NULL DEFAULT …` on the line after `ALTER TABLE` (as 457 does). Never write `DELETE FROM`, `DROP INDEX` or `TRUNCATE`, even inside function bodies.
- **CHECK C:** a new table needs RLS plus a `CREATE POLICY`, or a `-- service-role-only` marker comment. **CHECK D:** a `-- Rollback:` comment. **CHECK G:** add `-- migration-override-ok: <reason>` to 460 and 461, which replace functions from 457/459.
- **test_admin_secdef_fn_revokes.py:** every `CREATE OR REPLACE` of a SECURITY DEFINER function must be followed *in the same file* by `REVOKE ALL ON FUNCTION … FROM PUBLIC, anon, authenticated;`.
  - Public RPCs: `SECURITY DEFINER`, `SET search_path = pg_catalog, public`, `SET lock_timeout = '2s'`, `GRANT EXECUTE … TO service_role`.
  - Private helpers and seams: `SECURITY INVOKER`, the same search_path, and REVOKE from `PUBLIC, anon, authenticated, service_role`.
- **Idempotency.** The direct_pool fixture re-applies each migration for every test, so every statement must be safe to repeat:
  - `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `CREATE OR REPLACE`;
  - `DROP POLICY IF EXISTS` followed by `CREATE POLICY`;
  - constraints added through a `pg_constraint` lookup inside a `DO` block, using `NOT VALID`.
- **Transactions.** Use `BEGIN; SET LOCAL lock_timeout='2s'; SET LOCAL statement_timeout='20s'; … COMMIT;`. Any `CREATE INDEX CONCURRENTLY IF NOT EXISTS` goes after `COMMIT`. End with `NOTIFY pgrst, 'reload schema';`.
- **PG15 compatibility** (CI runs postgres:15): no `IS JSON`, `JSON_OBJECT()`, `ANY_VALUE`, `pg_input_is_valid`, or PG16 numeric literals.

### 4.F The F-series fixes

**F2 SQL (amend 457):**
- **System actor.** `v_system := p_authenticated_session_id LIKE 'system:%'`.
  - A system actor may only run stop_requests, pause_policy, pause_unreachable, pause_idle or pause_misses; anything else raises `22023`.
  - For a system actor, skip the current-session check (101-104) and the controller check (119-125).
- **Controller rebind.** Change the mismatch guard to also exclude `(p_action='go_online' AND NOT v_driver.is_online)`, and bind with `IF p_action='go_online' AND (controller IS NULL OR NOT v_driver.is_online) THEN controller := p_session`. Taking over from a session that is still online remains T11 `displace_controller`.
- **Contact stamp.** Set `last_contact_at` only `WHEN p_action IN (…) AND NOT v_system`.
- **Seams**, each `LANGUAGE sql STABLE SECURITY INVOKER`, revoked from everyone:
  - `driver_ready_window()` → `interval '62 minutes'`;
  - `driver_readiness_prompt_lead()` → `interval '2 minutes'`;
  - `driver_readiness_enforced()` → `false`.
- **Uses of the seams:**
  - T1 go_online: `ready_until = clock_timestamp() + public.driver_ready_window()`.
  - Snapshot adds `'readiness_enforced', public.driver_readiness_enforced()` and `'readiness_prompt_at', d.ready_until - public.driver_readiness_prompt_lead()`.
  - `renew_driver_presence`, after the contact-gap block: if `driver_readiness_enforced() AND accepting_requests AND is_available AND (ready_until IS NULL OR ready_until <= v_now)`, call T1 with (presented session, `'pause_idle'`, `'readiness-expired:'||epoch`) and return `{status:'stale_epoch', code:'READINESS_EXPIRED', online_epoch, state_version}`. A non-OK T1 result returns `{status:'unavailable', code:'READINESS_RECONCILIATION_FAILED'}`.

**Python:**
- `driver_availability_repo.system_actor(source)`: allowed sources are {policy, contact_gap, readiness, missed_offers, finalize, stale_intent}; returns `"system:"+source`.
- `change_driver_availability` returns SESSION_RECONCILE_REQUIRED for any token session starting with `system:`.
- `pause_driver_for_policy` uses `system_actor("policy")` and no longer reads `users`.
- The snapshot evaluates READY_TIMEOUT only when `raw["readiness_enforced"]` is true, and exposes `readiness_enforced` and `readiness_prompt_at`.
- `driver_presence.renew_driver_presence` maps READINESS_EXPIRED to itself. `_presence_conflict` treats it like CONTACT_GAP: 409 `{"code":"ONLINE_EPOCH_STALE","reason_code":"READINESS_EXPIRED","online_epoch"}`.

| # | Files | Tests / verification |
|---|---|---|
| F1 | utils/error_handling.py, tests/test_error_response_sanitisation.py | allowed dict passes; `{"err":…}` and dicts carrying `message` are sanitized. `ruff`, then CI |
| F2-1 | 457 (T1: system actor, rebind, contact stamp), tests/direct_pool/test_driver_availability_epoch.py | system pause works when current session ≠ controller; system go_online raises; offline re-login binds a new controller; online re-login still MISMATCH; system stop leaves `last_contact_at` alone. Scratch DB + CI |
| F2-2 | repositories/driver_availability_repo.py, services/driver_availability_service.py, tests/test_driver_availability_snapshot.py | policy pause needs no `users` lookup; `system:` token rejected |
| F2-3 | 457 (3 seams, T1 window, snapshot fields), direct_pool epoch test | seams exist and are revoked; ready_until is about now + 62 min; snapshot keys present |
| F2-4 | driver_availability_service.py, test_driver_availability_snapshot.py | no READY_TIMEOUT when `readiness_enforced` is false |
| F2-5 | 457 (renew readiness branch), utils/driver_presence.py, tests/direct_pool/test_driver_availability_presence_epoch.py | in the test, `CREATE OR REPLACE` the seam to return true; expired and idle → paused with a new epoch; obligated driver not paused |
| F2-6 | routes/drivers/location.py (`_presence_conflict`), tests/test_live_location.py | 409 shape |
| F3 | routes/drivers/status.py (`_finish_v2_status`: both session codes → `SESSION_SUPERSEDED` plus `reason_code`), test_driver_availability_snapshot.py | mapped codes |
| F5 | location.py (v2 only: 499 → `{"code":"DRIVER_OFFLINE","online_epoch"}`, 617 → `{"code":"RIDE_STATE_CONFLICT","ride_status"}`), tests/test_location_batch.py, tests/test_idle_location_batch.py | flag-off strings unchanged |
| F7 | routes/websocket.py (bind using `driver_availability_repo.get_driver_availability_snapshot`: `{"is_online": d["is_online"], "online_epoch": str(d["online_epoch"])}`), tests/test_websocket_live_location.py | no eligibility reads on connect |

F4 and F6 are optional single-purpose commits.

### 4.1 T4 — dispatch admission and atomic claim (migration 458_driver_claim_epoch_fence.sql)

**Schema changes:**
- `ALTER TABLE public.ride_offers` adds `online_epoch bigint` and `controller_session_id text` (both nullable). These record the epoch and the session the offer is addressed to.
- `ALTER TABLE public.settings` adds `dispatch_admission_shadow_enabled boolean NOT NULL DEFAULT false` (NOT NULL on the next line).

**Private predicate.**
`driver_offer_admission_reason(p_driver public.drivers, p_session_id text, p_online_epoch bigint, p_contact_valid_until timestamptz, p_location_valid_until timestamptz, p_now timestamptz, p_mode text, p_ride_id text, p_require_subscription boolean) RETURNS text` (INVOKER, STABLE). It returns the first failing reason, in this order:

1. `DRIVER_OFFLINE` — `NOT is_online`.
2. `SESSION_SUPERSEDED` — the controller is NULL, or it differs from `COALESCE(p_session_id, controller)`, or the user's current session is not that session.
3. `ONLINE_EPOCH_STALE` — automatic mode and `online_epoch <> p_online_epoch`.
4. `REQUESTS_PAUSED` — `NOT accepting_requests`.
5. `ELIGIBILITY_BLOCKED` — `status <> 'active' OR NOT is_verified`.
6. `OBLIGATION_CONFLICT` — any of: `NOT is_available`, `availability_claim_id IS NOT NULL`, a ride in (driver_assigned..in_progress) with `id <> p_ride_id`, or a pending/accepted offer on a live ride with `ride_id <> p_ride_id`.

Automatic mode only:

7. `PRESENCE_UNAVAILABLE` — contact_valid_until is NULL, or `<= p_now`, or `> p_now + 95s`; or `last_contact_at` is NULL or `<= p_now - 120s` (a 90 s lease plus 30 s write coalescing).
8. `LOCATION_STALE` — location_valid_until is NULL or `<= p_now`; or `location_captured_at` is NULL, older than `p_now - 60s`, or later than `p_now + 5s`.
9. `READINESS_EXPIRED` — `driver_readiness_enforced()` and (`ready_until` is NULL or `<= p_now`).
10. `ENTITLEMENT_BLOCKED` — `p_require_subscription` and no `driver_subscriptions` row with `status='active'` and (`expires_at` NULL or `> p_now`).

Document expiry is covered by the `status` check (the sweep and the accept-time check move status). Do not re-check the legacy expiry columns: they can be stale and would wrongly block drivers whose documents were re-approved.

**The RPC.** `dispatch_claim_offers_v3(p_ride_id text, p_candidates jsonb, p_max_offers integer, p_offer_ttl_seconds integer, p_require_subscription boolean DEFAULT false, p_mode text DEFAULT 'automatic') RETURNS jsonb`. DEFINER; `lock_timeout 2s`.

Validation (raises `22023`):
- ride id is non-empty;
- mode is `automatic` or `admin_direct`;
- max_offers is 1..10; ttl is 5..120 seconds;
- candidates is a JSON array with at most 50 entries;
- `admin_direct` requires exactly one candidate and max_offers = 1.

If the v2 flag is off → `{code:'AVAILABILITY_V2_DISABLED', results:[]}`.

1. **Unlocked ride pre-check.** Automatic requires `searching` with `driver_id IS NULL`. Admin requires `driver_assigned` with `driver_id = candidate`. Otherwise return `RIDE_STATE_CONFLICT` with `ride_status`.
2. **Phase 1 — driver locks, before any ride lock.** Loop over `jsonb_array_elements(p_candidates) WITH ORDINALITY` and stop once the number of locked eligible drivers reaches `p_max_offers`. For each candidate:
   - Parse `driver_id`, and in automatic mode `session_id`, `online_epoch` (must match `^[0-9]{1,19}$`), `contact_valid_until` and `location_valid_until` (ISO timestamps inside a `BEGIN…EXCEPTION` block), and `eta_seconds` (≥0).
   - A parse failure appends `INVALID_EVIDENCE`. Parse before locking.
   - Lock: `SELECT d.* … FOR UPDATE SKIP LOCKED` in automatic mode; plain `FOR UPDATE` (waits) in admin mode. Not found → `CLAIM_CONTENDED` (automatic) or `DRIVER_NOT_FOUND` (admin).
   - `v_now := clock_timestamp()` after the lock, then run the predicate. A failure appends `{driver_id, claimed:false, reason_code}`; the lock is kept until commit, which is harmless.
3. **Phase 2 — ride lock.** `SELECT … FROM rides WHERE id=p_ride_id FOR UPDATE`. Re-check the Step 1 conditions; on failure, mark every locked candidate `RIDE_STATE_CONFLICT` and return.
4. **Timestamps.** `v_offer_now := clock_timestamp()`; `v_expires := v_offer_now + make_interval(secs => p_offer_ttl_seconds)`.
5. **Phase 3 — claim, in rank order.** For each locked candidate:
   - Re-read the drivers row and **re-run the predicate at `v_offer_now`**, so a long wait for the ride lock cannot commit a lease that has expired.
   - Then `RIDER_OWNED_DRIVER` if `rides.rider_id = driver.user_id`; in automatic mode `ALREADY_OFFERED` if any offer exists for (ride, driver).
   - Claim: `UPDATE drivers SET is_available=false, availability_claim_id=gen_random_uuid(), availability_claimed_at=v_offer_now RETURNING availability_claim_id`.
   - Automatic: `INSERT ride_offers(ride_id, driver_id, status='pending', eta_seconds, offered_at=v_offer_now, expires_at=v_expires, claim_id, online_epoch=driver.online_epoch, controller_session_id=driver.controller_session_id) RETURNING id`.
   - Admin: `UPDATE rides SET driver_notified_at=v_offer_now`; no offer row.
   - Period 2 exactly as 448:640-648: `BEGIN v_p := record_insurance_period_transition(driver, 2, ride); v_ins := status IN ('ok','noop'); EXCEPTION WHEN OTHERS THEN v_ins := false; RAISE WARNING …; END`.
   - Append `{driver_id, user_id, claimed:true, offer_id|null, claim_id, online_epoch:text, eta_seconds, insurance_written}`.
6. **Return** `{code:'OK', ride_id, ride_status, server_time:v_offer_now, offered_at, expires_at, claimed_count, results:[…]}`.

The claim always stamps the claim UUID, whatever `dispatch_claim_identity_enabled` says: turning on v2 already requires every replica to run v2-aware code. Revoke the predicate from everyone. Revoke v3 from PUBLIC, anon and authenticated, and grant it to service_role.

**Python:**
- `repositories/driver_offer_repo.py` (new): `async claim_offers(ride_id, candidates, *, max_offers, offer_ttl_seconds, require_subscription=False, mode="automatic") -> dict`.
  - Validate arguments, then `supabase.rpc("dispatch_claim_offers_v3", …)` with `run_sync(retry_policy="write")`.
  - A non-dict result raises `TypeError`.
  - On OK, invalidate the driver cache for every attempted driver.
- `services/dispatch_service.py`:
  - `async admit_candidates_v2(candidates) -> (admitted, outcome)`, where outcome is ok, none_present, store_unavailable or error. It calls `scoped_driver_presence_evidence`. It **never fails open**: an unreachable store or an exception returns `[]` and increments `spinr_dispatch_presence_filter_failed_total{mode="v2_closed"}`. Each admitted driver gets `_admission = {session_id, online_epoch:str, contact_valid_until, location_valid_until}` (ISO timestamps built from the ms fields).
  - `v3_claim_candidates(ranked) -> list[dict]`.
- `routes/rides/matching.py` (loguru module):
  - Set `_availability_v2 = bool(app_settings.get("driver_availability_v2_enabled"))` after line 345.
  - Under v2: add `accepting_requests: True` to `_dispatch_filter` (492-499) and `_casc_filter` (835-843). Replace the primary presence block (589-625) and the cascade presence lines (868-888) with `admit_candidates_v2`. The quota filter's `except` (790-794) now fails closed (`all_drivers = []`), because v2 removed the forced-offline backstop in spinr_pass.py.
  - In the claim phase, add a v3 branch before `if _direct_pool_enabled:` (1040):
    - OK → build `claimed_drivers` from the results and set `_v3_claimed = True`. Log ERROR and increment `spinr_insurance_period_write_failed_total{reason="claim_v3"}` when `insurance_written` is false. Increment `spinr_dispatch_claim_rejected_total{reason}` for each rejected candidate.
    - `AVAILABILITY_V2_DISABLED` → run the legacy branch with the same ranked list.
    - `RIDE_STATE_CONFLICT` → return.
    - Anything else → raise.
  - Skip the offer-insert (1258-1284) and insurance (1286-1311) phases when `_v3_claimed`.
  - Put the envelope (§5) into `dispatch_payload` (1433-1475). FCM picks it up automatically.
- `routes/admin/rides.py` (1287-1367), under v2 (read `get_app_settings()` earlier):
  - Replace `set_driver_available` plus the period write with `claim_offers(ride_id, [{"driver_id": X}], max_offers=1, offer_ttl_seconds=_admin_timeout+15, mode="admin_direct")`.
  - Claimed → send the WS/push with the envelope (`offer_id` null) and spawn `_offer_timeout_handler` as today.
  - Not claimed, or `RIDE_STATE_CONFLICT` → CAS the ride from `driver_assigned`/X to `searching` with `driver_id NULL`, spawn `match_driver_to_ride`, and add `"driver_assignment": {"assigned": false, "reason_code": …}` to the response (status "searching").
  - RPC exception → re-read the driver. If it holds a claim (`availability_claim_id` set, `is_available` false) and the ride is still assigned to it, treat it as claimed. Otherwise revert and re-dispatch.
  - `AVAILABILITY_V2_DISABLED` → legacy block.
- Snapshot entitlement, which subscriptions.py:1778 already promises: `_eligibility_reason` adds `SUBSCRIPTION_REQUIRED` (from area/parent `subscription_required` or the global `require_driver_subscription`, when there is no active unexpired pass; a failed lookup raises `AvailabilityLookupError`) and `QUOTA_EXHAUSTED` (`spinr_pass.quota_status`; a failed lookup is logged and ignored).
- Shadow mode (flag off plus `dispatch_admission_shadow_enabled`): after the legacy filter, compare with scoped evidence and increment `spinr_dispatch_admission_shadow_total{result=same|legacy_only|v2_only}`. No behaviour change.

| # | Files | Tests / verification |
|---|---|---|
| T4-1 | 458 part A (columns, predicate), tests/direct_pool/test_driver_claim_epoch_fence.py | each reason code (the test connects as postgres and can call the private function); predicate revoked from service_role; applying 458 twice is a no-op |
| T4-2 | 458 part B (v3), same test | v3 grants; 3 claims with the 448 triggers satisfied; Period 2 stamped with claim_id; stale epoch, superseded session, expired lease, stale GPS, readiness (seam replaced in the test), subscription, rider-owned, already-offered; `RIDE_STATE_CONFLICT`; admin mode; **races**: a held driver lock gives `CLAIM_CONTENDED`; a held ride lock followed by a concurrent T1 stop gives no deadlock and the stop sees the obligation; lease expiring during the ride-lock wait is rejected in Phase 3. Scratch DB, psql race harness, CI |
| T4-3 | repositories/driver_offer_repo.py, tests/test_driver_offer_repo.py | argument validation; non-dict raises `TypeError`; cache invalidation |
| T4-4a | services/dispatch_service.py, tests/test_dispatch_admission_v2.py | healthy-empty / unreachable / exception all return `[]` |
| T4-4b | matching.py (filters, primary, cascade, quota), tests/test_dispatch_presence_failopen.py, tests/test_dispatch_cascade.py | under v2, Redis empty or down → no claim and the 10 s retry is armed; flag-off cases unchanged |
| T4-5 | matching.py (claim phase and envelope), tests/test_dispatch_v3_claim.py | payload keys; `AVAILABILITY_V2_DISABLED` falls back; `RIDE_STATE_CONFLICT` returns; `test_dispatch_claim_parity.py` still passes with the flag off (CI) |
| T4-6 | admin/rides.py, tests/test_admin_ride_assignment_v2.py | claimed, rejected-then-revert, and lost-response cases |
| T4-7 | driver_availability_service.py, test_driver_availability_snapshot.py | SUBSCRIPTION_REQUIRED and QUOTA_EXHAUSTED shown as "blocked" |
| T4-8 | dispatch_service.py, matching.py, tests/test_dispatch_admission_shadow.py | metric only |

Every Python commit: `python3 -m py_compile`, `ruff check` and `ruff format --check` on the changed files (config: /ruff.toml). In loguru modules (matching.py, websocket.py, features.py, push_retry.py, cancellation.py) there must be no `exc_info=` or `extra=` and no `%s`. Then push and wait for "Run backend test suite" and "Driver Availability DB Tests" in CI.

### 4.2 T5 — atomic offer decisions (459_offer_decision_atomicity.sql)

**Schema changes:**
- New table `driver_offer_decisions` (`-- service-role-only`): `offer_id uuid REFERENCES ride_offers ON DELETE CASCADE`, `request_id text`, `action`, `claim_id`, `expected_epoch`, `actor_session_id`, `result jsonb`, `created_at`, primary key `(offer_id, request_id)`. RLS enabled; revoked from PUBLIC, anon and authenticated; SELECT and INSERT granted to service_role.
- `ride_offers` adds `outcome text` and `outcome_at timestamptz`. Add CHECK `ride_offers_outcome_check` NOT VALID, allowing: accepted, declined, preempted, cancelled, expired_nonresponse, expired_delivery_unknown, expired_availability_changed, expired_late_response.
- `drivers` adds `offer_miss_streak integer NOT NULL DEFAULT 0`, `offer_miss_streak_epoch bigint` and `offer_miss_streak_at timestamptz`. This replaces the Redis streak under v2; a streak from another epoch or older than 30 min counts as 0.

**Seam.** `offer_expiry_counts_as_miss(p_offer ride_offers, p_driver drivers) RETURNS boolean`. In 459 it keeps legacy behaviour: true when the driver's epoch equals the offer's epoch and its controller equals the offer's addressed session. 460 replaces it.

**Private helper `_finalize_deferred_availability_locked(p_driver drivers, p_request_id)`** (caller holds the driver lock):
- If `is_online AND NOT accepting_requests AND availability_reason IN (stop_requests, pause_policy, pause_unreachable, pause_idle, pause_misses)` and the driver has no obligation, call T1 with `(driver, online_epoch, 'system:finalize', availability_reason, p_request_id)`. T1 records Period 0 and bumps the epoch.
- Return `{finalized, availability}`.

**Private helper `_release_offer_claim_locked(p_driver_id, p_offer ride_offers, p_now, p_streak_mode 'reset'|'increment'|'keep', p_refresh_ready, p_miss_threshold)`** (caller holds driver, ride and offer):
- If `availability_claim_id IS DISTINCT FROM p_offer.claim_id` → `{released:false, reason:'claim_changed'}`.
- If there is another obligation → `{released:false, reason:'other_obligation'}`.
- Compute the streak. `v_pause := increment AND streak >= threshold AND online AND accepting`.
- Update the driver:
  - `is_available = is_online AND accepting_requests`; `availability_claim_id = NULL`; `availability_claimed_at = NULL`;
  - streak columns set according to the mode;
  - `ready_until = p_now + driver_ready_window()` only when `p_refresh_ready AND accepting AND online`;
  - `state_version + 1`.
- Then exactly one of:
  - `v_pause` → T1 `(…, 'system:missed_offers', 'pause_misses', 'miss-pause:'||offer_id)`, which records Period 0 and resets the streak;
  - finalize applies (`'finalize:'||offer_id`);
  - otherwise lock the open period `FOR UPDATE`, then `record_insurance_period_transition(driver, is_online ? 1 : 0, NULL)`. A status other than ok/noop raises `P0001`.

This gives **exactly one insurance transition** per decision.

**Public finalize.** `finalize_deferred_driver_availability(p_driver_id text, p_request_id text) RETURNS jsonb`: lock the driver, check the flag, call the helper.

**The RPC.** `resolve_driver_offer(p_offer_id uuid, p_claim_id uuid, p_expected_epoch bigint, p_actor_session_id text, p_action text, p_request_id text, p_miss_threshold integer DEFAULT 3) RETURNS jsonb`. DEFINER; `lock_timeout 2s`.

Validation:
- accept and decline need `epoch >= 0` and a session that does not start with `system:`;
- expire and cancel_unaccepted need epoch and session to be NULL;
- request_id is 1..128 characters;
- threshold is 1..20.

Steps:
1. Unlocked lookup of the offer's driver and ride (else `OFFER_NOT_FOUND`).
2. **Driver `FOR UPDATE`.**
3. Idempotency lookup. Same parameters → saved result plus `{replayed:true}`; different parameters → `IDEMPOTENCY_KEY_CONFLICT`.
4. Flag → `AVAILABILITY_V2_DISABLED`.
5. **Ride `FOR UPDATE`.** For accept only, then lock *all* offers of the ride `ORDER BY id FOR UPDATE`.
6. **Offer `FOR UPDATE`.** `v_now := clock_timestamp()`.
7. An offer missing claim, epoch or session → `OFFER_PROTOCOL_MISMATCH`. A different claim → `CLAIM_MISMATCH`.

**accept:**
- Offer already `accepted` with ride.driver = this driver and ride post-accept → OK `already_accepted`.
- Other terminal statuses (not saved): expired → `OFFER_EXPIRED`; preempted → `RIDE_STATE_CONFLICT/RIDE_TAKEN`; cancelled → `RIDE_STATE_CONFLICT/RIDE_CANCELLED`; declined → `OFFER_ALREADY_RESOLVED`.
- Session must equal the offer's session, the driver's controller and the user's current session, else `SESSION_SUPERSEDED`.
- **`v_now >= expires_at`** → expire now with outcome `expired_late_response` (no miss, streak kept) and return `OFFER_EXPIRED` with `{expires_at, server_time}`.
- Epoch must match both driver and offer, else `ONLINE_EPOCH_STALE`. Driver must be online, else `DRIVER_OFFLINE`. Ride must be searching with `driver_id` NULL, else `RIDE_STATE_CONFLICT`. Driver's claim must equal the offer's claim, else `CLAIM_MISMATCH`.
- Commit the accept:
  - `UPDATE rides SET status='driver_accepted', driver_id, driver_accepted_at=v_now, updated_at=v_now`;
  - winner offer: status accepted, outcome accepted;
  - losers: `UPDATE … SET status='preempted', outcome='preempted' WHERE ride_id=… AND id<>… AND status='pending' RETURNING id, driver_id, claim_id` → `losers[]`;
  - driver: streak = 0; `ready_until` refreshed if accepting; `state_version + 1`;
  - lock the period, then `record_insurance_period_transition(driver, 2, ride)`, which must be ok/noop (a safety net; normally noop).
- **Loser drivers are not locked here.**

**decline:**
- Offer not pending → if the claim is still held, release it (`keep`) and return `OFFER_ALREADY_RESOLVED{outcome, released}`.
- Session check as for accept. A stale epoch is allowed; readiness is refreshed only when the epoch is current.
- Set `declined` and release with `reset`.

**expire:**
- Offer not pending → idempotent release, `OFFER_ALREADY_RESOLVED`.
- `v_now < expires_at` → `OFFER_NOT_EXPIRED`.
- Outcome:
  - ride cancelled at or before `expires_at` → `cancelled`;
  - else seam true → `expired_nonresponse`;
  - else epoch or session changed → `expired_availability_changed`;
  - else `expired_delivery_unknown`.
- Set status expired (or cancelled) with the outcome; release with `increment` only for `expired_nonresponse`.

**cancel_unaccepted:**
- Pending offer on a ride that is still searching → `RIDE_STATE_CONFLICT/RIDE_STILL_SEARCHING`.
- Pending otherwise → set cancelled and release with `keep`.
- Already cancelled, preempted, expired or declined → set `outcome` only where it is NULL, then idempotent release. This is how post-accept losers and bulk-cancelled offers get released.

Persist a decision row for every mutating result (OK and the late `OFFER_EXPIRED`).

The result includes `code, action, offer_id, claim_id, ride_id, driver_id, driver_user_id, outcome, offer_status, online_epoch, offered_at, expires_at, server_time, ride_status, remaining_pending_offers, released, period, miss_counted, miss_streak, paused, availability, losers, already_accepted`.

**Python:**
- `driver_offer_repo`:
  - `resolve_offer(offer_id, claim_id, *, action, request_id, expected_epoch=None, actor_session_id=None, miss_threshold=3)` with `retry_policy="idempotent_write"`;
  - `finalize_deferred_availability(driver_id, *, request_id)`.
- `services/driver_offer_service.py` (new) handles orchestration and side effects:
  - `is_v2_offer(row)`: online_epoch, claim_id and id are all set.
  - `accept_offer_v2`: reads the offer row via `get_rows("ride_offers", {ride_id, driver_id}, columns="id,status,claim_id,online_epoch,controller_session_id,offered_at,expires_at")`. It returns None when the offer is legacy or v2 is disabled (the caller then uses the legacy path). Client values are validated and used when present; otherwise they default to the row's values.
  - `decline_offer_v2`: also does acceptance-rate(False), the audit row, the offer_skip key, and re-dispatch when `remaining_pending_offers==0 and ride_status=='searching'`.
  - `release_preempted_losers(losers, ride_id)`: calls `cancel_unaccepted` with request id `preempted:{offer_id}`, then sends a `ride_taken` WS.
  - `expire_offer_v2(offer_row, threshold)`: request id `expire:{offer_id}`; returns won = OK and not replayed. Side effects: `spinr_dispatch_offer_terminal_total{outcome}`, acceptance-rate(False) **only when a miss was counted**, the offer_skip key, and the WS (`ride_offer_expired{ride_id, offer_id, claim_id, outcome}` or, if paused, `availability_changed` plus `auto_offline{reason:"missed_offers", miss_count}`).
  - `release_cancelled_offer_v2(row)`: request id `cancel:{offer_id}`.
- `routes/drivers/offer_decisions.py` (new):
  - `is_v2_driver(driver)`: `controller_session_id` is a string;
  - `parse_decision_body(request)`: validates offer_id and claim_id as UUIDs, online_epoch as a decimal string, request_id up to 128 characters;
  - `decision_http_exception(result, snapshot=None)`: HTTP mapping per §5.
- **Protocol follows the offer.** v2 decisions apply only when the offer row has `online_epoch` (created by v3); every other offer stays on the legacy path. This means no extra settings reads, flag-off tests are untouched, and a flag flip in the middle of an offer is handled correctly.

**Call sites:**
- `ride_flow.accept_ride`:
  - add `request: Request=None` and `token_session_id=Depends(get_token_session_id)`; coerce a non-string session to None;
  - v2 driver offline at 85-90 → 409 `DRIVER_OFFLINE`;
  - in the batch branch (292-320), when `is_v2_driver`, call `accept_offer_v2`: None → legacy CAS (321-376) unchanged; a result → skip the CAS;
  - after accept, when v2, replace the winner/loser block (406-484) with `release_preempted_losers`, and take the offer→accept latency from the result.
- `decline_ride`: a v2 driver on a non-searching ride (602-608) gets a structured `RIDE_STATE_CONFLICT`; when `not is_assigned and is_v2_driver`, call `decline_offer_v2` before 653 and return early on a non-None result.
- `process_expired_offer(ride_id, driver_id, miss_threshold, *, offer=None)`: a v2 offer → `expire_offer_v2`. `_batch_offer_timeout_handler` selects `"driver_id,id,claim_id,online_epoch"` (1952-1961) and passes `offer=row`.
- `offer_expiry_reaper`: columns `"ride_id,driver_id,id,claim_id,online_epoch"` (76-82), and pass `offer=o`.
- `_offer_timeout_handler` (admin direct), when the v2 flag in settings is on: no Redis streak, no raw auto-offline (1676-1699); always `release_driver_and_close_period`.
- `cancellation.py` (684-735): for each returned row where `is_v2_offer`, call `release_cancelled_offer_v2`; otherwise the legacy release RPC.

| # | Files | Tests / verification |
|---|---|---|
| T5-1 | 459 part A, tests/direct_pool/test_offer_decision_atomicity.py | finalize for each deferrable reason; obligation blocks it; helpers revoked; applying twice is a no-op |
| T5-2 | 459 part B (resolve RPC), same test | **accept vs expire in both orders** (one session holds the lock via the RPC inside `BEGIN … pg_sleep … COMMIT`, a thread runs the other); accept exactly at the deadline → late expiry; at most one accepted, never both accepted and a miss; one open period per driver; losers preempted and their drivers not locked; lost-response replay; `IDEMPOTENCY_KEY_CONFLICT`; stale claim released after a newer offer (claim_changed); displaced-session accept; rider cancel vs accept; miss threshold crossed once and paused once; exactly one period row per decision |
| T5-3 | driver_offer_repo.py, tests/test_driver_offer_repo.py | wrapper argument checks |
| T5-4a | services/driver_offer_service.py, tests/test_driver_offer_service.py | side effects by outcome; replay does not repeat side effects |
| T5-4b | routes/drivers/offer_decisions.py, tests/test_offer_decision_routes.py | code → HTTP mapping; body parsing |
| T5-5 | ride_flow.py (accept), tests/test_offer_decision_routes.py | v2 and legacy branches; existing accept tests pass in CI |
| T5-6 | ride_flow.py (decline), same test | |
| T5-7 | matching.py (`process_expired_offer`, batch handler select), tests/test_matching_offer_decisions.py | |
| T5-8 | utils/offer_expiry_reaper.py, tests/test_offer_expiry_reaper.py | |
| T5-9 | matching.py (`_offer_timeout_handler`), tests/test_offer_timeout.py | |
| T5-10 | routes/rides/cancellation.py, tests/test_rider_cancel_offer_release_v2.py | |

### 4.3 T6 — delivery receipts and fair missed-offer counting (460_offer_delivery_receipts.sql)

**Receipts table.** `driver_offer_receipts` with:
- `offer_id uuid → ride_offers ON DELETE CASCADE`, `session_id text`;
- `event` in received|presented;
- `driver_id → drivers ON DELETE CASCADE`;
- `channel` in ws|push|stored|android_auto;
- `app_state` in foreground|background|inactive|unknown;
- `remaining_ms int` (±3,600,000);
- `received_at timestamptz DEFAULT clock_timestamp()`;
- **primary key `(offer_id, session_id, event)`**;
- index `(driver_id, received_at DESC)`.

RLS enabled. `REVOKE ALL FROM PUBLIC, anon, authenticated`; `GRANT SELECT TO authenticated`; `GRANT SELECT, INSERT TO service_role` (append-only). Policy `driver_offer_receipts_driver_select FOR SELECT TO authenticated USING (driver_id IN (SELECT d.id FROM public.drivers d WHERE d.user_id = auth.uid()::text))`.

**The RPC.** `record_offer_receipt(p_offer_id uuid, p_claim_id uuid, p_user_id text, p_session_id text, p_event text, p_channel text, p_app_state text, p_remaining_ms integer) RETURNS jsonb`. It takes no row locks.
- Validate the enums (else `INVALID_RECEIPT`). A `presented` event must have `app_state='foreground'`, else `INVALID_RECEIPT`.
- Join the offer to the driver. Missing, or `driver.user_id ≠ p_user_id` → `OFFER_NOT_FOUND`.
- Claim differs → `CLAIM_MISMATCH`. Offer's session is NULL or differs from the caller's → `SESSION_SUPERSEDED`.
- `INSERT … ON CONFLICT DO NOTHING`.
- Return `{code:'OK', recorded, offer_status, expires_at, server_time, late: now > expires_at}`.

**Seam replacement.** `offer_expiry_counts_as_miss` becomes true only when the epoch and session are unchanged **and** a `presented`, foreground receipt exists for the offer's addressed session with `received_at <= expires_at`. No receipt means delivery unknown, never a miss.

**Snapshot replacement.** Add `offer_id` (an alias of `id`), `claim_id` and `online_epoch::text` to `live_offer`, and keep the F2-3 readiness fields.

**Route.** `routes/drivers/offer_receipts.py`: `POST /offers/{offer_id}/receipts`, mounted as **`POST /api/v1/drivers/offers/{offer_id}/receipts`**. Uses `@ride_read_limit`, `get_current_user` and `get_token_session_id`; register it before `status` in `drivers/__init__.py:280-297`. Emits `spinr_dispatch_offer_receipt_total{event,channel}` and `spinr_dispatch_offer_delivery_duration_ms`.

**Push expiry (v2 only, keyed on `data.offer_protocol=="v2"`):**
- `_build_fcm_message` sets `AndroidConfig(ttl=timedelta(seconds=max(1, ceil(expires_at − now))))` and the APNs header `"apns-expiration": str(int(expires_at.timestamp()))`.
- `send_dispatch_offer_pushes_batch` drops pushes that have already expired (`spinr_dispatch_offer_push_skipped_total{reason="expired"}`) and never queues them for retry.
- `push_retry._process_row` deletes expired v2 dispatch rows without sending, and `_send_fcm_push` applies the same TTL.

**Stored channel.** `ride_reads.get_ride_offer` selects `"id,status,expires_at,claim_id,online_epoch"` (318-327) and adds the envelope when `online_epoch` is set. The snapshot's `safe_offer` allowlist (service:247-250) adds `offer_id`, `claim_id` and `online_epoch`.

| # | Files | Tests / verification |
|---|---|---|
| T6-1 | 460 part A (table, RLS, RPC), tests/direct_pool/test_driver_availability_offer_receipts.py | wrong driver → NOT_FOUND; wrong session; duplicates across channels; `has_table_privilege` for authenticated insert is false; policy exists |
| T6-2 | 460 part B (seam, snapshot, override annotation), same test | presented then expire twice → one miss; no receipt → delivery unknown; a presented receipt after the deadline does not count; decline and accept → no miss; threshold crossed once |
| T6-3 | driver_offer_repo.py, routes/drivers/offer_receipts.py (new), tests/test_offer_receipts.py | mapping and validation |
| T6-4 | routes/drivers/__init__.py, tests/test_offer_receipts.py | route mounted |
| T6-5 | features.py, tests/test_offer_notification_deadline.py | TTL and header; expired pushes skipped; legacy payloads unchanged |
| T6-6 | utils/push_retry.py, tests/test_push_retry_offer_expiry.py | |
| T6-7 | routes/drivers/ride_reads.py, services/driver_availability_service.py, tests/test_offer_envelope_stored.py | |

### 4.4 T12 backend — readiness (461_driver_readiness_policy.sql, with `migration-override-ok`)

**Schema changes:**
- `settings`:
  - `driver_readiness_policy_enabled boolean NOT NULL DEFAULT false`;
  - `driver_readiness_idle_minutes integer NOT NULL DEFAULT 60`, CHECK 15..240;
  - `driver_readiness_prompt_minutes integer NOT NULL DEFAULT 2`, CHECK 1..10.
- `drivers.readiness_prompt_sent_for timestamptz`: a claim flag holding the `ready_until` value that has already been prompted.
- The 5-minute contact gap stays a constant (already in 457). This is a deliberate decision.

**Seams replaced** (the bodies of 457, 458 and 459 do not change):
- `driver_ready_window()` → `COALESCE((SELECT make_interval(mins => idle + prompt) FROM settings WHERE id='app_settings'), interval '62 minutes')`;
- `driver_readiness_prompt_lead()` → `make_interval(mins => prompt)`;
- `driver_readiness_enforced()` → `COALESCE((SELECT driver_availability_v2_enabled AND driver_readiness_policy_enabled FROM settings WHERE id='app_settings'), false)`.

**`confirm_driver_ready(p_driver_id text, p_expected_epoch bigint, p_authenticated_session_id text, p_request_id text, p_reason text DEFAULT 'still_ready') RETURNS jsonb`.** Reasons are still_ready and trip_completed; `p_expected_epoch` may be NULL only for trip_completed.
- Lock the driver. Idempotency via `driver_availability_requests` with action `'confirm_ready:'||reason`. Check the flag.
- Session must be current and the controller, and must not start with `system:`, else `SESSION_SUPERSEDED`. Epoch mismatch → `ONLINE_EPOCH_STALE`. Offline → `DRIVER_OFFLINE`. Not accepting → `REQUESTS_PAUSED`.
- `still_ready` when enforced, idle and `ready_until <= now` → call T1 `pause_idle` with the caller's session and request id `readiness-expired:<epoch>`, then return `READINESS_EXPIRED` with the new epoch. A late confirmation cannot revive readiness.
- Otherwise set `ready_until = now + window`, clear `readiness_prompt_sent_for`, increment `state_version`, and return `{code:'OK', ready_until, online_epoch, state_version, server_time}`.

**`list_driver_availability_reconcile_candidates(p_limit integer) RETURNS jsonb`** uses the DB clock and returns up to `p_limit` of each:
- `contact_gap`: `is_online AND controller_session_id IS NOT NULL AND last_contact_at < now − 5 min`;
- `readiness_due` (when enforced): `is_online AND accepting_requests AND is_available AND ready_until <= now`;
- `prompt_due` (when enforced): the same idle predicate with `ready_until` in (now, now + lead] and `readiness_prompt_sent_for IS DISTINCT FROM ready_until`.

**`reconcile_driver_readiness(p_driver_id text, p_expected_epoch bigint, p_kind text, p_request_id text)`:**
- Lock the driver `FOR UPDATE SKIP LOCKED` (else `BUSY`). Check the flag. A different epoch → `ONLINE_EPOCH_STALE`.
- Re-check the kind's predicate with `clock_timestamp()`.
- Call T1 system `pause_unreachable` (contact_gap) or `pause_idle` (readiness). T1 keeps any trip and its insurance period.

**`claim_readiness_prompt(p_driver_id text, p_expected_epoch bigint, p_ready_until timestamptz)`:**
`UPDATE drivers SET readiness_prompt_sent_for = p_ready_until WHERE id=… AND online_epoch=… AND ready_until = p_ready_until AND readiness_prompt_sent_for IS DISTINCT FROM p_ready_until AND is_online AND accepting_requests AND is_available RETURNING user_id`.

**Indexes, after `COMMIT`:**
- `CREATE INDEX CONCURRENTLY IF NOT EXISTS drivers_availability_contact_due_idx ON drivers (last_contact_at) WHERE is_online AND controller_session_id IS NOT NULL`;
- `drivers_availability_ready_due_idx ON drivers (ready_until) WHERE is_online AND accepting_requests AND is_available`.

**Python:**
- `driver_availability_repo` wrappers: `confirm_driver_ready`, `list_availability_reconcile_candidates`, `reconcile_driver_readiness`, `claim_readiness_prompt`.
- Service `confirm_driver_ready(user_id, command, session)`: validates the command, then returns a fresh snapshot plus `code`.
- **`POST /api/v1/drivers/me/availability`** (status.py, next to GET, before the catch-all routes). Body `{"action":"confirm_ready","online_epoch":"<decimal>","request_id":"<=128"}`. Any other action returns 422 `INVALID_AVAILABILITY_COMMAND`: Go, Stop and Offline stay on `PUT /drivers/{id}/status`, which runs the eligibility gates.
- `utils/driver_readiness_reconciler.py`:
  - loop name `"driver_readiness_reconciler (20s)"`, catalog placement `api`;
  - Redis lock `spinr:driver_readiness_reconciler:lock`, TTL `int(20*0.85)`; if Redis errors, proceed anyway, because the database fences make duplicate runs safe;
  - heartbeat on every tick;
  - each tick runs the list, then the per-driver reconcile or prompt claim, using request ids `contact-gap:{d}:{epoch}` and `readiness:{d}:{epoch}`;
  - on a pause it sends WS `availability_changed` and a normal-priority push; on a claimed prompt it sends WS `availability_readiness_prompt` and a priority `"dispatch"` push;
  - if the settings or list call fails, skip the tick — never pause on guesswork.
- `stale_intent_reconciler` under v2:
  - candidates are `is_online AND last_contact_at < cutoff`, plus legacy rows with `last_contact_at IS NULL AND updated_at < cutoff`;
  - skip active rides; skip drivers with live scoped evidence;
  - call T1 `(epoch, system_actor("stale_intent"), "pause_unreachable", f"stale-intent:{d}:{epoch}")`;
  - send the push only when the result is offline. Flag off: unchanged.
- `insurance_periods.close_period_after_release`: when the released row has a string controller, `is_online`, and `accepting_requests is False`, call `finalize_deferred_availability` (request id `finalize:{d}:{epoch}`). If it finalized, return 0 without writing Period 1 (this closes the "stop requests" gap after a trip). NOOP or error → the legacy period write.
- `ride_complete.complete_ride` adds `token_session_id`. For a v2 driver, spawn `confirm_driver_ready(driver_id, None, session, f"trip-completed:{ride_id}", reason="trip_completed")`. Completions by the rider or an admin do not refresh readiness.

| # | Files | Tests / verification |
|---|---|---|
| T12-1 | 461 part A (settings, seams, confirm), tests/direct_pool/test_driver_availability_readiness.py | heartbeats every minute for 63 min: `ready_until` stays at start + 62 and the claim gives READINESS_EXPIRED at 63; confirm refreshes; a late confirm pauses; GPS and pongs never extend; auth untouched |
| T12-2 | 461 part B (list, reconcile, prompt, indexes), same test | duplicate workers (two threads); a stale worker after a newer Go gets EPOCH_STALE; active trip across the deadline is not paused; prompt claimed once; `EXPLAIN` uses the partial indexes |
| T12-3 | driver_availability_repo.py, driver_availability_service.py, tests/test_driver_readiness_command.py | |
| T12-4 | routes/drivers/status.py, tests/test_driver_readiness_command.py | |
| T12-5 | utils/driver_readiness_reconciler.py (new), tests/test_driver_readiness_reconciler.py | settings or Redis failure; replay |
| T12-6 | core/background_loop_registry.py (after `offer_expiry_reaper (10s)`), core/lifespan.py (`_spawn` after 707-717), tests/test_background_loop_registry.py | test_lifespan_watchdog_coverage passes in CI |
| T12-7 | utils/stale_intent_reconciler.py, tests/test_stale_intent_reconciler.py | |
| T12-8 | utils/insurance_periods.py, tests/test_insurance_release_helper.py | |
| T12-9 | routes/drivers/ride_complete.py, tests/test_ride_complete_readiness.py | |

---

## 5. Cross-team contracts (for the mobile architect)

### Offer envelope (v2 only; mark it with `"offer_protocol":"v2"`)
Keys: `offer_id` (uuid; **null or absent for admin-direct offers**), `claim_id` (uuid), `online_epoch` (decimal string, the epoch at claim time), `server_time` (RFC 3339 UTC, database time when the offer was made), `expires_at` (RFC 3339 UTC). `offer_expires_at` stays on the wire with the same value, and `countdown_seconds` stays too.

| Channel | Where | What changes |
|---|---|---|
| WS | `new_ride_assignment` (matching.py:1433; admin/rides.py:1311) | add the envelope keys |
| FCM | data built from the same payload minus `_FCM_EXCLUDE`, plus `deeplink` and `booking_id` | envelope keys as strings; TTL = remaining lifetime; `apns-expiration` set; expired offers never sent or retried |
| Stored | `GET /api/v1/drivers/rides/{ride_id}/offer` (envelope added); `GET /api/v1/drivers/me/availability` `pending_offer` = `{id, offer_id, ride_id, claim_id, online_epoch, offered_at, expires_at, ride_status}` | |

Other WS events:
- `ride_offer_expired` = `{ride_id, offer_id, claim_id, outcome}`;
- `ride_taken` = `{ride_id}`;
- `availability_changed` = `{online_epoch, state_version, reason_code, server_time}`;
- `availability_readiness_prompt` = `{online_epoch, ready_until, server_time}`;
- `auto_offline` keeps its current shape for missed-offer pauses.

The app must treat `expires_at` as absolute. Take the clock offset from fresh API responses (the receipt response's `server_time`), not from the envelope of a delayed push.

### Accept and decline
- `POST /api/v1/drivers/rides/{ride_id}/accept` and `…/decline` take an optional body `{"offer_id","claim_id","online_epoch","request_id"}`; decline also takes `reason`.
- Defaults when omitted: the server uses the offer row's values and `request_id = "{action}:{offer_id}:{session}"`.
- Success: accept returns `{"success":true,"offer_id","already_accepted"}`. Decline returns `{"success":true,"outcome","already_resolved"}` (it also returns 200 when the offer was already resolved).

### Receipts
`POST /api/v1/drivers/offers/{offer_id}/receipts`
- Body: `{"claim_id","event":"received"|"presented","channel":"ws"|"push"|"stored"|"android_auto","app_state":"foreground"|"background"|"inactive"|"unknown","remaining_ms":int}`.
- 200 response: `{"recorded":bool,"offer_status","expires_at","server_time","late":bool}`.
- Send `presented` only while foreground and interactive.

### Readiness
- Snapshot fields: `ready_until` (already exists), `readiness_enforced` (bool), `readiness_prompt_at` (RFC 3339 or null).
- Command: `POST /api/v1/drivers/me/availability` with `{"action":"confirm_ready","online_epoch","request_id"}` returns the snapshot plus `"code":"OK"`.

### Error shape
- New structured errors are `HTTPException` bodies: `{"success":false,"detail":{"code":…,…},"error":{"code":<http>,"message":<same>,"request_id","timestamp"}}`. **Read `detail.code`.**
- The existing eligibility gates still use the `SpinrException` shape `{"success":false,"error":{"code":<numeric>,"message","message_key","action_hint"}}`: suspended, documents, subscription 402, area 403, quota 403.

| Code | HTTP | Where | Exists at fb1777b? |
|---|---|---|---|
| DRIVER_OFFLINE | 409 (+online_epoch) | live location ✓; batch (F5); accept/decline v2 (T5) | partly |
| ONLINE_EPOCH_STALE (reason_code ONLINE_EPOCH_STALE / CONTACT_GAP / READINESS_EXPIRED) | 409 | location ✓ (READINESS_EXPIRED via F2-6), status ✓, accept (T5) | partly |
| SESSION_SUPERSEDED (+reason_code) | 409 | location and WS ✓; status (F3); accept, decline, receipts, confirm | partly |
| PRESENCE_UNAVAILABLE | 503 | location ✓ (**not delivered to the client until F1**) | yes |
| ELIGIBILITY_UNAVAILABLE | 503 | status ✓ (needs F1); accept/decline RPC failure | yes |
| OFFER_EXPIRED | 409 `{offer_id, expires_at, server_time, availability?}` | accept | T5 |
| RIDE_STATE_CONFLICT (reason_code RIDE_TAKEN / RIDE_CANCELLED / RIDE_NOT_SEARCHING) | 409 (+ride_status) | accept, decline (T5); batch (F5) | new |
| OFFER_NOT_FOUND 404; OFFER_MISMATCH, CLAIM_MISMATCH, OFFER_ALREADY_RESOLVED, IDEMPOTENCY_KEY_CONFLICT, SESSION_RECONCILE_REQUIRED, AVAILABILITY_V2_DISABLED 409; INVALID_RECEIPT 422 | | T5/T6 | new |
| READINESS_EXPIRED, REQUESTS_PAUSED 409 | | confirm_ready | new |
| Existing status/command codes: OBLIGATION_ACTIVE, ELIGIBILITY_BLOCKED (+reason_code), AVAILABILITY_UPGRADE_REQUIRED 409; INVALID_AVAILABILITY_COMMAND 422 | | | yes |

The snapshot's `reason_code` gains LOCATION_STALE, SUBSCRIPTION_REQUIRED and QUOTA_EXHAUSTED.

### Required app behaviour under v2
- Upload a fresh-GPS live location at least every ~30 s while idle online, **even when stationary**. After 60 s without one, the driver becomes LOCATION_STALE and is not dispatchable. A WS pong keeps contact alive but does not refresh GPS.
- A re-login while offline takes over availability on Go. A re-login while another session is online gets SESSION_SUPERSEDED/CONTROLLER_SESSION_MISMATCH until T11 adds displacement.

---

## 6. Risks, non-goals, decisions

**Risks:**
- There is no local pytest, so every Python commit is proven only in CI. SQL is proven on the local PG16 scratch database plus CI on PG15.
- The accept path (P95 under 2 s) gains one RPC but loses several legacy round trips.
- v2 dispatch fails closed: a Redis outage means no automatic offers. This is the intended trade-off; watch `spinr_dispatch_presence_filter_failed_total{mode="v2_closed"}`.
- The rider map and fare estimates still show database state during a Redis outage.
- Path selection reads cached settings (up to 60 s stale); the RPCs are authoritative and fall back safely.
- Rollback: a WS socket keeps the mode it bound at connect until it reconnects.

**Non-goals:**
- direct-pool transport for v3 (v2 always uses the PostgREST RPC);
- re-checking document expiry or Spinr Pass quota in SQL (status and entitlement are covered as above);
- Expo push TTL;
- H3 purge under v2;
- touching dead DispatchService methods;
- durable expiry for admin-direct offers (a legacy gap, #4598);
- the logout-all raw offline write (T11);
- retention purge for the new tables (they cascade with ride_offers).

**Defaults chosen (confirm only if you disagree):**
1. An admin-selected driver who is ineligible under v2: the ride reverts to searching and auto-dispatches, and the response carries `driver_assignment.reason_code`.
2. Acceptance rate drops only on a counted non-response.
3. Readiness copy is "Still available for ride requests?"; run the legal contractor-language check before enabling the flag.
4. Amend 457 in place.
5. Push TTL applies to v2 payloads only.

---

## 7. Developer environment (no PyPI; PG16 and Redis are local)

- **Scratch DB.** This mirrors the direct_pool conftest `_MIGRATION_FILES` plus the availability fixture. Run from the repo root:
  ```bash
  export PGHOST=localhost PGUSER=postgres; DB=avail_scratch; P="psql -v ON_ERROR_STOP=1 -q -d $DB"
  psql -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB TEMPLATE template0"
  $P -c "CREATE SCHEMA IF NOT EXISTS auth" -c "CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS \$\$ SELECT (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid \$\$"
  for r in anon authenticated; do psql -d postgres -c "DO \$\$ BEGIN CREATE ROLE $r NOLOGIN NOINHERIT; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$"; done
  psql -d postgres -c "DO \$\$ BEGIN CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END \$\$"
  python3 - <<'PY' | $P
  from pathlib import Path
  s = Path("backend/supabase_schema.sql").read_text()
  def block(t):
      a = s.index(f"CREATE TABLE IF NOT EXISTS {t} ("); d = 0
      for j in range(s.index("(", a), len(s)):
          d += {"(": 1, ")": -1}.get(s[j], 0)
          if d == 0: return s[a:s.index(";", j) + 1]
  print("\n".join(block(t) for t in ("users", "drivers", "rides", "settings")))
  print("INSERT INTO settings (id) VALUES ('app_settings') ON CONFLICT (id) DO NOTHING; CREATE TABLE service_areas (id text PRIMARY KEY);")
  PY
  for f in 100_batch_dispatch 131_ride_offers_preempted_status 143_ride_offers_cancelled_status 143_ride_offers_one_accepted_index 224_ride_offers_expires_at 64_driver_insurance_periods 253_insurance_period_transition_rpc 354_revoke_public_execute_on_security_definer_fns 12_driver_lifecycle_status 157_driver_availability_claimed_at 402_dispatch_claim_batch 403_dispatch_claim_batch_v2 421_insurance_period_ride_identity 442_release_cancelled_batch_offer 448_durable_dispatch_claim_identity; do $P -f backend/migrations/$f.sql; done
  $P -c "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()"
  for f in 42_drivers_last_status_changed_at 97_driver_intent_timestamps 457_driver_availability_epoch; do $P -f backend/migrations/$f.sql; done
  $P -c "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version integer NOT NULL DEFAULT 0" -c "ALTER TABLE drivers ADD COLUMN IF NOT EXISTS location_captured_at timestamptz" -c "CREATE TABLE IF NOT EXISTS driver_subscriptions (id text PRIMARY KEY DEFAULT md5(random()::text), driver_id text, status text, expires_at timestamptz)"
  for f in backend/migrations/4{58,59,60,61}_*.sql; do $P -f "$f"; $P -f "$f"; done   # the second apply proves idempotency
  $P -c "UPDATE settings SET driver_availability_v2_enabled=true WHERE id='app_settings'"
  ```
  Run each scenario as a SQL script against the scratch database. Check grants with `has_function_privilege('authenticated', '<sig>', 'EXECUTE')`.
- **Race harness.** Run session A in the background (`run_in_background`):
  `psql -d $DB -c "BEGIN" -c "SELECT resolve_driver_offer(...,'accept',...)" -c "SELECT pg_sleep(3)" -c "COMMIT"`.
  Then run session B in the foreground:
  `psql -d $DB -c "SET deadlock_timeout='100ms'" -c "SELECT pg_sleep(0.5)" -c "SELECT resolve_driver_offer(...,'expire',...)"`.
  B must block, then return the result that corresponds to A's committed outcome. Use `pg_sleep`, not shell `sleep`. Swap the roles to test the other ordering.
- **CI tests.**
  - Follow `availability_db` in test_driver_availability_epoch.py:8-28: a function-scoped fixture on `pg_cur` that applies 42, 97, 457 and later migrations plus the column stubs above.
  - For races, use two psycopg2 connections with threads, as in test_claim_batch.py:477-545 and test_atomic_driver_refund_holds.py:141-147.
  - The test file names match the CI globs: `test_driver_availability_*.py`, `test_driver_claim_epoch_fence.py`, `test_offer_decision_atomicity.py`.
  - The CI jobs to watch are "Driver Availability DB Tests", "Migration safety check" and "Run backend test suite".

### Critical Files for Implementation
- /home/user/spinrvm/backend/migrations/457_driver_availability_epoch.sql (F2 amendments) and the new 458–461 files
- /home/user/spinrvm/backend/routes/rides/matching.py
- /home/user/spinrvm/backend/routes/drivers/ride_flow.py
- /home/user/spinrvm/backend/services/driver_availability_service.py
- /home/user/spinrvm/backend/utils/error_handling.py

---

# Addendum A (2026-09-24): architect answers to mobile asks X1–X10, and must-fix F2(e)

Binding together with the contract-decisions file, which still wins where they conflict. The architect withdrew these earlier drafts:
- X4 via PUT status, and the field name `readiness_policy_enabled`;
- the X6 commit (F8);
- an opt-in `ERR_AUTH_UNAVAILABLE`;
- extra `auto_offline` fields;
- removing `controller_session_id`;
- `system:logout_all`.

## A1. Answers to X1–X10

### X1 — Offer envelope: accepted as C1
- `offer_id` is `ride_offers.id`, taken from v3's `INSERT … RETURNING id`. It is the same as `snapshot.pending_offer.id` and the new `pending_offer.offer_id` alias.
- WS `new_ride_assignment` and FCM data carry:
  - `offer_protocol:"v2"`, `offer_id`, `claim_id`;
  - `online_epoch` as a decimal string;
  - `server_time` (v3's returned DB time), `expires_at`, and `offer_expires_at` equal to `expires_at`;
  - `countdown_seconds`.
- FCM values are strings.
- Admin-direct offers have no offer row. `offer_id` is null on WS and absent on FCM. The app sends no receipts and uses the legacy accept-by-ride.
- The stored-offer GET and `pending_offer` also add `offer_protocol` and `server_time`, only when the row has `online_epoch` (T6-7).

### X2 — Accept and decline: accepted as C2
- Optional request body: `{offer_id, claim_id, online_epoch, request_id}`, plus `reason` for decline. Missing fields default to the offer row's values, and `request_id` defaults to `"{action}:{offer_id}:{session}"`.
- Success responses only gain keys:
  - accept → `{success:true, offer_id, already_accepted}`;
  - decline → `{success:true, outcome, already_resolved}`.
- Declining an offer that is no longer pending: the RPC releases the claim with `keep` and returns `OFFER_ALREADY_RESOLVED` plus the outcome. The HTTP response depends on the outcome:

| Outcome | HTTP response |
|---|---|
| `declined` | 200 with `already_resolved:true` |
| `expired_*` | 409 `OFFER_EXPIRED` |
| `preempted` | 409 `RIDE_STATE_CONFLICT` / `RIDE_TAKEN` |
| `cancelled` | 409 `RIDE_STATE_CONFLICT` / `RIDE_CANCELLED` |

- Accepting an offer that was already declined → 409 `OFFER_ALREADY_RESOLVED`.
- The 409 detail key is **`snapshot`**, built with `get_driver_availability(user_id, token_session_id)` after the RPC. On any exception, omit it and call `logger.error(..., exc_info=True)` (ride_flow uses stdlib logging).
- Legacy offers and flag-off keep the 403 "No active offer for this ride".

### X3 — Receipts: accepted with changes (C3)
- `app_state` is one of `active|background|inactive|unknown`. It replaces `foreground` in the 460 CHECK, the `presented` rule and the seam predicate.
- `presented` requires `active`, otherwise 422 `INVALID_RECEIPT`.
- `remaining_ms` accepts any JSON number, is converted with `int(round(x))`, and must be within ±3,600,000 (else 422).
- The primary key `(offer_id, session_id, event)` with `ON CONFLICT DO NOTHING` makes it idempotent. A duplicate returns 200 with `recorded:false`.

### X4 — Confirm ready: accepted with changes (C4)
- The command is `POST /api/v1/drivers/me/availability` with `{action:"confirm_ready", online_epoch, request_id}`. The snapshot fields are `readiness_enforced` and `readiness_prompt_at`.
- A successful confirm changes only `ready_until`, `readiness_prompt_sent_for` and `state_version`. It does not bump the epoch or reset the miss streak.
- A confirm after the deadline pauses the driver (`pause_idle`, epoch bump) and returns `READINESS_EXPIRED`.
- Check order in `confirm_driver_ready` (replaces the backend design's order):
  1. caller is not `users.current_session_id`, or is `system:` → `SESSION_SUPERSEDED`;
  2. driver offline → `DRIVER_OFFLINE`;
  3. controller is not the caller → `REQUESTS_PAUSED` with `reason_code:"CONTROLLER_SESSION_MISMATCH"`;
  4. epoch differs → `ONLINE_EPOCH_STALE`;
  5. not accepting → `REQUESTS_PAUSED`;
  6. readiness enforced, driver idle, and `ready_until <= clock_timestamp()` → pause, then `READINESS_EXPIRED`;
  7. otherwise → OK.

### X5 — Stop requests when a session ends: accepted (C8, C9; T11 backend)
New service function `stop_requests_for_session_end(user_id, *, cause: "logout"|"logout-all"|"superseded", ended_session_id)`. It returns `"stopped"`, `"skipped"`, `"legacy"` or `"failed"`. Steps:
1. Read the flag with `settings_loader.get_app_settings()`. A read error or a missing flag → `"legacy"`.
2. Read the driver with `driver_availability_repo.get_driver_availability_snapshot(user_id)`.
   - controller is NULL → `"legacy"`;
   - no driver, or not `is_online` → `"skipped"`;
   - cause is `logout` and the controller is not `ended_session_id` → `"skipped"`.
3. Call T1 with `(driver_id, epoch, "system:logout", "stop_requests", f"{cause}:{ended_session_id or 'all'}:{epoch}")`. The request id must be at most 128 characters.
   - `ONLINE_EPOCH_STALE` → re-read once, re-check step 2, and retry once.
   - OK → best-effort `clear_scoped_driver_presence(driver_id, controller, epoch)`, then `notify_availability_changed`.
   - Anything else, or an exception → log with `exc_info=True` and return `"failed"`.

What T1 then does:
- No obligation → the driver goes offline and Period 0 opens.
- An obligation → the driver stays online but not accepting; the trip and Period 2/3 are kept.
- A pending offer runs out as `expired_availability_changed` (the epoch has moved, so no miss is counted). T5's `system:finalize` completes offline later.

Wiring in auth (stdlib logger):
- **`logout()`**: at the top, for a driver that has a `token_session_id`, call it with `cause="logout"` inside try/except, before `revoke_refresh_token`. Logout must never fail because of it.
- **`_offline_driver_for_logout_all(user_id, *, cause="logout-all", ended_session_id=None)`**: call the service first and return unless it answered `"legacy"`. Under v2 with a controller, never run the raw offer declines or the raw offline + Period 0 writes, even on `"failed"`: they break epoch rules. The v3 predicate and the contact-gap reconciler protect a failed stop.
- **Re-login cleanup** in verify-otp, firebase and `_cleanup_superseded_session`: pass `cause="superseded", ended_session_id=str(previous_session_id)`.
- Keep exactly 5 `get_real_client_ip(request)` calls in auth.py. routes/admin/auth.py stays untouched.

### X6 — Rejected (C5)
- The 409 already carries the current epoch, and mobile §2.6 defers and reconciles.
- **Enablement gate:** builds without T9 send no epoch. Under v2 their live location gets a 409 for the whole trip, which freezes the rider's map marker; trip history is not affected. **Do not enable v2 until the forced-upgrade floor is at or above T9.**

### X7 — Accepted (C10)
- refresh_tokens.py `lookup_refresh_token` raises `DatabaseError(message="Could not verify session; please try again") from e`, matching the users lookup nearby.
- Not-found, revoked and expired still return None, so the anti-oracle rule is unchanged.
- The client receives a 503 with `error.code 9005`. The shared client already exempts `/auth/refresh` from its automatic 503 retry. Admin refresh also becomes 503; that is intended, and routes/admin/auth.py stays unchanged.
- Replace `test_lookup_refresh_token_db_error_returns_none` with `…_raises_503`, modelled on test_refresh_generation_binding.py.

### X8 — Accepted, default off (C10)
Needs a security audit before commit, and the flag is never enabled in this PR.

**Migration 462.** `ALTER TABLE public.settings` goes on one line and `ADD COLUMN IF NOT EXISTS refresh_successor_commitment_enabled boolean NOT NULL DEFAULT false;` on the next, inside `BEGIN; SET LOCAL lock_timeout='2s'; … COMMIT; NOTIFY pgrst, 'reload schema';`, with a `-- Rollback:` comment.

**`issue_refresh_token(..., raw: Optional[str] = None)`**:
- uses `raw` when given;
- on a UNIQUE conflict (`23505`) retries once with a server-generated token;
- never logs token data.

**`classify_committed_replay(parent_raw, proposed_raw) -> ("recover"|"dead"|"no_match", successor|None)`**:
- The parent is not revoked, or has no `replaced_by` → `no_match`.
- The successor's `token_hash` does not match sha256(proposed), compared with `hmac.compare_digest` → `no_match`.
- `recover` only when all of these hold:
  - same `user_id`;
  - audience is rider or driver;
  - successor not revoked and not expired;
  - same `token_version`;
  - `refresh_token_generation_matches(successor, user)`.
- Anything else → `dead`. DB errors raise `DatabaseError`.

**Route.**
- `RefreshRequest.proposed_refresh_token: Optional[str]`, ignored unless it matches `^[A-Za-z0-9_-]{64}$` and the flag is on. An unreadable flag counts as off.
- Classify before `lookup_refresh_token`:
  - **recover**: load the user and `_enforce_account_active`. Build the response through a shared helper extracted from the normal path. Return `refresh_token = proposed` and `refresh_expires_at = successor.expires_at`. No new row, no cascade. Increment `spinr_auth_refresh_recovered_total`.
  - **dead**: `TokenExpiredException` (401, code 1003), no cascade.
  - **no_match**: today's path, rotating with `issue_refresh_token(..., raw=proposed)`.
- Commits: 462 → 11-5b1 (tokens) → 11-5b2 (route) → 11-5b3 (docs/known-forks.md note: rider/driver only, by design).

### X9 — Accepted with changes (C4)
- `auto_offline` keeps its shape.
- After every successful, non-replayed v2 **system** transition, `notify_availability_changed` (F2-9) sends WS `availability_changed {online_epoch, state_version, reason_code, server_time}` to `driver_{user_id}`.
  - `reason_code` comes from `availability_reason_code(is_online, availability_reason)`, which is extracted from the snapshot mapping.
  - `server_time` comes from T1's new `server_time` result key.
- Emitters: policy pause, T5 expiry pause (still followed by `auto_offline`), the T12 reconciler, T12-7 stale-intent, T12-8 finalize, and the X5 stop. Commands the driver makes do not emit it.
- T12-2's candidate rows carry `{driver_id, user_id, online_epoch, ready_until}`.

### X10 — Rejected (C11)

## A2. Findings (a) and (b)

**(a) Confirmed and unintended.** With the flag off, an online driver shows `paused/REQUESTS_PAUSED` in the snapshot.
- Fixed in F2-4: `accepting = bool(driver.get("accepting_requests")) if raw.get("protocol_enabled") else is_online`.

**(b) Missed offers are counted exactly once under v2.**
- The move from pending to terminal happens once, under the driver → ride → offer locks. The streak is incremented only for `expired_nonresponse`, in the same transaction.
- The batch timeout and the reaper share the request id `expire:{offer_id}`, so the second call replays. With a different id, it gets `OFFER_ALREADY_RESOLVED` and no increment.
- An accept at or after the deadline becomes `expired_late_response`, which is not a miss.
- Crossing the threshold runs exactly one `pause_misses` (id `miss-pause:{offer_id}`), which resets the streak.
- 459's seam keeps legacy counting until 460's receipt-based seam lands.
- **Gate:** enable v2 only after 458, 459 and 460 are deployed and the D1 client is at the forced-upgrade floor.

## A3. Must-fix F2(e): the newest login is told it has been superseded

**Evidence.** Every path checks `users.current_session_id` before the controller. So a controller mismatch always means the *stored* controller is stale. But today:
- renewal returns `CONTROLLER_SESSION_MISMATCH` even when the driver is offline;
- presence maps that to `SESSION_SUPERSEDED`, and the WS closes with 1008;
- live location returns 409 `SESSION_SUPERSEDED`;
- the snapshot reports `SESSION_SUPERSEDED` whenever the controller is not the caller.

Result: every new login loops through "session ended", and GO is never shown.

**Fix.**
- **F2-7** (457 in place):
  - In `renew_driver_presence`, the order becomes flag, current session, epoch, offline, controller.
  - The snapshot RPC adds `current_session_id` for server use only.
- **F2-8a** (presence):
  - `CONTROLLER_SESSION_MISMATCH` → `ONLINE_EPOCH_STALE` with `reason_code:"CONTROLLER_SESSION_MISMATCH"`.
  - `UNAUTHORIZED_SESSION` stays `SESSION_SUPERSEDED`.
  - `_presence_conflict` passes `reason_code` through (F2-6).
- **F2-8b** (service), under v2:
  - a controller but no caller session or no current session → `reconnecting/SESSION_RECONCILE_REQUIRED`;
  - caller is not the current session → `reconnecting/SESSION_SUPERSEDED`;
  - controller is not the caller and the driver is online → `paused/REQUESTS_PAUSED`; GO rebinds.
  - Never send `current_session_id` to the client, and test that the key is absent.
- **F2-1** (C7 as SQL):
  - Remove `go_online` from the mismatch guard for non-system callers.
  - Bind with `IF p_action IN ('go_online','displace_controller') THEN controller := caller`. The caller is always current, so this matches C7.
  - `OBLIGATION_ACTIVE` still refuses GO during a trip or pending offer.
  - The result gains `controller_rebound` and `server_time`.
- **T5-2**, accept or decline:
  - caller is not the current session → `SESSION_SUPERSEDED`;
  - caller is current, but the offer's session or the controller differs → 409 `OFFER_EXPIRED` with `reason_code:"OFFER_SESSION_ENDED"`. No state change and no decision row.
- **Tests** replace "online re-login still MISMATCH":
  - re-login over an online old controller with no obligation → OK, new controller, epoch + 1;
  - the same with a `driver_assigned` ride → `OBLIGATION_ACTIVE`;
  - the old session → `UNAUTHORIZED_SESSION`;
  - a system `go_online` → 22023.
- **Known limitation:** after a re-login during a trip, phone B cannot move the rider's map marker until the trip ends. Trip batches still work. Active-trip takeover is out of scope.

## A4. Revised commit order
1. F1, then F2-1 … F2-9, then F3, F5, F7.
2. T11-B1 (`stop_requests_for_session_end` + tests/test_driver_availability_session_end.py) and T11-B2 (auth wiring + tests/test_auth_logout_availability_v2.py). Both need F2-1, F2-2 and F2-9.
3. T4, then T5 (with the X2 and F2(e) changes), then T6 (with the X3 changes), then T12 (confirm order, `user_id` in candidate rows, emitting through F2-9).

11-5a (X7) can go at any time. X8 (462, 11-5b1–3) goes only after a `spinr-security-auditor` pass.

## A5. Enablement gates
| Setting | Enable only when |
|---|---|
| v2 | 458–460 are deployed and the mobile T7–T10 build is at the forced-upgrade floor |
| Readiness | 461 is deployed, the T12 mobile work is shipped, and the copy has passed a legal check |
| Successor commitment | a security audit has passed; never in this PR |

The PR stays a draft with no flags enabled.
