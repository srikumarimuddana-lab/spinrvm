# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-06 |
| Author | Claude Code (branch `claude/rideshare-monitoring-tools-iflbg2`) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (observability) |
| PR / commit link | (branch `claude/rideshare-monitoring-tools-iflbg2`) |
| Related issue or gap ID | ADR-010 §3 ("Background-loop stall"); `ACTION_ITEMS.md` C11 |

## 1. Issue / gap identified

Background-loop staleness is detectable only through the in-app `loop_watchdog`,
which posts to `ALERT_WEBHOOK_URL`. That is a single point of failure: if the
watchdog loop itself stalls or its webhook is misconfigured, a stalled loop
(e.g. `stuck_ride_sweeper`, `payment_retry`) is a silent correctness regression
with no second signal and no dashboard view. ADR-010 §3 asks for heartbeats to
*additionally* be exposed as a gauge so there is an independent detection path.

## 2. Root cause

`loop_monitor` was built for one consumer — the `/health` response — and stores
`time.monotonic()`, which is correct for staleness (immune to NTP steps and DST)
but is not exportable as a Prometheus timestamp. PromQL compares against
`time()`, which is epoch seconds; on Linux `time.monotonic()` counts from boot,
so exporting it directly would render every loop as roughly 56 years stale.

## 3. Fix / remediation

`record_heartbeat` now records a wall-clock epoch alongside the existing
monotonic value, in the same lock acquisition. A new `get_heartbeat_epochs()`
accessor returns a copy, and the `/metrics` handler publishes one
`spinr_loop_heartbeat_timestamp_seconds{loop="<name>"}` gauge per ticked loop,
refreshed at scrape time — the same pattern already used for the Redis gauges
immediately below it.

The `loop_watchdog` path is untouched and remains primary: it does not depend on
the metrics pipeline being healthy, which is exactly why ADR-010 keeps it.

## 4. Risk & impact on existing functionality

**Blast radius: wide but shallow — 56 `record_heartbeat` call sites** across
every background loop (`surge_engine`, `allowance_reset`, `driver_claim_reaper`,
`payment_retry`, `loop_watchdog`, …). Each now performs one additional
`dict[str] = float` assignment *inside the lock that was already being taken*,
so there is no new lock acquisition and no added contention. No call site
changed; no signature changed.

Consumers of the existing state are unaffected:
- `get_loop_status()` still reads `_heartbeats` (monotonic) and is byte-for-byte
  unchanged in behavior — covered by a regression test.
- `/health` embeds `get_loop_status()` and is untouched.
- `utils/loop_alert.check_and_alert` reads the same status; untouched.

Memory: one extra float per loop name (~20 loops). Negligible and bounded — the
map is keyed by a fixed set of loop names, so it cannot grow unboundedly.

No interaction with the ride state machine, money/wallet deltas, auth, or any DB
table. Nothing is written to durable storage.

**Deliberate design choice with an operational consequence:** never-ticked loops
are *omitted* from the gauge rather than exported as `0`. A `0` reads as "last
ticked at the Unix epoch" — permanently stale — which would false-alarm on every
deploy for loops that legitimately wait for their first window
(`stripe_reconcile` sleeps until 02:00 UTC). The cost is that a loop which never
starts at all produces no series; an alert that needs to catch that must use
PromQL `absent()`. This is documented in the accessor's docstring and pinned by
a test.

## 5. User-experience effect

**Nobody.** Backend-only observability. No rider, driver, corporate-admin, or
internal-admin surface changes. Not visible mid-session. No copy change. The new
gauge is only readable via the `METRICS_AUTH_TOKEN`-gated `/metrics` endpoint.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/loop_monitor.py` | Added `_wall_heartbeats` map, epoch write in `record_heartbeat`, new `get_heartbeat_epochs()` | Provide an epoch value exportable as a Prometheus timestamp without disturbing monotonic staleness logic |
| `backend/server.py` | `/metrics` publishes `spinr_loop_heartbeat_timestamp_seconds{loop=}` at scrape time | ADR-010 §3 second, independent loop-stall detection path |
| `backend/tests/test_loop_heartbeat_gauge.py` | New — 6 tests | Pin epoch-vs-monotonic, absent-not-zero, copy semantics, and label rendering |

## 7. Before / after

Purely additive to `record_heartbeat` — no existing caller changes behavior:

```python
# Before
def record_heartbeat(loop_name: str) -> None:
    with _lock:
        _heartbeats[loop_name] = time.monotonic()
```

```python
# After — same single lock acquisition, one extra write
def record_heartbeat(loop_name: str) -> None:
    with _lock:
        _heartbeats[loop_name] = time.monotonic()
        _wall_heartbeats[loop_name] = time.time()
```

## 8. Rollback plan

`git revert` is sufficient and the qualifying conditions hold: nothing durable
is written, no live state is mutated (no ride rows, wallet deltas, Stripe
charges, or insurance-period rows), and the only consumer is a scrape endpoint
that nothing currently scrapes. Reverting removes the gauge on next deploy;
`get_loop_status()` and the `loop_watchdog` alert path are unaffected either way
because this diff does not touch them.

No feature flag: the gauge is inert until an agent scrapes `/metrics`, which is
itself gated on operator action (C11), so it effectively ships dark already.

## 9. Verification performed

- [x] Automated tests — `pytest tests/test_loop_heartbeat_gauge.py` → **6 passed**.
      Regression run of `test_metrics_auth.py`, `test_metrics_histogram.py`,
      `test_monitoring_health.py`, `test_loop_alert.py` → **24 passed**. Unit tier.
- [x] Explicit regression test that `get_loop_status()` still reports `ok` and
      still reads the monotonic clock.
- [x] Blast-radius grep performed — searched `loop_monitor`, `record_heartbeat`,
      `get_loop_status` across `backend/`; 56 call sites, all analysed in §4.
- [x] Reviewed against `CLAUDE.md` observability conventions — gauge name follows
      `spinr_<domain>_<metric>_<unit>`; the scrape-time refresh matches the
      adjacent Redis-gauge pattern.
- [x] Reviewed against `CLAUDE.md` "do not silently swallow errors" — the
      exception path around gauge export logs at `error` with `exc_info`
      rather than `pass` (ruff S110 caught the first draft, which used `pass`).
- [x] `ruff check` and `ruff format --check` clean on all three files.
- [ ] Not feature-flagged — justified above (ships dark by construction).

## 10. What was NOT verified

- **No staging or production scrape.** The gauge was verified through
  `render_prometheus()` in unit tests, not by an actual Prometheus/Grafana agent
  reading it from a deployed instance. Nothing scrapes `/metrics` yet (C11).
- **The stall alert is unproven.** `time() - spinr_loop_heartbeat_timestamp_seconds
  > 2 * interval` has never been evaluated against real data; this change makes
  the data available, it does not demonstrate the alert fires.
- **Never-ticked-loop behavior was tested in-process, not across a real deploy** —
  the claim that `stripe_reconcile` is absent until 02:00 UTC follows from the
  code path and existing `LOOP_THRESHOLDS` comments, not from an observed
  overnight run.
- **No multi-replica verification.** ADR-010 §4's warning that loop metrics must
  be evaluated per-provider and never summed is documented, not enforced or
  demonstrated here.
- `ruff check .` remains red repo-wide (36 pre-existing errors) — unrelated to
  this diff, flagged as standing gate decay per pre-merge gate #8.
