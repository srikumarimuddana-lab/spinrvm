# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b Track 1, item 4 (`backend/routes/admin/`) |

## 1. Issue / gap identified

`backend/routes/admin/support.py` (disputes / support-ticket CRUD / flags /
complaints admin endpoints) and `backend/routes/admin/support_tickets.py`
(Zoho Desk help-desk proxy) were at ~39% and ~43% measured coverage
respectively — well under the ≥70% admin-routes minimum in `CLAUDE.md`.
Neither file had a dedicated route-level test suite; only the newer
service-area and AI-suggest sub-routes of `support_tickets.py` had tests
(`test_support_tickets_service_area_routes.py`,
`test_support_tickets_ai_suggest.py`).

## 2. Root cause

These two files were added/extended incrementally (dispute resolution,
flag/complaint workflows, then the Zoho Desk integration) without a
matching test pass at each step — the classic "shipped working, coverage
caught up later" gap called out generally in `ACTION_ITEMS.md` A1b.

## 3. Fix / remediation

Test-only change. Added two new test files exercising every route handler
in both files (happy path, 404/400 branches, DB/Zoho error mapping, and
the audit-log call sites), using the existing `get_admin_user`
dependency-override pattern (a `super_admin` override satisfies both
`get_admin_user` and the `require_module(...)` RBAC gate these routers are
mounted behind). No application code was changed.

## 4. Risk & impact on existing functionality

- **Blast radius: none.** This PR adds only test files
  (`backend/tests/test_admin_support_routes.py`,
  `backend/tests/test_admin_support_tickets_routes.py`) plus two doc
  updates (`ACTION_ITEMS.md`, this change-log). No production code in
  `backend/routes/admin/support.py`, `backend/routes/admin/support_tickets.py`,
  or anywhere else was touched.
- Grepped for other callers/importers of these two route modules
  (`grep -rn "admin.support\b\|admin\.support_tickets" backend/`) — the
  only import sites are `backend/routes/admin/__init__.py` (router mount,
  unchanged) and the tests themselves (existing + new). No other route
  file, background loop, or service imports these modules directly.
- Zero risk to the ride/dispatch/payment state machine, corporate wallet
  deltas, or Stripe flows — these routes only touch `disputes`,
  `support_tickets`, `support_messages`, `flags`, `complaints`, and the
  Zoho Desk mirror tables, all mocked in the new tests.

## 5. User-experience effect

None. No admin-dashboard-facing behavior changed — this is test coverage
only, invisible to riders, drivers, corporate admins, and internal admins
alike.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_support_routes.py` | New file: 40 tests covering every handler in `routes/admin/support.py` | Close the coverage gap on disputes/tickets/flags/complaints admin CRUD |
| `backend/tests/test_admin_support_tickets_routes.py` | New file: 35 tests covering config/sync/dashboard/trends/ticket CRUD/reply/comment/patch/tags in `routes/admin/support_tickets.py` | Close the coverage gap on the Zoho Desk help-desk proxy (service-area + AI-suggest routes already covered elsewhere) |
| `ACTION_ITEMS.md` | Added sub-bullets under A1b Track 1 item 4 with measured before/after % for both files | Track progress per the existing item's structure |
| `docs/change-log/2026-07-29-a1b-admin-support-coverage.md` | New file (this doc) | Mandatory Change Impact & Risk Log entry per `CLAUDE.md` |

## 7. Before / after

Not applicable — no behavior-changing diff. Purely additive test files.

## 8. Rollback plan

`git revert` of this commit is sufficient and complete: it only removes
test files and doc updates, touches no live data, no migration, no
feature flag, and no runtime code path. This is the one category of
change where a plain revert *is* a full rollback plan, per `CLAUDE.md`'s
own carve-out ("a `git revert` is not a rollback plan for anything already
applied to live data" — nothing here is applied to live data).

## 9. Verification performed

- [x] Automated tests run: unit (new files run standalone and as part of
  the full suite). Ran `python -m pytest tests/test_admin_support_routes.py
  tests/test_admin_support_tickets_routes.py tests/test_support_tickets_ai_suggest.py
  tests/test_support_tickets_service_area_routes.py -q` — 88 passed.
  Also ran the **full backend suite** (`pytest tests/ -q`, all files) to
  confirm zero regressions elsewhere.
- [ ] Manual repro steps followed in staging — not applicable (test-only
  change, nothing to manually repro in a running environment).
- [x] Blast-radius grep performed: `grep -rn "admin\.support\b\|admin\.support_tickets"
  backend/` — only `routes/admin/__init__.py` (unchanged mount) and the
  test files import these modules.
- [x] Reviewed against relevant `CLAUDE.md` conventions: patch target for
  DB mocks is `<module>.db_supabase.<fn>` (matching the dual-import
  pattern each route module already uses, not a hardcoded
  `backend.db_supabase.supabase` string — these route modules call through
  their own imported `db_supabase` reference, so tests patch
  `routes.admin.support.db_supabase.<fn>` / `routes.admin.support_tickets.db_supabase.<fn>`,
  which is the correct patch surface for this file's import style). No raw
  PII (phone/email/GPS/names) used in test fixtures beyond placeholder
  values already used elsewhere in the test suite (e.g. `a@spinr.app`).
- [x] Feature-flagged if user-visible and non-trivial — not applicable,
  no user-visible change.

Measured coverage (pasted from real `pytest --cov` output, not estimated):

```
routes/admin/support.py            267      8    97%   177, 220, 222, 228, 374, 376, 451, 551
routes/admin/support_tickets.py    357     31    91%   62, 75-76, 78, 145, 235-237, 309-310,
                                                        355-356, 383-384, 463-464, 512-513,
                                                        520-521, 530-531, 581-582, 632-633,
                                                        674-675, 742, 788-789
```

Both exceed the 80% target given in this task and the repo's general ≥70%
admin-routes minimum.

## 10. What was NOT verified

- Real Supabase / real Zoho Desk API were never exercised — all DB and
  Zoho calls are mocked (`AsyncMock`), per the repo's unit-test
  convention (`mock_supabase_client` pattern; here mocked at the
  `db_supabase`/`zoho` module-attribute level instead since these routes
  call through thin service wrappers, not the raw Supabase client
  directly).
- The remaining uncovered lines in `support_tickets.py` (31 statements)
  are mostly the `except ImportError:` fallback branches of the dual-import
  pattern (never exercised when running via `python -m backend.server`/
  pytest's `backend.*` import path) and a handful of narrow Zoho-error
  edges on endpoints whose primary path (success + one representative
  error case) is already tested; chasing 100% here was assessed as
  diminishing returns and not attempted, consistent with `ACTION_ITEMS.md`'s
  stated policy against chasing coverage past the point of reducing real
  risk.
- No admin-dashboard frontend change accompanies this — nothing to build
  or visually verify (`npm run build` not applicable, backend-only PR).
- A pre-existing bug was found but **not fixed** (out of scope per this
  task's constraints): `admin_get_dispute_stats` in `routes/admin/support.py`
  computes `Decimal(str(d.get("refund_amount") or 0))` for each resolved
  dispute and only catches `(TypeError, ValueError)`; a non-numeric
  `refund_amount` string (e.g. corrupted data) raises
  `decimal.InvalidOperation` instead, which is uncaught, so the whole
  `/admin/disputes/stats` endpoint would 500 rather than skip the bad row.
  Flagged in `ACTION_ITEMS.md` item 4 sub-bullet for follow-up; the new
  test suite documents this explicitly rather than asserting around it
  silently.
