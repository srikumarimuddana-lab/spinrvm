# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch: `claude/a1c-drivers-shared-batch`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c Sub-tier A |

## 1. Issue / gap identified

The last three unclosed files in the `backend/routes/drivers/` package coverage
sweep sat well below target:

- `routes/drivers/_shared.py` — 51.32% documented baseline (PII vault
  encrypt/decrypt, ride-route-snapshot render/upload/write-back pipeline,
  pickup-leg road-snap backfill, post-trip GPS route validation, small money
  helpers). Measured fresh baseline: **228 statements, 111 missing (51%)**.
- `routes/drivers/status.py` — 48.39% documented baseline (driver detail +
  online/available status transitions). Measured fresh baseline: **31
  statements, 16 missing (48%)**.
- `routes/drivers/profile.py` — 67.65% documented baseline (driver profile,
  registration, config, heatmap, destination mode). Measured fresh baseline:
  **136 statements, 44 missing (68%)**.

(Fresh baseline command, matching the task's specified scoping:
`pytest tests/ -q --cov=routes.drivers._shared --cov=routes.drivers.status
--cov=routes.drivers.profile --cov-report=term-missing --no-cov-on-fail` —
full-suite run, 7263 passed / 8 skipped / 1 xfailed, 0 failed, confirming the
documented percentages were accurate as of this session's start, not
drifted.)

## 2. Root cause

- `_shared.py`: existing tests (`test_pickup_otp_not_leaked.py`,
  `test_snapshot_renderer_policy.py`, and the PII-decrypt mocking scattered
  across `test_drivers_extended.py`/`test_sgi_forms_route.py`/
  `test_compliance_reports_http.py`/`test_p2_payout_t4a.py`) all **mock
  around** `_shared.py`'s heaviest functions rather than exercising them:
  every route-level test patches `_encrypt_driver_pii`/`_decrypt_driver_pii`/
  `_generate_and_store_ride_snapshot` wholesale, so the vault RPC functions
  (`_vault_encrypt`/`_vault_decrypt`) and the snapshot pipeline's storage/
  upload/write-back tail (`_generate_and_store_ride_snapshot` lines
  363–493 — everything past the OSM-render fallback) had never run for
  real. `test_snapshot_renderer_policy.py` specifically stubs the OSM
  renderer to return `None` in every case, so the function always hits its
  early `if not png_bytes: return` guard and never reaches the
  upload/ledger/write-back code beneath it. `_snap_pickup_leg_async` and
  `_validate_ride_route` (post-trip background tasks) had zero direct
  coverage at all.
- `status.py`: `update_driver_status` (the online/available toggle) is
  already thoroughly covered by `test_go_online_availability.py` (C2
  availability-reassert regression) and `test_p1_driver_offline.py` (P1-10
  offline-mid-trip guard) — the entire remaining gap was the **other**
  route in this file, `GET /drivers/{driver_id}` (`get_driver`), which had
  zero tests covering any of its three branches (admin/self full detail,
  rider-with-active-ride safe projection, rider-without-active-ride 403).
- `profile.py`: `get_my_driver`/`update_my_driver`'s safe-field path and
  `register_driver` were covered by `test_drivers_extended.py`, but several
  branches were not: `get_driver_config`'s `app_settings` lookup exception
  fallback, `update_my_driver`'s auto-create-driver-row path and its
  vehicle-change → `needs_review` re-review branch (with the insurance
  period-transition and driver-notification side effects), the entire
  `get_demand_heatmap` endpoint, and the 404 branches of
  `clear_destination_mode`/`get_destination_mode`.

## 3. Fix / remediation

Test-only change. Added `backend/tests/test_drivers_shared_status_profile_coverage.py`
(59 tests, one combined file per the task's "whichever fits the existing
convention better" guidance — three source files but one cohesive package
surface, mirroring the sectioned-single-file style already used inside
`test_subscriptions_coverage.py`). No application code changed.

Coverage closed, by function:

