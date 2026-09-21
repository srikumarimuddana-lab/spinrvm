# Change Impact & Risk Log — `purge_pii_retention()` never deleted never-revoked refresh tokens

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | mkkreddy52@gmail.com (Claude Code assisted) |
| Surface(s) | backend (migration only) |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/purge-retention-never-revoked-tokens` |
| Related issue or gap ID | Found by `spinr-security-auditor` reviewing PR #5654; blocker raised there, fixed here |

## 1. Issue / gap identified

`purge_pii_retention()` Step E deleted a `refresh_tokens` row only if it had been **explicitly
revoked**. A token that was issued and then simply expired was never purged — retained indefinitely,
along with its `user_agent` and `ip` columns.

## 2. Root cause

Two independent facts that are each fine alone and broken together:

1. **The predicate narrowed.** Migration 50 (`migrations/50_pii_retention_purge.sql:212-223`) purged
   on expiry: `DELETE FROM refresh_tokens WHERE expires_at < cutoff`. Between migration 50 and 117
   it became `revoked_at IS NOT NULL AND revoked_at < cutoff`, and was carried verbatim through every
   `CREATE OR REPLACE` since — most recently `migrations/434_..._conflict.sql:179-189`, the live
   definition.
2. **Nothing stamps `revoked_at` on expiry.** Its only three writers are explicit acts: rotation
   (`utils/refresh_tokens.py:191`), revoke (`:636`), logout-all (`:680`). `lookup_refresh_token`
   (`:313-314`) returns `None` on an expired row and writes no column.

So `revoked_at` stays `NULL` forever for any abandoned session, and Step E's filter never matches it.

**Why now:** this was latent and harmless until 2026-09-21. `refresh_tokens.ip` had been storing a
constant Fly edge-proxy address (`172.16.x.x`), which is not personal information — retaining it
forever cost nothing. PR #5654 fixed that resolution bug so `ip` now holds the **real client IP**,
converting unbounded retention of a useless constant into unbounded retention of identifying PII.
The retention gap is older than PR #5654 and is not caused by it; PR #5654 is what makes it matter.

## 3. Fix / remediation

Add a second arm to Step E for never-revoked rows:

```sql
WHERE (revoked_at IS NOT NULL AND revoked_at < cutoff)
   OR (revoked_at IS NULL     AND expires_at < cutoff)
```

The first arm is **kept, not replaced** — revocation can precede expiry (a token revoked on day 1 of
a 30-day lifetime is purgeable on day 31, not day 61), so dropping it would *lengthen* retention for
the rows currently handled correctly. `expires_at` is `NOT NULL`
(`migrations/08_complete_schema.sql:168`), so the two arms together cover every row with no third
case to leak through.

### Alternative considered (gate 10)

Backfill `revoked_at` on expired rows (a one-off `UPDATE ... SET revoked_at = expires_at WHERE
revoked_at IS NULL AND expires_at < now()`), leaving Step E's predicate alone. **Rejected:** it
corrupts the meaning of the column — `revoked_at` means "someone revoked this," and reuse-detection
reads it (`_is_benign_rotation_replay`, `utils/refresh_tokens.py:104-130`) to distinguish a benign
rotation replay from a genuine theft. Overloading it with "expired naturally" would poison that
signal to fix a retention job. It also fixes only today's rows, leaving the predicate to re-accrue
the same backlog forever. Widening the predicate fixes the cause once.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface — one Postgres function, one table, one step.**

Mechanically verified that the executable SQL diff between 434's **function body** and 436's is
**exactly the two changed predicates** (the `DELETE` arm and the dry-run `COUNT` arm), with identical
executable line counts (339 vs 339) and no other table written by any changed line. This matters more
than usual: a `CREATE OR REPLACE` silently reverts any step accidentally dropped or mangled while
copying, so "only Step E changed" is a correctness requirement, not a nicety.

> **Correction (`spinr-migration-reviewer`):** an earlier revision of this entry, and the commit
> message for `6ba6b5b`, said "exactly the two predicates" without qualification. That overstated the
> scope of the check. My equivalence script compared lines 78-480 — the function body — which
> **excludes the trailing `COMMENT ON FUNCTION` string literal**, and that string was also
> deliberately changed (436:509 vs 434:486) to document the new fix. The reviewer re-derived the
> comparison independently with different boundaries (344 vs 344 lines, including the
> REVOKE/GRANT/COMMENT tail) and reached the same substantive conclusion — equal length, single
> logic-bearing diff region, no step silently dropped — while correctly catching that the
> documentation string is a second, benign changed region. The correct claim is: **the only
> logic-bearing change is Step E's predicate; the only other change is a documentation string.**

- **No live session can be signed out.** Every row the new arm deletes has `expires_at` in the past,
  so `lookup_refresh_token` (`:313-314`) already returns `None` for it. Deleting it cannot revoke
  access that still exists.
- **Reuse/replay detection is unaffected in practice.** `_handle_refresh_token_reuse` and
  `_record_post_revoke_race` (`utils/refresh_tokens.py:416`, `:591`) trigger on rows that *were*
  revoked — the first arm's population, whose purge timing is unchanged. A 30-day-expired,
  never-revoked row was not a live theft signal.
- **No index change needed, no seq scan introduced.** The new arm is served exactly by the existing
  partial index `idx_refresh_tokens_expires ON refresh_tokens (expires_at) WHERE revoked_at IS NULL`
  (`migrations/25_refresh_tokens_and_token_version.sql:62-64`) — the predicate matches the index's
  own `WHERE` clause.
- **No ride, dispatch, money, wallet, or insurance-period interaction.** No background loop other
  than the retention job reads this table for purge purposes.

**Principal risk — first-run volume.** This widens a `DELETE` to rows it has never touched on a table
that has been accumulating them for the life of the bug. A single unbatched `DELETE` could hold locks
longer than the purge job's normal tick. The migration header therefore makes a dry run **mandatory,
not advisory**, and supplies the exact pre-flight query:

```sql
SELECT
  count(*) FILTER (WHERE revoked_at IS NOT NULL AND revoked_at < now() - interval '30 days')
    AS purgeable_before_436,
  count(*) FILTER (WHERE revoked_at IS NULL     AND expires_at < now() - interval '30 days')
    AS newly_purgeable_by_436,
  count(*) AS total_rows
