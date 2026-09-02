# Change Impact & Risk Log — WS-E: redact exception text in 4xx responses

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-01 |
| Author | Claude Code session (branch `claude/topology-remediation-plan-g516e0`) |
| Surface(s) | backend (admin + driver API surfaces) |
| Domain (Sentry tag) | admin (primary), drivers, auth |
| PR / commit link | commits `dafe6cb` … `3fe74f4` on `claude/topology-remediation-plan-g516e0` |
| Related issue or gap ID | Critical issue **C6** in `docs/audit/2026-09-01-engineering-director-teardown.md`; WS-E of `plans/2026-09-01-critical-topology-remediation-plan.md` (both land via PR #4863) |

## 1. Issue / gap identified

33 route sites returned raw exception text to the client as an HTTP `detail`
(`detail=str(e)` or `detail=f"...{e}"`). The concentration is in the admin CSV
importers and legacy backfills — the code paths whose exceptions are *about* the
row that failed validation, so the message routinely carries the SIN, date of
birth, email, phone or postal code that made the row invalid.

## 2. Root cause

Not an oversight in one route — a gap in a deliberate policy. `http_exception_handler`
sanitises 5xx details (B-P2-1) but passes 4xx through **by design**, because 4xx
details are the user-facing UX copy ("Invalid phone number", "Card declined").
That design is right; what was missing is that it silently also covers any 4xx
detail an author built out of an exception. Nothing distinguished "vetted copy"
from "arbitrary exception text" at the 4xx boundary, and nothing stopped a new
route from adding another such site.

## 3. Fix / remediation

Two layers.

1. **`redact_client_text()` / `client_safe_detail()`** (`backend/utils/pii.py`).
   `redact_client_text` reuses `ai/pii.py`'s existing value patterns (phone,
   email, GPS, postal code, PAN, grouped SIN) rather than re-deriving them, and
   adds the patterns that are exception-shaped rather than message-shaped:
   absolute server paths, JWT/provider-key/Bearer tokens, bare 9-digit SIN, and
   date-shaped runs. Output is truncated to 300 chars with an explicit marker.
   `client_safe_detail(exc, fallback=…)` is what a route calls in place of
   `str(e)`; it falls back when nothing usable survives redaction.
2. **Blanket 4xx redaction in `http_exception_handler`.** Every 4xx *string*
   detail is redacted (not replaced) on the way out, so a future `detail=str(e)`
   that forgets layer 1 still cannot leak a known identifier shape.

All 33 sites were converted, plus 1 more the plan had not enumerated
(`support_tickets._err`, which passed a Zoho vendor message through). A
`tokenize`-based drift guard (`tests/test_error_handling_detail_leak_guard.py`)
holds the line with an **empty allowlist**.

## 4. Risk & impact on existing functionality

**Blast radius: cross-cutting, and deliberately so.** Layer 2 is not scoped to
the admin routes — it sits in `http_exception_handler`, which
`backend/core/middleware.py` registers for the whole app. Every 4xx response
from every router now passes through the redactor. That is the point of the
second layer, and it is also the main risk this change carries.

What that touches, and why each is assessed safe:

| Consumer | Assessment |
|---|---|
| Every route raising `HTTPException` with a 4xx and a string detail | **Swept: 1335 `detail=` string literals across `backend/routes/**` and `documents.py` were run through `redact_client_text`; 0 were altered.** Redaction is a verified no-op on every message the codebase actually ships. |
| Routes with non-string details (dicts) | Untouched — the branch is guarded by `isinstance(detail, str)`. Covered by a test. |
| 5xx paths (B-P2-1 sanitiser) | Untouched. The new branch is `elif`, so the 5xx replacement runs first and unchanged. Covered by two tests. |
| Mobile/admin clients reading `errorData.detail` | rider-app (`otp.tsx`, `profile-setup.tsx`, `ride-status.tsx`, `attemptRidePayment.ts`, `aiChat.ts`), driver-app (`useDriverDashboard.ts`, `documentStore.ts`, `driverStore.ts`, `profile-setup.tsx`) all render the string as-is. Since no shipped message changes, rendering does not change. |
| Backend tests asserting on `["detail"]` | Swept the same way: of 1370 string literals on `detail`-mentioning lines in `backend/tests/**`, 32 would be altered by the redactor. Each inspected — all are either audit-log `details` payloads (a different field: `test_admin_staff_coverage.py:228`, `test_corporate_company_routes.py:1038`), scrubber self-tests, or this change's own new tests. The one genuine HTTP-detail case (`test_driver_sin_collection.py:227`) is a **negative** assertion (`"123456789" not in detail`) that this change strengthens. **No test regression identified.** |
| Ride state machine / money paths / background loops | No interaction. No state field, table, wallet delta or loop is read or written. |

