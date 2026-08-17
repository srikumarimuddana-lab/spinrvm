# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | ACTION_ITEMS.md B27 |

## 1. Issue / gap identified

Three defects in `charge.dispute.closed`'s handler in `backend/routes/webhooks.py` (found 2026-08-14 while writing `docs/runbooks/payment-dispute-evidence.md`, no live chargeback has exercised the buggy paths yet):

1. `warning_closed` (an early-fraud-warning/inquiry that resolved without becoming a real chargeback) was treated as a loss, permanently marking a fully-paid ride `dispute_lost`.
2. The dispute row was looked up by `payment_intent_id` — not unique, and empty (`""`) when Stripe sends no PI — instead of `stripe_dispute_id`, the table's actual unique index. A PI-less close (or two disputes sharing one PI) could match and overwrite an unrelated dispute row.
3. The disputed-amount debit and Stripe's own per-dispute fee (`dispute.balance_transactions`) were never recorded to `financial_events`, leaving an unexplained delta in `docs/runbooks/stripe-reconciliation.md` for every chargeback.

Also missing: `charge.dispute.updated` wasn't in `_STRIPE_HANDLED_EVENTS`, so intermediate status transitions (`needs_response` → `under_review`) were invisible.

## 2. Root cause

The `charge.dispute.closed` handler was written keyed on `payment_intent_id` (mirroring the sibling `charge.dispute.created` handler's ride-lookup pattern) rather than the dispute's own `stripe_dispute_id`, which is the table's real unique constraint (`idx_stripe_disputes_dispute_id`, migration 88). The `won`/else-`dispute_lost` binary mapping never accounted for the third status Stripe actually sends (`warning_closed`). Ledger recording for refunds (`record_refund_event`) was added in an earlier pass (C3) but no equivalent was ever added for disputes.

## 3. Fix / remediation

- **Lookup keyed on `stripe_dispute_id`** (from `data_object.get("id")`, always present on a real Stripe dispute object) instead of `payment_intent_id`. Falls back to a `rides` lookup by `payment_intent_id` only when no `stripe_disputes` row is found (unchanged fallback behavior).
- **Status mapping inverted to fail toward `paid`, not `dispute_lost`**: `new_payment_status = "dispute_lost" if dispute_status == "lost" else "paid"`. Only an actual `lost` chargeback marks the ride lost; `won` and `warning_closed` both restore `paid` (the charge stands in both cases).
- **New `record_dispute_close_events` function** in `backend/services/payment_service.py`, mirroring `record_refund_event`'s existing pattern: one `financial_events` row per `dispute.balance_transactions` entry (Stripe's own signed `amount`, unmodified — no sign inference), `ref=stripe_dispute_id`, `fee_cents`/`balance_transaction_id`/`dispute_status` in metadata. Never raises (matches every other `record_*_event` function's never-fail posture — the money has already moved by webhook-delivery time).
- **`charge.dispute.updated` added to `_STRIPE_HANDLED_EVENTS`** with a minimal handler that mirrors the status onto the existing `stripe_disputes` row (status-only, no ride/ledger side effects — money-moving logic stays exclusively in `closed`).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one handler + the new ledger-write function.** Grepped the entire backend and every frontend surface (`admin-dashboard`, `rider-app`, `driver-app`, `shared`) for `dispute_lost` — the only occurrence anywhere is the write site itself (`webhooks.py`'s new mapping line and its own comments). No route, service, background loop, or frontend screen currently reads `rides.payment_status == 'dispute_lost'`, so there is no downstream consumer whose behavior could regress from the status-mapping change.
- **Who else reads/writes `stripe_disputes`**: `charge.dispute.created` (insert, unaffected), `charge.dispute.closed` (this fix), the new `charge.dispute.updated` handler (this fix), and the admin dispute-detail routes (`routes/disputes.py`, read-only — confirmed via `test_routes_disputes_coverage.py`/`test_disputes_admin_coverage.py`, neither writes the table).
- **`charge.dispute.created`'s insert already stamps `stripe_dispute_id`** (`dispute_id_stripe = data_object.get("id", "")`, present in the insert payload since this table's original implementation) — confirmed the new `closed`/`updated` lookups can actually find rows created by `.created`; this isn't a new column, just a switch to keying on the one that was always populated.
- **Idempotency**: `claim_stripe_event(event_id)` (called once, upstream of the whole `if/elif` dispatch chain in `stripe_webhook()`) already dedupes a full webhook redelivery before any handler runs — a retried `charge.dispute.closed` delivery never reaches this code twice, so the new `financial_events` writes don't need their own dedup key (same posture as the existing `record_refund_event`/`record_charge_event` writes, neither of which is separately deduped either).
- **Direction of change is corrective, not expansive**: the `won`/`warning_closed` fix only prevents a wrong write that was previously happening; the ledger write is additive (new rows on a table nothing currently reads for these events); the lookup-key fix only prevents a wrong-row update that was previously possible.

## 5. User-experience effect

None rider/driver-facing directly — this is a backend webhook handler. Indirect effect: a rider whose dispute resolves as `warning_closed` will no longer have their ride incorrectly show as a lost chargeback (previously silent, since nothing renders `dispute_lost` in any app UI today — confirmed via the blast-radius grep above — but it would have corrupted revenue/reconciliation reporting, which is an internal-admin-facing correctness issue).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | `charge.dispute.closed`: keyed lookup on `stripe_dispute_id`; `warning_closed`/`won` both restore `paid`, only `lost` sets `dispute_lost`; calls new `record_dispute_close_events` when `balance_transactions` present. Added `charge.dispute.updated` handler + allowlist entry. | Fix the three B27 defects; make intermediate dispute status visible |
| `backend/services/payment_service.py` | New `record_dispute_close_events()`, mirroring `record_refund_event`'s pattern | One `financial_events` row per Stripe balance transaction on dispute close |
| `backend/tests/test_routes_webhooks_coverage.py` | Existing 3 dispute-closed tests updated (dispute payloads now include `id`, rider-lookup mocked); 7 new tests: `warning_closed` regression pin, dispute-id-keyed lookup assertion, PI-less-close-updates-only-its-own-row, balance-transactions-recorded, no-balance-transactions-skips-ledger, no-rider-id-skips-ledger, plus a new `TestStripeWebhookDisputeUpdated` class (2 tests) | Regression coverage for all 3 fixes + the new handler |
| `backend/tests/test_webhooks_coverage_gap.py` | 2 existing dispute-closed tests updated (payloads now include `id`; rider-lookup mocked) | Same fix's data shape needed by this file's own dispute tests |
| `backend/tests/test_dispute_close_ledger.py` | New — direct unit coverage for `record_dispute_close_events` (literal `event_type`, zero-amount skip, falsy-`user_id` skip, non-dict-entry skip, never-raises), mirroring `test_refund_ledger.py`'s pattern | Money-auditor's finding needed a test that asserts on the actual INSERT payload, which the webhook-level mocked tests can't see |
| `docs/change-log/2026-08-17-b27-dispute-closed-webhook-fixes.md` | This file | Mandatory Change Impact Log |

## 7. Before / after

```python
# Before
elif event_type == "charge.dispute.closed":
    payment_intent_id = data_object.get("payment_intent") or ""
    dispute_status = data_object.get("status", "")

    existing = await db_supabase.find_one(
        "stripe_disputes",
        {"payment_intent_id": payment_intent_id},
    )
    ...
    if ride_id:
        new_payment_status = "paid" if dispute_status == "won" else "dispute_lost"
        ...
    # (no financial_events write)
```

```python
# After
elif event_type == "charge.dispute.closed":
    dispute_id_stripe = data_object.get("id", "")
    payment_intent_id = data_object.get("payment_intent") or ""
    dispute_status = data_object.get("status", "")

    existing = None
    if dispute_id_stripe:
        existing = await db_supabase.find_one(
            "stripe_disputes",
            {"stripe_dispute_id": dispute_id_stripe},
        )
    ...
    if ride_id:
        new_payment_status = "dispute_lost" if dispute_status == "lost" else "paid"
        ...

    balance_transactions = data_object.get("balance_transactions") or []
    if balance_transactions:
        await record_dispute_close_events(
            dispute_id=dispute_id_stripe,
            user_id=rider_id or "",
            ride_id=ride_id,
            balance_transactions=balance_transactions,
            dispute_status=dispute_status,
        )
```

## 8. Rollback plan

`git-revert-safe`. This is webhook-handler code only, additive on the ledger side (new `financial_events` rows — a revert simply stops writing new ones going forward, it doesn't need to un-write anything, since nothing downstream reads them yet per the blast-radius grep). Reverting restores the pre-fix `payment_intent_id` keying and `won`-only mapping — a regression to the known bugs described above, not a new failure mode. No Stripe-side state, migration, or schema change is involved.

## 9. Verification performed

- [x] `pytest backend/tests/test_routes_webhooks_coverage.py backend/tests/test_webhooks_main.py backend/tests/test_webhooks_coverage_gap.py backend/tests/test_p2_corporate_decimal.py backend/tests/test_ledger_service.py backend/tests/test_dispute_refund_cents.py backend/tests/test_disputes_admin_coverage.py backend/tests/test_p3_addresses_favorites_safety_disputes.py backend/tests/test_routes_disputes_coverage.py backend/tests/test_refund_ledger.py backend/tests/test_dispute_close_ledger.py -q --no-cov` — 261/261 pass.
- [x] `ruff check` + `ruff format --check` on every touched Python file — clean.
- [x] Blast-radius grep: `dispute_lost` occurs nowhere else in `backend/`, `admin-dashboard/`, `rider-app/`, `driver-app/`, or `shared/` outside the write site itself — confirmed no downstream reader could regress.
- [x] Confirmed `charge.dispute.created`'s insert already stamps `stripe_dispute_id`, so the new `stripe_dispute_id`-keyed lookup can find rows it creates.
- [x] `spinr-money-auditor` review requested for this diff before PR creation (Codex auto-review is off per CLAUDE.md C7/C9). Found 2 real blockers in the first version of item 3's ledger write, both fixed before merge — see §11.
- [ ] Not run against a real Stripe test-mode webhook or a live Supabase instance — no live chargeback has exercised any of these branches yet (per the original finding's own note); verified via unit tests with mocked `db_supabase` calls and hand-constructed Stripe event payloads matching the documented `dispute.balance_transactions` shape.

## 11. Money-auditor findings, both fixed before merge

`spinr-money-auditor` reviewed the staged diff (Codex auto-review is off per CLAUDE.md C7/C9, so a manual review is mandatory for anything money-touching). Verdict on the first pass: **"FIX BLOCKERS"** — items 1 and 2 (dispute-id keying, `warning_closed` classification) were confirmed correct and safe; item 3 (ledger write) had two real defects:

1. **CHECK-constraint violation, 100% of the time.** The first version used `event_type=f"stripe_dispute_{bt.get('type') or 'adjustment'}"` (e.g. `stripe_dispute_adjustment`). `financial_events.event_type` has a fixed CHECK-constraint enum (migration 58) — `'stripe_charge', 'stripe_refund', 'stripe_dispute', 'wallet_topup', 'wallet_debit', 'fare_settle', 'fare_split_debit', 'driver_payout', 'tax_adjust'`, no per-subtype dispute values. Every call would have failed the INSERT, exhausted `ledger_service`'s retry budget, and silently-but-loudly (Sentry-tagged `LEDGER WRITE FAILED`) never written the row — the whole point of item 3, non-functional in production. **Fixed:** `event_type` is now always the literal `"stripe_dispute"`; the Stripe balance-transaction `type` (`adjustment`/`stripe_fee`) moved into `metadata.balance_transaction_type`, still fully queryable. Verified via `test_writes_one_row_per_balance_transaction_with_literal_event_type`, which pins the literal value as a named regression case.
2. **FK-violation risk on `user_id`.** `financial_events.user_id` is `NOT NULL REFERENCES users(id)`; the original call site passed `rider_id or ""` — an empty string for a dispute whose ride/rider couldn't be resolved (e.g. a PI-less dispute) would fail the FK on top of the `event_type` bug. **Fixed two ways**: (a) `record_dispute_close_events` itself now no-ops with a logged warning when `user_id` is falsy, so the function can never attempt the FK-violating insert regardless of caller; (b) the webhook call site in `webhooks.py` now also guards on `if balance_transactions and rider_id:` before even importing/calling the function, avoiding wasted work on a call known in advance to be a no-op. Verified via `test_no_user_id_skips_write_entirely` (function-level) and `test_balance_transactions_present_but_no_rider_id_skips_ledger_call` (webhook-level).

Also added defensively per the audit's WARNINGS section (not blockers, but real robustness gaps): a non-`dict` entry in `balance_transactions` is now skipped rather than raising `AttributeError` (`isinstance(bt, dict)` guard — a malformed/non-dict entry previously would have turned into an uncaught 5xx and a stuck `stripe_events` row per this file's own retry-on-error convention), verified via `test_non_dict_balance_transaction_entry_skipped_not_raised`.

The auditor's idempotency note (whether a manual `stripe_events`-replay could theoretically double-write) was flagged as a pre-existing systemic property shared by every `record_*_event` function in this codebase (not a regression introduced here) — not fixed as part of this PR, consistent with how `record_refund_event`/`record_payment_event` already behave.

## What was NOT verified

- Not exercised against a real Stripe test-mode dispute (create → close a live test dispute in Stripe's dashboard) — the `balance_transactions` payload shape is taken from Stripe's public API docs, not observed live.
- Whether Stripe ever sends `charge.dispute.closed` with `balance_transactions` empty/absent for a `won` dispute where nothing was ever actually debited (funds only held, never captured) — the `if balance_transactions:` guard means this case correctly skips the ledger write, but this specific real-world payload shape wasn't observed, only reasoned about from Stripe's documented dispute lifecycle.
- `charge.dispute.funds_withdrawn`/`funds_reinstated` (mentioned in the original finding as "also missing") were deliberately left out of scope — the finding's own "Action" list only called for adding `charge.dispute.updated`; the other two are noted as an open follow-up if a real gap is found in production.
