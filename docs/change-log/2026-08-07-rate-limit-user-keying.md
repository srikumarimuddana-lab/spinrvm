# Change Impact & Risk Log — per-user rate-limit keying (read/estimate routes)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-07 |
| Author | Claude Code (session: postgres-scaling-supabase) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/postgres-scaling-supabase-ypnwiy` |
| Related issue or gap ID | Burst-tolerance request. Same class as gap #41 (company_booking_limit IP keying) and ACTION_ITEMS AI1 (AI chat IP keying) — both already fixed the same way |

## 1. Issue / gap identified

`api_rate_limit` (30/min) and `ride_read_limit` (120/min) were keyed on client
IP. Mobile carriers place hundreds of subscribers behind a single carrier-grade
NAT egress IP, so **every rider on a given carrier shared one bucket**. Roughly
6 riders polling ride status simultaneously on one carrier exhausted
`ride_read_limit` between them; the rest got 429s while doing nothing wrong.

This is the first rejection real users hit during a burst — it bites well before
the Fly connection limits raised in the previous commit.

## 2. Root cause

`default_limiter`'s `key_func` is `get_real_client_ip`, and neither limiter
overrode it. IP keying is the correct default for *unauthenticated* surfaces
(OTP, login) where no trustworthy identity exists yet, but these are
authenticated routes where a user id is available and is the meaningful unit.

The repo had already fixed this exact bug twice on other surfaces — gap #41 for
corporate guest bookings and AI1 for AI chat — but the fix was never generalized
to the ride routes.

## 3. Fix / remediation

New `get_user_or_ip_key` in `backend/utils/rate_limiter.py`: key on the
authenticated user id when a bearer token is present, fall back to client IP for
anonymous traffic. Applied to `api_rate_limit` and `ride_read_limit`.

**Numeric limits are unchanged.** 30/min and 120/min are generous per user, and
the limiter scope already includes the URL path, so `/active`, `/history`,
`/scheduled`, and `/{ride_id}` each get their own bucket. Changing both the key
and the number would make a regression impossible to attribute.

The key function reuses the existing `_extract_unverified_user_id` helper — the
same mechanism `get_ai_chat_key` has used in production since AI1.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend ride routes), 7 decorated endpoints.**
Enumerated by grep, not assumed:

| Limiter | Route sites |
|---|---|
| `api_rate_limit` (30/min) | `routes/rides/estimates.py:656`, `routes/rides/lifecycle.py:35`, `routes/rides/sharing.py:77` |
| `ride_read_limit` (120/min) | `routes/rides/queries.py:41`, `:149`, `:274`, `:292` |

`routes/rides/_deps.py:65,71,111,117` and `routes/rides/__init__.py:63,111,356,429`
also reference these names, but they are **imports and `__all__` re-exports of
the same objects**, not additional decoration sites — no separate behavior.

Not touched: `otp_rate_limit`, `login_rate_limit`, `admin_rate_limit`,
`promo_*`, `company_booking_limit`, `ai_chat` limiters. OTP and login stay
IP-keyed deliberately — they guard unauthenticated surfaces where accepting a
client-supplied identity as the bucket key would let an attacker mint unlimited
buckets.

**Security direction of travel: this tightens, not loosens.** IP keying was
evadable for free by rotating through a proxy pool (the documented reasoning
behind gap #41 and AI1). User keying binds to an identity the caller cannot
change without authenticating as someone else. A forged token cannot borrow
another user's bucket in any meaningful way either: `Depends(get_current_user)`
resolves before the handler body, so such a request 401s regardless — the worst
case is a throwaway bucket for a request that fails auth. This is the same trust
argument already accepted for `get_ai_chat_key`.

**Interaction with shared Redis:** rate-limit counters live in Redis
(`RATE_LIMIT_REDIS_URL`, required in production per `core/middleware.py:633-648`).
Key cardinality shifts from "number of active IPs" to "number of active users" —
higher cardinality, but each key is a small counter with the limiter's own
expiry, and Redis is already sized for OTP lockouts, presence, and WS pub/sub.
No change to eviction or memory policy is required at Saskatchewan scale.
`spinr_redis_used_memory_bytes` is already exposed on `/metrics` if this needs
watching.

**Background loops, ride state machine, money paths:** untouched. This commit
changes only which string is used as a rate-limit bucket key.

**Could this regress a working flow?** The realistic failure mode is a route
where the caller is authenticated but the limit was implicitly relied on as a
*global* throttle rather than a per-caller one. None of these 7 routes is such a
case — all are per-rider reads and estimates. A genuine misbehaviour of the key
function itself (e.g. every request collapsing into one bucket) is covered by
the kill switch in §8.

## 5. User-experience effect

- **Riders:** strictly fewer spurious 429s. A rider previously refused because
  strangers on the same carrier IP were polling now gets served. No copy change,
  no new error state, no new screen.
- **Drivers:** unaffected by this commit (driver location batching is handled
  separately).
- **Visible mid-session?** Yes, in the positive direction only: a rider who was
  being throttled mid-session stops being throttled. Nothing a user has to
  learn, acknowledge, or do differently.
- **No notification or copy changes.**

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/rate_limiter.py` | Added `get_user_or_ip_key` with `RATE_LIMIT_USER_KEYING` kill switch; passed it as `key_func` to `api_rate_limit` and `ride_read_limit`; comments record why the numeric limits stayed put | Makes the limit mean "per user" instead of "per carrier NAT" |
| `backend/tests/test_rate_limit_user_keying.py` | New: 15 tests — key-function behavior, malformed-token fallbacks, kill switch, and two end-to-end tests through the real `AsyncLimiter`/storage | Every auth-adjacent change needs allowed and denied paths covered |
| `docs/change-log/2026-08-07-rate-limit-user-keying.md` | This log | CLAUDE.md mandate for live-tested surfaces |

