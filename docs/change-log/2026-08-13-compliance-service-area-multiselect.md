# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code (agent session), on branch `claude/compliance-tax-service-area-multiselect-fd7c3r` |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | `8a9c796` (backend), `e74b8fc` (backend tests), `72cde31` (admin-dashboard) |
| Related issue or gap ID | Product request: "in Compliance & Tax Reporting I need the service area dropdown with multi select option in it" |

## 1. Issue / gap identified

Compliance & Tax Reporting (`/dashboard/records` → Compliance Reports tab, backed by
`admin-dashboard/src/app/dashboard/compliance/page.tsx`) had no way to scope a report to
one or more service areas. Every export covered the whole platform, so producing a
per-area figure — an SGI or Knight Archer invoice reconciliation for one city, a
per-jurisdiction GST/PST number, an airport authority's trip count — meant exporting
everything and filtering the spreadsheet by hand.

## 2. Root cause

Not a bug; a missing capability. `rides.service_area_id` and `drivers.service_area_id`
have both existed for a long time (migrations 159/164/165/194 already scope analytics and
payouts by them), but `routes/admin/compliance.py` was written without an area parameter
and never gained one. The admin UI kit also had no multi-value control at all — Radix's
`Select` primitive is single-value by design, and the kit ships no `Command`/`Popover`
to build one on — so no page in the dashboard offered multi-select filtering.

## 3. Fix / remediation

A single page-level **Service Area** multi-select above the report tabs, applied to five
of the six reports. Empty selection means every area, which is the pre-existing behaviour
of all five.

Scoping differs by what each report's source table actually knows, which was a deliberate
product decision, not an implementation detail:

| Report | Scoped by | Why |
|---|---|---|
| GST/PST Remittance | `rides.service_area_id` | A per-jurisdiction tax number must follow where the ride was dispatched. |
| Airport Trips | `rides.service_area_id` | The authority invoices for trips touching its airport, not for where the driver lives. |
| Driver Roster | `drivers.service_area_id` | The report's source table *is* `drivers`. |
| SGI Insurance Billing | `drivers.service_area_id` | Billed against the driver's policy; `driver_period_distances` has no area of its own. |
| Knight Archer Insurance Billing | `drivers.service_area_id` | Same. |
| **T4A Filer Handoff** | **not scoped** | A T4A / Part XX.1 return is per-driver and Canada-wide. An area-scoped slice would split a driver working across two areas into two partial exports and under-report them in both. |

Driver-home-area scoping for the insurance reports was chosen over per-ride resolution to
match the convention migrations 164/165/194 already use, so a given area's numbers
reconcile across analytics, payouts and these exports. The trade-off is real and stated on
the document: a driver who works outside their home area bills entirely to the home area.

Every report now prints its area scope into the generated PDF/CSV/XLSX/DOCX subtitle,
including the unfiltered `All service areas` case. This is the load-bearing safety
property: a filtered regulatory export that does not say it is filtered reads as a
complete one once it has been emailed to SGI or an airport authority.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the Compliance module.** Grepped and enumerated, not assumed:

- **Backend.** `_gst_pst_rows`, `_airport_trips_rows`, `_driver_roster_rows`,
  `_insurance_billing_detail_rows` and the two new helpers
  (`_parse_service_area_ids`, `_service_area_scope_label`, `_driver_ids_in_service_areas`)
  are referenced only from `backend/routes/admin/compliance.py` and its two test files.
  `grep -rn` across `backend/` for each name returns no other module.
- **Frontend.** The five `download*` helpers in `lib/api/data-transfer.ts` are re-exported
  through the `lib/api.ts` barrel but imported by exactly one consumer,
  `dashboard/compliance/page.tsx` (plus the new test). No other page calls them.
- **New component.** `components/ui/multi-select.tsx` is new with one consumer. It cannot
  regress anything because nothing else imported it before.
- **Page hosting.** `CompliancePage` is rendered both at `/dashboard/compliance` and,
  more importantly, embedded by `dashboard/records/page.tsx` (the "Records & Compliance"
  shell that the sidebar actually links to). Both surfaces get the new control; that is
  intended, and the embedded case was covered by the production build.

**No schema change.** `rides.service_area_id` and `drivers.service_area_id` are read-only
here. No migration, no RLS change, no new table.

**No money movement.** These are read-only reports. Nothing calls
`corporate_wallet_apply_delta`, touches a wallet, or writes a ride/driver row. No Stripe
path is involved. The insurance reports compute a billed amount for display, using the
existing `Decimal`-only arithmetic, untouched by this change.

