# Spinr Privacy Policy — Draft for Legal Review

> Same usage notes as `docs/legal/terms-of-service.md`: plain text only (no
> Markdown/HTML — both apps render this in a bare `<Text>` component), written
> as one combined document (Part A universal, Part B driver-specific) for the
> live shared `/settings/legal` path, splittable later for the per-audience
> `legal_documents` table. **Draft only — needs review by counsel licensed in
> Saskatchewan/Canada before publication**, and needs the specific
> pre-publication engineering/compliance sign-offs listed at the bottom of
> this file before the factual claims below are all true in production, not
> just architecturally intended.

---

## BEGIN PASTE-READY CONTENT

SPINR PRIVACY POLICY

Last updated: [INSERT PUBLICATION DATE]

Spinr Technologies Inc. ("Spinr," "we," "us," or "our") operates a ride-share platform for Saskatchewan, Canada. This Privacy Policy explains what personal information we collect, why we collect it, who we share it with, and the choices and rights you have. It applies to everyone who uses the Spinr rider app or driver app.

We are subject to Canada's Personal Information Protection and Electronic Documents Act (PIPEDA) and, for our drivers, to record-keeping obligations under Saskatchewan's ride-share regulatory framework. Where those two sets of obligations overlap — for example, PIPEDA gives you a right to ask us to delete your data, while Saskatchewan's transportation regulations require certain trip and driver records to be kept for a fixed number of years — we explain in the "Your Rights" section below exactly what that means for you.

PART A — HOW WE HANDLE YOUR INFORMATION

1. WHAT WE COLLECT

Account information: your name, phone number, and email address, collected when you sign up. We verify your phone number with a one-time passcode.

Trip information: pickup and drop-off locations, your route, fare, and payment amount for each ride. We only collect a saved home or work address if you choose to save one as a favorite — we never guess or infer where you live or work.

Location information: while you are matched to an active ride, we collect location updates so the app can show real-time position, calculate your fare and ETA, and — for drivers — establish which insurance coverage period applies to that moment of the trip. We retain the detailed GPS trail from a completed trip for 90 days, after which it is permanently deleted; we are separately required to retain the GPS trace at pickup and at drop-off only (not the full route) for three years, for regulatory and insurance-audit purposes.

Payment information: we use Stripe, a PCI-compliant payment processor, to handle card payments. We never see or store your full card number.

Communications: if you message your driver or rider through the in-app chat, or contact Support, we keep a record of that conversation. In-ride chat messages are automatically filtered to block phone numbers, email addresses, and off-platform payment requests, and are kept for 90 days after your trip unless the trip is part of an active safety investigation.

Safety information: if you add emergency contacts, we store their name, phone number, and relationship to you, encrypted at rest. If you use the SOS feature, we record the incident, your location at the time, and the outcome.

Device and diagnostic information: crash reports and basic performance data from your device, used to fix bugs and keep the app reliable.

Driver-specific information: driver's license number, vehicle registration, insurance documents, and background check results, collected to verify eligibility to drive and stored encrypted. This information is never shared with riders.

2. WHY WE COLLECT IT

We use your information to provide the ride-share service itself: matching you with a driver or rider, calculating fares, processing payment, showing real-time location during a trip, and providing customer support. We also use it to keep the platform safe — verifying driver eligibility, investigating safety reports, and operating the SOS feature — and to meet our legal and tax obligations, including issuing tax receipts and, for drivers, year-end earnings summaries.

We do not use your information to build advertising profiles, and we do not sell your personal information to third parties.

3. WHO WE SHARE IT WITH

We share the minimum information necessary between a matched rider and driver so the trip can happen — pickup/drop-off details, first name, vehicle description, and real-time location during the trip — but never your direct phone number to the other party; in-app calls and messages are relayed through Spinr.

We use a limited set of service providers to operate the platform, each of which only receives the specific data needed to perform its function, and each of which is contractually required to protect it:

- Supabase, our database provider, which stores substantially all of your account and trip data.
- Stripe, for payment processing.
- Twilio, to deliver SMS verification codes.
- Google (Firebase), for push notifications, app stability monitoring, and Google Maps for route calculation, which receives the addresses needed to calculate directions.
- Google (Gemini), a generative-AI service we use for certain in-app support and text-processing features. Text you enter in those specific features may be processed by this service.
- LogRocket, a session-diagnostics tool used on our driver app on iOS (currently disabled on Android) to help us reproduce and fix bugs.
- Railway and Fly.io, who host the servers that run the Spinr backend.
- Vercel, who hosts our web-based admin tools (not used for the rider or driver apps themselves).

Some of these providers operate outside Canada, principally in the United States, which means your information may be processed in the United States and could be accessible to US authorities under US law, consistent with PIPEDA's requirements for comparable protection when data crosses borders. Our primary database is hosted in Canada.

We will also share information where required by law — for example, in response to a valid court order — or to protect the safety of a user in an emergency.

4. HOW LONG WE KEEP YOUR INFORMATION

We keep different categories of information for different lengths of time, based on why we collected it:

Trip records, including fare and route summary: 7 years, to meet tax and financial audit requirements.

Driver and vehicle linkage to a specific trip: 7 years, for regulatory purposes.

Records of which insurance coverage period applied at each moment of a trip: 7 years, for insurance-audit purposes.

The detailed GPS trail of a completed trip: 90 days, then deleted; the GPS point at pickup and at drop-off only: 3 years.

Your account information, if you delete your account: most personal information is removed within 30 days. Certain records described above are kept longer because Saskatchewan's transportation regulations require it — this is explained further in "Your Rights" below.

5. YOUR RIGHTS

You can ask us to:

Access your information — from Settings, tap "Download My Data" to request a full export of your account information.

