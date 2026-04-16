# Corporate Accounts (B2B) — Design Spec
**Date:** 2026-04-15
**Status:** Approved (brainstorming phase)
**Scope:** v1 — Canadian market only, architected for regional expansion

---

## 1. Overview

Turn the existing corporate-accounts CRUD stub into a revenue-generating B2B product. Companies sign up, fund a prepaid master wallet, assign per-employee allowances, set policies, and let employees book work rides through the standard rider app. Designed for SMB + mid-market commute and business-travel use cases in Canada.

**Reference model blend:** Bolt Business product shape, Uber-style layered roadmap, Careem-style per-employee cost accounting, Lyft-Pass-style allowance requests.

### What this spec covers (v1)
- Company onboarding (self-serve ≤ 25 employees, sales-assisted above)
- Prepaid CAD master wallet with Stripe top-up + auto-top-up
- Per-employee allowances (fixed-recurring / one-time / unlimited) with "request more" flow
- Work Profile in the rider app (Personal ⇄ Work toggle)
- Company-wide policies (max fare, geofence, time windows, allowed payment source)
- Company-facing admin portal
- Monthly PDF statement + CSV exports with GST/HST/QST breakdown
- Manual KYB verification by the internal ops team
- Canadian tax compliance (GST/HST/QST), multi-locale (en-CA, fr-CA)

### Explicitly out of v1 (deferred to v2+, see §12)
- Admin-booked guest rides
- Monthly post-paid invoicing
- Groups / departments / cost centers
- Approval workflows (ride-by-ride)
- Per-employee policy overrides (only boolean bypass exists in v1)
- Purpose codes
- Voucher / Pass codes
- SSO, Concur/Expensify API, HRIS integration
- Anomaly detection, Finance role, weekly digests
- Cross-border rides, multi-currency per company

---

## 2. High-level architecture

Three surfaces in the codebase touch this feature:

1. **`backend/routes/corporate_accounts.py`** — existing file, split into three logical sub-routers:
   - `/admin/corporate-accounts/**` — super-admin endpoints (existing, expand)
   - `/company/**` — NEW: company admin & member endpoints, guarded by `requireCompanyAdmin(company_id)` / `requireCompanyMember(company_id)`
   - `/rider/work-profile/**` — NEW: rider-app Work Profile endpoints (balance, history, ask-for-more)

2. **`admin-dashboard/`**
   - `src/app/dashboard/corporate-accounts/**` — existing super-admin page, expand
   - `src/app/company/**` — NEW customer-facing company-admin portal, same Next.js app, separate auth scope

3. **`rider-app/`** — new Work Profile screen, Personal/Work toggle on home, booking-flow branch on active profile

**Driver-app is unchanged.** The driver never knows a ride is corporate.

### Ride-billing abstraction

A new `PaymentSource` concept is resolved at ride creation:

```json
{
  "type": "rider_wallet | rider_card | company_allowance",
  "company_id": "uuid | null",
  "member_id": "uuid | null"
}
```

The existing fare/payment flow branches on `type`. The driver-payout flow is unchanged.

---

## 3. Data model

