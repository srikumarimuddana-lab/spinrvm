# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (session), reviewed with @vikas |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/corporate-module-review-6eh65j` |
| Related issue or gap ID | Corporate module lifecycle audit — Finding 7 (Phase A only; see §11 for the deferred Phase B) |

## 1. Issue / gap identified

`corporate_policy_service.evaluate_policy` runs at exactly two points: booking time (`evaluate_policy_for_ride`, gates whether a ride can be created) and completion time (an audit-only, non-blocking re-evaluation inside `settle_corporate`). Neither compares the policy that was in effect *when the ride was booked* against the policy in effect *when it settles*. If a company admin tightens `allowed_payment_source` or lowers `max_fare_per_ride` while a ride is already in `searching`→`driver_arrived`, that ride settles under the new, tighter policy with zero record that the rider booked under a looser one — the drift is completely silent.

## 2. Root cause

The completion-time `evaluate_policy` call in `settle_corporate` re-fetches the *current* `corporate_policies` row fresh (`corp_policy = await db_supabase.get_corporate_policy(company_id)`), which is correct for pricing the ride, but there was never a stored snapshot of the policy at booking time to compare against — so there was no way to even detect that a change had happened, let alone flag it.

## 3. Fix / remediation (Phase A of a two-phase plan — see §11)

`settle_corporate` now computes `policy_changed_since_booking`: if the current policy's `updated_at` is later than the ride's own `created_at`, the policy was edited after this ride was booked. This required **no schema change** — `corporate_policies.updated_at` is already maintained by a DB trigger (migration 27), and `rides.created_at` already exists on every ride.

