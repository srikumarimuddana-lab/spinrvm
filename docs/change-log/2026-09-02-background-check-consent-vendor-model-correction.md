# Change Impact & Risk Log — correct the background-check-consent.md vendor model

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (this session), at the product owner's factual correction |
| Surface(s) | docs only — draft legal content, not yet published to `legal_documents` |
| Domain (Sentry tag) | safety |
| PR / commit link | branch `fix-background-check-consent-policy` |
| Related issue or gap ID | Product owner correction during the driver-deactivation-policy publish thread — background checks are police-issued, not third-party-vendor-issued |

## 1. Issue / gap identified

`docs/legal/background-check-consent.md` was drafted around a third-party
commercial background-check vendor model ("We ask [BACKGROUND CHECK VENDOR
NAME] to run a Criminal Record Check...") — the model Uber/Lyft use via a
private screening company in most US/Canadian markets. The product owner
corrected this: for Spinr, drivers currently obtain their Criminal Record
Check + Vulnerable Sector Check directly from local police services
(Regina and/or Saskatoon), and no third-party vendor is or has ever been
used. `.claude/context/regulatory-sk.md` carried the same wrong premise
("Third-party attestation").

## 2. Root cause

The original draft (predates this session) assumed the more common
industry pattern without verifying it against Spinr's actual process. No
prior session appears to have checked this against real municipal/police
sources — the publication checklist's own notes only ever flagged "vendor
name is a placeholder, undecided business fact," never questioned whether
a vendor model applied at all.

## 3. Fix / remediation

1. **Researched the actual requirement** for every city Spinr currently
   operates in (Regina, Saskatoon) and every city tracked as an Alberta
   expansion target in the existing launch-gate scaffolding (Calgary,
   Edmonton) — via web search (municipal government domains were blocked
   by this session's egress policy, so this is search-result-summary
   research, not full primary-source fetches; flagged explicitly in the
   document and checklist as needing human verification before final
   publication). Confirmed in all four cities: the check is issued
   directly by a police service (or the RCMP, where a municipality has no
   local force) to the applicant, who then submits/uploads it — there is
   no commercial background-check company in the flow. `policesolutions.ca`,
   used by several of these services as an online intake system, is a
   software vendor to the police service, not an independent screener.
