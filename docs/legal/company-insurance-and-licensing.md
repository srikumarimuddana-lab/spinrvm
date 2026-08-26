# Company-Level Insurance & TNC Licensing — Draft for Legal Review

> **What this is.** `docs/legal/insurance-coverage-periods.md` explains
> driver-facing TNC insurance (Periods 0-3) — coverage that follows a
> specific driver's app state during a specific ride. This document covers
> a different, company-level layer: the insurance Spinr Inc. (or the
> operating entity) itself needs to carry to legally operate as a
> Transportation Network Company, plus the municipal/provincial licensing
> that layer sits under. Neither document replaces the other; they cover
> different insured parties.
>
> **This is a draft, not legal advice.** It exists to name the gap and
> collect what's known so counsel has a starting point — it does not
> constitute proof of coverage and must not be treated as such.

---

## 1. What's missing today (the gap this doc names)

As of this research pass, this repository has no documentation of
company-level insurance requirements. `saskatoon-launch.md` §I-1/I-3
covers the **provincial TNC licence** and the **SGI ride-share
endorsement on each driver's personal policy** and the **company TNC fleet
policy** at a checklist-item level, but does not itemize what a company TNC
fleet policy needs to contain, nor does it cover general liability,
technology E&O, or cyber coverage at the company level at all. This
document is the collection point for that missing detail — counsel and
whoever owns the insurance-broker relationship should fill in the
"Status" column below, not this document's author.

## 2. Coverage layers to evaluate

| Layer | Covers | Status |
|---|---|---|
| Company TNC fleet policy (SGI Auto Fund) | Period 1 (app on, no ride) at minimum — see CLAUDE.md's insurance-period table | Referenced in `saskatoon-launch.md` §I-3 as a checklist item; not itemized here — confirm scope with the insurance broker before launch |
| Commercial General Liability (CGL) | Company-level liability exposure (e.g. a claim against Spinr Inc. itself, not tied to a specific insurance period) | **Not documented anywhere in this repo as of this research pass — real gap.** |
| Technology Errors & Omissions (Tech E&O) | Software-defect-caused harm (e.g. a dispatch bug that causes a real-world loss) | **Not documented anywhere in this repo — real gap.** |
| Cyber liability | Data breach costs (notification, credit monitoring, regulatory fines) — overlaps with but is distinct from the PIPEDA breach *process* already documented in `docs/runbooks/data-breach.md` | **Not documented anywhere in this repo — real gap.** `data-breach.md` covers the response *procedure*; it does not confirm a cyber policy exists to fund that response. |
| $1M provincial liability filing | Provincial minimum liability requirement referenced in external research this cycle — **not yet confirmed against the actual Saskatchewan regulation text** | Unverified — confirm with counsel/broker before treating as a hard number |

## 3. Licensing layer (company-level, distinct from driver eligibility)

`saskatoon-launch.md` §I-1/I-2 already covers:
- Provincial ride-share licence (Highway Traffic Board / Ministry of
  Highways and Infrastructure)
- Municipal Saskatoon TNC bylaw compliance — **the specific bylaw number is
  itself unverified** (see the caveat added to `saskatoon-launch.md` §I-2
  this cycle: external research surfaced "Bylaw No. 9651" as a possible
  current cite, and a Regina "Vehicle(s) For Hire Bylaw" with conflicting
  reported fee figures — neither confirmed against the City directly).

This document does not duplicate those checklist items — see
`saskatoon-launch.md` §I for the authoritative checklist. What this
document adds is the observation that **company-level licensing and
company-level insurance are gated by each other in practice**: a City
bylaw compliance filing typically requires proof of the CGL/fleet policy
in §2 above, so the "real gap" rows in §2 are not just an insurance
paperwork item — they may block the municipal licensing checklist item
too. Sequence accordingly: don't assume licensing can complete while
insurance is still in progress.

## 4. Real precedent: insurance as a launch-delay risk, not just a checklist item

Uride's own Saskatchewan-market launch was reportedly delayed by insurance-
arrangement lead time (per external research this cycle — not independently
confirmed with Uride, but cited because it's a same-province, same-industry,
recent precedent). This is the concrete reason this gap is being raised now
rather than left as an implicit checklist line: insurance procurement can be
a multi-week critical-path item, not a same-week formality, and a launch
date commitment made before confirming broker lead time risks becoming an
uncomfortable public number to walk back.

## 5. Action for whoever owns this

1. Confirm with an insurance broker (or existing broker relationship if one
   exists) which of the three "real gap" rows in §2 are actually already
   covered under an existing general business policy and which are truly
   uncovered.
2. Get a real lead-time estimate for whichever coverage is missing —
   treat Uride's reported delay (§4) as a reason to ask this question early,
   not as a number to plan around directly.
3. Confirm the $1M provincial liability figure and the bylaw number in §3
   against primary sources (regulator/City directly), not this document's
   external research.
4. Once confirmed, promote the relevant rows out of "Status: unverified"
   and link the actual policy documents (or their filing location) here.

## Sources (external research, not Spinr-verified)

$1M provincial liability filing figure and Uride insurance-related launch-
delay reporting — both from third-party public reporting gathered this
cycle, not independently confirmed with SGI, the Province, or Uride
directly.