All new tables use `UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `TIMESTAMPTZ` with `now()`, `NUMERIC(12,2)` for money (matches existing `wallets` convention), RLS enabled.

### Expand `corporate_accounts`
Add columns:
- `legal_name TEXT`
- `business_number TEXT` (CRA BN/GST number, validated against CRA BN format only — no live CRA API)
- `country_code TEXT DEFAULT 'CA'`
- `currency TEXT DEFAULT 'CAD'`
- `tax_region TEXT` — `ON | QC | BC | AB | SK | MB | NS | NB | NL | PE | YT | NT | NU`
- `timezone TEXT DEFAULT 'America/Toronto'`
- `locale TEXT DEFAULT 'en-CA'` (`en-CA | fr-CA`)
- `billing_email TEXT`
- `stripe_customer_id TEXT UNIQUE`
- `status TEXT` — `pending_verification | active | suspended | closed`
- `size_tier TEXT` — `smb | mid_market | enterprise` (drives signup path and CS touch)
- `kyb_document_url TEXT` (Supabase storage path to uploaded incorporation doc)
- `kyb_reviewed_at TIMESTAMPTZ`
- `kyb_reviewed_by UUID` (admin user id)

### `corporate_wallets` (new)
Master wallet — one row per company.
- `id, company_id UNIQUE, balance NUMERIC(12,2), currency TEXT`
- `auto_topup_enabled BOOL, auto_topup_threshold NUMERIC, auto_topup_amount NUMERIC`
- `auto_topup_daily_cap NUMERIC DEFAULT 5000.00`
- `low_balance_notified_at TIMESTAMPTZ NULL`
- `soft_negative_floor NUMERIC DEFAULT -50.00`
- `created_at, updated_at`

### `corporate_wallet_transactions` (new)
Append-only ledger for both master wallet and allowance movements.
- `id, wallet_id, scope TEXT` — `master` or `member:<uuid>`
- `type TEXT` — `topup | allowance_grant | allowance_reset | allowance_rollback | ride_debit | refund | adjustment`
- `amount NUMERIC(12,2)` (signed)
- `balance_after NUMERIC(12,2)`
- `ride_id UUID NULL`
- `member_id UUID NULL` — which employee (for allowance-scoped rows)
- `stripe_payment_intent_id TEXT NULL` — UNIQUE where not null, idempotency on Stripe webhooks
- `actor_user_id UUID NULL, notes TEXT, created_at`

### `corporate_members` (new)
- `id, company_id, user_id` — UNIQUE `(company_id, user_id)`
- `role TEXT` — `owner | admin | member`
- `status TEXT` — `invited | active | suspended | removed`
- `invited_email TEXT, invite_token TEXT, invited_at, joined_at, invited_by UUID`
- `policy_override BOOL DEFAULT false` (executive bypass flag)
- `created_at, updated_at`

### `corporate_member_allowances` (new)
- `id, member_id UNIQUE` (one active allowance per member)
- `type TEXT` — `fixed_recurring | one_time | unlimited`
- `amount NUMERIC(12,2)` (period cap for fixed; grant size for one_time; NULL for unlimited)
- `used NUMERIC(12,2) DEFAULT 0`
- `period_start DATE NULL, period_end DATE NULL` — NULL for unlimited
- `rollover BOOL DEFAULT false`
- `auto_approve_topup_amount NUMERIC NULL` — cap per auto-approved request
- `auto_approve_monthly_count INT NULL` — monthly count of auto-approved requests
- `auto_approved_this_period INT DEFAULT 0`
- `status TEXT` — `active | paused | expired`
- `created_at, updated_at`

### `corporate_allowance_requests` (new)
- `id, member_id, amount NUMERIC(12,2), reason TEXT`
- `status TEXT` — `pending | approved | denied | auto_approved`
- `reviewed_by UUID NULL, reviewed_at TIMESTAMPTZ NULL, decision_notes TEXT`
- `created_at`
- Rate-limit: employee cannot create a new pending request while one is pending.

### `corporate_policies` (new)
- `id, company_id UNIQUE, active BOOL`
- `max_fare_per_ride NUMERIC(12,2) NULL`
- `allowed_geofence JSONB NULL` — GeoJSON FeatureCollection of polygons; pickup AND dropoff must be inside one polygon
- `allowed_time_windows JSONB NULL` — `[{day:"mon", start:"09:00", end:"19:00"}]`, company timezone
- `allowed_payment_source TEXT DEFAULT 'both'` — `allowance_only | master_only | both`
- `tip_billed_to TEXT DEFAULT 'rider_card'` — locked at `rider_card` in v1; column exists for v2 config
- `created_at, updated_at`

### `corporate_allowed_domains` (new)
- `id, company_id, domain TEXT` — UNIQUE `(company_id, domain)` — lowercased, no `@`

### `ride_payment_sources` (new)
- `ride_id UUID PRIMARY KEY` (FK → rides)
- `source_type TEXT` — `rider_wallet | rider_card | company_allowance`
- `company_id UUID NULL, member_id UUID NULL`
- `allowance_debit_amount NUMERIC(12,2) DEFAULT 0`
- `master_fallback_amount NUMERIC(12,2) DEFAULT 0`
- `policy_check_result TEXT` — `pass | fail | override`
- `policy_failed_rules JSONB NULL`
- `created_at`

### `corporate_policy_evaluations` (new)
- `id, ride_id, company_id, result TEXT` — `pass | fail`
- `failed_rules JSONB NULL, evaluated_at, phase TEXT` — `booking | completion`

### Indexes
- `corporate_members (user_id, status)` — hot path, lookup on every ride request
- `corporate_wallet_transactions (wallet_id, created_at DESC)` — statements
- `corporate_wallet_transactions (stripe_payment_intent_id) WHERE stripe_payment_intent_id IS NOT NULL, UNIQUE` — webhook idempotency
- `corporate_accounts (stripe_customer_id)` — webhook reverse lookup
- `corporate_allowed_domains (domain)` — rider app "am I auto-eligible?" lookup
- `ride_payment_sources (company_id, created_at DESC)`, `ride_payment_sources (member_id, created_at DESC)`
- `corporate_allowance_requests (member_id, status, created_at DESC)`

### RLS
Every new table enables RLS. Baseline policies:
- Super-admin full access (matches existing pattern in `17_corporate_accounts_fk.sql`).
- Company members see rows where `company_id = current_user.company_id`.
- Members see only their own `corporate_member_allowances` row and their own `corporate_allowance_requests`.
- Admins/owners see all members & allowances & requests within their company.

---

## 4. Wallet & billing flow

### Top-up (card only in v1, no ACH)
1. Admin clicks **Top up** in the portal → selects CAD amount (min $100, max $10,000).
2. Backend creates a Stripe `PaymentIntent` with `customer=stripe_customer_id`, stores the `payment_intent_id` on a `corporate_wallet_transactions` row with `status=pending` (actually we rely on the webhook to insert the authoritative row).
3. Stripe Checkout / Payment Element completes the charge.
4. Webhook `payment_intent.succeeded` handler:
   - `SELECT ... FOR UPDATE` on the wallet row.
   - Insert `corporate_wallet_transactions` (`type=topup`, `scope=master`, `stripe_payment_intent_id` UNIQUE → idempotent on replays).
   - Increment `corporate_wallets.balance`.

### Auto-top-up
- When `balance < auto_topup_threshold` AND `auto_topup_enabled`:
  - Check `auto_topup_daily_cap` (sum of today's auto-top-ups).
  - If under cap: create off-session Stripe charge on the saved payment method for `auto_topup_amount`.
  - Same webhook path handles the rest.
- Per-wallet advisory lock prevents duplicate concurrent auto-top-ups.

### Low-balance email
- Send to `billing_email` when balance drops below threshold and auto-top-up is off, rate-limited via `low_balance_notified_at` (min 12h between emails).

### Ride debit (hot path) — Work rides
Pre-dispatch (at booking):
1. Resolve company + allowance from `corporate_members (user_id=rider, status=active)`.
2. Compute estimated fare via existing `fare_service`.
3. **Policy check** (§6). Fail → reject with reason.
4. **Pre-auth buffer**: require `allowance.remaining + (master fallback if permitted)` ≥ `est_fare × 1.5`. If not, reject with "Allowance low — tap 'Request more' to ask your admin."
5. Dispatch normally.

On ride completion (same transaction that finalizes fare):
1. `SELECT ... FOR UPDATE` on the allowance row.
2. Debit order:
   - `allowance_debit = min(remaining, final_fare)`
   - `master_fallback = final_fare - allowance_debit` (only if `policies.allowed_payment_source` permits)
   - Insert two `corporate_wallet_transactions` rows if both paths used (one scoped `member:<id>`, one scoped `master`).
3. Update `corporate_member_allowances.used` and `corporate_wallets.balance`.
4. Insert `ride_payment_sources` row.
5. Run policy re-check → insert `corporate_policy_evaluations (phase='completion')`. On failure: debit-and-flag (do not strand the driver).

### Soft-negative floor
Allowance may go to `-$50` (configurable, mirrors master wallet `soft_negative_floor`). Below that: ride is rejected at pre-dispatch. Both employee and admin notified on every negative debit.

### Cancellation / refund
- Rider cancels before dispatch → no debit, no refund.
- Driver cancels / rider cancels after dispatch → existing cancel-fee rules apply; whatever charges get booked to the rider today instead go via the same `PaymentSource` resolution.
- Dispute resolved in rider's favor → admin fires refund from super-admin panel → `type=refund` row, allowance + master rebalanced to match original debit split.

### Tips
Tips are **always** billed to the employee's personal card (no config in v1 — column reserved for v2). The Stripe tip charge uses the rider's saved personal card, not the company's Stripe customer.

### Concurrency & failure modes
- `SELECT ... FOR UPDATE` on wallet + allowance rows prevents double-spend.
- Webhook idempotency: UNIQUE constraint on `stripe_payment_intent_id`.
- Stripe charge fails mid-ride → soft-negative until threshold, then suspension.
- Admin changes policy mid-ride → booking-time check stands; completion-time re-check still logs the violation for audit, does not reverse the debit.

---

## 5. Employee Work Profile (rider app)

### Joining a company
Two paths:
1. **Email-domain auto-match**: rider has a verified email matching an entry in `corporate_allowed_domains` → in-app card "Join Acme Corp?" one-tap.
2. **Explicit invite**: admin sends to `alice@…` → email with tokenized deep-link `app://join?token=…` → opens rider app → confirm.

