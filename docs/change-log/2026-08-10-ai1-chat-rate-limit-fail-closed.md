# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-10 |
| Author | Claude (agent-assisted) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/spinr-ai-guardrail-reviewer-o2vups` |
| Related issue or gap ID | ACTION_ITEMS.md — AI1 |

## 1. Issue / gap identified

`/ai/chat`'s rate limit (`ai_chat_limit`) was keyed only on client IP, so a
single authenticated user spread across many IPs was not bounded by it.
Separately, the per-user daily LLM-cost cap in `orchestrator._over_daily_cap`
failed fully **open** on any Redis error — a transient Redis blip silently
removed the per-user daily cap for every user, leaving only the global kill
switch as a backstop.

## 2. Root cause

The IP key was the only key function ever wired to `ai_chat_limit`; no
user-identity dimension was added when the endpoint moved from anonymous to
authenticated. The daily-cap Redis-error handler used a blanket
`except Exception: return False` with no fallback accounting at all —
written as a "fail open, log loudly" policy mirroring this codebase's
general non-OTP rate-limit convention, but never revisited for a paid-LLM
cost-control path where "open" means "uncapped."

## 3. Fix / remediation

Added a second, independent, user-keyed rate limiter
(`ai_chat_user_limit`, `10/minute`, keyed by `get_authenticated_user_key`)
stacked alongside the existing IP-keyed `ai_chat_limit` on `/ai/chat` —
additive, nothing removed. `get_authenticated_user_key` decodes the bearer
JWT **without** signature verification purely to pick a bucket (mirrors the
existing low-trust pattern in `core/middleware.py`'s `_extract_user_id`);
real auth is still fully enforced downstream by `get_current_user`, which
does verify the signature, so a forged claim can only waste a rate-limit
slot, never bypass auth.

`_over_daily_cap`'s Redis-error path now falls back to a process-local
counter bounded by a fixed `_DEGRADED_CAP_FLOOR = 200`/user/day (not derived
from the admin-configured cap, since `get_app_settings()` could itself be
degraded in a wider outage) instead of returning `False` unconditionally. A
single blip still doesn't hard-block the very next message; a sustained
outage does eventually cap. The error log was upgraded from a plain
`logger.error(..., exc_info=True)` to include the underlying exception
object explicitly, per root CLAUDE.md's "do not silently swallow errors."

## 4. Risk & impact on existing functionality

- **Blast radius, stated explicitly (grep-confirmed):**
  - `ai_chat_limit` / new `ai_chat_user_limit`: only referenced in
    `backend/routes/ai.py`'s `/ai/chat` route. No other caller.
  - `_over_daily_cap`: only called from `orchestrator.run_chat_turn` — the
    one production call site, whose call shape is unchanged (same
    `(user_id, cap)` signature, same return type).
  - `get_authenticated_user_key`: brand new, only used by
    `ai_chat_user_limit`.
- **Known, deliberately out-of-scope sibling gap:** `mcp_server.py` has a
  structurally identical fail-open-on-Redis-error pattern in
  `_over_mcp_daily_cap`, guarding the separate `/mcp` surface. Not touched by
  this change (different route, different file, not part of AI1's stated
  scope) — flagging here so it isn't mistaken for already covered. Worth a
  follow-up ACTION_ITEMS entry.
- No interaction with the ride state machine, wallet/Stripe money paths, or
  RLS. Redis is already documented (`utils/redis_client.py`, root
  CLAUDE.md "Redis transparency") as falling back to an in-process dict when
  `REDIS_URL` is unset entirely — this change only affects the separate case
  where Redis **is** configured but errors transiently.

## 5. User-experience effect

Rider/driver-facing (both apps use the shared AI assistant surface). Under
normal operation, no visible change — the new user-keyed limiter uses the
same `10/minute` threshold as the existing IP-keyed one, so a normal single-
device user sees no new friction. The only user-visible change is in a
degraded-Redis scenario: previously a Redis outage silently removed the
daily cap (no visible effect, but a growing cost/abuse exposure); now, a
**sustained** Redis outage (200+ messages/user/day) begins rejecting further
messages for that user with the same cap-exceeded response the normal-path
cap already produces today — this is new behavior only in that narrow,
previously-uncapped failure window, not a change to the common case. Not
mid-session-disruptive for any normal user (200/day is far above real usage).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/rate_limiter.py` | Added `get_authenticated_user_key`, `ai_chat_user_limit` | Close the IP-only gap (AI1 part 1) |
| `backend/routes/ai.py` | Stacked `@ai_chat_user_limit` alongside existing `@ai_chat_limit` on `/chat` | Wire the new limiter in, additively |
| `backend/ai/orchestrator.py` | `_over_daily_cap` Redis-error path: fail-open → fail-closed-with-floor (`_degraded_daily_counts`, `_DEGRADED_CAP_FLOOR = 200`, `_prune_degraded_counts`) | Close the fail-open gap (AI1 part 2) |
| `backend/tests/test_ai_rate_limiting.py` | New, 16 tests | Regression coverage for both fixes |

