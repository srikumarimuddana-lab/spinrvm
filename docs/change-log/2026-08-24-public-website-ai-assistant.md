# Change Impact & Risk Log — public website AI assistant

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude (session: srikumarimuddana@gmail.com) |
| Surface(s) | backend (plus the separate `desktop_website` repo, which consumes it) |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/spinrvm-faq-legal-api-ioiryg` |
| Related issue or gap ID | — (requested directly: serve the website's FAQ, legal and chat from spinrvm) |

## 1. Issue / gap identified

The spinr.ca marketing site had no connection to this backend at all. It kept its
own copy of the FAQs and legal documents in its own Supabase project, and its chat
widget ran its own retrieval stack (LangChain + DashScope Qwen) over its own
`knowledge_base` table. So the same question could be answered one way in the
rider/driver app and a different way on the website, and a FAQ edited in the
spinrvm admin dashboard never reached the website at all.

## 2. Root cause

Not a bug — the two products were built separately and never integrated. The
public read endpoints this needs (`GET /faqs`, `GET /legal-documents`) already
existed and were already unauthenticated; nothing consumed them from outside the
apps. The AI assistant, by contrast, could not be consumed by the website even in
principle: `POST /ai/chat` requires a rider/driver JWT, and its tool set reads the
signed-in user's own rides, wallet and documents.

## 3. Fix / remediation

A new anonymous surface, `POST /api/v1/ai/public-chat`, backed by a new stateless
module `backend/ai/public_assistant.py`.

It deliberately does **not** reuse `orchestrator.run_chat_turn`. That path is built
around a signed-in user: it persists every turn to `ai_conversations`, whose
`user_id` is `NOT NULL REFERENCES users(id)` and whose `audience` is CHECKed
against `('rider','driver')`, and it meters a daily cap keyed on `user["id"]`. A
website visitor has no user row, and minting synthetic ones for anonymous traffic
would be both a foreign-key problem and a PIPEDA data-minimization failure — we
would be storing conversations for people who never signed up.

So this follows the precedent `ai/support_assistant.py` already set: a separate
path that reuses the shared provider factory and shared tool handlers and owns
nothing durable. It reaches the *same* provider, model and FAQ rows as the in-app
assistant — which is the whole point — while persisting nothing.

Safety is enforced by the tool registry rather than by the handler. A new `web`
tool audience is opted into by exactly two read-only tools (`search_faqs`,
`get_company_info`). Every account, ride and booking tool stays rider/driver-only,
and `execute_tool` re-checks the audience before dispatching, so a model asking
for one by name gets an error result rather than a handler call. The synthetic
caller carries no `id` key at all, so a handler that reached for one would raise
loudly rather than read someone's row by accident.

`escalate_to_support` is excluded on purpose: it can open a real Zoho ticket, and
an anonymous visitor has no account to attach one to.

## 4. Risk & impact on existing functionality

**Blast radius: isolated additive, with one shared-function edit.**

Blast-radius grep performed on every shared thing touched:

- `search_faqs` — callers are `ai/tools.py::execute_tool` (registry dispatch) and
  the `/mcp` mount (`ai/mcp_server.py`, auto-registers `mcp_exposed=True` specs).
  This is the only behavior-changing edit in the diff: the FAQ-row query now uses
  `_faq_audience(user)` instead of the tool audience directly. For rider and
  driver callers `_faq_audience` returns the tool audience unchanged, so both
  existing consumers are byte-identical; only the new `web` audience takes the
  new branch. `_current_area_scope` is deliberately still passed the *tool*
  audience, because its driver-area fallback calls
  `get_driver_by_user_id_cached(user["id"])` — passing the resolved FAQ audience
  would send an anonymous "driver-type" visitor down a path that needs a real
  user id.
- `get_company_info` — same two consumers; audience widened only, no body change.
- `build_system_prompt` — callers are `ai/orchestrator.py` and now
  `ai/public_assistant.py`. The driver/else ternary became a `_CORES.get(audience,
  _RIDER_CORE)` lookup; rider, driver and unknown audiences all resolve exactly as
  before.
- `faqs` table — read-only here. The public `GET /faqs` handler in `features.py`
  and the admin CRUD in `routes/admin/faqs.py` are untouched.
- `settings` table — additive column only.

**Not touched:** the ride state machine, dispatch, wallets, Stripe, insurance
periods, and all 18 background loops in `core/lifespan.py`. No new loop.

**What could regress:** the honest answer is the shared `search_faqs` handler — it
serves the rider app, the driver app and `/mcp`, and it is now also on an
anonymous path. The mitigation is that the anonymous path adds a branch rather
than changing one, and `test_ai_public_assistant.py` plus the existing 33 cases in
`test_ai_tools_support.py` both pass.

**Cost/abuse:** this is an unauthenticated path to a paid LLM. Bounded by the
`ai_public_chat_enabled` kill switch, a 6/minute per-IP rate limit, an 8-turn
history cap and a 3-iteration tool loop. Per-IP keying is defeatable by rotating
source IPs — stated plainly in the code comment rather than implied to be
load-bearing. The kill switch and the two-tool ceiling are the real controls: the
worst case for a determined abuser is LLM spend, not data exposure.

## 5. User-experience effect

**Nobody sees a difference yet** — `ai_public_chat_enabled` defaults false, so
merging this changes nothing until an admin flips it.

- Rider / driver: no change. The in-app assistant's prompt, tools and persistence
  are untouched.
- Internal admin: one new toggle in Settings → AI Assistant.
- Website visitor (once enabled): the chat widget starts answering from the same
  FAQ corpus and the same model the apps use, instead of the website's own copy.
- Nothing is visible mid-session to anyone already using the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/tools_support.py` | Added `WEB` audience + `_BOTH_AND_WEB`; `search_faqs`/`get_company_info` opt in; new `_faq_audience()` maps the web audience onto a rider/driver FAQ tag | Gate the anonymous tool set in the registry, not the caller |
| `backend/ai/prompts.py` | Added `_WEB_CORE`; `build_system_prompt` dispatches via `_CORES` | Public persona: no account language, no invented prices, Saskatoon-only |
| `backend/ai/public_assistant.py` | New stateless turn runner | Same provider/model/FAQ corpus as the app, with nothing persisted |
| `backend/routes/ai.py` | New `POST /ai/public-chat` + `PublicChatRequest`/`PublicChatMessage` | The anonymous entry point |
| `backend/utils/rate_limiter.py` | New `ai_public_chat_limit` (6/min, per IP) | Only bound available without an account to key on |
| `backend/routes/admin/settings.py`, `backend/schemas.py` | New `ai_public_chat_enabled` field | Kill switch independent of `ai_assistant_enabled` |
| `backend/migrations/363_settings_add_ai_public_chat_enabled.sql` | `ADD COLUMN IF NOT EXISTS ai_public_chat_enabled BOOLEAN DEFAULT false` | Without the column the first Settings save 503s (PGRST204) |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Added the column to `KNOWN_SETTINGS_COLUMNS` | Required same-PR by that test's own maintenance note |
| `backend/tests/test_ai_public_assistant.py` | New, 24 cases | Pin the boundary of the only unauthenticated AI path |

