# Dual-run driver roster policy (old app + new Spinr app, both live)

**Covers:** preventing the same driver from working an active shift on both
the old (previous-vendor) app and the new Spinr app at the same time, for as
long as both are live.
**Owner:** _unassigned — needs a name before this is in effect (see §0)_.
**Status:** DRAFT — proposed by an engineering session, not yet reviewed or
approved. This is an **operational policy**, not a code change: the
underlying risk is confirmed structurally possible in code
(`docs/audit/2026-08-15-dual-run-cutover/P1-financial-identity-reconciliation.md`
§P3.1) and currently latent only because dual-active drivers happen to be
zero today, not because anything prevents it.

Cross-reference: `ACTION_ITEMS.md` A34 (tracking item) · `docs/runbooks/old-app-decommission.md`
(the companion runbook for the teardown that ends this policy's relevance) ·
`backend/utils/dual_run_monitor.py` (the monitoring signals this policy's
detection step relies on)

---

## 0. Before this is in effect

- [ ] An owner is named — the person who actually maintains the roster
  (**USER DECISION**). This cannot be "engineering"; it requires whoever runs
  driver operations/dispatch on both apps, since the source data (who's
  scheduled where) doesn't live in this repo.
- [ ] That owner confirms whether a driver roster already exists somewhere
  outside this repo (an ops spreadsheet, the old app's own scheduling tool)
  that this policy can build on, rather than starting from nothing.

Until both are true, treat this document as a proposal, not a control that
is actually running.

## 1. Why this exists

Both apps can independently accept the same physical driver for an active
ride at the same time. Nothing in Spinr's dispatch, go-online, or payout
paths is aware the old app exists — confirmed at the code level:

- The go-online handler (`backend/routes/drivers/status.py`) has no
  cross-platform awareness; it only checks Spinr-local state.
- Dispatch offer/accept (`backend/services/dispatch_service.py`) has no
  concept of a driver being unavailable because they're mid-ride on a
  different platform.
- Payout paths (`backend/routes/drivers/payouts.py`) carry no cross-platform
  Stripe awareness — a driver paid out on both platforms for what is, from a
  regulatory/insurance-period standpoint, a single continuous shift would not
  be caught by either system.

This is a real structural gap, not a hypothetical: `driver_insurance_periods`
(the SGI-facing regulatory audit trail this repo's `CLAUDE.md` documents) is
built entirely from Spinr-local ride state. A driver double-booked across
both apps would have an insurance-period record that's silently wrong for
the overlap window, and a payout that's silently doubled for the same driving
time.

**No code change closes this** — see §4 for why building a technical guard
was rejected as the primary control, and what monitoring exists instead.

## 2. The policy

While both apps are live (today through whatever date `docs/runbooks/old-app-decommission.md`
step 1 sets — see that runbook for the stop-new-bookings decision that ends
this policy's relevance):

1. **Every driver active on the new Spinr app must be confirmed off-roster on
   the old app for the same shift window**, and vice versa. "Confirmed"
   means checked against whatever scheduling source the owner (§0) maintains
   — not assumed from a driver's own self-report.
2. **A driver may not be scheduled on both apps for overlapping windows.**
   If a driver genuinely needs to work both (e.g. transitioning from the old
   app to the new one), their shifts must be sequential with a buffer, not
   concurrent.
3. **New driver activations on the new app get cross-checked against the old
   app's active roster before their first go-online is approved**, for the
   duration of this policy. This is the highest-leverage check point, since
   it's a one-time gate per driver rather than an ongoing per-shift one.
4. **The roster owner reviews the monitoring signals in §3 on a fixed
   cadence** (owner sets the cadence — daily is the suggested starting point
   given launch-week volume, loosening once dual-active driver counts are
   confirmed low) and reconciles any surprise against the manual roster.
5. **Any detected overlap is treated as an incident**, not a data-quality
   footnote: the affected driver's insurance-period record and payout for
   the overlap window need manual review before either is trusted, per
   `docs/runbooks/corporate-compensating-transaction.md`'s "this is not a
   `git revert`" principle applied to the insurance-period table (which is
   append-only and must never be mutated — see `CLAUDE.md`'s insurance-period
   rules) — a correction is a new, clearly-flagged row, never an edit to the
   original.

## 3. What the code already gives this policy to work with

Three monitoring signals shipped in PR #3954
(`backend/utils/dual_run_monitor.py`), behind the `dual_run_monitoring_enabled`
`app_settings` flag (default on). Verified live as of this document (see
`ACTION_ITEMS.md` A34's monitoring-signal re-verification entry):

1. An `audit_logs` row `legacy_driver_first_go_online`, written once per
   imported driver the first time they actually go online in the new app —
   gives the roster owner a natural trigger point for check #3 above.
2. A labeled counter `spinr_drivers_go_online_total{is_legacy_import}` —
   lets the roster owner watch for an activation burst during launch week
   without querying the database directly.
3. A counter `spinr_payments_legacy_driver_payout_total` — every settled
   Stripe transfer to a legacy-imported driver, for the payout side of the
   review in §2.4.

These are **observation-only**. They tell the roster owner where to look;
they do not themselves prevent, block, or flag an overlap — that judgment
still requires the manual roster this policy establishes.

## 4. Why this is a policy and not a code guard

A technical guard (e.g. blocking go-online for any driver flagged active on
the old app) was considered and rejected as the primary control, for reasons
specific to this repo's actual access:

- **There is no live integration with the old app.** Spinr has no API,
  webhook, or shared database connection to the old platform — building a
  guard would require either standing up that integration (out of scope for
  a decommission-window control that exists for at most a few months) or
  relying on manually-imported roster data that's already stale by the time
  it's imported, which is worse than an honest manual check.
- **The risk is currently latent, not active** — zero drivers are dual-active
  today per the 2026-08-15 audit. A heavyweight technical control for a
  latent risk with a fixed, approaching end date (the old app's
  decommission) is disproportionate; a lightweight operational policy that
  scales down naturally as the old app winds down is the better fit.
- **The monitoring in §3 already gives early warning** without requiring new
  write-path code on a launch-adjacent surface — consistent with this audit
  thread's own stated posture of not proposing write-path code changes
  unless a user explicitly asks for one after reviewing a report.

If dual-active driver counts turn out to be materially higher than zero once
this policy's monitoring is actually reviewed, that's a signal to revisit
this decision — flag it back to the roster owner and reconsider a technical
guard at that point, rather than assuming the "latent" characterization still
holds.

## 5. What was NOT verified

- Whether a driver roster or scheduling system already exists outside this
  repo that the owner (§0) can build on — assumed not to exist per the
  repo's own record, same caveat as `docs/runbooks/old-app-decommission.md`.
- Actual dual-active driver counts today — the audit's "zero" figure is from
  2026-08-15; re-verify via the §3 signals before treating it as current.
- Whether the old app has any equivalent cross-check capability on its own
  side (this repo has no visibility into the old app's codebase or
  operations).
