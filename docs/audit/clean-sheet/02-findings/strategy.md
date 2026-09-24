# 02 — Findings: Vision, Business Model & Business Case (R3 Product Strategist, Wave W1)

> Lane: R3. Written 2026-09-24. Report-and-recommend only; no code or config was changed.
> Labels on every statement: VERIFIED / INFERRED / ASSUMED / PROPOSED / UNKNOWN (audit prompt §4).
> Evidence scope: repo at HEAD on 2026-09-24 (read-only); no live vendor data (Stripe, Sentry,
> Twilio, Redis MCP connectors all failed to connect this session — treated as connection failures,
> not missing access). Where a repo doc records a prior live pull (e.g. `docs/finance/stripe-payout-readiness.md`
> §7, 2026-09-12) it is cited as VERIFIED-IN-DOC with its date, never as current.
> Companion W0 inputs: `W0-SUMMARY.md`, `01-inventory/epics.md`, `traceability.csv`.
> Finished W2 lanes cited rather than re-derived: `02-findings/dispatch.md` (R8),
> `02-findings/trust-safety-fraud.md` (R11).

## §0 Status of this file

- [x] §1 Steelman — what the model gets right
- [x] §2 Revenue-line → shipped-code trace
- [x] §3 Per-ride unit-economics cost stack
- [x] §4 KPI measurement audit
- [x] §5 "What Spinr Is NOT" guardrail drift check
- [x] §6 Attack pass (competitor, CFO, COO, regulator, driver-vs-Uber)
- [x] §7 Ahead vs copying
- [x] §8 Finding cards (STRAT-nnn, §7.1 format)
- [x] §9 Rebuild Delta card — monetization model (§7.3)
- [x] §10 If Uber/Lyft rebuilt tomorrow — obvious / meaningful / difficult / novel / do-NOT-build
- [x] §11 Sweep-catalog §2.1 — every item answered
- [x] §12 Top 5 · NOT verified · human-only questions · escalations

(All sections on disk as of the final write, 2026-09-24.)

---

## §1 Steelman — what the current model gets right (and why it was built this way)

Read before the attack: many of these choices are deliberate and documented, not accidents.

