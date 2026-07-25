# Spinr Terms of Service — Draft for Legal Review

> **How to use this file.** The text between the `BEGIN PASTE-READY CONTENT` and
> `END PASTE-READY CONTENT` markers below is written in plain text on purpose —
> no Markdown, no HTML, no clickable-link syntax — because both `rider-app` and
> `driver-app` render this content in a bare React Native `<Text>` component
> with no formatting engine (`rider-app/app/legal.tsx`, `driver-app/app/legal.tsx`).
> Copy only the text inside the markers into the admin dashboard's legal-text
> editor.
>
> **Where this goes today.** As of this writing, neither app actually reads the
> newer per-audience `legal_documents` table — both still fetch the single
> shared blob at `GET /settings/legal`, set via Dashboard → Settings →
> "Terms of Service" (a single textarea, no audience split). This document is
> written as ONE combined Terms of Service that works correctly pasted into
> that single shared field: Part A applies to everyone, Part B adds terms that
> apply only when you are acting as a driver. See the companion report,
> `reports/audits/2026-07-22-legal-content-validation-v1.md`, for the
> engineering follow-up needed to route audience-specific content through the
> newer table, and for a full list of factual claims below that depend on
> something not yet true in the live system (data-residency attestation,
> vendor DPAs, etc.) and must be re-checked before publication.
>
> **This is a draft, not legal advice.** It is grounded in this repository's
> actual code, data flows, and documented policies as of 2026-07-22, but it
> has not been reviewed by a lawyer licensed in Saskatchewan or Canada. Do not
> publish it to real users until qualified counsel has reviewed it.

---

## BEGIN PASTE-READY CONTENT

SPINR TERMS OF SERVICE

Last updated: [INSERT PUBLICATION DATE]

Welcome to Spinr. These Terms of Service ("Terms") govern your access to and use of the Spinr mobile applications, website, and related services (together, the "Service"), operated by Spinr Technologies Inc. ("Spinr," "we," "us," or "our"). By creating an account, requesting a ride, or driving on the Spinr platform, you agree to these Terms. If you do not agree, do not use the Service.

Spinr is a Canadian company. The Service is currently offered in Saskatchewan, Canada, and is designed around Saskatchewan's transportation and insurance regulatory framework. These Terms are governed by the laws of the Province of Saskatchewan and the federal laws of Canada applicable in Saskatchewan, without regard to conflict-of-law principles.

PART A — TERMS THAT APPLY TO EVERYONE

1. WHAT SPINR IS

Spinr connects riders who need a trip with independent drivers who provide rides using their own vehicles. Spinr is a technology platform. We do not own vehicles, and drivers on our platform are independent contractors, not Spinr employees. Spinr does not take a commission on individual consumer rides; drivers keep 100 percent of the fare they earn. Our monetization comes from optional premium rider features, corporate business accounts, and partner referrals, not from a cut of your trip.

2. ELIGIBILITY AND YOUR ACCOUNT

You must be at least the age of majority in Saskatchewan to create an account. You must provide accurate information when you register, including a valid phone number that we verify by one-time passcode. You are responsible for keeping your login credentials secure and for all activity on your account. Tell us immediately if you believe your account has been accessed without your permission.

3. BOOKING AND TAKING A RIDE

When you request a ride, we show you an estimated fare before you confirm, including any applicable surge pricing. Surge pricing, when active, is capped and is always shown to you before you book — we never apply it after the fact. Scheduled rides booked in advance are not subject to surge pricing.

Once a driver accepts your request, we share your pickup and drop-off information with that driver, along with your first name and, once you are matched, a way to communicate through the app. We share the driver's first name, vehicle description, license plate, and real-time location with you. We do not share your phone number directly with your driver, or the driver's phone number directly with you — messages and calls made through the app are relayed by us so neither party sees the other's actual number.

4. FARES, TAXES, AND PAYMENT

Fares are calculated from a combination of a base fare, distance, time, any applicable booking fee, and surge pricing where active, and are shown to you before you confirm your ride. Applicable Goods and Services Tax (GST) and, where applicable, Provincial Sales Tax (PST), are shown as separate line items on your receipt, not folded into a hidden total. We do not add undisclosed "service fees" — every charge on your receipt corresponds to a line item you can see.

Payment is processed through our payment partner, Stripe. We do not store your full card number. You may add funds to an in-app wallet, pay by card, or, if you are riding on a corporate account, have your ride billed to your employer's account subject to that employer's policy.

5. CANCELLATIONS

You may cancel a ride before it starts. Depending on how much time has passed since a driver accepted your request, a cancellation fee may apply; the app will tell you before you cancel if a fee applies to that specific cancellation. Rides cannot be cancelled by either party once the trip is underway — at that point the trip must be completed and any dispute handled through Support afterward.

6. SAFETY

