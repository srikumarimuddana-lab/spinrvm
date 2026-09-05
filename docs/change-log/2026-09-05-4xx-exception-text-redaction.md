# Change Impact & Risk Log — Raw exception text redacted from 4xx responses (WS-E)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (secondary: drivers, auth — PIPEDA) |
| PR / commit link | branch `claude/pickup-otp-payment-fixes-5a8dnk` |
| Related issue or gap ID | `docs/audit/2026-09-05-engineering-director-review-round3.md` §1.7 (critical #7); WS-E in `plans/2026-09-01-critical-topology-remediation-plan.md` |

## 1. Issue / gap identified

`utils/error_handling.py` sanitised `HTTPException` details only when
`status_code >= 500`. About 30 route sites build a detail with `detail=str(e)`
or `detail=f"...{e}"`, so on a **4xx** the raw exception text went straight to
the client — and from there into browser history, Vercel logs and Sentry
breadcrumbs.

`routes/admin/legacy_sin_dob_backfill.py` is the worst case: its import service
raises with the offending CSV row, so a **SIN and a date of birth** end up in a
400 body. PIPEDA does not distinguish "only an admin saw it". Two of the sites
(`routes/drivers/appeals.py`, `routes/drivers/tax_exports.py`) are
**driver-facing**, leaking upstream text to a contractor.

## 2. Root cause

The 5xx sanitiser's own docstring states the reasoning that left 4xx open:
*"4xx pass through unchanged (those messages are intended user-facing UX —
'Invalid phone number', 'Card declined')."*

That is correct about the **purpose** of a 4xx detail and wrong about its
**provenance**. It assumes a 4xx detail is copy an engineer wrote; in ~30 places
it is an exception an upstream library or an import parser produced. Nothing
distinguished the two, so the safe default was applied only where the message
was known to be useless anyway.

## 3. Fix / remediation

Two layers, because neither is sufficient alone.

**1. Runtime backstop — `utils.pii.redact_error_detail`.** Applied to every 4xx
detail in the exception handler. It does not replace the message (that would
destroy the UX the docstring correctly protects); it scrubs identifiers out and
keeps the sentence. Removed: emails, JWTs, Stripe object ids and `sk_/rk_/pk_`
keys, UUIDs, Postgres/PostgREST internals (SQLSTATE, `PGRST###`, constraint and
relation names), ISO dates, and digit runs of 7+ (SIN, card PAN, phone, licence).
Output is truncated to 300 chars — a 4xx message longer than that is a stack
trace or a dumped row, not copy. Non-string details (dicts/lists, which FastAPI
allows) pass through untouched: those are structured payloads a route built
deliberately.

Ordering matters and is asserted: emails are redacted **first**, or the digit and
date patterns eat a local part like `2024test.user` and leave the domain exposed.

The raw detail is deliberately **not logged** on the 4xx path, unlike the 5xx
path which logs before sanitising — here the raw text may itself be the PII, so
logging it would move the leak rather than close it. Only a redaction-occurred
marker with the request id is logged.

**2. Build-time ratchet — `tests/test_no_raw_exception_in_4xx_detail.py`.** The
redactor is pattern-based and cannot catch a free-text street address, a person's
name, or a bespoke upstream error string. So a guard test fails the build on any
**new** `detail=str(e)` / `detail=f"...{e}"` site, against a frozen ledger of the
19 files that already do it. The ledger is documented as debt that only shrinks —
adding to it is called out in the test as the wrong fix.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface — this changes the body of every 4xx response the
backend produces.** That is the widest-reaching runtime change in this branch.

- `http_exception_handler` in `utils/error_handling.py` handles every
  `HTTPException` app-wide, so every rider-app, driver-app and admin-dashboard
  4xx passes through the new call.
- `utils/pii.py` gains new symbols only; nothing existing in it was modified, so
  its current callers (Sentry scrubber, log emission) are unaffected. Verified
  no circular import: `pii.py` imports only `re` and `typing`.
- The 5xx branch is untouched — it is now an `elif`, and 5xx cannot match the
  4xx condition, so its behaviour is identical.

Regression risks, stated plainly:

1. **A legitimate 4xx message containing a redacted pattern would be degraded.**
   The date pattern is the realistic one — "Document expired on 2026-01-01" would
   become "[redacted]". I grepped `routes/` for 4xx details interpolating dates
   or expiry values and **found none**, so no current message regresses. A future
   one would; the mitigation is to format such a date as human copy
   ("expired 3 days ago") rather than ISO.
2. **Error-code sentinels and client string-matching.** `ERR_*` sentinels
   (`ERR_DRIVER_ONLY`, …) contain no digits, emails or ids and are untouched, as
   are all the ordinary UX strings pinned by tests. The driver app surfaces the
   server `detail` verbatim via `getApiErrorMessage`, so a redacted id would show
   as `[redacted]` to a user — acceptable, and preferable to disclosing it.
3. **Per-request regex cost.** Seven regex passes over a short string, only on
   4xx responses. Negligible against any SLA in CLAUDE.md's table, and it does
   not touch the success path at all.
4. **The ledger can rot.** A second test fails if a listed file is cleaned up but
   left in the ledger, so the guard cannot silently stop covering it.

## 5. User-experience effect

