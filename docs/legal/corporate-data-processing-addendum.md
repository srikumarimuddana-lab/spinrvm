# Spinr Corporate Data Processing Addendum (MSA Exhibit B) — Draft for Legal Review

> **What this is.** The Exhibit B that `corporate-master-services-agreement.md`
> references but explicitly deferred drafting. **Not to be confused with
> `docs/dpa-register.md`**, which tracks DPAs Spinr signs *with its own
> vendors* (Supabase, Stripe, Twilio...) — this is the mirror document, from
> Spinr's side as processor, that Spinr offers *to* its corporate customers
> covering Spinr's processing of their Authorized Riders' personal
> information. A real enterprise security/procurement review will ask for
> this specifically and will not accept the MSA's Section 7 alone.
>
> **This is a draft, not legal advice**, modeled on standard DPA structure
> (categories/purposes, sub-processors, security measures, breach
> notification, audit rights, return/deletion), and has not been reviewed
> by counsel licensed in Saskatchewan or Canada. Several fields are
> placeholders pending real commitments Spinr can actually verify — do not
> fill them in with anything not independently confirmed.

---

## BEGIN DRAFT

DATA PROCESSING ADDENDUM

This Data Processing Addendum ("DPA") is Exhibit B to, and forms part of,
the Master Services Agreement between Spinr Technologies Inc. ("Spinr,"
acting as processor) and Company (acting as controller of its Authorized
Riders' personal information for the purposes described below).

## 1. DEFINITIONS

Terms not defined here have the meaning given in the Master Services
Agreement. "Personal Information" means information about an identifiable
individual, consistent with PIPEDA. "Processing" means any operation
performed on Personal Information, including collection, storage, use, and
disclosure.

## 2. ROLES

2.1. Company is the controller of the decision to designate an individual
as an Authorized Rider and of the Policy governing their ride eligibility.

2.2. Spinr is the processor of ride-related Personal Information generated
when an Authorized Rider books and takes a ride — pickup/drop-off location,
fare, trip time, and payment settlement details — processed on Company's
behalf for the purpose of providing the Services.

2.3. This DPA does not change Spinr's role as controller with respect to an
Authorized Rider's use of the Spinr rider app outside the scope of
Company's Corporate Account (for example, personal rides an Authorized
Rider pays for themselves) — that remains governed by Spinr's standard
Privacy Policy.

## 3. SCOPE OF PROCESSING

| | |
|---|---|
| **Categories of Personal Information** | Name, ride history, fare and payment settlement data, and any information the Authorized Rider provides in-app (e.g. saved addresses) that becomes visible in reports Company accesses |
| **Categories of data subjects** | Company's Authorized Riders |
| **Purpose of processing** | Providing the ride-booking Services, billing rides to the Corporate Wallet per Company's Policy, and generating the reports described in MSA Section 6 |
| **Duration** | For the term of the Master Services Agreement, plus the retention periods described in Section 8 below |

## 4. SPINR'S OBLIGATIONS

4.1. Spinr will process Personal Information only as necessary to provide
the Services and as instructed by Company through its Policy configuration,
except where otherwise required by law (for example, Saskatchewan's
transportation-regulatory retention requirements, which apply regardless of
Company's instructions).

4.2. Spinr will implement appropriate technical and organizational security
measures, including [DESCRIBE ACTUAL MEASURES — e.g. encryption at rest for
sensitive fields, role-based access control, audit logging of admin access —
PULL FROM ACTUAL SECURITY PRACTICE, DO NOT INVENT].

4.3. Spinr will ensure personnel authorized to process Personal Information
are subject to confidentiality obligations.

## 5. SUB-PROCESSORS

5.1. Company consents to Spinr's use of the sub-processors listed in
Spinr's public Subprocessor List (`docs/legal/subprocessor-list.md` /
[LIVE URL]), as updated from time to time.

5.2. Spinr will notify Company of a new sub-processor with material access
to Authorized Rider Personal Information at least [NUMBER, E.G. 30 DAYS]
before the change takes effect, giving Company an opportunity to object on
reasonable data-protection grounds.

## 6. DATA SUBJECT REQUESTS

If an Authorized Rider makes a request to Spinr directly to access, correct,
or delete their Personal Information, Spinr will handle it under Spinr's
standard Privacy Policy and will notify Company where the request affects
Company's own records (for example, a deletion request affecting historical
spend reports Company has already generated).

## 7. BREACH NOTIFICATION

Spinr will notify Company without undue delay, and in any case within
[NUMBER, E.G. 72 HOURS] of becoming aware, of a confirmed breach of security
safeguards affecting Company's Authorized Riders' Personal Information,
including the information reasonably available at the time: nature of the
breach, categories and approximate number of Authorized Riders affected, and
steps taken or planned. See `docs/legal/breach-notification-letter-template.md`
for Spinr's internal notification-content template.

## 8. RETENTION, RETURN, AND DELETION

8.1. On termination of the Master Services Agreement, Spinr will, at
Company's election, delete or return Authorized Rider Personal Information
Company designates, within [NUMBER, E.G. 90 DAYS], except for records
Saskatchewan's transportation regulations require Spinr to retain
regardless of the Master Services Agreement's termination (trip financial
records, driver/vehicle linkage, and insurance-period logs — see Spinr's
Privacy Policy and Data Retention Schedule).

8.2. Where Spinr retains a record past termination under Section 8.1, Spinr
will restrict its use to the regulatory purpose requiring retention.

## 9. AUDIT RIGHTS

9.1. On reasonable written notice, no more than once per 12-month period
absent a security incident, Company may request that Spinr provide evidence
of its compliance with this DPA — for example, a summary of relevant
security certifications or a completed security questionnaire — rather than
an on-site audit, unless the Parties agree otherwise.

## 10. DATA LOCATION

Spinr's primary datastore is hosted in Canada. [CONFIRM AGAINST THE SAME
DATA-RESIDENCY ATTESTATION GATING THE PRIVACY POLICY AND MSA DRAFTS —
`reports/legal/supabase-region-attestation-checklist.md` — BEFORE MAKING
THIS REPRESENTATION IN A SIGNED DPA.] Some sub-processors listed in the
Subprocessor List process data outside Canada, principally in the United
States, as disclosed there.

## 11. GENERAL

This DPA is governed by the same law as the Master Services Agreement. If
there's a conflict between this DPA and the Master Services Agreement on a
data-protection matter, this DPA controls.

## END DRAFT

---

## Pre-execution notes

1. **Section 10's data-residency claim depends on the same open
   attestation gate as the Privacy Policy draft.** Do not sign this DPA
   with a real corporate customer until that's actually closed — a signed
   DPA is a much higher-stakes place to be wrong about data residency than
   a Privacy Policy sentence.
2. Section 4.2's security measures list needs real content from
   engineering/security, not invented specifics.
3. This DPA and `docs/legal/corporate-master-services-agreement.md` should
   be executed together as one signing package — update that file's
   Exhibit B placeholder to point here now that this draft exists.
