# Change Impact & Risk Log — Ledger durability + double-entry legs

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-06 |
| Author | Claude Code (agent), branch `claude/stripe-rider-payment-arch-o5vyes` |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/stripe-rider-payment-arch-o5vyes` |
| Related issue or gap ID | Gaps 1 + 2 in `docs/architecture/payments-rider-stripe.md` ("Known gaps") |

## 1. Issue / gap identified

Two defects found while tracing the rider Stripe path for the architecture diagram:

1. **The `financial_events` write was silently best-effort.** `record_payment_event` /
   `record_refund_event` wrapped the INSERT in `except Exception: logger.error(...); return`.
   A failed insert left a real, settled Stripe charge with **no ledger row** — so the 7-year
   CRA/SK tax record under-stated collected revenue and GST, with only a log line as evidence.
2. **The ledger was single-entry.** `financial_events.delta_cents` is a signed scalar with no
   contra-account, so the ledger cannot be balanced, cannot produce a trial balance, and cannot
   answer "how much do we owe drivers vs. CRA vs. keep" without re-deriving it from `rides`.

## 2. Root cause

1. Deliberate but wrong trade-off: the write is intentionally issued *before* the ride update so
   a recovery record survives a stuck `processing` row, and "never raise" was chosen so
   bookkeeping could not fail a settled charge. The never-raise half is correct; the
   **no-retry, no-escalation** half was not. It directly contradicts CLAUDE.md ("never
   `logger.warning(...)` and continue on a DB/auth/payment error").
2. `financial_events` (migration 58) was designed as an audit log, not a book of account. That
   was adequate for CRA record-keeping and is why it went unnoticed.

## 3. Fix / remediation

- New `backend/services/ledger_service.py` owns all ledger writes.
- The header insert now supplies a **client-generated primary key** and retries 3× with
  backoff. A duplicate-key error on retry means a prior attempt committed and its response was
  lost — that is treated as **success**, not failure. This is real idempotency, not blind retry.
- On exhausted retries: `logger.error` **plus** a tagged Sentry event
  (`domain=payments`, `surface=backend`) so an alert rule can page. Still never raises — the
  money has already moved and failing the request would show the rider an error for a charge
  that succeeded. Three distinct `spinr_alert` values so on-call can route by severity:

  | Tag | Meaning | Severity |
  |---|---|---|
  | `ledger_write_failed` | Header lost — a real charge with no row in the 7-year tax record | **High** — page |
  | `ledger_legs_lost` | Header written, legs missing — accounting overlay incomplete, tax record intact | Low |
  | `ledger_legs_unbalanced` | Leg builder produced a lopsided entry; nothing written | Low, but a code defect |
- New `financial_event_entries` table (migration 286) holds balanced debit/credit legs.
  `amount_cents` is always positive; direction is carried by `side`. Written **only** when the
  new `ledger_double_entry_enabled` app_settings flag is on (default **off**).
- Daily reconciliation now also alerts on any unbalanced journal entry.

## 4. Risk & impact on existing functionality

**Blast radius: cross-module within backend, but deliberately contained to two of five writers.**

Grepped `financial_events` across all of `backend/` (excluding tests). Full consumer list:

| Consumer | Role | Touched? |
|---|---|---|
| `services/payment_service.py:record_payment_event` | writer (charge) | **Yes** — delegates to ledger_service |
| `services/payment_service.py:record_refund_event` | writer (refund) | **Yes** — delegates to ledger_service |
| `routes/rides/cancellation.py:220` | writer (cancellation fee) | No — still direct `insert_one` |
| `routes/rides/cancellation.py:491` | writer (notice-window fee) | No — still direct `insert_one` |
| `routes/webhooks.py` (~:237, :800) | writer (webhook-side settle mirror) | No |
| `utils/reconciliation.py:_sum_financial_events` | **reader — sums `delta_cents`** | Additive check appended |
| `utils/retention_purge.py` / migration 216 | 7-year DSAR hard delete | No — new FK is `ON DELETE CASCADE` |
| `routes/admin/rides.py:1496` | comment reference only | No |

**The single largest risk, and how it was avoided:** `_sum_financial_events` filters by
`event_type` and sums `delta_cents`. Had the contra legs been added as extra rows *inside*
`financial_events`, that sum would have cancelled to ~zero and **silently broken the daily
Stripe reconciliation** — the exact control that catches lost charges. This is why the legs live
in a separate child table rather than as an `ALTER TABLE`. `financial_events` is byte-for-byte
unchanged in shape apart from the row now carrying an explicit `id` (previously DB-defaulted).

Other risks considered:

- **Latency.** Retries only fire on failure; the happy path is one insert as before. Worst case
  adds 0.7 s to a failing settlement (SLA: fare settlement P95 < 1 s). Acceptable — the
  alternative is losing the row.
- **Background loops.** None of the 18 startup loops write `financial_events`; the
  reconciliation loop only reads. The new balance check is appended after the existing
  Stripe-vs-DB comparison and returns early if migration 286 is absent, so an unmigrated
  environment logs an info line rather than failing the run.
- **Leg write failure does not endanger the header.** Legs are inserted after the header
  succeeds; a leg failure leaves the tax record intact.
- **Unbalanced legs are never half-written**, on two levels. Validated in-process first (the
  whole set is skipped + escalated if it does not balance), *and* the legs are inserted with a
  single batched `insert_many` rather than a per-row loop. A loop that failed on leg 3 of 4
  would have committed a lopsided journal entry — precisely the state the validation exists to
  prevent. One statement is one transaction: all legs land or none do. The
  `UNIQUE (event_id, account, side)` constraint makes a retry of that batch idempotent.

## 5. User-experience effect

**Nobody. Backend-only, no user-visible change.** No copy, no new validation, no response-shape
change. Riders, drivers, corporate admins and internal admins see identical behaviour. Nothing
is visible mid-session to a rider mid-ride or a driver online.

The one behaviour that *could* become visible is strictly an improvement: a settlement whose
ledger write fails now takes up to ~0.7 s longer before returning the same success response.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/286_financial_event_entries.sql` | **New.** Child table + unbalanced view + RLS + append-only trigger | Double-entry legs without touching `financial_events` |
| `backend/services/ledger_service.py` | **New.** Retry/escalate write path, chart of accounts, leg builders, balance validation | Single owner for ledger writes |
| `backend/services/payment_service.py` | `record_payment_event` / `record_refund_event` delegate to `ledger_service`; refund captures `tax_reversed` as a Decimal for leg building | Durability + legs |
| `backend/schemas.py` | `AppSettings.ledger_double_entry_enabled: bool = False` | DB-backed kill switch, no redeploy |
| `backend/utils/reconciliation.py` | `_check_entry_balance()` appended to the daily run | Detect unbalanced journals |
| `backend/tests/test_ledger_service.py` | **New.** 17 tests | Cover both defects |
| `backend/tests/test_coverage_rides.py` | Repointed 2 tests' patch target from `payment_service.db_supabase` to `ledger_service.db_supabase`; the swallow test now also asserts the retry count | The INSERT now issues from `ledger_service`, so the old target no longer intercepts (CLAUDE.md patch-target rule) |
| `docs/architecture/payments-rider-stripe.md` | Gaps 1 + 2 marked addressed, with what remains; `financial_event_entries` added to the ledger table list | Keep the diagram honest about current state |

