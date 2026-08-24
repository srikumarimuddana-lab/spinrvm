# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (see PR for session link) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-24-wallet-import-service-built.md`, which deliberately deferred this step |

## 1. Issue / gap identified

`services/wallet_import_service.py` (built earlier this session) had no HTTP
route — it was unreachable code. The user explicitly asked to wire it up now.

## 2. Root cause

Not a bug — a deliberate prior deferral. The service's CSV column-name
assumptions (`_id`/`customer_id`/`driver_id`/`amount`/`type`/`status`) were
inferred from sibling collections in the same Mongo export, never confirmed
against a real `wallets.csv` header row. Wiring an admin route on top of an
unverified schema was judged, at the time, to be compounding one assumption
with another. This step proceeds on explicit direction rather than resolving
that ambiguity itself — the column-name risk is unchanged and still
documented in the service module's own docstring and this log.

## 3. Fix / remediation

- Added `read_csv_text`/`_rows_from_reader` to `wallet_import_service.py`
  (copied from `booking_import_service.py`'s own helpers — each importer
  module owns its copy per this repo's established convention, no
  cross-import).
- New `backend/routes/admin/wallet_import.py`, mirroring
  `routes/admin/booking_import.py` structurally: `POST
  /api/admin/wallets/import/validate` (dry-run, no writes) and `POST
  /api/admin/wallets/import/commit` (writes only if the plan has zero
  errors). Takes three CSVs (wallets, customers, drivers) — customers/drivers
  supply the phone numbers `build_plan` needs to match each wallet row to an
  existing account.
- `_require_super_admin` re-check inside each handler (belt-and-braces,
  survives a future re-mount), same posture as every other money-writing
  admin importer in this repo.
- Two new rate limits in `utils/rate_limiter.py`
  (`wallet_import_validate_limit` = 30/hour, `wallet_import_commit_limit` =
  10/hour), same shape and reasoning as `booking_import_*_limit`.
- Router mounted in `routes/admin/__init__.py` right after
  `booking_import_router`, gated on `require_super_admin` at the mount too
  (defence in depth, matching every other money-writing admin sub-router).
- `commit_wallet_import` surfaces `applied`/`deduped`/`failed` counts per
  request (not just a bare "committed" flag) so an operator can see a
  partial-failure batch's shape immediately, and logs the same counts to the
  admin audit trail (counts only — no CSV contents, no PII).
- 13 new end-to-end tests (`test_admin_wallet_import.py`) using a fake
  Supabase client whose `.rpc()` implements a faithful-enough
  `wallet_apply_delta` (lock/dedup/floor semantics) — not just mocked
  call-argument assertions, but an actual simulated apply/dedup/floor-breach
  round trip through the real route → service → RPC-call path. All 13 pass,
  including an explicit resend-is-idempotent test and a
  driver-owned-entry-uses-`drivers.user_id` test.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same routes/tables:** `routes/admin/wallet.py`
  (prefix `/wallet`, singular) already owns `/wallet/{user_id}`,
  `/wallet/credit`, `/wallet/debit` — no path collision with this route's
  `/wallets/import/*` (plural). `wallet_apply_delta` gains a fifth caller
  (alongside admin credit/debit, the no-show fee, and the cancellation fee
  paths) with no change to the RPC itself. `require_super_admin` is
  unchanged; no new grantable module was added (this route is
  super-admin-gated only, same as `booking_import_router`, so
  `test_admin_module_list_parity.py`'s module-list needs no update — verified
  by running it, still 11/11 passing).
- **Could this regress a flow that currently works?** No currently-working
  flow is touched — this is a new route with no prior callers. The
  `wallet_apply_delta` RPC's four existing live call sites are unmodified.
- **Blast radius:** isolated — one new route file, two new rate-limit
  constants, one router-mount line, two small additions
  (`read_csv_text`/`_rows_from_reader`) to the already-reviewed
  `wallet_import_service.py`. `booking_import.py` was read for the pattern
  but not modified.
- **Interaction with background loops / ride state machine / money:** none
  with background loops or the ride state machine. Money-wise: this route is
  now the first *reachable* path that can move real rider/driver wallet
  balances from this importer — see "Not fully de-risked" below for what
  that means in practice, since the CSV-shape assumption is still unverified.

## 5. User-experience effect

