# Spinr Corporate Master Services Agreement — Draft for Legal Review

> **What this is.** A Master Services Agreement ("MSA") for a business
> customer setting up a Spinr Corporate Account — the contract layer sitting
> on top of the product features already built in `services/corporate_*.py`
> and `routes/corporate_*.py` (wallet, allowance policy, KYB, member
> management). Today those features exist with no signed contract behind
> them; this draft is that contract. Delivery is out-of-band from the app —
> presented to a Company's authorized signatory during sales/onboarding
> (PDF, DocuSign, or similar), not rendered through any in-app legal-text
> viewer.
>
> **Structure.** This MSA is deliberately written to incorporate two
> documents by reference rather than duplicate their content inline:
>
> - an **Order Form** (Exhibit A) that carries the deal-specific terms —
>   company name, billing details, any subscription/platform fee, corporate
>   rate card, initial wallet funding, effective date, and term — so the MSA
>   body itself doesn't need to be renegotiated for every customer;
> - a **Data Processing Addendum** (Exhibit B, referenced but not drafted
>   here — see "What's still needed" below) covering Spinr's processing of
>   the Company's Authorized Riders' personal information, which a real
>   enterprise security/procurement review will ask for as a separate,
>   more technical document.
>
> **This is a draft, not legal advice.** It is grounded in this repository's
> actual corporate-account architecture (wallet deltas via
> `corporate_wallet_apply_delta`, allowance/policy configuration, KYB
> verification, admin-side reporting) as of 2026-08-17, but has not been
> reviewed by a lawyer licensed in Saskatchewan or Canada, and several
> commercial terms below are placeholders pending an actual pricing/legal
> decision — not something derivable from code. Do not present this to a
> prospective customer until qualified counsel has reviewed it and every
> bracketed placeholder has been resolved.
>
> **Bracketed items** (`[LIKE THIS]`) mark facts or business decisions this
> document cannot supply from the codebase alone. Fill these in deliberately;
> do not guess.

---

## BEGIN DRAFT

# SPINR CORPORATE MASTER SERVICES AGREEMENT

This Master Services Agreement ("Agreement") is entered into as of the date
signed on the applicable Order Form ("Effective Date") between Spinr
Technologies Inc., a corporation existing under the laws of
[PROVINCE/JURISDICTION OF INCORPORATION] with its principal place of
business at [SPINR REGISTERED ADDRESS] ("Spinr"), and the business entity
identified on the Order Form ("Company"). Spinr and Company are each a
"Party" and together the "Parties."

## 1. DEFINITIONS

**"Authorized Rider"** means an individual designated by Company, through the
Spinr corporate portal or otherwise, as eligible to book rides billed to
Company's Corporate Account, subject to Company's configured Policy.

**"Corporate Account"** means the account Spinr provisions for Company to
manage Authorized Riders, fund rides, configure Policy, and access reporting,
as further described in Section 3.

**"Corporate Wallet"** means the prepaid balance, funded by Company, from
which eligible rides booked by Authorized Riders are settled, subject to
Section 5.

**"Order Form"** means the ordering document, in the form of Exhibit A or a
Spinr-provided equivalent, that sets out Company's specific commercial
terms — legal name, billing contact, initial wallet funding, any
subscription or platform fee, rate card, term, and renewal terms — and is
incorporated into this Agreement by reference.

**"Policy"** means the ride-eligibility rules Company configures for its
Authorized Riders through the corporate portal — for example, permitted
booking windows, per-ride or per-period spend caps, service-area
restrictions, or purpose codes.

**"Rider Terms"** means Spinr's standard rider-facing Terms of Service and
Privacy Policy, which continue to apply to each Authorized Rider's individual
use of the Spinr rider app in addition to this Agreement.

**"Services"** means Spinr's corporate ride-booking platform, described in
Section 2.

## 2. THE SERVICES

2.1. **What Spinr provides.** Spinr provides Company with access to a
Corporate Account through which Company's Authorized Riders can book rides
on the Spinr platform, billed to Company subject to Policy, together with
reporting tools scoped to Company's own Authorized Riders and rides.

