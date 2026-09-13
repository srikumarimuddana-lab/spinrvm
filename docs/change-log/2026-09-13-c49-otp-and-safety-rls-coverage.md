# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_01Aq7xVA8zptrqFwExvqVrwN`) |
| Surface(s) | backend (test-only) |
| Domain (Sentry tag) | auth, safety |
| PR / commit link | branch `mvapps/dreamy-faraday-2wz914-c49-rls-coverage` |
| Related issue or gap ID | ACTION_ITEMS.md C49 (progressed, not closed); C111 (new, informational) |

## 1. Issue / gap identified

Five auth/safety-sensitive tables — `otp_records`, `rider_email_verification_otp`,
`emergency_contacts`, `safety_incidents`, `safety_incident_photos` — had zero
DB-role-level RLS test coverage. This repo's mocked test suite can never
exercise real Postgres Row-Level Security (RLS is enforced by the Postgres
engine itself against a real role-scoped connection, not something a mocked
Supabase client can simulate), so a real policy bug on these tables could
ship undetected.

## 2. Root cause

Not a bug — a coverage gap. `backend/tests/rls/` (the real-Postgres RLS
harness) has been built incrementally across several sessions since
2026-08-31; these 5 tables were simply not yet in scope. Picked this round
specifically (over the ~41 total remaining uncovered tables) because they're
the highest-consequence subset still missing coverage — auth/OTP and
safety/SOS are two of CLAUDE.md's explicitly flagged highest-caution
domains.

## 3. Fix / remediation

Added a new test file, `backend/tests/rls/test_otp_and_safety_rls.py` (36
tests), and extended `backend/tests/rls/conftest.py` to apply the real
shipped SQL for these 5 tables:

- `otp_records`: already fully wired (stub table + `supabase_rls.sql`'s
  `otp_deny_all` policy) from prior rounds — no conftest.py change needed,
  just the missing test file.
- `rider_email_verification_otp`: migration 362 only (the corrected
  replacement for migration 299, which the migration's own header comment
  says errors against this schema's `TEXT users.id` and was never actually
  applied in production).
- `emergency_contacts`: migration 120, verbatim.
- `safety_incidents`: migration 94 only (migrations 280/315 add nullable
  columns with no RLS effect — confirmed by reading both, not assumed).
- `safety_incident_photos`: migration 340, sliced via the harness's existing
  `_extract_section()` helper to exclude its `INSERT INTO storage.buckets`
  statement (this harness has no `storage` schema stub; confirmed by
  adversarial review that the excluded slice drops only that insert, no
  GRANT/policy/COMMENT).

Also caught and fixed, mid-session (not part of the reviewed diff): created
this branch off a stale local `main` (98 commits diverged from
`origin/main`) by mistake, instead of `origin/main` directly. Caught before
committing anything; recreated the branch correctly and verified via `git
diff --stat` and a full test re-run that nothing was lost in the process.

**Alternative considered:** cover a broader/random slice of the ~41
remaining uncovered tables in one larger PR, or attempt all of them at
once. **Rejected**: CLAUDE.md's task-decomposition rule caps subtasks at a
reviewable size, and a 41-table PR would be far harder to review correctly
than a themed, high-consequence 5-table slice — this also matches how every
prior round of this same backlog item was scoped (5-11 tables per round).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, test-only.** No production code changed. Two
  files: `backend/tests/rls/conftest.py` (extended) and
  `backend/tests/rls/test_otp_and_safety_rls.py` (new).
- **Other consumers of `conftest.py`'s `pg_cur` fixture or TRUNCATE list:**
  checked — no other test file in `backend/tests/rls/` references any of
  the 5 new tables, so nothing else could break. The 5 new TRUNCATE entries
  each run as an individual `TRUNCATE ... CASCADE` statement, so ordering
  relative to the `safety_incident_photos` → `safety_incidents` FK doesn't
  matter (CASCADE handles it regardless of order).
- **Verified against real production usage, not just the SQL:** grepped
  `routes/safety.py`, `routes/admin/safety.py`, and
  `utils/safety_checkin_loop.py` — every write to `safety_incidents` goes
  through the backend's service-role-backed helper layer
  (`db_supabase.insert_one`), never an authenticated-role client. The
  restrictive "INSERT/DELETE: service_role only" policy this diff's tests
  assert matches real production usage; no code path would break if this
  policy were ever tightened further.
