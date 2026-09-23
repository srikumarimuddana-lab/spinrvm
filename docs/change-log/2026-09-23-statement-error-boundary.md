# Change Impact & Risk: driver statement period errors

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | Backend / driver statements |
| Domain (Sentry tag) | drivers |
| PR / commit link | PR #5725 |
| Related issue or gap ID | PR 5725 implementation plan, Task 11 |

## 1. Issue / gap identified

The self-serve statement email endpoint returned a raw `ValueError` message as its 422 detail when weekly/monthly period validation failed.

## 2. Root cause

The route interpolated the exception raised by `build_statement` into the client-facing HTTP response.

## 3. Fix / remediation

All statement-anchor `ValueError`s now return fixed actionable copy and log only the period type and exception class. Invalid requests still return 422 and never queue the email task.

## 4. Risk & impact on existing functionality

- Blast radius: isolated to `POST /api/v1/drivers/statements/email` in `routes/drivers/tax_exports.py`.
- `email_driver_statement` is the sole route-level caller of `build_statement` in this module; grep found no other consumers changed by this patch. The scheduled driver statement job and admin statement routes are separate callers and unchanged.
- Valid periods still build the same statement and queue the same background email. Invalid weekly or monthly anchors keep status 422 but receive stable copy.
- No background loops, ride state, or money deltas changed.

## 5. User-experience effect

Drivers who submit a malformed weekly/monthly anchor see stable guidance naming the accepted anchor dates. This is visible only on an invalid request; no in-progress session behavior changes, and no email copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/tax_exports.py` | Replace raw exception response with fixed 422 copy and sanitized warning | Prevent arbitrary validator details from reaching drivers |
| `backend/tests/test_driver_statement_email.py` | Cover weekly/monthly anchors, redaction, status, and no queued email | Pin the error boundary and prevent accidental delivery |
| `docs/change-log/2026-09-23-statement-error-boundary.md` | Record impact and verification | Required runtime change record |

## 7. Before / after

```python
# Before
raise HTTPException(status_code=422, detail=str(e)) from e
```

```python
# After
logger.warning("Rejected driver statement period (type=%s, validation_error=%s)", ...)
raise HTTPException(status_code=422, detail="Choose a Monday for weekly statements or the first day of the month for monthly statements.") from None
```

## 8. Rollback plan

If the copy proves confusing, revise or revert this isolated route change and redeploy. No feature flag or data correction applies; this change queues no new task for invalid requests.

## 9. Verification performed

- [x] Automated: `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_driver_statement_email.py -q --no-cov` (8 passed).
- [ ] Manual repro in staging: not run; no production/staging changes were made.
- [x] Blast-radius grep: `email_driver_statement` and statement builder callers under `backend`.
- [x] Reviewed against fixed client error boundary; no money, ride state, or persisted state changed.
- [x] No feature flag needed; this corrects invalid-request handling only.

## 10. Sign-off

- [x] Rollback is an isolated code revert and redeploy; no data remediation applies.
- [x] Blast radius and visible effect are stated.
- [x] Invalid weekly/monthly requests are covered and verified not to queue mail.
