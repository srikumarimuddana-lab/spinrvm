# Change Impact & Risk Log — ride-read rate limiter was never registered

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (session: postgres-scaling-supabase) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | PR #3465, branch `claude/postgres-scaling-supabase-ypnwiy` |
| Related issue or gap ID | Code-review finding #1 on PR #3465 |

## 1. Issue / gap identified

All four ride-read endpoints had `@ride_read_limit` applied **above**
`@router.get`, which registers the **unwrapped** function. The limiter object
was constructed, looked correct to a grep, and never ran.

`/rides/active`, `/rides/history`, `/rides/scheduled`, and `/rides/{ride_id}`
have therefore been **completely unrated in production**, not merely
mis-keyed — no 120/minute ceiling, no throttle of any kind.

This also made the per-user keying shipped earlier in this branch a **no-op on
these four routes**: the earlier change log claimed a CGNAT fix for
`ride_read_limit` that could not have taken effect.

## 2. Root cause

Decorators apply bottom-up, and `APIRouter.get()` registers the function it
receives and then returns it unchanged. So with the limiter listed first:

1. `@router.get("/active")` runs first → registers the **bare** function
2. `@ride_read_limit` runs second → wraps a function the router never sees

Every other route file in the repo has the opposite (correct) order, so this was
a local inversion in `queries.py` rather than a misunderstood convention.

**Why it survived:** the earlier blast-radius check for this branch grepped for
`@ride_read_limit` and found four decoration sites — which is true, and useless.
A grep proves a decorator is *present*, not that it is *effective*. Nothing in
the suite asserted the limiter was actually attached to the registered endpoint.

## 3. Fix / remediation

Swap the decorator order on all four endpoints so `@router.get` is outermost.

Verified by inspecting the registered endpoints rather than re-reading the
source — before: `get_active_ride` with no `__wrapped__`; after: `__wrapped__`
present on all four, matching correctly-ordered routes like `safety.py:38`.

Added `backend/tests/test_rate_limit_decorator_order.py`: an AST walk over every
file in `backend/routes/` that fails on any endpoint whose limiter is listed
above its router decorator. This catches the whole class of bug, not just the
four instances fixed here.

## 4. Risk & impact on existing functionality

**Blast radius: 4 endpoints, and this is a genuine behavior change** — they go
from unlimited to 120 requests/minute per user.

| Endpoint | File | Was | Now |
|---|---|---|---|
| `GET /rides/active` | `queries.py:41` | unlimited | 120/min per user |
| `GET /rides/history` | `queries.py:149` | unlimited | 120/min per user |
| `GET /rides/scheduled` | `queries.py:274` | unlimited | 120/min per user |
| `GET /rides/{ride_id}` | `queries.py:292` | unlimited | 120/min per user |

**Headroom analysis.** The rider app polls active-ride state on roughly a 3 s
interval ⇒ ~20 requests/minute against a 120/minute ceiling, so ~6× headroom
per user. Because the limiter scope includes the URL path, each of these four
routes carries its own independent bucket rather than sharing one — a rider
polling `/active` cannot exhaust `/history`.

**Who could newly be throttled:** a client polling faster than every 500 ms on a
single route, or a retry storm. Normal app usage is well clear.

**Interaction with the per-user keying in this branch:** the two changes
compose — with the limiter now actually registered, the CGNAT fix finally
applies here, so the ceiling is per rider rather than per carrier NAT. Had this
been fixed *without* the keying change, restoring the 120/min limit would have
applied it per carrier-NAT IP and could have throttled riders in groups. Order
matters, and it is correct in this branch.

**Not affected:** every other limited route already had the correct order —
verified by the new AST test across all of `backend/routes/`, which reports 25+
correctly-ordered rate-limited endpoints and zero remaining violations.

**Ride state machine, money paths, dispatch:** untouched. This changes only
whether a limiter wrapper is invoked.

## 5. User-experience effect

- **Riders:** none in normal use — ~6× headroom over the app's polling cadence.
  A rider whose client misbehaves now receives 429s where it previously got
  unlimited service; the app already handles the standard 429 shape.
