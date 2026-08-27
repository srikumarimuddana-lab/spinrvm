# Change Impact & Risk Log — GPS teleportation filter: timestamp wiring + stale-reference guards

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | srikumarimuddana-lab |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | Follow-up to PR #4616 (`fix/gps-teleportation-spike-filter`, head `b1862de`) |
| Related issue or gap ID | Codex-style review of #4616 — findings 1–5 |

## 1. Issue / gap identified

PR #4616 added `filter_teleportation_spikes()` to keep GPS noise out of the Period-1
deadhead accumulator, but its headline mechanism — the 200 km/h implied-speed rule —
never executed in production. Review of the PR found five defects; this change fixes
all five.

## 2. Root cause

Three independent layers each blocked the speed rule, so the filter always fell through
to its coarse "drop any hop > 10 km" fallback:

1. `period1_distance._normalize()` rebuilds every point as `{lat, lng, accuracy, speed}` —
   it never carried a timestamp, so no point reaching the filter had one.
2. Even with pass-through, the filter parsed timestamps with `float()`, which raises on
   both shapes production actually sends: `datetime` objects (v2 outbox rows, built
   in-process by `breadcrumbs.persist_idle_location_batch` and never round-tripped
   through Postgres) and ISO-8601 strings (legacy v1 payloads). Both exceptions were
   caught and silently treated as "no timestamp".
3. The key lookup missed `captured_at` / `device_timestamp`, which `breadcrumbs.
   _point_capture_time` treats as the authoritative capture time.

Separately, the distance fallback never advanced its reference point after a drop, so one
legitimate long hop stranded it and every later fix in the batch was also rejected; and
the null-coordinate branch never advanced `prev_ts`, inflating the next hop's elapsed
time enough to hide a spike.

## 3. Fix / remediation

- New shared accessor `gps_filtering.point_epoch_seconds()` reads every timestamp shape
  the pipeline emits (`ts`, `captured_at`, `device_timestamp`, `recorded_at`,
  `timestamp`; `datetime`, ISO-8601 string, or numeric epoch in seconds or milliseconds)
  and returns `None` instead of raising when a value is unusable.
- `_normalize()` now carries capture time through as `ts`, with a docstring stating why
  dropping it silently disables the speed rule.
- The elapsed interval is clamped to `MIN_PLAUSIBLE_INTERVAL_S` (1.0 s). Burst/fused
  location providers emit fixes microseconds apart, which divides normal ~11 m GPS
  scatter out to over a million km/h; clamping asks "could a vehicle cover this ground in
  one sampling interval?" instead of dividing by ~zero.
- After `MAX_CONSECUTIVE_SPIKE_DROPS` (3) consecutive disagreements the reference is
  presumed stale and the filter re-anchors, bounding a legitimate capture gap's cost to
  at most three fixes instead of the rest of the batch.
- The null-coordinate branch now advances `prev_ts`.
- `MAX_PLAUSIBLE_SPEED_KMH` renamed to `TELEPORT_MAX_SPEED_KMH` (value unchanged at
  200.0) so it is no longer two letters from `route_segments.MAX_PLAUSIBLE_SPEED_KPH`
  (180); magic numbers `10.0` and `3` are now named constants.
- Fixed the `ruff` I001 failure PR #4616 introduced in `period1_distance.py`.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the Period-1 accumulator.** Verified by grep, not assumed:

- `filter_teleportation_spikes` has exactly one consumer, `period1_distance.py`
  (`grep -rn "filter_teleportation_spikes" backend/`).
- `batch_incremental_distance_km` has exactly two production call sites, both in
  `routes/drivers/location.py` (`:198` v2 idle path, `:652` legacy v1 path). Both write
  only `period1_accum_km` / `period1_accum_since` on the `drivers` row.
- The other `gps_filtering` consumers — `trip_distance.py` (the **fare-affecting** settled
  trip distance), `ride_route_analyzer.py`, and `route_segments.py` — do **not** import the
  new filter and are unchanged. No fare, no money path, no ride-state path is touched.
- `MAX_PLAUSIBLE_SPEED_KMH` had only two references, both inside `gps_filtering.py`, so the
  rename reaches nothing else. The constant was introduced in unmerged PR #4616 and has
  never shipped.
