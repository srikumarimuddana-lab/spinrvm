# Dimension 21 — Threat Model (STRIDE / Attack Tree)

**Question:** What are the realistic attacks against this module — not the generic OWASP list, but the ones specific to a Saskatchewan ride-share app with physical-safety consequences?

---

## Methodology

Use **STRIDE** per asset, then rank with **DREAD** (or a simple 1-5 likelihood × impact).
Re-run the threat model whenever: a new surface is added, a vendor changes,
a new payment method ships, or after any sev-1 incident.

STRIDE categories:
- **S**poofing · **T**ampering · **R**epudiation · **I**nformation disclosure · **D**enial of service · **E**levation of privilege

## Assets per module

### Backend API
Ride record · fare calculation · driver-PII table · Stripe connection · admin JWT
signing key · Supabase service-role key · Redis (rate-limit state, OTP lockout)
· WebSocket fan-out channel · the 7 background loops.

### Admin Panel
Admin JWT · support-console PII access · bulk mutation endpoints · broadcast
messaging · promo creation · wallet-transfer tools · regulator export endpoints.

### Rider / Driver App
OTP SMS channel · device fingerprint · locally-stored tokens · WebSocket
session · location stream · payment-method tokens · deep-link intents.

---

## Checklist

### For each asset, answer:
- [ ] What's the worst-case if an attacker owns it?
- [ ] Who can reach it (unauth? any user? driver? admin?)
- [ ] What compensating controls exist (auth, rate limit, audit log, MFA, segregation of duties)?
- [ ] What would the attacker's "easy path" look like — and is it still easy?
- [ ] What detection exists — do we *know* when it's being attacked?

### Ride-share-specific threat scenarios to rule in or out:
- [ ] **Fake driver** — rider accepts a ride from a spoofed driver (non-registered vehicle impersonates). OTP + pre-ride driver photo + licence plate display mitigate.
- [ ] **Fake rider** — driver arrives to find no one; fake pickup + cancellation fee extraction.
- [ ] **GPS spoofing** — driver fakes movement to complete fare without driving.
- [ ] **Surge manipulation** — colluding drivers go offline in an area to trigger surge.
- [ ] **Account takeover via SIM swap** — attacker captures OTP.
- [ ] **Cancellation-fee farming** — driver accepts then ghosts until rider cancels.
- [ ] **SOS abuse** — prank panic calls to waste police resources; rate limit without blocking genuine emergencies.
- [ ] **Corporate wallet siphoning** — admin creates a self-owned corporate account, adds fake ride charges, transfers to personal.
- [ ] **Promo-bombing** — new-user promo stacked with stolen card; chargeback after ride.
- [ ] **Physical safety: driver-to-rider or rider-to-driver** — is the flow to freeze + investigate well-defined?
- [ ] **Data scraper** — enumerate driver profiles via support console or rider app.
- [ ] **Deep-link hijack** — malicious app registers `spinr://` and intercepts auth or payment flow.
- [ ] **Webhook replay** — stale Stripe event resubmitted; idempotency gate only.
- [ ] **Privileged insider** — admin with legitimate access exfiltrates PII via view-as-user or data export.

### STRIDE quick scan
- [ ] **Spoofing** — can an unauth request pass as authed? (JWT algo pinning, App Check, cert pinning)
- [ ] **Tampering** — can client data modify server-side outcomes? (signed fare, server-validated discounts)
- [ ] **Repudiation** — can a user deny an action? (immutable audit log with user_id + request_id)
- [ ] **Information disclosure** — PII in logs? verbose error traces? leaked stack on 500?
- [ ] **Denial of service** — unbounded queries, absent rate limits, WS fan-out without caps
- [ ] **Elevation of privilege** — trusted JWT role claim, missing role checks, SQL injection → service-role

### Evidence / outputs
- [ ] Threat-model document exists per module — not one for the whole app
- [ ] Each identified threat has a DREAD score and a status (accepted, mitigated, detected)
- [ ] Mitigation owner + target date per threat
- [ ] Re-review date set

---

## Common Findings

- **Model done once in 2024, never revisited** after new features shipped.
- **STRIDE categories skipped for "unlikely" threats** — precisely the ones that bite (e.g. privileged-insider exfiltration).
- **Ride-share-specific threats missing** — generic OWASP applied without thinking about physical safety of rider.
- **No detection for the identified threats** — model says "attacker could do X", but nothing would alert us when they do.
- **Mobile-client trust boundary mis-drawn** — device-side validation treated as authoritative (e.g. promo code validated on client only).

## How to Test

```bash
# Look for admin "view as user" impersonation — does it write an audit row?
grep -rn "view_as\|impersonate\|act_as" backend/routes/admin/

# Are admin JWT role + modules actually enforced server-side or only in UI?
grep -rn "modules\|require_role\|is_super_admin" backend/routes/admin/

# Server-validated fare / promo (not client-supplied)
grep -n "body.fare\|body.amount\|body.discount\|body.promo_value" \
  backend/routes/payments.py backend/routes/rides.py backend/routes/wallet.py

# Deep-link intent validation on mobile
grep -rn "Linking.addEventListener\|intentFilter\|scheme:" \
  rider-app/app/ driver-app/app/ rider-app/app.config.ts driver-app/app.config.ts
```

## Regulatory tags
`PIPEDA` (data-breach threats + notification obligations) · `PCI-DSS` (card threats) · `SK-HRC` (harassment / safety threats) · `E911` (SOS abuse must not block genuine emergencies) · `AML` (money-movement threats)
