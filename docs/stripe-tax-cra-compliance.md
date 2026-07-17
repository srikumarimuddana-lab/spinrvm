# Stripe Tax & CRA Compliance — Advisory

_Status: advisory / decision record. No code changes ship with this document._
_Scope: how (and whether) to use Stripe Tax for CRA bookkeeping, rider tax collection and
pass-through to drivers, Spinr Pass subscription tax, and driver T4A slips._
_Owner decisions baked in: keep the driver-remits model for ride fares; keep in-app T4A._

---

## TL;DR

Spinr already runs **two separate tax regimes with two different merchants of record**, and
that fact decides where Stripe Tax fits:

| Area | Merchant of record today | Verdict on Stripe Tax |
|---|---|---|
| Ride fares (GST 5%) | **The driver** — each driver is the CRA GST registrant and remits their own tax | **Do not use.** Keep the in-app per-area calculation and pass-through. |
| Spinr Pass subscriptions (GST 5% + PST 6% SK) | **Spinr** — sells the SaaS, remits its own GST/PST | **Use Stripe Tax here.** `automatic_tax` on Checkout + Stripe Tax registrations + Stripe's tax reports for filing. |
| Driver T4A slips | n/a (income reporting, not sales tax) | **Not a Stripe Tax feature.** Stripe's tax-forms product is US-1099 only; keep the in-app T4A pipeline. |
| CRA books | Mixed | **Hybrid ledger.** `financial_events` + the daily reconcile loops remain the system of record; Stripe Tax reports cover only Spinr's own subscription remittance. |

There is no single Stripe product that covers all four asks. The clean win is enabling
Stripe Tax on the Spinr Pass billing surface; everything else stays in-app by design.

---

## 1. Current state (what the code does today)

### 1.1 Ride-fare tax — driver is the merchant of record

- Tax is computed **in-app**, not by Stripe: `backend/features.py::calculate_all_fees`
  (tax block around `features.py:924-950`). Rates are per-service-area columns on
  `service_areas` (`gst_enabled`/`gst_rate` default 5%, `pst_enabled` default **off** for
  rides, `hst_*` for HST provinces) — `backend/sql/03_features.sql:40-46`. The in-code note
  is explicit: *Saskatchewan rideshare is GST 5% only; PST does not apply to rideshare*.
- The result is persisted on the ride: `rides.tax_amount` + `tax_breakdown` JSONB
  (migration `46_rides_fees_and_taxes.sql`) and shown as separate GST/PST line items on
  every receipt (`backend/utils/email_receipt.py`, `backend/utils/receipt_pdf.py`,
  `backend/routes/rides/receipts.py`).
- The collected tax is **passed through to the driver**: ride completion builds
  `rides.driver_earnings_snapshot` with the tax as a component line
  (`backend/utils/earnings_snapshot.py`), and the driver's total earned includes it.
- The driver **remits GST to CRA themselves**. This is enforced, not aspirational:
  `_require_gst_for_payout` (`backend/routes/drivers/payouts.py:526-548`) hard-blocks any
  payout until the driver has a valid 9-digit CRA Business Number on file. Rideshare
  drivers must register for GST/HST from their first fare — the $30k small-supplier
  exemption does not apply (Excise Tax Act "taxi business"). The T4A PDF repeats the
  obligation to the driver (`backend/utils/t4a_pdf.py:176`).

### 1.2 Spinr Pass subscription tax — Spinr is the merchant of record

- Spinr sells the driver-side "Spinr Pass" subscription (the monetization model — drivers
  keep 100% of fares). Tax on it is Spinr's own GST/PST liability.
- Computed in-app by `_compute_subscription_tax`
  (`backend/routes/drivers/subscriptions.py:237-268`) from
  `service_areas.subscription_tax_config` (migration 185; SK defaults GST 5% + PST 6%).
- Billed via **Stripe Checkout** — `mode="subscription"` for recurring plans (Stripe Price)
  and `mode="payment"` for one-off passes. Renewal tax is written onto
  `subscription_payments` (migration 186) by the `invoice.paid` webhook handler
  (`backend/routes/webhooks.py:1259-1301`). Branded PDF invoices are generated in-app
  (`backend/utils/subscription_invoice.py` / `subscription_invoice_pdf.py`).
- **`automatic_tax` is not used anywhere** — Stripe only ever sees a tax-inclusive total.

### 1.3 Stripe integration surface

- `stripe==15.1.0`, API version `2025-04-30.basil` (`backend/utils/stripe_config.py`);
  keys loaded per-call from the `app_settings` row (`backend/settings_loader.py`).
