# Change Impact & Risk Log — Silent no-op writes across the repository layer

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / rides / auth / corporate |
| Related issue or gap ID | Follow-up to finding 7 of the PR #3464 code review; completes the deferred change documented at `repositories/_base.py::update_one` |

## 1. Issue / gap identified

Finding 7 fixed `ledger_service` reporting a written tax-ledger row when the
Supabase client was absent. The stated boundary was that **only the ledger writer was
guarded** and every other `insert_one`/`insert_many` caller had the same exposure.

Auditing that turned up something better than a hunch: `repositories/_base.py::update_one`
already carried a NOTE saying the warn-and-continue was a swallowed DB error CLAUDE.md
forbids, that promoting it to a raise was *deliberately* not done because
`insert_many`, `insert_many_ignore_conflicts`, `delete_many` and
`driver_repo.claim_driver_atomic` swallowed identically, and that fixing one in
isolation would be a worse inconsistency — "tracked as its own change so all five move
together." That change had never happened. This is it.

The full audit of `if not supabase:` branches across all six repository modules found
**19 write helpers** that returned `None`/`[]`/`False` with no exception and, in 18 of
19 cases, no log at all. `update_one` logged at `warning`; `set_driver_available` logged
a `[GO-ONLINE]` warning. The rest were completely silent — including
`ride_repo.update_ride`, which is how every ride state transition is persisted.

## 2. Root cause

The empty-return-when-unconfigured contract exists so the app can boot for local work
without Supabase (`core/lifespan.py` warns and continues below production). That
affordance is reasonable; what was missing is that a helper choosing not to perform a
write never said so. A caller cannot distinguish "wrote nothing" from "wrote
successfully" when both return without raising.

## 3. Fix / remediation

- **`_base._write_skipped(op, table)`** — one shared logger at **ERROR** (CLAUDE.md:
  never `logger.warning` and continue on a DB error; a lost write *is* a DB error, it
  just arrives without an exception attached).
- Wired into all 19 write helpers across `_base`, `driver_repo`, `ride_repo`,
  `auth_repo`, `corporate_repo`, `wallet_repo` — including `rpc()`, which is how the
  money-moving Postgres functions are invoked.
- **`update_one`'s deferred NOTE replaced** with a statement of the completed change.
- **`test_noop_write_visibility.py`** — parameterized over all 19, so a new helper that
  swallows silently fails a test rather than shipping.

**Deliberately NOT promoted to a raise.** `core/lifespan.py:25-30` raises on a falsy
client when `ENV == production`, so Uvicorn refuses to serve and production cannot reach
these branches at all. Below production it warns and boots on purpose. Raising from
every write helper would destroy exactly that affordance while fixing a case production
cannot hit. Loud and honest beats fatal here; a path needing more than a log checks the
client itself, as `ledger_service` now does.

**Reads deliberately excluded** (`get_rows`, `count_documents`, `get_*`,
`find_nearby_drivers`): an empty read degrades visibly — no rides, no driver — rather
than silently claiming success, and logging every read would drown the signal in the
one environment where this can happen.

## 4. Risk & impact on existing functionality

**Blast radius: additive logging only. Not one return value, branch, or signature
changed.** Every helper returns exactly what it returned before.

- **The money paths were already safe, and this is worth recording precisely** because
  it shrinks how bad finding 7's "every other caller" boundary actually was:
  `wallet_repo` raises `DatabaseError` on all seven wallet/promo RPCs
  (`wallet_pay_for_ride`, `wallet_apply_delta`, `fare_split_pay_share`, …),
  `ledger_repo` raises `SettleRpcUnavailable`, and `claim_stripe_event` raises
  `RuntimeError`. Writes that already raised — `create_user`, `insert_otp_record`,
  `insert_ride`, `claim_ride_payment_processing`, `insert_corporate_account`,
  `create_flag`, `create_complaint`, `create_lost_and_found` — were left alone.
- **Production behaviour is unchanged**, because production cannot reach these branches
  (startup raises). The change is visible only in dev/staging.
- **Log volume:** in a misconfigured environment this now emits one ERROR per attempted
  write. That is loud by design and is the point — but it does mean a developer
  deliberately running without Supabase will see a stream of errors rather than silence.
  Accepted: silence was the defect.
- **PIPEDA:** `_write_skipped` takes only an operation name and a table/function name.
  A test pins the signature so a payload parameter cannot be added later — this fires on
  every write in a broken environment, so it must never be able to carry a row.
- **`claim_driver_atomic` returning `False`** is the safe direction (an unclaimed driver
  is simply not offered a ride), so dispatch degrades rather than double-offering. It
  logs anyway, for consistency and because it was one of the five the note named.

## 5. User-experience effect

