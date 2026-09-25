# Change Impact & Risk Log — remove instant payouts (weekly-only)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (agent session) |
| Surface(s) | backend, driver-app (store listing copy only) |
| Domain (Sentry tag) | payments / drivers / admin |
| PR / commit link | local branch `claude/remove-instant-payouts` (not pushed) |
| Related issue or gap ID | Owner decision 2026-09-25 (weekly-only payouts); supersedes BENCH-004 / RR-109 / ROADMAP N22 |

## 1. Issue / gap identified

The backend still offered a fee-bearing instant payout (`POST /api/drivers/payouts/instant`, `GET /api/drivers/payouts/instant/quote`) that any authenticated driver could call, although the owner decided on 2026-09-25 that Spinr pays drivers weekly only (the Sunday auto-payout). The driver-app never had a screen for it, but the Play/App Store listing copy advertised "instant payouts".

## 2. Root cause

The instant payout feature was built before the weekly-only decision. When cashout moved to the weekly auto-payout (migration 314, 2026-08-14), instant payout was kept behind a per-area kill switch (`service_areas.instant_payout_enabled`, `DEFAULT TRUE`) instead of being removed. Later work (the ROADMAP N22 velocity cap, migration 473, and the `instant_payout_available` flag) hardened it instead of retiring it.

## 3. Fix / remediation

- `POST /drivers/payouts/instant` and `GET /drivers/payouts/instant/quote` stay mounted but raise **410** straight away, before any DB or Stripe call. Message: "Instant payouts are not offered. Earnings are paid automatically every week." The request body / `amount` query is ignored, so old clients get the 410 and not a 422. Auth (`get_current_user`) is still required, like every other driver route.
- Deleted dead helpers: `InstantPayoutRequest`, `INSTANT_PAYOUT_*` fee constants, `compute_instant_payout_fee`, `_require_instant_payout_enabled`, `_require_within_instant_payout_daily_cap`, `_instant_cap_day_start_utc`, `_INSTANT_CAP_*` constants (payouts.py), `_instant_payout_area_verdict` (_shared.py), and their re-exports (drivers `__init__.py`). **Kept:** `_attempt_transfer_reversal`, which the legacy standard path (`_request_payout_legacy`) still uses.
- `GET /drivers/balance`: `instant_payout_available` stays in the response for API compatibility but is always `False`. The `service_areas` read that computed it is gone.
- Admin `PUT /admin/service-areas/{id}`: `instant_payout_enabled` is removed from `ServiceAreaUpdateRequest` and the write allow-list. It cannot be re-enabled through the API. A client that still sends the key has it dropped (pydantic `extra=ignore`) and gets no error.
- Migration `483_service_areas_instant_payout_default_off.sql` sets the column default to `false` and normalises any row that is not `false`. The column is not dropped.
- Docs: stale docstring in `payouts.py` (`request_payout`); `docs/API_REFERENCE.md`; one-line "Superseded" notes on BENCH-004, RR-109 and N22.
- Store copy: `metadata.json` and `generate_screenshots.py` now say weekly automatic payouts. The generator no longer draws the mock "Cash out" button or the "Or cash out anytime" caption.
- **Approved production data change, done earlier today (outside this branch):** all 6 production service areas set to `instant_payout_enabled = false`. Verified afterwards: 0 rows still true. Production has never had an instant payout row.

**Kept on purpose (7-year trip/financial retention: old `payout_type='instant'` rows must stay representable):** the `'instant'` → "Instant payout" label in `backend/utils/driver_statement.py`; the "Instant" label in `admin-dashboard/src/app/dashboard/drivers/_components/driver-payouts-tab.tsx`; `_PAYOUT_TYPES = ("standard", "instant")` and the `instant-payout-transfer-{id}` idempotency-key rebuild in `backend/utils/stripe_reconcile.py`; the `payout.paid` / `payout.failed` handler in `backend/routes/webhooks.py`; migration 250's partial unique index; the inert `settings.instant_payout_daily_cap_cad` column and its entry in `backend/routes/admin/settings.py` (another workstream owns that file). `_attempt_transfer_reversal` keeps its `instant-payout-reversal-{id}` idempotency key unchanged, so a replay of an old reversal still dedupes.

## 4. Risk & impact on existing functionality

Blast radius: **backend single-surface**, plus driver-app store copy (no app code). Grep performed repo-wide (`backend/`, `driver-app/`, `rider-app/`, `admin-dashboard/`, `shared/`, `scripts/`, `.github/`, `.semgrep/`) for: `INSTANT_PAYOUT_FEE_PCT|MIN_FEE|MAX_FEE`, `compute_instant_payout_fee`, `InstantPayoutRequest`, `_require_instant_payout_enabled`, `_require_within_instant_payout_daily_cap`, `_instant_cap_day_start_utc`, `_INSTANT_CAP_`, `_instant_payout_area_verdict`, `request_instant_payout`, `get_instant_payout_quote`, `instant_payout_available`, `instant_payout_enabled`, `payouts/instant`, `driver_instant_payout`.

