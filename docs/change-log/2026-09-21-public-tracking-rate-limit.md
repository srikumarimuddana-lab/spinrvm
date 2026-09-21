# Change Impact & Risk Log — rate-limit the public trip-tracking endpoint

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | mkkreddy52@gmail.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | branch `claude/tracking-availability-8ij1ty` (new PR; follows merged #5658) |
| Related issue or gap ID | `spinr-security-auditor` WARNING on #5658; follows `docs/change-log/2026-09-21-public-trip-tracking-appcheck-exempt.md` |

## 1. Issue / gap identified

`track_shared_ride` (`GET /api/v1/rides/track/{share_token}`) carries **no rate
limit at all**, and no blanket fallback covers it. PR #5658 exempted it from App
Check so the browser tracking page could reach it — which means the endpoint is
now openly pollable by anyone, unauthenticated and unthrottled.

## 2. Root cause

Two things, only one of which is new:

1. **Pre-existing:** the handler has no limiter decorator, unlike every sibling
   in `sharing.py` (`@api_rate_limit`, `@ride_action_limit`). It also had no
   `request` parameter, so a decorator could not have been applied without one —
   `AsyncLimiter.limit()` raises `TypeError` at decoration time without a
   `request`/`websocket` parameter (`utils/async_limiter.py:65-71`).
2. **No default covers it.** `init_middleware` sets `app.state.limiter` but never
   adds `SlowAPIMiddleware`, so `default_limiter`'s `default_limits`
   (`100/minute`, `1000/hour`) are consulted **only** via an explicit `.limit()`
   decorator. An undecorated route is genuinely unlimited.

App Check was previously rejecting 100% of browser traffic here, which
incidentally masked the gap. #5658 removed that masking.

## 3. Fix / remediation

Add `share_track_limit = default_limiter.limit("120/minute")` and apply it, plus
the `request: Request = None` parameter the decorator requires.

120/minute was chosen by the repo owner. The page polls every 5 s (~12 req/min
per viewer), so this is ~10 simultaneous viewers of one link per egress IP.

**Alternative considered:** reuse the existing `ride_read_limit` (already
`120/minute`). Rejected — its `key_func=get_user_or_ip_key` and its comment both
describe *authenticated* ride reads, and reusing it on an endpoint that can never
have a user would make that comment misleading at its new call site. A dedicated
constant with its own reasoning costs one line.

## 4. Risk & impact on existing functionality

**Blast radius: isolated** — one handler, one new constant, plus the dual-import
re-export.

- Blast-radius grep: `share_track_limit` has exactly one call site. Adding it to
  `utils/rate_limiter.py` changes no existing limiter. `_deps.py` re-exports it
  through **both** halves of the dual-import pattern (relative and absolute), per
  `CLAUDE.md`.
- `request: Request = None` is added as a **trailing** parameter, so `share_token`
  stays first and the path parameter still binds. This matches the existing shape
  of `share_trip_with_contact` and `register_live_activity` in the same file.
- **The realistic regression is a false 429, not a bypass.** See §5.

## 5. User-experience effect

- **Rider's shared contacts:** no visible change in normal use. A viewer polls at
  ~12 req/min; the ceiling is ~10 concurrent viewers per egress IP.
- **Accepted trade-off, stated plainly:** `get_user_or_ip_key`'s own docstring
  records that IP keying previously broke authenticated ride reads — a
  carrier-grade NAT puts many subscribers behind one egress IP, and "~6
  simultaneously-polling riders on one carrier exhausted it between them." That
  hazard applies here and **cannot be keyed away**, because the endpoint is
  anonymous by design and has no user identity to key on. Recipients open these
  links from an SMS on mobile data, so simultaneous viewers of *different* links
  on one carrier NAT share this bucket. At current scale this is unlikely to
  bite; it becomes likelier as usage grows. The limit is deliberately set
  generously for that reason — **raise it before narrowing it.**
- A throttled viewer sees the page's existing "Tracking unavailable" error. It is
  a safety surface, so this is the cost being accepted to close a DoS vector.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/rate_limiter.py` | New `share_track_limit` (120/min, IP-keyed) with the NAT trade-off documented | The endpoint had no limit and no default covers it |
| `backend/routes/rides/_deps.py` | Re-export `share_track_limit` in both dual-import halves | Required by the package's import convention |
| `backend/routes/rides/sharing.py` | Decorate `track_shared_ride`; add `request: Request = None` | Apply the limit; the decorator requires the parameter |
| `backend/tests/test_public_tracking_rate_limit.py` | New tests | Pin that the handler stays limited and the limiter stays real |

## 7. Before / after

```python
# Before — unlimited, and no request param for a limiter to key on
@router.get("/track/{share_token}")
async def track_shared_ride(share_token: str):
```

```python
# After
@router.get("/track/{share_token}")
@share_track_limit
async def track_shared_ride(share_token: str, request: Request = None):
```

## 8. Rollback plan

`git revert` plus redeploy. Acceptable as the only path here: the change writes
no data and alters no stored state, so reverting restores the prior behaviour
(unlimited) with no residue.

Faster mitigation without a code deploy, if the limit proves too tight in
production: `RATE_LIMIT_USER_KEYING=off` does **not** help (this endpoint is
already IP-keyed). There is no env knob for this specific ceiling, which is a
deliberate simplicity trade-off — if one turns out to be wanted, add it rather
than loosening the limit blindly.

## 9. Verification performed

- [x] `ruff check` + `ruff format --check` clean on all four files
- [x] Confirmed no blanket limiter exists (`init_middleware` never adds `SlowAPIMiddleware`)
- [x] Confirmed `AsyncLimiter.limit()` uses `@wraps`, so `__wrapped__` is a valid decoration check
- [x] Confirmed `default_limiter._key_func is get_real_client_ip`
- [x] Confirmed decorator order matches the existing pattern in the same file
- [ ] Automated tests run — **NOT RUN LOCALLY**, see below

## 10. What was NOT verified

- **Tests not executed locally.** `pytest` is unavailable in the authoring
  environment and `pip` cannot reach PyPI. CI is the first execution. Note that a
  mistake in the decorator would fail **loudly at import**, not silently:
  `AsyncLimiter.limit()` raises `TypeError` at decoration time without a
  `request` parameter, so the whole module would fail to import.
- **The 120/min ceiling has not been load-tested.** It is reasoned from the
  page's 5 s poll interval, not measured.
- **No production probe** — outbound HTTPS to `api-spinr.spinr.ca` is blocked
  from this environment.

## 11. Adjacent finding NOT fixed here — `rides.shared_trip_token` is unindexed

Confirmed by a full sweep of `backend/migrations/`: **no `CREATE INDEX` on
`rides.shared_trip_token` exists anywhere.** Migration 313 added only the sibling
`shared_trip_token_created_at` column. Every call to this endpoint runs
`get_rows("rides", {"shared_trip_token": ...})`, so each miss is plausibly a
sequential scan of the `rides` table.

Deliberately not fixed in this PR: it needs a migration against a large,
live-tested table, which carries its own append-only/ordering/`CONCURRENTLY`
considerations and deserves its own review rather than riding along with a
rate-limit change. The rate limit bounds the request rate; the index would bound
the cost per request. **Recommend doing it next.**
