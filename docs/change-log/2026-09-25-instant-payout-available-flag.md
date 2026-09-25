# Change Impact & Risk Log — `instant_payout_available` follows the fail-closed area gate

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (session_01PPGd1wK6WRzbtxcGj3qNkX) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / drivers |
| PR / commit link | branch `claude/fix-instant-payout-available-flag`: `d49ed29` (helper extraction), `f6f8f97` (flag fix + tests). Not pushed. |
| Related issue or gap ID | ROADMAP N22; spinr-money-auditor SHOULD-FIX on #5791; follow-up named in `docs/change-log/2026-09-25-instant-payout-velocity-cap.md` |

## 1. Issue / gap identified

`GET /drivers/balance` returned `instant_payout_available: true` for a driver with no `service_area_id`, or one that matches no `service_areas` row. `POST /drivers/payouts/instant` refuses exactly those drivers with a 403 since #5791. So the flag could say "available" for a request that is then refused.

## 2. Root cause

The area rule existed twice. `payouts._require_instant_payout_enabled` (the gate) and `earnings.get_driver_balance` (the flag) each read `service_areas` on their own. #5791 made the gate fail closed on a missing or unresolvable area. The flag kept the old pass-through rule: it defaulted to `True` and only flipped to `False` on an explicit kill switch in a resolvable area. #5791 left `earnings.py` alone on purpose, because another PR (#5792) was editing that file at the same time.

## 3. Fix / remediation

- New helper `_shared._instant_payout_area_verdict(driver) -> (service_area_row, refusal)`. `refusal` is `None` when instant payout is allowed. Otherwise it is `"no_service_area"` (no id, or an id with no row) or `"disabled_in_area"` (the per-area kill switch from migration 314 is off). DB errors propagate to the caller.
- `payouts._require_instant_payout_enabled` now maps that verdict to the same two 403s as before. Its name, signature, return value and messages are unchanged.
- `earnings.get_driver_balance` sets `instant_payout_available = refusal is None`.
- **Kill switch:** mirrored. Both callers go through the same helper.
- **Daily cap (`settings.instant_payout_daily_cap_cad`):** deliberately **not** mirrored. The flag means "this feature is available to you", not "you have cap room left today". The cap depends on the amount requested, is checked per request, and returns its own 429 with its own copy. Folding it into the flag would also add a settings read and a scan of today's payouts to every balance load.
- **Area lookup fails (DB error):** the flag reports `false` and an `error`-level log line records the exception, `details["original"]` when present, `driver_id` and `service_area_id`. The endpoint does not return 503. The reasoning is below.

### Why `false` + error log, not 503

The flag is one field in a shared money response. A 503 on `/drivers/balance` would affect every consumer of that response:

- **driver-app `app/driver/payout.tsx` → `loadStripeStatus()`**: this reads `stripe_account_onboarded` / `payouts_enabled` / `stripe_id_number_provided` from the same response. Its `catch` sets `stripeAccountStatus = 'not_onboarded'` and `stripeIdOnFile = false`. A 503 caused by the area lookup would tell an onboarded driver they are **not onboarded**, which is misleading.
- **driver-app `store/driverStore.ts` → `fetchDriverBalance()`**: rethrows, so `loadData()` on the payout screen fails and the balance does not render.
- **backend `routes/users.py` account deletion** (`_earnings.get_driver_balance({"id": user_id})`): a 503 would block account deletion over a flag deletion never reads.
- **backend `payouts.request_instant_payout` and `_request_payout_legacy`**: both call `get_driver_balance` for `payable_balance`. The instant path has already run its own area lookup by then.

`false` is also the verdict the real gate reaches in the same situation: its lookup raises the same error, so the request fails and is never approved. Reporting `false` therefore cannot show a misleading "available". The error is not swallowed: it is logged at `error` level with the underlying exception.

**Alternatives considered and rejected:**

