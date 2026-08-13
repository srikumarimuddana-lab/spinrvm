# Change Impact & Risk Log — rate-limit driver location ingestion

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (session: postgres-scaling-supabase) |
| Surface(s) | backend (driver-app facing) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/postgres-scaling-supabase-ypnwiy` |
| Related issue or gap ID | Found while auditing rate-limit keying for burst tolerance |

## 1. Issue / gap identified

`POST /drivers/location-batch` (`backend/routes/drivers/location.py`) — the
endpoint every online driver's background tracker posts GPS batches to — had
**no rate limit at all**.

The limiter intended for it, `location_update_limit` (60/minute), existed in
`backend/utils/rate_limiter.py` but decorated **nothing**: a repo-wide grep for
the symbol returned only its own definition. It was dead code, and the surface
it was written to protect was unprotected.

This matters for burst tolerance: driver GPS is the highest-frequency write path
in the system, it fans out to WebSocket subscribers, and a runaway or
retry-looping client could saturate the DB thread pool for everyone.

## 2. Root cause

The limiter was defined but never wired up — most likely the decoration step was
missed when the endpoint was written or refactored, and nothing failed loudly
because an unused limiter is silently valid Python.

Contributing factor: the endpoint's signature had no `request: Request`
parameter, which `AsyncLimiter` requires (it raises `TypeError` at import when
absent, `utils/async_limiter.py:70`). Adding the decorator alone would have
crashed the app at startup, so wiring it up needed the signature change too.

## 3. Fix / remediation

- Add `request: Request = None` to `update_location_batch`.
- Decorate it with `@location_update_limit`.
- Give `location_update_limit` the same per-user keying as the other limiters
  in this branch (`key_func=get_user_or_ip_key`), so one runaway device cannot
  spend the budget of other drivers behind the same carrier NAT.
- Export `location_update_limit` and `Request` through `routes/drivers/_deps.py`,
  following that module's existing dual-import pattern.

**Headroom check:** the driver app's outbox flushes every 5–15 s ⇒ roughly
4–12 requests/minute against a 60/minute limit. That is ~5× headroom over normal
operation; the limit only engages on a genuinely misbehaving client.

## 4. Risk & impact on existing functionality

**Blast radius: single endpoint, but a live driver-facing one.**

- `update_location_batch` is the only route decorated. `location_update_limit`
  had no other consumers (it decorated nothing before this change), so there is
  no other caller whose behavior shifts.
- **This is the one commit in this branch that can newly reject a request that
  previously always succeeded.** Everything else here is permissive. A driver
  exceeding 60 location posts/minute now receives a 429 where they previously
  got a 200. At the documented flush cadence that is ~5× normal, but the cadence
  is from the client's design, not measured production telemetry (see "What was
  NOT verified").
- **Both v1 and v2 batch paths** run behind the decorator (the limiter wraps the
  whole handler, ahead of `_parse_v2_location_batch`), so neither can bypass it.
- **The revoked-session guard still runs first in the handler body** for any
  request the limiter admits; ordering of `_guard_revoked_session` relative to
  the parse branches is unchanged.
- **Dispatch:** driver location feeds `find_nearby_drivers` and ETA. A
  throttled driver's *last known* location remains in the DB — a 429 drops one
  batch update, it does not remove the driver from dispatch or mark them
  offline. `is_online`/`is_available` are not touched by this path.
- **Insurance periods:** unaffected. Period transitions derive from ride state,
  not from location posts.
- **Redis:** one more counter key per active driver per minute. Negligible
  against existing presence/pub-sub load.
- **Background loops, money paths, ride state machine:** untouched.

## 5. User-experience effect

- **Drivers:** no visible change in normal operation — the limit sits ~5× above
  the app's own flush rate. A driver whose client misbehaves (retry storm, clock
  skew, duplicated tracker) will see location batches rejected with 429; the app
  already tolerates failed batch posts by retrying from its outbox, so the
  practical effect is backpressure rather than data loss.
- **Riders:** none directly. In the pathological case a rider could see a
  slightly staler driver pin for the throttled driver — which is the intended
  trade against that driver degrading the DB pool for everyone.
- **Visible mid-session?** Only for a driver already exceeding 60 posts/minute,
  which should not occur with the shipped client.
- **No copy, notification, or UI changes.** The 429 body is the standard
  rate-limit shape (`utils/rate_limiter.py:443-525`) the driver app already
  handles.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/location.py` | Added `Request` and `location_update_limit` to the `_deps` import; added `request: Request = None` to `update_location_batch`; applied `@location_update_limit`; docstring records the limit and its headroom | Wires the limiter onto the endpoint it was written for |
