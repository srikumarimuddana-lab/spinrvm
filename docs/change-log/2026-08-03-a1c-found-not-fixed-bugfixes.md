# Change Impact & Risk Log — A1c "found not fixed" bug fixes

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch / rides / corporate / drivers |
| PR / commit link | (this branch: `claude/spinr-ai-guardrail-reviewer-o2vups`) |
| Related issue or gap ID | Follow-up fixes for bugs surfaced (not fixed) during the A1c Sub-tier B/C test-coverage sweeps |

This is a running log across several small, independently-committed fixes. Each numbered entry below is its own logical change with its own blast-radius/verification note; they are batched into this one document (rather than one file per bug) because they share the same origin (coverage-sweep "found not fixed" findings) and most are individually very small.

---

## Entry 1 — Unescaped PostgREST OR-clause in `claim_ride_atomic` / ride-completion incentive lookup

### 1. Issue / gap identified

Two call sites built a PostgREST `.or_()` filter with raw f-string interpolation instead of routing the interpolated value through the shared `_postgrest_or_value` escaping helper (`repositories/_base.py`) that every other or-clause builder in the codebase uses:

- `repositories/driver_repo.py::claim_ride_atomic` — `driver_id` interpolated raw into `f"driver_id.is.null,driver_id.eq.{driver_id}"`.
- `routes/drivers/ride_complete.py` (inside `complete_ride`'s incentive-claims block) — `sa_id` (`ride.get("service_area_id")`) interpolated raw into `f"service_area_id.is.null,service_area_id.eq.{sa_id}"`.

### 2. Root cause

CLAUDE.md's "Query filters" convention states the filter layer owns escaping and callers pass raw input — `_postgrest_or_value` double-quotes any value containing PostgREST's or-group-reserved characters (`,()"\\`). These two call sites predate/bypassed that convention (or were added without following it), likely because they build the `.or_()` string manually rather than going through the generic `get_rows`/`update_one` filter-dict path that other callers use.

### 3. Fix / remediation

Both call sites now route their interpolated value through `_postgrest_or_value` before building the f-string, matching every other or-clause builder in the codebase (e.g. `repositories/corporate_repo.py`). No change to query semantics for the normal case (a UUID with no reserved characters) — only the malformed/reserved-character case is affected, and it now degrades safely (the reserved characters are treated as part of one literal value) instead of corrupting the or-clause.

### 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `claim_ride_atomic` (`repositories/driver_repo.py`) is grepped for every caller: only `db_supabase.py` re-exports it, and no route file currently calls it directly (`grep -rn "claim_ride_atomic(" backend/routes/` returns nothing) — it appears to be dead/legacy code today, not wired into a live request path. The `ride_complete.py` call site is inside `complete_ride`'s incentive-claims block, used only by that one endpoint.
- **Both values (`driver_id`, `service_area_id`) are internal UUIDs** sourced from an authenticated JWT / the ride row, never directly user-supplied text — for the practical, non-malformed case the generated or-clause string is byte-identical before and after this fix (verified by the updated test assertions).
- No data-shape or write-path change; this only changes how a filter *string* is constructed, not what gets read/written.

### 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin facing behavior change for the normal (UUID) case. The only behavior change is in the previously-unreachable malformed-input edge case, which now fails safely instead of corrupting a filter.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/driver_repo.py` | `claim_ride_atomic`'s `.or_()` now routes `driver_id` through `_postgrest_or_value`; added the import (both dual-import branches) | Close the escaping gap found during the A1c Sub-tier C coverage pass |
| `backend/routes/drivers/ride_complete.py` | Incentive-claims `.or_()` now routes `sa_id` through `_postgrest_or_value`; added a local dual-import of the helper | Same fix, second call site found during the same pass |
| `backend/tests/test_driver_repo_coverage.py` | `TestClaimRideAtomicOrClauseBug` now asserts the corrected (escaped) or-clause output instead of pinning the old buggy shape | Test previously pinned the bug per the coverage-pass's "found not fixed" convention; now reflects the fix |
| `backend/tests/test_ride_complete_coverage.py` | Updated the stale "found not fixed" comment on the incentive-claims test to reflect the fix | Same reason |

### 7. Before / after

```python
# Before (repositories/driver_repo.py::claim_ride_atomic)
.or_(f"driver_id.is.null,driver_id.eq.{driver_id}")

# After
.or_(f"driver_id.is.null,driver_id.eq.{_postgrest_or_value(driver_id)}")
```

```python
# Before (routes/drivers/ride_complete.py, incentive-claims block)
iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{sa_id}")

# After
iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{_postgrest_or_value(sa_id)}")
```

### 8. Rollback plan

`git revert` — pure code-path fix, no migration, no data written differently for the normal case, no feature flag needed given the isolated blast radius.

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_driver_repo_coverage.py tests/test_ride_complete_coverage.py -q` — 72 passed.
- [x] Blast-radius regression check: `pytest tests/test_claim_ride.py tests/test_db_supabase_helpers.py tests/test_trip_window_compression.py tests/test_ride_state_machine.py tests/test_rides.py tests/test_drivers_extended.py -q` — 191 passed, 0 failed.
- [x] Blast-radius grep performed: see section 4.
- [x] Reviewed against CLAUDE.md's "Query filters" convention — this fix directly implements it.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- Not run against a real Supabase/PostgREST instance — the `.or_()` call itself is mocked in all touched tests (matching this repo's existing test-tier convention); the actual PostgREST parsing behavior for a quoted or-term is asserted by the shared `_postgrest_or_value`/`_base.py` test suite, not re-verified here.
- `claim_ride_atomic` appearing to have no live caller was confirmed by grep, not by a full call-graph/dead-code analysis tool — if it turns out to be reachable via some dynamic dispatch this session's grep missed, the practical risk is still low (UUID-shaped input, safe-degrade fix).

---

## Entry 2 — Three small, independent correctness fixes (redis diagnostics, favorite-route response staleness, support-ticket wording)

### 1. Issue / gap identified

Three unrelated small bugs found during the A1c Sub-tier C coverage pass:

1. `utils/redis_diag.py::_pubsub_roundtrip` — the per-poll `ps.get_message(...)` call hardcoded `timeout=1.0` instead of respecting the caller's requested `timeout`, so a caller asking for a fast probe (e.g. `timeout=0.01` for a UI health check) could still block up to ~1s per stalled iteration.
2. `routes/favorites.py::use_favorite_route` — returned the pre-increment `fav` row it fetched, not the post-increment row it just wrote, so a client reading `use_count` off the response saw a stale value even though the DB was updated correctly.
3. `services/zoho_desk_integration.py::create_support_ticket` — when `message` is blank but a `transcript` is supplied, the ticket description was built as `"(no message)\n\n--- Chat transcript ---\n<transcript>"`, misleadingly implying no content when a real transcript exists.

### 2. Root cause

1. A literal `1.0` was used instead of computing the remaining time until the caller's deadline.
2. The handler never merged its own DB write back into the value it returns.
3. The `"(no message)"` placeholder substitution didn't account for the transcript being available as an alternative "there is content" signal.

### 3. Fix / remediation

1. `_pubsub_roundtrip` now computes `remaining = deadline - time.monotonic()` each iteration and passes `min(1.0, max(0.0, remaining))` as the per-poll timeout.
2. `use_favorite_route` builds the `$set` payload once, applies it via `update_one`, and returns `{**fav, **updates}` (the merged post-increment row) instead of the raw pre-fetch `fav`.
3. `create_support_ticket` only falls back to `"(no message)"` when there is truly nothing to show (no message AND no transcript); a blank message with a transcript now produces a description that leads with the transcript content instead of the placeholder.

### 4. Risk & impact on existing functionality

- **`redis_diag.py`**: admin-only diagnostics endpoint (`/admin/redis/diag` or similar), not on any rider/driver hot path. Blast radius: isolated — `_pubsub_roundtrip` has one caller (`probe_redis_url`), which itself is only used by the admin Redis-health surface. No behavior change to the *total* wait time (bounded by the same `timeout`/`deadline`), only to how tightly the last poll is bounded.
- **`favorites.py`**: `use_favorite_route` is a rider-facing endpoint (`POST /favorites/{id}/use`). Grepped for other callers of the function itself (Python-internal): none — it's only reached via its own route. The response shape gains correct `use_count`/`last_used_at` values; any client that was tolerant of the stale value (or ignored it) is unaffected. No other file reads this endpoint's response shape in this repo (rider-app consumption is out of this backend-only PR's scope but the JSON key names are unchanged, only their values are now correct).
- **`zoho_desk_integration.py`**: `create_support_ticket` is called only from the support-chat escalation route. This only changes the ticket body text seen by Zoho Desk support agents (internal-team-facing), not any rider/driver-visible surface, not any money/state-machine path.

### 5. User-experience effect

- `redis_diag.py`: none rider/driver-visible; internal-admin diagnostics only.
- `favorites.py`: rider-visible — the `POST /favorites/{id}/use` response now correctly reflects the incremented `use_count`/`last_used_at` instead of the stale pre-increment value. This is a correctness fix to an already-shipped response shape (same fields, corrected values), not a new field or contract change.
- `zoho_desk_integration.py`: internal-team-facing only (Zoho Desk ticket body text for support agents), not rider/driver-visible.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/redis_diag.py` | `_pubsub_roundtrip`'s per-poll `get_message` timeout now bounded by remaining time, not hardcoded `1.0` | Close the found-not-fixed timeout-overshoot gap |
| `backend/routes/favorites.py` | `use_favorite_route` returns the merged post-increment row | Close the found-not-fixed stale-response gap |
| `backend/services/zoho_desk_integration.py` | `create_support_ticket`'s `"(no message)"` placeholder only used when there's no transcript either | Close the found-not-fixed misleading-wording gap |
| `backend/tests/test_redis_diag_coverage.py` | Updated the pinned bug test to assert the fixed (bounded) timeout | Reflect the fix |
| `backend/tests/test_routes_favorites_coverage.py` | Updated the pinned bug test to assert the fixed (post-increment) response | Reflect the fix |
| `backend/tests/test_zoho_desk_integration_coverage.py` | Updated the pinned bug test to assert the fixed description text; added a sibling test for the true-no-content case | Reflect the fix |

### 7. Before / after

```python
# Before (utils/redis_diag.py::_pubsub_roundtrip)
msg = await ps.get_message(ignore_subscribe_messages=True, timeout=1.0)

# After
remaining = deadline - time.monotonic()
msg = await ps.get_message(ignore_subscribe_messages=True, timeout=min(1.0, max(0.0, remaining)))
```

```python
# Before (routes/favorites.py::use_favorite_route)
await db.update_one("favorite_routes", {"id": favorite_id}, {"$set": {...}})
return fav

# After
updates = {"use_count": ..., "last_used_at": ...}
await db.update_one("favorite_routes", {"id": favorite_id}, {"$set": updates})
return {**fav, **updates}
```

```python
# Before (services/zoho_desk_integration.py::create_support_ticket)
description = msg or "(no message)"

# After
description = msg or ("" if transcript else "(no message)")
```

### 8. Rollback plan

`git revert` for all three — none touch a migration, a wallet delta, or Stripe-charged state; all three are pure code-path corrections with no live-data footprint.

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_redis_diag_coverage.py tests/test_routes_favorites_coverage.py tests/test_zoho_desk_integration_coverage.py -q` — 49 passed (20 + 10 + 19).
- [x] Blast-radius grep performed: see section 4 (each fix's caller graph).
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- `favorites.py`'s rider-app consumption of the response shape was not checked (out of scope for this backend-only PR) — the fix only corrects values under existing field names, so no rider-app contract change is expected, but this wasn't confirmed against the actual mobile client code.
- No staging/manual repro for any of the three — all verified via the existing/updated unit test suites against mocked DB/Redis clients.

---

## Entry 3 — Dispute-resolution notification wording; fares crash on NULL `surge_multiplier`

### 1. Issue / gap identified

1. `routes/disputes.py::admin_resolve_dispute` — the rider-facing push-notification wording compared `req.resolution == "refund"`, a value that is never actually sent (the documented/used set is `approved | partial_refund | rejected`), so the notification always said "Your dispute has been reviewed." regardless of the actual outcome — even a full approval with a real Stripe refund issued.
2. `routes/fares.py::build_fares_for_area` — `min(matched_area.get("surge_multiplier", 1.0), SURGE_CAP)` only falls back to `1.0` when the `surge_multiplier` key is *absent*, not when it's explicitly SQL `NULL` (Python `None`). A `service_areas` row with `surge_enabled=True`, `surge_active=True`, and an explicit `NULL` `surge_multiplier` (a plausible admin data-entry state) raised `TypeError` inside `min()`, a 500 on the public `/fares` endpoint for that entire service area.

### 2. Root cause

1. Likely a copy/paste or renamed-constant bug — `"refund"` isn't a valid `resolution` value anywhere else in the file.
2. `dict.get(key, default)`'s well-known "default only substitutes on absent key, not on `None` value" gap — every other value read in this function that could plausibly be `NULL` isn't run through `min()`/arithmetic directly, so this was the one spot the gap actually mattered.

### 3. Fix / remediation

1. `resolution_label` is now computed via an explicit dict lookup: `{"approved": "approved", "partial_refund": "approved", "rejected": "rejected"}.get(req.resolution, "reviewed")`. `rejected` disputes now correctly say "rejected" instead of the old generic "reviewed" fallback (an accuracy improvement, not a new category — `rejected` was always a valid, already-used `resolution` value in this same file).
2. `matched_area.get("surge_multiplier") or 1.0` replaces the two-arg `.get(..., 1.0)`, so an explicit `NULL` now degrades to a 1.0x multiplier (same as the "surge not enabled" case) instead of crashing.

### 4. Risk & impact on existing functionality

- **`disputes.py`**: `admin_resolve_dispute` is admin-only and its refund/Stripe/DB-write logic is entirely unchanged — only the *push notification wording* sent to the rider changes. Blast radius: isolated to this one notification string; no other file constructs this message. This is a live-tested, rider-visible copy change (flagged per CLAUDE.md's "No silent behavior change to a live-tested flow" rule) — riders resolving a dispute mid-session will now see accurate wording instead of always "reviewed".
- **`fares.py`**: `build_fares_for_area` is called from `routes/fares.py` itself (the `/fares` and `/rides` fare-estimate paths) and `diagnose_nearby_drivers.py` (an internal diagnostic script) — grepped, no other callers. For the common case (a real, non-NULL `surge_multiplier`) behavior is byte-identical; this only changes what happens on the previously-crashing NULL case, converting a 500 into a graceful 1.0x-surge estimate. Pure risk-reduction — there is no code path today that could regress from "worked" to "broken."

### 5. User-experience effect

- `disputes.py`: rider-visible — a rider who has an open dispute resolved will now see accurate wording ("approved"/"rejected") in their push notification instead of always "reviewed". This is a copy fix to an already-shipped notification, not a new notification type.
- `fares.py`: rider-visible only in the previously-broken case — a service area with a NULL `surge_multiplier` now successfully returns fare estimates (at 1.0x) instead of the `/fares` endpoint 500ing for that area. No change for the normal case.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/disputes.py` | `admin_resolve_dispute`'s `resolution_label` now maps `approved`/`partial_refund` → "approved", `rejected` → "rejected" | Close the found-not-fixed wording gap |
| `backend/routes/fares.py` | `build_fares_for_area`'s surge multiplier read now uses `or 1.0` instead of `.get(..., 1.0)` | Close the found-not-fixed NULL-crash gap |
| `backend/tests/test_routes_disputes_coverage.py` | Updated the pinned bug test to assert the corrected wording; added a `rejected`-resolution wording test | Reflect the fix |
| `backend/tests/test_routes_fares_coverage.py` | Updated the pinned bug test to assert the graceful 1.0x degrade instead of `pytest.raises(TypeError)` | Reflect the fix |

### 7. Before / after

```python
# Before (routes/disputes.py::admin_resolve_dispute)
resolution_label = "approved" if req.resolution == "refund" else "reviewed"

# After
resolution_label = {
    "approved": "approved",
    "partial_refund": "approved",
    "rejected": "rejected",
}.get(req.resolution, "reviewed")
```

```python
# Before (routes/fares.py::build_fares_for_area)
min(matched_area.get("surge_multiplier", 1.0), SURGE_CAP)

# After
min(matched_area.get("surge_multiplier") or 1.0, SURGE_CAP)
```

### 8. Rollback plan

`git revert` for both — no migration, no data written differently, no wallet/Stripe state touched by either fix (the `disputes.py` change is pure notification copy; the `fares.py` change only prevents a crash on a specific NULL-data shape).

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_routes_disputes_coverage.py tests/test_routes_fares_coverage.py -q` — 49 passed (16 + 33).
- [x] Blast-radius regression check: `pytest tests/test_ai_tools_support.py tests/test_corporate_surge_bypass.py tests/test_fares.py tests/test_routes_faqs_coverage.py tests/test_dispute_refund_cents.py -q` — 66 passed, 0 failed.
- [x] Blast-radius grep performed: see section 4.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- No staging/manual repro of the actual push notification delivery — verified at the unit level only (the string passed to `send_push_notification`).
- Did not check whether any admin-dashboard surface also displays `resolution`/the old "reviewed" wording independently — only the backend notification-copy call site was in scope for this fix.

---

## Entry 4 — Corporate-cancellation WebSocket-failure undercounting (two sibling services)

### 1. Issue / gap identified

`services/corporate_suspension_service.py::_cancel_one_ride` and `services/corporate_member_offboarding_service.py::_cancel_one_ride` — both used to auto-cancel a company's pre-pickup rides on account suspension / member removal — wrapped the rider push notification and (for guest bookings) the SMS notify in try/except, but NOT the driver/rider `manager.send_personal_message` WebSocket sends. Since the atomic DB claim that flips `status` to `cancelled` (and, for an assigned ride, the driver-availability/insurance-period rollback) runs *before* the WS sends, a WS blip meant: the ride was genuinely cancelled in the DB, but the exception propagated out of `_cancel_one_ride`, was caught only by the *outer* per-ride try/except in the calling loop, and that ride was **not** counted in the returned `cancelled_count` / logged as a generic "failed to cancel" message — even though it had actually succeeded.

### 2. Root cause

The WS sends were added without the same defensive wrapping applied to the notification calls immediately below them in the same function — an inconsistency within each function, not a deliberate design choice (the module docstrings describe "best-effort per ride" as the intended contract).

### 3. Fix / remediation

Both driver-side and rider-side `manager.send_personal_message(...)` calls, in both files, are now wrapped in their own try/except (logged via `logger.error(..., exc_info=True)`, matching the existing push/SMS pattern), so a WS failure no longer masks an already-successful DB cancellation.

### 4. Risk & impact on existing functionality

- **Blast radius: isolated to these two files' internal `_cancel_one_ride` helper.** Grepped every caller of the public entry points (`cancel_pre_pickup_rides_for_company`, `cancel_pre_pickup_rides_for_member`): `routes/corporate_accounts.py` and `routes/corporate_company.py` (the admin suspend/remove-member endpoints) — neither reads anything from `_cancel_one_ride` beyond the aggregate count already returned by the public function, so this fix only makes that count *more accurate*, it doesn't change the public function's signature or contract.
- This is strictly a "make the count/log more accurate" fix — the DB write (the money/state-relevant part) was already correct before and after; the only prior misbehavior was mis-reporting a successful cancellation as a failure due to an unrelated side-effect glitch.
- No new failure mode introduced: a genuine DB-write failure still surfaces normally (that code path is unchanged and unaffected by this fix).

### 5. User-experience effect

- Driver/rider: previously, a WS blip during one of these system-initiated cancellations meant the rider might not get their push notification (execution never reached that line) even though their ride was already cancelled. Now the push notification still fires even if the WS send itself failed — a rider is more likely to actually be told their ride was cancelled.
- Internal admin/ops: the returned cancellation count and the `[CORP-SUSPEND]`/`[CORP-MEMBER-REMOVE]` logs are now accurate — any monitoring/alerting built on these will no longer see false "failed to cancel" signals for rides that were actually cancelled successfully.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/corporate_suspension_service.py` | Driver and rider `manager.send_personal_message` calls each wrapped in try/except | Close the found-not-fixed undercounting gap |
| `backend/services/corporate_member_offboarding_service.py` | Same fix, sibling file | Same reason |
| `backend/tests/test_corporate_suspension_service_coverage.py` | Renamed/updated the pinned bug test to assert the ride is still counted and the rider still gets their push | Reflect the fix |
| `backend/tests/test_corporate_member_offboarding_service_coverage.py` | Same, sibling file | Same reason |

### 7. Before / after

```python
# Before (both files, driver-side WS send — unguarded)
if driver and driver.get("user_id"):
    await manager.send_personal_message({...}, f"driver_{driver['user_id']}")

# After
if driver and driver.get("user_id"):
    try:
        await manager.send_personal_message({...}, f"driver_{driver['user_id']}")
    except Exception as exc:
        logger.error("[...] driver WS notify failed ride_id=%s: %s", ride_id, exc, exc_info=True)
```

(Same pattern applied to the rider-side send in both files.)

### 8. Rollback plan

`git revert` — the fix only adds try/except around two already-existing WS calls per file; no migration, no data-write-path change, no feature flag needed given the isolated, additive nature of the change.

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_corporate_suspension_service_coverage.py tests/test_corporate_member_offboarding_service_coverage.py -q` — 11 passed (6 + 5).
- [x] Blast-radius regression check: `pytest tests/test_corporate_member_offboarding_service.py tests/test_corporate_suspension_service.py tests/test_create_ride_remaining_branches.py -q` — 30 passed, 0 failed.
- [x] Blast-radius grep performed: see section 4.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- No staging/manual repro against a real Redis-backed `manager.send_personal_message` failure — verified at the unit level only (mocked `RuntimeError` side effect).
- Did not audit other corporate-lifecycle services (e.g. any sibling cancellation helper not covered by this session's A1c sweep) for the same missing-try/except pattern — only the two files the coverage sweep actually touched were checked.

---

## Entry 5 — Three dispatch/notification silent-swallow and error-isolation fixes

### 1. Issue / gap identified

1. `services/guest_notification_service.py::_guest_recipient` — `db_supabase.get_user_by_id(rider_id)` had no try/except, breaking the module's own documented "never raise into their caller" contract (every other DB call in the file honors it).
2. `utils/driver_claim_reaper.py::_reap_tick` — the `_has_pending_offer`/`_has_active_ride` check for one driver had no try/except, so a lookup failure on one driver aborted the entire tick's batch, skipping every other already-fetched, otherwise-reapable driver.
3. `utils/offer_expiry_reaper.py::_reap_tick` — the `get_app_settings()` fallback used a bare `except Exception: miss_threshold = 3` with zero logging, violating CLAUDE.md's "do not silently swallow errors" rule.

### 2. Root cause

1. Inconsistency within the file — the sibling helpers (`_company_name`, `_ensure_tracking_token`, `_send_guest_sms`) are each individually wrapped; this one call site was missed.
2. The per-driver loop only guarded the initial fetch and the release call, not the offer/ride check in between — narrower failure isolation than the sibling `stuck_ride_sweeper.py`'s per-item try/except convention.
3. A settings-fetch failure was defaulted silently instead of logged, unlike every other "fail safe with a default" branch elsewhere in the same codebase (which all log).

### 3. Fix / remediation

1. Wrapped the `get_user_by_id` call in try/except, logging via `logger.error(..., exc_info=True)` and returning `None` (same degrade-to-no-op contract every sibling helper already uses).
2. Wrapped the per-driver offer/ride check in try/except, isolated to that one driver (`continue`s to the next candidate), matching the release-failure guard immediately below it.
3. Added `logger.error(..., exc_info=True)` before the `miss_threshold = 3` fallback.

### 4. Risk & impact on existing functionality

- All three changes are purely additive error-handling around existing DB/settings calls — no change to the happy-path behavior, no change to what gets written to any table, no change to any public function's return type or contract.
- **`guest_notification_service.py`**: `_guest_recipient` is called from `notify_guest_driver_assigned`/`notify_guest_driver_arrived`/`notify_guest_cancelled`, all three already spawned off the hot path per the module's own design — this fix makes a transient DB blip degrade to "skip the SMS for this event" instead of crashing the spawned task, which is strictly safer.
- **`driver_claim_reaper.py`**: `_reap_tick` runs inside `driver_claim_reaper_loop`, one of the 17 background loops (CLAUDE.md's `spinr-background-loop` recipe applies — this fix does not add a new loop, only makes an existing one's per-item isolation match its sibling `stuck_ride_sweeper.py`). Blast radius: isolated to this one internal function; the public `driver_claim_reaper_loop` already catches and retries on any `_reap_tick` exception, so this fix only changes *how much of one tick's batch* gets processed before that outer catch would have fired, never the loop's overall survivability.
- **`offer_expiry_reaper.py`**: same background-loop pattern; this fix only adds a log line, no control-flow change (the `miss_threshold = 3` fallback still fires identically).

### 5. User-experience effect

- None of the three are directly rider/driver-visible. `guest_notification_service.py`'s fix marginally improves reliability of guest SMS delivery under a transient DB blip (an edge case, not a behavior change under normal operation). The other two are purely internal observability/resilience improvements to background loops.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/guest_notification_service.py` | `_guest_recipient` wraps `get_user_by_id` in try/except | Honor the module's own "never raise into caller" contract |
| `backend/utils/driver_claim_reaper.py` | Per-driver offer/ride check wrapped in try/except, isolated to that driver | Match the sibling per-item isolation convention |
| `backend/utils/offer_expiry_reaper.py` | Settings-fetch failure now logs via `logger.error` before defaulting | Close the silent-swallow gap per CLAUDE.md |
| `backend/tests/test_guest_notification_service_coverage.py` | Updated the pinned bug test to assert the swallow-and-return-None behavior | Reflect the fix |
| `backend/tests/test_driver_claim_reaper_coverage.py` | Updated the pinned bug test to assert per-driver isolation (second driver still reaped) | Reflect the fix |
| `backend/tests/test_offer_expiry_reaper_coverage.py` | Updated the pinned bug test to assert the error is now logged | Reflect the fix |

### 7. Before / after

```python
# Before (services/guest_notification_service.py::_guest_recipient)
user = await db_supabase.get_user_by_id(rider_id)

# After
try:
    user = await db_supabase.get_user_by_id(rider_id)
except Exception:
    logger.error("guest SMS: recipient lookup failed for rider %s", rider_id, exc_info=True)
    return None
```

```python
# Before (utils/driver_claim_reaper.py::_reap_tick)
if await _has_pending_offer(driver_id) or await _has_active_ride(driver_id):
    continue

# After
try:
    if await _has_pending_offer(driver_id) or await _has_active_ride(driver_id):
        continue
except Exception as e:
    logger.error("[claim-reaper] offer/ride check failed for driver %s: %s", driver_id, e, exc_info=True)
    continue
```

```python
# Before (utils/offer_expiry_reaper.py::_reap_tick)
except Exception:
    miss_threshold = 3

# After
except Exception as e:
    logger.error("[offer-expiry-reaper] settings fetch failed, defaulting miss_threshold=3: %s", e, exc_info=True)
    miss_threshold = 3
```

### 8. Rollback plan

`git revert` for all three — purely additive error handling, no migration, no data-write-path change.

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_guest_notification_service_coverage.py tests/test_driver_claim_reaper_coverage.py tests/test_offer_expiry_reaper_coverage.py -q` — 42 passed (15 + 10 + 17).
- [x] Blast-radius regression check: `pytest tests/test_corporate_company_bookings_routes.py tests/test_driver_claim_reaper.py tests/test_guest_sms.py tests/test_offer_expiry_reaper.py tests/test_p0_ship_blockers.py -q` — 62 passed, 0 failed.
- [x] Blast-radius grep performed: see section 4.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- No staging/manual repro against real background-loop execution (`ENV=production`) — all three verified at the unit level via mocked DB/settings clients, consistent with this repo's dual-import-aware loop-testing convention (`core/lifespan.py`'s `ENV=test` no-op guard prevents real loop spawning in the test suite).
