# Change Impact & Risk Log — batch-offer losers get their real insurance period, not a blanket Period 1

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (2026-09-20 review, Phase 1 item 12) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety / drivers |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, 🛠️ "Insurance-period guard inconsistency" |

## 1. Issue / gap identified

When a driver accepts a batch-dispatched ride, `accept_ride`'s cleanup releases every other
driver who had a pending offer. That release recorded **Period 1 unconditionally** for each
loser (`routes/drivers/ride_flow.py`, `_release_loser`) — including a driver who had gone
offline between the offer going out and losing the race.

Period 1 is "app on, available, no assigned ride → TNC contingent liability". Writing it for an
offline driver asserts commercial coverage over someone who is on personal auto only, in an
**append-only, regulator-facing** table (`driver_insurance_periods`, SGI audit artefact, 7-year
retention). Nothing closes such a row: the driver is already offline, so no later go-offline
transition fires to end it.

## 2. Root cause

`repositories/driver_repo.py:172-173` enforces the `is_available ⇒ is_online` invariant by
clamping: `set_driver_available(id, True)` writes `is_available = False` when the driver's
current `is_online` is false. The release therefore already *knew* the driver was offline — it
returns the updated row — but `_release_loser` discarded the return value and recorded Period 1
from the intent rather than the outcome.

The two sibling release paths were written with this in mind and read the returned row
(`routes/rides/cancellation.py:459-469`, `routes/rides/matching.py:1879-1885`, each with a
comment explaining why). `_release_loser` is the one call site in the family that never got it.

## 3. Fix / remediation

`_release_loser` now records the period the returned row actually describes:

| Release result | Recorded | Meaning |
|---|---|---|
| dict, `is_available` truthy | Period 1 | online, free — clamp did not fire |
| dict, `is_available` falsy | **Period 0** | clamp fired ⇒ `is_online` false ⇒ personal auto |
| `None` / not a dict | nothing | unknown; do not guess at a regulatory row |

The Period 0 branch is the half the siblings do not have, and it is load-bearing rather than
cosmetic. A batch-offer loser holds an **open Period 2**, opened at claim time
(`matching.py:1272`, one per claimed driver — the batch model has no separate `driver_assigned`
write). Simply *skipping* the Period 1 write — the minimal reading of "guard the Period 1" —
would close nothing and leave that Period 2 open: a claim of full primary commercial coverage,
which is a worse misstatement than the Period 1 it replaces. Recording Period 0 closes it and
states the truth.

The `None` branch stays silent deliberately: `set_driver_available` returns `None` both when no
Supabase client is configured (`_write_skipped`) and when the update matched no row. Neither
Period 0 nor Period 1 is known to be true there, and `derive_insurance_period`'s own rule — never
degrade *upward* into a higher period, never invent coverage — argues against guessing in either
direction.

**Alternatives considered:**
- *Guard the Period 1 write only, exactly mirroring the two siblings.* Rejected: consistency with
  the siblings is not worth trading a wrong-but-closed row for a correct-but-open Period 2. The
  siblings' gap is written up in §4 instead.
- *Derive the period via `derive_insurance_period(...)` instead of reading the clamp.* Rejected:
  it would need a second read of the driver row plus a ride lookup on a path already inside the
  <2 s dispatch SLA, to answer a question the release response already answers.

## 4. Risk & impact on existing functionality

**Blast radius — every caller of `record_period_transition` on a release path**, grepped:

| Call site | Period | Touched? |
|---|---|---|
| `routes/rides/matching.py:1272` | 2 (claim/offer) | no — this is what we now close |
| `routes/rides/matching.py:1657/1671` | 0 / 1 (single-offer timeout) | no |
| `routes/rides/matching.py:1875/1884` | 0 / 1 (`process_expired_offer`) | no — see below |
| `routes/rides/cancellation.py:469` | 1 (rider cancel) | no — see below |
| `routes/drivers/ride_flow.py:404` | 2 (accept, no-op safety net) | no |
| `routes/drivers/ride_flow.py:477` (`_release_loser`) | **1 → 1 or 0** | **this change** |
| `routes/drivers/ride_flow.py:673` (`decline_ride`) | **1 → 1 or 0** | **this change** |
| `routes/drivers/ride_flow.py:1094/1168` | 3 / 3 | no |
| `routes/drivers/ride_cancel.py:228/684`, `ride_complete.py:841` | 1 | no |
| `routes/drivers/status.py:854/863/865`, `profile.py:306`, `subscriptions.py:1811` | derived / 0 | no |

Nothing else reads `_release_loser`'s result; it returns `None` and its only other effect (the
`ride_taken` WS message) is unchanged and still inside its own `try`.

