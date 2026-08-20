# Spinr Accessibility Statement — Draft for Legal Review

> **What this is.** A public accessibility page, closing the gap the Legal
> Ledger flagged: `docs/accessibility-plan.md` is internal-only, and the
> Privacy Policy draft's §8 already promises a public WCAG 2.1 AA commitment
> and a live `accessibility@spinr.ca` contact — this is that public page.
>
> **This is a draft, not legal advice**, and it must not overclaim. Per
> `docs/ACCESSIBILITY.md`, neither mobile app has actually been audited for
> WCAG 2.1 AA conformance as of this review — this statement is written to
> describe a target and a genuine ongoing effort, not a conformance claim.
> Do not publish a claim that Spinr's apps "meet" WCAG 2.1 AA until an
> actual audit backs that claim; a false accessibility claim is itself a
> discoverable liability. Do not publish this page until
> `accessibility@spinr.ca` is a real, monitored inbox — `docs/accessibility-plan.md`
> lists it as "to be provisioned" as of this review.

---

## BEGIN DRAFT

SPINR ACCESSIBILITY STATEMENT

Last updated: [INSERT PUBLICATION DATE]

Spinr is committed to making our rider app, driver app, and website
accessible to everyone, including people with disabilities.

OUR STANDARD

We design and build toward the Web Content Accessibility Guidelines (WCAG)
2.1, Level AA. This is our target for every customer-facing surface. We are
continuing to test and improve against this standard — [IF TRUE: an
independent accessibility audit is in progress / planned for
DATE — DO NOT STATE THIS UNLESS IT IS ACTUALLY TRUE].

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
an alternate format, contact accessibility@spinr.ca and we will work with
you to provide one.

TELL US ABOUT AN ACCESSIBILITY BARRIER

Email accessibility@spinr.ca with what you were trying to do, what
happened, and what device/assistive technology you were using. We review
every report and prioritize fixes based on severity and how many people are
affected.

## END DRAFT

---

## Pre-publication notes — do not skip these

1. **Do not claim WCAG 2.1 AA conformance as an achieved fact.**
   `docs/ACCESSIBILITY.md` confirms neither mobile app has been audited as
   of this review. This draft is written as a target/commitment, not a
   conformance claim — keep it that way, or get the audit done first.
2. **`accessibility@spinr.ca` must be live and monitored before this
   publishes** — checked again 2026-08-17: still "to be provisioned." No
   email/domain administration tool is available to an engineering/agent
   session (this repo's support stack is Zoho Desk —
   `backend/services/zoho_desk_integration.py`,
   `admin-dashboard/.../zoho-config-card.tsx` — but creating a new mailbox
   alias or DNS-level address requires a human with Zoho Mail/Google
   Workspace admin access, not a code change). Concretely, someone with
   that access needs to: (1) create the `accessibility@spinr.ca` mailbox or
   alias, (2) route it to whichever team handles accessibility reports —
   likely the same Zoho Desk queue Support already uses, as a new
   department, given the pattern in `zoho-config-card.tsx` — and (3) confirm
   it's actually monitored (someone reads it) before this page cites it. A
   public page pointing to a dead inbox is worse than no page.
3. **Fixed 2026-08-19**: "What we've built so far" is now filled in from
   `docs/ACCESSIBILITY.md`'s 2026-04-09 compliance status — admin dashboard
   automated checks (jsx-a11y static analysis + axe-core E2E scans) at 0
   critical violations, mobile-app screen-reader labeling, WAV matching, and
   mandatory service-animal accommodation. Explicitly did not claim anything
   for rider app, driver app, or website, since `docs/ACCESSIBILITY.md`
   marks all three "Not yet audited" — that gap is now named in "Known
   limitations" instead of glossed over. This section should be re-checked
   against `docs/ACCESSIBILITY.md` before publication in case its status
   table has moved since 2026-04-09, and the `spinr-accessibility-reviewer`
   agent should still sanity-check the final list against the actual
   codebase before publication.
