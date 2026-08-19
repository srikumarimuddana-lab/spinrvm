# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com (via Claude) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | local worktree commit (not yet pushed) — see commit SHAs in task report |
| Related issue or gap ID | Audit finding N15, `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` |

## 1. Issue / gap identified

Two admin analytics/support endpoints rounded a Decimal-sourced money value with Python's
built-in `round()` instead of this codebase's own `_round()`/`to_decimal()` convention, so a
displayed dollar figure can disagree with what the rest of the app would show for the same
underlying value on a `.5`-boundary case.

## 2. Root cause

`backend/routes/admin/support.py` and `backend/routes/admin/rides.py` are **not** in the
`spinr-no-float-in-money` semgrep gate's `include` allowlist (`.semgrep/spinr-rules.yml`, see
also the gate's exclusion notes and `CLAUDE.md` → "Money arithmetic"). That gate only watches
`routes/fares.py`, `routes/payments.py`, `routes/wallet.py`, `routes/rides/payments.py`,
`services/fare_service.py`, `services/payment_service.py`,
`services/corporate_wallet_service.py`, `services/corporate_allowance_service.py`,
`services/corporate_subscription_service.py`, and `utils/stripe_charge.py`. Admin-only
analytics/support screens were never added to that list, so a bare `round(some_decimal, 2)`
slipped in without the gate (or a human reviewer primed to look for it) flagging it.

Python's built-in `round()` on a `Decimal` argument uses the active decimal context's rounding
mode, which defaults to `ROUND_HALF_EVEN` ("banker's rounding"). Every other money-rounding
helper in this codebase (`_round()` in `services/fare_service.py`,
`services/cancellation_service.py`, `services/payment_service.py`,
`utils/preauth_capture.py`, `routes/rides/_shared.py`, and the general-purpose
`utils/money.to_decimal()`) explicitly quantizes with `ROUND_HALF_UP`. On an exact `.5`-boundary
input the two conventions disagree.

## 3. Fix / remediation

Replaced both bare `round(<Decimal-derived value>, 2)` calls with
`backend.utils.money.to_decimal()`, the existing cross-cutting money helper (already imported
by `routes/admin/rides.py` for `dollars_to_cents`, and consumed by 12+ other backend modules —
see blast-radius note below) that quantizes to 2 decimal places with `ROUND_HALF_UP`, matching
`_round()`'s convention. The underlying aggregate/formula being rounded is unchanged in both
call sites — only the final rounding step changed. Both call sites still end in `float(...)` at
the JSON response boundary, exactly as before, so the response schema/type is unchanged (still
a JSON number).

