# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code (session: zoho-token-refresh-error) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/zoho-token-refresh-error-xykstq` |
| Related issue or gap ID | Found while investigating the Zoho credential-autofill incident (`2026-08-13-zoho-credential-autofill-overwrite.md`) |

## 1. Issue / gap identified

The Audit Logs page held the evidence that cracked the Zoho incident, but the
page itself could not surface it — the investigation had to be run in raw SQL.
Four concrete defects, the first of which is a genuine bug rather than a
polish item:

1. **Both filter dropdowns silently returned nothing.** Verified against
   production `audit_logs` (5 000+ rows, 81 distinct actions, 23 distinct
   entity types):

   | Filter | Offered | Rows it matches |
   |---|---|---|
   | Action → `created` / `updated` / `deleted` / `login` / `status_change` | 5 options | **0 for all five** |
   | Entity → `driver` | | 0 (stored as `drivers`, 763 rows) |
   | Entity → `promotion` | | 0 (stored as `promotions`) |
   | Entity → `service_area` | | 0 (stored as `service_areas`) |
   | Entity → `setting` | | 0 (stored as `settings`) |
   | Entity → `subscription` | | 0 (stored as `subscription_plans`) |
   | Entity → `user` / `ride` / `staff` | | partial — misses `users` (1 280), `rides` (860) |

   Every Action option and five of eight Entity options matched zero rows.
   The three "working" entity options each missed the majority of their own
   records because of the singular/plural split.

2. **No date range.** "What happened between 19:20 and 21:00" — the actual
   question in an incident — was not expressible in the UI.

3. **`details` was unreadable.** It is stored as a JSON string and was
   rendered truncated in a `max-w-xs` cell. `fields_changed` — the field that
   identified exactly which credentials were overwritten — was invisible.

4. **`request_id` was never displayed**, despite `utils/audit_logger.py`
   storing it specifically so an investigation can join an `audit_logs` row to
   its log lines and Sentry event.

## 2. Root cause

The filter lists and the action-colour map were hand-written constants
(`ACTION_CONFIG`, `ENTITY_ICONS`, and the literal `<SelectItem>` values) that
encoded a generic CRUD vocabulary the writers never adopted. Writers emit
domain verbs (`driver_approve`, `otp_sent`, `zoho_desk_config_updated`) and
plural table names.

The failure was silent because `db_supabase.get_rows` returns an empty list for
a filter value that exists nowhere — it does not raise. So a filter that
matched nothing was indistinguishable from a filter that legitimately had no
results. This is the same class of defect the code comment at
`maintenance.py:303-308` already documented once for `resource_id` ("a quiet
SOC search gap, not a crash") — the same trap, a different column, caught
again.

## 3. Fix / remediation

- **backend** — new `GET /api/admin/audit-logs/facets` returns the distinct
  `action` / `entity_type` values actually present, with counts, over a
  bounded window (same shape and 5 000-row cap as the existing top-actors
  rollup, and it flags `rows_scanned_capped` so counts are never misread as
  complete).
- **backend** — `GET /api/admin/audit-logs` accepts `start` / `end` ISO
  bounds on `created_at`, including open-ended ranges.
- **admin-dashboard** — both dropdowns are populated from the facets endpoint
  with counts shown, so they cannot drift from the data again.
- **admin-dashboard** — date-range inputs, widened to whole local days.
- **admin-dashboard** — rows expand to show pretty-printed `details`, full
  `entity_id`, `actor_id` + `actor_role`, and `request_id`. The collapsed row
  shows a one-line gist that prefers `fields_changed` / `updated_fields` /
  `changed_fields` / `note` / `reason`.
- **admin-dashboard** — badge colour is derived from the action string's shape
  rather than a whitelist, with security-relevant verbs (`pii_revealed`,
  `refresh_token_reuse_detected`, `*_ban`, `*_suspend`, lockouts, escalations)
  ranked first so they can't be visually buried. Entity icons match on a
  singular/plural-tolerant key.
- **admin-dashboard** — CSV export gains `actor_id`, `actor_role` and
  `request_id`.

## 4. Risk & impact on existing functionality

Blast radius: **isolated**, verified by grep rather than assumed.

- `getAuditLogs` has one caller: `src/app/dashboard/audit-logs/page.tsx`.
  `getAuditLogFacets` is new with one caller.
- `GET /api/admin/audit-logs` has no other client — no mobile surface, no
  background loop.
- `maintenance.py`'s other endpoints (GPS cleanup, driver-daily rollup, PII
  reveal) are untouched; the new endpoint adds no shared state.
- The `audit_logs` table is **read-only** on every path changed here. No
  writer, retention job, or RLS policy is affected.
- `require_module("audit")` gating is unchanged and applies to the new
  endpoint identically.
- Adding `start`/`end` params is backward compatible — omitted means
  unfiltered, pinned by `test_no_range_leaves_created_at_unfiltered`.

Interaction risk with money, rides, dispatch, insurance periods: **none** —
this surface neither reads nor writes those.

Residual risk: the facets endpoint scans up to 5 000 recent rows on page load,
one extra bounded query per visit to an internal-admin page. Same cost profile
as the top-actors card already on the page. Past the cap the dropdown reflects
the most recent 5 000 rows rather than the full 90 days; the response flags
this, though the current UI does not yet display the flag — noted below as
unverified/incomplete rather than claimed as done.

## 5. User-experience effect

Internal admin only, `audit` RBAC module. No rider, driver, or corporate-admin
exposure; nothing visible mid-session to anyone using the apps.

- Filters that previously returned an empty table now return real results.
  This is a **visible behaviour change to an already-shipped screen**: an
  admin who had learned "the Action filter shows nothing" will now get rows.
- Filter options are now long (81 actions) and count-labelled, sorted by
  frequency. The list is scrollable (`max-h-80`).
