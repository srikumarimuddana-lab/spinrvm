# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | local worktree commit (see git log; not pushed/PR'd per task instructions) |
| Related issue or gap ID | Ranked blocker #25 / audit finding N10 — `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` |

## 1. Issue / gap identified

`POST /drivers/location-batch`'s legacy (v1) handler — the driver-location
write path, the tightest SLA budget in the system at P95 < 150ms — fetched
the same `drivers` row for the same `user_id` **twice**, sequentially, in the
same request handler: once early (for the GPS-integrity check and the
Period-1 distance accumulator), and again immediately before the
presence-refresh (`mark_present`) call at the end of the function.

## 2. Root cause

The two reads were added at different times for different purposes (GPS
plausibility/Period-1 work vs. the presence-keepalive addition) and neither
was threaded through to the other — the second one was written as a fresh,
independent `db_supabase.get_rows("drivers", {"user_id": ...}, limit=1)`
call instead of reusing the `driver_row` local variable that the first fetch
already populated a few lines earlier. Confirmed by reading the current file
in full (not by trusting the audit's original line numbers, which were
already stale due to the concurrent GPS-plausibility-check work landing in
this file this session): the actual duplicate lived at what is now
`location.py` lines ~525 (`driver_row = driver_rows[0] if driver_rows else
None`, from the fetch at line 494) and ~578-580 (a second, near-identical
`get_rows("drivers", {"user_id": current_user["id"]}, limit=1)` call). The
v2 batch path (`_persist_v2_location_batch`) was already correctly
deduplicated — it fetches the driver row once and threads it through,
including into `driver_last_known` for the plausibility check — so N10 was
specific to the v1 (legacy) path, not the newer GPS-plausibility code.

## 3. Fix / remediation

Removed the second `drivers` fetch. The presence-refresh block now reuses
the `driver_row` variable already populated by the first fetch (which
happened only a few lines above, before the single `update_one` write to
this row). This is a pure read-elimination, not a parallelization — the two
reads were a true duplicate (same table, same filter, same row), not two
different pieces of data that happen to overlap.

**Why the reused value is guaranteed identical to what a second read would
have returned:** the only field read from the second fetch is `is_online`.
Between the first fetch and where the second fetch used to run, the only
write to this driver row is the `update_one("drivers", {"user_id":
current_user["id"]}, update_data)` call a few lines earlier, and
`update_data` only ever contains `lat`, `lng`, `updated_at`, optionally
`heading`, and optionally `period1_accum_km`/`period1_accum_since` — it
never sets `is_online`. No other write to this row is possible between the
two reads within this single-threaded async handler (no `await` point that
yields control to another coroutine which could plausibly flip this
specific driver's `is_online` mid-request in a way that would change
correctness of the presence keepalive — and even if it somehow did, using
the slightly-earlier value is the same class of staleness the system
already accepts elsewhere, since presence-refresh is best-effort by design).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped for every other caller of
  `update_location_batch` / `_persist_v2_location_batch` in
  `backend/` (excluding `tests/`): the only production caller is the
  route registration in `routes/drivers/__init__.py` (re-export), which
  wires it to the single `POST /drivers/location-batch` endpoint. No other
  route, background loop, or service calls either function directly.
- **v2 path untouched.** `_persist_v2_location_batch` was not touched — it
  already had a single fetch.
- **No other reader.** Nothing else in this handler reads the removed
  fetch's result — it was assigned to `driver_row`, immediately consumed by
  one `if driver_row and driver_row.get("is_online")` check, and discarded.
- **Freshness was not a real requirement here.** The removed fetch was not
  serving any staleness-sensitive purpose — `is_online` is driver-toggled,
  not something that flips mid-request from other activity triggered by
  this same request. The GPS-plausibility check, Period-1 accumulator, and
  breadcrumb persistence are all unaffected — they read from the same
  first-fetch `driver_row`/`driver_id` they already used before this
  change.
- **No interaction with the 16 background loops**, the ride state machine,
  or money/wallet deltas.

## 5. User-experience effect

None visible to any user. This is a backend-only latency/read-count
optimization; the response shape, status codes, and all persisted data are
unchanged. Not visible mid-session to anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/location.py` | Removed the second `db_supabase.get_rows("drivers", {"user_id": ...}, limit=1)` call in `update_location_batch`'s presence-refresh block; reuses the `driver_row` populated by the earlier fetch instead | Eliminate a byte-for-byte duplicate DB read on the <150ms driver-location-write hot path (audit N10 / ranked #25) |
| `backend/tests/test_p3_background_location.py` | Added `test_single_driver_row_fetch_online_driver`, asserting the `drivers` table is fetched exactly once per request (not twice) and that `mark_present` is still awaited with the correct driver id | Regression coverage for the fix; a call-count assertion catches a reintroduced duplicate fetch reliably |

## 7. Before / after

```python
# Before
        driver_row = driver_rows[0] if driver_rows else None
        if driver_row is not None and driver_row.get("is_online"):
            ...  # Period-1 accumulator logic using driver_row

        await db_supabase.update_one("drivers", {"user_id": current_user["id"]}, update_data)
        ...
        try:
            await persist_ride_breadcrumbs(driver_id, points)
        except Exception:
            logger.error("location-batch breadcrumb persist failed", exc_info=True)

        # Keep presence alive ...
        driver_row = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        )
        if driver_row and driver_row.get("is_online"):
            await _deps.mark_present(driver_row["id"])
