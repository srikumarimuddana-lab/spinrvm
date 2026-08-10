# Change Impact & Risk Log — Review fixes 5–7: display-field durability, public `escalate()`, no-op ledger writes

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| Related issue or gap ID | Findings 5, 6, 7 of the code review of PR #3464 |

## 1. Issue / gap identified

**(5) The Change-Card display fields lost atomicity under the atomic-settle RPC.**
On the legacy path `payment_method_id`, `card_brand: None`, `card_last4: None` ride the
*same* `update_ride` as the `payment_status → paid` flip. Under the flag-on RPC they
became a separate write after the money committed, wrapped in a single best-effort
attempt with a bare `except: log`. If it failed, the admin ride-detail view kept
showing the **rejected** card — the exact confusion those fields were added to prevent
(`payment_service.py:1238`, flagged as a Codex P1) — and nobody learned it had
happened.

**(6) `ledger_service._escalate` was called from two other modules.**
`utils/ledger_projection.py` (`ledger_legs_degraded`) and `services/payment_service.py`
(`settlement_state_unverifiable`) both raise their own tagged alerts through it. The
leading underscore advertised a privacy the function does not have.

**(7) An uninitialised Supabase client read as a successful ledger write.**
`db_supabase.insert_one` / `insert_many` return `None` / `[]` **without raising** when
the client was never initialised (`repositories/_base.py:768,788`). `_attempt_insert`'s
`await do_insert(); return True` therefore reported the 7-year CRA/SK tax-ledger row as
durably written when nothing reached a database. Because there is no exception, nothing
logged and nothing escalated — and the projection's counters told the same lie, counting
unwritten legs as `projected`.

## 2. Root cause

5. The RPC was introduced to make *money* writes atomic. The display fields were
   correctly identified as non-money and moved out of the transaction, but their new
   failure path inherited the old "best effort" treatment instead of the durable
   treatment the rest of this branch applies to derived state.
6. `_escalate` was private when it had one caller. It grew two more across module
   boundaries and the name was never revisited.
7. The retry helper was written against the failure mode it expected — an exception —
   and the CRUD layer's unconfigured-client contract is to return an empty result
   rather than raise. Nothing in between asserted that a write actually reached a DB.

## 3. Fix / remediation

