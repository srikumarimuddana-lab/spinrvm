# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers (adjacent: dispatch, safety) |
| PR / commit link | (local branch `claude/spinrvm-schedule-ride-review-2jsank`, not pushed) |
| Related issue or gap ID | Tracker P1 #12 |

## 1. Issue / gap identified

`go_online` (`backend/routes/drivers/status.py::update_driver_status`) correctly
fail-closes on expired license/insurance/inspection/CRC-VSC documents at the
moment a driver goes online. But mid-session, `accept_ride`
(`backend/routes/drivers/ride_flow.py`) only checked `driver.status ==
"suspended"` — it never re-checked document expiry directly. The only other
enforcement was the `document_expiry` background sweep (12h interval,
`backend/utils/document_expiry.py`), so a driver whose license, insurance,
vehicle inspection, or background check expired *while already online* could
keep accepting **new** rides for up to 12 hours after expiry.

## 2. Root cause

`accept_ride` was written as a fast-path handler focused on the ride
state-machine race (atomic claim, idempotent replay, subscription/quota
gates) and never carried its own document-expiry gate — that check lived only
in `go_online` and the periodic sweep, both of which run at a different
cadence than "the moment a driver taps Accept." `regulatory-sk.md`'s
onboarding table states a "re-check cadence: on expiry" for these documents;
a 12h-interval sweep does not meet that for the accept action specifically.

## 3. Fix / remediation

Added a new helper, `check_driver_documents_current(driver)`, in
`backend/routes/drivers/_shared.py`, called from `accept_ride` immediately
after the existing `driver.status == "suspended"` gate (i.e. before the ride
is even loaded/validated) so an ineligible driver never reaches the ride
claim, idempotent-replay, or subscription-quota logic.

The helper checks two sources, mirroring `go_online`'s own two sources:

1. **Legacy expiry columns already on the loaded `driver` row** —
   `license_expiry_date`, `insurance_expiry_date`,
   `vehicle_inspection_expiry_date`, `background_check_expiry_date`. Zero
   extra DB calls — the row was already fetched by `accept_ride`'s existing
   `asyncio.gather` at the top of the handler.
2. **One additional query**: `driver_documents` filtered by
   `driver_id` + `status='approved'` — a simple row lookup on an indexed
   foreign key (`driver_id`), not a join or a table scan. This catches a
   document that was re-uploaded/re-approved since onboarding, since the
   legacy columns are onboarding-only and never refreshed on re-upload (same
   caveat `go_online`'s own code comments call out).

On a genuine expiry it raises `SpinrException(400, DRIVER_DOCUMENTS_EXPIRED)`
— the same error shape `go_online` uses — and, before raising, best-effort
flips the driver to `suspended` (`is_online=False, is_available=False,
status="suspended"`, CAS on `status != 'suspended'`) so a retried accept (or
the next go-online attempt) shows a clear "account suspended" message instead
of repeating the same expiry rejection indefinitely. This mirrors — same
filter shape, same three fields — what `document_expiry.py`'s 12h sweep
already does.

On a check error (e.g. the `driver_documents` query raises), the caller
(`accept_ride`) catches and returns `HTTPException(503, "Could not verify
your documents right now...")` — see "Fail-open vs fail-closed" below.

### Scope decisions made deliberately

- **Not gated on service-area `required_documents`.** Unlike `go_online`,
  this check does not fetch `service_areas` to determine which documents are
  "required" in the driver's area. The four categories checked (license,
  insurance, vehicle inspection, background/CRC-VSC) are unconditional
  regulatory requirements for every SK driver per `regulatory-sk.md`'s
  onboarding table ("Enforced ... again on every `go_online` call") — they
  are not area-scoped the way Spinr Pass requirements are. Checking them
  unconditionally is more correct here, not a shortcut, and it avoids a
  second query.
- **`go_online`'s own inline document-expiry block (status.py, ~150 lines,
  area-required-documents matching + legacy fallback) was left untouched.**
  A shared-helper refactor of that block was considered (per the task
  instructions) but rejected for this fix: it is a larger, higher-blast-radius
  change to a live, heavily-relied-on gate than this fix's scope justifies.
  `_shared.check_driver_documents_current` is a **new, smaller, intentionally
  duplicated** check, not a shared extraction of `go_online`'s logic. This is
  flagged as a real follow-up (see ACTION_ITEMS.md candidate) — a future PR
  could refactor `go_online` to call a common helper too, with its own
  dedicated regression pass against `go_online`'s existing test coverage.
