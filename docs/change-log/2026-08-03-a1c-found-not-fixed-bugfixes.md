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

---

## Entry 6 — `corporate_low_balance.py`: malformed timestamp defeats the 12h rate limiter

### 1. Issue / gap identified

`utils/corporate_low_balance.py::run_low_balance_tick` caught `ValueError` from `datetime.fromisoformat` on a corrupt `low_balance_notified_at` value and set `last_dt = None`. Because the rate-limit check is gated on `if last_dt and ...`, a `None` was treated identically to "never notified" — a malformed DB value silently defeated the 12h rate limiter every single tick (this loop runs hourly) until the column was manually repaired, with nothing logged about the corruption.

### 2. Root cause

The `except ValueError` branch chose `None` as the "couldn't parse" sentinel, which happens to collide with the same sentinel the rate-limit check uses for "column never set" — the two distinct states (malformed vs. genuinely-never-notified) were not distinguished.

### 3. Fix / remediation

On a parse failure, the value is now logged via `logger.error` (naming the wallet and the raw malformed value, for ops to find and repair) and `last_dt` is set to "now" instead of `None` — failing closed (treats the corrupt row as if it were *just* notified, so the full 12h window still applies) rather than failing open (spamming an email every tick).

### 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `run_low_balance_tick` has one caller: `corporate_low_balance_loop`, one of the 17 background loops. No other file reads `low_balance_notified_at`'s malformed-value branch.
- Behavior for the common case (a well-formed timestamp, or a genuinely `None`/absent value) is unchanged — only the malformed-string edge case changes, from "send immediately, every tick" to "wait one rate-limit window, log the corruption."
- This is a strictly safer default: the previous behavior could spam a corporate admin's inbox hourly; the new behavior waits and surfaces the root cause via logging instead.

### 5. User-experience effect

- Corporate-admin-visible (indirectly): a company whose wallet row has a corrupted `low_balance_notified_at` will now receive at most one low-balance email per 12h window (same as the normal case) instead of one per hour, once this fix ships. No rider/driver-visible effect.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/corporate_low_balance.py` | Malformed-timestamp branch now logs via `logger.error` and fails closed (treats as "just notified") instead of `None`/"never notified" | Close the found-not-fixed rate-limit-bypass gap |
| `backend/tests/test_corporate_low_balance_coverage.py` | Updated the pinned bug test to assert the fail-closed, logged behavior | Reflect the fix |

### 7. Before / after

```python
# Before
try:
    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
except ValueError:
    last_dt = None

# After
try:
    last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
except ValueError:
    logger.error(
        "low-balance: malformed low_balance_notified_at %r for wallet %s — "
        "treating as just-notified, not never-notified",
        last, w.get("id"),
    )
    last_dt = datetime.now(timezone.utc)
```

### 8. Rollback plan

`git revert` — pure code-path fix, no migration, no data written differently.

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_corporate_low_balance_coverage.py -q` — 8 passed.
- [x] Blast-radius regression check: `pytest tests/test_corporate_low_balance.py -q` — 5 passed, 0 failed.
- [x] Blast-radius grep performed: see section 4.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- No staging/manual repro of an actual corrupted DB row — verified at the unit level via a hand-crafted malformed string.

---

## Entry 7 — `retention_purge.py`: asymmetric error handling on the daily regulatory PII purge

### 1. Issue / gap identified

`utils/retention_purge.py::run_retention_purge_tick` validates two Postgres RPC responses (`purge_pii_retention` and `purge_trip_route_geometry`) with the identical `isinstance(data, dict)` shape check, but reacted asymmetrically to a malformed response: `purge_pii_retention` logged via `logger.error` and returned `None` (the calling loop saw a normal completion — no exception, no `spinr_bgloop_errors_total` increment), while `purge_trip_route_geometry` logged the same way but then raised `RuntimeError`, which the loop wrapper's `except Exception` catches and alerts on. `purge_pii_retention` is the function driving the **regulatory** purge (7-year DSAR/trip hard-delete, 3-year GPS-trace scrub, 7-year insurance-period audit trail per CLAUDE.md's PIPEDA/Saskatchewan Regulatory sections) — a silent stop there is a compliance risk, not just an availability one.

### 2. Root cause

The two response-shape guards were written with the same check but different failure-handling philosophies (soft-fail vs. hard-fail) for what is effectively the same failure mode (an unexpected RPC envelope shape, e.g. from a Supabase/PostgREST client upgrade) — an inconsistency, not a deliberate design choice; nothing in the module docstring or surrounding code explains why the two should differ.

### 3. Fix / remediation

`purge_pii_retention`'s malformed-response branch now raises `RuntimeError("purge_pii_retention returned an invalid response")` after logging, matching `purge_trip_route_geometry`'s existing behavior. The exception propagates through `_tick()` into `retention_purge_loop`'s `except Exception` handler exactly as the route-geometry half already does — logged via `logger.exception`, `spinr_bgloop_errors_total` incremented, and (since the loop retries every ~24h) the purge attempt again on the next scheduled run.

