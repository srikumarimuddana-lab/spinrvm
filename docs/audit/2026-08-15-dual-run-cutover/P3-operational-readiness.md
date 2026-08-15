# Dual-Run Cutover Audit — Phase 3: Operational Readiness

**Date:** 2026-08-15 · **Posture:** AUDIT-ONLY — the runbook below is a **plan to review**, not executed. P3.1 (monitoring gap) was pulled forward and reported in the Phase 1 doc because the new app launches publicly within days; its verdict, restated: **nothing today would catch a dual-run collision live**, and three cheap additive signals were identified there.

---

## Plain-English summary

The safe way to shut the old app down on Oct 31 is a strict order: stop its new bookings about two weeks out, let in-flight rides finish, freeze it read-only, take one final complete export, prove the export is complete and that no money is still owed, migrate the history in (per the Phase 2 plan), get an explicit sign-off closing the three known risks, and only then — as the last, irreversible step — tear the infrastructure down, with the export already stored somewhere that survives for the required 7 years. Several inputs can't come from this repo (the old app's hosting, DNS, Stripe access) and are listed as open questions rather than guessed. One adjacent launch-week risk is restated from the existing backlog: the Railway standby backend has been silently drifting from `main` (C5), so a Fly.io outage during exactly this window would fail over to a stale build — check what commit Railway is actually running before cutover week.

## 3.2 Decommission runbook draft (dates relative to Oct 31 = T-0)

| # | Step | When | Gate before it |
|---|---|---|---|
| 1 | Stop new bookings on old app | ~T-14 (**USER DECISION** on exact date) | New app stable in production for N days |
| 2 | Drain window — in-flight/scheduled old-app rides complete | T-14 → T-7 | — |
| 3 | Freeze old-app writes (read-only; payouts/refunds stopped) | T-7 | Drain confirmed: 0 active, 0 scheduled rides (**USER DECISION**) |
| 4 | Final full export — all ~34 collections, unfiltered | T-7 → T-5 | Freeze active (a pre-freeze export is not final) |
| 5 | Export verification — per-collection row counts vs old-app dashboard, archive checksum, spot-check the $276.59 figure against it | T-5 → T-4 | Step 4 |
| 6 | Zero-pending verification — old-side payouts/refunds/disputes provably $0 (needs old-app + Stripe access; P0 §0.3 checklist) | T-5 → T-3 | **USER DECISION** if any amount is knowingly written off |
| 7 | Historical migration into Supabase | T-10 → T-3 (can start once the export lands) | Written Phase 2 plan finalized; 224-vs-186 explained; 22 unmarked drivers resolved |
| 8 | Reconciliation sign-off — P0's three blockers closed or explicitly risk-accepted | T-3 | Steps 5–7. Insurance-period decision is a legal call (**USER DECISION**) |
| 9 | DNS / app-store sunset actions for the old app | T-3 → T-1 | Step 8 |
| 10 | Infra teardown (old hosting, MongoDB, Stripe usage wound down) | T-0 | **Hard irreversible.** Export durably stored OFF the infra being torn down and verified readable; archive location already provisioned (**USER DECISION** — the point of no return) |
| 11 | Post-teardown retention archive check — who owns the raw export for 7 years, access model | T-0 onward | Verified within days of teardown |

Ordering note: step 6 gates *sign-off* (8), not the export (4) — take the export even if pending money isn't yet provably zero; the export is what makes zero provable.

Communications checkpoints (timeline only, no copy): sunset notice at step 1; forced-migration messaging steps 1→3; driver payout-status comms before step 3 if any of the $276.59+ remains; app-store delisting at step 9; support macro cleanup after step 10.

### Required inputs this repo cannot supply
Old-app hosting/provider/credentials · old-app DNS records · MongoDB hosting + export tooling/size · old-app Stripe platform access · the true collection list/schemas · legal sign-off authority for the insurance-period gap · owner/expiry of the C5 Railway pause.

### Launch-week adjacency (restated, already tracked)
`ACTION_ITEMS.md` C5: Railway standby drifting from `main` — during the freeze window the new app becomes sole system of record; a Fly incident then would fail over to a stale build. Explicit go/no-go check of Railway's running commit belongs in cutover week, not assumed fixed.

## 3.3 Documentation truth-up

Done this session: `ACTION_ITEMS.md` gains item **A34** (dual-run cutover audit index: blockers, decisions, and the deferred Stripe checklist trigger); the four phase reports live in this directory; the stale-sprint warning's context is superseded by this audit for cutover matters.

## What was NOT verified
- Everything listed in the phase reports' own boundaries (old-app internals, Stripe, unopened collections).
- Whether any decommission planning already exists outside this repo (ops calendars, vendor contracts) — the runbook assumes none, per the repo's own record.