FROM refresh_tokens;
```

`newly_purgeable_by_436` is the entire blast radius as a number. If it is large enough to be a lock
concern, the header directs the operator to purge in batches out-of-band **before** applying, rather
than applying and letting the scheduled job take it all in one statement.

## 5. User-experience effect

**Nobody sees a difference — rider, driver, corporate admin, or internal admin.** No endpoint,
screen, response, or notification changes. Only already-invalid rows are removed, so no one is signed
out and nothing becomes visible mid-session to a rider mid-ride or a driver online. No copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/436_purge_retention_never_revoked_refresh_tokens.sql` | New. `CREATE OR REPLACE purge_pii_retention()` copied from 434 with Step E's predicate widened; header carries the dry-run procedure and rollback plan | Bound retention for never-revoked tokens |
| `backend/utils/retention_purge.py` | Docstring line 19 now describes both arms | It asserted `expires_at + 30 days`, which the live SQL had not implemented since ~migration 117 |
| `docs/runbooks/data-retention.md` | `refresh_tokens` row rewritten to state both arms and why each is needed | Same stale claim as above |

## 7. Before / after

```sql
-- Before (live, migrations/434_...:179-189) — never-revoked rows never match
DELETE FROM refresh_tokens
WHERE revoked_at IS NOT NULL
  AND revoked_at < v_started_at - c_token_grace_age;
```

```sql
-- After (436)
DELETE FROM refresh_tokens
WHERE (revoked_at IS NOT NULL AND revoked_at < v_started_at - c_token_grace_age)
   OR (revoked_at IS NULL     AND expires_at < v_started_at - c_token_grace_age);
```

Concrete scenario — a rider signs up, takes one ride, never reopens the app:

```
Day   0  refresh token issued, expires_at = day 30, revoked_at = NULL
Day  30  token expires. lookup_refresh_token returns None. revoked_at STILL NULL.
Day  60  Before: Step E does not match. Row survives.
Day 365  Before: row still there, holding the rider's real IP (post-PR #5654).
     ∞   Before: retained forever.
Day  60  After:  second arm matches (expires_at < now - 30d). Row deleted.
```

## 8. Rollback plan

Re-apply migration 434's definition of `purge_pii_retention()` verbatim. It is a pure
`CREATE OR REPLACE` with no DDL, so reverting the function is instant and needs **no second deploy**.
No schema change, no backfill, no index change to undo.