Consumers found and how each is affected:

| Consumer | Effect |
|---|---|
| `backend/routes/drivers/payouts.py` (`request_instant_payout`, `get_instant_payout_quote`) | now 410 stubs; the only callers of the deleted gate/cap/fee helpers |
| `backend/routes/drivers/__init__.py` | re-exports of deleted names removed; the two route handlers are still re-exported |
| `backend/routes/drivers/earnings.py` (`get_driver_balance`) | was the only other caller of `_instant_payout_area_verdict`; now returns constant `False`, one fewer DB read per balance call |
| driver-app / rider-app / admin-dashboard / shared | **no reader** of `instant_payout_available`, `/payouts/instant` or `instant_payout_enabled` (grep: zero hits). The driver-app never had an instant payout UI |
| `backend/routes/admin/service_areas.py` | field removed from the update model and allow-list; admin-dashboard never sent it |
| `backend/utils/auto_payout.py` (weekly Sunday loop) | **untouched**, and imports nothing that was removed (it only mirrors formulas in comments). Migration 250's in-flight index, which stops an auto/instant double-pay, is unchanged |
| `backend/utils/stripe_reconcile.py`, `backend/utils/driver_statement.py`, `backend/routes/webhooks.py` | untouched; still handle historic `instant` rows |
| `backend/routes/admin/settings.py` `instant_payout_daily_cap_cad` | untouched and now inert (nothing reads it) |
| Tests | `test_instant_payout.py` rewritten (410 + nothing called); `test_instant_payout_velocity_cap.py` deleted; the instant sections of `test_payouts_coverage.py` and `test_auto_payout.py` deleted; `test_payout_toctou.py` re-anchored on `_request_payout_legacy`; `test_instant_payout_available_flag.py` rewritten; new admin test in `test_admin_service_areas_coverage.py`. `test_stripe_reconcile_stuck_reserved_payouts.py`, `test_driver_statement.py` and `tests/rls/conftest.py` still use `instant` rows or migration 314 as history. They were left unchanged and still pass |

No ride state machine, insurance-period, or background loop impact. No money moves differently: instant payouts were already disabled in every production area (403) before this change, and 0 instant payout rows exist.

Could anything regress? A scripted client that called `/payouts/instant` got 403 before (area disabled) and now gets 410. It is a 4xx either way and no money moves. An admin tool posting `instant_payout_enabled` now has the key ignored instead of written.

Alternative considered: delete the routes outright (404). Rejected: a 410 with a plain message tells an old or scripted client what changed, and keeping two tiny stubs costs almost nothing. Also considered: keep the per-area flag as a re-enable switch. Rejected: the owner decision is "no instant payouts anywhere", and a flag that can be flipped back on is exactly what this change closes.

## 5. User-experience effect

- **Driver-app:** no in-app change. There was never an instant payout UI, and no client reads `instant_payout_available`. Nothing changes mid-session.
- **Store listing (Play / App Store):** the description and release notes now say "Weekly automatic payouts to your bank" / "Weekly automatic payouts" instead of instant payouts. The screenshot generator drops the mock "Cash out" button and the "Or cash out anytime" caption. **The PNG screenshots were NOT regenerated here. A human must regenerate them (`driver-app/store-assets/generate_screenshots.py`) and upload them with the new copy to both stores.**
- **Internal admin:** nothing visible. The dashboard never showed an instant payout toggle.
- **API callers:** new 410 message on the two instant endpoints.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/payouts.py` | instant POST/quote → immediate 410; fee model, area gate, daily-cap helpers and `InstantPayoutRequest` deleted; stale docstring/comments fixed | weekly-only decision |
| `backend/routes/drivers/__init__.py` | removed re-exports of deleted names | dead symbols |
| `backend/routes/drivers/_shared.py` | removed `_instant_payout_area_verdict` | no callers left |
| `backend/routes/drivers/earnings.py` | `instant_payout_available` always `False`; `service_areas` read removed | API compat without a pointless DB read |
| `backend/routes/admin/service_areas.py` | `instant_payout_enabled` removed from update model + allow-list | cannot be re-enabled via API |
| `backend/migrations/483_service_areas_instant_payout_default_off.sql` | new: default `false` + normalise rows | new areas must not come up instant-enabled |
| `backend/tests/test_instant_payout.py` | rewritten: 410, no DB/Stripe call, body/query ignored, auth still enforced, helpers gone | regression |
| `backend/tests/test_instant_payout_velocity_cap.py` | deleted | tested removed cap |
| `backend/tests/test_payouts_coverage.py` | instant gap tests removed | tested removed code |
| `backend/tests/test_payout_toctou.py` | standard section re-anchored on `_request_payout_legacy`; instant section asserts the stub has no Stripe/DB call | source-slicing anchor moved |
| `backend/tests/test_auto_payout.py` | instant kill-switch tests removed | tested removed gate |
| `backend/tests/test_instant_payout_available_flag.py` | rewritten: always `False`, `service_areas` never read | regression |
| `backend/tests/test_admin_service_areas_coverage.py` | new test: key not in model, not written | regression |
| `docs/API_REFERENCE.md` | payouts rows corrected | stale doc |
| `docs/audit/clean-sheet/03-benchmark.md` | BENCH-004 superseded note | owner decision |
| `docs/audit/clean-sheet/matrices/risk-register.md` | RR-109 superseded note | owner decision |
| `docs/audit/clean-sheet/ROADMAP.md` | N22 superseded notes | owner decision |
| `driver-app/store-assets/metadata.json` | instant wording → weekly payouts | store copy matches product |
| `driver-app/store-assets/generate_screenshots.py` | "Cash out" mock button removed; caption → "Automatic weekly deposits" | store copy matches product |
| `docs/change-log/2026-09-25-remove-instant-payouts.md` | this log | CLAUDE.md gate |

## 7. Before / after

```python
# Before (payouts.py)
@router.post("/payouts/instant")
@idempotent_endpoint(scope="driver_instant_payout")
async def request_instant_payout(req: InstantPayoutRequest, request: Request, current_user=...):
    driver = ...get_rows("drivers", ...)
    service_area = await _require_instant_payout_enabled(driver)   # 403 in every prod area
    ...fee, balance, daily cap, reserve row, stripe.Transfer.create, stripe.Payout.create(method="instant")...