**The same latent gap exists in the two remaining siblings, and is not live today.**
`cancellation.py:469` and `process_expired_offer` skip the Period 1 write for an offline driver
without recording Period 0, so they could in principle leave a claim-time Period 2 open. They do
not today: with `insurance_period_live_offer_enabled` **off**, a driver toggling offline goes
through `status.py:865`'s `record_period_transition(driver_id, 0)`, which closes the Period 2 at
the moment they go offline — so by the time either sibling runs, the open period is already 0 and
writing nothing is correct.

The gap opens if that flag is turned on, because `derive_insurance_period(is_online=False,
has_live_offer=True)` returns **2** (`utils/insurance_periods.py:137-140`), deliberately keeping
the commercial window open for an offline driver who is still under offer — that is the intended
fix for a different bug (migration 421), not an oversight. In that world nothing else closes the
row: `utils/insurance_period_reconciler.py` only sweeps drivers with a *pending* offer
(`_pending_offer_candidates`) or `is_online = true` (`_online_idle_candidates`), and an offline
driver whose offer has already flipped to `preempted`/`expired` matches neither;
`stale_p3_closer.py` handles Period 3 only. The row would then stay open until that driver next
comes online, or forever if they never do.

Recorded here as a follow-up rather than fixed in this diff: those two are separate files with
their own tests, which would put this change well past CLAUDE.md's ≤3-files-per-subtask rule, and
the flag is not merely off but currently **unreachable** (§10). They are the next subtask, not a
someone-else problem.

**Volume.** One extra RPC per *offline* loser. Losers who went offline mid-offer are rare, and
`record_insurance_period_transition` returns `noop` (counted as
`spinr_insurance_period_noop_total`) when the period is already open, so the common already-
Period-0 case costs one round-trip and writes no row. Online losers — the overwhelming majority —
see exactly the call count they saw before.

**Failure mode is unchanged.** `record_period_transition` is compliance-grade: it logs at ERROR
and swallows, never raising into dispatch. The whole cleanup block is additionally wrapped in
`try/except` at `ride_flow.py`. Acceptance cannot be blocked by this change.

## 5. User-experience effect

None. No rider, driver, corporate-admin or internal-admin surface reads `driver_insurance_periods`
in an interactive flow; it is a regulatory audit artefact. No copy, no API response, and no
screen changes. A driver already mid-session sees no difference — the WS `ride_taken` message and
the availability release are byte-identical.

The change is visible only to an SGI/regulatory audit of period rows, where it is a correction.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/ride_flow.py` | `_release_loser` **and** `decline_ride` record Period 1 / Period 0 / nothing from the release result instead of Period 1 unconditionally | an offline loser/decliner was getting a commercial-coverage row nothing would close |
| `backend/tests/test_driver_ride_flow_coverage.py` | `_accept_with_loser` + `_decline_with_release` helpers, 7 tests (each path: online→1, offline→not 1, offline→0, unknown→neither) | pin all three branches at both call sites, including the Period 2 close |
| `docs/change-log/2026-09-20-release-loser-period-guard.md` | this file | |

## 7. Before / after

```python
# Before
async def _release_loser(lid: str) -> None:
    await db_supabase.set_driver_available(lid, True)
    await _deps.record_period_transition(lid, 1)
```

```python
# After
async def _release_loser(lid: str) -> None:
    released = await db_supabase.set_driver_available(lid, True)
    if isinstance(released, dict):
        await _deps.record_period_transition(lid, 1 if released.get("is_available") else 0)