Both create a `corporate_members` row (`status=invited`) that flips to `active` on acceptance.

### Rider-app UI additions
- **Work Profile** screen under Profile → Settings:
  - Company name, role, **own allowance only** ("$142.50 of $500 available this month")
  - Policy summary ("Max $80/ride, Mon–Fri 9am–7pm inside GTA")
  - **Request more** button (opens amount + reason form)
  - This month's Work rides list with receipts
- **Personal ⇄ Work toggle** — shown in the home screen app bar when the rider has at least one active `corporate_members` row. Defaults to **Personal** on every cold start. Selection persists across warm restarts.
- **Booking flow with Work active**:
  - Payment method selector replaced with "Billed to Acme Corp (allowance)."
  - Pre-dispatch policy-rejection banner with specific reason.
  - Tip prompt still shown (charged to personal card — disclosed in the tip UI).
- **Ride history** — Personal / Work tabs, employee sees only their own Work rides.
- **Receipts** — every Work ride emails a PDF with company header to the employee; CSV export "last 30 days of Work rides" available in-app.

### "Request more" flow
1. Employee enters amount + reason → `corporate_allowance_requests` row (`status=pending`).
2. If an auto-approve rule applies (request ≤ `auto_approve_topup_amount` AND `auto_approved_this_period < auto_approve_monthly_count`) → status flips to `auto_approved` immediately, allowance topped up from master wallet.
3. Else → push + email to every active admin/owner of the company.
4. Admin approves/denies in portal → `status=approved|denied`, employee notified via push. Denied requests include the admin's note.
5. Employee cannot submit a new request while one is pending (cooldown implicit).