- **Patch `earnings.py` inline with its own copy of the new rule.** This is the smallest diff, but it keeps two copies of one rule, and that duplication is exactly how the two drifted apart. Rejected.
- **Have `earnings.py` call `_require_instant_payout_enabled` and treat a 403 as `false`.** This would make an exception drive normal control flow. It would need a function-level import to avoid the `payouts → earnings` circular import. It also risks catching unrelated `HTTPException`s. Rejected in favour of a verdict helper that does not raise.
- **Put the helper in `payouts.py` or `earnings.py`.** `payouts.py` already imports `earnings`, so either placement needs a circular or lazy import. `_shared.py` is the existing home for helpers shared across the drivers submodules.

## 4. Risk & impact on existing functionality

**Consumers of `instant_payout_available`** (grep across `backend/`, `driver-app/`, `rider-app/`, `admin-dashboard/`, `shared/`, all `.py/.ts/.tsx/.js/.md`, case-insensitive, including `instantPayoutAvailable`):

| Consumer | Where | Effect |
|---|---|---|
| Producer | `backend/routes/drivers/earnings.py` (`get_driver_balance` return dict) | the only place the field is set |
| driver-app | **none**. The `DriverBalance` interface in `driver-app/store/driverStore.ts` does not declare the field. `payout.tsx` reads only the Stripe fields from `/drivers/balance`. | no UI change today |
| rider-app / admin-dashboard / shared | none | — |
| Docs | `docs/change-log/2026-08-14-weekly-auto-payout.md` ("Client can hide instant option"), `docs/change-log/2026-09-25-instant-payout-velocity-cap.md` (names this follow-up) | historical, not changed |

**Callers of `get_driver_balance`**, whose response now differs in one field and, on an area-lookup failure, in status:

- `GET /drivers/balance`, called by driver-app `payout.tsx` `loadStripeStatus()` and `driverStore.fetchDriverBalance()`
- `routes/users.py` account-deletion guard
- `routes/drivers/payouts.py` `_request_payout_legacy` and `request_instant_payout`

**Callers of `_require_instant_payout_enabled`:** only `request_instant_payout`. Its tests patch `backend.routes.drivers.payouts.db_supabase.get_rows`. That targets the shared `db_supabase` module object, so the lookup in `_shared` sees the patch. All kill-switch tests (`test_auto_payout.py::TestInstantPayoutKillSwitch`) pass unchanged.

**Behaviour change on DB error:** before this change, a `service_areas` read error inside `get_driver_balance` propagated as a `DatabaseError`, which becomes a 503. Nothing handled that path on purpose; it was incidental. Now it is 200 with the flag `false`, plus an error log. All money fields (`payable_balance` and the rest) keep their existing 503-on-error handling, unchanged.

**Blast radius:** single surface (backend), two read-only code paths. There is no write, no Stripe call, no wallet delta and no state-machine change. Query count is unchanged: the flag still does at most one `service_areas` read, and none when the driver has no `service_area_id`. The instant-payout request path does the same two reads as before (gate + balance). No background loop is involved.

`docs/known-forks.md` lists neither file.

## 5. User-experience effect

- **Driver:** nothing visible today. No shipped screen reads the field. Any future screen that does will now hide or disable instant payout for drivers who would be refused, instead of offering a button that 403s.
- A driver who is mid-session sees no change. The field changes only on the next balance fetch, and nothing renders it.
- No copy changes. The 403 messages in `payouts.py` are byte-identical.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/_shared.py` | new `_instant_payout_area_verdict` | single source for the area rule |
| `backend/routes/drivers/payouts.py` | `_require_instant_payout_enabled` maps the verdict to the same 403s; import added | reuse, no behaviour change |
| `backend/routes/drivers/earnings.py` | flag derived from the verdict; DB error → `false` + error log | the fix |
| `backend/tests/test_instant_payout_available_flag.py` | 13 new tests | regression |
| `docs/change-log/2026-09-25-instant-payout-available-flag.md` | this file | CLAUDE.md gate |

## 7. Before / after

```python
# Before (earnings.py)
instant_payout_available = True
sa_id = driver.get("service_area_id")
if sa_id:
    sa_rows = await db_supabase.get_rows("service_areas", {"id": sa_id}, limit=1)
    if sa_rows and sa_rows[0].get("instant_payout_enabled") is False:
        instant_payout_available = False
