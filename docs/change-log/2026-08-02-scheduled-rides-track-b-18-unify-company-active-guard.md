# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #20 |

## 1. Issue / gap identified

Three separate code paths independently answer the same question — "can this
company be booked against right now?" — with three different definitions of
"inactive" and inconsistent kill-switch handling:

1. Employee self-book, `company_allowance` payment (`routes/rides/booking.py`):
   blocked `suspended`/`closed` only, respected the
   `corporate_inactive_company_blocks_booking` kill switch.
2. Company-portal guest booking (`routes/corporate_company_bookings.py`
   `_require_company_active`): blocked anything not `"active"` (including
   `pending_verification`), **ignored** the kill switch entirely.
3. Employee self-book, `work_profile` payment (`routes/rides/booking.py`,
   a separate block further down the same function): its own third copy,
   narrower like #1 (`suspended`/`closed` only), respects the kill switch.

Two riders hitting materially the same "is my company bookable" question
through different booking flows could get different answers for the same
company state (e.g. `pending_verification`), and the guest-booking path could
not be paused via the shared admin kill switch at all.

## 2. Root cause

The guest-booking guard (`_require_company_active`) was written independently
of the self-book guard, later, without reusing it — likely because there was
no shared "policy" module function to call at the time, only inline checks.
Nothing has unified them since.

## 3. Fix / remediation

New `require_company_bookable(company_id, settings=None)` in
`backend/services/corporate_policy_service.py` — single shared guard:
- Checks `settings.get("corporate_inactive_company_blocks_booking", True)`
  first; returns immediately (no DB call) if the kill switch is off.
- Otherwise fetches the company and raises `HTTPException(403, {"code":
  "company_not_active", "message": ..., "failed_rules": ["company_inactive"]})`
  unless `status == "active"`.
- Accepts a pre-fetched `settings` dict to avoid a duplicate `app_settings`
  round-trip within the same request; fetches fresh if omitted.

Wired into both of the **two guest-booking-shaped** call sites named in the
original Finding #20 scope:
- `backend/routes/rides/_deps.py` — imported alongside the existing
  `evaluate_policy_for_ride` import.
- `backend/routes/rides/booking.py` — the `company_allowance` pre-dispatch
  guard (around the existing corporate-policy check) now calls
  `await _deps.require_company_bookable(body.corporate_account_id,
  settings=_bk_settings_company)` instead of ~17 lines of inline
  status-string checking.
- `backend/routes/corporate_company_bookings.py::_require_company_active` —
  now a thin wrapper: `await require_company_bookable(company_id)`. Kept the
  function name/call site unchanged so nothing else in the file needed to move.

**Deliberate behavior decision**: the shared function uses the **broader**
"not active blocks" definition (guest-booking's prior stance — blocks
`pending_verification` too), not the self-book path's narrower prior
"suspended/closed only" check. This is a real, intentional narrowing of the
self-book path: a `pending_verification` company can no longer be self-booked
against with `company_allowance`, where previously it could. Reasoned as
correcting a likely oversight rather than an intentional prior design choice
— a company mid-verification should not yet be able to have real rides billed
against it either way it's booked — but flagging it here explicitly per the
mandatory "no silent behavior change" rule rather than treating it as pure
refactor.

**Explicitly out of scope**: the third copy (#3 above, the `work_profile`
guard further down `booking.py`, lines ~877–895) was **not** touched. It is
the same pattern a third time, but unifying it changes a third call site's
error shape (`400 {"reason": "company_inactive"}` vs. the shared function's
`403 {"code": "company_not_active", ...}`) and was not part of the original
two-guard finding. Left as a named follow-up rather than silently expanding
this change's blast radius.

## 4. Risk & impact on existing functionality