- **`_shared.py`**
  - `_route_snapshot_retention_due_at` — normal 3-year-add path and the Feb
    29 leap-day fallback (no equivalent date 3 years later).
  - `_d` — the invalid-value `except` branch (non-numeric input → `Decimal("0")`).
  - `_ride_income` — the legacy fare-component fallback branch (rows
    written before `driver_earnings` existed) and the canonical-column
    preference branch.
  - `_defer_snapshot_retry` — the `except Exception` branch (the
    `snapshot_attempts` column, migration 243, not yet deployed) that
    `test_snapshot_renderer_policy.py` doesn't reach.
  - `_vault_encrypt` — empty-value passthrough, `supabase_client`
    `ImportError` → 503, uninitialised client → 503, RPC-returns-no-data →
    503, RPC-raises → 503, and the success path.
  - `_vault_decrypt` — empty-value passthrough, `ImportError` → raw-token
    degrade, uninitialised client → raw-token degrade, success path, and
    the RPC-raises → raw-token degrade (never-raises contract).
  - `_encrypt_driver_pii`/`_decrypt_driver_pii` — present-field calls the
    vault helper; absent/falsy field skips it; decrypt returns a shallow
    copy (source dict untouched).
  - `_generate_and_store_ride_snapshot` — the missing-coordinates early
    return; the Google-render success log line (previously only the
    Google-*failure* path was exercised); the full legacy (`revision=0`)
    upload + `rides.route_snapshot_url` write, including a write failure
    being logged and swallowed and the `SUPABASE_URL` unconfigured early
    return; the Supabase Storage upload failure path; the full v2
    revisioned path (ledger insert success, ledger insert failure
    degrading to upload-anyway, the CAS-miss branch deleting the now-
    unreachable object, the `snapshot_attempts`-column-missing retry, and
    the reference-write exception being logged and swallowed by the
    outer handler despite the inner explicit `raise`); and the outermost
    catch-all around a failure inside the pipeline's own dual-import block.
  - `_snap_pickup_leg_async` — empty-breadcrumbs no-op, successful
    road-snap backfill, empty-polyline-result skip, and exception-swallowed
    (best-effort, never raises).
  - `_validate_ride_route` — too-few-breadcrumbs no-op, `ok`-verdict
    silent store, suspicious/spoofed-verdict warning log, `None`-result
    early return, store-failure logged-not-raised, and outer-exception
    swallow.
- **`status.py`**: `get_driver` — 404 (driver not found), admin sees full
  decrypted detail, self sees full decrypted detail, rider with an active
  ride assigned gets the safe public projection (and PII fields are
  asserted absent from it), rider without an active ride gets 403, and an
  active-ride-lookup DB exception degrading to 403 rather than 500.
- **`profile.py`**: `get_driver_config`'s `app_settings`-lookup-exception
  fallback to defaults; `update_my_driver`'s auto-create-driver-row path
  (no existing row + updates present) and the active-driver vehicle-change
  → `needs_review` branch (asserting `record_vehicle_changes`,
  `record_period_transition(driver_id, 0)`, and
  `notify_driver_status_change` all fire) plus the pending-driver skip
  variant (no review flip, no period transition); `get_demand_heatmap`'s
  disabled-area/no-driver/enabled-with-points branches (including a
  missing-coordinate ride being skipped from the points list);
  `clear_destination_mode`/`get_destination_mode`'s 404 branches.

## 4. Risk & impact on existing functionality

- **Blast radius — `_shared.py` is a real dependency of the whole
  `routes/drivers/` package**, exactly as flagged in the task. Grepped
  every other file in the package that imports from it:
  - `routes/drivers/__init__.py` — re-exports `_generate_and_store_ride_snapshot`
    (and effectively everything else via `from . import _shared`) as part
    of the package's public surface.
  - `routes/drivers/status.py` — imports `serialize_doc`; imports the
    `_shared` module itself for `_shared._decrypt_driver_pii` in
    `get_driver`.
  - `routes/drivers/profile.py` — imports `_STRIP_FROM_SELF_RESPONSE`,
    `serialize_doc`; imports the module for `_shared._encrypt_driver_pii`/
    `_shared._decrypt_driver_pii` in `get_my_driver`/`update_my_driver`/
    `register_driver`.
  - `routes/drivers/earnings.py`, `routes/drivers/payouts.py`,
    `routes/drivers/referrals.py`, `routes/drivers/tax_exports.py` — use
    `_ride_income`/`_d`/`_money_str` for earnings/payout/T4A math (currently
    being covered by the **sibling concurrent session** on
    `payouts.py`/`earnings.py`/`referrals.py`, branch
    `claude/a1c-drivers-payouts-batch` — not touched by this branch).
  - `routes/drivers/ride_flow.py`, `routes/drivers/ride_cancel.py`,
    `routes/drivers/ride_reads.py` — use `_require_ride_in_state`,
    `ARRIVE_FROM_STATES`/`START_FROM_STATES`/`COMPLETE_FROM_STATES`,
    `serialize_ride_for_driver`, `RideOTPRequest` (currently being covered
    by the **other sibling concurrent session**, branch
    `claude/a1c-drivers-ride-flow-batch` — not touched by this branch;
    `_require_ride_in_state` remains this session's one deliberately-left
    gap in `_shared.py`, see section 9).
  - `routes/drivers/ride_complete.py` — calls `_generate_and_store_ride_snapshot`,
    `_snap_pickup_leg_async`, `_validate_ride_route` directly on trip
    completion; not touched by this branch.
  - Outside the package: `routes/rides/booking.py` and
    `utils/route_finalizer.py` both call
    `_generate_and_store_ride_snapshot` directly (the scheduled-ride
    pre-generation path and the finalizer's async retry path,
    respectively) — neither touched.
  - `routes/corporate_company.py`, `routes/fares.py`, `routes/quests.py`,
    `routes/wallet.py`, `services/cancellation_service.py`,
    `services/company_booking_service.py`,
    `services/corporate_wallet_service.py`, `services/fare_service.py`,
    `services/payment_service.py` also matched the grep for the symbol
    names above, but on inspection these hits are their own local
    same-named helpers (e.g. their own private `_d`/`_money_str`), not
    imports of `routes/drivers/_shared`'s — confirmed by checking each
    file has no `from routes.drivers._shared import` / `from . import
    _shared` line. No actual dependency.
  - **This branch changed zero lines in `_shared.py`, `status.py`, or
    `profile.py` — test-only, as instructed.** No other importer's runtime
    behavior is affected by this change.
