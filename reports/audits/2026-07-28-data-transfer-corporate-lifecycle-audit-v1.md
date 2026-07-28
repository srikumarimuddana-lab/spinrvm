# Data Transfer + Corporate Billing — Structured SDLC Lifecycle Audit

**Date:** 2026-07-28
**Scope:** Data Transfer admin module (import/export) and Corporate billing/accounts module — requirements, design, development, testing, and cross-cutting (PIPEDA, blast-radius, Change Impact Log discipline) review.
**Method:** Two parallel read-only structured audits (backend/routes/admin/data_transfer_*, backend/services/data_transfer/*, backend/routes/corporate*, backend/services/corporate*), each following the requirement→design→dev→test→cross-cutting lifecycle framework.

## Headline finding

Corporate is the more mature module on paper (it has a real spec doc, `docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md`, that Data Transfer lacks) but carried the higher actual risk — it had one real double-spend bug reach a live-tested money path (the allowance-ceiling race, fixed in migration 258), and the regression test for that exact bug class didn't exist until this audit's remediation. Data Transfer's risk was compliance/access-control shaped (missing PIA, an access-control gate broader than intended), not money-shaped.

## P0 items found and remediated (all shipped 2026-07-28)

| Finding | Fix | PR / commit |
|---|---|---|
| `data_transfer_jobs.py`'s list/get/download endpoints were gated on the `bulk_operations` module flag (broader than intended) instead of `super_admin`, based on a stale docstring claim — let any bulk_operations admin see cross-admin export history and pull other admins' PII export bundles | Added `_require_super_admin()` check to all three endpoints + 6 tests | PR #2685, commit `88d9c51` |
| Migration 258's corporate allowance-ceiling race fix (a real double-spend bug that shipped once) had no regression test for the fix's own invariant | Added `test_corporate_allowance_cap_race.py` — 4 tests modeling the locked ceiling-check algorithm under real `asyncio` concurrency, plus a control test proving the pre-fix shape overspends; closed 3 coverage gaps in `corporate_allowance_service.py` (89%→97%) | PR #2686, commit `4257690` |
| No structured Change Impact & Risk Log existed for migration 258 / commit `2d9e673` (a money-touching fix), despite CLAUDE.md requiring one | Backfilled `docs/change-log/2026-07-26-corporate-allowance-cap-race-fix.md` | PR #2686, commit `4257690` |
| The Data Transfer export path moves full-fidelity, unredacted PII (government ID numbers, exact GPS ride history, identity documents) for up to 100 entities per export — no PIA existed | Wrote `docs/privacy/2026-07-28-pia-data-transfer-export.md` (PIPEDA-framed; 7 ranked recommendations, none yet implemented) | PR #2687, commit `48d2d0f` |

## Remaining open items (not yet implemented — tracked in ACTION_ITEMS.md)

See ACTION_ITEMS.md items **B11** (Data Transfer PIA recommendations) and **B12** (Corporate follow-ups) for the prioritized, not-yet-shipped punch list this audit produced. Highlights:

- The Data Transfer export path (up to 100 entities, unredacted, per request) is the same class of gap as the already-open **AI-3** finding ("Admin exports all users → offline PII leak", `docs/threat-model/admin-panel.md`) that B10 already extended to the Compliance module's export endpoints. This audit extends AI-3's scope a second time to Data Transfer's export route — when AI-3's shared dual-approval mechanism is built, Data Transfer's export endpoint should be wired through it too, not given a one-off gate.
- Corporate: no compensating-transaction runbook exists for a bad `corporate_wallet_apply_delta`/`corporate_allowance_apply_delta` application — the documented rollback is "drop the function," which doesn't undo money already moved.
- Corporate: `routes/corporate_rider.py` (65%), `routes/corporate_company_bookings.py` (57%), `routes/corporate_accounts.py`/`corporate_company.py` (79% each) remain below the 90% money-path coverage floor.
- Data Transfer PIA's 2 High recommendations (R-A: narrow the `bulk_operations` access gate; R-B: add data-minimization scope flags to the export request) are not implemented — this audit's PIA is the assessment only.

## Cross-cutting: CI/tooling gaps surfaced during PR review of the fixes

Not part of the audited modules, but discovered while shepherding the 4 remediation PRs through CI:

- **CR-2026-DATATRANSFER-01** (issue #2689): the `claude-review.yml` auto-review Action fails repo-wide with a missing `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN` — no PR in the repo has received its automated security/money/migration deep-review since whatever point this secret went missing.
- **CR-2026-DATATRANSFER-02** (issue #2691): the PR auto-summary bot's "Declaration vs diff" checkbox parser can't match the PR template's own backtick-wrapped `` `[x]` `` convention, producing false "tick this box" warnings on correctly-filled Tier 3 compliance flags. Advisory-only, does not block merge.