When true, `policy_changed_since_booking` is appended to `completion_eval["failed_rules"]` and a `corporate_policy_evaluations` audit row is written — the exact same non-blocking pattern gap #1 established for `company_inactive_during_ride`. **This never cancels, re-prices, or blocks the ride's settlement** — it only makes the drift visible for ops/finance review, closing the *silent* half of the gap. Proactively cancelling or re-evaluating in-flight rides when a policy changes (the more disruptive half) is deliberately **out of scope** for this change — see §11.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one function, purely additive.** `settle_corporate` is the only caller of this new comparison block; no other function reads `corp_policy.get("updated_at")` or is affected by this change.
- Grepped every other reader of `corporate_policy_evaluations`: only admin/finance-facing audit views (not modified in this change) and the existing `company_inactive_during_ride` flag logic immediately above this new block, which this change is deliberately modeled on and does not alter.
- Failure mode: the timestamp comparison is wrapped in `try/except`, logged via `logger.error` (not swallowed per CLAUDE.md's error-handling convention) on any parse failure, and defaults `policy_changed_since_booking = False` — a malformed or missing timestamp on either side degrades to "no flag," never to a raised exception or blocked settlement. Verified by a dedicated test (`test_no_policy_updated_at_never_raises`).
- Money impact: **zero** — no charge amount, fee, or wallet debit changes as a result of this flag. It is purely an audit-trail addition to a row that's already conditionally written.

## 5. User-experience effect

- **Rider**: none — nothing about the ride, its price, or its outcome changes.
- **Corporate admin**: none directly — this is a passive consequence of editing a policy while a ride is in flight, not a new control they interact with.
- **Internal admin/finance**: `corporate_policy_evaluations` can now show `policy_changed_since_booking` for a completed ride — new visibility into a previously invisible category of billing drift, no UI change (existing audit table/endpoint).
- Not visible mid-session to anyone — purely a post-settlement audit signal.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | `settle_corporate` computes `policy_changed_since_booking` from `corp_policy.updated_at` vs `ride.created_at`, appends to the existing completion-phase audit-flag block | Core fix — Finding 7 Phase A |
| `backend/tests/test_corporate_settle_suspended_audit_flag.py` | +3 tests: policy edited after booking flags audit, policy unchanged does not, missing timestamps never raise | Regression coverage |

## 7. Before / after

```python
# Before — services/payment_service.py::settle_corporate
completion_eval = evaluate_policy(corp_policy, completion_ctx)
if company_status in ("suspended", "closed"):
    completion_eval["failed_rules"] = [*completion_eval.get("failed_rules", []), "company_inactive_during_ride"]
    completion_eval["pass"] = False
if not completion_eval["pass"] or flag_violation:
    await db_supabase.insert_one("corporate_policy_evaluations", {...})
```

```python
# After
policy_changed_since_booking = False
try:
    _policy_updated_at = corp_policy.get("updated_at")
    _ride_created_at = ride.get("created_at")
    if _policy_updated_at and _ride_created_at:
        _policy_dt = datetime.fromisoformat(str(_policy_updated_at).replace("Z", "+00:00"))
        _ride_dt = datetime.fromisoformat(str(_ride_created_at).replace("Z", "+00:00"))
        # ... tz-normalize both ...
        policy_changed_since_booking = _policy_dt > _ride_dt
except Exception as _policy_ts_exc:
    logger.error(..., exc_info=True)

completion_eval = evaluate_policy(corp_policy, completion_ctx)
if company_status in ("suspended", "closed"):
    completion_eval["failed_rules"] = [*completion_eval.get("failed_rules", []), "company_inactive_during_ride"]
    completion_eval["pass"] = False
if policy_changed_since_booking:
    completion_eval["failed_rules"] = [*completion_eval.get("failed_rules", []), "policy_changed_since_booking"]
    completion_eval["pass"] = False
if not completion_eval["pass"] or flag_violation:
    await db_supabase.insert_one("corporate_policy_evaluations", {...})
```

## 8. Rollback plan

`git revert` is sufficient and complete — no flag was introduced (unlike the booking-blocking fixes in this batch, this is pure audit-trail visibility with zero behavior change to money movement, so it doesn't carry the same live-rollout risk that warrants a flag), no schema/data migration involved, and no already-applied money movement to unwind.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_corporate_settle_suspended_audit_flag.py -q` — 6 passed (3 existing + 3 new).
- [x] `ruff check` and `ruff format --check` clean.
- [x] Reviewed against relevant CLAUDE.md conventions: "do not silently swallow errors" (timestamp-parse failure is `logger.error` with full exception, not `warning`-and-continue), audit-only non-blocking pattern matches the established `company_inactive_during_ride` precedent exactly.
- [ ] Manual repro against real Supabase — **not done**, no dev environment available in this session; the `corporate_policies.updated_at` trigger's real behavior (exact timestamp precision, whether a no-op UPDATE still bumps it) was not observed against a live database, only assumed from the migration 27 trigger definition.

## 10. Sign-off

- [x] Rollback plan is concrete (plain revert, no flag needed since there's no user-visible behavior to roll back)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow — settlement outcome (amount charged, success/failure) is byte-for-byte identical before and after this change; only a new audit row can now appear

## 11. Deferred: Phase B (not implemented in this change)

**Proactively re-evaluating or cancelling pre-pickup rides when a policy is edited** was explicitly scoped out of this fix and is **not implemented here**. Reasoning, for whoever picks this up:

- Policy edits (fare caps, allowed payment source) are likely far more routine than a company suspend/close — auto-cancelling a live ride over what might be a minor admin tweak risks a worse UX regression than the gap it would close.
- Unlike gap #1 (suspend/close, which already disables auto-topup as a companion defensive action), a policy edit has no existing "something else already got safer" precedent to lean on — cancellation-on-policy-edit would be a net-new, more disruptive behavior needing its own default-off flag (like gap #2's Stripe-refund flag) and likely its own admin-facing confirmation UX ("this change may affect N active rides — continue?") before it should ship, which is more of a product decision than a backend bug fix.
- This audit-only flag (Phase A, this change) already closes the *silent* half of the problem — ops/finance can now see it happened. Phase B is tracked as a separate follow-up, not bundled here, so a UX-sensitive product decision doesn't get rushed under the same review pass as five lower-judgment consistency fixes in the same batch.

## What was NOT verified

- No real Supabase trigger behavior was observed (see §9) — the `updated_at` bump-on-every-UPDATE assumption comes from reading the migration, not from a live database.
- Edge case not tested: a ride created and a policy updated within the same second (clock-precision tie) — the `>` comparison would resolve to "no flag" in that case; considered acceptable for an audit-only signal, not verified as the "correct" choice one way or the other.