**No background loop interaction.** None of the 18 loops in `core/lifespan.py` calls
anything in `routes/admin/compliance.py`.

**No ride state machine or insurance-period interaction.** `driver_period_distances` rows
are read, never written; period classification logic is untouched.

**The regression this change could plausibly have caused, and the guard against it.**
`driver_period_distances` has no service-area column, so the insurance reports resolve
driver ids first and pass them as `{"$in": ids}`. If no driver matches the selected areas,
an unguarded implementation leaves an empty `$in`, which per CLAUDE.md's query-filter
conventions **widens back to every row** — the export would silently bill the insurer for
exactly the areas the admin excluded. The code returns an empty report before issuing the
distances query at all, and
`test_insurance_billing_returns_empty_when_no_driver_is_in_the_selected_areas` asserts
both the empty result *and* that `driver_period_distances` is never queried.

**Second guard: no silent cap.** The driver-id resolution is bounded by the module's
existing `_ROW_LIMIT` (10,000). Hitting it would shorten the driver set and make the
billing total *under*-report. That truncation propagates into the report's own
`⚠ TRUNCATED` marker rather than being absorbed
(`test_area_driver_scope_truncation_propagates_to_the_report`).

**Interaction with the dual-approval export gate** (`app_settings.dual_approval_exports_enabled`,
migration 268, currently default-off). `service_area_ids` is now part of the gate's params
key, so an approval granted for one area selection is not reusable for a different one —
correct, since it is a different export. The id list is deduped and sorted before it
reaches that key so `{b,a}` matches an approval already granted for `{a,b}` rather than
demanding a second approval for the same export.

**Audit trail.** `compliance_export_events.params` now carries `service_area_ids` on all
five reports (`null` when unscoped, explicitly recorded rather than omitted). Area ids are
not PII, so no PIPEDA concern. An existing consumer reading `params` as a free-form JSON
blob sees one additional key.

## 5. User-experience effect

- **Who sees a difference:** internal admins with the `compliance` module grant (or
  super_admin). No rider, driver, or corporate-admin surface is touched.
- **Visible mid-session:** no. This is an admin reporting page; there is no rider mid-ride
  or driver-online equivalent. The worst case for an admin mid-task is that a page reload
  drops an unsubmitted area selection, which is not persisted by design.
- **Default behaviour unchanged.** An admin who never opens the control gets a
  byte-identical export to before — with one intended exception: the subtitle now reads
  `Period: … — All service areas` instead of `Period: …`. That is an additive statement of
  scope on a document, deliberate, and the reason it is stated even in the unfiltered case
  is so the absence of a filter is affirmative rather than inferred.
- **Copy changes:** the new filter card's explanatory line, and a new paragraph on the T4A
  tab explaining why the filter does not reach that report. Both are specific and
  non-technical about consequence ("Currently exporting: …"), and neither is a
  notification.