- Rider ride charges → **PaymentIntents** in CAD (`backend/utils/stripe_charge.py`).
- Driver payouts → **Connect Express** Transfers + instant Payouts
  (`backend/routes/drivers/payouts.py`); SIN is collected by Stripe during Connect
  onboarding for Income Tax Act Part XX reporting (Spinr never stores the raw SIN — only
  a boolean + last4 are mirrored via `backend/services/stripe_kyc_sync.py`).
- Subscriptions → **Checkout** (above).
- Webhooks → single endpoint `backend/routes/webhooks.py`, dual signing secrets
  (platform + Connect), idempotency via `claim_stripe_event` / `stripe_events` table.

### 1.4 T4A and the CRA audit trail

- **T4A annual job** (`backend/utils/t4a_annual_job.py`): runs the last day of February,
  Redis-leader-locked, finds drivers with ≥ $500 prior-year `rides.driver_earnings`
  (CRA Box 048 threshold) and notifies them their slip is ready.
- **On-demand T4A**: `GET /t4a/{year}` summary, `/t4a/{year}/pdf` CRA-style slip
  (Box 048 fees-for-services; Box 020 only when `gst_registered`), CRA-compatible CSV
  export, and email delivery — `backend/routes/drivers/tax_exports.py`,
  `backend/utils/t4a_pdf.py`. Driver GST fields (`gst_registered`, `gst_bn`) come from
  migration 58 plus Connect KYC sync.
- **Books / reconciliation**: refunds and settlements write to the `financial_events`
  ledger with explicit tax reversal amounts (`backend/services/payment_service.py:183-239`
  — on refund the driver keeps their pay, Spinr absorbs it, and `tax_reversed` is recorded
  so GST remittance nets out). Two daily 02:00 UTC loops reconcile Stripe ↔ DB ↔ wallet
  (`backend/utils/stripe_reconcile.py`, `backend/utils/reconciliation.py`) and write
  summaries to `audit_logs`. Receipts and trip records are retained 7 years (CRA + SK).
- **Admin surfacing**: Earnings dashboard shows GST/PST-collected metrics and a ride-level
  finance CSV export built for monthly closeout
  (`admin-dashboard/src/app/dashboard/earnings/page.tsx`,
  `backend/routes/admin/rides.py` finance export endpoint).

---

## 2. How Stripe Tax works — and why merchant of record decides everything

Stripe Tax does four things, all on behalf of **the Stripe account owner as the seller**:

1. **Calculation** — given a customer location and a product tax code, it computes the right
   GST/HST/PST/QST per line item at charge time (`automatic_tax: {enabled: true}` on
   Checkout/Invoices/Subscriptions, or the standalone Tax Calculation API for custom
   PaymentIntent flows).
2. **Collection** — the computed tax is added to the charge and recorded per-transaction.
3. **Reporting** — exportable reports of tax collected per registration/jurisdiction/period,
   aligned to filing needs (for Canada: GST/HST return lines, provincial PST).
4. **Registrations** — you tell Stripe where *you* are registered (e.g. Spinr's GST number,
   SK PST number) and it applies those registrations.

The critical constraint: **Stripe Tax assumes the platform is the merchant of record.** It
calculates and reports tax owed by *Spinr's* registrations. It has no concept of "this
charge's GST actually belongs to driver #4821's CRA Business Number." It cannot aggregate,
report, or file per-driver GST across thousands of individual registrants.

That maps directly onto Spinr's two regimes:

- **Ride fares**: the driver is the registrant → Stripe Tax is the wrong tool.
- **Spinr Pass**: Spinr is the registrant → Stripe Tax is exactly the right tool.

---

## 3. Area-by-area recommendation

### 3.1 Ride fares — keep the in-app calculation and driver pass-through (no Stripe Tax)

**Decision (confirmed):** keep the driver-remits model. This is the standard Canadian TNC
structure and the codebase already enforces it end-to-end (per-area tax config → ride
persistence → receipt line items → earnings snapshot → BN-gated payout → T4A note).

Why not Stripe Tax here:

- Enabling `automatic_tax` on ride PaymentIntents would make Stripe report that tax as
  **Spinr's** liability — directly contradicting the pass-through model and double-counting
  tax that drivers are already remitting.
- Stripe Tax reports would then be wrong for both parties: inflated for Spinr, absent for
  drivers.
- The in-app calculation is already correct, Decimal-exact, per-area configurable, and
  receipt-transparent (a regulatory requirement).

