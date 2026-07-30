# Change Impact & Risk Log — Weekly/monthly driver earnings statements

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code (requested by operator) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | drivers (email/report), payments-adjacent (reads payouts) |
| PR / commit link | branch `claude/stripe-sync-t4-generation-shcvce` (statements commits) |
| Related issue or gap ID | Operator request: Uber-style periodic earnings/payout reports |

## 1. Issue / gap identified

Drivers have no periodic earnings/payout report — no weekly or monthly
statement email like Uber/Lyft send — and admins have no way to produce one
for a driver (support requests, disputes, record-keeping).

## 2. Root cause

Feature never existed. Earnings data is only visible live in the driver app;
nothing summarizes a closed period or delivers it as a document.

## 3. Fix / remediation

New periodic-statement pipeline, all additive:

- **Builder** (`utils/driver_statement.py`): aggregates one (driver, period) —
  ride earnings (incl. tips), incentives, bonuses, cancellation fees, GST/PST
  collected, payout list with instant fees. Anchored weekly (Mon–Sun) /
  monthly periods in America/Regina, plus admin-only custom date ranges
  (≤366 days). Math mirrors the app's Earnings tab.
- **PDF** (`utils/driver_statement_pdf.py`): branded one-pager, same chrome
  as the T4A slip.
- **Loop #17** (`utils/driver_statement_job.py`, wired in
  `core/lifespan.py`): every 30 min ensures the latest completed weekly +
  monthly periods have statements; builds, renders, emails.
- **Ledger** (`migrations/272_driver_statements.sql`): one row per
  (driver, period); the INSERT is the replay-safety claim (unique index), so
  a statement can never email twice across replicas.
- **Driver self-serve** (`POST /api/v1/drivers/statements/email`): re-request
  any anchored period, 6/hour rate limit (shared tax-doc limiter).
- **Admin API** (`routes/admin/driver_statements.py`): ledger list, PDF
  download for anchored period or date-filtered range, send-to-driver email.
- **Admin UI** (`admin-dashboard/.../driver-statements-panel.tsx`): a
  statements panel inside the driver detail Sheet's **Payouts tab** — sent
  statements with per-row Download/Resend, plus a From/To date filter with
  Download PDF and Email-to-driver actions.

## 4. Risk & impact on existing functionality

- **Reads only** from `rides`, `driver_bonuses`, `ride_incentive_claims`,
  `payouts`, `users`; **writes only** the new `driver_statements` table. No
  existing table, state field, or money value is mutated — no interaction
  with ride state, wallet deltas, or Stripe.
- **Background loops**: adds loop #17 to `lifespan.py` + the watchdog list.
  Replay-safety = insert-claim on the migration-272 unique index (recipe's
  claim-flag option). Crash-after-claim rows stay `claimed` and are never
  auto-retried (double-send guard) — visible in the admin ledger.
- **Email volume**: one email per active driver per week + per month.
  Inactive periods are skipped (`skipped_inactive`), so dormant drivers get
  nothing. First tick after deploy catches up the latest completed periods
  only (no deep backfill).
- **Shared consumers touched**: `routes/drivers/tax_exports.py` gains one
  endpoint + one background helper (existing T4A/CSV/DSAR endpoints
  untouched — their tests still pass); `routes/admin/__init__.py` gains one
  mount. `tax_doc_email_limit` is now shared by three senders — a driver
  hammering statement emails can exhaust the same 6/hour budget as T4A
  emails (accepted: it's the same "tax document" category).
- Blast radius: single-surface (backend), fully additive.

## 5. User-experience effect

- **Driver**: new weekly (Monday) and monthly (1st) statement emails with a
  PDF — new outbound copy, written to the customer-tone standard; visible in
  the inbox, not mid-session in-app. Drivers with no email on file are
  recorded and skipped.
- **Internal admin**: new "Earnings statements" panel in the driver detail
  Sheet's Payouts tab (between the Stripe/KYC block and the payout-history
  table) — list of sent statements, per-row Download/Resend, and a From/To
  date filter with Download PDF / Email-to-driver. Additive: no existing
  control on that tab changed, and the tab's other data still loads through
  the same `getDriverPayoutsSummary` call as before. The panel fetches its
  own list on mount, so opening the Payouts tab now issues one extra
  request per driver.
