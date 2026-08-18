# Spinr Cookie & Tracking Policy — Draft for Legal Review

> **What this is.** A standalone tracking-technology disclosure, separate
> from the Privacy Policy's vendor list. The admin dashboard runs Vercel
> Analytics and Sentry (per `docs/vendor-register.md`); the marketing
> website, wherever it's hosted, will run its own analytics. PIPEDA and
> CASL both expect clear, specific disclosure of tracking technology and,
> for the website, a way to control non-essential tracking — folding this
> into the general Privacy Policy isn't the same as a cookie-specific
> notice with an opt-out. The rider/driver **mobile apps** don't use
> browser cookies at all (native apps use device identifiers, not cookies)
> — this policy is scoped to the **website and admin dashboard**, and says
> so explicitly to avoid overclaiming or underclaiming what it covers.
>
> **This is a draft, not legal advice**, and assumes a cookie-consent banner
> mechanism exists or will exist on the website — confirm that before
> publishing a policy that describes controls the site doesn't yet offer.

---

## BEGIN DRAFT

SPINR COOKIE AND TRACKING POLICY

Last updated: [INSERT PUBLICATION DATE]

This policy explains how Spinr uses cookies and similar tracking
technologies on spinr.ca and in our admin dashboard. It does not apply to
the Spinr rider or driver mobile apps, which do not use browser cookies —
see our Privacy Policy for how those apps handle data.

WHAT ARE COOKIES

Cookies are small text files a website stores on your device to remember
information about your visit. We also use similar technologies, like local
storage, for the same purposes.

WHY WE USE THEM

Strictly necessary: to keep you signed in and to remember basic site
preferences. These cannot be turned off, because the site would not work
correctly without them.

Analytics: we use Vercel Analytics on our admin dashboard to understand
page-load performance and usage patterns. Entity IDs are stripped before
this data is collected — we do not use analytics cookies to build an
advertising profile of you, and we do not sell this data.

Error monitoring: we use Sentry to capture technical errors so we can fix
bugs. Personal information is filtered out of error reports before they
reach us.

We do not use third-party advertising cookies, and we do not permit
third-party ad networks to track visitors across our site.

YOUR CHOICES

[IF A COOKIE-CONSENT BANNER EXISTS OR IS PLANNED: describe how to accept or
decline non-essential cookies here, and how to change your choice later —
e.g. a "Cookie Settings" link in the site footer.] You can also control
cookies through your browser settings, though blocking strictly necessary
cookies may prevent parts of the site from working.

ADMIN DASHBOARD

Because the admin dashboard is an internal tool (`robots: noindex`, not
used by riders or drivers), it uses the same analytics and error-monitoring
tools described above, scoped to admin staff sessions only.

CHANGES TO THIS POLICY

We will update the "Last updated" date above when we make a material change
to the tracking technologies described here.

QUESTIONS

Contact privacy@spinr.ca with questions about this policy.

## END DRAFT

---

## Pre-publication notes

1. **Confirm the cookie-consent banner exists before publishing the "Your
   Choices" section as written** — if the website doesn't yet have one,
   either build it first or rewrite this section to accurately describe
   what control actually exists today (browser-level only), and treat
   adding the banner as a tracked follow-up rather than letting the policy
   overclaim.
2. Scope note is deliberate: this document should NOT be merged into the
   main Privacy Policy, because the mobile apps (the primary product
   surface) genuinely don't use cookies — conflating the two would
   overstate what tracking the apps do.
