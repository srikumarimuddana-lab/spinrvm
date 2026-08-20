# Spinr Breach Notification Letter Template — Draft for Legal Review

> **What this is.** A pre-drafted notification letter template, distinct
> from `docs/runbooks/data-breach.md` (which covers the *operational
> response process* — scope assessment, evidence preservation, the 24h/72h
> internal timeline). This document is the actual notice content sent *to
> affected users* and *to the Office of the Privacy Commissioner of Canada*
> once a breach is confirmed to pose a real risk of significant harm. The
> point of drafting this now, calmly, is that it should not be composed for
> the first time during a live incident.
>
> **This is a draft, not legal advice.** It is a fill-in-the-blanks
> template, not usable as-is — every bracketed field must be completed with
> facts specific to the actual incident, and the final letter for a real
> breach should be reviewed by counsel before sending, given PIPEDA's
> specific content requirements for breach notifications. See
> `docs/runbooks/data-breach.md` for who is authorized to approve sending
> this and when.

---

## BEGIN TEMPLATE

### A. Notice to affected users

Subject: Important notice about your Spinr account

[USER NAME OR "Spinr user"],

We're writing to let you know about a security incident that may have
involved your personal information.

WHAT HAPPENED

On [DATE], we discovered [BRIEF, PLAIN-LANGUAGE DESCRIPTION OF THE
INCIDENT — e.g. "unauthorized access to a database containing..."]. We
immediately [ACTIONS TAKEN — e.g. "revoked the affected access, patched the
vulnerability, and began an investigation with outside security
specialists"].

WHAT INFORMATION WAS INVOLVED

Based on our investigation, the information that may have been involved
includes: [SPECIFIC DATA CATEGORIES — e.g. name, phone number, trip history.
Be precise and factual; do not guess or minimize, and do not overstate
beyond what the investigation has actually confirmed].

[STATE CLEARLY: "Payment card numbers were not involved — Stripe, our
payment processor, handles that data and it does not pass through the
systems affected." OR the accurate equivalent if payment data was involved.]

WHAT WE'RE DOING

[SPECIFIC REMEDIATION STEPS — e.g. "We've reset the affected credentials,
added additional monitoring on the affected system, and are working with
[FORENSIC FIRM] to complete a full investigation."]

WHAT YOU CAN DO

[SPECIFIC, ACTIONABLE STEPS FOR THE USER — e.g. "We recommend changing your
Spinr password" / "Watch for unexpected account activity" / "If you receive
a suspicious message claiming to be from Spinr, do not click any links —
contact us directly at security@spinr.ca to verify."]

QUESTIONS

If you have questions, contact us at security@spinr.ca or [PHONE NUMBER, IF
STAFFED FOR THIS]. You can also file a complaint with the Office of the
Privacy Commissioner of Canada at priv.gc.ca if you're not satisfied with
our response.

We take the security of your information seriously, and we're sorry this
happened.

Spinr Mobility Inc.

---

### B. Notice to the Office of the Privacy Commissioner of Canada

Per PIPEDA's breach-of-security-safeguards reporting requirements. Submit
via the OPC's Breach Report Form (priv.gc.ca) using the following as source
content:

- **Organization:** Spinr Mobility Inc.
- **Date breach occurred / was discovered:** [DATES — these are often
  different; report both accurately]
- **Description of the breach:** [SAME FACTUAL DESCRIPTION AS SECTION A,
  with more technical detail as the OPC form requests]
- **Nature of information involved:** [DATA CATEGORIES]
- **Number of individuals affected:** [NUMBER OR BEST CURRENT ESTIMATE,
  clearly marked as an estimate if the investigation is ongoing]
- **Assessment of real risk of significant harm:** [WHY THIS BREACH
  MEETS OR EXCEEDS THE THRESHOLD — sensitivity of the data, probability of
  misuse]
- **Steps taken to reduce the risk of harm / prevent recurrence:**
  [REMEDIATION STEPS, SAME AS SECTION A PLUS ANY INTERNAL-ONLY DETAIL]
- **Whether and how affected individuals were notified:** [DATE, METHOD]

---

## END TEMPLATE

---

## Pre-publication / pre-use notes

1. **This is an internal template, not a public-facing legal document** —
   it belongs in `docs/legal/` for versioning discipline, but should never
   be published to the website; it's meant to be filled in and sent
   directly to affected users and to the OPC when needed.
2. Cross-reference `docs/runbooks/data-breach.md` for the operational
   timeline (24h scope assessment, 72h OPC notification threshold) — this
   template is the *content*, that runbook is the *process*, and both are
   needed together during an actual incident.
3. Have counsel review the final notification for a real incident before
   sending — PIPEDA has specific required content for breach reports, and
   getting the "real risk of significant harm" assessment wrong (in either
   direction) has consequences.
4. Keep this template current with actual practice — if `security@spinr.ca`
   or the incident-response process described in `docs/runbooks/data-breach.md`
   changes, update this template to match.
