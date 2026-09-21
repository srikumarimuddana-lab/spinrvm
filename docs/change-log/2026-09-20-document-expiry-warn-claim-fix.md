# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code (background investigation of a live production log finding) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers (regulatory-adjacent: document-expiry eligibility gate) |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/5537 (branch `claude/document-expiry-warn-claim-fix`) |
| Related issue or gap ID | Found live on Fly.io logs, repeating `Doc expiry: warn-claim failed` / `AttributeError: 'str' object has no attribute 'items'`; not previously tracked |

## 1. Issue / gap identified

The `document_expiry` background loop (12h cadence, `backend/core/lifespan.py` → `document_expiry_loop`, registered in `_WATCHDOG_LOOP_NAMES` as `"document_expiry (12h)"`) has been silently failing to send **any** "your document expires soon" push/email warning to drivers, on every tick, since this code was deployed. The failure was invisible: no error metric fired, the watchdog heartbeat recorded normally, and the only symptom was a repeating `warning`-level log line in production — `Doc expiry: warn-claim failed for driver <id>: Database operation failed`, with the underlying `AttributeError: 'str' object has no attribute 'items'` visible one line above it in `repositories._base.run_sync`'s own error log.

Only the **already-expired** branch (which suspends the driver) was unaffected — that CAS uses a normal `{"id": ..., "status": {"$ne": "suspended"}}` filter, not the broken one below.

## 2. Root cause

`check_expiring_documents()`'s warn-claim `update_one` call built its `$or` filter as:

```python
"$or": [f"doc_expiry_warned_at.is.null,doc_expiry_warned_at.lt.{cutoff}"]
```

— a list containing **one raw PostgREST-syntax string**. But `repositories/_base.py`'s `_apply_filters`/`_build_or_clause` (the shared PostgREST filter compiler used by every `db_supabase`/`db.update_one` caller in this repo, per CLAUDE.md's "Query filters" convention) expects `$or` to be a list of **Mongo-shaped `{column: predicate}` dicts**, and compiles each one itself:

```python
def _build_or_clause(clauses):
    for clause in clauses or []:
        for col, val in clause.items():   # <-- clause is a str here: no .items()
            ...
```

A `str` has no `.items()`, so this raised `AttributeError: 'str' object has no attribute 'items'` on every single tick, for every driver with a document expiring within the 7-day warning window (i.e. most ticks, once the fleet had any driver approaching an expiry). The exception propagated up through `repositories._base.run_sync` (logged there as `[DB] Supabase call failed (AttributeError): ...` and re-raised as a `DatabaseError`), into `document_expiry.py`'s own `except Exception as e: logger.warning(...); continue` around the claim call — which swallowed it and moved to the next driver. Because the loop's outer `try/except` in `document_expiry_loop()` never saw an exception (it was already caught one level in), `_had_error` stayed `False`, `spinr_bgloop_errors_total{loop="document_expiry"}` never incremented, and the watchdog heartbeat fired normally every tick. The bug was completely invisible except as a recurring `warning`-level log line — which is also why it was never elevated to an alert.

The correct pattern already exists elsewhere in this same file's neighbor, `backend/utils/driver_daily_rollup.py`:

```python
"$or": [
    {"ended_at": {"$notnull": False}},
    {"ended_at": {"$gte": win_start_iso}},
],
```

The document-expiry warn-claim code just didn't follow it — it looks like a leftover from writing the filter as a literal PostgREST clause string (perhaps copied from a raw SQL/PostgREST reference) rather than through the Mongo-shaped compiler this repo's DB layer actually expects.

## 3. Fix / remediation