### Leaving
- Employee self-removes → `corporate_members.status=removed`. In-flight Work rides still debit the allowance.
- Admin removes → same effect, plus push to employee.

### Two companies (contractor case)
A user can be an active member of multiple companies. The profile picker lets them choose one at a time. `corporate_members` has UNIQUE `(company_id, user_id)`, not UNIQUE on `user_id`.

---

## 6. Policy engine

Pure function:

```
evaluate_policy(policy, ride_context) -> {pass: bool, failed_rules: [...]}
```

Same function runs at booking and at completion. Lives in `backend/services/corporate_policy_service.py`.

### Rules (v1)
1. **Max fare per ride** — `max_fare_per_ride`. Booking-phase uses estimate; completion-phase uses actual. Both stored.
2. **Geofence** — PostGIS `ST_Contains` against every polygon in `allowed_geofence`. Pickup AND dropoff must be inside at least one polygon.
3. **Time window** — pickup time converted to company timezone, checked against `allowed_time_windows`. Day + time range.
4. **Allowed payment source** — `allowance_only | master_only | both`. If `allowance_only` and allowance empty, reject (no fallback to master).

### Override
`corporate_members.policy_override=true` short-circuits all rules. Every override-bypassed ride is still logged to `corporate_policy_evaluations` for audit (`result=pass`, `failed_rules=[]`, with a `bypassed_rules` JSONB of what would have failed).

### Failure behavior
- Booking phase: reject the ride before dispatch, return `failed_rules` array to rider app for UI message.
- Completion phase: `debit-and-flag`. Still debit the allowance (driver gets paid), log the violation, flag on admin portal for review.

---

## 7. Admin portal (company-facing)

