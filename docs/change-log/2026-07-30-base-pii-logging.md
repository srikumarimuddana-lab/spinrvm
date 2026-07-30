# Change Impact & Risk — PIPEDA log leak in the shared DB layer (T1)

**Date:** 2026-07-30 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Surface:** backend (shared repository layer) · **Risk:** high blast radius, low behavioural risk
**Related:** `docs/LAUNCH_GATE_IMPLEMENTATION_PLAN.md` (T1), PR #2877

---

## Issue / gap identified

`backend/repositories/_base.py` emitted personal information into stdout logs on two paths:
a `[GO-ONLINE]` debug block that logged the full write payload **and** the full returned row
for every write to `drivers`, and the catch-all DB error line that logged raw Postgres error
text for **every** table.

## Root cause

Two separate causes, both "the safe thing was never the default":

1. The `[GO-ONLINE]` block was temporary instrumentation added to chase a silent no-op write
   (a driver showing "online" while the DB row never flipped). It logged `payload=` and
   `res_data=` to see what PostgREST returned, was gated only on `if table == "drivers"` —
   no env flag, no log-level guard — and was never removed. It also became **redundant**:
   `routes/drivers/status.py:512-517,625-630,631-649` already logs the pre-write state, the
   post-write re-read, and the RLS/service-role diagnosis for exactly that failure mode.
2. The error path logged `str(exc)` verbatim. Postgres embeds column values in its error
   text, so a unique violation carried `Key (phone)=(+1306…)` and a CHECK/NOT NULL violation
   carried `Failing row contains (…)` — the entire row.

Contributing: the same bug was **already fixed in two sibling files** and the shared layer was
missed — `driver_repo.py:180-184` has the correct pattern with a PIPEDA comment, and
`ride_repo.py:225-226` has the payload-keys allowlist. And the pre-commit "PII in logs" check
(`.claude/hooks/pre-commit` step 3) is a six-pattern source-text denylist that cannot match a
runtime-interpolated payload, so it reported `✅ Clean` throughout.

## What actually leaked

Per `backend/migrations/32_encrypt_sensitive_fields.sql:11-15` and
`244_vehicle_vin_plaintext_at_rest.sql:3`, `drivers.license_number` holds a `vault.secrets`
UUID, **not** a plaintext licence number. The plaintext columns in the leaked row were:

| Field | Why it matters |
|---|---|
| `lat` / `lng` | Raw GPS — CLAUDE.md: "log geohashed area at most" |
| `name` | Full legal name |
| `phone` | Full phone number |
| `vehicle_vin` | Plaintext at rest since migration 244 |

Emitted at INFO to `sys.stderr` (`backend/server.py:420`), captured by the Fly.io and Railway
log aggregators.

## Fix / remediation

- Deleted both `[GO-ONLINE]` blocks. Replaced with one INFO line built by a new
  `_log_safe_write()` helper emitting `table`, filter **key names**, payload **key names**,
  `rows_updated`, and a precision-5 `geohash()` (~5 km cell) when the write carries
  coordinates. Copies the `driver_repo.py:180-184` pattern.
- Added `_redact_pg_error()`, applied **before** the string reaches either sink: the log line
  and `DatabaseError`/`DuplicateRecordError` `details["original"]`. Both are log sinks in
  practice — CLAUDE.md tells callers to log `e.details["original"]` when handling a
  `DatabaseError`. Keeps constraint and column names (schema, actionable); drops values.
- Narrowed the bad-payload `TypeError` to name the offending **type**, not `{update_data!r}`.
- Fixed a loguru `%s` call (`_base.py:483`) that silently dropped both its argument and its
  traceback (loguru uses `str.format` and `exception=`, not `%`-interpolation and `exc_info=`).

**Allowlist, not denylist.** A "scrub lat/lng" denylist is what let `name`/`phone`/`vin` leak
for as long as they did. Key-names-only means a new sensitive column on any table is safe here
by default.