- **Rider / corporate**: no change.
- New notification copy: statement email subject/body (this log is its
  review record).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_statement.py` | NEW — period math + aggregation | Statement data |
| `backend/utils/driver_statement_pdf.py` | NEW — branded PDF | The document |
| `backend/utils/driver_statement_job.py` | NEW — 30-min loop, insert-claim, email | Scheduled delivery |
| `backend/core/lifespan.py` | Spawn loop + watchdog registration | Loop #17 |
| `backend/migrations/272_driver_statements.sql` | NEW table + unique claim index, RLS | Ledger + replay safety |
| `backend/routes/drivers/tax_exports.py` | + self-serve statement email endpoint | Driver can always re-request |
| `backend/routes/admin/driver_statements.py` | NEW — list / pdf / email endpoints | Admin download + send |
| `backend/routes/admin/__init__.py` | Mount statements router (drivers module) | Routing |
| `CLAUDE.md` | Loop inventory 16 → 17 | Keep doc accurate |
| `admin-dashboard/src/lib/api/drivers.ts` | + list/download/email statement client fns | API access |
| `admin-dashboard/src/lib/api.ts` | Re-export the three fns + types | Barrel |
| `admin-dashboard/.../_components/driver-statements-panel.tsx` | NEW — statements panel | Admin UI |
| `admin-dashboard/.../drivers/page.tsx` | Render panel in Payouts tab; pass driverId + toast | Wiring |
| `backend/tests/…` (5 files) + panel test | 38 backend + 8 UI tests | Regression pins |

## 7. Before / after

Purely additive — no existing behavior changed (skipped per template rule).
The one shared-surface note: `get_t4a_summary` and the DSAR export in
`tax_exports.py` are untouched by the new endpoint added to the same file.

## 8. Rollback plan

No redeploy needed to stop sends: the loop only acts when a period lacks
rows, so **pre-inserting rows is the kill switch** —

```sql
-- Stop all future statement emails without a deploy (run per period as it
-- becomes due, or leave in place after removing the loop in the next deploy):
INSERT INTO driver_statements (id, driver_id, period_type, period_start, period_end, status)
SELECT 'stmt-weekly-' || to_char(date_trunc('week', now() at time zone 'America/Regina')::date - 7, 'YYYY-MM-DD') || '-' || d.id,
       d.id, 'weekly',
       date_trunc('week', now() at time zone 'America/Regina')::date - 7,
       date_trunc('week', now() at time zone 'America/Regina')::date - 1,
       'skipped_inactive'
FROM drivers d
ON CONFLICT DO NOTHING;
```

(Equivalent statement for `monthly`.) Emails already delivered cannot be
recalled — but statements are informational documents built from live data,
so a wrong statement is corrected by fixing the data and re-sending from the
admin screen, not by data remediation. Table rollback: `DROP TABLE
driver_statements` (migration header).

## 9. Verification performed

- [x] Automated tests: 38 new unit tests across builder (period math incl.
  leap-February, Regina offset, aggregation, custom ranges), PDF (bytes,
  empty-input, variants), job (claim conflict skips all work, non-unique DB
  error surfaces, sent/failed/skip paths, period selection), driver email
  endpoint, admin endpoints (anchored + date-range download, email, 404/422)
  — plus existing `test_t4a_email.py`, `test_p2_payout_t4a.py`,
  `test_dsar_export.py`, `test_replay_safety_payment_loops.py` suites re-run
  green (shared-file blast radius).
- [x] Sample PDFs rendered and visually reviewed (two fpdf2 layout bugs found
  and fixed during review: right-margin note clipping, stray blank page from
  footer/auto-page-break interaction).
- [x] **Admin-dashboard production build run** (`npm run build`) — passes.
  (It first failed on a pre-existing unrelated error: `motion/react`
  unresolved in `monitoring/alert-feed.tsx`. Root cause was a stale local
  `node_modules` — `motion@^12.43.0` is declared in package.json but was not
  installed; `npm install` fixed it. No code change was needed and none was
  made to that file.) `npx tsc --noEmit` and `eslint` also run: zero errors
  in the changed files. The one eslint warning in the new panel
  (`react-hooks/set-state-in-effect` on the fetch-on-mount effect) is the
  same warning the sibling `driver-notes.tsx` / `driver-timeline.tsx`
  components already produce — matched the established pattern rather than
  diverging.
- [x] Admin UI unit tests (vitest, 8): list rendering with totals, per-row
  download by anchored period, resend + list reload, custom-range download
  using the date inputs, inverted-range guard (button disabled, no API
  call), load-failure toast, download-failure toast, empty state.
- [x] Blast-radius grep: consumers of `payouts`, `driver_bonuses`,
  `tax_doc_email_limit`, `_email_driver_document` enumerated in §4.
- [x] Reviewed against CLAUDE.md conventions: background-loop replay-safety
  recipe (claim via unique-index insert), Decimal-only money, no PII in logs
  (ids only), errors surface loudly (provider-rejected email → error log +
  failed status / 502), RLS on the new table, migration numbered + reversible.
- [ ] Manual staging run: NOT performed (no staging environment in this
  session). Recommend: apply migration 272, let one tick run, verify one
  driver's `sent` row + received email before considering the loop live.

## What was NOT verified

- Not tested against live Supabase/SES/Resend — mocked clients only (repo
  unit-test convention). Real email deliverability (attachment size, SES
  identity) needs the staging check above.
- Admin UI was **not** exercised against a running backend or in a browser —
  the panel's tests mock the API module, and no visual/snapshot regression
  tooling exists for the admin dashboard (standing gap, `ACTION_ITEMS.md`),
  so the rendered layout inside the Payouts tab was reasoned about, not
  screenshotted. First staging pass should open a driver's Payouts tab and
  confirm the panel renders and a download returns a valid PDF.
- The DSAR export reads a `driver_payouts` table while payout flows write
  `payouts` — pre-existing inconsistency spotted during this work, NOT
  fixed here (out of scope); flagged for `ACTION_ITEMS.md`.
- Timezone choice is fixed America/Regina; a future multi-province expansion
  that wants local-week statements needs a per-service-area variant.