- **Rider / driver (visible):** a 4xx message that used to embed an id or an
  upstream error now shows `[redacted]` in that position. The actionable part of
  the sentence is preserved — that is what the "keeps usable messages" tests
  pin. Ordinary validation copy is byte-identical.
- **Internal admin (visible):** import/backfill errors no longer echo the
  offending CSV row. **This is a real reduction in admin debuggability** — an
  admin who could previously see which row failed now sees a redacted message.
  The un-redacted text is still available server-side via the logs the routes
  already write, correlated by the `request_id` in the response body. That
  trade is deliberate: the rows in question contain SINs.
- No copy rewrites, no new notification.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/pii.py` | Adds `redact_error_detail()` and its patterns | The scrubber WS-E specifies, next to the other PIPEDA redactors |
| `backend/utils/error_handling.py` | 4xx details now pass through the redactor; the 5xx branch becomes `elif`; docstring corrected | Apply it at the one place every 4xx flows through |
| `backend/tests/test_redact_error_detail.py` | New (29 cases) | Pins what must be removed and what must survive |
| `backend/tests/test_no_raw_exception_in_4xx_detail.py` | New (3 cases) | Build-time ratchet + a self-test that the detector regex still matches |

## 7. Before / after

```python
# Before — utils/error_handling.py
detail = exc.detail
sanitized = False
if exc.status_code >= 500 and _should_sanitize_5xx_detail(detail):
    ...
# 4xx: detail used verbatim
```

```python
# After
detail = exc.detail
sanitized = False
if 400 <= exc.status_code < 500:
    _redacted = _redact_error_detail(detail)
    if _redacted != detail:
        logger.opt(raw=True).warning(f"[{request_id}] Redacted 4xx ... status={exc.status_code}")
    detail = _redacted            # raw text deliberately NOT logged — it may BE the PII
elif exc.status_code >= 500 and _should_sanitize_5xx_detail(detail):
    ...
```

Concrete scenario — the SIN/DOB backfill import hits a bad row:

| | Before | After |
|---|---|---|
| 400 body `message` | `Row 12 invalid: Jane Doe,jane@example.com,306-555-0142,123456789,1987-04-12` | `Row 12 invalid: Jane Doe,[redacted],[redacted],[redacted],[redacted]` |
| Reaches browser history | SIN + DOB + email + phone | none of them |
| Reaches Sentry breadcrumbs | same | same, redacted |
| Admin can tell which row | yes | yes — "Row 12" survives |

## 8. Rollback plan

No migration, no schema change, no data written. A `git revert` is a complete
rollback.

No feature flag — a flag whose "off" position re-publishes SINs to browser
history is not a state worth being able to reach. If a specific message is found
to be over-redacted, the narrower fix is to adjust one pattern in
`utils/pii.py`, or to fix that route to raise vetted copy, rather than to revert
the handler.

Already-leaked data is **not** recoverable by this change: anything emitted while
this was open is in browser histories, Vercel logs and Sentry breadcrumbs that
this codebase does not control. **Assessing whether a PIPEDA breach notification
is required for the SIN/DOB backfill route's history is out of scope here and is
a decision for a human** — `docs/runbooks/data-breach.md` is the procedure, and
the 72-hour Privacy Commissioner clock in CLAUDE.md's breach protocol applies
from when the exposure is judged to pose real risk of significant harm.

## 9. Verification performed

- [x] **Tests written AND RUN — 32 passed.** `test_redact_error_detail.py`
      (29 cases) and `test_no_raw_exception_in_4xx_detail.py` (3 cases) both run
      green via `pytest --noconftest -c /dev/null`, which works here because
      neither imports the FastAPI stack. These are the only tests in this branch
      that could actually be executed.
- [x] The guard test **found a site the audit's grep missed** —
      `routes/admin/stripe_events.py:254` — which is a 5xx already neutralised
      by the wholesale sanitiser; it is in the ledger with that note.
- [x] Blast-radius grep performed — all `detail=str(e)` / `detail=f"...{e}"`
      sites enumerated (19 files); `routes/` searched for 4xx details carrying
      legitimate dates or expiry values (none found); confirmed `pii.py` imports
      only `re`/`typing`, so no circular import with `error_handling.py`.
- [x] Reviewed against `CLAUDE.md`: PIPEDA (no PII in responses/logs/Sentry),
      dual-import pattern used for the new import, loguru conventions in
      `error_handling.py` (`logger.opt(...)`, no `%s`).
- [x] `ruff check` and `ruff format --check` clean.

## What was NOT verified

**The handler change itself was never executed.** `utils/error_handling.py`
imports FastAPI, which could not be installed (this environment's network policy
returns 403 for PyPI), so `http_exception_handler` was **not** run — only
`redact_error_detail` in isolation was. That means the wiring is unproven: that
the new branch is reached for a real 4xx, that `sanitized`/`headers` behaviour is
unchanged, and that the OTP-lockout `Retry-After` / `RateLimit-*` headers still
propagate (B-P1-8) are all read from the code, not observed. The existing
`error_handling` tests were not re-run and are the most likely place a mistake
shows. **Needs a full `pytest` run before merge.**

Also not verified: no end-to-end check that a real 4xx response body is redacted
in a running app; the mobile clients' rendering of a `[redacted]` message was
reasoned about from `getApiErrorMessage` in `driver-app/store/driverStore.ts`,
not seen on a device; and the ~30-site count is 19 *files* by this guard's
regex — the audit's per-line count was not independently re-derived.
