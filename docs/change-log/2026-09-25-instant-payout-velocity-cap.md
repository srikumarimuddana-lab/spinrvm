# Change Impact & Risk Log: instant-payout velocity cap, and closing the no-service-area bypass

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (AI-assisted), branch `claude/fix-instant-payout-velocity-cap` |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / drivers |
| PR / commit link | `acad994` (migration + admin field), `2d20253` (test fixtures), `542d859` (area gate fails closed), `3aef85f` (cap enforcement). Not pushed. |
| Related issue or gap ID | ROADMAP N22; `08-hostile-review.md` §1.4 (RR-117, RR-109); session-3 T&S card "Instant-payout velocity cap" (TSF-002, CS-8); session-2 §3 Now item 1 |

## 1. Issue / gap identified

Any authenticated driver can call `POST /api/drivers/payouts/instant` with no per-driver daily limit. The endpoint is enabled in all 6 service areas, even though the driver app has no instant-payout UI. Also, a driver with no service area, or with a `service_area_id` that matches no row, passes the per-area kill switch.

## 2. Root cause

- **No cap:** `request_instant_payout` limited only the size of each request ($5 to $5,000) and the payable balance. Nothing summed a driver's earlier instant payouts. The partial unique index from migration 250 allows one in-flight payout per driver, but only in-flight: back-to-back payouts are unlimited.
- **Bypass:** `_require_instant_payout_enabled` returned early when `service_area_id` was falsy. It allowed the request when the area lookup returned no row, so the switch was opt-out per market. `PUT /drivers/me` accepts any `service_area_id` string without checking it against `service_areas`, so a driver could create the dangling-id case themselves. That write changes an active driver to `needs_review`, but the instant-payout endpoint does not check driver status.

## 3. Fix / remediation

1. **Migration 473** adds `settings.instant_payout_daily_cap_cad` as `NUMERIC(10,2)`, nullable, with `CHECK (NULL OR > 0)`. NULL means no cap, which is the default. A matching field on `SettingsUpdateRequest` lets ops set the cap through `PUT /api/admin/settings`, the same way as the neighbouring `corporate_wallet_admin_adjust_daily_cap`.
2. **Cap enforcement** (`_require_within_instant_payout_daily_cap`) is the endpoint's last check before the `reserved` row is inserted. When the cap is set, the check:
   - sums the gross `amount` of the driver's `payout_type='instant'` rows with `created_at` at or after local midnight;
   - skips rows with status `failed` or `reversed`, because no money moved;
   - counts `reserved`, `transfer_completed`, `completed` and `stranded` rows;
   - returns **429** with the cap and the remaining allowance if `used + amount > cap`.

   A payout that lands exactly on the cap is allowed. Rejected requests write no row and make no Stripe call, so nothing is ever partly transferred. If the sum query hits its 500-row limit, the check fails closed.
3. **Timezone:** `payouts.py` had no day-boundary logic before this change, so "today" is the calendar day in the driver's service area. The code reads `service_areas.timezone` (`NOT NULL DEFAULT 'America/Regina'`, migration 105) and falls back to UTC, with a warning log, when the value is missing or not a valid IANA zone.
4. **Area gate fails closed:** the gate returns 403 when the driver has no `service_area_id` or when the id matches no row. The message is "Instant payouts need a service area on your driver profile. Your earnings are paid out automatically every Sunday." The gate now returns the area row, which the cap uses for the timezone.

**Alternative considered:** enforce the cap in a Postgres `SECURITY DEFINER` function that sums and reserves under a row lock. That would be atomic, but it needs a new money function, a new migration and a review of that migration. The existing one-in-flight-per-driver unique index already serialises concurrent reservations, so the app-layer check leaves only a residual race of a few milliseconds (see §4). The app-layer check wins on cost and follows the existing `_check_daily_adjust_cap` pattern in `routes/corporate_wallet.py`.