- **`_write_display_fields(ride_id, fields)`** — bounded retry (3 attempts, 0.2/0.5s,
  matching the ledger writer's budget) then a **tagged escalation**,
  `spinr_alert=ride_card_display_stale`.

  **Deliberately NOT folded into `settle_ride_card_payment`**, even though that would
  make them atomic. These fields are a *display cache*: `card_brand`/`card_last4` are
  re-derived from the PaymentIntent and written back by
  `routes/admin/rides.py::_resolve_ride_card` whenever they are null
  (`rides.py:1270,1304`), and `payment_method_id` has no reader once the ride is paid —
  a paid ride is out of `payment_retry`'s scan set and will not be settled again. So the
  failure is a stale cache that self-heals. Widening a SECURITY DEFINER money function's
  signature — which means a new migration DROP/CREATE against an **already-applied,
  already-verified** function, plus re-verification — costs more than it prevents, and
  display state does not belong in a money transaction. The gap was never that it wasn't
  atomic; it was that failure was invisible.
- **`_escalate` → `escalate`**, with a docstring stating that the caller-supplied
  `alert` tag is the public contract. Pure rename: 3 internal + 2 cross-module call
  sites, 7 test patch targets.
- **`_client_unavailable()`** guard at the top of `_attempt_insert`. Returns `False`
  (failure) immediately rather than retrying — no number of attempts fixes an absent
  client, and the settlement request is holding. `record_event` then returns `None` and
  escalates `ledger_write_failed`; `write_legs` escalates `ledger_legs_lost`.

  It reads **`repositories._base.supabase`**, not `db_supabase.supabase`: `db_supabase`
  only re-exports the CRUD helpers, so `db_supabase.insert_one` *is*
  `_base.insert_one` and reads `_base`'s globals. Checking the re-export would test a
  binding the writer never consults — the same reason `tests/conftest.py` patches both
  spellings.

## 4. Risk & impact on existing functionality

**Blast radius: one new helper on one branch of `_finalize_card_settlement`, one
rename, one guard in the ledger retry loop.**

- **(5) Only reachable on the Change-Card path with the flag on.** `extra_ride_fields`
  is `None` on the capture-hold path and on every fresh charge without
  `payment_method_id_override`, so the common settle is unchanged. The retry adds up to
  ~0.7 s **only when the write is already failing**; the healthy path is one call, as
  before. Pinned by a test asserting exactly one `update_ride` when healthy.
- **(5) Cannot fail a settled payment.** `_write_display_fields` returns a bool that the
  caller ignores; the WS `payment_completed` still fires and the result is still
  `success=True`. Pinned by a test.
- **(5) PIPEDA:** the escalation context carries the ride id and the field *names* only
  — never the card id or brand. Asserted in the test (`"pm_new" not in str(ctx)`).
- **(6) Pure rename, no behaviour change.** Grepped for every reference before and
  after; `utils/safety_checkin_loop.py` has its own unrelated `_escalate` and is
  untouched.
- **(7) No production behaviour change.** Stated precisely — the first draft of this
  entry overstated it: `core/lifespan.py:25-30` **raises** on a falsy client when
  `ENV == production`, so Uvicorn refuses to serve and the branch is unreachable there.
  Below production it warns and boots anyway, deliberately, so local work without
  Supabase is possible. **Dev and staging are the real exposure** — which still matters,
  because staging is where the double-entry flags get exercised before they are trusted,
  and a run reporting every ledger write as successful while writing nothing produces
  false confidence. The `never raises` contract of
  `record_event` is unchanged: it still returns `None` rather than raising, so no caller
  sees a new exception (`payment_service` and `cancellation.py` both ignore the return;
  there is already a containment test that a `None` return does not block a cancel).
- **(7) Test-suite safety:** `conftest.patch_external_dependencies` is autouse and sets
  `repositories._base.supabase` to a MagicMock for every test, so the guard sees a
  configured client throughout. Verified by the full suite.
- Everything in (5) is behind `ledger_atomic_settle_enabled`; (7) is on the always-on
  header path but is a no-op whenever the client exists.

## 5. User-experience effect

- **(5)** No rider-facing change. Internal-admin: when the follow-up write now fails,
  the ride-detail card brand/last4 is the same stale value it would have been before —
  but on-call gets a `ride_card_display_stale` alert instead of silence, and a transient
  blip now self-corrects on retry where it previously did not.
- **(6), (7)** Nobody.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | `import asyncio`; `_write_display_fields` + `ALERT_CARD_DISPLAY_STALE`; RPC branch calls it | Finding 5 |
| `backend/services/ledger_service.py` | `_escalate` → `escalate`; `_client_unavailable()` guard in `_attempt_insert` | Findings 6, 7 |
| `backend/utils/ledger_projection.py` | `ledger_service.escalate(...)` | Finding 6 |
| `backend/tests/test_atomic_settle.py` | Replaced the best-effort test with 3: written-once, retry-then-succeed, exhausted-escalates | Finding 5 |
| `backend/tests/test_ledger_service.py` | +4 unconfigured-client tests | Finding 7 |
| `backend/tests/test_ledger_projection.py`, `test_replay_safety_payment_loops.py` | patch target rename | Finding 6 |

## 7. Before / after

```python
# Before (5) — one attempt, bare log; the stale-card state was invisible
try:
    await db_supabase.update_ride(ride_id, dict(extra_ride_fields))
except Exception:
    logger.opt(exception=True).error("[PAYMENT] display-field follow-up failed for ride {}", ride_id)
```

```python
# After (5) — bounded retry, then a taggable page
await _write_display_fields(ride_id, extra_ride_fields)
#   -> retries 3x, then escalate(alert="ride_card_display_stale")
#   Return value ignored: it must never fail a settled payment.
```

```python
# Before (7) — insert_one returns None without raising when unconfigured
async def _attempt_insert(do_insert, *, what: str) -> bool:
    for attempt in range(_INSERT_ATTEMPTS):
        try:
            await do_insert()
            return True        # <-- "written", though nothing reached a DB
```

```python
# After (7)
    if _client_unavailable():
        logger.error("[LEDGER] {} NOT WRITTEN — Supabase client is not initialised ...", what)
        return False           # -> caller escalates ledger_write_failed / ledger_legs_lost
```

## 8. Rollback plan

All three are code-only — no migration, no schema, no data, and nothing applied to a
database. `git revert` is a complete rollback here, which is *not* generally true on
this branch but is true for these three commits specifically: none of them writes,
migrates, or reinterprets a stored value.

Narrower levers if only part needs backing out:
- (5) is confined to the `ledger_atomic_settle_enabled = true` branch — setting that
  flag to `false` in `app_settings` (no deploy) bypasses `_write_display_fields`
  entirely and returns to the legacy single-`update_ride` path.
- (7) can be neutralised without a revert by fixing the environment it detects
  (`SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`), since the guard only fires when the
  client is genuinely absent.
- (6) has no runtime behaviour to roll back.

## 9. Verification performed

- **The 4 finding-7 tests were confirmed to FAIL against the pre-fix code** (guard
  temporarily removed via a scripted edit, run, then restored from a copy).
- Settle battery (`test_atomic_settle`, `test_settle_card_capture`,
  `test_process_payment_card`, `test_coverage_payments`, `test_ledger_service`,
  `test_ledger_projection`, `test_reconciliation`, `test_payment_retry`,
  `test_stripe_charge_coverage`) — **224 passed**.
- Blast-radius greps before acting: every `_escalate` reference across the repo (found
  the unrelated `safety_checkin_loop._escalate`, left alone); every reader of
  `card_brand`/`card_last4`/`payment_method_id` (found the self-healing admin resolver,
  which is what decided finding 5's approach); `conftest`'s supabase patch targets
  (which is what makes the finding-7 guard safe for 10k tests).
- `ruff check` + `ruff format --check` clean on all touched files.
- **Full backend suite run to completion BEFORE the push** — result in §11.

## 10. What was NOT verified

- **Nothing here has run against a real database or a real settle.** As with the rest of
  this branch, neither flag has been on end-to-end.
- **(5) The escalation has never fired for real.** `ride_card_display_stale` has no
  Sentry alert rule yet — it is a taggable signal, not a configured page. Creating the
  rule is a dashboard action outside this repo.
- **(5) The self-healing claim is read from the code, not observed.**
  `routes/admin/rides.py::_resolve_ride_card` re-deriving and writing back on null was
  verified by reading it; no test exercises the "follow-up failed, admin view later
  repairs it" sequence end to end.
- **(5) The retry's added latency is bounded by inspection** (≤ 0.7 s, and only when the
  write is already failing), not measured against the P95 fare-settlement SLA.
- **(7) The unconfigured-client path is exercised by patching `_base.supabase` to
  `None`**, not by actually starting the app without Supabase credentials. Whether a
  real failed init leaves that binding falsy — rather than, say, a client object that
  errors on use — was reasoned from `repositories/_base.py`, not reproduced.
- **(7) Only the ledger writer is guarded here.** Every other caller of
  `insert_one`/`insert_many` had the same silent-no-op exposure. That sweep was done as
  a follow-up — see `2026-08-08-crud-noop-write-visibility.md` — and it found a
  pre-existing documented decision at `repositories/_base.py:843` that had deferred
  exactly this work.
- Findings 1–4 from the review are fixed in earlier commits on this branch; there are no
  known open findings from that review after this commit.

## 11. Full suite result

`pytest backend/tests` run to completion **before** the push.

```
10056 passed, 8 skipped, 1 xfailed, 20 warnings in 470.42s (0:07:50)
```

Exit code 0, zero `FAILED`/`ERROR` lines. Baseline before these three commits was
**10,050 passed**, so the delta is **+6**, fully attributed: 4 new unconfigured-client
tests in `test_ledger_service.py`, and a net **+2** in `test_atomic_settle.py` (the
single best-effort display-extras test was replaced by three — written-once,
retry-then-succeed, exhausted-escalates). No pre-existing test changed state, which
matters most for the finding-6 rename: 7 test patch targets moved from `_escalate` to
`escalate`, and a missed one would have silently patched nothing rather than failing.
