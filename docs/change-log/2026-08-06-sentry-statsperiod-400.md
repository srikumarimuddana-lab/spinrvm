# Sentry viewer 502s on any look-back other than 24h or 14d

**Date:** 2026-08-06
**Surface:** backend (`routes/admin/sentry.py`)
**Found by:** production logs — `GET /api/admin/sentry/issues?...stats_period=30d` → 502

## Issue/gap identified

Three of the dashboard's five look-back options (7d, 30d, 90d) **never worked**. Selecting
one produced:

```
[sentry] GET /projects/spinr-backend/crimson-smoke-7445/issues/ -> 400
[sentry] listing project crimson-smoke-7445 (backend) failed: Sentry API returned HTTP 400
... status=502 detail=Sentry API failed for every configured surface: backend: Sentry API returned HTTP 400
```

The same request with `stats_period=14d` returned 200, which is what isolated the cause.

Two defects, one visible and one that hid it:

1. **The look-back was sent as `statsPeriod`.** Sentry's *List a Project's Issues* endpoint
   accepts only `""`, `"24h"`, `"14d"` there — it sizes the per-issue stats sparkline.
   Anything else is a hard 400.
2. **Sentry's 400 was flattened to a generic 502.** `_sentry_request` discarded the response
   body, so an unsupported-parameter error surfaced to the operator as *"Sentry API failed
   for every configured surface"* — outage-shaped wording for a request-shape bug. This is
   why it took a production log to diagnose rather than the error message.

## Root cause

`stats_period` was passed straight through from the query string to Sentry's `statsPeriod`
param (`_fetch_project_issues`), with no validation and no awareness that the endpoint's
accepted set is far narrower than the UI's options (`PERIOD_OPTIONS` in
`sentry-logs/page.tsx:59` offers 24h/7d/14d/30d/90d). The mismatch had no test because
every existing list test stubbed `_fetch_project_issues`, so nothing asserted what was
actually sent to Sentry.

## Fix/remediation

Decoupled the *filter* from the *sparkline*. `_period_params()` splits a requested window:

- `statsPeriod` is clamped to a value Sentry accepts (`14d` for anything outside the set).
- The actual look-back becomes a `lastSeen:-<window>` search term, which accepts arbitrary
  windows — Sentry: *"lastSeen:-2d returns issues last seen within the past two days"*.

All five UI options now work. The alternative — restricting the dashboard to 24h/14d —
would have removed 30d/90d triage, which is the window where slow-burn issues appear.

Also:
- Malformed windows are rejected at our edge with a clean **400** (`_PERIOD_RE`) instead of
  being forwarded and returning a 502 that looks like an outage.
- Sentry 400s now relay a truncated `detail` from the response body. PII-safe: a 400 body is
  a parameter-validation message, not issue content.

## Risk & impact on existing functionality

- **`_period_params` is new and called from exactly one place** (`_list_issues`). No other
  caller.
- **`_sentry_request` is shared** by every Sentry call — list, detail, status update. The
  change adds a `400` branch *before* the existing `>= 400` catch-all; 401/403/404 and 5xx
  paths are untouched. Confirmed by the pre-existing
  `test_sentry_request_maps_error_statuses` parametrisation still passing unchanged.
- **Semantic change worth naming:** filtering by `lastSeen` is not identical to the old
  intent. `statsPeriod` never filtered reliably anyway (it sized stats), so previously the
  only working options returned whatever the endpoint's default window gave. Results for
  24h/14d may therefore differ slightly from before — they are now *actually* filtered to
  the requested window, which is what the UI always claimed.
- The response still echoes the **requested** `stats_period`, not the clamped sparkline
  value, so the dashboard's period selector stays in sync.
- No frontend change; `PERIOD_OPTIONS` is now honoured rather than partly broken.

## User experience effect

Internal-admin only (`/dashboard/sentry-logs`, super-admin gated). 7d / 30d / 90d change
from a red "Sentry API failed for every configured surface" banner to working results. No
rider, driver, or corporate surface affected.

## Files modified

| file path | what changed | why |
|---|---|---|
| `backend/routes/admin/sentry.py` | `_SENTRY_STATS_PERIODS`, `_PERIOD_RE`, `_period_params()`; `_list_issues` applies the look-back as a query term and sends the clamped sparkline; `_sentry_request` relays Sentry's 400 detail | The fix |
| `backend/tests/test_admin_sentry.py` | +17 tests; updated the tag-mode query assertion to include the look-back term | Regression cover |

## Before/after

```python
# before — the requested window went straight to Sentry as statsPeriod
_fetch_project_issues(..., query=base_query, stats_period=stats_period)
# 30d -> Sentry 400 -> generic 502 "Sentry API failed for every configured surface"

# after — sparkline clamped, look-back moved into the search
stats_param, period_term = _period_params(stats_period)   # ("14d", "lastSeen:-30d")
base_query = f"is:{status} {period_term} ..."
_fetch_project_issues(..., query=base_query, stats_period=stats_param)
```

## Rollback plan

Revert the commit — pure request-shaping, no stored state, no migration, no money or ride
state. Behaviour returns to 24h/14d working and 7d/30d/90d 502-ing. The independent kill
switch is unchanged: unset `SENTRY_API_TOKEN` and the viewer renders its setup panel.

## Verification performed

- `pytest tests/test_admin_sentry.py` → **64 passed** (47 prior + 17 new).
- New tests assert the exact strings sent to Sentry — previously nothing did, which is why
  the bug shipped: `stats_period="30d"` now provably sends `statsPeriod="14d"` and
  `query="is:unresolved lastSeen:-30d"`.
- Malformed windows (`"30"`, `"1y"`, `"'; drop"`, `"24h OR 1=1"`, `"9999d"`) all 400.
- 400-relay tested both with a parseable body (detail surfaces) and an unparseable one
  (still fails cleanly as 502).
- Pre-existing `_sentry_request` status-mapping tests pass unchanged → 401/403/404/5xx
  behaviour intact.
- `ruff check` clean; `ruff format` reports no changes.

## What was NOT verified

- **Not run against live Sentry.** `lastSeen:-<window>` is taken from Sentry's documented
  searchable properties, not confirmed against the API. The production 400 that prompted
  this is explained by the documented `statsPeriod` constraint, and `14d` demonstrably
  worked, but that the fix *resolves* the 502 is inference until deployed.
- **Result-set equivalence not checked.** Whether `lastSeen:-14d` returns the same issues
  the old `statsPeriod=14d` call did has not been compared against real data.
- No frontend build or manual click-through of the period selector.
- The 400-detail relay assumes Sentry 400 bodies never carry issue content. True for
  parameter validation; not exhaustively verified across every 400 Sentry can emit — the
  value is truncated to 200 chars as a hedge.
