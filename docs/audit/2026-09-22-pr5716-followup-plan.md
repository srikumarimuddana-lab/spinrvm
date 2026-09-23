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

## Integrated verification result

- Payment/reconciliation/webhook and admin-revocation suites: 205 passed.
  A strict network-attempt collector blocks DNS and IPv4/IPv6 traffic in the
  four payment suites and fails at teardown even if application code catches
  the error. It exposed an existing missing subscription-retrieval mock;
  after correcting that mock, the integrated run recorded zero attempts.
- Dispatch, matching/parity, repository, reaper, insurance, settings, and
  migration checks: 191 passed. Eight native PostgreSQL tests were collected
  and skipped because no native database service is available here.
- The actual migration safety workflow script passed all hard checks.
  Merged migrations 441–443 are unchanged; 444 is the next available number.
- Independent disposable SQL execution applied 117 fixture/migration
  statements and passed 17 cases in PostgreSQL 18.3 via PGlite 0.5.8.
  Migration 444 SHA-256:
  `371b968530df2006bc88424820e32a86681f953164cad1b9a81dfa31b59c0338`.
  _2026-09-23: renumbered 444 → 448 after #5717 (444–446) and #5718 (447) landed; the header comment changed, so this hash is historical._
- Foreground auth tests passed in both driver and rider Jest configurations
  (20 each), and driver background auth passed 18 tests. Both apps exported
  Android, iOS, and web JavaScript bundles with their production patches.
- Independent architecture/security review checked the final ownership,
  clock-skew, ledger-proof, and rollout changes. `git diff --check` passed.

The PR remains draft: native PostgreSQL concurrency, Supabase staging,
mixed-version rollout/rollback, latency, native app builds, and actual
rider/driver device checks remain release gates. PGlite is single-session
and does not establish concurrent lock scheduling. No production migration,
feature-flag change, deployment, Stripe charge/refund, or wallet change was
performed. Missing payment proof remains a manual-review case; ordinary
terminal-offer availability/insurance writes retain their pre-existing
two-call crash window as documented in the dispatch impact log.
