# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Content/UX review (Claude Code, via first live run of `spinr-legal-readiness-reviewer`) |
| Surface(s) | docs only — pre-publication legal draft, not live content |
| Domain (Sentry tag) | safety (closest fit — the draft is a Safety Center explainer, not payments) |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | First live run of the new `spinr-legal-readiness-reviewer` agent / `/legal-check` command, targeted at `insurance-coverage-periods.md` since it was believed the closest of the 8 draft legal docs to publication-ready |

## 1. Issue / gap identified

`docs/legal/legal-text-publication-checklist.md`'s row for `insurance-coverage-periods.md` read "content-complete, pending only counsel review." That status is not accurate: the document's own header claims it's "reachable from the in-app Safety Center," but no such entry point exists on either app. `rider-app/app/safety-hub.tsx` has exactly two rows (Emergency Contacts, Report a Safety Issue) and no link to this content; `driver-app/` has no Safety Center screen at all.

## 2. Root cause

The checklist's "content-complete" status was set based on the document's *prose* being verified correct (Period-2 timing, retention/immutability claims, consistency with ToS §6/§13) without separately verifying the document's own claim about *where it's surfaced in-app*. A content-accuracy check and a build-completeness check are two different things, and the earlier pass only did the first.

## 3. Fix / remediation

- Updated the checklist row to split this out as its own explicit ☐ item, distinct from counsel review, so it isn't bundled with (and doesn't get closed by) the counsel-review sign-off.
- Framed it correctly as a build/product decision, not a content-accuracy problem — the document's prose is not being changed by this entry, only the checklist's tracked status.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one row in `legal-text-publication-checklist.md`.** No application code, schema, or live content touched. `insurance-coverage-periods.md` itself is unchanged — its prose was verified correct, not edited.
- No other consumer of this checklist row exists besides the humans (product/eng/legal) who read it before scheduling counsel review or building the entry point.

## 5. User-experience effect

None — pre-publication tracking-doc change only, not live.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/legal/legal-text-publication-checklist.md` | Split the "content-complete, pending only counsel" status into two separate tracked items: consistency-check (still ☑ closed) and in-app entry point (newly ☐ open) | Prevent the checklist from claiming a document is more ready than it is (the checklist's own process rule) |
| `docs/change-log/2026-08-20-insurance-periods-missing-entry-point.md` | New change-log entry | Standard practice for this repo's live-testing-era content fixes |

## 7. Before / after

```
# Before — legal-text-publication-checklist.md, insurance-coverage-periods.md row
☐ Counsel review · ☑ Consistency check against ToS §6/§13 (resolved 2026-08-18 —
see the document's own pre-publication notes; content-complete pending only
counsel review)

# After
☐ Counsel review · ☑ Consistency check against ToS §6/§13 (resolved 2026-08-18)
· ☐ In-app Safety Center entry point not built on either surface (2026-08-20) —
the document's own header claims it's "reachable from the in-app Safety
Center," but rider-app/app/safety-hub.tsx has only two rows and no link to
this content; driver-app/ has no Safety Center screen at all. Needs a
product/eng decision on where this page lives before counsel review is
scheduled.
```

## 8. Rollback plan

`git-revert-safe` — pure markdown checklist-status change, no schema, no data, no config, no live surface touched.

## 9. Verification performed

- This finding came from the first live run of the new `spinr-legal-readiness-reviewer` subagent (see `.claude/agents/spinr-legal-readiness-reviewer.md`, added earlier this session): it searched `rider-app/` and `driver-app/` for any route, screen, or link referencing `insurance-coverage-periods`, `InsuranceCoveragePeriods`, or general `insurance`/`coverage` text, and read `rider-app/app/safety-hub.tsx` lines 100–130 directly to confirm its actual row list.
- Independently re-confirmed the agent's other findings for this document were correctly left CLOSED (Period 2 timing fix still in effect at `backend/routes/rides/matching.py:783`; `driver_insurance_periods` append-only trigger at `backend/migrations/64_driver_insurance_periods.sql`; ToS §6/§13 consistency) — none of those needed a checklist change.

**What was NOT verified**: whether a Safety Center entry point is planned on a roadmap somewhere outside the repo (e.g. a product backlog tool this session can't see) — only that no code implementing it exists today. Also not verified: which of the three integration options (in-app screen, linked web page, folded into ToS view) product/eng will choose — that decision is explicitly left to them, not guessed here.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — one checklist row, no other file touched
- [x] No silent behavior change to an already-shipped flow: pre-publication tracking-doc only