- **Failure state:** if `/api/admin/service-areas` fails, the page shows
  "Could not load service areas — reports will cover every area. Reload to retry."
  rather than an empty dropdown, which would be indistinguishable from "no areas exist".

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/compliance.py` | Added `service_area_ids` query param to 5 endpoints; added `_parse_service_area_ids`, `_service_area_scope_label`, `_driver_ids_in_service_areas`; threaded the scope into the 4 row-builders, the subtitles, the audit-log params and the approval-gate key | The filter has to reach the actual query, and the resulting document has to state its own scope |
| `backend/tests/test_compliance_reports.py` | +18 unit tests (parse helper, scope label incl. lookup-failure fallback, per-report filter placement, empty-driver-set guard, truncation propagation) | The empty-`$in` widening case would misstate an insurer invoice; it needs a test, not a comment |
| `backend/tests/test_compliance_reports_http.py` | +9 HTTP tests (param reaches the query, audit row records it, subtitle states scope filtered and unfiltered, insurance labels home-area scoping, T4A ignores the param) | Covers the route wiring the unit tests deliberately skip |
| `admin-dashboard/src/components/ui/multi-select.tsx` | **New.** Checkbox-list dropdown on `DropdownMenuCheckboxItem` | The kit had no multi-value control; Radix `Select` is single-value by design |
| `admin-dashboard/src/lib/api/data-transfer.ts` | Added `serviceAreaParams()` and an optional `serviceAreaIds` arg to the 5 scoped download helpers | Param must be *omitted* when empty, not sent blank |
| `admin-dashboard/src/app/dashboard/compliance/page.tsx` | Page-level Service Area card above the tabs; fetches areas; passes the selection into all 5 downloads; T4A exclusion note | The requested control |
| `admin-dashboard/src/__tests__/api/compliance-service-area-params.test.ts` | **New.** 18 vitest cases pinning the query-string contract for all 6 helpers | Runnable frontend verification, since E2E cannot run in this environment (see §9) |
| `admin-dashboard/e2e/compliance.spec.ts` | +5 Playwright cases; service-areas mock added to the shared helper | Covers the control itself: default state, menu staying open across ticks, page-level scope reaching a second tab, clear, fetch-failure message |

## 7. Before / after

Backend — the guard that makes an area-scoped insurance invoice safe:

```python
# Before — no area scoping at all
distances = await db_supabase.get_rows(
    "driver_period_distances",
    {
        "period": {"$in": [2, 3]},
        "started_at": {"$gte": start_date.isoformat(), "$lte": end_date.isoformat()},
    },
    ...
)
```

```python
# After — id-first two-table lookup, with the empty case guarded
distance_filters: dict = {
    "period": {"$in": [2, 3]},
    "started_at": {"$gte": start_date.isoformat(), "$lte": end_date.isoformat()},
}
if service_area_ids:
    scoped_driver_ids, area_scope_truncated = await _driver_ids_in_service_areas(service_area_ids)
    if not scoped_driver_ids:
        # An empty `$in` would widen back to every driver and bill the
        # insurer for the areas the admin explicitly excluded.
        return [], Decimal("0"), area_scope_truncated, []
    distance_filters["driver_id"] = {"$in": scoped_driver_ids}

distances = await db_supabase.get_rows("driver_period_distances", distance_filters, ...)
```

Subtitle — the export now states its own scope:

```
# Before
Period: 2026-07-01 to 2026-07-31

# After (unfiltered)
Period: 2026-07-01 to 2026-07-31 — All service areas

