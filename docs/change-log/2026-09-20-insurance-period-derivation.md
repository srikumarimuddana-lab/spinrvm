# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (agentic fleet work), branch `mvapps/affectionate-tesla-mdqcn4` |
| Surface(s) | backend |
| Domain (Sentry tag) | safety (insurance periods), with a `drivers` touchpoint |
| PR / commit link | _to be filled on PR creation_ |
| Related issue or gap ID | Deferred non-goal documented in `backend/tests/test_ride_state_machine_properties.py` ("What this pilot deliberately does NOT cover") |

## 1. Issue / gap identified

CLAUDE.md's Period 0–3 insurance table was implemented **twice, independently**, and the two
implementations disagree about a driver holding a live batch-dispatch offer.

- `backend/utils/insurance_period_reconciler.py` encodes the table as magic integers inside four
  `async` DB-query functions. Its docstring says the mapping comes "straight from CLAUDE.md's
  period-derivation table", and it correctly maps *a driver named on a pending `ride_offers` row →
  Period 2*.
- `backend/routes/drivers/status.py` encodes the same table as inline ternaries
  (`3 if _busy_status == RideStatus.IN_PROGRESS else 2`, then `1 if is_online else 0`) and has **no
  live-offer case at all**.

Found by code reading during a blast-radius sweep, not by a bug report, an alert, or an observed
production incident. **It has not been reproduced against a live driver session** — see §11.

## 2. Root cause

Two causes compound:

1. **No single source of truth.** The period table existed only as duplicated inline logic, so
   nothing forced the two implementations to agree. There was no pure function any test or reviewer
   could point at as "the rule".