Route: `admin-dashboard/src/app/company/**`. New auth guard `requireCompanyAdmin(company_id)` / `requireCompanyMember(company_id)` for the `/company/**` tree.

### Screens
1. **Dashboard** — master wallet balance, active employees, spend-this-month, last-30d chart, pending allowance requests count, low-balance banner.
2. **Employees** — table (name, email, role, allowance used/total, status). Row actions: adjust allowance, pause, remove, promote to admin. Bulk invite (paste or CSV), bulk allowance assign.
3. **Invite** — allowed-domains list (add/remove), explicit invites, pending-invite list with resend/cancel.
4. **Wallet** — master balance, top-up button, auto-top-up settings, Stripe payment-method management, full ledger.
5. **Allowance requests** — queue with approve/deny + note. Mobile-friendly. Push-notified.
6. **Policies** — one company-wide policy form: max fare, geofence (map polygon drawn on existing Mapbox layer), time-window builder, day-of-week picker, payment-source toggle. Preview tool for sample ride.
7. **Reports** — monthly statement PDF, CSV exports (rides, per-employee spend, tax summary). Date-range picker.
8. **Settings** — company profile (legal name, BN, billing email, tax region), locale (en-CA / fr-CA), logo upload, admin seat management.

### Roles
- **Owner** — full access, only role that can close the account or add/remove other owners. One-per-company minimum.
- **Admin** — everything except owner management and account closure.
- **Member** — rider-app only; portal URL redirects them to a "no access" page.
- *(Finance role deferred to v2.)*

### Notifications
Via existing notification service — push + email on: allowance request, low balance, top-up success/failure, employee joined, KYB approval. Weekly digest deferred to v2.

### Super-admin side
`admin-dashboard/src/app/dashboard/corporate-accounts/**` — expand:
- KYB verification queue (pending docs, approve/reject with note)
- Company list with status filter, search
- Manual wallet adjustments (support/refund) — log to `admin_audit_log`
- View company audit log, suspend/reactivate

---

## 8. GTM / onboarding funnel

### Self-serve funnel (companies ≤ 25 employees declared at signup)
1. `/business` marketing page with primary CTA **"Create corporate account — 2 minutes"**.
2. Email + password → verify email.
3. Company form: legal name, BN, address, billing email, primary province.
4. KYB upload (incorporation or CRA registration) → status `pending_verification`.
   - Top-up allowed immediately.
   - Ride booking **blocked** until ops approves (SLA: 1 business day).
5. Add Stripe payment method.
6. Top up wallet (min $100, suggested $500).
7. Invite employees (bulk) or add allowed domain.
8. Set first policy (pre-filled sensible defaults: $100/ride, GTA polygon, Mon–Sun 24/7 — admin can tighten later).

### Sales-assisted funnel (> 25 employees OR explicit "Contact sales")
- Lead-capture form → Slack + email to sales channel.
- **CRM: HubSpot Free tier, Zapier-wired. Do not build in v1.**
- Demo playbook: one doc with 30-min walkthrough (rider app → portal → wallet → allowance → report).
- Pricing: same rates as self-serve in v1. No enterprise discount until ~10 self-serve customers provide baseline data.
- Onboarding CS: one-call walkthrough, help configure first allowance, import employee CSV.

### Acquisition channels (ranked)
1. LinkedIn outbound to HR/Finance at Canadian SMBs (Toronto, Montreal, Vancouver)
2. HRIS & expense-tool marketplaces: BambooHR, Rippling, Dext, Plooto
3. Co-working space partnerships (WeWork, IWG, Workhaus)
4. Chambers of commerce (Toronto Region Board of Trade, CCMM, GVBOT)
5. Content SEO ("business expense rides Canada", "T&E software Canada", "corporate rideshare GST")
6. Existing user base in-app banner ("Does your employer use rideshare?")

### KPIs
- Signup-funnel conversion per step
- Time-to-first-ride per company
- Weekly active companies (WAC)
- Avg employees per company
- Avg monthly spend per company by size_tier
- Master-wallet → allowance utilization rate
- Churn (zero-ride 30/60/90 day)

### NOT built in v1
- Own CRM (HubSpot Free)
- SSO/SAML
- Procurement / SOC2 docs (ad-hoc per prospect)
- Dedicated marketing site (one `/business` page)
- Automated KYB (Persona/Middesk) — manual review, 1-day SLA