**Optional future use (calculation only):** if Spinr expands to provinces with messier
rules (HST provinces, QST), the Stripe **Tax Calculation API** could be used purely as a
*rate oracle* — compute, compare against the in-app engine, log divergence — without ever
enabling collection/reporting. Not needed for SK today; noted for the expansion roadmap.

**What "collecting from the customer and providing it to each driver" needs instead** is
bookkeeping hardening, not Stripe Tax (see §3.4 and the rollout in §4): a per-driver GST
statement so each driver can see exactly how much GST was collected on their fares per
period — the number they must remit. All inputs already exist
(`rides.tax_amount` per completed ride keyed to `driver_id`).

> **⚠ Finding — GST on platform fees is currently passed to the driver.** Verified in code:
> the taxable base is `subtotal + fees` (`backend/features.py:930`), which includes the
> booking fee and airport fee — but those fees are **Spinr's revenue**
> (`admin_earnings = booking + ap_fee`, `backend/services/fare_service.py:213`), while
> **100% of the computed tax** goes into the driver's earnings snapshot
> (`tax=ride.tax_amount` → `backend/utils/earnings_snapshot.py`). Net effect: GST collected
> on Spinr's own booking/airport-fee revenue is handed to the driver instead of retained
> for Spinr's remittance, and Spinr still owes CRA GST on that fee revenue. This may be a
> deliberate agency-model treatment (driver as supplier of the entire ride) — but it must
> be an explicit accountant decision, not an accident. If it's wrong, the fix is a tax
> split at settlement: driver receives GST on the fare portion; Spinr retains GST on
> booking/airport/area-fee portions. Flagged as Phase 2 prerequisite in §4 and open
> question #6 in §5.

### 3.2 Spinr Pass subscriptions — enable Stripe Tax (the clean fit)

Spinr is the seller here, so Stripe Tax does what it's built for. Recommended shape:

1. **Register** Spinr's GST/HST number and SK PST number in the Stripe Tax dashboard
   (Settings → Tax → Registrations). _Prerequisite: confirm with the accountant that SK PST
   applies to the Spinr Pass as a SaaS/service sale — the current in-app default charges it._
2. **Recurring plans** (`mode="subscription"` Checkout): add
   `automatic_tax: {enabled: true}` to the Checkout session and set a tax behavior +
   product tax code on the Stripe Price (tax-exclusive, so the receipt still shows tax as
   separate line items — a Spinr invariant). Stripe then computes GST/PST per renewal and
   itemizes it on the subscription invoice.
3. **One-off passes** (`mode="payment"` Checkout): same `automatic_tax` flag; drop the
   in-app pre-add of tax to `unit_amount` (currently `_compute_subscription_tax` inflates
   the charged total) so tax isn't double-charged.
4. **Webhook persistence**: `invoice.paid` / `payment_intent.succeeded` handlers read the
   Stripe-computed tax breakdown (`invoice.total_tax_amounts` / tax transaction) and write
   it to the existing `subscription_payments` tax columns (migration 186) — same columns,
   new source of truth.
5. **Keep `_compute_subscription_tax` as a reconciliation check**, not the charger: on each
   webhook, compare Stripe's tax to the in-app computation and log/alert on divergence
   (fits the existing daily-reconcile pattern). This preserves an independent audit trail
   and catches misconfigured registrations or tax codes.
6. **Filing**: use Stripe Tax's exportable reports for Spinr's own GST/PST returns on
   subscription revenue. The in-app `subscription_payments` ledger remains the 7-year book
   of record; the Stripe report is the filing convenience layer.

Touch points when this is built: `backend/routes/drivers/subscriptions.py` (Checkout
session creation), `backend/routes/webhooks.py` (tax persistence), Stripe dashboard
(registrations, price tax codes). In-app PDF invoices keep working — they already read the
persisted tax columns.

### 3.3 Driver T4A — keep in-app (Stripe cannot do this)

**Decision (confirmed):** Stripe's tax-forms product generates **US 1099s only**; there is
no Canadian T4A support. The existing pipeline stays:

- Annual job (last-day-of-Feb, ≥ $500 Box 048 threshold, Redis-locked) + on-demand
  PDF/CSV/email.
- Stripe's role in T4A is limited to what it already does well: **SIN collection** via
  Connect Express onboarding (Spinr never touches the raw SIN) and the payout-gating
  KYC mirror.