- **Blast radius: two call sites, both already reading company status off
  the same `corporate_accounts` table.** Grepped
  `backend/routes/rides/_deps.py`, `backend/routes/rides/booking.py`, and
  `backend/routes/corporate_company_bookings.py` for all other readers of
  `get_corporate_account_by_id` / `corporate_inactive_company_blocks_booking`
  — the only other reader is the untouched `work_profile` guard (#3 above),
  which keeps its own independent inline check and is unaffected by this
  change.
- Grepped both `rider-app` and `admin-dashboard` (excluding `node_modules`)
  for the error-detail keys being touched (`company_not_active`,
  `company_inactive`) — no frontend consumes either key today, so the
  `code`/`message`/`failed_rules` shape change on the self-book path is safe
  to ship without a frontend change.
- `evaluate_policy_for_ride` itself (the separate max-fare/time-window/
  allowance policy engine) is untouched — this only changes the company
  *status* pre-check that runs before it.
- No interaction with money movement — this is a pre-dispatch/pre-booking
  gate, not a settlement-path change.

## 5. User-experience effect

**Rider-facing (corporate riders only, self-book `company_allowance` path)**:
a rider whose company is `pending_verification` now gets blocked at booking
with "Your company account isn't active right now..." instead of the ride
being created and later silently falling through to `payment_status="pending"`
at settlement (the same failure mode Finding #17/gap #3 already closed for a
different corporate edge case). This is a **new** rejection for a status that
was previously allowed through — a real, if narrow, behavior change on an
already-shipped screen. **Company-portal-facing (guest booking)**: no visible
change — same blocking statuses, same message shape as before; the guest path
gains the ability to be paused via the shared kill switch, which is an
admin-only capability with no rider-visible effect unless an admin actually
uses it. Not visible mid-session to a rider already on an active ride — this
only gates new bookings.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/corporate_policy_service.py` | New `require_company_bookable(company_id, settings=None)` | Single shared "is company active" guard |
| `backend/routes/rides/_deps.py` | Import `require_company_bookable` alongside `evaluate_policy_for_ride` | Make the shared guard available to `booking.py` |
| `backend/routes/rides/booking.py` | `company_allowance` pre-dispatch guard now calls the shared function instead of its own inline status check | Remove duplicated logic; adopt the broader status definition |
| `backend/routes/corporate_company_bookings.py` | `_require_company_active` now delegates to `require_company_bookable` | Remove duplicated logic; gain kill-switch support |
| `backend/tests/services/test_corporate_policy_service.py` | New tests for `require_company_bookable`: active/suspended/pending_verification/missing-company, kill-switch skip (with `assert_not_awaited` on the DB call), settings-not-provided fetch path | Cover the shared function directly, including the new `pending_verification`-blocking behavior |
| `backend/tests/test_create_ride_remaining_branches.py` | Fixed 5 tests' mock patch target: `require_company_bookable`'s own local `from .. import db_supabase` reaches the real `backend.db_supabase` singleton, which the tests' whole-module `patch("...\_deps.db_supabase")` replace does not intercept; added `patch("backend.db_supabase.get_corporate_account_by_id", ...)` alongside the existing `_deps`-scoped mock. Also fixed 2 more tests (suspended/closed) that were passing only by accident (an unmocked real DB call happened to return non-active) | Regression fix surfaced by this refactor; also closes a latent test-isolation bug independent of this change |

## 7. Before / after

```python
# Before — backend/routes/rides/booking.py (company_allowance guard)
if body.corporate_account_id and body.payment_method == "company_allowance":
    try:
        _bk_settings_company = await _deps.get_app_settings() or {}
    except Exception:
        _bk_settings_company = {}
    if _bk_settings_company.get("corporate_inactive_company_blocks_booking", True):
        _corp_company_row = await _deps.db_supabase.get_corporate_account_by_id(
            body.corporate_account_id
        )
        if _corp_company_row and (_corp_company_row.get("status") or "").lower() in (
            "suspended", "closed",
        ):
            raise HTTPException(status_code=403, detail={"failed_rules": ["company_inactive"], ...})
    _policy_result = await _deps.evaluate_policy_for_ride(...)
```

```python
# After
if body.corporate_account_id and body.payment_method == "company_allowance":
    try:
        _bk_settings_company = await _deps.get_app_settings() or {}
    except Exception:
        _bk_settings_company = {}
    await _deps.require_company_bookable(body.corporate_account_id, settings=_bk_settings_company)
    _policy_result = await _deps.evaluate_policy_for_ride(...)
```

```python
# Before — backend/routes/corporate_company_bookings.py
async def _require_company_active(company_id: str) -> None:
    settings = await get_app_settings() or {}
    if not settings.get("corporate_inactive_company_blocks_booking", True):
        pass  # (guest-booking path previously ignored the kill switch entirely)
    company = await db_supabase.get_corporate_account_by_id(company_id) or {}
    if (company.get("status") or "").lower() != "active":
        raise HTTPException(status_code=403, detail={"code": "company_not_active", ...})
```

```python
# After
async def _require_company_active(company_id: str) -> None:
    await require_company_bookable(company_id)
```

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully restores
the two independent prior implementations. The shared
`corporate_inactive_company_blocks_booking` `app_settings` flag (already
existed, unchanged) remains the live kill switch for the self-book path and
now also covers guest booking — flipping it to `False` restores fail-open
behavior on both paths without a redeploy, same as before this change for the
self-book path. If specifically the `pending_verification`-blocking widening
on the self-book path needs to be reverted without touching anything else, the
fastest path is a one-line change narrowing `require_company_bookable`'s
status check back to `in ("suspended", "closed")` — no data remediation
needed, since this gate never wrote anything, only rejected bookings.

## 9. Verification performed

- [x] Automated tests: full existing `test_corporate_company_bookings_coverage.py`
      + `test_corporate_company_bookings_routes.py` (41 tests, guest-booking
      path) — all pass with **zero modifications**, confirming the refactor
      is behavior-preserving for that path. `test_create_ride_remaining_branches.py`
      (23 tests, self-book path) — 5 initially failed due to a test-mock
      patch-target gap surfaced by this refactor (see §6), fixed, now all
      pass. `test_corporate_settle_suspended_audit_flag.py` (6 tests,
      unrelated `settle_corporate` audit-flag path) — unaffected, still
      passes. New `backend/tests/services/test_corporate_policy_service.py`
      tests for `require_company_bookable` itself (37 tests total in file,
      7 new) — all pass. Ran via the session's `/tmp/spinr_venv` venv.
- [x] `ruff check` on all five touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4): both backend call sites, and
      both `rider-app`/`admin-dashboard` for the changed error-detail keys.
- [x] Reviewed against CLAUDE.md's corporate-billing conventions and the
      "no silent behavior change" pre-merge gate — the `pending_verification`
      widening is called out explicitly above rather than folded silently
      into "refactor, no behavior change."
- [x] Dry-run scenario: a company is created and sits in
      `pending_verification` (KYB not yet complete). An employee opens the
      rider app, selects "Work" payment with `company_allowance`, and taps
      book. Before this change: booking succeeded (self-book only checked
      suspended/closed), creating a ride that would settle against an
      unverified company. After this change: booking is rejected at
      creation with a 403 and a clear message, consistent with what already
      happens if the same employee's booking had gone through the
      company-portal guest-booking flow instead.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — two call sites unified, the
      third (`work_profile`) explicitly left alone and named as a follow-up
- [x] No silent behavior change — the `pending_verification` widening on the
      self-book path is called out in §3 and §5, not buried in "just a
      refactor"

## What was NOT verified

Not tested against a live/staging Supabase instance — only mocked
`db_supabase` responses. The `work_profile` guard (the third duplicate) was
read and confirmed unaffected but not otherwise re-tested beyond its own
existing suite, which already passed unmodified. No admin-dashboard or
rider-app UI changes were made or visually checked — this is a backend-only
error-shape/status-check change; the affected error keys are confirmed unread
by either frontend today via grep, not by running the apps. Did not check
whether any other, non-frontend consumer (e.g. a support runbook, an internal
script) parses the old self-book error shape (`{"failed_rules": [...]}` had
no `code`/`message` before) — the `failed_rules` key itself is unchanged
(`["company_inactive"]` either way), only `code` and `message` are new
additions, which is additive, not breaking, for any such consumer.
