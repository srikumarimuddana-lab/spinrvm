# Regulatory — Saskatchewan

_Load when working on: driver onboarding, trip log retention, tax display, insurance endorsements, accessibility, deletion requests, provincial reporting._

Spinr is a Saskatchewan-first TNC (Transportation Network Company) regulated under the **Saskatchewan Traffic Safety Act**, the **Passenger Transportation Regulations**, and the **Saskatchewan Government Insurance (SGI) Auto Fund** rules. This file is a working checklist; the legally binding source is the actual statutes — **confirm with legal before shipping any change that touches this domain**.

## Driver onboarding — eligibility gate

Enforced at signup, again on every `go_online` call, and audited monthly:

| Requirement | Evidence | Re-check cadence |
|---|---|---|
| Class 5 driver's license, valid SK or reciprocal | License scan + SGI lookup | On expiry |
| Minimum 3 years licensed driving experience | License history from SGI | Once at onboarding |
| Clean driver abstract — no major violations in past 3 years | SGI abstract | Annually |
| No Criminal Code driving offences ever | Criminal record check | Annually |
| Criminal Record Check (CRC) + Vulnerable Sector Check (VSC) | Third-party attestation | Annually |
| Vehicle < 10 model years old | Registration + VIN decode | On vehicle change |
| Vehicle passes annual safety inspection | Inspection certificate | Annually |
| Ride-share endorsement on auto insurance | SGI Auto Fund confirmation | On renewal |
| Business license (where municipality requires) | License copy | Per municipal rule |

Failures of any item must hard-block `go_online` with a specific reason string — never a generic "not eligible".

## Insurance periods (SGI commercial layer)

See `domain-safety.md` and CLAUDE.md for the 0/1/2/3 table. Regulatory-specific notes:

- SGI requires **per-period logs retained 7 years** — never truncate
- Personal auto insurance covers **Period 0 only**. Any claim during Period 1+ goes through the TNC commercial layer.
- A gap in period coverage during an incident can void the claim — the audit trail is the legal defence
- Driver must carry proof of TNC endorsement; app displays a QR-code proof-of-coverage in driver profile

## Trip log retention (cannot be overridden by PIPEDA deletion)

| Data | Retention | Reason |
|---|---|---|
| Trip record (rider_id, driver_id, fare, times) | 7 years | CRA tax + provincial |
| Driver/vehicle linkage at trip time | 7 years | Insurance claim defence |
| GPS trace — pickup + dropoff only (not full route) | 3 years | Provincial audit |
| Insurance period transitions | 7 years | SGI audit |
| Receipts with tax line items | 7 years | CRA |
| Rider identity linked to trip | 7 years (hashed after 2) | Balance audit vs. privacy |

After the retention window, rows are anonymized (user_id nulled, coordinates rounded to city centroid), not deleted — preserves statistical continuity for regulatory reporting.

## Tax display

- GST (5%, federal) on every fare
- PST (6%, SK) on fare where applicable — ride-share currently PST-applicable in SK
- Must appear as **separate line items** on the rider receipt, not bundled
- Driver earnings summary is T4A-compatible when annual earnings exceed the CRA reporting threshold ($500 for T4A box 48 self-employment, subject to change — verify annually)
- Fare floor: follow municipal minimum if set (Regina, Saskatoon currently have none specific to TNCs — verify)
- Surge cap: provincial guideline is 2.5× auto (Spinr hard cap matches)

## Accessibility

- **Wheelchair-accessible vehicle (WAV)** requests must be served if a WAV driver is online in the service area. If none online, app shows estimated wait for next WAV availability and offers standard vehicle alternative.
- **Service animals**: mandatory accommodation. Driver refusal based on service animal is a terms violation → account review.
- **Visual impairments**: app must meet **WCAG 2.1 AA**. Screen-reader labels on every ride-flow control; no color-only state encoding.
- **Hearing impairments**: SMS fallback for every push notification affecting ride state; in-app chat available as alternative to driver phone call.

## Driver classification (contractor, not employee)

Provincial labour boards and CRA scrutinize control-of-work signals. Language and features must not imply employment:

Forbidden patterns:
- Mandatory shifts or minimum hours
- Required uniforms or app-provided branded apparel requirements
- "Employee handbook", "manager", "performance review" terminology
- Penalties for going offline (deactivation thresholds tied to hours, not safety/quality)
- Employer-style benefits (vacation pay, sick leave) without separate contractor structure

Allowed:
- Quality metrics (acceptance rate, rating) with transparent thresholds
- Optional incentive programs
- Training materials labeled as "resources", not mandatory
- Safety requirements (TNC endorsement, vehicle inspection) tied to regulatory compliance, not control-of-work

Any UX copy or feature that arguably crosses this line goes through legal before merge.

## Right-to-delete (PIPEDA × Saskatchewan Transportation Act)

When a user requests deletion:

1. Personal profile fields (name, email, home address, payment methods) → scrubbed within 30 days
2. Trip records → **retained** but anonymized: `user_id` nulled, coordinates rounded to city, narrative fields redacted
3. Safety incidents involving the user → retained in full for the longer of (7 years, investigation close + 2 years)
4. Driver documents (license scan, insurance proof) → retained 7 years post-deactivation for SGI defence
5. Communication logs (chat, support) → anonymized after 1 year; retained for dispute window

Always log the deletion request in `user_deletion_requests` with the scope applied, for proof-of-compliance in audits.

## Provincial reporting (periodic)

- **Quarterly** ride volume + incident count to SGI (template in `docs/compliance/sgi-quarterly.md` — to be created)
- **Annual** driver roster with license + insurance status
- **On-demand** trip record production within 14 days of subpoena or regulator request, target run < 30 min against prod — **not yet built**: `scripts/compliance_export.py` is referenced here but doesn't exist; see `docs/compliance/sgi-quarterly.md` §7 for the confirmed format/definition-of-done and its tracking status before assuming this tooling is live

## Common pitfalls

- Don't conflate "account deleted" with "trip records deleted" — they are separate operations with different retention rules
- Don't ship a feature that nudges drivers toward minimum hours without legal review — reclassification risk
- Don't log raw addresses in compliance exports — city/postal-prefix only unless subpoena specifies
- Don't let a driver onboard without an active SGI ride-share endorsement, even in dev — the check must be environment-agnostic
- Don't assume other provinces are similar — this file is SK-specific; expanding to AB/MB requires a parallel file
