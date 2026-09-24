# Clean-Sheet Audit — Sweep Catalog

Companion to `docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md`. The checklist each
lane sweeps. Every line is a **question to answer with evidence**, not a claim that the
gap exists. Mark each: Handled / Partial / Unhandled / N/A, with `path:line` or source.

---

## 1. How to use

- Lanes load only their sections (see the "Lane" tag on each heading).
- §3 edge cases feed the Scenario card (master prompt §7.2). Each one ends in a column
  "Dispute risk" — the goal is **zero situations that end in an unresolvable dispute**.
- §4 regulatory/tax items are ASSUMED until a primary source is cited.
- When a real incident adds a new check, append it here with the incident link.

---

## 2. Domain checklists

### 2.1 Vision, business model, business case — Lane R3
- Every revenue stream (corporate SaaS, premium rider, partner referral) traceable to shipped code, pricing config, and a Stripe product.
- 0%-commission promise provably true: driver payout = fare − disclosed pass-through items only.
- Per-ride cost stack known: Stripe fees, Maps calls, SMS/OTP, push, infra, support minutes.
- Every CLAUDE.md KPI has a query/endpoint; unmeasured ones listed.
- Feature list checked against "What Spinr Is NOT" — any drift toward commission, unbounded surge, control-of-work, ad tracking, hidden fees.

### 2.2 Requirements & traceability — Lanes R2, R4–R7
- Every PRD requirement → story → code → test (traceability.csv complete).
- Orphan code and unbuilt requirements listed.
- Acceptance criteria exist for every L4 story, including failure behaviour.

### 2.3 Architecture & design — Lanes R2, R19
- Single source of truth for: ride state, money, driver availability, feature flags, settings, design tokens.
- Duplicate implementations (state guards, rider/driver `_shared.py`, `features.py` legacy writes, forks in `docs/known-forks.md`).
- Layering violations (utils → routes imports), god files (> 1,000 lines), cyclic imports.
- ADRs current vs code (`docs/adr/`).

### 2.4 Workflows, sequences, data flows — Lanes R4–R8
- Sequence diagram per critical flow: sign-up/OTP, book, match/offer/accept, arrive, start, complete, pay, tip, rate, cancel, refund, dispute, payout, SOS, go-online, document expiry, corporate booking.
- For each step: who writes which table, which WS event fires, which metric increments, which audit/insurance row is appended, what happens if this step fails halfway.
- Data lineage for PII: collected → stored (encrypted?) → shared (vendors) → retained → deleted.

### 2.5 Business & functional logic — Lanes R8, R9
- Fare: base, distance, time, booking fee, minimum fare, surge (cap 2.5×, never retroactive, not on corporate/scheduled-outside-window), stops, waiting time, tolls, airport fees, promos, tax, tip, rounding.
- Cancellation fees: grace windows, who's at fault, no-show rules, disclosed before booking.
- Dispatch fairness: ranking inputs, any bias by area/rating, offer timeout, re-dispatch limits.
- Allowances/policies for corporate riders; payment-source fallback order.

### 2.6 Security & data security — Lane R10
- OWASP Top 10 + API Top 10 on every route; IDOR on every `{id}` path param.
- JWT trust model (admin trusted, rider/driver re-read from DB), token lifetimes, refresh rotation, logout/revocation.
- OTP hashing, lockout, dev-bypass disabled in prod.
- Secrets: none in repo/history; rotation runbook executed at least once.
- RLS: live vs dormant; service-role key exposure surface (see 2026-07-30 incident).
- PII in logs/Sentry/analytics/AI prompts — the CLAUDE.md never-log list.
- Dependency and container supply chain (Semgrep, audit, pinned images, SBOM).

### 2.7 Error handling & resilience — Lanes R13, all
- No silent `except`/warning-and-continue on DB/auth/payment/dispatch paths.
- Every user-facing error has a clear message + next step; no raw 500 text.
- Timeouts, retries with idempotency, circuit breakers on every vendor call.
- Client retry behaviour on 401/409/503; duplicate-tap protection on book/pay/accept.

