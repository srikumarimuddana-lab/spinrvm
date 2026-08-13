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
| Rider identity linked to trip | 7 years (hashed after 2 — **not yet implemented, ACTION_ITEMS.md B23**) | Balance audit vs. privacy |

After the 7-year window, trip records are **hard-deleted**, not anonymized
(recorded decision, ACTION_ITEMS.md B18, 2026-08-10 — corrects this file's
prior "anonymized... not deleted" claim, which never matched what
`purge_pii_retention()` Step H actually ships: migration 216's "Uber/Lyft
attributable retention" model, made operative by 289). GPS pickup/dropoff
still drops earlier, at the separate 3-year ceiling in Step A. The "hashed
after 2 years" identity promise on the row above is a distinct, still-open
gap — see B23; it is not satisfied by the 7-year hard delete, and the
literal fix (hashing `rides.rider_id` for every ride, not just DSAR ones)
would break active riders' own trip-history screens, so it needs its own
product/legal scoping before implementation.

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

1. Personal profile fields (name, email, home address, payment methods) → scrubbed within 30 days (implemented: `purge_pii_retention()` Step N, migration 296, ACTION_ITEMS.md B18). Home address specifically means `saved_addresses` — hard-deleted alongside the profile scrub, not anonymized. Payment methods are Stripe-held (no local table); a Stripe-side detach is a separate follow-up, not covered by Step N.
2. Trip records → **retained**, then **hard-deleted** (not anonymized) at the 7-year floor: recorded decision, ACTION_ITEMS.md B18, 2026-08-10 — corrects this line's prior claim, which never matched migration 216/289's shipped "Uber/Lyft attributable retention" model.
3. Safety incidents involving the user → retained in full for the longer of (7 years, investigation close + 2 years)
4. Driver documents (license scan, insurance proof) → retained 7 years post-deactivation for SGI defence
5. Communication logs (chat, support) → not yet verified against this exact wording — `ride_messages` (Step D) hard-deletes at 90 days, not "anonymized after 1 year." Flagged here as a further doc/code divergence found while reconciling this file for B18, not itself fixed by B18 — worth its own follow-up ticket rather than silently left.

Always log the deletion request in `user_deletion_requests` with the scope applied, for proof-of-compliance in audits.

## Provincial reporting (periodic)

- **Quarterly** ride volume + incident count to SGI — gap write-up in `docs/compliance/sgi-quarterly.md`; submission format/aggregation grain still unconfirmed (§5), no export tooling yet
- **Annual** driver roster with license + insurance status — SGI's own D00032/D00033 AcroForm PDFs can be filled from `drivers` data (`backend/services/data_transfer/sgi_form_filler.py` + `sgi_field_maps.py`), but there is no scheduled/on-demand job driving them yet
- **On-demand** trip record production within 14 days of subpoena or regulator request, target run < 30 min against prod — built: `scripts/compliance_export.py` (see `docs/compliance/sgi-quarterly.md` §1/§6 for the PII boundary and export shape it implements)

## Common pitfalls

- Don't conflate "account deleted" with "trip records deleted" — they are separate operations with different retention rules
- Don't ship a feature that nudges drivers toward minimum hours without legal review — reclassification risk
- Don't log raw addresses in compliance exports — city/postal-prefix only unless subpoena specifies
- Don't let a driver onboard without an active SGI ride-share endorsement, even in dev — the check must be environment-agnostic
- Don't assume other provinces are similar — this file is SK-specific; expanding to AB/MB requires a parallel file
