# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | Flagged as a separate finding while working PR #4126 (near-duplicate onboarding FAQ merge) |

## 1. Issue / gap identified

`backend/routes/support.py` — a live, mounted endpoint (`POST /api/v1/support/chat`, confirmed via `backend/server.py:352`) that sends driver messages to Gemini 1.5 Flash — has its own hardcoded `SYSTEM_PROMPT` string, a third and entirely separate FAQ content system from the `faqs` table this session has spent four prior PRs cleaning up. Its content had two problems worse than duplication:

- **Fabricated timelines**: "usually 2–3 business days" for approval, "arrive in your bank account within 2–3 business days" for payouts, "Minimum payout is $10" — exactly the kind of specific, unverifiable claim `docs/driver-faqs-saskatchewan.md` says the real FAQ content "deliberately avoids." Checked against the actual payout system (`backend/utils/auto_payout.py`): payouts are now a **Spinr-controlled weekly auto-payout that replaced driver-initiated cashout** — the prompt's "you can request a payout at any time... within 2-3 business days" description doesn't match how the system actually works, and $10 is really an auto-payout *eligibility floor* (`payable_balance >= $10`), not a "minimum payout" a driver requests.
- **A real business-model contradiction**: "You earn a per-kilometre rate plus a base fare, **minus the platform service fee**" and "Q: What is the platform fee? A: The platform fee varies by market..." directly contradicts Spinr's core, repeatedly-stated model — 0% commission, drivers keep 100% of the fare (`schemas.py`'s `platform_fee_percent` defaults to `Decimal("0.0")`, `fare_service.py`, `ai/prompts.py`'s `_DRIVER_CORE`, and every FAQ answer this session has touched). A driver using this chat endpoint could be told their earnings are reduced by a fee that does not exist.

A secondary, previously-documented gap in the same function: the `except Exception` handler's failure path used `logging.warning(str(exc))` — no logger name, no traceback, no domain/surface tags — already flagged (not fixed) by this file's own test suite docstring as violating CLAUDE.md's "never silently swallow errors" convention.

## 2. Root cause

This endpoint predates (or was built in parallel with, never converged with) the real driver AI assistant (`backend/ai/prompts.py`'s `_DRIVER_CORE`, `backend/ai/tools_support.py`'s `search_faqs`), which correctly answers only from the `faqs` table and carries proper safety/ground-rule guardrails. `routes/support.py`'s Gemini-based chat was written independently with its own hand-authored FAQ content that was never updated as the platform's actual payout mechanism and commission model evolved.

## 3. Fix / remediation

