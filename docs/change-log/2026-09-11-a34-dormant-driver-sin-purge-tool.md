# Change Impact & Risk Log — A34 dormant-driver SIN purge tool

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (spinr platform) |
| Surface(s) | backend (migration, service, script) |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `mvapps/blissful-thompson-u14frt` |
| Related issue or gap ID | ACTION_ITEMS.md A34 (2026-09-10 dormant-PII finding) |

## 1. Issue / gap identified

A 2026-09-10 read-only production check (`docs/change-log/2026-09-10-a34-pre-launch-data-contamination-check.md`)
found that 97 of 854 confirmed-dormant (zero real activity, ever) legacy-imported
driver profiles still carry a real SIN with no operational purpose being
served — a PIPEDA data-minimization gap, not a breach. No purge tool existed;
the decision was explicitly left open pending product-owner sign-off.

## 2. Root cause

`pre_launch_flag_service.py` (the tool that flags dormant legacy-imported
profiles) is additive-only by design — it never deletes anything. The
separate SIN/DOB backfill tool (`driver_import_service.py`) that originally
wrote these values has no activity or launch-date gate at all. No tool in
this codebase has ever purged a driver PII field.

## 3. Fix / remediation

Presented the finding plainly to the product owner via `AskUserQuestion`
(not decided unilaterally) and got two explicit decisions:

1. **Scope: SIN only.** `date_of_birth`, `driver_vehicle_history`, and
   dormant riders' `saved_addresses` are explicitly out of scope for this
   pass — separate, still-undecided items.
2. **Grace period: 180 days past Spinr's 2026-03-30 launch** (cutoff
   2026-09-26) — deliberately chosen over 90/120 days (both already
   elapsed) so a driver who imported cleanly but hasn't taken a first ride
   yet isn't stripped of a document prematurely.

Built:

- **`backend/migrations/413_purge_driver_pii_secret_fn.sql`** — new
  `purge_driver_pii_secret(secret_id text) RETURNS boolean` RPC, modeled
  directly on the existing `encrypt_driver_pii`/`decrypt_driver_pii`
  pattern (migrations 32→78→137→138) and the more recent
  `encrypt_emergency_contact_pii` fix (migration 359): `SECURITY DEFINER`,
  `SET search_path = public, vault, pg_temp`, `OWNER TO supabase_admin`,
  `service_role`-only execute grant. Deletes the actual `vault.secrets`
  row — migration 289's own top comment already documented that nulling
  `drivers.sin` alone orphans the vault row without deleting the
  ciphertext ("a PIPEDA problem, not a clean [purge]"); no RPC existed to
  do the delete from application code before this.
- **`backend/services/dormant_driver_sin_purge_service.py`** —
  `build_sin_purge_plan()` (read-only; population = drivers already
  flagged `pre_launch_test = true` by `pre_launch_flag_service.py`, with a
  non-null `sin` — never re-derives the activity gate) and
  `apply_sin_purge()` (deletes the vault secret via the new RPC, then
  nulls `sin`/`sin_last4`/`sin_collected_at`, in that order). **The grace
  period is enforced as a hard code check inside `apply_sin_purge`** — it
  raises and writes nothing if called before the cutoff, regardless of
  caller intent. Optimistic-concurrency guard on the update (re-reads
  `sin` immediately before writing, same pattern as
  `pre_launch_flag_service.apply_pre_launch_flags`) — a driver whose SIN
  changed between plan and apply is reported as a conflict, never
  silently overwritten.
- **`backend/scripts/purge_dormant_driver_sin.py`** — thin CLI, dry-run by
  default, `--apply` required to write, mirrors
  `backfill_legacy_driver_sin_dob.py`'s existing shape exactly (same flag
  names, same batch-tagging convention, same "never print PII" discipline).
- **13 new unit tests** covering: grace-period math, candidate selection
  (flagged+SIN-only), the hard pre-cutoff refusal (with and without
  candidates), the happy-path delete-then-null ordering, the conflict
  guard, and that `print_report` never emits a SIN value.
- **`spinr-migration-reviewer` run** on the new migration before merge (see
  Verification below).