Residual risk, stated plainly: a 4xx message that *legitimately* needs to echo a
value matching one of the patterns would now be redacted. The 1335-literal sweep
found no such message today, but a future one would be silently degraded rather
than failing loudly. The guard test does not catch that class.

## 5. User-experience effect

- **Internal admin:** the only visible change. On a failed CSV import or
  backfill, a row error that used to read `row 8: bad sin 123 456 789 for
  nighil@example.com` now reads `row 8: bad sin [GOVID] for [EMAIL]`. The row
  and column identifiers — the part that makes the error actionable — are
  preserved. Two 409/502 messages become fixed copy: "This export request has
  already been decided" and "Upstream LMS error".
- **Driver:** no change. The three driver-facing sites all return validation
  copy containing no redactable pattern, so `client_safe_detail` returns them
  byte-for-byte ("weekly period_start must be a Monday", "SIN failed its
  checksum — check for a mistyped digit").
- **Rider / corporate admin:** no change.
- **Visible mid-session?** No. No message on a rider-mid-ride or driver-online
  path changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/pii.py` | +`redact_client_text()`, `client_safe_detail()`, `CLIENT_DETAIL_MAX_LEN`, `_CLIENT_TEXT_PATTERNS` | The redactor. Reuses `ai/pii.py` patterns; adds path/token/bare-SIN/date. |
| `backend/utils/error_handling.py` | 4xx string details redacted in `http_exception_handler` | Layer 2 — a forgotten helper call cannot leak. |
| `backend/routes/admin/driver_import.py`, `legacy_driver_import.py`, `rider_import.py` | `client_safe_detail` / `redact_client_text` at 8 sites | CSV parser + service-area + import-token errors. |
| `backend/routes/admin/booking_import.py`, `wallet_import.py`, `stripe_import.py` | same, 3 sites | Same parser leak; `{label} CSV:` prefix kept. |
| `backend/routes/admin/legacy_sin_dob_backfill.py`, `legacy_saved_address_backfill.py`, `legacy_vehicle_history_backfill.py` | same, 6 sites | Highest-value targets: SIN/DOB, addresses, postal codes. |
| `backend/routes/admin/tax_id_import.py`, `data_transfer_import.py`, `driver_statements.py` | same, 4 sites | Tax-id rows, bundle parse errors, statement date ranges. |
| `backend/routes/admin/drivers.py` | LMS 502 → fixed message + redacted log; SIN 422 → `client_safe_detail` | Vendor text; SIN endpoint. |
| `backend/routes/admin/export_approvals.py`, `driver_appeals.py` | fixed 409 copy; `client_safe_detail` on the 400 | Echoed internal request id / reflected caller input. |
| `backend/routes/drivers/appeals.py`, `tax_exports.py`, `profile.py` | `client_safe_detail`, 3 sites | Driver-facing validation copy, preserved verbatim. |
| `backend/routes/admin/support_tickets.py` | `_err()` redacts the Zoho message | Not in the plan's list; found by the guard. |
| `backend/documents.py` | 3× dropped `{e}` from 500 details | Already logged one line above; 5xx detail is replaced anyway. |
| `backend/tests/test_error_handling_4xx_redaction.py` | new | Both layers, incl. "legit copy unchanged". |
| `backend/tests/test_error_handling_detail_leak_guard.py` | new | Drift guard, empty allowlist. |

## 7. Before / after

```python
# Before — backend/routes/admin/legacy_sin_dob_backfill.py
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
# → 422 {"detail": "row 8: bad sin 123 456 789 for nighil@example.com"}
```

```python
# After
    except ValueError as e:
        raise HTTPException(
            status_code=422, detail=client_safe_detail(e, fallback="CSV could not be parsed")
        ) from e
# → 422 {"detail": "row 8: bad sin [GOVID] for [EMAIL]"}
```

```python
# Before — backend/utils/error_handling.py (4xx fell through untouched)
    if exc.status_code >= 500 and _should_sanitize_5xx_detail(detail):
        ...
        detail = "Internal server error"
```

```python
# After
    elif 400 <= exc.status_code < 500 and isinstance(detail, str) and detail:
        redacted = redact_client_text(detail)
        if redacted != detail:
            logger.warning(... f"redacted_detail={redacted[:200]}")
            detail = redacted
```

## 8. Rollback plan

`git revert` is a genuine rollback here, and this is one of the cases the policy
allows it for: **no data is written, mutated, or migrated by this change.** No
schema change, no `app_settings` value, no wallet delta, no ride-state or
insurance-period row. Reverting the two commits touching `utils/` restores
byte-identical previous responses on the next deploy; reverting the route
commits restores the raw details. The two layers are independent, so either can
be reverted alone (reverting only the route commits leaves the blanket redaction
in place, which is the safer half).

No feature flag: WS-D (the typed flag layer) does not exist yet, and adding an
`app_settings` boolean solely for this would put a DB read on every error path.
Stated as a deliberate trade-off, not an oversight.

## 9. Verification performed

- [x] **Blast-radius sweep (automated, not eyeballed):** all 1335 `detail=`
      string literals under `backend/routes/**` + `documents.py` extracted via
      `ast` and passed through `redact_client_text` — **0 altered**. Same sweep
      over `backend/tests/**` detail-lines: 32 hits, each inspected individually
      (results in §4).
- [x] **Guard test executed** (pure stdlib, runs without the dependency stack):
      0 offenders, empty allowlist, and its own regex self-check passes.
- [x] **Redactor behaviour executed** against 11 leak shapes and 6 legitimate-copy
      shapes, including that API route names (`POST /admin/drivers/import`)
      survive — the file-path pattern is root-anchored specifically for this.
- [x] `ruff check` + `ruff format --check` clean on every file touched.
- [x] `python -m py_compile` clean on every file touched.
- [x] Reviewed against CLAUDE.md PIPEDA rules (see §11 on the log-line deviation)
      and the "don't silently swallow errors" rule — no error is swallowed;
      exception context is preserved via `from e` at every site, and the LMS path
      gained an `logger.error` it did not have.
- [ ] **Not feature-flagged** — justified above.

## 10. What was NOT verified

- **`pytest` was never executed.** PyPI egress is blocked in this environment
  (the gateway answers 403 to CONNECT), so `fastapi`/`pydantic`/`pytest` could
  not be installed. Everything verifiable with stdlib + `ruff` was run and is
  listed above; the two new test files and the existing suite have **not** been
  executed and will first run in CI. This is the single largest gap in this
  entry — the reasoning above about which existing tests are unaffected is a
  static analysis of their assertions, not a green test run.
- No staging deploy, no manual admin-dashboard repro of a failing CSV import.
- No visual-regression coverage for the admin-dashboard error toast that renders
  these strings — per CLAUDE.md release gate 6 this is the standing repo-wide gap
  (`ACTION_ITEMS.md` B38: the Playwright job has zero committed baselines and
  skips itself), so the copy change was reasoned about, not screenshotted.
- `drivers.py` (4×`B904`) and `driver_statements.py` (1×`I001`) carry
  pre-existing ruff findings. Confirmed identical counts before and after by
  re-running `ruff` against a stashed tree; not fixed, per the surgical rule.

## 11. Deviations from the plan (§2 of the remediation plan)

Three, each deliberate:

1. **Log the post-redaction text, not the pre-redaction text.** The plan asked
   for the raw detail to be logged at `warning`. CLAUDE.md's PIPEDA section names
   these exact values as ones that may *never* appear in logs — writing them to
   the log to prove they were kept out of the response would relocate the breach,
   not close it. The redacted text still identifies the route and which pattern
   fired.
2. **Guard test lands last, not at E2.** The plan expected it red from E2 until
   E7. It is committed after the sites it guards so no commit in the series
   carries a knowingly-failing test.
3. **Driver-facing sites use `client_safe_detail`, not fixed messages.** The plan
   specified fixed messages for E8 on the grounds that they are driver-facing.
   The actual exception texts are user-actionable validation copy; replacing them
   would be a UX regression on a live-tested surface (release gate 5) for no
   security gain, since none contains a redactable pattern. Detail in the E8
   commit message.

Additionally, `documents.py`'s three sites were fixed rather than left as the
plan suggested — its "leave them (surgical rule)" conflicts with its own
"allowlist must be **empty**" requirement, and three one-line changes with no
behaviour change is the cheaper way to satisfy both.

## 12. Sign-off

- [x] Rollback plan is concrete and testable (pure code revert; no data touched)
- [x] Blast radius is stated and measured (1335-literal sweep), not assumed
- [x] No silent behavior change to an already-shipped flow — the one visible
      change (admin import error copy) is documented in §5
- [ ] **Not signed off on test execution** — see §10