- Rows are clickable to expand — new affordance, previously static.
- No customer-facing copy or notification changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/maintenance.py` | Added `_FACET_SCAN_CAP`, `GET /audit-logs/facets`, `start`/`end` params on `GET /audit-logs` | Serve filter options from data; make time-window investigation possible |
| `backend/tests/test_admin_maintenance_coverage.py` | Added `TestAuditLogDateRange` (3) + `TestAuditLogFacets` (5); updated 2 existing calls for the new params | Regression cover |
| `admin-dashboard/src/lib/api/staff-subscriptions.ts` | `getAuditLogs` gains `start`/`end`; new `getAuditLogFacets` | Client for the above |
| `admin-dashboard/src/lib/api.ts` | Re-export `getAuditLogFacets` | Barrel consistency |
| `admin-dashboard/src/app/dashboard/audit-logs/page.tsx` | Facet-driven filters, date range, expandable rows, shape-based colours, richer CSV | The four defects above |
| `admin-dashboard/src/__tests__/dashboard/audit-logs.test.tsx` | New — 11 tests | Pin the fixed behaviour |
| `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx` | Added `Server` to the hand-maintained `lucide-react` mock | New icon import broke the smoke test |

## 7. Before / after

```tsx
// Before — options that matched 0 rows in a 5000-row table
<SelectItem value="created">Created</SelectItem>
<SelectItem value="updated">Updated</SelectItem>
<SelectItem value="driver">Driver</SelectItem>      {/* stored as "drivers" */}
<SelectItem value="promotion">Promotion</SelectItem> {/* stored as "promotions" */}
```

```tsx
// After — options come from the data, with counts
{facets.actions.map((a) => (
  <SelectItem key={a.value} value={a.value}>
    {humanizeAction(a.value)} ({a.count})
  </SelectItem>
))}
```

```tsx
// Before — the evidence, truncated into a JSON-string blob
<TableCell className="text-sm text-muted-foreground max-w-xs truncate">
    {log.details || "—"}
</TableCell>
```

```tsx
// After — gist collapsed, full JSON + request_id on expand
<TableCell className="... truncate">{detailsSummary(log.details)}</TableCell>
{/* expanded row */}
<pre>{formatDetails(log.details)}</pre>   // "fields_changed": ["client_id", ...]
<p>{log.request_id || "— (background job)"}</p>
```

```python
# Before — no way to bound the window
filters: Dict[str, Any] = {}
if action:
    filters["action"] = action
```

```python
# After
if start or end:
    bounds: Dict[str, Any] = {}
    if start:
        bounds["$gte"] = start
    if end:
        bounds["$lte"] = end
    filters["created_at"] = bounds
```

## 8. Rollback plan

Code-only, read-only, no migration and no live-data mutation — `git revert` is
a complete rollback. Reverting restores the previous (silently-empty) filters;
it cannot corrupt anything, because no path here writes.

Partial rollback is available without a redeploy if the facets query proves
expensive: the frontend already falls back to empty facet lists on a failed
request (the `.catch` sets `{actions: [], entity_types: []}`), so the endpoint
can be blocked at the edge and the page degrades to "All Actions" / "All
Entities" only, rather than breaking.

## 9. Verification performed

- [x] **Root cause verified against production data**, not inferred — queried
      `audit_logs` for distinct `action` / `entity_type` with counts and
      compared against the hardcoded lists. That's the source of the table in
      §1.
- [x] Backend tests — `pytest tests/test_admin_maintenance_coverage.py`, 30
      passed. New: date-range bounds (both/open-ended/absent), facet counting,
      real-world action names surviving verbatim, blank skipping, scan-cap
      flag, days window.
- [x] `ruff check` + `ruff format` clean on both backend files.
- [x] Frontend tests — 11 new in `audit-logs.test.tsx`, all passing; full
      `src/__tests__/dashboard/` suite green.
- [x] `npx tsc --noEmit` clean.
- [x] **Production build run** — `npm run build` for `admin-dashboard`,
      exit 0. (Not just a dev server or `tsc`.)
- [x] `npx eslint` — 0 errors. 2 warnings remain, both pre-existing
      `set-state-in-effect` on effects this change did not introduce.
- [x] Blast-radius grep — `getAuditLogs`, `getAuditLogFacets`,
      `getAuditLogTopActors`, `audit-logs`, `audit_logs` across
      `admin-dashboard/src` and `backend/`.

## 10. What was NOT verified

- **Not exercised against live Supabase.** Backend tests mock `get_rows`; the
  facets endpoint has never run against the real table, so its latency at the
  5 000-row cap is unmeasured. The production data in §1 came from direct SQL,
  not from calling the endpoint.
- **`rows_scanned_capped` is returned but not displayed.** If the 90-day
  window exceeds 5 000 rows, the dropdown silently reflects a recent sample.
  The flag exists for a future UI note; today a user cannot see it. Called out
  rather than left implicit.
- **No visual-regression check.** This repo has no snapshot/visual tooling for
  `admin-dashboard`, so the new expandable row and the wider filter bar were
  reasoned about and unit-tested, not screenshotted. Standing gap —
  `ACTION_ITEMS.md`.
- **Radix `Select` dropdown contents are not asserted open in jsdom.** The
  tests verify the facets are fetched and that the page uses them; they do not
  click the trigger and read the rendered option list.
- **Accessibility not audited** beyond adding `aria-label` / `aria-expanded`
  on the new controls. No `spinr-accessibility-reviewer` pass was run on the
  expandable-row pattern or the date inputs.
- **Timezone handling of the date range is browser-local**, converted to UTC
  ISO before being sent. An admin in a different timezone from the incident
  will see boundaries shifted accordingly; this was not tested across
  timezones.
