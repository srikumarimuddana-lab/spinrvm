# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude (agent) for vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/portal-otp-bypass-testing-60bqjz` |
| Related issue or gap ID | Corporate portal registration unusable — work-email OTP send fails with 500 Internal Server Error |

## 1. Issue / gap identified

`POST /api/portal/auth/send-email-otp` (the first step of corporate portal
registration/login — the OTP session it establishes is required before
`POST /api/portal/companies/signup` can even be called) intermittently fails
with a raw 500 Internal Server Error instead of a clean, already-handled
error response, blocking the OTP screen from ever appearing.

## 2. Root cause

`send_transactional_email` (`backend/utils/email_provider.py`) is documented
and relied upon across all of its call sites to **return a bool and never
raise** — every one of its 10 real call sites (including
`send_company_email_otp`) calls it unwrapped on that assumption, matching the
module's own decision-tree docstring ("SES fails → log and fall through …
Resend fails → log and fall through … neither configured → log and return
False").

That contract was violated by one line: `_load_settings()`'s internal
`get_app_settings()` call (an `app_settings` DB read) was **not** wrapped in
try/except, unlike every other DB-touching branch in the same function
(`_is_suppressed` already fails open with a logged error; `_log_send` already
swallows write failures). A transient `app_settings` read failure — e.g. a
DB hiccup on a cache-expired read, which several of the other 9 call sites
hit as their *only* settings read — propagated as a raw, unhandled exception
straight through `send_company_email_otp`, which does not wrap the send call
(consistent with the function's documented contract), producing the reported
500.

Confirmed independently with a minimal repro before the fix:
```
RAISED: RuntimeError DB unavailable
```
from a direct call to `send_transactional_email` with `get_app_settings`
patched to raise.

## 3. Fix / remediation

Wrapped the `_load_settings()` call inside `send_transactional_email` in a
try/except that logs the failure at ERROR level (with `exc_info=True`, per
CLAUDE.md's "surface loudly" rule — this is not a silent swallow) and falls
back to `settings = {}`, which the existing "neither provider configured"
branch already handles correctly (returns `False`, records a `status=failed`
row in `email_send_log`). This mirrors the `_is_suppressed` fail-open pattern
already in the same file almost verbatim.

`send_company_email_otp`'s pre-existing `if not sent: raise
HTTPException(502, ...)` (and its OTP-row cleanup) now runs instead of the
raw exception reaching the client — a clean, already-tested error path.

## 4. Risk & impact on existing functionality

- **Blast radius**: `send_transactional_email` is shared by 10 real call
  sites — `routes/auth.py` (corporate OTP), `routes/corporate_signup.py`
  (KYB-queue ops notify), `routes/corporate_company.py` (member invites),
  `routes/corporate_accounts.py` (KYB decisions), `routes/admin/messaging.py`
  (admin broadcast), `routes/drivers/subscriptions.py`,
  `utils/email_notifications.py`, `utils/email_receipt.py` (ride receipts),
  `utils/marketing_email.py`, `features.py`. Grepped and reviewed all 10 —
  none has a try/except that depends on an exception *from this call*, and
  none behaves differently now that it correctly returns bool. This fix
  makes email sending strictly more resilient everywhere it's used, not just
  in the corporate-OTP path that surfaced it.
- **No OTP/auth security guarantee weakened**: in `send_company_email_otp`
  the OTP row is persisted (hashed) *before* the email-send attempt,
  independent of delivery outcome. When the send now correctly returns
  `False` instead of raising, the existing cleanup path deletes that OTP row
  and returns 502 — a fail-closed outcome, not a bypass. The separate
  non-production dev-bypass gate (`"1234"` when no provider is configured)
  is untouched and independently guarded by its own `get_app_settings()`
  call + `ENV != production` check.
- **Intentional asymmetry, called out explicitly** (per adversarial review):
  this DB-read failure fails open (returns `False`, logged) while the OTP
  row's own DB writes in the same route fail closed (raise `SpinrException`,
  503). This is deliberate, not an oversight — email delivery is inherently
  best-effort and already has a working non-email fallback in `_is_suppressed`,
  whereas OTP persistence and user-lookup correctness cannot degrade without
  producing a wrong result.

## 5. User-experience effect

Corporate-admin facing (portal registration/login only). Before: a DB hiccup
during OTP send surfaced as an opaque "Internal Server Error" with no path
forward. After: the same condition now returns the pre-existing, portal-side
"Could not send verification code" message (still an error — the code
genuinely wasn't delivered — but a coherent one instead of a crash), and
critically, transient hiccups elsewhere in the stack that used to escalate a
recoverable email-send failure into a full request failure now degrade
gracefully instead. Not visible mid-session to anyone already authenticated.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/email_provider.py` | Wrapped `_load_settings()` in `send_transactional_email` in try/except, falling back to `settings = {}` on failure | Restore the function's documented "returns bool, never raises" contract; fixes the corporate-portal OTP 500 and hardens all 9 other callers |
| `backend/tests/test_email_provider.py` | Added `test_settings_load_error_fails_open_not_raises`, mirroring the existing `test_suppression_lookup_error_fails_open`/`test_failed_send_logged_as_failed` pattern | Regression coverage; independently verified to fail on pre-fix code with the exact predicted `RuntimeError` |
| `docs/change-log/2026-08-12-corporate-otp-email-send-fail-open.md` | This log | Required by `CLAUDE.md` for any auth-surface change during live app testing |

## 7. Before / after

```python
# Before
settings = await _load_settings()

# 1. Primary: AWS SES.
ses_id = await _try_ses(settings, ...)
```

```python
# After
try:
    settings = await _load_settings()
except Exception:
    logger.error("[EMAIL] app_settings load failed log_id=%s — treating as unconfigured", log_id, exc_info=True)
    settings = {}

# 1. Primary: AWS SES.
ses_id = await _try_ses(settings, ...)
```

## 8. Rollback plan

`git revert` — pure code change, no schema/data mutation, no migration. No
feature flag: the fixed behavior is strictly a superset of the old one for
every success path (identical), and only changes the previously-undefined
"settings DB read fails" path from a crash to the function's own documented
contract.

## 9. Verification performed

- [x] Reproduced the bug directly against the pre-fix code (`RuntimeError`
  propagating from a bare `send_transactional_email` call with
  `get_app_settings` patched to raise)
- [x] Independently verified the new regression test fails on pre-fix code
  with the exact predicted exception, then passes post-fix
- [x] `pytest backend/tests/test_email_provider.py backend/tests/test_company_email_login.py backend/tests/test_auth.py backend/tests/test_corporate_signup.py -q --no-cov` → 69 passed
- [x] **`spinr-security-auditor` adversarial review** — verdict SAFE TO MERGE, no blockers. Confirmed: (1) fail-open here is justified and not an unqualified "don't swallow errors" violation — it still logs at ERROR with `exc_info=True` and flows into an already-tested fail-closed 502 path, not a fake success; (2) no OTP/auth security guarantee weakened — OTP row is written before send and cleaned up on failure; (3) no sibling unguarded `_load_settings()`/`get_app_settings()` call exists elsewhere in the file; (4) all 10 real callers of `send_transactional_email` checked — none has dead/broken exception handling as a result; (5) new test coverage is adequate and correctly modeled on its sibling test. Two WARNINGS noted (both addressed in this log): the fail-open/fail-closed asymmetry vs. the route's other DB reads should be documented explicitly (done, see §4), and the comment's "cache miss" framing is over-specific for this exact call path though the underlying bug/fix is correct regardless (several of the other 9 callers genuinely hit a cold cache on their only settings read).
- [x] Full backend suite: `11147 passed, 8 skipped, 1 xfailed` (6 pre-existing, unrelated failures in `test_dispatch_cascade.py`/`test_dispatch_match_attempt_branches.py`/`test_rides_matching_coverage.py`, independently confirmed to fail identically on unmodified `origin/main` — not touched by this diff)
- [x] `ruff check` / `ruff format --check` on both changed files → clean
- [x] Blast-radius grep on all 10 real callers of `send_transactional_email` — reviewed each, none depends on it raising

## What was NOT verified

- Not reproduced against a live production/staging Supabase outage — verified
  at the unit level with a mocked `get_app_settings` failure, which is the
  exact failure mode this fix targets.
- Whether the specific "test environment" the original report came from had
  a genuine, ongoing Supabase connectivity issue at the time, vs. a
  transient blip — not diagnosable from this sandbox (no access to that
  environment's logs/Sentry). The fix is correct and defensive regardless of
  the precise trigger, since it restores a documented contract that was
  silently broken for every caller, not just this one.
- No visual regression tooling exists for `admin-dashboard`; this change has
  no UI diff, so N/A.