**Deliberately not built:** no admin-dashboard button, no background loop,
no cron. A destructive PII purge stays a manually-triggered CLI action a
human runs deliberately, on or after 2026-09-26 — not a one-click UI
affordance or anything that could fire unattended.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New migration (additive — one new function,
  no table/column change, no data touched by the migration itself), one new
  service module, one new script. No existing function, route, or table is
  modified. Grepped for every other caller of `vault.secrets`,
  `encrypt_driver_pii`, `decrypt_driver_pii`: none call the new RPC or
  module — this is net-new, unreferenced by anything else in the repo.
- **The migration itself is a pure `CREATE OR REPLACE FUNCTION` + grants** —
  safe to apply against production traffic in flight, no lock contention,
  no data rewrite.
- **Cannot fire early.** Today is 2026-09-11; the grace-period cutoff is
  2026-09-26 (15 days out). `apply_sin_purge` raises `RuntimeError` and
  writes nothing if invoked before that date — confirmed by
  `test_apply_refuses_before_grace_period_elapses` and
  `test_apply_refuses_even_with_zero_candidates_before_cutoff`. Merging
  this PR today has **zero live effect** on any driver's data.
- **No cascading state.** Unlike a ride/wallet/Stripe change, purging a
  SIN triggers no downstream write, notification, or financial event —
  confirmed by reading every other reader of `drivers.sin`
  (`sin_source()` in `driver_import_service.py`, the T4A/reveal-SIN admin
  path) — none of them fail differently on a null `sin` than they already
  do for a driver who never had one on file (that state already exists
  today for every non-legacy driver).
- **Once it does fire, it is genuinely irreversible** — the vault
  ciphertext is deleted, not archived. This is the explicit point of a
  purge, not a bug; see Rollback below for what "rollback" actually means
  here.

## 5. User-experience effect