### 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `run_retention_purge_tick`'s only caller is `_tick()` → `retention_purge_loop`, one of the 17 background loops (spawned from `core/lifespan.py`), tracked by `utils/loop_monitor.py`. No route or other service calls this function directly.
- For the common case (a well-formed RPC response, which is what happens on every day this has run successfully in production) behavior is byte-identical — this only changes what happens on the previously-silent malformed-response edge case.
- This is a monitoring/alerting-visibility fix, not a data-write-path change: the RPC call to Postgres itself, and what it deletes/anonymizes, is completely unchanged. The only behavior change is whether a shape mismatch in the *response* now surfaces loudly.

### 5. User-experience effect

None — this is a purely internal, backend-only background-loop change with no rider/driver/corporate-admin-facing surface. Internal-admin/ops-facing: a malformed-response failure of the regulatory purge will now show up in `spinr_bgloop_errors_total` and the loop's exception log, instead of looking like a normal, silent completion.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/retention_purge.py` | `purge_pii_retention`'s malformed-response branch now raises `RuntimeError` after logging, instead of returning `None` | Close the found-not-fixed silent-compliance-purge-stop gap |
| `backend/tests/test_retention_purge.py` | Renamed/updated the pre-existing test that pinned the old soft-fail behavior to assert the raise instead | Reflect the fix (this test predates this session's coverage sweep) |
| `backend/tests/test_retention_purge_coverage.py` | Updated the pinned "found not fixed" comparison test and its sibling to assert the now-consistent raising behavior on both halves | Reflect the fix |

### 7. Before / after

```python
# Before
if not isinstance(data, dict):
    logger.error("retention_purge: unexpected rpc response shape: %r", type(data).__name__)
    return None

# After
if not isinstance(data, dict):
    logger.error("retention_purge: unexpected rpc response shape: %r", type(data).__name__)
    raise RuntimeError("purge_pii_retention returned an invalid response")
```

### 8. Rollback plan

`git revert` — pure code-path fix, no migration, no change to the underlying Postgres RPC functions or what they delete/anonymize. If this fix somehow caused an undesired alert-noise increase (e.g. a genuinely transient/expected response-shape quirk), reverting restores the prior soft-fail-and-log behavior with no data-level cleanup needed.

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_retention_purge_coverage.py tests/test_retention_purge.py -q` — 34 passed (26 + 8).
- [x] Blast-radius grep performed: only `core/lifespan.py` (spawns the loop) and `utils/loop_monitor.py` (heartbeat) reference this module outside its own file — see section 4.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- Not run against a real Supabase/Postgres RPC call — verified at the unit level only, against mocked `supabase.rpc(...).execute()` responses.
- Did not confirm with the compliance/legal owner of the retention policy whether hard-failing (vs. the prior soft-fail) is the definitively correct choice for this specific RPC — the fix follows this same file's own existing precedent (the `purge_trip_route_geometry` half already does this), which is the strongest available signal for "intended" behavior in this codebase, but a compliance sign-off was not separately sought.

---

## Entry 8 — `driver_onboarding_reminder_rules.py`: per-area document opt-out silently ignored

### 1. Issue / gap identified

`utils/driver_onboarding_reminder_rules.py::mandatory_requirements` — when every item in an area's `required_documents` list was explicitly marked not-required (`required: False` / `is_mandatory: False`), the filtered result (`out`) ended up empty, and the function fell through to the *global* mandatory-document list. This made an area-level "nothing is required here" configuration indistinguishable from "this area has no `required_documents` configured at all" — an operator's deliberate opt-out was silently overridden and the global list was enforced instead.

### 2. Root cause

The fallback-to-global decision was gated on `if out: return out`, checking the *filtered* result rather than whether the area had configured a list at all — conflating two different input states ("no list" vs. "list present, all items opted out") that should produce different outputs.

### 3. Fix / remediation

The check now inspects `raw_items` (the list *before* the `required: False` filter) rather than `out` (the list *after*). An area with no `required_documents` key at all still falls back to global (unchanged); an area with an explicit list — even one where every entry is opted out — now returns that empty result and is respected as-is.

### 4. Risk & impact on existing functionality

- **Blast radius: isolated to reminder-email content.** `mandatory_requirements` feeds `missing_required_document_uploads`, which is used only by `utils/driver_onboarding_reminders.py`'s reminder-nudge loop (one of the 17 background loops) and `utils/driver_status_notifications.py`. Grepped both callers: neither uses this function's output to gate actual driver approval/activation — that logic lives elsewhere (`routes/admin/drivers.py`'s verification endpoints) and is untouched by this change. This fix can only change *which documents a driver is reminded to upload*, never whether they're approved to drive.
- Any area that currently has a `required_documents` list with every entry marked not-required (a real, if unusual, config state) will see its drivers stop getting global-list reminder nudges for documents the area operator deliberately opted out of. This is the intended effect of the fix — before, such an area's opt-out was silently ignored.
- Areas with a normal (non-empty, has-active-entries) `required_documents` list, or no list at all, are unaffected — verified by the existing/updated test suite's coverage of both branches.

### 5. User-experience effect

- Driver-facing (indirectly): a driver whose service area explicitly opted out of area-specific document requirements will no longer receive onboarding reminder nudges for the global document list on that area's behalf. This only affects reminder *email/push content*, not account status or driving eligibility.
- No rider or corporate-admin-facing effect.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_onboarding_reminder_rules.py` | `mandatory_requirements`'s global-fallback check now inspects the raw (pre-filter) list instead of the filtered result | Close the found-not-fixed silent-opt-out-override gap |
| `backend/tests/test_driver_onboarding_reminder_rules_coverage.py` | Renamed/updated the pinned bug test to assert the area's explicit empty list is now respected | Reflect the fix |

### 7. Before / after

```python
# Before
out: list[...] = []
for item in _load_list((area or {}).get("required_documents")):
    ...
if out:
    return out
return [...global fallback...]

# After
raw_items = _load_list((area or {}).get("required_documents"))
out: list[...] = []
for item in raw_items:
    ...
if raw_items:
    return out
return [...global fallback...]
```

### 8. Rollback plan

`git revert` — pure code-path fix, no migration, no data-write-path change (this function only reads config and produces reminder content, it writes nothing).

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_driver_onboarding_reminder_rules_coverage.py -q` — 67 passed.
- [x] Blast-radius regression check: `pytest tests/test_driver_needs_review_notification.py tests/test_driver_onboarding_reminders.py tests/test_driver_status_notifications.py -q` — 50 passed, 0 failed.
- [x] Blast-radius grep performed: confirmed neither caller of `mandatory_requirements`'s output gates driver approval/activation — see section 4.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- Did not check production data for how many service areas, if any, currently have a `required_documents` list with every entry marked not-required — the practical prevalence of the previously-silently-overridden config state is unknown.

---

## Entry 9 — `core/config.py`: ADMIN_PASSWORD missing length check (security, explicit user approval obtained)

> **⚠️ DEPLOYMENT RISK — READ BEFORE MERGING.** This change makes backend
> startup **fail immediately** in production if the currently-deployed
> `ADMIN_PASSWORD` env var (Railway and/or Fly.io) is shorter than 20
> characters. **Verify the current production `ADMIN_PASSWORD` value's
> length in both deploy targets before merging this PR** — if it's short,
> rotate it to a ≥20-char value first (`python -c 'import secrets; print(secrets.token_urlsafe(24))'`),
> or this fix will take the API down on next deploy.

### 1. Issue / gap identified

`core/config.py::Settings._guard_production_secrets` validated `ADMIN_PASSWORD` only against 3 hardcoded known-weak literal strings (`"admin123"`, `"password"`, `"changeme"`) — unlike `JWT_SECRET`, which additionally gets a ≥32-char minimum-length check in the same validator. A production deploy with `ADMIN_PASSWORD=x` (or any short-but-not-literally-listed password) passed this guard cleanly. Separately, `core/middleware.py::_validate_production_config` *does* enforce a 20-char minimum on the same field — but that's a different guard, invoked from a different place (`init_middleware`), not the one embedded in `Settings` itself.

### 2. Root cause

The `_guard_production_secrets` validator's weak-literal check and JWT_SECRET's length check were both present, but no equivalent length check was ever added for `ADMIN_PASSWORD` in this specific validator — an omission, not a deliberate design choice (the sibling `middleware.py` guard shows the intended minimum was already decided at 20 chars elsewhere in the codebase).

### 3. Fix / remediation

**User was asked explicitly via `AskUserQuestion` before this fix was applied**, given the security-sensitivity and production-startup-risk of the change; the user approved adding a 20-char minimum matching `middleware.py`. `_guard_production_secrets` now raises `ValueError` if `len(ADMIN_PASSWORD) < 20` in production, with the same error-message style as the existing `JWT_SECRET` check (states the actual length, suggests a `secrets.token_urlsafe` generation command).

### 4. Risk & impact on existing functionality

- **Blast radius: this validator runs on every `Settings()` construction in production mode** (`ENV=="production"`), which is exactly the point of the fix (defense-in-depth vs. `middleware.py`'s separate, skippable guard) — but it also means **this is the highest-risk fix in this entire batch**, because it can turn a previously-successful production startup into a crash if the currently-deployed `ADMIN_PASSWORD` happens to be short.
- Grepped the full backend test suite for other places constructing a production-mode `Settings()`: found and fixed one pre-existing regression in `tests/test_p1_auth_hardening.py::TestProductionStartupGuards._make_settings` (its baseline `ADMIN_PASSWORD` was `"StrongPass123!"`, 14 chars — would have failed every test built on that fixture). No other production-mode `Settings()` construction sites found in the test suite.
- Non-production environments (`ENV != "production"`, which is every local/dev/test/staging-if-not-flagged-production run) are completely unaffected — the entire `_guard_production_secrets` validator early-returns before this check for any non-production `ENV`.

### 5. User-experience effect

None directly rider/driver/corporate-admin-facing — this is a backend startup-time guard. Internal-admin-facing risk: **if the actual deployed production `ADMIN_PASSWORD` is currently shorter than 20 characters, this change will prevent the backend from starting on the next deploy** until that env var is rotated to a longer value. See the deployment-risk callout at the top of this entry.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/config.py` | `_guard_production_secrets` now raises if `ADMIN_PASSWORD` is under 20 characters in production | Close the found-not-fixed security gap, per explicit user approval |
| `backend/tests/test_core_config_coverage.py` | Renamed/updated the pinned "found not fixed" test into a positive assertion of the new guard; added boundary tests (19 vs. 20 chars) | Reflect the fix |
| `backend/tests/test_p1_auth_hardening.py` | Fixed a pre-existing test fixture whose baseline `ADMIN_PASSWORD` ("StrongPass123!", 14 chars) would have failed under the new guard | Regression found during blast-radius verification |

### 7. Before / after

```python
# Before — only a fixed weak-literal check, no length check
weak = {
    "JWT_SECRET": ("your-strong-secret-key",),
    "ADMIN_PASSWORD": ("admin123", "password", "changeme"),
}
# ... (JWT_SECRET length check follows, but nothing equivalent for ADMIN_PASSWORD)

# After — length check added, mirroring the JWT_SECRET pattern
admin_password = self.ADMIN_PASSWORD or ""
if len(admin_password) < 20:
    raise ValueError(
        f"ADMIN_PASSWORD must be at least 20 characters in production "
        f"(got {len(admin_password)}). Matches core/middleware.py's "
        "_validate_production_config check. Generate one with: "
        "python -c 'import secrets; print(secrets.token_urlsafe(24))'"
    )
```

### 8. Rollback plan

- **Not revertible without also checking live data first** in the sense that matters here: this is a startup guard, not a data mutation, so `git revert` alone is safe from a *data* perspective — but if this fix is merged and deployed while the live `ADMIN_PASSWORD` is short, the **correct incident response is to rotate the env var to a ≥20-char value** (not revert the code), since the guard is doing exactly what it's supposed to.
- If the fix itself needs to be rolled back (e.g. it's blocking a deploy and there's no time to rotate the secret first): `git revert` is safe and instant — it only removes a startup-time validation, touches no persisted data, no migration, no wallet/Stripe state.
- **Action required before merge**: verify current production `ADMIN_PASSWORD` length on both Railway and Fly.io deploy targets (see CLAUDE.md's Deployment section — both run from `main` in parallel). If short, rotate first.

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_core_config_coverage.py tests/test_p1_auth_hardening.py tests/test_middleware_production_config_guard.py tests/test_admin_routes_auth.py -q` — 72 passed.
- [x] Blast-radius grep performed: searched the full test suite for other production-mode `Settings()` construction sites; found and fixed one regression (see section 6).
- [x] User explicitly approved this specific fix via `AskUserQuestion` before it was applied, given the security-sensitivity and deployment risk.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).
- [ ] **Production `ADMIN_PASSWORD` length NOT verified against live Railway/Fly env vars** — this session has no access to production secrets. Flagged as a required pre-merge check, not completed.

### 10. What was NOT verified

- **Production `ADMIN_PASSWORD`'s actual current length** — this is the single most important unverified item in this entire change-log. Confirm before merging (see the risk callout at the top of this entry).
- Did not check whether any other internal script or CI job constructs `Settings()` with `ENV=production` outside the test suite (e.g. a one-off ops script) that might also be affected — only the pytest suite was grepped.

---

## Entry 10 — `location_integrity.py`: GPS-spoofing mock-flag detection bypass (safety, explicit user approval obtained)

### 1. Issue / gap identified

`utils/location_integrity.py::check_location_integrity`'s mock-location detector used `if mocked is True:` — a strict identity check against the `True` singleton. The value reaches this function via raw dict access from client-supplied JSON (`latest.get("mocked")` / `data.get("mocked")` / `last_pt.get("mocked")`, no Pydantic bool coercion upstream), so a client sending the mock flag as `1` or `"true"` instead of a literal JSON `true` produced `mocked is True == False` for both — the spoofed point silently passed the check instead of being rejected. Since this module's entire purpose is defeating mock-location/GPS-spoofing, this was a real detection bypass, not a style nit.

### 2. Root cause

`is True` identity comparison instead of a truthiness check — a common Python pitfall (`1 == True` is `True`, but `1 is True` is `False`), likely written without considering that the incoming value isn't guaranteed to be a real Python `bool` by the time it reaches this function.

### 3. Fix / remediation

**User was asked explicitly via `AskUserQuestion` before this fix was applied**, given the safety-sensitivity (driver trust-scoring / fraud-detection logic) of the change; the user approved switching to a truthy check. `if mocked is True:` is now `if mocked:`, so any of `1`, `"true"`, `True`, or any other truthy value is caught.

### 4. Risk & impact on existing functionality

- **Blast radius**: `check_location_integrity` has three callers — `routes/drivers/location.py` (batch location-update endpoint), and `routes/websocket.py` (two call sites: live driver-location WS messages). All three already pass the raw, unvalidated `mocked` value straight from client JSON with no coercion, so all three benefit from the fix identically.
- **Known false-positive tradeoff, accepted**: a plain truthy check also means a client sending the *string* `"false"` or `"0"` (intending "not mocked") would be treated as truthy (non-empty string) and incorrectly flagged as mocked — the opposite direction from the fixed bypass. This is judged acceptable because it fails in the *safe* direction for an anti-spoofing check (a false positive rejects a real point; the pre-fix bug was a false negative that accepted a spoofed one) and no legitimate client in this codebase is known to send the mock flag as a string at all (every caller passes it straight through from a JSON body, where Android SDKs report a real JSON boolean).
- Grepped every test file touching `check_location_integrity` or its three callers: none asserts on a specific string/falsy `mocked` value, so no test-suite regression from this edge case.

### 5. User-experience effect

- Driver-facing (indirectly, safety-relevant): a driver whose client sends a mock-location flag in a non-boolean shape (`1`/`"true"`) will now correctly have that location point rejected as untrusted, instead of it silently passing through as a legitimate GPS point. This directly strengthens the platform's ability to detect GPS spoofing, which feeds driver trust-scoring and potential fraud enforcement.
- No rider or corporate-admin-facing effect.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/location_integrity.py` | `check_location_integrity`'s mock-flag check now uses a truthy comparison instead of `is True` identity | Close the found-not-fixed GPS-spoofing-detection bypass, per explicit user approval |
| `backend/tests/test_location_integrity_coverage.py` | Renamed/updated the two pinned bug tests to assert the spoofed points are now correctly rejected | Reflect the fix |

### 7. Before / after

```python
# Before
if mocked is True:
    logger.warning(...)
    return False, "mock_location"

# After
if mocked:
    logger.warning(...)
    return False, "mock_location"
```

### 8. Rollback plan

`git revert` — pure code-path fix, no migration, no data-write-path change (this function only reads the incoming location payload and reports trust/reason, it writes nothing itself beyond what its caller already does with the existing `(trusted, reason)` return value).

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_location_integrity_coverage.py -q` — 25 passed.
- [x] Blast-radius regression check: `pytest tests/test_location_batch.py tests/test_p3_background_location.py tests/test_period1_accumulation_endpoint.py -q` — 24 passed, 1 pre-existing unrelated xfail, 0 failed.
- [x] Blast-radius grep performed: confirmed all three callers pass raw client JSON with no coercion, and no test asserts on a specific falsy-string `mocked` value — see section 4.
- [x] User explicitly approved this specific fix via `AskUserQuestion` before it was applied, given the safety-sensitivity of anti-spoofing logic.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- No staging/manual repro against a real Android/iOS client sending a non-boolean mock flag — verified at the unit level only, with hand-crafted `mocked=1`/`mocked="true"` inputs.
- Did not audit whether any client-side (rider-app/driver-app) code in this repo ever sends the mock flag as a non-boolean shape in practice — the fix is defensive regardless of current client behavior, but the real-world prevalence of the bypass being actively exploited (vs. theoretical) was not investigated.

---

## Entry 11 — `driver_import_service.py`: bulk import ignores document approval status (regulatory, explicit user approval obtained)

### 1. Issue / gap identified

`services/driver_import_service.py::build_plan`'s `has_import_documents` flag — which gates whether a bulk-imported driver is planned with `status="active"`/`is_verified=True` (vs. `"needs_review"`) — was computed from mere *presence* of a document row for that driver's `old_driver_id` in the documents CSV, never checking that document row's own `status` column. A driver row with `spinr_approved=yes` + `regulatory_authority_approved=yes` plus a document row whose `status` was explicitly `"rejected"` (or merely `"pending"`) was still planned as `status="active"`, `is_verified=True` — bulk-import could mark a driver active/verified with a rejected or unreviewed document attached, bypassing the document-approval gate the rest of the system relies on for driver eligibility.

### 2. Root cause

`document_old_ids` (the set used to compute `has_import_documents`) was built from every document row's `old_driver_id` unconditionally, without filtering on that row's `status` field — even though the very next code block (the per-document-row validation loop) already reads and validates `status` against `VALID_DOC_STATUSES = {"pending", "approved", "rejected"}`. The two pieces of logic simply weren't connected.

### 3. Fix / remediation

**User was asked explicitly via `AskUserQuestion` before this fix was applied**, given this is a regulatory driver-eligibility gate; the user approved requiring `status == "approved"`. `document_old_ids` now only includes a driver's `old_driver_id` if at least one of their document rows has `status == "approved"` (defaulting unset/blank status to `"pending"`, matching the same default the validation loop already uses).

### 4. Risk & impact on existing functionality

- **Blast radius: isolated to the bulk-import planning function.** `build_plan` is called from the admin bulk-driver-import flow (`routes/admin/...` — CSV-based import) and the CLI/script import path; grepped every test file mentioning `driver_import_service`: all 8 pass unmodified against this fix (`test_admin_driver_import.py`, `test_driver_import_service.py`, and 6 others whose match was incidental, not functional — see verification below).
- **Real behavior change**: any bulk-import CSV batch that includes a driver with a non-approved (pending or rejected) document row will now correctly plan that driver as `needs_review` instead of `active`. This is the intended, corrective effect of the fix — a driver import operator relying on the old (buggy) behavior to fast-track drivers with unreviewed documents will see those drivers routed to manual review instead, which is the desired regulatory posture.
- This only affects the *planning* stage (`build_plan`, a pure function with no I/O) — `commit_plan` (the actual DB write step) is unmodified; it simply persists whatever `driver_status`/`is_verified` values `build_plan` computed, so no separate write-path risk.

### 5. User-experience effect

- Internal-admin-facing (bulk driver import operators): a CSV batch containing a driver with a pending/rejected document will now correctly show that driver routed to `needs_review` in the import preview/report, instead of silently marking them `active`/`verified`. This is a correctness fix to the import tool's own reporting, not a new feature.
- Driver-facing (indirectly): a driver whose bulk-imported document was rejected or not yet reviewed will now correctly stay in `needs_review` status (unable to go online) instead of being incorrectly marked active — this closes a real regulatory-eligibility gap, not a cosmetic one.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | `document_old_ids`'s set comprehension now filters on `status == "approved"`, not mere row presence | Close the found-not-fixed document-approval-bypass gap, per explicit user approval |
| `backend/tests/test_driver_import_service_coverage.py` | Renamed/updated the pinned bug test to assert `needs_review`/`is_verified=False` for a rejected document; added sibling tests for the approved-document and pending-document cases | Reflect the fix |

### 7. Before / after

```python
# Before
document_old_ids = {r.get("old_driver_id") for r in document_rows if r.get("old_driver_id")}

# After
document_old_ids = {
    r.get("old_driver_id")
    for r in document_rows
    if r.get("old_driver_id") and (r.get("status") or "pending").strip().lower() == "approved"
}
```

### 8. Rollback plan

`git revert` — `build_plan` is a pure function (no I/O); reverting restores the prior (buggy) planning behavior with no data-level cleanup needed. Note: if any driver was already committed as `active`/`verified` via `commit_plan` under the old buggy planning logic *before* this fix ships, reverting the code does not retroactively fix those already-imported rows — that would require a separate data remediation pass (out of scope for this fix, flagged here for awareness).

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_driver_import_service_coverage.py -q` — 62 passed.
- [x] Blast-radius regression check: `pytest tests/test_admin_driver_import.py tests/test_admin_stripe_import.py tests/test_admin_stripe_payout_sync.py tests/test_driver_import_service.py tests/test_entity_export_service.py tests/test_entity_import_service.py tests/test_stripe_mapping_import_service.py tests/test_stripe_payout_sync_service.py -q` — 125 passed, 0 failed.
- [x] Blast-radius grep performed: see section 4.
- [x] User explicitly approved this specific fix via `AskUserQuestion` before it was applied, given the regulatory driver-eligibility sensitivity.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- Did not check production import history for whether any already-committed driver rows were affected by the pre-fix bug (i.e. imported as active/verified with only a pending/rejected document) — see the rollback-plan note above; a data remediation pass, if warranted, is a separate follow-up.
- No staging/manual repro of an actual bulk-import CSV run — verified at the unit level only, via `build_plan`'s pure in-memory planning logic.

---

## Entry 12 — `distance_reconciliation.py`: nightly claim step marks unevaluated rides as reconciled (explicit user approval obtained)

### 1. Issue / gap identified

`utils/distance_reconciliation.py::_run_reconciliation_tick`'s claim step stamped `distance_reconciled_at` onto **every ride fetched in the batch**, not just the rides that were actually evaluated by `evaluate_reconciliation()`. A ride whose `actual_distance_km` wasn't usable yet (e.g. the deferred route finalizer hadn't backfilled it by the time this tick ran) was silently skipped by `evaluate_reconciliation()` — contributing no ratio, no divergence event — yet still got `distance_reconciled_at` stamped by the claim step below it, because that step built its `ids` list from the raw `rides` batch instead of the evaluated subset.

### 2. Root cause

The claim step (`ids = [str(r.get("id")) for r in rides if r.get("id")]`) and the evaluation step (`evaluate_reconciliation`, which internally skips rides failing an inline usable-distance check) used two different, disconnected eligibility checks over the same batch — the claim step had no awareness of which rides evaluation actually skipped.

### 3. Fix / remediation

**User was asked explicitly via `AskUserQuestion` before this fix was applied**, given this is a detection/regulatory-adjacent surface (the module's whole purpose is catching platform-wide fare-distance bias, per its module docstring referencing the prior haversine fare bug). User approved: "Only mark rides actually evaluated." Extracted a shared `_has_usable_distance(ride)` helper (same predicate `evaluate_reconciliation` already used inline) and now use it in both places: `evaluate_reconciliation()` calls it to decide whether to `continue`, and the claim step in `_run_reconciliation_tick()` now filters `ids` through the same helper, so only rides that actually contributed a ratio get `distance_reconciled_at` stamped. Rides skipped for missing distance data are left unclaimed and will be re-fetched (`{"distance_reconciled_at": None}` filter) and evaluated on a future tick once their data lands. Added a `skipped` count to the tick's `logger.info(...)` summary for observability.

### 4. Risk & impact on existing functionality

- **Blast radius: isolated to this module.** Grepped the whole repo for `distance_reconciliation` outside `tests/`: only `core/lifespan.py` (which just starts `distance_reconciliation_loop()` as a background task at startup — no logic coupling) and the module itself. No other module reads or writes `rides.distance_reconciled_at` directly (confirmed via grep for the column name).
- **Real behavior change**: a ride whose measured distance wasn't ready yet used to be permanently excluded from future reconciliation (silently, forever, once `distance_reconciled_at` was stamped) even after its distance data eventually landed. After this fix, such a ride is correctly re-evaluated on a later tick. This *increases* reconciliation coverage over time — no ride that was previously being checked stops being checked; some rides that were previously slipping through uncounted are now counted once their data is available.
- This is a detection-only module (per its own docstring: "it never changes a fare or a displayed distance") — the fix cannot affect any fare, receipt, or rider/driver-facing value, only which rides feed the bias-detection aggregate and per-ride divergence events.
- Batch size effect: rides now left unclaimed will reappear in the next day's `BATCH_LIMIT=500`-row fetch (`ride_completed_at` ascending order) alongside newly-completed rides. If a large volume of rides were persistently missing measured distance (a symptom of a separate bug in the route finalizer), they'd now accumulate in the query window rather than being silently claimed-and-dropped — worth monitoring the `skipped` count in the tick's info log if that scenario is suspected, but not a regression this fix introduces on its own (it restores the intended re-check behavior).

### 5. User-experience effect

None directly rider/driver/admin-facing — this is an internal detection loop with no UI surface. Indirect effect: the platform-wide bias alert (`logger.error` → Sentry, the module's actual purpose) now has a slightly larger and more accurate sample of evaluated rides over time, since previously-dropped rides are no longer permanently excluded.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/distance_reconciliation.py` | Added `_has_usable_distance(ride)` helper; `evaluate_reconciliation()` now calls it instead of an inline check; `_run_reconciliation_tick()`'s claim step now filters `ids` through the same helper and logs a `skipped` count | Close the found-not-fixed claim/evaluate eligibility mismatch, per explicit user approval |
| `backend/tests/test_distance_reconciliation_coverage.py` | Renamed and rewrote the pinned bug test (`test_ride_with_missing_measured_distance_is_claimed_anyway` → `test_ride_with_missing_measured_distance_is_left_unclaimed`) to assert only the evaluated ride is claimed | Reflect the fix |

### 7. Before / after

```python
# Before
ids = [str(r.get("id")) for r in rides if r.get("id")]
if ids:
    await db_supabase.update_one("rides", {"id": {"$in": ids}}, {"distance_reconciled_at": now.isoformat()})
logger.info(
    "distance reconciliation tick: %d rides, %d claimed, %d divergences, mean_ratio=%s",
    len(rides), len(ids), len(divergences), aggregate["mean_ratio"],
)

# After
ids = [str(r.get("id")) for r in rides if r.get("id") and _has_usable_distance(r)]
skipped = len(rides) - len(ids)
if ids:
    await db_supabase.update_one("rides", {"id": {"$in": ids}}, {"distance_reconciled_at": now.isoformat()})
logger.info(
    "distance reconciliation tick: %d rides, %d claimed, %d skipped (missing distance data), "
    "%d divergences, mean_ratio=%s",
    len(rides), len(ids), skipped, len(divergences), aggregate["mean_ratio"],
)
```

### 8. Rollback plan

`git revert` — the claim step is the only write this module performs (`rides.distance_reconciled_at`), and the fix only *narrows* which rides get that timestamp set (never widens it), so no ride that was correctly claimed before is affected. Reverting restores the prior (buggy) wider-claim behavior with no data-level cleanup needed — no ride's `distance_reconciled_at` needs to be unset either way, since the fix doesn't retroactively touch already-stamped rows, only future tick runs.

### 9. Verification performed

- [x] Automated tests run: `pytest tests/test_distance_reconciliation_coverage.py -q --no-cov` — 10 passed.
- [x] Blast-radius regression check: `pytest tests/test_distance_reconciliation.py -q --no-cov` (the pre-existing, non-coverage-pass test file for this module) — 9 passed, 0 failed.
- [x] Blast-radius grep performed: see section 4 (only `core/lifespan.py` references the module outside tests; no other reader/writer of `rides.distance_reconciled_at`).
- [x] User explicitly approved this specific fix via `AskUserQuestion` before it was applied.
- [ ] Full backend suite — pending, run once at the end of this batch of fixes (per explicit user instruction to defer the full-suite/CI run).

### 10. What was NOT verified

- Did not check production data for how many rides, historically, were affected by the pre-fix bug (i.e. already stamped `distance_reconciled_at` despite never having been evaluated) — those rows remain excluded from reconciliation going forward regardless of this code fix; a one-time data remediation (clearing `distance_reconciled_at` for rides that have no corresponding integrity-event/ratio history) would be a separate, out-of-scope follow-up if deemed necessary.
- No staging/manual repro against a live Supabase instance — verified at the unit level only, via mocked `db_supabase.get_rows`/`update_one`.

---

## Entry 13 — `routes/webhooks.py`: `payment_intent.succeeded` "processing" race — investigated, NOT a bug, approved fix reverted

### 1. Issue / gap identified (as originally flagged)

`payment_intent.succeeded`'s handler treats a ride whose `payment_status` is already `'processing'` as "already settled" — skipping the `financial_events` ledger write, the GST/PST tax receipt, and the tip credit — while still unconditionally force-writing `payment_status: "paid"`. The sibling `_handle_ride_invoice_paid` handler (same file, invoice.paid path) treats the identical `'processing'` status by **raising** (HTTP 500, Stripe retries) instead of settling. The two handlers disagreeing on how to handle the same race condition was flagged as a "found not fixed" money-safety concern during the original A1c Sub-tier C coverage pass.

### 2. Root cause investigation — and reversal

The user approved "match the sibling: raise on 'processing'" as the fix. Before committing it, I applied the change (raise `HTTPException(500, ...)` as soon as `payment_status == "processing"` is observed, mirroring `_handle_ride_invoice_paid`) and ran the full webhook test surface as a blast-radius check per CLAUDE.md's pre-merge gates. That surfaced a **pre-existing regression test that predates this session** — `TestWebhookTimeoutDivergence::test_finalizes_ride_stuck_in_processing` in `backend/tests/test_webhooks_main.py` — whose docstring states explicitly: *"The ride is stuck in payment_status='processing'. The webhook must still finalize it to 'paid' — the handler makes no assumptions about the prior payment_status value."*

Tracing why: `routes/payments.py::confirm_payment` calls `db_supabase.claim_ride_payment_processing(ride_id)` to atomically claim the ride into `payment_status='processing'` **before** it talks to Stripe. If that synchronous call then times out client-side, or the process crashes mid-flight, the ride is left permanently stuck in `'processing'` — and there is **no other recovery path**: `utils/stuck_ride_sweeper.py` (grepped directly) only handles ride *state* (searching/driver_assigned/etc.), never `payment_status`. `payment_intent.succeeded` arriving later (Stripe's own confirmation that the charge did succeed) is the *only* mechanism that unsticks such a ride. This is a load-bearing, intentional design, not an oversight — the sibling comparison in the original finding was a false equivalence: for the invoice.paid path, `'processing'` genuinely means a *different, concurrent* in-app charge (no claim/rescue relationship exists there), whereas for `payment_intent.succeeded`, `'processing'` is very often *this same payment's own* claimed-but-unfinished state, and this webhook is its designed rescue.

Raising here instead — as approved and initially implemented — would have reintroduced a real "ride stuck in payment_status='processing' forever" bug on any client-side `confirm_payment` timeout, silently regressing a working recovery path in favor of "fixing" a difference between two handlers that turned out not to be a bug.

**Decision: the fix was reverted.** `git checkout -- backend/routes/webhooks.py` restored the file to its pre-fix state (verified `git diff` is empty). This is a case of CLAUDE.md Rule 9 working as intended two levels deep: escalate before shipping (done, user approved) — but pre-merge blast-radius verification is what actually caught this, and finding new disqualifying evidence after approval is itself an escalation trigger. Not silently keeping the approved-but-now-known-wrong fix.

### 3. Fix / remediation

**None applied to application code.** The genuine remaining gap — the ledger write and tax receipt are skipped whenever `payment_status` was already `'processing'`, relying on an unverified assumption that a concurrent `settle_card()` call performs them — is real, but is a *separate* question from raise-vs-settle, and fixing it (e.g. by having the webhook itself perform the ledger write/receipt even for an already-'processing'-then-now-succeeded ride) needs its own design pass, not this session's remaining budget. Left as a pinned "found, investigated, not fixed" item.

### 4. Risk & impact on existing functionality

None — no application code changed in the end. Blast-radius verification (this investigation) is itself the deliverable: confirmed via grep that `stuck_ride_sweeper.py` doesn't cover payment-status recovery, and via the pre-existing test's docstring that this webhook's tolerance of `'processing'` is intentional.

### 5. User-experience effect

None (no code change). Avoided a regression that would have made a rider's ride, whose in-app payment confirmation timed out, unpayable via any automatic path — the money would eventually settle in Stripe but the ride/receipt would never reflect it without manual ops intervention.

### 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | None — fix applied then reverted via `git checkout` | Pre-existing regression test proved the approved fix was based on a false premise |
| `backend/tests/test_routes_webhooks_coverage.py` | Module docstring and `TestStripeWebhookProcessingRace` (renamed from `...RiskFoundNotFixed`) rewritten to document the investigation and reversal; test body unchanged (still asserts the original, correct, settle-not-raise behavior) | Keep the pinned-bug test accurate and record the investigation trail |

### 7. Before / after

No production code diff (net zero — applied then reverted). Test-file framing only:

```
# Before (original coverage pass)
"FOUND NOT FIXED (money-safety concern)" — assumed the two handlers'
divergent treatment of 'processing' was a bug in payment_intent.succeeded.

# After (this entry)
"FOUND, INVESTIGATED, NOT A BUG" — the divergence is intentional:
payment_intent.succeeded's tolerance of 'processing' is the only recovery
path for a confirm_payment timeout; _handle_ride_invoice_paid's raise is
correct for its own, unrelated 'processing' race. Genuine remaining gap
(skipped ledger/receipt) reframed as its own separate, still-open item.
```

### 8. Rollback plan

N/A — no code change shipped.

### 9. Verification performed

- [x] Applied the approved fix, then ran the full webhook-adjacent test surface as a blast-radius check: `pytest tests/test_corporate_e2e_wallet.py tests/test_corporate_webhook.py tests/test_money_decimal.py tests/test_orphan_refund.py tests/test_p2_corporate_decimal.py tests/test_p2_promo_wallet_loyalty.py tests/test_process_payment_card.py tests/test_routes_main_coverage.py tests/test_ses_webhook.py tests/test_spinr_pass_subscription.py tests/test_stripe_reconcile.py tests/test_twilio_inbound.py tests/test_wallet.py tests/test_webhook_stripe_v15.py tests/test_webhooks_main.py -q --no-cov` — 1 failure (`TestWebhookTimeoutDivergence::test_finalizes_ride_stuck_in_processing`), which is what triggered this investigation.
- [x] Reverted via `git checkout -- backend/routes/webhooks.py`; confirmed `git diff` empty.
- [x] Re-ran `pytest tests/test_routes_webhooks_coverage.py tests/test_webhooks_main.py -q --no-cov` after reverting — 83 passed, 0 failed.
- [x] Grepped `utils/stuck_ride_sweeper.py` for any payment-status handling — none found, confirming `payment_intent.succeeded` is the only recovery path.

### 10. What was NOT verified

- Did not design or scope a real fix for the genuine remaining gap (ledger/receipt skipped on an already-'processing' ride) — flagged as still-open in the test docstring, not resolved.
- Did not check production incident history for whether the assumption "a concurrent settle_card() call writes the ledger" has ever actually been violated in practice.