2.2. **What Spinr is, and is not.** Spinr is a technology platform. Spinr
does not own or operate vehicles and does not employ the drivers who provide
rides to Company's Authorized Riders — drivers are independent contractors
under a separate agreement with Spinr, as described in
`docs/legal/independent-contractor-agreement.md`. This Agreement does not
create any employment, agency, or contractor relationship between Company
and any driver, and Company will not attempt to direct, discipline, or
otherwise exercise control over a driver's work.

2.3. **No commission model unaffected.** Spinr does not take a commission on
individual consumer or corporate rides — drivers keep 100 percent of every
fare. Any fee Company pays Spinr under this Agreement is a fee for access to
the corporate platform and reporting tools, described on the Order Form, and
is separate from, and does not reduce, driver earnings.

2.4. **Availability.** Spinr will use commercially reasonable efforts to make
the Services available, but does not guarantee driver availability in any
given service area at any given time. [IF A SPECIFIC UPTIME/AVAILABILITY SLA
IS BEING OFFERED, STATE IT ON THE ORDER FORM RATHER THAN HERE, AND HAVE
COUNSEL REVIEW ANY COMMITTED SLA AGAINST ACTUAL SYSTEM CAPABILITY BEFORE IT
IS OFFERED.]

## 3. ACCOUNT SETUP AND VERIFICATION

3.1. **Know-Your-Business (KYB).** Before Company's Corporate Account is
activated, Company will provide the business verification information Spinr
requests (for example, business registration and authorized-signatory
information) to confirm Company is a legitimate business entity. Spinr
collects this information solely to verify eligibility for a Corporate
Account and to meet its own compliance obligations, and will retain it as
described in the Privacy Policy and this Agreement's data-protection terms.

3.2. **Authority.** The individual accepting this Agreement on Company's
behalf represents that they have authority to bind Company.

3.3. **Accurate information.** Company will keep its account, billing, and
Authorized Rider information accurate and current.

## 4. AUTHORIZED RIDERS

4.1. **Designation.** Company may add or remove Authorized Riders through the
corporate portal at any time. Company is solely responsible for who it
designates as an Authorized Rider and for promptly removing an individual's
access when they should no longer be able to book rides on Company's
account — for example, when their employment ends.

4.2. **Rider Terms still apply.** Each Authorized Rider must separately
accept the Rider Terms to use the Spinr rider app, in addition to riding
under Company's Corporate Account. Company is not a party to, and does not
assume liability for, an individual Authorized Rider's compliance with the
Rider Terms, but Company agrees to reasonably cooperate with Spinr's safety
and conduct enforcement (for example, promptly revoking an Authorized
Rider's access if Spinr reports a serious safety or conduct issue).

4.3. **Company's own workforce relationship.** Nothing in this Agreement
requires Company to designate any particular individual as an Authorized
Rider, and Spinr has no visibility into or involvement in Company's own
employment relationship with its Authorized Riders.

## 5. CORPORATE WALLET, ALLOWANCES, AND BILLING

5.1. **Funding.** Company funds its Corporate Wallet as described on the
Order Form — by prepayment, by an auto-topup arrangement triggered at a
low-balance threshold Company configures, or by invoiced net terms, as
applicable.

5.2. **Policy-based eligibility.** A ride booked by an Authorized Rider is
billed to the Corporate Wallet only if it falls within the Policy Company has
configured. Company is solely responsible for configuring Policy correctly;
Spinr is not liable for charges that fall within a Policy Company configured,
even if Company later determines the Policy was broader than intended.
Company may correct its Policy prospectively at any time through the
corporate portal.

5.3. **Fallback and declines.** If a ride falls outside Policy or the
Corporate Wallet has insufficient balance, the ride is either declined,
routed to the Authorized Rider's personal payment method, or handled
according to whatever fallback behavior Company has configured, consistent
with the corporate billing rules disclosed in the portal at the time.

5.4. **Invoicing (if applicable).** If Company's Order Form specifies invoice
billing rather than prepayment, Spinr will invoice Company on the cycle
stated on the Order Form, and Company will pay each invoice within
[NUMBER, E.G. 30] days of the invoice date. Amounts not disputed in good
faith within [NUMBER, E.G. 15] days of invoice, and not paid within the
payment period, may accrue interest at [RATE] per month or the maximum rate
permitted by law, whichever is lower, and Spinr may suspend Company's
Corporate Account for material non-payment after written notice.

5.5. **Taxes.** All fares billed to the Corporate Wallet include applicable
GST and, where applicable, PST as separate disclosed line items, consistent
with Spinr's rider-facing receipt practices. Any subscription or platform
fee under this Agreement is exclusive of applicable taxes, which Company is
responsible for in addition to the stated fee, except taxes on Spinr's net
income.

5.6. **Disputed charges.** Company may dispute a specific ride charge within
[NUMBER, E.G. 60] days of the charge by contacting Spinr corporate support.
Spinr will investigate in good faith and issue a wallet credit or refund
where the dispute is substantiated.

## 6. REPORTING AND DATA ACCESS

6.1. Spinr will make available to Company, through the corporate portal,
reporting on rides and spend attributable to Company's own Authorized
Riders only. Company will not attempt to access, and Spinr will not provide,
reporting data belonging to a different corporate customer.

6.2. Reports exported by Company (for example, CSV exports for expense
reconciliation) may contain Authorized Rider personal information (name,
trip details, fare) necessary for Company's own business purposes (expense
management, budgeting). Company will handle any personal information it
receives through these reports in compliance with applicable privacy law,
including PIPEDA, and consistent with the purposes disclosed to its own
employees.

