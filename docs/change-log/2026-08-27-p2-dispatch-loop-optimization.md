# Change Impact & Risk Log — P2 dispatch + background-loop optimization

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (session-driven, per @srikumarimuddana) |
| Surface(s) | backend only |
| Domain (Sentry tag) | `dispatch`, `rides`, `corporate`, `drivers`, `safety` |
| PR / commit link | branch `claude/p2-dispatch-loop-optimization` |
| Related issue or gap ID | `docs/audit/2026-08-26-db-query-optimization-recommendations.md` P2 items #15–#21 (PR #4579) |

## 1. Issue / gap identified

The 2026-08-26 DB audit found the dispatch hot path and the background loops
generating far more queries than the work requires: the ride's `service_areas` row
re-read 4–5× per dispatch attempt, a per-driver quest query inside the notify loop, two
`driver_subscriptions` reads that differ only in projection, a guaranteed-uncached
driver read right after the atomic claim, two loop N+1s, an httpx pool a third the size
of the thread pool feeding it, and four high-frequency loops running on every replica.

Separately, two loops had a genuine **correctness** race, not just a load problem: both
did a read-then-write with no lock, so two replicas could both act.

## 2. Root cause

- **Dispatch `service_areas` re-reads**: five independent consumers (matching config,
  subscription gate, quota timezone, vehicle cascade, offer-card polygon) each fetched
  the row they needed, with no way to share. Each was a `SELECT *` carrying the `polygon`
  JSONB.
- **Quest N+1**: written inside the per-driver notify loop because that is where the
  banner is assembled.
- **Duplicate subscription read**: the gate and the quota filter were added at different
  times and each specified its own projection; nothing noticed they query identical rows.
- **Claim-then-read**: `claim_driver_atomic` returned a bool and discarded the row its
  own UPDATE returned, forcing the caller to re-read — and since the function invalidates
  the driver cache on both sides of that update, the re-read could never hit cache.
- **Loop N+1s**: `_latest_capture_time` answers "newest row for one ride", so it was
  called per ride; `document_expiry` was written per-driver end to end.
- **httpx pool**: only `keepalive_expiry` was ever set, so httpx's
  `max_keepalive_connections=20` default silently applied against a 64-thread executor.
- **Replay races**: `corporate_low_balance` and `kyb_reverification` predate the repo's
  atomic-claim convention.

## 3. Fix / remediation

| Item | Change |
|---|---|
| **B1** | Fetch the ride's `service_areas` row once per dispatch attempt and thread it through; `resolve_matching_config` gains an `area=` kwarg for the same reason it already has `app_settings=`. Parent row stays lazy behind a memoized closure. |
| **B2** | One `.in_("driver_id", uids)` quest query before the notify loop, keyed back per driver. |
| **B3** | The subscription gate carries the union of both projections; the quota filter narrows those rows in Python instead of re-querying. |
| **B4** | `claim_driver_atomic` returns the claimed row instead of a bool; the caller revalidates on it directly. |
| **B5** | `corporate_low_balance` and `kyb_reverification` converted to **atomic claims** (conditional update), not Redis locks. |
| **B6** | Shared `try_acquire_leader_lock()` in `redis_client`, applied to the four highest-frequency loops. |
| **B7** | `route_gap_monitor` uses one `DISTINCT ON` RPC (migration 371) instead of 1–2 queries per ride; `document_expiry` gains a column projection and one fleet-wide documents fetch. |
| **B8** | httpx pool sized against the run_sync thread pool. |
| **B9** | `CHANGE_IMPACT_LOG.md` template points at `_WATCHDOG_LOOP_NAMES` instead of citing a count that had already drifted (said 16; there are 39 plus the watchdog). |

## 4. Risk & impact on existing functionality

**Blast radius: cross-cutting.** B1–B4 are on the live dispatch path — the most
timing-sensitive surface in the product. B5–B7 change background loops. B8 affects every
DB call in the process.

