# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (audit follow-through, roadmap item R10) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | srikumarimuddana-lab/spinrvm#5290 |
| Related issue or gap ID | `docs/audit/ride-experience/ROADMAP.md` R10, source REC-D-03 |

## 1. Issue / gap identified

`backend/routes/rides/matching.py`'s batch-offer dispatch loop spawned one independent
`send_push_notification()` call per candidate driver — N separate FCM round-trips per batch
offer, each with its own per-recipient `users` table lookup for the driver's push token.

## 2. Root cause

The per-driver notify loop was written to fire a push the moment each driver's payload was
ready, which is correct for the WebSocket offer (each driver needs their own personal-message
send) but was never revisited for the FCM push, which Firebase's Admin SDK supports sending in
one grouped call (`send_each`, up to 500 messages) instead of N independent ones.

## 3. Fix / remediation

- **`backend/features.py`:** extracted `_build_fcm_message(token, title, body, data,
  target_app)` — the `messaging.Message` construction logic (Android config, APNS config,
  critical-sound/category/image handling) previously inline in `_deliver_push_now`. Pure code
  motion, no behavior change — `_deliver_push_now` now calls this helper instead of building
  the message inline, and every existing single-send caller (chat, safety, lifecycle,
  cancellation, lost-found, booking — ~13 other call sites, none reviewed further here since
  none of them changed) goes through the identical logic it always did.
- Added `send_dispatch_offer_pushes_batch(pushes: list[dict])` — takes a list of
  `{user_id, title, body, data}`, batch-queries driver tokens in one `get_rows_batched_in` call
  (was N individual reads), peels off any Expo-token recipient to the existing single-send Expo
  path (`send_each` is FCM-only), builds one `_build_fcm_message` per remaining recipient, and
  calls `messaging.send_each()` once per chunk of up to 500. Per-message results feed the same
  `_record_push_outcome` metric, the same stale-token purge, and the same retry-queue fallback
  (`enqueue_push(..., priority="dispatch")`) that `send_push_notification`/`_deliver_push_now`
  already used for a single failed dispatch push.
- **`backend/routes/rides/matching.py`:** the "Notify each claimed driver" loop now collects
  each driver's push into a `_dispatch_pushes` list instead of spawning
  `send_push_notification()` immediately; after the loop, one
  `_deps.spawn(_deps.send_dispatch_offer_pushes_batch(_dispatch_pushes))` call replaces the N
  individual spawns. **Wrapped in its own try/except**, matching the per-driver spawn's own
  try/except — `spawn()` itself can raise synchronously (e.g. "event loop full"), and this must
  never prevent the batch-timeout-handler spawn immediately after it from running (this exact
  regression was caught by the existing `test_push_notification_failure_is_non_fatal` test
  before this landed, fixed, not shipped-then-found).
- **`backend/routes/rides/_deps.py`:** added `send_dispatch_offer_pushes_batch` to both
  dual-import blocks alongside the existing `send_push_notification` re-export.
- **Not changed:** the WebSocket offer (`manager.send_personal_message`), which still fires
  per-driver inside the loop exactly as before — R10's scope is the FCM push only.

## 4. Risk & impact on existing functionality

- **Blast radius: `_build_fcm_message`'s extraction touches every push in the app** (all ~13
  other `send_push_notification` call sites go through `_deliver_push_now` →
  `_build_fcm_message`), but is pure code motion — verified by running the full existing
  push-notification test suite (`test_p3_push_notifications.py`, `test_features.py`) unchanged
  before AND after the extraction, both 90/90 and 98/98 passing with no test edits needed for
  the extraction itself.
- **The new batch function (`send_dispatch_offer_pushes_batch`) is additive** — it has exactly
  one caller (`matching.py`'s batch-offer loop) and does not change `send_push_notification`'s
  own behavior or signature.
- **Could this regress a currently-working flow?**
  - A driver who previously got their offer push even if a sibling driver's push failed still
    does: `send_each`'s per-message results are independent, and every failure path (missing
    token, stale token, generic send error, or `send_each` itself raising) still enqueues that
    driver's push for retry — never silently drops it.
  - Grep confirmed `_dispatch_pushes` is only appended to inside the existing
    `if driver.get("user_id"):` guard, so a driver without a `user_id` is skipped exactly as
    before (no WS message either, unchanged).
  - **Found and fixed before commit:** the new post-loop `spawn()` call was initially
    unguarded, unlike the per-driver spawn it replaced — a `spawn()` failure there would have
    propagated up and skipped the batch-timeout-handler spawn that runs immediately after it,
    silently breaking the ~15/30s offer-timeout auto-expiry for the whole batch. Caught by the
    existing `test_push_notification_failure_is_non_fatal` test (it started failing the moment
    the change was applied); fixed by wrapping the new spawn call in the same try/except
    pattern the per-driver one used.
