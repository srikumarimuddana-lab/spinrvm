# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (session), reviewed with @vikas |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/corporate-module-review-6eh65j` |
| Related issue or gap ID | Corporate module lifecycle audit — Finding 1 |

## 1. Issue / gap identified

Suspending or closing a corporate account stopped its **existing** pre-pickup rides (gap #1, shipped earlier in this branch) but did nothing to stop **new** ones. A suspended or closed company's still-active members could keep creating brand-new `company_allowance` and `work_profile` rides indefinitely — the only effect of suspension was a one-time cleanup of whatever was in flight at that exact moment.

## 2. Root cause

Neither of the two corporate booking paths in `routes/rides/booking.py` (`company_allowance` payment method, and `work_profile=true`) ever read `corporate_accounts.status`. The `work_profile` path resolves membership via `list_active_memberships_for_user`, which filters only on the *member's* own status — never the company's. The `company_allowance` path's fail-closed membership check (gap #3, same branch) also only checks membership status, not company status. A systematic lifecycle audit (matrix of every corporate lifecycle event × every expected cascade effect) found this as the one row/column combination none of gaps #1–#3 covered — cited as Finding 1.

Separately confirmed during this fix: `routes/corporate_company_bookings.py`'s guest-booking path (`POST /company/{company_id}/bookings`) is **not** affected — it already calls `_require_company_active()` before every booking (tagged `M2.6` in its own code), which rejects anything other than `status == "active"`. This gap was confined to the rider's own in-app booking flow.

## 3. Fix / remediation

Both corporate booking paths in `routes/rides/booking.py` now read the company's `status` before proceeding:

- **`company_allowance` path**: if `corporate_accounts.status` is `suspended` or `closed`, reject with 403, `failed_rules: ["company_inactive"]`, before the policy/membership checks run (cheaper failure, and semantically "is this company even active" is a precondition to "does this fare pass policy").
- **`work_profile` path**: same check, matching that path's own existing convention — 400 with `detail: {"reason": "company_inactive"}` (not 403/`failed_rules`, to stay consistent with its sibling checks `no_corporate_membership` / `policy_violation` in the same block).
- Blocks **only** the corporate payment method for that specific booking — the rider can still book with a personal card/wallet. Mirrors gap #3's scope exactly.
- Gated behind new `app_settings.corporate_inactive_company_blocks_booking`, **default `true`** — same reasoning as gaps #1/#3: the un-flagged behavior (fail open) was the bug, so the fix ships on by default with the flag as an emergency kill-switch.

## 4. Risk & impact on existing functionality

- **Blast radius: two call sites in one file**, both are pre-dispatch guards inside `create_ride` — no other route calls into either code block. Grepped every other caller:
  - `routes/corporate_company_bookings.py`'s `create_booking` — already independently protected via `_require_company_active`, untouched by this change, confirmed by reading its code (§2 above).
  - No other route in the codebase reads `body.corporate_account_id`/`body.work_profile` inside `create_ride` besides these two blocks.
- New DB read: one extra `get_corporate_account_by_id` call per corporate booking attempt — a single indexed row read, no measurable SLA risk (this endpoint already does 5+ similar reads before this point).
- Money impact: none directly — this only blocks a booking attempt from being created; no fee, charge, or wallet delta happens either way (the rider simply can't create the ride at all with that payment method).
- Interaction with gaps #1–#3: complementary, not overlapping. Gap #1 cancels rides that already exist at the moment of suspension; this fix stops new ones. Gap #2 (wallet refund on close) makes this fix more urgent — a closed company's wallet may already be at zero, so a booking that slipped through previously would have silently fallen through to `payment_status="pending"` or a master-wallet negative-floor fallback at settlement instead of failing loudly at booking time.

## 5. User-experience effect

- **Rider (member of a suspended/closed company)**: attempting to book a `company_allowance` or work-profile ride now gets an immediate, clear rejection ("Your company account is currently inactive...") instead of either a ride that silently fails to bill later, or (for `work_profile`) a generic `reason` code the app must already handle (matches the existing `no_corporate_membership`/`policy_violation` UX pattern for that path).
- **Corporate admin**: no change — this is a consequence of the existing suspend/close action, not a new admin-facing control.
- Not visible mid-session to anyone already on an active ride — this only fires at the moment of a *new* booking attempt.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/booking.py` | Both corporate booking paths (`company_allowance`, `work_profile`) now check company status before proceeding | Core fix — Finding 1 |
| `backend/schemas.py` | New `AppSettings.corporate_inactive_company_blocks_booking: bool = True` | No-redeploy rollback switch |
| `backend/tests/test_create_ride_remaining_branches.py` | +5 tests: suspended/closed blocks `company_allowance`, active control case, flag-off rollback, work_profile blocks; +7 existing tests updated to mock the new `get_corporate_account_by_id` call | Regression coverage + fixture drift fix (same pattern gap #3 caused in `test_corporate_surge_bypass.py`) |

## 7. Before / after

```python
# Before — routes/rides/booking.py, company_allowance path
if body.corporate_account_id and body.payment_method == "company_allowance":
    _policy_result = await _deps.evaluate_policy_for_ride(...)
    # ... policy check only; company status never read
```

```python
# After
if body.corporate_account_id and body.payment_method == "company_allowance":
    _bk_settings_company = await _deps.get_app_settings() or {}
    if _bk_settings_company.get("corporate_inactive_company_blocks_booking", True):
        _corp_company_row = await _deps.db_supabase.get_corporate_account_by_id(body.corporate_account_id)
        if _corp_company_row and (_corp_company_row.get("status") or "").lower() in ("suspended", "closed"):
            raise HTTPException(status_code=403, detail={"message": ..., "failed_rules": ["company_inactive"]})
    _policy_result = await _deps.evaluate_policy_for_ride(...)
```

(Same shape applied to the `work_profile` block, with that path's own 400/`reason` error convention.)

## 8. Rollback plan

**Immediate, no-redeploy**: flip `app_settings.corporate_inactive_company_blocks_booking` to `False` from the admin dashboard. A plain `git revert` is also safe — no schema/data migration involved, no fee/wallet delta is charged by this code either way.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_create_ride_remaining_branches.py tests/test_corporate_ride_payment.py tests/test_corporate_surge_bypass.py tests/test_coverage_rides.py tests/test_corporate_settle_suspended_audit_flag.py tests/test_company_guest_booking.py tests/test_guest_auto_settle.py tests/test_allowance_cap_fallback.py tests/test_allowance_rpc_sign_contract.py tests/test_corporate_suspension_service.py tests/test_corporate_wallet_winddown_service.py tests/test_corporate_member_offboarding_service.py tests/test_corporate_company_routes.py tests/test_corporate_status.py tests/test_corporate_allowance_reset.py tests/test_c_allowance_reset_atomic.py -q` — 304 passed.
- [x] `ruff check` and `ruff format --check` clean on all changed files.
- [x] Blast-radius grep performed: confirmed `corporate_company_bookings.py`'s guest-booking path is independently protected (§2).
- [x] Reviewed against relevant CLAUDE.md conventions: fail-closed booking-time checks (same pattern as gap #3), "do not silently swallow errors" (settings-fetch failure defaults to `{}` → flag defaults `True` → fail-closed, not fail-open), feature-flagged for a live-tested surface.
- [ ] Manual repro against real Supabase / running app — **not done**, no dev environment available in this session.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip; plain revert also safe)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow — this only affects the moment a *new* booking is attempted; nothing mid-ride changes

## What was NOT verified

- No real or test-mode Supabase call was exercised — only mocked `AsyncMock`/`patch` unit tests.
- The `work_profile` booking path's client-side handling of the new `{"reason": "company_inactive"}` detail shape was not verified against the rider app (no app build available in this session) — the shape matches its sibling `no_corporate_membership`/`policy_violation` codes, so the client should already have a generic handler for unrecognized `reason` values, but this is inferred from code, not observed.
