# Change Impact & Risk Log — Analytics driver-acceptance truncation

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | srikumarimuddana@gmail.com (via Claude Code) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-dashboard-analytics-review-xsjyuk` |
| Related issue or gap ID | Operational Analytics review, finding P1-1 |

## 1. Issue / gap identified

`GET /api/admin/analytics/driver-acceptance` reported a `low_performer_count`
across every driver, but returned only the first `limit` (default 50) rows of
an acceptance-rate **descending** sort. Low performers sort last, so the
drivers the summary card counted were precisely the rows the slice discarded.
The admin page showed "Low Performers (&lt;70%): N" above a table that could not
contain them, with no pagination, no search, and no "showing 50 of N".

Found during a read-through of `/dashboard/analytics` requested by the user,
not from a bug report — it is silent by construction.

## 2. Root cause

Two independent decisions that are each defensible alone and broken together:

1. `result.sort(key=acceptance_rate, reverse=True)` then `result[:limit]` —
   a fixed "top N" slice with no way to reach the tail.
2. Summary stats computed over the full list while the payload carried the
   page — so the count and the rows it described came from different sets.

Compounding: the frontend sorted client-side with `useTableSort`, which only
reorders rows already on the page. Clicking "Acceptance Rate" ascending looked
like it surfaced the worst drivers; it surfaced the worst of the best 50.

## 3. Fix / remediation

Made every counted driver reachable, server-side:

- Added `offset`, `search`, `sort_by`, `order`, `min_rides`, and
  `low_performers_only` to the endpoint.
- Summary stats (`total_drivers`, `avg_acceptance_rate`,
  `low_performer_count`) now cover the full filtered set explicitly; the page
  is a separate slice. Response carries `returned`/`offset`/`limit`/`has_more`.
- Centralised the low-performer rule in `_is_low_performer()` and returned the
  threshold (`low_performer_threshold`) so the card, the row highlight, and
  the filter cannot drift apart.
- Service-area scoping moved into the query. It previously ran in Python
  **after** a 500-row cap, so an operator above 500 drivers filtered an
  arbitrary subset. Cap raised to `_DRIVER_SCAN_CAP = 2000` and, when hit,
  logged at warning level and surfaced as `scan_truncated` in the payload —
  no silent truncation (CLAUDE.md "No silent caps").
- Frontend: server-side sort, debounced name search, pagination via the
  existing `Pagination` component, a "Show only these" link on the low-performer
  card, low-performer row highlighting, and a banner when `scan_truncated`.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** Grepped `getDriverAcceptanceRates` and
`driver-acceptance` across the repo:

- `admin-dashboard/src/app/dashboard/analytics/page.tsx` — sole caller, updated.
- `admin-dashboard/src/lib/api.ts` — re-export only, signature unchanged at that layer.
- `backend/tests/test_admin_analytics_coverage.py` — 4 existing tests, all still pass unmodified.

No other surface reads this endpoint. No ride state, money, wallet, dispatch,
or background loop (`core/lifespan.py`) is touched — this is a read-only admin
aggregation over `drivers`/`users` plus the existing
`admin_driver_acceptance_rates` RPC. No migration, no schema change.

The API client's `getDriverAcceptanceRates(dateRange, serviceAreaId?)` became
`(dateRange, opts?)`. That is a breaking TS signature change, but the only
caller passed one argument. Verified by grep and by a clean `tsc --noEmit`.

New query params are all optional with defaults matching prior behavior, so an
un-updated client keeps its old response shape plus additive keys.

## 5. User-experience effect

**Internal admin only.** No rider, driver, or corporate-admin surface changes.
Not visible mid-session to anyone using the rider or driver app.

Visible change for admins on `/dashboard/analytics` → Driver Acceptance Rates:
page size drops 50 → 25 with real pagination, a search box appears, column
sorting now sorts the whole result set rather than the visible page, and low
performers are reachable and highlighted. This is a deliberate behavior change
to an already-shipped internal screen — the previous behavior silently hid the
rows the page told you to care about.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/analytics.py` | `/driver-acceptance`: added pagination/search/sort/filter params; summary over full set; `_is_low_performer()`, `_DRIVER_SCAN_CAP`, `scan_truncated`; area filter pushed into the query | Make every counted driver reachable; stop filtering after the cap |
| `backend/tests/test_admin_analytics_coverage.py` | +13 tests in `TestDriverAcceptancePagination` | Regression cover for the count-vs-page mismatch |
| `admin-dashboard/src/lib/api/analytics-payouts.ts` | `getDriverAcceptanceRates` takes an options object | Pass the new params |
| `admin-dashboard/src/app/dashboard/analytics/page.tsx` | Server-side sort/search/pagination; low-performer filter + highlight; truncation banner | Surface the drivers the card counts |

