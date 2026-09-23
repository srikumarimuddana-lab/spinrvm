# PR 5722: service-animal decline reasons

| Field | Detail |
|---|---|
| Issue / root cause | A ride's service_animal flag was treated as evidence that any decline was an accommodation refusal. |
| Fix | Reject and audit only an explicitly service_animal-based refusal. Validate offer ownership before recording a refusal. Keep ordinary and unrelated declines on the existing authorization/offer-release path. |
| Alternative | Mandatory free-text justification would change every offer interaction and collect unnecessary data; the existing explicit reason contract is sufficient for this bug. |
| Risk / blast radius | Only driver decline_ride callers (manual offer decline and timeout clients). Cancellation has its separate unchanged explicit-refusal rule. Existing offer ownership, cooldown, dispatch, acceptance-rate and insurance release remain in place. |
| UX | Vehicle trouble, unsafe pickup, and a normal decline are usable on flagged trips; explicit accommodation refusal remains blocked. |
| Rollback | Revert this predicate; no schema or historical audit rewrite. Already-declined offers remain in the normal dispatch lifecycle. |
| Verification | Three regressions reproduced HTTP 400 before the fix; unauthorized-refusal coverage also protects audit attribution. Targeted decline/refusal suite run after. |
| Limits | Mocked backend tests only; no real drivers/offers changed and no mobile production build for this backend-only fix. |

| File | Change | Why |
|---|---|---|
| backend/routes/drivers/ride_flow.py | Check explicit reason only | Do not infer discriminatory motive |
| backend/tests/test_drivers_extended.py | No-reason, breakdown, unsafe-pickup regressions | Assert successful release and normal audit |

Before: `if ride.get('service_animal') or reason == 'service_animal': reject`

After: `if reason == 'service_animal': reject`

Dry run: driver declines a service-animal trip because of vehicle trouble. Previously refused and audited as discrimination; now the offer is declined/released through existing logic and audit retains its actual reason.
