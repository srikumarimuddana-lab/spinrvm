# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, admin, auth |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Critical #3 |

## 1. Issue / gap identified

`corporate_accounts_router` and `corporate_wallet_router` — the routers that
move corporate wallet money, approve/reject KYB, change company status
(including a real Stripe refund trigger), and stream private KYB identity
documents — were mounted in `server.py` with no module-level RBAC check at
all. Any authenticated admin, regardless of which modules their role grants,
could reach every endpoint on both routers.

## 2. Root cause

Every other comparably sensitive admin router in this codebase is
module-gated at its mount point (e.g. `admin/wallet.py` and
`admin/subscriptions.py`, gated inside `routes/admin/__init__.py`'s own
composition via `dependencies=[Depends(require_module("earnings"))]`).
`corporate_accounts_router` and `corporate_wallet_router` are two standalone
top-level routers mounted directly in `server.py` (not nested inside
`admin_router`'s composition), and were simply never wrapped in an
equivalent dependency at any of their four mount points — two for the
canonical `/api/v1` paths, two for the legacy `/api` paths.

## 3. Fix / remediation

Wrapped all four `include_router()` calls for these two routers in
`server.py` with `dependencies=[Depends(require_module("corporate_accounts"))]`.
`"corporate_accounts"` was chosen over inventing a new module string because
it already exists in `AVAILABLE_MODULES` (`routes/admin/staff.py`), is
already granted to the `finance` role preset, and is already present in
JWT-claim generation and test fixtures (`routes/admin/auth.py`,
`test_admin_rbac.py`) — it was defined anticipating exactly this kind of
gating but never actually wired to any route until now.

## 4. Risk & impact on existing functionality

- **Blast radius: four `include_router()` call sites in `server.py`.** No
  route-handler code was touched — this is purely an authorization-layer
  change at the mount point, identical in shape to how every other
  module-gated router in this codebase is already wired.
- **Test blast radius: the shared `admin_override` pytest fixture
  (`backend/tests/conftest.py`), used by 29 test files.** This fixture
  previously returned `{"id": "admin_1", "role": "admin"}` with no
  `modules` claim — sufficient before this fix, since neither router
  checked modules at all. Added `"modules": ["corporate_accounts"]` to the
  fixture's returned dict (role unchanged) so the ~14 test files that
  exercise `/admin/corporate-accounts/...` and the corporate wallet
  endpoints keep passing.
  - Deliberately did **not** grant the fixture `role: "super_admin"` or a
    broader module list: several other files sharing this fixture
    (`test_admin_stripe_import.py`, `test_admin_stripe_payout_sync.py`,
    `test_data_transfer_jobs.py`, and others) have dedicated
    `test_non_super_admin_is_403`-style tests that specifically rely on
    `admin_override` representing a non-super-admin to verify a
    `require_super_admin` denial path. Widening the fixture's role or
    module grant beyond the single module this change actually requires
    would have risked silently defeating those denial tests.
  - Grepped all 29 files using `admin_override` for any test that depends
    on it having **zero** modules (i.e., a module-based denial test built
    on this specific fixture) — found none; every module-based denial test
    in the suite (`test_admin_rbac.py`, `test_data_transfer_jobs.py`, etc.)
    uses its own purpose-built admin dict via `_make_admin`/`_set_admin` or
    a separate `regular_admin_override` fixture, not `admin_override`.
- Confirmed via full regression run (222 tests across 24 files: all 14
  corporate-router test files, the RBAC matrix suite, and 9 other files that
  independently reference `require_module` as a sanity check that this
  change didn't affect unrelated module-gating elsewhere) — all passed with
  the one fixture change above and no other modifications needed.

## 5. User-experience effect

**Internal admin-facing only, no rider/driver/corporate-customer-visible
change.** An admin whose role does not include the `corporate_accounts`
module (e.g. a `support` or `operations`-role staff account, per the
existing role presets in `staff.py`) will now get a 403 attempting to view
or modify a corporate account, adjust a corporate wallet, or review KYB —
where previously they could. This is not visible mid-session to anyone
already using the app; it only affects internal staff members' access to
the admin dashboard's corporate-accounts screens, and only for staff whose
role was never supposed to include this access in the first place (the
`finance` and `super_admin` role presets already include
`corporate_accounts`; the gap was that no other role was actually being
checked against it).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/server.py` | Added `dependencies=[Depends(require_module("corporate_accounts"))]` to all 4 mount points of `corporate_accounts_router`/`corporate_wallet_router`; added the `Depends` and `require_module` imports | Close the open authorization gap on the two routers that move corporate money and handle private KYB documents |
| `backend/tests/conftest.py` | `admin_override` fixture now includes `"modules": ["corporate_accounts"]` | Keep the ~14 dependent corporate-router test files passing under the new gate, without widening the fixture's role or granting modules beyond what this change requires |

## 7. Before / after

```python
# Before
v1_api_router.include_router(corporate_accounts_router)
v1_api_router.include_router(corporate_wallet_router)
...
app.include_router(corporate_accounts_router, prefix="/api")
app.include_router(corporate_wallet_router, prefix="/api")
```

```python
# After
v1_api_router.include_router(
    corporate_accounts_router, dependencies=[Depends(require_module("corporate_accounts"))]
)
v1_api_router.include_router(corporate_wallet_router, dependencies=[Depends(require_module("corporate_accounts"))])
...
app.include_router(
    corporate_accounts_router, prefix="/api", dependencies=[Depends(require_module("corporate_accounts"))]
)
app.include_router(
    corporate_wallet_router, prefix="/api", dependencies=[Depends(require_module("corporate_accounts"))]
)
```

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully
restores the previous (open) mount points. No feature flag — this closes an
unconditional authorization gap; there is no meaningful dark-ship version of
"module-gate the routers that move money," and the fix is symmetric with
every other module-gated router already in production.

## 9. Verification performed

- [x] Automated tests: full regression run across 24 files, 222 tests — all
      14 corporate-router test files (`test_corporate_accounts_lifecycle.py`,
      `test_corporate_admin_routes.py`, `test_corporate_e2e_foundation.py`,
      `test_corporate_e2e_wallet.py`, `test_corporate_kyb.py`,
      `test_corporate_kyb_upload.py`, `test_corporate_status.py`,
      `test_corporate_stripe_customer.py`, `test_corporate_wallet_bootstrap.py`,
      `test_corporate_wallet_config.py`, `test_corporate_wallet_freeze.py`,
      `test_corporate_wallet_routes.py`, `test_corporate_wallet_view.py`,
      `test_deprecated_route_admin_exempt.py`), the RBAC matrix suite
      (`test_admin_rbac.py`), and 9 other files independently referencing
      `require_module` as a regression check — all passed via the session's
      `/tmp/spinr_venv` venv.
- [x] `ruff check` on both touched files — clean.
- [x] `ast.parse` + a full `pytest` import of `backend.server` (via the RBAC
      suite, which imports `backend.server.app`) — confirms the app still
      boots correctly with the new dependency imports.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4): every file using `admin_override`,
      cross-checked against files with `require_super_admin`/module-based
      denial tests to confirm none rely on this fixture's absence of a
      `corporate_accounts` grant.
- [x] Dry-run scenario: a `support`-role admin (module list per
      `ROLE_PRESETS["support"]` in `staff.py`, which does not include
      `corporate_accounts`) attempts `POST
      /admin/corporate-accounts/{id}/wallet/adjust`. Before this fix: the
      request succeeds — the router has no module check, only the base
      "is this an admin" check. After this fix: 403, `"Access denied —
      module 'corporate_accounts' not in your role permissions"`.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — four mount points, one shared
      test fixture, cross-checked against every file that could plausibly
      be affected by widening that fixture
- [x] No silent behavior change to a working flow for any admin whose role
      already includes `corporate_accounts` (`finance`, `super_admin`) —
      this only removes access that should never have been implicitly
      granted to every other role in the first place

## What was NOT verified

Not tested against a live/staging Supabase or a real admin JWT with a
genuinely restricted `modules` claim end-to-end through the deployed
admin-dashboard frontend — only through the backend's own TestClient-based
route tests with `app.dependency_overrides`. Did not audit whether any
production admin account currently relies on out-of-band access to these
routes despite lacking the `corporate_accounts` module (e.g. a support-role
staff member who has been manually adjusting corporate wallets as a
workaround) — if such a workflow exists, this change will surface it as a
403 in production, which is the intended outcome, but it's worth flagging
to whoever owns admin role assignments before this ships, since a role
change to grant `corporate_accounts` may be needed for specific individuals.
