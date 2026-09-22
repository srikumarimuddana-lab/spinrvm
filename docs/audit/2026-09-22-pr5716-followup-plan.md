# PR 5716 review follow-up

Baseline: `97aad30f45a017154e1f5278cba7ca19b4e62834`, including the corrected
admin logout-all test. This follow-up addresses the verified post-merge review
of PR 5714. It does not authorize production deployment or historical data repair.

## Implementation and verification

Each implementation commit is scoped to at most three files and one logical
change. Separate worktrees isolate the authentication, payment, and dispatch
changes; the integrated PR receives an independent review before publication.

1. Foreground authentication: shared auth store and focused regression tests.
   Prefer server-relative token lifetime; preserve legacy response compatibility.
   Verify ahead/behind device clocks, successor persistence, malformed responses,
   and a subsequent refresh using the successor rather than the spent token.
2. Background authentication: driver background provider and focused tests.
   Apply the same lifetime contract while preserving session-lock/logout fences.
3. Payment webhook: handler and focused success-webhook regression tests.
   Terminally acknowledge genuine underpayment without marking the ride paid;
   retain transient retries for an actual settlement still in flight.
4. Payment recovery: existing retry/finalization helpers and their tests.
   Require durable evidence of the entire obligation before marking paid. A
   primary captured intent alone must never stand in for a missing tip component.
5. Driver claim ownership: new additive migration and its SQL tests/fixtures.
   Replace application/database timestamp ordering with durable claim identity.
   Cover both PostgREST and direct-pool producers and preserve service-role ACLs.
6. Cancel/reaper integration: scoped helpers and tests, committed separately.
   Release only the still-owned claim with no active competing obligation;
   close the matching insurance period atomically with availability changes.
7. Per-domain impact logs, then integrated verification and PR metadata update.

## Design choices

- Relative token lifetime avoids client/server clock comparison. Merely deleting
  the expiry check would preserve credentials but leave scheduling inconsistent.
- Durable claim identity distinguishes an old cleanup from a new assignment.
  Increasing timestamp tolerance or restoring unconditional availability release
  could incorrectly free a driver who already has another obligation.
- Permanent underpayment is an event outcome, not a reason to retry the same
  immutable Stripe event indefinitely. Genuine in-flight settlement still needs
  retries so early webhooks cannot race the app's tip and ledger finalizer.
- Recovery must reconcile recorded financial evidence. Trusting a succeeded
  primary intent alone can lose an overflow tip or mark a partial capture paid.

## Constraints

Merged migrations 441–443 stay unchanged: their filenames and checksums are part
of migration history. New replacement functions need the repository's explicit
`migration-override-ok` annotation and a coordinated rollout/rollback description.

Validation uses synthetic fixtures, mocked external services, and disposable SQL
execution. Native device behavior, production clocks/data, and actual Stripe
delivery are separate release checks. Results and any unresolved limits will be
recorded in the updated PR rather than inferred from unit-test success.