2. **The Go Offline guard checks the wrong table for batch dispatch.** `status.py` rejects (409) a
   Go Offline attempt when the driver has an active `rides` row, and its comment says *"To go
   offline during an offer, decline it first."* But batch dispatch holds its claim in `ride_offers`
   and sets no `rides.driver_id` until the driver accepts (`status.py`'s own comment: *"Batch
   dispatch holds no ride row pre-acceptance — the claim lives in ride_offers"*). A driver mid-offer
   therefore passes the guard, and nothing enforces the intent the comment states.

   Independently confirmed by `spinr-insurance-period-auditor`, which also established the *bound*
   of the bug: the **single-offer** dispatch path does write `rides.driver_id` at
   `driver_assigned`, so that path is correctly caught by the 409 guard. Only **batch** dispatch —
   where the ride stays `searching` with the claim held in `ride_offers` (migration 100: "Ride
   stays in 'searching' while multiple offers are pending") — bypasses it. The gap is narrower
   than "any pending offer", and this log should not be read as claiming otherwise.

The result: `routes/rides/matching.py` opens the correct Period 2 at claim time, then a status
toggle overwrites it with Period 0 (offline tap — the offer lookup is guarded on `is_online` and is
skipped entirely) or Period 1 (online tap — a pending offer sets `is_available = False` but leaves
`_busy_ride_row` as `None`, falling through to the blanket branch).

In regulatory terms: for roughly the 15-second offer window, a driver's audit trail can say
"personal auto only" while they are already contractually obligated to a ride. CLAUDE.md names
exactly this: *"Misclassification is a regulatory and insurance liability."*

## 3. Fix / remediation

1. **New pure function** `derive_insurance_period()` in `backend/utils/insurance_periods.py` — the
   single source of truth for the Period 0–3 table. No DB, no async, no clock. Keyword-only
   arguments, because a transposed positional argument on a regulatory classification would fail
   silently.
2. **The reconciler now calls it** instead of restating the table as literals. This is a pure
   de-duplication: its 12 existing tests pass **with zero edits**, which is the evidence that
   behaviour is unchanged.
3. **`status.py` routes its decision through the same function**, gated on the new
   `insurance_period_live_offer_enabled` app-setting (default **OFF**). With the flag ON, a live
   offer yields Period 2 tied to the offer's `ride_id` on both the online and offline toggle. With
   it OFF, behaviour is byte-identical to before — bug included.

### Alternative considered and rejected

Extending the Go Offline 409 guard to also reject during a live offer would match the intent stated
in that guard's own comment. Rejected for this change because (a) it newly rejects a
previously-allowed user action, which is a heavier UX and support change than correcting an audit
record, and (b) it does nothing for the *Go Online* case, which is half the bug. Recorded here as a
reasonable product follow-up, not as work this change performs.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), contained by a default-OFF flag.**

Blast-radius grep performed on `record_period_transition`: **~24–29 production call sites across
14–15 files**, depending on counting method (an independent sweep by
`spinr-insurance-period-auditor` found 29 across 15, differing on whether a file's multiple internal
call lines count once or separately; it confirmed no file was missed either way). Treat the file
list, not the number, as the authoritative artifact:

`routes/drivers/` (ride_flow, ride_complete, ride_cancel, profile, status, subscriptions),
`routes/rides/` (matching, lifecycle, cancellation), `routes/admin/rides.py`, `routes/auth.py`,
`routes/users.py`, `services/corporate_suspension_service.py`,
`services/corporate_member_offboarding_service.py`, `utils/stale_intent_reconciler.py`,
`utils/spinr_pass.py`, `utils/insurance_period_reconciler.py`.

**Exactly 2 of those files are touched** — `routes/drivers/status.py` and
`utils/insurance_period_reconciler.py`. Every other call site passes a literal period and is left
exactly as it was. That is deliberate: roughly 9 of them record Period 0 for *session* events (logout, account
deletion, subscription lapse, Spinr Pass expiry, corporate suspension) which are not ride-state
derived at all, and the rest are already correct. Rewriting them would be churn on a live-tested
surface for no behavioural gain.

**What shares this state:**

- `driver_insurance_periods` table (migration 64), partial-unique on `driver_id WHERE ended_at IS
  NULL`. Append-only — this change adds no direct writes; everything still goes through
  `record_period_transition`, so the append-only trigger contract is untouched.
- Background loops reading/writing the same rows: `insurance_period_reconciler` (modified — see
  above), `stale_p3_closer`, `stale_intent_reconciler`. None of their behaviour changes.
- **Interaction worth naming:** with the flag ON, `status.py` and the reconciler now agree on the
  live-offer case. With the flag OFF they continue to disagree, and the reconciler will keep
  correcting `status.py`'s Period 0/1 back to Period 2 on its next 10-minute tick (its Period 2/3
  self-heal is unconditional). So the flag-OFF state is *self-healing but lagging* — the audit trail
  is briefly wrong and then repaired, rather than permanently wrong. This materially lowers the
  urgency of flipping the flag, and is the strongest argument for shipping it dark first.
- No money, wallet, fare, or Stripe path is touched. No ride state transition is touched.
- `backend/schemas.py` gains one `AppSettings` field. Additive; existing rows without the key fall
  back to the `False` default.

**Could this regress a working flow?** The two realistic candidates:

- *Go Offline gains a DB read* (`ride_offers`) when the flag is ON and no offer was already found.
  One indexed lookup, `limit=5`. Go Offline has no stated P95 SLA in CLAUDE.md's table. Zero extra
  reads when the flag is OFF.
- *`get_app_settings()` is now called on the Go Offline path*, where it previously was not (it was
  fetched only inside `if is_online:`). It is TTL-cached in-process, so this is a cache hit in the
  overwhelming majority of cases.

## 5. User-experience effect

**Driver-facing: none.** No driver can see, or is blocked by, anything that changed. The Go Offline
409 guard behaves exactly as before; no new rejection, no new prompt, no copy change. What changes
is the contents of a regulatory audit table.

**Mid-session visibility: none.** A driver already online or mid-offer sees no difference. Riders,
corporate admins, and internal admins see no difference.

