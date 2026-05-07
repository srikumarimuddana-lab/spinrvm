# P0-5 Merge Resolution Guide

**Branch**: `claude/p0-5-stripe-card-charge` → `claude/plan-e2e-testing-SK3bX`

**Status**: GATED on Phase E validation (see `P0-5_PHASE_E_RUNBOOK.md`)

---

## Summary

Merging P0-5 into the current branch introduces **1 conflict** in `backend/routes/rides.py` with **2 conflict sections**. Both are structural (import merging + payment handler routing), not logic conflicts. **Estimated resolution time: < 10 minutes**.

---

## Conflict Details

### File: `backend/routes/rides.py`

#### Conflict 1: Import Block (Line ~51)

**Current branch adds:**
```python
try:
    from ..utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )
    from ..services import corporate_allowance_service, corporate_wallet_service
    from ..services.corporate_policy_service import evaluate_policy
except ImportError:
    from utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )
    from services import corporate_allowance_service, corporate_wallet_service
    from services.corporate_policy_service import evaluate_policy
```

**P0-5 branch adds:**
```python
# Lift to module scope so tests can patch backend.routes.rides.charge_ride
# directly; the handler's card branch references this bound name.
try:
    from ..utils.stripe_charge import charge_ride
except ImportError:
    from utils.stripe_charge import charge_ride
```

**Resolution**: Keep both blocks. The merged result should have:
```python
try:
    from ..utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )
    from ..services import corporate_allowance_service, corporate_wallet_service
    from ..services.corporate_policy_service import evaluate_policy
    from ..utils.stripe_charge import charge_ride
except ImportError:
    from utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )
    from services import corporate_allowance_service, corporate_wallet_service
    from services.corporate_policy_service import evaluate_policy
    from utils.stripe_charge import charge_ride
```

---

#### Conflict 2: Payment Method Handler (Line ~1220)

**Current branch modifies** `process_payment()` to add the **company_allowance** branch:
```python
elif payment_method == "company_allowance":
    # ... ~100 lines of corporate allowance debit logic ...
```

**P0-5 branch modifies** `process_payment()` to add the **card** branch (Stripe charge):
```python
elif payment_method == "card":
    # ... ~80 lines of Stripe charge + decline handling logic ...
```

**Resolution**: Keep both branches. The merged `process_payment()` handler should route on:
1. `payment_method == "wallet"` → existing code (unchanged)
2. `payment_method == "company_allowance"` → current-branch code (unchanged)
3. `payment_method == "card"` → P0-5 code (unchanged)
4. Default → error (unchanged)

No logic rewriting needed — they are disjoint handlers.

---

## Merge Procedure

### Prerequisites

1. **Phase E validation must be complete** (manual Stripe-staging test per `P0-5_PHASE_E_RUNBOOK.md`)
   - Verify card charge flow in staging with test card
   - Confirm decline handling and 3DS flow work end-to-end
2. Ensure current branch is up-to-date with `origin/claude/plan-e2e-testing-SK3bX`

### Steps

```bash
# 1. Create a feature branch for the merge work (optional but recommended)
git checkout -b merge/p0-5-stripe-card-charge

# 2. Attempt the merge
git merge origin/claude/p0-5-stripe-card-charge

# 3. Git will report: CONFLICT (content): Merge conflict in backend/routes/rides.py
#    This is expected and manageable.

# 4. Resolve the conflict by editing backend/routes/rides.py
#    - Remove the <<<<<<, ======, >>>>>> markers
#    - Apply the resolution guide above (keep both imports, keep both handlers)
#    - Verify imports are grouped logically
#    - Verify handler routing is correct (no duplicate elif conditions)

# 5. Run tests to verify resolution
cd backend
pytest backend/tests/test_p0_ship_blockers.py -v
pytest backend/tests/test_process_payment_card.py -v
pytest backend/tests/test_stripe_charge.py -v
cd ..

# 6. Stage the resolved file
git add backend/routes/rides.py

# 7. Complete the merge
git commit -m "merge: P0-5 Stripe card charge into e2e testing branch

Resolves conflict in backend/routes/rides.py by merging:
- Imports: add stripe_charge alongside estimate_token + corporate services
- Payment handlers: preserve both company_allowance and card branches

P0-5 Phase E validation completed before merge.

Fixes: card charge wire-up at ride completion (P0-5 A–E)
See: docs/scoping/P0-5_STRIPE_CARD_CHARGE.md for Phase A–D implementation
     docs/scoping/P0-5_PHASE_E_RUNBOOK.md for manual validation runbook"

# 8. Push to the working branch
git push -u origin merge/p0-5-stripe-card-charge

# 9. Create PR for review + CI validation
#    (or manually push to claude/plan-e2e-testing-SK3bX once tests pass locally)
```

---

## Files Added by P0-5 (No Merge Conflict)

These 10 files are pure additions and merge cleanly:

| File | Purpose |
|------|---------|
| `backend/utils/stripe_charge.py` | Core Stripe charge utility (ChargeOutcome dataclass, charge_ride function) |
| `backend/tests/test_stripe_charge.py` | Unit tests for Stripe charge flow |
| `backend/tests/test_process_payment_card.py` | Integration tests for card payment in process_payment() |
| `rider-app/utils/attemptRidePayment.ts` | Playwright E2E rider payment flow utility |
| `rider-app/utils/__tests__/attemptRidePayment.test.ts` | Unit tests for payment utility |
| `rider-app/e2e/payment-completion.spec.ts` | Playwright E2E spec (rider payment completion) |
| `scripts/smoke/stripe_charge_smoke.py` | Stripe smoke test harness for manual staging validation |
| `docs/scoping/P0-5_STRIPE_CARD_CHARGE.md` | Scoping doc for Phases A–D |
| `docs/scoping/P0-5_PHASE_E_RUNBOOK.md` | Manual validation runbook for Phase E |

---

## Verification Checklist

After merge resolution:

- [ ] `git status` shows clean working tree
- [ ] `backend/tests/test_process_payment_card.py` passes (card payment integration)
- [ ] `backend/tests/test_stripe_charge.py` passes (Stripe utilities)
- [ ] `backend/tests/test_p0_ship_blockers.py` passes (overall E2E + phase coverage)
- [ ] No trailing conflict markers in `backend/routes/rides.py`
- [ ] Payment method router handles all branches: wallet, company_allowance, card
- [ ] New Stripe imports are accessible (test by importing in REPL)
- [ ] Phase E validation notes are documented in commit message

---

## Rollback Plan

If issues arise during resolution:

```bash
# Abort the merge
git merge --abort

# Return to main branch
git checkout claude/plan-e2e-testing-SK3bX

# Delete the merge branch if created
git branch -D merge/p0-5-stripe-card-charge
```

---

## Notes

- **P0-5 Branch Status**: Phases A–D complete (implementation + tests), Phase E pending (manual validation)
- **Current Branch Status**: Has all E2E test coverage for P0-4 (estimate_token), P1 (reconnect, multi-stop), P2 (scheduled rides, DST), P3 (background location scaffolding)
- **Merge Semantics**: Additive—no deletions, no refactoring. P0-5 cleanly adds Stripe payment flow alongside existing corporate allowance flow.
- **CI Readiness**: After merge, all tests should pass. No additional setup needed (no new dependencies, no env var changes).