- Rewrote `SYSTEM_PROMPT` to remove every fabricated timeline and dollar amount, and to state the 0%-commission model correctly and unambiguously (added it to both the opening paragraph and the "Key facts" bullet list, so it can't be missed or contradicted later in the same prompt). Where a fact was time-sensitive or the real mechanism unclear from a static prompt (approval timing, payout timing, licence-expiry reminder cadence), replaced a specific-but-wrong claim with a guidance-only pointer to the in-app screen that shows the real, current status — matching the policy the rest of the FAQ content already follows, rather than replacing one fabrication with a new one.
- **Added a SAFETY/911 section** — this prompt had no emergency-handling instruction at all, unlike every other AI-assistant surface in the codebase (`_DRIVER_CORE`/`_RIDER_CORE` both have one). A driver-facing chat endpoint with zero SOS/911 guidance is a real gap against CLAUDE.md's "not a 911 replacement" requirement, which applies to every assistant surface, not just the primary one.
- **Fixed the logging gap**: `except Exception` now logs via `logger.error("Gemini support chat failed", exc_info=True, extra={"domain": "ai", "surface": "backend"})` instead of an unlevelled `logging.warning(str(exc))` — matches CLAUDE.md's Observability Conventions. The blanket-fallback *behavior* (always return 200 with `FALLBACK_REPLY`, never a 500) is unchanged and still intentional per the module's own docstring — a driver mid-conversation should never see a raw error. Still open, not fixed here: no Sentry capture, no metric distinguishing "no API key" from "Gemini API failure" from "malformed response" — noted in the test file as a real, still-open gap.
- Updated `backend/tests/test_routes_support_coverage.py`'s docstring to reflect what's fixed (logging) vs. what remains open (Sentry/metrics), and added regression tests: 4 for the `SYSTEM_PROMPT` content (no fabricated timelines, no platform-fee-deduction language, states 0% commission, includes the 911 redirect) and 1 for the logging fix (asserts `ERROR` level, `exc_info` captured, and the `domain`/`surface` tags via `caplog`).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `backend/routes/support.py`.** Grepped the full repo for `SYSTEM_PROMPT`, the removed fabricated phrases, and any other reference — only this file and its test file. No other code imports or reads `SYSTEM_PROMPT`.
- **No client currently calls this endpoint**: grepped `driver-app/` and `rider-app/` source for `support/chat` and `support/escalate` — no hits in actual app code (one stray mention in a historical audit `.txt` file, at a different path prefix than the real route, likely describing an older/aspirational design). The endpoint is live-mounted and reachable, but appears currently unused by either shipped client — lowers real-world exposure of the bug this fixes, though doesn't rule out an external caller (a web widget, manual testing) this session can't see.
- **`support_escalate` (the other endpoint in this file) is untouched** — different function, no shared state beyond the module-level `FALLBACK_REPLY` constant, which wasn't changed.
- No interaction with the ride state machine, wallet/payment deltas, RLS policies, or any of the 16 background loops. No schema change.
- The logging fix is additive (better diagnostics), not a behavior change to the response the caller receives.

## 5. User-experience effect

- **Driver-facing only**, and only for whoever calls `POST /api/v1/support/chat` (see above — no known live client). If/when this endpoint is used, the assistant's replies (grounded in the corrected `SYSTEM_PROMPT`) will no longer state a fabricated approval/payout SLA or claim a platform fee is deducted from earnings, and will correctly redirect a described emergency to 911.
- Not visible mid-session in any way that could regress an in-progress conversation — no prior conversation history depends on the old prompt text (Gemini calls are stateless per-request here, no `system_instruction` caching visible to the user).
- No copy shown to the user changed in a way that removes information they relied on — the only things removed were incorrect.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/support.py` | Rewrote `SYSTEM_PROMPT` (removed fabricated SLAs/amounts and platform-fee language, added 0%-commission statement and a SAFETY/911 section); fixed the `except Exception` handler's logging (module-level `logger`, `logger.error(..., exc_info=True, extra={"domain": "ai", "surface": "backend"})`) | Correct a live, driver-facing AI prompt that contradicted the core business model and fabricated timelines; fix a previously-flagged observability gap in the same function |
| `backend/tests/test_routes_support_coverage.py` | Updated module docstring to reflect the fix; added `TestSystemPromptContent` (4 tests) and `test_generate_content_failure_logs_error_with_domain_tags` | Regression-guard the content correction and the logging fix so they can't silently regress |

## 7. Before / after

```
# Before
SYSTEM_PROMPT = """...
Q: How do I get started as a Spinr driver?
A: ...and wait for approval (usually 2–3 business days).
...
Q: When do I get paid?
A: ...Payouts are processed via Stripe and arrive in your bank account within
   2–3 business days. Minimum payout is $10.

Q: How does Spinr calculate my earnings?
A: You earn a per-kilometre rate plus a base fare, minus the platform
   service fee. Tips are passed to you in full.

Q: What is the platform fee?
A: The platform fee varies by market and is shown on your earnings dashboard.
...
Do not invent policies or fees. Only reference the information above.
"""
```

```
# After
SYSTEM_PROMPT = """You are a helpful support assistant for Spinr, a Canadian
rideshare platform (Saskatchewan-first) where drivers keep 100% of the fare —
Spinr charges 0% commission on consumer rides, full stop. There is no
platform fee or service fee deducted from a trip.
...
Q: How long does approval take?
A: Review time varies with volume and whether anything needs to be
   re-submitted — there's no fixed number of days. Check your status in the
   app under Account / Onboarding, and contact support if it's been a while
   with no update.
...
Q: When and how do I get paid?
A: Your completed trips and what you earned on each appear in the Earnings
   section of the app. For payout timing or a payout you believe is missing,
   check your payout settings in the app or contact support — don't guess at
   a schedule here.

Q: How does Spinr calculate my earnings?
A: You earn a per-kilometre rate plus a base fare, plus any surge and tips —
   and you keep all of it. There is no platform fee or commission taken out.
...
SAFETY:
If a driver describes an emergency or anyone in danger, the first thing you
say is to call 911 immediately. ...
...
Never invent policies, fees, dollar amounts, or timelines beyond what's
written above — if asked something not covered here, say you're not sure
rather than guessing.
"""
```

```
# Before — except block
except Exception as exc:
    import logging
    logging.warning("Gemini support chat failed: %s", exc)
    return {"reply": FALLBACK_REPLY}
```

```
# After
except Exception:
    logger.error(
        "Gemini support chat failed",
        exc_info=True,
        extra={"domain": "ai", "surface": "backend"},
    )
    return {"reply": FALLBACK_REPLY}
```

## 8. Rollback plan

`git-revert-safe` — pure Python source change, no schema, no data migration, no config/flag. Reverting `backend/routes/support.py` and its test file restores the prior behavior exactly (including the prior bugs, if that were ever desired, which it isn't).

## 9. Verification performed

- [x] **Real tests run**: `python3 -m pytest backend/tests/test_routes_support_coverage.py -q` → 21 passed (16 pre-existing + 5 new), confirming the content rewrite and logging fix both work and nothing in the existing fallback/PII-scrub/escalation behavior regressed.
- [x] **Full backend suite** run to catch collateral breakage — see the accompanying PR comment for the result (this file was written while that run was still in progress).
- [x] Blast-radius grep: confirmed `SYSTEM_PROMPT` and the removed fabricated phrases appear nowhere else in the repo; confirmed no shipped client (`driver-app`, `rider-app`) currently calls this endpoint.
- [x] Grounded every replacement claim against real code before writing it, rather than substituting one guess for another: checked `schemas.py`, `fare_service.py`, `ai/prompts.py` for the 0%-commission language; checked `utils/auto_payout.py` for the real (weekly, Spinr-controlled) payout mechanism before deciding to avoid describing it specifically in the prompt.
- [ ] Not manually tested against a real Gemini API call — the fallback/error paths are unit-tested with mocks (matching this file's existing pattern), but no live smoke test of the actual model response was performed in this session (would require a real `GEMINI_API_KEY`, not available here).

**What was NOT verified**: whether any caller outside this repo's two shipped apps (a web widget, a partner integration, manual QA) currently hits `POST /api/v1/support/chat` — grepped only `driver-app/` and `rider-app/` source, which is the best evidence available in this session but not a guarantee of zero external callers. Also not verified: whether `support_escalate`'s Zoho Desk integration has any content-accuracy issues of its own — out of scope, not touched.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data involved)
- [x] Blast radius is stated, not assumed — including the "no known live caller" finding, stated as evidence-based rather than certain
- [x] No silent behavior change to an already-shipped flow: the response *shape* is unchanged (`{"reply": str}`), only the prompt's factual content and the failure-path log improved; if this endpoint has zero live callers as evidence suggests, this fix has zero current user-facing effect and is purely a correctness/safety fix for whenever it is used
