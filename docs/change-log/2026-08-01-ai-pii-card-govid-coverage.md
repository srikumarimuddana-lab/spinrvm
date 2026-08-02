# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code (AI guardrail audit follow-up) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | (this branch) |
| Related issue or gap ID | Found by `/ai-check` (`spinr-ai-guardrail-reviewer`) auditing `backend/ai/pii.py` directly |

## 1. Issue / gap identified

`backend/ai/pii.py`'s `scrub_pii()` — the PII scrubber applied before every message reaches a third-party LLM provider and before persistence — had zero regex coverage for payment card numbers and zero coverage (and no documented mitigation, unlike the "names" gap) for government ID/SIN numbers. Both are explicit CLAUDE.md PIPEDA ban-list items ("Payment card numbers — Stripe handles; never log even masked PANs", "Government IDs, SIN, driver license numbers"). A rider pasting either into an AI chat message or a support ticket would have had it forwarded to Anthropic/OpenAI/Gemini and persisted to `ai_messages` unredacted.

## 2. Root cause

The module was extracted from `routes/support.py` (per its own docstring) covering "the highest-risk identifiers" at the time — phones, emails, GPS coordinates, postal codes — but was never extended as new provider-egress paths (the AI chat orchestrator, the MCP tool layer) were added. No one had audited the scrubber's own regex coverage against the full PIPEDA ban-list; audits so far had focused on whether `scrub_pii` was *called* on each path, not on whether the function itself covers every banned category.

## 3. Fix / remediation

Added two entries to `_PII_PATTERNS`:
- **Card numbers** (`[CARD]`): prefix-gated on recognized card-network IINs (Visa, Mastercard, Amex, Discover) — same discriminator principle this file already uses for phone numbers (gate on a valid-shape prefix, not digit count alone, to avoid colliding with this codebase's own long numeric ids). Matches bare or space/dash-grouped digits for the common brands; documented limitation: a dash/space-separated Amex number in its real 4-6-5 display grouping is not matched (bare Amex still is).
- **Government ID / SIN** (`[GOVID]`): grouped 3-3-3 form only (`123-456-789` / `123 456 789`). Bare ungrouped 9 digits is deliberately NOT matched — there is no reliable prefix-style discriminator for 9 bare digits in this codebase's own log/id shapes, so matching on digit count alone would repeat this file's own documented timestamp-collision regression (an earlier bare-10-digit phone pattern rewrote every unix timestamp to `[PHONE]` in production Sentry events). Driver's license numbers remain unmitigated by regex (no fixed cross-provincial format) — documented explicitly in the module docstring and the pattern's own comment, same treatment as the pre-existing "names" gap (mitigated via data-minimization: `prompts.py` never asks for these fields — verified by grep, no hits).

Also updated `backend/utils/log_guard.py`'s `_SCREEN` cheap pre-filter to recognize both new shapes. `_SCREEN` gates whether the (comparatively expensive) `scrub_pii` call runs at all on the loguru sink path — without this, the new patterns would exist in `pii.py` but never actually fire for a raw log line, reproducing this codebase's own documented "two independent paths that look like one check" failure mode (`.claude/context/domain-corporate.md`'s lessons-learned, cited directly in `spinr-ai-guardrail-reviewer`'s own rules). Added a comment requiring the two files be kept in sync going forward.

## 4. Risk & impact on existing functionality

Blast radius grep performed on `scrub_pii` (all callers, not just the AI surface):

| Caller | What it scrubs | Effect of this change |
|---|---|---|
| `backend/ai/orchestrator.py` (2 call sites) | User chat message before provider egress; assistant reply before persistence | Now also catches card/SIN in AI chat — the primary target of this fix |
| `backend/ai/support_assistant.py` (3 call sites) | Support ticket subject, description, thread body before summarization | Now also catches card/SIN in support tickets |
| `backend/routes/support.py` | Legacy driver support chat message | Same |
| `backend/utils/log_guard.py` | Loguru sink guard — every log line across the whole backend (via `_SCREEN` + `scrub_pii`) | New: a card number or grouped SIN accidentally interpolated into a raw log line (e.g. an error handler logging a request body) is now redacted where it previously was not. This is a net-new redaction on a shared, whole-backend surface, not scoped to AI. |
| `backend/utils/sentry_scrub.py` | Every captured Sentry exception/breadcrumb value across the whole backend | Same as log_guard — new redaction coverage, whole-backend scope |

**Regression risk (over-matching / observability damage):** this is the specific failure mode this file has hit before (the timestamp-as-phone-number regression documented in its own comments), so it was the primary thing tested for:
- Card pattern is prefix-gated (not digit-count-only) — verified a 16-digit non-card-prefixed id (e.g. a hypothetical ride/session reference) is NOT redacted.
- SIN pattern requires the 3-3-3 separator shape — verified a bare ungrouped 9-digit number (e.g. an order number) is NOT redacted.
- Re-ran every pre-existing test case in `test_ai_pii.py` and `test_log_guard.py`'s observability-preservation list by hand (pytest unavailable in this session's environment — see Verification section) — all pass unchanged.