| Change | What else touches it | Regression risk |
|---|---|---|
| B1 | Every dispatch attempt and every retry. The 5 consumers were each read and converted individually. | **Failure semantics were the real risk here.** Hoisting the read above `resolve_matching_config` would have made an area-lookup failure fatal where the cascade block previously survived it — a test caught this. On error the row is left empty AND `area=None` is passed down, so `resolve_matching_config` re-attempts and raises exactly as today. Dispatch must never quietly fall back to the global radius and rating floor. |
| B2 | Offer-card payload only. Cosmetic banner data. | A batch that fanned one driver's quest to everyone would be worse than the N+1; the regression test asserts per-driver correctness, not just the query count. Fails open as one unit now rather than per driver — accepted, the data is decorative. |
| B3 | The subscription gate and quota filter. | Dropping `rides_per_day` from the shared projection would make the quota filter treat every finite pass as unlimited — invisible to a query-count assertion, so the test asserts the projection explicitly. |
| B4 | `claim_driver_atomic`'s only production caller. Return type changed bool → `Optional[dict]`; truthiness unchanged. | The eligibility revalidation (`is_online`/`is_verified`/`status`) is untouched and now runs on a *strictly fresher* row — the state at the instant of the atomic claim, with no window for a concurrent write between claim and read. Test churn was the contract change, not behaviour drift. |
| B5 | `corporate_wallets.low_balance_notified_at`, `corporate_accounts.kyb_reverify_flagged_at`. Both columns are read by the same loops and by admin views. | **Ordering trade-off, taken deliberately:** the low-balance claim now precedes `send_email`, so a send failure after a won claim costs one missed nudge until the next 12h window. That is strictly better than emailing a customer twice. |
| B6 | The four loops, plus anything depending on their cadence. | Fails **open**, so a Redis error restores exactly today's every-replica behaviour. With `REDIS_URL` unset the in-process dict makes every replica win its own lock — also today's behaviour. The TTL rule is the sharp edge: a TTL ≥ the minimum sleep silently halves a loop's cadence. Respected per loop and documented at each call site. |
| B7 | `route_gap_monitor` feeds SOS-adjacent location-gap alerting; `document_expiry` gates Period 1+ eligibility and can **suspend drivers**. | The batching hazard is real and was designed around: a capped batch ordered by time would let busy rides push a quiet ride's newest row out of the result, reintroducing the exact blindness that let an 11-minute mid-trip outage go undetected (ride SPR-PE7TTB). `DISTINCT ON` avoids the cap entirely. For `document_expiry`, the test asserts each driver sees only its OWN documents — a leaky batch would suspend the wrong people. |
| B8 | Every Supabase call in the process. | Raising pool limits cannot starve anything; the prior state was the constrained one. `http2=False` deliberately untouched — the h2 hpack thread-safety race documented above it is unrelated and still real. |

**No interaction with**: the ride state machine (no transition added or changed), fare
calculation, wallet or Stripe money movement, surge, or insurance-period rows.

## 5. User-experience effect

- **Rider**: none. Dispatch produces the same offers to the same drivers; only the
  number of queries behind them changed.
- **Driver**: none intended. The offer-card quest banner is assembled from a batched
  query rather than a per-driver one, with the same "any one active quest" contract.
- **Corporate admin**: strictly better — a low-balance email that could arrive twice
  (once per replica) now arrives once.
- **Internal admin**: none.
- **Mid-session visibility**: none. No change is observable to a rider mid-ride or a
  driver online.
- **Copy / notifications**: unchanged. B5 changes *how often* an existing email can be
  sent, never its content.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | One area read per attempt; batched quest query; shared subscription read; claim returns the row | B1, B2, B3, B4 |
| `backend/services/dispatch_service.py` | `resolve_matching_config` accepts `area=` | B1 |
| `backend/repositories/driver_repo.py` | `claim_driver_atomic` returns the claimed row | B4 |
| `backend/repositories/corporate_repo.py` | `claim_low_balance_notification` added; `mark_kyb_reverify_flagged` made conditional | B5 |
| `backend/utils/corporate_low_balance.py`, `backend/utils/kyb_reverification.py` | Claim before acting; only the winner counts | B5 |
| `backend/utils/redis_client.py` | `loop_pod_id()` + `try_acquire_leader_lock()` (fails open, TTL floored at 1s) | B6 |
| `backend/utils/{route_finalizer,route_gap_monitor,push_retry,stuck_ride_sweeper}.py` | Leader lock per tick | B6 |
| `backend/migrations/371_route_gap_latest_captures_fn.sql` | New `DISTINCT ON` RPC | B7 |
| `backend/utils/route_gap_monitor.py` | `_latest_capture_time` → batched `_latest_capture_times` | B7 |
| `backend/utils/document_expiry.py` | Driver projection + one fleet-wide documents fetch; log debug → error | B7 |
| `backend/supabase_client.py` | httpx pool sized to the thread pool | B8 |
| `docs/templates/CHANGE_IMPACT_LOG.md` | Points at the loop registry instead of a stale count | B9 |
| `backend/tests/*` (17 files) | Fixtures for the new contracts + 12 new regression tests | — |

