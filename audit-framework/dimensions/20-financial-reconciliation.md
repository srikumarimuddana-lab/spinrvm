# Dimension 20 — Financial Reconciliation

**Question:** Do fares collected, wallet balances, driver payouts, taxes owed, and Stripe records all agree at end of day — or is the ledger quietly drifting?

---

## Checklist

### Daily reconciliation (automated)
- [ ] Cron: fares-settled-today vs Stripe charges-captured-today → delta must be 0
- [ ] Cron: wallet-debit total vs ride-fare total paid-by-wallet → delta must be 0
- [ ] Cron: driver-payout total vs Stripe-Connect transfers → delta must be 0
- [ ] Cron: refund totals (DB) vs Stripe refund totals → delta must be 0
- [ ] Any non-zero delta pages on-call — reconciliation failure is sev-2 minimum

### Wallet integrity
- [ ] Every wallet delta flows through the `corporate_wallet_apply_delta` Postgres function (CLAUDE.md rule)
- [ ] No direct `UPDATE wallets SET balance = ...` anywhere in application code
- [ ] Idempotency key on every delta to prevent double-apply on retry
- [ ] Periodic re-derive: recompute wallet balance from delta history; compare to stored balance

### Tax ledger (CRA / SK-PST)
- [ ] GST 5% + SK-PST 6% line-itemised on receipt AND stored as separate columns
- [ ] Tax-collected totals per month exported in CRA-friendly format
- [ ] Driver T4A rows generated for ≥ $500/yr threshold
- [ ] BN-9 validation on corporate account creation (nine-digit format, Luhn check not required by CRA but recommended)
- [ ] GST/HST registration status stored per driver; reconciled against ≥$30k/yr threshold

### Payout accuracy
- [ ] Driver earnings = fare × (1 − platform_fee_percent) + tips — confirmed in settlement code
- [ ] Spinr's 0% commission model stored in `platform_fee_percent` — not hardcoded
- [ ] Driver net = gross − Stripe fee (Connect transfer cost passed through correctly)
- [ ] Held-amounts (failed KYC, document expiry) tracked in a separate ledger
- [ ] Failed-transfer auto-retry + alerting

### Dispute / chargeback accounting
- [ ] Every chargeback creates a reversing entry in the ledger (not just a Stripe webhook log)
- [ ] Won-dispute refunds tracked back to driver; loss covers accounting
- [ ] Reserve account for disputes visible on admin dashboard

### Corporate billing
- [ ] Corporate master-wallet and member-allowance wallets reconcile daily
- [ ] Member-allowance resets are audit-logged
- [ ] Invoice PDFs archived off-app; storage retention ≥ 7 years (CRA requirement)

### Audit trail
- [ ] Every financial event has a row in an append-only `financial_events` table (ride fare, refund, payout, chargeback, wallet delta, tax adjustment)
- [ ] Rows are immutable; schema enforces append-only via RLS + no UPDATE grant
- [ ] Retention: ≥ 7 years (CRA)

---

## Common Findings

- **"We don't reconcile daily"** — delta goes undetected for weeks; root-cause investigation impossible.
- **Wallet drift** — direct UPDATEs bypass the `corporate_wallet_apply_delta` function; two races cause lost writes.
- **Tax line not stored separately** — only a "total fare" column; CRA audit requires per-tax breakdown.
- **T4A generation is manual** — risk of missed filings after tax year close.
- **Platform fee hardcoded** — changing to any rate other than 0% requires a code release.
- **No reserve account view** — disputes eat driver payouts without clear accounting.

## How to Test

```bash
# Wallet delta function enforcement
grep -rn "corporate_wallet_apply_delta\|UPDATE.*wallets" backend/ --include="*.py" --include="*.sql"
# The UPDATE hits should be ZERO outside of the delta function definition.

# Tax column presence
grep -n "gst_amount\|pst_amount\|tax_" backend/schemas.py backend/migrations/*.sql | head

# Reconciliation jobs
grep -rn "reconcil\|daily_settlement\|ledger_check" backend/core/lifespan.py backend/utils/ 2>/dev/null

# T4A generation
grep -rn "t4a\|T4A\|tax_year" backend/routes/drivers.py backend/services/
```

## Regulatory tags
`CRA` (tax + T4A + retention) · `SK-PST` (line-itemisation on receipts) · `PCI-DSS` (Stripe reconciliation) · `AML` (FINTRAC reporting thresholds) · `SOC2` (audit trail immutability)