- No DB schema, no migration, no background loop, no ride state transition, no WS event.
  `driver_insurance_periods` rows are **not** touched — this is the scalar km accumulator
  only, and raw breadcrumbs (the SGI audit trail) remain untouched, per the
  `gps_filtering` module contract.

**What could regress:** the accumulator's value itself. This change makes the filter
*more* selective than PR #4616 (spikes under 10 km are now dropped) and *less* selective
in two cases that were over-filtering (a timed long hop, and the batch tail after one).
For a driver with clean GPS, output is unchanged. The one behavior worth naming: a burst
of fixes sharing a capture instant but spread more than ~55 m apart is now judged
implausible; the re-anchor guard bounds that to three consecutive drops.

## 5. User-experience effect

Driver-facing, indirectly and off by default. `period1_accum_km` is gated behind the
`period1_distance_tracking_enabled` `app_settings` flag, so with the flag off there is no
observable change at all. With it on, a driver's Period-1 deadhead total becomes more
accurate in both directions: it no longer inflates from GPS spikes, and no longer silently
under-counts a real deadhead leg across a capture gap. No copy, notification, or screen
changes. Nothing is visible mid-session — the accumulator is a background scalar, not a
surface the driver app renders live.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/gps_filtering.py` | Added `point_epoch_seconds()`; rewrote `filter_teleportation_spikes()` with interval clamp + re-anchor guard + `prev_ts` fix; renamed `MAX_PLAUSIBLE_SPEED_KMH` → `TELEPORT_MAX_SPEED_KMH`; named `MAX_UNTIMED_HOP_KM`, `MAX_CONSECUTIVE_SPIKE_DROPS`, `MIN_PLAUSIBLE_INTERVAL_S`, `_EPOCH_MS_THRESHOLD`; imported `parse_iso_utc` | Make the speed rule actually run, and stop a stale reference from eating real distance |
| `backend/utils/period1_distance.py` | `_normalize()` emits `ts`; import block reformatted | Carry capture time to the filter; fix the `ruff` I001 PR #4616 introduced |
| `backend/tests/test_gps_teleportation_filter.py` | +7 unit tests (re-anchor bound, spike-and-return preserved, out-of-order, `prev_ts` advance, burst fixes, identical timestamps, sub-second spike, accessor shapes) | Cover each guard and each timestamp shape |
| `backend/tests/test_period1_distance.py` | +5 integration tests (sub-10 km spike, `datetime` / ISO / epoch-ms shapes, capture gap not zeroed) | Pin the wiring end-to-end — all five fail on `b1862de` |

## 7. Before / after

```python
# Before — period1_distance._normalize(): timestamp discarded, so the filter
# downstream could only ever apply its 10 km distance fallback.
return {
    "lat": lat,
    "lng": lng,
    "accuracy": point.get("accuracy"),
    "speed": point.get("speed"),
}

# Before — gps_filtering: float() raises on the datetime and ISO shapes
# production actually sends; both exceptions swallowed as "no timestamp".
raw = cur.get("timestamp") or cur.get("recorded_at")
if raw is not None:
    try:
        cur_ts = float(raw)
    except (TypeError, ValueError):
        pass
if cur_ts is not None and prev_ts is not None and cur_ts > prev_ts:
    dt_hours = (cur_ts - prev_ts) / 3600.0
    ...
```

```python
# After — capture time is parsed once at the boundary, in every shape.
return {
    "lat": lat,
    "lng": lng,
    "accuracy": point.get("accuracy"),
    "speed": point.get("speed"),
    "ts": point_epoch_seconds(point),
}

# After — sub-second jitter is clamped instead of dividing by ~zero, and a
# stale reference re-anchors rather than eating the rest of the batch.
if cur_ts is not None and prev_ts is not None and cur_ts >= prev_ts:
    elapsed_s = max(cur_ts - prev_ts, MIN_PLAUSIBLE_INTERVAL_S)
    implausible = dist_km / (elapsed_s / 3600.0) > max_speed_kmh
else:
    implausible = dist_km > max_untimed_hop_km

if implausible and consecutive_drops < max_consecutive_drops:
    dropped += 1
    consecutive_drops += 1
    continue
