# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | dispatch, payments, corporate |
| PR / commit link | branch `claude/e5-kill-switches` |
| Related issue or gap ID | `ACTION_ITEMS.md` E5 |

## 1. Issue / gap identified

Three of the platform's riskiest automatic subsystems — the surge pricing engine, promo redemption, and corporate billing's automatic money movement — had no way to pause them short of a full deploy. A fourth, scheduled dispatch, already had a working backend flag but no way to set it via the admin API or dashboard.

## 2. Root cause

These subsystems were built without an incident-response "pause" mechanism from the start; `app_settings` already covers steady-state config (e.g. per-area surge toggles) but nothing gates the automatic loops/paths themselves globally.

## 3. Fix / remediation

Four dark-launched, default-on `app_settings` boolean flags, one per subsystem (not per loop), each checked at the entry point of its gated path, fail-open on a settings-read error:

- `scheduled_dispatch_enabled` — already existed backend-side (2026-08-02); added to `SettingsUpdateRequest` (previously unsettable via the admin API at all) and gave it a dashboard toggle.
- `surge_engine_enabled` — new. Gates `utils/surge_engine.py::recalculate_all_surges`'s automatic recompute, independent of the existing per-area `surge_source`/`surge_enabled` controls.
- `promo_redemption_enabled` — new. Gates `routes/promotions.py::_validate_promo_for_user`, the single chokepoint both the rider self-service and admin-on-behalf-of-rider promo paths already share.
- `corporate_billing_enabled` — new. Gates `services/payment_service.py::settle_corporate` and 4 corporate background loops (autotopup, low-balance, allowance reset, KYB reverification — layered on top of that last loop's own pre-existing specific toggle). Deliberately does not gate the low-level `corporate_wallet_service.py` helpers an admin uses for manual corrections during an incident.

Admin dashboard: one new "Kill Switches" card (Settings → Operations tab) with all 4 toggles and help text on each flag's scope boundaries.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated per flag, each touching only its own subsystem's entry point(s).** No shared code path between the 4 flags except the common `get_app_settings()` mechanism every other dark-launched flag in this codebase already uses.
- **Other readers of the gated functions:** `recalculate_all_surges()` has no other callers besides its own loop and its existing test suite. `_validate_promo_for_user()` is called by exactly the two documented paths (rider `/promo/apply`, admin `apply_promo_for_admin`) — both now covered by the single check. `settle_corporate()` has one caller (the ride-payment settlement dispatcher) — unaffected by this change beyond the new guard. The 4 corporate loops are each spawned exactly once in `core/lifespan.py`, unaffected elsewhere.
- **A formatter-hook gotcha discovered mid-implementation:** a hook in this repo strips additions to some files' module-level except-branch dual-import lists (confirmed hitting it in `routes/promotions.py`; the fix landed and was verified with a passing test run before commit). Worked around by using a lazy (function-local) dual import for `get_app_settings` in every subtask after that discovery, matching `services/payment_service.py::_atomic_settle_enabled`'s pre-existing identical workaround. Flagging this as a real, previously-undocumented repo quirk worth knowing for any future dual-import addition to an except-branch.
- **No ride-state-machine, WebSocket, or schema/migration changes.** All 4 flags are additive booleans on the existing `app_settings` table/row.

## 5. User-experience effect

None today — all 4 flags default `True` (today's always-on behavior) and none has been flipped off anywhere. If an admin does flip one off during a future incident: surge pricing freezes at its last value (riders/drivers see no immediate price change, just no further recompute); promo codes stop applying (a rider attempting to redeem gets a clear 503, not a silent failure); corporate ride settlement and background wallet processes pause (a corporate ride's payment_status stays `pending` with a 503 rather than silently succeeding or failing ambiguously). Internal-admin-facing only for the toggle itself — new "Kill Switches" card on the Settings page.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | +3 flags (`surge_engine_enabled`, `promo_redemption_enabled`, `corporate_billing_enabled`), all default `True` | Kill-switch schema |
| `backend/routes/admin/settings.py` | +3 new fields, +1 fix (`scheduled_dispatch_enabled` was missing) on `SettingsUpdateRequest` | Admin API exposure |
| `backend/utils/surge_engine.py` | Flag check in `recalculate_all_surges()` | Surge engine gate |
| `backend/routes/promotions.py` | Flag check in `_validate_promo_for_user()` | Promo redemption gate |
| `backend/services/payment_service.py` | Flag check as first guard in `settle_corporate()` | Corporate ride-settlement gate |
| `backend/utils/corporate_autotopup.py`, `corporate_low_balance.py`, `allowance_reset.py`, `kyb_reverification.py` | Flag check at top of each tick function | Corporate loop gates |
| `admin-dashboard/src/app/dashboard/settings/page.tsx` | New "Kill Switches" card, 4 toggles | Admin visibility/control |
| 7 backend test files | ~24 new tests (flag-off, missing-key-defaults-enabled, settings-error-fails-open per gated path) | Coverage |
| `ACTION_ITEMS.md` | E5 entry closed | Tracking |

## 7. Before / after

```python
# Before (services/payment_service.py::settle_corporate)
async def settle_corporate(ride, ride_id, total_charge, tip_amount):
    """Corporate allowance + master wallet saga."""
    company_id = ride.get("corporate_account_id")
    if not company_id:
        return PaymentResult(success=False, error="Corporate account not set on ride", status_code=400)
```
```python
# After
async def settle_corporate(ride, ride_id, total_charge, tip_amount):
    """Corporate allowance + master wallet saga."""
    try:
        from ..settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings
    try:
        settings = await get_app_settings()
        if not settings.get("corporate_billing_enabled", True):
            return PaymentResult(success=False, error="Corporate billing is temporarily disabled", status_code=503)
    except Exception as settings_err:
        logger.warning("[PAYMENT] app_settings lookup failed ({}), proceeding as enabled", settings_err)

    company_id = ride.get("corporate_account_id")
    if not company_id:
        return PaymentResult(success=False, error="Corporate account not set on ride", status_code=400)
```

## 8. Rollback plan

`git-revert-safe` for all 4 flags — no migration, no data mutation, and since none has ever been flipped off in production, there's no live state to reconcile even before a code revert. If a flag IS flipped off and needs reverting during an incident, that's a single `PUT /api/admin/settings` call (or the new dashboard toggle) — instant, no deploy.

## 9. Verification performed

- [x] Automated tests run: ~24 new tests across `test_kill_switch_flags.py` (new), `test_surge_engine.py`, `test_routes_promotions_coverage.py`, `test_corporate_ride_payment.py`, `test_corporate_autotopup.py`, `test_corporate_low_balance.py`, `test_corporate_allowance_reset.py`, `test_kyb_reverification.py` — all pass. Full regression sweep of every touched subsystem's complete test-file set (surge: 34/34; promo: 181/181 across 9 files; corporate payment: 100/100 across 11 files; autotopup: 18/18; low-balance: 13/13; allowance-reset: 19/19; KYB: 14/14) — 0 failures, 0 regressions anywhere.
- [x] Admin dashboard: real `npm run build` (exit 0) — no existing test file for the settings page to extend (checked first, not invented).
- [x] Blast-radius grep performed for each gated function/loop — see §4.
- [x] Reviewed against relevant `CLAUDE.md` conventions: additive/dark-launch via the existing `app_settings` pattern, fail-open on settings-read errors (never let a lookup hiccup itself act as a kill switch), no PII added to logs.
- [x] Feature-flagged: all 4, default on, matches every other kill-switch precedent in this codebase.

**What was NOT verified:**
- No live-Supabase/live-Redis integration test — unit-level against mocked fixtures, this repo's established convention for this class of change.
- No staging repro of any flag actually pausing its subsystem end-to-end against a live backend.
- No manual click-through of the new admin-dashboard toggles in a running app — verified via a real production build only.
- None of the 4 flags has been flipped off anywhere; this is a pure capability addition, not yet exercised in anger.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (instant flag flip, or a plain revert with zero data footprint)
- [x] Blast radius is stated, not assumed (isolated per flag, enumerated in §4)
- [x] No silent behavior change to an already-shipped flow — every flag defaults to today's exact behavior; the only real behavior change (the `scheduled_dispatch_enabled` admin-API fix) is additive capability, not a change to what already ships