## 7. Before / after

```python
# Before — summary counted every driver, payload carried the best 50
result.sort(key=lambda x: x["acceptance_rate"], reverse=True)
low_performers = [r for r in result if r["acceptance_rate"] < 70 and r["total_rides"] >= 5]
return {
    "total_drivers": len(result),
    "low_performer_count": len(low_performers),
    "drivers": result[:limit],          # <- low performers sort last, dropped here
}
```

```python
# After — same summary, but the tail is reachable
if low_performers_only:
    result = [r for r in result if _is_low_performer(r)]
low_performer_count = sum(1 for r in result if _is_low_performer(r))
result.sort(key=lambda x: (x.get(sort_by) or 0, x["driver_id"]), reverse=order == "desc")
page = result[offset : offset + limit]
return {
    "total_drivers": len(result),
    "low_performer_count": low_performer_count,
    "low_performer_threshold": {"rate_below": _LOW_PERFORMER_RATE, "min_rides": _LOW_PERFORMER_MIN_RIDES},
    "drivers": page,
    "returned": len(page), "offset": offset, "limit": limit,
    "has_more": offset + len(page) < len(result),
    "scan_truncated": scan_truncated,
}
```

## 8. Rollback plan

`git revert` is sufficient and complete here. No migration, no schema change,
no write path, no live data touched — the endpoint is read-only and the change
is confined to request parsing, in-memory ordering, and response shape.
Reverting restores the previous payload exactly; no client persists any of the
new fields.

No feature flag: the change is internal-admin-only and read-only, and flagging
it would mean keeping the broken code path selectable.

## 9. Verification performed

- [x] Automated tests run — `pytest tests/test_admin_analytics_coverage.py`: **41 passed** (28 pre-existing unmodified + 13 new). Includes an explicit regression test that reproduces the original failure (`test_low_performers_are_off_the_default_page_but_still_counted`) alongside the two fixes that make them reachable.
- [x] `ruff check` + `ruff format` clean on both changed Python files.
- [x] **Real production build run** — `npm run build` in `admin-dashboard`, exit 0, full route table emitted (not just `tsc --noEmit`, which was also run separately and exited 0).
- [x] Blast-radius grep performed — searched `getDriverAcceptanceRates`, `driver-acceptance` across `*.ts`/`*.tsx`/`*.py` excluding `node_modules`/`.next`. Consumers listed in §4.
- [x] Reviewed against CLAUDE.md conventions: observability (warning log carries `extra={"domain": "admin"}`, no PII — driver ids only, no names/GPS in logs), "no silent caps", error handling (DB failures still raise 503, never softened).
- [x] Not feature-flagged — justified in §8.

## 10. What was NOT verified

- **Not tested against live Supabase.** All tests use mocked `db.get_rows`/`db.rpc`; the service-area filter now passed to `get_rows` as `{"service_area_id": ...}` is exercised only against the mock, which ignores filters. The PostgREST compilation of that filter is unverified in this change.
- **No visual regression check.** This repo has no snapshot/visual tooling for `admin-dashboard` (standing gap). The pagination, search box, row highlighting, and truncation banner were reasoned about and type-checked, not screenshotted.
- **`scan_truncated` at real scale is untested** — the cap path is covered by a synthetic 2000-driver fixture, never against a real fleet of that size.
- **Current live driver count is unknown to me**, so I cannot say whether this bug is actively biting today or was latent above 50 drivers. The code path is wrong either way.
- No load/latency measurement. Sorting and filtering are in-process over at most `_DRIVER_SCAN_CAP` rows; not benchmarked.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change — the UX field (§5) documents the intentional change to a shipped internal screen