Nobody. No rider, driver, corporate-admin or internal-admin surface changes, in any
environment. This is log output only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/_base.py` | `_write_skipped()`; wired into 6 helpers; `update_one`'s deferred NOTE resolved | The shared fix |
| `backend/repositories/driver_repo.py` | 6 helpers | incl. the 5th one the note named |
| `backend/repositories/ride_repo.py` | 3 helpers | `update_ride` is the ride state-machine writer |
| `backend/repositories/auth_repo.py` | 2 helpers | OTP verify/delete |
| `backend/repositories/corporate_repo.py` | 2 helpers | account update/delete |
| `backend/repositories/wallet_repo.py` | 3 helpers | the non-raising stragglers, incl. stripe-event bookkeeping |
| `backend/tests/test_noop_write_visibility.py` | **New.** 25 tests | Pin all 19 + the helper's level, message, and PII shape |

## 7. Before / after

```python
# Before — insert_one returns None; the caller cannot tell this from success
async def insert_one(table: str, doc: Dict[str, Any]):
    if not supabase:
        return None
```

```python
# After
async def insert_one(table: str, doc: Dict[str, Any]):
    if not supabase:
        _write_skipped("insert_one", table)
        return None
```

```python
# Before — update_one, the one helper that logged, and at the wrong level
        # NOTE: ... Tracked as its own change so all five move together.
        logger.warning(f"update_one({table}): supabase client is not configured — write skipped")
```

```python
# After — that coordinated change, done
        _write_skipped("update_one", table)
#   -> logger.error("update_one(rides): supabase client is not configured
#                    — WRITE SKIPPED, no data was persisted")
```

## 8. Rollback plan

`git revert` is a complete rollback: additive log statements plus one new test file, no
schema, no data, no migration, no behaviour. Nothing here is applied to a database and
nothing reinterprets a stored value.

If the log volume proves intolerable in a local no-Supabase workflow before a revert is
warranted, the narrower lever is a one-line change of `logger.error` to `logger.debug`
inside `_write_skipped` — one function, one line, all 19 call sites follow.

## 9. Verification performed

- **Scripted audit of every `if not supabase:` branch in `backend/repositories/`**
  before and after, classifying each as read or write and each write as raising,
  logging, or silent. Re-run after the change: 0 silent writes remain, and the only
  remaining silent branches are reads (deliberate).
- Repository-layer battery (`test_ledger_service`, `test_atomic_settle`,
  `test_reconciliation`, `test_ledger_projection`, `test_driver_repo_coverage`,
  `test_ride_repo`, `test_ride_repo_coverage`, `test_auth_repo`, `test_wallet_repo`,
  `test_corporate_repo_coverage`, `test_db_supabase_helpers`,
  `test_noop_write_visibility`) — **537 passed**.
- `ruff check` + `ruff format --check` clean across `backend/repositories/`.
- **Full backend suite run to completion BEFORE the push** — result in §11.

## 10. What was NOT verified

- **The new log line has never been observed firing.** It is exercised only by the unit
  test calling `_write_skipped` directly; no test boots the app without Supabase, and
  the suite's autouse `conftest` fixture patches a MagicMock client into every module,
  so the guarded branches are never taken during the suite. That is also why the change
  is safe, but it means "what it looks like in a real misconfigured environment" is
  reasoned, not seen.
- **The log-volume concern is not measured.** "One ERROR per attempted write" is derived
  from the call sites, not from running a workload against an unconfigured instance.
- **Only `backend/repositories/` was swept.** Modules that hold their own Supabase
  handle or call `supabase_client.supabase` directly were not audited; the sweep covers
  the shared CRUD layer that `db_supabase` re-exports, which is what the overwhelming
  majority of callers use.
- **No production or staging exposure was confirmed either way** — the claim that
  production cannot reach these branches rests on reading `core/lifespan.py`, not on
  observing a production boot with the client absent (which would, by that reading,
  refuse to serve).

## 11. Full suite result

`pytest backend/tests` run to completion **before** the push.

```
10081 passed, 8 skipped, 1 xfailed, 20 warnings in 506.16s (0:08:26)
```

Exit code 0, zero `FAILED`/`ERROR` lines. Baseline before this change was
**10,056 passed**, so the delta is **+25** — exactly `test_noop_write_visibility.py`,
which is 19 parameterized helper cases plus 6 covering `_write_skipped` itself. No
pre-existing test changed state.

### A flake this run exposed, and fixed

The **first** attempt at this run came back `2 failed, 10079 passed`, in
`test_sgi_field_maps.py` — nothing to do with this diff. The cause was a UTC-midnight
crossing: the test snapshotted the real date into a module constant at import and
compared it against `_today_iso()` called inside the mapper, so a run straddling
midnight had the constant saying yesterday and the mapper saying today. Confirmed by
re-running the file on unmodified code, where all 8 passed once both sides agreed on
the date.

Fixed rather than re-run past (CLAUDE.md release gate 8 — a gate that goes red on the
clock rather than on the code trains people to re-run instead of read): `_TODAY` is now
a pinned literal and an autouse fixture stubs `maps._today_iso`. Nothing about what the
tests assert changed. Committed separately (`2315d60`) so it is not buried in this
change.

Note the arithmetic ties out across both runs: 10,079 passed + 2 failed = 10,081, the
same total as the clean run.