## 7. Before / after

The one behavior-changing diff. Everything else in this change is additive.

```python
# Before — ai/tools_support.py::search_faqs
async def search_faqs(user, query):
    audience = user.get("ai_audience", "rider")
    ...
    rows = await db_supabase.get_rows(
        "faqs", {"is_active": True, "audience": {"$in": ["both", audience]}}, ...
    )
    scope = await _current_area_scope(user, audience)
```

```python
# After — the row query uses the resolved FAQ audience; the area scope
# deliberately still uses the TOOL audience (its driver branch needs a user id)
async def search_faqs(user, query):
    audience = user.get("ai_audience", "rider")
    faq_audience = _faq_audience(user)     # "web" -> rider|driver; else unchanged
    ...
    rows = await db_supabase.get_rows(
        "faqs", {"is_active": True, "audience": {"$in": ["both", faq_audience]}}, ...
    )
    scope = await _current_area_scope(user, audience)
```

For `audience in ("rider", "driver")`, `_faq_audience` returns `audience` — the
two existing consumers see no change.

```python
# Before — ai/prompts.py
core = _DRIVER_CORE if audience == "driver" else _RIDER_CORE
# After
core = _CORES.get(audience, _RIDER_CORE)   # rider/driver/unknown resolve identically
```

## 8. Rollback plan