## 7. DATA PROTECTION

7.1. **Roles.** As between the Parties, Spinr acts as the data processor with
respect to ride, location, and payment data generated when an Authorized
Rider books a ride, and Company acts as the data controller with respect to
its own decisions about who it designates as an Authorized Rider and what
Policy it configures. Each Party will comply with its respective obligations
under PIPEDA and any other applicable privacy law.

7.2. **Data Processing Addendum.** The Parties will execute a Data Processing
Addendum, attached as Exhibit B, governing the specific terms of Spinr's
processing of Authorized Rider personal information on Company's behalf,
including data location, sub-processors, breach notification timelines, and
data return or deletion on termination. The Data Processing Addendum is
incorporated into this Agreement by reference and controls over this
Section 7 to the extent of any conflict on data-protection matters
specifically.

7.3. **Sub-processors.** Spinr's current sub-processors are disclosed in
[LINK TO PUBLIC SUBPROCESSOR LIST — see the companion recommendation in the
Spinr Legal Ledger review to publish one; until it exists, reference
`docs/vendor-register.md` internally and provide the list to Company on
request during procurement review].

7.4. **Data residency.** Spinr's primary datastore is hosted in Canada
(`ca-central-1`), confirmed 2026-08-17 via direct query against the
Supabase Management API. **Still open before this representation is fully
safe to make to a real customer:** a signed DPA between Spinr and Supabase
itself, and confirmation that `SUPABASE_REGION=ca-central-1` is actually
set on Railway (unverifiable from this repository — Railway env config is
dashboard-managed, not committed). See
`reports/legal/supabase-region-attestation-checklist.md` for the full,
current status.

## 8. CONFIDENTIALITY