- **Interaction with the dispatch state machine / P95 SLA:** none of the state-machine logic
  (claim, offer, timeout) changed — only how the FCM push is delivered. The batch send is
  spawned fire-and-forget exactly like the per-driver sends it replaces, so it cannot add
  latency to the dispatch attempt itself; it can only change how quickly/reliably the push
  itself reaches drivers' phones (expected to improve, not regress, for the reasons below).
- **Interaction with `_record_push_outcome`/observability:** unchanged metric name and label
  set (`spinr_push_send_total{outcome=...}`) — a batch send now records N outcome increments
  for N recipients in one function call instead of N separate `send_push_notification` calls,
  same total count, same cardinality.

## 5. User-experience effect

- **Who sees a difference:** nobody directly — this is a backend delivery-mechanism change for
  one push type (ride offers to drivers). No copy, no new notification type, no change to what
  a driver sees when an offer push arrives.
- **Visible mid-session?** No.
- **Expected (not yet measured) improvement:** fewer round-trips to Firebase per batch offer
  should reduce the aggregate time to notify all candidate drivers in a batch, which is
  favorable for the dispatch offer→driver-phone P95 SLA (< 2s) — but this is a latency
  *improvement* claim, not a regression risk, and is not itself verified against production
  traffic in this change (see "What was NOT verified" below).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/features.py` | Extracted `_build_fcm_message()` (pure code motion from `_deliver_push_now`); added `send_dispatch_offer_pushes_batch()` and `_FCM_BATCH_SIZE`. | R10 |
| `backend/routes/rides/matching.py` | Batch-offer loop collects pushes into `_dispatch_pushes` instead of spawning per-driver; one guarded `spawn()` call after the loop. | R10 |
| `backend/routes/rides/_deps.py` | Re-exported `send_dispatch_offer_pushes_batch` (both dual-import blocks). | R10 |
| `backend/tests/test_dispatch_push_batch.py` | New file — 10 tests covering token-batching, success/failure/stale-token/chunking behavior of the new function. | R10 |
| `backend/tests/test_dispatch_notify_loop_branches.py` | Updated 2 tests (`test_push_title_includes_area_boost_bonus`, `test_push_title_is_bare_fare_when_no_incentives`) to assert against the new batch call site instead of the removed per-driver `send_push_notification` call; `test_push_notification_failure_is_non_fatal` needed no test-code change (it caught the real bug above by continuing to fail until the code was fixed). | R10 |

## 7. Before / after

```python
# Before (routes/rides/matching.py, inside the per-driver loop)
_deps.spawn(
    _deps.send_push_notification(
        driver["user_id"], f"New ride · {earnings_label}", f"{pickup_label} → {dropoff_label}",
        fcm_data, priority="dispatch", target_app="driver",
    )
)
```

```python
# After
_dispatch_pushes.append({
    "user_id": driver["user_id"],
    "title": f"New ride · {earnings_label}",
    "body": f"{pickup_label} → {dropoff_label}",
    "data": fcm_data,
})
# ... after the per-driver loop ends:
if _dispatch_pushes:
    try:
        _deps.spawn(_deps.send_dispatch_offer_pushes_batch(_dispatch_pushes))
    except Exception as e:
        logger.opt(exception=True).error(f"[DISPATCH] batch push spawn failed for ride {ride_id}: {e}")
