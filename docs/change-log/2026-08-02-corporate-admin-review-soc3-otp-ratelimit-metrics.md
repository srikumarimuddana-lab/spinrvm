# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | auth, admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — SOC #3 |

## 1. Issue / gap identified

Two security-relevant events had no counter metric: an OTP brute-force
lockout firing (5 failures/hour → 24h Redis lockout, per CLAUDE.md), and
any request tripping a slowapi rate limit (429). Both were only visible
by grepping application logs (`OTP_LOCKOUT_TRIGGERED` / "Rate limit
exceeded" warning lines) — no counter existed for either, so there was
no way to see brute-force or abuse activity trending on the admin
dashboard's infra monitoring page, only after-the-fact log archaeology.

## 2. Root cause

Neither violation site ever called `utils.metrics.inc(...)`.
`routes/auth.py` didn't even import the metrics helper. `utils/rate_
limiter.py` already imports it (used for `spinr_rate_limit_storage_
errors_total`, a related but different signal — storage backend
failures, not client violations) but never called it from the actual
429-handling path.

## 3. Fix / remediation

- `routes/auth.py::_record_otp_failure`: added
  `_metric_inc("spinr_auth_otp_lockout_total")` inside the
  `if count >= settings.OTP_MAX_FAILURES:` block, right where the
  lockout is set and the existing `logger.error(OTP_LOCKOUT_TRIGGERED...)`
  already fires. Added the `utils.metrics.inc` import (dual-import, both
  branches, matching this file's existing convention).
- `utils/rate_limiter.py::rate_limit_exceeded_handler`: added
  `_metric_inc("spinr_rate_limit_violation_total", {"path":
  request.url.path})` right after the existing `logger.warning(...)`
  that already logs the same path — `_metric_inc` was already imported
  in this module.
- No changes needed to the metrics registry (`utils/metrics.py`) or the
  monitoring page/endpoint — confirmed both are fully generic: `inc()`
  lazily creates any counter name on first call with no pre-registration
  step, `GET /admin/monitoring/infrastructure` sums every counter in the
  snapshot into a flat `metrics: Record<string, number>` dict with no
  hardcoded name list, and the admin-dashboard's monitoring page
  (`admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx`) renders
  every entry in that dict as a stat tile. Both new counters therefore
  "just work" end-to-end with these two `inc()` call additions alone.

## 4. Risk & impact on existing functionality

- **Blast radius: 2 single-line metric increments, 2 files, 1 new
  import.** No change to the lockout logic itself (still triggers at the
  same threshold, same Redis keys), no change to the 429 response body/
  headers/status code, no change to any log line.
- `_metric_inc` calls are synchronous, in-process dict updates
  (`utils/metrics.py`'s `inc()` — confirmed via research: plain
  `dict.setdefault` + increment, no I/O) — cannot fail, block, or throw
  in any way that would affect the surrounding lockout/429 logic.
- `path`-labeled cardinality on `spinr_rate_limit_violation_total`: uses
  `request.url.path` (the raw path string, not a route template), so a
  path with an embedded ID (e.g. `/rides/{id}`) would create one label
  value per distinct ID hit. This matches the existing logging
  convention in the same handler (the `logger.warning` a few lines above
  already logs the same raw path) and no fixed label-cardinality
  discipline exists elsewhere in this metrics module — acceptable for
  this in-process, per-replica counter registry.
- Added 4 new tests (2 for the OTP lockout trigger — at-threshold emits,
  below-threshold doesn't — and 1 for the 429 handler emitting the
  labeled counter, per assertion) plus ran the full dependent test suite
  (`test_auth_send_otp.py`, `test_rate_limit_response_shape.py`,
  `test_verify_otp_login_flow.py` — 34 tests) — all passing.

## 5. User-experience effect

None rider/driver/corporate-admin-facing. Internal-only: an on-call
engineer or SOC analyst viewing the admin dashboard's infra monitoring
page (`/dashboard/monitoring/redis`) now sees
`spinr_auth_otp_lockout_total` and `spinr_rate_limit_violation_total`
alongside every other existing counter — automatically, since that
page's rendering is fully generic — instead of needing to grep
application logs to notice a brute-force or abuse spike.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | Added `utils.metrics.inc` import (both dual-import branches); `_record_otp_failure` now increments `spinr_auth_otp_lockout_total` at the lockout-trigger threshold | Make OTP brute-force lockouts visible as a metric, not just a log line |
| `backend/utils/rate_limiter.py` | `rate_limit_exceeded_handler` now increments `spinr_rate_limit_violation_total` labeled by path | Make rate-limit violations visible as a metric, not just a log line |
| `backend/tests/test_auth_send_otp.py` | 2 new tests: at-threshold emits the metric, below-threshold doesn't | Cover the new OTP lockout metric |
| `backend/tests/test_rate_limit_response_shape.py` | 1 new test: 429 emits the labeled metric | Cover the new rate-limit metric |

## 7. Before / after

```python
# Before — routes/auth.py, no metrics import at all
if count >= settings.OTP_MAX_FAILURES:
    await redis_set(_LOCK_KEY.format(phone), "1", settings.OTP_LOCKOUT_DURATION_SECONDS)
    logger.error(f"OTP_LOCKOUT_TRIGGERED phone=...{phone[-4:]} after {count} failures")
```

```python
# After
if count >= settings.OTP_MAX_FAILURES:
    await redis_set(_LOCK_KEY.format(phone), "1", settings.OTP_LOCKOUT_DURATION_SECONDS)
    logger.error(f"OTP_LOCKOUT_TRIGGERED phone=...{phone[-4:]} after {count} failures")
    _metric_inc("spinr_auth_otp_lockout_total")
```

```python
# Before — utils/rate_limiter.py, logged but not counted
logger.warning(f"Rate limit exceeded | Path: {request.url.path} | ...")
return JSONResponse(status_code=429, ...)
```

```python
# After
logger.warning(f"Rate limit exceeded | Path: {request.url.path} | ...")
_metric_inc("spinr_rate_limit_violation_total", {"path": request.url.path})
return JSONResponse(status_code=429, ...)
```

## 8. Rollback plan

Plain code change, no migration, no data written beyond new keys in the
already-existing, in-process metrics dict (which resets on every
process restart — nothing persisted). `git revert` fully restores the
prior (log-only) behavior. No feature flag — this is a pure observability
addition with zero behavioral surface.

## 9. Verification performed

- [x] Automated tests: `test_auth_send_otp.py` (16, incl. 2 new),
      `test_rate_limit_response_shape.py` (5, incl. 1 new),
      `test_verify_otp_login_flow.py` (13) — 34 passed, run via the
      session's `/tmp/spinr_venv` venv from repo root.
- [x] `ruff check` on both touched backend files — clean.
- [x] Confirmed (via research, not modified) that `utils/metrics.py`'s
      `inc()` requires no pre-registration and that
      `GET /admin/monitoring/infrastructure` +
      `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx`
      render every counter generically — no frontend or additional
      backend change needed for the "surface on the existing infra
      monitoring page" half of this finding.
- [ ] Manual repro in staging — not performed, no staging access; did
      not visually confirm the two new tiles rendering on the live
      monitoring page (reasoned about via the generic-rendering code
      path, not screenshotted).
- [x] Dry-run scenario: a phone number hits its 5th OTP failure within
      the failure window. Before this fix: `OTP_LOCKOUT_TRIGGERED` log
      line only. After this fix: same log line plus
      `spinr_auth_otp_lockout_total` increments by 1, confirmed via the
      new unit test asserting `_metric_inc` is called exactly once with
      that name at the threshold and not called below it.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — both call sites are
      synchronous, non-throwing, in-process dict updates
- [x] No silent behavior change to a working flow — lockout logic, 429
      response shape, and all existing logging are byte-for-byte
      unchanged; this is a pure additive observability signal

## What was NOT verified

Did not verify against a live/staging admin dashboard or take a
screenshot of the new tiles rendering — reasoned about the generic
rendering pipeline via source reading, not visually confirmed (no
browser access in this environment; no automated visual-regression
tooling exists in this repo per CLAUDE.md's standing gap). Did not add
Prometheus alerting rules or a dashboard panel threshold for either new
counter — this fix makes the data visible on the existing page; deciding
what counts as an alertable spike is a follow-up product/ops decision,
not implemented here.