# After
@router.post("/payouts/instant")
async def request_instant_payout(current_user: dict = Depends(get_current_user)):
    raise HTTPException(status_code=410, detail=_INSTANT_PAYOUT_GONE_DETAIL)
```

```python
# Before (earnings.py get_driver_balance)
_sa, refusal = await _instant_payout_area_verdict(driver)   # service_areas read
instant_payout_available = refusal is None
# After
"instant_payout_available": False,
```

Concrete scenario: a driver in Regina (area flag already `false` in prod) calls `POST /payouts/instant {"amount": "50.00"}`. Before: 403 "Instant payouts are not available in your service area…" after reading `drivers` + `service_areas`. After: 410 "Instant payouts are not offered. Earnings are paid automatically every week." No reads besides auth. The Sunday auto-payout for that driver is unchanged.

## 8. Rollback plan

- **Data flag:** production already has `instant_payout_enabled = false` in all 6 areas from today's approved data change. The code no longer reads the flag, so it is not a live kill switch any more. Turning instant payouts back on is a product reversal and needs a code revert.
- **Code:** revert this branch's commits (`git revert`). This restores the old handler, gate and cap. Instant payouts would still be refused (403) until an area row is set back to `true`. This is safe because no instant payout rows, Stripe transfers or wallet deltas were ever created, so there is no live data to repair.
- **Migration rollback SQL** (also in the migration header): `ALTER TABLE public.service_areas ALTER COLUMN instant_payout_enabled SET DEFAULT true;`. Row values are left as they are. Re-enable an area only with a deliberate `UPDATE`.

## 9. Verification performed

- [x] Automated tests: `cd backend && python -m pytest -q -p no:cacheprovider --no-cov --ignore=tests/rls -k "payout or earnings or balance or service_area or statement or reconcile or drivers"` → **1988 passed, 18 skipped, 2 xfailed, 0 failed** (15226 deselected).
- [x] Targeted: `tests/test_instant_payout.py` (17 passed), `tests/test_payout_toctou.py` + `tests/test_payouts_coverage.py` (39 passed), `tests/test_auto_payout.py` (67 passed), `tests/test_instant_payout_available_flag.py` + `tests/test_earnings_coverage.py` (49 passed), admin service-area suites (78 passed).
- [x] `ruff check` + `ruff format --check` on every changed `.py` file; `metadata.json` parses; `generate_screenshots.py` compiles.
- [x] Blast-radius grep (symbols listed in section 4).
- [x] `BASE_SHA=origin/main HEAD_SHA=HEAD python scripts/check_change_impact.py`.
- [x] Reviewed against CLAUDE.md: Decimal rules (no money arithmetic added), dual-import pattern (untouched), no PII in logs or docs, migration append-only (new file 483, column kept).
- Not feature-flagged: this removes a feature that was already disabled in every production area. The only user-visible change is the 410 message and the store copy.

## 10. What was NOT verified

- Not run against live Supabase or real Stripe. All tests use mocks. Migration 483 was **not applied** to any database from this branch. Its SQL was reviewed, not executed.
- Store screenshots were **not regenerated**, and the listing copy was **not uploaded** to Play Console / App Store Connect. A human must do both.
- No driver-app / rider-app / admin-dashboard build was run: no app code changed (store-asset text and a Python generator only). driver-app has no visual regression tooling.
- The full backend suite (outside the `-k` selection above) and the RLS tier were not run.
- No `spinr-money-auditor` agent review was run on the diff in this session.