## 7. Before / after

```python
# Before — one attempt, exception swallowed, charge silently unrecorded
try:
    await db_supabase.insert_one("financial_events", {...})   # no id supplied
except Exception as ledger_err:
    logger.opt(exception=True).error(
        "[PAYMENT] financial_events write failed for ride {} pi={}: {}", ...
    )
    # ← returns normally; caller never learns the tax record is missing
```

```python
# After — client-supplied PK makes the retry idempotent; exhaustion pages
await ledger_service.record_event(
    event_type="stripe_charge", user_id=user_id, ride_id=ride_id,
    delta_cents=amount_cents, ref=payment_intent_id, metadata=meta,
    legs=legs,          # written only when ledger_double_entry_enabled
)
# → 3 attempts; duplicate-key counts as written; on exhaustion:
#   logger.error + sentry capture_message(tags={"spinr_alert": "ledger_write_failed"})
#   returns None. Never raises.
```

Double-entry legs for a $20.00 charge ($15.00 driver, $2.20 tax):

```
DR stripe_receivable  2000
   CR driver_payable    1500
   CR tax_payable        220
   CR platform_revenue   280      debits == credits == 2000
```

## 8. Rollback plan

Three independent levers, none requiring a deploy:

1. **Legs misbehaving** → set `app_settings.ledger_double_entry_enabled = false` in the admin
   dashboard. Leg writes stop within the 60 s settings cache TTL. `financial_events` — the tax
   record and the input to daily reconciliation — is unaffected either way, and nothing reads
   the legs to make a money decision.
