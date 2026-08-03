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