**Flag decision:** the cap is flagged: NULL skips the check and the query. **The area-gate change is not flagged. It is the one immediate behaviour change in this PR.** Reasons:
- The driver app has no instant-payout UI. The standard-payout 410 message deliberately avoids advertising instant payout, so no app user can reach this path. Only a hand-crafted API call can.
- The bypass defeats a kill switch that already exists. Putting the fix behind the cap's NULL check would leave a market-level `instant_payout_enabled = false` ineffective for these drivers until ops also sets an unrelated cap.
- Weekly auto-payout (`utils/auto_payout.py`) does not use this gate. It already reports drivers with no area under an `"unassigned"` slice. An affected driver still gets paid every Sunday.

## 4. Risk & impact on existing functionality

Blast radius: **single surface (backend), one endpoint.**

| Reader/writer | Relationship | Effect |
|---|---|---|
| `routes/drivers/payouts.py::request_instant_payout` | only caller of the gate and the cap | changed as described |
| `routes/drivers/__init__.py` | re-exports `request_instant_payout` and the fee helpers | unchanged; the gate and cap helpers are not re-exported |
| `routes/drivers/earnings.py` (~l.291) | reads `service_areas.instant_payout_enabled` on its own to set `instant_payout_available` in the balance response | **not changed (another agent owns this file).** A driver with no or an unresolvable area still gets `instant_payout_available: true` while the endpoint now returns 403. No shipped UI reads this field for instant payout. Follow-up for the owner of `earnings.py`: mirror the fail-closed rule. |
| `routes/drivers/payouts.py::get_instant_payout_quote` | fee quote only | does not reflect the cap or the area; unchanged |
| `routes/admin/service_areas.py` | writes `instant_payout_enabled` | unchanged |
| `utils/auto_payout.py` | Sunday payouts; shares the `payouts` table and the migration-250 in-flight index | unchanged; no gate or cap in that path |
| `payouts` table | new read: `driver_id` + `payout_type` + `created_at >= X`, columns `amount,status`, limit 500 | read only, and only when a cap is set. Readers such as `stripe_reconcile`, `stripe_payout_sync_service`, `driver_statement`, `dual_run_monitor`, admin `drivers.py` and the admin payouts tab are unaffected, because no new statuses or rows are written. |
| `settings` table and `get_app_settings()` | new column | the reader uses `.get()`, so a missing column (migration not yet applied) means no cap. The settings cache TTL is 60 s, so a cap change takes up to 60 s per replica. |
| `test_settings_column_parity.py`, `test_admin_settings_write_allowlist_drift.py` | schema/model drift guards | drift snapshot updated; both pass |

Other points:
- **State machine, insurance periods, wallet deltas:** none touched. No Stripe call shape changed.
- **Residual race:** request B's cap read can miss request A only if A reserves, runs both Stripe calls and reaches a terminal status in the few milliseconds between B's read and B's reserve INSERT. The in-flight unique index returns 409 to B in every other interleaving. This is documented in the helper's docstring.
- **Calendar-day window:** a driver can take up to the cap just before midnight and again just after. A rolling 24-hour window would close that. The task asked for "today", so this change uses the calendar day; a rolling window would be a follow-up if wanted.
- **Area switching:** a driver can move to another enabled area through `PUT /drivers/me`. The cap applies per driver in every area, so switching does not reset it. It can move the day boundary only between timezones, and all current areas are in Saskatchewan.
- **Latency:** adds one indexed-by-driver read when a cap is set. None when NULL.

## 5. User-experience effect