**Flip `ai_public_chat_enabled` to false** in admin → Settings → AI Assistant. No
deploy, effective within the 60s `settings_loader` TTL. The surface then refuses
every request before constructing an adapter, and the website falls back to its
own pipeline (that fallback is implemented on the `desktop_website` side).

No data-level remediation is possible or needed: this path writes nothing. There
are no rows to unwind — no conversation, no message, no ride, no money.

Migration 363 rollback, if ever wanted (not expected — the flag is the real
rollback):

```sql
ALTER TABLE settings DROP COLUMN IF EXISTS ai_public_chat_enabled;
```

## 9. Verification performed

- [x] Automated tests: `tests/test_ai_public_assistant.py` (24 new, all pass);
      `pytest -k "ai or settings"` → **3552 passed, 2 skipped, 1 xfailed**.
      `ruff check` + `ruff format` clean on every touched file.
- [x] Route registration verified against the live OpenAPI schema —
      `/api/v1/ai/public-chat` is present.
- [x] Tool gating verified at runtime, not just asserted in a test:
      `tool_defs_for("web")` → `['get_company_info', 'search_faqs']`, against 18
      rider tools and 6 driver tools.
- [x] Blast-radius grep performed: `search_faqs`, `get_company_info`,
      `build_system_prompt`, `audiences=`, `ai_audience`, `_current_area_scope`,
      `faqs` table readers, `settings` write allowlist.
- [x] Reviewed against CLAUDE.md conventions: PIPEDA (nothing persisted for
      anonymous visitors; PII scrubbed pre-egress), observability (Sentry
      `domain=ai`/`surface=backend`, `spinr_ai_*_total` metric naming), dual
      import pattern, "do not silently swallow errors" (no canned fallback —
      provider failures raise), additive-over-destructive, ship-dark flag.
- [x] Feature-flagged: `ai_public_chat_enabled`, default false.
- [ ] Manual repro in staging — **not done**, see below.

## 10. What was NOT verified

State it plainly rather than letting the ticks above imply full coverage:

- **No real LLM call was ever made.** Every test uses a scripted `FakeAdapter`.
  The tool loop, the message shapes and the bounds are covered; the actual
  quality of an answer from the configured provider is not.
- **Not run against live Supabase.** No real `faqs` rows were searched, and
  migration 363 has not been applied anywhere — it is committed, not deployed.
- **No staging deploy, no manual browser repro.** The endpoint was verified by
  route registration and unit tests, not by a request over the wire.
- **CORS is unverified and is a deployment prerequisite.** `ALLOWED_ORIGINS`
  currently defaults to localhost origins only. The website origin must be added
  there or the browser call fails preflight. That is a config change outside this
  diff.
- **The per-IP rate limit is only as good as its storage.** With
  `RATE_LIMIT_REDIS_URL` unset the limiter is per-process, so on a multi-replica
  deploy the effective limit is 6/minute × replicas. This is pre-existing
  behavior, not introduced here, but it matters more on an unauthenticated path.
- **The threat tripwire's `source="public_web"` value has no admin-console
  filter yet.** Events are recorded and will appear, but nothing groups them as
  website traffic.