## Risk & impact on existing functionality

**Blast radius — 28 `update_one("drivers", …)` call sites across 15 files** lose their verbose
log lines: `documents.py`, `features.py`, `routes/auth.py`, `routes/users.py`,
`routes/admin/drivers.py`, `routes/drivers/{location,profile,payouts,status}.py`,
`services/stripe_kyc_sync.py`, `services/stripe_mapping_import_service.py`,
`services/data_transfer/entity_import_service.py`, plus 3 test files. **No caller parses logs**,
so nothing functional depends on them.

`_redact_pg_error` affects **every** table's error path, not just `drivers` — that is the point,
but it is the widest-reaching part of this change. Verified no test or caller depends on raw
Postgres text: `test_db_supabase_helpers.py:527-543` asserts `"must be a dict"` and `"drivers"`
survive in `details["original"]`, and both still do.

**Deliberately NOT changed:** the `if not supabase: return None` swallow. It is a CLAUDE.md
violation (warn-and-continue on a DB error), but `insert_many`, `insert_many_ignore_conflicts`,
`delete_many`, and `driver_repo.claim_driver_atomic` all swallow identically. Fixing one in
isolation creates a worse inconsistency, so all five move together in a separate change. A
comment marks it in place.

## User experience effect

**None.** No rider-, driver-, corporate-admin-, or internal-admin-facing behaviour changes. No
API response shape changes. Nobody mid-ride or mid-session is affected. The only observable
difference is in backend log output, and the error `details` a support engineer sees now carries
`<values redacted>` where it previously carried row data.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/repositories/_base.py` | Deleted 2 `[GO-ONLINE]` blocks; added `_log_safe_write()` + `_redact_pg_error()`; narrowed `TypeError`; fixed loguru `%s` call; imported `utils.pii.geohash` | Stop emitting GPS, name, phone, VIN, and full Postgres rows to logs |
| `backend/tests/test_base_pii_logging.py` | New — 12 tests | Pin both leaks shut; mutation-verified |
| `docs/change-log/2026-07-30-base-pii-logging.md` | New — this file | Required by CLAUDE.md |

## Before / after

```python
# BEFORE — fired on every write to `drivers`
if table == "drivers":
    logger.info(
        f"[GO-ONLINE] db_supabase.update_one about to execute: "
        f"table={table} filters={filters} payload={update_data} upsert={upsert}"
    )
...
if table == "drivers":
    raw_data = getattr(res, "data", None) if res else None
    logger.info(f"[GO-ONLINE] db_supabase.update_one executed: ... res_data={raw_data}")

# AFTER — all tables, key names + outcome only
_rows = getattr(res, "data", None) or []
_n = len(_rows) if isinstance(_rows, list) else 0
logger.info(f"update_one executed: {_log_safe_write(table, filters, update_data)} rows_updated={_n}")
```

```python
# BEFORE — "Failing row contains (Jane Doe, +1306…, 52.13, -106.67)" straight to the log
exc_str = str(last_exc)
logger.error(f"[DB] Supabase call failed ({exc_name}): {exc_str}")
raise DatabaseError(details={"original": exc_str, ...})

