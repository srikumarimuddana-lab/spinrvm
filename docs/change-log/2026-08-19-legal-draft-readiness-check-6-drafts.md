# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Content/UX review (Claude Code) |
| Surface(s) | docs only — pre-publication legal drafts, not live content |
| Domain (Sentry tag) | corporate (closest fit; not payments/dispatch/auth/safety) |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | Follow-up from Support-section (FAQ/Legal) content audit — readiness check on the remaining 6 unpublished legal drafts |

## 1. Issue / gap identified

Of the 8 draft legal documents under `docs/legal/`, this session's earlier PR closed content errors in 2 (`cancellation-fee-policy.md`, `promotions-referral-terms.md`). The remaining 6 (`community-guidelines.md`, `non-discrimination-policy.md`, `driver-deactivation-appeals-policy.md`, `accessibility-statement.md`, `insurance-coverage-periods.md`, `background-check-consent.md`) had never been checked against the actual codebase to see which of their non-counsel gating conditions were already closed vs. still genuinely open.

## 2. Root cause

Each draft's own header lists what it depends on, but nothing had cross-checked those dependencies against the real code since the drafts were written — the checklist rows were carried over unverified.

## 3. Fix / remediation

Checked each of the 6 drafts' non-counsel gating conditions directly against the codebase:

- `driver-deactivation-appeals-policy.md`: confirmed the in-app appeal channel is real (`driver-app/app/appeal.tsx` → `backend/services/driver_appeals.py` → `routes/drivers/appeals.py`/`routes/admin/driver_appeals.py`). Checked for a real SLA number and found none anywhere — genuinely still open, not something more code-reading resolves.
- `background-check-consent.md`: confirmed the consent-capture screen is real (`driver-app/app/crc-consent.tsx`). Checked for a real CRC/VSC vendor name and found none — genuinely still open.
- `community-guidelines.md`: performed the cross-document consistency check against `non-discrimination-policy.md` and `driver-deactivation-appeals-policy.md` — consistent on what counts as a "serious violation." Same open SLA gap as the appeals policy (shared dependency).
- `insurance-coverage-periods.md`: confirmed its one open item was already resolved 2026-08-18 (Period 2 timing fix, documented in the file's own notes) — now content-complete pending only counsel review.
- `accessibility-statement.md`: filled in the "What we've built so far" section using `docs/ACCESSIBILITY.md`'s real 2026-04-09 compliance status (admin dashboard automated checks at 0 critical violations, mobile screen-reader labeling, WAV matching, mandatory service-animal accommodation), and explicitly excluded any claim for rider app/driver app/website since those are marked "Not yet audited" there — named as a limitation instead of omitted. The `accessibility@spinr.ca` inbox-provisioning gate remains open and still blocks publication regardless of this content fix.
- `non-discrimination-policy.md`: no code-checkable gate to close — its remaining item (protected-grounds list vs. current SK Human Rights Code text) requires counsel, not code review.

Updated `docs/legal/legal-text-publication-checklist.md`'s rows for all 6 documents to record exactly what's now closed vs. still open, per the checklist's own process rule.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `accessibility-statement.md`, `legal-text-publication-checklist.md`, and one new change-log doc.** No application code, schema, or live content touched. None of these 6 documents exist in the `legal_documents` table (confirmed earlier this session: only `tos`/`privacy` are published).
- **No user-facing effect today** — pre-publication drafts, not reachable from any app screen or admin dashboard content yet.
- The accessibility-statement content pulled in is grounded in `docs/ACCESSIBILITY.md`'s dated compliance table (2026-04-09) — if that table has moved since, this section needs a re-check before publication (already noted in the document's own pre-publication notes).

## 5. User-experience effect

None — pre-publication content, not live.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/legal/accessibility-statement.md` | Filled in "What we've built so far" and "Known limitations" from real `docs/ACCESSIBILITY.md` data; updated pre-publication notes | Close the one code-checkable gate; avoid an aspirational claim |
| `docs/legal/legal-text-publication-checklist.md` | Updated rows for all 6 remaining drafts with what's confirmed closed vs. still genuinely open | Keep the canonical tracking table accurate per its own process rule |
| `docs/change-log/2026-08-19-legal-draft-readiness-check-6-drafts.md` | New change-log entry | Standard practice for this repo's live-testing-era content fixes |

## 7. Before / after

```
# Before — accessibility-statement.md, "What we've built so far"
[LIST SPECIFIC, VERIFIED ACCESSIBILITY FEATURES ALREADY SHIPPED — e.g.
screen-reader support, dynamic text sizing, sufficient color contrast,
keyboard navigation on the admin dashboard, WAV ride matching, service
animal accommodation as a non-negotiable policy. Pull this list from
`docs/ACCESSIBILITY.md` and `docs/accessibility-plan.md`, and only include
items that are actually true today, not planned.]

# After
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
```

## 8. Rollback plan

`git-revert-safe` — pure markdown content change, no schema, no data, no config, no live surface touched.

## 9. Verification performed

- Grepped `driver-app/`, `backend/` for an appeal SLA constant (none found) after confirming the appeal channel itself exists.
- Grepped `driver-app/app/crc-consent.tsx` and `backend/` for a CRC/VSC vendor name (none found) after confirming the consent screen itself exists.
- Read `docs/ACCESSIBILITY.md` in full and only transcribed items listed as actually shipped/measured, not planned or aspirational.
- Cross-read `community-guidelines.md`, `non-discrimination-policy.md`, and `driver-deactivation-appeals-policy.md` together for the "serious violation" consistency check.
- No production build was run — none applicable, this change touches no `admin-dashboard`/`rider-app`/`driver-app` runtime code, only markdown.

**What was NOT verified**: whether `docs/ACCESSIBILITY.md`'s 2026-04-09 compliance table is still current as of today (2026-08-19) — it's over four months old; re-check it before this page actually publishes. Also not verified: whether any *other* file in the repo (outside `driver-app/` and `backend/`) names a CRC/VSC vendor or an appeal SLA — the grep was scoped to the code paths that would plausibly contain them, not a full-repo sweep.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow: pre-publication content only
