# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session 01Sspqro7zzjKdTbUh6D61wQ), design-audit follow-up |
| Surface(s) | backend, admin-dashboard (investigated; nothing changed in either) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/staff-faqs-pagination` |
| Related issue or gap ID | `/design Spinr Apps` audit → PR #4947's "flagged as a real but low-priority backend+frontend pair for a future round" (staff, faqs) |

## 0. Outcome

**Declined.** Investigated both endpoints and their realistic data volumes; pagination
for `staff` and `faqs` would be added complexity for effectively zero real-world
benefit. No code changed. This doc records the investigation and the decision so the
item can be closed instead of re-surfacing every audit round.

## 1. Issue / gap identified

PR #4947's triage (`docs/change-log/2026-09-04-admin-pagination-gaps.md`) described both
`GET /api/admin/staff` and `GET /api/admin/faqs` as lacking `limit`/`offset` support, and
flagged wiring both up (backend + frontend) as a low-priority future round. This task is
that follow-up round.

**Correction found during investigation:** that description was only half accurate.

- `GET /api/admin/staff` (`backend/routes/admin/staff.py::list_staff`) **already has**
  `limit`/`offset` query params (`Query(500, ge=1, le=1000)` / `Query(0, ge=0)`), already
  returns `X-Total-Count`/`X-Limit` response headers, and this was present **before**
  PR #4947 was opened (confirmed via `git show <PR-4947-base-sha>:backend/routes/admin/staff.py`
  — identical to HEAD). The claim "the backend endpoint has no pagination support at all"
  was wrong for `staff` at the time it was written; only the frontend (`getStaff()` /
  `staff/page.tsx`) never called it with anything but the default. This correction was
  missed by both the original design audit and PR #4947's own triage.
- `GET /api/admin/faqs` (`backend/routes/admin/faqs.py::admin_get_faqs`) genuinely has
  **no** `limit`/`offset` params — it calls `db_supabase.get_rows(..., limit=500)` with a
  hardcoded cap and no way for a caller to request page 2. This half of the original
  claim is accurate.

## 2. Root cause

Not a code defect in either case — both are a byproduct of the audit's own aggregate
"8+ pages lack pagination" claim being checked shallowly (see PR #4947 §2 for the same
root-cause pattern on other pages in that round): `staff`'s backend pagination was
already there and simply unused by the frontend, which reads as "no pagination" from the
frontend/UX-only lens the audit used, and got carried forward without re-verifying the
backend.

## 3. Investigation performed (why "add pagination" is being declined, not deferred)

Checked realistic collection size for both, since CLAUDE.md's release-gate #9 asks for a
real judgment call rather than reflexively matching the pattern used elsewhere
(`stripe-events`, `drivers/decals`) where pagination or a fetch-loop was a real fix for a
real, growing-over-time collection:

- **`admin_staff`** — internal Spinr employees (admins/ops/support/finance), created only
  through the admin console (`POST /api/admin/staff`); grepped `backend/migrations/` for
  any `INSERT INTO admin_staff` seed data — **none exists**, confirming this table is
  populated exclusively by real staff onboarding, not bulk-seeded. A single-province
  (Saskatchewan-first) rideshare company's internal admin/ops/support/finance headcount is
  realistically tens of people, not hundreds — nothing like driver or rider counts, which
  scale with fleet/market growth. Even generously assuming multi-province expansion, this
  is an order of magnitude below the existing `500`-row default cap the endpoint already
  enforces.
- **`faqs`** — curated help-content entries, written by content/support staff via the
  admin FAQ editor. Counted every `INSERT INTO faqs` seed migration's row-tuples
  (`backend/migrations/210/212/230/330/366/367_*_faqs.sql`): 8 + 33 + 14 + 4 + 2 + 5 = **66
  rows** across all seed migrations combined (several later migrations, e.g.
  `322_consolidate_sos_faq.sql`, `327_merge_duplicate_onboarding_faqs.sql`,
  `365_merge_duplicate_crc_and_payout_faqs.sql`, actively *reduce* duplicate rows further).
  Plus whatever an admin has added by hand since — realistically dozens, not hundreds.
  Again well under the `500`-row hardcoded cap already in the query.

For both tables, the existing (or, for `staff`, already-shipped-but-unused) `500`-row cap
is roughly **5–8× the realistic real-world row count**. That's the material difference
from the two genuinely-fixed truncation bugs found in this same audit round
(`stripe-events` stuck-event backlog, and especially `drivers/decals`'s 500-driver silent
truncation — driver counts are exactly the kind of number that grows with the business
and can plausibly cross a 500 cap): staff and FAQ counts don't grow that way, and even a
generous 5–10 year growth projection for either doesn't approach the existing cap. Adding
real `limit`/`offset` pagination to `faqs`'s backend, threading `limit`/`offset` through
`getStaff()`/`getFaqs()`, and wiring the `Pagination` component + "fetch limit+1, slice,
hasNextPage" pattern into both pages (plus backend pytest coverage and updated frontend
tests) is nontrivial surface area for a benefit that, on current and realistically
projected data, is zero — nobody will ever see a "next page" control on either page,
because neither collection will reach even the 51st row under the shared-component
default page size these other pages use, let alone the 500-row hard cap already in place.

This is a real gap in the strict sense ("caller cannot pass limit/offset"), but a low
one, and — per this task's explicit framing — a case where the honest answer is "don't
build it," not "build a smaller version of it."

## 4. Risk & impact on existing functionality

None — no code changed. If a future admin round finds either collection has genuinely
grown past a few hundred rows (unexpected multi-province staffing scale, or an unusually
large FAQ content push), revisit: `faqs` would need the backend `limit`/`offset` params
`staff` already has, and both pages would need the same `Pagination`-component wiring
`stripe-events/page.tsx` (PR #4947) already demonstrates. Until then, tracking this as
closed rather than a recurring backlog item avoids re-litigating the same investigation
every audit pass.

## 5. User-experience effect

None. No behavior changed for riders, drivers, corporate admins, or internal admins.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/change-log/2026-09-04-staff-faqs-pagination-declined.md` | New file (this doc) | Records the investigation and decision to decline, correcting the stale "staff has no backend pagination" claim carried from the original audit through PR #4947 |

