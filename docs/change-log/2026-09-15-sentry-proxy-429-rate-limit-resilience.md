# Change Impact & Risk Log — Sentry Proxy 429 Rate-Limit Resilience

**Date:** 2026-09-15
**PR branch:** `mvapps/great-bohr-qoesla`
**Sentry issue:** e0ed3014 (CRIMSON-SMOKE-7445)

## Issue/gap identified

The admin Sentry Issues viewer (`GET /api/admin/sentry/issues`) fires 5
concurrent HTTP requests to the Sentry API when fetching issues across all
surfaces. In tag mode (current deployment), all 5 hit the same Sentry project
endpoint simultaneously, tripping Sentry's per-project rate limit (HTTP 429).
When all 5 fail, the endpoint returns a 502 to the admin dashboard.

## Root cause

Three compounding factors:
1. **No 429-specific handling** — the `_sentry_request` function treated 429
   as a generic error, raising a 502 immediately instead of respecting the
   `Retry-After` header and retrying.
2. **Parallel fan-out in tag mode** — all targets hit the same project
   endpoint concurrently, maximizing the burst rate against Sentry's
   per-project quota.
3. **No response caching** — every dashboard refresh/filter change triggered
   a fresh 5-request burst, even if the same query was made seconds ago.

## Fix/remediation

1. **429 retry with Retry-After** — `_sentry_request` now detects HTTP 429,
   reads the `Retry-After` header (capped at 10s, default 2s), waits, and
   retries once. A second 429 still raises the 502.
2. **Staggered fan-out in tag mode** — when all targets share the same
   project (tag mode), requests are serialized with a 250ms stagger instead
   of fired in parallel, spreading the load across Sentry's rate-limit
   window.
3. **60-second Redis cache** — successful responses are cached by query
   parameters for 60 seconds. Cache misses or Redis failures fall through
   to live Sentry calls transparently. Partial-failure responses (with
   `errors`) are never cached.

## Risk & impact on existing functionality

- **`_sentry_request`** is called by `_fetch_project_issues`, `_get_issue`,
  and `_update_issue_status`. The retry only applies to 429 (read-safe);
  mutating calls (`_update_issue_status`) rarely hit 429 and a retry of
  an idempotent PUT is safe.
- **Tag-mode stagger** adds ~1 second total latency (4 × 250ms) to the
  all-surfaces list call in tag mode. Acceptable for a triage dashboard.
  Project mode (parallel fan-out) is unchanged.
- **Cache** is keyed on (surface, status, query, stats_period, limit).
  The 60s TTL means a newly-reported Sentry issue may take up to 60 seconds
  to appear on the dashboard; acceptable for triage. The Refresh button
  still works within the TTL if the query parameters differ.
- **Redis unavailability** — cache read/write failures are caught and
  logged at debug level; the endpoint degrades to live Sentry calls, which
  is the current behavior.

## User experience effect

Admin dashboard Sentry viewer will stop showing "Sentry API failed for
every configured surface" during normal use. No visible change to
rider/driver/corporate apps.

## Files modified

| File | What changed | Why |
|------|-------------|-----|
| `backend/routes/admin/sentry.py` | Added 429 retry, tag-mode stagger, Redis cache | Core fix |
| `backend/tests/test_admin_sentry.py` | Added 7 tests, autouse cache fixture | Coverage |
| `docs/change-log/2026-09-15-sentry-proxy-429-rate-limit-resilience.md` | This file | Change log |

## Before/after snippet

**Before** (`_sentry_request`, 429 handling):
```python
if resp.status_code >= 400:
    raise HTTPException(status_code=502, detail=f"Sentry API returned HTTP {resp.status_code}")
```

**After**:
```python
if resp.status_code == 429 and _retry < 1:
    retry_after = 2.0
    ra_header = resp.headers.get("Retry-After")
    if ra_header:
        try:
            retry_after = min(float(ra_header), 10.0)
        except (ValueError, TypeError):
            pass
    await asyncio.sleep(retry_after)
    return await _sentry_request(client, method, path, params=params, json=json, _retry=_retry + 1)
```

## Rollback plan

Revert the commit. The endpoint returns to the pre-fix behavior (immediate
502 on 429). No data migration or state change involved.

## Verification performed

- All 71 tests in `test_admin_sentry.py` pass (64 existing + 7 new)
- Lint clean (`ruff check`)
- Syntax validated

## What was NOT verified

- Not tested against live Sentry API (would require burning real API quota)
- Cache behavior under Redis cluster mode not tested (single-node only)
- No load test to verify the 250ms stagger is sufficient under sustained
  dashboard usage by multiple admins simultaneously