- **Visible mid-session?** Only for a client exceeding 2 requests/second on one
  of these routes, which the shipped app does not do.
- **Drivers / corporate / admin:** unaffected; these are rider ride-read routes.
- **No copy, notification, or UI changes.**

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/queries.py` | Swapped decorator order on 4 endpoints so `@router.get` is outermost | The limiter was registering an unwrapped function and never running |
| `backend/tests/test_rate_limit_decorator_order.py` | New: AST walk over `backend/routes/` failing on any limiter above its router decorator, plus self-tests proving the detector catches the real shape and does not flag the correct one | A grep cannot detect this; only ordering can |
| `docs/change-log/2026-08-08-ride-read-limit-never-registered.md` | This log | CLAUDE.md mandate — live-tested surface, real behavior change |

## 7. Before / after

```python
# Before — router registers the BARE function; limiter wraps something unused
@ride_read_limit
@router.get("/active")
async def get_active_ride(...):
```

```python
# After — limiter wraps the endpoint, router registers the wrapper
@router.get("/active")
@ride_read_limit
async def get_active_ride(...):
```

Observed on the registered endpoint object:

```
Before:  /active -> get_active_ride    __wrapped__: False   (limiter absent)
After:   /active -> get_active_ride    __wrapped__: True    (limiter attached)
```

## 8. Rollback plan

**Config lever (no redeploy)** — if 120/min proves too tight now that it is
actually enforced, the limit is a plain literal in `utils/rate_limiter.py` and
the keying kill switch remains:

```bash
fly secrets set RATE_LIMIT_USER_KEYING=off -a spinr-backend-yyz   # keying only
```

Note that the kill switch changes *keying*, not the limit — it does **not**
restore the previous unlimited behavior. To do that, `git revert` this commit,
which returns the endpoints to unrated. That is an honest description: there is
no config flag that un-registers a decorator.

No durable state is touched — no ride rows, wallet deltas, or Stripe calls — so
a code revert is a complete rollback.

## 9. Verification performed

- [x] **Empirically verified the bug and the fix** by inspecting FastAPI's
      registered endpoint objects, not by re-reading source. Before: no
      `__wrapped__` on any of the four; after: present on all four, matching a
      known-correct route (`safety.py:38`).
- [x] **Regression test proven to catch it** — reverted the fix, confirmed the
      new AST test fails and names all four endpoints with file:line, then
      restored and confirmed it passes. A guard that has never seen the bug fail
      is not a guard.
- [x] **Repo-wide check** — the AST walk covers every file in `backend/routes/`
      and reports zero remaining violations across 25+ rate-limited endpoints,
      so this was the only instance.
- [x] **Automated tests run:** `test_rate_limit_decorator_order.py` — 4 passed;
      `pytest -k "queries or ride_history or ride_read"` — 39 passed, 1 skipped.
- [x] **Reviewed against CLAUDE.md conventions** — no state-machine, money, or
      RLS surface touched; the change is decorator ordering only.

## What was NOT verified

- **No live traffic was observed against these endpoints**, so the "~20
  requests/minute" polling figure is from the client's documented interval, not
  measured production telemetry. If any shipped rider build polls faster, the
  real headroom is smaller than stated.
- **No test asserts the 429 path on these four routes specifically.** Coverage
  proves the limiter is attached and the routes still serve normally; the
  throttling behaviour itself is covered by the limiter's own tests.
- **Not tested against live Supabase** — `mock_supabase_client` fixtures only.
- **The four endpoints have never been rate-limited in production**, so there is
  no historical data on how often real users approach 120/min. This is a
  first-time enforcement, and that uncertainty cannot be resolved before deploy.

## 10. Sign-off

- [x] Rollback plan is concrete, and honestly states that the kill switch does
      not undo this change
- [x] Blast radius is stated, not assumed — 4 endpoints named, repo-wide scan
      confirms no others
- [x] No silent behavior change — this is a real change from unlimited to
      limited, and §4/§5 say so plainly rather than framing it as a pure bugfix
