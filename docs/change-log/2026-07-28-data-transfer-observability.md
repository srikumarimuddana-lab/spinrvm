# Change Impact & Risk Log — Data Transfer module: Sentry + Prometheus instrumentation

## Issue/gap identified
Flagged in the earlier critical review: zero Sentry tagging or Prometheus
metrics existed anywhere in the Data Transfer module, despite CLAUDE.md's
Observability section requiring `domain`/`surface`/entity-ID Sentry tags on
user-visible errors and `spinr_<domain>_<metric>_<unit>` metrics on state
transitions. For a module that moves PII between environments, "an export
silently failed and nobody was paged" was a real, named failure mode —
especially for the export route, which is backgrounded (no request left to
surface a failure to) since the earlier follow-up.

## Root cause
The module was built without checking the metrics/Sentry conventions
already established elsewhere (`routes/rides/payments.py`'s
`spinr_payment_settlement_total`, `utils/refresh_tokens.py`'s explicit
tagged Sentry capture for refresh-token-reuse).

## Fix/remediation
New `backend/services/data_transfer/observability.py` — a small shared
module (three call sites needed the same shape, so a shared helper avoids
tripling the boilerplate):
- `record_export_result(status, format, duration_ms=None)` →
  `spinr_data_transfer_export_total{format,status}` counter +
  `spinr_data_transfer_export_duration_ms{format}` histogram.
- `record_import_result(status)` → `spinr_data_transfer_import_total{status}`.
- `record_sgi_form_result(form_type, status)` →
  `spinr_data_transfer_sgi_form_total{form_type,status}`.
- `capture_failure(message, alert, contexts)` — explicit tagged
  `sentry_sdk.capture_message` (`domain="admin"`, `surface="backend"`,
  `spinr_alert=<alert>`), same shape as `utils/refresh_tokens.py`'s only
  other explicit-tagged capture in this codebase (try/except around the
  `sentry_sdk` import + call, `logger.debug` fallback, never raises). Note:
  ordinary `logger.error(...)` calls are already captured by Sentry
  automatically via `LoggingIntegration(event_level="ERROR")` in
  `server.py` — this adds the missing domain/surface tags + structured
  context on top of that existing pipeline, it doesn't replace it.

Wired into the three highest-signal failure points:
- `data_transfer_export.py::_run_export_job` — records
  completed/failed + duration on every background job; the failure branch
  is the single most important one to have Sentry-tagged, since there is no
  request left to see it once backgrounding landed.
- `data_transfer_import.py::commit_bundle_import` — records
  completed/failed on commit (the write path; `/validate` is a pure
  dry-run with no side effects worth metering).
