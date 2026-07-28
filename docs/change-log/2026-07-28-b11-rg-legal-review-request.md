# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (B11/R-G follow-up on the Data Transfer PIA) |
| Surface(s) | backend (docs only) |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B11 / R-G (`docs/privacy/2026-07-28-pia-data-transfer-export.md`) |

## 1. Issue / gap identified

PIA recommendation R-G ("formal legal review of the implied-consent basis for this secondary use") cannot be resolved by an engineering task — it requires a determination from a human with privacy/legal authority, and no such role is currently assigned in this repo. Left as a bare one-line recommendation, it was at risk of staying open indefinitely with no one owning the next step.

## 2. Root cause

Not applicable — this isn't a bug fix. The PIA correctly identified that this item needs human legal review; the gap was that nothing packaged the question into something a reviewer could actually act on without first reading and synthesizing the entire PIA themselves.

## 3. Fix / remediation

Prepared a self-contained legal-review request at `reports/legal/data-transfer-implied-consent-review.md`, following the existing house format already used for `reports/legal/supabase-region-attestation-checklist.md` (Owner/Due/Regulation/Risk-if-missed header, Background, the specific question, Contact, Status table). It states the precise PIPEDA question, summarizes what's already been assessed (the PIA's Section 5 reasoning, explicitly flagged as "the PIA author's analysis, not a legal opinion"), and surfaces two facts not fully drawn out in the PIA itself: (1) the unpublished `docs/legal/privacy-policy.md` draft has no language covering this use case, so if legal concludes a disclosure is needed, that's the natural place to add it before first publication; (2) the transfer stays entirely within Spinr's own Supabase project (no third-party recipient), which is relevant to the reasonable-secondary-use analysis. Updated the PIA (Section 8, R-G) and `ACTION_ITEMS.md` (B11) to point at the request and make clear this is a request, not a resolution — R-G stays open.

## 4. Risk & impact on existing functionality

- **What else reads this?** Nothing programmatic — these are docs consumed by humans (privacy/legal reviewers, engineers checking B11's status). No code, route, or data path touched.
- **Could this regress a working flow?** No — zero application code changed.
- **Blast radius:** isolated to three documentation files (new request doc, PIA doc, ACTION_ITEMS.md).

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin-facing change. This is an internal compliance-process artifact.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `reports/legal/data-transfer-implied-consent-review.md` | New — self-contained legal-review request package | Give a human reviewer everything needed to make the R-G determination without re-deriving it from the full PIA |
| `docs/privacy/2026-07-28-pia-data-transfer-export.md` | R-G entry (Section 8) updated to reference the request and clarify it's still open pending human review | Keep the PIA's own record accurate — a request is not a resolution |
| `ACTION_ITEMS.md` | B11 status summary and R-G bullet updated with the same clarification and a pointer to the request | Keep the canonical backlog accurate |

## 7. Before / after

Not applicable — this is additive documentation, not a behavior-changing diff.

## 8. Rollback plan

`git-revert-safe` — pure documentation, no schema, no data, no code path affected.

## 9. Verification performed

- [x] Manual review: confirmed no existing privacy-officer/legal role or process doc in this repo assigns an owner for this kind of review (searched `docs/legal/`, `docs/`, `reports/legal/`).
- [x] Confirmed `docs/legal/privacy-policy.md` is genuinely still unpublished ("Draft for Legal Review" header) and does not currently mention this use case, before stating that as a fact in the request.
- [x] Followed the existing house format (`reports/legal/supabase-region-attestation-checklist.md`) rather than inventing a new one.
- [ ] Not applicable: no automated tests (docs-only change).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`).
- [x] Blast radius is stated, not assumed — three docs files, zero code.
- [x] No silent behavior change — R-G is explicitly still marked open in both the PIA and ACTION_ITEMS.md; this change does not claim the legal question is resolved.