None today (nothing has run). Once the script is actually invoked
(2026-09-26 or later, by a human, deliberately): a purged driver's own SIN
field becomes empty, identical in every way to a driver who never had a
SIN on file — no UI change, no error state, no notification to the driver
(this is silent, deliberate data minimization on an account with zero
activity, not a rider/driver-facing behavior change). If any of the 97
current candidates has since gone active by 2026-09-26, they are
automatically excluded — the RPC-time query re-checks `pre_launch_test`
and activity state fresh, it does not purge against the 2026-09-10
snapshot.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/413_purge_driver_pii_secret_fn.sql` | New — `purge_driver_pii_secret` RPC | Delete the actual vault ciphertext, not just the column reference |
| `backend/services/dormant_driver_sin_purge_service.py` | New — plan/apply/print_report | The purge logic, grace-period gate, conflict guard |
| `backend/scripts/purge_dormant_driver_sin.py` | New — CLI wrapper | Human-triggered, dry-run-default entry point |
| `backend/tests/test_dormant_driver_sin_purge_service.py` | New — 13 tests | Cover grace period, candidate selection, apply ordering, conflicts |
| `ACTION_ITEMS.md` | A34 addendum | Record the decision and what was built |
| `docs/change-log/2026-09-11-a34-dormant-driver-sin-purge-tool.md` | New — this file | Change Impact Log |

## 7. Before / after

Not applicable — every file is new; there is no prior behavior to diff
against.

## 8. Rollback plan

`coordinated` — **the migration itself** (`413_...sql`, adding the RPC) is
`git-revert-safe`: `DROP FUNCTION IF EXISTS public.purge_driver_pii_secret(text);`,
no data affected, stated in the migration's own top comment. **A purge run**
(once one has actually happened, on or after 2026-09-26) is **not
revertible** — the vault ciphertext is deleted, by design, and there is no
code-level undo for that. What IS preserved: every purged driver's id and
purge batch/timestamp/reason, stamped into
`legacy_import_metadata.dormant_sin_purge` — so the *population and timing*
of any purge remains fully auditable even though the SIN *value* does not.
If a purge is ever found to have run in error, the only remediation is the
affected driver re-submitting their SIN.

## 9. Verification performed

- [x] Automated tests run — unit only: `pytest tests/test_dormant_driver_sin_purge_service.py`
  (13/13 passing). Related existing suites re-run for regressions:
  `test_pre_launch_flag_service.py`, `test_admin_pre_launch_flag.py`,
  `test_legacy_sin_dob_import_service.py`,
  `test_admin_legacy_sin_dob_backfill.py` (76/76 passing, unaffected).
  `ruff check`/`ruff format --check` clean on all 3 new backend files.
- [x] Blast-radius grep performed — confirmed no existing caller of
  `vault.secrets`, `encrypt_driver_pii`, or `decrypt_driver_pii` reaches
  the new RPC or service; confirmed no other reader of `drivers.sin`
  behaves differently for a null value than it already does today for a
  driver who never had one.
- [x] `spinr-migration-reviewer` run on `413_purge_driver_pii_secret_fn.sql`
  before merge. **Verdict: SAFE TO APPLY, no blockers** — confirmed correct
  numbering, append-only, reversible-on-paper, forward-compatible against
  live traffic, and the exact post-138/359 `SECURITY DEFINER`/search_path/
  ownership/grant end-state (not an earlier transitional pattern that would
  reproduce migration 357's original `42501` gap). One item flagged for
  confirmation, not a fix: `apply_sin_purge`'s `_apply_one` doesn't catch
  an exception from the `purge_pii_secret` RPC call, so a failure there
  aborts that row's future loudly rather than being caught per-row.
  Checked against this exact codebase's own precedent —
  `driver_import_service.apply_legacy_sin_dob_import` does the same thing
  (raises hard on a malformed `encrypt_driver_pii` result rather than
  silently skipping) — confirming this is deliberate, matching CLAUDE.md's
  "do not silently swallow DB/PII errors" rule, not an oversight.
- [x] Reviewed against CLAUDE.md's PIPEDA conventions (this is a
  data-minimization purge, explicitly not the DSAR `purge_pii_retention()`
  flow, and does not touch anything that flow's own guards protect) and
  the migration conventions in `backend/migrations/CLAUDE.md` (append-only,
  next-available numbering, reversible-on-paper).
- [x] Feature-flagged if user-visible and non-trivial (or justify why not) —
  not user-visible (backend-only, no UI); deliberately NOT wired into any
  admin-dashboard button or background loop specifically because a
  destructive PII purge should stay a manual, deliberate action, not
  something a flag could accidentally leave on.

## What was NOT verified

- **Not run against a real Supabase project at all** — the `purge_driver_pii_secret`
  RPC's actual behavior against a live `vault.secrets` table (permission
  model, the `invalid_text_representation` exception path for a
  non-UUID/plaintext-era `secret_id`) is verified by code review and by
  matching the exact pattern of 3 other functions already proven correct
  in production (`encrypt`/`decrypt_driver_pii`, `encrypt`/`decrypt_emergency_contact_pii`)
  — not by actually invoking it, since doing so requires a live Supabase
  connection with vault access this session doesn't exercise destructively
  by design.
- **Not tested end-to-end against real dormant-driver data** — the 97
  candidates named in the 2026-09-10 finding were read live; this tool's
  own candidate query has not been re-run against production (it would
  return the same 97 today, since nothing has changed the flagged
  population, but that re-check wasn't performed here to avoid touching
  production from this pass).
- **Whether any of the 97 candidates have since gone active** (taken a
  ride, gone online) between 2026-09-10 and today — not re-checked; the
  tool's own query re-evaluates this fresh whenever it's actually run, so
  this doesn't affect correctness, only means today's exact count is
  unconfirmed.
- **The DOB / vehicle-history / rider-address purge decisions** remain
  fully open — not addressed, not scoped, not built here.

## 10. Sign-off

- [x] Rollback plan is concrete and testable for the migration itself;
  explicitly stated as **not** revertible once an actual purge has run,
  rather than glossed over
- [x] Blast radius is stated, not assumed — isolated, net-new, unreferenced
  by any existing code path; cannot fire before 2026-09-26 by hard
  code-level gate, not just documentation
- [x] No silent behavior change — nothing runs automatically; a human must
  deliberately invoke the CLI script on or after the cutoff date