An internal super-admin now has a route to run (previously nothing existed).
No rider/driver/corporate-admin sees anything — no UI page was built in this
change (the admin-dashboard frontend tool for this route does not exist yet;
this is the backend endpoint only). No mid-session visibility change for
anyone already using the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/wallet_import_service.py` | Added `read_csv_text`/`_rows_from_reader`; updated module docstring to document the now-wired route and restate the still-open column-name caveat | The route needs a CSV-text parser; the docstring must stay accurate about what's actually wired |
| `backend/routes/admin/wallet_import.py` | New file — `/validate` and `/commit` endpoints | The route this Change Impact Log is about |
| `backend/routes/admin/__init__.py` | Import + mount `wallet_import_router`, gated on `require_super_admin` | Wire the route into the admin app |
| `backend/utils/rate_limiter.py` | Added `wallet_import_validate_limit`/`wallet_import_commit_limit` | Every admin write/dry-run route in this codebase carries an explicit rate limit; matches `booking_import_*_limit`'s shape |
| `backend/tests/test_admin_wallet_import.py` | New file — 13 end-to-end route tests | Regression coverage for the new route, including a realistic `wallet_apply_delta` fake (lock/dedup/floor) |
| `docs/change-log/2026-08-24-wallet-import-admin-route-wired.md` | This file | Change Impact Log, mandatory for a money-writing admin route per CLAUDE.md |

## 7. Before / after

```python
# Before: wallet_import_service.py had no HTTP entry point at all.
# (no routes/admin/wallet_import.py existed)
```

```python
# After:
@router.post("/wallets/import/validate")
@wallet_import_validate_limit
async def validate_wallet_import(...):
    ...  # dry-run only

@router.post("/wallets/import/commit")
@wallet_import_commit_limit
async def commit_wallet_import(...):
    ...  # applies real wallet_apply_delta calls, only if plan.errors == []
```

## 8. Rollback plan

- **Code:** `git revert` — the route, rate limits, and router-mount line are
  additive; reverting removes the reachable path entirely with zero effect
  on the four existing `wallet_apply_delta` call sites.
- **Data, if this route is actually run against production:** each written
  `wallet_transactions` row carries `metadata.source =
  "legacy_mongo_wallet_import"` and `metadata.old_wallet_entry_id` (unchanged
  from the underlying service, already reviewed) — an incorrect batch can be
  identified and reversed with an equal-and-opposite `wallet_apply_delta`
  call per row, same as documented in the service's own Change Impact Log.
  No raw balance UPDATE is ever used, so this reversal path stays available
  regardless of what else has happened to the wallet since.

## 9. Verification performed

- [x] Automated tests run — unit + endpoint: `pytest
  backend/tests/test_admin_wallet_import.py backend/tests/test_wallet_import_service.py
  backend/tests/test_admin_booking_import.py backend/tests/test_booking_import_service.py
  backend/tests/test_wallet_repo.py backend/tests/test_wallet_apply_delta_contract.py
  backend/tests/test_wallet.py -q --no-cov` → 184 passed, 0 failed. Also ran the
  broader admin-routes/rate-limit test slice (146 passed, 2 skipped) and
  `test_admin_module_list_parity.py` (11/11) to confirm no RBAC drift.
- [x] Blast-radius grep performed — confirmed no path collision with
  `routes/admin/wallet.py`'s `/wallet/*` routes; confirmed
  `wallet_apply_delta`'s 4 existing call sites are unmodified; confirmed this
  route needs no new grantable admin module (super_admin-only, same as
  booking import).
- [x] Reviewed against relevant `CLAUDE.md` conventions — money function
  called only from the backend via the RPC (never a raw client call, `EXECUTE`
  already locked to `service_role`); "never silently swallow a money-path
  error" (per-row RPC failures are logged loudly inside `commit_plan` and
  surfaced in the response's `failed` count, and an unexpected whole-batch
  exception is caught, logged, and returned as a 502 rather than a
  half-committed 200); admin route follows the exact `require_super_admin`
  double-gate (router mount + per-handler re-check) every other
  money-writing admin route in this codebase uses.
- [ ] Manual repro steps followed in staging — **not done**, no live
  Supabase access from this session.
- [x] Feature-flagged — not applicable in the usual sense (this is an
  admin-only tool, not a user-facing feature), but the natural equivalent —
  super_admin gating — is in place at both the router mount and the handler.

## What was NOT verified

- **The CSV column-name assumption is still unverified.** This change makes
  the importer *reachable*, not *validated*. The first real `/validate` call
  against a real `wallets.csv` is still the first time the column-name
  inference gets tested — this route wiring does not resolve that risk, only
  makes it possible to discover (loudly, as plan errors, per the service's
  own fail-fast design) the moment someone actually runs it.
- **No real Supabase run.** All 13 new route tests exercise a hand-written
  fake `wallet_apply_delta` implementation (lock/dedup/floor logic
  reimplemented in Python for test purposes) — a faithful model of the real
  RPC's documented behavior (per migration 249 and
  `test_wallet_apply_delta_contract.py`), but not the real RPC running
  against real Postgres. RLS, the real `wallets_balance_non_negative` CHECK
  constraint, and any interaction with concurrent live traffic on the same
  wallet row are untested here.
- **No admin-dashboard frontend page exists** for this route yet — it's
  reachable only via direct API call (e.g. curl/Postman) until a UI is built,
  same bootstrapping state the booking importer likely had before its own
  frontend tool was built.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — `git revert` for code;
  per-row reversal via `wallet_apply_delta` for any data actually written.
- [x] Blast radius is stated, not assumed — see section 4, every existing
  caller/reader of the touched RPC and adjacent routes enumerated.
- [x] No silent behavior change to an already-shipped flow — this is new,
  previously-unreachable code becoming reachable; nothing already working
  changed behavior.