1. **The 0%-commission promise is a code invariant, not marketing.** `backend/services/fare_service.py:228-233` — `admin_earnings = booking_fee + airport_fee`, `driver_earnings = total_fare − admin_earnings`; the minimum-fare uplift is deliberately the driver's. `driver_earnings_with_tip()` (`fare_service.py:249-300`) recomputes from persisted columns so a tip can never be under-credited (fixed after the 2026-08-19 live incident where a driver got $0.17 instead of $0.67). The legacy app *did* take a commission (`backend/utils/legacy_rides.py:128-146`: 186 imported rides carry "commission-GST on Spinr's own platform-commission fee") — so 0% is a conscious pivot with a paper trail, not a founding assumption. VERIFIED.
2. **The fare is fully itemised and shown before booking.** Receipt lines `Base fare / Distance / Time / Booking fee / Airport surcharge / Surge / GST / PST / Tip` (`fare_service.py:333-353`, `routes/rides/_shared.py:729-761`); rider copy "100% goes to your driver · ride local, support local" appears on `ride-options.tsx:1121`, `payment-confirm.tsx:543`, `ride-details.tsx:432`. `docs/legal/terms-of-service.md:64` promises no undisclosed service fees. Dimension-24 audit scored fare UX at maturity 4 (`docs/audit/ride-experience/REPORT.md` §2 row 6). VERIFIED.
3. **Surge is bounded at every fare-calc site.** `SURGE_CAP` is referenced at 21 non-test sites across 8 modules (`fare_service.py`, `routes/fares.py`, `features.py`, `surge_engine.py`, `routes/rides/booking.py`, `routes/admin/service_areas.py`, `ai/guardrails.py`, `routes/rides/_deps.py`); admin override >2.5 is record-only. R9 (`money-cra.md` §3) owns the per-site closure; cited, not re-derived. VERIFIED (count), INFERRED (every site clamps — R9's closure).
4. **Corporate SaaS is a real flat fee, not a per-ride markup.** `services/corporate_subscription_service.py:1-6` docstring states it explicitly; real Stripe Subscriptions with `stripe_price_id`; one live subscription per company enforced by partial unique index (migration 281). Ships dark (`_DEFAULT_BILLING_ENABLED = False`, `routes/corporate_subscriptions.py:54`) plus a per-company pilot gate — exactly the flagged rollout CLAUDE.md gate 3 asks for. VERIFIED.
5. **The company already knows it has no float cushion and no Stripe-fee visibility**, and wrote it down: `docs/finance/stripe-payout-readiness.md` §2, §6–§7 (real account pull 2026-09-12: `delay_days: 3`, `interval: "manual"`, one payout ever, $415.79 available). `money-cra.md:190` names the missing `BalanceTransaction` read. Honest self-knowledge is a strength; the gap it names is in §3 below. VERIFIED-IN-DOC (2026-09-12).
6. **Growth docs refuse to invent numbers.** `docs/growth/rider-acquisition-strategy.md` §5 declines to publish a CAC; `driver-acquisition-strategy.md` §4 cites Uride's figures as *not Spinr's*; `driver-retention-strategy.md` §1 states that commission is not the dominant churn driver (NBER). This is the right epistemic posture for a pre-revenue pilot. VERIFIED.
7. **Cost governance exists where it matters most (Maps).** `backend/utils/maps_budget.py` is a Redis daily circuit breaker with per-SKU prices, session-aware autocomplete accounting, and a fail-open rationale; `MAPS_DAILY_BUDGET_USD = 5.0` (`core/config.py:193`); self-hosted OSRM is tried first at zero metered cost (`maps_budget.py` header, `deploy/osrm/`). Dimension-24 found the biggest Directions call site still outside it (`REPORT.md` REC-D-01) — a gap, but the mechanism is right. VERIFIED.
8. **Analytics vocabulary already resists the commission framing.** `routes/admin/analytics.py:1443-1448` names rider-paid volume `gross_bookings` and comments "must never be presented as company revenue"; `efficiency` endpoint (`:1349`) reasons about deadhead "because the driver keeps 100%". Someone thought about how the model changes what to measure. VERIFIED.
9. **The regulatory-native thesis is partly real.** Insurance-period logging, WAV dispatch, GST/PST split lines, T4A job, PIPEDA purge — all exist as code units (`01-inventory/epics.md` §2; R11/R12 lanes own correctness). No incumbent markets "we log your SGI period" — it is a genuine, if invisible, edge. VERIFIED (existence), correctness deferred to R11/R12.

Why it was built this way (INFERRED from ADRs/docs): a small team pivoting from a commission-taking legacy app chose a driver-first wedge (0%) for a thin Saskatchewan market where Uber/Lyft are absent or weak, and bet that corporate accounts (Captain Taxi already sells them locally — `docs/CORPORATE_B2B_GTM.md` §1) fund the consumer side. The bet is coherent. What §2–§3 show is that the *code* has quietly evolved a different, driver-paid monetization that the vision documents never mention.

---

## §2 Revenue-line → shipped-code trace

Stated model (three sources, word-for-word aligned): `CLAUDE.md:503` "Monetization is SaaS corporate accounts + premium rider features + partner referrals — never per-trip cuts"; `docs/PRD.md:19-21`; `docs/legal/terms-of-service.md:50` ("Our monetization comes from optional premium rider features, corporate business accounts, and partner referrals"). PRD line 33: *"Every one of these claims is enforced by a code invariant, a test, or an audit row."*

### 2.1 The three stated lines

| # | Stated revenue line | Shipped code | Pricing config | Stripe product | Evidence of any revenue ever | Verdict |
|---|---|---|---|---|---|---|
| R1 | **Corporate SaaS subscriptions** | `routes/corporate_subscriptions.py` (5 endpoints), `services/corporate_subscription_service.py` (`assign_subscription`, `cancel_subscription`, `list_plans`), webhooks mirror `customer.subscription.*` / `invoice.*` | `corporate_subscription_plans.monthly_price` (migration 281:42-45); **no plan rows seeded in any migration** (grep `INSERT` on 281 → none) | Real `stripe.Subscription` with `items=[{price: plan.stripe_price_id}]` (`service:129`) — price object must be created by hand in the Stripe dashboard | **None found.** Ships dark: `_DEFAULT_BILLING_ENABLED = False` (`routes/corporate_subscriptions.py:54`) + per-company `subscription-pilot` gate. Whether any company is in the pilot: UNKNOWN (DB state) | **Built, dark, unpriced.** VERIFIED code; revenue UNKNOWN, most likely $0 |
| R2 | **Premium rider features** | **None.** `grep -ri "premium|subscription|spinr pass" rider-app/app rider-app/components rider-app/hooks` → only the *vehicle class* named "Premium" (`ride-options.tsx:90`). No rider subscription table, route, screen or Stripe product exists | — | — | — | **Unbuilt.** The only thing called "Spinr Pass" in the codebase is a *driver* subscription (§2.2 U1). VERIFIED absent |
| R3 | **Partner referrals** | **None.** `grep -ri "partner referral|affiliate|partner program|partner revenue"` across backend, apps, docs, ACTION_ITEMS → zero code hits; the only textual hits are the three policy sentences above plus `change-log/2026-08-19-referral-fraud-fix.md:110` explicitly noting the referral system is "not an affiliate/influencer payout system" | — | — | — | **Unbuilt and undefined.** The referral code that exists (`routes/drivers/referrals.py`, `routes/promotions.py`) is a *cost* line: Spinr pays $10 CAD per driver referee at 10 rides (`referrals.py:59-60`, migration 173 default 10) and a symmetric $5/$5 rider program (`referrals.py:64`). VERIFIED absent |

**Net: of three stated revenue lines, one is built-but-dark-and-unpriced, two do not exist.** PRD's "every claim is enforced by a code invariant" is true for the *guardrails* (0%, surge cap) and false for the *revenue model*. The ToS tells riders about revenue lines that do not exist and omits the ones that do (§2.2).

### 2.2 Revenue lines that exist in code but are absent from the stated model

| # | Undisclosed line | Where the money is taken | Who pays | Disclosed to whom | Live today? |
|---|---|---|---|---|---|
| U1 | **Spinr Pass — driver subscription** | `routes/drivers/subscriptions.py` (plans, Stripe Checkout, invoices, expiry loop), `utils/spinr_pass.py` (daily ride quota, `rides_per_day`, `-1` unlimited; force-offline on quota exhaustion), `routes/admin/subscriptions.py`; dispatch filters out quota-exhausted drivers (`dispatch_service.py:37,523`); go-online gate via `service_areas.subscription_required` (migration 182) and global `require_driver_subscription` (`schemas.py:310-313`, default `False`) | **Drivers** — an N-day pass (1/7/30/365 days) that grants N rides/day | Drivers only (`driver-app/app/driver/subscription.tsx:372` "0% commission — keep 100% of your fares. Pay a flat subscription fee."; welcome email `utils/driver_emails.py:87-90`). **Not** in ToS:50, PRD, CLAUDE.md, ICA | **Probably not.** Zero `subscription_invoice` emails of any status 2026-08-18→09-11; last invoice attempt *failed* 2026-07-29 (ACTION_ITEMS ~L18354, read of `email_send_log` 2026-09-11). Plan prices in repo are test fixtures only ($49.99; admin comment "19.99, 49.99" `routes/admin/subscriptions.py:32`). Counted into admin "Net Revenue" as `spinr_pass_mrr` (`routes/admin/rides.py` `/earnings/overview`) |
| U2 | **Booking fee** | `fare_service.py:213,228-233`: `admin_earnings = booking_fee + airport_fee`; surfaced to admins as `platform_fees` (`routes/admin/rides.py:2592`) and as the "per-ride platform margin" inside `net_revenue` / `take_rate_pct` (`rides.py:2811, 2951`) | **Riders** | As a receipt line "Booking fee" (`fare_service.py:352-353`); ToS:64 "any applicable booking fee". **Not** disclosed as *Spinr's* revenue anywhere rider-facing — the same screens say "Spinr takes no cut" (`rider-app/app/(tabs)/index.tsx:53`) | **$0 in both live areas.** `DEFAULT_FARE.booking_fee = 2.00` (`fare_service.py:36`), but Regina and Saskatoon `vehicle_pricing` rows have `booking_fee: 0` for every vehicle type (ACTION_ITEMS ~L5717-5739, DB read). Only `Regina Airport` and the two Riyadh rows carry $2 |
| U3 | **Airport surcharge** | Same `admin_earnings` bucket (`fare_service.py:215,232,349-350`) | Riders | Receipt line "Airport surcharge" | Configured on `Regina Airport`. **Whether Spinr remits this to the airport authority or keeps it: UNKNOWN** — no remittance code or doc found (`grep -ri airport docs/legal` → none) |
| U4 | **Cancellation-fee platform share** | `schemas.py:301-302` defaults `cancellation_fee_admin = 0.50`, `cancellation_fee_driver = 4.00`; per-area `cancel_fee_admin_share` (`routes/rides/queries.py:441-444`); written per ride in `routes/drivers/ride_cancel.py:733-734` | Riders (on cancellation) | `docs/legal/cancellation-fee-policy.md` exists (not read line-by-line here — R4 lane); split not stated to riders in-app (INFERRED) | Yes, whenever a fee-bearing cancellation occurs |
| U5 | **Instant-payout fee** | `routes/drivers/payouts.py:84-98`: 1.5% of gross, $0.50 floor, $15 ceiling, "matches Uber Instant Pay / Lyft Express Pay"; per-area `instant_payout_enabled` | **Drivers** | Driver-app payout screen (INFERRED) | Yes when enabled. **Net margin UNKNOWN**: Stripe charges the platform its own Instant Payout fee; no code nets the two |
| U6 | **`platform_fee_percent`** | `schemas.py:303` default `0.0` "0% commission"; admin PATCH accepts `0 ≤ x ≤ 1.0` (`routes/admin/settings.py:267`); column in `supabase_schema.sql:260`; write-allowlisted (`tests/test_admin_settings_write_allowlist_drift.py:139`) | — | — | **Dead config**: no reader anywhere in fare/payout code (grep → only schema, settings route, tests). Not revenue today, but it is an un-wired percentage-commission knob sitting in the admin settings surface — see STRAT-005 |

### 2.3 What this means

- The **economic** model in code is: *drivers pay a flat pass to work; riders pay a flat booking fee per ride (currently switched off); Spinr keeps small cancellation and instant-payout slices; corporate pays a flat monthly SaaS fee (dark)*. That is a coherent, defensible "flat-fee, not percentage" model — closer to a taxi dispatch licence than to Uber — and it is arguably *more* honest than "premium rider features" that nobody has designed.
- The **stated** model (ToS, PRD, CLAUDE.md, ICA) describes a different business. A rider reading ToS:50 is told Spinr earns from premium rider features and partner referrals; neither exists. A driver reading "Spinr takes no cut — 100% of your fare" and the ICA is not told, in those documents, that they may be required to buy a pass to go online (`service_areas.subscription_required`).
- With booking fee at $0 in both live areas, Spinr Pass dormant, corporate billing dark, and the legacy commission gone, **the platform's live per-ride revenue is $0 and its total recurring revenue is, on the evidence in the repo, $0** (UNKNOWN precisely — needs `driver_subscriptions`, `corporate_subscriptions`, and Stripe balance-transaction reads; see §12(d)). The $415.79 Stripe balance (2026-09-12) is not evidence of revenue: it is the residue of rider/corporate wallet top-ups (a liability), tax collected, un-transferred driver earnings, and legacy-era commission — see STRAT-002.

---

## §3 Per-ride unit-economics cost stack — who pays what, with 0% commission

**Method.** Every row names its repo source or is marked. Vendor list prices were fetched from public pricing pages on 2026-09-24 (WebSearch, 3 of 6 budget used) and are INFERRED — they are the vendor's published defaults, not Spinr's negotiated or actual invoiced rates (no Stripe/GCP/Twilio account data was reachable). Volumes use the finance doc's own placeholders (`docs/finance/stripe-payout-readiness.md` §6.1: 5–10 drivers × 10 rides/day × $15 average fare) so the numbers are comparable with the model finance already has; **they are directional, not a number to build a cash policy on.**

### 3.1 Who bears each cost (the structural answer)

| Cost | Who pays today | Evidence | Label |
|---|---|---|---|
| Stripe card processing (2.9% + C$0.30 domestic; +0.8% intl, +0.5% keyed, +2% FX) | **Spinr**, on 100% of GBV incl. tax and tip | Driver `Transfer` amount = gross `driver_earnings` (`utils/auto_payout.py:142,774-775`); no `application_fee`, no `BalanceTransaction` fee read anywhere (`grep application_fee|stripe_fee|fee_details` → only dispute-side parsing in `payment_service.py:533-603`); `money-cra.md:190` "driver keeps 100% minus Stripe processing is never measured". Rate: stripe.com/en-ca/pricing (fetched 2026-09-24) | VERIFIED (bearer) / INFERRED (rate) |
| Stripe Connect Express: ~$2 per monthly-active connected account + 0.25% + $0.25 per payout | **Spinr** | `routes/drivers/payouts.py:298-299` `stripe.Account.create(type="express")`; stripe.com/connect/pricing (fetched 2026-09-24; page did not state a CA-specific figure — treat as USD-equivalent) | VERIFIED (Express) / INFERRED (rate) |
| Stripe Instant Payout cost | Spinr pays Stripe; **driver pays Spinr 1.5% ($0.50–$15)** | `payouts.py:84-98`; Stripe's own instant-payout charge to the platform not fetched | VERIFIED (driver fee) / UNKNOWN (Spinr's cost, net margin) |
| Chargeback / dispute fee (Stripe, ~C$15 per dispute, typically non-refundable on loss) | **Spinr** | Dispute flow exists (`payment_service.py:533-603`); fee amount not fetched | INFERRED (fee), VERIFIED (bearer: no code passes dispute fees to drivers/riders) |
| Google Maps Platform | **Spinr** | `utils/maps_budget.py` `_PRICE_USD` (autocomplete $0.00283, details/geocode/directions $0.005, distance_matrix $0.010, text_search $0.032); 22-row call-site inventory in `docs/audit/ride-experience/cost-inventory-table.md` | VERIFIED (bearer, constants) — constants are self-declared estimates cross-checked 2026-09-12 |
| Self-hosted OSRM (map-matching + routing) | **Spinr**, fixed infra | `deploy/osrm/` (Dockerfile, `railway.json` → hosted on Railway) | VERIFIED (exists) / UNKNOWN (monthly cost) |
| Twilio SMS | **Spinr** | 6 send sites: OTP (`routes/auth.py:414`), admin messaging, SOS contact notices ×2 (`routes/rides/safety.py:414,854`, `utils/sos_contact_notice.py:49`), guest bookings (`services/guest_notification_service.py:69`), marketing (`utils/marketing_sms.py:72`). Twilio CA rate page found but per-segment figure not surfaced by the search — public list is on the order of US$0.008–0.01/segment | VERIFIED (sites) / INFERRED (rate) |
| Push (FCM / APNs / Expo) | free | `cost-inventory-table.md` rows 14–15 | VERIFIED |
| Transactional email (Resend/SendGrid — vendor "TBD-decided" per renewal calendar) | Spinr | `docs/runbooks/renewal-calendar.md` row | UNKNOWN (vendor + rate) |
| Fixed infra: Fly.io 2× `shared-cpu-1x` 1 GB (`capacity-scaling.md` §4), Railway standby + OSRM, Supabase Pro + Small compute (+$5/mo net, `capacity-scaling.md` §5; PITR would add $100/mo), Redis (provider behind `redis.spinr.ca` — UNKNOWN), Vercel, Sentry, PostHog (off), LogRocket, Expo EAS, Apple Developer, GitHub Actions, Zoho Desk, Cloudflare, domain | **Spinr** | `docs/runbooks/renewal-calendar.md` (every renewal date `TBD`; no dollar column at all) | VERIFIED (vendor list) / UNKNOWN (every dollar figure except Supabase's) |
| AI assistant (OpenAI embeddings `ai/embeddings.py:43-46`; chat model via app_settings) | Spinr, per use | not per-ride unless the rider books through the assistant | UNKNOWN |
| Support minutes | Spinr (human) | Zoho Desk integration exists; no cost or minutes-per-ticket data in repo | UNKNOWN — founder only |
| Driver acquisition/retention spend, rider promos, referral rewards ($10/driver referee at 10 rides; $5/$5 rider), quests, loyalty points (redemption disabled) | Spinr, variable | `routes/drivers/referrals.py:57-72`, `services/incentive_service.py`, `routes/loyalty.py:1-10` | VERIFIED (mechanisms) / UNKNOWN (budgets) |

**GST/PST collected on fares and who remits it is R9's (money-cra) question, not restated here** — but note that tax collected inflates the Stripe base on which the 2.9% is charged, and Spinr bears that too.

### 3.2 Per-ride cost table (illustrative $15 card-paid consumer ride, GST 5%, $2 tip, OSRM healthy)

| Line | Basis | Per ride (CAD) | Label |
|---|---|---|---|
| Stripe processing | 2.9% × $17.75 + $0.30 | **$0.81** | INFERRED |
| Stripe Connect Express (amortised: $2/mo ÷ 300 rides + 0.25% + $0.25 on weekly payouts ÷ 70 rides) | | **$0.02** | INFERRED |
| Google Maps — estimate Directions (≥1 call per pin-drag, unbudgeted row #1), autocomplete session (≤12 paid keystrokes), details, reverse geocode (cached), client-direct `MapViewDirections` (rows 17–22, unbudgeted) | 3–8 calls × $0.003–0.005 | **$0.02–0.06** | INFERRED from call inventory; real count unmeasured (REC-D-01) |
| Google Maps — OSRM-down case (Distance Matrix per GPS ping during approach, in-trip Directions fallback, Roads snap) | e.g. 60 pings × $0.01 | **+$0.60 or more** (tail risk, not steady state) | INFERRED |
| SMS | ~0 per ride (OTP is per login/new device, refresh token 30 d `core/config.py:155`); guest bookings and SOS add 1–N segments | **<$0.01** | INFERRED |
| Fixed infra amortised (assume US$100–250/mo all-in, INFERRED) | ÷ 3,000 rides/mo (10 drivers) | **$0.03–0.08**; ÷ 300 rides/mo (pilot week 1) → $0.30–0.80 | INFERRED |
| Support, incentives, promos, chargebacks | | **UNKNOWN** — founder inputs | UNKNOWN |
| **Direct variable cost, steady state** | | **≈ $0.90–0.95 per ride** before support/incentives | INFERRED |

### 3.3 Per-ride revenue against that cost

| Scenario | Per-ride revenue | Margin per ride | Comment |
|---|---|---|---|
| **Today, Regina/Saskatoon** | `booking_fee = 0`, Spinr Pass dormant, corporate dark | **≈ −$0.93** | Every ride loses money; the loss scales linearly with GBV because the dominant cost is a percentage of the fare Spinr doesn't keep |
| Booking fee on at the `DEFAULT_FARE` $2.00 | $2.00 − 2.9% of $2 | **≈ +$1.01** | Covers processing with ~$1 headroom for infra/support at pilot volume; still zero margin from the fare itself by design |
| Spinr Pass only (driver pays P per 30 days, R rides/mo) | P ÷ R | Break-even at **R = P ÷ 0.93** → a $49.99 pass breaks even at **~54 rides/month**; a full-time driver at 300 rides/mo costs Spinr **≈ $280/mo** in processing against a $50 pass | The pass model has the *wrong slope*: Spinr's cost rises with driver success, its revenue does not. Confirms the CFO attack in §6 |
| Corporate SaaS (flat monthly F per company) | F ÷ company rides | Same slope problem, but corporate rides are also higher-value and paid from a pre-funded wallet (one Stripe charge per top-up, not per ride → processing cost per ride falls to ~0.25%+ amortised) | This is the only line whose economics improve with volume. It is dark. |

**Headline (INFERRED, every input marked above):** with the fare-percentage cost (Stripe) borne by the platform and no fare-percentage revenue, **the 0% model is only solvent if a flat per-ride rider fee (booking fee) is on, or if wallet/corporate prepayment shifts processing off the per-ride path.** The code already contains both levers — `DEFAULT_FARE.booking_fee = 2.00`, rider wallet top-ups (`routes/wallet.py:139`), corporate master wallets — and the live configuration currently uses neither.

### 3.4 Vendor-price sources used (public pages, fetched 2026-09-24 — INFERRED, not Spinr's contracted rates)
- Stripe Canada card pricing: https://stripe.com/en-ca/pricing (2.9% + C$0.30; +0.5% keyed, +0.8% international, +2% FX)
- Stripe Connect pricing: https://stripe.com/connect/pricing ($2/monthly-active account; 0.25% + $0.25 per payout when the platform handles pricing)
- Twilio Canada SMS: https://www.twilio.com/en-us/sms/pricing/ca (per-segment figure not surfaced by the search result; re-fetch before use)
- Google Maps SKU prices: taken from `backend/utils/maps_budget.py` `_PRICE_USD`, which the 2026-09-12 audit cross-checked against Google's pricing page
- Uber take rate context (for §6): https://www.uber.com/en-CA/blog/more-transparency-with-upfront-offers-in-canada (upfront offers "net of Uber's service fee"); third-party summaries put the nominal service fee at 25% and Q4-2025 mobility take rate at 29.9% (vizologi.com, ridesharemechanic.com — secondary sources, INFERRED)

---

## §4 KPI measurement audit — every CLAUDE.md KPI row against the code

Sources read: `backend/routes/admin/analytics.py` (15 endpoints, lines 124–1546), migrations 352/422/423/383, `services/payment_service.py:1689`, `routes/drivers/ride_flow.py:471,515`, rapid-baseline `A6-observability.md` OBS-002, `01-inventory/epics.md` §1.

| # | KPI (CLAUDE.md) | Target | Query/endpoint/metric today | Verdict |
|---|---|---|---|---|
| 1 | Match rate | ≥ 85% | `GET /api/admin/analytics/overview` (`analytics.py:440`), `marketplace-funnel` (`:952`) | **Measured** (VERIFIED endpoint exists; formula not re-derived) |
| 2 | Rider cancellation rate | ≤ 8% | `overview`, `cancellation-reasons` (`:124`) | **Measured** |
| 3 | Driver cancellation rate | ≤ 3% | `overview`, `driver-acceptance` (`:224`) | **Measured** |
| 4 | Driver utilisation | ≥ 55% | `supply-utilization` (`:1064`), `efficiency` deadhead (`:1339`) | **Measured** |
| 5 | P95 dispatch latency | < 2 s | `spinr_dispatch_offer_to_accept_duration_ms` emitted at `ride_flow.py:471,515`; `dispatch-latency` endpoint (`:1154`, migration 422) | **Measured** — but nothing scrapes the Prometheus metric in production (ACTION_ITEMS C11; OBS-002), so the endpoint is the only live view |
| 6 | P95 fare-calc latency | < 300 ms | `spinr_fare_calc_duration_ms` (referenced `services/data_transfer/observability.py:4`) | **Emitted, not observed** (C11); documented 3.5 s exception accepted |
| 7 | Payment success rate | ≥ 99% | `spinr_payment_settlement_total{outcome=…}` (`payment_service.py:1689`) | **Emitted, not observed**; and its failure denominator was silently wrong for two months (B42: 53 of 55 `payment_failed` webhooks lost, `W0-SUMMARY` #1) — the KPI could not have shown the incident |
| 8 | Weekly-active driver retention (rolling WoW) | ≥ 80% | **None.** `retention-cohorts` (`:1241`, migration 423) is signup-**cohort** W1/W4/W12 retention; the code comment at `analytics.py:45-51` and `:1308-1319` says so explicitly. CLAUDE.md's own "Known gap" note is accurate | **Unmeasured** (VERIFIED) |
| 9 | Safety incident rate | < 1 / 10k rides | **None.** `grep -ri "incident_rate|per_10k|incidents_per"` across `routes/admin`, `services` → 0 hits. `routes/admin/safety.py` lists incidents; no rate, no denominator join | **Unmeasured** (VERIFIED absent by grep; R11 lane may hold more) |
| 10 | Support ticket P1 response | < 2 h | **None in code.** `grep -i "first_response|response_time|sla_"` in `routes/admin/support_tickets.py`, `services/zoho_desk*.py` → 0 hits. Zoho Desk may compute it externally | **Unmeasured in-repo** (VERIFIED absent); external status UNKNOWN |

**Business KPIs that do not exist at all** (no row in CLAUDE.md, no endpoint): revenue by line (Spinr Pass MRR exists — `admin_mrr_at_cutoff`, migration 383, sums `driver_subscriptions.price` only; **corporate subscription MRR is not computed anywhere**), Stripe fee % of GBV, contribution margin per ride, cost per ride, CAC, payback, wallet float / liability balance, Maps spend in dollars (E13 open; "maturity 1 / RED" in `ride-experience/REPORT.md`). The admin `earnings/overview` reports a **"Take Rate %"** (`routes/admin/rides.py:2811,2951`) — on a platform whose founding claim is that it has none; internally harmless, but it should be renamed "platform margin %" before it appears on a slide.

**Performance-SLA rows** (separate table in CLAUDE.md): 3 of 8 have no emitting metric at all — driver-location write, auth token refresh, Stripe-webhook processing (OBS-002, rapid baseline, VERIFIED there, not re-verified here). 0 of 8 have a live alert (CR-2026-008).

**Net:** 7 of 10 KPI rows have *some* query; 3 have none; of the 7, three are Prometheus counters nobody scrapes. "Measured" in this codebase means "an admin could open a page," not "someone would be paged."

---

## §5 "What Spinr Is NOT" — guardrail drift check against shipped code

| Guardrail (CLAUDE.md "What Spinr Is NOT") | Evidence of drift or pressure | Status |
|---|---|---|
| **Not a commission-taking marketplace** | Fare: 0% enforced (`fare_service.py:228-233`). Pressure points: (a) `platform_fee_percent` is a live admin-writable setting accepting 0–1.0 with **no reader** (`routes/admin/settings.py:267`, `schemas.py:303`) — a dormant percentage-commission knob (STRAT-005); (b) booking fee and airport surcharge are retained per ride as `admin_earnings` — a flat per-trip platform amount, disclosed as a line but presented next to "Spinr takes no cut" (`rider-app/app/(tabs)/index.tsx:53`) (STRAT-003); (c) admin analytics literally compute a "take rate" (§4); (d) Spinr Pass is a driver-paid licence to work, absent from ToS/PRD/ICA (STRAT-001) | **Letter kept, spirit under pressure.** VERIFIED |
| **Not a surge-first product** | `SURGE_CAP` at 21 sites / 8 modules; admin override to 10× is stored verbatim (audit-only). Surge exempt on corporate rides per CLAUDE.md — R9 owns closure | **Holding.** VERIFIED (count) — per-site behaviour deferred to R9 |
| **Not a driver-control platform** | ICA §1.2 language is clean (`docs/legal/independent-contractor-agreement.md:18,81`); retention doc explicitly avoids quotas. Pressure: Spinr Pass **daily ride quota with force-offline on exhaustion** (`utils/spinr_pass.py` header; enforced at go-online, dispatch, accept, completion) and per-area `subscription_required` — the platform, not the driver, decides when a driver may stop/keep working, gated on payment. Not employer-style control, but a novel "pay-to-work" limiter no incumbent uses; classification impact is a legal question (§12 e) | **Watch.** VERIFIED (mechanism) / ASSUMED (legal effect) |
| **Not a data-harvesting product — "never add third-party ad SDKs or behavioral retargeting"** | `react-native-fbsdk-next` is in both apps (`rider-app/package.json`, `driver-app/package.json`) with `autoLogAppEventsEnabled: true` (`rider-app/app.config.ts:323-330`); backend `utils/meta_capi.py` + `services/meta_conversions_service.py` send **a `Purchase` event with `value`, `currency`, `promo_code`, `ride_id` for every completed paid ride** plus `CompleteRegistration`/`FirstRide`/driver events, with SHA-256-hashed email/phone Advanced Matching (`META_EVENTS.md` §1; `meta_capi.py` docstring rule 2). The team's posture is careful (no IDFA, ATT off, hashed only) and the stated purpose is install attribution for an App Promotion campaign — but this **is** an ad SDK, and per-ride purchase values with hashed identity **are** the raw material of behavioural ad optimisation on Meta's side. **Meta does not appear in `docs/legal/subprocessor-list.md` or `docs/legal/privacy-policy.md`** (grep 2026-09-24 → 0 hits; LogRocket and PostHog do). PostHog session replay is flag-gated off and US-hosted (`subprocessor-list.md:82`). | **Drift — the guardrail is being read as "no retargeting" when it says "no ad SDKs."** STRAT-004. VERIFIED (code, docs) — R12 compliance lane not found to have filed it (grep of `compliance.md`/`security.md` this session) |
| **Not a hidden-fee operator** | Receipt lines are complete (`fare_service.py:333-353`). Instant-payout fee (drivers), cancellation-fee platform share ($0.50 default) — disclosed in policy docs (INFERRED for in-app copy). Airport surcharge retained as platform revenue with no remittance path found (§2.2 U3) — if the airport charges Spinr a fee, that is a pass-through; if not, it is a location-based platform fee labelled "surcharge" | **Mostly holding; airport line UNKNOWN.** |
| **Not a country-agnostic product** | Production `service_areas` contains `riyadh` and `riyadh airport`. ACTION_ITEMS says both "test/dev data" (~L5535) and "intentional (international market), confirmed with product" (~L5700) — the repo contradicts itself. `schemas/corporate.py` has `Locale fr-CA`; CAD hard-coded in Meta events and Stripe calls | **Contradiction on record** — STRAT-007; needs the founder's answer |
| **Not a 911 replacement** | R11 (`trust-safety-fraud.md`) owns; not re-derived | Deferred |

---

## §6 Attack pass — five adversaries

**Competitor (Uber/Lyft/Uride/Captain Taxi).** "Spinr's whole pitch is 0%, and I can neutralise it in one line: *0% of a smaller fare.*" Live Regina pricing is $2 base + $2/km, Saskatoon $4 + $1/km, **no per-minute, no minimum fare, no booking fee, identical across vehicle types** (ACTION_ITEMS ~L5687-5739). A 5 km Saskatoon trip is $9; a 1 km trip is $5; a 20-minute traffic crawl over 3 km pays $7. Uber's upfront offer nets the driver ~70–75% of a fare that includes time and a minimum; on short or slow trips Uber's driver take-home can exceed Spinr's 100%. Second move: "Their corporate product is dark and can't be bought self-serve (`CORPORATE_B2B.md` §11: no self-serve onboarding, no ride-time policy enforcement); I already invoice clinics monthly" (Captain Taxi, `CORPORATE_B2B_GTM.md` §1). Third: "Their support SLA isn't measured (§4 row 10) — I'll win on response time, the thing NBER says drivers actually churn on (`driver-retention-strategy.md` §1)." Defence Spinr has: the transparency itself is real and testable; the counter is to fix pricing config (STRAT-008), not the model.

**CFO.** "Show me a unit that makes money." §3.3: none today. Payment processing is ~2.9% + $0.30 on every dollar of GBV *including tax and tip*, borne by the platform, with zero fare-linked revenue. The pass model's revenue is flat while cost is proportional to driver success (a $50 pass breaks even at ~54 rides/mo; a full-time driver costs ~$280/mo in processing). The Stripe balance is $415.79 with one payout ever and `interval: "manual"` (`stripe-payout-readiness.md` §7) — and that finding was **never filed in ACTION_ITEMS** (grep `interval.*manual|payout.*manual` → 0 hits). Nobody computes Stripe fees (`money-cra.md:190`), Maps dollars (E13 open), fixed infra (renewal calendar has no cost column), CAC, or float. "You have a growth plan with bonus tables and a cash-gap model — and no contribution margin line anywhere. Turn on the booking fee or move consumers to prepaid wallets before spending a dollar on acquisition."

**COO.** "What happens on day 3 of launch?" (1) `MAPS_DAILY_BUDGET_USD` defaults to $5/day (`core/config.py:193`); at ~$0.04/ride that is ~125 rides/day — the pilot's own upper scenario is 100 rides/day, and the breaker's open state is a **503 on the Maps proxy** (`maps_budget.py` docstring), i.e. destination search stops working for every rider at once; production override value UNKNOWN. (2) Payouts are manual — a human must click every payout, and the last click was 2026-03-31. (3) Spinr Pass invoice emails have never been observed to send (ACTION_ITEMS ~L18354) — if a driver is ever charged, they get no invoice. (4) Every vendor renewal date is `TBD` (`renewal-calendar.md`). (5) `platform_fee_percent` is one admin PATCH away from being set to 1.0 with nobody knowing it does nothing — or, worse, someone wiring it up "because the setting exists" (STRAT-005). None of these are code bugs; all are operating-model gaps.

**Regulator / plaintiff's lawyer.** (1) ToS:50 tells riders Spinr earns from "premium rider features … and partner referrals" — neither exists (§2.1); the line that does exist (drivers pay to work) is not mentioned. That is a consumer-facing accuracy problem in a legal document (ASSUMED as to consequence — escalate to counsel, §12 e). (2) Meta receives a `Purchase` event with value and hashed identity for **every** paid ride (`META_EVENTS.md` §1) and is not on the subprocessor list or in the privacy policy — a PIPEDA openness/consent question (ASSUMED as to breach status; R12's domain), and a direct collision with the company's own published "no ad SDKs" stance (`.planning/PROJECT.md` Out-of-scope; `CLAUDE.md`). (3) Spinr Pass + daily quota + force-offline: `compliance.md` X18 already asks whether contractor status survives an unsigned ICA plus a fee drivers must pay to work; the quota adds "platform decides when you stop." (4) Airport surcharge retained by the platform with no remittance path found — if an airport authority's ground-transportation fee is being collected in its name, that is a pass-through mislabelled as revenue (UNKNOWN).

**A driver comparing take-home vs Uber.** Worked example, INFERRED rates: 300 rides/mo at Spinr's Saskatoon Economy pricing, average 6 km / 12 min → $10.00 fare → **$3,000/mo, 100% kept**, minus pass (UNKNOWN, ~$50?) minus instant-payout fees if used. Uber (secondary sources: 25% nominal service fee, 29.9% Q4-2025 mobility take rate) on a fare that includes ~$0.25–0.35/min and a ~$6–8 minimum: a 6 km/12 min trip ≈ $13–15 gross → ~$9.75–10.50 net to driver. **Per trip the two are within ±10%** of each other on this trip shape; Spinr wins on longer/faster trips, loses on short and slow ones because it charges nothing for time and has no floor. The driver's real question is not commission, it is *income predictability* (retention doc §5) — and Spinr's own config currently makes short trips unpredictable. 0% is necessary but not sufficient; pricing config is the lever (STRAT-008).

---

## §7 Where Spinr is genuinely ahead vs where it is copying

| Area | Ahead / parity / copying | Evidence | Defensible? |
|---|---|---|---|
| 0% commission as a **code invariant** with receipt-line proof | **Ahead** (no incumbent can follow without rewriting its P&L) | `fare_service.py:228-233`; rider copy ×5 sites | Yes — cost-structure advantage, but only if the fee model that funds it is on (§3) |
| Surge hard cap enforced at every call site, shown before booking | **Ahead** on trust; parity on UX | 21 sites; `REPORT.md` row 6 maturity 4 | Trust advantage; copyable as a feature |
| Regulatory-native artefacts (SGI periods, WAV dispatch, T4A job, GST/PST lines, PIPEDA purge) | **Ahead** in a Saskatchewan-specific way | `epics.md` §2; R11/R12 own correctness | Operational/compliance moat in-province; irrelevant elsewhere (ties to STRAT-007) |
| Corporate B2B with row-locked wallet RPC, KYB, allowances | **Parity on architecture, behind on go-to-market** | `CORPORATE_B2B.md` §9; §11 gaps; billing dark | Would be the moat if it were sellable; today it is a well-built latent asset |
| Map/marker/route/receipt/notifications | **Parity** (maturity 4, `REPORT.md` §2) | Dimension-24 audit | Table stakes, copied correctly |
| Turn-by-turn navigation | **Behind** (maturity 2) | `REPORT.md` row 3, conditional-go proposal | Table stakes gap |
| Instant payout with 1.5% fee, quests, referral credits, loyalty tiers, promo codes, Live Activity | **Copying** Uber/Lyft mechanics one-for-one | `payouts.py:85` says so; `epics.md` Promotions epic (116 units) | None — and each is a cost line without a revenue line beside it |
| Spinr Pass (driver N-day pass with daily ride quota) | **Novel** — no incumbent sells drivers a ride quota | `utils/spinr_pass.py` | Novel ≠ good: wrong cost slope (§3.3), pay-to-work optics, undisclosed (§2.2) |
| Meta CAPI per-ride Purchase events | **Copying growth-team defaults** from ad-funded consumer apps | `META_EVENTS.md` | Contradicts the stated positioning (§5) |

---

## §8 Finding cards

### STRAT-001 — The revenue model in the ToS/PRD/CLAUDE.md is not the revenue model in the code
- Hierarchy: L0 Vision › L1 "Monetise via corporate SaaS + premium rider + partner referrals" › L2 Promotions & Loyalty / Corporate B2B / Driver Earnings › L3 (Spinr Pass, corporate subscriptions) › L4 none (no story exists for "premium rider features" or "partner referrals") › L5 n/a
- Severity: HIGH   Priority score: S×B×L = 4 × 4 (every legal + product doc, every rider who reads ToS) × 5 (certain) = 80
- Status: VERIFIED   Existing item: **new** (grep ACTION_ITEMS `premium rider|partner referral|monetiz` → 0 relevant hits)
- Adversary: regulator / plaintiff's lawyer (ToS accuracy); competitor (points out the pass); a driver discovering `subscription_required`
- Evidence: `docs/legal/terms-of-service.md:50`; `docs/PRD.md:19-21,33`; `CLAUDE.md:503`; `docs/legal/independent-contractor-agreement.md:62,157`; absence: `grep -ri premium rider-app/` → vehicle class only; `grep -ri "partner referral|affiliate"` → 0 code hits; presence: `routes/drivers/subscriptions.py`, `utils/spinr_pass.py`, migration 09/150/182, `driver-app/app/driver/subscription.tsx:372`; `routes/corporate_subscriptions.py:54` dark flag
- What happens (plain language): a rider is told Spinr earns from things that don't exist; a driver is told "no cut, ever" by the welcome email and ToS, then finds an area where they must buy a pass to go online. Internally, product decisions are made against a model that isn't the one being built.
- Root cause: the vision text was written once (2026-05 GSD init, `.planning/PROJECT.md` "Core Value") and consolidated into PRD/ToS without ever being reconciled with the Spinr Pass and booking-fee code that was already there (migration 09 is one of the oldest in the repo).
- Recommendation: one decision memo (founder) choosing the real model — recommended: *"flat, disclosed fees: a per-ride booking fee paid by riders; an optional/area-required driver pass; flat corporate SaaS; instant-payout and cancellation-fee slices"* — then a single PR updating ToS:50, ICA:62/157, PRD Competitive thesis #1, CLAUDE.md:503, `.planning/PROJECT.md` Core Value, and the driver welcome email. Delete "premium rider features" and "partner referrals" unless a story is written for them.   Alternative considered: build premium rider features and a partner-referral programme to match the documents — rejected: no design, no demand evidence, no owner, and it would add two more cost/complexity lines to an unprofitable unit (§3).
- Blast radius: documents only (6 files) + one email template (`utils/driver_emails.py:87-90`); no runtime behaviour. Legal review required for ToS/ICA wording (`docs/legal/legal-text-publication-checklist.md`).
- Rollout: doc/legal change; ToS version bump triggers the re-consent path if material (CLAUDE.md PIPEDA "Consent" — confirm with counsel whether this is material).   Rollback: revert the text; re-consent cannot be un-asked, so get wording right once.
- Verification to close: a test in `backend/tests/test_ai_prompts_policy.py`-style that asserts the ToS/ICA/PRD strings name every revenue mechanism that has a code path (`driver_subscriptions`, `booking_fee`, `corporate_subscriptions`, instant-payout fee, `cancellation_fee_admin`) and none that has no code path.

### STRAT-002 — Per-ride contribution margin is negative today and is not measured anywhere
- Hierarchy: L0 Vision › L1 (no KPI row exists for margin) › L2 Ride Completion & Payments › L3 Wallet settlement / Stripe capture › L4 S-pay-02/03 › L5 "card-paid consumer ride settles"
- Severity: HIGH   Priority score: 4 × 5 (every ride) × 5 = 100
- Status: INFERRED (economics; every rate labelled in §3) / VERIFIED (bearer: no fee deduction code; `booking_fee = 0` live; no margin metric)   Existing item: partial — `money-cra.md:190` (Stripe fees unmeasured), ACTION_ITEMS E13 (Maps dollars), pricing-decision item ~L5680 (booking fee); **the margin question itself is new**
- Adversary: CFO
- Evidence: `services/fare_service.py:228-233`; `utils/auto_payout.py:142,774-775`; `routes/drivers/payouts.py:298` (Express accounts); ACTION_ITEMS ~L5717-5739 (`booking_fee: 0` in Regina/Saskatoon); `docs/finance/stripe-payout-readiness.md` §7 ($415.79 balance, 1 payout ever); `routes/admin/rides.py:2811` (net revenue counts only booking/airport fees + Spinr Pass MRR — no cost side); stripe.com/en-ca/pricing (2.9% + $0.30, fetched 2026-09-24)
- What happens (plain language): each consumer ride costs Spinr roughly a dollar in processing and API fees and returns nothing; the more riders and the busier the drivers, the larger the loss. Nobody sees this number because no report computes it.
- Root cause: the 0% promise was implemented at the fare layer without deciding who absorbs the fare-proportional *costs*; the booking fee that was designed to cover them (`DEFAULT_FARE.booking_fee = 2.00`) was zeroed in live pricing config, and the finance model (`stripe-payout-readiness.md` §6) models cash timing, not margin.
- Recommendation: (1) **Decision (founder, this week):** set a booking fee in Regina/Saskatoon at ≥ $1.00 (covers processing at $15 average fare) and label it honestly (STRAT-003); (2) **Engineering (S):** read `balance_transaction.fee` on each settlement and store `stripe_fee` on `financial_events` (R9's ledger is the natural home), then add `platform_margin` = admin_earnings − stripe_fee − est. maps cost to `/earnings/overview`; (3) **Product (M):** push consumers toward wallet prepay (one processing fee per top-up instead of per ride) with a small wallet incentive.   Alternative considered: a percentage platform fee — rejected: guardrail, and it is the thing Spinr exists not to do. Alternative: charge drivers the processing cost via `application_fee_amount` — rejected: makes "100% of the fare" false in the driver's own ledger; a disclosed rider-side flat fee is cleaner.
- Blast radius: booking fee > 0 changes `total_fare`, GST base, receipt lines, corporate policy `max_fare_per_ride` checks, Meta `Purchase.value`, and every fare test fixture — grep `booking_fee` → 9 backend files + `shared/types/api/money.ts:43,54`, `ride.ts:60`; margin metric is additive.
- Rollout: booking fee is per-area DB config (`service_areas.vehicle_pricing`) — no deploy; flag-like by construction. Margin metric additive.   Rollback: set `booking_fee` back to 0 in the same JSONB; rides already charged keep their receipts (no clawback).
- Verification to close: `/earnings/overview` shows `platform_margin` ≥ 0 over a 7-day window on staging fixtures with the fee on; `test_fares.py` extended for fee > 0 per area.

### STRAT-003 — "Spinr takes no cut — 100% of your fare goes to your driver" is true only because the booking fee is switched off
- Hierarchy: L2 Ride Booking & Matching › L3 Fare estimation › L4 S-book-01 › L5 "rider sees fare breakdown"
- Severity: MEDIUM   Priority score: 3 × 3 × 4 = 36
- Status: VERIFIED   Existing item: new
- Adversary: plaintiff's lawyer; competitor screenshot
- Evidence: `rider-app/app/(tabs)/index.tsx:53`; `ride-options.tsx:1121`; `payment-confirm.tsx:543`; `ride-details.tsx:432`; `ride-completed.tsx:658`; `fare_service.py:228-233` (`admin_earnings = booking + airport`); admin "Take Rate %" `routes/admin/rides.py:2811,2951`
- What happens: the day STRAT-002's fee is turned on, five screens say "no cut" beside a receipt line that is Spinr's cut. Internally, dashboards call it a take rate.
- Root cause: copy was written for the fare, code retains fees; no test ties the two.
- Recommendation: make the badge conditional — "100% of the fare goes to your driver" (always true) plus, when `booking_fee > 0`, "Booking fee $X goes to Spinr"; rename `take_rate_pct` → `platform_margin_pct` in the admin API/UI.   Alternative: keep copy, keep fee at 0 forever — rejected by STRAT-002.
- Blast radius: 5 rider-app copy sites; `shared/` fare types unchanged; admin earnings page consumes `take_rate_pct` (grep admin-dashboard before renaming; keep old key for one release).
- Rollout: rider-app store release (no visual-regression tooling for rider-app — reasoned, not screenshotted).   Rollback: copy-only.
- Verification to close: Jest test on the badge component with `booking_fee` 0 and 2.

### STRAT-004 — Meta ad SDK + per-ride `Purchase` conversions collide with the "no ad SDKs" guardrail and are undisclosed to users
- Hierarchy: L2 Platform Foundation / Compliance › L3 Analytics & attribution › L4 none (no PRD story; orphan by §2 rule) › L5 "every paid ride completes"
- Severity: HIGH   Priority score: 4 × 5 (every user, both apps) × 5 = 100
- Status: VERIFIED   Existing item: new (grep ACTION_ITEMS `meta_capi|conversions api|fbsdk` → not found as an open item; `compliance.md`/`security.md` grep → not filed)
- Adversary: regulator (PIPEDA openness/consent), plaintiff, competitor ("no data harvesting" claim)
- Evidence: `rider-app/package.json`/`driver-app/package.json` `react-native-fbsdk-next`; `rider-app/app.config.ts:301-330` (`autoLogAppEventsEnabled: true`); `shared/analytics/meta.ts:1-24`; `backend/utils/meta_capi.py` docstring; `backend/services/meta_conversions_service.py:59-65,324-385`; `META_EVENTS.md` §1 ("Every completed paid ride → `Purchase` … `value`, `currency`, `promo_code`, `ride_id`"); `docs/legal/subprocessor-list.md`, `docs/legal/privacy-policy.md` (0 hits for Meta/Facebook); `CLAUDE.md` "Not a data-harvesting product"; `.planning/PROJECT.md` Out of Scope
- What happens: every paid ride's value, promo code and a hashed identity go to Meta for ad optimisation; riders are told there are no ad SDKs and are not told Meta is a processor.
- Root cause: an install-attribution need (App Promotion campaign) was met with the industry-default tool; the guardrail was read as "no retargeting" rather than "no ad SDKs"; the disclosure step was skipped because the team judged it "not tracking" under ATT rules — a store-policy lens, not a PIPEDA one.
- Recommendation: (1) **Legal, before next release:** add Meta to `subprocessor-list.md` and `privacy-policy.md`; (2) **Product decision:** either amend the guardrail by ADR ("attribution-only ad SDK permitted; no per-ride purchase values") and drop `send_ride_purchase`/`FirstRide` value fields, or keep CAPI Purchase and rewrite the "no data harvesting" claim. Recommended: keep `CompleteRegistration`, drop per-ride `Purchase` value — attribution survives, the behavioural stream stops.   Alternative: remove the SDK entirely — rejected pending the founder's paid-acquisition plan (rider-acquisition doc §5 says paid is a later test, so removal may be the cheapest right answer; escalate).
- Blast radius: `meta_conversions_service.send_ride_purchase` callers (settlement path — grep `send_ride_purchase` before touching; it is fire-and-forget, so removal cannot break settlement per its own rule 1); `meta_capi_deliveries` table; app.config plugins (store build).
- Rollout: server side is `app_settings`-gated (dataset id / token) — clearing the token stops CAPI without a deploy; client SDK removal needs a store release.   Rollback: re-add the token.
- Verification to close: `META_EVENTS.md` updated; privacy policy diff published; grep confirms no `Purchase` send.

### STRAT-005 — `platform_fee_percent` is an admin-writable percentage-commission setting with no reader
- Hierarchy: L2 Admin Dashboard & Operations › L3 Settings › L4 (orphan) › L5 "admin edits fees"
- Severity: LOW (today) / MEDIUM (latent)   Priority score: 2 × 3 × 3 = 18
- Status: VERIFIED   Existing item: new
- Adversary: malicious/confused insider; a future engineer who "wires up the existing setting"
- Evidence: `backend/schemas.py:303` (default 0.0 "0% commission"); `routes/admin/settings.py:267` (`ge=0, le=1.0`); `supabase_schema.sql:260`; `tests/test_admin_settings_write_allowlist_drift.py:139`; grep for readers → none
- What happens: nothing — which is the problem: an admin can set 1.0 and see no effect, or a developer can make it effective in one line.
- Root cause: legacy-app commission column carried into the new schema.
- Recommendation: remove from the PATCH model and write-allowlist (additive removal), keep the column (append-only), add a test asserting no module reads it.   Alternative: leave it — rejected: it is the one knob that turns Spinr into what it says it is not.
- Blast radius: admin settings page field (grep admin-dashboard `platform_fee_percent`), 3 tests.
- Rollout: backend + admin PR; no migration.   Rollback: revert PR.
- Verification to close: `test_admin_settings_write_allowlist_drift.py` no longer lists it; new "no reader" test.

### STRAT-006 — Maps daily budget breaker is not sized to launch volume and fails closed on the booking path
- Hierarchy: L2 Maps & Routing › L3 Budget governance › L4 S-maps-0x › L5 "budget exhausted mid-day"
- Severity: MEDIUM   Priority score: 3 × 4 × 3 = 36
- Status: INFERRED (per-ride call count unmeasured — `cost-inventory-table.md` says so) / VERIFIED (default $5, 503 behaviour)   Existing item: EXTENDS-E13, REC-D-01/REC-D-02 (`ride-experience/REPORT.md`) — the *sizing* angle is new
- Adversary: COO on launch day; vendor-outage (OSRM down multiplies calls)
- Evidence: `core/config.py:193` `MAPS_DAILY_BUDGET_USD = 5.0`; `utils/maps_budget.py` docstring ("proxy routes should respond 503"); §3.2 per-ride estimate $0.02–0.06 healthy, +$0.60 with OSRM down; `stripe-payout-readiness.md` §6.2 (10 drivers × 10 rides/day)
- What happens: on a good day near 100 rides, or any OSRM outage, the breaker opens and destination search/geocoding return 503 for every rider until UTC midnight.
- Root cause: the breaker was sized as "defence in depth" against runaway spend, never against the business's own volume plan; production override value unknown.
- Recommendation: set the budget from expected rides × §3.2 cost × 3 headroom (≈ $15–25/day at pilot scale) and make booking-critical SKUs degrade (cached geocode, haversine) rather than 503; keep the hard stop for AI text-search.   Alternative: remove the breaker — rejected (Maps cost visibility is maturity 1).
- Blast radius: `maps_proxy.py`, `_shared.py`, `maps_eta.py` budget checks; all riders.
- Rollout: env var change (no deploy on Fly secrets? — confirm); degrade-path is code + flag.   Rollback: env revert.
- Verification to close: load test at 150 rides/day against staging with OSRM disabled; no 503 from `/maps/*`.

### STRAT-007 — Production service areas include `riyadh` / `riyadh airport`; the repo contradicts itself on whether this is test data or an international market
- Hierarchy: L0 Vision ("Saskatchewan goes first; the model scales nationally") › L2 Maps & Routing › L3 Service areas › L4 S-maps-03 › L5 n/a
- Severity: LOW (data) / strategic UNKNOWN   Priority score: 2 × 2 × 5 = 20
- Status: VERIFIED (contradiction) / UNKNOWN (intent)   Existing item: ACTION_ITEMS ~L5535 ("test/dev data") vs ~L5700 ("intentional (international market), confirmed with product")
- Adversary: regulator (PIPEDA residency, SGI assumptions, CAD hard-coding in Meta/Stripe); competitor ("they're not even focused")
- Evidence: the two ACTION_ITEMS lines above; `DEFAULT_FARE` seeded verbatim on both rows; `CLAUDE.md` "Not a country-agnostic product"
- What happens: either stray test rows sit in a production table that gates dispatch, WAV and pricing — or an undocumented second country is being served by a product whose compliance, currency, tax and insurance logic are all Saskatchewan-specific.
- Recommendation: founder answers one question (§12 d); if test data, delete the rows via the admin UI and add a `country = 'CA'` check constraint; if real, an ADR is mandatory before any further work.   Alternative: ignore — rejected: it is in the dispatch-gating table.
- Blast radius: `service_areas` readers (dispatch, fares, heatmaps, WAV).   Rollout/Rollback: data change via admin; reversible.
- Verification to close: `SELECT name FROM service_areas` shows only Canadian areas, or an ADR exists.

### STRAT-008 — Live pricing config (no minimum fare, no per-minute rate, identical rates across vehicle classes) undermines the driver-take-home thesis more than commission ever could
- Hierarchy: L2 Ride Booking & Matching › L3 Fare estimation › L4 S-book-01 › L5 "short trip / slow trip"
- Severity: MEDIUM (business) — HIGH for driver retention   Priority score: 3 × 4 × 4 = 48
- Status: VERIFIED (config values as recorded in ACTION_ITEMS from a DB read) / INFERRED (Uber comparison, §6)   Existing item: **stuck** — the pricing item at ACTION_ITEMS ~L5680-5745 has drafted SQL and is "blocked on a pricing decision, not on more investigation" since 2026-08
- Adversary: competitor; a driver comparing take-home; COO (support tickets about $3 rides)
- Evidence: ACTION_ITEMS ~L5687-5739 (Regina 2/2/0/0/0, Saskatoon 4/1/0/0/0 for Economy and XL); `fare_service.py:36` `DEFAULT_FARE` has min_fare 8, per_min 0.25, booking_fee 2; `routes/admin/analytics.py:1339-1349` (deadhead "comes straight out of their earnings")
- What happens: a 1 km ride pays a Regina driver $4 gross before their own costs; a 20-minute crawl pays for distance only; XL costs the rider the same as Economy. Drivers experience exactly the income unpredictability the retention doc names as the #1 churn driver.
- Root cause: pricing was seeded by duplicating rows in the admin editor (ACTION_ITEMS root-cause note) and the decision to set real values has no owner.
- Recommendation: founder sets Economy min_fare (≥ any municipal floor — R12 to confirm Regina/Saskatoon bylaw), per_min ≥ $0.20, booking_fee per STRAT-002, and class multipliers; apply the already-drafted `UPDATE`s.   Alternative: leave pricing to "learn from data" — rejected: the data will be churn.
- Blast radius: every fare estimate in both areas; corporate `max_fare_per_ride` policies; no code change.
- Rollout: DB config, per area, immediate; announce to drivers first.   Rollback: `UPDATE` back to prior JSONB (record it before changing).
- Verification to close: estimate endpoint returns min_fare for a 300 m trip in each area.

### STRAT-009 — Three CLAUDE.md KPIs have no query at all, and no business KPI exists
- Hierarchy: L1 Objectives/KPIs › L2 Admin Dashboard & Operations › L3 Analytics › L4 (orphan for the missing three)
- Severity: MEDIUM   Priority score: 3 × 3 × 5 = 45
- Status: VERIFIED (§4)   Existing item: retention gap documented in CLAUDE.md (no ACTION_ITEMS id found); safety-rate and support-SLA gaps new; business-KPI absence new
- Adversary: CFO, regulator (safety rate), competitor (support SLA)
- Evidence: §4 table; `analytics.py:45-51,1241-1319`; grep results for incident rate and support SLA; `admin_mrr_at_cutoff` (migration 383) sums `driver_subscriptions` only
- Recommendation: three SQL functions in the migration-423 pattern: `admin_rolling_driver_retention(p_week)`, `admin_safety_incident_rate(p_start,p_end)` (safety_incidents ÷ completed rides × 10k), `admin_support_first_response(p_start,p_end)` (from ticket timestamps, or a Zoho pull); add `platform_margin`, `stripe_fee_pct`, `corporate_mrr`, `wallet_float` to `/earnings/overview`.   Alternative: Zoho/Sentry dashboards only — rejected: the KPI table is the contract; it should be answerable from the product.
- Blast radius: additive endpoints/functions.   Rollout: migrations + admin page; Rollback: drop functions.
- Verification to close: every CLAUDE.md KPI row cites an endpoint.

### STRAT-010 — Corporate SaaS, the declared "profit engine," cannot be bought today: dark, unpriced, no self-serve, no MRR metric
- Hierarchy: L2 Corporate / B2B Billing › L3 Invoicing/subscriptions › L4 S-corp-06 › L5 "a clinic signs up and pays"
- Severity: MEDIUM (business)   Priority score: 3 × 3 × 5 = 45
- Status: VERIFIED   Existing item: `CORPORATE_B2B.md` §11 known gaps (self-serve onboarding, policy enforcement); R6 lane owns correctness; the business-case sequencing is new
- Adversary: competitor (Captain Taxi); CFO
- Evidence: `routes/corporate_subscriptions.py:54` dark; migration 281 no seeded plans; `CORPORATE_B2B_GTM.md` §4 "Days 0-30: self-serve floor" vs `CORPORATE_B2B.md` §2 "Admin dashboard drives every B2B write until self-serve onboarding lands"; no corporate MRR function
- What happens: the GTM plan's first 30 days depend on a motion that does not exist; the line that funds the 0% promise produces $0 and nobody can see that it does.
- Recommendation: sequence corporate revenue **before** consumer promo spend: (1) founder prices 2 plans, (2) admin seeds them + Stripe prices, (3) enable billing for one pilot company (`subscription-pilot`), (4) add `corporate_mrr` to the overview, (5) then decide on self-serve build.   Alternative: lead with consumer growth — rejected by STRAT-002's slope (consumer volume loses money until the fee is on).
- Blast radius: none (config + one company).   Rollout: already flagged.   Rollback: `cancel_subscription` is never gated (by design, `routes/corporate_subscriptions.py:51-53`).
- Verification to close: one real `invoice.paid` webhook mirrored into `corporate_subscriptions`.

### STRAT-011 — "Spinr holds zero float by design" is false: rider and corporate wallets are prepaid balances the platform holds, and no report totals them
- Hierarchy: L2 Ride Completion & Payments › L3 Wallet settlement › L4 S-pay-02 › L5 "rider tops up wallet"
- Severity: LOW (today's balances are small) / MEDIUM (finance/legal treatment)   Priority score: 2 × 3 × 4 = 24
- Status: VERIFIED (mechanism) / ASSUMED (accounting/regulatory treatment — accountant + counsel)   Existing item: new
- Adversary: auditor; CFO
- Evidence: `docs/finance/stripe-payout-readiness.md` §2; `routes/wallet.py:139` (`top_up_wallet`), `CORPORATE_B2B.md` §3 (`corporate_wallets.balance`); ToS:66 discloses the wallet; no float KPI (grep `float|liabilit` in analytics → none)
- Recommendation: add `wallet_float = Σ rider wallet balances + Σ corporate wallet balances` to the financial endpoint; ask the accountant how prepaid balances are treated (customer deposits — liability) and whether any provincial/federal rule applies to holding them (ASSUMED; escalate).   Alternative: none needed — this is a reporting gap, not a code defect.
- Blast radius: additive.   Verification: number appears; accountant answer recorded in `.claude/context/memory.md`.

### STRAT-012 — The most urgent finding in the finance doc (payouts set to manual; one payout ever) was never filed as a tracked item
- Hierarchy: L2 Driver Earnings & Payouts › L3 Per-driver payouts › L4 S-earn-04 › L5 "platform moves money to its bank"
- Severity: MEDIUM (governance)   Priority score: 3 × 2 × 5 = 30
- Status: VERIFIED-IN-DOC (2026-09-12 Stripe pull) / VERIFIED (not in ACTION_ITEMS: grep `interval.*manual|payout.*manual|acct_1SSk` → 0)   Existing item: none — that is the finding
- Adversary: CFO; auditor
- Evidence: `docs/finance/stripe-payout-readiness.md` §7 (`interval: "manual"`, `po_…` 2026-03-31 $92.96, `available: $415.79`)
- What happens: money sits in Stripe; the doc says "needs an explicit owner decision" and the decision has no home.
- Recommendation: file it (ACTION_ITEMS band C, finance owner) and decide daily/weekly automatic vs deliberate manual with a calendar reminder. Not an engineering change.   Alternative: flip to automatic in code/API now — rejected per the doc's own CLAUDE.md-money-caution reasoning.
- Verification to close: ACTION_ITEMS entry + `settings.payouts.schedule.interval` re-read.

---

## §9 Rebuild Delta card — Epic: Monetization model (§7.3)

## Epic: Monetization model (cross-cutting: Corporate B2B · Driver Earnings & Payouts · Ride Completion & Payments · Promotions & Loyalty)
- Verdict per inherited pattern:
  - 0% fare commission as a code invariant — **KEEP** (`fare_service.py:228-233`; the one thing incumbents cannot copy)
  - Flat rider-side booking fee retained by platform — **KEEP, MODIFY** (turn on; disclose as Spinr's; make copy conditional — STRAT-002/003)
  - Spinr Pass driver N-day pass with daily ride quota — **REPLACE** the quota/force-offline mechanic; **MODIFY** the pass into an optional, benefits-based subscription (instant-payout-fee waiver, priority support, statement tooling) rather than a licence to work (wrong cost slope §3.3, pay-to-work optics §5, X18)
  - Corporate flat SaaS subscription — **KEEP** (right slope; dark-flagged correctly); **MODIFY** go-to-market (price, seed, pilot — STRAT-010)
  - Instant-payout fee 1.5% — **KEEP** (drivers choose it; Uber/Lyft parity), but net it against Stripe's cost and report it
  - Cancellation-fee platform share $0.50 — **KEEP** (covers the Stripe fee on the cancellation charge; disclose in policy)
  - `platform_fee_percent` — **REMOVE** (STRAT-005)
  - "Premium rider features" and "partner referrals" as stated revenue lines — **REMOVE from documents** until a story exists (STRAT-001)
  - Per-ride Meta `Purchase` conversions — **REMOVE** (keep attribution-only) or amend the guardrail by ADR (STRAT-004)
- Keep (already best-in-class): receipt itemisation with GST/PST lines; the attribution invariant `total_fare == driver_earnings + admin_earnings`; corporate wallet RPC; surge cap clamps.
- Uber/Lyft do: ~25% service fee nominal, ~30% effective take rate (uber.com/en-CA upfront-offers page + secondary sources, INFERRED) funding processing, insurance, support, marketing; instant pay at ~1.5%; business tier with invoicing.       Spinr today: 0% fare + $0 booking fee live + dormant driver pass + dark corporate SaaS → ≈ −$0.93/ride (§3).
- Clean-sheet Spinr would: **"flat, disclosed, and slope-matched."** A per-ride flat rider fee sized to processing + infra (the only cost that scales with rides is paid per ride); prepaid wallet and corporate top-ups as the *default* consumer rail (one processing fee per top-up, not per ride — the fee then covers infra/support, not Stripe); corporate SaaS as the margin engine; driver pass optional and benefits-based; every fee named "Spinr fee" on the receipt.      Why (the edge it creates): the honesty claim becomes literally true ("we keep $X per ride and nothing of the fare"), unit economics stop degrading with driver success, and the corporate line's economics improve with volume — the only line where that is true.
- How (architecture/pattern): no new tables. `service_areas.vehicle_pricing.booking_fee` (exists) → on; `financial_events.stripe_fee` (new column, R9 ledger) → measured margin; wallet incentive as a `promotions` row (exists); corporate plans seeded via `routes/admin/subscriptions`-style admin UI (exists); Spinr Pass quota gates behind a flag → off.      Who: founder (pricing + model memo), R9 owner (fee column), admin owner (copy + metric).      When: **Now** (fee on, docs true, Meta disclosure), **Next** (margin metric, corporate pilot, wallet default), **Later** (pass redesign), **Rewrite-only**: none — nothing here needs a rewrite.
- Incremental path from today (no big-bang): 1 — founder memo picks the model; ToS/ICA/PRD/CLAUDE.md updated (STRAT-001) → 2 — booking fee on in Regina/Saskatoon via DB config + conditional badge copy (STRAT-002/003; per-area, reversible) → 3 — `stripe_fee` captured + `platform_margin` on the overview (additive) → 4 — corporate plans priced + one pilot company billed (already flagged) → 5 — Spinr Pass quota flag off; pass re-scoped to benefits; welcome email updated → 6 — wallet-prepay nudge promo → 7 — Meta `Purchase` send removed, disclosure published.
- Cost/effort: S (steps 1–2, 5, 7), M (3, 4, 6) · Risk: low-medium (fee changes are visible mid-session to riders; announce) · Reversibility: high (all DB config / additive) · Build / Buy / Partner / Open source: build (trivial), Stripe stays the rail.
- Advantage type: **cost + trust** (a fee model whose honesty can be audited from the receipt); not feature-copyable in the sense that incumbents' P&L cannot adopt 0% fare — but the *flat-fee* pattern itself is easy to copy by a new entrant; the moat is the corporate + regulatory layer on top, not the fee.
- "Why not?": it doesn't exist because the fee was zeroed during pricing setup without a margin model to object; because "premium rider features" was a placeholder phrase nobody owned; and because the driver pass predates the vision text. Could something simpler get the same result? Yes: **steps 1–3 alone** (memo, fee on, margin metric) get ~80% of the value; everything else is optimisation.

---

## §10 If Uber/Lyft had to rebuild from scratch tomorrow — what a Spinr-native platform would do differently (all PROPOSED)

Structural · Economic · Operational · Technical · Experiential, sorted by how hard the idea is, not how good it sounds. Each line names the closest Spinr evidence so a reader can judge distance-from-today.

**Obvious (any competent rebuild does this)**
- Structural: one canonical implementation per component, parity tests instead of forks (W0 #2; CARTO-002). Spinr today: 5 CarMarkers.
- Economic: measure margin per ride from day one (`stripe_fee`, Maps dollars, infra amortised) — §3; Spinr today: none.
- Operational: automatic payouts with a reconciliation cron watched by a human on launch week (`stripe-payout-readiness.md` §4.5/§7).
- Technical: metrics that are scraped and alerted, not only emitted (C11, OBS-002).
- Experiential: in-app turn-by-turn (`ride-experience/REPORT.md` conditional go) — table stakes.

**Meaningful (real edge, achievable incrementally)**
- Economic: flat per-ride fee + prepaid rails as the default, so processing cost is per top-up not per ride (§9). Corporate SaaS as the margin engine, sold before consumer promo spend (STRAT-010).
- Structural: the "receipt is the contract" — every platform dollar appears as a named receipt line and the fare line is 100% driver's; a test enforces ToS ⇄ code (STRAT-001 verification).
- Operational: driver support response time as a *measured* KPI with a public SLA (retention doc §3 says this is where incumbents are weakest; Spinr today: unmeasured, §4 row 10).
- Experiential (driver): income predictability tooling — typical earnings by hour/area, per-trip breakdown with deadhead shown (retention doc §5; `efficiency` endpoint already computes deadhead).
- Regulatory: insurance-period, WAV and tax artefacts as first-class product surfaces (already built — make them visible to drivers/riders as trust signals).

**Difficult (worth it, expensive)**
- Technical: a double-entry ledger as the only money writer (R9's Rebuild Delta, `money-cra.md` §7) — the precondition for every margin/float/fee number above being trustworthy.
- Economic: dynamic *floor* pricing (minimum fare and per-minute that track municipal floors and fuel) rather than dynamic *ceiling* pricing — the anti-surge. Spinr today: min_fare 0 (STRAT-008).
- Operational: rural/offline trip completion with post-hoc fare reconciliation (greenfield-extensions §12; R8/R13).
- Structural: multi-city rollout kit (service area + pricing + bylaw checklist + SGI mapping) so "scales nationally" is a config change, not a rewrite — but only after STRAT-007 settles what "national" means.

**Genuinely novel (nobody does this; fits Spinr's model)**
- Economic: **publish the per-ride cost stack to drivers** ("this ride cost Spinr $0.84 to process; you kept $9.16; Spinr kept $1.00") — turns §3 from an internal weakness into the trust product. Requires STRAT-002's measurement first.
- Structural: **driver-advisory input on pricing config** (retention doc Tier 3) — contractor-safe if advisory, and the one thing a 30%-take incumbent structurally cannot do.
- Regulatory/trust: **a public transparency report** — take-rate (0%), surge-cap compliance, SOS counts, deactivation/appeal outcomes, data-request counts (greenfield §12 row 4). Most of the inputs exist as tables; none are published.
- Experiential (corporate): **duty-of-care as the corporate SKU** (SHA nurse-safety use case, GTM §2): check-in loop + trip share + insurance-period proof bundled for employers — Spinr already has the pieces; incumbents sell "expense management."

**Sounds good but should NOT be built**
- A percentage platform fee "just to cover Stripe" — it is the guardrail; a flat fee does the same job honestly (STRAT-002 alternative).
- A driver ride *quota* sold as a pass — wrong slope, pay-to-work optics, classification risk (X18); benefits-based pass instead (§9).
- Per-ride purchase events to ad networks for "growth" — contradicts the positioning; attribution-only if anything (STRAT-004).
- Premium rider tiers (queue-jumping, priority dispatch) — creates a two-class rider base on a fairness brand and complicates dispatch invariants; if a rider subscription is ever built it should be *prepaid rides at a discount* (wallet economics), not priority.
- Pooling, multi-country fleets, FX — explicitly out of scope (greenfield §13), and STRAT-007 shows the cost of even one stray non-CA row.
- Unbounded incentive ladders / quests as retention — rider-acquisition doc §4's own warning; retention doc §4's "avoid" list.

---

## §11 Sweep-catalog §2.1 — every item answered

| §2.1 item | Answer | Evidence / card |
|---|---|---|
| Every revenue stream (corporate SaaS, premium rider, partner referral) traceable to shipped code, pricing config, and a Stripe product | **Corporate SaaS:** code yes, pricing config table yes but no plan rows, Stripe product wired by `stripe_price_id` but must be hand-created, billing dark. **Premium rider:** no code, no config, no product. **Partner referral:** no code, no config, no product. **Undeclared streams that do trace:** Spinr Pass (code + Stripe Checkout + plans table, dormant), booking fee (config, $0 live), airport surcharge, cancellation share, instant-payout fee | §2.1, §2.2; STRAT-001, STRAT-010 |
| 0%-commission promise provably true: driver payout = fare − disclosed pass-through items only | **True at the fare layer** (`driver_earnings = total_fare − (booking_fee + airport_fee)`, both disclosed lines; tip 100% to driver). **Caveats:** the "pass-through" items are retained by Spinr, not passed through (airport remittance UNKNOWN); Stripe processing is absorbed by Spinr (so the driver truly gets 100%, at Spinr's cost); drivers may pay a pass and instant-payout fees outside the fare | §1 #1, §2.2 U2/U3/U5; STRAT-003 |
| Per-ride cost stack known: Stripe fees, Maps calls, SMS/OTP, push, infra, support minutes | **Built here (§3.2), every number sourced or marked:** Stripe ≈ $0.81 (INFERRED rate, VERIFIED bearer); Connect ≈ $0.02 (INFERRED); Maps $0.02–0.06 healthy / +$0.60 OSRM-down (INFERRED from 22-row inventory; unmeasured); SMS <$0.01 (INFERRED); push $0 (VERIFIED); infra $0.03–0.08 at 3k rides/mo (INFERRED, every vendor dollar UNKNOWN except Supabase); support minutes UNKNOWN. **Not known in-repo before this pass.** | §3; STRAT-002, STRAT-006 |
| Every CLAUDE.md KPI has a query/endpoint; unmeasured ones listed | 7 of 10 have an endpoint/metric; **unmeasured: rolling WoW driver retention, safety incident rate, support P1 response.** 3 of 7 are emitted-not-scraped. Business KPIs (margin, fee %, corporate MRR, float, CAC) absent entirely | §4; STRAT-009 |
| Feature list checked against "What Spinr Is NOT" — drift toward commission, unbounded surge, control-of-work, ad tracking, hidden fees | Commission: letter kept, dormant knob + take-rate vocabulary + undisclosed pass (STRAT-001/003/005). Surge: holding (R9). Control-of-work: quota/force-offline to watch (X18). **Ad tracking: drift — Meta SDK + per-ride Purchase CAPI, undisclosed (STRAT-004).** Hidden fees: holding; airport line UNKNOWN. Country-agnostic: contradiction on record (STRAT-007) | §5 |

---

## §12 Closing

### (a) Top 5
1. **STRAT-002** — Every consumer ride loses ≈ $0.93 today and nobody can see it: Stripe fees on 100% of GBV are borne by the platform with $0 per-ride revenue (booking fee zeroed in both live areas); the pass model has the wrong cost slope. Decision needed before any acquisition spend.
2. **STRAT-001** — The revenue model in ToS/PRD/CLAUDE.md (premium rider features, partner referrals) does not exist in code; the one that exists (driver pass, booking fee) is not in the documents. One memo + one PR.
3. **STRAT-004** — Meta ad SDK in both apps and a server-side `Purchase` conversion for every paid ride, undisclosed in the privacy policy and subprocessor list, against a published "no ad SDKs" guardrail.
4. **STRAT-008** — Live pricing config (no minimum fare, no per-minute, identical classes) makes short/slow trips pay drivers less than Uber would; the fix has drafted SQL and has been blocked on a human decision since 2026-08.
5. **STRAT-010 + STRAT-009** — The declared profit engine (corporate SaaS) is dark, unpriced and unsellable self-serve, and no metric would show revenue if it existed; three KPI rows and every business KPI are unmeasured.

### (b) Sweep-catalog §2.1 — see §11 (all five items answered).

### (c) NOT verified in this pass (do not assume clean)
- Any live DB state: whether any `driver_subscriptions`, `corporate_subscriptions`, or pilot-flag rows exist; current `service_areas.vehicle_pricing` values (cited from ACTION_ITEMS' recorded DB read, not re-read); production `MAPS_DAILY_BUDGET_USD` override; `app_settings.platform_fee_percent` current value.
- Any live Stripe data (connector failed): current balance, payout schedule, actual fee rates, Instant Payout cost to platform, dispute fees, Connect Canada pricing specifics.
- Actual per-ride Google Maps call counts (the inventory is qualitative; `REPORT.md` §9 says so); Twilio per-segment CA price (search did not surface the figure).
- Per-site `SURGE_CAP` clamp behaviour (count verified; semantics deferred to R9); receipt/GST correctness (R9); SOS/911 guardrail (R11); WAV/insurance correctness (R11/R12).
- In-app driver copy for instant-payout fee and cancellation split (INFERRED from policy docs, screens not read).
- Whether Zoho Desk measures a P1 SLA externally.
- The `cancellation-fee-policy.md` wording on the $0.50 platform share.
- Correctness of the formulas behind the 7 "measured" KPI endpoints (existence verified, math not re-derived).

### (d) Human-only questions (incl. unit-economics inputs only the founder has)
1. Which revenue model is intended — the documented one (premium rider + partner referrals) or the coded one (driver pass + booking fee + corporate SaaS)? (STRAT-001)
2. Booking fee: what amount, and in which areas, and may the rider copy say "goes to Spinr"? (STRAT-002/003)
3. Spinr Pass: intended prices, whether any area is meant to require it, and whether the daily ride quota is a deliberate product choice. Has anyone ever paid for one?
4. Corporate plans: monthly prices; which company is the pilot; has any company been billed?
5. Actual Stripe invoice for the last full month (fees, disputes, Instant Payout cost) and the negotiated rate, if any; Connect account count.
6. Monthly invoices for Fly, Railway (incl. OSRM), Supabase tier, Redis provider, Vercel, Sentry, LogRocket, Expo, Zoho, Twilio, email vendor — the renewal calendar has no dollar column.
7. Real ride volume and average fare for the last 30 days (replaces the 10 rides/day × $15 placeholders in §3 and in `stripe-payout-readiness.md` §6).
8. Support: minutes per ticket, tickets per 100 rides, who staffs P1.
9. Acquisition: any spend to date, referral payouts made, promo budget — CAC inputs.
10. `riyadh` / `riyadh airport`: test data or a market? (STRAT-007)
11. Airport surcharge: is any amount remitted to an airport authority, or is it platform revenue? (§2.2 U3)
12. Payout schedule: has the `interval: "manual"` decision been made since 2026-09-12? (STRAT-012)
13. Is Meta CAPI live in production (dataset id/token set in `app_settings`)? Is a paid App Promotion campaign running? (STRAT-004)
14. Is PostHog session replay enabled anywhere? (subprocessor list says off)
15. Which KPI rows would the founder actually page on, if any?

### (e) Escalations (claims lacking a primary source, or legal/finance decisions)
- **Counsel:** ToS:50 / ICA:62,157 describe revenue lines that do not exist and omit a driver-paid pass — materiality and re-consent implications (STRAT-001; ASSUMED).
- **Counsel / privacy:** Meta as an undisclosed processor receiving hashed identity + per-ride purchase values — PIPEDA openness/consent status (STRAT-004; ASSUMED; hand to R12).
- **Employment counsel:** driver pass + daily quota + force-offline under CRA four-factor / SK employment standards — extends `compliance.md` X18 (ASSUMED).
- **Accountant:** treatment of rider/corporate wallet balances as customer deposits; whether any provincial/federal rule applies to holding prepaid balances; GST on the booking fee and on Spinr Pass (STRAT-011; ASSUMED; R9 for tax mechanics).
- **Finance:** margin decision (STRAT-002) and the manual-payout decision (STRAT-012) — both are choices, not engineering defaults.
- **Municipal bylaw (R12):** whether Regina/Saskatoon impose a minimum fare or licence fee that the pricing config must respect (STRAT-008; UNKNOWN).
- **Airport authority (R12/R9):** whether a ground-transportation fee applies at Regina Airport and who remits it (§2.2 U3; UNKNOWN).

### Cross-references
- R8 `dispatch.md`: cited for state-machine correctness; no monetization overlap.
- R9 `money-cra.md`: MONEY-006 (float on estimate path), `:190` (Stripe fees unmeasured), §7 ledger Rebuild Delta — STRAT-002's measurement step lands there.
- R11 `trust-safety-fraud.md`: TSF-008 (no damage/cleaning-fee flow — a missing *disclosed* fee line, consistent with §5's hidden-fee row), TSF-010 (no trip PIN); 911 guardrail owned there.
- R12 `compliance.md`: X18 (contractor status with a pass fee); STRAT-004 handed to R12 for the PIPEDA determination.
- W0: HIST family "docs are snapshots" (W0 #4) is the mechanism behind STRAT-001; CARTO orphan rows in Promotions & Loyalty (116 units on a 0-bullet PRD footprint) are the inventory view of the same drift.

*End of R3 strategy lane.*