## 7. Before / after

```python
# Before — backend/ai/orchestrator.py
async def _over_daily_cap(user_id: str, cap: int) -> bool:
    try:
        count = await redis_incr(key)
        ...
        return count > cap
    except Exception:
        logger.error("ai daily-cap check failed — failing open", exc_info=True, ...)
        return False  # <-- cap silently removed on any Redis error

# After
except Exception as exc:
    logger.error("ai daily-cap check failed — Redis unavailable; failing CLOSED "
                  "with a %s/day process-local floor ...", _DEGRADED_CAP_FLOOR, ...)
    count = _degraded_daily_counts.get(degraded_key, 0) + 1
    _degraded_daily_counts[degraded_key] = count
    return count > _DEGRADED_CAP_FLOOR
```

```python
# Before — backend/routes/ai.py
@ai_chat_limit
async def ai_chat(...):

# After
@ai_chat_limit        # IP-keyed (unchanged)
@ai_chat_user_limit   # user-keyed (new)
async def ai_chat(...):
```

## 8. Rollback plan

No feature flag was added (the change is a tightening of an existing control,
not new user-facing behavior in the common case). Rollback is a plain
`git revert` — this touches no persisted data (the degraded-mode counter is
process-local and non-persistent by design) and no Stripe/wallet state.
Reverting restores the prior fail-open behavior and IP-only keying with no
data cleanup required.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_ai_rate_limiting.py backend/tests/test_ai_chat_route.py backend/tests/test_ai_orchestrator.py backend/tests/test_ai_mcp_coverage.py backend/tests/test_ai_admin_console.py backend/tests/test_rate_limit_response_shape.py` — 100 passed. Combined with the B0 test files in the same session: 421 passed total, 0 failures.
- [ ] Manual repro steps followed in staging — **not performed**, no staging access in this environment.
- [x] Blast-radius grep performed: `ai_chat_limit`/`ai_chat_user_limit` (routes/ai.py only), `_over_daily_cap` (orchestrator.run_chat_turn only) — see section 4.
- [x] Reviewed against relevant CLAUDE.md conventions: "Do not silently swallow errors" (logger.error with exception, not warning), "Redis transparency" (fallback pattern mirrors the documented in-process-dict approach).
- [ ] Feature-flagged — not applicable/not done; justified above (tightening of an existing control, degraded-mode floor only bites in an already-abnormal Redis-outage scenario, not the common path).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no persisted state to unwind)
- [x] Blast radius is stated, not assumed (both changed functions have exactly one production caller, grep-confirmed)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — see section 5

## What was NOT verified

Not tested against a real Redis instance — all Redis interaction is mocked
per this repo's existing test conventions. Real Redis timeout/
connection-refused behavior is assumed to raise (matching
`utils/redis_client.py`'s documented contract) but not independently
verified end-to-end against a live Redis. The `_DEGRADED_CAP_FLOOR = 200`
value is a judgment call ("generous floor" per the task framing), not
validated against real LLM cost-per-request numbers — a finance/business
sanity check on that specific number is recommended before relying on it
through a real sustained outage. No frontend/admin-dashboard build was run
(this is a backend-only Python change, `npm run build` not applicable).
