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

`backend/routes/rides/safety.py`'s `trigger_emergency` (in-ride SOS) was at 92% (combined with `test_coverage_rides.py`'s coverage of the separate `safety_checkin_response` endpoint in the same file). The uncovered lines were all in the emergency-contact SMS notification block: a raised exception from one contact's SMS send, an SMS send that returns `success: False` without raising, and the outer try/except around the whole contact-notification block.

## 2. Root cause

Not applicable — new test coverage for existing, already-shipped behavior.

## 3. Fix / remediation

Extended `test_p2_sos.py`'s `_trigger` helper to accept injectable `send_sms_side_effect` and `get_app_settings_side_effect` overrides, and added 3 new tests:
- `test_sms_exception_for_one_contact_does_not_block_the_others` — one contact's SMS send raises; the other contact still gets notified; the error is logged with the exception **type name only** (never the exception text, which could embed the phone number — PIPEDA).
- `test_sms_failure_result_is_logged_and_not_counted` — `send_sms` returns `{"success": False, "error": ...}` (doesn't raise) — logged via the PII-free `error` string `send_sms` guarantees, contact not counted as notified.
- `test_contact_notification_outer_failure_returns_warning` — a failure anywhere in the block (e.g. `get_app_settings` raising) doesn't fail the whole request (the incident is already persisted); the response carries `notification_warning` instead.

**Test infra note:** `routes/rides/safety.py` logs via `loguru`'s `logger` (imported through `_deps`), which does not propagate to pytest's `caplog` (that only hooks stdlib `logging`, which is what `utils/insurance_periods.py` uses instead — the two files log through different systems). Asserting on log output here required patching `backend.routes.rides.safety.logger` directly with a `MagicMock` and inspecting `call_args_list`, not `caplog`.

## 4. Risk & impact on existing functionality

- **What else calls `trigger_emergency`?** Grepped: only the rider-app/driver-app SOS button UI calls `POST /{ride_id}/emergency`; no other backend code path invokes the function directly.
- **Could this regress a working flow?** No — test-only, zero production code touched.
- **Blast radius:** isolated — one test file (`_trigger` helper extended with new optional params, backward-compatible with all 10 existing call sites since both new params default to `None`).

## 5. User-experience effect

None — backend test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_p2_sos.py` | `_trigger` helper gains 2 optional injectable-failure params; 3 new tests | Close the SMS-failure and outer-exception branches in the SOS emergency-contact notification block |

## 7. Before / after

Not applicable — purely additive test file, no existing test or production code changed (the `_trigger` helper's new params are additive-only with `None` defaults; all 10 pre-existing call sites are unaffected).

## 8. Rollback plan

`git-revert-safe` — test-only, no data or schema dependency.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_p2_sos.py` — 13 passed, 0 failed (was 10 before this change).
- [x] Coverage measured directly: `routes/rides/safety.py` — combined with `test_coverage_rides.py` (which already covers the file's other endpoint, `safety_checkin_response`), now **100%** (was 92%).
- [x] `ruff check` — clean.
- [x] Blast-radius grep performed.

## 10. What was NOT verified

- Not run against real Twilio/Supabase — mocked per this file's existing convention.

## 11. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed (§4).
- [x] No silent behavior change — test-only, zero production code touched.
