# Change Impact & Risk Log — AI chat quotes the admin Settings email instead of `[EMAIL]`

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Author | agent |
| Surface(s) | backend (in-app rider/driver AI chat) |
| Domain (Sentry tag) | ai |
| PR / commit link | |
| Related issue or gap ID | Rider Help & Support AI chat showing `[EMAIL]` for the company address configured in admin Settings |

## 1. Issue / gap identified

The rider-app AI assistant (Help & Support chat) answers contact questions with the literal token `[EMAIL]` instead of the company email saved on the admin Settings page (`company_email`).

## 2. Root cause

`scrub_pii` redacts every email-shaped string to `[EMAIL]`. That runs on (1) `get_company_info` tool results before they re-enter the model and (2) the streamed/persisted assistant reply. The public web assistant already skips reply-scrubbing because the support address is public marketing contact; the in-app path did not, so the model either received `[EMAIL]` from the tool or had the real address stripped on the way to the rider. F08 (filter streamed output) is still required — a personal or synthetic email in the same reply must still redact.

## 3. Fix / remediation

Allowlist the admin Settings `company_email` / `company_phone` through the email/phone pass (`preserve=` on `scrub_pii`). Wired into the in-app orchestrator (user message, stream filter, persisted reply) and into `_cap_result` (settings cache, plus the unscrubbed `get_company_info` payload when the cache is cold). Comparison is whole-match: case-insensitive for emails, digit-normalized for phones, so a lookalike address is not kept as a substring.

Alternative considered: skip reply-scrubbing entirely (the public-assistant approach). Rejected — F08 exists specifically so a scripted provider reply containing a synthetic email cannot reach the rider.

## 4. Risk & impact on existing functionality

- **Blast radius:** single-surface (backend AI chat). `scrub_pii` default (no `preserve`) is unchanged for Sentry (`utils/sentry_scrub.py`), logs (`utils/log_guard.py`), support tickets (`routes/support.py`, `ai/support_assistant.py`, `ai/tools_support.py` transcript), the public visitor *message* scrub, and `/mcp`'s STRICT re-serializer. Personal emails still redact in-app.
- Callers of `scrub_pii` / `scrub_pii_deep` / `StreamingOutputFilter` grepped: only orchestrator, tools `_cap_result`, and stream_filter pass `preserve`. Every other caller keeps prior behaviour.
- No ride state machine, wallet, Stripe, insurance-period, or background-loop interaction.
- FAQ-cache entries written *before* this deploy may still contain `[EMAIL]` until `ai_faq_cache_ttl_seconds` (default 1 hour) expires.

## 5. User-experience effect

- **Who:** rider and driver in-app AI chat (Help & Support). Internal admin Settings is the source of the address, not a changed screen.
- **Visible mid-session:** yes — the next assistant reply after deploy can show the real email. Previously cached first-message FAQ replies may still show `[EMAIL]` until TTL.
- **Copy:** no new notification/copy; the assistant can now print the already-configured support address.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/pii.py` | `preserve=` allowlist + `official_contact_preserve()` | Keep official contact; still redact personal identifiers |
| `backend/ai/stream_filter.py` | Thread `preserve` through the incremental scrub | Rider-visible stream must match the stored copy |
| `backend/ai/orchestrator.py` | Pass Settings contact into user-scrub, stream filter, persistence | In-app chat is the live-tested surface |
| `backend/ai/tools.py` | `_cap_result` preserves Settings contact; `get_company_info` keeps its own email/phone | Model must not be handed `[EMAIL]` |
| `backend/tests/test_ai_pii.py` | Official-contact preserve cases | Pin whole-match / case / phone-digit behaviour |
| `backend/tests/test_ai_tools_support.py` | Assert `get_company_info` email | Direct repro of the rider-visible token |
| `backend/tests/test_ai_orchestrator.py` | Streamed + persisted reply keeps Settings email, redacts a personal one | Wiring pin |
| `backend/tests/test_ai_stream_filter.py` | Incremental release keeps Settings email | Holdback path, not flush-only |
| `docs/change-log/2026-09-15-ai-chat-preserve-official-contact.md` | This log | Live-tested AI surface |

## 7. Before / after

```
# Before (get_company_info → model, then streamed reply)
{"email": "[EMAIL]", "phone": "1-800-SPINR"}
"Email [EMAIL] for this."
```

```
# After
{"email": "support@spinr.ca", "phone": "1-800-SPINR"}
"Email support@spinr.ca for this."
# Personal address in the same reply is still "[EMAIL]"
```

## 8. Rollback plan

Backend-only behaviour. No migration, no `app_settings` flag. Revert the deploy (or revert the four `backend/ai/*.py` files) to restore previous redaction. No Stripe/wallet/ride-state rows to unwind. Stale FAQ-cache entries with the real email after rollback would be the inverse of today's bug and expire on the same TTL; flush `ai:faq:*` in Redis if that window matters.

Not feature-flagged: this restores the intended public contact quote rather than shipping new UX, and a flag-off path would recreate the `[EMAIL]` bug. The allowlist is only the values already saved in admin Settings.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_ai_pii.py tests/test_ai_stream_filter.py tests/test_ai_tools_support.py tests/test_ai_orchestrator.py tests/test_ai_mcp.py tests/test_ai_public_assistant.py --no-cov -p no:xonsh` → 384 passed, 1 skipped; then stream-filter preserve cases re-run parametrized across chunk sizes 1/7/29/97
- [x] `ruff check` on the four production files + four test files → clean
- [ ] Manual repro in a live rider-app session against a real provider (not done)
- [x] Blast-radius grep: `scrub_pii(`, `scrub_pii_deep(`, `StreamingOutputFilter(`, `_cap_result(`, `official_contact_preserve`
- [x] PIPEDA: personal email/phone/card/SIN still redact; only admin-configured company contact is kept. `/mcp` STRICT re-scrub still redacts company email on that third-party egress (unchanged).
- [x] Not feature-flagged — see §8
- [x] Red-green: new tests failed first (`get_company_info` email was `[EMAIL]`; streamed reply was `Email [EMAIL], not [EMAIL].`) then passed after the allowlist

## 10. What was NOT verified

- No live Supabase / live LLM provider turn; tests use FakeAdapter and patched Settings.
- rider-app has no visual-regression tooling; this is a backend text change, reasoned about rather than screenshotted.
- No `npm run build` (no admin-dashboard / rider-app / driver-app code change).
- `/mcp` clients still see `[EMAIL]` for company contact in STRICT serialization — out of scope for the in-app Help & Support bug.
- Existing FAQ-cache rows were not flushed.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
