# Change Impact & Risk Log — admin screen for unpaid rides

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code session, on the repository owner's request |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | payments / admin |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | follow-on from `2026-09-21-uncollected-rides-stay-payable.md` |

## 1. Issue / gap identified

`GET /api/admin/rides/unpaid` returns every completed ride whose fare was never collected — rider
name and phone, driver, fare, retry count — and **nothing in the admin dashboard calls it.**
Grepped `admin-dashboard/src`: zero references, no API client function, no page.

So the only way an admin learns about an unpaid ride is a push notification or a live-dashboard
broadcast at the moment `payment_retry` gives up (`utils/payment_retry.py`). Miss that moment and
the ride is invisible: there is no list to work through, no total, and no way to find the ones from
last week. The data, the recovery tool (a payable Stripe invoice) and the alerting all exist. The
queue does not.

This matters more now that the policy is explicit: Spinr pays the driver and absorbs the failed
charge, so the outstanding amount is a **receivable against the rider** that someone has to chase.
An unchased receivable with no screen is just a loss.

## 2. Root cause

The endpoint shipped with its own docstring describing the admin workflow ("Admin must either
resolve payment manually or waive via…") and the UI for that workflow was never built. Nothing
failed — the gap is invisible to tests, because an endpoint with no caller still passes every test
it has.

## 3. Fix / remediation

- `admin-dashboard/src/lib/api/rides.ts` — `getUnpaidRides({ limit, offset })` plus the `UnpaidRide`
  row type, matching the endpoint's actual response (`{ rides, count }`), re-exported from
  `lib/api.ts` alongside the existing ride helpers.
- `admin-dashboard/src/app/dashboard/rides/unpaid/page.tsx` — the queue: sortable table of rider
  (name + phone), driver, fare incl. tip, retry count and completion date, with a **Send invoice**
  action per row wired to the existing `sendPayableRideInvoice`. Two summary tiles, pagination,
  empty state, and an error surface.
- `admin-dashboard/src/app/dashboard/rides/unpaid/loading.tsx` — skeleton, matching the convention
  every other dashboard route follows.
- `admin-dashboard/src/components/sidebar.tsx` — "Unpaid Rides" as a child of Rides, so it is
  reachable without typing a URL.

Deliberate choices worth stating:

- **The invoice result is kept per row.** `sendPayableRideInvoice` returns the hosted Stripe
  `invoice_url`; the row turns into a link to it rather than a "sent!" toast, so an admin can open
  exactly what the rider received instead of trusting that an email went out.
- **Per-row in-flight state**, not a single page-level `sending` flag — one slow invoice must not
  disable every other row's button.
- **A superseded-request guard** (`reqIdRef`) on the fetch, the same idiom
  `dashboard/disputes/page.tsx` uses: paging quickly would otherwise let an older page's response
  land last and overwrite the newer one.
- **The money tile is labelled "Outstanding (this page)"**, not "Outstanding". The endpoint returns
  one page and exposes no aggregate, so a total across all pages would be a number this screen
  cannot actually compute. Labelling it honestly beats implying a figure that isn't there.
- **No "waive" action here**, even though `POST /rides/{id}/complete` can waive. Waiving writes off
  real money; it belongs behind the ride detail view where the full context is, not one click away
  in a list. Sending an invoice is the safe default action and the only one offered.

**Alternative considered:** a tab on the existing `/dashboard/rides` page. Rejected for two
reasons — that page is already a dense multi-filter table, and it is one of the six
visual-regression-seeded baselines, so a tab strip would change it far more than a collapsed
sidebar child does (§4).

## 4. Risk & impact on existing functionality

**Additive.** One new route, one new API client function, one sidebar child. No existing page,
endpoint, query or permission is modified.

**Permissions:** the page is gated with `useRequireModule("rides")`, matching the backend, where
the whole admin rides router is mounted behind `require_module("rides")`
(`routes/admin/__init__.py:302`). An admin without the rides module never sees the nav entry and
would be redirected off the page. This is a client-side convenience on top of a real server gate,
not the gate itself.

**PII:** the screen shows rider name and phone. That is a deliberate part of the workflow — an
admin chasing an unpaid fare needs to identify and contact the rider — and it is the same data the
endpoint already returned and the same data the rides and riders pages already display to the same
module holders. Nothing is logged; nothing new leaves the browser.

**⚠️ Visual-regression baseline: `dashboard-rides` will diff and needs re-seeding by a human.**
The sidebar gained a child under Rides. Children are collapsed by default and a parent auto-expands
**only while it or a child is the active route** (`sidebar.tsx`, 2026-09-04 design review), so the
other seeded pages — `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`,
`dashboard-settings`, `login` — render an unchanged sidebar and are unaffected. `dashboard-rides`
is the exception: the Rides parent is active there, so it auto-expands and the new child is
visible. That is an intended UI change, not a spurious diff, and per CLAUDE.md the baseline must be
re-captured by a human running `update-visual-baselines.yml` — an agent has no Actions-dispatch
access.

**Load:** one paginated read (50 rows) on an admin-only route, issued on mount and on explicit
refresh. No polling. No SLA in the Performance table covers an admin list endpoint.

## 5. User-experience effect

- **Internal admin (rides module):** a new "Unpaid Rides" entry under Rides in the sidebar, and a
  screen listing every uncollected fare with a one-click payable invoice. Previously this
  information existed only in a transient alert.
- **Rider:** none directly — but a rider with an unpaid fare is now materially more likely to be
  sent the invoice that lets them clear it and unblock their own booking.
- **Driver / corporate admin:** none.
- No copy changes anywhere else. Nothing changes mid-session for anyone already using the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api/rides.ts` | `getUnpaidRides` + `UnpaidRide` type | the endpoint had no client |
| `admin-dashboard/src/lib/api.ts` | re-export both | matches how every other rides helper is surfaced |
| `admin-dashboard/src/app/dashboard/rides/unpaid/page.tsx` | new page | the queue itself |
| `admin-dashboard/src/app/dashboard/rides/unpaid/loading.tsx` | new skeleton | route convention |
| `admin-dashboard/src/components/sidebar.tsx` | "Unpaid Rides" child + `Receipt` icon import | discoverability |
| `docs/change-log/2026-09-21-admin-unpaid-rides-screen.md` | this file | |

## 7. Before / after

**Before:** an admin is pushed *"Ride 3f9a21c8… — all 3 retries failed. Rider is blocked from
booking."* If they are not looking, that is the end of it. There is no screen, no list, no total,
and no way to find the ride again except by knowing its id.

**After:** Sidebar → Rides → Unpaid Rides shows every one of them, newest first, with the rider's
name and phone, what is owed, how many retries were attempted, and a **Send invoice** button that
emails the rider a Stripe hosted pay page. Paying it settles the ride automatically through the
existing `invoice.paid` webhook — no further admin action.

## 8. Rollback plan

`git revert` + redeploy the dashboard. Front-end only: no migration, no schema, no settings row, no
backend change, and nothing written to any table by this diff. Reverting restores the previous
state exactly (an endpoint with no UI).

If only the nav entry is a problem, removing the sidebar child alone leaves the page reachable by
URL and restores the `dashboard-rides` baseline — a smaller revert that needs no backend involvement.

## 9. Verification performed

- Read every component API used against its own source rather than assuming: `PageHeader`
  (`title: ReactNode`, `description`, `actions`), `Pagination` (`page` / `pageSize` /
  `hasNextPage` / `onPageChange` — **no `currentCount` prop**, which an earlier draft passed and
  which is now removed), `useTableSort` (returns `{ sorted, sort, toggle, setSort }`),
  `SortableHead` (`column` / `sort` / `onSort`), `Badge` (`outline-warning` exists),
  `formatDate` (exported from `@/lib/utils`).
- `UnpaidRide` is declared as a `type` alias, not an `interface`, specifically because
  `useTableSort`'s constraint is `T extends Record<string, any>` and an interface carries no
  implicit index signature. This was changed pre-emptively rather than discovered by a compiler —
  see §10.
- Confirmed the response shape field-by-field against the endpoint's own `result.append({...})`
  in `backend/routes/admin/rides.py`.
- Confirmed the backend gate is `require_module("rides")` at router mount, so the page's
  `useRequireModule("rides")` matches rather than over- or under-claiming.
- Confirmed the sidebar's collapse behaviour from its own source before asserting which visual
  baselines are affected (§4).
- Read `spinr-admin-design-system` before writing any markup, and used only existing semantic
  tokens and the established `outline-*` badge vocabulary — no new colors, no hardcoded hex.

## 10. What was NOT verified — read this before merging

- **Nothing was compiled, built, linted or tested.** `admin-dashboard/node_modules` is **absent** in
  this environment and `npm` returns 403 (registry blocked by policy), so `npm install`,
  `npm run build`, `tsc --noEmit` and `eslint` were all impossible. A `tsc --noEmit` attempt
  produced only two errors about missing test type-definitions and could not have resolved a single
  project import — it is not evidence of anything. **CLAUDE.md requires a real production build for
  an admin-dashboard change; that has not happened, and CI is the first thing that will actually
  compile this code.**
- **`admin-dashboard/AGENTS.md` instructs agents to read the version-specific guides in
  `node_modules/next/dist/docs/` before writing any code, because this Next.js has breaking changes
  relative to older releases. That was impossible — `node_modules` is absent.** The stack is Next
  `^16.3.5`, React `19.3.0`, TypeScript `^6`, all newer than this model's training data. The page
  was therefore written by mirroring the conventions of existing sibling pages
  (`dashboard/disputes/page.tsx`, `dashboard/drivers/expiring/page.tsx` — both `"use client"` +
  hooks, same imports, same table/pagination components), which is the strongest available
  evidence of what this version expects, but it is not the same as having read the guides.
- No screenshot, no rendering, no browser. Layout, spacing, dark mode and the table's behaviour at
  narrow widths were reasoned about from the tokens and from sibling pages, not seen.
- No accessibility check was run (no axe, no rendered DOM). The design-system notes warn that a
  reused token in a *new* pairing is not automatically contrast-safe — the new pairings here are
  `text-success` on card background for the "Invoice sent" link and `outline-warning` on the retry
  badge, and both want a real check.
- `dashboard-rides`' visual baseline **will** fail until a human re-seeds it (§4).
- The invoice action was not exercised end to end. `sendPayableRideInvoice` is pre-existing and
  used elsewhere, but that this page calls it with a valid ride id and renders the returned
  `invoice_url` correctly is untested.
- No test file was added for the page. The dashboard has testing infrastructure
  (`*.test.tsx` beside pages), and this screen has real logic worth pinning — the superseded-request
  guard, per-row sending state, and the page-total label. Left out because a test that cannot be run
  is a liability, not coverage; it should be added by whoever can execute it.