**No interaction** with the ride state machine, background loops, or wallet/money deltas — this is a text-transformation utility with no side effects beyond the string it returns.

## 5. User-experience effect

None directly visible. If a rider or driver pastes a card number or SIN into AI chat or a support ticket, the assistant/agent-facing transcript and any provider payload will now show `[CARD]`/`[GOVID]` instead of the raw value — this is strictly a privacy improvement to what already-redacted text looks like (phones/emails/coordinates were already tokenized the same way), not a new user-facing behavior class.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/pii.py` | Added `[CARD]` and `[GOVID]` patterns to `_PII_PATTERNS`; updated module docstring | Close the two PIPEDA ban-list gaps found by `/ai-check` |
| `backend/utils/log_guard.py` | Added matching prefix/shape detection to `_SCREEN`; added a sync-requirement comment | Without this, the new patterns are invisible to the loguru sink guard |
| `backend/tests/test_ai_pii.py` | Added `TestCardNumbers` and `TestGovernmentIds` (positive + negative/boundary cases) | Regression coverage for both new patterns and their false-positive guards |
| `backend/tests/test_log_guard.py` | Added two sink-level tests (`test_card_number_in_message_is_caught_at_the_sink`, `test_grouped_sin_in_message_is_caught_at_the_sink`) | Prove the `_SCREEN` + `scrub_pii` pipeline works end to end, not just `scrub_pii` in isolation |

## 7. Before / after

```python
# Before — backend/ai/pii.py's _PII_PATTERNS ended at postal codes:
    # Canadian postal codes (A1A 1A1 or A1A1A1)
    (re.compile(r"\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b"), "[POSTAL]"),
]
```

```python
# After — two new entries added (see backend/ai/pii.py for full patterns/comments):
    (re.compile(r"\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b"), "[POSTAL]"),
    (re.compile(r"(?<!\d)(?:4\d{3}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}|...)(?!\d)"), "[CARD]"),
    (re.compile(r"(?<!\d)\d{3}[\s-]\d{3}[\s-]\d{3}(?!\d)"), "[GOVID]"),
]
```

```python
# Before — backend/utils/log_guard.py's _SCREEN:
_SCREEN = re.compile(
    r"@"
    r"|-?\d{1,3}\.\d{2,}"
    r"|(?<![\d+])(?:\+\d{10}|[2-9]\d{9})"
    r"|\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b"
)
```

```python
# After:
_SCREEN = re.compile(
    r"@"
    r"|-?\d{1,3}\.\d{2,}"
    r"|(?<![\d+])(?:\+\d{10}|[2-9]\d{9})"
    r"|\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b"
    r"|(?<!\d)(?:4\d{3}|5[1-5]\d{2}|2(?:2[2-9]\d|[3-6]\d{2}|7[01]\d|720)|3[47]\d{2}|6(?:011|5\d{2}))"
    r"|(?<!\d)\d{3}[\s-]\d{3}[\s-]\d{3}(?!\d)"
)
```

## 8. Rollback plan

`git revert` is sufficient and safe here: this is a pure code change to a text-transformation function with no persisted state, no migration, and no feature flag. Reverting restores the exact prior (narrower) redaction behavior with no data-level cleanup needed — there is no wallet delta, ride state, or Stripe charge involved. (Note: a revert does NOT retroactively re-expose any card/SIN value already redacted-then-persisted before the revert — those `ai_messages` rows already contain `[CARD]`/`[GOVID]` tokens, not the original value, since scrubbing happens before persistence.)

## 9. Verification performed

- [x] Automated tests — **not run via pytest** (pytest/loguru not installed in this session's environment; SessionStart hook noted "backend pip install skipped (no venv?)"). Instead, hand-verified every new test case and every pre-existing test case in `test_ai_pii.py` and `test_log_guard.py`'s observability-preservation parametrized list by importing `backend.ai.pii.scrub_pii` directly and asserting behavior in a standalone script, plus reconstructed `log_guard.py`'s `_SCREEN` regex standalone (avoiding the loguru/metrics import chain) to verify the screen+scrub pipeline end to end. All pass. **This is a real gap, not a substitute for CI** — the actual `pytest` run against the real `log_guard.py`/`test_log_guard.py` (with loguru, not a hand-reconstructed regex) has not happened in this session and should be treated as outstanding until CI runs it.
- [ ] Manual repro steps followed in staging — not performed; no staging environment available in this session.
- [x] Blast-radius grep performed — `grep -rn "scrub_pii" backend --include="*.py"`, all 5 call-site files listed in section 4.
- [x] Reviewed against relevant CLAUDE.md conventions — PIPEDA ban-list (the gap this fixes), "do not silently swallow errors" (n/a, no error handling changed), observability (explicitly tested against this file's own documented false-positive regression).
- [x] Feature-flagged if user-visible and non-trivial — not applicable; this is a strictly-narrower-false-positive-surface addition to an existing redaction function, not a new user-visible behavior, and CLAUDE.md's guidance is to prefer additive changes for exactly this kind of gap-closing fix (this is additive: new patterns added, no existing pattern removed or altered).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data cleanup needed)
- [x] Blast radius is stated, not assumed (5 call sites across AI chat, support, general logging, and Sentry — table in section 4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (section 5 — the only visible effect is stronger redaction of already-redacted-style text)

## What was NOT verified

- **Not run against the real pytest/loguru test suite** — verified by hand-reconstructing the relevant regex/function behavior in a standalone script instead, because this session's Python environment doesn't have `pytest`/`loguru` installed. The added test cases in `test_ai_pii.py`/`test_log_guard.py` are real, correctly-shaped pytest tests that should be run by CI on this PR — this is not a "trust me" substitute, but it is a real gap between what I verified and what a full CI run verifies (e.g. loguru's actual `serialize=True` JSON emission path in `test_log_guard.py` was not exercised, only the regex-and-`scrub_pii` combination it depends on).
- **Amex's real 4-6-5 dash/space-grouped display format is not matched** — only bare (unseparated) Amex numbers and the generic 4-4-4-3 chunking are covered. Documented as a known limitation in the code comment, not silently accepted.
- **Driver's license numbers are not covered by any pattern** — format varies by province with no fixed shape to regex against; relies entirely on the data-minimization mitigation (prompts never ask for one), which was verified by grep (`prompts.py` has zero mentions of "driver licen[cs]e"/"government id"/"SIN"/"passport") but not verified against every possible way a rider could volunteer one unprompted in free text — that residual risk is the same one this file already accepts for names.
- **International (non-Canadian) government ID formats** are out of scope — only the Canadian SIN's 3-3-3 grouped format is covered, consistent with this codebase's existing Canada-first postal-code/phone-number scope.
