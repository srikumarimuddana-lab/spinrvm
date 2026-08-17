# Spinr Independent Contractor Agreement — Draft for Legal Review

> **How this differs from `terms-of-service.md`.** The Terms of Service is a
> clickwrap policy rendered in a bare React Native `<Text>` component
> (`driver-app/app/legal.tsx`) and accepted by tapping through onboarding. This
> document is a **signed contract**, not a clickwrap policy — it is meant to be
> presented for e-signature (e.g. DocuSign/HelloSign, or an in-app signature
> capture step) at the point a driver applicant is approved to drive, before
> their first ride. It is written with ordinary Markdown headings and numbered
> clauses because it is not intended to render through the shared bare-`<Text>`
> legal viewer — treat formatting as intentional, not a mistake to strip out
> before use.
>
> **Why this exists as a document separate from ToS Part B.** `terms-of-service.md`
> Part B already states drivers are independent contractors in one clause
> (§11). That is enough for a clickwrap policy but is thin protection on its
> own against a misclassification challenge — a signed, standalone agreement
> with explicit indicia of independence (no minimum hours, no exclusivity, no
> employee benefits, driver bears operating expenses, driver may work for
> competitors) is the standard mitigant used across the industry, and matters
> more given the direction of recent Canadian gig-worker rulings outside
> Saskatchewan. See `docs/legal/terms-of-service.md` Part B and CLAUDE.md's
> "Driver classification" and "What Spinr Is NOT" sections — this agreement
> exists to put that product philosophy into a document a court or CRA
> auditor can actually read.
>
> **This is a draft, not legal advice.** It is grounded in this repository's
> actual driver-eligibility rules, insurance-period model, and fare logic as
> of 2026-08-17, but it has not been reviewed by a lawyer licensed in
> Saskatchewan or Canada, and has not been checked against current CRA
> guidance on the four-factor / control-and-integration tests for worker
> classification. Do not present this for signature until qualified counsel
> has reviewed it, and do not treat any clause below as changing how drivers
> are actually treated in the product — the agreement should describe reality,
> not the other way around.
>
> **Bracketed items** (`[LIKE THIS]`) mark facts this document cannot supply
> from the codebase alone — a real legal entity name, an address for notices,
> a specific payout schedule figure, or a number that is a business decision
> rather than an engineering fact. Fill these in before use; do not guess at
> them.

---

## BEGIN DRAFT

# SPINR INDEPENDENT CONTRACTOR AGREEMENT

This Independent Contractor Agreement ("Agreement") is entered into between
Spinr Technologies Inc., a corporation existing under the laws of
[PROVINCE/JURISDICTION OF INCORPORATION] with its principal place of business
at [SPINR REGISTERED ADDRESS] ("Spinr," "we," "us," or "our"), and the
individual completing driver onboarding and accepting this Agreement
electronically ("Contractor," "Driver," "you," or "your"). This Agreement is
effective as of the date you accept it electronically ("Effective Date").

## RECITALS

Spinr operates a technology platform (the "Platform") that connects riders
seeking transportation with independent drivers who provide rides using their
own vehicles. Spinr does not own or operate a fleet of vehicles, does not
employ drivers, and does not take a commission on individual consumer rides —
drivers keep 100 percent of the fare for every completed ride. Contractor
wishes to use the Platform to find and provide rides to riders as an
independent business, and Spinr wishes to grant Contractor access to the
Platform on the terms below.

## 1. INDEPENDENT CONTRACTOR STATUS

1.1. **Not an employee.** Contractor is an independent contractor, not an
employee, agent, partner, or joint venturer of Spinr. Nothing in this
Agreement creates an employment, partnership, agency, or franchise
relationship. Contractor is not entitled to minimum wage, overtime pay,
vacation pay, statutory holiday pay, workers' compensation coverage as an
employee, Employment Insurance, or any other benefit arising from an
employment relationship, except where mandatory law provides otherwise
despite this Agreement.

1.2. **No control over how you work.** Spinr does not set your work hours,
does not require a minimum number of hours online or rides accepted per day
or week, does not assign you a shift, does not require you to wear a uniform,
does not dictate the route you take to a destination (beyond what the app
suggests as guidance), and does not prohibit you from providing rides or
delivery services through any other platform, including a competitor, at the
same time you have the Spinr app open. You decide when to go online, which
ride requests to accept or decline, and when to go offline, subject only to
the safety and eligibility requirements in Section 2.