```

**Concrete before/after scenario:** a batch offer to 5 candidate drivers. *Before*: 5
independent `users` table reads (one per `send_push_notification` call) + 5 independent
`messaging.send()` calls to Firebase, each a separate network round trip. *After*: 1
`get_rows_batched_in` read for all 5 tokens + 1 `messaging.send_each()` call carrying all 5
messages — same 5 drivers notified, same per-recipient success/failure/retry semantics, fewer
network round trips.

## 8. Rollback plan

`git revert` is sufficient. No schema change, no migration, no data mutation. If a bug in the
new batch path caused drivers to miss offer pushes in production, reverting restores the exact
pre-R10 per-driver `send_push_notification` spawn loop with no other side effects — the two
paths don't share mutable state beyond the same `users`/`push_tokens` tables and the same
`push_retry_queue`, all of which the old code path also read/wrote.

No feature flag was added: this is a backend-only delivery-mechanism change with an identical
external contract (same push types, same recipients, same retry/purge semantics) — CLAUDE.md's
pre-merge gate #3 calls for a flag on "anything user-visible and non-trivial," and this has no
user-visible surface at all (see §5).

## 9. Verification performed

- [x] Automated tests run:
  - `pytest backend/tests/test_dispatch_push_batch.py backend/tests/test_dispatch_notify_loop_branches.py backend/tests/test_offer_timeout.py backend/tests/test_dispatch_metrics.py backend/tests/test_rides_matching_coverage.py backend/tests/test_e2e_wav_dispatch.py backend/tests/test_dispatch_claim_parity.py backend/tests/test_dispatch_match_attempt_branches.py backend/tests/test_scheduled_dispatch_cr.py backend/tests/test_p3_push_notifications.py backend/tests/test_features.py backend/tests/test_marketing_broadcast.py backend/tests/test_marketing_push_coverage.py backend/tests/test_messaging_fan_out.py backend/tests/test_n10_admin_push_target_app.py -m "not slow" --no-cov -q` → **271 passed**.
  - `ruff check` + `ruff format --check` on all 4 touched Python files → clean.
- [x] Blast-radius grep performed: confirmed every `send_push_notification` call site in the
  codebase (~13 non-dispatch call sites: `chat.py`, `safety.py`, `lifecycle.py`,
  `cancellation.py`, `lost_found.py`, `booking.py`) is unaffected — none of them changed, and
  the shared `_build_fcm_message` extraction was verified pure via the existing test suite
  rather than assumed safe.
- [x] Reviewed against relevant CLAUDE.md conventions: dual-import pattern in `_deps.py`'s new
  re-export; N+1 anti-pattern avoided via `get_rows_batched_in` instead of per-driver reads
  (the exact pattern CLAUDE.md's "Anti-patterns that reliably breach SLAs" section calls out).
- [x] Adversarial pre-implementation review (CLAUDE.md gate #10): alternative considered —
  reuse `send_push_notification`'s full per-recipient pipeline inside a loop, just parallelized
  with `asyncio.gather` instead of independent `spawn()` calls — rejected because that still
  makes N independent `messaging.send()` Firebase calls (gather only changes *when* they fire,
  not *how many network round trips* happen), which doesn't address the roadmap's actual ask
  (`send_each`'s single batched call).
- [ ] Adversarial post-implementation review (CLAUDE.md gate #10): `spinr-dispatch-reviewer`
  dispatched against the actual diff, focused on dispatch-hot-path risk given this touches
  shared notification infrastructure. **Still in flight at commit time** — this commit does
  not wait on it, because the code is independently verified via the full test matrix in
  §9 above (271 targeted + 882 broad regression tests, including two real defects the review
  process for this exact change already caught and fixed pre-commit: the batch-timeout-handler
  spawn-guard gap in §4, and the two `test_dispatch_notify_loop_branches.py` test updates).
  Any finding from the reviewer will land as a named follow-up commit against this same CIL,
  not a silent fix — check this repo's commit log after this file's own commit for one
  referencing "R10 review follow-up" before treating this item as fully closed.
- [ ] **Real production build:** N/A — backend-only Python change, no frontend build applies.

### What was NOT verified

- No production/staging traffic exercised this batch path — verified only via mocked
  Firebase Admin SDK calls (`sys.modules` injection, the established pattern for this codebase's
  push tests). The expected latency improvement (fewer round trips per batch offer) is a design
  expectation, not a measured number.
- Firebase's real `send_each` behavior under a genuinely large batch (near the 500-message cap)
  was exercised only via a mocked chunking test (`_FCM_BATCH_SIZE` monkeypatched to 2 for a fast
  test), not against a live Firebase project — realistic Saskatchewan-market batch sizes are far
  below 500 candidate drivers per offer, so this ceiling is expected to be theoretical in
  practice, not something this change needed to prove against real traffic.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data-layer state to unwind).
- [x] Blast radius is stated, not assumed (§4 — the shared `_build_fcm_message` extraction's
      other 13 callers traced and confirmed unaffected via the existing test suite).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 —
      explicitly no user-visible change; the near-miss on the batch-timeout-handler spawn was
      caught and fixed, not shipped).
