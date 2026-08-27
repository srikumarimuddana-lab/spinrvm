# Change Impact & Risk Log — Driver location marker write gate

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | srikumarimuddana@gmail.com (with Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | `430dfe6`, `86b45ff` — branch `claude/gps-pings-redis-wh6h8r` |
| Related issue or gap ID | None open. Adjacent: ACTION_ITEMS B3 (closed), E2 (blocked on E1), `docs/audit/findings.md` #13 |

## 1. Issue / gap identified

Driver GPS is the highest-frequency write path in the system, and its durable
`UPDATE drivers SET lat, lng, updated_at` was throttled inconsistently: the
WebSocket handlers coalesced to one write per 3 s, while all three REST
`POST /drivers/location-batch` handlers had **no** write throttle at all — bounded
only by the 60/min per-driver rate limit. The two mechanisms could not see each
other, so a driver flushing the REST outbox while pinging over WebSocket wrote
the same row from two uncoordinated paths.

Found by tracing the ingestion path in response to a scaling question, not by a
bug report or an audit finding. No production incident is attributed to this.

## 2. Root cause

Two independent throttles grew separately:

- The WS throttle used `conn_state["last_loc_db_write"]`, in-process and scoped
  to a single WebSocket connection. It reset on every reconnect and was invisible
  to any other route or replica.
- The REST handlers were added as a durable outbox path and never got an
  equivalent gate; the 60/min rate limit was treated as sufficient (see
  `docs/change-log/2026-08-07-location-batch-rate-limit.md`, which explicitly
  scoped itself to runaway clients rather than steady-state volume).

Estimated steady-state cost at 500 drivers on a 30% on-trip / 70% idle mix:
roughly 120 `drivers`-row UPDATEs/sec. **This is arithmetic from the configured
client cadences, not a measurement** — see §9.

## 3. Fix / remediation

Adds `backend/utils/location_write_gate.py`: a single Redis-keyed window per
driver (`SET spinr:locwrite:{id} 1 NX EX 3`) shared by every GPS ingestion route,
replacing both the per-connection WS timer and the absent REST throttle.

Ships **flag-off, in shadow mode**. While `location_marker_write_gate_enabled` is
off the gate evaluates and counts what it *would* have skipped as
`outcome="shadow_throttled"`, but every write still lands. That produces a real
production number for write volume and throttle hit-rate before any behaviour
changes.

Also removes the `manager.update_driver_location(...)` call sites from the two WS
handlers. That wrote a 60 s `spinr:driver:location:{id}` cache on every ping,
whose only reader — `ConnectionManager.get_driver_location` — had **zero
callers**. Removing it keeps Redis ops per ping net-neutral: one dead `SET` out,
one gate `SET NX` in.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), but the read side is cross-cutting.**
Every consumer of `drivers.lat/lng` was enumerated by grep:

| Consumer | Location | Tolerance |
|---|---|---|
| Dispatch candidate query | `services/dispatch_service.py:407` | ≤3 s staleness; documented budget is 30 s |
| Live dispatch bounding box | `routes/rides/matching.py:297` | same |
| `/drivers/nearby` rider map | `routes/drivers/location.py:364` | same |
| Surge supply counts | `utils/surge_engine.py:135` | 2-min engine cadence |
| Admin live monitoring | `routes/admin/monitoring.py:161` | cosmetic |
| Stale-intent reconciler | `utils/stale_intent_reconciler.py:135` | see below |

`.claude/context/domain-dispatch.md:55` sets the documented freshness budget at
**30 s**; a 3 s gate is well inside it. `dispatch_service.py:115-142` warns *"Do
not reintroduce a staleness gate here"* — this change does not add one; it slows
the write, and `_is_dispatchable_driver` only requires non-null `lat`/`lng`.

**Two risks specifically checked, because both fail silently:**

1. **Period 1 insurance accumulator.** `routes/drivers/location.py` folds
   `period1_accum_km` / `period1_accum_since` into the *same* `update_data` dict
   as lat/lng (v1 and v2-idle paths; the v2-trip hot path carries no
   accumulator). Naively skipping the write would under-count a regulated SGI
   audit figure. Mitigated: `_write_marker_if_due` detects those columns and
   passes `force=True`, which always writes and restarts the window. Covered by
   `test_period1_force_never_skipped`.

2. **`drivers.updated_at` as a staleness signal.**
   `utils/stale_intent_reconciler.py` selects `is_online = true AND updated_at <
   now - stale_intent_offline_hours` (default **4 h**) to flip force-killed apps
   intent-offline, and its own docstring names `updated_at` as its "durable
   staleness signal". The gate delays that column's refresh by at most one
   interval — 3 s, worst case ~63 s on the idle REST path — which is orders of
   magnitude inside a 4 h cutoff, and the loop additionally re-checks Redis
   presence immediately before its atomic claim. Guarded by
   `test_interval_stays_far_below_stale_intent_threshold` so a future interval
   increase can't silently break it.

**Background loops:** no new loop added; no change to `core/lifespan.py`. No ride
state machine, money, or wallet path is touched. `is_online` / `is_available` are
never written by this path, so the `is_available ⇒ is_online` invariant is
unaffected — and the open race in `docs/audit/findings.md` #13 (location writes
vs. dispatch claim on the shared `drivers` row) is *reduced*, not widened, since
there are strictly fewer writes.

## 5. User-experience effect

**Nobody, in the shipped state.** The flag is off, so every write still lands and
behaviour is byte-identical to before — apart from the removed dead Redis cache
write, which had no reader and therefore no observable effect.

