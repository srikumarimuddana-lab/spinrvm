# Change Impact & Risk Log — one definition of "release a driver and close their Period 2"

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code session (2026-09-20 review; architecture-review follow-up) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety / drivers |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-21 architecture review, concern #5 |

## 1. Issue / gap identified

The release-and-close-period pattern — `set_driver_available(id, True)` → inspect the returned row
→ `record_period_transition` — is hand-mirrored at **five** call sites:

| # | Site | Behaviour before this change |
|---|---|---|
| 1 | `routes/drivers/ride_flow.py` `_release_loser` | Period 1 or 0 |
| 2 | `routes/drivers/ride_flow.py` `decline_ride` | Period 1 or 0 |
| 3 | `routes/rides/cancellation.py:459-469` | Period 1 or nothing |
| 4 | `routes/rides/matching.py` `_offer_timeout_handler` | Period 1 or nothing |
| 5 | `routes/rides/matching.py` `process_expired_offer` | Period 1 or nothing |

Five copies of three lines, each wrapped in a 6–12 line comment re-deriving the same reasoning and
cross-referencing the others — and already drifted into **two behaviours**, a divergence the
previous change (`2026-09-20-release-loser-period-guard.md` §4) documented in a comment and
deferred rather than removed.

That is precisely the failure mode CLAUDE.md names: the 2026-09-12 ride-experience audit's clearest
finding was a fix proven correct in one app that never reached the sibling the other app served.
Documenting a divergence is not mitigating it.

Both new sites also hand-computed a regulatory classification inline (`1 if released.get(
"is_available") else 0`) while `derive_insurance_period` — documented as "Single source of truth
for CLAUDE.md's Period 0-3 table", pure, keyword-only and already tested — sat unused two modules
away.

## 2. Root cause

No shared definition ever existed. Each site grew its own copy as its surrounding flow was written,
and the reasoning is subtle enough (why not blanket Period 1; why silence is worse, not safer) that
each copy carries a long comment instead of a function call. Once the reasoning lives in comments
rather than code, the next fix updates the comment it is standing in front of and nothing else.

## 3. Fix / remediation

`release_driver_and_close_period(driver_id, *, reason, ride_id=None) -> int | None` in
`backend/utils/insurance_periods.py`, next to `record_period_transition`, which already owns period
semantics and the noop/race handling.

Contract:

- **`driver_id` positional, everything else keyword-only** — matching `derive_insurance_period`'s
  own discipline, which is keyword-only precisely because "a transposed positional argument would
  silently misstate SGI commercial coverage."
- **`reason` is a label, never a behaviour switch.** It reaches the log line and the
  `spinr_insurance_period_release_total{reason,period}` metric so the five sites stay
  distinguishable in Grafana, and nothing else. The moment it changes what gets written, five
  copies have been rebuilt inside one function — pinned by
  `test_every_reason_produces_the_same_period_for_the_same_state`.
- **`ride_id` is for the log line only** and is deliberately *not* written to the period row: a
  Period 0/1 row must not name a ride (only Period 2/3 may), so passing it through would produce a
  malformed audit record. Pinned by its own test.
- **Returns the recorded period** (0 or 1), or `None` when no row came back — so tests assert one
  layer, not two.
- **The 0-vs-1 choice is delegated to `derive_insurance_period`**, not re-derived. Pinned by a test
  that stubs `derive_insurance_period` to return 0 for an online driver and asserts the helper
  follows it.

This change swaps **only the two `ride_flow.py` sites**, which is behaviour-identical to what they
already did — a pure consolidation with no semantic change. The three remaining siblings are the
next subtask (§4), per CLAUDE.md's ≤3-files-per-subtask rule and because three of them sit on the
dispatch hot path.

**Alternative considered:** leave the five copies and fix only the behavioural divergence (add the
Period 0 branch to the three laggards). Rejected — it produces five *identical* copies, which is
the state that allowed this drift in the first place; the next change would face the same five-way
edit and the same chance of missing one.

