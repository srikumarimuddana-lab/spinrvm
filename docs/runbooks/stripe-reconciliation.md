# Runbook — Stripe ↔ DB ↔ Wallet Daily Reconciliation

**Owner:** `backend` + `finance` · **Cadence:** Daily cron (02:00 UTC)
**Regs:** PCI-DSS, SOC2 CC9.1, CRA record-keeping · **D20 dimension**

---

## Purpose

Detect any divergence between:
- Stripe's ledger (authoritative for card payments)
- Spinr's `financial_events` table (internal double-entry)
- Spinr's `wallets.balance_cents` sum

A divergence > $0.01 indicates a bug, a missed webhook, or fraud.

---

## Inputs

| Source | Query |
|---|---|
| Stripe | `stripe.PaymentIntent.list(created={gte: yesterday_00_00_UTC, lt: today_00_00_UTC})` — sum `amount_received` where `status == succeeded` |
| DB `financial_events` | `SELECT SUM(delta_cents) FROM financial_events WHERE created_at::date = yesterday AND event_type = 'stripe_charge'` |
| DB `wallets` | `SELECT SUM(balance_cents) FROM wallets` — daily snapshot vs yesterday's snapshot, net of wallet top-ups |

---

## Cron Job

```python
# backend/utils/reconciliation.py (create if absent)
# Scheduled via backend/core/lifespan.py background loop.

async def daily_reconciliation():
    yesterday = (datetime.utcnow() - timedelta(days=1)).date()

    stripe_total = sum_stripe_intents(yesterday)
    db_total = sum_financial_events(yesterday, event_type='stripe_charge')
    wallet_delta = wallet_balance_delta(yesterday)

    discrepancy = abs(stripe_total - db_total)

    if discrepancy > 1:   # > $0.01 in cents
        alert_finance(
            level='HIGH',
            stripe=stripe_total,
            db=db_total,
            delta=discrepancy,
            date=yesterday,
        )
        # File automatic OPEN-ITEMS-TRACKER row
    else:
        emit_heartbeat('reconciliation.ok', stripe_total=stripe_total)

    archive_report(yesterday, stripe_total, db_total, wallet_delta)
```

Runs at **02:00 UTC** (post-midnight Stripe settlement window, low-traffic).

---

## Alert Action (when discrepancy detected)

### Severity ladder
| Discrepancy | Severity | Action |
|---|---|---|
| $0.01–$1.00 | MEDIUM | Slack `#finance-alerts`; investigate same day |
| $1.01–$100 | HIGH | Page on-call + file HIGH item in OPEN-ITEMS-TRACKER |
| > $100 | CRITICAL | Page on-call + incident commander + CFO |

### Investigation steps
1. [ ] Check `stripe_events` table for any `unprocessed` rows — webhook delivery miss
2. [ ] Check for PaymentIntents in Stripe with status `requires_action` or `canceled` at day boundary (off-by-one)
3. [ ] Diff Stripe event list vs `stripe_events` table — missing events get replayed via `/stripe/events/replay?ids=...`
4. [ ] Check for out-of-band wallet adjustments (admin credits, refunds via dashboard)
5. [ ] Check for timezone boundary errors — always use UTC for the day window

---

## Monthly Close

At month-end, produce `reports/finance/YYYY-MM-reconciliation.md` containing:

- Daily discrepancy log (should be all zeros)
- Total Stripe gross vs DB gross
- Total refunds vs DB refunds
- Total wallet top-ups
- Total driver payouts (Stripe Connect) vs DB `driver_payouts` table
- GST/PST collected (for SK PST remittance) per `rides.gst_amount` + `rides.pst_amount`
- T4A YTD accumulation per driver (for CRA filing)

Signed by `finance` lead. Retained 7 years (CRA).

---

## Known Edge Cases

- **Stripe 3DS challenge retries** create multiple PaymentIntents — ensure only
  the `succeeded` one counts
- **Partial captures** — rare but possible; use `amount_received` not `amount`
- **Disputes / chargebacks** land as separate events up to 60 days later;
  reconcile in the month the dispute settles, not the original charge month
- **Stripe Connect payouts** settle T+2; cross-date boundary is expected
- **Refunds** — idempotency key prevents double-processing; verify `refunded` webhook landed

---

## Integration with DV-4 (Idempotency Key Fix)

Once DV-4 is remediated (UUID-based idempotency keys), reconciliation alerts
from idempotency-key collisions should drop to zero. Monitor the 7-day rolling
discrepancy rate before and after the fix as a regression signal.

---

## Failure Modes

- **Reconciliation cron didn't run** → absence of heartbeat log → SRE alert at 04:00 UTC
- **Stripe API rate limit** → retry with exponential backoff; escalate if > 1 h
- **DB read timeout on `financial_events`** → add index on `(created_at, event_type)` if not present

---

## SLAs

- **Daily discrepancy check**: zero open HIGH/CRITICAL discrepancy > 24 h
- **Monthly close**: signed by `finance` within 10 business days of month-end
- **Annual audit file**: T4A data correct per `reports/finance/YYYY-annual-t4a.md`