### 2.8 Operations & internal workflows — Lane R7
- Driver onboarding verification SLA and queue; document expiry reminders.
- Refund/credit authority matrix and two-person rule above threshold.
- Support ticket routing, SLAs (P1 < 2 h), escalation to safety team.
- Lost-and-found, damage claims, cleaning fees — evidence standard and appeal.
- Service-area launch checklist (`docs/runbooks/saskatoon-launch.md`).

### 2.9 Observability, incidents, triage — Lane R13
- Sentry tags (domain/surface/ids/env) on every capture; metric names per CLAUDE.md.
- Alert for every SLA and KPI breach; on-call rota; runbook per alert.
- Incident history: MTTD, MTTR, recurrence; post-mortem actions closed?

### 2.10 Quality & release — Lane R14
- Real (non-stubbed) test for every state transition, fare branch, auth/RLS policy, Stripe webhook type.
- Mobile: smoke, crash-free rate, OTA update policy, forced-upgrade path for breaking API changes.
- Release gates in CLAUDE.md actually enforced by CI vs by convention only.

### 2.11 Integrations & vendors — Lane R15
- Per vendor: outage behaviour, fallback, cost/month, data residency, DPA, key location (`app_settings` vs env), rotation.

### 2.12 Support, KB, training, manuals — Lane R16
- Help articles for every rider/driver FAQ; answers match code; bilingual decision recorded.
- Driver training: safety, accessibility (WAV, service animals), contractor-safe language.
- Admin manual per admin module; runbooks tested.
- AI assistant: grounded in KB, cannot take unsafe actions, escalates to human.

### 2.13 UX & accessibility — Lane R17
- Loading/empty/error states; offline banner; one-hand use; glove/cold use; dark mode at night.
- WCAG 2.1 AA: labels, contrast, dynamic type, screen-reader booking path.

### 2.14 Governance & data — Lanes R12, R20
- Data classification, retention jobs actually running, deletion requests SLA, access reviews for admin modules, change-management (Change Impact Log compliance rate).

---

## 3. Ride-share edge-case catalog (Scenario cards) — Lanes R4, R5, R8, R9, R11

For each: expected industry behaviour, Spinr today, status, dispute risk.

### 3.1 Booking & matching
1. Double-tap "Request" → two rides / two holds?
2. Rider books while a previous ride is still active (at-most-one invariant).
3. No drivers for 5 min → auto-cancel; is the hold released and the rider told why?
4. Driver accepts at the same instant the rider cancels.
5. Two drivers accept the same ride (race guard → 409 + `ride_taken`).
6. Offer timeout while driver's phone is backgrounded/locked.
7. Pickup pin on wrong side of divided road / inside airport / gated community.
8. Scheduled ride: driver cancels 10 min before; surge active at dispatch time but not at booking.
9. Price changes between quote and confirm (known 12.12 → 16.46 km incident).
10. Rider books for someone else (guest) — who gets notifications and receipt?
11. WAV or service-animal request with no eligible driver online.

### 3.2 En route & pickup
12. Driver GPS frozen / drifting / teleporting.
13. Driver going the wrong way; ETA grows; rider wants to cancel — who pays the fee?
14. Rider no-show: wait timer, fee, evidence (driver location at pickup).
15. Wrong rider gets in (verification PIN?).
16. Rider changes pickup after acceptance.
17. Rider or driver phone dies before pickup.

### 3.3 In trip & completion
18. Phone dies / app killed / no connectivity mid-trip (rural SK) — who completes the trip and on what distance?
19. Driver forgets to end the trip; rider already out.
20. Driver ends trip early / far from destination.
21. Route deviation or long-hauling; safety alert vs fare adjustment.
22. Stops added/removed mid-trip; re-pricing disclosure.
23. SOS pressed; false SOS; SOS with no data connection.
24. Vehicle breakdown or collision mid-trip — insurance Period 3 evidence.
25. Rider intoxicated / vomits (cleaning fee evidence, photo, appeal).
26. Minor riding unaccompanied.
27. Extreme cold (−40 °C) — pickup wait tolerance, battery drain, stranded rider priority.