- **Drivers:** no shipped UI reaches this endpoint. Direct API callers with no or an unresolvable service area now get 403 immediately. When ops sets a cap, over-cap requests get 429 with this text: "Instant payouts are limited to $X per day. You can cash out up to $Y more today. The rest of your balance is paid out automatically every Sunday." No money is lost; it goes out with the Sunday payout.
- **Mid-session:** nothing is visible to riders, or to drivers online or on a trip.
- **Internal admin:** a new API-settable field. **No admin-dashboard UI was added.** The settings page has a seeded visual-regression baseline, and the task was backend-only. Until a UI follow-up, set the cap through `PUT /api/admin/settings` (`{"instant_payout_daily_cap_cad": 500}`). The existing `settings_updated` audit-log row records the change.
- **Copy:** both messages are specific, non-technical and point to the Sunday payout. X3's future cash-out screen should show the cap message verbatim.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/473_instant_payout_daily_cap.sql` | new nullable `NUMERIC(10,2)` column, CHECK > 0, COMMENT, rollback note | storage for the cap; default NULL = off |
| `backend/routes/admin/settings.py` | `instant_payout_daily_cap_cad: Optional[Decimal]` (gt 0, le 50000, 2 dp) | admin-writable the same way as its neighbour |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | added the column to `KNOWN_SETTINGS_COLUMNS` | the drift guard requires it |
| `backend/routes/drivers/payouts.py` | gate fails closed and returns the area row; new `_instant_cap_day_start_utc` and `_require_within_instant_payout_daily_cap`; call wired in before the reserve | the fix |
| `backend/tests/test_auto_payout.py` | two "allows" kill-switch tests are now "blocks" tests (no area, `""`, dangling id); new test for the returned row | they asserted the bypass |
| `backend/tests/test_instant_payout.py`, `backend/tests/test_payouts_coverage.py` | class-level autouse fixture that stands in an enabled area for the plumbing tests | their drivers had no area and passed only through the bypass |
| `backend/tests/test_instant_payout_velocity_cap.py` | new: 13 tests through the real `_base` CRUD helpers on `mock_supabase_client` | dry run for gate 4 |
| `docs/change-log/2026-09-25-instant-payout-velocity-cap.md` | this file | CLAUDE.md gate |

## 7. Before / after

```python
# Before: _require_instant_payout_enabled
sa_id = driver.get("service_area_id")
if not sa_id:
    return                                   # no area -> allowed
sa_rows = await db_supabase.get_rows("service_areas", {"id": sa_id}, limit=1)
if sa_rows and sa_rows[0].get("instant_payout_enabled") is False:   # dangling id -> allowed
    raise HTTPException(403, ...)
```

```python
# After
sa_rows = await db_supabase.get_rows("service_areas", {"id": sa_id}, limit=1) if sa_id else []
if not sa_rows:
    raise HTTPException(403, "Instant payouts need a service area on your driver profile. ...")
if sa_rows[0].get("instant_payout_enabled") is False:
    raise HTTPException(403, ...)
return sa_rows[0]
# ...and in request_instant_payout, immediately before the reserve INSERT:
await _require_within_instant_payout_daily_cap(driver, service_area, req.amount, settings)
```

**Scenario (dry run in `test_instant_payout_velocity_cap.py`).** Driver in Regina, cap set to $200. Today's instant rows: $100 `completed` and $50 `stranded`, plus earlier `failed` and `reversed` attempts. The driver requests $100.
- Before: the transfer runs. Nothing limits a sequence of $5,000 requests up to the payable balance.
- After: used = $150 (failed and reversed rows excluded). $150 + $100 > $200, so the response is 429 "…limited to $200.00 per day. You can cash out up to $50.00 more today…". No `payouts` row is inserted and `stripe.Transfer.create` is not called. A $50 request would succeed because $150 + $50 = $200 exactly. With the cap NULL, the same $100 request (or $500 on top of $5,000 already taken today) succeeds and no cap query runs, which is today's behaviour.

**Scenario (gate).** A driver with `service_area_id = "does-not-exist"` or no area requests $50. Before: the transfer runs even if every real market had `instant_payout_enabled = false`. After: 403 before the GST/SIN checks, the balance read and any Stripe call. This happens regardless of the cap.

## 8. Rollback plan

- **Cap (no deploy):** `UPDATE settings SET instant_payout_daily_cap_cad = NULL WHERE id = 'app_settings';` takes effect within 60 s (settings cache TTL). The admin PUT drops `None` values, so clearing the cap needs this SQL, not the admin API. To stop enforcement without clearing, raise the cap instead: PUT a large value such as 50000.
- **Migration:** `ALTER TABLE settings DROP COLUMN IF EXISTS instant_payout_daily_cap_cad;` is safe against live code, which reads the key with `.get()`. Remove the `SettingsUpdateRequest` field in the same change, or an admin save that sets it will fail with PGRST204.
- **Area gate (no deploy, per driver):** assign the affected driver a real `service_area_id`, as an admin data fix. The switch has no flag, so a fleet-wide revert of the fail-closed rule needs a code revert and redeploy. That is acceptable because no app UI reaches the endpoint, and the Sunday auto-payout still pays those drivers in full.
- **Live data:** no Stripe, wallet or ride-state writes are added, and rejections write nothing. No data cleanup is ever needed.