- **Adversarial review (`spinr-security-auditor`, CLAUDE.md gate #10)
  performed before commit:** independently re-derived every policy the new
  test file asserts against directly from the migration SQL (not the test
  file's own docstring claims), verified the skip-299/skip-280/skip-315
  decisions by reading those files in full (not trusting the diff's
  comments), verified the migration-340 `_extract_section()` slice boundary
  drops only the storage-bucket insert, and verified every "raises
  `InsufficientPrivilege`" vs "silently affects 0 rows" test expectation
  against real Postgres RLS semantics for each specific policy shape.
  **Verdict: zero bugs found, safe to commit as-is.**
- **One real, pre-existing production finding surfaced by that review, not
  introduced by this diff:** `emergency_contacts` has no admin/super_admin
  override policy at all (unlike `safety_incidents`, which deliberately
  grants one). Not a live gap today — the only real read path
  (`routes/safety.py`'s SOS notification) uses the service-role client,
  bypassing RLS — but worth a deliberate decision if an admin-facing
  emergency-contact lookup is ever built. Filed as ACTION_ITEMS.md C111
  (informational, not blocking, not fixed by this diff).

## 5. User-experience effect

None. Test-only change; no production code, no deploy artifact, no
behavior change to any rider/driver/admin-facing flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/rls/conftest.py` | Applies migrations 362, 120, 94, and a sliced 340; adds a baseline GRANT for the 4 new real tables; adds all 5 new tables to the `pg_cur` TRUNCATE list | Wires the harness's real-Postgres database to include these 5 tables' actual shipped schema + policies |
| `backend/tests/rls/test_otp_and_safety_rls.py` | New file, 36 tests | DB-role-level RLS coverage for the 5 tables |
| `ACTION_ITEMS.md` | C49 status updated (this round + catch-up on undocumented prior-session progress); new C111 filed | Keep the backlog record accurate — same discipline this file's own history requires of every round |

## 7. Before / after

Not applicable — pure test addition, no existing behavior changed.

## 8. Rollback plan

`git-revert-safe` — a plain revert removes the new test file and reverts
`conftest.py`'s additions. No data, no migration, no config involved either
direction.

## 9. Verification performed

- [x] Automated tests run: `backend/tests/rls/test_otp_and_safety_rls.py`
      standalone (36 passed) and the full `backend/tests/rls/` suite (230
      passed, 0 failed) against a real local Postgres 16 instance started
      in this sandbox specifically for this verification — per this
      suite's own documented invocation
      (`pytest tests/rls -c /dev/null --confcutdir=tests/rls`).
- [x] Blast-radius grep performed: every other RLS test file for a
      reference to any of the 5 new tables (none found); every production
      write path to `safety_incidents` (all service-role); every
      `routes/admin/*.py` file for an `emergency_contacts` reader (none
      found, informing the C111 write-up).
- [x] Reviewed against relevant CLAUDE.md conventions: this is exactly the
      "Testing Conventions" RLS tier this file documents; auth/safety
      domain caution applied via the mandatory adversarial review before
      commit (gate #10).
- [x] Adversarial review performed and passed with zero findings requiring
      a fix (one informational finding filed separately, C111).
- [ ] Not run against CI's actual `postgres:15` service container — run
      against a local Postgres 16 instead. `ci.yml` runs `tests/rls`
      against `postgres:15` per C49's own prior correction; this session's
      local Postgres 16 is close enough for the SQL surface these
      migrations use (no version-specific syntax), but the exact CI
      container was not itself exercised.

## What was NOT verified

- Not run against CI's own `postgres:15` container (see above) — a real
  local Postgres 16 instance was used instead, matching the version other
  recent rounds of this same backlog item used.
- The C111 informational finding (`emergency_contacts` admin-override gap)
  was investigated only by reading code (migrations + `routes/admin/*.py`
  grep) — no live/staging verification, consistent with it being a
  documentation/decision item, not a code fix.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`, test-only.
- [x] Blast radius stated: two files, test-only, no other consumer affected.
- [x] No silent behavior change to a working flow — nothing in production
      changed; this is pure test coverage addition.