---

## 9. Reporting

### Monthly statement (PDF, auto-generated 1st of month)
- Company header, logo, BN, legal name
- Period, master-wallet top-ups total, total ride spend, closing balance
- GST/HST/QST breakdown per province
- Line per ride: date, employee, pickup → dropoff, fare, tax, paid-by (allowance vs master)
- Emailed to `billing_email`
- Scheduled job follows the `utils/scheduled_rides.py` pattern
- Idempotent on `(company_id, period)`

### On-demand exports
- Rides CSV (date range, employee filter)
- Tax summary CSV (CRA T2 / provincial filing format)
- Per-employee spend CSV
- **Expensify and Concur CSV formats** — column schemas match their import spec (no API integration yet, but matching their CSV format is a 2-day item with high perceived value)

---

## 10. Canadian tax

- Tax calculated at ride completion based on pickup province (fallback to `corporate_accounts.tax_region`).
- Top-up itself is not taxable (prepayment, not a service).
- GST/HST/QST/PST logic in a new `backend/services/tax_service.py` with province-aware rates. Pluggable: adding `uk_vat` later means adding an adapter, not editing tax code for Canada.
- French-language invoices legally required for Quebec customers — `locale=fr-CA` on the company drives statement template selection.

---

## 11. Testing

- **Unit**: `evaluate_policy` (pure function, exhaust cases), allowance math, fare-to-ledger reconciliation, tax calculation per province, Stripe webhook idempotency.
- **Integration**: top-up via Stripe test-mode webhook, concurrent ride debits (10 concurrent completions against one wallet — prove no double-spend), ride cancel → refund round-trip, auto-top-up daily cap enforcement.
- **Manual QA**: Work Profile toggle, ask-for-more end-to-end (rider → admin → rider), policy rejection UX, domain auto-match onboarding, KYB approval flow, PDF statement generation.
- **Canadian tax**: fixture rides in ON (HST 13%), QC (GST + QST), BC (GST + PST), AB (GST only). Audit-exposed surface.

---

## 12. Regional-expansion architecture (deliberate choices)

1. Currency lives on `corporate_wallets.currency`, `corporate_accounts.currency`, transaction rows — no hardcoded CAD.
2. Tax via pluggable `tax_service` — Canadian adapter in v1.
3. Timezone per company, not system-wide.
4. Locale per company (`en-CA`, `fr-CA` today; more later).

### NOT doing now
- Multi-currency wallets per company
- Cross-border rides
- Per-country legal entity routing
- Stripe Connect platform setup for international payouts

---

## 13. v2+ roadmap (deferred items)

### v2 (~3 months post v1)
- Admin-booked guest rides (healthcare, hotels, events — unlocks verticals)
- Monthly post-paid invoicing + credit terms
- Groups / departments / cost centers
- Approval workflows
- Per-employee policy overrides (not just the boolean bypass)
- Purpose codes / ride-reason prompts
- Voucher / Pass codes
- Concur / Expensify API integration (not just CSV)
- Finance role
- Weekly digest email
- Automated KYB (Persona / Middesk)

### v3
- Regional expansion (start with GCC, US, or EU)
- Mobile admin app
- Public API + HR-tech marketplace presence
- White-label portal, franchise groups, custom pricing
- Anomaly / spend-spike detection

---

## 14. Migration

The consolidated v1 schema lives in `backend/migrations/27_corporate_b2b_v1.sql`. It is safe to re-run and compatible with the existing `corporate_accounts` table (migration 05) and its FKs (migration 17).

---

## 15. Acceptance criteria (summary)

A v1 delivery is complete when:
- A Canadian SMB can self-serve-sign-up, pass KYB, top up a CAD wallet, invite employees, set a policy, and employees can book rides that debit their allowance — all without developer intervention.
- A >25-employee prospect can submit a lead, get a demo, and be onboarded by CS with the same product (no feature gap).
- Monthly PDF statements generate automatically with correct GST/HST/QST math.
- Allowance "request more" flow works end-to-end with both manual approval and auto-approve rules.
- Policy engine rejects rides at booking for out-of-geofence, out-of-hours, and over-max-fare cases.
- Stripe webhook replays are idempotent; concurrent ride completions against one wallet never double-spend.
