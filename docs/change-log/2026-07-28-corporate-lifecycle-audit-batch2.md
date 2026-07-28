# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (session), reviewed with @vikas |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/corporate-lifecycle-batch2` (follow-up to PR #2615, merged) |
| Related issue or gap ID | Corporate module lifecycle audit — Findings 2, 3, 4, 6, 9 (batch 2 of the audit's P1/P2 findings; Finding 5 is documented as accepted risk, not implemented — see §11) |

## 1. Issue / gap identified

PR #2615 fixed the two P0 findings from a structured corporate lifecycle audit (Finding 1: suspended/closed companies could still book new rides; Finding 7 Phase A: policy changes weren't audit-visible). This batch closes the remaining P1/P2 findings from the same audit — all lower-judgment, same established fix patterns (status guard, or missing audit-log call), none requiring a new product decision.

| # | Finding | Symptom |
|---|---|---|
| 2 | `allowance_reset_loop` only checked member status, never company status | A suspended company's still-active members kept getting their monthly allowance auto-refilled |
| 3 | `corporate_low_balance_loop` had no company-status check at all | A suspended/closed company kept receiving "top up your wallet" emails indefinitely |
| 4 | Reactivating a company (suspended→active) never restores `auto_topup_enabled` | Silent, unnoticed loss of auto-topup convenience after reactivation |
| 6 | Member reactivation (`suspended`→`active`) skipped the audit-log call entirely | The access-*granting* mirror of removal (which IS logged) went unaudited |
| 9 | `corporate_wallet.py`'s top-up/adjust/config endpoints, plus member-invite and policy create/edit, never called `log_admin_action`/`log_user_action` | Money-moving and access/policy-changing admin actions left no trail in the unified audit table — only the wallet ledger (a different table) recorded top-up/adjust actor+notes |

## 2. Root cause

Same shape as the original three gaps and Findings 1/7: each background loop, reactivation path, and admin endpoint was written and tested in isolation, without a systematic check against every other corporate lifecycle event or every other admin endpoint's own established audit-logging convention. The structured lifecycle matrix (rows = every lifecycle event, columns = every expected cascade effect) is what caught these — none were found by incident or bug report.

## 3. Fix / remediation

- **Finding 2**: `utils/allowance_reset.py::run_allowance_reset_tick` now fetches the member's company via `get_corporate_account_by_id` and skips the reset (same as the existing member-status skip) unless `status == "active"`.
- **Finding 3**: `utils/corporate_low_balance.py::_notify_one` skips the email when the company's status isn't `"active"`.
- **Finding 4**: `routes/corporate_accounts.py::change_company_status` now checks, on a `suspended→active` transition, whether the wallet's `auto_topup_enabled` is still `False`, and surfaces `auto_topup_needs_review: true` in the `change_company_status` audit-log entry. **Deliberately does not auto-restore the toggle** — see §4 for why.
- **Finding 6**: new `_maybe_log_reactivation` helper in `routes/corporate_company.py`, mirroring `_maybe_revoke_access_on_removal`'s idempotent-transition-guard pattern, fires `log_user_action` on `removed`/`suspended` → `active`.
- **Finding 9**: added `log_admin_action`/`log_user_action` calls (same try/except/logger.error pattern used everywhere else in these files) to: `manual_topup`, `manual_adjust`, `update_wallet_config` (`corporate_wallet.py`); `invite` (member invite), `replace_policy`, `patch_policy` (`corporate_company.py`).

## 4. Risk & impact on existing functionality

- **Blast radius: five files, each change isolated to the specific loop tick or endpoint named above.** No shared helper was modified in a way that changes behavior for any other caller:
  - `get_corporate_account_by_id` — read-only, already used identically throughout the corporate module; no signature or behavior change.
  - `log_admin_action`/`log_user_action` — existing audit-logger functions, called with the same argument shape used by every other call site in these files; not modified.
  - `_maybe_revoke_access_on_removal` — untouched; `_maybe_log_reactivation` is a new, separate function called alongside it, not a modification to its logic.
- **Finding 4's deliberate non-fix**: auto-restoring `auto_topup_enabled` on reactivation was considered and rejected. Blindly flipping a billing toggle back on without knowing whether the company had it enabled *before* suspension would be a genuine, undocumented behavior change with real money-charging consequences (CLAUDE.md: "additive over destructive," "no silent behavior change to an already-shipped flow"). The chosen fix (surface for admin review) closes the visibility gap without taking on that risk.
- Interaction with background loops: Findings 2 and 3 touch two of the 16 background loops directly (`allowance_reset_loop`, `corporate_low_balance_loop`) — both changes are pure additional read-and-skip guards, no change to loop cadence, claim/CAS logic, or replay-safety.
- Money impact: **zero** across all five findings. None move money, change a charge amount, or alter what gets billed — this batch is entirely "stop an unwanted side-effect" (2, 3) or "add missing visibility" (4, 6, 9).

## 5. User-experience effect

- **Rider/corporate admin**: no visible change. Findings 2/3 stop unwanted background behavior (allowance refill, nag emails) the company shouldn't have been receiving while inactive — arguably a UX *improvement*, not a regression, since the current behavior is itself the bug.
- **Internal admin/finance**: `change_company_status`'s audit entry gains `auto_topup_needs_review`; five more admin actions (wallet top-up/adjust/config, member invite, policy replace/patch) now appear in the unified audit trail where they previously didn't. New visibility, no UI change (existing audit-log viewer/table).
- Not visible mid-session to anyone — none of these fire during an active ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/allowance_reset.py` | `run_allowance_reset_tick` skips reset for a non-active company | Finding 2 |
| `backend/utils/corporate_low_balance.py` | `_notify_one` skips notification for a non-active company | Finding 3 |
| `backend/routes/corporate_accounts.py` | `change_company_status` flags `auto_topup_needs_review` on reactivation | Finding 4 |
| `backend/routes/corporate_company.py` | New `_maybe_log_reactivation` helper wired into `update_member`; audit-log calls added to `invite`, `replace_policy`, `patch_policy` | Finding 6, Finding 9 (rows 5/13/14) |
| `backend/routes/corporate_wallet.py` | Audit-log calls added to `manual_topup`, `manual_adjust`, `update_wallet_config` | Finding 9 |
| `backend/tests/test_corporate_allowance_reset.py` | +1 test (suspended company skips reset); 2 existing tests updated to mock `get_corporate_account_by_id` | Regression coverage + fixture drift fix |
| `backend/tests/test_c_allowance_reset_atomic.py` | `_patches` helper extended with the new mock; both call sites updated for the 6-tuple unpack | Fixture drift fix |
| `backend/tests/test_corporate_low_balance.py` | +1 test (suspended company skips notify); 2 existing tests updated with `status: "active"` | Regression coverage + fixture drift fix |
| `backend/tests/test_corporate_status.py` | +2 tests (reactivation flags/doesn't flag `auto_topup_needs_review`) | Regression coverage |
| `backend/tests/test_corporate_company_routes.py` | +5 tests (reactivation audit log ×2, policy PUT/PATCH audit log ×3, invite audit log) | Regression coverage |
| `backend/tests/test_corporate_wallet_routes.py` | +2 tests (top-up and adjust audit log) | Regression coverage |
| `backend/tests/test_corporate_wallet_config.py` | +2 tests (config-update audit log, empty-body no-op) | Regression coverage |

## 7. Before / after (representative — Finding 2)

```python
# Before — utils/allowance_reset.py
member = await get_corporate_member_by_id(r["member_id"])
if not member:
    continue
if member.get("status") != "active":
    continue
wallet = await get_corporate_wallet_by_company(member["company_id"])
```

```python
# After
member = await get_corporate_member_by_id(r["member_id"])
if not member:
    continue
if member.get("status") != "active":
    continue
company = await get_corporate_account_by_id(member["company_id"])
if not company or (company.get("status") or "").lower() != "active":
    continue
wallet = await get_corporate_wallet_by_company(member["company_id"])
```

## 8. Rollback plan

**No flags introduced in this batch** — every change here is either a bug fix to a background loop (stopping an unwanted side-effect that was already the bug, same reasoning as why gaps #1/#3's flags defaulted `true`: doing nothing was the actual defect) or a pure audit-log addition with zero behavior change to the response/outcome of any endpoint. A plain `git revert` is sufficient and complete for all five findings — no schema/data migration, no already-applied money movement to unwind.

## 9. Verification performed

- [x] Automated tests run: full corporate-tagged subset (`pytest -k corporate -q`) — 391 passed, 3 skipped. Full backend suite run separately (see session record) — clean, 0 failures.
- [x] `ruff check` and `ruff format --check` clean on all changed files.
- [x] `pytest tests/test_dual_import_parity.py` — 3 passed, confirming no latent NameError was introduced in any of the three files' try/except dual-import blocks (a real risk surfaced mid-session: an editor-format-on-save hook silently stripped a newly added import from the `except ImportError` fallback branch of `allowance_reset.py` on first attempt — caught and fixed before commit).
- [x] Reviewed against relevant CLAUDE.md conventions: "do not silently swallow errors" (every new audit-log call is wrapped in try/except with `logger.error(..., exc_info=True)`, never `warning`-and-continue, and never lets an audit-log failure break the underlying action), background-loop replay-safety (both loop changes are pure read-and-skip, no interaction with existing CAS/claim logic), "additive over destructive" (Finding 4's non-auto-restore decision).

## 10. Sign-off

- [x] Rollback plan is concrete (plain revert, no flags to manage)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow — Findings 2/3 stop behavior that was itself undocumented/unintended; Findings 4/6/9 are additive audit-log/visibility only

## 11. Deferred: Finding 5 (not implemented in this batch)

**Member removal doesn't touch the removed member's allowance ledger** — `corporate_member_offboarding_service.py` cancels pre-pickup rides and revokes access, but leaves the member's `corporate_member_allowances` row (`amount`/`used`/`period`) untouched. Considered and **not implemented**:

- No direct financial leak — spend is already gated by membership status (a removed member can't book, per the existing fail-closed check), so a stale allowance row sitting unused carries no money risk.
- "Fixing" this would mean mutating a historical ledger value (e.g. zeroing `used`) on a row that's otherwise just informational once the member is removed — that's a destructive edit to preserve, not a bug with a clean additive fix, and CLAUDE.md's "additive over destructive" guidance argues against introducing one for a data-hygiene-only issue.
- If this becomes relevant (e.g. a reactivated member's stale allowance actually causes a real problem, tying into Finding 6's noted allowance-staleness sub-item), it's better addressed together with that at the same time, not as an isolated ledger mutation now.

## What was NOT verified

- No real or test-mode Supabase call was exercised — only mocked `AsyncMock`/`patch` unit tests, consistent with every other change in this corporate-module review.
- The `auto_topup_needs_review` flag's consumption by the admin dashboard's audit-log viewer was not verified against a running app — same standing gap already noted for `pre_pickup_rides_cancelled` in the original PR #2615.
