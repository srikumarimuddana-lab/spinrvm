# Change Impact & Risk Log — Dispute-close ledger append is replay-safe

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/pickup-otp-payment-fixes-5a8dnk` |
| Related issue or gap ID | `docs/audit/2026-09-05-engineering-director-review-round3.md` §1.10 finding **N4** (Major, reporting) |

## 1. Issue / gap identified

`record_dispute_close_events` looped Stripe's `dispute.balance_transactions` and
called `ledger_service.record_event` unconditionally. `record_event` minted a
fresh `uuid4` for each row's primary key, and `financial_events` has no unique
constraint beyond that key — so nothing stopped the same money movement being
booked twice.

Replay is not hypothetical: stuck events are deliberately re-run through
`_dispatch_stripe_event` by the admin replay endpoint
(`routes/admin/stripe_events.py:65-77`, `webhooks.py:660-668`). A crash after
the ledger write and before the event was marked done therefore books the same
−$42.50 chargeback and −$15.00 dispute fee a second time. `financial_events` is
the 7-year tax record, so this is a reporting-integrity defect, not just noise.

## 2. Root cause

`record_event`'s `id` carried the comment *"client-supplied so a retry is
idempotent"*, which is true only of the **internal** retry inside
`_insert_with_retry` — the three attempts of one call share one id. It says
nothing about a *caller* re-run, where a new `uuid4` is generated up front. The
comment made the write look protected when only half of it was.

## 3. Fix / remediation

`record_event` gains an optional `dedupe_key`. When supplied, the row id becomes
`uuid5(_LEDGER_ID_NAMESPACE, dedupe_key)` instead of a random `uuid4`.
`record_dispute_close_events` passes
`f"stripe_dispute|{dispute_id}|{balance_transaction_id}"`.

Why a derived primary key rather than the unique index the audit also offered:
`financial_events.id` is **already** the table's PRIMARY KEY (migration 58), so
a deterministic id turns the constraint that already exists into the replay
guard — no migration, no schema risk, no index to backfill on a live table. And
`_is_duplicate_key` already treats the resulting 23505 as **success**
(`ledger_service.py:343-345`), so the replay silently no-ops exactly as intended
without any new error handling.

The Stripe balance-transaction id is globally unique and stable across
redeliveries, so it identifies the *money movement* rather than the attempt: the
chargeback and its separate fee row still produce two distinct ledger rows, while
the same balance transaction seen twice produces one.

A balance transaction with no `id` (should not happen — Stripe always sets one)
falls back to the previous random-id behaviour and logs at `error`, rather than
collapsing every such row onto one shared key, which would *lose* legitimate
rows.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend).**

- `record_event` — grepped for all callers. The new parameter is **optional and
  defaults to `None`**, and the `None` path is byte-identical to the previous
  behaviour (`str(uuid.uuid4())`). Every existing caller is therefore unchanged;
  an explicit test pins that two un-keyed calls still produce different ids.
- `record_dispute_close_events` — one caller, `routes/webhooks.py:1487`
  (`charge.dispute.closed`).
- `financial_events` — no schema change. Readers (`utils/ledger_projection.py`,
  reporting/T4A paths) see the same columns; only the *value* of `id` changes
  for dispute rows, and nothing derives meaning from an id's randomness.
- `write_legs` is keyed off the returned `event_id`, so legs inherit the same
  determinism and a replay no-ops there too.

Interactions considered:

- **Stripe idempotency** — unchanged; `claim_stripe_event` still gates upstream.
  This fixes the case where that gate is legitimately bypassed (admin replay).
- **Double-entry projection** — reads headers by id; a stable id is strictly
  better for it.
- **Money rules** — no arithmetic changed; `delta_cents` is still taken verbatim
  from Stripe's signed `amount`.

Regression risk: if two genuinely *different* money movements ever shared a
`(dispute_id, balance_transaction_id)` pair, the second would now be silently
swallowed as a duplicate. Stripe's balance-transaction ids are unique per
account, so this requires Stripe to violate its own contract — but it is the
failure mode to watch, and it is silent by design (duplicate = success).

**Known remaining gap (not fixed here):** only the dispute path is keyed. Other
`record_event` callers still use random ids and remain replay-unsafe if their
handlers are ever re-run. `_LEDGER_ID_NAMESPACE` and `derive_event_id` are
written to be reusable for those, but converting them is out of scope for this
change and each needs its own analysis of what identifies its money movement.

## 5. User-experience effect

**Nobody — backend/reporting only.** No rider, driver, corporate-admin or
internal-admin screen changes. No money moves as a result of this change; it
governs whether a *record* of money that already moved is written once or twice.
Internal admins running the Stripe-event replay will no longer double-book a
chargeback, which previously showed as a doubled negative on financial reports.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/ledger_service.py` | Adds `_LEDGER_ID_NAMESPACE`, `derive_event_id()`, and an optional `dedupe_key` on `record_event` | Let a caller make its ledger write idempotent across replays using the existing PK constraint |
| `backend/services/payment_service.py` | `record_dispute_close_events` derives a dedupe key per balance transaction; logs at `error` when one has no id | The replay path that actually books a chargeback twice |
| `backend/tests/test_dispute_ledger_replay_safety.py` | New | Pins per-transaction distinctness, replay stability, the no-id fallback, and that un-keyed callers keep random ids |