1. Replaced the raw-string `$or` value with the Mongo-shaped equivalent (`{"doc_expiry_warned_at": {"$notnull": False}}` / `{"doc_expiry_warned_at": {"$lt": cutoff}}`), which `_build_or_clause` compiles to the **byte-identical** PostgREST clause (`doc_expiry_warned_at.is.null,doc_expiry_warned_at.lt.<cutoff>`) the original code intended — no behavior change to which rows match, only to whether the call succeeds.
2. While touching the try/except directly wrapping this call, also fixed its log level: it used `logger.warning(...)` with no `exc_info` on a DB write failure, which is the exact anti-pattern CLAUDE.md's "Do not silently swallow errors" section forbids ("Never `logger.warning(...)` and continue on a DB/auth/payment error") — and is precisely why this bug produced no error-level signal for as long as it did. Changed to `logger.error(..., exc_info=True)`, matching the already-correct sibling suspension-CAS except block four lines earlier in the same function (`Doc expiry: failed to suspend driver ...`). This surfaced during a manual application of the `spinr-observability-reviewer` checklist against the diff (see §9 — the Agent/Task tool to run it as an actual subagent was not available in this session, so its checklist was applied by hand).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one CAS filter inside `check_expiring_documents()`.** Grepped the whole backend for other `"$or"` usages passing a raw string instead of a Mongo-shaped dict list (`grep -rn '"\$or":\s*\[f["\x27]' backend/`) — this was the only occurrence anywhere in the codebase. The general `$or` compiler (`repositories/_base.py`) is untouched.
- **Other readers/writers of `doc_expiry_warned_at`:** `backend/routes/admin/drivers.py`'s admin "nudge driver" endpoint reads it for display (`last_nudged_at`) and writes it via a plain `{"id": driver_id}` filter (no `$or`) — unaffected by this change, filter shape is unrelated. Migration `45_drivers_doc_expiry_warned_at.sql` only defines the column; no logic there to affect.
- Once this CAS starts actually succeeding (it never had before), drivers with documents expiring within 7 days will start receiving push + email warnings again — this is the *intended*, previously-missing behavior, not a new side effect, but it does mean a burst of first-time notifications to any driver currently in the warning window whose claim has been silently failing. No rider-facing change; no ride-state, dispatch, fare, or wallet interaction.
- The log-level change (`warning` → `error`) only affects observability/alerting for *future, unrelated* DB failures on this same call (e.g. a real Supabase outage) — it does not change any user-visible behavior, and does not change what value is returned or whether the loop continues to the next driver.
- **Accepted gap (found by `spinr-observability-reviewer`, non-blocking):** the now-`error`-level log line is bridged to Sentry (`sentry_runtime.py`'s `LoggingIntegration(event_level="ERROR")`) but carries no `domain`/`driver_id` tag — it string-interpolates the ID into the message instead of passing `extra={...}`, and the codebase's `extra`→tag promotion helper (`tags_from_log_extra`) is only wired into the loguru bridge, not the stdlib one `before_send` actually uses. This is a pre-existing gap shared by this file's other `logger.error` calls (lines 125, 174, 257, 269, 387), not a regression this PR introduces — fixing it here would touch untouched call sites outside this fix's scope. Recommending a fast-follow ticket to add `extra={"domain": "drivers", "driver_id": ...}` across `document_expiry.py`'s error logs and to check whether `sentry_scrub.py`'s `before_send` should also call `tags_from_log_extra` for stdlib-logging-sourced events codebase-wide.

## 5. User-experience effect

- **Driver-facing.** Drivers with a license/insurance/inspection/background-check/work-eligibility document expiring within 7 days will resume receiving the "expires in N days" / "expires today" / "expires tomorrow" push notification and transactional email — which they were regulatorily supposed to be receiving all along per CLAUDE.md's Saskatchewan Regulatory section (document expiry blocks `go_online`), but silently were not.
- Not visible mid-session in the sense of an active ride being affected — this is an out-of-band notification loop, not something a driver is looking at live. No admin-dashboard, rider-app UI, or copy changes.
- No visual regression tooling applies (backend-only change, no UI touched).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/document_expiry.py` | `$or` filter in the warn-claim `update_one` call changed from a raw PostgREST string to a Mongo-shaped list of `{col: predicate}` dicts; the wrapping `except` block's log level changed from `logger.warning` to `logger.error(..., exc_info=True)` | Root-cause fix for the `AttributeError` that silently defeated every warn-claim CAS; log-level fix to match CLAUDE.md's error-handling rule and the sibling suspension-CAS pattern in the same function |
| `backend/tests/test_document_expiry_coverage.py` | Added `test_warn_claim_or_filter_is_mongo_shaped_not_raw_postgrest_string`, which runs `check_expiring_documents()` through the *real* `db.update_one` → `repositories._base.update_one` → `_apply_filters`/`_build_or_clause` chain (not a stubbed `update_one`) so the actual filter compiler is exercised | Regression test that reproduces the exact production `AttributeError` against pre-fix code and confirms the push/email fire post-fix; existing tests all stub `db.update_one` directly and so could never have caught this |

## 7. Before / after

```python
# Before
"$or": [f"doc_expiry_warned_at.is.null,doc_expiry_warned_at.lt.{cutoff}"],
...
except Exception as e:
    logger.warning(f"Doc expiry: warn-claim failed for driver {driver['id']}: {e}")
    continue
```

```python
# After
"$or": [
    {"doc_expiry_warned_at": {"$notnull": False}},
    {"doc_expiry_warned_at": {"$lt": cutoff}},
],
...
except Exception as e:
    logger.error(f"Doc expiry: warn-claim failed for driver {driver['id']}: {e}", exc_info=True)
    continue
```

## 8. Rollback plan

No feature flag / `app_settings` value gates this loop, and no migration is involved. This is a pure backend logic fix with no live-data mutation of its own (the `doc_expiry_warned_at` writes it now actually performs are the *intended*, previously-missing writes — not a new class of write). Rollback is a plain `git revert` of this commit followed by a normal redeploy; there is no Stripe charge, wallet delta, or ride-state change to remediate at the data level. If reverted, the loop returns to its current (silently broken) behavior — no new risk is introduced by rolling back.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_document_expiry.py tests/test_c_document_expiry_atomic.py tests/test_document_expiry_coverage.py tests/test_document_expiry_email.py tests/test_document_expiry_app_name.py tests/test_accept_ride_document_expiry.py` — 58 passed. Also ran `tests/test_loguru_call_conventions.py` (unaffected — this file uses stdlib `logging`, not loguru) — 8 passed.
- [x] Confirmed the new regression test actually reproduces the bug: temporarily reverted the fix locally, re-ran the new test alone, and confirmed it fails with the exact production symptom (`AttributeError: 'str' object has no attribute 'items'` inside `repositories._base.run_sync`, then the `Doc expiry: warn-claim failed` log line) before re-applying the fix.
- [x] `ruff check` and `ruff format --check` on both touched files — clean.
- [x] Blast-radius grep performed: `grep -rn '"\$or"' backend/` (22 files, all other usages already Mongo-shaped) and specifically `grep -rn '"\$or":\s*\[f["\x27]' backend/` (zero other matches — confirmed isolated to this one call site) and `grep -rn doc_expiry_warned_at backend/` (only the admin nudge endpoint and the defining migration touch this column elsewhere, both unaffected).
- [x] Reviewed against relevant CLAUDE.md conventions: the "Query filters" convention (this is exactly the bug class it documents), "Do not silently swallow errors", and the background-loop replay-safety section (the CAS semantics and throttle windows are unchanged — same compiled PostgREST clause as before, just now reachable).
- [x] **Real `spinr-observability-reviewer` subagent run** (superseding the manual pass originally noted here — the background session that authored this fix had no Agent/Task dispatcher available, so it applied the checklist by hand as a stopgap; the parent session subsequently ran the actual subagent against this diff). Verdict: **SAFE TO MERGE**. The reviewer independently re-derived the filter-compiler correctness (didn't just trust this doc's trace), built its own throwaway worktree, reverted the fix, reran the new test to confirm it fails with the exact production `AttributeError`, then confirmed it passes post-fix — genuine reproduction, not a re-read. It also swept the rest of `check_expiring_documents()` for the named mandatory "logger.warning-and-continue on a DB path" BLOCKER pattern and found no other instance. Two non-blocking warnings surfaced (Sentry tag gap on the new/sibling error logs — see §4's "Accepted gap" entry above; and a cosmetic mismatch between this doc's "Domain: drivers" metadata and the absence of an actual runtime tag), both recommended as a fast-follow, not a merge blocker.
- [ ] Not run against a real Supabase/PostgREST instance — verified via `mock_supabase_client` (real filter-compiler code path exercised, but the actual HTTP/PostgREST layer is mocked, not a live Supabase project).
- [ ] Not manually verified in staging (no staging repro attempted — this is a background-loop fix with full mocked-DB test coverage instead).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data-level remediation needed).
- [x] Blast radius is stated, not assumed (isolated to one CAS filter; only other column readers/writers identified and confirmed unaffected).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — §5 states the (previously-missing, now-restored) driver notification behavior explicitly.