1.3. **You operate your own business.** You provide, own, and are solely
responsible for maintaining and insuring your own vehicle, fuel, mobile
device and data plan, and any other equipment you use to provide rides. You
bear all costs of operating your business, and Spinr does not reimburse
vehicle expenses, fuel, or maintenance. You are free to structure your
driving activity, including through a personal corporation or other business
entity, subject to Spinr's eligibility and background-check requirements
being met by the individual actually driving.

1.4. **Taxes.** You are solely responsible for reporting and remitting all
applicable income tax, Canada Pension Plan contributions as a self-employed
person, and, if you meet the relevant CRA registration threshold, GST/HST on
your driving income. Spinr does not withhold income tax, CPP, or EI premiums
from amounts paid to you. Where CRA thresholds require it, Spinr will provide
you a T4A-compatible year-end earnings summary to assist with your own tax
filing; that summary is a courtesy and does not change, and must not be
interpreted as changing, your status as an independent contractor.

1.5. **No exclusivity.** This Agreement does not require you to provide rides
exclusively through Spinr, and Spinr does not require a minimum acceptance
rate, minimum trip count, or minimum online time as a condition of continued
access to the Platform, except that a pattern of declining or cancelling
accepted rides may affect the ride requests you are offered, as described in
the Terms of Service.

## 2. ELIGIBILITY AND ONGOING COMPLIANCE

2.1. **Eligibility requirements.** To provide rides on the Platform, you must
at all times:

   (a) hold a valid Class 5 driver's license (or an approved equivalent), and
   have separate approval on file if you hold a Class 1–4 license instead;

   (b) have at least three years of licensed driving experience;

   (c) maintain a clean driving abstract, with no major violations and no
   Criminal Code driving offences in the past three years;

   (d) operate a vehicle less than ten years old that has passed its required
   annual inspection;

   (e) carry a ride-share endorsement on your vehicle insurance through SGI
   Auto Fund, in addition to whatever personal auto coverage you already
   carry, sufficient to cover the periods described in Section 4;

   (f) have a current Criminal Record Check and Vulnerable Sector Check on
   file with Spinr, renewed at least annually or as Spinr's policy requires.

2.2. **You keep these current.** You are responsible for renewing each
document above before it expires and for uploading proof of renewal through
the app. If any required document expires, the Platform will not let you go
online until it is renewed — this is a safety and insurance-coverage
requirement, not a disciplinary action, and Spinr is not liable for lost
earning opportunity during a period your documents have lapsed.

2.3. **Re-verification.** Spinr may periodically re-run a driving-abstract
or criminal-record check as permitted by law and as disclosed in the Privacy
Policy, to confirm continued eligibility. You consent to this ongoing
verification as a condition of continued access to the Platform.

## 3. HOW YOU PROVIDE RIDES

3.1. **Accepting rides.** When you are online and available, the Platform may
offer you ride requests. You may accept or decline any request. Once you
accept, you are expected to complete the trip in good faith, subject to the
cancellation terms in the rider- and driver-facing Terms of Service.

3.2. **Fares.** You keep 100 percent of the fare for every ride you complete.
Spinr does not take a commission on consumer rides. Fares are calculated
according to the fare structure disclosed to riders and to you in the app,
including any applicable surge multiplier, which is capped and always shown
before a rider books.

3.3. **Wheelchair-accessible and service-animal requests.** If you operate a
wheelchair-accessible vehicle (WAV), you may be matched to accessibility
requests when you are the nearest available accessible vehicle in the service
area. You may never refuse a ride to a rider because they are accompanied by
a service animal — accommodation of service animals is required by
Saskatchewan human rights law and by this Agreement, and a refusal is treated
as a serious conduct violation under Section 7.

## 4. INSURANCE AND COVERAGE PERIODS

4.1. **You carry your own insurance.** You are solely responsible for
maintaining valid, ride-share-endorsed vehicle insurance at all times you
intend to drive on the Platform. Spinr's provision of contingent or primary
commercial coverage described below supplements, and does not replace, your
own insurance obligations, and does not relieve you of the obligation to
disclose your ride-share activity to your insurer.

