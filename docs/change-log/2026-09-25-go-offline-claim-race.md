# Change Impact & Risk Log — v1 go-offline could land on top of a dispatch claim

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | follow-up to the Codex review of #5775 |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/inspiring-ritchie-9ginf3-go-offline-cas` |
| Related issue or gap ID | Codex P1 on #5775: "Make the forced-offline transition atomic with dispatch" |

## 1. Issue / gap identified

On the default availability path (`driver_availability_v2_enabled = false`), a driver going offline at the same moment dispatch claims them could end up offline with a live offer, and with a Period 0 insurance row while obligated to a ride.

## 2. Root cause

`PUT /drivers/{id}/status` (`routes/drivers/status.py`) checks for an active ride and a fresh pending `ride_offers` row. It then writes `is_online=false, is_available=false` unconditionally several hundred lines later. `claim_driver_atomic` flips `is_available` true → false and stamps `availability_claimed_at` *before* the `ride_offers` row is inserted. A claim that lands between the checks and the write was therefore overwritten. The v2 path does the same transition in one RPC (`OBLIGATION_ACTIVE`) and is not affected.

This predates #5775. Every go-offline hits it, the manual toggle included. #5775's flagged forced-offline adds more calls to the same endpoint.

## 3. Fix / remediation

- **Compare-and-set.** The online → offline write filters on the `is_available` value the checks saw (`{"id": driver_id, "is_available": <pre-read value>}`). `claim_driver_atomic` conditions on the same column of the same row (`is_available = true`), so only one of the two writes can win:
  - Offline first: the claim finds `is_available=false` and does not claim.
  - Claim first: the offline write matches zero rows.
- **Detection.** The handler already re-reads the row after writing. If the driver is still online and `is_available` changed, it returns **409** ("A ride offer just arrived…") before any insurance-period write. That case previously fell through to the "silent no-op" 500.
- **Claim in flight.** If the pre-read row already shows `is_available=false` with an `availability_claimed_at` younger than 30 s and no offer row yet, the request returns **409** up front. Release clears the stamp. An older unreleased stamp is an orphan for the claim reaper and does not block.
- Go-online writes and offline re-asserts (driver already offline) keep the plain `{"id"}` filter.

Alternatives considered:
- **Turn on `driver_availability_v2_enabled` in production.** This is already atomic, but it is a whole-protocol rollout with its own client and epoch contract, and not a fix for the default path.
- **A new Postgres RPC for v1 go-offline.** Stronger, but a migration plus a second implementation of what v2 already does.

The row-level compare-and-set reuses the claim's own guard column and needs no schema change.

## 4. Risk & impact on existing functionality

- Blast radius: single-surface (backend, `update_driver_status` v1 go-offline branch only). v2 path, go-online, admin force-offline and logout-all are unchanged. Those use other writers; grep `update_one("drivers"` for them.
- New 409s:
  - (a) A claim won the race. The driver now holds an offer and must accept or decline it, which is correct.
  - (b) A claim is in flight, for at most 30 s.
  - (c) Rare false positive: some other writer changed `is_available` in the milliseconds between the pre-read and the write, for example a release. The driver taps again and it succeeds.
- Driver app: the toggle already maps 409 to "Cannot go offline — Decline the offer or finish the trip first" using the server's reason, and #5775's forced-offline reports it to Sentry and retries on the next resume.
- Stuck-online risk: a driver with `is_available=false` and a fresh unreleased claim stamp but no offer is blocked for up to 30 s. After that the stamp counts as an orphan.
- Insurance periods: both 409 paths return before `record_period_transition`, so there is no Period 0 row for an obligated driver. The normal go-offline path records Period 0 as before.
- Readers of the same columns: the claim reaper (`utils/driver_claim_reaper.py`), dispatch candidate queries, and `set_driver_available`. None changed.

## 5. User-experience effect

- Drivers: in the rare race, Go offline now fails with "A ride offer just arrived. Please accept or decline it before going offline." (or "…is arriving…"), and the offer is shown. Previously the driver went offline while an offer was live. No other visible change.
- Visible mid-session only when the race happens.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/status.py` | `_claim_in_flight` pre-check; conditional offline write; 409 on a lost write | Close the claim/offline race |
| `backend/tests/test_go_offline_claim_race.py` | 6 tests: conditional filter, lost-write 409 with no period write, re-assert filter, in-flight 409, orphan stamp, stale stamp on an available driver | Regression |

## 7. Before / after

```python
# Before: unconditional
await db_supabase.update_one("drivers", {"id": driver_id}, _payload)

# After: online -> offline only if the claim column is unchanged
_write_filters = {"id": driver_id}
if _offline_flip:
    _write_filters["is_available"] = driver.get("is_available")
await db_supabase.update_one("drivers", _write_filters, _payload)
...
if _offline_flip and verify.get("is_online") and verify.get("is_available") != driver.get("is_available"):
    raise HTTPException(409, "A ride offer just arrived. …")
```

Scenario, before:
1. The driver taps Go offline; the checks find no offer.
2. Dispatch claims the driver: `is_available` becomes false and the offer is inserted and pushed.
3. The unconditional write sets `is_online=false`, and Period 0 is recorded.
4. The offer is live on an offline driver, and acceptance fails or times out.

After: step 3 matches zero rows, the verify re-read sees the driver online with `is_available=false`, and the endpoint returns 409 with no period write. The driver sees the offer.

## 8. Rollback plan

Backend-only, no schema or data change. Revert the commit and redeploy. There is no flag, because the only new behavior is rejecting a go-offline that would otherwise corrupt dispatch and insurance state. Nothing is written that a rollback would need to undo.

## 9. Verification performed

- `status.py` and the new test file parse, and the lines fit the 120-character limit.
- `spinr-dispatch-reviewer` and `spinr-insurance-period-auditor` were run on the commit (results in the PR).
- **Not run:** pytest and ruff. This session's network policy blocked PyPI, so backend dependencies could not be installed. CI's `backend-test` is the first run.

## 10. What was NOT verified

- No run against real Postgres/Supabase. Row-level atomicity of the two conditional `UPDATE`s is Postgres behavior (READ COMMITTED re-checks the `WHERE` on the locked row), reasoned about rather than exercised.
- No load or concurrency test of the race itself. The mocked tests exercise each branch, not true interleaving.
- The driver-app copy for these 409s is the existing toggle handler's; it was not checked on a device.
