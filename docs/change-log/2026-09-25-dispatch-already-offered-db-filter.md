# Change Impact & Risk Log — durable "already offered" dispatch filter

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session, for the founder (requested "Add the redis fix") |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | srikumarimuddana-lab/spinrvm#5776 |
| Related issue or gap ID | Found while planning Phase 1 of `.claude/plans/2026-09-25-dispatch-reoffer-and-search-window.md` |

## 1. Issue / gap identified

During a Redis outage, or after a driver's 300 s offer-skip key expires, a
driver who was already offered a ride can be ranked for it again. On the
default PostgREST claim path that stalls the ride. Found by code reading, not
by an incident.

## 2. Root cause

`ride_offers` has `UNIQUE (ride_id, driver_id)` (`ride_offers_ride_driver_uq`,
migration 100). The only thing keeping an already-offered driver out of the
candidate pool was the Redis key `spinr:offer_skip:{ride}:{driver}` (TTL
300 s). The primary skip MGET fails open on a Redis error
(`matching.py`, "Fail open (no skips)") and the cascade's skip filter sits
inside a try that also fails open. Once such a driver is ranked,
`claim_driver_atomic` succeeds (it only checks `drivers.is_available`), the
batch `ride_offers` insert violates the unique constraint, the whole round's
claimed drivers are released and the attempt raises — again on every 10 s
retry while that driver ranks in the top N. The v3 RPC (`ALREADY_OFFERED`)
and direct pool (`ON CONFLICT DO NOTHING`) do not stall but waste an offer
slot. Scheduled rides can search longer than 300 s, so they can hit this
without any Redis outage.

## 3. Fix / remediation

New `_already_offered_driver_ids(ride_id)` in `backend/routes/rides/matching.py`
reads `ride_offers.driver_id` for the ride (uses the `ride_offers(ride_id)`
index from migration 100, row cap 1000). Each dispatch attempt removes those
drivers from the primary pool (after the Redis skip filter) and from the
vehicle-cascade pool (both the v2 and legacy presence branches). If the read
fails it raises: `match_driver_to_ride`'s recovery shell logs it and re-arms
`_dispatch_retry` with 10/30/60 s backoff. (First version fell back to the
Redis-only filter; Codex review P1 on #5776 — correctly — pointed out that
continuing without the filter re-opens the exact batch-insert failure this
fixes.)

No feature flag: excluding a driver who holds an offer row can only remove a
candidate the claim step would have rejected or crashed on, so there is no
behaviour to keep behind a switch.

Alternative considered: re-read the offers only after a Redis MGET failure.
Rejected — it does not cover the expired-key case (scheduled rides, or any
search longer than 300 s), which needs no outage.

## 4. Risk & impact on existing functionality

Blast radius: single-surface (backend dispatch hot path).

- Callers: `_match_driver_to_ride_attempt` only (via `match_driver_to_ride`,
  which is called from booking, the batch/single offer timeout handlers,
  `offer_expiry_reaper`, `_dispatch_retry`, decline re-dispatch, scheduled
  dispatch and admin tools). All go through the same filter.
- Readers of `ride_offers`: unchanged; this adds one more read.
- Latency: one indexed read per dispatch attempt on the P95 < 2 s offer path.
  Not measured.
- Behaviour: a driver is never re-offered a ride they already had an offer
  row for. That was already impossible (unique constraint); this only stops
  the failed attempt. Pending-offer drivers are claimed (`is_available`
  false) and are not in the pool anyway.
- Tests: `backend/tests/conftest.py` adds an autouse fixture that stubs the
  helper to "nobody offered" for every test module except the new one,
  because many existing dispatch tests fake `get_rows` with ordered
  side-effect lists that an extra read would shift. Those tests therefore do
  not exercise this filter; the new module does.
- No ride state, money, or insurance-period change.

## 5. User-experience effect

- Riders: a ride that would have stalled in "searching" until auto-cancel
  (Redis outage, or a long scheduled search) now keeps offering to other
  drivers. No copy change.
- Drivers: none.
- Visible mid-session only as the absence of that stall.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | `_already_offered_driver_ids()` + filter on the primary and cascade pools | Durable already-offered filter |
| `backend/tests/test_dispatch_already_offered_filter.py` | New: 5 cases (DB excludes with expired key, Redis outage, empty, read failure aborts before ranking, helper query) | Coverage |
| `backend/tests/conftest.py` | Autouse stub of the helper for other modules | Keep order-sensitive dispatch mocks stable |

## 7. Before / after

```python
# Before — only Redis, fails open
_skip_ids = {d["id"] for d, v in zip(all_drivers, _skip_vals) if v}
all_drivers = [d for d in all_drivers if d["id"] not in _skip_ids]

# After — plus the durable DB read
_offered_ids = await _already_offered_driver_ids(ride_id)   # error → raises into the retry shell
all_drivers = [d for d in all_drivers if d["id"] not in _offered_ids]
```

## 8. Rollback plan

Revert the commit and redeploy. There is no flag (see section 3) and no data
written, so a code revert fully restores today's behaviour. If the extra
read fails in production, the attempt is retried with backoff rather than
dispatched without the filter; a DB outage long enough to exhaust retries
would already fail the claim step itself.

## 9. Verification performed

- [ ] Automated tests run — **not run.** pytest cannot be installed in this
      sandbox (network policy blocks pypi.org). Written for CI on #5776.
- [x] `ruff check`, `ruff format`, `python -m py_compile`.
- [ ] Manual repro in staging — not done.
- [x] Blast-radius grep: `offer_skip` readers/writers, `ride_offers` unique
      constraint and indexes (migration 100), claim paths (PostgREST
      `claim_driver_atomic` + batch insert, `dispatch_claim_batch*`,
      `dispatch_claim_offers_v3`), existing dispatch tests mocking `get_rows`.
- [x] Reviewed against CLAUDE.md: no silent error swallowing (error log +
      traceback on read failure), loguru calls use `{}` placeholders and
      `opt(exception=True)`.

## What was NOT verified

- No test executed; the cascade-pool filter has no dedicated test (covered
  only by reading the code).
- The Redis-outage stall itself was reasoned from code, not reproduced.
- Added latency of the extra read not measured against the P95 target.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
