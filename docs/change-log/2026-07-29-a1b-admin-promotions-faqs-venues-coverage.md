# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled on PR open) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b Track 1 item 4 |

## 1. Issue / gap identified

`backend/routes/admin/promotions.py` (~48%), `backend/routes/admin/faqs.py`
(~42%), and `backend/routes/admin/venues.py` (~43%) were below the 70% admin
coverage floor set in `CLAUDE.md`. Admin actions are audited but not
necessarily tested, and `promotions.py` in particular affects real fare
discounting — an unverified bug there is money-adjacent.

## 2. Root cause

These three admin CRUD files had no (or only partial, stats-only) dedicated
test coverage. `promotions.py` had `test_admin_promo_stats.py` covering only
`GET /promotions/stats`; the create/update/delete/list/usage endpoints —
including the money-relevant discount-value validation branches — were
untested. `faqs.py` and `venues.py` had no admin-route tests at all.

## 3. Fix / remediation

**Test-only change.** Added three new test files:

- `backend/tests/test_admin_promotions_crud.py` — create validation
  (negative discount, percentage > 100%, flat > $500, negative optional
  money fields), create happy path (code uppercasing, audit log call,
  optional-field insert fallback on schema-mismatch exception), update
  (partial updates, no-op skip, optional-field retry fallback), delete
  (audit log), list (status/search filter construction), and promo usage
  (user/ride enrichment, missing-table graceful empty list, date-range
  filtering).
- `backend/tests/test_admin_faqs_crud.py` — FAQ create/update/delete,
  audience-enum validation (422), embedding-invalidation-on-edit branch,
  `service_area_ids` explicit-null-vs-omitted distinction, notification
  send for all four `audience` branches (single user, all, riders,
  drivers), and notification list including the `created_at`-vs-`sent_at`
  ordering fallback.
- `backend/tests/test_admin_venues_crud.py` — venue CRUD, name
  whitespace-stripping, lat/lng validation (422), 404-not-found on
  update/delete, and 503-on-db-error branches for list/create/update/delete.

No application code was changed. No bugs found during this pass — see
"What was NOT verified" for the one caveat around 5xx-detail sanitization
that initially looked like a bug but is confirmed intended behavior
(`utils/error_handling.py`'s `http_exception_handler` sanitizes all 5xx
`detail` strings to a generic message unless they match an `ERR_*` sentinel
— tests were adjusted to assert only `status_code` for those cases, not the
specific detail string).

## 4. Risk & impact on existing functionality

**Blast radius: zero — this is a test-only, additive change.** No
production code in `backend/routes/admin/promotions.py`,
`backend/routes/admin/faqs.py`, or `backend/routes/admin/venues.py` was
modified. No other file reads/writes are affected. Grepped for other
consumers of these three route modules — none found outside
`backend/routes/admin/__init__.py`'s router mounting (unchanged).

Per the task's explicit constraint, other `routes/admin/*` files and
`routes/auth.py` were left untouched (other agents were working those in
parallel worktrees).

## 5. User-experience effect

None. No production code changed; nobody — rider, driver, corporate admin,
or internal admin — sees any behavior difference.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_promotions_crud.py` | New file — 19 tests | Close coverage gap on money-adjacent promo CRUD |
| `backend/tests/test_admin_faqs_crud.py` | New file — 15 tests | Close coverage gap on FAQ CRUD + notification broadcast |
| `backend/tests/test_admin_venues_crud.py` | New file — 13 tests | Close coverage gap on venue CRUD |
| `ACTION_ITEMS.md` | Added sub-bullets under A1b Track 1 item 4 with measured %s | Track completion per item 4 |
| `docs/change-log/2026-07-29-a1b-admin-promotions-faqs-venues-coverage.md` | New file (this doc) | Mandatory Change Impact & Risk Log |

## 7. Before / after

Not applicable — test-only additive change, no existing behavior modified.

## 8. Rollback plan

Revert the three new test files (and the `ACTION_ITEMS.md` sub-bullets) via
`git revert` of this commit. Since no production code or live data (DB rows,
Stripe charges, wallet deltas) was touched, a straight `git revert` is a
complete and sufficient rollback here — the "not a rollback plan for live
data" caveat in `CLAUDE.md` does not apply to a test-only change.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_admin_promotions_crud.py
      tests/test_admin_faqs_crud.py tests/test_admin_venues_crud.py -q`
      (all pass) and the full backend suite `pytest tests/ -q` (see final
      report for pass/fail count — run as part of this same change).