## 4. Risk & impact on existing functionality

**Behaviour is unchanged at both swapped sites.** Verified by executing the helper directly against
the full matrix (§9): online → Period 1, offline → Period 0, no row → nothing, row missing
`is_online` → falls back to the clamp → Period 1. That is exactly what the inline code did.

**One deliberate internal difference:** the inline code keyed on `is_available`; the helper prefers
`is_online` (what the Period table actually keys on) and falls back to `is_available` when the row
omits it. These cannot disagree in practice — `set_driver_available` writes `is_available = is_online
AND requested`, so `is_available` truthy implies `is_online` truthy — and the fallback covers a
projection that omits the column.

**Blast radius:**
- `utils/insurance_periods.py` — additive only. `record_period_transition`,
  `derive_insurance_period` and every existing caller are untouched.
- `routes/drivers/_deps.py` and `routes/drivers/__init__.py` — mechanical re-export plumbing so
  `ride_flow.py` can reach the helper through the established `_deps` seam. Both import branches
  updated; verified symmetric by the repo's own AST detector (§9).
- **Three sibling sites still hold the old behaviour** (Period 1 or nothing). That gap is latent,
  not live, for the reason set out in `2026-09-20-release-loser-period-guard.md` §4: with
  `insurance_period_live_offer_enabled` off — and it is currently *unreachable*, being on neither
  `SettingsUpdateRequest` nor any migration — a driver going offline already closes their Period 2
  via `status.py`. Tracked as the immediate follow-up.
- No schema change, no migration, no flag, no new DB round trip (the helper makes the same two
  calls the inline code made).

**New metrics** (follow `spinr_<domain>_<metric>_<unit>`, counters end `_total`):
`spinr_insurance_period_release_total{reason,period}` and
`spinr_insurance_period_release_skipped_total{reason}`. Both are additive; no dashboard or alert
reads them yet.

**Error posture unchanged.** `record_period_transition` remains compliance-grade (logs at ERROR,
never raises), and at the loser site the whole cleanup block stays wrapped in its own `try` — pinned
by a new test that makes the helper raise and asserts acceptance still succeeds.

## 5. User-experience effect

None. `driver_insurance_periods` is a regulatory audit artefact with no interactive reader. No
rider, driver, corporate-admin or internal-admin surface changes; no copy, no API response, no
screen. A driver mid-session sees nothing different — the availability write and the `ride_taken`
WebSocket message are byte-identical.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/insurance_periods.py` | new `release_driver_and_close_period` + `RELEASE_REASONS` | the single definition |
| `backend/routes/drivers/ride_flow.py` | `_release_loser` and `decline_ride` delegate; ~35 lines of duplicated comment removed | the two sites being consolidated |
| `backend/routes/drivers/_deps.py` | re-export the helper in both import branches | the established seam `ride_flow` reaches deps through |
| `backend/routes/drivers/__init__.py` | re-export + `__all__` entry | package facade parity with `record_period_transition` |
| `backend/tests/test_insurance_release_helper.py` | new: 11 tests — period matrix, ride_id exclusion, reason-is-a-label, keyword-only, delegation to `derive_insurance_period` | the decision now has one home, so it is tested once, thoroughly |
| `backend/tests/test_driver_ride_flow_coverage.py` | the 7 period-value tests become 3 delegation tests | assert one layer, not two |
| `docs/change-log/2026-09-21-insurance-release-helper.md` | this file | |

Seven files, above CLAUDE.md's ≤3 guideline. Two are one-line re-export plumbing and two are
tests/docs; the grouping (helper + both `ride_flow` swaps together) is deliberate, because landing
the helper without its first callers would leave dead code, and splitting the two `ride_flow` sites
would reintroduce the divergence mid-branch.

## 7. Before / after

```python
# Before — routes/drivers/ride_flow.py, twice, under ~12 lines of comment each
released = await db_supabase.set_driver_available(lid, True)
if isinstance(released, dict):
    await _deps.record_period_transition(lid, 1 if released.get("is_available") else 0)