## 7. Before / after

```python
# Before — services/ledger_service.py
event_id = str(uuid.uuid4())   # fresh per CALL, so a caller re-run books again

# Before — services/payment_service.py
for bt in balance_transactions:
    await ledger_service.record_event(event_type="stripe_dispute", ref=dispute_id, ...)
```

```python
# After — services/ledger_service.py
event_id = derive_event_id(dedupe_key) if dedupe_key else str(uuid.uuid4())

# After — services/payment_service.py
bt_id = bt.get("id") or ""
_dedupe = f"stripe_dispute|{dispute_id}|{bt_id}" if bt_id else None
await ledger_service.record_event(..., dedupe_key=_dedupe)
```

Concrete scenario (gate 4 dry run) — dispute `dp_1` on a $42.50 ride closes
`lost`, Stripe posts two balance transactions; the handler crashes after the
ledger write, and an admin replays the event:

| | Before | After |
|---|---|---|
| First run | 2 rows: −$42.50, −$15.00 | 2 rows: −$42.50, −$15.00 |
| Replay | 2 **more** rows (new uuid4s) | 2 duplicate-key hits, treated as written |
| `financial_events` total | **−$115.00** | **−$57.50** |

## 8. Rollback plan

No migration, no schema change. A `git revert` is a complete rollback for future
writes.

Rows already double-booked by this bug are **not** removed by this change —
`financial_events` is append-only by design. They are identifiable as rows
sharing `(event_type='stripe_dispute', ref=<dispute_id>,
metadata->>'balance_transaction_id')` with more than one distinct `id`, and the
correction is a compensating `tax_adjust` entry rather than a delete, to keep the
7-year record intact. **That reconciliation is not performed here and is left for
a human to scope against production data.**

Note the rollback asymmetry worth flagging: after this ships, a derived id is
durable. Reverting the code does not re-randomise ids already written, and
re-applying it later is still safe — the namespace is fixed precisely so derived
ids never change. Do **not** change `_LEDGER_ID_NAMESPACE`; doing so
re-randomises every derived id and silently re-enables double-booking.

## 9. Verification performed

- [x] Blast-radius grep performed — `record_event` callers (all keep the
      unchanged `None` path), `record_dispute_close_events` (one caller),
      `financial_events` readers, `write_legs`.
- [x] Confirmed `financial_events.id` is `uuid PRIMARY KEY` (migration 58) so no
      new index is required, and that `_is_duplicate_key` already maps 23505 to
      success.
- [x] Confirmed `payment_service.py` uses **loguru**, so the new log line uses
      `{}` placeholders, not `%s` (CLAUDE.md / `test_loguru_call_conventions.py`).
- [x] Before/after money scenario written out above (gate 4).
- [x] `ruff check` and `ruff format --check` clean.
- [ ] **Automated tests NOT run** — see below.

## What was NOT verified

**No tests were executed.** PyPI is blocked by this environment's network policy
(403), so backend dependencies could not be installed and `pytest` could not run.
`backend/tests/test_dispute_ledger_replay_safety.py` is written but **has never
been run**. Existing ledger and dispute tests were not re-run either — and since
`record_event` is used across the money layer, the un-keyed-caller regression
test is the one that matters most and is unproven.

Also not verified: **the duplicate-key path was never exercised against a real
Postgres.** The claim that a second insert with the same derived id raises 23505
and that `_is_duplicate_key` matches Supabase's error shape for it is read from
the code, not observed — this is the single most important untested assumption
in this change, because if the error text does not match, the replay surfaces as
a lost ledger row (logged + escalated) instead of a silent no-op. An integration
test against the RLS tier's real Postgres would settle it. No production query
was run to size the already-double-booked set in §8.
