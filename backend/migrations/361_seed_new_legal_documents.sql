-- 361_seed_new_legal_documents.sql
--
-- Seeds the 6 remaining shared legal_documents rows (rider + driver each,
-- 12 rows total) that were still drafts-only: community-guidelines,
-- non-discrimination, accessibility, cancellation-fees, promotions-referral,
-- insurance-periods. Terms of Service and Privacy Policy were already
-- published live on 2026-08-17 (see migration 49 comment and
-- docs/legal/legal-text-publication-checklist.md); this closes the
-- remaining gap in the app's 8-section Legal screen, at the explicit
-- direction of the product owner and WITHOUT counsel review -- same
-- accepted-risk pattern already recorded for ToS/Privacy. Content is
-- copied verbatim from the resolved docs/legal/*.md drafts as of
-- 2026-08-21; see legal-text-publication-checklist.md for the open
-- gating conditions each of these documents still carries (counsel review
-- on all six, plus a few document-specific factual gaps noted per row).
--
-- Requires migration 360 (widens legal_documents.doc_type CHECK to allow
-- these doc_type values) to run first.
--
-- Idempotent: ON CONFLICT (audience, doc_type) DO NOTHING -- never
-- overwrites a row an admin may have already edited via the dashboard
-- since these seed values were drafted. Forward-compatible, no schema
-- change, no locks (12-row insert on a tiny table).
--
-- Rollback: (manual, additive-only so safe to leave in place, but if
-- needed) DELETE FROM legal_documents WHERE doc_type IN (
--   'community-guidelines','non-discrimination','accessibility',
--   'cancellation-fees','promotions-referral','insurance-periods'
-- ) AND version = 1;

INSERT INTO legal_documents (audience, doc_type, content, version)
VALUES
    ('rider', 'community-guidelines', $doc0$SPINR COMMUNITY GUIDELINES

Last updated: August 21, 2026

Spinr works because riders and drivers trust each other. These Guidelines
describe what we expect from everyone on the platform, and what happens when
those expectations aren't met. They apply in addition to, not instead of,
the Terms of Service.

TREAT EACH OTHER WITH RESPECT

Riders and drivers are expected to treat one another courteously. Harassment,
threats, discriminatory remarks, or unwanted contact of any kind are not
tolerated, whether they happen in the vehicle, through in-app chat, or after
the trip ends.

SAFETY COMES FIRST

Drivers are expected to operate their vehicle safely and follow the rules of
the road. Riders are expected not to distract or interfere with a driver's
ability to drive safely. Anyone — rider or driver — who asks the other
person to break traffic law, drive recklessly, or exceed the vehicle's safe
passenger capacity is in violation of these Guidelines.

NO DISCRIMINATION

Spinr does not tolerate a driver refusing a rider, or a rider treating a
driver differently, because of race, religion, ethnicity, national origin,
disability, sex, gender identity, sexual orientation, age, or any other
ground protected under the Saskatchewan Human Rights Code. See our
Non-Discrimination Policy for the full detail, including the specific rule
that service animals must always be accommodated.

NO REQUESTS TO GO OUTSIDE THE APP

Don't ask the other person to pay you directly, arrange a future ride
outside the app, or exchange personal contact information to avoid Spinr's
safety and payment protections. This protects both riders and drivers — it's
how Spinr can step in if something goes wrong.

RESPECT THE VEHICLE AND EACH OTHER'S PROPERTY

Riders are expected to leave the vehicle in the condition they found it.
Deliberate damage, mess requiring professional cleaning, or theft from the
vehicle is a serious violation and may result in a cleaning or damage charge
in addition to account action.

NO FRAUD OR MANIPULATION

Creating multiple accounts to get around a suspension, manipulating fares,
submitting false safety reports, or abusing promotions and referral offers
are all violations of these Guidelines and of the Terms of Service.

WHAT HAPPENS IF THESE GUIDELINES AREN'T FOLLOWED

Most disagreements are minor and don't affect your account. For a serious
violation — safety, harassment, discrimination, or fraud — Spinr's safety
team may place a temporary hold on the account while it investigates, and
may permanently deactivate an account where the investigation substantiates
the violation. Drivers can appeal a deactivation decision under our Driver
Deactivation and Appeals Policy. Riders whose account is deactivated can
contact Support to ask for the reason and request review.

REPORTING A CONCERN

Report a safety or conduct concern through the app immediately after a
trip, or in an emergency, use the in-app SOS feature and call 911 if you are
in immediate danger.$doc0$, 1),
    ('driver', 'community-guidelines', $doc0$SPINR COMMUNITY GUIDELINES

Last updated: August 21, 2026

Spinr works because riders and drivers trust each other. These Guidelines
describe what we expect from everyone on the platform, and what happens when
those expectations aren't met. They apply in addition to, not instead of,
the Terms of Service.

TREAT EACH OTHER WITH RESPECT

Riders and drivers are expected to treat one another courteously. Harassment,
threats, discriminatory remarks, or unwanted contact of any kind are not
tolerated, whether they happen in the vehicle, through in-app chat, or after
the trip ends.

SAFETY COMES FIRST

Drivers are expected to operate their vehicle safely and follow the rules of
the road. Riders are expected not to distract or interfere with a driver's
ability to drive safely. Anyone — rider or driver — who asks the other
person to break traffic law, drive recklessly, or exceed the vehicle's safe
passenger capacity is in violation of these Guidelines.

NO DISCRIMINATION

Spinr does not tolerate a driver refusing a rider, or a rider treating a
driver differently, because of race, religion, ethnicity, national origin,
disability, sex, gender identity, sexual orientation, age, or any other
ground protected under the Saskatchewan Human Rights Code. See our
Non-Discrimination Policy for the full detail, including the specific rule
that service animals must always be accommodated.

NO REQUESTS TO GO OUTSIDE THE APP

Don't ask the other person to pay you directly, arrange a future ride
outside the app, or exchange personal contact information to avoid Spinr's
safety and payment protections. This protects both riders and drivers — it's
how Spinr can step in if something goes wrong.

RESPECT THE VEHICLE AND EACH OTHER'S PROPERTY

Riders are expected to leave the vehicle in the condition they found it.
Deliberate damage, mess requiring professional cleaning, or theft from the
vehicle is a serious violation and may result in a cleaning or damage charge
in addition to account action.

NO FRAUD OR MANIPULATION

Creating multiple accounts to get around a suspension, manipulating fares,
submitting false safety reports, or abusing promotions and referral offers
are all violations of these Guidelines and of the Terms of Service.

WHAT HAPPENS IF THESE GUIDELINES AREN'T FOLLOWED

Most disagreements are minor and don't affect your account. For a serious
violation — safety, harassment, discrimination, or fraud — Spinr's safety
team may place a temporary hold on the account while it investigates, and
may permanently deactivate an account where the investigation substantiates
the violation. Drivers can appeal a deactivation decision under our Driver
Deactivation and Appeals Policy. Riders whose account is deactivated can
contact Support to ask for the reason and request review.

REPORTING A CONCERN

Report a safety or conduct concern through the app immediately after a
trip, or in an emergency, use the in-app SOS feature and call 911 if you are
in immediate danger.$doc0$, 1)
ON CONFLICT (audience, doc_type) DO NOTHING;

INSERT INTO legal_documents (audience, doc_type, content, version)
VALUES
    ('rider', 'non-discrimination', $doc1$SPINR NON-DISCRIMINATION POLICY

Last updated: August 21, 2026

Spinr is committed to providing a platform free of discrimination for every
rider and driver, consistent with the Saskatchewan Human Rights Code and the
Canadian Human Rights Act where applicable.

WHAT THIS POLICY COVERS

Riders and drivers may not be refused service, treated differently, or
subjected to a lower standard of service on the basis of:

- race, ancestry, or place of origin
- religion or creed
- ethnic or national origin
- disability (physical or mental)
- sex, including pregnancy
- gender identity or gender expression
- sexual orientation
- marital or family status
- age
- receipt of public assistance

This list follows the protected grounds under the Saskatchewan Human Rights
Code as of this policy's last update.

SERVICE ANIMALS

A driver may never refuse a ride, charge an additional fee, or ask a rider
to remove a service animal because the rider is accompanied by one. This
applies regardless of the driver's own allergies or preferences. Refusing a
rider with a service animal is treated as a serious violation of the Terms
of Service and Community Guidelines, and repeated or deliberate refusal may
result in permanent deactivation.

If a rider believes they were refused because of a service animal, they
should report it immediately through the app so Spinr's safety team can
investigate. Spinr will not disclose the identity of the person who filed
the report to the driver being investigated.

WHEELCHAIR-ACCESSIBLE VEHICLE (WAV) REQUESTS

Where a wheelchair-accessible vehicle is online and available in the service
area, WAV ride requests are matched to that vehicle. Spinr does not currently
guarantee WAV availability in every service area at every time, and is
working to expand accessible vehicle coverage. If no WAV is available, the
app will tell the rider before they book, rather than silently matching them
to an inaccessible vehicle.

DRIVER OBLIGATIONS

Drivers must accommodate a rider's disability-related needs where reasonably
possible — for example, allowing extra time for a rider to enter or exit the
vehicle, or assisting with a mobility device at the rider's request. A
driver may decline a specific ride only for reasons unrelated to a protected
ground (for example, the vehicle's safe passenger or cargo capacity is
genuinely exceeded), and must be able to explain that reason if asked.

HOW TO REPORT A CONCERN

Report a discrimination concern through the app's Support flow, or by
emailing support@spinr.ca. Every report is reviewed by Spinr's safety team.
A substantiated violation is handled under the Community Guidelines and,
for drivers, the Driver Deactivation and Appeals Policy.$doc1$, 1),
    ('driver', 'non-discrimination', $doc1$SPINR NON-DISCRIMINATION POLICY

Last updated: August 21, 2026

Spinr is committed to providing a platform free of discrimination for every
rider and driver, consistent with the Saskatchewan Human Rights Code and the
Canadian Human Rights Act where applicable.

WHAT THIS POLICY COVERS

Riders and drivers may not be refused service, treated differently, or
subjected to a lower standard of service on the basis of:

- race, ancestry, or place of origin
- religion or creed
- ethnic or national origin
- disability (physical or mental)
- sex, including pregnancy
- gender identity or gender expression
- sexual orientation
- marital or family status
- age
- receipt of public assistance

This list follows the protected grounds under the Saskatchewan Human Rights
Code as of this policy's last update.

SERVICE ANIMALS

A driver may never refuse a ride, charge an additional fee, or ask a rider
to remove a service animal because the rider is accompanied by one. This
applies regardless of the driver's own allergies or preferences. Refusing a
rider with a service animal is treated as a serious violation of the Terms
of Service and Community Guidelines, and repeated or deliberate refusal may
result in permanent deactivation.

If a rider believes they were refused because of a service animal, they
should report it immediately through the app so Spinr's safety team can
investigate. Spinr will not disclose the identity of the person who filed
the report to the driver being investigated.

WHEELCHAIR-ACCESSIBLE VEHICLE (WAV) REQUESTS

Where a wheelchair-accessible vehicle is online and available in the service
area, WAV ride requests are matched to that vehicle. Spinr does not currently
guarantee WAV availability in every service area at every time, and is
working to expand accessible vehicle coverage. If no WAV is available, the
app will tell the rider before they book, rather than silently matching them
to an inaccessible vehicle.

DRIVER OBLIGATIONS

Drivers must accommodate a rider's disability-related needs where reasonably
possible — for example, allowing extra time for a rider to enter or exit the
vehicle, or assisting with a mobility device at the rider's request. A
driver may decline a specific ride only for reasons unrelated to a protected
ground (for example, the vehicle's safe passenger or cargo capacity is
genuinely exceeded), and must be able to explain that reason if asked.

HOW TO REPORT A CONCERN

Report a discrimination concern through the app's Support flow, or by
emailing support@spinr.ca. Every report is reviewed by Spinr's safety team.
A substantiated violation is handled under the Community Guidelines and,
for drivers, the Driver Deactivation and Appeals Policy.$doc1$, 1)
ON CONFLICT (audience, doc_type) DO NOTHING;

INSERT INTO legal_documents (audience, doc_type, content, version)
VALUES
    ('rider', 'accessibility', $doc2$SPINR ACCESSIBILITY STATEMENT

Last updated: August 21, 2026

Spinr is committed to making our rider app, driver app, and website
accessible to everyone, including people with disabilities.

OUR STANDARD

We design and build toward the Web Content Accessibility Guidelines (WCAG)
2.1, Level AA. This is our target for every customer-facing surface. We are
continuing to test and improve against this standard.

WHAT WE'VE BUILT SO FAR

On our admin dashboard, we run automated accessibility checks on every code
change: static analysis of every screen for missing alt text, unlabeled form
inputs, and invalid ARIA attributes, plus automated scans of our sign-in and
core dashboard screens. As of our most recent review, these checks report
zero critical violations. In our mobile apps, icon-only buttons and custom
interactive controls are labeled for screen readers, and where a
wheelchair-accessible vehicle is online and available in your service area,
WAV requests are matched to it. Accommodating a service animal is a
non-negotiable policy for every driver, not a courtesy.

We have not yet completed a full accessibility audit of our rider app,
driver app, or website — see "Known limitations" below.

WHEELCHAIR-ACCESSIBLE VEHICLES AND SERVICE ANIMALS

Where a wheelchair-accessible vehicle is online and available in your
service area, a WAV request is matched to it. A driver may never refuse a
rider accompanied by a service animal — see our Non-Discrimination Policy
for the full detail.

KNOWN LIMITATIONS

Our rider app, driver app, and website have not yet had a full accessibility
audit — today's automated coverage is strongest on the admin dashboard. We
are not aware of every accessibility barrier in our apps, and we don't claim
full conformance today. If you encounter something that doesn't work well
with assistive technology, we want to know — see "Tell us" below.

REQUESTING AN ALTERNATE FORMAT

If you need this statement, our Terms of Service, or our Privacy Policy in
an alternate format, contact support@spinr.ca and we will work with you to
provide one.

TELL US ABOUT AN ACCESSIBILITY BARRIER

Email support@spinr.ca with what you were trying to do, what happened, and
what device/assistive technology you were using. We review every report and
prioritize fixes based on severity and how many people are affected.$doc2$, 1),
    ('driver', 'accessibility', $doc2$SPINR ACCESSIBILITY STATEMENT

Last updated: August 21, 2026

Spinr is committed to making our rider app, driver app, and website
accessible to everyone, including people with disabilities.

OUR STANDARD

We design and build toward the Web Content Accessibility Guidelines (WCAG)
2.1, Level AA. This is our target for every customer-facing surface. We are
continuing to test and improve against this standard.

WHAT WE'VE BUILT SO FAR

On our admin dashboard, we run automated accessibility checks on every code
change: static analysis of every screen for missing alt text, unlabeled form
inputs, and invalid ARIA attributes, plus automated scans of our sign-in and
core dashboard screens. As of our most recent review, these checks report
zero critical violations. In our mobile apps, icon-only buttons and custom
interactive controls are labeled for screen readers, and where a
wheelchair-accessible vehicle is online and available in your service area,
WAV requests are matched to it. Accommodating a service animal is a
non-negotiable policy for every driver, not a courtesy.

We have not yet completed a full accessibility audit of our rider app,
driver app, or website — see "Known limitations" below.

WHEELCHAIR-ACCESSIBLE VEHICLES AND SERVICE ANIMALS

Where a wheelchair-accessible vehicle is online and available in your
service area, a WAV request is matched to it. A driver may never refuse a
rider accompanied by a service animal — see our Non-Discrimination Policy
for the full detail.

KNOWN LIMITATIONS

Our rider app, driver app, and website have not yet had a full accessibility
audit — today's automated coverage is strongest on the admin dashboard. We
are not aware of every accessibility barrier in our apps, and we don't claim
full conformance today. If you encounter something that doesn't work well
with assistive technology, we want to know — see "Tell us" below.

REQUESTING AN ALTERNATE FORMAT

If you need this statement, our Terms of Service, or our Privacy Policy in
an alternate format, contact support@spinr.ca and we will work with you to
provide one.

TELL US ABOUT AN ACCESSIBILITY BARRIER

Email support@spinr.ca with what you were trying to do, what happened, and
what device/assistive technology you were using. We review every report and
prioritize fixes based on severity and how many people are affected.$doc2$, 1)
ON CONFLICT (audience, doc_type) DO NOTHING;

INSERT INTO legal_documents (audience, doc_type, content, version)
VALUES
    ('rider', 'cancellation-fees', $doc3$SPINR CANCELLATION AND NO-SHOW FEE POLICY

Last updated: August 21, 2026

This page explains when a cancellation or no-show fee applies. It doesn't
replace the Terms of Service — if anything here conflicts with the Terms of
Service, the Terms of Service controls.

CANCELLING BEFORE A DRIVER ACCEPTS

You can cancel a ride request before a driver accepts it at no charge.

CANCELLING AFTER A DRIVER ACCEPTS

Once a driver has accepted your ride and is on the way, a cancellation fee
may apply if you cancel more than 2 minutes after acceptance (currently 120
seconds by default, admin-configurable per service area), because the
driver has already committed time and travel to reach you. The app will
always show you whether a fee applies to that specific cancellation before
you confirm the cancellation — you will never be charged a fee you weren't
told about in advance.

The cancellation fee is a flat amount, currently $4.50 by default (admin-
configurable). $4.00 goes to the driver to compensate the time and travel
already spent reaching you, and $0.50 is a Spinr service portion. This fee
is separate from — and does not change — Spinr's 0% commission on the fare
of a completed ride: drivers still keep 100% of every fare they actually
drive.

NO-SHOW FEES

If a driver arrives at your pickup location and you do not show up within 5
minutes of arrival (currently 300 seconds by default, admin-configurable
per service area), and the driver cancels the trip as a result, a no-show
fee may apply. The app notifies you when your driver has arrived so you
have a chance to respond before a no-show fee is charged.

CANCELLING AFTER THE TRIP STARTS

Once a trip is in progress, it cannot be cancelled by either party — this
matches Spinr's ride-state rules (a trip in the `in_progress` state can only
move to `completed`). If something goes wrong mid-trip, contact Support
after the trip ends, or use the in-app SOS feature if you are in immediate
danger.

DRIVER CANCELLATIONS

If a driver cancels after accepting your ride, you are not charged a
cancellation fee. Repeated driver cancellations may affect the rides that
driver is offered, as described in the Independent Contractor Agreement.

DISPUTING A CANCELLATION OR NO-SHOW FEE

If you believe a cancellation or no-show fee was charged in error, contact
Support through the app within 60 days of the charge. Spinr will review the
trip's timeline and issue a refund where the fee was applied incorrectly.$doc3$, 1),
    ('driver', 'cancellation-fees', $doc3$SPINR CANCELLATION AND NO-SHOW FEE POLICY

Last updated: August 21, 2026

This page explains when a cancellation or no-show fee applies. It doesn't
replace the Terms of Service — if anything here conflicts with the Terms of
Service, the Terms of Service controls.

CANCELLING BEFORE A DRIVER ACCEPTS

You can cancel a ride request before a driver accepts it at no charge.

CANCELLING AFTER A DRIVER ACCEPTS

Once a driver has accepted your ride and is on the way, a cancellation fee
may apply if you cancel more than 2 minutes after acceptance (currently 120
seconds by default, admin-configurable per service area), because the
driver has already committed time and travel to reach you. The app will
always show you whether a fee applies to that specific cancellation before
you confirm the cancellation — you will never be charged a fee you weren't
told about in advance.

The cancellation fee is a flat amount, currently $4.50 by default (admin-
configurable). $4.00 goes to the driver to compensate the time and travel
already spent reaching you, and $0.50 is a Spinr service portion. This fee
is separate from — and does not change — Spinr's 0% commission on the fare
of a completed ride: drivers still keep 100% of every fare they actually
drive.

NO-SHOW FEES

If a driver arrives at your pickup location and you do not show up within 5
minutes of arrival (currently 300 seconds by default, admin-configurable
per service area), and the driver cancels the trip as a result, a no-show
fee may apply. The app notifies you when your driver has arrived so you
have a chance to respond before a no-show fee is charged.

CANCELLING AFTER THE TRIP STARTS

Once a trip is in progress, it cannot be cancelled by either party — this
matches Spinr's ride-state rules (a trip in the `in_progress` state can only
move to `completed`). If something goes wrong mid-trip, contact Support
after the trip ends, or use the in-app SOS feature if you are in immediate
danger.

DRIVER CANCELLATIONS

If a driver cancels after accepting your ride, you are not charged a
cancellation fee. Repeated driver cancellations may affect the rides that
driver is offered, as described in the Independent Contractor Agreement.

DISPUTING A CANCELLATION OR NO-SHOW FEE

If you believe a cancellation or no-show fee was charged in error, contact
Support through the app within 60 days of the charge. Spinr will review the
trip's timeline and issue a refund where the fee was applied incorrectly.$doc3$, 1)
ON CONFLICT (audience, doc_type) DO NOTHING;

INSERT INTO legal_documents (audience, doc_type, content, version)
VALUES
    ('rider', 'promotions-referral', $doc4$SPINR PROMOTIONS AND REFERRAL TERMS

Last updated: August 21, 2026

These terms apply to promo codes, referral bonuses, and other promotional
offers on the Spinr platform, in addition to the Terms of Service.

PROMO CODES

A promo code entitles the holder to the specific discount or credit stated
when the code is presented in-app. Promo codes: are valid only for the
period stated at the time of offer; may be limited to new accounts, specific
service areas, or specific ride types; cannot be combined with another promo
code unless explicitly stated; and have no cash value and cannot be
exchanged for cash.

Spinr may cancel or adjust a promo code before it is redeemed if it was
issued in error, and may decline to honor a promo code where we reasonably
believe it was obtained through fraud, abuse, or a violation of these terms.

REFERRAL PROGRAM

When you refer a new rider or driver using your referral code or link, both
you and the person you referred may receive a bonus once the referred
person completes the qualifying action stated in the app at the time (for
example, completing their first ride, or completing a minimum number of
trips as a new driver).

To qualify: the person referred must be a genuinely new Spinr user — you may
not refer an account you already control, a household member's duplicate
account, or an account created to farm referral bonuses; and the person you
referred has 30 days from applying your referral code to complete the
qualifying action (1 ride for a rider referral, 10 rides for a driver
referral) — after 30 days with no qualifying action, the referral expires
unpaid. Once the qualifying action is completed, the bonus is credited to
both accounts.

Spinr monitors referral activity for abuse, including an unusually high
rate of referral bonuses paid to the same account in a short period, and
does not count a ride toward a referral's qualifying activity if the ride
itself was free. An account found to be abusing the referral program may
have pending or already-issued bonuses reversed and may be suspended under
the Community Guidelines.

DRIVER REFERRAL BONUSES

A driver referral bonus is a promotional payment for successfully referring
a new driver who completes the stated qualifying activity — it is not part
of your fare earnings and does not affect the 100 percent fare you keep for
rides you personally complete.

CHANGES AND TERMINATION

Spinr may modify or end a promotional offer at any time, and will honor a
bonus already earned under the terms in place when you completed the
qualifying action. Spinr does not owe you a promotional bonus you have not
yet earned as of the date an offer changes or ends.$doc4$, 1),
    ('driver', 'promotions-referral', $doc4$SPINR PROMOTIONS AND REFERRAL TERMS

Last updated: August 21, 2026

These terms apply to promo codes, referral bonuses, and other promotional
offers on the Spinr platform, in addition to the Terms of Service.

PROMO CODES

A promo code entitles the holder to the specific discount or credit stated
when the code is presented in-app. Promo codes: are valid only for the
period stated at the time of offer; may be limited to new accounts, specific
service areas, or specific ride types; cannot be combined with another promo
code unless explicitly stated; and have no cash value and cannot be
exchanged for cash.

Spinr may cancel or adjust a promo code before it is redeemed if it was
issued in error, and may decline to honor a promo code where we reasonably
believe it was obtained through fraud, abuse, or a violation of these terms.

REFERRAL PROGRAM

When you refer a new rider or driver using your referral code or link, both
you and the person you referred may receive a bonus once the referred
person completes the qualifying action stated in the app at the time (for
example, completing their first ride, or completing a minimum number of
trips as a new driver).

To qualify: the person referred must be a genuinely new Spinr user — you may
not refer an account you already control, a household member's duplicate
account, or an account created to farm referral bonuses; and the person you
referred has 30 days from applying your referral code to complete the
qualifying action (1 ride for a rider referral, 10 rides for a driver
referral) — after 30 days with no qualifying action, the referral expires
unpaid. Once the qualifying action is completed, the bonus is credited to
both accounts.

Spinr monitors referral activity for abuse, including an unusually high
rate of referral bonuses paid to the same account in a short period, and
does not count a ride toward a referral's qualifying activity if the ride
itself was free. An account found to be abusing the referral program may
have pending or already-issued bonuses reversed and may be suspended under
the Community Guidelines.

DRIVER REFERRAL BONUSES

A driver referral bonus is a promotional payment for successfully referring
a new driver who completes the stated qualifying activity — it is not part
of your fare earnings and does not affect the 100 percent fare you keep for
rides you personally complete.

CHANGES AND TERMINATION

Spinr may modify or end a promotional offer at any time, and will honor a
bonus already earned under the terms in place when you completed the
qualifying action. Spinr does not owe you a promotional bonus you have not
yet earned as of the date an offer changes or ends.$doc4$, 1)
ON CONFLICT (audience, doc_type) DO NOTHING;

INSERT INTO legal_documents (audience, doc_type, content, version)
VALUES
    ('rider', 'insurance-periods', $doc5$UNDERSTANDING YOUR INSURANCE COVERAGE ON A SPINR TRIP

Saskatchewan requires ride-share platforms to track which insurance coverage
applies at every moment a driver has the app open. Here's what that means in
plain language.

APP OFF

Your personal auto insurance applies, the same as any other time you're not
driving for Spinr. Spinr provides no coverage.

APP ON, WAITING FOR A RIDE (PERIOD 1)

The driver is online and available, but hasn't been matched to a ride yet.
Spinr's contingent commercial liability coverage applies in addition to the
driver's personal insurance during this period.

DRIVER ON THE WAY TO PICK YOU UP (PERIOD 2)

From the moment a driver accepts your ride request until they pick you up,
Spinr's primary commercial insurance coverage applies. This starts as soon
as the driver is assigned to your ride — even before they've accepted — 
because at that point the driver is already committed to the trip.

YOU'RE IN THE CAR (PERIOD 3)

From pickup to drop-off, Spinr's primary commercial coverage applies with
full coverage for the trip in progress.

WHY THIS MATTERS

If you're ever in an accident during a Spinr trip, which insurance policy
responds depends on which of these periods the trip was in at the time.
Spinr logs the exact moment each period starts and ends for every trip, and
that log is kept for 7 years and never altered after the fact — specifically
so this question can be answered reliably if it's ever in dispute.

WHAT TO DO IF YOU'RE IN AN ACCIDENT

1. Make sure everyone is safe, and call 911 if anyone is injured or if the
   situation requires police attendance.
2. Report the accident to SGI as you normally would.
3. Report it to Spinr through the app as soon as you reasonably can — this
   helps us provide accurate coverage-period information to your insurer.

QUESTIONS

If your insurer or SGI needs Spinr's insurance-period record for a specific
trip, contact support@spinr.ca.$doc5$, 1),
    ('driver', 'insurance-periods', $doc5$UNDERSTANDING YOUR INSURANCE COVERAGE ON A SPINR TRIP

Saskatchewan requires ride-share platforms to track which insurance coverage
applies at every moment a driver has the app open. Here's what that means in
plain language.

APP OFF

Your personal auto insurance applies, the same as any other time you're not
driving for Spinr. Spinr provides no coverage.

APP ON, WAITING FOR A RIDE (PERIOD 1)

The driver is online and available, but hasn't been matched to a ride yet.
Spinr's contingent commercial liability coverage applies in addition to the
driver's personal insurance during this period.

DRIVER ON THE WAY TO PICK YOU UP (PERIOD 2)

From the moment a driver accepts your ride request until they pick you up,
Spinr's primary commercial insurance coverage applies. This starts as soon
as the driver is assigned to your ride — even before they've accepted — 
because at that point the driver is already committed to the trip.

YOU'RE IN THE CAR (PERIOD 3)

From pickup to drop-off, Spinr's primary commercial coverage applies with
full coverage for the trip in progress.

WHY THIS MATTERS

If you're ever in an accident during a Spinr trip, which insurance policy
responds depends on which of these periods the trip was in at the time.
Spinr logs the exact moment each period starts and ends for every trip, and
that log is kept for 7 years and never altered after the fact — specifically
so this question can be answered reliably if it's ever in dispute.

WHAT TO DO IF YOU'RE IN AN ACCIDENT

1. Make sure everyone is safe, and call 911 if anyone is injured or if the
   situation requires police attendance.
2. Report the accident to SGI as you normally would.
3. Report it to Spinr through the app as soon as you reasonably can — this
   helps us provide accurate coverage-period information to your insurer.

QUESTIONS

If your insurer or SGI needs Spinr's insurance-period record for a specific
trip, contact support@spinr.ca.$doc5$, 1)
ON CONFLICT (audience, doc_type) DO NOTHING;

NOTIFY pgrst, 'reload schema';
