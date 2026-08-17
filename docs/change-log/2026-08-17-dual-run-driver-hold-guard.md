# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers / dispatch / payments |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/4134 |
| Related issue or gap ID | Fixes #4104 (CR-4104, ACTION_ITEMS.md A34) — code-guard half only |

## 1. Issue / gap identified

Nothing in this codebase is aware the old (previous) rideshare app still exists during the dual-run cutover window. A driver imported from the old app can go online, get dispatched, and get paid out in Spinr while simultaneously active on the old app, and the system would never notice, refuse, or log it. Two failure modes follow from this: double-dispatch (conflicting insurance-period truth, wrong ETAs) and double-payout (same driver paid by both systems via a potentially-shared Stripe Connect account — see `stripe_mapping_import_service.py`'s own docstring).

## 2. Root cause

The dual-run cutover (old app + new Spinr app running in parallel ahead of the tentative Oct 31, 2026 old-app decommission) was never designed with any cross-system signal. No field, join, webhook, or shared table anywhere in this codebase knows the old app exists. `utils/dual_run_monitor.py` (a prior session's work on the same audit finding, A34/P3.1) added *observation-only* signals — a counter and an audit-log row on first go-online, a payout counter — but explicitly does not block anything.

## 3. Fix / remediation

Adds the **optional code guard** the CR itself scopes out as a separate, engineering-owned follow-up to the roster-coordination policy question (which remains organizational, not code, and is explicitly NOT resolved by this PR):

1. **New column** `drivers.dual_run_hold BOOLEAN NOT NULL DEFAULT FALSE` (migration 327) — admin-settable, no automated writer anywhere.
2. **Go-online guard** (`routes/drivers/status.py::update_driver_status`) — rejects `is_online=True` with a clear 403 when `dual_run_hold=True` AND the driver carries non-empty `legacy_import_metadata`. Placed immediately after the existing authorization check, before any other status/document/subscription gate.
3. **Payout guard** (`routes/drivers/payouts.py::request_payout`) — same check, same error, placed before the endpoint's existing unconditional 410 (standard cashout has been disabled platform-wide since an earlier change — see §4 "Important scoping note" below).
4. Reuses the existing `ErrorCode.DRIVER_NOT_AVAILABLE` / new `ErrorKeys.DRIVER_DUAL_RUN_HOLD` i18n key (added to `driver-app/i18n/en.json` only, matching `error_keys.py`'s own documented convention).

**Deliberate scope reduction from the CR's own description** (stated explicitly, not silently): the CR names three enforcement points — go-online, `dispatch_service.claim_driver`, and payout. This PR implements only go-online and payout. Blocking go-online is sufficient to keep a `dual_run_hold` driver out of the dispatch pool entirely: `is_available` (what `claim_driver`/dispatch actually reads) can only ever become `True` through this same go-online endpoint, so a driver who can never go online can never be claimed. Guarding `claim_driver` separately would be defense-in-depth but is a second, independent code path with its own blast radius (services/dispatch_service.py:369-383 and :555-566) — deferred to a follow-up if the simpler single-gate approach is found insufficient in practice.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), two existing endpoints touched, one new column, zero existing behavior changed.**

**The key safety property: this is a pure no-op on merge, by construction.** `dual_run_hold` defaults to `FALSE` for every row (existing and new) and nothing in this codebase — no migration backfill, no importer, no background loop, no other route — ever sets it to `TRUE`. The guard clauses in both endpoints are `if is_online and driver.get("dual_run_hold") and driver.get("legacy_import_metadata")`, so for every driver in production today (all `dual_run_hold` rows read `False`) the added `if` evaluates false and execution falls through to the exact pre-existing code path, unchanged. The guard only has any effect once a human operator manually flips the flag for a specific driver — which nothing in this PR does, and no admin-dashboard UI yet exists to do (deliberately out of scope; flag is settable via direct DB/future admin work only).

**Blast-radius grep performed:**

- **`update_driver_status` (go-online, PUT `/drivers/{driver_id}/status`)**: the only Python-level callers are the router mount (`routes/drivers/__init__.py`), the god-file re-export (`routes/drivers/_shared.py`, `documents.py`, `routes/admin/documents.py`, `utils/spinr_pass.py`, `routes/drivers/ride_complete.py` all *reference* the module for docstrings/comments, not call the function directly), and the test suite (`test_go_online_availability.py`, `test_p1_driver_offline.py`, `test_driver_status_notifications.py`). No other production code path calls this function directly — it is reached only via the HTTP route. The new guard sits before every existing check (ban/suspended/needs-review, document expiry, subscription, quota) and before the actual DB write, so none of those existing checks or the write itself changes for an unflagged driver.
- **`request_payout` (POST `/drivers/payouts`)**: only caller is the router mount and the test suite. **Important scoping note, confirmed while implementing this PR**: this specific endpoint has been fully disabled since an earlier change — it unconditionally raises `410` for every caller today, before this PR and after it. The live payout paths are `request_instant_payout` (`payouts.py:1044`, fee-bearing instant cashout) and the weekly `auto_payout.py` background loop (automatic Sunday payouts, ≥$10 balance). **Neither of those two live paths is touched by this PR** — the guard was added at the exact location the CR names (`request_payout`, ~line 799-883) per the task's explicit scope, but because that endpoint is already dead code (always 410), this specific guard currently provides no additional real money-safety protection. It is added anyway so the guard's presence is consistent and future-proof if standard cashout is ever re-enabled. **Flagging this as a real gap for fast follow-up**: if the guard is meant to have live teeth against double-payout, `request_instant_payout` and the `auto_payout.py` weekly sweep are the two paths that actually move money today and would need the same check. This was not silently expanded into scope — see `AskUserQuestion`-equivalent judgment call recorded here rather than unilaterally touching two more files.
- **`drivers.dual_run_hold` column**: grepped for every reader — only the two new guard clauses (`status.py`, `payouts.py`) and this PR's own tests reference it. No admin route, serializer, or background loop reads or writes it. Because `routes/drivers/_shared.py::serialize_doc` is an identity function (returns the driver row as-is), the new column will appear as `dual_run_hold: false` in the driver's own `GET /drivers/{driver_id}` response and any admin driver-detail response once this migration lands — not a security issue (a boolean, not PII, defaults false, no RLS column-level restriction exists on `drivers` today), but noted as a minor additive surface-area increase on those existing read paths.
- **`utils/dual_run_monitor.py`**: not modified. Its `is_legacy_driver()` helper checks the same `legacy_import_metadata` truthiness this PR's guard clauses check inline — not reused directly (kept the two guard sites free of a new cross-module import for a one-line boolean check), but the same semantics.
- **No ride state machine, wallet, or Stripe-webhook interaction. No dispatch code touched** (`services/dispatch_service.py` is unmodified — see the deliberate scope-reduction note in §3).

## 5. User-experience effect

- **Driver-facing**: none by default (the no-op property above). For the small population an operator manually flags in the future, going online or attempting payout returns a specific, translated error ("Your account is on hold while active on the previous app. Please contact support.") instead of either succeeding silently (today's bug) or the endpoint's normal error path. Not visible mid-session to any driver today, because no driver has this flag set.
- **Admin-facing**: none — no admin UI reads or writes this column yet; an operator would need direct DB access to set it until a follow-up adds an admin toggle.
- **Rider-facing**: none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/327_drivers_dual_run_hold.sql` | New additive column `drivers.dual_run_hold BOOLEAN NOT NULL DEFAULT FALSE` | Storage for the admin-settable hold flag |
| `backend/routes/drivers/status.py` | New guard clause in `update_driver_status`, before the ban/suspended checks | Blocks go-online for a held, legacy-imported driver |
| `backend/routes/drivers/payouts.py` | New guard clause in `request_payout`, before the existing unconditional 410; added `ErrorKeys`/`ErrorCode`/`SpinrException` imports | Blocks payout for a held, legacy-imported driver |
| `backend/utils/error_keys.py` | New constant `DRIVER_DUAL_RUN_HOLD = "errors.driver.dual_run_hold"` | i18n key for the new error |
| `driver-app/i18n/en.json` | New `errors.driver.dual_run_hold` string | English copy for the new error (only `en.json` required per `error_keys.py`'s documented convention) |
| `backend/tests/test_dual_run_hold_guard.py` | New test file, 6 tests | Covers: blocked when flagged, unaffected when default/false, unaffected when flagged but not legacy-imported, offline transition unaffected, payout blocked/unaffected |

## 7. Before / after

**Go-online (`routes/drivers/status.py`):**

```python
# Before
if driver.get("user_id") != current_user["id"]:
    raise HTTPException(status_code=403, detail="Not authorized")

# Ban check: prevent banned drivers from going online
if is_online and driver.get("status") == "banned":
    ...
```

```python
# After
if driver.get("user_id") != current_user["id"]:
    raise HTTPException(status_code=403, detail="Not authorized")

# CR-4104 / A34 dual-run cutover guard: ... (see file for full comment)
if is_online and driver.get("dual_run_hold") and driver.get("legacy_import_metadata"):
    raise SpinrException(
        message="Your account is on hold while active on the previous app. Please contact support.",
        error_code=ErrorCode.DRIVER_NOT_AVAILABLE,
        status_code=403,
        message_key=ErrorKeys.DRIVER_DUAL_RUN_HOLD,
        action_hint="Contact support",
    )

# Ban check: prevent banned drivers from going online
if is_online and driver.get("status") == "banned":
    ...
```

**Payout (`routes/drivers/payouts.py`):**

```python
# Before
async def request_payout(current_user: dict = Depends(get_current_user)):
    """Standard cashout is disabled ..."""
    raise HTTPException(status_code=410, detail="Cash out has been replaced ...")
```

```python
# After
async def request_payout(current_user: dict = Depends(get_current_user)):
    """Standard cashout is disabled ..."""
    _driver_row = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user.get("id")}, limit=1)
    )
    if _driver_row and _driver_row.get("dual_run_hold") and _driver_row.get("legacy_import_metadata"):
        raise SpinrException(..., status_code=403, message_key=ErrorKeys.DRIVER_DUAL_RUN_HOLD, ...)
    raise HTTPException(status_code=410, detail="Cash out has been replaced ...")
```

## 8. Rollback plan

**`git-revert-safe`, additive-only.** No live data is mutated by this change:

- **Code revert**: `git revert` the guard-code commit(s) — the endpoints return to their exact prior behavior. Safe at any time; no driver has `dual_run_hold=True` in production (nothing sets it), so no in-flight request is affected by reverting.
- **Column revert**: the migration's own rollback comment — `ALTER TABLE public.drivers DROP COLUMN IF EXISTS dual_run_hold;` — is safe to run at any time; nothing else in the codebase reads or writes the column (see blast-radius grep above), so dropping it cannot orphan any other write path.
- **Fastest rollback if ever needed live**: none should be needed, since the flag is never auto-set — but if an operator ever mistakenly flags a driver, the fix is a single `UPDATE drivers SET dual_run_hold = false WHERE id = '<id>'`, no deploy required.

## 9. Verification performed

- [x] Automated tests: added `backend/tests/test_dual_run_hold_guard.py` (6 new unit tests, all mocked via `patch(...db_supabase...)`, no real DB). Ran via `pytest backend/tests/test_dual_run_hold_guard.py -v` — **6 passed**.
- [x] Regression check: ran the pre-existing `test_go_online_availability.py`, `test_p1_driver_offline.py`, `test_driver_status_notifications.py`, `test_auto_payout.py`, `test_stripe_account_discovery.py` alongside the new file — see PR body for the exact pass count recorded at PR-open time.
- [x] `python3 backend/scripts/check_migration.py backend/migrations/327_drivers_dual_run_hold.sql` — all hard checks passed (naming, sequence, RLS-skip-correct, rollback-comment present, no destructive ops).
- [x] `ruff check` on all 3 modified Python files — all checks passed.
- [x] Blast-radius grep performed — see §4, both for the two guarded endpoints and for the new column itself.
- [x] Reviewed against CLAUDE.md conventions: additive-only migration (no RLS needed, no new query pattern so no index), dual-import pattern followed in `payouts.py`'s try/except block, `ErrorCode`/`ErrorKeys`/`SpinrException` used instead of a bare `HTTPException` string for the new error (matches the rest of `status.py`'s gating style).
- [ ] **No real production build was run** — this is a backend-only Python change; `admin-dashboard`/`rider-app`/`driver-app` frontend build steps do not apply (only a JSON i18n string was added to `driver-app`, no compiled/bundled code path touched).

## 10. What was NOT verified

- **Not exercised against a live Supabase instance** — only `mock_supabase_client`-equivalent hand-mocked `db_supabase` calls (matching the existing pattern in `test_go_online_availability.py`), per this repo's stated unit-test convention. The migration itself was checked with `check_migration.py` (static checks) but not applied to a real database in this session.
- **No visual regression tooling exists for `driver-app`** (per CLAUDE.md's standing gap) — the new i18n string was reasoned about, not screenshotted in the driver app UI.
- **`request_payout`'s guard has no live effect today** — see §4's "Important scoping note." This is stated explicitly, not left implied by the passing tests (the tests confirm the *code path* is correct; they do not claim the endpoint is reachable in normal driver usage, because it is not).
- **The dispatch-claim path (`services/dispatch_service.py::claim_driver`) is untouched** — deliberate scope reduction, restated from §3, not an oversight.
- **No admin-dashboard UI to set the flag** — an operator must use direct DB access until a follow-up PR (out of scope here) adds one.
