# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | (branch `claude/zoho-pii-scrub-gap`) |
| Related issue or gap ID | Discovered while fixing `TestScrubPii` (test_utils_extended.py) after F04's `routes/support.py` rewrite; not previously tracked in ACTION_ITEMS.md |

## 1. Issue / gap identified

Two of the three code paths that open a Zoho Desk support ticket sent rider/driver-authored free text to Zoho — a third party — **without PII scrubbing**:

1. `POST /support/escalate` (`backend/routes/support.py`) — `message`/`transcript` from the request body went straight to `create_support_ticket()` unscrubbed.
2. The AI assistant's `escalate_to_support` tool (`backend/ai/tools_support.py`) — the `reason` argument (model-relayed free text from the rider/driver's own conversation) was embedded unscrubbed into the ticket `message`, even though the *transcript* in the same function was already correctly scrubbed.

A rider typing "call me at 306-555-1234" while explaining a declined card, or mentioning that number to the AI assistant while asking for a human, would have had that phone number land verbatim in a third-party ticketing system.

## 2. Root cause

`ai/pii.py`'s `ScrubPolicy.STRICT` docstring already named `routes/support.py` as a protected boundary (referencing ADR-012, "scrub at the boundary"), and `ai/support_assistant.py` correctly applies `scrub_pii` to ticket data when drafting AI replies — but the actual ticket-creation call sites in `routes/support.py` and `ai/tools_support.py::escalate_to_support` were never wired to call it. `scrub_pii` had in fact been imported in `routes/support.py` at some point and was later removed as part of F04's Gemini-call removal (PR #5138) — F04 correctly gutted `/support/chat`'s direct AI call, but the accompanying import cleanup over-reached and also stripped `scrub_pii` from the file entirely, even though `/support/escalate` (a separate, untouched endpoint) still needed it. This was masked because the two pre-existing `/support/escalate` tests used non-PII fixture text ("I need help", "help"), so they never exercised the scrubbing behavior either way.

## 3. Fix / remediation

- `backend/routes/support.py`: re-import `scrub_pii` (dual-import pattern) and apply it to `req.message`/`req.transcript` before calling `create_support_ticket()` in `support_escalate()`.
- `backend/ai/tools_support.py`: wrap the `reason`-derived ticket `message` in `scrub_pii(...)` in `escalate_to_support()`, matching the scrubbing already applied to the transcript two lines above it in the same function.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped for every caller/reader of both changed functions:
  - `support_escalate` / `routes/support.py`: referenced only by its own test file (`tests/test_routes_support_coverage.py`) and `tests/test_utils_extended.py::TestScrubPii`. No other route or service calls `support_escalate` directly (FastAPI dispatches to it by route only).
  - `escalate_to_support` / `ai/tools_support.py`: referenced by `ai/orchestrator.py` (registers/dispatches the tool; only wires `_conversation_id` for the transcript, never touches `message`), `ai/prompts.py` (prompt text only), `schemas.py` (tool schema definition, not the ticket body), and its own tests (`tests/test_ai_tools_support.py`) plus two incidental hits in `tests/test_ai_mcp.py`/`tests/test_ai_public_assistant.py` that only check the tool is registered/dispatchable, not ticket content.
  - Neither change touches `create_support_ticket()` itself (`services/zoho_desk_integration.py`) or `zoho_desk_service.py` — only the two call sites that feed it.
- **No regression to existing behavior for non-PII input**: `scrub_pii` on STRICT policy is a no-op on text containing none of the patterns it targets (phone/email/GPS/postal/card/SIN), so the existing happy-path tests (`test_happy_path_returns_ticket_number`, `test_flag_enables_zoho_ticket`, etc., which all use non-PII fixture strings) pass unchanged.
- **What could regress**: none identified. This is strictly additive redaction on an egress boundary; no return shape, status code, or control-flow branch changed.

## 5. User-experience effect

Backend-only, invisible to rider/driver/admin. The ticket a Zoho Desk agent sees for a PII-bearing message will now show `[PHONE]`/`[EMAIL]`/etc. placeholders instead of the raw value — a support-ops-visible change (Zoho Desk agents), not a rider/driver/admin-facing one. No app UI, notification copy, or API response shape changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/support.py` | Re-added `scrub_pii` import; `support_escalate()` scrubs `message`/`transcript` before calling `create_support_ticket()` | Close PIPEDA gap: rider/driver support text reached Zoho Desk unscrubbed |
| `backend/ai/tools_support.py` | `escalate_to_support()` wraps the `reason`-derived `message` in `scrub_pii(...)` | Close the same class of gap at the AI-assistant tool's own Zoho escalation path |
| `backend/tests/test_routes_support_coverage.py` | Fixed a stale comment implying `scrub_pii` was deliberately removed from all of `routes.support` (only true for `/support/chat`); added `test_pii_in_message_and_transcript_is_scrubbed_before_reaching_zoho` | Lock in the fix with a real PII fixture; the two pre-existing escalate tests used non-PII text and gave no coverage of scrubbing |
| `backend/tests/test_ai_tools_support.py` | Added `test_reason_pii_is_scrubbed_before_reaching_zoho` to `TestEscalation` | Lock in the second fix; no existing test asserted on the `message` kwarg passed to `create_support_ticket` |

## 7. Before / after

```python
# Before — backend/routes/support.py
async def support_escalate(req: EscalateRequest, current_user: dict = Depends(get_current_user)):
    try:
        result = await create_support_ticket(
            user=current_user, message=req.message, transcript=req.transcript or None
        )
    ...
```

```python
# After — backend/routes/support.py
async def support_escalate(req: EscalateRequest, current_user: dict = Depends(get_current_user)):
    scrubbed_message = scrub_pii(req.message)
    scrubbed_transcript = scrub_pii(req.transcript) if req.transcript else None
    try:
        result = await create_support_ticket(
            user=current_user, message=scrubbed_message, transcript=scrubbed_transcript
        )
    ...
```

```python
# Before — backend/ai/tools_support.py (escalate_to_support)
ticket = await create_support_ticket(
    user=user, message=f"[AI escalation:{category}] {reason}", transcript=transcript
)
```

```python
# After — backend/ai/tools_support.py (escalate_to_support)
ticket = await create_support_ticket(
    user=user,
    message=scrub_pii(f"[AI escalation:{category}] {reason}"),
    transcript=transcript,
)
```

## 8. Rollback plan

No feature flag or data migration is involved — this is a pure code change with no schema/config dependency, and it does not touch already-applied Stripe charges, wallet deltas, or ride state. A `git revert` of this commit is a complete and sufficient rollback: the only observable effect of reverting is that ticket text sent to Zoho would again be unscrubbed, which is the pre-existing (undesired) state, not a new failure mode.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest tests/test_ai_tools_support.py tests/test_routes_support_coverage.py tests/test_utils_extended.py --no-cov -q` → 215 passed, 1 skipped, 1 failed. The 1 failure (`TestSupportChatIsARetiredStub::test_module_reaches_for_no_ai_engine_at_all`) is **pre-existing and unrelated** — confirmed via `git stash` and re-running against unmodified `main`: it fails identically because the module's own pre-existing docstring prose contains the substring `"run_chat_turn"`, which the test's source-scan assertion doesn't distinguish from an actual call. Not touched by this change; not fixed here (out of scope).
- [x] Manual repro: confirmed `scrub_pii()` output directly via `python3 -c` for representative phone/email fixtures before writing test assertions (e.g. `"Call me at 306-555-1234, my email is rider@example.com"` → `"Call me at [PHONE], my email is [EMAIL]"`).
- [x] Blast-radius grep performed: `grep -r "escalate_to_support"` (9 files, reviewed each) and `grep -r "support_escalate\|from.*routes\.support import\|routes/support"` (12 files) across `backend/`.
- [x] Reviewed against `CLAUDE.md` PIPEDA conventions ("what can never appear in logs/Sentry/analytics" — extended here to third-party egress) and `ai/pii.py`'s own `ScrubPolicy.STRICT` docstring, which already documented `routes/support.py` as an intended protected boundary.
- [ ] Feature-flagged: not applicable — this closes a data-leak gap outright; there is no "old" behavior worth preserving behind a flag, and the change is not user-visible.

## What was NOT verified

- Not tested against the real Zoho Desk sandbox/API — verified only against `create_support_ticket` mocked in unit tests. The scrubbing happens before the Zoho call, so this is a low-risk gap, but an actual Zoho ticket was not inspected end-to-end.
- Backend has no staging environment exercised in this session; no manual click-through of `/support/escalate` or the AI assistant's escalation flow against a running server was performed — verification is unit-test-level only, per this session's environment.
- Did not audit `services/zoho_desk_integration.py`/`zoho_desk_service.py` themselves for other unscrubbed fields (e.g. ticket subject line derivation, if any) beyond the two call sites identified — scoped to the two paths the failing test and the `escalate_to_support` review surfaced.

## Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level remediation needed)
- [x] Blast radius is stated, not assumed (isolated to two call sites and their own tests; confirmed via grep)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (UX effect: Zoho-agent-visible only, stated above)