- **Spinr Pass subscription mid-session re-check was NOT added/expanded in
  this pass.** `accept_ride` already has a subscription-expiry re-check (see
  the "Subscription guard" block a few lines below the new code, pre-existing)
  — it flips an expired `driver_subscriptions` row to `expired` and rejects
  with 402 — but only when the *ride's own service area* requires a pass. It
  does not also check the global `require_driver_subscription` app-setting
  the way `go_online` does. That residual gap is smaller and pre-existing
  (not part of what this task described), and is called out explicitly as
  **not fixed here** — see "What was NOT verified" below.
- **Other ride-action endpoints (`arrive`, `verify-otp`, `start`) were NOT
  touched.** Once a driver has accepted a ride, the current design's position
  is that the accept-time check is the enforcement point going forward for
  *this* ride — see "What was NOT verified" for the residual gap this leaves.

### Fail-open vs fail-closed

This is a regulatory eligibility gate on a money/liability-bearing action
(accepting a ride while unlicensed/uninsured is real regulatory + insurance
exposure — see `regulatory-sk.md` and the Insurance Periods table in
`CLAUDE.md`). Per `CLAUDE.md`'s "Do not silently swallow errors" convention
and `go_online`'s own precedent (its `driver_documents` lookup failure raises
503 rather than letting the driver online), a DB error while checking
document expiry **fails CLOSED**: `accept_ride` catches any non-`SpinrException`
raised by `check_driver_documents_current` and returns `HTTPException(503,
...)`, blocking the accept. It does **not** fall back to "no expiry data
found, so allow it." This intentionally differs from the *unrelated*,
pre-existing `assert_quota_available` call a few lines below in the same
handler, which fails **open** on a lookup error (a business-continuity
ride-allowance check, not a regulatory eligibility gate) — that asymmetry is
pre-existing code, not introduced by this fix, and is called out here only to
avoid the appearance of inconsistency.

## 4. Risk & impact on existing functionality

**Blast radius grep performed** — every other reader/writer of the tables
touched:

- `driver_documents` (read-only in this change, filtered by `driver_id` +
  `status='approved'` — same filter shape `go_online` already uses): other
  readers are `routes/admin/documents.py`, `routes/admin/drivers.py`,
  `routes/admin/sgi_forms.py`, `routes/drivers/tax_exports.py`,
  `services/data_transfer/entity_export_service.py`,
  `services/data_transfer/bundle_document_uploader.py`,
  `services/driver_import_service.py`, `utils/document_expiry.py`,
  `utils/driver_onboarding_reminders.py`, and `routes/drivers/status.py`
  (`go_online`). None of these are affected — this change only adds a new
  `SELECT`, no new writer to that table.
- `drivers.status = 'suspended'` write (new in this change, via
  `_suspend_driver_for_expired_documents`): other writers of the same
  three-field CAS shape are `utils/document_expiry.py`'s sweep (identical
  filter/payload) and `routes/admin/drivers.py` (admin-initiated suspend, a
  different code path). Other **readers** of `drivers.status` that branch on
  `suspended` specifically: `routes/drivers/ride_flow.py` (the pre-existing
  check this fix sits right after), `routes/drivers/status.py` (`go_online`),
  `routes/rides/booking.py`, `services/dispatch_service.py`,
  `utils/driver_status_notifications.py`, `utils/suspension_reactivation.py`
  (auto-reactivation after a *time-based* rider suspension — does not apply
  to drivers, separate table/flow). None of these read a *new* meaning into
  `status='suspended'` — this fix writes the exact same value other suspend
  paths already write, so nothing downstream needs to distinguish "why"
  suspended.
- `backend/routes/drivers/_shared.py`: this module is imported by every
  `routes/drivers/*.py` submodule (`ride_flow`, `status`, `profile`,
  `location`, `ride_cancel`, `ride_complete`, `ride_reads`, `earnings`,
  `payouts`, `referrals`, `subscriptions`, `tax_exports`). The change is
  **additive only** — new names appended, nothing existing renamed, removed,
  or reordered — so none of those other submodules are affected.
- `backend/routes/drivers/ride_flow.py::accept_ride`: the only caller of the
  new check. No other route or background loop calls `accept_ride` directly.

**Could this regress a currently-working flow?** The new gate runs before the
ride lookup/claim, so a driver who is NOT suspended and has no expired
documents sees **zero behavior change** — confirmed by
`TestAcceptRideAllowsValidDriver` (new tests) and by the full pre-existing
`test_ride_accept_flow.py` / `test_claim_ride.py` / `test_subscription_enforcement.py`
/ `test_go_online_availability.py` suites passing unmodified (209 tests, all
green — see Verification below). The one behavior change for an affected
driver: an accept that previously succeeded (document expired mid-session,
previously unenforced until the sweep) now correctly fails with 400 +
suspension, which is the intended fix, not a regression.