2. **Table itself problematic** → migration 286's header comment carries the drop SQL
   (`DROP VIEW financial_event_entries_unbalanced; DROP TABLE financial_event_entries;
   DROP FUNCTION _financial_event_entries_immutable();`). Safe because the table is write-only
   from the app's perspective.
3. **Retry behaviour problematic** → `git revert` is sufficient *for this specific change*,
   because the retry only adds attempts to an insert that previously happened once. No live data
   is mutated or migrated by the code path, so there is no data-level remediation to undo. Rows
   already written stay valid under the old code — the schema of `financial_events` did not
   change.

## 9. Verification performed

- [x] **Automated tests run (unit).** `backend/tests/test_ledger_service.py` — **17 passed**,
      covering: leg balancing, zero-value leg omission, inconsistent-amount refusal, refund
      sparing `driver_payable`, tax-over-refund clamping, retry-then-succeed, duplicate-key-as-
      success, exhausted-retry escalation without raising, PII-free Sentry context, flag off/on,
      unbalanced-legs skipped, header surviving leg failure, and an explicit assertion that all
      legs go out in exactly ONE statement (guarding the atomicity property above).
- [x] **Regression run on every touched consumer.** `test_refund_ledger.py`,
      `test_ledger_pii.py`, `test_reconciliation.py`, `test_cancellation_fee_card_charge.py`,
      `test_process_payment_card.py` — **44 passed**. This run caught a real regression: the
      formatter had stripped `ledger_service` from the non-package branch of the dual-import
      block, producing `NameError` on every card settlement. Fixed by folding the import into
      the existing `services` import in both branches.
- [x] **Targeted re-run after the final refactors** (batched leg insert + distinct alert tags):
      `test_ledger_service.py`, `test_coverage_rides.py`, `test_refund_ledger.py` —
      **189 passed**.
- [x] **Full backend suite — clean against the exact committed tree.**
      `pytest backend/tests` → **9,979 passed, 8 skipped, 1 xfailed, 0 failed** (exit 0, 7m55s).
      Two earlier full runs informed this: one against a pre-fix revision surfaced the
      `test_coverage_rides.py` patch-target regression noted above (since fixed); a second, run
      before the final leg-batching and alert-tag refactors, was also clean at 9,979 passed. The
      run recorded here post-dates every change in this diff.
      *Note: the commit message was written before this run finished and therefore states the
      suite was incomplete — this entry supersedes it.*
- [x] **Blast-radius grep performed.** `grep -rn "financial_events" backend/ --include=*.py`
      (all 8 non-test consumers enumerated in §4), plus the `financial_events_no_mutate` trigger
      and migration-216 delete path.