```

Measured effect on the two scenarios that motivated the change (real pipeline, via
`batch_incremental_distance_km`):

| Scenario | On `b1862de` | After |
|---|---|---|
| 5 km spike-and-back in 10 s (implied ~3600 km/h) | 9.896 km phantom | 0.111 km (correct) |
| Legit 15 km deadhead across a 600 s capture gap, then normal pings | 0.0 km (tail stranded) | 15.345 km (correct) |

## 8. Rollback plan

Two independent levers, neither needing a redeploy:

1. **Primary — flip `period1_distance_tracking_enabled` to `false`** in the `app_settings`
   Supabase table via the admin dashboard. That disables both call sites at
   `routes/drivers/location.py:198` and `:652`, so no accumulator write happens at all.
   This is the same flag that gates the feature today.
2. **Partial — no data remediation is needed.** The filter is a pure function in the
   *computation* path only: raw breadcrumbs in `driver_location_history` (the SGI audit
   trail) are never deleted or rewritten, so any `period1_accum_km` value can be
   recomputed from the retained raw rows after a revert. `git revert` is therefore
   sufficient for the code, and unlike wallet or ride-state changes there is no
   already-applied live-data side effect to unwind.

## 9. Verification performed

- [x] **Automated tests run.** 50 unit + integration tests across
      `test_gps_teleportation_filter.py` (16), `test_period1_distance.py` (13),
      `test_gps_filtering.py` (13), `test_idle_location_batch.py` (8) — all pass.
      Wider sweep of every plausible consumer
      (`-k "route or breadcrumb or location or trip_distance or gps or period1 or segment
      or driver or insurance"`): **3739 passed, 0 failed**.
- [x] **Regression tests genuinely fail without the fix.** All 5 new integration tests were
      re-run against the `b1862de` source and fail there; the 3 new guard scenarios were
      run against the old filter directly and produce the old (wrong) results.
- [x] **Blast-radius grep performed.** Searched `filter_teleportation_spikes`,
      `batch_incremental_distance_km`, `MAX_PLAUSIBLE_SPEED_KMH`, and every importer of
      `gps_filtering` across `backend/`. Consumers enumerated in section 4.
- [x] **Reviewed against `CLAUDE.md` conventions** — dual-import pattern preserved in both
      files; no float money math involved (distance, not currency); no error silently
      swallowed (the accessor returns `None` by documented contract rather than masking a
      DB/auth/payment failure); PIPEDA — no coordinates are logged or retained, only the
      scalar km, unchanged from before.
- [x] **`ruff check` and `ruff format --check` clean** on all four changed files (the I001
      that `b1862de` introduced is fixed).
- [ ] **Manual repro in staging — NOT performed.** See below.
- [x] **Feature flag** — already gated by `period1_distance_tracking_enabled`; no new flag
      needed.

## 10. What was NOT verified

- **Not run against live or staging Supabase.** All verification is against pure-function
  execution and the `mock_supabase_client` fixtures. The v2 row shape (`captured_at` /
  `timestamp` as `datetime` objects) was confirmed by reading
  `breadcrumbs.persist_idle_location_batch`'s construction and return of those rows, not by
  observing a live payload.
- **No real-device GPS trace was replayed.** The burst-fix scenario (fixes sharing a
  capture instant) is modelled from the `test_idle_location_batch` fixture that exposed it,
  not from a captured production trace. `MIN_PLAUSIBLE_INTERVAL_S = 1.0` and
  `MAX_CONSECUTIVE_SPIKE_DROPS = 3` are reasoned defaults, not values tuned against
  field data — worth revisiting if driver-reported deadhead totals look off once the flag
  is on.
- **No before/after comparison on historical production data.** How much `period1_accum_km`
  drift exists today from spikes that already landed is unmeasured; this change fixes the
  computation going forward and does not backfill.
- **No visual-regression tooling applies** — backend-only change, no UI surface.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`app_settings` flag flip, no redeploy)
- [x] Blast radius is stated with the greps that established it, not assumed
- [x] No silent behavior change to an already-shipped flow — the accumulator is
      flag-gated and its UX effect is stated in section 5