# AFTER — redacted before both sinks
raw_exc_str = str(last_exc)
exc_str = _redact_pg_error(raw_exc_str)      # → '... violates check constraint "x" <values redacted>'
logger.error(f"[DB] Supabase call failed ({exc_name}): {exc_str}")
raise DatabaseError(details={"original": exc_str, ...})
```

Sample log line, before → after:

```
- [GO-ONLINE] db_supabase.update_one about to execute: table=drivers filters={'user_id': 'usr_1'} payload={'lat': 52.1332, 'lng': -106.67, 'heading': 91.0} upsert=False
- [GO-ONLINE] db_supabase.update_one executed: res_type=APIResponse res_data=[{'id': 'drv_1', 'name': 'Jane Doe', 'phone': '+13065551234', 'vehicle_vin': '1HGBH41JXMN109186', 'lat': 52.1332, 'lng': -106.67}]
+ update_one executed: table=drivers filter_keys=['user_id'] payload_keys=['heading', 'lat', 'lng'] geohash=cbqfx rows_updated=1
```

## Rollback plan

`git revert` is a genuine rollback here — the change is log-emission and exception-detail text
only. It applies no migration, writes no data, and alters no state, so there is nothing already
applied to live data to unwind. No feature flag needed.

The one consideration: reverting **restores the leak**, so it should only be done if the new log
line breaks a diagnostic need — and the fix for that is to add another allowlisted field, not to
revert.

## Verification performed

- **New tests:** 12 pass (`pytest tests/test_base_pii_logging.py --no-cov`).
- **Mutation-verified — this is the part that matters.** Reintroduced the leak in two ways and
  confirmed the tests fail: (a) restoring `payload=`/`res_data=` logging failed 2 tests;
  (b) bypassing `_redact_pg_error` at its call site failed 2 tests. The second mutation
  initially failed **nothing**, which exposed that the redaction tests only covered the helper
  in isolation — so `test_db_error_path_redacts_before_log_and_before_details` and
  `test_duplicate_key_path_redacts_the_conflicting_value` were added to drive a real failure
  through `run_sync` and cover the call site. Every negative assertion is paired with a positive
  anchor so it cannot pass vacuously.
- **Targeted regression:** 97 pass across `test_drivers.py`, `test_db_supabase_helpers.py`,
  `test_base_like_escape.py`, `test_db_circuit_breaker{,_probe}.py`,
  `test_insert_many_ignore_conflicts.py`, `test_health_db_ping.py`,
  `test_go_online_availability.py`.
- **Full suite:** `pytest -m "not slow"` → **5766 passed, 8 skipped, 1 xfailed, 1 failed**.
- **Lint:** `ruff check` clean on both files.
- **The 1 failure is pre-existing and unrelated:**
  `test_compliance_reports.py::TestInsurancePeriodRows::test_joins_driver_name` — a timestamp
  format mismatch (`2026-07-01 09:00 UTC` vs `2026-07-01T09:00:00Z`). Proven pre-existing by
  `git stash`-ing this change and re-running: it fails identically. Not fixed here (out of
  scope); needs its own `[CR]` per CLAUDE.md gate 8 rather than being left unexplained.

## What was NOT verified

- **No production build applies** — backend-only change, no `admin-dashboard`/`rider-app`/
  `driver-app` code touched, so `npm run build` is not applicable. Stated explicitly rather
  than omitted.
- **Not tested against live Supabase.** All DB interaction is via `mock_supabase_client`. The
  real shape of a PostgREST `APIError` string is therefore **inferred from Postgres error-message
  conventions, not observed** — `_redact_pg_error`'s patterns are tested against
  hand-constructed strings. A real error whose wording differs (e.g. a Supabase-specific
  wrapper prefix) could carry values past the two markers. This is the weakest link in the
  change and the reason T2 (a runtime guard at the log sink itself) is the actual containment.
- **No verification of what already reached production logs.** This change stops the leak going
  forward; it says nothing about historical exposure. That assessment is T4 and requires
  `git fetch --unshallow` (this checkout is shallow at 141 commits, so the leak's first-shipped
  date is not currently derivable) plus the Fly/Railway retention window, which is not knowable
  from the repo.
- **Sentry is a separate, still-open sink.** `include_local_variables` defaults to `True` and
  `utils/sentry_scrub.py` never scrubs stack-frame locals, so `capture_exception` still ships
  raw locals. Tracked as its own task; not addressed here.
- No load or latency measurement — the new helper runs per write, but it does `sorted()` over
  two small dicts and one geohash, so no benchmark was taken. T2's sink filter is the change
  that will need one.
