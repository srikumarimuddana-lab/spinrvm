# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-06 |
| Author | Claude Code (branch `claude/rideshare-monitoring-tools-iflbg2`) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (branch `claude/rideshare-monitoring-tools-iflbg2`) |
| Related issue or gap ID | ADR-010 §2; `ACTION_ITEMS.md` C11 / CR-2026-008 / issue #3295 |

## 1. Issue / gap identified

`spinr_dispatch_offer_to_accept_duration_ms` is recorded with
`DEFAULT_MS_BUCKETS`, whose bounds jump straight from `1000` to `2500` ms. The
dispatch SLA threshold — P95 offer→accept < **2000 ms** — falls inside that gap,
so `histogram_quantile()` has no bucket boundary at the threshold and must
linearly interpolate across a 1.5 s-wide bucket to decide whether the SLA was
breached. Identified in ADR-010 §2, which flagged it as a real but non-blocking
gap to fix "once real traffic volume justifies the precision."

## 2. Root cause

Bucket bounds were chosen as a general-purpose latency ladder covering the whole
SLA table (fare calc < 300 ms, WS fan-out < 100 ms, dispatch < 2 s) rather than
per-metric. A single shared ladder cannot put an exact boundary at every
metric's individual threshold, and dispatch's 2000 ms was the one that landed
mid-bucket.

## 3. Fix / remediation

Added a dispatch-specific `DISPATCH_MS_BUCKETS` — `DEFAULT_MS_BUCKETS` plus an
explicit `2000` bound — and passed it at the single
`spinr_dispatch_offer_to_accept_duration_ms` call site. The 2 s SLA is now an
exact cumulative-bucket boundary, so the breach/no-breach decision is read
directly rather than interpolated.

`DEFAULT_MS_BUCKETS` is deliberately **unchanged**. `observe()` pins a metric's
bucket layout on its first observation (`utils/metrics.py:87`), so widening the
shared default would silently change the layout of every other metric that uses
it and invalidate already-recorded series.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** Grep results:

- `DEFAULT_MS_BUCKETS` — referenced only as the default parameter value of
  `observe()` (`utils/metrics.py:101`). No other module imports it. Unchanged
  by this diff, so every existing histogram keeps its current layout byte for byte.
- `buckets=` — `routes/drivers/ride_flow.py:320` is now the **only** explicit
  caller in the backend. No other call site's layout can be affected.
- `spinr_dispatch_offer_to_accept_duration_ms` — emitted from exactly one place
  (`routes/drivers/ride_flow.py:315`). Read by nothing today: nothing scrapes
  `/metrics` yet, which is the entire subject of C11.

No interaction with the ride state machine, the background loops, money/wallet
deltas, or any DB table. The change is confined to the `buckets` argument of one
instrumentation call; the surrounding `accept_ride` logic, its optimistic-lock
guard, and its WS emissions are untouched.

One genuine (accepted) consequence: a replica that has already observed this
metric under the old layout keeps the old layout until process restart, because
layout is pinned per process on first observation. Since nothing scrapes the
endpoint yet, no dashboard or alert can observe the transition. After a normal
deploy every replica is new, so the fleet is uniform.

## 5. User-experience effect

**Nobody.** Backend-only instrumentation. No rider, driver, corporate-admin, or
internal-admin surface reads this metric; there is no dashboard consuming it
yet. Not visible mid-session to anyone. No copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/metrics.py` | Added `DISPATCH_MS_BUCKETS` constant | Give the 2 s dispatch SLA an exact bucket boundary without touching the shared default |
| `backend/routes/drivers/_deps.py` | Re-export `DISPATCH_MS_BUCKETS` through both dual-import branches | Follow the established `_deps` shim pattern for the drivers package |
| `backend/routes/drivers/ride_flow.py` | Pass `buckets=DISPATCH_MS_BUCKETS` at the offer→accept observation | Apply the new layout at the one call site that has the 2 s SLA |
| `backend/tests/test_dispatch_metrics.py` | Extended the accept_ride test; added 2 bucket tests | Pin the call site's layout and the integral `le="2000"` rendering |

## 7. Before / after

```python
# Before — layout defaults to DEFAULT_MS_BUCKETS (…1000, 2500…), so a 2000 ms
# SLA check interpolates across the 1000→2500 bucket.
_metric_observe(
    "spinr_dispatch_offer_to_accept_duration_ms",
    (datetime.now(timezone.utc) - offered_at).total_seconds() * 1000.0,
)
```

```python
# After — explicit 2000 bound, so histogram_quantile() reads the SLA boundary.
_metric_observe(
    "spinr_dispatch_offer_to_accept_duration_ms",
    (datetime.now(timezone.utc) - offered_at).total_seconds() * 1000.0,
    buckets=DISPATCH_MS_BUCKETS,
)
```

Resulting exposition (verified locally, 1500 ms sample):

```
spinr_..._bucket{le="1000"} 0
spinr_..._bucket{le="2000"} 1     # <- new, integral label, cumulative
spinr_..._bucket{le="2500"} 1
```

## 8. Rollback plan

`git revert` is a complete and sufficient rollback here, which is normally *not*
acceptable — but the qualifying conditions genuinely hold: the change writes no
data, touches no live state (no ride rows, wallet deltas, Stripe charges, or
insurance-period rows), and has no consumer to break. Reverting restores the
previous bucket layout on the next deploy, and because layout is pinned per
process at first observation, restarted replicas immediately record under the
old bounds again. Nothing needs data-level remediation because nothing durable
was written.

No feature flag: a flag on a histogram's bucket layout would itself split the
series into two layouts, which is worse than either layout alone.

## 9. Verification performed

- [x] Automated tests run — `pytest tests/test_dispatch_metrics.py` → **5 passed**
      (3 pre-existing + 2 new). Unit tier.
- [x] Direct verification of exposition output: confirmed `le="2000"` renders
      integral (not `"2000.0"`, which would silently fail to match the ADR-010 §3
      alert expression) and that cumulative counting is correct across the bound.
- [x] Blast-radius grep performed — searched `DEFAULT_MS_BUCKETS`, `buckets=`,
      and `spinr_dispatch_offer_to_accept_duration_ms` across `backend/`.
      Results in §4.
- [x] Reviewed against `CLAUDE.md` observability conventions — metric name
      unchanged and still matches `spinr_<domain>_<metric>_<unit>` with the
      `_duration_ms` histogram suffix.
- [x] `ruff format --check` clean on all four files.
- [ ] Not feature-flagged — justified above (a flag would fragment the series).
- [ ] Not manually reproduced in staging — see §10.

## 10. What was NOT verified

- **No staging run.** Verified against `mock_supabase_client` fixtures only; the
  metric was not observed end-to-end through a real dispatch on staging.
- **No production build run** — not applicable, this is a backend-only change
  with no `admin-dashboard`/`rider-app`/`driver-app` code in the diff.
- **The alert expression itself is unproven.** ADR-010 §3's PromQL has never run
  against real data, because nothing scrapes `/metrics` yet (C11). This change
  makes the threshold *exact*; it does not demonstrate the alert fires correctly.
- **`ruff check .` is red repo-wide (36 pre-existing errors), including one
  B905 in `utils/metrics.py:194`** (`zip()` without `strict=`) inside
  `render_prometheus()` — a function this diff does not modify. Confirmed
  pre-existing by running ruff against `HEAD`. Not fixed here to keep the commit
  to one logical change; flagged as standing gate decay per `CLAUDE.md`
  pre-merge gate #8.
