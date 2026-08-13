# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude (agent) for vikas@ngitservices.com |
| Surface(s) | backend, database (migration) |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/portal-otp-bypass-testing-60bqjz` |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-12-corporate-otp-email-send-fail-open.md` — corporate portal login still failing after that fix; root-caused as a genuine, ongoing email-delivery failure |

## 1. Issue / gap identified

After yesterday's fix (2026-08-12, PR #3860) turned a raw 500 into a clean
502 for the corporate portal's `POST /api/portal/auth/send-email-otp`, the
user reported the login was still broken — same "Internal server error"
message, now correctly surfaced as a sanitized 502 rather than a crash.
Investigation confirmed this is a **real, currently-active email delivery
failure**, not a code bug: `email_send_log` shows every `corporate_email_otp`
send attempt since **2026-08-03** (the feature's original ship date) has
resulted in `provider="none"`, `status="failed"` — AWS SES has never
successfully delivered one of these, despite appearing fully configured
(`aws_ses_access_key_id`/`aws_ses_secret_access_key`/`aws_ses_region`/
`aws_ses_from_email` all set in `settings`). Resend, the intended guardrail
fallback, has no working configuration at all — `resend_api_key`/
`resend_from_email` columns don't even exist on the live `settings` table.

Neither `email_send_log` nor any log this session had access to captured
*why* SES rejects the send — only that it failed.

## 2. Root cause

Two independent things, both real:

1. **The actual SES/Resend failure reason is genuinely unknown** — this
   session has no Fly app-log or Sentry access (Sentry requires the user to
   authorize the connector), and `_try_ses`/`_try_resend` in
   `utils/email_provider.py` already capture and PIPEDA-redact the
   underlying exception before logging it to stdout, but never persisted
   that string anywhere durable. `email_send_log` recorded only
   `provider`/`status`/`message_id` — enough to know delivery is broken,
   nothing to diagnose why without live log access.
2. **Separately, and found while investigating**: the `settings` table is
   missing the `resend_api_key`/`resend_from_email` columns that migration
   `110_settings_resend_email.sql` adds — meaning that migration never
   actually applied to this production Supabase project, despite existing
   in-repo. Tracing why surfaced a bigger, pre-existing infrastructure gap:
   `backend/scripts/migrate.py`'s bookkeeping (`SELECT version FROM
   schema_migrations` / `INSERT INTO schema_migrations (version) ...`)
   requires a `version` column that the live `schema_migrations` table does
   not have — it has `filename`/`checksum`/`applied_at`/`applied_by`
   instead (migration `24_schema_migrations.sql`'s shape, not `00`'s). This
   means the migration runner cannot currently record progress against this
   database at all, and likely hasn't been able to since some point after
   migration `24`. **Filed separately as `ACTION_ITEMS.md` C22** — this is
   a substantial reconciliation task, not something to fix inline here.

## 3. Decision

No product decision needed for the code change (restores diagnosability,
doesn't change delivery behavior). The user chose, when asked: (a) authorize
Sentry for future direct root-cause access [pending on their end — connector
authorization can't be done from this session], and (b) have this session
add durable error-detail logging so the next attempt is self-diagnosing
without needing live log access.

## 4. Fix / remediation

- **New migration** `307_email_send_log_error_detail.sql`: adds a nullable
  `error_detail TEXT` column to `email_send_log`.
- **`utils/email_provider.py`**: `_try_ses`/`_try_resend` now return
  `(message_id, error_detail)` tuples instead of a bare `Optional[str]` —
  `error_detail` carries the same PIPEDA-redacted string that was already
  being logged to stdout on failure (SES: recipient address scrubbed from
  the exception text; Resend: HTTP status code only, never the response
  body, which can echo the recipient). `send_transactional_email` now
  passes whichever error fired (preferring SES, the primary) into
  `_log_send`, which writes it to the new `error_detail` column.
  `send_ops_alert_email` (the other caller of `_try_ses`/`_try_resend`)
  updated to unpack the new tuple shape; it only ever used the message_id
  half, so no behavior change there.
- **Migration applied directly to production** (`soavhtdhefowwvforzwb`) via
  Supabase MCP, ahead of this PR merging — `scripts/migrate.py` cannot
  currently be trusted to apply it (see root cause #2 / C22), and the user
  was actively blocked, so this was done with explicit confirmation rather
  than waiting on the normal deploy path. The DDL is `ADD COLUMN IF NOT
  EXISTS` — idempotent, safe to also ship via the file for the historical
  record and safe if `scripts/migrate.py` is ever fixed and re-run.

## 5. Risk & impact on existing functionality

- **Blast radius**: `_try_ses`/`_try_resend`'s signature change affects
  exactly 2 callers in the same file (`send_transactional_email`,
  `send_ops_alert_email`) — both updated. Grepped the full repo for any
  other import/call of `_try_ses`/`_try_resend`; both are private
  (underscore-prefixed) module functions with no other consumers, and no
  test mocks them directly except `test_capacity_watchdog.py`'s
  `test_alert_email_never_touches_the_database`, which was updated to match
  the new tuple return (this was caught by the full test suite, not
  assumed — see verification).
- **No PII/security regression**: `error_detail` stores exactly the same
  string that was already being logged to stdout/Sentry — this change only
  adds a second, durable destination for it. Recipient-address redaction
  logic in `_try_ses` is untouched; Resend's error path still never touches
  `response.text` (only the HTTP status code), consistent with the existing
  PIPEDA comment there.
- **Migration is purely additive**: nullable column, no default, no
  backfill, no lock risk, `IF NOT EXISTS` guarded. Rollback is a trivial
  `DROP COLUMN IF EXISTS`.
- **Does not fix the underlying SES delivery failure** — this change makes
  the *next* failure diagnosable from the database; it does not itself
  restore corporate portal login. That requires either Sentry/Fly log
  access (pending user authorization) or checking the AWS SES console
  directly (verified identity status, sandbox mode) — outside what this
  session can do.

## 6. User-experience effect

No user-facing change — corporate portal login remains broken until the
actual SES/Resend delivery issue is fixed (separate, pending). This change
is purely an operational/diagnostic improvement.

## 7. Before / after

```python
# Before
async def _try_ses(...) -> Optional[str]:
    ...
    except Exception as e:
        ...
        return None

# send_transactional_email
ses_id = await _try_ses(...)
```

```python
# After
async def _try_ses(...) -> Tuple[Optional[str], Optional[str]]:
    ...
    except Exception as e:
        ...
        return None, safe  # safe = the same PIPEDA-redacted string, now returned too

# send_transactional_email
ses_id, ses_err = await _try_ses(...)
...
error_detail = ses_err or resend_err
await _log_send(..., error_detail=error_detail)
```

## 8. Rollback plan

Code: `git revert` — pure refactor, both callers updated together, no
behavior change to what gets sent or when. Migration: `ALTER TABLE
email_send_log DROP COLUMN IF EXISTS error_detail;` — safe, no other code
reads the column outside this change.

## 9. Verification performed

- [x] Independently confirmed the actual production failure via direct
  Supabase queries (not assumed): `email_send_log` shows 15 consecutive
  `corporate_email_otp` failures from 2026-08-03 through 2026-08-13, all
  `provider="none"`; `settings` table confirmed SES credentials present;
  `resend_api_key`/`resend_from_email` columns confirmed absent.
- [x] `pytest backend/tests/test_email_provider.py backend/tests/test_capacity_watchdog.py backend/tests/test_company_email_login.py backend/tests/test_auth.py -q --no-cov` → 91 passed
- [x] New regression test `test_failed_send_persists_ses_error_detail` — asserts a configured-but-failing SES send (no Resend fallback) persists the actual exception text to `email_send_log.error_detail`, not just `provider=none`/`status=failed` with nothing else to go on.
- [x] `test_alert_email_never_touches_the_database` (capacity watchdog) — genuinely broke on the tuple-shape change (its own fake `_try_ses` returned a bare string), confirming the signature change has real reach; fixed the fixture, not the assertion.
- [x] **`spinr-migration-reviewer` review** — flagged a real numbering conflict (`305_` was already taken by an unrelated migration); renamed to the actual next-free slot, `307_email_send_log_error_detail.sql`. Also independently reasoned through (without live DB access) that `scripts/migrate.py`'s bookkeeping query is incompatible with a `filename`-shaped `schema_migrations` table — this session had already verified live via Supabase MCP that this is exactly the case; filed as `ACTION_ITEMS.md` C22.
- [x] Full backend suite: `11184 passed, 8 skipped, 1 xfailed` (`pytest tests/ -m "not slow" -q --no-cov`) — up from `11147` (37 more: 6 previously-failing-but-unrelated dispatch tests are no longer failing in this run — not investigated further since they were already confirmed pre-existing/flaky and unrelated to this diff in the prior session; net new tests from this change: +1)
- [x] `ruff check` / `ruff format --check` on both changed backend files → clean
- [x] Migration applied to production and confirmed via a follow-up `information_schema.columns` query that `error_detail` exists on `public.email_send_log`

## What was NOT verified

- **The actual SES/Resend rejection reason** — still unknown. The next real
  send attempt (once this code deploys) will populate `error_detail` and
  make it queryable; this session cannot force a send against the real
  provider to pre-emptively check.
- **`ACTION_ITEMS.md` C22's full scope** — only `110_settings_resend_email.sql`
  was confirmed missing from production; no migrations between `24_` and
  `110_`, or between `110_` and `304_`, were individually audited. Flagged
  explicitly as unaudited in C22 rather than assumed fine.
- Whether Sentry, once authorized, will show anything beyond what
  `error_detail` will now capture — not checked, session has no Sentry
  access yet.