| `backend/routes/drivers/_deps.py` | Re-exported `location_update_limit` in both halves of the dual-import block | The dual import pattern is mandatory per CLAUDE.md; both halves must stay in sync |
| `backend/utils/rate_limiter.py` | `location_update_limit` now takes `key_func=get_user_or_ip_key`; comment records that it decorated nothing until today | Per-user keying, and a note so the dead-code period is discoverable later |
| `docs/change-log/2026-08-07-location-batch-rate-limit.md` | This log | CLAUDE.md mandate — live driver surface |

## 7. Before / after

```python
# Before — no limiter; limiter object existed but decorated nothing
@router.post("/location-batch")
async def update_location_batch(
    batch: Union[List[dict], dict, LocationBatchRequest],
    current_user: dict = Depends(get_current_user),
    token_session_id: str | None = Depends(get_token_session_id),
):
```

```python
# After — 60/min per driver
@router.post("/location-batch")
@location_update_limit
async def update_location_batch(
    batch: Union[List[dict], dict, LocationBatchRequest],
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    token_session_id: str | None = Depends(get_token_session_id),
):
```

```python
# rate_limiter.py — before
location_update_limit = default_limiter.limit("60/minute")            # unused
# after
location_update_limit = default_limiter.limit("60/minute", key_func=get_user_or_ip_key)
```

## 8. Rollback plan

This is the one change in the branch whose rollback is a **code revert**, and
that is stated rather than dressed up: removing a newly-added decorator has no
config lever, because the limiter did not exist on this route before.

- **Full revert:** `git revert` this commit. Safe and complete — no live data is
  written or migrated by this change, so there is no data-level remediation to
  perform. The endpoint returns to unlimited.
- **Partial mitigation without a revert:** `RATE_LIMIT_USER_KEYING=off` changes
  the *keying* back to IP but leaves the 60/minute limit in force, so it does
  **not** undo this commit. Do not reach for it here.
- If 60/minute proves too tight in production, raising the number is a one-line
  change; the value is deliberately a plain literal in `rate_limiter.py` rather
  than being buried.

Accepted as low-risk-with-redeploy because the endpoint is idempotent-ish
(location batches are retried from the driver app's outbox), the limit sits ~5×
above the client's own cadence, and nothing about the change touches durable
state.

## 9. Verification performed

- [x] **Blast-radius grep performed** — `location_update_limit` across the repo
      returned only its definition before this change (confirming dead code) and
      now returns the definition plus one decoration site and the `_deps`
      re-exports. Verified no other route decorates it.
- [x] **Import-time validation** — `AsyncLimiter` raises `TypeError` at import
      if the decorated function lacks a `request`/`websocket` parameter, so the
      module was imported directly with the test env to prove the signature is
      accepted, and that the wrapper is attached:
      `routes.drivers.location` imported OK, `__wrapped__` present.
- [x] **Automated tests run:**
      - `tests/test_location_batch.py`, `tests/test_location_batch_revoked_session.py`
        — **19 passed**
      - `pytest -k "location or driver_ride_flow or drivers_shared"` —
        **403 passed, 1 skipped, 1 xfailed**
- [x] **Reviewed against CLAUDE.md conventions** — dual import pattern preserved
      in `_deps.py` (both halves edited); driver `is_online`/`is_available`
      invariant untouched; no PII added (the rate-limit key is a user id, and
      no coordinates are logged by this change).
- [x] **Feature-flag consideration** — considered and rejected as
      disproportionate for a single additive decorator on an endpoint whose
      client posts at ~1/5 of the limit. The trade is recorded here instead so
      the decision is auditable rather than implicit.

## What was NOT verified

- **The 4–12 requests/minute flush cadence is from the driver app's design
  (5–15 s outbox interval), not from measured production telemetry.** If any
  driver build in the field flushes faster than every second, it would hit this
  limit. That is the main residual risk in this commit and it was not
  quantified against real traffic.
- **No test asserts the 429 path on this specific route.** Coverage proves the
  route still works normally with the limiter attached and that the limiter is
  wired; the throttling behavior itself is covered generically by the limiter's
  own tests, not end-to-end on `/drivers/location-batch`.
- **Driver-app client behavior on a 429 from this endpoint was not exercised.**
  The outbox is expected to retry, but that path was reasoned about from the
  endpoint's design, not run.
- **Not tested against live Supabase** — `mock_supabase_client` fixtures only.
- **No load test** — `loadtest/locustfile.py` needs a staging target
  (ACTION_ITEMS E1).

## 10. Sign-off

- [x] Rollback plan is concrete — and honestly labelled as a code revert rather
      than claiming a config lever that does not apply
- [x] Blast radius is stated, not assumed (one route; limiter previously unused)
- [x] No silent behavior change — this is the one commit in the branch that can
      newly reject a previously-successful request, and §4/§5 say so explicitly
