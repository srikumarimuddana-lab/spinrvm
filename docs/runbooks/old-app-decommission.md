# Old-app decommission runbook (dual-run cutover, tentative Oct 31, 2026)

**Covers:** stopping the old (previous-vendor) app, taking its final export, migrating the
tail of its history in, and tearing its infrastructure down.
**Owner:** the product owner (self-assigned 2026-09-07, via `ACTION_ITEMS.md` A34 interview — see §0).
**Status:** §0's four prerequisites are now all resolved (2026-09-07) — see §0 for each. **Still DRAFT for one reason**: the per-step `Owner` column in §2 (steps 1–9, 11 — who actually executes/confirms each individual step day-to-day, distinct from the overall accountable owner resolved in §0) is not yet filled in. Originally proposed by an engineering session from `docs/audit/2026-08-15-dual-run-cutover/P3-operational-readiness.md` §3.2.

Cross-reference: `ACTION_ITEMS.md` A34 (tracking item) · `docs/audit/2026-08-15-dual-run-cutover/` (source audit, all 4 phase reports) · `docs/runbooks/dual-run-driver-roster-policy.md` (the companion operational policy for the window *before* this runbook's step 1) · `docs/runbooks/full-app-audit.md` (repeatable audit prompt used to re-verify this thread) · `ACTION_ITEMS.md` C5 (Railway standby drift — a launch-week-adjacent risk, not this runbook's subject, but gates step 10 below)

---

## 0. Before this is actionable

This runbook is a **plan to review**, not a plan being executed. Nothing in
§2 should start until:

- [x] An owner is named for this runbook as a whole (**USER DECISION**) —
  **resolved 2026-09-07: the product owner**, per `ACTION_ITEMS.md` A34's
  2026-09-07 interview addendum.
- [x] An owner is named for the final teardown step specifically (§2, step 10) —
  it is the one irreversible action in this document and needs a named,
  accountable person, not just "engineering." **Resolved 2026-09-07: same
  person, the product owner** — explicitly confirmed as a deliberate choice
  (one accountable person for both the whole sequence and the point-of-
  no-return step), not a default.
- [x] The exact T-14 stop-new-bookings date is set (**USER DECISION** — see
  step 1). Every other date in the table is relative to it. **Resolved
  2026-09-07: tied automatically to the Oct 31, 2026 tentative
  decommission target — T-14 = October 17, 2026.** The product owner chose
  to compute this from the existing target date rather than set an
  independent one; if the Oct 31 target itself moves, this date moves with
  it. This also implicitly answers the companion "N days of new-app
  stability" question the same way: N is whatever the gap between today
  and Oct 17 provides, not a separately tracked number — flag to the
  product owner if a specific minimum N (e.g. "at least 30 days stable")
  was actually intended and should be checked against that gap explicitly.
- [x] `docs/runbooks/dual-run-driver-roster-policy.md` is approved and in
  effect — it covers the window *before* step 1 fires, and this runbook
  assumes it's already running. **Confirmed 2026-09-07 by the product
  owner: approved and running.**

**This repo cannot supply:** old-app hosting/provider credentials, old-app DNS
records, MongoDB hosting/export tooling, old-app Stripe platform access, the
true collection list/schemas (only ~11 of ~34 collections have ever been
opened from a prior export), legal sign-off authority for the insurance-period
gap, or the owner/expiry of the C5 Railway pause. Anywhere this runbook
references one of these, it is stating an open question, not a completed
step.

---

## 1. Plain-English summary

Stop the old app taking new bookings about two weeks before teardown. Let
whatever's already in flight finish. Freeze the old app read-only. Take one
final, complete export — every collection, unfiltered. Prove that export is
complete and that no money is still owed on the old side. Migrate the
remaining history in, following the same validate-then-commit pattern the
existing importers (`backend/services/*_import_service.py`) already use. Get
an explicit sign-off that closes the three known risk categories (financial,
identity, regulatory). Only then — as the last, irreversible step — tear the
old infrastructure down, with the export already stored somewhere durable
enough to survive the 7-year regulatory retention window this repo's own
`CLAUDE.md` documents for trip records.

---

## 2. Step sequence (dates relative to teardown = T-0)

**Dates below are computable now**: T-0 = Oct 31, 2026 (still the *tentative*
decommission target per `ACTION_ITEMS.md` A34 — if it moves, every date
below moves with it). T-14 was the one date requiring an explicit
**USER DECISION**; resolved 2026-09-07, see §0.