## 9. Verification performed

- [x] **Automated tests** (`python -m pytest -o addopts="" -q -p no:cacheprovider`): 195 passed. Files: `test_instant_payout_velocity_cap.py` (new, 13), `test_instant_payout.py`, `test_payouts_coverage.py`, `test_auto_payout.py`, `test_payout_toctou.py`, `test_settings_column_parity.py`, `test_admin_settings_write_allowlist_drift.py`, `test_loguru_call_conventions.py`.
- [x] **Mutation check:** with the cap call temporarily unwired, 4 of the new tests failed (over cap, at cap, row-limit fail-closed, under-cap query shape). The file was then restored.
- [x] **Gate 4 dry run on `mock_supabase_client`:** the new tests drive the real `repositories._base` `get_rows`, `insert_one` and `update_one` through the conftest-patched client. A per-table fake records the PostgREST chain: they assert `eq driver_id`, `eq payout_type=instant` and `gte created_at`, and that no reserve insert happens on rejection.
- [x] **Day boundary:** with `datetime` frozen at 03:30 UTC, Regina gives 06:00 UTC the previous day, and an invalid or missing zone gives 00:00 UTC.
- [x] `ruff check` and `ruff format --check` are clean on all touched Python files. The pre-commit hook passed on every commit.
- [x] **Blast-radius grep** for `request_instant_payout`, `_require_instant_payout_enabled`, `instant_payout_enabled`, `payouts/instant`, `payout_type == "instant"`, `instant_payout` across backend, admin-dashboard, driver-app, rider-app and shared. Results are in §4.
- [x] **Money conventions:** Decimal only (`Decimal(str(x))` for DB numerics), no float, no Stripe amount changes. The admin write boundary keeps the existing `float(Decimal)` conversion pattern.
- [x] **Feature flag:** the cap is dark (NULL). The area gate is unflagged, with the justification in §3.
- [ ] **Staging repro:** not done.
- [ ] **Production build:** not applicable. No admin-dashboard, rider-app or driver-app file changed.

**What was NOT verified:**
- Nothing was run against live or staging Supabase or Stripe; all results come from mocked responses. Migration 473 has not been applied anywhere, and the `run_migrations.py --dry-run` path was not exercised.
- I did not verify that PostgREST returns `payouts.created_at` so that a `gte` against an ISO-8601 UTC string compares correctly across stored formats. The column is `TIMESTAMPTZ`, and existing rows are written with `datetime.now(timezone.utc).isoformat()`, so the comparison should be sound, but only by reasoning.
- I did not verify how many live drivers have a NULL or dangling `service_area_id`, because production was out of scope. **Before merge, a human should run a read-only count:** `SELECT count(*) FROM drivers d LEFT JOIN service_areas s ON s.id = d.service_area_id WHERE s.id IS NULL;`.
- The performance of the new `payouts` read at scale was not measured. It filters on `driver_id` and the per-driver row count per day is tiny, but no index plan was checked.
- The following were reasoned about, not tested:
  - whether `get_current_user` or anything else upstream blocks suspended or `needs_review` drivers from this endpoint (the endpoint does not check driver status itself);
  - the residual reservation race in §4.

## 10. Sign-off

- [x] The rollback plan is concrete: SQL to clear the cap, per-driver area assignment for the gate, and the column drop.
- [x] The blast radius is stated, including the unfixed `earnings.py` availability mismatch.
- [x] There is no silent behaviour change. The one immediate change (the area gate fails closed) is called out in §3 and §5.
- Review: self-reviewed against `.claude/agents/spinr-money-auditor.md` and `.claude/agents/spinr-fraud-auditor.md` checklists (not run as sub-agents).