```

```python
# After
        driver_row = driver_rows[0] if driver_rows else None
        if driver_row is not None and driver_row.get("is_online"):
            ...  # Period-1 accumulator logic using driver_row (unchanged)

        await db_supabase.update_one("drivers", {"user_id": current_user["id"]}, update_data)
        ...
        try:
            await persist_ride_breadcrumbs(driver_id, points)
        except Exception:
            logger.error("location-batch breadcrumb persist failed", exc_info=True)

        # Keep presence alive ... Reuses `driver_row` fetched above instead
        # of a second `drivers` fetch for the same user_id (audit N10).
        if driver_row and driver_row.get("is_online"):
            await _deps.mark_present(driver_row["id"])
```

## 8. Rollback plan

No feature flag needed — this is a pure internal read-elimination with no
externally observable behavior change and no data migration involved.
Rollback is a plain code revert of the one commit (`git revert <sha>`); no
live data (Stripe charges, wallet deltas, ride state, insurance-period
rows) is touched by this change, so a code-only revert is a complete and
sufficient rollback plan here — the general "`git revert` is not a rollback
plan" caveat in `CLAUDE.md` applies to changes that touched live data, which
this one does not.

## 9. Verification performed

- [x] Automated tests run (unit, via `/tmp/spinr-venv/bin/pytest`, `--no-cov`
      for the multi-file runs to avoid the repo-wide coverage gate irrelevant
      to a single-file change):
  - `tests/test_p3_background_location.py` — 11 passed, 1 xfailed (pre-existing
    real-device xfail, unrelated to this change) — includes the new
    call-count regression test.
  - `tests/test_location_batch.py`, `tests/test_location_integrity_coverage.py`,
    `tests/test_breadcrumb_persistence.py`, `tests/test_drivers_extended.py`,
    `tests/test_period1_accumulation_endpoint.py`,
    `tests/test_location_batch_revoked_session.py` — 184 passed.
  - `tests/test_breadcrumb_buffer.py`, `tests/test_breadcrumbs_late_tail.py`,
    `tests/test_live_breadcrumbs.py` — 21 passed.
  - `ruff check` on both modified files — all checks passed.
- [x] Blast-radius grep performed: `grep -rn "update_location_batch\|_persist_v2_location_batch"`
      across `backend/` outside `tests/` — only the route registration in
      `routes/drivers/__init__.py`; no other caller.
- [x] Reviewed against relevant `CLAUDE.md` conventions: driver
      online/available invariant untouched (this fix reads `is_online`,
      never writes it); "N+1 Supabase reads" anti-pattern is exactly what
      this fix removes; no money/state-machine code touched.
- [x] Feature flag: not applicable — internal read-count optimization,
      not a user-visible or business-logic change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; no live-data
      dependency)
- [x] Blast radius is stated, not assumed: isolated to one function,
      one route, confirmed by grep
- [x] No silent behavior change to an already-shipped flow — the fix is
      purely a redundant-read removal; response shape and all persisted
      fields are identical, and the "What was NOT verified" section below
      states the two boundaries this claim rests on.

## What was NOT verified

- **No real <150ms SLA timing measurement was taken.** This sandbox has no
  live Supabase/Postgres connection and no load-testing harness wired to
  this endpoint, so the performance win (one fewer sequential network round
  trip on the hot path) is an architectural improvement reasoned about from
  the code, not a benchmarked before/after latency number. Do not read this
  change-log as claiming a measured SLA improvement — only that the
  request now makes N-1 DB round trips for the same inputs (confirmed by
  the added call-count assertion), which removing a fully-sequential
  `await` should reduce wall-clock time on any real network, proportional
  to that call's actual latency in production.
- **Not tested against a live Supabase instance** — only against
  `mock_supabase_client`/hand-rolled `get_rows`/`update_one` fakes per this
  repo's existing unit-test convention. No integration-tier test exists for
  this endpoint in this repo currently.
- **No production build step applies** — this is a `backend/` (Python)
  change; there is no `admin-dashboard`/`rider-app`/`driver-app` build to
  run for this fix, and none was run.