Hardening candidates for a later pass (not in this doc's scope to build):

- Verify the $500 Box 048 threshold annually against CRA guidance (per
  `.claude/context/regulatory-sk.md`).
- The annual job currently notifies but does not file — the electronic T619/T4A XML filing
  to CRA (mandatory e-filing above slip-count thresholds) is still a manual/external step.
  Worth a dedicated design when volume warrants it.
- Reconcile T4A totals against the `financial_events` ledger (refund rows record
  `driver_earnings_retained`, which affects what the driver actually earned).

### 3.4 CRA books — the hybrid ledger model

No single system covers the books; the recommended posture is explicit about which system
is authoritative for what:

| Ledger question | System of record | Notes |
|---|---|---|
| Tax collected per ride (GST on fares) | `rides.tax_amount` + `tax_breakdown` (7-yr retention) | Pass-through to driver; never in Stripe Tax reports |
| What each driver must remit | Derived: sum of `rides.tax_amount` by `driver_id` per period | **Gap today** — no per-driver GST statement is surfaced (§4 Phase 2), and the driver-attributable portion needs the §3.1 platform-fee ruling first |
| Spinr's own GST/PST on subscriptions | `subscription_payments` tax columns + Stripe Tax reports | Stripe report = filing layer; DB = audit layer |
| Refund/settlement adjustments | `financial_events` ledger | Already records `tax_reversed`, `driver_pay_absorbed_by_platform` |
| Stripe ↔ DB integrity | Daily reconcile loops → `audit_logs` | Extend, don't replace, when Stripe Tax lands |
| Driver income for T4A | `rides.driver_earnings` per calendar year | In-app job + exports |
| Monthly closeout / accountant handoff | Admin finance CSV export (`admin/rides.py`) | Candidate for a dedicated "Tax Reports" admin page later |

This hybrid keeps the CRA position clean: Spinr's returns cover subscription revenue
(backed by Stripe Tax reports + DB), drivers' returns cover fare GST (backed by the
per-driver statements), and every number traces to a 7-year in-app ledger.

---

## 4. Recommended phased rollout (future work)

**Phase 1 — Stripe Tax on Spinr Pass** (the §3.2 items): registrations, `automatic_tax` on
both Checkout modes, webhook tax persistence, reconciliation check. Small, contained,
highest value.

**Phase 2 — Per-driver GST statements**: a driver-facing (and admin) periodic statement of
GST collected on their fares — the amount they must remit. Data already exists; this is a
reporting feature (`tax_exports.py` is the natural home, alongside the earnings CSV).
_Prerequisite: resolve the §3.1 finding on GST-on-platform-fees first — the statement must
report the correct driver-attributable portion, which depends on that accountant decision._

**Phase 3 — Admin "Tax Reports" page**: consolidate the GST/PST metrics, subscription tax
totals, per-driver GST summaries, and closeout CSVs into one admin surface with period
filters — replacing the scattered Earnings-page metrics for accounting use.

**Phase 4 (conditional, expansion-triggered)** — Stripe Tax Calculation API as a rate
oracle for new provinces (HST/QST), compare-only against the in-app engine.

Each phase is an independent PR-sized effort following the repo's task-decomposition rules.

---

## 5. Open questions / sign-offs required before Phase 1

1. **SK PST on SaaS**: confirm with the accountant that the Spinr Pass is PST-taxable in SK
   (the in-app default says yes at 6%; Stripe Tax registration should match).
2. **Stripe Tax registrations**: needs Spinr's actual GST/HST number and SK PST number —
   finance/legal to enter these in the Stripe dashboard.
3. **Tax-inclusive vs. exclusive pricing** on existing Spinr Pass Prices: switching one-off
   passes from "tax pre-added to unit_amount" to `automatic_tax` changes what the driver
   sees at checkout — product sign-off on the displayed price.
4. **T4A threshold**: re-verify the $500 Box 048 threshold against current-year CRA
   guidance (annual chore per `regulatory-sk.md`).
5. **Historical rows**: `subscription_payments` rows predating migration 186 have NULL tax
   columns (legacy tax-free invoices) — confirm the accountant is comfortable with that
   boundary or wants a backfill note in the books.
6. **GST on platform fees (the §3.1 finding)**: accountant/legal to rule on whether the
   full ride tax passing to the driver — including GST attributable to Spinr's booking and
   airport fees — is the intended agency treatment, or whether a settlement-time tax split
   is required. This affects both parties' CRA returns and Phase 2's statement math.
7. **Instant-payout fee GST**: the 1.5% instant-payout fee (`payouts.py`) is Spinr service
   revenue — confirm whether GST must be charged on it (it currently isn't).

---

_References: `.claude/context/domain-payments.md`, `.claude/context/regulatory-sk.md`,
CLAUDE.md (money arithmetic, Stripe idempotency, retention). All file/line citations
resolve against the repo as of this commit._
