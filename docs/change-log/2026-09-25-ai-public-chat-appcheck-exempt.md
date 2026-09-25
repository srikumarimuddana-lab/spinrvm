# Change Impact & Risk Log — website AI chat 401 (App Check exemption)

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (requested by mkkreddy52) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/intelligent-goodall-42w02z` |
| Related issue or gap ID | Production access log 2026-09-25 14:35 — `POST /api/v1/ai/public-chat HTTP/1.1" 401 Unauthorized` |

## 1. Issue / gap identified

Every message sent to the spinr.ca website chat widget is rejected with 401 before it reaches the assistant, so the public website assistant does not work at all.

## 2. Root cause

`FirebaseAppCheckMiddleware` (backend/core/middleware.py) requires an `X-Firebase-AppCheck` header on every `/api/*` path not in `_APP_CHECK_EXEMPT_PREFIXES`. App Check attests Spinr's registered mobile builds; a browser cannot send it. `/api/v1/ai/public-chat` is anonymous by design (routes/ai.py) but was never added to the exemption list, so with enforcement on in production it always returns `{"detail": "App Check token required"}`. Same failure class as the tracking page, company portal and Stripe webhooks, each previously fixed the same way.

Not confirmed from here: that production has App Check enforcement on (the only 401 source on this handler is the middleware — the handler has no auth dependency — so this is the only explanation consistent with the log line).

## 3. Fix / remediation

Add the exact path `/api/v1/ai/public-chat` to `_APP_CHECK_EXEMPT_PREFIXES`. No other `/api/v1/ai/*` route is exempted.

Alternative considered: register the website with Firebase App Check's web (reCAPTCHA Enterprise) provider and send the header from the widget. Rejected for now: needs Firebase console setup and a change in the website codebase (not in this repo), while the endpoint's documented security model already treats it as public. Worth revisiting if bot spend becomes a problem.

## 4. Risk & impact on existing functionality

- Blast radius: single route. `str.startswith` matching; the only route served under this prefix is `POST /ai/public-chat` (routes/ai.py). `/ai/chat`, `/ai/config`, `/ai/conversations*` stay App-Check-enforced and keep their JWT dependencies — pinned by the new test.
- Security trade-off (accepted, and already documented in utils/rate_limiter.py): without App Check, the endpoint's controls are the `ai_public_chat_enabled` + `ai_assistant_enabled` kill switches, a 6/min per-IP limit (defeatable by IP rotation), and the "web" tool audience (read-only `search_faqs` + `get_company_info`, no user data). Worst case abuse = OpenAI spend; no data exposure.
- No DB, state-machine, money or background-loop interaction.

## 5. User-experience effect

- Website visitors: the chat widget starts answering instead of failing (the 401 is emitted outside CORSMiddleware, so browsers likely showed a generic network error).
- Riders/drivers in the apps: no change.
- Visible immediately on deploy, no mid-session effect.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/core/middleware.py | Added `/api/v1/ai/public-chat` to `_APP_CHECK_EXEMPT_PREFIXES` with rationale comment | Browser surface cannot attach App Check |
| backend/tests/test_appcheck_ai_public_chat_exempt.py | New test | Public chat reachable; signed-in AI routes still enforced; exemption is exact and matches the real served route |

## 7. Before / after

```
# Before — enforced, no header
POST /api/v1/ai/public-chat  -> 401 {"detail": "App Check token required"}
```

```
# After
POST /api/v1/ai/public-chat  -> handler (403/503 if kill switches off, 429 if rate limited, 200 otherwise)
POST /api/v1/ai/chat         -> 401 without App Check (unchanged)
```

## 8. Rollback plan

- To stop website AI traffic instantly without a deploy: Admin → Settings → AI Assistant → turn off "Enable public chat (website)" (`ai_public_chat_enabled = false`).
- To restore App Check on the path: revert the one-line exemption (redeploy). Acceptable — no live data is written by this change.

## 9. Verification performed

- [x] Exemption logic checked against the real `_APP_CHECK_EXEMPT_PREFIXES` tuple (AST-parsed): public-chat exempt; `/ai/chat`, `/ai/config`, `/ai/conversations`, `/ai/conversations/{id}/messages` still enforced.
- [x] Blast-radius grep: all `@api_router` routes in routes/ai.py; AI router mounted only under `/api/v1` (server.py).
- [x] ruff check + format clean.
- [ ] **New pytest file NOT executed** — the session sandbox could not install backend dependencies (PyPI blocked). CI must run it.
- [ ] No staging/production repro.

## What was NOT verified

- Whether production CORS (`ALLOWED_ORIGINS` env var) includes `https://spinr.ca` / `https://www.spinr.ca`. If the widget calls the API directly from the browser and the origin is missing, it will still fail after this fix (as a CORS error, not a 401). `always_allowed` in `init_middleware` does not include them.
- That the website widget sends the request body shape `PublicChatRequest` expects.
- Rate-limit keying in production: the access log shows an internal `172.16.x` peer, but `default_limiter` keys on `get_real_client_ip` (CF-Connecting-IP behind Cloudflare, utils/rate_limiter.py), so visitors should get separate 6/min buckets. Checked by reading code only, not observed live.
