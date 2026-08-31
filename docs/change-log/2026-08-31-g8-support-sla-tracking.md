# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (local commit only — see session) |
| Related issue or gap ID | ACTION_ITEMS.md G8 |

## 1. Issue / gap identified

CLAUDE.md's KPI table states a P1 support-ticket response target (≤ 2 hours), but nothing in
code tracked or enforced it: `routes/admin/support_tickets.py` has no deadline/SLA field,
`core/lifespan.py`'s background loops had no breach sweep, and `utils/metrics.py` had no
`spinr_support_*` metric.

## 2. Root cause

The Zoho Desk integration (`services/zoho_desk_service.py`, mirror table `zoho_desk_tickets`
from migration 123) was built to proxy/mirror tickets for the admin dashboard, not to enforce
Spinr's own SLA policy. Zoho Desk has native SLA/due-date support, but it was never configured
(G8's own finding: zero "due"/"SLA" references in the integration code, and `due_date` in the
mirror is only ever populated from whatever Zoho happens to send — which is nothing, since no
Zoho-side SLA policy exists). The KPI target existed only as documentation with nothing
computing actual response time against it.

## 3. Fix / remediation

Built the code-side tracking/enforcement half only (the Zoho-console SLA policy config named
in G8 is out of scope for this session — no Zoho admin credentials/MCP tool available here;
G8 stays open for that half):

