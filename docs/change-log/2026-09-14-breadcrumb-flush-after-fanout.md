# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Found during a 2026-09-13/14 deep-dive audit into reported car-marker lag/latency complaints (user-reported, this session) |

## 1. Issue / gap identified

In `backend/routes/websocket.py`'s single-ping `driver_location`/`location_update` handler, the durable breadcrumb write (`buffer_ride_breadcrumb`) ran **before** the rider/admin location fan-out (`manager.broadcast_driver_location_to_admins` and the rider-facing broadcast above it). `buffer_ride_breadcrumb` occasionally flushes its buffer to Postgres (~every 10 points/10s), so roughly 1-in-10 location ticks paid a synchronous DB round-trip *before* any rider or admin connection ever saw the update — inside the same request path this repo's own Performance SLA table gives a <100ms fan-out target.

## 2. Root cause

The breadcrumb-buffer call and the fan-out calls have no data dependency on each other — nothing downstream of the fan-out reads anything `buffer_ride_breadcrumb` sets — but the code was originally written with the persistence call first, ahead of the broadcast, purely as an artifact of the order the two features were added in, not a deliberate sequencing decision.

## 3. Fix / remediation

Moved the `if data.get("durable", True) and not await _ws_session_revoked(): await buffer_ride_breadcrumb(...)` block to run immediately **after** `await manager.broadcast_driver_location_to_admins(...)`, instead of before the rider/admin fan-out. The call itself, its guard condition, and how it's awaited are all unchanged — only its position in the source moved. Added a comment at the new call site explaining why the reorder is safe (nothing below it reads a value the call sets) and reaffirming that this stays a plain `await`, not fire-and-forget, so a flush failure still propagates to this request's own error handling per `breadcrumb_buffer.py`'s documented loss-semantics contract ("Flush failures still raise so callers see them").

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one call's position within the single-ping `driver_location`/`location_update` branch of `websocket_endpoint`.** Grepped every other caller of `buffer_ride_breadcrumb` — the only other one is the disconnect-cleanup path's `flush_driver_breadcrumbs`, which is untouched (different function, different call site, no ordering relationship to this fix).
- **No fire-and-forget introduced.** Considered wrapping the call in `asyncio.create_task` to fully decouple it from the request path, but rejected: `breadcrumb_buffer.py`'s own module docstring guarantees "Flush failures still raise so callers see them" — a bare `create_task` would silently swallow a flush exception with no caller to observe it, breaking that existing contract. A pure reorder preserves the exact same error-propagation behavior while still removing the write from ahead of the fan-out on the common (non-flushing) tick.
- **Does not change what is persisted, when a flush is triggered, or the durable/ephemeral gating.** `test_breadcrumb_write_is_gated_on_the_durable_flag`, `test_single_ping_breadcrumb_also_checks_revocation`, and `test_legacy_driver_location_messages_remain_durable` (pre-existing, unchanged) still pass — the guard condition itself (`data.get("durable", True)` + `_ws_session_revoked()`) is untouched, only its position moved.
- **Batch path (`location_batch`/`driver_location_batch`) is untouched** — this fix only touches the single-ping branch; `persist_ride_breadcrumbs`'s call site and its own revocation check ordering are unaffected.
- On the ~1-in-10 tick that does flush, total request latency is unchanged (the DB write still happens once per request either way) — the fix only changes whether the rider/admin already have the fresh location by the time that write starts, not whether the write itself is fast.

## 5. User-experience effect

Rider/driver/admin-facing, mid-session, on every currently-live ride: riders and admins watching a driver's live position should see marker updates fan out slightly sooner and more consistently on the ~1-in-10 ticks that previously paid a synchronous DB write first. No change to what data is shown, no new UI, no new message shape — purely a latency/ordering fix on an already-shipped flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/websocket.py` | Moved the `buffer_ride_breadcrumb` guarded call to run after `manager.broadcast_driver_location_to_admins` instead of before it | Removes an occasional synchronous DB write from ahead of the rider/admin fan-out inside the <100ms WS fan-out SLA path |
| `backend/tests/test_websocket_live_location.py` | New test `test_single_ping_breadcrumb_buffer_runs_after_fanout` | Proves the ordering and guards against the regression coming back |

## 7. Before / after

```python
# Before
active_ride = active_rides[0] if active_rides else None
ride_id = active_ride["id"] if active_ride else None

if data.get("durable", True) and not await _ws_session_revoked():
    await buffer_ride_breadcrumb(driver_id, data, active_ride=active_ride)

# ... Maps API key refresh, rider fan-out, then:
await manager.broadcast_driver_location_to_admins(driver_id, location_update)
```

```python
# After
active_ride = active_rides[0] if active_rides else None
ride_id = active_ride["id"] if active_ride else None

# ... Maps API key refresh, rider fan-out, then:
await manager.broadcast_driver_location_to_admins(driver_id, location_update)

if data.get("durable", True) and not await _ws_session_revoked():
    await buffer_ride_breadcrumb(driver_id, data, active_ride=active_ride)
```

## 8. Rollback plan

`git-revert-safe` — this is a pure statement-reorder within a single function with no data/schema/flag involved. Reverting restores the previous ordering with no other side effect; no persisted state's shape or meaning changes either way.

## 9. Verification performed

- [x] New test `test_single_ping_breadcrumb_buffer_runs_after_fanout` — confirms `buffer_ride_breadcrumb`'s call now appears later in source than `broadcast_driver_location_to_admins`'s.
- [x] Full `backend/tests/test_websocket_live_location.py` re-run — 9/9 passing, including the three pre-existing guard/ordering tests for the durable-flag and revocation checks (unaffected).
- [x] Broader regression sweep: `tests/test_breadcrumb_buffer.py`, `tests/test_ws_rider_location_statuses.py`, `tests/test_websocket_auth.py`, `tests/test_ws_disconnect_presence_grace.py`, `tests/test_websocket_coverage.py` run together — 80 passed, 0 failed.
- [x] `python3 -c "import ast; ast.parse(open('routes/websocket.py').read())"` — syntax OK.
- [x] `ruff check routes/websocket.py` — all checks passed.
- [x] Blast-radius grep: confirmed `buffer_ride_breadcrumb` has exactly one other call site (unrelated, disconnect-cleanup path via `flush_driver_breadcrumbs`), untouched by this change.
- [ ] `spinr-realtime-reliability-reviewer` run against this diff — pending at commit time; any finding lands as a follow-up commit on this same PR before merge.

**What was NOT verified:** no live/staging WebSocket load test was run to measure the actual fan-out latency improvement on the ~1-in-10 flushing tick — the fix is reasoned from the code path (the DB write no longer blocks the fan-out) and confirmed via unit tests that behavior/guards are unchanged, not measured end-to-end against a real Postgres flush under load.

## 10. Alternatives considered

- **Fire-and-forget the breadcrumb write (`asyncio.create_task`)**: would fully decouple the DB write from the request's own latency, not just reorder it. Rejected — `breadcrumb_buffer.py`'s own documented contract requires flush failures to propagate to the caller; `create_task` has no caller to propagate to, silently dropping that guarantee. A pure reorder gets the fan-out-latency win without touching that contract.
