# Spinr Background Check Consent — Draft for Legal Review

> **What this is.** A purpose-built consent artifact for the Criminal Record
> Check (CRC) and Vulnerable Sector Check (VSC) required at driver onboarding
> and renewed annually (CLAUDE.md, "Driver eligibility"). Criminal-record
> information is a sensitive category under PIPEDA; relying on the general
> Privacy Policy's blanket consent for this specific collection is weaker
> practice than capturing an explicit, separate consent at the moment the
> check is authorized. Meant to be presented as its own screen during driver
> onboarding (and again at annual renewal), not folded into the ToS/Privacy
> Policy acceptance step.
>
> **This is a draft, not legal advice**, and has not been reviewed by
> counsel licensed in Saskatchewan or Canada.
>
> **Corrected 2026-09-02: this document previously assumed a third-party
> commercial background-check vendor model** ("we ask [VENDOR] to run a
> check on your behalf") — the same model Uber/Lyft use in most of the US
> and much of Canada via a private screening company. **That is not how it
> actually works in any Spinr-relevant market.** Confirmed against real
> municipal/police-service sources for every city Spinr currently operates
> in or is tracked as an expansion target (see the launch-gate scaffolding
> in `backend/routes/admin/sgi_forms.py`'s `regulatory_authority`/
> `regulatory_region` fields and `docs/proposals/2026-08-16-safety-toolkit-gap-analysis.md`):
>
> | City | Check required | Issued by | Renewal |
> |---|---|---|---|
> | Regina, SK (live) | Certificate of Approval = Criminal Record Check + Vulnerable Sector Check, purpose "Rideshare" | Regina Police Service (in person, or online via the police-service-operated intake portal at policesolutions.ca) | Annual; check must be no more than 90 days old at renewal |
> | Saskatoon, SK (live) | Criminal Record Check + Vulnerable Sector Check | Saskatoon Police Service (residents) or the applicant's local RCMP detachment (non-residents) | Annual |
> | Calgary, AB (tracked expansion target, not yet live) | Police Information Check + Vulnerable Sector Check, purpose "TNDL" (Transportation Network Driver's Licence) | Calgary Police Service (ePIC online portal, or in person at Calgary Police Service; local police jurisdiction if the applicant lives outside Calgary) | Dated within 60 days for a new TNDL application |
> | Edmonton, AB (tracked expansion target, not yet live) | Police Information Check | Edmonton Police Service | Not more than 90 days old; renewal window opens 45 days before expiry |
>
> Every one of these is issued **directly by a police service** (or the
> RCMP, where a municipality has none of its own) **to the applicant**, who
> then submits or uploads the result — not a private commercial
> background-check company running a check on Spinr's behalf. `policesolutions.ca`,
> which several of these services use as their online intake system, is a
> third-party *software portal* (a division of Forrest Green, a Canadian
> company) that police services use to take applications and payment — it
> does not itself perform, adjudicate, or issue the check; the police
> service does. Spinr has no vendor relationship, no data-sharing agreement,
> and no commercial background-check provider anywhere in this flow as of
> this writing. The document below is rewritten to describe that reality:
> the driver personally obtains their own check from their local police
> service (or RCMP) and gives Spinr the result — Spinr does not "obtain a
> check on your behalf" from anyone.
>
> Sources checked 2026-09-02 (web search, current as of that date — reverify
> before publication, municipal requirements change): [City of Regina —
> Rideshare](https://www.regina.ca/bylaws-permits-licences/licences/rideshare/),
> [Regina Police Service record checks (policesolutions.ca)](https://www.policesolutions.ca/checks/services/regina/index.php),
> [Saskatoon Police Service — Criminal Record Checks](https://saskatoonpolice.ca/recordcheck/),
> [City of Calgary — Becoming a TNC driver](https://www.calgary.ca/taxis-ride-share/drivers-licence-application.html),
> [Calgary Police Service record checks (policesolutions.ca)](https://www.policesolutions.ca/checks/services/calgary/index.php),
> [City of Edmonton — Driver and Vehicle Licensing](https://www.edmonton.ca/business_economy/driver-and-vehicle-licensing),
> [CTV News Regina — Uber drivers will now need police background checks](https://regina.ctvnews.ca/uber-drivers-will-now-need-police-background-checks-before-they-drive-in-regina-1.5322302).
> These were reached via search-result summaries, not a full page fetch (the
> municipal domains were blocked by this session's network egress policy) —
> **a human should verify the primary source pages directly before this
> document is treated as final**, especially the exact fee amounts and
> processing-time figures, which are more likely to have drifted.

---

## BEGIN DRAFT

CONSENT TO CRIMINAL RECORD CHECK AND VULNERABLE SECTOR CHECK

Before you can drive on the Spinr platform, we need your consent to collect
and review a Criminal Record Check and Vulnerable Sector Check that you
obtain yourself, and to renew this consent at least annually while you
continue to drive.

HOW THIS WORKS

Unlike some platforms, Spinr does not send your information to a
background-check company. Instead, you personally request a Criminal
Record Check and Vulnerable Sector Check from your local police service
(or the RCMP detachment serving your area, if your municipality does not
have its own police service), and you give Spinr the result. The exact
process, fee, and processing time depend on which police service issues
your check — your city's driver requirements will tell you the specific
steps.

WHAT WE COLLECT AND WHY

We collect and review the Criminal Record Check and Vulnerable Sector
Check result you provide, along with your name and date of birth to match
it to your driver profile. We use the result solely to confirm you meet
Spinr's driver eligibility requirement of no major violations and no
Criminal Code driving offences in the past three years, as described in
the Independent Contractor Agreement. We do not use this information for
any other purpose, and we do not request or receive anything beyond the
result document itself — we never contact a police service directly to
obtain your check.

HOW WE STORE IT

Your background-check result is stored encrypted and is never shared with
riders or with any other Spinr driver. Access inside Spinr is limited to
the staff who review driver eligibility.

HOW LONG WE KEEP IT

We retain the result of your background check, and the record of each
renewal, for as long as you are an active driver, and for a limited period
after that consistent with Spinr's regulatory retention obligations
described in the Privacy Policy.

ANNUAL RENEWAL

Your consent here covers the initial check and each annual renewal while
you continue to drive. You are responsible for obtaining a current check
from your police service before each renewal and submitting it to Spinr.
You will be prompted to reconfirm before each renewal. If a current check
is not on file, you will not be able to go online.

WHAT HAPPENS IF THE RESULT AFFECTS YOUR ELIGIBILITY

If a check result means you no longer meet Spinr's eligibility
requirements, we will tell you the specific requirement affected before
taking any action on your account, consistent with our obligations under
applicable consumer-reporting and human-rights law, and you may respond
before a final decision is made.

YOUR CONSENT

By checking the box below and continuing, you consent to Spinr collecting
and reviewing the Criminal Record Check and Vulnerable Sector Check result
you provide from your police service, for the purpose and retention
period described above.

[ ] I consent to Spinr collecting and reviewing my Criminal Record Check
and Vulnerable Sector Check as described above.

## END DRAFT

---

## Pre-publication notes

1. **Resolved 2026-09-02**: the third-party-vendor framing (and its
   `[BACKGROUND CHECK VENDOR NAME]` placeholder) has been removed — Spinr
   has no background-check vendor, and none is needed. See the header note
   for the researched city-by-city model this document now reflects.
2. **Retention figure intentionally left non-numeric** — no authoritative
   retention period for CRC/VSC results specifically exists anywhere in
   this codebase (checked `docs/data-classification.md`, the Privacy
   Policy's driver-specific section, and CLAUDE.md's PIPEDA retention
   table — none state a CRC/VSC-specific figure; the codebase's only
   concrete 7-year figures are for trip records, driver/vehicle linkage,
   and insurance-period transition logs, which are a different record
   type). Same treatment as `driver-deactivation-appeals-policy.md`'s
   unresolved SLA brackets: don't invent a number, don't ship a bracket,
   describe honestly what's actually true today. Needs a real Legal/Safety
   decision before a specific figure can be published.
3. **"What happens if the result affects your eligibility" still needs a
   real process behind it** — confirm with the safety/eligibility team
   that a driver is actually given a chance to respond before an adverse
   decision, consistent with fair-reporting practice, before this
   paragraph is published as a promise. Unchanged from the prior draft's
   note — not verified in this pass.
4. **Alberta (Calgary/Edmonton) rows are forward-looking** — Spinr is
   Saskatchewan-first and not yet live in Alberta (per CLAUDE.md and the
   `regulatory_region`/pre-launch-flag scaffolding already in the
   codebase). Included here so the document doesn't need a rewrite the
   day Alberta launches, and because the underlying model (driver
   self-obtains a police-issued check, no vendor) is identical across all
   four cities — only the issuing police service, exact fee, and
   processing window differ. Re-verify Alberta's requirements against
   primary sources before that expansion actually ships, not just at
   this draft stage.
5. **City-specific process detail (fees, processing times, purpose codes
   like "Rideshare" or "TNDL") deliberately left out of the published
   consent text itself** — that level of detail belongs in city-specific
   onboarding guidance (e.g. a driver FAQ or the become-driver wizard),
   not a PIPEDA consent form, which should state what Spinr does with the
   data rather than walk through police-service application mechanics
   that vary by city and can change without Spinr's involvement. The
   header note above captures that detail for internal reference only.