2. **Rewrote `docs/legal/background-check-consent.md`** end-to-end around
   this reality: removed the `[BACKGROUND CHECK VENDOR NAME]` placeholder
   entirely (not filled in — there's nothing to name), replaced "we ask
   [VENDOR] to run a check" with "you obtain your own check from your
   police service and give us the result," added a header table
   documenting the researched per-city model for internal reference.
3. **Corrected `.claude/context/regulatory-sk.md`**'s matching "Third-party
   attestation" row to describe the real police-issued model.
4. **Left the retention-period gap non-numeric**, same treatment as
   `driver-deactivation-appeals-policy.md`'s SLA gaps from the prior PR
   (#4868) — no authoritative CRC/VSC retention figure exists anywhere in
   the codebase (confirmed by checking `docs/data-classification.md`, the
   Privacy Policy's driver section, and CLAUDE.md's PIPEDA retention
   table — none state a CRC/VSC-specific number). Not inventing one; not
   publishing this document until that's resolved either.

**Not published in this change.** This document stays `Draft` — the
retention-figure gap and the adverse-eligibility-process gap (both
pre-existing, unrelated to the vendor-model fix) are still open, and this
was a factual-accuracy fix, not a "ready to publish" pass. Unlike
`driver-deactivation-appeals-policy.md`, there's no equivalent of the
non-numeric-SLA workaround for "who is the vendor" — the fix here was
correcting a wrong premise, not resolving a placeholder within a correct
one.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to 2 files**, both content-only
  (`docs/legal/background-check-consent.md`,
  `.claude/context/regulatory-sk.md`), plus the checklist and this log.
  No production code, schema, or published content touched — this
  document was never live in `legal_documents` (confirmed: still `Draft`
  per the checklist, both before and after this change).
- **`driver-app/app/crc-consent.tsx`** (the screen that renders whatever
  is published under `type=background-check-consent`) needs no code
  change — it displays whatever content string it's given; today it shows
  the fallback "This consent form has not been published yet" since
  nothing is published. Confirmed by reading the component.
- **`backend/tests/test_legal_documents.py`** and
  `driver-app/__tests__/app/crcConsentScreen.test.tsx` reference only the
  `background-check-consent` doc-type string, never the markdown prose —
  re-ran the backend test file, 8/8 passed, unaffected.
- `.claude/context/regulatory-sk.md` is a loaded-on-demand context file
  for future sessions working on driver onboarding — the correction there
  prevents this same wrong assumption from being reintroduced by a future
  session reading it as ground truth.

## 5. User-experience effect

None yet — this document isn't published, so no driver sees any of this
text today. The eventual effect (once published) is a materially more
accurate PIPEDA consent: a driver reading it will correctly understand
they personally obtain their own police check and Spinr never contacts a
police service or a private company on their behalf, rather than being
told (incorrectly) that Spinr "asks" an unnamed vendor to process their
data.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/legal/background-check-consent.md` | Full rewrite around the real self-obtained-police-check model; vendor placeholder removed | Correct a wrong factual premise, not just fill a placeholder |
| `.claude/context/regulatory-sk.md` | CRC/VSC "Evidence" column corrected from "Third-party attestation" to the real police-issued model | Same wrong premise existed here too; this file is read as ground truth by future sessions |
| `docs/legal/legal-text-publication-checklist.md` | `background-check-consent.md` row updated with the full correction history | Keep the checklist accurate as the source other sessions read |
| `docs/change-log/2026-09-02-background-check-consent-vendor-model-correction.md` | New — this log | Required for a safety-domain content change |

## 7. Before / after

```
# Before
We ask [BACKGROUND CHECK VENDOR NAME] to run a Criminal Record Check and
Vulnerable Sector Check using your name, date of birth, and any other
identifying information the check requires.
```

```
# After
Unlike some platforms, Spinr does not send your information to a
background-check company. Instead, you personally request a Criminal
Record Check and Vulnerable Sector Check from your local police service
(or the RCMP detachment serving your area, if your municipality does not
have its own police service), and you give Spinr the result.
```

## 8. Rollback plan

Plain `git revert` — no data or runtime state involved, document was never
published.

## 9. Verification performed

- [x] `backend/tests/test_legal_documents.py` — 8/8 passed, unaffected
  (doc_type-only assertions).
- [x] Read `driver-app/app/crc-consent.tsx` to confirm it renders
  published content generically — no code change needed there.
- [x] Cross-checked the retention-figure claim against
  `docs/data-classification.md`, the Privacy Policy's driver section, and
  CLAUDE.md's PIPEDA retention table — confirmed no CRC/VSC-specific
  figure exists anywhere, consistent with the prior 2026-08-20 finding.
- [x] Web research performed for all 4 cities (Regina, Saskatoon, Calgary,
  Edmonton) with sources cited in the document's own header note.
- [ ] **Primary source pages NOT directly fetched** — regina.ca,
  saskatoonpolice.ca, calgary.ca, edmonton.ca were all blocked by this
  session's network egress policy; research is based on search-result
  summaries and secondary sources (news coverage, the policesolutions.ca
  portal pages, which loaded via search snippets not direct fetch either).
  Flagged explicitly in the document itself — a human should verify the
  primary municipal/police-service pages before this is treated as final,
  especially fee amounts and processing-time windows.
- [ ] Not verified: whether Calgary/Edmonton requirements have changed
  since this research (2026-09-02) — municipal bylaws and police-service
  processes can change without notice; these rows are explicitly marked
  forward-looking/pre-launch in the document, not currently operative.
- [ ] Counsel review — not done, as with every other legal draft in this
  repo.

## 10. What was NOT verified

- Exact current fees and processing-time windows for each city's check
  (deliberately left out of the published consent text for this reason —
  see the document's own pre-publication note 5).
- Whether Spinr's actual onboarding flow today asks a driver which city/
  police-service jurisdiction they're in, or otherwise varies its guidance
  per city — this document describes the check's origin generically
  ("your local police service") rather than assuming that flow exists;
  worth checking separately if city-specific onboarding guidance is
  wanted.
- The retention-figure and adverse-eligibility-process gaps, both
  pre-existing and explicitly left open (not this change's scope).