## 7. Before / after

```python
# Before — one bucket per IP; a whole carrier's riders shared it
api_rate_limit = default_limiter.limit("30/minute")
ride_read_limit = default_limiter.limit("120/minute")
```

```python
# After — one bucket per authenticated user; IP only for anonymous callers
api_rate_limit = default_limiter.limit("30/minute", key_func=get_user_or_ip_key)
ride_read_limit = default_limiter.limit("120/minute", key_func=get_user_or_ip_key)
```

Effective behavior, 6 riders on one carrier NAT each polling `/rides/active`
every 3 s (20 requests/min each, 120/min combined):

```
Before:  bucket "ip:203.0.113.7"  -> 120/min limit hit -> riders start 429ing
After:   buckets "user:a" ... "user:f" -> 20/min each against a 120/min limit -> all served
```

## 8. Rollback plan

**No redeploy required:**

```bash
fly secrets set RATE_LIMIT_USER_KEYING=off -a spinr-backend-yyz
```

This rolls machines with the new env value and reverts both limiters to pure IP
keying — the exact pre-change behavior, including its CGNAT flaw. Covered by
tests (`test_kill_switch_reverts_to_ip_keying`, parametrized over
`off/OFF/0/false/False/no`).

The `app_settings`-in-DB flag pattern is **not** usable here: limiter key
functions are invoked synchronously by `AsyncLimiter` (`utils/async_limiter.py:112`)
while `get_app_settings()` is async. An env var is the mechanism that fits the
call site, and it satisfies the "config revert, not a second deploy" requirement.

No live data is touched — no ride state, wallet delta, Stripe charge, or
insurance-period row — so the config revert is a complete rollback.

## 9. Verification performed

- [x] **Blast-radius grep performed.** Searched `api_rate_limit` and
      `ride_read_limit` across `backend/routes/`: 7 decoration sites (listed in
      §4), plus re-export-only references in `rides/_deps.py` and
      `rides/__init__.py`. Confirmed no other limiter shares the changed objects.
- [x] **Automated tests written** — `backend/tests/test_rate_limit_user_keying.py`,
      15 tests including two end-to-end cases through the real `AsyncLimiter` +
      `MemoryStorage` (mirroring the pattern proven in
      `test_corporate_booking_rate_limit_key.py`): one asserting rider A
      exhausting the budget does not block rider B on the same carrier IP, one
      asserting rider A still cannot evade the limit by changing IP.
- [x] **Reviewed against CLAUDE.md conventions** — auth/RLS ("every auth policy
      needs allowed and denied paths"), the JWT trust model (this never grants
      authorization; role is still re-read from `users` downstream), and the
      observability rules (no PII added to logs — the key is a user id, which is
      the identifier CLAUDE.md explicitly prescribes *instead of* names, phones,
      or emails).
- [x] **Feature-flag consideration** — user-visible but strictly permissive, and
      gated by an env kill switch rather than a rollout flag. A staged rollout
      would mean deliberately leaving some users 429ing on a bug we can fix
      atomically.
- [ ] **Manual repro in staging** — not possible, no staging environment exists
      (ACTION_ITEMS E1).

### Test execution status

Backend deps were absent from this session's container (the SessionStart hook
reported "backend pip install skipped"), so a `backend/.venv` was created from
`requirements.txt` before running anything. Results:

```
tests/test_rate_limit_user_keying.py .......................   23 passed

Regression check on the existing limiter suites:
tests/test_rate_limit_response_shape.py .....
tests/test_async_limiter.py ........
tests/test_corporate_booking_rate_limit_key.py .....
tests/test_promo_rate_limit.py ........                        26 passed
```

(23 rather than 15 because the malformed-token and kill-switch cases are
parametrized.)

## What was NOT verified

- **No production build was run** for any frontend surface — none is touched.
- **Not tested against live Supabase or a real carrier NAT.** The CGNAT scenario
  is reproduced with synthetic `cf-connecting-ip` headers, which is what the key
  function actually reads, but no traffic from a real carrier was observed.
- **Redis key-cardinality impact is reasoned, not measured.** Moving from
  IP-keyed to user-keyed buckets raises distinct-key count; no load test
  confirms the effect on Redis memory at burst scale.
- **No load test was run** — `loadtest/locustfile.py` needs a staging target
  (ACTION_ITEMS E1) and refuses production.
- **The 6-riders-per-carrier figure in §7 is arithmetic**, derived from the
  polling interval and the limit, not sampled from production traffic.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`RATE_LIMIT_USER_KEYING=off`, no deploy)
- [x] Blast radius is stated, not assumed (7 sites enumerated by grep)
- [x] No silent behavior change to an already-shipped flow — UX field filled in;
      the only user-facing delta is fewer spurious 429s