8.1. Each Party may disclose confidential business, technical, or commercial
information to the other in connection with this Agreement ("Confidential
Information"). The receiving Party will use the disclosing Party's
Confidential Information only to perform its obligations under this
Agreement, and will protect it using at least the same degree of care it
uses for its own confidential information of similar importance, and no less
than a reasonable degree of care.

8.2. Confidential Information does not include information that is or
becomes public through no fault of the receiving Party, was already known to
the receiving Party without an obligation of confidentiality, or is
independently developed without use of the disclosing Party's Confidential
Information.

8.3. This Section survives termination of this Agreement for
[NUMBER, E.G. 3] years, except for trade secrets, which remain protected for
as long as they remain trade secrets.

## 9. TERM, RENEWAL, AND TERMINATION

9.1. **Term.** This Agreement begins on the Effective Date and continues for
the initial term stated on the Order Form, renewing automatically for
successive terms of the same length unless either Party gives notice of
non-renewal at least [NUMBER, E.G. 30] days before the end of the then-current
term.

9.2. **Termination for cause.** Either Party may terminate this Agreement if
the other Party materially breaches it and fails to cure the breach within
[NUMBER, E.G. 30] days of written notice.

9.3. **Termination for convenience.** [DECIDE WHETHER EITHER PARTY MAY
TERMINATE WITHOUT CAUSE ON NOTICE, AND WHAT NOTICE PERIOD APPLIES — A
BUSINESS DECISION, NOT SOMETHING TO DEFAULT SILENTLY.]

9.4. **Effect of termination.** On termination: (a) Company's Authorized
Riders' access to book rides on the Corporate Account ends; (b) any
undisputed remaining Corporate Wallet balance will be refunded to Company
within [NUMBER, E.G. 30] days, less any amount owed to Spinr for completed
rides or fees; (c) Spinr will handle Company's and its Authorized Riders'
data as described in the Data Processing Addendum, including any agreed data
return or deletion timeline, subject to the retention periods required by
Saskatchewan's transportation regulations for trip and insurance records
described in the Privacy Policy, which apply regardless of this Agreement's
termination.

## 10. REPRESENTATIONS AND WARRANTIES

10.1. Each Party represents that it has the authority to enter into this
Agreement and that doing so does not violate any other agreement it is bound
by.

10.2. Company represents that the business information it provides during
KYB verification is accurate and that it has the right to designate the
individuals it identifies as Authorized Riders.

10.3. EXCEPT AS EXPRESSLY STATED IN THIS AGREEMENT, THE SERVICES ARE
PROVIDED "AS IS," AND SPINR DISCLAIMS ALL OTHER WARRANTIES, EXPRESS OR
IMPLIED, TO THE MAXIMUM EXTENT PERMITTED BY LAW.

## 11. LIMITATION OF LIABILITY

To the maximum extent permitted by Saskatchewan and Canadian law, neither
Party's aggregate liability arising from this Agreement will exceed the fees
Company paid to Spinr under this Agreement in the twelve months preceding
the event giving rise to the claim, except for: (a) a Party's
indemnification obligations under Section 12; (b) a Party's breach of
Section 8 (Confidentiality); (c) Company's payment obligations under
Section 5; or (d) liability that cannot be limited under applicable law,
including gross negligence, willful misconduct, or death or personal injury
caused by a Party's negligence. Neither Party is liable to the other for
indirect, incidental, or consequential damages, except as provided in the
carve-outs above.

## 12. INDEMNIFICATION

12.1. Spinr will indemnify Company against a third-party claim that the
Services, as provided by Spinr and used in accordance with this Agreement,
infringe that third party's intellectual property rights.

12.2. Company will indemnify Spinr against a third-party claim arising from
Company's breach of this Agreement, Company's misconfiguration of Policy in
a way that violates applicable law, or Company's own violation of applicable
law in its use of the Services.

## 13. INSURANCE

Spinr maintains commercial general liability insurance appropriate to its
business as a technology platform operator. [CONFIRM ACTUAL COVERAGE AND
LIMITS WITH SPINR'S INSURANCE BROKER BEFORE STATING A SPECIFIC FIGURE HERE —
DO NOT GUESS AT A LIMIT.] This Section does not create or expand any
insurance obligation with respect to individual driver-vehicle coverage,
which is addressed in `docs/legal/independent-contractor-agreement.md` and
the rider-facing Terms of Service.

## 14. COMPLIANCE WITH LAW

Each Party will comply with applicable law in performing this Agreement,
including PIPEDA, Canada's Anti-Spam Legislation (CASL) with respect to any
marketing communications sent to Company's contacts, and, for Spinr,
Saskatchewan's transportation and insurance regulatory framework applicable
to the underlying rides.

## 15. DISPUTE RESOLUTION AND GOVERNING LAW

This Agreement is governed by the laws of the Province of Saskatchewan and
the federal laws of Canada applicable in Saskatchewan. The Parties will
attempt to resolve a dispute through good-faith negotiation between
designated business contacts before commencing formal proceedings. [DECIDE
WHETHER DISPUTES ARE LITIGATED IN SASKATCHEWAN COURTS OR SUBJECT TO
ARBITRATION, AND HAVE COUNSEL DRAFT THE SPECIFIC CLAUSE — A CORPORATE
COUNTERPARTY'S OWN PROCUREMENT/LEGAL TEAM WILL LIKELY NEGOTIATE THIS
SECTION.]

## 16. GENERAL

16.1. **Order of precedence.** If there is a conflict between this Agreement,
an Order Form, and the Data Processing Addendum, the Data Processing
Addendum controls on data-protection matters, the Order Form controls on
commercial terms specific to that Company, and this Agreement controls on
everything else.

16.2. **Assignment.** Neither Party may assign this Agreement without the
other's consent, except in connection with a merger, acquisition, or sale of
substantially all assets.

16.3. **Notices.** Notices to Spinr should be sent to
[LEGAL NOTICE ADDRESS / legal@spinr.ca]. Notices to Company should be sent
to the billing or authorized-signatory contact on the Order Form.

16.4. **Force majeure.** Neither Party is liable for a delay or failure to
perform caused by circumstances beyond its reasonable control.

16.5. **Entire agreement.** This Agreement, the applicable Order Form, and
the Data Processing Addendum are the entire agreement between the Parties
regarding the Services, and supersede any prior agreement on the same
subject.

16.6. **Amendment.** Spinr may update the general terms of this Agreement
with [NUMBER, E.G. 30] days' notice to Company's billing contact for
non-material changes; a material change requires Company's written
agreement, which may be given electronically. Order Form terms specific to
Company may only be changed by mutual written agreement.

## SIGNATURES

Spinr Mobility Inc., by: ______________________  Date: ______________

Name / Title: ______________________

Company: ______________________, by: ______________________  Date: ______________

Name / Title: ______________________

---

## EXHIBIT A — ORDER FORM (template)

| Field | Value |
|---|---|
| Company legal name | [ ] |
| Billing contact (name, email) | [ ] |
| Authorized signatory | [ ] |
| Effective Date | [ ] |
| Initial term | [ ] |
| Renewal term | [ ] |
| Billing method | ☐ Prepaid wallet &nbsp; ☐ Auto-topup &nbsp; ☐ Invoiced net terms |
| Subscription / platform fee (if any) | [ ] |
| Corporate rate card / fare terms | [ ] reference standard rider fare structure unless a negotiated rate applies |
| Initial wallet funding | [ ] |
| Service areas covered | [ ] |
| Special Policy terms (if negotiated) | [ ] |

## EXHIBIT B — DATA PROCESSING ADDENDUM

See the companion file `docs/legal/corporate-data-processing-addendum.md`,
drafted separately from this MSA body since it is a more technical document
a corporate customer's security/procurement review will scrutinize on its
own terms — categories of personal information processed, purposes,
sub-processor list (cross-referencing `docs/legal/subprocessor-list.md` and
`docs/vendor-register.md`), data location/residency commitments, security
measures, breach notification timeline, audit rights, and data return/
deletion terms on termination. It mirrors, from Spinr's side as processor,
the same structure `docs/dpa-register.md` uses to track DPAs Spinr signs
with its own vendors. Execute both documents together as one signing
package once counsel review is complete.

## END DRAFT

---

## What's still needed before this can be sent to a real customer

1. **Every bracketed placeholder** — entity details, notice periods,
   termination-for-convenience terms, an actual insurance-limit figure, and
   the arbitration-vs-litigation decision in Section 15 — resolved by Spinr's
   actual legal/finance leadership, not inferred from code.
2. **Exhibit B (the Data Processing Addendum)** drafted as its own document,
   per the note above — a serious enterprise buyer's security review will
   ask for it specifically and will not accept the MSA's data-protection
   section alone as a substitute.
3. **Pricing model decision** — this draft assumes Spinr may charge Company a
   subscription/platform fee separate from ride fares (consistent with
   CLAUDE.md's statement that corporate accounts are a monetization pillar),
   but leaves the actual fee structure to the Order Form. Confirm this
   matches the real go-to-market pricing model before using this template in
   a live deal.
4. **Consistency check against the product.** Section 5 (wallet/billing) and
   Section 6 (reporting) describe the `corporate_wallet_apply_delta` and
   corporate reporting behavior as currently built; re-check this Agreement
   for drift any time those services change materially — see
   `.claude/context/domain-corporate.md`'s cascade-effect checklist for what
   else reads the same state.