## 7. Before / after

Not applicable — no behavior-changing diff.

## 8. Rollback plan

Not applicable — no code changed, nothing to roll back.

## 9. Verification performed

- Read `backend/routes/admin/staff.py::list_staff` — confirmed `limit`/`offset` +
  `X-Total-Count`/`X-Limit` headers already present.
- `git show <PR-4947 base sha b1fd786214a04d8ee39813fcb14801cd20efbcf0>:backend/routes/admin/staff.py`
  — confirmed identical pagination code predates PR #4947, correcting that PR's own
  "no backend limit/offset support at all" claim for `staff` specifically.
- Read `backend/routes/admin/faqs.py::admin_get_faqs` — confirmed no `limit`/`offset`
  params exist (hardcoded `limit=500`), so the claim holds for `faqs`.
- Read `admin-dashboard/src/lib/api/staff-subscriptions.ts`'s `getStaff()` and
  `admin-dashboard/src/lib/api/content-area.ts`'s `getFaqs()` — confirmed neither takes
  or forwards pagination params, and `staff/page.tsx` / `faqs/page.tsx` both call them
  with no arguments.
- Counted realistic collection sizes via `backend/migrations/` grep (see §3) rather than
  assuming "small" without evidence.
- Fetched PR #4947 via the GitHub API to confirm its own stated scope and "not changing
  but considered" note matches what's described here, rather than relying on the task
  prompt's secondhand summary of it.

## 10. What was NOT verified

- Did not query the live/production `admin_staff` or `faqs` tables directly (no DB
  credentials in this environment) — collection-size reasoning in §3 is from migration
  history and domain knowledge of what these tables represent, not a live `COUNT(*)`.
- No backend or frontend tests were added, run, or needed — no code changed.
- `npm run build` / `npx tsc --noEmit` / `npm run test` were not run for this branch —
  there is no frontend diff to verify. (Confirmed no staged changes outside this doc via
  `git status`.)

## 11. Sign-off

- [x] Rollback plan is concrete and testable (n/a — no code changed)
- [x] Blast radius is stated, not assumed (none — investigation-only)
- [x] No silent behavior change to an already-shipped flow (nothing changed)
- [x] Escalate-vs-ship judgment made explicitly per CLAUDE.md gate #9, with the
      reasoning and the data behind it recorded above, rather than silently picking a
      side