4.2. **Coverage periods.** Saskatchewan's commercial ride-share insurance
framework recognizes four coverage periods, and different insurance layers
apply to each:

   - **App off:** your personal auto insurance applies; Spinr provides no
     coverage.
   - **App on, available, no assigned ride (Period 1):** Spinr's contingent
     commercial liability coverage applies in addition to your personal
     insurance.
   - **En route to pickup, once a ride is assigned through drop-off
     (Periods 2–3):** Spinr's primary commercial coverage applies.

Spinr logs the transition between these periods for every trip, for
regulatory and insurance-audit purposes, and this log is retained and never
altered after the fact. You acknowledge and consent to this logging as
described in the Privacy Policy.

4.3. **Accidents.** You must report any accident that occurs while the app is
open, regardless of which coverage period applies, to Spinr and to SGI as
required by law, as soon as reasonably possible.

## 5. STANDARDS OF PLATFORM ACCESS

These standards describe the conditions of continued access to the Platform.
They are safety and conduct standards, not work direction — they do not
require you to work particular hours, follow a particular route, or accept
any particular ride, and nothing in this Section should be read to establish
control over how you perform your work beyond what is necessary for rider
and public safety.

5.1. You will not discriminate against a rider, or refuse a rider, on the
basis of a protected ground under the Saskatchewan Human Rights Code, and
will not refuse a rider accompanied by a service animal.

5.2. You will operate your vehicle safely and in compliance with applicable
traffic and vehicle-safety law.

5.3. You will not harass, threaten, or endanger a rider, and will not attempt
to arrange payment or contact with a rider outside the Platform to avoid
Spinr's safety and payment protections.

5.4. A serious safety report against you (an accident, a harassment
complaint, or a similar safety incident) may result in a temporary,
immediate hold on your access to the Platform while Spinr's safety team
investigates. This is a safety precaution, not a presumption of fault, and
you will be notified of the outcome and have an opportunity to respond as
described in Spinr's Driver Deactivation and Appeals Policy.

## 6. DATA AND PRIVACY

You acknowledge that Spinr collects and processes information about you,
including your license and vehicle documents, background-check results,
location data while the app is open, and insurance-coverage-period logs, as
described in the Privacy Policy, and that certain records (including trip
records, driver/vehicle linkage, and insurance-period logs) must be retained
for up to seven years under Saskatchewan's transportation regulations even if
you later request deletion of your account.

## 7. TERM AND TERMINATION

7.1. **Term.** This Agreement begins on the Effective Date and continues
until terminated as described below.

7.2. **Termination by you.** You may stop providing rides and terminate this
Agreement at any time, for any reason, by deactivating your driver account in
the app or by notifying Spinr.

