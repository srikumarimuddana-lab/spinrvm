# Change Impact & Risk Log — C50 Phase 2 direct-pool claim path: review fixes (PR #4873)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude Code review pass on PR #4873 (with PR #4881's six fix commits folded in) |
| Surface(s) | backend, migrations, CI, loadtest, docs |
| Domain | dispatch (safety-adjacent: insurance periods) |
| PR / commit link | #4883 (`fix/c50-phase2-direct-pool-review-fixes`), off `main` after #4873 and #4881 merged |
| Related issue or gap ID | `ACTION_ITEMS.md` C50; C54–C56 (renumbered from the branch's C51–C53) |

## 1. Issue / gap identified

The Phase 2 half of #4873 (migration `dispatch_claim_batch`, the `matching.py` gate, the
psycopg3 pool) was never reviewed — the earlier review stopped at `f4df2af`. A full pass
found ten High and fourteen Medium findings. The ones that change behaviour:

- **Deadlock/convoy in the batched claim.** The per-driver claim was a bare
  `UPDATE … WHERE id AND is_available` inside one transaction that holds every claimed row's
  lock until COMMIT. Two dispatches ranking the same drivers in different orders block on
  each other; Postgres aborts one with `40P01` after `deadlock_timeout` and the ride waits a
  full retry backoff. Impossible on the PostgREST path (one UPDATE per transaction).
- **Flag on + pool closed stalls all dispatch.** The flag is re-read per attempt but the
  pool opens only at boot; flipping the flag on with `DISPATCH_POOL_DSN` unset made every
  attempt raise and every ride sit in `searching` until the flag was flipped back.
- **Insurance-period write failures were invisible on the direct path.** A failed Period-2
  write inside the RPC surfaced only as a Postgres `RAISE WARNING`; no ERROR log, no
  `spinr_insurance_period_write_failed_total` increment.
- **Query execution was unbounded.** The client deadline bounded connection acquisition only;
  a stalled Postgres could park all eight pooled connections indefinitely.
- **`SECURITY DEFINER` with `search_path = public, pg_catalog`** (public first — the
  CVE-2018-1058 shape), `NULL p_max_offers` claiming every candidate, a `unique_violation`
  on re-offer rolling back the whole batch, migration prefix 400 colliding with `main`,
  `--ignore=tests/rls` dropping the RLS suite from CI, the DB-call metric undercounting
  gathered children, live staging tokens written into a committed directory, and the
  seed script's environment guard being a production denylist rather than an allowlist.

Full list with file:line evidence: the review posted on #4873.

## 2. Root cause