```

**Concrete scenario.** Three drivers are offered ride `R`; all three get a Period 2 row at claim
time. Driver B closes the app and toggles offline at t+4 s (Period 2 closed, Period 0 opened).
Driver A accepts at t+6 s.

- **Before:** B's release clamps `is_available→False` (they are offline), and B is nonetheless
  recorded as Period 1 — an open TNC contingent-liability window for a driver on personal auto,
  which stays open until B next comes online. If B never returns, it stays open permanently.
- **After:** B is recorded as Period 0. The RPC sees Period 0 already open and returns `noop`, so
  no duplicate row is written and `spinr_insurance_period_noop_total{period="0"}` increments.
  B's audit trail correctly shows one Period 2 (t+0 → t+4) and one Period 0 (t+4 → open).

Driver C, still online, is recorded Period 1 exactly as before.

## 8. Rollback plan

Code-only: no migration, no flag, no schema or settings change. `git revert` + deploy restores the
previous behaviour exactly.

Rows already written under the new behaviour need no cleanup and must not get any:
`driver_insurance_periods` is append-only by rule, and every row this change writes is a *more*
accurate classification than the one it replaced. Reverting stops new correct rows from being
written; it does not invalidate the ones already there.

There is no live-data hazard to roll back from — no money moves, no ride state changes, and the
driver's availability write is untouched.

## 9. Verification performed

- `ruff check` / `ruff format --check` / `py_compile` clean on both changed files.
- Read `set_driver_available` (`repositories/driver_repo.py:145-197`) end to end to confirm the
  three return shapes the new branches key on: the clamp at `:172-173`, `_single_row_from_res` at
  `:190` (row or `None`), and the `_write_skipped` early `return None` at `:147-148`.
- Read `record_period_transition` (`utils/insurance_periods.py:144-241`) to confirm it closes the
  currently-open period before opening the new one, returns `noop` when the requested period is
  already open, and swallows every failure at ERROR — i.e. that the Period 0 branch is both
  self-healing and incapable of blocking acceptance.
- Read all sibling guards (`cancellation.py:459-469`, `matching.py:1879-1885`, and
  `decline_ride` in this same file) and confirmed the new Period 1 branch is equivalent to theirs;
  the divergence is only the added Period 0 branch, justified in §3.
- **`spinr-insurance-period-auditor` was run against the diff** per CLAUDE.md's pre-commit gate. It
  independently reached §3's conclusion — that skipping the write leaves the claim-time Period 2
  open and is worse than the mislabel it replaces — and contributed two findings folded into this
  change: `decline_ride` is a fourth call site in this same family (its comment asserted "their
  go-offline already logged Period 0" as unconditional fact, which holds only while the flag is
  off), and `insurance_period_reconciler.py` provides no safety net for the dangling row (§4). It
  also confirmed the RPC in migration 421 closes whatever period is open with no period filter,
  which is what makes the Period 0 branch a valid close.
- Traced the flag interaction in §4 through `status.py:815-865` and
  `utils/insurance_periods.derive_insurance_period` to establish the sibling gap is latent, not
  live.
- Test wiring checked against the existing, already-passing
  `test_batch_offer_winner_and_losers_resolved`, which uses the same re-patch-after-
  `_base_success_patches` pattern; the assertions filter on `"loser-1"`, which cannot collide with
  the winner's `_DRIVER_ID = "drv-cov-1"`.

## 10. What was NOT verified

- **pytest was not run** — this sandbox cannot reach PyPI, so the suite is unavailable locally. CI
  is the gate for `tests/test_driver_ride_flow_coverage.py` and the rest of
  `tests/test_insurance_period*.py`. The four new tests are reasoned about and statically checked,
  not observed passing.
- **Not exercised against a real Supabase.** That `set_driver_available`'s returned row carries
  `is_available` post-clamp was read from the repository code and the PostgREST `update` semantics,
  not observed on a live row. If PostgREST were ever configured to return no representation on
  update, the result would be `None` and every loser would fall into the silent branch — a
  regression in coverage, not a false record. Worth one manual check on staging.
- **No production reconciliation of existing bad rows.** Period 1 rows already written for offline
  losers are still in `driver_insurance_periods` and are still open. Sizing that cohort and
  deciding what to do about it (append a corrective Period 0, or annotate) is a compliance call,
  not made here. A read-only query would be `SELECT driver_id, started_at FROM
  driver_insurance_periods p JOIN drivers d ON d.id = p.driver_id WHERE p.period = 1 AND
  p.ended_at IS NULL AND d.is_online = false`.
- **The sibling Period 0 gap in `cancellation.py` / `process_expired_offer` is documented, not
  fixed** (§4). It is unreachable while `insurance_period_live_offer_enabled` is off, and is the
  next subtask.
- **A shared latent gap in all four guards is NOT addressed:** when `set_driver_available` returns
  `None` because the update matched zero rows (stale or deleted driver row) for a driver who is in
  fact still online, no period is recorded at all. This predates the change and is identical at
  every call site, but it means "unknown" and "offline" are indistinguishable from a missing row.
  Making that case loud (it is a DB anomaly on a regulatory path, so arguably `logger.error`) was
  left out to keep this diff to the one behaviour it is about.
- **Separately observed, not addressed here:** `insurance_period_live_offer_enabled` appears to be
  unreachable entirely — it is declared on `AppSettings` (`backend/schemas.py:326`) but is on
  neither `SettingsUpdateRequest` nor any `ALTER TABLE settings ADD COLUMN` migration, so
  `get_app_settings()` can never return it true and the 2026-09-20 insurance-period-derivation fix
  it gates is permanently dark. Not touched by this change; flagged for the owner of that change.
- No visual/snapshot regression tooling is relevant — this is backend-only, no UI surface.