**Interaction with the 12h/6h background sweeps**: none — this fix adds an
independent, redundant enforcement point. If the sweep runs first, the driver
is already `suspended` and hits the pre-existing (unmodified)
`driver.status == "suspended"` check instead of reaching the new code at all.
If the accept happens first, this fix now suspends immediately instead of
waiting for the sweep. No double-suspend risk: the suspend write is CAS'd on
`status != 'suspended'`, so a redundant sweep tick after this fix's suspend
is a no-op (same CAS pattern the sweep already relies on for its own
idempotency).

## 5. User-experience effect

**Driver-facing.** A driver whose license/insurance/inspection/CRC-VSC has
expired since they last went online will now see an immediate, specific
rejection ("{Document} has expired. Please update your documents before
accepting rides.") the next time they try to accept a ride, instead of being
allowed to accept and only being suspended up to 12h later (at which point
they'd already be mid-obligation to a rider). This is **visible mid-session**
to a driver who is already online with an expired document — exactly the
population this fix targets. The driver's app will also see `is_online` flip
to `false` server-side on the same request (via the suspend write), so their
next status poll / WS reconnect will reflect offline+suspended, consistent
with what the 12h sweep would eventually have shown them anyway — just
sooner and tied to a concrete action instead of an unexplained state flip.

No rider-facing or corporate-admin-facing change. No new notification copy
beyond the existing `SpinrException` message shown by the driver app's
existing error-handling for `DRIVER_DOCUMENTS_EXPIRED` (already wired for
`go_online`'s identical error shape).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/_shared.py` | Added `check_driver_documents_current()`, `_suspend_driver_for_expired_documents()`, `_parse_document_expiry()`, `_document_matches_category()`, `_CORE_DOC_EXPIRY_CATEGORIES`; added `SpinrException`, `ErrorCode`, `ErrorKeys`, `datetime`, `timezone` to the existing `_deps` import block | New shared helper for the mid-session expiry re-check |
| `backend/routes/drivers/ride_flow.py` | Imported `check_driver_documents_current`; added a call + try/except (fail-closed 503) immediately after the existing `driver.status == "suspended"` check in `accept_ride` | Wires the new check into the accept flow |
| `backend/tests/test_accept_ride_document_expiry.py` | New test file, 8 tests | Coverage for expired-legacy-field, expired-approved-doc, valid-driver regression, and fail-closed-on-DB-error paths |

## 7. Before / after

```python
# Before (backend/routes/drivers/ride_flow.py::accept_ride, excerpt)
if driver.get("status") == "suspended":
    raise AccountDisabledException(
        message="Your account is suspended. Please renew your documents to continue driving.",
        message_key=ErrorKeys.AUTH_ACCOUNT_SUSPENDED,
        action_hint="Contact support",
    )

if not ride:
    raise HTTPException(status_code=404, detail="Ride not found")
```

```python
# After
if driver.get("status") == "suspended":
    raise AccountDisabledException(
        message="Your account is suspended. Please renew your documents to continue driving.",
        message_key=ErrorKeys.AUTH_ACCOUNT_SUSPENDED,
        action_hint="Contact support",
    )

# Mid-session document-expiry re-check (P1 #12): ...
try:
    await check_driver_documents_current(driver)
except SpinrException:
    raise
except Exception:
    logger.error(
        "accept_ride: document expiry re-check failed for driver=%s", driver["id"], exc_info=True
    )
    raise HTTPException(
        status_code=503,
        detail="Could not verify your documents right now. Please try again.",
    ) from None

if not ride:
    raise HTTPException(status_code=404, detail="Ride not found")
```

## 8. Rollback plan

No feature flag was added — this is a straight regulatory-correctness fix on
a fail-closed gate, not a UX experiment, and CLAUDE.md's own guidance treats
document-expiry enforcement as non-optional ("Document expiry ... blocks
Period 1+"). If this needs to be reverted quickly without a redeploy:

- **Fastest**: comment out / early-return the `check_driver_documents_current`
  call site in `ride_flow.py` requires a redeploy (no flag) — acceptable here
  because the change is purely additive to the reject path (it can only turn
  a previously-allowed accept into a rejection, never the reverse), so the
  blast radius of "leave it broken a bit longer" while a redeploy goes out is
  low, and a bad redeploy of this file alone is a single small diff to revert.
- **If the new suspend side-effect (not the rejection itself) is the
  problem** (e.g. a false-positive category match starts wrongly suspending
  drivers): the CAS write in `_suspend_driver_for_expired_documents` can be
  neutralized without touching `ride_flow.py` by making that function a
  no-op (single-function edit), which leaves the 400 rejection in place but
  stops the suspend — narrower blast than a full revert.
- No migration, no data write to roll back beyond the `drivers.status`
  suspend itself, which is already reversible via the existing admin
  "reactivate driver" action (`routes/admin/drivers.py`) — the same recovery
  path used today for sweep-triggered suspensions.
- `git revert` of the 1-2 commits is sufficient as the code-level rollback;
  called out explicitly because a driver-suspend write did happen to live
  data for any driver actually caught by this check before a revert — those
  drivers stay correctly suspended (accurate state, not a bug) unless an
  admin/support flow reactivates them, which is unchanged from today's sweep
  behavior.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest -q --no-cov tests/test_accept_ride_document_expiry.py` — 8/8 passed (new tests: expired license/insurance/inspection/background-check legacy fields; expired approved `driver_documents` row; two happy-path regression tests; one fail-closed-on-DB-error test).
- [x] Automated tests run (unit, regression): `pytest -q --no-cov tests/test_ride_accept_flow.py tests/test_claim_ride.py tests/test_subscription_enforcement.py tests/test_go_online_availability.py tests/test_spinr_pass_quota.py tests/test_spinr_pass_subscription.py tests/test_drivers.py tests/test_drivers_shared_status_profile_coverage.py` — 209/209 passed, no regressions.
- [x] Wider sweep: `pytest -q --no-cov -k "accept or document_expiry or documents_current"` — 246 passed, 1 skipped (pre-existing skip, unrelated).
- [x] `ruff check routes/drivers/ride_flow.py routes/drivers/_shared.py tests/test_accept_ride_document_expiry.py` — clean.
- [x] `ruff format --check` on the same three files — already formatted.
- [x] Blast-radius grep performed (listed in §4): every `driver_documents` reader/writer and every `drivers.status == "suspended"` reader/writer in `routes/` and `services/` and `utils/`.
- [x] Reviewed against `CLAUDE.md` conventions: dual-import pattern (not needed — no new cross-package import boundary crossed beyond the existing `_deps`/`_shared` pattern this package already uses), fail-closed-on-DB-error for a regulatory gate, "do not silently swallow errors" (propagates DB errors, doesn't catch-and-continue), insurance-period table (Period 2/3 logic untouched — this fix runs before the ride is even loaded, so it cannot affect `record_period_transition` calls later in the handler).
- [ ] Manual repro in staging — **not performed** (no staging environment access in this session).
- [ ] Feature-flagged — deliberately not flagged; see Rollback plan for why a flag was judged unnecessary here.

This is backend-only (no `rider-app`/`driver-app`/`admin-dashboard` code
touched), so the CLAUDE.md requirement to run a real production build
(`npm run build` or equivalent) for those surfaces does not apply to this
change.

## 10. What was NOT verified / documented remaining gaps

- **Not tested against a live Supabase** — only against `mock_supabase_client`-style
  mocks via `unittest.mock.patch` at the `db_supabase` module-attribute level,
  following this repo's existing test convention. The `driver_documents`
  query shape (`{"driver_id": ..., "status": "approved"}`, `limit=200`) is
  copy-consistent with `go_online`'s own already-production query, so the
  PostgREST filter shape itself is not new/unverified, but the actual query
  plan/latency against a live indexed table was not measured in this session.
- **No staging/manual repro performed** — no staging environment access in
  this session; verification is unit-test-level only, per the checkboxes
  above.
- **`arrive`, `verify-otp`, and `start` (the rest of `ride_flow.py`) were NOT
  given the same re-check.** This fix is scoped to `accept_ride` only, per
  the task. A driver whose documents expire *between* accept and pickup (or
  between pickup and drop-off) is not re-checked by this fix at any of those
  later transitions — only the original `go_online` gate (at the start of
  the session) and this new accept-time gate apply; the 12h/6h sweeps remain
  the only enforcement for the rest of an in-progress ride. This is a real,
  documented remaining gap, not silently left out — flagging as a follow-up
  candidate for `ACTION_ITEMS.md` rather than expanding this fix's blast
  radius further in one pass.
- **The global `require_driver_subscription` app-setting is still not
  checked by `accept_ride`'s subscription-guard block** (only the ride's own
  service-area `subscription_required` flag is). This is a smaller,
  pre-existing gap distinct from the document-expiry issue this task
  targeted, and was not fixed here — see §3 "Scope decisions."
- **No visual/UI verification** — this is a backend-only change; the driver
  app's existing error-handling for `DRIVER_DOCUMENTS_EXPIRED` /
  `AccountDisabledException` was not re-screenshotted, since it is the same
  error shape already exercised by `go_online` today and no new client-side
  code was touched.
- **`go_online`'s own inline document-expiry block was not refactored to
  share code with the new helper** — see §3 for the explicit reasoning
  (blast-radius/risk trade-off). Flagging here again per the "don't silently
  leave duplication unmentioned" instruction: `status.py`'s block and
  `_shared.check_driver_documents_current` will drift out of sync if one is
  edited without the other being considered — worth a follow-up
  consolidation ticket.