Phase 2 translated a one-claim-per-transaction Python loop into a one-transaction-per-batch
SQL function without revisiting the locking model, the observability contract of the
best-effort insurance write, or the runtime coupling between a per-attempt flag and a
boot-time pool. The infrastructure PR and its review branch (#4881) then diverged, so the
Phase 2 commits never received the fixes the review had already made.

## 3. Fix / remediation

#4873 and #4881 both merged while the review was in progress (`main` therefore already
carries #4881's six fixes and the 401/402 migration renumber), so the fixes below are a
fresh series on `fix/c50-phase2-direct-pool-review-fixes` (#4883), each one logical change:

| Commit | Change |
|---|---|
| `fix(migrations): harden dispatch_claim_batch …` | `FOR UPDATE SKIP LOCKED` claim; 399-style argument validation; `SECURITY INVOKER` + `pg_catalog, public`; `ON CONFLICT (ride_id, driver_id) DO NOTHING` with release; `insurance_written` return column; release clamped to `is_online`; `NOTIFY pgrst` |
| `fix(dispatch): guard the direct-pool claim path …` | `is_open()` guard → loud PostgREST fallback + `spinr_dispatch_claim_path_total{path=postgrest_pool_unavailable}`; `insurance_written=false` → ERROR log + `spinr_insurance_period_write_failed_total{reason=direct_pool}`; redacted error log; bounded, concurrent cache invalidation carrying `user_id` |
| `fix(dispatch-pool): bound query execution …` | transaction-local `statement_timeout` (remaining deadline capped at 10 s) + `asyncio.wait_for` backstop; `check=check_connection`; wait histogram observed on timeout; `_redact_dsn`; pool closed on failed open; `::text[]`/`::int[]` casts and empty-list early return; corrected lock-count docstring; sub-ms queue-wait buckets |
| `fix(config,lifespan): …` | `Field(ge=…)` + min ≤ max validator; boot-time flag read bounded to 10 s |
| `test(dispatch): …` | psycopg3 real-pool test; SKIP LOCKED two-session proof; ON CONFLICT; argument validation; privileges; pool-closed fallback; insurance reporting; RPC argument alignment |
| `fix(ci): …` | real `DATABASE_URL` composed from PG* vars (un-skips `test_phase_distance_parity.py`); `.gitignore` for `loadtest/results/*.json`; `.coveragerc` omit |
| `fix(loadtest): …` | seed script environment allowlist, exhaustive server-side cleanup behind `--yes`, decimal-string money; harness refuses production hosts; email redacted |

## 4. Risk & impact on existing functionality

- **Blast radius:** single-surface (backend dispatch) while `dispatch_direct_pool_enabled`
  is off — the PostgREST claim loop is untouched except for the additive
  `spinr_dispatch_claim_path_total` counter that already existed on the branch. With the
  flag on, every ride assignment goes through `dispatch_claim_batch`.
- **Other readers/writers of the same state:** `driver_claim_reaper` (keys off
  `availability_claimed_at` + no pending offer — unchanged semantics), `offer_expiry_reaper`
  (`ride_offers.expires_at` — now guaranteed non-NULL by the RPC), `stuck_ride_sweeper`,
  `stale_intent_reconciler`, `set_driver_available`, `claim_driver_atomic` (the PostgREST
  path, unchanged). `_pre_invalidate_for_table` is bypassed by an RPC, which is why
  `matching.py` evicts both cache keys per attempted driver.
- **Background loops:** none added or modified. `lifespan.init_database` gains a 10 s bound
  on an existing read; `cleanup_database` already closed the pool.
- **What could regress:** the `SKIP LOCKED` claim means a driver locked by a concurrent
  batch is skipped rather than waited for — the same outcome as losing the race today, but
  reached faster. The release clamp (`is_available = is_online`) changes one edge: a driver
  who went offline between the candidate read and the claim is released as unavailable
  rather than available, which is the invariant `set_driver_available` already enforces.
- **Migration numbering:** the two migrations were renamed; neither had been applied
  anywhere (staging `schema_migrations` tops out at 371 per the T16 round-2 report; nothing
  merged). If either was applied by hand somewhere, the runner will see the new filename
  as pending — check `schema_migrations` before deploying.

## 5. User-experience effect

None while the flag is off. With the flag on: no visible change in the normal case; in the
concurrent-dispatch case riders no longer wait a retry backoff for a deadlock to resolve.
Not visible mid-session to anyone already using the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/401_settings_dispatch_direct_pool_enabled.sql` | renamed from 400 | prefix collided with `main`'s 400 |
| `backend/migrations/402_dispatch_claim_batch.sql` | renamed from 401; function body hardened | deadlock, visibility, security, validation, conflict handling |
| `backend/routes/rides/matching.py` | pool-open guard, insurance reporting, redacted log, bounded invalidation | flag-on failure modes |
| `backend/repositories/dispatch_pool.py` | statement timeout, liveness check, timeout metric, DSN redaction, leak close, casts | unbounded execution, blind saturation signal, credential leak, binding risk |
| `backend/repositories/_base.py` | docstring numbers, queue-wait buckets | wrong derived ceilings; unusable histogram |
| `backend/core/config.py`, `backend/core/lifespan.py` | size validation; bounded boot read | fail at load, not in production; no boot hang |
| `backend/tests/**` (5 files + 1 new) | see commit | coverage of every fix |
| `.github/workflows/ci.yml`, `.gitignore`, `backend/.coveragerc` | DSN composition, token cache ignore, omit | see §3 |
| `backend/scripts/seed_loadtest_bots.py`, `loadtest/preauth_bots.py`, T16 doc | guards, cleanup, redaction | production safety, PII |
| `ACTION_ITEMS.md`, retro doc, `requirements.txt` | merge resolution | numbering collision, lockfile drift |

## 7. Before / after

Claim step in `dispatch_claim_batch` (the deadlock fix):

```sql
-- before
UPDATE drivers
SET is_available = false, availability_claimed_at = now()
WHERE id = v_driver_id AND is_available = true
RETURNING * INTO v_driver_row;

-- after
UPDATE drivers AS d
SET is_available = false, availability_claimed_at = now()
FROM (SELECT c.id FROM drivers AS c
      WHERE c.id = v_driver_id AND c.is_available = true
      FOR UPDATE OF c SKIP LOCKED) AS locked
WHERE d.id = locked.id
RETURNING d.* INTO v_driver_row;
```

Gate in `matching.py` (the stall fix):

```python
# before
_direct_pool_enabled = bool(app_settings.get("dispatch_direct_pool_enabled", False))

# after
_direct_pool_enabled = bool(app_settings.get("dispatch_direct_pool_enabled", False))
if _direct_pool_enabled and not _dispatch_pool.is_open():
    logger.error("[DISPATCH] dispatch_direct_pool_enabled is on but the direct pool is not open ...")
    _metric_inc("spinr_dispatch_claim_path_total", labels={"path": "postgrest_pool_unavailable"})
    _direct_pool_enabled = False
```

## 8. Rollback plan

- Runtime: set `app_settings.dispatch_direct_pool_enabled = false` (admin PUT
  `/api/admin/settings` or SQL `UPDATE settings SET dispatch_direct_pool_enabled = false
  WHERE id = 'app_settings'`). Propagates within the 60 s settings cache; no redeploy. Note
  the admin dashboard has no UI toggle for this field yet.
- Schema: `DROP FUNCTION IF EXISTS public.dispatch_claim_batch(text, text[], int[], int,
  timestamptz, timestamptz);` (nothing calls it while the flag is off). Migration 401's
  column is additive and can stay.
- **Deploy order:** apply migrations 401 and 402 before the backend that carries this
  change. A backend that ships first makes `PUT /api/admin/settings` with the new field
  fail with PGRST204 until the column exists.
- No Stripe, wallet, or ride-state data is touched by the fixes; `git revert` of the code
  commits is safe on its own.

## 9. Verification performed

- [x] `ruff check` and `ruff format --check` on every changed Python file; `py_compile`.
- [x] Migration 402 applied to a throwaway local Postgres 16 cluster via `psql` using the
      same schema extraction and migration list `tests/direct_pool/conftest.py` uses, then
      exercised: ordered claim with stop-at-max, revalidation release with the `is_online`
      clamp, NULL/zero/over-cap `p_max_offers` and NULL/inverted timestamps raising with no
      writes, empty array, `ON CONFLICT` release keeping the rest of the batch, forced
      insurance-write failure reporting `insurance_written = false` with claim and offer
      intact, `has_function_privilege` false for `anon`/`authenticated`, and the two-session
      `SKIP LOCKED` check (an uncommitted claim in session A; session B's batch returns
      immediately with that driver unclaimed, 39 ms) plus a negative control: the pre-fix
      function body installed under another name blocked on the same locked row until the
      2.5 s statement timeout, so the check detects the regression it was written for.
      This run also caught a defect in the first cut of the fix — inside plpgsql
      `ON CONFLICT (ride_id, driver_id)` is ambiguous because `driver_id` is an output
      variable — corrected to `ON CONFLICT ON CONSTRAINT ride_offers_ride_driver_uq`.
      Results are in the review posted on #4873.
- [x] Pre-commit hook (secret scan, forbidden files, PII-in-logs, money arithmetic,
      doc-cited paths) on every commit.
- [ ] `pytest` — **not run here**; unavailable in this environment (no PyPI access). CI runs
      the mocked suite and the direct-pool real-Postgres step.
- [ ] `psycopg3` — **not importable here**; `test_claim_batch_psycopg3.py` first executes
      in CI.
- [ ] No production build applies (backend only).

## 10. What was NOT verified

- Nothing was run against production or staging. Pooler mode/port/pool size on the real
  Supabase project remain unconfirmed (C50 plan gate G5).
- `pip-compile` was not run; `requirements.txt` was hand-resolved in `main`'s style and
  `pip-compile-check.yml` must confirm it.
- CI has never triggered on this PR (zero check runs on every head so far); if the next push
  also produces none, the workflow trigger, not the code, needs attention.
- The T16 round-2 report records a `DISPATCH_POOL_DSN` Fly secret already staged on
  `spinr-backend-staging` that nobody in that session set. It will activate on the next
  staging deploy; its origin should be confirmed before merge.

## Sign-off

- [ ] Reviewer with dispatch ownership has read §4 and §8
- [ ] Migrations 401/402 applied to staging before the backend deploy
- [ ] `dispatch_direct_pool_enabled` confirmed `false` in production `settings`