**Note on the audit's cited line range for the `rides.py` finding:** N15 lists
`routes/admin/rides.py:2470-2492`. Inspecting that exact range (`_attribute_mrr`,
`cur_take_rate`/`prev_take_rate`, `cur_avg_fare`/`prev_avg_fare`) shows those `round(..., 2)`
calls operate on plain Python `float`s (already converted from Decimal a few lines earlier by
a local `_f()` closure at line 2393) — not on a `Decimal` object directly, so they don't match
the "round() directly on a Decimal" description. The two calls in this file that literally do
match — `round(float(Decimal(str(sr.get("gbv") or 0))), 2)` and the equivalent for
`net_revenue` — sit a few lines further down, at lines 2498 and 2500 (just past the cited
range, in the same function's "daily series" block). Given the severity (Low, display-only) and
the exact match to the finding's technical description, I fixed lines 2498/2500 rather than the
2470-2481 float-rounding calls, and am flagging this line-number discrepancy explicitly rather
than silently picking one interpretation. If N15 in fact intended the 2470-2481 block, that
block operates on floats (not Decimals) and is a different, lower-priority issue (float
`round()` still uses `ROUND_HALF_EVEN` in CPython, so the same `.5`-boundary divergence applies
there too, on percentages/averages rather than raw dollar amounts) — see "Other undetected
instances" below, where it's now also listed for follow-up triage.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two read-only, display-only response fields.**
  - `support.py`: `GET /api/admin/disputes/stats` → `total_refunded` field. Grepped
    `backend/routes/admin/support.py` and `backend/tests/` for any other reader of this
    response field or any write derived from it — none found. The endpoint only aggregates
    `disputes.refund_amount` for display; it does not write back to `disputes` or any other
    table, and does not feed Stripe, wallet, or settlement logic.
  - `rides.py`: `GET /api/admin/earnings/overview` → `daily_series[].gbv` /
    `daily_series[].net_revenue` (the line-chart data on the admin earnings dashboard). Grepped
    for other callers of `admin_get_earnings_overview` or consumers of `daily_series` in
    `backend/` and found none outside this route — it's a pure read/aggregate endpoint sourced
    from the `admin_earnings_daily_series` Postgres RPC, with no downstream write.
  - **Confirmed: neither call site's output is written to any DB column, used in a Stripe call,
    or otherwise consequential beyond rendering in the admin dashboard.** Both are purely
    rendered in an admin-only view (disputes stats card; earnings-overview line chart).
- **Shared-helper impact (`utils.money.to_decimal`)**: already imported by
  `services/stripe_payout_sync_service.py`, `services/legacy_payout_correction_service.py`,
  `services/legacy_gst_backfill_service.py`, `services/company_booking_service.py`,
  `services/booking_import_service.py`, `scripts/verify_restore.py`,
  `utils/stripe_charge.py`, `utils/earnings_snapshot.py`, `routes/drivers/__init__.py`,
  `routes/drivers/ride_complete.py`, `routes/drivers/_deps.py`. Adding two more call sites to
  an already widely-used, side-effect-free pure function (no shared mutable state, no I/O)
  carries no risk to those existing consumers.
- No interaction with any of the 16/18 background loops, the ride state machine, or a
  wallet/allowance delta path.
- The numeric *value* returned only changes for inputs that fall exactly on a `.5` rounding
  boundary at the 3rd decimal place (e.g. `x.xx5`) — an edge case, not a routine shift in every
  displayed number.

## 5. User-experience effect

- **Internal-admin-facing only.** No rider, driver, or corporate-admin surface reads either
  field.
- Not visible mid-session to anyone already using the rider/driver app — this is an
  admin-dashboard-only analytics/support screen, refreshed on page load/poll, not a live
  ride/payment flow.
- The only observable effect: on the rare `.5`-boundary input, the displayed dollar figure on
  the admin Disputes stats card or the Earnings-overview line chart may now differ from what an
  admin saw pre-fix by up to $0.01, and will now match what `_round()`/`to_decimal()` would
  compute for the same underlying settlement value elsewhere in the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/support.py` | `admin_get_dispute_stats()`: `float(round(total_refunded, 2))` → `float(to_decimal(total_refunded))`; added `to_decimal` import (dual-import pattern) | Match the codebase's `ROUND_HALF_UP` money-rounding convention instead of Python's default banker's-rounding `round()` on `Decimal` |
| `backend/routes/admin/rides.py` | `admin_get_earnings_overview()` daily-series loop: `round(float(Decimal(str(sr.get("gbv") or 0))), 2)` → `float(to_decimal(sr.get("gbv") or 0))`, and the equivalent for `net_revenue`; added `to_decimal` to the existing `utils.money` import (dual-import pattern) | Same — daily-series GBV/net-revenue chart values |
| `backend/tests/test_admin_support_routes.py` | Added `test_dispute_stats_total_refunded_uses_round_half_up` | Regression test pinning `ROUND_HALF_UP` behavior on a `.5`-boundary value (`"10.125"` → `10.13`, not the banker's-rounding `10.12`) |
| `backend/tests/test_admin_rides_coverage.py` | Added `test_get_earnings_overview_daily_series_uses_round_half_up` | Same, for the `daily_series` `gbv`/`net_revenue` fields |

## 7. Before / after

```python
# Before — backend/routes/admin/support.py:180 (admin_get_dispute_stats)
return {**counts, "total_refunded": float(round(total_refunded, 2))}

# total_refunded = Decimal("10.125") (e.g. two resolved refunds summing to
# an exact .5-boundary at the 3rd decimal place)
# round(Decimal("10.125"), 2) == Decimal("10.12")   (ROUND_HALF_EVEN — Python's
#                                                     default Decimal context)
# → API returns total_refunded: 10.12
```

```python
# After
return {**counts, "total_refunded": float(to_decimal(total_refunded))}

# to_decimal(Decimal("10.125"))
#   == Decimal("10.125").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
#   == Decimal("10.13")
# → API returns total_refunded: 10.13 — matches the codebase's own
#   ROUND_HALF_UP convention (services/fare_service.py `_round()`, etc.)
```

```python
# Before — backend/routes/admin/rides.py:2498/2500 (admin_get_earnings_overview)
"gbv": round(float(Decimal(str(sr.get("gbv") or 0))), 2),
"net_revenue": round(float(Decimal(str(sr.get("net_revenue") or 0))), 2),

# sr.get("gbv") == "10.125" → Decimal("10.125") → round(10.125, 2) == 10.12
# (float round() also defaults to round-half-to-even in CPython, and adds a
# second layer of imprecision by rounding a float rather than the Decimal)
```

```python
# After
"gbv": float(to_decimal(sr.get("gbv") or 0)),
"net_revenue": float(to_decimal(sr.get("net_revenue") or 0)),

# to_decimal("10.125") == Decimal("10.13") → float() → 10.13
```

## 8. Rollback plan

Pure code change, no migration, no feature flag, no data mutation. Revert is a plain
`git revert` of this commit (safe here specifically because nothing downstream of these two
read-only display fields was written to a table, charged via Stripe, or otherwise applied to
live data — the general CLAUDE.md caveat that `git revert` is not a rollback plan for
already-applied money/state changes does not apply to this diff). No redeploy-only concern
beyond the normal deploy pipeline; no `app_settings` flag needed since the change is not
user-visible outside the internal admin dashboard and carries negligible regression risk.

## 9. Verification performed

- [x] Automated tests run — unit/route tests via `/tmp/spinr-venv/bin/pytest`:
  - `backend/tests/test_admin_support_routes.py` (37 tests, all pass, includes new regression test)
  - `backend/tests/test_admin_rides_coverage.py` (91 tests, all pass, includes new regression test)
  - `backend/tests/test_admin_rides_read_endpoints_coverage.py` (all pass, unaffected)
  - `backend/tests/test_earnings_coverage.py` (all pass, unaffected)
  - Full combined run: 128 passed (support + rides coverage files), 88 passed (read-endpoints +
    earnings-coverage files) — 0 failures.
- [x] `ruff check` on all 4 modified files — 0 findings.
- [ ] Manual repro in staging — **not performed**; this is a backend-only unit-test change, no
  staging environment available in this session.
- [x] Blast-radius grep performed — see section 4 above (searched for other readers of
  `total_refunded` / `daily_series`, and for other consumers of `utils.money.to_decimal`).
- [x] Reviewed against `CLAUDE.md` → "Money arithmetic" convention.
- [x] Feature-flag: not applicable — internal-admin-only display fix, not user-visible,
  additive-safe (no state-machine, wallet, or Stripe interaction).
- **No `npm run build` / production frontend build applies** — this is a Python backend-only
  change; `admin-dashboard`/`rider-app`/`driver-app` are untouched.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, justified above as safe for this
  specific diff).
- [x] Blast radius is stated, not assumed: isolated to 2 read-only admin display fields; no DB
  write, no Stripe call, no downstream consumer found by grep.
- [x] No silent behavior change to an already-shipped *flow* — the numeric value shown can
  change by at most $0.01, and only on an exact `.5`-boundary input; this is presented above as
  a UX effect, not hidden.

---

## Repo-wide check for other undetected instances (per task request)

Grepped the whole backend for `round(` applied to a `Decimal`-typed value, outside the
`spinr-no-float-in-money` gate's protected paths (`routes/fares.py`, `routes/payments.py`,
`routes/wallet.py`, `routes/rides/payments.py`, `services/fare_service.py`,
`services/payment_service.py`, `services/corporate_wallet_service.py`,
`services/corporate_allowance_service.py`, `services/corporate_subscription_service.py`,
`utils/stripe_charge.py`):

```
grep -rn "round(.*Decimal(\|round(float(_agg_dec\|round(float(Decimal" backend/ \
  | grep -v tests/ | grep -v <protected paths>
```

**Found 4 additional, currently-unfixed instances — all in `backend/routes/admin/rides.py`,
all in admin-only display endpoints, none fixed in this PR:**

| Line | Endpoint | Value |
|---|---|---|
| 3031 | `/payouts/stuck-summary` (or similar; inside the stuck-ride/payout stats block) | `"amount": round(float(_agg_dec("stuck_amount")), 2)` |
| 3039 | same block | `"outstanding_balance": round(float(_agg_dec("blocked_outstanding")), 2)` |
| 3181 | T4A annual summary endpoint | `"ytd_gross_earnings": round(float(_agg_dec("t4a_ytd_gross")), 2)` |
| 3241 | `GET /payouts/stats` | `return round(float(Decimal(str(row.get(key) or 0))), 2)` (local `_d()` helper used for `total_paid`/`total_pending`/`total_failed`) |

`_agg_dec()` (defined at line 2897 in the same file) returns a `Decimal`, so all four follow the
identical bug pattern fixed in this PR. **Not fixed here** — out of this PR's stated 2-call-site
scope — but should be triaged as a fast follow using the same `to_decimal()` fix, since they're
the same low-severity, display-only class of bug in the same file. Recommend a single follow-up
PR sweeping all four plus re-checking the `cur_take_rate`/`prev_take_rate`/`cur_avg_fare`/
`prev_avg_fare` float-`round()` calls at rides.py:2477-2481 flagged above (those operate on
`float`, not `Decimal`, but CPython's `round()` on `float` also defaults to round-half-to-even,
so the same `.5`-boundary display mismatch risk applies to percentages/averages, just not
strictly "round() on Decimal").

I did **not** exhaustively grep every bare `round(` call in the repo (that pattern also matches
non-money uses — timestamps, distances, percentages — and a full audit of all of those is out
of scope here); this check was scoped to the `round(...Decimal(...)...)` / `round(float(<Decimal
call>))` shape that mirrors the two fixed bugs.