## 7. Before / after

**B4 — the claim (the highest-volume saving):**

```python
# Before — the atomic UPDATE had the fresh row and returned a bool; the
# follow-up read could never hit cache, because this function invalidates
# that cache entry on both sides of the update.
if await claim_driver_atomic(driver["id"]):
    fresh = await get_driver_by_id(driver["id"])
```

```python
# After — one round-trip, and a strictly fresher row
fresh = await claim_driver_atomic(driver["id"])
if fresh:
    ...
```

**B5 — the low-balance ordering (the correctness fix):**

```python
# Before — two replicas both clear the window, both email the billing contact
await send_email(to=company["billing_email"], subject=subject, body=body)
await mark_low_balance_notified(wallet_id=wallet["id"])
```

```python
# After — the DB picks the winner; the loser stays silent
if not await claim_low_balance_notification(
    wallet_id=wallet["id"], not_notified_since_iso=not_notified_since_iso
):
    return
await send_email(to=company["billing_email"], subject=subject, body=body)
```

**B7 — the route-gap lookup:**

```python
# Before — 1-2 queries per ride, up to 500 rides, every 15s, every replica
last_captured_at=await _latest_capture_time(str(ride_id))
```

```python
# After — one RPC for the whole tick
latest_captures = await _latest_capture_times([...])
last_captured_at=latest_captures.get(str(ride_id))
```

## 8. Rollback plan

- **B1–B4, B8 (code-only, no data written)**: `git revert` is complete. None writes to
  any table, moves money, mutates ride state, or creates an insurance-period row.
- **B5**: `git revert` restores the unconditional writes. **No data cleanup needed** —
  the claim only ever writes the same timestamp column the old code wrote, and writing
  it more conservatively cannot corrupt anything. `claim_low_balance_notification` can
  be left in place unused.
- **B6**: revert the four call sites; the helper is inert without callers. Alternatively,
  **unset `REDIS_URL` to disable every lock at once without a deploy** — the in-process
  fallback makes every replica win, restoring today's behaviour exactly. That is a real
  no-deploy rollback lever, not a theoretical one.
- **B7**: revert `route_gap_monitor.py` to the per-ride helper — it works whether or not
  the function exists. `DROP FUNCTION IF EXISTS public.route_gap_latest_captures(uuid[]);`
  is optional and separate. **Deploy ordering: migration 371 must be applied before or
  with the code, or the route-gap monitor errors every tick.**
- **No feature flag.** These are read-path and loop-cadence changes with no user-visible
  behaviour to dark-launch, except B5 — where the flagged-off state is the *bug*
  (duplicate customer emails), so shipping dark would mean deliberately keeping the
  defect running.

## 9. Verification performed

- [x] **Automated tests** — full `pytest -m "not slow"`, split into 10 chunks (a single
      process is killed by this container's memory ceiling; an environment limit, not a
      test failure). **12,785 passed, 7 skipped, 1 xfailed, 0 failed.**

      The first full run surfaced **11 real failures in `test_stale_p3_closer.py`**, and
      they were mine: renaming `_latest_capture_time` to the batched
      `_latest_capture_times` orphaned `utils/stale_p3_closer.py`, which imports it by
      name. My blast-radius grep had been scoped to `route_gap_monitor.py` and missed the
      cross-module caller. It surfaced badly, too — that file's tests patch the symbol by
      string, so `pkgutil.resolve_name` swallowed the `ImportError` and fell back to
      `getattr`, turning one broken import into 11 "module 'utils' has no attribute
      'stale_p3_closer'" errors pointing nowhere near the cause. Fixed by keeping a
      single-ride wrapper that delegates to the batched form, with a regression test.
- [x] **12 new regression tests, each verified to FAIL against the pre-fix code** — this
      was done by stashing only the source files and re-running, so the failure is proof
      the test has teeth, not an assumption. Two early drafts passed against the old code
      and were rewritten until they didn't (the first counted reads through a db handle
      the old code didn't use; the second asserted a kwarg's value where the old code
      passed no kwarg at all).