- [x] Coverage measured per file via `pytest tests/<file> -q` with the
      repo's default `--cov` (pytest-cov is enabled via `pyproject.toml`/
      pytest ini, no extra flags needed) — real, pasted numbers below, not
      estimated.
- [x] Blast-radius grep performed: searched for other importers of
      `routes/admin/promotions.py`, `faqs.py`, `venues.py` — none outside
      `routes/admin/__init__.py`'s router include (unchanged).
- [x] Reviewed against `CLAUDE.md` conventions: dual-import pattern
      (unaffected — tests patch `backend.db_supabase.*` targets per the
      `Patch target for DB is always backend.db_supabase.supabase` /
      module-function convention used elsewhere, e.g.
      `test_admin_promo_stats.py`), money arithmetic (no app code touched,
      so no Decimal concerns introduced), admin-route test pattern
      (`admin_override`/`get_admin_user` dependency-override fixture,
      matching `tests/conftest.py` and `test_admin_promo_stats.py`).
- [ ] Feature-flagged: not applicable (test-only change, nothing
      user-visible).
- No `npm run build` applicable — this is a Python backend change only, no
  `admin-dashboard`/`rider-app`/`driver-app` code touched.

**Real measured coverage (pasted from `pytest --cov` output):**

```
# promotions.py — combined with pre-existing test_admin_promo_stats.py
routes/admin/promotions.py    253     29    89%   109, 122-123, 128, 147, 204, 206, 208, 210, 212, 214, 216, 218, 220, 269, 292, 316-317, 334-335, 356, 358, 360, 362, 364, 378-380, 426

# faqs.py — new test_admin_faqs_crud.py alone
routes/admin/faqs.py          107      3    97%   100, 104, 106

# venues.py — new test_admin_venues_crud.py alone
routes/admin/venues.py         65      0   100%
```

## 10. What was NOT verified

- Not run against a real Supabase instance — all DB calls are mocked via
  `AsyncMock` patches on `backend.db_supabase.*`, consistent with this
  repo's unit-test tier convention (`mock_supabase_client` /
  direct-function-patch pattern). No integration-tier coverage was added.
- The remaining uncovered lines in `promotions.py` (89%, not 100%) are
  mostly defensive/rare branches: `promo_applications` table-missing
  exception path already covered, but a few specific filter-construction
  edge combinations (e.g. `status=inactive` combined with `search`) and
  the `min_ride_fare`/`referrer_reward` truthy-vs-falsy `if promotion.X:`
  insert branches at lines 204-220 were judged diminishing-returns for
  this pass — flagging rather than chasing the last ~11%.
- Did not audit whether the discount-value validation logic itself (0-100%
  cap, $500 flat cap) is the *correct* business rule — only that the
  existing logic is now exercised by tests. That's a product/pricing
  question, out of scope for a coverage-only pass.
- No new bugs were found in `promotions.py`, `faqs.py`, or `venues.py`
  during this pass. One thing that initially looked like a bug — 5xx
  HTTPException `detail` strings (e.g. `"venues_unavailable"`) coming back
  from the API as the generic `"Internal server error"` instead of the
  route's actual detail string — was confirmed to be intended repo-wide
  behavior (`utils/error_handling.py`'s sanitization of non-`ERR_*` 5xx
  details), not a defect in these three files.
