# Change Impact & Risk Log — `/send-otp` maps a permanent Twilio input error to a retryable 503

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (daily `/sentry-triage --severity-only` scan) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (this branch, `fix/otp-twilio-permanent-error-classification`) |
| Related issue or gap ID | Sentry CRIMSON-SMOKE-7445 send-otp/Twilio-21211 cluster (short-ids ZN/ZP/ZM/ZK/YD/133) |

## 1. Issue / gap identified

54 events across a cluster of Sentry issues fired from `POST /api/v1/auth/send-otp` in a single
24h window, all Twilio error code 21211 ("invalid 'To' phone number") or the sibling 21614
("not an SMS-capable number"). Every one was surfaced to the rider as the same generic
"Failed to send verification code" / 503 Service Unavailable — telling them to retry later for a
condition that can never succeed until they fix the number, and flooding Sentry as backend-service
noise for what is actually bad user input.

## 2. Root cause

`backend/validators.py`'s `validate_phone()` only checks NANP shape (`+1` + valid area-code digit
+ 9 digits) via regex — it cannot detect an unassigned or non-SMS-capable number; only Twilio can.
`backend/routes/auth.py`'s `send_otp` handler mapped **every** `send_otp_sms` failure, transient or
permanent, to the identical `SpinrException(status_code=503)`. `backend/sms_service.py`'s
`_send_sms_on` already captured Twilio's numeric error code internally (used only to build the
human-readable `error` log string) but never returned it as a separate, machine-readable field, so
`routes/auth.py` had no way to distinguish "Twilio is down" from "this number will never work."

## 3. Fix / remediation

- `sms_service.py`: `_send_sms_on`'s failure returns (`TwilioRestException` branch and the
  `ExecutorSaturated` branch, for shape consistency) now include `"error_code"` — Twilio's own
  numeric code (an `int`) when present, `None` otherwise — alongside the existing human-log-only
  `"error"` string. No existing field changed; this is purely an additive key.
- `routes/auth.py`: `send_otp` now checks `sms_result.get("error_code")` against a new
  `_TWILIO_PERMANENT_INPUT_ERROR_CODES = frozenset({21211, 21614})`. A match raises a plain
  `HTTPException(400, detail="This phone number can't receive SMS. Please check it and try
  again.")` — the same plain-`HTTPException` pattern `validate_phone()` already uses for its own
  format-validation 400s, so this doesn't introduce a new i18n key (`utils/error_keys.py`'s
  `ErrorKeys` requires a coordinated `en.json` entry in both rider-app and driver-app for anything
  routed through `SpinrException`/`message_key`, which this avoids). Anything else — a genuinely
  transient Twilio failure, `ExecutorSaturated`, or a missing `error_code` — falls through to the
  existing, unchanged 503 `SpinrException` path.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two failure-path branches.** No change to the happy path (successful
  SMS send), no change to `validate_phone()`'s own upfront format check, no change to
  `_enforce_otp_send_cap`, OTP storage, or `/verify-otp`.
- Grepped every reader of `send_sms`/`send_otp_sms`/`send_sos_sms`'s return dict: `routes/auth.py`
  (this fix), `routes/rides/safety.py` (SOS fan-out — only logs `result.get("error")`, never
  branches on error_code, unaffected), `services/guest_notification_service.py` (same, log-only),
  `utils/marketing_sms.py` (only checks `.get("success")`). None assume a fixed/closed key set on
  the returned dict, so the new `error_code` key is additive-safe everywhere.
- **One real regression found and fixed during verification**: `tests/test_sms.py`'s
  `test_saturated_pool_fails_fast_and_default_executor_stays_free` asserted the `ExecutorSaturated`
  result dict via exact equality (`assert result == {...}` with no `error_code` key) — updated to
  include `"error_code": None`, matching the new shape. Confirmed via full-suite grep that this was
  the only exact-dict-equality assertion on a `send_sms`-family return value in the repo.
- `21211`/`21614` are Twilio's own stable, documented numeric error codes (not derived from
  parsing free text), so this is not brittle string-matching.

## 5. User-experience effect

Rider-facing: a rider who enters a phone number Twilio cannot deliver to (wrong/unassigned number,
or a landline) now gets an immediate "check the number" message (400) instead of being told to
"try again later" (503) for a condition that will never resolve on retry. Not visible mid-session —
this only affects the pre-authentication OTP-request screen. Driver-app shares the same
`/send-otp` endpoint and gets the identical improvement.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/sms_service.py` | Added `"error_code"` (int or `None`) to both failure-path return dicts in `_send_sms_on` | Surface Twilio's numeric code as a machine-readable field, not just embedded in a log-only string |
| `backend/routes/auth.py` | New `_TWILIO_PERMANENT_INPUT_ERROR_CODES` frozenset; `send_otp` branches to a 400 for those codes, unchanged 503 otherwise | Stop telling riders to retry a permanently-undeliverable number |
| `backend/tests/test_auth_send_otp.py` | New `TestSendOtpTwilioErrorClassification` (4 cases: 21211→400, 21614→400, other code→503 unchanged, missing error_code→503 unchanged) | Pin the fixed classification and prove the default path is untouched |
| `backend/tests/test_sms.py` | Updated one exact-dict-equality assertion to include the new `error_code: None` key | Fix a real regression the new field introduced; confirmed no other test asserts this shape |

## 7. Before / after

```py
# Before (routes/auth.py)
if not sms_result.get("success"):
    logger.error(f"Failed to send OTP SMS: {sms_result.get('error')}")
    raise SpinrException(
        message="Failed to send verification code",
        error_code=ErrorCode.SERVICE_UNAVAILABLE,
        status_code=503,
        message_key=ErrorKeys.SYSTEM_SERVICE_UNAVAILABLE,
    )