**The honest limit:** rows already deleted by a run of this version are **not** recoverable by that
revert — they need a PITR snapshot restore. That asymmetry is exactly why the dry run in §4 is
mandatory rather than advisory, and why this was not bundled into PR #5654. Per CLAUDE.md, a
`git revert` is not a rollback plan for something already applied to live data; the rollback plan for
the *data* is PITR, and the rollback plan for the *behavior* is re-applying 434.

## 9. Verification performed

- [x] **Mechanical 434→436 equivalence check** — executable SQL diff is exactly the two predicates;
      339 vs 339 executable lines; no other table written by a changed line.
- [x] **Blast-radius grep** — all `revoked_at` writers (3, all explicit); `lookup_refresh_token`'s
      expiry path (writes nothing); readers of `refresh_tokens.ip`; existing indexes on the table;
      `expires_at` nullability.
- [x] **Index support confirmed** — existing partial index matches the new arm's predicate exactly.
- [x] **Migration numbering** — `ls backend/migrations | sort -V | tail -1` → 435; 436 is next free,
      no collision, no renumbering of existing files.
- [x] **Reviewed against `backend/migrations/CLAUDE.md`** — append-only (new file, no edit to a merged
      one), rollback plan in a top comment, no new index needed, idempotent `CREATE OR REPLACE`.
- [x] **`spinr-migration-reviewer`** run against the actual diff (gate 10). **Verdict: FIX BLOCKERS
      (all now resolved) + NEEDS DBA REVIEW.** It independently re-derived the 434→436 comparison
      and confirmed no step was silently dropped, and independently confirmed: `expires_at` has
      never had `NOT NULL` loosened (zero `DROP NOT NULL` hits across the migration tree); the
      partial index was never dropped; no production reader depends on never-revoked expired rows;
      `NEVER_APPLY` is unaffected. Its findings and their disposition:
      1. *Blocker — missing `migration-override-ok` marker.* Already fixed in `369ed60` before the
         review returned; CI confirms it (Migration Safety Check went from 2 failures to 1).
      2. *Warning — CHECK E needs a CR + admin merge-override, do not weaken any DELETE.* Matches
         the read already acted on; CR filed as #5656.
      3. *Warning — the daily caller has no batching or row cap.* **New, acted on** — added the
         03:00 UTC timing warning to the migration header (§4 below).
      4. *Precision note — the "exactly two predicates" claim omitted the COMMENT string.*
         **Correct; retracted in §4 above.**
      5. *Informational — a pre-existing redundant index `idx_refresh_tokens_expires_at`
         (`50_pii_retention_purge.sql:87-89`).* Not introduced here, nothing to change.
- [ ] **Executed against a real Postgres** — not done, see §10.
- [x] **Feature flag** — not applicable; a retention predicate cannot be meaningfully flagged. The
      dry-run gate serves the same "measure before committing" purpose.

## 10. What was NOT verified

- **The SQL has never been executed.** No Postgres was available in this environment (the RLS tier
  needs `TEST_DATABASE_URL` and none is set; package installs are blocked by the network policy, so
  no local server could be brought up either). The function was verified by mechanical text
  equivalence against 434 and by reading, **not by running it**. It has not been parsed by Postgres,
  so a syntax error in the copied body would not have been caught here. Apply `--dry-run` first.
- **`newly_purgeable_by_436` is unknown.** The pre-flight query exists but has not been run — nobody
  yet knows whether this deletes a thousand rows or a million. That number must be obtained before
  applying, and it determines whether batching is required.
- **No staging run.** Not applied to any environment, including staging.
- **The first purge run is unattended and unbatched.** `run_retention_purge_tick`
  (`utils/retention_purge.py:148-170`) makes one `rpc("purge_pii_retention", ...)` call with no row
  cap, no chunking, and no automatic dry-run gate, fired daily at ~03:00 UTC by
  `retention_purge_loop`. Nothing in the calling code will measure the backlog first. **Someone with
  production DB access must run the header's pre-flight query, and the migration must be applied
  with lead time before 03:00 UTC, not just before it.**
- **Historical `ip` values are not corrected.** Rows written before PR #5654 hold the Fly proxy
  address; the real IP was never captured and cannot be reconstructed. This migration deletes such
  rows on schedule but does not fix the ones still inside the window.
- **No production build** — backend/SQL only, no frontend surface touched.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (with its PITR caveat stated, not hidden)
- [x] Blast radius is stated, not assumed — and quantified as a query the operator must run
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