```

```python
# After
await _deps.release_driver_and_close_period(lid, reason="lost_race", ride_id=ride_id)
```

```python
# The helper's decision, in one place, delegating the classification
released = await db_supabase.set_driver_available(driver_id, True)
if not isinstance(released, dict):
    logger.error(...); _metric_inc("spinr_insurance_period_release_skipped_total", {"reason": reason})
    return None
_online = released.get("is_online")
if _online is None:
    _online = released.get("is_available")
period = derive_insurance_period(ride_status=None, is_online=bool(_online), has_live_offer=False)
await record_period_transition(driver_id, period)
```

**Scenario.** Three drivers are offered ride `R`; all three get a Period 2 at claim time. Driver B
goes offline at t+4s. Driver A accepts at t+6s. B's release clamps `is_available → False`, the
helper derives Period 0, and B's audit trail reads Period 2 (t+0 → t+4) then Period 0 (t+4 → open).
Driver C, still online, gets Period 1. Identical to the behaviour before this change — the
difference is that there is now one implementation of it instead of two, and three more sites to
bring onto it.

## 8. Rollback plan

Code-only: no migration, no flag, no schema or settings change, no data written that differs from
what the previous code wrote. `git revert` + deploy restores the two inline copies exactly.

Rows already written by the helper need no cleanup and must not get any — `driver_insurance_periods`
is append-only by rule, and every row it writes is the same row the previous code wrote for the same
driver state.

No live-data hazard: no money moves, no ride state changes, the driver availability write is
untouched.

## 9. Verification performed

- `ruff check` / `ruff format --check` / `py_compile` clean on all six changed Python files.
- **The helper's real logic was executed directly** against a stubbed DB layer (pytest is
  unavailable in this sandbox), covering every branch: online → `1`, offline → `0`, no row →
  `None` with nothing recorded and the skip metric emitted, row missing `is_online` → falls back to
  the clamp → `1`, unknown `reason` → `ValueError` **without touching the driver row**, positional
  `reason` → `TypeError`. The recorded tuples confirmed `ride_id` is never passed to
  `record_period_transition`.
- **Dual-import symmetry** re-checked with the repo's own AST detector across every backend module
  after the `_deps.py` edit (both branches now bind `release_driver_and_close_period`): 0
  asymmetric blocks.
- Read `set_driver_available` (`repositories/driver_repo.py:145-197`) again to confirm the three
  return shapes the helper branches on, and `derive_insurance_period`
  (`utils/insurance_periods.py:85-141`) to confirm `is_online=False, has_live_offer=False` yields 0
  and `is_online=True` yields 1.
- Confirmed no other caller of `record_period_transition` is affected: the helper is additive and
  the 11 other call sites are untouched.

## 10. What was NOT verified

- **pytest was not run.** CI is the gate for `test_insurance_release_helper.py` and
  `test_driver_ride_flow_coverage.py`. The helper's behaviour was executed directly; the *tests*
  themselves are reasoned about and statically checked, not observed passing. One test asserts a
  `TypeError` for a positional `reason`, which is a language guarantee rather than a code path, so
  it is the least load-bearing of the eleven.
- **Not exercised against a real Supabase.** That `set_driver_available`'s returned row carries
  `is_online` was read from the repository code and PostgREST's default return-representation
  behaviour, not observed on a live row. If it were ever configured `return=minimal`, the helper
  would receive `None` and correctly write nothing — a coverage regression, not a false record.
- **The three sibling sites are not converted** (§4) — the immediate follow-up, deliberately not in
  this diff.
- **The new metrics have no dashboard or alert.** They are emitted and named per convention;
  nothing consumes them yet.
- Backend-only, no UI surface, so admin-dashboard's Playwright visual baselines are not implicated.