- **Driver online/available invariant respected in the new tests**: the one
  `status.py` gap closed here (`get_driver`) does not touch
  `is_online`/`is_available` at all — those are fully exercised by the
  pre-existing `test_go_online_availability.py`/`test_p1_driver_offline.py`
  suites, which this session did not modify. No new test in this pass
  asserts or implies `is_available=True` with `is_online=False`.
- **PII/vault-adjacent, but test-only**: `_vault_encrypt`/`_vault_decrypt`
  guard `license_number` (the one field in `_VAULT_PII_FIELDS`) per PIPEDA
  — every new test either uses a fully-mocked fake `supabase_client` module
  (via `sys.modules` patching, since the function does an inline
  `from supabase_client import supabase`) or exercises the documented
  fail-closed (encrypt raises 503) / fail-open (decrypt degrades to raw
  token) contracts explicitly. No real Vault/Supabase RPC was invoked; no
  plaintext PII was written or logged by any new test.
- **Insurance-period adjacency**: the new `update_my_driver` vehicle-change
  test asserts `record_period_transition(driver_id, 0)` fires when an
  active, online driver edits vehicle/document fields (existing behavior,
  documented in the function's own comments) — this is the one place
  `profile.py` touches the Period 0–3 state machine from root CLAUDE.md.
  No change to that ordering; only new coverage of the existing call.
- **No production code touched** — nothing to regress in ride state,
  dispatch, or money paths. The ride-route-snapshot pipeline tested here
  (`_generate_and_store_ride_snapshot`) writes to Supabase Storage and the
  `rides`/`ride_routes`/`ride_route_snapshot_objects` tables in production,
  but every new test replaces both the Supabase Storage client and every DB
  call with mocks — no real writes.

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | New file — 59 tests | Close coverage gap on `_shared.py`/`status.py`/`profile.py` |
| `docs/change-log/2026-08-02-a1c-drivers-shared-batch-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface (drivers) |
| `ACTION_ITEMS.md` | Updated A1c Sub-tier A's `_shared.py`/`status.py`/`profile.py` bullet | Track progress per the existing series format; merged alongside two concurrent sibling sessions' edits to the same bullet |

## 7. Before / after

Not applicable — purely additive test file; no existing behavior-changing diff.

**Bug found, not fixed (test-only scope, no wide blast radius — internal to
`_generate_and_store_ride_snapshot`'s own error handling, not a live-data
risk):** the private (v2/revisioned) route-snapshot reference write's
`except Exception as exc:` block (lines ~460–465) both logs the error *and*
re-raises it — but the caller is the function's own outermost
`try`/`except Exception` (lines ~280/492), which catches that re-raise and
only logs a second time. Net effect: the `raise` is dead code — it can never
propagate past the outer handler, so the double-log is harmless (not a
correctness bug, no data loss — the object was already uploaded and the
ledger entry, if it landed, still lets a backfill job reconcile it) but the
`raise` reads as intentional signal-propagation to a caller that never
receives it. Not fixed per task scope (test-only, no application-code
changes); noted here rather than silently working around it with a test
that pretends the exception propagates.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration, nothing to flag off.

## 9. Verification performed

- [x] Fresh baseline measured (full suite):
  `pytest tests/ -q --cov=routes.drivers._shared --cov=routes.drivers.status
  --cov=routes.drivers.profile --cov-report=term-missing --no-cov-on-fail`
  — **`_shared.py`: 228 stmts, 111 missing (51%)**; **`status.py`: 31
  stmts, 16 missing (48%)**; **`profile.py`: 136 stmts, 44 missing (68%)**.
  `7263 passed, 8 skipped, 1 xfailed, 0 failed` (625s) — matches the
  documented ACTION_ITEMS.md baseline percentages closely enough to confirm
  no material drift.
- [x] New test file run alone:
  `pytest tests/test_drivers_shared_status_profile_coverage.py -q --no-cov`
  — **59 passed**.
- [x] Run together with every other test file that already touches these
  three modules (`test_drivers_extended.py`, `test_go_online_availability.py`,
  `test_p1_driver_offline.py`, `test_pickup_otp_not_leaked.py`,
  `test_snapshot_renderer_policy.py`, `test_sgi_forms_route.py`,
  `test_compliance_reports_http.py`, `test_p2_payout_t4a.py`,
  `test_admin_drivers_coverage.py`, `test_driver_statement.py`) with
  coverage:
  `pytest tests/test_drivers_shared_status_profile_coverage.py
  tests/test_drivers_extended.py tests/test_go_online_availability.py
  tests/test_p1_driver_offline.py tests/test_pickup_otp_not_leaked.py
  tests/test_snapshot_renderer_policy.py tests/test_sgi_forms_route.py
  tests/test_compliance_reports_http.py tests/test_p2_payout_t4a.py
  tests/test_admin_drivers_coverage.py tests/test_driver_statement.py -q
  --cov=routes.drivers._shared --cov=routes.drivers.status
  --cov=routes.drivers.profile --cov-report=term-missing --no-cov-on-fail`
  — **327 passed**, no collisions. Coverage:
  - `routes/drivers/_shared.py`: **228 stmts, 8 missing → 96%** (up from
    51%). The 8 remaining lines are `_require_ride_in_state` (lines
    565–585), deliberately left for the concurrent sibling session
    (`claude/a1c-drivers-ride-flow-batch`) whose files actually call it —
    see section 4.
  - `routes/drivers/status.py`: **31 stmts, 0 missing → 100%** (up from
    48%).
  - `routes/drivers/profile.py`: **136 stmts, 0 missing → 100%** (up from
    68%).
- [x] Full backend suite re-run after adding this session's tests:
  `pytest tests/ -q --no-cov` — see section 11 for the exact after-count
  (captured post-merge, once concurrent sibling sessions' commits were
  incorporated, per the task's step 7 instruction to re-run after merging
  main).
- [x] Blast-radius grep performed: see section 4 above, every real
  in-package and cross-package caller of `_shared.py` enumerated.
- [x] Reviewed against CLAUDE.md conventions: patch targets follow this
  module's dual-binding pattern — `db_supabase.<fn>` (module reference) for
  generic CRUD, the dual-import-per-call-site source module
  (`settings_loader.get_app_settings`, `utils.route_snapshot.render_ride_snapshot[_google]`,
  `supabase_client.supabase`, `core.config.settings`,
  `utils.route_distance.compute_road_route`,
  `utils.route_validation.validate_trip_route`,
  `utils.vehicle_history.record_vehicle_changes`,
  `utils.driver_status_notifications.notify_driver_status_change`) via the
  `_patch_both` helper copied from `test_snapshot_renderer_policy.py`, and
  `_deps.<name>` for the bound-name copy (`_deps.record_period_transition`)
  — matching the conventions documented at the top of the new test file.
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference.
- [ ] Feature-flagged — not applicable, test-only.

## 10. What was NOT verified

- Not run against real Supabase, real Supabase Storage, or a real Vault
  (`encrypt_driver_pii`/`decrypt_driver_pii` RPC) — every DB/storage/RPC
  call is mocked throughout, matching repo convention for this test tier.
- `_generate_and_store_ride_snapshot`'s actual PNG rendering
  (`render_ride_snapshot`/`render_ride_snapshot_google` themselves, and the
  underlying Google Static Maps / OSM HTTP calls) is not exercised here —
  both renderers are replaced with fakes returning literal byte strings;
  the renderer internals have their own test coverage elsewhere
  (`utils/route_snapshot.py`, separately scoped, not part of this batch).
- The `_require_ride_in_state` gap in `_shared.py` (8 remaining lines) is
  deliberately unclosed in this branch — left for the concurrent sibling
  session whose files (`ride_flow.py`/`ride_cancel.py`/`ride_reads.py`)
  actually call it, to avoid duplicate/conflicting test-writing effort on
  the same lines during the same day (the `ride_repo.py` precedent in
  ACTION_ITEMS.md shows duplicate coverage across sessions is tolerated,
  but here avoiding it was straightforward since the natural owner is
  self-evident).
- No production code touched — nothing to regress in ride state, dispatch,
  or money paths; this was a pure test-coverage exercise per task
  instructions. One dead-code observation is flagged in section 7 above
  (not a bug in outcome, just an unreachable `raise`) — not fixed per
  scope.
