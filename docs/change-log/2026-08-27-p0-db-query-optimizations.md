# Change Impact & Risk Log — P0 database query optimizations (#4583–#4590)

> Backfilled 2026-08-31 as part of a gate-compliance sweep
> (docs/audit/2026-08-27-cicd-gates-guardrails-audit.md's follow-through) —
> PR #4593 merged 2026-08-27 with only a Tier 1 summary filled in; Tier 2
> (blast radius), Tier 3 (compliance flags — despite touching a money
> path and shipping 2 migrations), and Tier 5's money/migration checklists
> were all left as unfilled template. This entry reconstructs it from the
> actual merged diff.

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 (PR merged) / entry backfilled 2026-08-31 |
| Author | Claude Code (original PR); backfilled by Claude Code |
| Surface(s) | backend, migrations |
| Domain (Sentry tag) | payments, drivers, admin |
| PR / commit link | #4593 (merge commit `152c6f1`) |
| Related issue or gap ID | Closes #4583–#4590 (2026-08-26 DB query optimization audit, PR #4579) |

## 1. Issue / gap identified

Eight separate P0 items from a DB query audit: missing indexes, a
`get_rows(limit=0)` bug that returned all rows instead of zero, blocking
Supabase calls not wrapped in `run_sync`, a dead unassigned query, a
redundant `get_ride()` round-trip in `confirm_payment`, no default row cap
on `get_rows`, and two regulatory export functions (GST/PST, T4A) that
silently truncated at a row cap — a compliance risk, not just a
performance one.

## 2. Root cause

Accumulated technical debt across the query layer, not one root cause —
this PR is a P0 remediation batch, not a single bug fix.

## 3. Fix / remediation (item by item)

| # | Item | Change |
|---|---|---|
| #4583 | Missing indexes | Migration 368: composite + 2 FK indexes on `driver_documents`, all `CONCURRENTLY IF NOT EXISTS` |
| #4584 | Duplicate indexes | Migration 369 — **shipped broken** (see Section 4, this is the most important finding in this backfill) |
| #4585 | `get_rows(limit=0)` bug | `elif limit:` → `elif limit is not None:` so `limit=0` correctly returns `[]` instead of silently falling through to no-limit (all rows) |
| #4586 | Blocking calls | 3 direct `.execute()` calls in `earnings.py`/`ride_reads.py` wrapped in `run_sync` |
| #4587 | Dead query | Removed an unassigned `count_documents` call + unused variable in `promotions.py` |
| #4588 | Payment path dedup | `confirm_payment`: 2 of 3 `get_ride(ride_id)` calls replaced with reuse of the already-fetched, already-ownership-checked `ride` variable — see Section 4 for the real verification this needed |
| #4589 | Default row limit | `_DEFAULT_ROW_LIMIT = 1000` added to `get_rows()` when no `limit` is passed — cross-cutting, see Section 4 |
| #4590 | Regulatory export truncation | New `_get_all_rows_paginated()` helper; GST/PST and T4A export functions switched to it so they fetch ALL matching rows instead of capping at `_ROW_LIMIT` |

## 4. Risk & impact on existing functionality — the two items that needed real scrutiny

### #4588 — `confirm_payment` ownership-check reuse (money path)

**Verified independently, not taken on the PR's own word.** The code
fetches `ride` once near the top of `confirm_payment`, checks
`ride["rider_id"] != current_user["id"]` there (403 if mismatched), then
reuses that same `ride` object later instead of a second `get_ride()`
round-trip. The PR's own comment claims this is safe because
`rides.rider_id` is never mutated after ride creation. Independently
grepped `backend/routes/`, `backend/services/`, `backend/repositories/`
for any `update_ride`/`update_one` call whose payload includes a
`rider_id` key — **found none**. The claim holds: a second fetch could
never have observed a different `rider_id` than the first, so the removed
re-check was genuinely dead code from a security standpoint, not a live
guard being removed.

One real, honestly-disclosed side effect: this dedup is what broke
`test_confirm_payment_real_ride_ownership_mismatch` (it scripted a second,
different-owner `get_ride` mock response that this code path no longer
consumes) — later reconciled by a separate fix (see #4622 / the mock
drift referenced in PR #4594's own merge notes). Not a functional
regression, but worth recording as this PR's downstream test-suite
consequence.

### #4589 — `_DEFAULT_ROW_LIMIT = 1000` on `get_rows()` — genuinely cross-cutting, **not fully blast-radius-checked**

`get_rows()` is the core repository read helper used across the entire
backend. This change means **every caller that omits an explicit `limit`
now silently caps at 1000 rows** instead of fetching everything — a
correctness change, not just a performance one, for any caller that
genuinely expected all matching rows.

The PR itself found and fixed exactly 2 such callers (GST/PST and T4A
regulatory exports, #4590) — but grepping `get_rows(` across `backend/`
turns up **200+ call sites**, and a same-line check for a missing
`limit=` kwarg is not a reliable signal (many calls pass `limit=` on a
wrapped line a single-line grep won't see). **A full audit of every
caller to confirm none of the *other* ~200 sites relied on unbounded
fetch was not performed by the original PR** (no evidence of one in the
diff or description) **and was not performed by this backfill either** —
the scale of that audit (confirming each call site's actual row-count
expectations) is its own task, not something to rush inside a
documentation backfill, and asserting "verified safe" without doing it
would be exactly the kind of false-certainty this Change Impact Log
process exists to prevent.

**This is a real, open gap, named explicitly rather than smoothed over.**
Recommended as a follow-up: grep every `get_rows(` call site repo-wide
(handling multi-line calls) and confirm each either passes an explicit
`limit`, or is verified safe to silently cap at 1000 rows.

### Migration 369 — the most consequential finding in this backfill

**This PR shipped `backend/migrations/369_drop_duplicate_indexes.sql` in
a broken state** — its `DROP INDEX IF EXISTS` statements used literal,
unfilled placeholder text (`<DUPLICATE_INDEX_NAME_1>` etc.), which is a
SQL syntax error. The migration file's own header explicitly called this
out as **"ACTION REQUIRED (DevOps-Omar)"** — i.e., it was authored and
merged as a template for a human to finish, not as applyable SQL.

Because `backend/scripts/run_migrations.py` applies migrations in
filename order and hard-stops on the first failure, this meant **every
migration numbered 363 and above was blocked from ever applying** until
369 was fixed — which didn't happen until 2026-08-27, and the actual
`run_migrations.py` execution catching production up (363→371) didn't
happen until 2026-08-29, roughly two days after this PR merged. This is
the direct, traced root cause of two separate live-production incidents
found and fixed earlier in this same audit thread: `GET
/admin/drivers/stats` throwing on a missing RPC function, and the
active-trip GPS gap monitor being completely non-functional — both from
migrations (370, 371) that depended on 369 clearing first. See
`docs/audit/2026-08-27-cicd-gates-guardrails-audit.md`'s follow-through
work for the full incident.

**Blast radius of shipping a broken migration file**: every PR merged
after this one that added a new migration was silently building up an
unapplication backlog with no CI signal — `migration-check.yml` validates
migration *syntax/conventions*, not whether the migration *actually
applies* to production, so a broken earlier migration blocking a
perfectly fine later one produces no red check anywhere in this repo's
CI. This is a genuine gap in the pipeline, not just a one-off authoring
mistake — worth flagging for `ACTION_ITEMS.md` if not already tracked
there.

## 5. User-experience effect

- **Rider**: none directly from #4588 (payment confirmation still behaves
  identically — same ownership check, same 403 on mismatch, just fewer DB
  round-trips to get there).
- **Driver**: none from #4586/#4587.
- **Internal admin**: #4590 fixed a real compliance gap — GST/PST
  remittance and T4A/CRA exports were silently truncating above
  `_ROW_LIMIT`, which is itself a regulatory risk (incomplete tax filing
  data) independent of this PR's performance framing.
- **Everyone, indirectly**: the ~2-day migration-backlog gap from the
  broken migration 369 (see above) left two backend code paths
  (admin stats, GPS gap monitoring) broken in production until resolved.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/368_add_driver_documents_indexes.sql` | 3 new `CONCURRENTLY` indexes | #4583 |
| `backend/migrations/369_drop_duplicate_indexes.sql` | **Shipped with unfilled placeholder SQL — broken until a later fix** | #4584 |
| `backend/repositories/_base.py` | `limit=0` fix; `_DEFAULT_ROW_LIMIT = 1000` | #4585, #4589 |
| `backend/routes/admin/compliance.py` | New `_get_all_rows_paginated()`; GST/PST + T4A exports switched to it | #4590 |
| `backend/routes/drivers/earnings.py` | 2 blocking calls wrapped in `run_sync` | #4586 |
| `backend/routes/drivers/ride_reads.py` | 1 blocking call wrapped in `run_sync` | #4586 |
| `backend/routes/payments.py` | `confirm_payment`: reuse `ride` instead of re-fetching (2 sites) | #4588 |
| `backend/routes/promotions.py` | Removed dead unassigned query + unused variable | #4587 |

## 7. Before / after

```python
# Before — get_rows(limit=0) fell through to "no limit" (all rows)
elif limit:
    q = q.limit(limit)
```

```python
# After — limit=0 correctly means zero rows
elif limit is not None:
    q = q.limit(limit)
```

```python
# Before — confirm_payment re-fetched a ride it had already fetched and checked
if ride_id:
    _ride = await db_supabase.get_ride(ride_id)
    if not _ride or _ride.get("rider_id") != current_user["id"]:
        raise HTTPException(...)
```

```python
# After — reuse the already-checked object; rider_id is never mutated post-creation (verified)
if ride_id:
    _ride = ride  # reuse already-fetched & validated ride (was: get_ride round-trip)
```

## 8. Rollback plan

- `#4583`/`#4585`/`#4586`/`#4587`/`#4588`/`#4590`: `git-revert-safe` — all
  read-path or additive changes, no data written.
- `#4589` (`_DEFAULT_ROW_LIMIT`): also `git-revert-safe` in isolation, but
  see Section 4 — reverting would restore unbounded fetches everywhere,
  which is the state that made the GST/PST/T4A truncation-vs-compliance
  problem this same PR fixed possible in the first place. Not a clean
  either-direction rollback; a revert should be paired with re-confirming
  #4590's paginated exports don't silently truncate again.
- Migration 369: **already fixed by a later change** (its current
  on-disk content has real `DROP INDEX` statements against
  already-confirmed-absent production indexes, not the placeholder text
  this PR shipped). No rollback needed for the current state; documented
  here for the historical record of what actually happened between merge
  and fix.

## 9. Verification performed (backfilled, not the original PR's own claim)

- [x] Blast-radius grep performed for #4588 (rider_id mutation sites) —
      **confirmed safe**.
- [ ] Blast-radius grep for #4589 (`_DEFAULT_ROW_LIMIT`) — **attempted,
      not completed**: 200+ call sites found, full per-site verification
      out of scope for this backfill, named as an open gap rather than
      signed off on.
- [ ] Automated tests run — not re-run as part of this backfill (would
      require reconstructing the PR's original test environment).
- [ ] Manual repro / staging — no staging environment existed at merge
      time.
- [x] Reviewed against `CLAUDE.md` money convention — confirmed no
      Decimal/float handling changed in `confirm_payment`; the dedup is
      purely about which fetch supplies the same already-checked object.

## 10. Sign-off

- [x] Rollback plan is concrete for 7 of 8 items; #4589's is qualified,
      not silently assumed clean
- [ ] Blast radius is stated, but **not fully verified** for #4589 —
      named as open, not claimed complete
- [x] No silent behavior change to an already-shipped flow without
      disclosure — the migration-369 incident chain is traced and stated
      plainly rather than left implicit