- [x] **Migration 371 validated against production, read-only in effect** — compiled
      under a throwaway name, run over every ride that has location history, and diffed
      against a SQL reproduction of the old per-ride logic: **identical for all 10 rides**.
      The throwaway was dropped and zero `_tmp_validate*` remain.
- [x] **Blast-radius greps performed** (see the caveat above — one was scoped too
      narrowly and a full-suite run caught it, not the grep) — every `service_areas` read
      on the dispatch path;
      every `claim_driver_atomic` caller (one in production, the rest mocks); every
      `mark_low_balance_notified` / `mark_kyb_reverify_flagged` caller; every driver field
      read by `document_expiry`; the executor size feeding the httpx pool.
- [x] **Reviewed against `CLAUDE.md` conventions** — dual-import pattern preserved in
      every touched module; the atomic-claim pattern preferred over Redis locks per
      `.claude/skills/spinr-background-loop`; loop TTL rule respected per loop; no money
      arithmetic touched; no PII added to logs (the `document_expiry` projection *narrows*
      what leaves the DB).
- [x] **Reviewer agents run** (automated PR review is dark per `ACTION_ITEMS.md` C9, so
      these were manual). Both found real defects and both are fixed in this branch:
      - `spinr-dispatch-reviewer` on B1–B4 → **three defects**, all confirmed against
        `origin/main` before fixing. The serious one: collapsing five `service_areas`
        reads also collapsed five *different* failure handlers, which I had treated as
        interchangeable. The subscription gate's read used to fail **closed**; my
        `_parent_area()` swallowed the error and returned `{}`, which reads as
        `subscription_required=False` — so a failed parent lookup stopped aborting the
        attempt and started dispatching to drivers with no pass. My own docstring
        asserted the opposite. Fixed by removing both swallows so each consumer is back
        on its original handler. Second defect: the `area=None`-on-failure hand-off
        assumed the resolver's retry would also fail; if it succeeded, nothing raised and
        `_ride_area` stayed `{}` — same bypass, second route. Third: B4's claimed row
        replaced a `get_driver_by_id` that filtered `deleted_at IS NULL`, so a
        soft-deleted driver could pass revalidation; the claim's UPDATE now filters it.
      - `spinr-realtime-reliability-reviewer` on B5–B7 → **verdict REPLAY-SAFE, no
        blockers**; TTL arithmetic verified correct per loop, all four confirmed already
        replay-safe, the `$or` PostgREST encoding verified at the wire level, and
        migration 371's precedence traced end-to-end. Two observability warnings, both
        fixed: `push_retry` recorded no heartbeat at all (pre-existing, but B6 made the
        silent branch the common case in production), and no test covered any loop's
        lock-denied branch — which is precisely why the heartbeat gap went unnoticed.
- [ ] **Manual repro in staging** — not performed; see below.
- [x] **Feature flag** — deliberately none; justified in §8.

## What was NOT verified

- **No staging run, and no dispatch dry-run against a real Supabase.** Everything was
  verified against `mock_supabase_client` fixtures, plus the read-only production
  comparison for migration 371's SQL. A real ride was never dispatched through this code.
  Given B1–B4 sit on the live dispatch path, **this is the most important gap in this
  change** and the reason a staging pass should precede merge.
- **Migration 371 is not applied anywhere.** It must be applied before or with the code.
- **The four leader locks were not exercised multi-replica.** Election was tested at the
  helper level with a mocked `redis_set_nx`; nothing verified two real replicas contending
  for one key. The failure mode if the TTL arithmetic is wrong is a silently halved loop
  cadence, which no test here would catch.
- **No before/after latency measurement.** The P95 dispatch claim (< 2 s) was not
  re-measured; the improvement is round-trip arithmetic, not a profile.
- **`repositories/corporate_repo.py::mark_low_balance_notified` is now dead** in the
  production call graph — B5 switched its only caller to the claim variant. Left in
  place: it is still re-exported from `db_supabase` and has its own direct unit test, so
  deleting a public helper is outside this change's scope. Flagged, not removed.
- **No frontend surface was touched, so no build was run.** There is also **no active
  visual-regression coverage on any surface** (`ACTION_ITEMS.md` B38) — irrelevant to this
  backend-only change, stated so silence doesn't imply coverage.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