Correct your information — most profile fields can be edited directly in the app; for anything else, contact Support.

Delete your information — from Settings, tap "Delete Account." We will remove your personal information within 30 days, except for the specific categories described above (trip financial records, driver/vehicle linkage, insurance-period records, and the pickup/drop-off GPS point) that Saskatchewan's transportation regulations require us to keep for longer. When we retain those records past a deletion request, we anonymize what we can — for example, removing your name and rounding saved addresses — while keeping the fare and route summary intact for tax purposes.

Withdraw marketing consent — you can turn off marketing emails, SMS, and push notifications independently in Settings at any time; this does not affect service notifications about your rides.

If you have a privacy concern we haven't resolved to your satisfaction, you may also contact the Office of the Privacy Commissioner of Canada.

6. HOW WE PROTECT YOUR INFORMATION

We encrypt sensitive information such as driver license numbers and emergency-contact details at rest. Access to personal information inside Spinr is limited to staff who need it to do their job — for example, our support team can see the information needed to resolve your ticket, not your full account history by default. If we ever become aware of a data breach that poses a real risk of harm to you, we will notify the Office of the Privacy Commissioner of Canada within the timeframe PIPEDA requires and notify you directly as soon as reasonably possible, explaining what happened, what information was involved, and what we're doing about it.

7. CHILDREN'S PRIVACY

Spinr is not directed at children, and you must meet the age of majority in Saskatchewan to create an account. We do not knowingly collect personal information from children.

8. ACCESSIBILITY

We aim to meet WCAG 2.1 AA accessibility standards across our rider- and driver-facing surfaces, and we provide alternative formats of our policies on request. If you need this policy in an alternative format, or encounter an accessibility barrier anywhere in the Spinr app, contact accessibility@spinr.ca.

9. CHANGES TO THIS POLICY

If we make a material change to how we handle your information, we will notify you in the app before the change takes effect, and where required by law, we will ask for your renewed consent.

10. CONTACT US

Questions about this policy, or to exercise any of the rights above, contact our Privacy Officer at privacy@spinr.ca. To report a suspected security or privacy incident, contact security@spinr.ca.

PART B — ADDITIONAL INFORMATION FOR DRIVERS

11. WHAT WE COLLECT FROM DRIVERS SPECIFICALLY

In addition to the information described in Part A, as a driver we collect your driver's license number, vehicle registration and insurance documents, and the results of your Criminal Record Check and Vulnerable Sector Check, to verify your eligibility to drive on the platform. This information is stored encrypted and is never shared with riders. We also track which of the four insurance-coverage periods applies to you at every moment the app is open, as described in our Terms of Service, because Saskatchewan's commercial insurance rules require different coverage for each period — this tracking log is kept for 7 years and is never altered after the fact, only appended to, so it remains a reliable audit trail if your insurance coverage is ever in question.

12. TAX INFORMATION

Because you are an independent contractor, we provide a T4A-compatible year-end earnings summary where CRA thresholds require it, to help with your own income tax filing. We keep the underlying records supporting that summary for 7 years, consistent with CRA record-keeping requirements.

## END PASTE-READY CONTENT

---

## Pre-publication sign-offs this draft depends on

This draft describes the *intended* and largely-*built* data-handling architecture accurately, but the following need to be true in fact — not just in design — before every sentence above is verifiable if challenged. Cross-referenced in full in `reports/audits/2026-07-22-legal-content-validation-v1.md`:

1. **Data residency — partially closed 2026-08-17.** The Supabase project's actual region was confirmed via the Supabase Management API directly (not a screenshot or a guess): `ca-central-1`, status `ACTIVE_HEALTHY`. `backend/fly.toml` confirms `SUPABASE_REGION=ca-central-1` is set on Fly (the intended primary backend). **Still open:** a signed DPA with Supabase (a Legal/contractual action, not verifiable from this repo) and confirmation of the same env var on Railway (`railway.json` carries no env vars — Railway config is dashboard-managed and unverified from code; treat this as unconfirmed given `ACTION_ITEMS.md` C5's note that Railway is already drifting from `main`). See `reports/legal/supabase-region-attestation-checklist.md` for the full status table. Section 3's "primarily hosted in Canada" language is now backed by a verified fact for the primary backend, but the DPA gap means this section still can't be published as a fully attested fact until Legal closes it.
2. **Gemini and LogRocket disclosure** — this draft is what closes `docs/data-classification.md`'s open item **DV-16** (Gemini undisclosed) and the undisclosed-LogRocket gap found in the implementation-plan audit. Once published, `docs/vendor-inventory.md` and `docs/vendor-register.md` should be updated to mark both as "disclosed."
3. **GPS retention figure** — this draft uses **3 years** for the pickup/drop-off GPS trace, per CLAUDE.md and `docs/runbooks/data-breach.md`. `docs/data-classification.md` currently says 2 years in two places — that internal doc needs to be corrected to match before this policy goes live, so engineering's actual retention-purge job (once built — see DV-8 below) enforces the same number this policy promises.
4. **Scheduled deletion enforcement (DV-8)** — this policy promises deletion "within 30 days" and the specific longer regulatory-retention periods. As of this research pass, no scheduled hard-delete job actually enforces any of these horizons yet. Do not publish this policy's retention promises until that job exists, or the promise is false on day one.
5. **Accessibility statement** — section 8's WCAG 2.1 AA commitment is accurate as a target; do not let it be read as a claim that the mobile apps have been audited — `docs/ACCESSIBILITY.md` confirms neither has been, as of its last update. The `accessibility@spinr.ca` address referenced is listed as "to be provisioned" in `docs/accessibility-plan.md` — make sure it's live and monitored before this policy publishes it as a working contact.