The only audience is the SGI/regulatory audit trail and anyone reading
`driver_insurance_periods` (internal admin reporting, compliance export).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/insurance_periods.py` | Added pure `derive_insurance_period()`, `_EN_ROUTE_STATUSES`, `_KNOWN_RIDE_STATUSES`, and a `RideStatus` dual-import | Single source of truth for the Period 0–3 table |
| `backend/utils/insurance_period_reconciler.py` | Four magic period literals replaced with calls to the shared function; `_ASSIGNED_RIDE_STATUSES` now derived from `_EN_ROUTE_STATUSES` | De-duplicate the table; stop the query filter and the classification drifting apart |
| `backend/routes/drivers/status.py` | Tracks `_live_offer_ride_id`; adds `ride_id` to the pending-offer column list; flag-gated branch routing the period decision through the shared function, incl. an offer lookup on the offline path | Fix the live-offer misclassification without changing driver-visible behaviour |
| `backend/schemas.py` | New `insurance_period_live_offer_enabled: bool = False` on `AppSettings` | Make the flag flippable from the admin dashboard with no redeploy |
| `backend/tests/test_insurance_period_derivation.py` | New — 21 tests pinning the table | Regression floor for the shared rule |
| `backend/tests/test_driver_status_live_offer_period.py` | New — 5 tests, both flag states | Pin the fix **and** pin that flag-OFF is unchanged |

## 7. Before / after

```python
# Before — routes/drivers/status.py, and no live-offer case anywhere
if status_flipped:
    if is_online and _busy_ride_row:
        _busy_status = _busy_ride_row.get("status")
        _busy_period = 3 if _busy_status == RideStatus.IN_PROGRESS else 2
        await _deps.record_period_transition(driver_id, _busy_period, ride_id=_busy_ride_row.get("id"))
    else:
        # A driver holding a live batch-dispatch offer lands here:
        # Period 1 on a Go Online tap, Period 0 on a Go Offline tap,
        # overwriting the Period 2 opened at claim time.
        await _deps.record_period_transition(driver_id, 1 if is_online else 0)
```

```python
# After — flag ON; the OFF branch is the block above, verbatim
if _live_offer_period_enabled:
    _period = _derive_insurance_period(
        ride_status=_busy_ride_row.get("status") if _busy_ride_row else None,
        is_online=is_online,
        has_live_offer=_live_offer_ride_id is not None,
    )
    _period_ride_id = None
    if _period in (2, 3):
        _period_ride_id = (_busy_ride_row or {}).get("id") or _live_offer_ride_id
    await _deps.record_period_transition(driver_id, _period, ride_id=_period_ride_id)