# After (filtered)
Period: 2026-07-01 to 2026-07-31 — Service areas: Saskatoon, Regina
```

Frontend — omitted, not empty:

```ts
// Before
const sp = new URLSearchParams({ ...dateWindowParams(dateFrom, dateTo), format });
```

```ts
// After — an empty selection contributes no key at all, so the backend
// takes its untouched "every area" path
function serviceAreaParams(serviceAreaIds?: string[]): Record<string, string> {
    if (!serviceAreaIds?.length) return {};
    return { service_area_ids: serviceAreaIds.join(",") };
}
const sp = new URLSearchParams({
    ...dateWindowParams(dateFrom, dateTo),
    ...serviceAreaParams(serviceAreaIds),
    format,
});
```

## 8. Rollback plan

**No feature flag, and no data-level remediation is needed — deliberately.** The
justification, per the template's "explicitly state why none of the above applies":

1. The change is **purely additive and opt-in**. Omitting `service_area_ids` — which is
   what every caller other than the new UI control does, including any bookmarked report
   URL — produces the exact query and the exact rows the endpoint produced before.
2. These are **read-only report endpoints**. Nothing is written to live data: no wallet
   delta, no Stripe charge, no ride-state mutation, no insurance-period row. There is no
   "already applied to live data" class of damage for a rollback to undo, which is the
   specific case CLAUDE.md says `git revert` cannot cover.
3. Immediate mitigation with **no deploy at all**: an admin stops selecting areas. The
   control defaults to empty on every page load and the selection is not persisted, so a
   reload restores pre-change behaviour.
4. Frontend-only revert with no backend deploy: revert `72cde31` and redeploy Vercel; the
   backend param becomes unused and inert.
5. Full revert: `git revert 72cde31 e74b8fc 8a9c796`. Safe in any order — no migration,
   no schema change, nothing to un-apply.

The one artefact that outlives a rollback is `compliance_export_events` rows carrying a
`service_area_ids` key in their `params` JSON. That is an append-only audit table and an
extra JSON key is not a corruption; no cleanup is required or wanted.

## 9. Verification performed

- [x] **Automated tests run.**
  - Backend unit + HTTP: `pytest tests/test_compliance_reports.py
    tests/test_compliance_reports_http.py tests/test_compliance_rate_limit.py` →
    **84 passed** (57 pre-existing, all still green; 27 new).
  - Frontend unit: `npx vitest run` → **182 passed / 22 files**, including the 18 new
    cases. No pre-existing test regressed.
  - `ruff check` + `ruff format` clean on all changed Python.
  - `npx tsc --noEmit` clean; `npx eslint` clean on all changed TS/TSX.
- [x] **Real production build run** (CLAUDE.md requires this explicitly for any
  `admin-dashboard` change): `npm run build` → exit 0, all routes compiled. This is a real
  `next build`, not a dev server and not `tsc --noEmit` alone.
- [x] **Blast-radius grep performed.** Searched `backend/` for each of the 7 backend
  helper names; searched `admin-dashboard/src` for all 5 `download*` helpers, for
  `compliance/page` importers, and for importers of the new `multi-select` component.
  Results enumerated in §4.
- [x] **Reviewed against CLAUDE.md conventions.** Query-filter rules (id-first two-table
  lookup, empty-`$in` guard, no `re.escape` — no `$regex` used here); no-silent-cap rule
  (truncation propagates); no-silent-error-swallowing (the scope-label DB failure is
  `logger.error` with `exc_info`, and degrades to printing ids rather than hiding the
  scope); PIPEDA (area ids only, no PII added to logs or audit params); money (no
  arithmetic changed, `Decimal` path untouched).
- [x] **Not feature-flagged — justified.** CLAUDE.md gate 3 asks for a flag on
  user-visible non-trivial change or a shared component used by 3+ pages. This is an
  opt-in filter on an internal-admin-only reporting page, defaulting to the exact prior
  behaviour, with a brand-new component that has exactly one consumer. The "flip the flag
  off" mitigation is already available to the admin at zero latency: deselect the areas.

## 10. What was NOT verified

Stated explicitly rather than left to silence:

- **The 5 new Playwright E2E cases were written but never executed green.** The
  `compliance.spec.ts` suite cannot run in this container: `e2e/auth.setup.ts` stores an
  admin whose session carries `role: 'admin'` with no `modules` array, so
  `useRequireModule("compliance")` denies and the page never renders. **This was confirmed
  to be pre-existing, not caused by this change** — the unmodified baseline was stashed
  and the pre-existing test `page loads on the GST/PST Remittance tab by default` fails
  identically on it. Per CLAUDE.md gate 8 this is a decayed gate worth a `[CR]`, not
  something to force green here; flagging it rather than silently reporting E2E coverage
  I do not have. The frontend behaviour is instead covered by the 18 runnable vitest
  cases, which pin the query-string contract but **do not exercise the React component's
  rendering or the dropdown's open/tick interaction**.
- Playwright additionally needed a browser override to run at all in this image (the repo
  pins a build the image lacks); a scratchpad config pointed it at the installed Chromium.
  The repo's own `playwright.config.ts` was not modified.
- **No visual-regression coverage for this page.** `e2e/visual-regression.spec.ts` exists
  in this repo but does not cover `/dashboard/compliance` or `/dashboard/records`. The new
  filter card's appearance was reasoned about and confirmed to compile, not screenshotted
  or diffed. Per CLAUDE.md gate 6, flagging this rather than implying visual coverage.
- **No accessibility audit tooling was run** against the new `MultiSelect`. It inherits
  Radix's roles/keyboard handling and carries an explicit `aria-label`, and the E2E cases
  address it by `getByRole('menuitemcheckbox')`, but no axe/`a11y-baseline.json` check was
  executed for it. WCAG 2.1 AA is a stated obligation for customer-facing surfaces; this
  is an internal admin surface, but the gap is real and unmeasured either way.
- **Not tested against live or staging Supabase.** All backend tests mock
  `db_supabase.get_rows`. In particular, that `rides.service_area_id` and
  `drivers.service_area_id` are populated in production — as opposed to widely NULL — was
  **not** verified against real data. If either column is sparsely populated, a scoped
  report will legitimately return fewer rows than an operator expects. That is a data
  question, not a code one, and it is the single thing most worth checking in staging
  before an operator relies on a scoped export for a real invoice.
- The generated PDF/XLSX/DOCX files were **not** opened and read to confirm the new
  subtitle line renders legibly at width; the tests assert the subtitle string reaches the
  renderer, not how `report_branding` lays it out.
- The dual-approval export gate path (`dual_approval_exports_enabled = true`) was not
  exercised with an area selection. The flag is default-off in production, and the params
  dict flows through unchanged apart from the extra key, but the 202-approval flow was not
  re-run end to end.