| # | Step | When | Gate before it | Owner |
|---|---|---|---|---|
| 1 | Stop new bookings on the old app | **T-14 = October 17, 2026** (resolved 2026-09-07, tied to the Oct 31 target — see §0) | New Spinr app stable in production for N days — N itself resolved implicitly as "however long the gap to Oct 17 is," per §0; flag if a specific minimum N was actually intended | Product owner |
| 2 | Drain window — in-flight/scheduled old-app rides complete | T-14 → T-7 (Oct 17 → Oct 24) | Step 1 | ? |
| 3 | Freeze old-app writes (read-only; payouts and refunds stopped) | T-7 (Oct 24) | Drain confirmed: 0 active, 0 scheduled rides on the old app (**USER DECISION** on how this is verified — old-app dashboard access needed) | ? |
| 4 | Final full export — all ~34 collections, unfiltered | T-7 → T-5 (Oct 24 → Oct 26) | Freeze active. A pre-freeze export is not final and must not be treated as one. | ? |
| 5 | Export verification — per-collection row counts vs. the old-app's own dashboard, archive checksum, spot-check the outstanding-payout figure against the export ($185.31–$228.08 for 13 of 15 buckets confirmed as of 2026-08-16; 2 buckets, $42.77, still genuinely ambiguous — see A34 in `ACTION_ITEMS.md`) | T-5 → T-4 (Oct 26 → Oct 27) | Step 4 | ? |
| 6 | Zero-pending verification — old-side payouts/refunds/disputes provably $0 (needs old-app + Stripe platform access this repo doesn't have; the ready checklist is in `docs/audit/2026-08-15-dual-run-cutover/P0-critical-money-and-regulatory.md` §0.3 — run it the day access arrives) | T-5 → T-3 (Oct 26 → Oct 28) | **USER DECISION** required if any amount is knowingly written off rather than resolved | ? |
| 7 | Historical migration into Supabase — extend the existing `backend/services/*_import_service.py` validate/commit pattern, don't build a new path | T-10 → T-3 (Oct 21 → Oct 28; can start once the export lands, doesn't have to wait for step 6) | A written migration plan for the final tail exists (the running crosswalk-table design work already covers the bulk of this — see `docs/audit/2026-08-15-dual-run-cutover/P2-migration-completeness.md`); the 22 currently-unmarked legacy drivers question is resolved, not carried forward again | ? |
| 8 | Reconciliation sign-off — the three open risk categories (financial, identity, regulatory — same three A34 has tracked since 2026-08-15) closed with evidence or explicitly risk-accepted by name | T-3 (Oct 28) | Steps 5–7 complete. The insurance-period reconstruction decision is a legal call, already made once for the first 186 rides (CR #4081, reconstruct-and-flag) — confirm the same call applies to whatever the final export adds, don't assume it silently carries over | ? |
| 9 | DNS / app-store sunset actions for the old app | T-3 → T-1 (Oct 28 → Oct 30) | Step 8 | ? |
| 10 | Infra teardown — old hosting, MongoDB, old Stripe platform usage wound down | T-0 (Oct 31) | **Hard irreversible — the point of no return.** The export from step 4 must already be stored *off* the infrastructure being torn down, verified independently readable, and its 7-year archive location already provisioned before this step runs. This is a **USER DECISION**, not something to green-light on an engineering session's own judgment — **resolved 2026-09-07: the product owner is this decision's accountable name**, but the go/no-go call itself still has to actually be made at T-0, this only names who makes it. | **Product owner** (resolved 2026-09-07 — deliberately the same person as the overall runbook owner) |
| 11 | Post-teardown retention archive check — who owns the raw export for the 7-year window, and under what access model | T-0 onward (Oct 31 onward) | Verified within days of teardown, then re-verified on whatever cadence the owner sets | ? |

**Ordering note:** step 6 (zero-pending verification) gates the *sign-off*
(step 8), not the export (step 4). Take the export even if the old side's
pending money isn't yet provably zero — the export itself is what makes "zero"
provable later. Don't let a slow Stripe reconciliation delay the one
time-boxed, freeze-dependent step.

**Communications checkpoints** (timeline only — this runbook does not draft
copy): sunset notice at step 1; forced-migration messaging steps 1 through 3;
driver payout-status communication before step 3 if any of the outstanding
$185–$228 figure (or its resolution) is still unsettled; app-store delisting
at step 9; support-macro cleanup after step 10.

---

## 3. Launch-week adjacency (restated from the existing backlog, not new)

`ACTION_ITEMS.md` C5: the Railway standby backend has been silently drifting
from `main` because `deploy-backend.yml` is blocked by a GitHub Environment
protection rule. During the freeze window (step 3 onward), the new Spinr app
becomes sole system of record for all ride traffic — a Fly.io outage during
exactly that window would fail over to a stale Railway build. Add an explicit
go/no-go check of Railway's actually-running commit as a gate before step 3,
not an assumption that C5 has been separately fixed by then.

---

## 4. What this runbook does NOT cover

- The dual-run window *before* step 1 (both apps live, old app still taking
  bookings) — see the companion `docs/runbooks/dual-run-driver-roster-policy.md`.
- Drafting the actual sunset/forced-migration copy shown to users — flagged
  as open in `docs/runbooks/full-app-audit.md` §3.4 ("migration-facing user
  experience... has never been reviewed"), a separate task for
  `spinr-design-consistency-reviewer` / `spinr-accessibility-reviewer` plus a
  human product/copy owner.
- The support & operations playbook for dual-run-adjacent tickets (refund
  routing when a ride was booked in the old app, "charged twice" triage,
  which team answers which app's tickets) — flagged as open, unowned, in
  `docs/runbooks/full-app-audit.md` §3.3.
- Any actual write-path code for the tail migration in step 7. Per this
  audit thread's own stated posture, write-path code is not proposed until a
  user explicitly asks for it after reviewing the relevant report — this
  runbook is that review artifact, not the code.

## 5. What was NOT verified

- Every input listed in §0 as unsuppliable by this repo.
- Whether decommission planning already exists somewhere outside this repo
  (an ops calendar, a vendor contract, a termination-notice clock already
  running with the old app's hosting provider) — this runbook assumes none
  exists, per the repo's own record, and should be corrected the moment
  someone confirms otherwise.
- Any legal review of the step sequence or the retention/export durability
  claims in step 10 — drafted from `CLAUDE.md`'s existing 7-year retention
  rule, not independently confirmed against counsel for this specific
  decommission scenario.
