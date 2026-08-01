# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code (background job) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai, admin |
| PR / commit link | branch `claude/ai12-admin-console-rate-limit` (PR to be opened) |
| Related issue or gap ID | ACTION_ITEMS.md — AI12 (AI assistant / MCP guardrail backlog) |

## 1. Issue / gap identified

`backend/routes/admin/ai_console.py`'s `POST /admin/ai/chat` (the super-admin
AI console, used to test the assistant as a specific rider/driver) had no
rate limiter, and its module docstring falsely claimed admin-console turns
"count against their daily cap" — the orchestrator actually exempts them.

## 2. Root cause

Two related but separate gaps:

- **Stale docstring**: the module docstring was written before (or was never
  updated after) `backend/ai/orchestrator.py`'s `run_chat_turn` added the
  `admin_actor_id is None` guard around `_over_daily_cap` — a deliberate
  exemption (per that code's own comment: "heavy console testing was
  429ing and silently draining the target rider's quota") so admin testing
  doesn't consume the impersonated user's quota. The docstring was never
  updated to reflect that exemption and kept asserting the opposite.
- **No rate limiter**: the rider-facing `POST /ai/chat`
  (`backend/routes/ai.py`) is decorated with `@ai_chat_limit`
  (`default_limiter.limit("10/minute")`, per-IP). The admin console endpoint
  was never given an equivalent decorator. Combined with the daily-cap
  exemption above, this meant the admin-console path had **no** per-request
  ceiling at all on LLM spend — only the audit log and the super-admin role
  check bounded it, and neither of those limits *request volume*.

## 3. Fix / remediation

- Rewrote the module docstring in `backend/routes/admin/ai_console.py` to
  state the real behavior: admin-console turns are exempt from the daily cap
  (with a pointer to the exact guard in `orchestrator.py`), and that this
  endpoint now carries its own rate limit as the resulting defensive
  ceiling.
- Added `admin_ai_console_limit = default_limiter.limit("20/minute")` in
  `backend/utils/rate_limiter.py` and applied it via `@admin_ai_console_limit`
  above `@router.post("/ai/chat")` on `admin_ai_chat`, adding the required
  `request: Request` parameter the decorator needs (matches the existing
  convention on `routes/admin/support_tickets.py`'s `@admin_ai_suggest_limit`
  usage).
- **Chosen value: 20/minute, per-IP** (same key function as the rest of
  `default_limiter` — `get_real_client_ip`, i.e. Cloudflare's
  `CF-Connecting-IP`). Reasoning:
  - This is squarely the same *class* of endpoint as
    `admin_ai_suggest_limit` (`backend/routes/admin/support_tickets.py`'s
    `/tickets/{ticket_id}/ai-suggest-reply`) — admin-only, hits a paid LLM,
    audited/reviewed by a human before anything reaches an end user — and
    that endpoint already uses `20/minute`. Reusing the same value keeps the
    convention consistent instead of inventing a new number.
  - The item's own text says this is lower risk than the rider-facing path
    (super-admin-only + audited already), so it should not be *tighter* than
    `ai_chat_limit`'s `10/minute` — it is deliberately looser (2x), matching
    the item's framing of "a reasonable limiter... doesn't need to be as
    tight as the rider-facing cap."
  - It is still a real ceiling, not a token gesture: 20/minute bounds a
    compromised/malicious super-admin session or a runaway automation
    script driving this endpoint to a small, known multiple of the
    rider-facing per-user rate, rather than leaving it fully unbounded (as
    it is today).
  - Other admin-only rate limits in this codebase range from very tight
    (`admin_mass_notify_limit` 3/minute, `admin_staff_delete_limit`
    5/minute — destructive/high-blast-radius actions) to generous
    (`admin_rate_limit` 100/minute — general admin API traffic,
    `data_transfer_jobs_limit`/`data_transfer_search_limit` 60/minute —
    read-only polling). This endpoint sits in the middle: it is a write
    (persists a real conversation + calls a paid LLM) but is already gated
    by super-admin role + audit logging, so `20/minute` (matching the one
    other admin+paid-LLM precedent) was chosen over inventing a new number.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to `POST /admin/ai/chat` in
`backend/routes/admin/ai_console.py`, plus one new shared constant in
`backend/utils/rate_limiter.py`.**

Grepped for every other caller/consumer:

- **Backend**: `admin_ai_chat` is registered once, as
  `router.post("/ai/chat")` in `backend/routes/admin/ai_console.py`, mounted
  once via `admin_router.include_router(ai_console_router)`
  (`backend/routes/admin/__init__.py`). No other Python module calls this
  function directly or imports it — it is reached only through the mounted
  route. The two other routes in the same file
  (`GET /ai/users/{user_id}/conversations`,
  `GET /ai/users/{user_id}/conversations/{id}/messages`,
  `GET /ai/security-events`) were **not** touched or rate-limited — out of
  scope for AI12, which named only the `/ai/chat` POST endpoint.
- **New shared constant** (`admin_ai_console_limit` in
  `backend/utils/rate_limiter.py`): grepped the whole `backend/` tree for
  the name — it is defined once and imported/used exactly once (in
  `ai_console.py`). It does not alias or share a bucket with any existing
  limiter (`ai_chat_limit`, `admin_ai_suggest_limit`, etc. are separate
  `default_limiter.limit(...)` instances, each keyed independently by
  `(limit_key, scope)` where `scope` is `request.url.path` — different
  paths never collide in the same bucket). Adding it does not change the
  behavior of any other pre-configured limiter in that file.
- **Frontend**: exactly one caller — `adminAiChat()` in
  `admin-dashboard/src/lib/api/settings-ai.ts` (`POST /api/admin/ai/chat`),
  invoked from `admin-dashboard/src/app/dashboard/ai-console/page.tsx` (the
  AI console page's chat panel). No other frontend surface (rider-app,
  driver-app) calls this endpoint — it is admin-only by design and by
  route mount (`/admin/...` prefix, `get_admin_user` dependency). Normal
  interactive use by a human operator (a handful of test turns while
  debugging a rider issue) sits far below 20/minute; this only engages
  under sustained, rapid-fire calling.
- **Tests**: `backend/tests/test_ai_admin_console.py` already existed
  (role gating, impersonation, audit-log assertions) and continues to pass
  unmodified; added a new `TestRateLimit` class in the same file (see §6/§9).
- **Could this regress a flow that currently works?** Only if an existing
  legitimate workflow issues more than 20 admin-console chat turns in a
  single minute from one IP. No such automated workflow exists today (the
  console is a human-operated debugging tool); a support/ops agent manually
  testing turns one at a time will never approach this rate. If a future
  legitimate bulk-testing use case needs a higher rate, the fix is to raise
  the constant (or add a `key_func` override), not to remove the limiter.
- **No interaction** with the 16 background loops, the ride state machine,
  or money/wallet deltas — this endpoint only proxies a chat turn through
  the existing AI orchestrator and writes an `audit_logs` row, both
  unchanged by this diff.

## 5. User-experience effect

- **Internal-admin-facing only** (super-admin AI console operators). No
  rider, driver, or corporate-admin-facing surface is touched — the
  orchestrator's daily-cap exemption behavior (what actually determines
  whether admin turns count against a *target user's* quota) is unchanged;
  only the docstring describing it was corrected, and only a new ceiling on
  the *admin's own* request rate was added.
  - **Note**: while gathering the blast radius above we confirmed the
    exemption is real and already live on `main` (merged prior to this
    branch) — this PR does not change what riders/drivers experience in any
    way, it only documents the existing exemption accurately and closes the
    "no ceiling at all" gap on the admin side.
- Not visible mid-session to a rider or driver already using the app — this
  endpoint is never reached by rider/driver clients.
- For the super-admin operator: no behavior change under normal use. Only
  under sustained rapid-fire calling (>20 requests/minute from one IP) does
  a new 429 response appear, with the standard
  `{"error": "rate_limit_exceeded", "retry_after": ..., "limit": 20, ...}`
  body and `Retry-After`/`RateLimit-*` headers already used everywhere else
  in this codebase (`rate_limit_exceeded_handler`) — no new response shape
  introduced.
- No new user-facing copy or notification was added.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/ai_console.py` | Rewrote the module docstring to state the daily-cap exemption accurately (was: "counts against their daily cap"; now: explicitly exempt, with a pointer to `orchestrator.py`'s guard). Added `request: Request` parameter and `@admin_ai_console_limit` decorator to `admin_ai_chat`; added a short rate-limit note to the function's own docstring. | AI12 — fix the stale docstring claim and close the no-rate-limiter gap on `POST /admin/ai/chat`. |
| `backend/utils/rate_limiter.py` | Added `admin_ai_console_limit = default_limiter.limit("20/minute")` with a comment explaining the daily-cap exemption context and the choice to match `admin_ai_suggest_limit`'s precedent. | New rate limiter for the admin console endpoint, following this file's existing per-endpoint-constant convention. |
| `backend/tests/test_ai_admin_console.py` | Added `TestRateLimit` class (2 tests): 20 requests within the limit all succeed; the 21st returns 429 with the standard `rate_limit_exceeded` body. Locally re-enables `default_limiter` (globally disabled by the autouse `reset_rate_limiters` conftest fixture) and resets its storage, mirroring `test_data_transfer_rate_limit.py`'s established pattern. | Proves the new limiter is wired up and behaves per convention. |
| `ACTION_ITEMS.md` | Marked the AI12 bullet `[x]` with a short "done" note and a pointer to this change-log entry. | Close out the backlog item per repo convention (minimal, localized edit — did not touch the neighboring AI9/AI2 bullets other agents are working on in parallel). |

## 7. Before / after

```python
# Before (backend/routes/admin/ai_console.py)
"""Super-admin AI console — test the assistant as a specific rider/driver.
...
The conversation is REAL: it persists under the target user's account (the
rider sees it in their app), runs the same orchestrator/tool path with the
target user's data scoping, and counts against their daily cap — a true
end-to-end test. Strictly super_admin ...
"""

@router.post("/ai/chat")
async def admin_ai_chat(body: AdminAiChatRequest, admin: dict = Depends(get_admin_user)):
    """Run one assistant turn as the target user (non-streaming)."""
    _require_super_admin(admin)
    ...
```

```python
# After
"""Super-admin AI console — test the assistant as a specific rider/driver.
...
The conversation is REAL: it persists under the target user's account (the
rider sees it in their app) and runs the same orchestrator/tool path with
the target user's data scoping — a true end-to-end test. Unlike the
rider-facing /ai/chat path, admin-console turns are deliberately EXEMPT from
the target user's daily message cap (backend/ai/orchestrator.py
`_over_daily_cap`, gated on `admin_actor_id is None`) so heavy console
testing doesn't silently drain the impersonated rider/driver's quota — turns
run here do NOT count against it. That exemption removes the ceiling that
would otherwise bound LLM spend on this path, so /ai/chat below carries its
own `admin_ai_console_limit` (utils/rate_limiter.py) as a defensive ceiling
against a compromised/malicious admin session or a runaway automation
script (ACTION_ITEMS.md AI12). Strictly super_admin ...
"""

@router.post("/ai/chat")
@admin_ai_console_limit
async def admin_ai_chat(request: Request, body: AdminAiChatRequest, admin: dict = Depends(get_admin_user)):
    """Run one assistant turn as the target user (non-streaming).

    Rate-limited (admin_ai_console_limit, 20/minute — see
    utils/rate_limiter.py) as a defensive ceiling: admin-console turns are
    exempt from the impersonated user's daily message cap (see module
    docstring), so this is the only per-request bound on LLM spend here.
    """
    _require_super_admin(admin)
    ...
```

## 8. Rollback plan

This is a pure code change: no migration, no data write changes, no new
`app_settings` flag. Nothing in this diff touches live data (Stripe charges,
wallet deltas, ride state, insurance-period rows) — the changed endpoint
only proxies a chat turn to the existing (unchanged) orchestrator and writes
an `audit_logs` row exactly as before.

- **Rollback**: `git revert` the commit, or simply don't merge the PR. The
  worst case of reverting is a return to today's live state — no rate
  limiter and the stale docstring — which is a known, already-shipped
  condition, not a new risk introduced by rolling back.
- If the *value* (20/minute) turns out to be wrong in practice (too tight
  for a legitimate bulk-testing workflow, discovered post-merge), the fix is
  a one-line change to the `default_limiter.limit(...)` string in
  `backend/utils/rate_limiter.py` — no redeploy-blocking migration, no data
  remediation. No feature flag was introduced because there is no existing
  `app_settings` mechanism for per-endpoint rate-limit values in this
  codebase (rate limits are code constants, consistent with every other
  entry in `utils/rate_limiter.py`), and the change is low-risk/isolated
  enough (per §4) that a flagged rollout was judged unnecessary.

## 9. Verification performed

- [x] Automated tests run:
  - `pytest backend/tests/test_ai_admin_console.py -v` — **24 passed** (22
    pre-existing + 2 new `TestRateLimit` tests), using a pre-existing venv
    (`/tmp/spinr-venv`, already had `backend/requirements.txt` installed).
  - `pytest backend/tests/test_ai_chat_route.py backend/tests/test_async_limiter.py
    backend/tests/test_data_transfer_rate_limit.py backend/tests/test_promo_rate_limit.py
    backend/tests/test_compliance_rate_limit.py backend/tests/test_rate_limit_response_shape.py -v`
    — **52 passed**, confirming the shared `default_limiter`/`rate_limiter.py`
    change did not regress any other pre-configured limiter or the rider-facing
    `/ai/chat` route.
  - `ruff check backend/routes/admin/ai_console.py backend/utils/rate_limiter.py
    backend/tests/test_ai_admin_console.py` — clean.
- [ ] Manual repro steps followed in staging — **not done**, see below.
- [x] Blast-radius grep performed: every backend caller of `admin_ai_chat`
  (route mount only, no other Python callers), every reference to the new
  `admin_ai_console_limit` name, and every frontend caller of
  `POST /api/admin/ai/chat` (`admin-dashboard/src/lib/api/settings-ai.ts` →
  `admin-dashboard/src/app/dashboard/ai-console/page.tsx`, the only one).
- [x] Reviewed against relevant `CLAUDE.md` conventions: matched the
  existing `@<name>_limit` decorator-above-route-decorator pattern and
  `request: Request`-parameter requirement already used by
  `admin_ai_suggest_limit` in `routes/admin/support_tickets.py`; did not
  touch the dual-import pattern's shape (both `try`/`except` branches
  updated); did not touch the orchestrator's daily-cap exemption logic
  itself (out of scope — AI12 is about the docstring and the missing
  limiter, not about changing the exemption).
- [ ] Feature-flagged: **not flagged** — justified above (§8) as an
  additive rate-limit ceiling with no legitimate prior traffic pattern
  that would be rejected by it, and no live-data mutation involved.

## What was NOT verified

- **No end-to-end run against a live/staging backend.** Only the unit test
  suite (mocked Supabase, mocked orchestrator `run_chat_turn` generator) was
  exercised — the real `slowapi`/`limits` Redis-or-memory storage path was
  exercised via `TestClient` with the real `default_limiter` locally
  re-enabled (not mocked), but against in-memory storage, not a real Redis
  instance behind `RATE_LIMIT_REDIS_URL`. Multi-replica behavior (whether
  the limit is correctly shared across Fly/Railway replicas via Redis in
  production) was not verified live — it relies on the same
  `RATE_LIMIT_REDIS_URL`-backed `default_limiter` every other rate-limited
  admin endpoint already uses, so it inherits that existing, already-verified
  infrastructure rather than introducing a new one.
- **No `admin-dashboard` production build was run** (`npm run build` /
  equivalent). This PR does not modify any admin-dashboard/TypeScript file —
  only backend Python files and `ACTION_ITEMS.md`/a new doc were changed —
  so a frontend build was not applicable and was not run. The single
  frontend caller (`adminAiChat()` in `settings-ai.ts`) was read and
  confirmed unaffected (same request/response shape; a 429 was already a
  possible HTTP status from any `request()`-wrapped call in this codebase,
  and this endpoint gets no special client-side handling today), not
  exercised against a running admin-dashboard build.
- **No load/stress test** confirming the exact 20/minute threshold behaves
  correctly under concurrent requests from the same IP (e.g. race conditions
  in the `FixedWindowRateLimiter` counting) — relied on the existing,
  already-tested `AsyncLimiter`/`default_limiter` implementation
  (`backend/utils/async_limiter.py`, covered by `test_async_limiter.py`)
  rather than re-verifying its correctness from scratch.
- **The orchestrator's daily-cap exemption itself was read and confirmed
  correct by inspection** (`backend/ai/orchestrator.py` lines ~155-166) but
  not independently re-tested here — it is pre-existing, already-shipped
  behavior on `main` and out of scope for this change (AI12 only asked for
  the docstring to be fixed to match it, not for the exemption logic to be
  changed or newly tested).
- **AI9, AI10, AI11, AI14 and every other open item in the "AI assistant /
  MCP guardrail backlog"** were explicitly out of scope and not touched, per
  the task instructions (AI9 and AI2 are being worked on by other agents in
  parallel on the same `ACTION_ITEMS.md` file).

## 10. Sign-off

- [x] Rollback plan is concrete and testable — `git revert`, no data-layer
  remediation needed (no migration, no live-data mutation, no flag).
- [x] Blast radius is stated, not assumed — isolated to `POST /admin/ai/chat`
  and one new shared rate-limit constant; every backend and frontend caller
  enumerated in §4.
- [x] No silent behavior change to an already-shipped flow without the UX
  field filled in — §5 states plainly that only the admin-console operator
  is affected, only under sustained rapid-fire use, and that the
  rider/driver-facing daily-cap exemption behavior itself is unchanged (only
  its documentation was corrected).