- `sgi_forms.py::generate_sgi_form` — the PDF-fill step previously had no
  `try/except` at all (an unhandled exception would leak a raw 500); now
  catches, records `failed`, captures to Sentry, and returns a clean
  `HTTPException(502)` instead — a real correctness fix alongside the
  instrumentation (CLAUDE.md: "return a clean HTTPException ... instead of
  handing back a half-valid response").

## Risk & impact on existing functionality
Blast radius: `observability.py` is a new file with three consumers, all
modified in this same commit. `metrics.inc`/`metrics.observe` are the
existing, unmodified helpers from `utils/metrics.py` — no changes to that
module. `capture_failure` only ever calls `sentry_sdk.capture_message`
(never touches request/response flow) and is wrapped in a blanket
`try/except` that never raises, matching the `refresh_tokens.py` precedent
exactly — a failure to import or call Sentry cannot break the export/
import/SGI-form flow it's attached to. The one behavior change beyond pure
instrumentation is `sgi_forms.py`'s new `try/except` around PDF-filling,
which changes an unhandled-500 into a clean 502 — strictly an improvement,
not a new failure mode.

## User experience effect
None for a successful call. For a failure, `sgi_forms.py` now returns a
clean `502 Could not generate the SGI form` instead of a raw 500 — visible
to an admin only in the (rare) case a fill actually fails.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/services/data_transfer/observability.py` | New: metrics + Sentry helpers | Shared instrumentation, avoid tripling boilerplate across 3 routes |
| `backend/routes/admin/data_transfer_export.py` | Records export result/duration; tagged Sentry capture on background-task failure | Highest-signal gap — backgrounded, no request to fail loudly to |
| `backend/routes/admin/data_transfer_import.py` | Records import commit result; tagged Sentry capture on commit failure | Metering + tagged alerting on the write path |
| `backend/routes/admin/sgi_forms.py` | Records SGI form result; wraps PDF-fill in try/except (previously unguarded) with tagged Sentry capture | Metering + fixes an unhandled-500 gap found while instrumenting |

## Before/after snippet
```python
# data_transfer_export.py, _run_export_job failure branch, before:
except Exception as e:
    logger.error("data-transfer export: job %s failed", job_id, exc_info=True)
    await db_supabase.update_one(...)
    return

# after:
except Exception as e:
    duration_ms = (time.monotonic() - t0) * 1000.0
    logger.error("data-transfer export: job %s failed", job_id, exc_info=True)
    observability.record_export_result("failed", fmt, duration_ms)
    observability.capture_failure(
        "Data Transfer export job failed", "data_transfer_export_failed",
        {"job_id": job_id, "admin_id": admin.get("id"), "format": fmt, "requested_count": len(pairs), "error": str(e)},
    )
    await db_supabase.update_one(...)
    return
```
```python
# sgi_forms.py, before: no try/except around the fill step at all
row_dicts = [...]
pdf_bytes = sgi_form_filler.fill_driver_details_form(row_dicts)  # unhandled exception -> raw 500

# after: wrapped, records + captures + returns a clean 502
try:
    ...
    pdf_bytes = sgi_form_filler.fill_driver_details_form(row_dicts)
except Exception as e:
    observability.record_sgi_form_result(body.form_type, "failed")
    observability.capture_failure(...)
    raise HTTPException(status_code=502, detail="Could not generate the SGI form") from e
```

## Rollback plan
Revert all four files (`git revert` is safe — pure instrumentation addition
plus one strictly-safer error-handling change; no data or schema
involved). `observability.py` has no other consumers to break if removed.

## Verification performed
- `python3 -m py_compile` on all four files — passes.
- **Actually executed `observability.py` against the real, unmodified
  `utils/metrics.py`** (bypassing the package import chain via
  `importlib.util`, same technique used earlier in this module's SGI-filler
  verification): called all three `record_*` functions and
  `capture_failure`, then rendered the real Prometheus exposition output
  and confirmed the counter/histogram shapes match what's shown above —
  not just "should work," genuinely executed.
- `capture_failure` was exercised with no `sentry_sdk` installed (this
  session's actual environment) and correctly fell through to the
  `except Exception: logger.debug(...)` no-op path without raising —
  confirming the fail-open behavior works, not just that it's written.
- Confirmed metric names follow CLAUDE.md's
  `spinr_<domain>_<metric>_<unit>` convention by comparing directly against
  the existing `spinr_payment_settlement_total`/
  `spinr_payment_settlement_duration_ms` pair in `routes/rides/payments.py`.
- Confirmed `capture_failure`'s tag/context shape matches
  `utils/refresh_tokens.py`'s refresh-token-reuse capture exactly (only
  other explicit-tagged Sentry call in this codebase) rather than inventing
  a new shape.

## What was NOT verified
- Not exercised with a real `sentry_sdk` installed and a real/mock DSN — the
  actual Sentry event shape (tags, contexts) was not observed landing in a
  Sentry project, only reasoned about via the identical code shape to the
  working `refresh_tokens.py` precedent.
- The `/metrics` HTTP exposition endpoint itself was not hit — verified the
  underlying `render_prometheus()` output directly, not the full route.
- No unit test added for `observability.py` — standing coverage gap, not
  new to this change.