### 3.4 Payment, fraud & disputes
28. Card declined at completion; 3DS required after trip.
29. Stripe webhook arrives twice / out of order / never.
30. Refund issued twice; partial refund + chargeback on the same ride.
31. Tip added after payout already sent.
32. Promo stacking; referral self-referral; multi-account device farming.
33. Driver–rider collusion fake trips for incentives.
34. GPS spoofing apps; impossible speed between pings.
35. Account takeover via SIM swap; OTP brute force; stolen refresh token.
36. Chargeback: is the evidence pack (route, times, receipt) auto-assembled (`docs/runbooks/payment-dispute-evidence.md`)?
37. Lost item returned — fee, contact masking, abuse.
38. Corporate allowance exhausted mid-trip; company suspended mid-trip.

### 3.5 Driver lifecycle
39. Licence/insurance/registration expires while online or mid-trip.
40. Deactivation: reason shown, appeal path, data retained.
41. Driver changes vehicle; document re-verification.
42. Payout fails (bank account closed); negative balance.
43. Driver disputes a rider's low rating or false report.

### 3.6 Platform & ops
44. Deploy mid-ride (WS reconnect, state resync).
45. Redis down (in-process fallback loses locks/lockouts); Supabase down; failover to Railway.
46. Background loop runs twice across replicas.
47. Clock skew / time zones — **Saskatchewan does not observe DST**; check every schedule, statement period, and cutoff.
48. Old app version calling a changed API.
49. Admin mis-configures fare/surge/service area — guardrails and undo.

---

## 4. Regulatory, tax & CRA checks — Lanes R9, R12 (all ASSUMED until cited)

Verify each against a **primary source** (canada.ca/CRA, Government of Saskatchewan,
SGI, city bylaws, OPC). Record the URL and retrieval date.

### 4.1 CRA / federal tax
- GST/HST: rules for ride-sharing drivers' registration (CRA treats ride-sharing as a taxi business — confirm whether the small-supplier threshold applies) and how Spinr supports drivers in collecting/remitting.
- Who is the supplier of record for the ride (driver vs platform) and what that means for receipts and GST numbers shown.
- Rider receipts show GST/PST as separate lines (CLAUDE.md) — confirm PST applicability to passenger transport in Saskatchewan.
- Platform-operator reporting of seller (driver) income under the digital-platform reporting rules — confirm scope, data fields (incl. SIN/TIN collection), deadlines, and penalties.
- T4A (or equivalent) issuance rules and thresholds for driver payments; T4A job correctness (`lifespan.py` T4A annual job).
- Books & records retention period (CRA) vs Spinr's 7-year trip retention.
- Corporate customers: invoices meet input-tax-credit documentation requirements.
- Promotions/credits: tax treatment of rider credits and driver incentives.

### 4.2 Saskatchewan & municipal
- Provincial TNC/ride-share rules, and **city-level licensing** (Regina, Saskatoon) — licence, fees, data-sharing/reporting obligations to the city.
- SGI ride-share insurance product and the Period 0–3 mapping it requires.
- Driver eligibility rules (CLAUDE.md list) — confirm each against current source.
- Saskatchewan Human Rights Code — service animals, accessibility obligations.

### 4.3 Privacy & consumer
- PIPEDA: consent records, access/deletion SLAs, breach reporting, cross-border transfer disclosure for each vendor.
- Canada's Anti-Spam Legislation (CASL) for marketing push/SMS/email consent.
- Consumer-protection: price disclosure before booking, cancellation fee disclosure.
- Language: record the decision on French support (not mandated provincially — confirm; consider for federal/corporate customers).

### 4.4 Employment & classification
- Contractor classification indicators in app copy, training, and policies.
- Deactivation fairness / appeal standards emerging in Canadian gig-work law — confirm current Saskatchewan and federal status.
