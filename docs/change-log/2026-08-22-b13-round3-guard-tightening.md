# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | Claude Code session (vikas@ngitservices.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR for this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B13 (Round 3 — closes the item) |

## 1. Issue / gap identified

`routes/admin/sgi_forms.py`'s `_out_of_scope_drivers()` (the Alberta-expansion
segregation guard that blocks generating an SGI D00032/D00033 form for a
driver regulated by a non-SGI authority) treated a NULL/missing
`regulatory_authority` as **in-scope** (not blocked) — a deliberate
grandfather allowance for legacy rows that predated the field's backfill.
B13's own text already flagged this as a real hole once every driver has a
populated field: a NULL row post-backfill can no longer mean "legitimate
legacy row," only "the write-path fix regressed" — and a regressed write
path is exactly the failure mode the guard exists to catch.

## 2. Root cause

Not a bug in the original guard — a deliberately incomplete rollout, staged
in two rounds already tracked in B13: Round 1 backfilled 22 legacy NULL
rows (migration 265); Round 2 found and fixed the actual write-path bug (4
driver-creation code paths never set the field) and backfilled the 7 rows
it had already produced (migration 333), but explicitly deferred the guard
tightening itself until 333 was confirmed applied — doing it first would
have blocked real drivers whose rows were still NULL. This pass verified
333 is applied (see below) and completed the deferred tightening.

## 3. Fix / remediation

- `_out_of_scope_drivers()` now returns a driver row whenever
  `regulatory_authority != "SGI"` — this includes `None`/missing, not just
  an explicit non-SGI value.
- The three call sites' `authorities = sorted({d["regulatory_authority"] for d in out_of_scope})`
  error-message construction was patched to
  `d.get("regulatory_authority") or "unspecified"` — defensive, since a NULL
  row is now reachable in `out_of_scope` and the old expression would either
  raise (`sorted()` comparing `None` to `str`) or render the literal string
  `"None"` in a 422 detail shown to an admin.
- Verified against live production (`soavhtdhefowwvforzwb`) before making
  any change: `schema_migrations` already has `333_drivers_regulatory_authority_backfill_round2.sql`
  applied, and `SELECT count(*) FILTER (WHERE regulatory_authority IS NULL) FROM drivers`
  returns `0` of `212` total, all `SGI`/`SK` — confirming the precondition
  B13 set for this tightening (ACTION_ITEMS.md's own "not yet applied" note
  was stale, same drift class B8 already documented).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `_out_of_scope_drivers()` has exactly 3
  callers, all in `routes/admin/sgi_forms.py` itself (verified via grep) —
  the D00032/D00033 form-generation endpoint, and the two other endpoints
  in the same file that reuse the same guard (removal-queue-adjacent
  checks). No other route, service, or background loop reads this function
  or replicates its logic.
- **What could regress:** an admin trying to generate an SGI form for a
  driver whose `regulatory_authority` is somehow NULL again in the future
  (e.g. a new write path is added later and misses `_resolve_regulatory_defaults()`)
  would now get a 422 instead of being silently allowed through — this is
  the intended new behavior, not a regression, but it is a genuine behavior
  change for that hypothetical row. Given production is confirmed 100%
  non-NULL right now, this has zero effect on any row that exists today.
- **Test fixtures:** both `test_sgi_forms_route.py` and
  `test_admin_sgi_forms_coverage.py` share a base `_DRIVER_ROW` fixture that
  never set `regulatory_authority` — 20 unrelated tests in those files
  (form generation success paths, removal-stamp tests, document-bundle
  tests) started failing 422 once the guard tightened, because their
  fixture driver was implicitly relying on the NULL-passes grandfather.
  Fixed by adding `"regulatory_authority": "SGI"` to the shared fixture
  (matching production reality — every real driver is SGI/SK today), not
  by loosening the guard back — this was the actual size of the change.
- No interaction with the ride state machine, money/wallet deltas, or any
  background loop.

## 5. User-experience effect