# After
if not sms_result.get("success"):
    logger.error(f"Failed to send OTP SMS: {sms_result.get('error')}")
    if sms_result.get("error_code") in _TWILIO_PERMANENT_INPUT_ERROR_CODES:
        raise HTTPException(
            status_code=400,
            detail="This phone number can't receive SMS. Please check it and try again.",
        )
    raise SpinrException(
        message="Failed to send verification code",
        error_code=ErrorCode.SERVICE_UNAVAILABLE,
        status_code=503,
        message_key=ErrorKeys.SYSTEM_SERVICE_UNAVAILABLE,
    )
```

```py
# Before (sms_service.py, TwilioRestException branch)
return {"success": False, "provider": "twilio", "error": safe_error}

# After
return {"success": False, "provider": "twilio", "error": safe_error, "error_code": _code}
```

## 8. Rollback plan

`git revert` is a complete rollback — no data migration, no config/flag, no schema change. Reverting
returns `/send-otp` to mapping every SMS failure to 503, the prior (already-suboptimal, not
newly-broken) behavior.

## 9. Verification performed

- [x] Automated tests run — `python -m pytest tests/test_auth_send_otp.py -q --no-cov` (20 passed);
  full cross-consumer sweep (`test_sos_contact_notice.py`, `test_sos_rideless.py`, `test_sms.py`,
  `test_sos_paging.py`, `test_p2_sos.py`, `test_guest_sms.py`, `test_marketing_sms.py`,
  `test_coverage_rides.py`, `test_guest_notification_service_coverage.py`,
  `test_admin_messaging_coverage.py`) — 370 passed
- [x] Regression proof — the 2 new 400-path tests confirmed to FAIL (raised the old 503
  `SpinrException` instead) without the fix (`git stash` on `routes/auth.py` + `sms_service.py`
  together) and PASS with it; the 2 unchanged-503-path tests correctly stayed passing throughout,
  proving the fix doesn't widen beyond the two named codes
- [x] `ruff check` on all 4 modified files — clean
- [ ] Manual repro against a real Twilio account with an actual invalid number — not performed; no
  Twilio credentials/staging access in this sandboxed session; verified via the existing mocked
  `send_otp_sms`/Twilio-client test harness only
- [x] Blast-radius grep performed — confirmed every consumer of the `sms_service.py` return-dict
  shape across the repo (see §4); found and fixed the one exact-dict-equality assertion the new
  field broke
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — PIPEDA phone-number handling
  (`sms_service.py`'s existing no-raw-number-in-logs contract is untouched; `error_code` is a bare
  Twilio integer, never PII) and the "do not silently swallow errors" rule (this fix doesn't
  soften an error — it correctly reclassifies a permanent one, still surfaced loudly via
  `logger.error` either way); dispatched `spinr-security-auditor` before commit

## 9a. Adversarial review

`spinr-security-auditor` reviewed the diff before commit. Verdict: **SAFE TO MERGE**, no blockers
or warnings. Independently re-verified rather than trusting the PR's own claims: (1) `error_code`
is a bare, publicly-documented Twilio integer, no PII, and the new 400's `detail` is a static
string that never echoes the phone number — confirmed the existing no-raw-`str(e)` contract in
`_send_sms_on` is unchanged; (2) a plain `HTTPException` (vs. `SpinrException`) is not a new
pattern in this handler — `send_otp` already raises plain `HTTPException` twice (from
`validate_phone()` and `_enforce_otp_send_cap()`), and `utils/error_handling.py`'s global
`http_exception_handler` runs it through the same redaction/response-shaping pipeline; (3) no
rate-limiting/abuse change — the per-phone send cap and per-IP `6/minute` limiter both apply
before `send_otp_sms` is ever called, so 400-vs-503 doesn't affect either; (4) re-grepped every
consumer of the `send_sms`-family return dict independently and confirmed all use `.get(...)`,
never a closed-key assumption, and that the one exact-dict-equality test this diff had to fix was
the *only* one in the repo; (5) confirmed the test suite's negative cases (unclassified code,
missing `error_code`) genuinely prove the unchanged-503 default still fires, not just that the
code runs. One non-blocking note: this diff touches `routes/auth.py`, so whoever opens the PR
should tick CLAUDE.md's "Auth / RLS" compliance-flag box in the PR template.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — the happy path and every other
  failure mode (transient Twilio error, executor saturation, DB store failure) are byte-for-byte
  unchanged; only the two named permanent-input codes get a different (more correct) status code

## What was NOT verified

- Not tested against a real Twilio sandbox/production account — verified via the existing mocked
  Twilio-client test harness only, per this repo's existing test conventions for `sms_service.py`.
- Whether 21211/21614 are the *only* two Twilio codes worth this treatment was not exhaustively
  researched against Twilio's full error-code catalog — the investigator's report named these two
  specifically from the observed cluster; other permanent-input codes (if any surface later) would
  need their own addition to the frozenset, not assumed covered by this fix.
- Rider-app/driver-app client-side handling of the new 400 response (vs. the previous 503) was not
  verified in either app's UI — this PR is backend-only; whichever client copy currently renders a
  generic "service unavailable" message for a 503 may want its own follow-up to render the new
  400's `detail` text distinctly, but the API contract change itself (503→400 for two specific
  cases) does not break either app's existing error handling, since both already handle arbitrary
  4xx/5xx from this endpoint.
