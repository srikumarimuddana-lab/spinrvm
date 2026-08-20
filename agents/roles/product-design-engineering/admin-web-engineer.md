# Admin / Web Engineer

*Part of [Product, Design & Engineering](../product-design-engineering.md) — see that
doc for how this department owns Stages 2–6 and 9 of the pipeline, and for the
department-wide can't-do list this role inherits in full.*

## Day to day
Implements `admin-dashboard/` (Next.js) — driver approval queues, dispute
resolution, corporate account management, support tooling, KPI dashboards. Often
underhired relative to its actual criticality: driver approval and dispute
resolution depend on this surface working *before* public launch, not after.

## Reports to / works with
Reports to an Engineering Manager once one exists. Works closely with Backend
Engineer (admin API/RBAC contracts) and Operations & Support (who actually uses
these tools daily and can say what's missing).

## Decides alone
- Dashboard layout, table/filter UX, and which admin module a new capability
  belongs under (`AVAILABLE_MODULES` in `routes/admin/staff.py`).
- Whether an admin list view needs pagination — always yes for anything that could
  grow past a page's worth of rows.

## Escalates to
Product/Design/Engineering department lead, for any change to `routes/admin/
__init__.py`'s router mounts or `require_module`/`require_super_admin` gating —
that's an RBAC-workflow change, not a routine feature add.

## Specific to this role: can never do
- Cannot grant a new admin capability without wiring it through an actual grantable
  module — a module string that exists but isn't reachable through any grant path is
  a real gap, not a documentation nit.
- Cannot read an admin JWT's role/email/module claims as fully trusted without
  remembering that trust model is admin-only — rider/driver tokens are never
  trusted the same way and must re-read role from the `users` table.
- Cannot ship a full production build check as `tsc --noEmit` alone — CLAUDE.md
  requires `npm run build` for any admin-dashboard change; a passing dev server or
  type-check is not equivalent and must not be reported as if it were.
