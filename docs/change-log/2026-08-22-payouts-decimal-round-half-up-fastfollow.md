# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | vikas@ngitservices.com (via Claude) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | see this branch's commit |
| Related issue or gap ID | Audit finding N15 fast-follow (`ACTION_ITEMS.md` A40), originally flagged but not fixed in `docs/change-log/2026-08-19-admin-decimal-round-convention-fix.md` §"Repo-wide check for other undetected instances" |

## 1. Issue / gap identified

Four admin payout/T4A endpoints rounded a Decimal-sourced money value with Python's built-in
`round()` instead of this codebase's `to_decimal()` (`ROUND_HALF_UP`) convention, so a displayed
dollar figure can disagree with what the rest of the app would show for the same underlying
value on an exact `.5`-boundary case (e.g. `10.125`).

## 2. Root cause

Same root cause as the 2026-08-19 fix this follows up on: `backend/routes/admin/rides.py` is not
in the `spinr-no-float-in-money` semgrep gate's `include` allowlist, so a bare
`round(float(<Decimal>), 2)` slipped in without the gate (or a reviewer primed to look for it)
flagging it. The 2026-08-19 fix explicitly identified these 4 instances by name and line number
but scoped them out as a "fast follow" rather than fixing them in that PR.

**Re-identification note:** the original fix's grep (`round(.*Decimal(\|round(float(_agg_dec\|
round(float(Decimal`) cited lines 3031/3039/3181/3241. Re-running the identical grep against the
current file found the same 4 call sites, unchanged in substance, now at lines 3050/3058/3200/3260
(the file grew by ~19 lines from unrelated work in between). Confirmed via direct read of each
site that the code shape is byte-for-byte the pattern described in the original finding before
changing anything.

## 3. Fix / remediation

Replaced all 4 bare `round(float(<Decimal-derived value>), 2)` calls with
`float(to_decimal(<value>))`, the same helper and pattern the 2026-08-19 fix used for the other 2
instances in this file. `_agg_dec()` (unchanged) still returns a `Decimal`; only the final
rounding step changed at each of the 4 sites. All 4 still end in `float(...)` at the JSON response
boundary, so response schema/type is unchanged.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 4 read-only, display-only response fields**, all on
  `GET /api/admin/payouts/overview` and `GET /api/admin/payouts/stats` — both pure aggregate/read
  endpoints, no downstream write:
  - `stuck_over_48h.amount` (payouts/overview) — grepped `admin-dashboard/src` for consumers:
    only `earnings/page.tsx` (rendered via `fmtMoney(...)`), a TypeScript type declaration in
    `analytics-payouts.ts`, and a hardcoded e2e fixture value (unaffected — doesn't hit this
    endpoint).
  - `blocked_drivers.outstanding_balance` (payouts/overview) — same file, same `fmtMoney(...)`
    display pattern, no other consumer found.
  - `t4a_snapshot.ytd_gross_earnings` (payouts/overview) — same file, same `fmtMoney(...)` display
    pattern, plus the same type declaration and e2e fixture.
  - `total_paid`/`total_pending`/`total_failed` (payouts/stats, via the local `_d()` helper) —
    grepped `admin-dashboard/src` for `payouts/stats` consumers: `analytics-payouts.ts` only
    (a fetch wrapper + type declaration); no write-back found.
- **Confirmed: none of the 4 fields are written to a DB column, used in a Stripe call, or feed the
  T4A generation pipeline itself** (the T4A pipeline reads `driver_earnings` directly, per the
  existing code comment at the `t4a_snapshot` block — this field is a read-only snapshot for the
  admin dashboard, not an input to that pipeline).
- No interaction with any background loop, the ride state machine, or a wallet/allowance delta
  path.
- Value only changes for inputs falling exactly on a `.5` boundary at the 3rd decimal place — not
  a routine shift in every displayed number.

## 5. User-experience effect

- **Internal-admin-facing only.** No rider, driver, or corporate-admin surface reads any of these
  4 fields.
- Not visible mid-session to anyone using the rider/driver app — admin-dashboard-only analytics,
  refreshed on page load/poll.
- Only observable effect: on a rare `.5`-boundary input, the displayed figure on the admin
  Payouts-overview health cards or T4A snapshot may differ from what an admin saw pre-fix by up to
  $0.01, now matching the codebase's own `ROUND_HALF_UP` convention.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/rides.py` | 4 call sites (`stuck_over_48h.amount`, `blocked_drivers.outstanding_balance`, `t4a_snapshot.ytd_gross_earnings`, and the `/payouts/stats` `_d()` helper): `round(float(...), 2)` → `float(to_decimal(...))` | Match the codebase's `ROUND_HALF_UP` money-rounding convention instead of Python's default banker's-rounding `round()` on `Decimal` |
| `backend/tests/test_admin_rides_read_endpoints_coverage.py` | Added `test_payouts_overview_uses_round_half_up` | Regression test pinning `ROUND_HALF_UP` on all 3 `/payouts/overview` fields at a `.125`-boundary input |
| `backend/tests/test_admin_rides_coverage.py` | Added `test_get_payout_stats_uses_round_half_up` | Same, for `/payouts/stats`'s `total_paid` field |

## 7. Before / after

```python
# Before — backend/routes/admin/rides.py (stuck_over_48h / blocked_drivers / t4a_snapshot)
"amount": round(float(_agg_dec("stuck_amount")), 2),
"outstanding_balance": round(float(_agg_dec("blocked_outstanding")), 2),
"ytd_gross_earnings": round(float(_agg_dec("t4a_ytd_gross")), 2),

# _agg_dec("stuck_amount") == Decimal("10.125")
# round(Decimal("10.125"), 2) == Decimal("10.12")   (ROUND_HALF_EVEN)
```

```python
# After
"amount": float(to_decimal(_agg_dec("stuck_amount"))),
"outstanding_balance": float(to_decimal(_agg_dec("blocked_outstanding"))),
"ytd_gross_earnings": float(to_decimal(_agg_dec("t4a_ytd_gross"))),

# to_decimal(Decimal("10.125")) == Decimal("10.13")   (ROUND_HALF_UP)
```

```python
# Before — backend/routes/admin/rides.py (admin_get_payout_stats's local _d() helper)
def _d(key: str) -> float:
    return round(float(Decimal(str(row.get(key) or 0))), 2)
```

```python
# After
def _d(key: str) -> float:
    return float(to_decimal(row.get(key) or 0))
```

## 8. Rollback plan

Pure code change, no migration, no feature flag, no data mutation. Revert is a plain `git revert`
of this commit — safe here specifically because nothing downstream of these 4 read-only display
fields is written to a table, charged via Stripe, or otherwise applied to live data (same
reasoning as the 2026-08-19 fix this follows). No `app_settings` flag needed; not user-visible
outside the internal admin dashboard.

## 9. Verification performed

- [x] Automated tests run via a fresh venv (`/tmp/spinr-venv`, this session had no pre-existing
  one): `backend/tests/test_admin_rides_coverage.py` + `test_admin_rides_read_endpoints_coverage.py`
  — 136 passed, 0 failed, includes both new regression tests.
- [x] Also ran `test_earnings_coverage.py` + `test_admin_module_list_parity.py` (adjacent
  surfaces) — 55 passed, 0 failed.
- [x] `ruff check` and `ruff format --check` on all 3 modified files — clean.
- [ ] Manual repro in staging — **not performed**; no staging environment available in this
  session (backend-only unit-test change).
- [x] Blast-radius grep performed — see section 4 above.
- [x] Reviewed against `CLAUDE.md` → "Money arithmetic" convention.
- [x] Feature-flag: not applicable — internal-admin-only display fix, additive-safe.
- **No `npm run build` applies** — Python backend-only change; `admin-dashboard` untouched.

## What was NOT verified

- Not tested against live Supabase/Stripe — all 4 endpoints exercised only via mocked
  `db_supabase.rpc` return values in unit tests.
- The dollar-value change only manifests on an exact `.5`-boundary Decimal input; no attempt was
  made to check whether any real historical payout/T4A aggregate has ever actually landed on such
  a boundary in production — this fix corrects the rounding *rule*, not a specific observed wrong
  number.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, justified above).
- [x] Blast radius stated, not assumed: isolated to 4 read-only admin display fields; no DB write,
  no Stripe call, no downstream consumer found by grep.
- [x] No silent behavior change to an already-shipped flow — value can change by at most $0.01,
  only on an exact `.5`-boundary input, presented above as a UX effect, not hidden.
