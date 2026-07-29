# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude (ACTION_ITEMS.md A1b, Track 1 item 2 — safety/insurance coverage push) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md A1b |

## 1. Issue / gap identified

`backend/utils/safety_checkin_loop.py` (the 30s loop that sends a safety check-in push after 20 minutes in-progress and auto-escalates to the safety team after 90s with no response) had 5 real untested branches: missing `ride.id`, an unparseable `ride_started_at`, an unparseable sent-timestamp, `_escalate` raising inside `_tick` (must not propagate), and `_log_audit` raising inside `_escalate` (must not undo the already-successful escalation).

## 2. Root cause

Not applicable for the coverage additions — new tests for existing, already-shipped behavior.

**However, writing these tests surfaced a separate, real production bug — not fixed in this change, flagged for a follow-up decision (see §12):** the module's `except ImportError` fallback branch — confirmed to be the branch actually active in production, since `core/lifespan.py` imports this module in a way that triggers it (`from utils.safety_checkin_loop import ...`, not a `backend.`-prefixed import) — never imports `notify_safety_team`, only the `try` branch does. `_escalate`'s call to `notify_safety_team(incident)` therefore raises `NameError` every time, silently caught by `_escalate`'s own surrounding `except Exception` and logged as a generic "notify_safety_team failed" — meaning the safety team is never actually notified (WS broadcast + email + PagerDuty-triggering log) for an automated no-response escalation; only the DB row and audit log get written.

## 3. Fix / remediation

Added 5 new tests to `backend/tests/test_safety_checkin_loop.py`:
- `test_tick_skips_ride_with_no_id` — ride row missing `id` → skipped before any Redis/push work.
- `test_tick_skips_ride_with_unparseable_started_at` — garbage `ride_started_at` → caught, ride skipped.
- `test_tick_skips_ride_with_unparseable_sent_timestamp` — garbage sent-key value → caught, tick continues.
- `test_tick_escalation_failure_is_logged_and_does_not_propagate` — `_escalate` raising doesn't crash `_tick`; logged with the ride id for ops correlation.
- `test_escalate_audit_log_failure_does_not_prevent_escalation` — `_log_audit` raising doesn't undo the already-set `escalated_key`.

**Not fixed here (see §12):** the `notify_safety_team` import gap in the except-branch fallback. Fixing the underlying bug is a one-line production-code change and a behavior change, not a test-only coverage addition — flagged to the user rather than fixed unilaterally inside a coverage-only PR, per CLAUDE.md's "escalate, don't silently ship" rule and the standing "STOP and ask before softening/masking an error" instruction. The last new test (`test_escalate_audit_log_failure_does_not_prevent_escalation`) deliberately does **not** patch `notify_safety_team` — it lets the real (currently no-op-via-NameError) call happen and asserts only on the specific behavior under test (audit-log failure doesn't undo escalation), so it doesn't accidentally mask or paper over the discovered bug.

## 4. Risk & impact on existing functionality

- **What else calls `_tick`/`_escalate`/`safety_checkin_loop`?** Grepped: only `core/lifespan.py`'s startup-loop spawn (`_spawn("safety_checkin (30s)", safety_checkin_loop)`) — no other caller.
- **Could this regress a working flow?** No — test-only, zero production code touched in this change.
- **Blast radius:** isolated — one test file, no application code modified.

## 5. User-experience effect

None from this change (test-only). The **discovered** `notify_safety_team` gap (not fixed here) does have a real safety-team-facing effect already live in production — flagged, awaiting a fix decision.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_safety_checkin_loop.py` | 5 new tests | Close missing-ride-id, unparseable-timestamp, and escalation/audit-failure-swallow branches |

## 7. Before / after

Not applicable — purely additive test file, no existing test or production code changed.

## 8. Rollback plan

`git-revert-safe` — test-only, no data or schema dependency.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_safety_checkin_loop.py` — 17 passed, 0 failed (was 11 before this change).
- [x] Coverage measured directly: `utils/safety_checkin_loop.py` 85%→87% measured against this file alone (was 84% against the keyword-filtered slice used for initial scoping); the 5 targeted lines (90, 98-99, 141-142, 150-153, 212-213) are all closed. Remaining gaps: dual-import fallback (32-33, 37-39, 47-49 — structurally untestable, same convention noted elsewhere) and the outer `safety_checkin_loop()` wrapper (67-73 — covered when running the full suite, per the earlier full-suite baseline measurement of this same file).
- [x] `ruff check` — clean.
- [x] Blast-radius grep performed.
- [x] Confirmed the `notify_safety_team` import gap via a direct reproduction: `patch("utils.safety_checkin_loop.notify_safety_team", ...)` raises `AttributeError: module ... does not have the attribute 'notify_safety_team'` when the module is imported the same way `core/lifespan.py` imports it.

## 10. What was NOT verified

- Not run against a real Supabase DB or Redis — mocked per this file's existing convention.
- The `notify_safety_team` bug's actual production Sentry/log history was not checked (no access to live logs from this environment) — the finding is based on static/import-path analysis and a direct reproduction of the import failure, not a confirmed production incident record.

## 11. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed (§4).
- [x] No silent behavior change from this change itself (test-only). The pre-existing `notify_safety_team` bug is explicitly surfaced, not silently worked around (§3, §12).

## 12. Follow-up needed (not part of this change)

**Real bug, not fixed here — awaiting explicit direction on which PR/timing to fix it in:** add `notify_safety_team` to `utils/safety_checkin_loop.py`'s `except ImportError` fallback import list (mirrors the existing `try` branch exactly). One-line fix, but it's a genuine safety-domain behavior change (the safety team starts actually getting notified for auto-escalations, where today they silently don't), so it should ship as its own reviewable, revertable change — not folded into this test-only coverage PR.
