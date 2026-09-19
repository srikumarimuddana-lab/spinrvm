# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-19 |
| Author | Claude (spinr-migration-reviewer WARNING findings, applied same session) |
| Surface(s) | backend (ops scripts, not request-path routes) |
| Domain (Sentry tag) | rides |
| PR / commit link | srikumarimuddana-lab/spinrvm#5508 |
| Related issue or gap ID | Two WARNINGS from spinr-migration-reviewer's legacy-backfill audit (companion to the BLOCKER fixed in `86cfbdd`), explicitly held open pending a decision — user asked to fix both |

## 1. Issue / gap identified

Two consistency/coverage gaps in the legacy-backfill script family, found alongside the `86cfbdd` blocker:

1. `backfill_imported_ride_routes.py` and `backfill_imported_ride_snapshots.py` defaulted to **apply mode** (`--dry-run` was opt-in) — the only two scripts in the audited 37-file set that broke from this suite's otherwise-universal "dry run is the default, `--apply` is opt-in" convention (e.g. `backfill_legacy_driver_sin_dob.py`, `backfill_statement_totals.py`).
2. `backfill_imported_ride_routes.py` (limit=500) and `backfill_period_distances.py` (limit=10000) capped their Supabase read with no pagination or "more rows remain" signal, unlike `backfill_statement_totals.py`/`backfill_stripe_customer_emails.py`, which already page correctly.

## 2. Root cause

These two scripts were written before (or independent of) the dry-run-by-default / paged-read conventions the rest of the family later standardized on, and nothing enforced consistency across the set until `spinr-migration-reviewer`'s scope was widened today to cover this whole file class.

## 3. Fix / remediation

- Flipped both scripts' CLI to `--apply` (default: dry run), matching the rest of the family. `backfill_imported_ride_snapshots.py` already had `dry_run`/`force`/`limit` params; only the CLI flag and default changed.
- Added a paged reader (`_PAGE_SIZE = 500`) with a `has_more` return value to `backfill_imported_ride_routes.py` (new `_fetch_imported_rides`) and `backfill_imported_ride_snapshots.py` (new `_fetch_rides_needing_snapshots`), each logging a warning when the read was truncated by a `--limit` (routes/snapshots scripts) or the safety ceiling (`backfill_period_distances.py`, which has no user-facing `--limit` — its `_ROW_LIMIT = 10000` is now a paged ceiling with the same has_more warning instead of a single capped read).
- Added tests for all three behavior changes across three files (one new test file for `backfill_imported_ride_snapshots.py`, which had zero prior coverage; extended the other two's existing suites).

## 4. Risk & impact on existing functionality

- Blast radius: **isolated** — three standalone, manually-invoked ops scripts. Grepped for other callers/importers of each: none found (all three are CLI-only entry points per their own docstrings).
- **Behavior change an operator must know about**: anyone with a saved/muscle-memory invocation of `backfill_imported_ride_routes.py` or `backfill_imported_ride_snapshots.py` with **no flags** will now get a dry run instead of a live write — this is the intended fix (matching every other script's safety convention), but it is a real behavior change to how a bare invocation behaves. No automated caller exists (confirmed above), so this only affects a human running the script directly, and the dry-run output tells them exactly what to do (`--apply`).
- `backfill_period_distances.py`'s change is additive-only from a call-site perspective — same `_main(apply_changes, before)` signature, only the internal read is now paged; no CLI flag changed for this script.
- No interaction with the ride state machine, dispatch, money/wallet deltas, or insurance-period writes (`backfill_period_distances.py` writes `driver_period_distances`, an append-only regulatory audit table, only via its already-replay-safe `record_ride_period_distances()` — unchanged by this fix).

## 5. User-experience effect

None — none of these three scripts are reachable by any rider/driver/admin request path; they are operator-invoked CLI tools only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/scripts/backfill_imported_ride_routes.py` | `--dry-run` → `--apply` (default: dry run); added paged `_fetch_imported_rides` + `has_more` warning; added `--limit` | Match family convention; stop silent under-coverage beyond the 500-row cap |
| `backend/scripts/backfill_imported_ride_snapshots.py` | `--dry-run` → `--apply` (default: dry run); added paged `_fetch_rides_needing_snapshots` + `has_more` warning | Same as above |
| `backend/scripts/backfill_period_distances.py` | Added paged `_fetch_completed_rides` + `has_more` warning when the 10000-row safety ceiling is hit | Stop silent under-coverage beyond the ceiling |
| `backend/tests/test_backfill_imported_ride_routes.py` | Added 3 tests: dry-run-default, apply-flag mapping, pagination-warning | Regression coverage for both fixes |
| `backend/tests/test_backfill_imported_ride_snapshots.py` (new) | 4 tests: dry-run-default (with render/upload mocked so a false-pass can't hide behind an unrelated exception), apply-flag mapping, pagination-warning, exhaustion-vs-truncation | No test file existed for this script before |
| `backend/tests/test_backfill_period_distances.py` | Added 1 test: hitting the row ceiling logs a warning, not a silent truncation | Regression coverage for the pagination fix |

## 7. Before / after

```python
# Before (backfill_imported_ride_routes.py)
parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
args = parser.parse_args()
asyncio.run(main(dry_run=args.dry_run))
# main(dry_run: bool = False) -- bare invocation writes
```

```python
# After
parser.add_argument("--apply", action="store_true", help="write to the DB (default: dry run)")
parser.add_argument("--limit", type=int, default=None, help="cap the rides considered (default: all)")
args = parser.parse_args()
asyncio.run(main(dry_run=not args.apply, limit=args.limit))
# main(dry_run: bool = True, limit=None) -- bare invocation is a dry run
```

## 8. Rollback plan

Plain `git revert` — all three scripts are manually-invoked CLI tools with no automatic trigger; reverting restores the previous CLI flags/defaults with no data-level cleanup needed (no data was written by this change itself).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_backfill_imported_ride_routes.py backend/tests/test_backfill_imported_ride_snapshots.py backend/tests/test_backfill_period_distances.py` (15 passed)
- [x] Confirmed each new/changed test fails against the pre-fix code and passes against the fix (`git stash` each script individually, re-run its tests) — including catching a false-positive in the first draft of the snapshots dry-run test (an unmocked render/upload exception was masking the actual apply-mode bug) and correcting it before relying on it
- [x] `ruff check` clean on all six changed/added files
- [x] Blast-radius grep performed: no other callers/importers of any of the three scripts
- [ ] Manual repro steps followed in staging — **not verified against real OSRM/Google Maps/Supabase**; mocked unit tests only, consistent with these being manually-invoked, infrequently-run ops tools rather than live-traffic paths

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no automatic caller, no data written by this change)
- [x] Blast radius is stated: isolated to three standalone ops scripts, no automated callers
- [x] No silent behavior change to an already-shipped *request-path* flow — the one real behavior change (bare invocation defaults to dry-run) is exactly the intended fix and is scoped to a manually-invoked CLI tool, documented above
