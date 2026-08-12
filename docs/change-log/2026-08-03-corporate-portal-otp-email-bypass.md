# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-03 |
| Author | Claude (agent) for vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/portal-otp-bypass-testing-60bqjz` |
| Related issue or gap ID | Corporate portal login unusable in test env — email OTP send fails with 500 (no email provider configured) |

## 1. Issue / gap identified

Corporate portal login (`POST /api/send-email-otp`) fails with an internal server error in the current test environment because no email provider (AWS SES or Resend) is configured, so `send_transactional_email` returns `False` and the endpoint raises a 502 — the frontend never advances past the email-entry screen to the OTP-entry screen.

## 2. Root cause

`send_company_email_otp` (`backend/routes/auth.py`) always calls `send_transactional_email` regardless of whether an email provider is configured, and treats a delivery failure as a hard error. The existing phone-based `/send-otp` endpoint already has a documented, ENV-gated dev bypass (`otp_code = "1234"` when Twilio is unconfigured and `ENV != production`) for exactly this situation; the email-OTP endpoint added later never got the equivalent bypass.

## 3. Fix / remediation

Added the same pattern to `send_company_email_otp`:
- If SES or Resend is configured (via `app_settings`) → unchanged behavior: random OTP, sent by email.
- If neither is configured **and** `ENV != production` → issue the fixed code `"1234"`, store its hash, skip the email send, and still return `{"success": true}` so the portal can advance to the OTP-entry screen.
- If neither is configured **and** `ENV == production` → unchanged fail-loud behavior (503), no bypass — refuses to silently let anyone log in as any company email.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated** to `POST /api/send-email-otp` (`send_company_email_otp`). Grepped for all callers/importers of this function and the route path — only `backend/tests/test_company_email_login.py`, `backend/tests/test_auth_remaining_endpoints.py`, `backend/core/middleware.py` (rate-limit config, unaffected), and the `admin-dashboard` client (`companyApi.ts`) reference it; none of those touch the OTP-verification logic, which is unchanged.
- `verify_company_email_otp` is untouched — it already hashes/compares whatever code was stored, so it works identically whether the stored hash came from `generate_otp()` or the fixed `"1234"`.
- The production path is bit-for-bit unchanged except that the "unconfigured" check now also covers SES (previously only implicitly failed via `send_transactional_email` returning `False`); no regression to a working configured-provider flow.
- No new callers, no shared state/table beyond `corporate_email_otp_records`, which this endpoint already owned exclusively.

## 5. User-experience effect

- Corporate-admin facing (portal login only). In `ENV=production` there is **no visible change** — a misconfigured provider still fails loudly as before. In non-production environments, a tester requesting a code now reaches the OTP screen and can enter `1234` instead of hitting a 502.
- Not visible mid-session to anyone already authenticated — this only affects the pre-auth login flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | `send_company_email_otp`: check `app_settings` for SES/Resend config before generating the OTP; when unconfigured, bypass with `"1234"` in non-production, refuse in production | Unblock corporate portal login testing without a working email provider, matching the existing phone-OTP dev-bypass convention |
| `backend/tests/test_company_email_login.py` | Updated the existing send-success tests to patch `get_app_settings` with a configured provider; added two new tests for the non-production bypass and the production refusal | Cover the new branch; existing tests would otherwise silently hit the bypass path instead of the "configured provider" path they're meant to exercise |
| `docs/change-log/2026-08-03-corporate-portal-otp-email-bypass.md` | This log | Required by `CLAUDE.md` for any auth-surface change during live app testing |

## 7. Before / after

```python
# Before
otp_code = generate_otp()
...
sent = await send_transactional_email(...)
if not sent:
    ...
    raise HTTPException(status_code=502, detail="Could not send verification code")
```

```python
# After
is_production = settings.ENV.lower() == "production"
if email_provider_configured:
    otp_code = generate_otp()
elif not is_production:
    otp_code = "1234"
    deliver_via_email = False
else:
    raise SpinrException(..., status_code=503)  # unchanged fail-loud in prod
...
if deliver_via_email:
    sent = await send_transactional_email(...)
    if not sent:
        raise HTTPException(status_code=502, detail="Could not send verification code")
```

## 8. Rollback plan

Pure code change, no data migration and no live data mutated by this diff. Revert the commit on `backend/routes/auth.py` to restore the always-attempt-send behavior; no feature flag needed since production behavior is unchanged by design (the bypass is dead code whenever `ENV == production`, which fails fast the same way it did before this change).

## 9. Verification performed

- [x] Automated tests run: `backend/tests/test_company_email_login.py` (10 passed) via a fresh venv (`/tmp/spinr-venv`, `pip install -r requirements.txt`, `python -m pytest tests/test_company_email_login.py -q`)
- [x] `ruff check` on both changed files — no findings
- [ ] Manual repro in staging — **not performed** (no staging access in this session)
- [x] Blast-radius grep performed: searched for `send_company_email_otp` and `send-email-otp` across the repo (see §4)
- [x] Reviewed against relevant `CLAUDE.md` convention: mirrors the documented "OTP security" dev-bypass pattern (`"1234"` only when `ENV != production`) already in place for the phone `/send-otp` endpoint
- [ ] Feature-flagged — not applicable; behavior is gated by `ENV` (same mechanism the existing phone bypass uses), not a separate flag

## What was NOT verified

- Not tested against a real Supabase instance or a real SES/Resend account — only against mocked `get_app_settings`/`send_transactional_email` in unit tests.
- Not exercised end-to-end through the `admin-dashboard` frontend (no running dev server / browser check in this session) — verified only that the backend response shape (`{"success": true}`) matches what `companyApi.ts` already expects to advance the login step.
- No visual/UI regression tooling exists for `admin-dashboard`; this change has no UI diff, so N/A.