1. **Assumption stated, not silently resolved** (per CLAUDE.md's "Think before coding" rule):
   mapped CLAUDE.md's "P1" KPI row to Zoho's `priority == "Urgent"` — the only priority tier
   this codebase's own UI (`admin-dashboard/.../tickets/page.tsx`) treats as the top severity.
   No code anywhere names a literal "P1" priority value, so this mapping is a judgment call,
   documented at the top of `backend/utils/support_sla.py`. If the intended definition is
   different (e.g. also includes "High"), `P1_PRIORITIES` in that file is the single place to
   change it.
2. **Deadline anchor = ticket `created_time`**, not a distinct "first response" timestamp —
   confirmed via grep that neither `zoho_desk_service.py` nor the `zoho_desk_tickets` mirror
   schema (migration 123) carries a first-response field. `created_time + 2h` is a derived
   value computed on read, not stored (see `sla_deadline()`/`is_breached()` in
   `backend/utils/support_sla.py`), except for the one nullable claim-flag column below.
3. **New nullable column** `zoho_desk_tickets.sla_breach_alerted_at` (migration
   `377_zoho_desk_tickets_sla_breach_alerted.sql`) — additive, needed for the sweep loop's
   replay-safety (atomic per-ticket claim), not for the deadline computation itself.
4. **New metric** `spinr_support_ticket_sla_breach_total{priority}` — a counter, incremented
   once per breached ticket the sweep wins the claim on. Per CLAUDE.md's own Observability
   Conventions ("The metric names themselves are defined at their emitting call sites... —
   `utils/metrics.py` provides the underlying counter/gauge registry"), this metric name is
   **not** a new entry in `utils/metrics.py` itself (that file has no name registry to edit —
   confirmed by grepping every other `spinr_*_total` name in the repo, e.g.
   `spinr_dispatch_offer_sent_total` in `routes/rides/matching.py`: none of them appear as
   literals inside `utils/metrics.py`). It's emitted via the existing `metrics.inc(name,
   labels)` API from the new sweep loop, matching every other metric in the codebase.
5. **New background loop** `support_sla_breach_sweep (5min)`
   (`backend/utils/support_sla.py::support_sla_breach_sweep_loop`), spawned in
   `core/lifespan.py` and registered in `_WATCHDOG_LOOP_NAMES`. Every 5 minutes: query open,
   `Urgent`-priority tickets past `created_time + 2h` with `sla_breach_alerted_at IS NULL`;
   for each, atomically claim it (`UPDATE ... WHERE sla_breach_alerted_at IS NULL`) before
   logging `warning` and incrementing the metric.
   - **Log level = `warning`, not `error`.** CLAUDE.md's own guidance is ambiguous here on
     purpose ("use judgment... justify in the Change Impact Log"): a P1 SLA breach is a
     genuine user-visible failure (closer to "user-visible errors → Sentry + error log" in
     spirit), but it is not a code/infra failure — no exception, nothing crashed, and nothing
     here needs an on-call page the way a DB/auth/payment error does. It's an
     operational/staffing signal (closer to CLAUDE.md's reserved meaning of `error` as
     "actionable failures" in the DB/auth/payment sense). Landed on `warning` + metric,
     matching this file's "reserve `error` for DB/auth/payment" convention, while the new
     Prometheus counter (not the log line) is what actually makes the breach durably
     alertable — a log line alone would be easy to miss.
6. **Admin-dashboard**: additive "SLA" column on the existing ticket list
   (`admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx`), computed
   client-side from the ticket's own `createdTime`/`priority`/`status` — no new API call, no
   restructuring of the existing table. Shows "Due in Xh Ym" (amber) or "Breached Xh Ym ago"
   (red) for open P1 tickets; blank for everything else. Uses the same "Urgent" P1 mapping as
   the backend module (commented as such, so the two definitions don't silently drift).
7. **Tests**: `backend/tests/test_support_sla.py` (10 cases — deadline computation, priority
   filtering, closed-ticket exclusion, and the atomic-claim race: a lost claim must not
   double-count the metric). Updated `backend/tests/test_lifespan_watchdog_coverage.py`'s
   pinned loop count (40 → 41 total `_spawn()` calls) since it's a hard-coded regression
   pin that intentionally fails on any loop-count change.

## 4. Risk & impact on existing functionality

- **`zoho_desk_tickets` table**: read by `zoho_desk_db.py` (dashboard/trends/list/search) and
  written by `zoho_desk_sync.py` (upsert) and `zoho_ticket_service_area.py` (service-area
  assignment). The new column is additive/nullable and is never read or written by any of
  those three — grepped for `sla_breach_alerted_at` and confirmed it appears only in the new
  migration, `support_sla.py`, and its test. Zero risk of interfering with the existing
  upsert (`_upsert_batch` in `zoho_desk_sync.py` upserts a fixed column set from `_map_ticket`,
  which does not touch this column, so a sync run never clobbers a set claim flag).
- **New index** (`idx_zdt_sla_breach_pending`, partial on `sla_breach_alerted_at IS NULL`):
  additive, does not change any existing query plan for other index-eligible queries.
- **`utils/metrics.py`**: no changes — it's a generic registry, not touched. Blast radius:
  zero, since no existing call site references the new metric name.
- **`core/lifespan.py`**: one new `_spawn()` call + one new `_WATCHDOG_LOOP_NAMES` entry.
  The file's own self-check (a `RuntimeError` in production if spawned/watched sets don't
  match exactly) already guards against a mismatch; verified via the count-pinned test.
  Isolated — no existing loop's code was touched.
- **Admin dashboard tickets page**: only one file touched
  (`support-tickets/tickets/page.tsx`); the sibling ticket-detail page
  (`support-tickets/tickets/[id]/page.tsx`) and the older `support/_tabs/tickets.tsx` list
  were **not** touched — this session did not investigate whether `support/_tabs/tickets.tsx`
  is a duplicate/legacy list surface also worth this column; flagging it rather than silently
  leaving an inconsistency across the two surfaces unexamined. `colSpan` values on the loading
  and empty-state rows were bumped from 8 to 9 to match the new column count — the only
  existing-row-shape change in this diff.
- **Blast radius: isolated to the Zoho Desk help-desk surface** (backend loop + one new
  table column + one dashboard page). No ride/dispatch/payment/auth code touched.

## 5. User-experience effect

- **Internal admin only** (support-tickets module). No rider/driver/corporate-admin-facing
  change.
- The new "SLA" column appears immediately on page load/refresh for anyone with the
  `support_tickets` RBAC module — visible mid-session to an admin already on the page (next
  refresh/reload), since it's computed client-side with no new permission gate.
- No new notification/copy reviewed against the customer-centric tone standard — this
  doesn't touch rider/driver copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/377_zoho_desk_tickets_sla_breach_alerted.sql` | New nullable `sla_breach_alerted_at` column + partial index on `zoho_desk_tickets` | Idempotency claim flag for the sweep loop |
| `backend/utils/support_sla.py` | New module: `sla_deadline()`, `is_breached()`, `run_sweep()`, `support_sla_breach_sweep_loop()` | SLA computation + breach sweep, replay-safe |
| `backend/core/lifespan.py` | New `_spawn("support_sla_breach_sweep (5min)", ...)` call + `_WATCHDOG_LOOP_NAMES` entry | Register the new loop |
| `backend/tests/test_support_sla.py` | New test file (10 cases) | Deadline/breach logic + atomic-claim race coverage |
| `backend/tests/test_lifespan_watchdog_coverage.py` | Pinned spawned/watched loop counts bumped 40→41 / 39→40 | Regression pin intentionally updated for the new loop |
| `admin-dashboard/src/app/dashboard/support-tickets/tickets/page.tsx` | New "SLA" column (client-computed `slaStatus()`), `TableHead` import added, `colSpan` 8→9 | Surface SLA due/breach status next to priority/status |
| `ACTION_ITEMS.md` | G8 entry updated | Record what was/wasn't closed |

## 7. Before / after

Ticket list table — new column only, no existing column's rendering changed:

```
# Before
<TableCell><Badge ...>{t.priority}</Badge></TableCell>
<TableCell>{assignee...}</TableCell>

# After
<TableCell><Badge ...>{t.priority}</Badge></TableCell>
<TableCell>{sla ? <Badge>{sla.label}</Badge> : "—"}</TableCell>
<TableCell>{assignee...}</TableCell>
```

## 8. Rollback plan

- **Loop**: revert the two `core/lifespan.py` hunks (the `_spawn()` call and the
  `_WATCHDOG_LOOP_NAMES` entry) and redeploy — no data was mutated by the loop that isn't
  self-contained to the new column, so no cleanup is needed. This is a plain code revert
  because the loop's only "state" is the claim column, which reverting the code leaves inert
  (unused, never read again).
- **Column**: `ALTER TABLE zoho_desk_tickets DROP COLUMN IF EXISTS sla_breach_alerted_at;`
  (in the migration's own rollback comment) — safe at any time since nothing else reads it.
- **Dashboard column**: revert the one file; no server state involved.
- No feature flag was added — this is judged additive/observation-only (a new metric, log
  line, and read-only UI column; nothing here changes any existing write path, blocks any
  action, or is user-visible outside the admin support-tickets page), so per CLAUDE.md's
  gate #3 ("prefer additive/flagged rollout... new/changed UX") a flag was not added because
  there is no changed behavior to gate — only new, silent-by-default observability. If this
  judgment is wrong, the rollback above is a same-deploy-cycle code revert, not a second
  migration.

## 9. Verification performed

- `cd backend && python3 -m pytest tests/test_support_sla.py tests/test_lifespan_watchdog_coverage.py -q --no-cov` — 17 passed.
- `cd backend && ruff check utils/support_sla.py core/lifespan.py tests/test_support_sla.py tests/test_lifespan_watchdog_coverage.py` — all checks passed.
- `cd admin-dashboard && npm install` (fresh — `node_modules` did not exist in this worktree)
  then `npm run build` (real production build via `next build`, not `tsc --noEmit` alone or a
  dev server, per CLAUDE.md's explicit requirement) — **passed**: "Compiled successfully",
  TypeScript check passed, and the full route table was generated including
  `/dashboard/support-tickets/tickets`, with zero error/failure lines in the log. (A bare
  `npx tsc --noEmit -p .` was tried first and failed on two pre-existing, unrelated
  `@testing-library/jest-dom` / `vitest/globals` type-definition errors that exist on a clean
  checkout independent of this diff — `npm run build`'s own internal TypeScript pass, which is
  what actually gates a deploy, does not hit them and is the result recorded here.)
- Manually traced the migration's additivity: grepped every reader/writer of
  `zoho_desk_tickets` (`zoho_desk_db.py`, `zoho_desk_sync.py`, `zoho_ticket_service_area.py`)
  and confirmed none references the new column.

## 10. What was NOT verified

- **Not run against a live Supabase** — the new column/migration was not applied to any real
  database in this session (no `DATABASE_URL` here); only reasoned about via the migration
  file and existing schema conventions (nullable additive column, matches migration 123's own
  pattern of adding columns to this same table in migration 200).
- **No live Zoho Desk account** was exercised — `run_sweep()`'s DB-layer logic is unit-tested
  against mocked `db_supabase.get_rows`/`update_one`; the end-to-end path (Zoho → sync loop →
  mirror → sweep → metric) was not exercised against a real Zoho org.
- **No visual regression tooling** for admin-dashboard exists (per CLAUDE.md's standing note:
  `e2e/visual-regression.spec.ts` has zero committed baselines, ACTION_ITEMS.md B38) — the new
  "SLA" column was reasoned about, not screenshotted.
- **The P1 = "Urgent" mapping is this session's judgment call**, not a confirmed product
  decision — flagged explicitly in both `support_sla.py`'s docstring and the admin-dashboard
  comment; if wrong, `P1_PRIORITIES` in `support_sla.py` and `SLA_PRIORITIES` in
  `tickets/page.tsx` are the two places to change (kept in sync by comment cross-reference,
  not by a shared constant, since the two run in different languages/processes).
- **Whether `support/_tabs/tickets.tsx` (the older/alternate ticket list surface) also needs
  this column** was not investigated — see §4.
- **The Zoho-console-side SLA/due-date policy config itself remains completely undone** — this
  change only builds Spinr's own independent detector; it does not read or depend on any Zoho
  native SLA feature. See ACTION_ITEMS.md G8 for that still-open half.