7.3. **Termination or suspension by Spinr.** Spinr may suspend or terminate
your access to the Platform: (a) immediately, if you no longer meet the
eligibility requirements in Section 2, if a required document has expired, or
in response to a serious safety report as described in Section 5.4; or
(b) with [NOTICE PERIOD, E.G. 14 DAYS'] written notice, for any other reason
or no reason, consistent with the at-will, non-exclusive nature of this
relationship. Spinr will provide the reason for a suspension or termination
where it relates to a conduct or safety issue, and you may appeal as
described in Spinr's Driver Deactivation and Appeals Policy.

7.4. **Effect of termination.** Termination does not affect your right to be
paid for rides completed before termination. Sections 1, 6, 8, 9, 10, and 11
survive termination.

## 8. INDEMNIFICATION

You agree to indemnify and hold Spinr harmless from any claim, loss, or
liability arising from your operation of your vehicle, your breach of this
Agreement, or your violation of applicable law, except to the extent caused
by Spinr's own negligence or willful misconduct.

## 9. LIMITATION OF LIABILITY

To the maximum extent permitted by Saskatchewan and Canadian law, neither
party's liability to the other under this Agreement will exceed the total
fares paid to you through the Platform in the three months preceding the
event giving rise to the claim, except for a party's indemnification
obligations, breach of confidentiality, or liability that cannot be limited
under applicable law, including gross negligence or willful misconduct.

## 10. DISPUTE RESOLUTION AND GOVERNING LAW

10.1. This Agreement is governed by the laws of the Province of Saskatchewan
and the federal laws of Canada applicable in Saskatchewan.

10.2. Before commencing a legal proceeding, each party agrees to attempt to
resolve a dispute arising from this Agreement through good-faith negotiation
for [NUMBER] days, followed by mediation if negotiation does not resolve it.
[IF SPINR INTENDS TO USE ARBITRATION: insert an arbitration clause here and
have it reviewed by counsel — an arbitration clause that effectively waives a
worker's access to a labour board or court can itself be evidence used
against independent-contractor status in some jurisdictions, so this
decision should be made deliberately with counsel, not by default.]

## 11. GENERAL

11.1. **Entire agreement.** This Agreement, together with the Terms of
Service and Privacy Policy incorporated by reference, is the entire agreement
between you and Spinr regarding your provision of rides on the Platform, and
supersedes any prior agreement on the same subject.

11.2. **Amendment.** Spinr may update this Agreement from time to time. If
Spinr makes a material change, it will notify you in the app before the
change takes effect, and your continued use of the Platform after the change
takes effect constitutes acceptance. You may decline a material change by
ceasing to use the Platform.

11.3. **Assignment.** You may not assign this Agreement. Spinr may assign
this Agreement in connection with a merger, acquisition, or sale of all or
substantially all of its assets.

11.4. **Severability.** If any provision of this Agreement is found
unenforceable, the remaining provisions remain in effect.

11.5. **Notices.** Notices to Spinr should be sent to [LEGAL NOTICE ADDRESS /
legal@spinr.ca]. Notices to you will be sent to the contact information on
file in your driver account.

11.6. **Electronic signature.** You agree that your electronic acceptance of
this Agreement during onboarding has the same legal effect as a handwritten
signature.

## ACKNOWLEDGMENT

By accepting this Agreement, you confirm that you have read and understood
it, that you are entering into it as an independent business, and that you
understand you are not an employee of Spinr.

Contractor signature: ______________________  Date: ______________

Contractor name (printed): ______________________

Spinr Technologies Inc., by: ______________________  Date: ______________

## END DRAFT

---

## Where this fits in the onboarding flow

This Agreement is meant to be presented and signed once, at the point a
driver applicant passes eligibility review (Section 2 requirements
confirmed) and before they are permitted to go online for the first time —
not bundled into the same tap-through screen as the Terms of Service. See
the companion `docs/legal/independent-contractor-agreement.md` sibling files
(`terms-of-service.md`, `privacy-policy.md`) for how the two relate: the ToS
Part B governs ongoing platform conduct for every driver; this Agreement is
the underlying contractual relationship. Engineering follow-up needed before
this can be presented for real signature:

1. A signature-capture step (native e-signature widget or a DocuSign/HelloSign
   integration) — no such step currently exists in the driver onboarding
   flow.
2. Storage of the signed, timestamped agreement version per driver, so a
   specific driver's specific accepted version can be produced if challenged
   — parallel to how `legal_documents` versioning is intended to work for the
   ToS/Privacy Policy per `reports/audits/2026-07-22-legal-content-validation-v1.md`.
3. A process for re-presenting the Agreement for re-acceptance when it is
   materially amended (Section 11.2), distinct from the routine document
   uploads (license, insurance) already handled by `go_online` eligibility
   checks.

## Pre-signature sign-offs this draft depends on

1. **Bracketed fields** must be filled in by Spinr's actual legal/finance
   leadership (registered entity name and address, notice period, any
   arbitration clause) — not inferred from code.
2. **Arbitration clause (Section 10.2)** intentionally left as an open
   decision rather than drafted — counsel should weigh Spinr's interest in
   efficient dispute resolution against the risk that a broad arbitration
   clause becomes evidence of control in a future misclassification
   challenge.
3. **CRA classification review** — this draft describes the *product's*
   independent-contractor design accurately as of this writing, but a
   worker-classification determination is a legal conclusion, not an
   engineering one; have counsel confirm the agreement's language matches
   current CRA four-factor guidance before use.
4. **Consistency with the ToS** — if `docs/legal/terms-of-service.md` Part B
   is edited in the future, re-check this Agreement for drift; the two
   documents describe the same relationship and should not contradict each
   other.