- [x] **Reviewed against CLAUDE.md conventions:** money (Decimal-only via `to_cents`, integer
      cents at the boundary), observability (structured `logger.error`, Sentry `domain`/`surface`
      tags), PIPEDA (Sentry context asserted by test to carry IDs and amounts only), migrations
      (append-only, new number 286, RLS in the same file, rollback in the header comment,
      indexes for the stated query patterns), error handling (no silent swallow).
- [x] **Feature-flagged.** `ledger_double_entry_enabled`, default off, DB-backed.
- [x] **Lint/format.** `ruff check` — all checks passed; `ruff format --check` — 5 files already
      formatted.

## 10. What was NOT verified

> **UPDATE 2026-08-08 — the database layer of this gap is now CLOSED.** Migrations 286–291 were applied to a real Postgres and `backend/scripts/verify_migrations_286_291.sql` passed all checks. See `docs/change-log/2026-08-08-migration-verification-result.md`.


Stated explicitly rather than left to silence:

- ~~**Migration 286 was never executed.**~~ **Applied and asserted 2026-08-08.** The
  semantics the pglast parse could not reach are now proven: the FK target resolves, the
  `ON DELETE CASCADE` takes legs with their header, the append-only trigger blocks UPDATE,
  the `unbalanced` view both ignores a balanced entry and catches a lopsided one, and every
  CHECK rejects what it should. The migration also gained a `REVOKE` block after the security
  audit — see `docs/change-log/2026-08-08-ledger-grant-lockdown.md`; the grants are asserted
  too. The flag may now be enabled on the strength of the DB layer; an end-to-end run with
  it on is still outstanding.
- ~~**The double-entry path has never executed against a real Postgres.**~~ **Corrected
  2026-08-08 — the DB layer is verified** (see
  `docs/change-log/2026-08-08-migration-verification-result.md`). What follows was the
  position before that run; the Python leg-writing path driving those constraints on real
  data is still unexercised. Originally: the `CHECK` constraints
  (`account IN (...)`, `amount_cents > 0`) and the `UNIQUE (event_id, account, side)` index are
  exercised only against mocked `insert_one` in tests. Agreement between the Python
  `LEDGER_ACCOUNTS` set and the SQL CHECK is maintained by hand, not enforced.
- **No end-to-end Stripe test.** All Stripe interaction is mocked; no live or test-mode charge
  was placed.
- **The `financial_event_entries_unbalanced` view was never queried** against real data — only
  the app-side guard that prevents unbalanced writes is tested.
- **Only 2 of 5 `financial_events` writers were migrated** to the durable path. The two
  cancellation-fee writers (`routes/rides/cancellation.py:220,491`) and the webhook-side writer
  (`routes/webhooks.py`) still use the old direct-insert-and-swallow pattern and remain exposed
  to defect #1. Deliberately out of scope to keep this diff reviewable; they should follow.
- **No load/latency measurement.** The retry's worst-case latency contribution is reasoned from
  the backoff constants (0.2 s + 0.5 s), not measured.

## 11. Adjacent findings (NOT fixed here)

Two pre-existing bugs found during the blast-radius check. Neither is touched by this change.

1. **The 7-year DSAR hard delete cannot work.** `purge_pii_retention` (migration 216, line ~196)
   runs `DELETE FROM financial_events WHERE user_id = v_uid`, but migration 58 installs a
   `BEFORE UPDATE OR DELETE` trigger that unconditionally `RAISE EXCEPTION`s. The function's
   handler catches only `foreign_key_violation`, so the raised `P0001` propagates and aborts the
   purge. PIPEDA/retention impact — worth its own ticket.
2. **Reconciliation breaks on month-end.** `_sum_financial_events`
   (`utils/reconciliation.py:155`) builds its upper bound as
   `datetime(date.year, date.month, date.day + 1)`, which raises `ValueError: day is out of
   range for month` on the last day of every month. The new `_check_entry_balance` deliberately
   uses `+ timedelta(days=1)` instead; the existing function was left alone because changing
   reconciliation windows needs its own risk assessment.