With the flag **on**: no rider- or driver-facing copy, screen, or flow changes. A
driver's car marker on the admin live map and the rider's `/drivers/nearby` view
could lag real position by up to 3 s more than today. Not visible mid-session to
a rider in a ride — the in-ride driver marker is driven by the WebSocket fan-out
to the rider, which still runs on **every** ping and is untouched by this change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/location_write_gate.py` | New. Redis `SET NX EX` window, shadow mode, fail-open, `force` bypass, metrics | One shared throttle for every ingestion route |
| `backend/routes/drivers/location.py` | Added `_write_marker_if_due`; routed all three REST write sites through it | Closes the unthrottled REST path; enforces the Period 1 rule |
| `backend/routes/websocket.py` | Both handlers call `should_write_marker`; dropped `_DRIVER_LOC_DB_WRITE_INTERVAL_S` and the cache-write call sites | Unifies WS with REST; survives reconnects |
| `backend/socket_manager.py` | Annotated `update_driver_location` / `get_driver_location` as unused | Kept as the seed of a future Redis read path |
| `backend/tests/test_location_write_gate.py` | New. 11 tests | Fail-open, Period 1, shadow mode, cross-path coalescing |
| `backend/tests/test_websocket_coverage.py` | One assertion updated to the new contract | Asserted the now-removed cache write |

## 7. Before / after

```python
# Before — routes/websocket.py: per-connection, in-process, invisible to REST
_loc_now = asyncio.get_event_loop().time()
if _loc_now - conn_state.get("last_loc_db_write", 0.0) >= _DRIVER_LOC_DB_WRITE_INTERVAL_S:
    await db_supabase.update_driver_location(driver_id, lat, lng, heading=data.get("heading"))
    conn_state["last_loc_db_write"] = _loc_now
await manager.update_driver_location(driver_id, lat, lng)   # cache nothing reads
```

```python
# After — shared Redis window, survives reconnects, dead cache write gone
if await should_write_marker(driver_id, path="ws_single"):
    await db_supabase.update_driver_location(driver_id, lat, lng, heading=data.get("heading"))
```

```python
# Before — routes/drivers/location.py: unconditional on every REST flush
await db_supabase.update_one("drivers", {"user_id": current_user["id"]}, update_data)
```

```python
# After — gated, with Period 1 writes forced through
await _write_marker_if_due({"user_id": current_user["id"]}, update_data, str(driver_id), "rest_v1")
```

## 8. Rollback plan

**Flip `location_marker_write_gate_enabled` to `false` in the `app_settings`
table.** No redeploy, no migration, no data to unwind. Since the flag ships off,
this is also the *current* state — turning it on is the deliberate action, and
turning it back off is instant.

Nothing here writes money, ride state, or insurance-period rows, so there is no
data-level remediation to plan. A `git revert` of both commits restores the prior
throttles cleanly if the gate module itself misbehaves.

## 9. Verification performed

- [x] **Automated tests (unit).** 11 new tests in `test_location_write_gate.py`;
      415 passing across `-k "location or presence or websocket or socket"`. The
      cross-path coalescing tests run against the **real** `redis_set_nx`
      in-process fallback rather than a mock, so they cover actual keying.
- [x] **Blast-radius grep performed.** Searched every reader of `drivers.lat`,
      `drivers.lng`, `drivers.updated_at`, `update_driver_location`,
      `get_driver_location`, `_DRIVER_LOC_DB_WRITE_INTERVAL_S`,
      `last_loc_db_write`, `GEOADD`/`georadius`/`zadd`. Results in §4.
- [x] **Reviewed against `CLAUDE.md` conventions:** dual-import pattern kept;
      no float money math involved; error paths use `logger.error` with the
      underlying exception and never silently swallow; metrics follow
      `spinr_<domain>_<metric>_<unit>` with `_total` counters.
- [x] **Feature-flagged**, via the `app_settings`-in-DB pattern, default off.
- [x] `ruff check` / `ruff format` clean on all touched files. Two lint findings
      in the touched files (`location.py` B904, `test_websocket_coverage.py`
      F841) were verified **pre-existing in HEAD** and left alone; repo-wide
      `ruff check .` reports 39 pre-existing errors, i.e. that gate is already
      red on `main` independent of this change.
- [ ] **Manual repro in staging — NOT DONE.** No staging environment exists
      (ACTION_ITEMS E1).

### What was NOT verified

- **No load measurement, before or after.** The ~120 UPDATEs/sec figure is
  arithmetic from configured client cadences
  (`driver-app/hooks/useDriverDashboard.ts:65-71`,
  `tripLocationRecorder.ts:13`), not telemetry. The load harness that would
  produce a real number (ACTION_ITEMS E2) is built but unrun, blocked on E1.
  **This is precisely why the gate ships in shadow mode** — the
  `shadow_throttled` counter is the measurement, and the flag should not be
  flipped on until it has been read.
- **No timing measurement against the 150 ms location-write SLA.** Consistent
  with the prior fix on this path (audit N10), which was also verified by
  call-count assertion rather than live timing.
- **Not tested against live Supabase or a real Redis** — all tests use the
  `mock_supabase_client` fixture and the in-process Redis fallback. Behaviour
  under a genuine Redis partition is reasoned about (fail-open) and unit-tested
  via an injected exception, not exercised against a real outage.
- **No visual-regression coverage applies** — backend-only change, no UI surface.
- The interaction with `docs/audit/findings.md` #13 was reasoned about (strictly
  fewer writes to the shared row), not empirically retested.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single `app_settings` flag).
- [x] Blast radius is stated and enumerated by grep, not assumed.
- [x] No silent behavior change to an already-shipped flow — the shipped state is
      behaviourally identical; the flag flip is the behaviour change and its UX
      effect is stated in §5.