Spinr includes an in-app SOS feature. Pressing and holding the SOS button alerts your listed emergency contacts and our safety team, and gives you a one-tap option to call 911. To be completely clear: Spinr's SOS feature is an assist tool. It is not a replacement for calling emergency services yourself, and Spinr never automatically dials 911 on your behalf, because automatic dispatch cannot reliably reach the correct emergency call center for your exact location. If you are in immediate danger, call 911 directly.

We log which "insurance period" every trip is in — essentially, whether a driver is offline, online and waiting for a ride, en route to pick you up, or actively driving you — because Saskatchewan's commercial ride-share insurance rules require different coverage for each of those situations. This tracking exists to make sure the right insurance coverage applies at every moment of your trip.

If you use our trip-sharing feature to share your live location and ETA with someone outside the app, that link automatically expires two hours after your trip ends — it is never a permanent link, and it never reveals the driver's phone number or last name.

7. CONDUCT

You agree not to use the Service to harass, endanger, or discriminate against another user; not to request rides for illegal purposes; and not to attempt to arrange payment or contact outside the app to avoid Spinr's safety and payment protections. We may suspend or terminate accounts that violate these rules, and safety-related reports (harassment, unsafe driving, accidents) are reviewed by our safety team, with serious incidents resulting in an immediate account hold pending investigation.

8. DISPUTES AND LIABILITY

If you have a complaint about a ride, contact Support through the app first — most issues are resolved there. To the maximum extent permitted by Saskatchewan and Canadian law, Spinr's liability for any claim arising from your use of the Service is limited to the amount you paid for the ride giving rise to the claim. Nothing in these Terms limits liability that cannot be limited under applicable law, including liability for gross negligence or willful misconduct.

9. CHANGES TO THESE TERMS

We may update these Terms from time to time. If we make a material change, we will notify you in the app before it takes effect. Continuing to use the Service after a material change takes effect means you accept the updated Terms.

10. CONTACT

Questions about these Terms can be sent to legal@spinr.ca. Safety concerns can be reported in-app or, for active emergencies, always call 911 first.

PART B — ADDITIONAL TERMS THAT APPLY WHEN YOU DRIVE FOR SPINR

These additional terms apply only to your use of the Service as a driver, in addition to Part A above.

11. YOU ARE AN INDEPENDENT CONTRACTOR

You provide rides on the Spinr platform as an independent contractor, not as an employee, agent, or partner of Spinr. You choose when to go online, which rides to accept, and how you operate your own vehicle. Spinr does not require minimum hours, does not mandate a uniform, and does not provide employee benefits. You are responsible for your own vehicle expenses, fuel, insurance, and income tax obligations arising from your driving income. Spinr will provide you with a T4A-compatible earnings summary at year end where CRA thresholds require it, to help with your own tax filing — this summary does not change your status as an independent contractor.

12. ELIGIBILITY TO DRIVE

To drive on Spinr you must hold a valid Class 5 driver's license (or an approved equivalent), have at least three years of licensed driving experience, have a clean driving abstract with no major violations in the past three years, and have a vehicle less than ten years old that passes its annual inspection. You must carry ride-share endorsement on your vehicle insurance through SGI Auto Fund, and you must have a current Criminal Record Check and Vulnerable Sector Check on file, renewed annually. You must keep these documents current — Spinr will not let you go online with an expired document, because doing so would put you in an insurance coverage gap.

13. INSURANCE PERIODS WHILE YOU DRIVE

Every moment you spend with the app open maps to one of four coverage periods under Saskatchewan's commercial ride-share insurance framework: your personal insurance applies when the app is off; Spinr's contingent commercial coverage applies when you are online and available but have no assigned ride; Spinr's primary commercial coverage applies once you are matched to a ride, from the moment you are assigned through drop-off. We log every transition between these periods for regulatory audit purposes, and that log is never deleted.

14. FARES, KEEPING WHAT YOU EARN, AND ACCESSIBILITY REQUESTS

You keep 100 percent of the fare for every ride you complete — Spinr does not take a percentage of your fare. If you drive a wheelchair-accessible vehicle, you may be matched to accessibility-requesting riders when you are the nearest available accessible vehicle in the service area. You may never refuse a rider because they use a service animal — Saskatchewan human rights law and these Terms both require you to accommodate service animals.

15. CONDUCT AND ACCOUNT STANDING

Passenger safety reports are taken seriously. A serious incident report (accident, harassment, or a safety complaint) may result in an immediate, temporary hold on your account while our safety team investigates, even before a final determination is made. This is a safety precaution, not a presumption of fault.

## END PASTE-READY CONTENT

---

## Splitting this into the per-audience `legal_documents` table (future state)

Once `rider-app`/`driver-app` are pointed at `GET /legal-documents?audience=&type=tos` (see the engineering gap noted in the validation report), this single document splits cleanly:

- `audience=rider, doc_type=tos` → Part A only.
- `audience=driver, doc_type=tos` → Part A + Part B, with the Part A/Part B section break removed so it reads as one continuous document.

No content needs to be rewritten to make that split — it's a copy-paste operation once the wiring exists.