```

**Concrete scenario.** Driver `drv-1` is online and idle (Period 1). Batch dispatch claims them for
ride `ride-7`; `matching.py` opens Period 2 tied to `ride-7`. Two seconds later the driver taps
Go Offline.

| | Period recorded | `ride_id` |
|---|---|---|
| Before / flag OFF | `0` (personal auto only) | none |
| After, flag ON | `2` (TNC primary commercial) | `ride-7` |

## 8. Rollback plan

**Flip `insurance_period_live_offer_enabled` to `false`** in the `app_settings` row via the admin
dashboard. No redeploy, no migration, no data cleanup. The flag-OFF path is the pre-change code
verbatim and is pinned by two tests (`TestFlagOffIsUnchanged`), so reverting is a return to a tested
state rather than to an untested one.

**On already-written data:** rows written while the flag was ON are *more* accurate, not less, so
there is nothing to remediate — and `driver_insurance_periods` is append-only by trigger (migration
64), so they could not be rewritten in any case. Flipping the flag off simply stops new correct rows
from being written; the reconciler's Period 2/3 self-heal then resumes covering the gap on its
10-minute cadence.

The reconciler and schema changes are behaviour-neutral and need no rollback; if one were wanted,
`git revert` is sufficient for those two files because neither writes data.

## 9. Verification performed

- [x] **Automated tests run (unit).** 26 new tests (21 derivation + 5 status). Counts below are
      reproducible against this branch's base (`main` @ `f998554`). An earlier draft of this log
      cited "136 passed" from a narrower 5-suite selection taken against the *pre-merge* base; that
      figure did not reproduce and has been replaced. Caught by `spinr-insurance-period-auditor`.
      - `pytest tests/ -k insurance --ignore=tests/rls` → **167 passed, 12 skipped**
      - the 7 suites this change actually touches → **152 passed**
      - `pytest tests/ -k "insurance or status or period or reconciler or settings or schema"
        --ignore=tests/rls` → **1981 passed, 15 skipped** (run against the pre-merge base)

      Reconciler's 12 pre-existing tests pass **with zero edits** — the evidence for
      "behaviour unchanged".
- [x] **Mutation-verified, not just "tests pass".** Every new assertion was checked by deliberately
      breaking the production code and confirming the intended test fails, then restoring the file:
      7 mutations of `derive_insurance_period` (period values, en-route set membership, branch
      order, the unknown-status ERROR log) and 5 of `status.py` (flag forced ON, flag forced OFF,
      offline offer lookup removed, `ride_id` dropped, online offer capture removed). All 12 caught.
      A 13th mutation **survived** and exposed dead code in my own first draft — an explicit
      `ride_status = None` reset that changed nothing, since an unrecognised value already fails
      both subsequent checks. It was removed rather than papered over with a test.
- [x] **Cross-check that the wiring is real.** Breaking `derive_insurance_period` makes the
      *reconciler's* tests fail, proving it genuinely depends on the shared function rather than
      importing it decoratively.
- [x] **Blast-radius grep performed.** `record_period_transition` across `backend/` (24 production
      sites / 14 files, enumerated in §4); `tax_breakdown`-style column checks on the two queries
      whose column lists changed; `app_settings` scope trace confirming `get_app_settings` was
      previously unreachable on the Go Offline path.
- [x] **Reviewed against CLAUDE.md conventions:** insurance Period 0–3 table; append-only
      `driver_insurance_periods`; dual-import pattern (one of my own imports was initially wrong at
      `....utils` and only worked via the ImportError fallback — corrected to `...utils`);
      release gate #3 (flag), #7 (rollback before merge), #10 (alternative named in §3).
- [x] **Feature-flagged**, default OFF, registered in `AppSettings` so an operator can flip it
      without a redeploy.
- [ ] **Manual repro in staging — NOT done.** See §11.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (single `app_settings` boolean; OFF path has its own tests)
- [x] Blast radius is stated, not assumed (24 sites enumerated; 2 touched, 22 deliberately not)
- [x] No silent behaviour change to an already-shipped flow — the only behaviour change is
      flag-gated and default OFF, and §5 states its (nil) user-visible effect

## 11. What was NOT verified

- **Not reproduced against a live driver session or real Supabase.** This is a code-reading finding.
  The reachability argument is traced through source (`rides`-only Go Offline guard + batch dispatch
  storing claims in `ride_offers` + `_busy_ride_row` guarded on `is_online`), and the unit tests
  drive the real handler — but nobody has watched a real driver produce a wrong Period 0 row. Treat
  the frequency as unknown.
- **No query was run against production `driver_insurance_periods`** to count how many
  Period 0/1 rows were written during a live offer window. That count would size the real-world
  impact and is the obvious next diagnostic; it needs DB access this session does not have.
- **The flag has not been exercised in staging or canary.** It is dark-shipped precisely so it can
  be, before anyone flips it on.
- **Timing/race behaviour untested.** A toggle arriving in the same instant as the offer expiring,
  or two toggles in flight, is not covered. The reconciler's 10-minute convergence is the existing
  safety net for that class, and it is unchanged.
- **The 22 untouched `record_period_transition` call sites were read, not audited.** They were
  enumerated for blast radius and judged out of scope; this change makes no claim that each is
  correct.
- **Driver-app and rider-app were not examined at all.** No client change is implied, but "no
  client impact" is reasoning from the backend contract, not from reading the apps.