```

```python
# After (earnings.py)
try:
    _sa, refusal = await _instant_payout_area_verdict(driver)
    instant_payout_available = refusal is None
except Exception as e:
    logger.error(..., exc_info=True, extra={"driver_id": ..., "service_area_id": ...})
    instant_payout_available = False
```

| Driver | Before | After | `POST /payouts/instant` |
|---|---|---|---|
| no `service_area_id` | `true` | `false` | 403 |
| `service_area_id` with no row | `true` | `false` | 403 |
| area, `instant_payout_enabled = false` | `false` | `false` | 403 |
| area, enabled or NULL | `true` | `true` | proceeds to the later checks |
| `service_areas` read errors | whole response 503 | `false` + error log, 200 | fails |

## 8. Rollback plan

There is no feature flag, and none is warranted: no UI consumes the field, and the change is read-only. Rollback is `git revert f6f8f97`, then redeploy. That restores the old flag and leaves the gate alone. `d49ed29` is behaviour-neutral and can stay. No live data is written by either commit, so no data remediation is needed. The gate's own behaviour (#5791) is untouched by both commits.

## 9. Verification performed

- [x] **New tests:** `tests/test_instant_payout_available_flag.py`, 13 passed. Cases: no area (None and ""), unresolvable area, enabled area (True and NULL), kill switch off, lookup failure (false, error log, money fields still render), flag-matches-gate parity across 5 scenarios, and the daily cap not being consulted. Against the pre-fix `earnings.py`, 6 of the 13 fail as expected: no area ×2, unresolvable area, lookup failure, and parity ×2.
- [x] **Related suites:** `test_earnings_coverage`, `test_drivers_extended`, `test_auto_payout`, `test_instant_payout`, `test_instant_payout_velocity_cap`, `test_payouts_coverage`, `test_p2_payout_t4a`, `test_payout_toctou`, `test_driver_deletion_tombstone`, `test_previous_app_sunset`, `test_admin_drivers_coverage`. Together with the new file: 564 passed. `test_loguru_call_conventions`: 8 passed (earnings uses the stdlib logger, so `extra=`/`exc_info=` are valid).
- [x] **Behaviour-neutral refactor check:** the 4 payout suites were run after the helper extraction and before the flag change: 133 passed.
- [x] `ruff check` and `ruff format --check` are clean. The pre-commit hook passed on both commits (it warned about an unrelated missing path cited in `sprint-current.md`).
- [x] **Blast-radius grep:** `instant_payout_available` / `instantPayoutAvailable` repo-wide; `get_driver_balance`; `/drivers/balance` in driver-app, rider-app, admin-dashboard and shared; `_require_instant_payout_enabled` in backend and tests; `docs/known-forks.md`.
- [ ] Staging or manual repro: not done.
- [x] **Feature flag:** not used. The change is not user-visible today (no consumer).

## 10. What was NOT verified

- No run against a real Supabase or staging environment. Everything used mocked `get_rows` and `mock_supabase_client`.
- No `spinr-money-auditor` or other `spinr-*` reviewer was run on this diff: the sub-agent that wrote it had no Agent tool. **A human or orchestrator should run `spinr-money-auditor` before merge.**
- No driver-app build or tests were run, since driver-app was not changed. The claim that no UI reads the flag comes from grep, not from a runtime check. driver-app has no visual regression tooling.
- The production share of drivers with a NULL or unresolvable `service_area_id`, i.e. how many flags flip to `false`, was not checked. No production access was used.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behaviour change to an already-shipped flow (UX field filled in; no shipped UI reads the field)