Internal-admin-only (SGI compliance-form generation is an admin-dashboard
feature, not rider/driver-facing). No mid-session visibility concern — this
is a synchronous admin action (click "Generate form"), not a state an admin
could be sitting inside when the flag flips. No copy/notification change;
the existing 422 error message format is unchanged except for the
`"unspecified"` fallback label on the (currently unreachable in production)
NULL case.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/sgi_forms.py` | `_out_of_scope_drivers()` now blocks NULL too; 3 call sites' `authorities` construction defensive against `None` | Complete B13's deferred guard tightening now that the backfill precondition is confirmed |
| `backend/tests/test_admin_sgi_forms_coverage.py` | 2 unit tests flipped from NULL-passes to NULL-blocked; 1 new HTTP-level test for the `"unspecified"` message; shared fixture given explicit `regulatory_authority: "SGI"` | Pin the new behavior; keep unrelated tests passing |
| `backend/tests/test_sgi_forms_route.py` | Class docstring updated; `test_null_regulatory_authority_grandfathered_through` replaced with `test_null_regulatory_authority_now_blocked`; shared fixture given explicit `regulatory_authority: "SGI"` | Same |
| `ACTION_ITEMS.md` | B13 marked `[x]` CLOSED (Round 3) | Backlog hygiene |

## 7. Before / after

```python
# Before
def _out_of_scope_drivers(driver_rows: list[dict]) -> list[dict]:
    """... A NULL/missing `regulatory_authority` is treated as in-scope
    (not blocked) — 22 of 209 real drivers ... predate this field being
    backfilled ..."""
    return [d for d in driver_rows if d.get("regulatory_authority") and d["regulatory_authority"] != _SGI_AUTHORITY]
```

```python
# After
def _out_of_scope_drivers(driver_rows: list[dict]) -> list[dict]:
    """Drivers whose `regulatory_authority` is not exactly `_SGI_AUTHORITY`
    — this now includes NULL/missing. ... The former NULL-passes grandfather
    allowance is retired ..."""
    return [d for d in driver_rows if d.get("regulatory_authority") != _SGI_AUTHORITY]
```

```python
# Before (3 call sites)
authorities = sorted({d["regulatory_authority"] for d in out_of_scope})
```

```python
# After
authorities = sorted({d.get("regulatory_authority") or "unspecified" for d in out_of_scope})
```

## 8. Rollback plan

**`git-revert-safe`** — pure application-code change (guard logic + error
message + tests), no migration, no data write. A plain `git revert` fully
restores the old NULL-passes behavior with no data cleanup needed, since
this pass wrote no data (migration 333's data change was already applied by
an earlier session; this pass only verified it and changed code that reads
the resulting state).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_admin_sgi_forms_coverage.py backend/tests/test_sgi_forms_route.py -q --no-cov` → **52 passed**, 0 failed.
- [ ] Manual repro steps followed in staging — not performed; no staging admin session available this pass, relied on the automated HTTP-level test suite (`test_client`/`TestClient`) instead.
- [x] Blast-radius grep performed: `grep -rn "_out_of_scope_drivers" backend/` — exactly 3 callers, all in `sgi_forms.py`, all covered by this change.
- [x] Reviewed against relevant CLAUDE.md convention(s): this is an additive tightening of an existing admin-only guard, not a state-machine/money/RLS change — no dual-import, Decimal, or ride-state-guard convention applies here.
- [ ] Feature-flagged — not flagged. Justification: this is a compliance/safety guard tightening (closing a hole, not adding new user-visible behavior), scoped to a single internal-admin endpoint, with a confirmed-zero-impact production precondition (0 NULL rows). CLAUDE.md's flag guidance is for user-visible/non-trivial changes to a shared surface; this doesn't meet that bar, and per B13's own history, flagging a safety guard's tightening (making it optionally skippable) would undercut the point of the guard.

## What was NOT verified

- Not verified against a real staging admin session — only the automated `TestClient` HTTP-level suite.
- `services/data_transfer/entity_import_service.py`'s cross-environment driver-import path (copies whatever `regulatory_authority` the source entity had, including possibly `None`) was not touched or audited further — it's a different write path from the four B13 Round 2 already fixed, and out of scope for what this item asked for. If that import path is ever used against a live Alberta-adjacent dataset, it could still produce a NULL row that the tightened guard would now correctly block (not silently pass) — flagged here rather than assumed covered.
- No new NULL row exists in production to manually exercise the blocked path end-to-end against a real generate-form click; correctness is established via the unit/HTTP test suite's synthetic NULL rows, not a live repro.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data cleanup)
- [x] Blast radius is stated, not assumed (3 callers, 1 file, grep-verified)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — internal-admin-only, filled in above
