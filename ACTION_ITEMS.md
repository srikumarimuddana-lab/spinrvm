# Spinr — Production Readiness Action Items

> **How to use this file (for Claude / any AI session):** pick the highest-priority
> `[ ]` item, read its *Files* and *Acceptance* fields, implement it following
> `CLAUDE.md` conventions (≤3 files per subtask, one logical change per commit,
> tests required), then flip it to `[x]` with the PR/commit reference in the
> *Done* column. Do not re-litigate `[x]` items. Companion document with full
> context: `docs/PRODUCTION_READINESS.md`.

_Last updated: 2026-08-17 — A39's deferred `migrate.py` decision resolved
(product owner: reconcile, not just delete). Ported `migrate.py`'s tested
CONCURRENTLY-safe SQL splitter (B0) into `run_migrations.py` — which
never had that fix and would have failed on any `CREATE INDEX
CONCURRENTLY` migration — fixed every living-doc/CI/runtime reference
across `CLAUDE.md`, `AGENTS.md`, runbooks, CI workflow comments, and a
real admin-facing error message, then deleted `migrate.py` outright.
`spinr-migration-reviewer` then found the reference-cleanup was
incomplete (11 more files); all fixed or explicitly judged out of scope
(self-declared provenance records, auto-generated build artifacts) — see
A39.
128 directly affected tests pass. Prior same day: A40 CLOSED (all three questions resolved:
#1 confirmed by the product owner that dual-run — old app still
processing real Stripe charges on the shared account — is intentional
right now, not an incident; #2 checked live and found no evidence of
Connect-account overlap, `transfer.created` has no handler at all and
zero of the 15 observed `account.updated` events match any current
driver's Stripe account; #3 downgraded to a low-priority hygiene item
— split the webhook endpoint before the Oct 31 decommission, not
urgent since current handling is already confirmed safe). Prior same
day: A38 CLOSED (migration 321 adds the missing
`rides.driver_id` guard to `purge_pii_retention()`'s Step H, the
sanctioned DSAR hard-delete path — closes the same class of gap A35
found in an ad-hoc script, verified byte-identical to migration 296
except the fix, reviewed clean by both `spinr-migration-reviewer`
(one numbering-conflict blocker found and fixed) and
`spinr-regulatory-compliance-checker`; see
`docs/change-log/2026-08-17-a38-step-h-driver-rides-guard.md`).
Prior same day: A36 CLOSED (root cause of the empty
`financial_events` table found via live webhook-payload evidence: no
native Spinr ride has ever completed a real payment yet — 100% of
completed card-paid rides in production are legacy-imported, and the
write path itself is sound, simply never invoked with real traffic;
neither original hypothesis, wiped or broken, held up); A40 ADDED (open,
operational question) — the same investigation confirmed, via real
webhook payloads (not inference), that the OLD app is still issuing
live Stripe charges on the shared Stripe account as recently as
2026-08-15. Prior same day: A37 FIXED (real-time `ddl_command_end` event
trigger, migration 318, closing the poll-interval gap A35 deliberately
deferred; verified against a real isolated Supabase branch, not a mock;
`spinr-migration-reviewer` + `spinr-regulatory-compliance-checker` manual
passes run, one blocker found and fixed — missing `(action, created_at)`
index for the new `audit_logs` query pattern, added via `CREATE INDEX
CONCURRENTLY` — see
`docs/change-log/2026-08-17-a37-guard-trigger-ddl-realtime-audit.md`).
Prior same day: A35 FIXED (detection loop + sanctioned
cleanup-tool replacement, migration 317 applied to production, reviewed by
migration + regulatory-compliance agents); A38 ADDED (open, deliberately
deferred follow-up spun off from A35's review); A39 ADDED and CLOSED same
day (two competing migration runners found — `migrate.py` doesn't match
production's actual schema and would fail if run; docs corrected to point
at `run_migrations.py`, the one that's actually live). Prior: A34 ADDED (open): dual-run cutover readiness
audit complete (PR #3954, `docs/audit/2026-08-15-dual-run-cutover/`);
decommission blockers, launch-week collision/monitoring gaps, and required
user decisions consolidated there. Prior: C21 ADDED (open): two PRs (#3719, #3728, the
notification-throttling feature) merged via GitHub's native per-PR
auto-merge before/without their full check set completing — #3719 merged
while `CI/CD Pipeline`/`CI Guard Rails`/`Security Gates` were still
`queued`/`in_progress` and one check had already failed; #3728 merged with
2 pre-existing, unrelated `yarn audit` failures still red. Ruled out
`dependabot-auto-merge.yml` (hard-gated to `dependabot[bot]`, neither PR
qualifies) — points to `main`'s required-status-checks branch-protection
list being stale relative to the ~57 checks that now run per PR. Same
root-cause shape as C13 and the still-open mobile-bundle-smoke
required-checks ask, and compounds with E8 (CODEOWNERS non-functional) —
together, a PR can currently merge to `main` with zero human review and no
guarantee the full check suite finished green. Not resolvable from an
engineering session (needs repo-admin access to Settings → Branches and
Settings → General). Prior same-day: B8 CLOSED (Regina/Saskatoon only, stopgap): applied
the 1.4×/1.8× multiplier proposal against production to fix identical fares
across vehicle tiers, but only after catching that live data had drifted
since the original 2026-08-11 investigation (Regina's Economy rate was
`0.02/0.02` live, not the documented `2/2`; Saskatoon had an undocumented
Economy>XL per_km inversion) and that the drafted `UPDATE` SQL used the
wrong JSON shape (object-keyed `jsonb_set`, not the actual array-of-objects
schema — would have silently no-op'd). Absolute price-vs-Uber positioning
explicitly deferred per user direction, not resolved by this stopgap — see
`docs/change-log/2026-08-12-b8-regina-saskatoon-vehicle-pricing.md`. New
follow-ups logged: undocumented "Saskatoon Airport" area has the same
identical-fares defect; a "Regina Airpot" (typo) area also has it. Prior
same-day: A28 CLOSED: audit's 4 P2 findings + P2-B
triaged. P2-C already closed (same finding as P0-A/#3678). Float-on-money
in `routes/drivers/earnings.py` (4 sites) fixed — Decimal accumulation
instead of raw `float()`, with regression tests independently verified to
fail on the pre-fix file with the predicted drift value. Remaining items
(driver-import VIN/email/phone validation, `/balance` vs `/earnings`
composition, rider total-rides definition, missing import change-logs)
filed as backlog — most need a product decision, not a blind code change.
Prior same-day: A27 CLOSED: audit's 2 P1 findings. P1-A (dead
`drivers.total_earnings` fleet-wide stat) fixed with a live, legacy-excluded,
batched earnings computation; also fixed a related gap on the per-driver
"Earnings" header (missing legacy exclusion vs. its own "Payouts" tab). P1-B
investigation (was PST hidden in legacy receipts?) surfaced a bigger live
issue: the current fare engine had PST disabled for Saskatchewan with a
comment claiming it doesn't apply — contradicting regulatory-sk.md. User
confirmed PST does apply; enabled it in production for the 4 real SK service
areas (effective for new quotes only, no backdating), fixed the stale
comment, added the first direct tax-calculation unit tests. Prior same-day
(2026-08-12): C18 CLOSED: all 176 `uses:` references across 23
`.github/workflows/*.yml` files pinned from mutable version tags (`@v7`) to
verified commit SHAs (`@<sha> # v7`), resolved via anonymous public-repo git
reads (not the release-page scrape the original investigation correctly
rejected) — see `docs/change-log/2026-08-12-c18-pin-github-actions-shas.md`.
One reference (`8398a7/action-slack@v3`) turned out to resolve to a mutable
**branch**, not even a tag. Prior same-day (2026-08-12): A1c/Sub-tier C:
`utils/kyb_reverification.py` CLOSED, 67% → 92% (75 stmts) — a gap in the
prior "fully CLOSED" sweeps (2026-08-03, 2026-08-10/11), found via a live
`pytest --cov` re-check before starting a planned 28-file batch; the other
27 files in that batch were already closed by concurrent sessions (in most
cases under the exact same test-file name this session independently
chose) and were discarded without being committed — see
`docs/change-log/2026-08-12-a1c-kyb-reverification-coverage.md` for the
full collision list. Prior (2026-08-11): A26 CLOSED: `EXCLUDE_LEGACY_RIDES` compiled to
an unsatisfiable `legacy_import_metadata IS NULL` SQL predicate against a
`NOT NULL DEFAULT '{}'::jsonb` column, matching zero rows always at 9+
driver-facing earnings/statement/T4A call sites — confirmed live against
production (authorized Supabase MCP access) and fixed by adding a proper
`$eq` filter operator to `repositories/_base.py` and changing the constant
to `{"legacy_import_metadata": {"$eq": {}}}`, a single-source-of-truth fix
covering all 9+ call sites. Found earlier the same day while closing A25/P0-B
(admin financial-dashboard legacy-ride double-counting, 3 PRs merged:
#3674, #3678, #3683). Prior same-day: B25 ADDED (open): Maestro real-device mobile E2E (`.github/workflows/maestro-e2e.yml`) is wired but never fires — missing `EXPO_TOKEN`/`MAESTRO_CLOUD_API_KEY` secrets unconfirmed, opt-in-only trigger (`workflow_dispatch`/`run-maestro` label), no iOS lane. Found while explaining the Playwright-vs-Maestro split to a user; Playwright (`rider-app/e2e/`, `driver-app/e2e/`) only covers the Expo web export, not a real native device. Prior (2026-08-10): B17 CLOSED: `financial_events.ride_id` FK changed to `ON DELETE SET NULL` (migration 294) so `purge_pii_retention()` Step B no longer FK-aborts the entire daily retention purge once a paid ride crosses 7 years; `docs/runbooks/data-retention.md` extended to cover Steps H–M. Prior same-day (2026-08-10): B20 CLOSED: `ledger_projection.py`'s `_decompose` now degrades (whole amount to `platform_revenue`, Sentry-escalated) instead of silently decomposing from stale `driver_earnings`/`tax_amount` when a fare-settlement event's ride isn't yet `payment_status='paid'` — source-aware by construction (cancellation-fee/notice-fee events never reach the check). Also found and fixed in the same change: `_RIDE_COLUMNS` omitted `payment_status` entirely, which would have made the new check degrade *every* fare charge unconditionally had it shipped alone — added a column-membership regression test for it, mirroring the existing `discount_amount` one. 23 + 122 tests pass. Prior same-day (2026-08-10): B19 and B21 CLOSED: `payment_retry.py`'s `requires_capture` branch now routes through `_finalize_card_settlement` (picking up the atomic RPC + Sentry escalation + WS notify the other two settlement paths already had), and all 4 background loops (`payment_retry.py`, `driver_claim_reaper.py`, `offer_expiry_reaper.py`, `orphaned_hold_reconciler.py`) now have correct throttle-lock TTL arithmetic (`interval * 0.85` instead of `1.5x`/`2x`), each with the same two regression tests `ledger_projection.py` already used to catch this class of bug. 165 + 113 tests pass across the affected surfaces. Prior same-day (2026-08-10): A1c CLOSED: full-suite backend coverage verified at 90% aggregate on the latest `main` run (job 93335534234), all three sub-tiers done, no remainder. Same check also fixed the one test failing on that run (`test_snap_to_road_returns_none_without_any_provider_configured` — stale test hit a live public OSRM router instead of mocking "no provider configured") and filed **C12** (Codecov push uploads silently rejected — tokenless upload, `continue-on-error: true` hides it as a green check). Prior (2026-08-03): A1c (Track 2) Sub-tier C fully CLOSED across two parallel sessions (both converged on all 39 files in the 60-80% coverage band, fresh snapshot, not the stale 55-file estimate): `routes/faqs.py` 78.12%→94%, `utils/apns_client.py` 78.72%→100%, `server.py` 79.20%→88% (test-only, no bugs found; `server.py`'s Sentry-init block left as a documented import-time-only gap); `services/zoho_desk_integration.py` 74.42%→98%, `utils/distance_reconciliation.py` 74.70%→96%, `services/data_transfer/observability.py` 75.00%→100%; `utils/retention_purge.py` 69.12%→98%, `utils/orphaned_hold_reconciler.py` 69.23%→95%, `utils/driver_online.py` 69.70%→100% (the `is_available ⇒ is_online` invariant helper, explicit parametrized invariant test added); `utils/payment_retry.py` closed to 99% (reconciled in rather than overwritten). A separate parallel-session pass found and fixed 5 found-not-fixed bugs surfaced during the coverage sweep (see Sub-tier C entry below for the full list) and investigated a 6th, reverting its approved fix after a blast-radius test proved it was based on a false premise (Entry 13, `docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md`); its own final full suite ran 9235 passed, 1 known pre-existing flaky test deselected (order-dependent, passes standalone — see Sub-tier C entry). Prior (2026-08-02): `routes/drivers/subscriptions.py` (Sub-tier A, Spinr Pass) CLOSED, 61%→99% across two same-day sessions; `ride_flow.py`/`ride_cancel.py`/`ride_reads.py` (Sub-tier A) CLOSED, 66.30%/51.75%/58.95%→99%/100%/98%; `utils/redis_client.py` closed to 100%; `routes/websocket.py` closed to 80.3% (PR #3154); `repositories/ride_repo.py` 54.83%→84.1%. A1b closed 2026-08-01 (Track 1 done); Track 2 spun off as A1c — full-repo scoping pass done (Sub-tiers A/B/C), `utils/reconciliation.py` (16%→90%) closed; AI15 added and closed 2026-08-01 (`backend/ai/pii.py` card-number/SIN scrubbing gaps, found via `/ai-check`). Sections: A=launch-gating, B=pre-launch fixes, C=operational, D=post-launch, E=industry-parity._

---

## P0 — Launch gating (code)

### A34. Dual-run cutover readiness audit (2026-08-15) — decommission blockers and required decisions
> **Note:** there is a second, unrelated `### A34` further down this file
> ("Legacy-imported ride count dropped 224 → 186 in production — CLOSED") from
> a different session on 2026-08-16. Both numbers are already cross-referenced
> from other docs, so neither has been renumbered — treat the heading text as
> the disambiguator, not the number. Filed as a process gap: this repo has no
> mechanism preventing two parallel sessions from picking the same "next free"
> ACTION_ITEMS number, the same collision class already found in SQL migration
> filenames (310×2, 313×3, `docs/audit/2026-08-15-full-fleet-launch-readiness.md`
> item #11).
- [ ] **Status:** open, materially updated 2026-08-17 by later sessions'
  work — see `docs/runbooks/full-app-audit.md`'s Prior-Findings Ledger for the
  full detail; summary below. Old app decommission target: **Oct 31, 2026
  (tentative)**.
  - **RESOLVED**: the 224-vs-186 discrepancy — see the *other* A34 below,
    CLOSED 2026-08-16 as intentional pre-launch test-account cleanup (also
    spun off **A35**, a real finding: the cleanup script bypassed the 7-year
    `driver_insurance_periods` retention guard triggers — read it).
  - **RESOLVED (superseded number)**: the $276.59/20-driver figure — Stripe
    cross-check ran 2026-08-16
    (`docs/change-log/2026-08-16-gst-backfill-and-stripe-crosscheck.md` §1a).
    Revised: **$185.31–$228.08** for buckets 1–15 (2 unresolved, $42.77,
    genuinely ambiguous — one shows evidence trending *owed*, not excluded);
    1 bucket ($22.43) likely already paid via Stripe, correctly excluded;
    buckets 16–20 ($26.08) unaffected, still blocked on driver re-link.
    Structural finding: the Stripe mirror schema can never definitively link a
    ledger row to a specific ride — "likely" is the ceiling. Still open: which
    Stripe account the mirror covers (old app's/new app's/both) — unconfirmed.
  - **RESOLVED**: rider legacy-import provenance — 918/1,137 users backfilled
    2026-08-17 (`docs/change-log/2026-08-17-rider-provenance-backfill-executed.md`).
    **Correction (2026-08-17, later same day):** that change-log's claim that
    "the code gap (`rider_import_service.py` never stamps new imports) is
    still open" was stale/wrong — `rider_import_service.py` already stamps
    `legacy_import_metadata` on both create and update as of `a591cf1`
    (PR #3678, merged 2026-08-11, **before** the backfill even ran), with
    dedicated coverage in `test_admin_rider_import.py`
    (`test_commit_stamps_provenance_on_created_user`,
    `test_commit_stamps_provenance_on_updated_user_without_clobbering_other_metadata`).
    No code change needed; this note exists only to stop a future session
    from re-doing already-shipped work off a stale status line.
  - **RESOLVED**: `payout_gst_amount` for the 186 already-migrated rides —
    backfilled additive-only, $102.09 total
    (`docs/change-log/2026-08-16-gst-backfill-executed.md`). **D1 remains
    open**: what `tax_amount` itself should read for those 186 rows is a
    business/legal decision, not resolved by the backfill — needs an owner
    + due date.
  - **STILL OPEN, unchanged**: fresh final old-app export (unblocks the true
    pending-money figure, the full identity map, and the corporate-money
    unknown); insurance-period audit-trail gap for imported rides (legal/SGI
    decision — engineering must NOT fabricate period rows); no
    final-export/teardown runbook owner/dates (draft exists,
    `docs/runbooks/full-app-audit.md` Part B §3.2); double-dispatch/
    double-payout structural risk (needs an operational roster policy — code
    provides no guard); 3 monitoring signals **now shipped** (PR #3954,
    `dual_run_monitoring_enabled`, verify still live rather than treating as
    a to-do); open $16.63 Stripe dispute needs a response; rider-referral
    velocity/identity-cross-check gap unchanged; 22 unmarked drivers; two
    incompatible legacy-ID namespaces still need a crosswalk table.
- **Files:** `docs/audit/2026-08-15-dual-run-cutover/` (4 phase reports),
  `docs/runbooks/full-app-audit.md` (repeatable master audit prompt — supersedes
  ad-hoc scratch prompts for future runs), PR #3946 (merged, dry-run-only as
  designed, extend don't duplicate the write path).
- **Acceptance:** each numbered blocker either closed with evidence or explicitly risk-accepted by the user with a dated note here.

### A1. Per-module test-coverage floors for money paths
- [x] **Status:** DONE (2026-07-28) — `matching.py` and `rides/payments.py`,
  the two remaining files below the 80% target, are now closed:
  - `routes/rides/matching.py`: 78% → **89%** (18 new tests,
    `backend/tests/test_rides_matching_coverage.py`), commit `3c83ee8`.
  - `routes/rides/payments.py`: 70% → **96%** (14 new tests,
    `backend/tests/test_rides_payments_coverage.py`), commit `297b776`.
  Test-only, additive changes — no production code touched. All files in the
  `routes/rides/` package and the money-path modules listed below now meet
  or exceed their 80%/90% targets.
  <details><summary>History</summary>

  **Status (2026-07-27):** in progress — measured actual current per-file
  coverage (full local suite run, `coverage.xml`), which changes the shape of
  this item from what it originally assumed:
  - `routes/payments.py`: **90.72%** — already meets the 90% target.
  - `services/fare_service.py`: **99.36%** — already meets the 90% target.
  - `services/dispatch_service.py`: was 75.86%, **now 92.53%** after adding
    10 tests (`tests/services/test_dispatch_service.py`) covering the
    previously-untested Spinr Pass gate block in
    `DispatchService.find_candidate_drivers` — required-area subscription
    filter, parent-area inheritance, expired-subscription handling, the daily
    ride-allowance/quota filter, and both fail-open exception paths (quota
    lookup failure, general pass-filter DB error). Meets the 80% target.
  - `routes/rides.py` no longer exists as a single file — the god-file split
    (see `backend/CLAUDE.md` / `docs/refactors/god-file-split.md`) replaced it
    with a `routes/rides/` package, and CLAUDE.md's target was never updated
    to reflect that. Per-file coverage in that package was **highly uneven**:
    `lost_found.py` 25% (now **100%** — see below, meets target),
    `receipts.py` 58.3% (now **100%** — see below, meets target),
    `matching.py` 64.7% (now **79.41%** combined — PR #2557 merged, this PR
    adds the remaining increment; see below — just under the 80% target),
    `lifecycle.py` 65.1% (now **87.88%** — meets target),
    `booking.py` 65.7% (now **91.75%** — meets target, PR #2559 + follow-up),
    `queries.py` 69.2% (now **92.20%** — meets target, PR #2544),
    `estimates.py` 71.0% (now **93.99%** — meets target, PR #2552),
    `cancellation.py` 71.0% (now **95.06%** — meets target, PR #2555),
    `rides/payments.py` 79.5%.
  - `routes/rides/lost_found.py`: was 25%, **now 100%** after adding 10 tests
    (`tests/test_lost_found.py`) covering the 404/403/400 guard clauses,
    category-validation fallback, the driver-notification success path (push
    sent + status updated to `driver_notified`), and three
    notification-skip/failure branches. Full backend suite re-run after:
    4697 passed, 8 skipped, 1 xfailed, 0 failed — no regressions.
  - `routes/rides/lifecycle.py`: was 65.1%, **now 87.88%** after adding 23
    tests (`tests/test_coverage_rides.py`) covering the ride state-machine
    functions' previously-untested guard clauses (404/403/400/409 branches
    across `simulate_driver_arrival`, `rider_start_ride`,
    `rider_complete_ride` — including both atomic-transition race-lost 409
    paths) and their fail-open exception paths (insurance-period audit write,
    daily-quota check, driver-earnings snapshot, admin broadcast, quest
    scheduling, quota-exhaustion driver notification). Meets the 80% target.
    Remaining gap: the ride-incentive-claim happy path (lines 208-230,
    a Supabase query-builder chain) and a small WS/push branch in
    `rider_start_ride` — left uncovered, lower priority than the other
    `routes/rides/` files below.
  - `routes/rides/matching.py`: was 64.7%, then 72.48% (25 tests,
    `tests/test_offer_timeout.py` + `tests/test_p0_ship_blockers.py`,
    covering the file's smaller, self-contained functions:
    `create_demo_drivers` (deprecated no-op), `_dispatch_retry`'s
    attempt-cap/ride-left-searching guards and error-backoff reschedule,
    `process_expired_offer`'s claim-lost-returns-False path, the
    auto-offline-at-threshold branch, and its two fail-open exception paths
    (Redis skip-key write, WS notify), `_batch_offer_timeout_handler`'s two
    early-returns and settings-fetch-failure fallback plus its outer
    exception handler, and `ride_search_timeout`'s pre-auth-release
    success/failure branches, the attribution-column-fallback retry, the
    guest-booking SMS branch, and its outer exception handler), **then
    76.05%** (PR #2557, merged) after adding 8 more tests
    (`tests/test_dispatch_match_attempt_branches.py`) covering
    `_match_driver_to_ride_attempt`'s (lines 151-933, ~780 lines) most
    self-contained guard clauses and fail-open/fail-closed exception paths:
    the stale-ride-status skip (dispatch already progressed past
    `searching`), the subscription filter's fail-**closed** exception
    (empties the pool — a subscription-gated area must never leak an offer
    to a non-subscriber on a DB hiccup), the daily-quota filter's fail-**open**
    exception (must NOT drop the pool — a transient quota-lookup error can't
    strand every ride), the cascade pool's own subscription sub-filter
    (drops non-subscribed cascade/upgrade-type drivers, and its own
    fail-closed exception), the cascade lookup's outer exception (non-fatal —
    falls through to the no-eligible-drivers retry), the
    `ride_offers`-insert failure (releases the claimed driver back to
    available *before* re-raising, so a transient insert failure can't
    strand a driver as claimed-but-never-offered), the final
    no-eligible-drivers-after-all-filters retry, and the
    no-drivers-could-be-claimed early return (claim lost to a race). **This
    PR** adds a further increment on top of that 76.05% baseline (5 tests,
    `tests/test_dispatch_notify_loop_branches.py`) covering the
    ETA-ranking/batch-claim/parallel-enrichment/per-driver-notify block
    (~lines 650-930): a full happy-path test building the WS
    `new_ride_assignment` payload end-to-end (quest progress, active
    incentive, signed offer-card URL, FCM push all populated) plus the
    ETA-ranking Distance-Matrix-failure fallback and the notify loop's three
    fail-open exception paths (quest-progress lookup, offer-card URL
    signing, FCM push spawn). **Combined with PR #2557: 76.05% → 79.41%** —
    just under the 80% target. Full local suite confirmed 0 new/different
    failures (4821 passed, same known 9 A7 `test_ai_tools_booking.py`
    failures). Remaining gap (~0.6pp) is scattered across the file's
    smallest remaining uncovered branches — deferred as low-priority; not
    worth a dedicated follow-up PR for under a percentage point.
  - `routes/rides/receipts.py`: was 58.3%, **now 100%** after adding 15 tests
    (`tests/test_coverage_rides.py`) covering both endpoints end-to-end:
    `get_ride_receipt`'s no-driver-shows-"Unknown Driver" branch, the
    vehicle-type lookup, the corporate-account "Corporate Account" payment
    method + name branch, the cancelled-ride cancellation-fee sum, the
    fare-lock snapshot path (including the synthesized tip line when the
    snapshot predates the tip), and the settings-lookup-failure fallback to
    the dynamic (non-locked) rebuild; plus `email_ride_receipt` end-to-end
    (404/403/400 guards, success, and the 503-on-send-failure path). Meets
    the 80% target.
  - `routes/rides/booking.py`: was 65.7%, **now 77.84%** after adding 21
    tests (new `tests/test_ride_insert_and_dispatch_prep.py`, plus 5 more in
    `tests/test_ride_preauth_booking.py`) covering the file's two
    previously-fully-uncovered helpers — `_insert_ride_with_code` (the
    PGRST204 pre-migration-40 fallback, both constraint-specific branches —
    `rides_one_active_per_rider` → 409, `idx_rides_rider_idempotency_key`
    replay → returns the winning ride so the caller skips re-running
    dispatch/promo side-effects — the ride_code-collision retry, and the
    3-attempt-exhaustion → 503 path) and `_prep_and_dispatch` (the
    post-booking background pipeline: pickup road-snap, server polyline
    computation, dispatch kickoff, and its fail-open behavior at every
    stage — a snap/polyline/dispatch failure must never propagate, since
    this runs as a fire-and-forget background task) — plus 3 previously-
    missed branches in the already-tested `_preauthorize_ride_card` /
    `_attach_preauthorized_hold` pre-auth helpers (fare-only retry
    requires-SCA, fare-only retry ops-failure degrade, PI-reuse-lookup
    failure fail-open), **now 84.54%** after adding 10 more tests
    (`tests/test_create_ride_remaining_branches.py`) covering `create_ride`'s
    remaining guard clauses: the `service_areas` fetch failure → 503,
    insufficient wallet balance → 400, the pre-dispatch corporate policy
    check (both the 403-with-reasons failure and the passing path's
    `corporate_member_id` resolution), the `work_profile` corporate
    pre-dispatch block (no active membership / policy violation / allowance
    too low to cover the 1.5× buffer — all → 400 with a `reason` code), the
    SCA two-step first-leg early return (hands back `client_secret` without
    ever inserting a ride — the state machine must stay clean until the
    client re-books with the confirmed hold), the DB-level idempotency-key
    replay early return, and `calculate_all_fees` failing mid-booking → 503.
    Meets the 80% target. **Now 91.75%** after a further 7 tests
    (new `tests/test_create_ride_post_insert_branches.py`) covering
    `create_ride`'s post-insert side-effect blocks: promo-code application
    (success — updates `grand_total`/`discount_amount`/`promo_code` on the
    ride; rejection via `HTTPException` — sets `promo_error`, non-fatal;
    unexpected exception — sets a generic `promo_error`, non-fatal), the
    fare-breakdown snapshot save failure (non-fatal, logged), the admin
    live-monitoring broadcast failure (non-fatal), the post-dispatch
    `ride_search_timeout` spawn when the ride is still `searching`, and the
    road-distance settings-fetch exception inside the haversine-fallback
    safety net (swallowed, falls back to the `"road"` mode default). The
    geofence stop-loop's `s_lat is None or s_lng is None: continue` branch
    (line 507) was investigated and confirmed **unreachable via the public
    API** — `CreateRideRequest.validate_stops` already rejects any stop
    missing lat/lng at the Pydantic layer before `create_ride` runs, so it's
    defensive dead code, not a real gap; not covered, and not worth
    covering. Remaining ~8% gap is the corporate work-profile pre-dispatch
    block's happy path and the planned-route-snapshot spawn's inner
    success/failure branches — low-value, not scheduled.
  - `routes/rides/queries.py`: was 69.2%, **now 92.20%** (PR #2544) — meets
    the 80% target.
  - `routes/rides/estimates.py`: was 71.0%, **now 93.99%** (PR #2552) — meets
    the 80% target.
  - `routes/rides/cancellation.py`: was 71.0%, **now 95.06%** (PR #2555) —
    meets the 80% target.
- **Why:** CLAUDE.md mandates ≥90% for `routes/payments.py` + `services/fare_service.py`
  and ≥80% for `routes/rides.py` (now the `routes/rides/` package) +
  `services/dispatch_service.py`; the global floor in `backend/pytest.ini` is
  only 60%.
- **Files:** `backend/pytest.ini`, new tests under `backend/tests/` — done so
  far: `backend/tests/services/test_dispatch_service.py`,
  `backend/tests/test_lost_found.py`,
  `backend/tests/test_coverage_rides.py` (lifecycle + receipts + queries
  functions), `backend/tests/test_rider_stats_empty.py` (queries.py),
  `backend/tests/test_offer_timeout.py` + `backend/tests/test_p0_ship_blockers.py`
  (matching.py's smaller functions), `backend/tests/test_dispatch_match_attempt_branches.py`
  (PR #2557, matching.py's guard/fail-open branches),
  `backend/tests/test_dispatch_notify_loop_branches.py` (this PR,
  matching.py's ETA-ranking/enrichment/notify-loop branches),
  `backend/tests/test_ride_insert_and_dispatch_prep.py`
  + `backend/tests/test_ride_preauth_booking.py`
  + `backend/tests/test_create_ride_remaining_branches.py` (booking.py),
  `backend/tests/test_ride_estimate_branches.py` (estimates.py),
  `backend/tests/test_ride_cancellation_branches.py` (cancellation.py).
  Combined with PR #2557 (merged): `matching.py` **64.7% → 79.41%** — the
  only remaining file in the `routes/rides/` package, ~0.6pp short of the
  80% target. Not scheduling a dedicated follow-up for the remainder; see
  Acceptance below.
- **Approach:** measure current per-file coverage (`pytest --cov --cov-report=term-missing`),
  write tests for the uncovered branches (fare tiers, surge, corporate, promo, refund,
  webhook types, ride-state transitions), then enforce with
  `coverage report --fail-under` per path or a `ci-guardrails` step. Ratchet,
  don't big-bang — one file/PR at a time, per CLAUDE.md's ≤3-files-per-subtask
  rule.
- **Acceptance:** CI fails if payments/fare coverage drops below 90% or
  `routes/rides/*` / dispatch below 80%. Payments, fare, dispatch,
  `lost_found.py`, `lifecycle.py`, `receipts.py`, `queries.py`,
  `estimates.py`, `cancellation.py`, `booking.py`, `matching.py` (89%), and
  `rides/payments.py` (96%) all meet target as of 2026-07-28. All A1-scoped
  money/dispatch paths now meet their coverage floors.
  </details>

### A1b. Backend test-coverage floor — Track 1 (money/safety/compliance-adjacent)
- [x] **Status:** closed (2026-08-01) — Track 1 complete. Raised 2026-07-27 when
  the user asked why A1 only touched money/dispatch paths rather than the
  whole backend. Answer: A1's mandate (CLAUDE.md) is explicitly ≥90% for
  payments/fare and ≥80% for rides/dispatch — not a whole-codebase target.
  The global CI floor (`backend/pytest.ini`) is only 60%, and everything
  outside A1's file list currently sits there or below. This item originally
  scoped two tracks; Track 2 (breadth, lower urgency) was never started and
  is spun off as its own item, **A1c**, below — closing this item's
  acceptance against Track 1 only, which is fully done (all four priority
  groups below at or above their 80%/90% targets).
- **Why:** the same logic that justified A1 (higher-risk code deserves a
  higher bar) applies to other domains this session never touched —
  corporate billing, safety/SOS, auth/RLS, and admin actions all have
  real-world consequences (money, safety, compliance) if a regression ships
  untested. Full-backend uniform 80% is not the recommendation — see
  Approach below for why targeted beats uniform.
- **Files:** none yet — this is a scoping entry. When picked up, follow the
  same pattern as A1: one file/PR at a time, ≤3 files per subtask, measure
  real `coverage.xml` numbers before writing tests (files are frequently
  already better- or worse-covered than assumed).
- **Approach — Track 1 (money/safety/compliance-adjacent, recommend first):**
  measure current coverage for, in priority order:
  1. Corporate billing layer — **measured 2026-07-28** (post PRs #2615,
     #2696): module aggregate ~52% against a proposed 80% target (see
     `CLAUDE.md`'s coverage-minimums table and `.claude/context/domain-corporate.md`).
     New code from the lifecycle-audit fixes is well-covered (79–90%); the
     gap is concentrated in pre-existing files, priority order for a future
     session:
     - `routes/corporate_accounts.py` — **done, 82%** (was 39% as measured
       against a narrow corporate-only test subset in the original scoping
       pass; re-measured against the full corporate-admin-route test set —
       `test_admin_business_logic.py`, `test_admin_rbac.py`,
       `test_corporate_admin_routes.py`, `test_corporate_b2b_schema.py`,
       `test_corporate_db_helpers.py`, `test_corporate_e2e_foundation.py`,
       `test_corporate_e2e_wallet.py`, `test_corporate_kyb.py`,
       `test_corporate_status.py`, `test_corporate_stripe_customer.py`,
       `test_corporate_wallet_bootstrap.py`, `test_corporate_wallet_freeze.py`,
       `test_db.py`, `test_deprecated_route_admin_exempt.py`,
       `test_error_response_sanitisation.py`, `test_features.py`,
       `test_p3_admin_jwt_modules.py`, `test_stripe_event_loop_offload.py`
       — the real baseline was 77%, not 39%. +9 tests
       (`test_corporate_admin_routes.py`) closed the highest-value remaining
       gaps: validator no-ops, the `is_active` list filter, the
       `X-Total-Count` exception fallback, the previously entirely-untested
       `kyb_upload_url` endpoint, and two `kyb_document_confirm` error
       branches. Meets the 80% target. Remaining 18% is `change_company_status`'s
       deepest nested exception branches and `kyb_review`'s email-failure
       paths — lower priority, not pursued further in this pass. See
       `docs/change-log/2026-07-28-corporate-accounts-coverage-80.md`.
     - `services/corporate_wallet_service.py` — **done, 97%** (closed
       2026-07-28, see `docs/change-log/2026-07-28-corporate-wallet-service-coverage-80.md`),
       `services/corporate_allowance_service.py` — **done, 97%**
       (closed 2026-07-28 — the existing `tests/services/test_corporate_allowance_service.py`
       already covered every branch once measured in isolation (`pytest tests/ -k allowance`);
       the previously-tracked 39% figure was stale/measured differently, no new tests were
       needed; see `docs/change-log/2026-07-28-corporate-allowance-service-coverage-80.md`)
     - `routes/corporate_company_bookings.py` — was 38%, now **87%** (test-only
       PR `claude/corporate-company-bookings-coverage`, 2026-07-28): added
       `backend/tests/test_corporate_company_bookings_coverage.py` (31 tests
       incl. the pre-existing `test_corporate_sections.py`) covering
       `create_booking`, `list_bookings` (tenancy scoping, filters, N+1-free
       batch joins), `cancel_booking` (authz + tenancy + missing-guest paths),
       `booking_fare_estimate`, `_require_company_active`, and the
       `_booking_row` OTP-redaction projection. Remaining 13% is dual-import
       `except ImportError` fallback lines (structurally only one branch runs
       per process) and section-CRUD duplicate/404 paths already exercised by
       `test_corporate_sections.py` — diminishing returns, see
       `docs/change-log/2026-07-28-corporate-company-bookings-coverage-80.md`.
     - `routes/corporate_signup.py` — **89%** (was 32-33%; closed
       2026-07-28, see `docs/change-log/2026-07-28-corporate-signup-coverage-80.md`).
       Remaining 7 uncovered lines are the dual-import `ImportError` fallback
       block (untestable per the dual-import convention, not a real gap).
     - `routes/corporate_rider.py` — **97%** (closed 2026-07-28, up from
       32-33%, see `docs/change-log/2026-07-28-corporate-rider-coverage-80.md`).
     - `routes/corporate_company_kyb.py` — **closed 2026-07-28: 32-33% →
       98%** (target was 80%). See
       `docs/change-log/2026-07-28-corporate-company-kyb-coverage-80.md`.
     - `services/corporate_membership_service.py` — **100%** (was 27%,
       closed 2026-07-28: 17 unit tests added for the invite/accept race
       branches, domain auto-match edge cases, `join_via_domain` fallback,
       `_uuid_or_none` actor-id coercion, and both `bootstrap_owner` modes
       — test-only change, see `docs/change-log/2026-07-28-corporate-membership-service-coverage-80.md`),
       `services/corporate_policy_service.py` — **98%** (was 68%, closed
       2026-07-28: 13 unit tests added for `evaluate_policy_for_ride` —
       previously entirely untested (0 of its 52 lines) — covering the
       happy path, no-matching-membership, policy-fetch-failure fail-open,
       membership-lookup-failure graceful-degradation, and member-level vs
       caller-level `policy_override` precedence; plus 2 pure-function
       branch tests for datetime-object and tz-aware `pickup_time` in
       `evaluate_policy`'s time-window rule — test-only change, see
       `docs/change-log/2026-07-28-corporate-policy-service-coverage-80.md`)
     All items in this track are now at or above the 80% target.
  2. `backend/utils/insurance_periods.py`, safety check-in / SOS-related
     routes (see `.claude/context/domain-safety.md`) — regulatory +
     rider/driver safety consequence if untested code has a latent bug.
     Closed 2026-07-29:
     - `backend/utils/insurance_periods.py` — see
       `docs/change-log/2026-07-29-a1b-insurance-periods-coverage.md`.
     - `backend/routes/safety.py` — see
       `docs/change-log/2026-07-29-a1b-routes-safety-coverage.md`.
     - `backend/routes/rides/safety.py` — see
       `docs/change-log/2026-07-29-a1b-rides-safety-coverage.md`.
     - `backend/routes/admin/safety.py` — see
       `docs/change-log/2026-07-29-a1b-admin-safety-coverage.md`.
     - `backend/utils/safety_checkin_loop.py` — coverage raised 85%→87%,
       see `docs/change-log/2026-07-29-a1b-safety-checkin-loop-coverage.md`.
       Writing the coverage tests surfaced a **real production bug**, since
       fixed: the module's `except ImportError` fallback (the branch
       actually active in production) never imported `notify_safety_team`,
       so every auto-escalated no-response safety check-in silently
       NameError'd inside `_escalate`'s broad exception handler — the
       incident row and audit log were written, but the safety team was
       never actually paged (no WS broadcast, no email, no PagerDuty log
       line). Fixed by adding `notify_safety_team` to the fallback import
       list to mirror the `try` branch, plus a regression test
       (`test_escalate_calls_notify_safety_team`) that fails against the
       pre-fix code and passes now. See
       `docs/change-log/2026-07-29-safety-checkin-notify-fix.md`.
  3. Auth/RLS-adjacent code: JWT handling, OTP verification
     (`backend/utils/crypto.py` is already tracked at ≥90% target per
     CLAUDE.md but should be re-verified), refresh-token rotation
     (`backend/utils/refresh_tokens.py`). **In progress (2026-07-29):**
     - `backend/utils/crypto.py` — re-verified: **100%**, already exceeds
       the ≥90% target, no work needed.
     - `backend/utils/refresh_tokens.py` — **done, 99%** (was 62%; the
       existing test file only pinned the reuse-detection cascade —
       `issue_refresh_token`, `lookup_refresh_token`'s remaining branches
       (empty input, DB error, expiry parsing), `revoke_refresh_token`,
       and `revoke_all_for_user` had zero coverage). Added 25 new tests
       across two files: `tests/test_refresh_tokens_lifecycle.py` (mint/
       lookup/revoke lifecycle) and one Sentry-capture-failure test added
       to the existing `tests/test_refresh_token_reuse_detection.py`.
       Remaining 1 line is the dual-import fallback. Test-only, no bugs
       found. See `docs/change-log/2026-07-29-a1b-refresh-tokens-coverage.md`.
     - `repositories/auth_repo.py` — **done, 96%** (was 67%; had zero
       dedicated test file). Added `tests/test_auth_repo.py` (18 tests)
       covering the "Supabase client not configured" branch and the happy
       path for all 8 functions (user lookup/creation, OTP CRUD).
       Remaining 2 lines are the dual-import fallback. Test-only, no bugs
       found. See `docs/change-log/2026-07-29-a1b-refresh-tokens-coverage.md`.
     - `dependencies/__init__.py` — **done, 93%** (was 62%/77%; the JWT
       auth-gate module's Firebase-token success path — uid lookup, phone
       fallback, session-revocation via `sessions_invalid_before`, driver
       caching, deleted-account enforcement — had **zero** direct
       coverage; existing tests only exercised the "not a Firebase token,
       fall through to JWT" branch. Also closed `_verify_admin_payload`'s
       staff-inactive / stale-token-version / idle-timeout / malformed-
       timestamp branches, JWT-path DB-error propagation (never silently
       swallowed, per CLAUDE.md), and `get_current_user_allow_expired`'s
       admin-audience-gets-no-grace and not-actually-expired branches.
       Added `tests/test_dependencies_auth_gaps.py` (20 tests). Remaining
       21 lines are the dual-import fallback plus a handful of
       log-statement-only branches. Test-only, no bugs found.
     - `core/middleware.py` — **done, 81%** (was 60%/69%;
       `_validate_production_config` — the fail-fast guard that stops a
       misconfigured deploy from ever serving traffic when
       `ENV=production` — was only ever *patched away* (mocked out) in
       `test_p1_cors.py`, never exercised directly. Added
       `tests/test_middleware_production_config_guard.py` (16 tests)
       covering every one of its 5 checks (JWT secret weak/short,
       Supabase URL missing/placeholder, service-role key
       missing/malformed/short, admin creds weak, rate-limit Redis URL
       missing/non-redis-scheme) both individually and combined into one
       `RuntimeError`, plus the Firebase-creds-missing warn-only path.
       Remaining gap is four nested middleware classes defined inside
       `init_middleware(app)` (App Check enforcement, CORS exception
       handler, relative-redirect rewriting, deadline propagation) —
       these need `TestClient`-level request testing, not unit-testable
       in isolation; lower priority, diminishing returns for this pass.
       See `docs/change-log/2026-07-29-a1b-dependencies-middleware-coverage.md`.
     - `routes/auth.py` — **55%→69%** (`verify_otp` — the core rider/driver
       login/signup endpoint, 382 lines — had zero direct coverage of its
       success path; existing tests only pinned the lockout helpers and
       the "DB error is not a wrong code" 503 case). Added 13 tests
       (`tests/test_verify_otp_login_flow.py`) covering existing-user
       login, guest-account claim-on-verify (`is_guest` cleared),
       session-update-failure-doesn't-block-login, the PIPEDA
       `pending_deletion` reactivation handoff, the fully-deleted-account
       410, new-user creation (+ `create_user` DB-failure → 503, never
       mints a token for an unpersisted row), and 4 OTP-record validation
       branches (wrong code, expired, malformed/missing `expires_at`).
       See `docs/change-log/2026-07-29-a1b-verify-otp-coverage.md`.
       **Update 2026-07-30 — now 66%**: added 44 more tests
       (`tests/test_auth_remaining_endpoints.py`) covering the
       company-email-OTP flow (`send_company_email_otp`/
       `verify_company_email_otp`), `firebase_auth_login`,
       `refresh_access_token`, `logout`/`logout_all`, and
       `reactivate_account` — success paths, validation-error branches,
       and DB-failure propagation. No application code changed, no bugs
       found. Remaining gap is deeper validation branches and the
       dual-import fallback. See
       `docs/change-log/2026-07-30-a1b-auth-remaining-endpoints-coverage.md`.
       **Update 2026-08-01 — done, 84.6%**: closed the last real gap —
       `GET /me`'s three failure branches (`profile_complete` self-heal DB
       write, rider ride-count fetch, driver-onboarding-status derivation)
       had zero coverage despite each being explicitly commented as a
       "must log, never silently swallow" path (one citing B-P1-5 /
       CLAUDE.md directly). Added `TestGetMeFailureBranches` (3 tests,
       `tests/test_auth_remaining_endpoints.py`) confirming all three
       already correctly log-and-continue — no bug found, closes coverage
       on already-correct behavior. Also added 6 tests to
       `tests/test_auth_send_otp.py` for `send_otp`'s rate-limit (per-
       minute/hourly 429), Redis-fail-closed 503, production-without-
       Twilio 503, and OTP-store-write-failure 503 branches. No
       application code changed. Full suite: `6715 passed, 8 skipped,
       1 xfailed, 0 failed`. Remaining 15.4% is dual-import fallback plus
       lower-value validation/log-only branches — diminishing returns, not
       pursued further. See
       `docs/change-log/2026-08-01-a1b-routes-auth-coverage-finish.md`.
       **This closes Track 1 item 3 (auth/RLS-adjacent code) — every file
       in this item is now at or above its target, and with items 1
       (corporate), 2 (safety), and 4 (`backend/routes/admin/`) already
       closed, Track 1 of A1b is now fully complete.**
     - `routes/admin/auth.py` — **done, 94%** (was 70%, re-measured fresh
       against the full suite — the previously-tracked 64-70% figure was in
       the right ballpark). The endpoint was well-covered for login/MFA/
       refresh/logout flows by 9 pre-existing sibling test files
       (`test_admin_mfa_enforcement.py`, `test_admin_mfa_totp_lockout.py`,
       `test_admin_login_resets_idle_clock.py`, `test_admin_logout_revocation.py`,
       `test_admin_token_aud_lockdown.py`, `test_admin_privilege_escalation.py`,
       `test_admin_staff_mfa_reset.py`, `test_admin_security.py`,
       `test_admin_routes_auth.py`) — the entire gap was two endpoints with
       **zero** direct coverage: `/admin/auth/break-glass` (emergency
       super-admin token mint — every guard branch: feature-gated-off,
       short justification, Redis-unreadable/increment-failure/allowlist-
       failure fail-closed paths, rate-limit-exceeded, invalid token, and
       the happy path incl. audit-log-write-failure being logged but not
       blocking) and `/admin/auth/unlock` (role guard, empty email,
       target-not-found, idempotent not-locked path, Redis-read failure,
       successful unlock) — plus `/mfa/status`, `/mfa/enroll`, `/session`'s
       malformed-header shapes, `/refresh`'s admin-001 branch, and
       `/logout-all`'s malformed-token branches. Added
       `tests/test_admin_auth_coverage_gap.py` (33 tests). Remaining 6% is
       the dual-import fallback plus a few log-only branches in
       `_require_staff_from_token`. Test-only, no bugs found. See
       `docs/change-log/2026-07-29-a1b-admin-auth-coverage.md`.
  4. `backend/routes/admin/` (15+ admin-only endpoints) — admin actions are
     audited but not necessarily tested; a broken admin endpoint can corrupt
     production data at scale (e.g. bulk driver approval, wallet
     adjustments).
     - `backend/routes/admin/drivers.py` — **improved 59% → 70%** (1015
       statements, 301 remaining uncovered; measured via full `pytest tests/
       -q`, real pytest-cov output). Prioritized write/mutation endpoints
       over read-only list/search per the item's stated risk (a broken
       write here can lock a real driver out or leave an ineligible driver
       online — regulatory consequence under the Saskatchewan
       Transportation Act driver-eligibility rules). Added
       `tests/test_admin_drivers_coverage.py` (52 tests) covering:
       `POST /drivers/{id}/action` (approve/suspend/ban/unban/reactivate,
       missing-reason 400s, driver-not-found 404, DB-failure 500,
       push-failure non-fatal), `PUT /drivers/{id}/status-override`,
       `POST /drivers/{id}/verify`, `PUT /drivers/{id}` (field routing
       across `users`/`drivers`, null-coalescing, work-authorization-status
       flag sync, 409 on email/gender without a linked user), driver notes
       CRUD, `POST/GET /drivers/{id}/photo` + `/photo-review`,
       `PUT /drivers/{id}/area`, `POST /drivers/{id}/nudge-expiry`,
       `POST /drivers/{id}/refresh-stripe-kyc`, and
       `POST /drivers/{id}/reveal-sin` (super_admin-only gate, SIN never
       logged, Stripe-failure 502). Deprioritized (left at their existing
       coverage): the pure read/list/export endpoints
       (`GET /drivers`, `/drivers/stats`, `/drivers/approval-queue`,
       `/drivers/expiring`, referral leaderboards/analytics,
       payouts-summary, location-trail, daily-activity) — lower real-world
       consequence than a broken write, and several are already exercised
       by `test_admin_approval_queue.py` / `test_admin_drivers_expiring.py`
       / `test_referral_analytics.py`. **Two pre-existing bugs found, not
       fixed (test-only task)** — see
       `docs/change-log/2026-07-29-a1b-admin-drivers-coverage.md` for
       detail: (1) `admin_driver_action`'s `DriverActionRequest.action`
       Literal and docstring both list `"reject"` as valid, but the
       if/elif chain has no `reject` branch — it 400s with "Unknown
       action: reject", so an admin can never reject a driver application
       through this endpoint; (2) `admin_override_driver_status`'s
       `DriverStatusOverride.status` Literal includes `"rejected"` but the
       endpoint's own `valid` set does not (and vice versa for
       `"needs_review"`), so some pydantic-valid status values 400 at the
       handler's internal guard.
     - `backend/routes/admin/analytics.py` — **done, 91%** (was ~24%, lowest
       -covered file in `routes/admin/`). Added `tests/test_admin_analytics_
       coverage.py` (24 tests) covering `cancellation-reasons`,
       `driver-acceptance`, `overview` (incl. Redis cache hit/corrupt-cache/
       set-failure paths), `dashboard`, `demand-forecast`(+summary), and
       `driver-offer-stats`/`-trends` — happy path, empty/zero-division
       guards, and the 503-on-DB-error path for each. `surge-history` was
       already covered by `test_admin_surge_history.py`. Test-only, no bugs
       found. See `docs/change-log/2026-07-29-a1b-admin-analytics-incentives-coverage.md`.
     - `backend/routes/admin/incentives.py` — **done, 98%** (was ~34%).
       Driver-incentive/bonus program management is money-adjacent (bonus
       payouts to drivers), so create/update/toggle/delete were prioritized
       over the read-only list/stats endpoints. Added `tests/test_admin_
       incentives_coverage.py` (24 tests, incl. `bonus_amount` boundary
       validation at the >500/<=0 Pydantic gate and the 503-on-DB-error path
       for every write endpoint). Test-only, no bugs found. See
       `docs/change-log/2026-07-29-a1b-admin-analytics-incentives-coverage.md`.
     - `backend/routes/admin/rides.py` — **34% → 42% (2026-07-30) → two
       independent, concurrent 2026-08-01 sessions both picked up the
       deferred read/list/export/analytics gap, unaware of each other; both
       landed, reconciled here rather than picking one over the other:**
       - **Batch 2** (PR #3057) extended the existing
         `tests/test_admin_rides_coverage.py` in place (57 → 81 tests, 24
         new), measured single-file (`--cov=routes.admin.rides` against that
         file alone): **42%/52.35% → 70%**. Closed ride location-trail/live/
         invoice, send-receipt, heatmap-data, earnings (+/rides +/overview),
         export/rides, export/drivers, payouts/overview (incl. the
         no-drivers-in-area empty-shell branch), dashboard `/stats`,
         fare-estimate, promo/preview, and the places-proxy not-configured
         503 guards. Full-suite `--cov` re-measurement wasn't obtained in
         that session (sandbox-specific coverage-instrumentation/
         `pyiceberg` import interaction) — see
         `docs/change-log/2026-08-01-a1b-admin-rides-coverage-batch2.md`.
       - **This session** (unaware of #3057 in flight, branched from main
         before it merged) added a *second*, separate file,
         `tests/test_admin_rides_read_endpoints_coverage.py` (41 tests,
         overlapping in target endpoints with batch 2 above but written
         independently), and — per this task's own instruction not to trust
         the stale 42% figure — re-measured **full-suite** coverage twice
         (before and after its own changes, both against a pre-#3057 base):
         **already 80%** (242/1190 uncovered) *before* this session added
         anything, because the backend suite had grown from ~5610 to 6576
         tests since 2026-07-30 via unrelated A1b work, several of which
         incidentally exercise this file as a side effect. This session's 41
         new tests all pass (suite pass count rose 6576→6617) but did not
         move the aggregate number (byte-identical 242-line gap before/
         after) — every line they touch was already reachable elsewhere.
         See `docs/change-log/2026-07-30-a1b-admin-rides-coverage.md` and
         `docs/change-log/2026-08-01-a1b-admin-rides-coverage-continued.md`.
       - **Net effect:** both sessions' test files are kept (some endpoint
         overlap between them is redundant but harmless — extra CI time, no
         correctness risk); no application code was changed by either; no
         new bugs found by either. `routes/admin/rides.py` is confirmed
         clear of the 70% admin-routes target by two different measurement
         methods (single-file 70%, full-suite 80%) using two independently
         written test suites — about as solid a confirmation as this
         backlog's convention produces. Neither session independently
         re-confirmed a **full-suite** number *after* both sets of tests
         landed together; if that number matters later, it's a cheap
         re-run, not a re-investigation.
     - `routes/admin/support.py` (disputes, support-ticket CRUD, flags,
       complaints) — was ~39%, now 97% (267 stmts, 8 missed: two narrow
       DB-exception logging branches at 220/228, the `updated_at`-set
       branches at 374/376/451, and line 177/551 filter edges). 40 new
       tests in `backend/tests/test_admin_support_routes.py`, following the
       `get_admin_user` dependency-override pattern from
       `test_support_tickets_service_area_routes.py`. Bug found, not fixed
       (test-only scope): `admin_get_dispute_stats` does
       `Decimal(str(d.get("refund_amount") or 0))` inside a bare
       `except (TypeError, ValueError)` — a non-numeric `refund_amount`
       string raises `decimal.InvalidOperation`, which is not caught, so
       the stats endpoint 500s instead of tolerating the bad row. See
       `docs/change-log/2026-07-29-a1b-admin-support-coverage.md`.
     - `routes/admin/support_tickets.py` (Zoho Desk proxy: config, sync,
       dashboard, trends, ticket list/search/reply/comment/patch/tags) —
       was ~43%, now 91% (357 stmts, 31 missed — mostly `ImportError`
       fallback branches for the dual-import pattern, and a few narrow
       Zoho-error edges not exercised: e.g. `update_config`'s no-op
       `data_center` normalization skip, `/tickets/{id}/threads` error
       paths already covered by other error-path tests but not every
       endpoint's Zoho-error branch). 35 new tests in
       `backend/tests/test_admin_support_tickets_routes.py` (service-area
       and AI-suggest routes already had dedicated coverage in
       `test_support_tickets_service_area_routes.py` /
       `test_support_tickets_ai_suggest.py` and were intentionally not
       duplicated).
     - `promotions.py`: 48% → 89% (combined with pre-existing
       `test_admin_promo_stats.py`) via new `tests/test_admin_promotions_crud.py`
       — prioritized the money-adjacent create/update/delete paths (discount
       value validation, code uppercasing, optional-field insert/update
       fallback, audit logging) over read-only stats. See
       `docs/change-log/2026-07-29-a1b-admin-promotions-faqs-venues-coverage.md`.
     - `faqs.py`: 42% → 97% via new `tests/test_admin_faqs_crud.py` — FAQ
       CRUD, embedding-invalidation-on-edit branch, and the three
       notification-broadcast audiences (all/riders/drivers).
     - `venues.py`: 43% → 100% via new `tests/test_admin_venues_crud.py` —
       venue CRUD, 404-not-found and 503-db-error branches, audit logging.
     - `backend/routes/admin/wallet.py` — **99%** (was low, exact baseline
       not separately tracked). Added `tests/test_admin_wallet_endpoints.py`
       (rider-wallet admin read/adjust endpoints).
     - `backend/routes/admin/rider_import.py` — **89%**. Added
       `tests/test_admin_rider_import.py`.
     - `backend/routes/admin/users.py` — **74%**. Added
       `tests/test_admin_users_management.py`.
       All three: no application code changed, no bugs found. Full suite
       not re-run locally this pass (relying on this PR's CI as the
       regression gate — see
       `docs/change-log/2026-07-30-a1b-admin-wallet-users-coverage.md`).
     - `backend/routes/admin/maintenance.py` — **99%**. Added
       `tests/test_admin_maintenance_coverage.py`.
     - `backend/routes/admin/vehicle_fleet.py` — **94%**. Added
       `tests/test_admin_vehicle_fleet_coverage.py`.
     - `backend/routes/admin/staff.py` — **89%**. Added
       `tests/test_admin_staff_coverage.py` (internal-staff account CRUD:
       password validation, role presets, super_admin demotion guard,
       session revoke on deactivation, credential stripping).
       All three: no application code changed, no bugs found. Full suite
       not re-run locally this pass (relying on this PR's CI as the
       regression gate — see
       `docs/change-log/2026-07-30-a1b-admin-maintenance-fleet-staff-coverage.md`).
     - `backend/routes/admin/subscriptions.py` — 68.42% → **98%**. Added
       `tests/test_admin_subscriptions_coverage.py` (32 tests: Spinr Pass
       plan CRUD, subscription-stats aggregation, subscription-payments
       pagination/date-filter/legacy-tax-row branches, tax-config update,
       offer-analytics pagination/truncation/date-parsing, invoice
       download/resend 404/429/502 branches). No application code changed,
       no bugs found. See
       `docs/change-log/2026-08-01-a1b-admin-subscriptions-coverage.md`.
     - `backend/routes/admin/service_areas.py` — 65.80% → **91%**. Added
       `tests/test_admin_service_areas_coverage.py` (32 tests): service-area
       create/update/delete (airport bbox + subregion guards, surge-above-cap
       justification gate, `subscription_required`↔`spinr_pass_enabled`
       coercion, vehicle-pricing auto-seed), surge-pricing activate/deactivate,
       surge-status 503, area-fees CRUD, area-tax get/update, vehicle-pricing
       get. No application code changed. Found (not fixed, flagged in PR):
       the handler's manual `surge_multiplier` range check is dead code —
       `ServiceAreaUpdateRequest`'s Pydantic `Field(ge=1.0, le=10.0)` already
       matches `_SURGE_MAX` exactly, so out-of-range values 422 before the
       handler's own 400 branch is ever reached. Full suite re-run locally
       this pass — see
       `docs/change-log/2026-08-01-a1b-admin-service-areas-coverage.md`.
     - `routes/admin/monitoring.py`: 54% → 96% (live-map driver/ride
       fetchers, Redis health/connectivity/flush-prefix, WebSocket health,
       infrastructure snapshot). See
       `docs/change-log/2026-07-29-a1b-admin-monitoring-messaging-legal-coverage.md`.
     - `routes/admin/messaging.py`: 60% → 97% (recipient resolution +
       service-area filter, per-channel senders, `_fan_out` stats
       write-back including the failure-to-persist path, audience-preview,
       suppressions). Same change-log entry.
     - `routes/admin/legal_documents.py`: 47% → 100% (upsert version-bump
       semantics — PIPEDA consent-version tracking). Same change-log entry.
- **Track 2 (breadth) spun off:** see **A1c** below — not part of this
  item's acceptance. Note: one Track 2 file (`repositories/wallet_repo.py`)
  was already picked up under this item on 2026-08-01, before the Track 2
  split — its progress is preserved under A1c, not lost.
- **Also explicitly out of scope for this item (unchanged):** frontend test
  coverage (rider-app/driver-app/admin-dashboard — React Native / Next.js,
  not measured or covered by anything in A1/A1b) and a correctness audit of
  fare/pricing *values* (e.g. whether Economy vs. XL vehicle-type pricing
  in `fare_configs` is intentional — that data lives in the live DB via the
  admin dashboard's Service Areas → Vehicle Pricing editor, not in this
  repo, and needs a live DB read to answer, not a coverage pass). Both are
  real, separate asks the user raised in the same session as A1b's
  scoping — track them as their own items if/when the user wants them
  picked up.
- **Acceptance:** ✅ met for Track 1 — all four priority groups (corporate
  billing, safety/SOS, auth/RLS, admin routes) measured and closed at or
  above their 80%/90% targets, per the file-by-file breakdown above. Track 2
  acceptance was never defined and now lives under A1c, not here.

### A1c. Backend test-coverage floor — Track 2 (breadth, lower priority, in progress)
- [x] **Status:** CLOSED (2026-08-10) — spun off from A1b (2026-08-01) when
  A1b's Track 1 work closed out. All three sub-tiers itemized below are
  done (Sub-tier A closed 2026-08-01/02, Sub-tier B closed 2026-08-02 in a
  full 26-file sweep, Sub-tier C closed 2026-08-03 across all 39 files in
  the 60-80% band). Verified against the actual latest `main` CI run
  ([job `93335534234`](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/31348756297/job/93335534234),
  commit `64a720e`, 2026-08-10): full-suite backend coverage is
  **46448 stmts, 4531 missed → 90%** (up from the 78.5% aggregate recorded
  when this item was first scoped), 10592 passed, 6 skipped, 1 xfailed. Also
  spot-checked the 19 brand-new backend files added since the 2026-08-03
  snapshot (Stripe payouts/Connect ledger sync, corporate Stripe
  identity-drift repair, email-branding retrofit, admin Sentry viewer) —
  every one already shipped with a dedicated test file in its own PR, no new
  gap found. Closing rather than leaving open with no acceptance criteria:
  the item's own **Acceptance** note below explicitly rejected "cover
  everything to 80%" as a goal, and there is no unscoped remainder left to
  define one against. If a future session finds a real file below the CI
  floor, re-open as a new dated entry rather than reusing this one.
  **Not part of this closure** (found during the same check, filed
  separately): the 1 test failing on this same `main` run
  (`test_snap_to_road_returns_none_without_any_provider_configured`, fixed
  same day, see git history) and the Codecov tokenless-upload gap (**C12**,
  new).
- **Why:** same logic as A1/A1b (higher-risk code deserves a higher bar),
  but for everything *outside* the money/safety/compliance-adjacent set
  Track 1 already covers — utils/services with no explicit coverage target,
  currently sitting at or below the global 60% CI floor. Lower real-world
  consequence than Track 1's scope, hence lower priority — not launch-gating
  on its own.
- **Files:**
  - `backend/repositories/wallet_repo.py` (444 lines — wallet & Stripe
    repository: atomic wallet RPCs, promo application, fare-split, Stripe
    event helpers; extracted from `db_supabase.py` per Phase 4 god-object
    decomposition) — **40% → 99%** (2026-08-01, both numbers measured via
    the full `pytest tests/ -q --cov=repositories.wallet_repo`, not a
    keyword-filtered subset — 169 stmts, 102→2 missed). Had no dedicated
    test file; only indirect coverage as a side effect of route-level tests
    (`test_wallet.py`, `test_p2_promo_wallet_loyalty.py`,
    `test_p3_wallet_concurrency.py`, `test_p3_promo_concurrency.py`,
    `test_webhooks_main.py`, etc.) and `test_wallet_apply_delta_contract.py`
    (SQL-migration-text assertions only, no Python execution). Treated as
    money-adjacent per CLAUDE.md's Critical Conventions (wallet deltas,
    Decimal handling, Stripe idempotency), so targeted the same ≥80%+ bar
    as Track 1. Added `backend/tests/test_wallet_repo.py` (67 tests): happy
    path + DB-error-propagation for all 12 public functions
    (`wallet_increment_balance`, `wallet_apply_credit`, `wallet_apply_delta`
    incl. floor/clamp_to_floor branches, `wallet_pay_for_ride` incl. all 4
    typed `ValueError` translations, `wallet_transfer` incl. list-vs-dict
    RPC-row shape, `increment_promo_uses`/`claim_promo_user_slot`
    (parametrized True/1/list/False/None/empty branches),
    `release_promo_user_slot`, `fare_split_pay_share`, `claim_stripe_event`
    (all 3 duplicate-detection message-pattern branches + the stuck-vs-
    processed distinction), `mark_stripe_event_processed`,
    `unclaim_stripe_event`). Remaining 2 uncovered lines are the dual-import
    `except ImportError` fallback (structurally untestable in a single
    process, per this repo's own documented convention). Test-only PR — no
    application code changed. **Bug found, not fixed (test-only scope):**
    `mark_stripe_event_processed` swallows a DB/payment error via
    `logger.warning(...)` + continue and always returns `None` regardless of
    success or failure, matching the exact pattern CLAUDE.md's "Do not
    silently swallow errors" section forbids for payment errors — the
    caller cannot detect the stamp failed short of grepping logs. Contrast
    with the sibling `unclaim_stripe_event`, which signals the same class of
    failure to its caller via a boolean return (documented as "the caller
    must escalate on False") — not a swallow. `mark_stripe_event_processed`'s
    docstring argues the trade-off is bounded (Stripe already got its 2xx;
    a reconciliation job distinguishes stuck-vs-processed rows via
    `stripe_events.processed_at`), so it is likely not a live financial-
    correctness bug today, but it is a real deviation from the documented
    convention and worth a follow-up decision (fix vs. formally accept).
    **Second finding, also not fixed (pre-existing test-suite reliability
    bug, not application code):** running the new test file as part of the
    full suite (not in isolation) initially produced 53 failures, all
    `ServiceUnavailableException("database")` from `repositories/_base.py`'s
    deadline-exhausted guard — root-caused to `tests/test_utils_extended.py`'s
    `TestDeadline*` class, several of whose tests call
    `utils/deadline.py:set_request_deadline(...)` directly and never reset
    the `contextvars.ContextVar` afterward (no `reset_token` cleanup), so a
    permanently-past deadline leaks into every later test in the same pytest
    process that calls `run_sync` — order-dependent, same failure class as
    the already-tracked A8 (leaked-coroutine test pollution). Worked around
    locally with an autouse fixture in `test_wallet_repo.py` that resets the
    contextvar to `None` before each test (see the file's `_clear_request_deadline`
    fixture for the full writeup); did not touch `test_utils_extended.py`
    itself since that's a different file/feature, out of scope for a
    wallet_repo-coverage PR. A future session should either fix
    `test_utils_extended.py`'s cleanup or add a session-scoped autouse
    contextvar reset to `conftest.py` so no other file has to defend against
    this individually.
    See `docs/change-log/2026-08-01-a1b-wallet-repo-coverage.md`.
  - **Full-repo scoping pass (2026-08-01)** — measured every non-test,
    non-migration backend file via a single full-suite
    `pytest tests/ -q --cov=. --cov-report=json` run (315 files, 78.5%
    aggregate). 88 files sit below 80%; excluding `wallet_repo.py` (above,
    already closed) and everything Track 1 already owns
    (`routes/admin/`, corporate, safety, auth). Also surfaced: one
    pre-existing flaky test, `test_e2e_ride_lifecycle.py::
    TestRideLifecycleConcurrency::test_two_drivers_accepting_same_ride_one_wins`
    (passes standalone, intermittently fails under full-suite load —
    timing-sensitive, not investigated further, not blocking).
    - **Sub-tier A — money/ride/dispatch-adjacent, arguably deserves
      Track-1-grade priority despite living in "Track 2"** (recommend
      picking up first):
      - `repositories/ride_repo.py` — **CLOSED, covered twice by
        independent concurrent sessions on 2026-08-01/02** (both merged;
        no file-path collision since each added a differently-named test
        file, so both landed intact — leaving genuinely redundant but
        harmless double coverage, not a conflict to pick a winner from):
        - **Session A (2026-08-01), 55% → 96%** (383 stmts, 173→17
          missed; both numbers via the full
          `pytest tests/ -q --cov=repositories.ride_repo`, not a
          keyword-filtered subset). Added `backend/tests/test_ride_repo.py`
          (56 tests, mirrors `test_ride_route_contract.py`'s existing
          local fake-client convention rather than the generic
          `mock_supabase_client` fixture, since several functions need
          per-table differentiated responses within a single
          `asyncio.gather` fan-out): CRUD (`insert_ride`/`update_ride`),
          admin enrichment (`get_ride_details_enriched`'s multi-table
          fan-out), payment-claim race guard
          (`claim_ride_payment_processing`, both branches of the
          optimistic lock + DB-error-not-swallowed), flags/auto-ban
          (`create_flag`, below- and at-threshold for both rider and
          driver targets), complaints, lost-and-found, location trail,
          `get_user_status`/`get_flags_for_target`/`get_live_ride_data`.
          Deliberately left `_safe_route_segments`/`_project_route_detail`
          alone (already `test_ride_route_contract.py`'s dedicated
          domain) — accounts for most of the remaining 17 missed lines.
          Test-only, no application code changed, no bugs found.
          Full-suite regression: 6830 → 6886 passed, 0 failed. See
          `docs/change-log/2026-08-01-a1c-ride-repo-coverage.md`.
        - **Session B (2026-08-02), 54.83% → 84.1%** (383 stmts, 61
          missed). Added `backend/tests/test_ride_repo_coverage.py` (74
          tests) — broader scope than Session A: also covers
          `_safe_route_segments`'s allowlist-projection branches
          (malformed/out-of-range/unknown-provider coordinate rejection)
          and `_project_route_detail`'s v1-legacy vs. v2-segmented-geometry
          branches (completion-point validation, snapshot-URL
          signing-failure graceful-degrade, revision-mismatch skip) —
          i.e. it duplicates part of `test_ride_route_contract.py`'s
          domain rather than deferring to it, the opposite scoping choice
          from Session A. Also test-only, no application code changed, no
          bugs found. Full suite: 6904 passed, 8 skipped, 1 xfailed, 0
          failed. See `docs/change-log/2026-08-02-a1b-ride-repo-coverage.md`.
        - **Net effect:** the file now has three test files exercising
          overlapping surface (`test_ride_route_contract.py`,
          `test_ride_repo.py`, `test_ride_repo_coverage.py`) — some
          redundant test-writing effort from the concurrent-session
          collision, but net positive: real coverage is whichever of the
          two runs highest per-line (96% and 84.1% don't fully overlap in
          which lines they hit), no regression either way, and no
          production code touched by either. Not worth unwinding
          post-merge — flagging here so a future consolidation pass (if
          any) knows both exist and why, rather than treating one as
          silently superseding the other.
      - `routes/websocket.py` — **done, 80.3%** (569 stmts, 112 missing,
        up from 50.44%) — WS auth + dispatch fan-out. Nine existing WS test
        files covered handshake/auth edge cases, per-user rate limiting,
        live-location durability/revocation guards, health, and fan-out
        metrics, but none drove the main receive loop end-to-end. Added
        `tests/test_websocket_coverage.py` (35 tests) via
        `TestClient.websocket_connect`: message-size guard, malformed JSON,
        `driver_location`/`location_batch` persistence + rider/admin
        fan-out (integrity check pass/fail, throttled DB write, breadcrumb
        buffering with session-revoked skip, ETA cache hit/miss), batch
        rate-limit and session-revoked-ack paths,
        `ride_status_update`/`chat_message`/`get_nearby_drivers`/admin
        snapshot handling, the disconnect/exception cleanup tail, and
        `heartbeat_task`/`_handle_driver_ws_disconnect` edge branches
        (stale-pong close, token-version/Firebase-watermark revocation,
        newer-socket-already-reconnected skip, idle-driver-skips-broadcast,
        audit-log-failure-still-broadcasts). No application code changed.
        One pre-existing (not fixed) behavior gap noted: `location_batch`
        with an empty `points: []` list never sends a `location_batch_ack`
        — the ack call lives inside a guard that's falsy for an empty
        (but well-typed) list. Full suite: `6865 passed, 8 skipped,
        1 xfailed, 0 failed`. See
        `docs/change-log/2026-08-02-a1b-websocket-coverage.md`.
      - `routes/drivers/subscriptions.py` — **CLOSED, 61% → 99%** (575
        stmts, 227→6 missing), across two same-day sessions. Spinr Pass,
        money-adjacent (NOT the same file as `routes/admin/subscriptions.py`,
        already closed under Track 1 — this is the driver-facing one).
        - **Session A (2026-08-02), 61% → 69%** (measured via
          `pytest tests/ -q -k subscription --cov=routes.drivers.subscriptions`,
          575 stmts, 181 missing). `test_spinr_pass_subscription.py` already
          covered the checkout/webhook/verify-session/cancel flow end-to-end,
          but `_compute_subscription_tax` and `_record_subscription_payment`
          were only exercised through the no-service-area short-circuit, and
          the driver-facing resend-invoice endpoint had zero coverage (only
          its unrelated admin-console sibling was tested). Added
          `backend/tests/test_driver_subscriptions_tax_ledger_coverage.py`
          (17 tests) covering the tax-rate math (GST/PST/HST, disabled-config,
          missing-config defaults), the ledger's duplicate-vs-real-DB-error
          swallow distinction, and `resend_subscription_invoice`'s 404/502
          guards plus legacy-vs-tax-columns resend paths. No application code
          changed, no bugs found. Flagged the remaining gap as concentrated
          in `_send_subscription_invoice_email`'s own rendering body and
          `check_expiring_subscriptions` (one of the 17 background startup
          loops) — left for its own dedicated pass. See
          `docs/change-log/2026-08-02-a1c-driver-subscriptions-tax-ledger-coverage.md`.
        - **Session B (2026-08-02, this pass), 69% → 99%** (measured via
          `pytest tests/test_subscriptions_coverage.py
          tests/test_spinr_pass_subscription.py tests/test_webhooks_main.py
          tests/test_admin_subscriptions_coverage.py
          tests/test_driver_subscriptions_tax_ledger_coverage.py
          --cov=routes.drivers.subscriptions --cov-report=term-missing` —
          575 stmts, 6 missing). Picked up exactly where Session A left off:
          added `backend/tests/test_subscriptions_coverage.py` (66 tests,
          new file rather than extending `test_spinr_pass_subscription.py`,
          mirroring how `test_ride_repo_coverage.py` was kept separate from
          `test_ride_route_contract.py`) covering
          `_send_subscription_invoice_email` in full (PDF-attachment success
          path, PDF-generation-failure degrade, delivery failure, GST/PST/HST
          row rendering, the `_pct_label` divide-by-zero guard), the
          `check_expiring_subscriptions` background loop end-to-end
          (distributed-lock acquired/not-acquired, the `cancel_pending` retry
          sweep, `get_app_settings` failure defaulting the enforcement gate
          off, the full online-driver enforcement path with every
          best-effort side-effect — presence clear, WS disconnect,
          activity-log insert, push, admin broadcast — independently
          swallowing its own failure, the 24h/3-day warning branches
          including push-failure swallow and the lost-atomic-claim-race
          skip), `_activate_subscription`'s degrade branches (driver-lookup
          exception, area-timezone-lookup exception, prior-subscription
          Stripe-cancel-failure → `cancel_pending`), plus smaller gaps in
          `get_subscription_plans`, `get_current_subscription`,
          `_cancel_stripe_subscription`'s `raise_on_error` paths,
          `subscribe_to_plan`'s error/edge branches, `cancel_subscription`'s
          missing-driver/no-active-sub paths, and
          `subscription_checkout_return` (previously zero coverage). Some
          overlap with Session A's tax/ledger/resend tests (both files ran
          together with no collisions — 215 passed) — left as harmless
          redundant coverage rather than deduplicated, same call as the
          `ride_repo.py` multi-session precedent above. No application code
          changed. **No bugs found** (unlike the two prior sibling passes
          today that found the `mark_stripe_event_processed` swallow and the
          `location_batch_ack` gap — this pass's remaining 6 uncovered lines
          are two defensive fallback branches: the dual-import `ImportError`
          fallback for `redis_set_nx` when neither import form resolves, and
          the loop's outermost catch-all exception guard — both judged not
          worth chasing via `sys.modules` monkeypatching). Full suite before
          this session's file existed but after fast-forwarding onto
          Session A's merged commit (which itself added 68 tests across
          the tax-ledger and `redis_client.py` coverage files): 7068
          passed. After adding this session's 66 tests: `7134 passed, 8
          skipped, 1 xfailed, 0 failed` — exactly +66, matching the new
          test count, zero regressions. See
          `docs/change-log/2026-08-02-a1c-subscriptions-coverage.md`.
      - `ride_flow.py`, `ride_cancel.py`, `ride_reads.py` — **CLOSED**
        (2026-08-02, branch `claude/a1c-drivers-ride-flow-batch`): fresh
        baseline measured via the full `pytest tests/ -q
        --cov=routes.drivers.ride_flow --cov=routes.drivers.ride_cancel
        --cov=routes.drivers.ride_reads` matched the documented numbers
        exactly — `ride_flow.py` 66.30%→99% (273 stmts, 92→2 missing);
        `ride_cancel.py` 51.75%→100% (144 stmts, 69→0 missing);
        `ride_reads.py` 58.95%→98% (190 stmts, 78→3 missing). Added
        `backend/tests/test_driver_ride_flow_coverage.py` (95 tests), run
        alongside every pre-existing test file already touching these three
        modules (`test_drivers_extended.py`, `test_ride_accept_flow.py`,
        `test_subscription_enforcement.py`, `test_c2_driver_cancel_atomic.py`,
        `test_active_ride_rider_pii.py`, `test_rides.py`,
        `test_dispatch_metrics.py`, `test_claim_ride.py`,
        `test_fee_wallet_atomic.py`, `test_preauth_release_on_cancel.py`,
        `test_idor_ownership_guards.py`) with no collisions (297 passed).
        Coverage focus: `accept_ride`'s subscription-guard sub-branches
        (child-area-inherits-from-parent, expired-sub-row auto-marked
        expired, plan service-area/vehicle-type allowlist mismatch incl. the
        parent-area-coverage exception, the DB-error-fails-closed 503), the
        searching/broadcast claim path (no-pending-offer 403, offer-lookup
        exception, not-assigned-and-not-searching 400), the claim-lost
        re-check (same-driver-idempotent-success vs. taken-by-another-409),
        the batch-dispatch winner/loser resolution (incl. a loser's WS push
        failing non-fatally), the ride_metrics pickup-leg write
        (success + non-fatal-failure), guest-booking notifications;
        `decline_ride`'s 404/409/403 guards, the audit-log and Redis-cooldown
        non-fatal failure branches, and the early-redispatch decision
        (no-offers-remain / offers-remain / rematch-check-exception);
        `arrive_at_pickup`'s 200m geofence rejection and the
        nav-point-vs-raw-pin nearest-of-either check; `verify_pickup_otp`
        (previously zero standalone coverage — otp-mismatch 400, guard-none
        409, success + rider notify); `start_ride`'s production 410 block
        and ride-not-found 404; `cancel_ride`'s (driver-side) JSON-body vs.
        query-param reason precedence, the PGRST204 attribution-write
        fallback, the pre-auth-release success/exception/write-failure
        branches, and the scheduled-ride `is_scheduled` broadcast flag;
        `mark_rider_noshow`'s full success path (previously only the
        409-claim-lost branch had coverage) — wallet debit + driver payout,
        partial-wallet-collection logging, card-vs-wallet payment-method
        branching, area-level wait-seconds override, the naive-datetime
        `driver_arrived_at` normalization, and the extended-fee-columns
        PGRST204 fallback; `get_active_ride`'s batch-offer fallback (found
        / not-found / stale-ride-no-longer-searching / lookup-exception),
        the rider/vehicle-type lookup exception paths, the
        incentives+quest-hint enrichment (incl. the service-area `or_`
        clause and vehicle-type filtering) with each lookup's independent
        non-fatal exception, and the service-area-polygon fetch; and
        `get_ride_history`'s incentive-claims enrichment,
        `driver_earnings_snapshot`-present vs. legacy-computed branches, the
        `fare_breakdown_snapshot` tax fallback, the period=None/"all"/"week"/
        "month" branches of `history_start_for_period`, and the explicit
        `status="scheduled"` `history_date_field` branch. Test-only, no
        application code changed. **Bug found, not fixed (test-only scope,
        per instructions):** `get_active_ride`'s except-handler at the
        vehicle-type lookup (and similarly the earlier rider lookup) logs
        `ride['vehicle_type_id']` via direct dict indexing instead of
        `ride.get(...)` — harmless in production (a Supabase `rides` row
        always carries the column, value possibly `None`) but a
        theoretical second `KeyError` inside the except-handler itself if a
        ride dict ever legitimately lacked the key entirely; not fixed per
        the test-only-pass instruction, and not realistically reachable
        given the DB schema, so not escalated further. Remaining uncovered
        lines: `ride_flow.py` 537-538 and `ride_reads.py` 347-348 are both
        the dual-import `except ImportError` fallback for a same-process
        re-import (`match_driver_to_ride` / `_redact_driver_location_fields`)
        — structurally unreachable in a single test process, same
        documented pattern as the `redis_set_nx` fallback in the
        subscriptions-coverage pass above; `ride_reads.py` line 279 is
        `history_date_field`'s trailing `return "created_at"` fallback,
        unreachable because both of its call sites already guard
        `status_value` to `completed`/`cancelled`/`scheduled` before
        calling it. See
        `docs/change-log/2026-08-02-a1c-drivers-ride-flow-batch-coverage.md`.
      - `payouts.py`, `earnings.py`, `referrals.py` — **CLOSED** (2026-08-02,
        branch `claude/a1c-drivers-payouts-batch`): `payouts.py` 69.47%→98.44%
        (321 stmts, 98→5 missing); `earnings.py` 37.25%→98.69% (306 stmts,
        192→4 missing); `referrals.py` 38.82%→98.82% (170 stmts, 104→2
        missing). Added `backend/tests/test_payouts_coverage.py` (34 tests),
        `backend/tests/test_earnings_coverage.py` (36 tests),
        `backend/tests/test_referrals_coverage.py` (20 tests) — 90 tests
        total, run alongside every pre-existing test file already touching
        these three modules (`test_p2_payout_t4a.py`, `test_instant_payout.py`,
        `test_payout_toctou.py`, `test_drivers_extended.py`,
        `test_referral_terms.py`, the `test_referral_payout_*.py` family,
        `test_referral_failed_claims_admin.py`,
        `test_referral_recredit_failed_claim.py`) with no collisions (287
        passed). Coverage focus: `payouts.py`'s WITH-Stripe branch of
        `request_payout` (untested before — the existing pin only exercised
        the no-Stripe-key "pending" fallback), the reserve-insert
        conflict/error paths, the terminal-write-failure reversal branches
        (success, failure→stranded, and the no-Stripe skip-reversal case)
        for both standard and instant payouts, `_ensure_stripe_account`'s
        new-account-creation + persist-failure branches, and the previously
        wholly-untested `save_bank_account`/`delete_bank_account`;
        `earnings.py`'s previously near-zero-coverage
        `get_driver_bonuses`/`get_driver_trip_earnings`/
        `get_driver_weekly_earnings`/`get_driver_monthly_earnings`/
        `get_driver_earnings_comparison`/`get_driver_earnings_forecast`, plus
        the service-area-timezone, incentive-claims-lookup-failure, and
        fare-breakdown-snapshot tax-fallback branches of `get_driver_earnings`;
        `referrals.py`'s previously wholly-untested `apply_referral_code`
        (all three code-resolution paths incl. the regex-fallback swallow)
        and `get_driver_leaderboard` (RPC happy path, RPC-failure→daily-stats
        fallback, and three independent degrade-to-empty/placeholder
        branches). Test-only, no application code changed. **Bug found, not
        fixed (test-only scope, per instructions):** none in this batch —
        every exception branch exercised behaves as documented (loud
        logging, clean HTTP status, no silent swallow of a money-moving
        error). Full suite: baseline before this session's files existed
        7263 passed, 8 skipped, 1 xfailed; after adding this session's 90
        tests (run in isolation on this branch, not mixed with concurrent
        sibling sessions' own untracked test files in the shared working
        directory): 90/90 passed, and combined with every referral/payout
        test file above: 287/287 passed, zero regressions. See
        `docs/change-log/2026-08-02-a1c-drivers-payouts-batch-coverage.md`.
      - `_shared.py`, `status.py`, `profile.py` — **CLOSED** (2026-08-02,
        branch `claude/a1c-drivers-shared-batch`): `_shared.py` 51%→96%
        (228 stmts, 111→8 missing — the 8 remaining lines are
        `_require_ride_in_state`, deliberately left for the sibling
        `ride_flow.py`/`ride_cancel.py`/`ride_reads.py` session since that's
        where it's actually called from); `status.py` 48%→100% (31 stmts,
        16→0 missing — the whole gap was the untested `GET
        /drivers/{driver_id}` endpoint; `update_driver_status`'s
        online/available invariant was already covered by
        `test_go_online_availability.py`/`test_p1_driver_offline.py`);
        `profile.py` 68%→100% (136 stmts, 44→0 missing). Added
        `backend/tests/test_drivers_shared_status_profile_coverage.py` (59
        tests) covering the PII-vault RPC functions
        (`_vault_encrypt`/`_vault_decrypt`, previously 0% direct coverage —
        every route test mocks around them), the ride-route-snapshot
        pipeline's storage/upload/write-back tail
        (`_generate_and_store_ride_snapshot` lines 363–493, previously
        unreached because every existing test stubs the OSM renderer to
        return `None`), `_snap_pickup_leg_async`/`_validate_ride_route`
        (zero prior coverage), `status.py`'s `get_driver` (admin/self/
        rider-with-active-ride/rider-without-active-ride/404/DB-exception-
        degrades-to-403 branches — the rider-facing safe projection was
        also asserted to strip PII fields), and `profile.py`'s
        `get_driver_config` exception fallback, `update_my_driver`'s
        auto-create-driver-row and vehicle-change → `needs_review`
        re-review branches (asserting
        `record_period_transition(driver_id, 0)` fires per the Period 0-3
        insurance state machine), `get_demand_heatmap`, and the
        destination-mode 404 branches. Test-only, no application code
        changed. **Bug found, not fixed (test-only scope):** the v2
        route-snapshot reference-write `except` block in
        `_generate_and_store_ride_snapshot` both logs and re-raises, but
        its only caller is the function's own outermost catch-all, so the
        `raise` is dead code (double-logs, never actually propagates) —
        harmless (no data loss, object already uploaded) but noted rather
        than silently worked around. Full suite: run together with every
        other test file already touching these modules — 327 passed, no
        collisions. See
        `docs/change-log/2026-08-02-a1c-drivers-shared-batch-coverage.md`.
      - `utils/redis_client.py` — **done, 100%** (2026-08-02, 220/220 stmts;
        was 55% full-suite side-effect coverage, all of it via the
        in-process-fallback path). Presence/rate-limit backbone. Every prior
        test touched this module only as a side effect of a higher-level
        caller through the `mock_redis` fixture (in-process dict only) — the
        real-Redis-connected branch of every public function had zero direct
        coverage. Added `backend/tests/test_redis_client_coverage.py` (51
        tests) covering both modes for every function, `_get_redis()`'s
        URL-change reconnect + connect-failure-falls-back branches, the
        configured-but-erroring-must-raise-loudly contract (per CLAUDE.md's
        Redis transparency note) for every primitive except the
        documented-intentional exception (`redis_set_nx`'s belt-and-braces
        local-lock fallback), `get_redis_stats`/`count_keys_by_prefix` in
        both modes, and `_humanize_bytes`'s unit boundaries. No application
        code changed. **Bug found, not fixed (test-only scope):**
        `_humanize_bytes` mislabels any petabyte+-scale value as bytes
        instead of the correct unit — `unit` is only reassigned inside a
        branch that never fires once `size` starts at T-scale, so the loop
        silently divides through every remaining unit without ever updating
        it. Not a live risk today (no realistic Redis holds petabytes; feeds
        only an admin dashboard gauge), pinned by a test asserting the
        actual buggy output rather than worked around. See
        `docs/change-log/2026-08-02-a1c-redis-client-coverage.md`.
    - **Sub-tier B — below 60%, genuinely lower-risk breadth** (utils/services,
      admin-adjacent tooling, third-party integrations) — **CLOSED,
      full sweep, 2026-08-02**: all 26 files below now have a dedicated
      test file, mid-80s%–100% coverage each except `core/lifespan.py`
      (64.3%, see its own note). No application code changed anywhere in
      this sweep. Full details, the concurrent-session-drift handling
      (two source files this sweep tests were rewritten mid-session by
      other sessions — `corporate_repo.py`'s search escaping and
      `demand_forecast.py`'s `confidence`→`data_basis` rename — both
      re-verified and fixed to match, not silently patched over), and
      every "found, not fixed" flag are in
      `docs/change-log/2026-08-02-a1c-subtier-b-sweep-coverage.md`.
      - `routes/main.py`: 0% → **84.6%** (`tests/test_routes_main_coverage.py`).
        **Correction (2026-08-02, found by an independent concurrent
        session's identical-scope coverage pass, PR #3324, closed
        unmerged as redundant with this sweep — but the finding itself is
        real and worth keeping):** the note above was wrong. This file's
        `api_router` (including its `/health` route) is **never mounted**
        — grepped `server.py` for any `routes.main`/`api_router`
        reference and found none. The actual `/health` that Railway's
        readiness probe, `fly.toml`'s `[[http_service.checks]]`, and the
        A2 post-deploy smoke test depend on is a separate, independent
        implementation defined directly in `server.py` (`@app.get
        ("/health")`, with its own DB-ping cache and loop-liveness check)
        — this file's `/health` is dead code that happens to share a
        route path with the real one, never wired in. Not deleted here
        (out of scope for a coverage pass); worth a repo-owner call on
        whether to remove `routes/main.py` entirely or wire it in and
        delete the duplicate in `server.py` instead.
      - `utils/t4a_pdf.py`: 4.40% → **97.8%** (`tests/test_t4a_pdf_coverage.py`).
      - `utils/subscription_invoice_pdf.py`: 7.97% → **99.3%**
        (`tests/test_subscription_invoice_pdf_coverage.py`).
      - `services/zoho_desk_db.py`: 11.76% → **99.2%**
        (`tests/test_zoho_desk_db_coverage.py`). **Found by the same
        independent PR #3324/#3325 pass, not yet fixed:**
        `list_mirror`'s search `.or_()` builder hand-rolls comma/LIKE-
        wildcard handling instead of routing through
        `repositories/_base.py`'s shared `_escape_like`/
        `_postgrest_or_value` helpers per this file's documented
        convention (see root `CLAUDE.md`'s "Query filters" section) — an
        over-matching-only quirk on an internal admin search box, not a
        security issue, but a real deviation from the required pattern.
      - `utils/demand_forecast.py`: 18.52% → **98.8%**
        (`tests/test_demand_forecast_coverage.py`). Source renamed its
        `confidence` field to `data_basis` mid-sweep (concurrent PR #3289,
        Admin #3) — tests updated to match, not left pinning the stale name.
      - `utils/zoho_desk_sync.py`: 22.33% → **95.2%**
        (`tests/test_zoho_desk_sync_coverage.py`).
      - `utils/analytics.py`: 22.70% → **98.2%** (`tests/test_analytics_coverage.py`).
      - `routes/lost_and_found.py`: 25.85% → **89.1%**
        (`tests/test_lost_and_found_route_coverage.py`).
      - `services/stripe_kyc_sync.py`: 30.70% → **97.4%**
        (`tests/test_stripe_kyc_sync_coverage.py`).
      - `utils/marketing_push.py`: 33.33% → **100%**
        (`tests/test_marketing_push_coverage.py`).
      - `utils/ws_pubsub.py`: 38.46% → **100%** (`tests/test_ws_pubsub_coverage.py`).
      - `services/data_transfer/bundle_document_uploader.py`: 38.75% →
        **100%** (`tests/test_bundle_document_uploader_coverage.py`). This
        sweep's tests originally flagged a hardcoded-declared-MIME-type
        bug that made `replay_documents` silently skip every document —
        that bug was independently fixed by a concurrent session before
        this branch's tests were committed; the test was updated to pin
        the now-correct behavior rather than the stale bug.
      - `routes/users.py`: 39.86% → **93.6%** (`tests/test_routes_users_coverage.py`).
        **Found, not fixed:** `DELETE /users/profile` is documented as
        "permanently delete" but only soft-deletes — near-duplicate of the
        explicitly-soft-delete `DELETE /users/account`. Worth confirming
        which the rider app's delete flow actually calls.
      - `routes/support.py`: 42.22% → **88.9%** (`tests/test_routes_support_coverage.py`).
        **Found, not fixed:** `support_chat`'s single broad `except Exception`
        converts any Gemini SDK failure into a 200 OK fallback reply with
        only a `logging.warning` — no Sentry, no error-level log — masking
        a real AI-outage as an ordinary chat answer.
      - `repositories/corporate_repo.py`: 42.29% → **99.4%**
        (`tests/test_corporate_repo_coverage.py`). Source rewritten
        mid-sweep by concurrent PR #3289 (search now escapes reserved
        PostgREST characters via the shared `_apply_filters`/
        `_build_or_clause_term` path instead of stripping them) — 2 tests
        updated to match the new (correct) behavior.
      - `utils/push_retry.py`: 45.30% → **98.3%** (`tests/test_push_retry_coverage.py`).
        **Found, not fixed:** `_process_row` bumps the claim's
        `attempts`/`next_attempt_at` before delivery; if delivery succeeds
        but the following `sent_at` UPDATE itself raises, the row stays
        due and can be re-delivered (at-least-once, pre-existing).
      - `routes/maps_proxy.py`: 51.35% → **83.8%** (`tests/test_maps_proxy_coverage.py`,
        deliberately non-overlapping with the pre-existing `test_maps_proxy.py`).
      - `utils/route_validation.py`: 53.33% → **100%**
        (`tests/test_route_validation_coverage.py`).
      - `utils/scheduled_rides.py`: 55.40% → **93.5%**
        (`tests/test_scheduled_rides_coverage.py`). **Found, not fixed:**
        `_dispatch_scheduled_ride`'s outer `except Exception` on the claim
        call gives the caller no distinct signal for "transient DB error"
        vs. "legitimately already claimed."
      - `utils/suspension_reactivation.py`: 55.93% → **94.9%**
        (`tests/test_suspension_reactivation_coverage.py`).
      - `utils/route_snapshot.py`: 57.08% → **99.1%**
        (`tests/test_route_snapshot_coverage.py`).
      - `utils/stuck_ride_sweeper.py`: 57.32% → **90.2%**
        (`tests/test_stuck_ride_sweeper_coverage.py`).
      - `core/security.py`: 57.89% → **100%** (`tests/test_core_security_coverage.py`).
      - `core/lifespan.py`: 58.52% → **64.3%** (`tests/test_core_lifespan_coverage.py`).
        Deliberately not chased higher: the function individually
        try/excepts 17 separate background-loop imports+spawns, and
        covering each one's import-success/failure branch would need
        mocking all 17 import targets individually. The shared logic that
        matters most is covered — `init_database`, `cleanup_database`, and
        (the regression this sweep most cares about) the `ENV=="test"`
        no-op guard from issue #2981, explicitly locked in by asserting
        the real stdlib `asyncio.create_task` is never invoked for any of
        the 17 loop names under `ENV=test`.
      - `routes/marketing.py`: 58.57% → **94.3%** (`tests/test_routes_marketing_coverage.py`).
      - `utils/document_expiry.py`: 58.71% → **91.6%**
        (`tests/test_document_expiry_coverage.py`). One of the 17
        background loops; regulatory-adjacent (Saskatchewan Transportation
        Act driver-eligibility — expired documents must suspend the driver).
    - **Sub-tier C — 60-80% band, lowest urgency per the original Track 2
      scoping note** — **itemized 2026-08-02** (fresh re-scan; the prior
      "55 files, not itemized" note was correctly flagged as going stale
      and is superseded by this list). Methodology: same full-suite
      `pytest tests/ -q --cov=. --cov-report=json --cov-report=term-missing
      --no-cov-on-fail` command as the original 2026-08-01 pass (8374
      passed, 8 skipped, 1 xfailed, 318 files with statements, 86.32%
      aggregate — aggregate has risen from 78.5% because Sub-tier A/B are
      now closed). Every file below is non-test, non-migration, sits
      strictly in the 60–80% band, and is **not** already closed under
      Sub-tier A or B above and **not** already owned by A1b/Track 1 (all
      of `routes/admin/*`, anything with "corporate" in its domain —
      including `utils/allowance_reset.py`, which reads as a generic
      background loop by filename but is entirely corporate-employee-
      allowance logic — and safety/auth-adjacent files). Two Track-1-owned
      files happen to also sit in the 60-80% band and are excluded here as
      out-of-scope, not because they're fine: `routes/admin/drivers.py`
      (73.44%, already tracked under A1b item 4, deprioritized there) and
      `routes/admin/sgi_forms.py` (70.27%, untouched). **This scan found
      38 files, 5635 statements** — a 39th file, `utils/payment_retry.py`,
      was independently closed by a concurrent session between this scan
      running and this edit landing (see its own entry immediately below,
      kept intact rather than folded away); it is excluded from the
      itemized batches below because it's no longer in the 60-80% band.
      Batched ≤3 files/subtask per CLAUDE.md's working-style convention —
      pick a batch, verify its numbers with a targeted `--cov=<module>`
      run before writing tests (per this backlog's established pattern, a
      keyword-filtered baseline can be stale), and update this list when a
      batch closes rather than leaving it to rot:
      - **Batch 1:** `utils/offer_expiry_reaper.py` (60.61%, 66 stmts),
        `utils/period1_distance_finalizer.py` (64.38%, 73 stmts),
        `utils/driver_claim_reaper.py` (64.71%, 68 stmts) — all three are
        dispatch/offer-cleanup background-loop helpers, similar shape to
        the already-closed `utils/stuck_ride_sweeper.py`.
      - **Batch 2:** `services/zoho_desk_service.py` (65.84%, 202 stmts),
        `routes/promotions.py` (65.85%, 328 stmts — rider-facing promo
        redemption; NOT the same file as the already-closed
        `routes/admin/promotions.py` CRUD), `utils/data_export_purge.py`
        (68.42%, 57 stmts — PIPEDA deletion-retention purge,
        compliance-adjacent).
      - **Batch 3** — **CLOSED 2026-08-02.** `utils/retention_purge.py`
        69.12% → **98%** (136 stmts, 42→3 missing), `utils/orphaned_hold_reconciler.py`
        69.23% → **95%** (91 stmts, 28→5 missing), `utils/driver_online.py`
        69.70% → **100%** (33 stmts, 10→0 missing). Measured via
        `pytest tests/test_driver_online.py tests/test_retention_purge.py
        tests/test_retention_purge_coverage.py tests/test_orphaned_hold_reconciler.py
        tests/test_orphaned_hold_reconciler_coverage.py tests/test_p3_loop_jitter_metrics.py
        tests/test_estimate_intent_projection.py --cov=utils.retention_purge
        --cov=utils.orphaned_hold_reconciler --cov=utils.driver_online
        --cov-report=term-missing --no-cov-on-fail` (120 passed, 0
        collisions). All three are compliance/dispatch-critical despite
        their raw Sub-tier C ranking — PIPEDA retention (`retention_purge.py`),
        Stripe pre-auth-hold cleanup (`orphaned_hold_reconciler.py`), and
        the `is_available ⇒ is_online` invariant helper
        (`driver_online.py`) — treated at Sub-tier-A-style urgency per this
        list's own note under Batch 3 below. Added
        `backend/tests/test_driver_online.py` (50 tests — every
        `intent_online`/`_parse_ts`/`effective_online`/`effective_available`/
        `filter_effective_online` branch, plus an explicit parametrized
        invariant test asserting `effective_available ⇒ effective_online`
        across every intent×presence×active-ride combination),
        `backend/tests/test_retention_purge_coverage.py` (20 tests — every
        error branch in `_delete_expired_route_snapshot_objects`, the
        plain-dict rpc-response alt-parsing paths, the trip-route-geometry
        post-storage refetch's 3 branches, the `skipped_fk` loud-log line,
        `_pod_id`, and both `_tick` branches), and
        `backend/tests/test_orphaned_hold_reconciler_coverage.py` (8 tests
        — `release_open_hold` raising mid-batch, `_pod_id`, and the
        `orphaned_hold_reconciler_loop`'s stagger sleep / lock-skip /
        summary-log / error-metric / CancelledError-propagation branches).
        Test-only, no application code changed. **No bugs found** in the
        two payment/compliance files; remaining uncovered lines in all
        three are the dual-import `ImportError` fallback boilerplate
        (structurally near-impossible to reach in this harness without
        risky `sys.modules` manipulation — same documented pattern as
        prior Sub-tier B/C files). Full log:
        `docs/change-log/2026-08-02-a1c-subtier-c-batch3-coverage.md`.
      - **Batch 4 — CLOSED 2026-08-03.**
        - `services/guest_notification_service.py` — **CLOSED, 70.34% →
          96%** (118 stmts, 35→5 missing; measured via `pytest
          tests/test_guest_sms.py
          tests/test_guest_notification_service_coverage.py
          --cov=services.guest_notification_service
          --cov-report=term-missing`, 22 passed, 0 collisions). The
          pre-existing `test_guest_sms.py` (5 tests) pinned the PII-safe-
          logging contract and the two most common paths; this pass closed
          `_send_guest_sms`'s crash-not-just-failure path, all three
          `_guest_recipient` guard clauses, `_company_name`'s no-id/DB-
          exception fallbacks, `_ensure_tracking_token`'s reuse-existing-
          token and mint-failure branches, the no-phone guard and scheduled-
          ride body in `notify_guest_booking_created`, and — the biggest gap
          — `notify_guest_driver_arrived`/`notify_guest_cancelled`, which
          previously only had their early-return guard exercised, never
          their actual SMS-send body. Added
          `backend/tests/test_guest_notification_service_coverage.py` (17
          tests, new file, kept separate from `test_guest_sms.py` per this
          backlog's established pattern). Test-only, no application code
          changed. **No bugs found.** Remaining 5 lines are the dual-import
          `ImportError` fallback, structurally unreachable in this test
          harness (same documented pattern as every other Sub-tier B/C
          file).
        - `services/driver_import_service.py` — **CLOSED, 70.34% → 99%**
          (381 stmts, 113→4 missing; measured via `pytest
          tests/test_driver_import_service.py
          tests/test_admin_driver_import.py
          tests/test_driver_import_service_coverage.py
          --cov=services.driver_import_service
          --cov-report=term-missing`, 87 passed, 0 collisions). The largest
          file in this batch. The two pre-existing test files (22 tests
          combined) covered `build_plan`'s prefetch/resume/web-flow-
          rejection semantics and the admin HTTP endpoints thoroughly, but
          none of the small pure helpers (`parse_bool`, `parse_date`,
          `date_is_ambiguous`, `split_name`, `normalize_phone`,
          `canonical_requirement_key`, `work_auth_status`,
          `regulatory_authority_defaults`) were exercised branch-by-branch,
          `storage_signed_url`/`encrypt_pii` had no test touching
          `supabase.storage`/`supabase.rpc`, `get_service_area`'s by-id and
          multiple-match/no-match branches were untested, and — the biggest
          gap — the entire CLI document-row pipeline (`build_plan` with
          `files_root` set, plus `commit_plan`'s file-upload/document-insert
          logic and `print_report`) had zero coverage, since every existing
          test either used `files_root=None` (web flow) or never called
          `commit_plan` directly with documents/updates queued. Added
          `backend/tests/test_driver_import_service_coverage.py` (65 tests,
          new file). Test-only, no application code changed. **No bugs
          found in application logic; one dead-code observation:**
          `parse_date`/`date_is_ambiguous` both contain a manual
          "`year < 100` → `+2000`" adjustment after a `%y`-format
          `strptime` that appears unreachable — Python's own `%y` parsing
          already pivots two-digit years into the 1969-2068 range before
          the manual check runs (confirmed empirically). Left as-is (out of
          scope for a test-only pass) and flagged for whoever next touches
          this file. Remaining 4 uncovered lines: the import fallback, the
          two dead `year<100` branches just described, and one defensive
          empty-batch guard in `_select_in` that current call sites never
          trigger (`_prefetch_existing` only calls it when its input list is
          already non-empty).
        - `utils/quest_tracker.py` — **CLOSED, 70.42% → 99%** (71 stmts,
          21→1 missing; measured via `pytest tests/test_quests.py
          tests/test_quest_tracker_coverage.py --cov=utils.quest_tracker
          --cov-report=term-missing`, 46 passed, 0 collisions). The
          pre-existing `TestQuestTrackerOnRideComplete` class in
          `test_quests.py` (4 tests) covered the `ride_count` happy path and
          the `peak_rides` local-timezone math, but the progress-fetch DB-
          error guard, a missing/inactive quest, an expired quest, the
          `earnings_target` quest type, the `service_areas` timezone lookup
          (both success and its swallowed-exception fallback — the existing
          peak-hour tests deliberately kept `service_area_id: None` to avoid
          `test_quests.py`'s shared `make_mock_db()` raising on an
          unconfigured `service_areas` sub-mock), an invalid area timezone,
          a naive (no-tzinfo) `completed_at`, no usable completion timestamp
          at all, and the per-progress exception guard (one bad quest row
          must not abort the batch) were all untested. Added
          `backend/tests/test_quest_tracker_coverage.py` (14 tests, new
          file, using its own local mock-db helper rather than
          `test_quests.py`'s shared `make_mock_db()` so a peak_rides test
          could freely set `service_area_id`). Test-only, no application
          code changed. **No bugs found.** Remaining 1 line is the
          dual-import `ImportError` fallback, structurally unreachable in
          this test harness.
        Full writeup:
        `docs/change-log/2026-08-03-a1c-subtier-c-batch-guest-driverimport-quest-coverage.md`.
        Full backend suite was **not** run for this batch — deferred to a
        later consolidated pass across all in-flight A1c batches per
        explicit instruction; see the change-log's "What was NOT verified"
        section.
      - **Batch 5** — **CLOSED 2026-08-03.** `utils/redis_diag.py` 71.43% →
        **99%** (112 stmts, 32→1 missing), `routes/drivers/ride_complete.py`
        71.75% → **96%** (400 stmts, 113→15 missing — trip-completion/fare-
        settlement-kickoff/earnings-snapshot; natural companion to the
        already-closed `ride_flow.py`/`ride_cancel.py`/`ride_reads.py`
        triplet from the same package, treated at Sub-tier-A urgency as
        recommended), `utils/meta_capi.py` 72.37% → **100%** (152 stmts,
        42→0 missing — Meta Conversions API marketing integration). Measured
        via `pytest tests/test_meta_capi_transport_coverage.py
        tests/test_meta_conversions.py tests/test_redis_diag_coverage.py
        tests/test_redis_diag.py tests/test_ride_complete_coverage.py
        tests/test_rides.py tests/test_ride_completion_location.py
        --cov=utils.meta_capi --cov=utils.redis_diag
        --cov=routes.drivers.ride_complete --cov-report=term-missing` (205
        passed). Added `backend/tests/test_meta_capi_transport_coverage.py`
        (new — HTTP transport error/malformed-response branches),
        `backend/tests/test_redis_diag_coverage.py` (new — diagnostic-probe
        error paths), `backend/tests/test_ride_complete_coverage.py` (new,
        largest file in this batch — every non-fatal side-branch in
        `complete_ride` — breadcrumb-flush failure, GPS-aggregation
        fallback, the ride_routes 3-attempt retry loop and its own
        status-write failure, quest-progress scheduling failure, milestone
        compression check, first-ride Meta activation — plus
        `_completion_fix_rejection`'s remaining branches and
        `_fire_driver_activated`'s both-imports-fail/spawn-raises paths).
        Test-only, no application code changed. **No bugs found** in
        `redis_diag.py` or `meta_capi.py`. One coverage-instrumentation note
        for `ride_complete.py`: `_completion_fix_rejection`'s
        `invalid_capture_time` branch is unreachable via the public API
        surface — `CompletionFix.captured_at` is pydantic-typed `datetime`,
        so a non-parseable string is rejected at model construction before
        that branch's own `parse_iso_utc(None)` check would ever run;
        exercised directly with a raw stand-in object instead (same
        "structurally near-impossible to reach through this harness" class
        as the dual-import fallback lines already documented elsewhere in
        this backlog). Full log:
        `docs/change-log/2026-08-03-a1c-subtier-c-batch-redis-ride-meta-coverage.md`.
      - **Batch 6:** `utils/preauth_capture.py` (72.41%, 87 stmts — Stripe
        pre-authorization capture, payment-adjacent), `utils/presence_sweeper.py`
        (72.73%, 33 stmts — **flag: this module is explicitly documented
        as RETIRED in its own docstring** — "the loop body is unreachable
        from production startup," kept only as a no-op so
        `test_p3_loop_jitter_metrics` keeps its symbols stable. Its real
        logic was fully superseded by `utils/driver_online.py` in Batch 3.
        Chasing higher coverage on a documented no-op is low value —
        recommend deprioritizing or skipping this file, same call as
        `routes/main.py`'s dead-code flag under Sub-tier B above). Only 2
        files in this batch — `utils/payment_retry.py`, the batch's
        original third file, was independently closed by a concurrent
        session (see its full entry below) before this list's Batch 6
        would otherwise have been picked up.
      - **Batch 7:** `routes/fares.py` (72.79%, 136 stmts — fare-estimate/
        service-area endpoint, distinct from `services/fare_service.py`
        which is already ≥90% under A1's original scope), `repositories/driver_repo.py`
        (72.99%, 137 stmts), `routes/favorites.py` (73.13%, 67 stmts —
        rider saved-route convenience feature).
      - **Batch 8:** `ai/mcp_server.py` (73.39%, 124 stmts), `ai/providers/__init__.py`
        (73.68%, 38 stmts — the AI-provider adapter factory; small but
        real, not a stub), `routes/disputes.py` (73.88%, 134 stmts —
        rider/driver-facing dispute/refund-request endpoints, distinct
        from the already-closed `routes/admin/support.py`'s admin-side
        dispute handling).
      - **Batch 9:** `utils/driver_onboarding_reminder_rules.py` (74.00%,
        100 stmts), `ai/response_cache.py` (74.29%, 35 stmts),
        `services/zoho_desk_integration.py` (74.42%, 129 stmts).
      - **Batch 10:** `utils/distance_reconciliation.py` (74.70%, 83
        stmts), `services/data_transfer/observability.py` (75.00%, 20
        stmts — Sentry/Prometheus tagging helper for the Data Transfer
        module; small and wrapper-shaped but not a trivial pass-through —
        worth a real test file), `utils/location_integrity.py` (75.00%,
        52 stmts — GPS spoofing/integrity checks, safety-adjacent but not
        under the `safety.py`/`insurance_periods.py` umbrella Track 1
        already owns, so kept here).
      - **Batch "zoho-distrecon-obs" (`services/zoho_desk_integration.py`
        from Batch 9 above, plus `utils/distance_reconciliation.py` and
        `services/data_transfer/observability.py` from Batch 10 above) —
        CLOSED 2026-08-03.** `services/zoho_desk_integration.py` 74.42% →
        **98%** (129 stmts, 33→2 missing), `utils/distance_reconciliation.py`
        74.70% → **96%** (83 stmts, 21→3 missing),
        `services/data_transfer/observability.py` 75.00% → **100%** (20
        stmts, 6→0 missing). Measured via `pytest tests/test_zoho_desk.py
        tests/test_zoho_desk_integration_coverage.py
        tests/test_distance_reconciliation.py
        tests/test_distance_reconciliation_coverage.py
        tests/test_data_transfer_observability_coverage.py -o addopts=""
        --cov=services.zoho_desk_integration --cov=utils.distance_reconciliation
        --cov=services.data_transfer.observability --cov-report=term-missing`
        (65 passed, 0 collisions). Added `backend/tests/test_zoho_desk_integration_coverage.py`
        (21 tests — `create_ticket_for_complaint`/`create_ticket_for_flag`,
        never exercised at all before this batch despite an existing,
        misleadingly-named test claiming to cover them; `_link_ticket`'s
        best-effort `ZohoDeskError`/generic-`Exception` swallow;
        `close_linked_records`'s already-closed skip branches, empty-id
        no-op, and per-table exception isolation; `create_support_ticket`'s
        missing-email re-fetch merge and transcript-append branches),
        `backend/tests/test_distance_reconciliation_coverage.py` (9 tests —
        `_pod_id`, `_seconds_until`'s same-day/wrap-to-tomorrow branches,
        the systematic-bias `logger.error(...)` branch (the whole point of
        this module per its own docstring), and
        `distance_reconciliation_loop`'s lock-acquired/lock-held-elsewhere/
        tick-raises branches), and `backend/tests/test_data_transfer_observability_coverage.py`
        (8 tests, new file — module had none before — every `record_*`
        helper plus `capture_failure`'s tagged-event/unimportable-SDK/
        capture-raises branches). Test-only, no application code changed.
        **No bugs found** in any of the three files. Remaining uncovered
        lines in `zoho_desk_integration.py` (2) and `distance_reconciliation.py`
        (3) are the dual-import `ImportError` fallback boilerplate —
        structurally near-impossible to reach in this harness once the
        module is cached in `sys.modules`, same documented pattern as
        prior Sub-tier B/C files. `utils/location_integrity.py`, the third
        file originally itemized under Batch 10, was **not** in this
        batch's scope (a separate concurrent session's assignment per this
        session's task boundaries) and remains open. Full log:
        `docs/change-log/2026-08-03-a1c-subtier-c-batch-zoho-distrecon-obs-coverage.md`.
      - **Batch 11:** `routes/webhooks.py` (75.40%, 748 stmts — largest
        file in the whole Sub-tier C list, Stripe-webhook-adjacent;
        recommend Sub-tier-A-style urgency given `CLAUDE.md`'s Stripe-
        idempotency conventions, even though it lands in this tier),
        `ai/embeddings.py` (76.79%, 56 stmts), `core/config.py` (76.86%,
        121 stmts — `Settings` fail-fast validation, referenced directly
        by CLAUDE.md's Critical Conventions).
      - **Batch 12:** `utils/route_gap_monitor.py` (77.78%, 108 stmts),
        `utils/route_distance.py` (78.12%, 489 stmts — second-largest file
        in this list), `routes/faqs.py` (78.12%, 32 stmts — public
        unauthenticated FAQ read endpoint, distinct from the already-
        closed `routes/admin/faqs.py` CRUD).
      - **Batch "locintegrity-routegap-routedist" (`utils/location_integrity.py`
        from Batch 10 above — left open when the zoho-distrecon-obs batch
        closed its other two files — plus `utils/route_gap_monitor.py` and
        `utils/route_distance.py` from Batch 12 above) — CLOSED 2026-08-03.**
        `utils/location_integrity.py` 75.00% → **96%** (52 stmts, 13→2
        missing; no test file existed for this module before this batch),
        `utils/route_gap_monitor.py` 77.78% → **95%** (108 stmts, 24→5
        missing), `utils/route_distance.py` 78.12% → **99%** (489 stmts,
        166→3 missing against the two pre-existing route_distance test
        files alone, or 115→3 missing measured against its full existing
        test surface including the indirect-coverage files — see below).
        Measured via `pytest tests/test_location_integrity.py
        --cov=utils.location_integrity --cov-report=term-missing` (16
        passed); `pytest tests/test_route_gap_monitor.py
        tests/test_route_gap_monitor_coverage.py --cov=utils.route_gap_monitor
        --cov-report=term-missing` (24 passed); `pytest
        tests/test_route_distance.py tests/test_route_distance_osrm.py
        tests/test_route_distance_coverage.py tests/test_compute_route_fallback.py
        tests/test_e2e_route_tail_recovery.py tests/test_live_route.py
        tests/test_maps_eta_osrm.py tests/test_phase_distance_parity.py
        tests/test_trip_distance.py --cov=utils.route_distance
        --cov-report=term-missing` (118 passed, 2 skipped). All three
        modules' full test surface together: 158 passed, 2 skipped, 0
        collisions. Added `backend/tests/test_location_integrity.py` (16
        tests, new file — mock-flag rejection, both accuracy-sanity
        branches, impossible-speed rejection, teleport detection within and
        outside the window, malformed/empty cached-point handling, both
        Redis soft-failure paths), `backend/tests/test_route_gap_monitor_coverage.py`
        (11 tests — `_now()`, both `_configured_threshold_seconds`
        validation failures, `_open_gap_event`'s no-op branch, id-less-ride
        and `unknown`-state tick scans, and all three `route_gap_monitor_loop`
        wrapper branches including `CancelledError` not being miscounted as
        a failure), and `backend/tests/test_route_distance_coverage.py` (52
        tests — small-helper edge cases, `compute_segmented_road_route`'s
        three failure branches, `_compute_route_via_osrm`/`snap_endpoint_via_osrm`'s
        error paths, both `compute_gap_route_via_*` functions' full
        validation chains, `_decode_encoded_polyline`, `_compute_route_via_google`'s
        seven branches, and `snap_to_road`'s full OSRM→Google fallback
        chain — previously untested entirely). Test-only, no application
        code changed. **No bugs found** in any of the three target files'
        own logic — every branch behaves per its documented soft-fail/
        best-effort contract. **One test-hygiene bug was found and fixed
        during self-review** (not in application code): an early draft of
        the `_open_gap_event` no-op test patched
        `route_gap_monitor.db_supabase.insert_many_ignore_conflicts` via a
        direct attribute assignment + manual `del` in a `finally` block
        instead of `monkeypatch.setattr(...)`; the `del` removed that
        function from the real, shared `db_supabase` module for the rest of
        the test process, which broke an unrelated, otherwise-passing test
        (`test_e2e_route_tail_recovery.py`) whenever both files ran
        together — caught by running the new files together with the wider
        route-adjacent suite before committing (not by the standalone run
        alone), fixed by switching to `monkeypatch.setattr`. Remaining
        uncovered lines in all three files (2, 5, and 3 respectively) are
        the dual-import `ImportError` fallback boilerplate — structurally
        near-impossible to reach in this harness once the module is cached
        in `sys.modules`, same documented pattern as prior Sub-tier B/C
        files. `routes/faqs.py`, the third file originally itemized under
        Batch 12, was **not** in this batch's scope (this session's task
        boundaries covered only the two `route_*` utils plus the carried-
        forward `location_integrity.py`) and remains open. Full log:
        `docs/change-log/2026-08-03-a1c-subtier-c-batch-locintegrity-routegap-routedist-coverage.md`.
      - **Batch 13:** `utils/apns_client.py` (78.72%, 141 stmts — Apple
        push client), `server.py` (79.20%, 250 stmts — app factory/router
        mounting, see CLAUDE.md's Key Backend Files), `utils/stripe_charge.py`
        (79.74%, 227 stmts — payment-adjacent, closest file in this list
        to the 80% line).
      - **Batch "faqs-apns-server" (`routes/faqs.py` from Batch 12 above,
        plus `utils/apns_client.py` and `server.py` from Batch 13 above) —
        CLOSED 2026-08-03.** `routes/faqs.py` 78.12% → **94%** (32 stmts,
        7→2 missing), `utils/apns_client.py` 78.72% → **100%** (141 stmts,
        30→0 missing), `server.py` 79.20% → **88%** (253 stmts — 3 more
        than the 250 quoted above, minor drift since that number was taken;
        71→31 missing). Measured via `pytest tests/test_utils_extended.py
        tests/test_faqs_coverage.py tests/test_apns_client.py
        tests/test_apns_client_coverage.py tests/test_live_activity.py
        tests/test_p3_push_notifications.py tests/test_server_coverage.py
        tests/test_deprecated_route_admin_exempt.py tests/test_metrics_auth.py
        -o addopts="" --cov=routes.faqs --cov=utils.apns_client
        --cov=backend.server --cov-report=term-missing` (290 passed, 1
        pre-existing skip, 0 collisions). Added `backend/tests/test_faqs_coverage.py`
        (9 tests — `_resolve_area_scope`'s explicit-id-wins, lat/lng-resolves-
        to-an-area, lat/lng-resolves-to-no-area, partial-coordinates no-op,
        and outer exception-swallow branches; `get_public_faqs`'s end-to-end
        area-scope filtering), `backend/tests/test_apns_client_coverage.py`
        (15 tests — `_load_apns_config`'s settings-load-raises and
        malformed-PEM branches; `_load_templates`'s real file load/missing/
        malformed-JSON branches against the real bundled template file, not a
        monkeypatched stand-in; the real `_get_client()`/`aclose()`
        lifecycle; `send_apns_live_activity`'s httpx/jwt-unavailable and
        empty-token guards, the `use_sandbox=None` settings-driven branch,
        the retry-still-fails branch, and the outer exception handler;
        `_reason`'s own exception branch), and `backend/tests/test_server_coverage.py`
        (18 tests, new file — module had zero dedicated tests before —
        `_db_ready`'s cache-hit/fresh-success/non-dict-result/ping-raises/
        ping-timeout branches; `/health`'s healthy/unhealthy shapes;
        `_metrics_token()`'s real env-var read; `/metrics`'s Redis-connected
        gauge block including missing-field defaults and `get_redis_stats`
        raising, plus its query-param-token path;
        `DeprecatedRootPathMiddleware`'s root-prefix `/settings`/
        `/company-info` branch, the only branch taking the non-`/api/`
        canonical-path derivation). Test-only, no application code changed.
        **No bugs found** in any of the three files. Remaining gaps: `routes/faqs.py`'s
        2 missing lines are the dual-import `ImportError` fallback (same
        structurally-near-impossible-to-reach class documented elsewhere in
        this backlog); `utils/apns_client.py` is fully closed at 100%;
        `server.py`'s 31 missing lines are its Sentry-init module-level block
        (`if sentry_dsn: ...`) and `if __name__ == "__main__":` entrypoint —
        both execute only at *import* time before any test can patch
        `settings.sentry_dsn`/`settings.ENV`, and reloading `backend.server`
        mid-suite to reach them risks corrupting the single shared `app`
        instance every other test file in the suite depends on; deliberately
        not attempted per this task's "test additively, don't refactor"
        instruction for this file — flagged as a standing gap for a future
        subprocess-isolated import test, not chased further here.
        `utils/stripe_charge.py`, the third file originally itemized under
        Batch 13, was **not** in this batch's scope (a separate concurrent
        session's assignment per this session's task boundaries) and remains
        open. Full log:
        `docs/change-log/2026-08-03-a1c-subtier-c-batch-faqs-apns-server-coverage.md`.
      - **Batch "stripe-charge" (`utils/stripe_charge.py` from Batch 13
        above) — CLOSED 2026-08-03.** 79.74% → **99%** (227 stmts, 46→1
        missing). Measured via `pytest tests/test_stripe_charge.py
        tests/test_stripe_charge_coverage.py --cov=utils.stripe_charge
        --cov-report=term-missing` (70 passed); also verified against the
        wider payment-adjacent test sweep (every file in `backend/tests/`
        that references `stripe_charge` — 18 files, 484 passed, 0
        collisions, same 99%). Added `backend/tests/test_stripe_charge_coverage.py`
        (51 tests, new file, kept separate from the existing
        `test_stripe_charge.py` which is scoped to `charge_ride()` only) —
        `charge_ancillary_fee()` had **zero** prior direct coverage (every
        branch: amount≤0 no-op, unconfigured, missing customer/payment-method,
        success + fee-scoped idempotency key, requires_action,
        declined-by-status, CardError, StripeError, unhandled status);
        `authorize_ride()`/`verify_authorization()`/`capture_ride()` (the
        booking-time hold / SCA-verify / settlement-capture trio) had large
        guard-clause and non-happy-path gaps, including
        `verify_authorization()`'s two security checks (customer-mismatch
        and amount-too-small rejection) which had no prior test at all;
        plus the remaining `stripe is None` branches on
        `_resolve_stripe_secret()`, `cancel_authorization()`, and
        `charge_ride()`'s own early-return. Test-only, no application code
        changed. **No bugs found** — every branch, including both
        `verify_authorization()` security checks and every idempotency-key
        namespace, behaved exactly as documented. Remaining uncovered line
        (1) is the dual-import `ImportError` fallback boilerplate —
        structurally near-impossible to reach in this harness once the
        module is cached in `sys.modules`, same documented pattern as prior
        Sub-tier B/C files. Full log:
        `docs/change-log/2026-08-03-a1c-subtier-c-batch-stripe-charge-coverage.md`.
      - **Excluded as already closed (Sub-tier A/B, or independently
        closed mid-sweep) or Track-1-owned:** every file named in
        Sub-tier A/B above, plus `utils/payment_retry.py` (closed by a
        concurrent session — full entry below), `utils/allowance_reset.py`
        (corporate — see above), `routes/admin/drivers.py`,
        `routes/admin/sgi_forms.py` (both admin-owned).
      - **New finding, not itemized above (flag only, not a Sub-tier C
        item — this file is below the 60% floor, which per this backlog's
        own definition would make it a new Sub-tier B candidate, not C):**
        `utils/driver_presence.py` — **50.49%, 103 stmts.** Real production
        code (module docstring: superseded-legacy shim referenced by
        `utils.driver_presence`, tied to the same online/available
        composition domain as `utils/driver_online.py` above), not a dev
        script — worth a follow-up session confirming whether this is a
        genuine regression/gap or dead code being phased out in favor of
        `driver_online.py`, before anyone writes tests against it blind.
        Not investigated further here per this task's scoping-only
        mandate. The other below-60% hits from this scan
        (`scripts/*.py` — `_requeue_failed_referrals.py`,
        `check_migration.py`, `check_route_shadowing.py`,
        `reconcile_orphaned_holds.py`, `split_god_file.py`,
        `update_patch_targets.py`, `analyze_ride_route.py`,
        `report_fare_attribution_gap.py`, all near-0%–58%; plus
        `ai/context.py` at 55.56% on only 9 statements) are one-off
        CLI/dev tooling and a trivial file respectively — consistent with
        CLAUDE.md's "Explicitly NOT recommended" guidance against chasing
        coverage on one-off admin scripts, not flagged as regressions.
      - `utils/payment_retry.py` — **CLOSED, 72.54% → 99%** (2026-08-02,
        244 stmts, 67→2 missing; measured via
        `pytest tests/test_payment_retry.py tests/test_payment_retry_coverage.py
        tests/test_cancellation_fee_card_charge.py
        tests/test_e4_d10_payment_3ds_quests.py tests/test_guest_auto_settle.py
        tests/test_replay_safety_payment_loops.py tests/test_stripe_charge.py
        --cov=utils.payment_retry --cov-report=term-missing`). Picked ahead
        of Sub-tier C's raw ranking — same "real-world consequence" call as
        the earlier `reconciliation.py` pick — because a bug here means a
        rider's failed payment or a driver's stuck payout silently never
        retries. `tests/test_payment_retry.py` already covered the core
        double-charge guard (atomic claim race, invoice-skip,
        `requires_capture` happy/edge paths, unexpected-intent-state
        release) in detail but had zero coverage of the Meta
        Purchase-conversion side hook, the invoice-claim staleness helper,
        the admin-alert/payout-notify error-swallow branches, the
        24h-age/30-min-processing-window scan skips, the
        `admin_alerted_payment_exhausted` claim race, the guest-corporate
        settlement sweep, and the `payment_retry_loop` background loop
        itself. Added `backend/tests/test_payment_retry_coverage.py` (40
        tests, kept as a separate file alongside the existing one — same
        pattern as `test_redis_client_coverage.py`) covering all of the
        above, run together with every existing payment test file touching
        this module (106 passed, 0 collisions). Test-only, no application
        code changed. **No bugs found** — every exception branch behaves as
        documented (loud `logger.error(..., exc_info=True)`, no silent
        swallow of a money-moving error; the two `logger.debug`
        push-notification swallows are best-effort side channels, not the
        retry-state source of truth). Remaining 2 uncovered lines are the
        dual-import `ImportError` fallback for
        `utils.loop_monitor.record_heartbeat`, structurally unreachable in
        this test harness (same documented pattern as prior Sub-tier B
        files). See
        `docs/change-log/2026-08-02-a1c-payment-retry-coverage.md`.
    - **Sub-tier C — CLOSED 2026-08-03.** All 39 files in the fresh 60-80%-band
      snapshot (superseding the stale 55-file estimate above — re-run the
      `--cov=.` scoping command before trusting a file count/list here again)
      got dedicated coverage files, including `routes/webhooks.py`,
      `routes/promotions.py`, `repositories/driver_repo.py`,
      `routes/disputes.py` named above. Test-only, written without running
      pytest per this pass's process (mirroring Sub-tier B), then verified in
      one dedicated pass: 28 blind-authored test bugs found and fixed (wrong
      assertions against real, correct behavior — e.g. mock-patch targets,
      `caplog` not reliably capturing DEBUG records in this repo's full-suite
      logging setup, `spawn()`-fire-and-forget assertions checking
      `.awaited` instead of `.called`) — no application code involved in that
      triage pass.
      - **Found-not-fixed bugs from the sweep — 5 fixed as a follow-up batch**
        (2026-08-03, each with its own Change Impact & Risk Log entry in
        `docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md`,
        entries 1-13): unescaped PostgREST OR-clause (`claim_ride_atomic`,
        ride-completion incentive lookup), redis-diagnostics timeout ignoring
        caller deadline, `favorite_routes.use_favorite_route` returning a
        stale pre-increment row, dispute-resolution notification wording,
        `routes/fares.py` NULL `surge_multiplier` crash, two corporate
        pre-pickup-cancellation services swallowing WS-send failures without
        counting the ride as cancelled, three dispatch/notification
        silent-swallow fixes, `corporate_low_balance.py`'s malformed-timestamp
        rate-limiter bypass, `retention_purge.py`'s asymmetric error handling,
        `driver_onboarding_reminder_rules.py`'s per-area opt-out being
        ignored, `core/config.py` missing an `ADMIN_PASSWORD` length guard in
        production (**deployment risk flagged — verify the live
        Railway/Fly.io `ADMIN_PASSWORD` is ≥20 chars before this lands, or
        backend startup will crash**), `location_integrity.py`'s GPS-spoofing
        mock-flag bypass (`is True` vs truthy), `driver_import_service.py`
        bulk-import ignoring document approval status, and
        `distance_reconciliation.py` claiming unevaluated rides. A 6th
        candidate — `routes/webhooks.py`'s `payment_intent.succeeded`
        "processing"-status race — was investigated, a fix was approved and
        applied, then **reverted** after blast-radius testing surfaced a
        pre-existing regression test (`test_webhooks_main.py`'s
        `TestWebhookTimeoutDivergence`) proving the two Stripe handlers'
        differing treatment of `payment_status='processing'` is intentional,
        not a bug — see Entry 13 for the full investigation. Net application
        diff there is zero.
      - **Known pre-existing flake, not fixed, not new**:
        `tests/test_routes_webhooks_coverage.py::TestTwilioInboundSignatureVerification::test_invalid_signature_returns_403`
        passes standalone and in every smaller combination tried, but fails
        intermittently in a full-suite run (cross-file `sys.modules`/module-
        identity interaction somewhere in the ~9,200-test suite, not yet
        bisected — this repo's dual-import module-identity quirks, documented
        elsewhere in this doc and CLAUDE.md, are the likely family of cause).
        Final full-suite run for this batch deselected it explicitly to get a
        clean signal on everything else (9235 passed, 8 skipped, 1 xfailed).
        File a `[CR]` and bisect in a future session rather than re-discover
        this each time; do not treat its absence from a green CI run as "no
        Twilio-webhook coverage" — the test passes in isolation.
      - **Detailed per-batch itemization below, from a parallel concurrent
        session that independently closed the same Sub-tier C scope batch by
        batch** (kept for the file-by-file coverage numbers and methodology
        notes the summary above doesn't repeat):
      - **Batch 11 (of the 13-batch Sub-tier C itemization, PR #3335) — CLOSED
        2026-08-02.** Flagged Sub-tier-A-style urgency because it contains
        `routes/webhooks.py`, the largest file in the entire Sub-tier C list.
        - `routes/webhooks.py` — **75.40% → 95%** (748 stmts, 184→40 missing;
          measured via `pytest tests/test_webhooks_coverage_gap.py
          tests/test_webhooks_main.py tests/test_corporate_webhook.py
          tests/test_webhook_stripe_v15.py tests/test_orphan_refund.py
          tests/test_ses_webhook.py tests/test_twilio_inbound.py
          --cov=routes.webhooks --cov-report=term-missing`, 157 passed, 0
          collisions). This file already had extensive pre-existing coverage
          (`test_webhooks_main.py` alone is 2019 lines) — the gap closed here
          is everything those files didn't reach: the entire
          `charge.dispute.created`/`charge.dispute.closed` lifecycle (~120
          lines, had ZERO coverage anywhere in the repo), the
          `account.updated` webhook dispatch branch, `charge.refunded`'s full
          dispatch through the route (the pre-existing orphan-refund tests
          called `_record_orphan_refund` directly, never exercising the
          surrounding dispatch), `checkout.session.completed`'s
          subscription-linking and stale/superseded-cancel branches,
          `customer.subscription.deleted`'s legacy customer-id fallback
          lookup, `customer.subscription.updated`'s `past_due`/`active`/
          no-row branches, `invoice.payment_failed`'s no-row branch,
          `_extract_invoice_payment_intent`'s successful-retrieve fallback,
          the "matched allowlist but fell through dispatch" defensive guard,
          roughly a dozen best-effort push/receipt/WS failure-swallow
          branches, and several SES/Twilio helper edge cases
          (`_confirm_sns_subscription`, `_suppress_marketing_email`,
          `_resolve_user_id_by_phone`). Added `backend/tests/test_webhooks_coverage_gap.py`
          (56 tests, new file — kept separate from the 2019-line
          `test_webhooks_main.py` for the same reason
          `test_payment_retry_coverage.py` was kept separate from
          `test_payment_retry.py`). Test-only, no application code changed.
          **No bugs found in `routes/webhooks.py` itself** — Stripe idempotency
          (`claim_stripe_event`/`mark_stripe_event_processed`) and the
          "do not silently swallow" convention were both already correctly
          implemented on every branch touched here; this session explicitly
          re-checked against the two related fixes from 2026-08-01
          (`docs/change-log/2026-08-01-c10-stripe-events-reconciliation-sweep.md`,
          `docs/change-log/2026-08-01-fix-mark-stripe-event-processed-swallow.md`)
          and found nothing to add or contradict. **A real bug was found and
          fixed, but in the test suite's own hygiene, not application code:**
          the full 8500+-test suite exposed a pre-existing `sys.modules`
          leak (same class as the already-fixed A8) — some other test
          somewhere in the suite leaves `sys.modules["openai"]`/`["twilio"]`
          replaced by an incomplete stand-in for the rest of the process,
          which made 4 of this batch's new tests fail (but only under the
          full suite, never standalone) with symptoms ranging from
          `AttributeError: <module 'openai'> does not have the attribute
          'AsyncOpenAI'` to a real webhook signature-validation check
          silently degrading to a no-op (200 instead of the expected 403).
          Root cause of the leak itself was not tracked down (exhaustive
          grep across the whole backend found no obviously-unscoped
          `sys.modules` mutation; bisecting ~300 test files was out of scope
          for a coverage pass) — fixed defensively at the point of impact
          instead, by forcing a fresh real import of the affected package in
          each of the 4 affected tests before relying on it. Flagged as a
          standing gap for a future session to bisect and fix at the source,
          the same way A8 itself was eventually run to ground. Full
          writeup: `docs/change-log/2026-08-02-a1c-subtier-c-batch11-coverage.md`.
          Remaining 40 uncovered
          lines: 12 are the dual-import `ImportError` fallback at the top of
          the file (structurally unreachable, same documented pattern as
          every other Sub-tier B/C file); 28 are the bodies of the two
          `@default_limiter.limit(...)`-decorated routes (`ses_sns_webhook`,
          `twilio_inbound_sms`) — confirmed via a from-scratch coverage run
          that these lines were **already** unmeasurable as covered in the
          *original* 75.40% baseline despite being exercised by
          assertion-passing `test_client`-fixture requests in the
          pre-existing `test_ses_webhook.py`/`test_twilio_inbound.py`; this
          is a pre-existing coverage-instrumentation blind spot on
          rate-limiter-decorated routes in this environment, not something
          introduced or fixable by this pass (business logic is exercised
          and asserted correct; only the coverage tool's line-attribution is
          affected). See
          `docs/change-log/2026-08-02-a1c-subtier-c-batch11-coverage.md`.
        - `backend/ai/embeddings.py` — **76.79% → 100%** (56 stmts, 13→0
          missing; measured via `pytest tests/test_ai_embeddings.py
          tests/test_ai_embeddings_coverage.py --cov=ai.embeddings
          --cov-report=term-missing`, 18 passed). The pre-existing
          `test_ai_embeddings.py` covered `embed_texts`'s soft-fail contract
          thoroughly but always patched `_embed_openai`/`_embed_gemini`
          wholesale, so the real provider-calling bodies (the actual
          `AsyncOpenAI`/`google.generativeai` call sites) were never
          exercised. Added `backend/tests/test_ai_embeddings_coverage.py` (5
          tests) covering both bodies directly plus reachability through
          `embed_texts`, and the `asyncio.wait_for` timeout-returns-None
          path. Test-only, no application code changed, no bugs found.
        - `backend/core/config.py` — **76.86% → 100%** (121 stmts, 28→0
          missing; measured via `pytest tests/test_core_config_coverage.py
          tests/test_p1_auth_hardening.py tests/test_admin_routes_auth.py
          tests/test_csrf_middleware.py --cov=core.config
          --cov-report=term-missing`, 81 passed). The `Settings` fail-fast
          production guard was already substantially covered by
          `test_p1_auth_hardening.py`'s `TestProductionStartupGuards` (the
          JWT_SECRET-length, Firebase-app-id, and SUPABASE_REGION branches)
          — the remaining gap was the placeholder-value checks
          (`JWT_SECRET == "your-strong-secret-key"`, `ADMIN_PASSWORD in
          ("admin123", "password", "changeme")`), the missing-`SUPABASE_URL`/
          `SUPABASE_SERVICE_ROLE_KEY` guards, `_hash_admin_password`, the
          entire `review_login_map`/`_validate_review_accounts` App
          Store/Play reviewer-OTP-allowlist parser (had zero test coverage),
          and the `SECRET_KEY`/`debug` properties. Added
          `backend/tests/test_core_config_coverage.py` (33 tests). Tests
          assert the fail-fast behavior actually raises (not just that lines
          execute) for each production guard, per the task's explicit ask.
          Test-only, no application code changed, no bugs found. One
          non-obvious finding surfaced while debugging a flaky `caplog`
          assertion (not a bug, a test-authoring note worth recording): in
          this test harness `backend.core.config` and `core.config` resolve
          to the same already-imported module object (verified via `id()`),
          and that module's `logger = logging.getLogger(__name__)` was bound
          under the name `"core.config"` — so `caplog.at_level(...,
          logger="backend.core.config")` silently captures nothing, while
          `caplog.at_level(..., logger="core.config")` works. Worth knowing
          for any future test in this file or a sibling that asserts on this
          module's log output.
    - First file since this scoping pass picked up below.
      - **Note on `routes/webhooks.py` appearing twice below:** this
        session's Batch 11 (95%, `test_webhooks_coverage_gap.py`, 56 tests)
        and a separate concurrent session's pass (78%, explicitly marked
        "not fully closed" below) both landed independently. No file-path
        collision (different test file names), so both are kept — Batch
        11's 95% is the higher/more complete result and should be treated
        as superseding the 78% entry for planning purposes, but the 78%
        entry's own findings (the SES/Twilio helper-function coverage) are
        real and additive, not duplicated by Batch 11, so its bullet is
        kept intact rather than deleted.
      - `routes/promotions.py` — **CLOSED, 65.85% → 93%** (2026-08-02, 328
        stmts, measured via `pytest tests/test_promotions_coverage.py
        tests/test_p2_promo_wallet_loyalty.py tests/test_promo_discount_parity.py
        tests/test_promo_per_user_race.py tests/test_promo_rate_limit.py
        tests/test_ai_tools_booking.py tests/test_create_ride_post_insert_branches.py
        tests/test_admin_rides_coverage.py tests/test_admin_rides_read_endpoints_coverage.py
        tests/test_p3_promo_concurrency.py --cov=routes.promotions
        --cov-report=term-missing`). Existing test files covered rules 1-4
        of `_validate_promo_for_user`'s 10-rule engine (expiry, total-usage,
        per-user limit, min fare) and flat/percentage discount math but
        nothing on rules 5-10 (private coupon, first-ride-only, new-user-only,
        inactive-user targeting, min/max ride count, budget cap), the
        `free_ride` branch, the `ride_id` server-side fare re-fetch branch,
        the malformed-expiry catch, or most of `list_available_promos` (the
        `/promo/available` engine — service-area resolution, ineligible-but-
        shown min-fare marking, per-promo exception isolation, sorting).
        Added `backend/tests/test_promotions_coverage.py` (41 tests). Also
        found and documented (not fixed) that this module's `admin_router`
        (4 CRUD functions) is dead code — never mounted in
        `backend/server.py` (only `routes/admin/promotions.py`'s router is,
        for the live `/api/admin/promotions` surface); exercised directly as
        plain functions for coverage purposes only. Test-only, no
        application code changed. Full suite re-run after: 8456 passed (was
        8415), 8 skipped, 1 xfailed, 0 failed. See
        `docs/change-log/2026-08-02-a1c-promotions-coverage.md`.
      - `routes/webhooks.py` — **IMPROVED (not fully closed), 75.40% → 78%**
        (2026-08-02, 748 stmts, measured via `pytest
        tests/test_webhooks_helpers_coverage.py tests/test_webhooks_main.py
        tests/test_orphan_refund.py tests/test_webhook_stripe_v15.py
        tests/test_ses_webhook.py tests/test_twilio_inbound.py
        tests/test_corporate_webhook.py --cov=routes.webhooks
        --cov-report=term-missing`). The huge `stripe_webhook` route already
        had deep coverage from existing test files; the module-private
        SES/Twilio/invoice helper functions
        (`_extract_invoice_payment_intent`, `_invoice_period_end_iso`/
        `_invoice_period_start_iso`, `_confirm_sns_subscription`,
        `_topic_arn_allowed`, `_suppress_address`,
        `_suppress_marketing_email`, `_handle_ses_notification`,
        `_resolve_user_id_by_phone`, `_handle_sms_keyword`) had zero direct
        unit tests. Added `backend/tests/test_webhooks_helpers_coverage.py`
        (39 tests). Real remaining gap: large chunks of `stripe_webhook`'s
        deep event-type branches (~lines 904-1094, 1867-1901) are still
        untested — flagging as unfinished for a future session rather than
        overstating this as "closed."
      - `repositories/driver_repo.py` — **CLOSED, 72.99% → 99%**
        (2026-08-02, 137 stmts, measured via `pytest
        tests/test_driver_repo_coverage.py
        tests/test_set_driver_available_invariant.py
        tests/test_go_online_availability.py tests/test_claim_ride.py
        tests/test_driver_claim_reaper.py --cov=repositories.driver_repo
        --cov-report=term-missing`). Only `set_driver_available
        (available=True)` had a direct unit test before this. Added
        `backend/tests/test_driver_repo_coverage.py` (38 tests) covering
        every function's no-supabase/success/exception branches, including
        the `available=False` release path and claim-won-vs-claim-lost
        races for `claim_driver_atomic`/`claim_ride_atomic`/
        `match_and_claim_driver`.
      - `routes/disputes.py` — **CLOSED, 73.88% → 94%** (2026-08-02, 134
        stmts, measured via `pytest tests/test_disputes_admin_coverage.py
        tests/test_dispute_refund_cents.py
        tests/test_p3_addresses_favorites_safety_disputes.py
        --cov=routes.disputes --cov-report=term-missing`). User-facing
        endpoints and the Stripe-refund happy path were already covered;
        `admin_get_disputes` had zero direct test and
        `admin_resolve_dispute`'s guard/error branches (404, 400×2,
        `manual_required`, 503, 502, `rejected`, no-refund-amount,
        notify-failure-swallow) were untested. Added
        `backend/tests/test_disputes_admin_coverage.py` (13 tests). Also
        found and documented (not fixed) that this module's `admin_router`
        (same dead-code pattern as `promotions.py`'s) is never mounted in
        `backend/server.py` — the live `/api/admin/disputes` surface is
        `routes/admin/support.py`.
      - Test-only across all three files, no application code changed. Full
        suite re-run after: 8546 passed (was 8456), 8 skipped, 1 xfailed, 0
        failed. See
        `docs/change-log/2026-08-02-a1c-webhooks-driver_repo-disputes-coverage.md`.
      - **Fresh re-scope performed** (`pytest tests/ -q --cov=. --cov-report=json`
        from `backend/`, 2026-08-02): confirms the 60-80% band is now ~41
        files (down from the original ~54-55 estimate as batches 1-2
        closed files). Three more picked ahead of raw ranking for
        dispatch/corporate/payments real-world-consequence, same reasoning
        as `reconciliation.py`/`payment_retry.py`:
        - `utils/offer_expiry_reaper.py` — **CLOSED, 61% → 94%** (66 stmts,
          measured via `pytest tests/test_offer_expiry_reaper_coverage.py
          tests/test_offer_expiry_reaper.py --cov=utils.offer_expiry_reaper
          --cov-report=term-missing`). The durable backstop for offer-timeout
          timers lost on a pod restart — `_reap_tick`'s fetch-exception/
          scan-cap/settings-fallback/redispatch-exception branches and the
          entire `offer_expiry_reaper_loop` wrapper (lock branches,
          tick-exception-survives) were untested. Added
          `backend/tests/test_offer_expiry_reaper_coverage.py` (8 tests).
        - `utils/corporate_low_balance.py` — **CLOSED, 62% → 91%** (64
          stmts, measured via `pytest
          tests/test_corporate_low_balance_coverage.py
          tests/test_corporate_low_balance.py --cov=utils.corporate_low_balance
          --cov-report=term-missing`). Low-balance email nudges for
          corporate wallets with auto-topup off — the company-not-found
          branch, malformed-timestamp catch, one-wallet-failure-doesn't-
          abort-batch swallow, and the entire `corporate_low_balance_loop`
          wrapper were untested. Added
          `backend/tests/test_corporate_low_balance_coverage.py` (5 tests).
        - `utils/orphaned_hold_reconciler.py` — **CLOSED, 69% → 90%** (91
          stmts, measured via `pytest
          tests/test_orphaned_hold_reconciler_loop_coverage.py
          tests/test_orphaned_hold_reconciler.py --cov=utils.orphaned_hold_reconciler
          --cov-report=term-missing`). Releases stranded Stripe card-hold
          authorizations on cancelled rides — `find_orphaned_holds`/
          `_claim`/`reconcile_tick` were already extensively covered (17
          tests), but the `orphaned_hold_reconciler_loop` wrapper (lock
          branches, the `CancelledError`-must-propagate contract,
          generic-exception counting) was only referenced to confirm it's
          registered in lifespan, never exercised. Added
          `backend/tests/test_orphaned_hold_reconciler_loop_coverage.py`
          (6 tests).
        - Test-only across all three files, no application code changed.
          Full suite re-run after: 8565 passed (was 8546), 8 skipped, 1
          xfailed, 0 failed. See
          `docs/change-log/2026-08-02-a1c-offer-reaper-corp-low-balance-orphaned-hold-coverage.md`.
      - Continuing from the same re-scope list, three more background-loop
        utilities picked ahead of raw ranking (dispatch/payments/corporate):
        - `utils/driver_claim_reaper.py` — **CLOSED, 65% → 94%** (68
          stmts, measured via `pytest
          tests/test_driver_claim_reaper_coverage.py
          tests/test_driver_claim_reaper.py --cov=utils.driver_claim_reaper
          --cov-report=term-missing`). Releases drivers orphaned by a
          crashed dispatch claim — `_reap_tick`'s fetch-exception and
          release-exception branches and the entire
          `driver_claim_reaper_loop` wrapper were untested. Added
          `backend/tests/test_driver_claim_reaper_coverage.py` (7 tests).
        - `utils/preauth_capture.py` — **CLOSED, 72% → 94%** (87 stmts,
          measured via `pytest tests/test_preauth_capture_coverage.py
          tests/test_preauth_capture.py --cov=utils.preauth_capture
          --cov-report=term-missing`). Captures booking-time card holds
          after the tip window — the Meta Purchase-conversion hook
          (fires-on-new-capture / skipped-on-already_paid-replay), the
          receipt-send-exception swallow, `_capture_tick`'s fetch-exception
          branch, and the entire `preauth_capture_loop` wrapper were
          untested. Added `backend/tests/test_preauth_capture_coverage.py`
          (10 tests).
        - `utils/allowance_reset.py` — **CLOSED, 68% → 89%** (76 stmts,
          measured via `pytest tests/test_allowance_reset_coverage.py
          tests/test_c_allowance_reset_atomic.py
          tests/test_corporate_allowance_reset.py --cov=utils.allowance_reset
          --cov-report=term-missing`). Rolls corporate allowance periods
          forward — the no-wallet-found skip, one-row-exception-doesn't-
          abort-batch swallow, `_add_one_month`'s day-clamp edge case, and
          the entire `allowance_reset_loop` wrapper were untested. Added
          `backend/tests/test_allowance_reset_coverage.py` (7 tests).
        - Test-only across all three files, no application code changed.
          Full suite re-run after: 8711 passed (was 8565 plus other merged
          main commits' tests in between, e.g. #3341), 8 skipped, 1
          xfailed, 0 failed. See
          `docs/change-log/2026-08-02-a1c-claim-reaper-preauth-allowance-reset-coverage.md`.
      - Three more dispatch/insurance-audit-adjacent files:
        - `utils/period1_distance_finalizer.py` — **CLOSED, 64% → 88%** (73
          stmts, measured via `pytest
          tests/test_period1_distance_finalizer_coverage.py
          tests/test_period1_distance_finalizer.py
          --cov=utils.period1_distance_finalizer --cov-report=term-missing`).
          Drains Period-1 (deadhead) distance accumulators into the
          append-only insurance-period audit table — the active-ride-check
          exception branch (conservatively doesn't finalize),
          `db_supabase.supabase is None` early returns, one-driver-
          exception-doesn't-abort-batch, and the entire
          `period1_distance_finalizer_loop` wrapper were untested. Added
          `backend/tests/test_period1_distance_finalizer_coverage.py`
          (7 tests).
        - `utils/driver_online.py` — **CLOSED, 70% → 100%** (33 stmts,
          measured via `pytest tests/test_driver_online_coverage.py
          --cov=utils.driver_online --cov-report=term-missing`). No
          dedicated test file existed at all for this pure-function
          intent+presence composition every dispatch reader routes
          through. Added `backend/tests/test_driver_online_coverage.py`
          (21 tests) covering every branch of `_parse_ts`/`intent_online`/
          `effective_online`/`effective_available`/`filter_effective_online`.
        - `utils/presence_sweeper.py` — **CLOSED, 73% → 94%** (33 stmts,
          measured via `pytest tests/test_presence_sweeper_coverage.py
          tests/test_p3_loop_jitter_metrics.py --cov=utils.presence_sweeper
          --cov-report=term-missing`). A documented RETIRED no-op (own
          module docstring: no longer scheduled at startup, kept only for
          loop-jitter-test symbol stability) — the tick-exception-counts
          branch and the `CancelledError`-must-propagate branch were
          untested. Added `backend/tests/test_presence_sweeper_coverage.py`
          (3 tests).
        - Test-only across all three files, no application code changed.
          Full suite re-run after: 8742 passed (was 8711), 8 skipped, 1
          xfailed, 0 failed. See
          `docs/change-log/2026-08-02-a1c-period1-finalizer-driver-online-presence-sweeper-coverage.md`.
      - Two more files (Zoho Desk integration + PIPEDA export purge), scoped
        as a 3-file batch alongside `period1_distance_finalizer.py` — that
        third file was dropped mid-task once PR #3354 (above) turned out to
        already be in flight on it; see the change-log's scope note:
        - `services/zoho_desk_service.py` — **CLOSED, 65.84% → 100%** (202
          stmts, measured via `pytest tests/test_zoho_desk.py
          tests/test_zoho_desk_service_coverage.py
          --cov=services.zoho_desk_service --cov-report=term-missing`).
          Most individual Zoho Desk endpoint wrappers (`search_tickets`,
          `create_ticket`, `get_ticket_threads`, `get_thread`,
          `add_comment`, `update_ticket`'s success path,
          `add_tags`/`remove_tags`, `list_agents`, `list_departments`,
          `get_default_department_id`) had never been called directly by
          any test — only reached indirectly, success-path-only, via
          `zoho_desk_integration.py`'s tests mocking `zoho.create_ticket`
          itself. Also untested: `_token_is_fresh`'s non-string/naive-
          datetime/unparseable-expiry branches, and `_refresh_access_token`'s
          / `_request`'s transport-error and malformed-response-body
          branches. Added `backend/tests/test_zoho_desk_service_coverage.py`
          (32 tests).
        - `utils/data_export_purge.py` — **CLOSED, 68.42% → 91%** (57
          stmts, measured via `pytest tests/test_data_export_purge.py
          tests/test_data_export_purge_loop_coverage.py
          --cov=utils.data_export_purge --cov-report=term-missing`). PIPEDA
          deletion-retention purge (hourly loop, deletes expired DSAR
          export ZIPs + admin Data Transfer exports from Storage). The
          `supabase is None` early-out, a row missing `storage_path`/`id`
          being skipped (never guessed at, never marked deleted), and the
          entire `data_export_purge_loop` wrapper (both tables ticked per
          iteration with independent exception guards, heartbeat) were
          untested. Added `backend/tests/test_data_export_purge_loop_coverage.py`
          (6 tests). Remaining 5 lines (35-40) are the dual-import
          `ImportError` fallback boilerplate, not pursued (same convention
          as every prior Sub-tier B/C session).
        - Test-only across both files, no application code changed, no bugs
          found. Full suite re-run after: 8761 passed (fresh session-start
          baseline on this branch after merging in #3353/#3354), 0 failed.
          See
          `docs/change-log/2026-08-03-a1c-subtier-c-p1df-zoho-export-coverage.md`.
      - **Batch (`ai/providers/__init__.py` from Batch 8 + `driver_onboarding_reminder_rules.py`/
        `ai/response_cache.py` from Batch 9) — CLOSED 2026-08-03.** Deliberately
        recombined scope per explicit task instruction — excludes Batch 9's
        third file (`services/zoho_desk_integration.py`, still open) and
        Batch 8's other two files (`ai/mcp_server.py`, since independently
        closed by `claude/a1c-subtier-c-batch-fares-fav-mcp`/PR #3359;
        `routes/disputes.py`, already closed above at 94%).
        - `ai/providers/__init__.py` — **73.68% → 89.47%** (38 stmts, 10→4
          missing; measured via `pytest tests/test_ai_provider_factory.py
          tests/test_ai_provider_factory_coverage.py --cov=ai.providers
          --cov-report=term-missing`, 13 passed). The existing test file
          patches `_get_app_settings_fn` wholesale, so its own lazy-import
          body never ran; the `int(ai_max_output_tokens)` cast's
          `except (TypeError, ValueError)` fallback and
          `_load_adapter_class`'s `except ImportError` fallback (relative
          import failing, retried as an absolute import) were both
          untriggered. Added `backend/tests/test_ai_provider_factory_coverage.py`
          (5 tests): invalid/`None` max-tokens fallback, a direct unpatched
          call to `_get_app_settings_fn()`, and the adapter-loader's
          `ImportError` fallback forced via patching `importlib.import_module`
          (both a unit-level check and an end-to-end `get_adapter()` call
          through it). Remaining 4 uncovered lines are the two dual-import
          `ImportError` fallbacks at module-import time (top-of-file `from
          .base import ...` and inside `_get_app_settings_fn`) —
          structurally unreachable without breaking `sys.modules` import
          machinery, same documented pattern as every other Sub-tier B/C
          file; this repo's `test_dual_import_parity.py` verifies these
          structurally via AST rather than runtime coverage, which this pass
          follows rather than works around.
        - `utils/driver_onboarding_reminder_rules.py` — **74.00% → 100%**
          (100 stmts, 26→0 missing; measured via `pytest
          tests/test_driver_onboarding_reminders.py
          tests/test_driver_onboarding_reminder_rules_coverage.py
          --cov=utils.driver_onboarding_reminder_rules
          --cov-report=term-missing`, 76 passed). The only existing test
          file drives this module *indirectly* through the reminder loop,
          whose fixture always supplies a valid timezone, dict-shaped
          `required_documents`, and an empty `docs` list — which
          short-circuits `missing_required_document_uploads` before
          `doc_matches_requirement` is ever called. Added
          `backend/tests/test_driver_onboarding_reminder_rules_coverage.py`
          (53 tests) calling every pure function directly: `_zone`'s
          invalid-timezone-with-warning fallback, `parse_remindable_statuses`'s
          JSON-array (valid and invalid) and non-list-value branches,
          `_load_list`'s string/JSON branches, `_pretty`,
          `mandatory_requirements`'s string-item branch and the
          previously-fully-untested `global_reqs` fallback (when the area
          has no `required_documents`), `doc_matches_requirement` itself
          (never called anywhere in the repo's test suite before this), and
          `missing_required_document_uploads`'s no-requirements/superseded/
          rejected/pending branches.
        - `ai/response_cache.py` — **74.29% → 100%** (35 stmts, 9→0 missing;
          measured via `pytest tests/test_ai_response_cache.py
          tests/test_ai_response_cache_coverage.py --cov=ai.response_cache
          --cov-report=term-missing`, 26 passed). The existing test file's
          orchestrator-wiring tests patch `orch.response_cache.get_cached`/
          `store_cached` entirely, so `get_cached`/`store_cached`'s own
          `redis_get`/`redis_set`-wrapping try/except bodies (the "never
          raises" cache-fault contract) had zero direct coverage anywhere.
          Added `backend/tests/test_ai_response_cache_coverage.py` (5
          tests): success, miss, and swallowed-Redis-exception paths for
          both functions, patching `rc.redis_get`/`rc.redis_set` directly.
        - Test-only across all three files, no application code changed, no
          bugs found — every exception branch behaves as documented (a
          cache read/write fault logs and falls through/returns without
          raising; the invalid-provider and missing-API-key paths in
          `get_adapter` still raise `AIConfigError` loudly, never a silent
          fallback). Per this session's explicit batching instruction, the
          full backend suite was **not** re-run — only the three new files,
          standalone and combined with each target module's existing test
          file (see coverage commands above), all passing with 0 collisions.
          Full-suite/CI verification across all in-flight A1c batches is
          deferred to a later consolidated pass. See
          `docs/change-log/2026-08-03-a1c-subtier-c-batch-providers-onboarding-respcache-coverage.md`.
      - Continuing from the same re-scope list, three more files picked
        from the raw Sub-tier C ranking (rider-facing fare-estimate +
        saved-route surface, plus the AI/MCP surface):
        - `routes/fares.py` — **CLOSED, 65% → 97%** (2026-08-03, 136
          stmts, measured via `pytest tests/test_fares_coverage.py
          tests/test_favorites_coverage.py tests/test_ai_mcp_coverage.py
          tests/test_fares.py tests/test_ai_mcp.py
          tests/test_p3_addresses_favorites_safety_disputes.py
          --cov=routes.fares --cov-report=term-missing`; a fresh
          `-k fares` run measured 65% at session start against the
          72.79% documented baseline — other test files exercise the
          `/fares` HTTP endpoint incidentally without "fares" in their
          filename, which a name-filtered run misses). The existing
          `tests/test_fares.py` covered the surge-cap regression and the
          vehicle-pricing-vs-fare_configs precedence directly, but had
          zero coverage of `_fd`/`_money_str`'s exception branches, the
          fare-cache key/invalidate helpers, `resolve_service_area_for_point`,
          `resolve_area_scope`'s empty-input guard, `build_fares_for_area`'s
          two early-return guards and its legacy `fare_configs` fallback
          path, the full `_fares_for_location_impl` orchestration
          function, and the `/fares` endpoint's Redis cache hit/miss/
          read-error/write-error branches. Added
          `backend/tests/test_fares_coverage.py` (29 tests). Per
          CLAUDE.md's surge-pricing conventions, explicitly asserted (not
          just exercised) that `SURGE_CAP=2.5` holds even against a 9.9
          DB value, that the `surge_enabled` admin toggle gates a stale
          `surge_multiplier`, and that the `/fares` cache TTL caps at 60s
          while surge is active.
        - `routes/favorites.py` — **CLOSED, 73% → 97%** (2026-08-03, 67
          stmts, measured via the same combined command above,
          `--cov=routes.favorites`). `tests/test_p3_addresses_favorites_safety_disputes.py`
          covered list/create/duplicate/address-mismatch/delete-not-found
          but never exercised `POST /favorites/{id}/use`, the delete
          *success* path, or `POST /favorites/from-ride/{ride_id}` (all
          three branches). Added `backend/tests/test_favorites_coverage.py`
          (10 tests).
        - `ai/mcp_server.py` — **CLOSED, 73% → 94%** (2026-08-03, 124
          stmts, measured via the same combined command above,
          `--cov=ai.mcp_server`). `tests/test_ai_mcp.py` covered
          `MCPAuthMiddleware` and the SDK-exposure rules thoroughly, but
          the `_list_tools`/`_call_tool` closures registered inside
          `build_mcp_asgi_app()` were never driven end-to-end — both the
          `mcp` SDK's `list_tools()`/`call_tool()` decorators return the
          *original* undecorated function to the caller, not the
          registered handler, so the only way to exercise them is via the
          `Server`'s own `request_handlers` dict, which
          `backend/tests/test_ai_mcp_coverage.py` (12 tests) now does
          directly against the real `mcp` SDK (present in this
          environment). Also closed `build_mcp_asgi_app()`'s top-level
          exception-swallow branch, `_audience_for`, two
          `MCPAuthMiddleware.__call__` branches (non-HTTP ASGI scope, and
          an auth failure raising something other than `HTTPException`),
          and `stop_mcp()`'s shutdown-exception swallow.
        - Test-only across all three files, no application code changed,
          no bugs found. Full suite re-run after: 8762 passed (was 8711,
          measured fresh at session start), 8 skipped, 1 xfailed, 0
          failed — delta +51 exactly matches the 51 new tests added
          (29 + 10 + 12). See
          `docs/change-log/2026-08-03-a1c-subtier-c-fares-fav-mcp-coverage.md`.
    - Next file since this re-scope picked up below.
  - `backend/utils/reconciliation.py` (Sub-tier B above, daily Stripe ↔ DB ↔
    `financial_events` reconciliation loop — the only alarm for a Stripe/DB
    financial drift going undetected) — **16% → 90%** (2026-08-01, measured
    via `pytest tests/test_reconciliation.py --cov=utils.reconciliation`;
    102 stmts, 10 missed). Had no dedicated test file. Picked ahead of
    Sub-tier B's raw ranking (lower than `t4a_pdf.py`'s 4% and
    `subscription_invoice_pdf.py`'s 8%) specifically for real-world
    consequence — a silent bug here means a real financial discrepancy goes
    undetected, not just untested, unlike a cosmetic PDF-rendering bug.
    Added `backend/tests/test_reconciliation.py` (19 tests): the loop's
    survive-a-failing-tick behavior, the before-2am / lock-not-acquired /
    lock-acquired branches of `_maybe_run_tick`, `_run_reconciliation`'s
    stripe-key-not-configured / other-RuntimeError / generic-exception /
    financial-events-query-failure early returns, the exact `> threshold`
    (not `>=`) 1-cent boundary, `_sum_stripe_intents`'s pagination and
    succeeded-only filter, `_sum_financial_events`'s None-skip and
    empty-`.data` handling, and `_record_discrepancy`'s insert shape plus
    its swallow-on-failure contract. Test-only, no application code changed,
    no bugs found. Full suite re-run after: 6801 passed (was 6782), 0
    failed, 0 new warnings. See
    `docs/change-log/2026-08-01-a1c-reconciliation-coverage.md`.
  - ~~Rest of Sub-tier A/B/C above: not yet started~~ — stale as of
    2026-08-10: this line was never updated after Sub-tier B (2026-08-02)
    and Sub-tier C (2026-08-03) both closed above. Left struck through
    rather than deleted so the correction is visible in-place.
- **Approach:** everything currently below the 60% CI floor or in the
  60-80% band with no explicit target, that Track 1 didn't already touch.
  Only worth picking up once a specific file becomes a live incident source,
  or if the user explicitly wants full-backend breadth next.
- **Explicitly NOT recommended:** raising the CI floor to 80% uniformly
  across the whole backend in one move. Many low-risk files (CSV export
  helpers, LMS integration, one-off admin scripts) would cost
  disproportionate effort for coverage that doesn't reduce real risk —
  same diminishing-returns logic that stopped A1's `matching.py` pass at
  79.4% rather than chasing the last 0.6%.
- **Also explicitly out of scope:** frontend test coverage
  (rider-app/driver-app/admin-dashboard — React Native / Next.js) and a
  correctness audit of fare/pricing *values* (that data lives in the live
  DB, not this repo, and needs a live DB read to answer, not a coverage
  pass). Real, separate asks — track as their own items if/when wanted,
  don't fold into A1c.
- **Acceptance:** met (2026-08-10) — no fixed target was ever set (deliberately,
  see above); closed instead on the combination of all three scoped
  sub-tiers being done and the measured 90% full-suite aggregate. See
  Status line above for the verification run.

### A2. Post-deploy smoke test in CI
- [x] **Status:** done — already implemented before this checklist was last
  reviewed. `.github/workflows/ci.yml`'s `smoke-test` job curls `/health` +
  DB check, `app_settings`, `vehicle-types`, and confirms auth/fare-estimate
  return 401 not 500; `notify-failure` job alerts on failure. `deploy-fly.yml`/
  `deploy-backend.yml` already have health-poll-and-rollback logic. Landed in
  commit `3bae3db`. (Note: a prior attempt to mark this done, PR #2504,
  reported `merged: true` on GitHub but its commit never actually landed on
  `main` — re-applying the doc fix here.)
- **Why:** deploys to Fly/Railway succeed or fail silently; a bad deploy is currently
  discovered by users. The smoke script from PR #172 already exists.
- **Files:** `.github/workflows/deploy-fly.yml`, `.github/workflows/deploy-backend.yml`
- **Approach:** add a job after deploy that curls `/health`, exercises auth (expect 401
  not 500), and the fare-service health path with `--fail-with-body`; page on failure.
- **Acceptance:** a deliberately broken deploy turns the workflow red within minutes.

### A3. PIPEDA breach record register
- [x] **Status:** done — already implemented before this checklist was last
  reviewed. `docs/audit/breach-record.md` exists with a superset of the
  requested columns (date, scope, RROSH assessment, notified?, evidence
  location) and the required "no entries to date" first row. Created in PR
  #2222 (2026-07-25). (Note: a prior attempt to mark this done, PR #2504,
  reported `merged: true` on GitHub but its commit never actually landed on
  `main` — re-applying the doc fix here.)
- **Why:** referenced by `docs/runbooks/data-breach.md` but never created; PIPEDA
  requires a 24-month breach record.
- **Files:** create `docs/audit/breach-record.md`
- **Acceptance:** template with columns (date, scope, RROSH assessment, notified?,
  evidence location) and a "no entries to date" first row.

### A4. 156 failing backend tests on `main`
- [x] **Status:** ✅ fully complete (2026-07-27) — all 4 buckets cleared, 0
  known backend test failures remain. Bucket 2 (the last holdout —
  `test_wallet.py::TestTransfer`/`TestTopUp`,
  `test_p2_promo_wallet_loyalty.py::TestWalletTopUp`) was resolved per
  explicit product confirmation: wallet-to-wallet transfer is a removed
  feature (no `/transfer` route exists in `routes/wallet.py`) — `TestTransfer`
  was deleted, and `TestTopUp` was rewritten against the current Stripe
  PaymentIntent + EphemeralKey response shape (credit now happens
  asynchronously via the `payment_intent.succeeded` webhook, already covered
  by `test_webhooks_main.py::test_wallet_topup_credits_idempotently_on_reference_id`).
  Fixed across ~22 PRs (#2394 through #2421 and follow-ups), plus several
  genuine production bugs found and fixed along the way (a broken dual-import
  fallback silently dropping an insurance-period audit write; a corporate
  allowance RPC's `p_actor_user_id` parameter silently re-narrowed from
  `TEXT` back to `UUID` by two later migrations, reopening the exact
  `22P02` bug 214 had already fixed). Originally found while triaging PR
  #2377's CI failures (2026-07-26); root-caused 2026-07-26 (see below).
  Confirmed **test drift, not a product regression** — production code
  changed correctly; tests were never updated to match. Full local suite
  run (2026-07-27): `4667 passed, 8 skipped, 1 xfailed, 0 failed`.
- **Root cause breakdown (ranked by likely share of the 156):**
  1. **Orphaned `patch()` targets after module splits (likely >half of the 156).**
     `routes/drivers.py` → `routes/drivers/` package, `routes/rides.py` →
     `routes/rides/` package, and `routes/wallet.py` logic partially extracted
     into `repositories/wallet_repo.py`. Tests still `patch()` symbols at their
     old location (e.g. `routes.drivers.set_presence`, now `utils.driver_presence`;
     `routes.wallet.wallet_increment_balance`, now `repositories/wallet_repo.py:31`
     re-exported via `db_supabase.py:300,315`) — `AttributeError` on `patch()`
     fails the test before any assertion runs. Confirmed-dead patch targets found
     in `test_coverage_rides.py` (128 tests), `test_drivers_extended.py` (81
     tests), `test_wallet.py`, `test_p2_promo_wallet_loyalty.py`,
     `test_dispatch_cascade.py`, `test_dispatch_presence_failopen.py`.
     **Fix:** mechanical sweep re-pointing each `patch()` string at the module
     that now actually owns the symbol — no logic changes, lowest-risk bucket to
     clear first and likely clears well over half the 156 in one pass.
  2. **Wallet endpoints genuinely changed shape.** `POST /wallet/transfer` was
     removed entirely (`routes/wallet.py` has no `/transfer` route — the 404s
     in `TestTransfer` are correct current behavior, not a bug). `POST
     /wallet/top-up` was rewritten to create a Stripe PaymentIntent +
     EphemeralKey instead of crediting the balance synchronously — credit now
     happens via the `payment_intent.succeeded` webhook (`wallet.py:144-227`).
     Old tests assert the pre-rewrite synchronous-credit contract.
     **Fix:** delete `TestTransfer` (dead feature) or replace with a real
     transfer test if the feature is coming back; rewrite `TestTopUp` against
     the PaymentIntent-creation response shape, add a webhook-level test for
     the actual credit path.
  3. **New tax computation exhausts fixed-length mocks.**
     `_compute_subscription_tax` (`routes/drivers/subscriptions.py:237`) is new
     code that adds 2 extra `db_supabase.find_one` calls in the one-off
     subscription-activation path (`subscriptions.py:1285`) and the
     `invoice.paid` webhook handler (`webhooks.py:1329`). Old tests supply a
     3-element `side_effect` list; the 4th call raises `StopAsyncIteration`.
     Sibling tests on the *recurring* branch (which skips this new code path)
     still pass, confirming the new calls are additive, not broken.
     **Fix:** add 2 more `find_one` mock responses (drivers row, service_areas
     row) to each affected `side_effect` list.
  4. **New guard clauses the old fixtures don't satisfy** — each deliberate,
     correct behavior:
     - `surge_engine.py:263` added a `surge_enabled` backstop; old
       `TestRecalculateAllSurges` fixtures omit that field so every area is
       skipped. Fix: add `"surge_enabled": True` to the fixtures.
     - `verify_subscription_session` now re-reads status after activation
       (a newer sibling test, `test_verify_session_superseded_returns_superseded`,
       already models this correctly) — the old test's mock returns a stale
       `"pending"` status via `return_value` instead of a `side_effect`
       sequence. Fix: match the newer sibling test's mocking pattern.
     - `_WSPubSub.active` (`utils/ws_pubsub.py:72-83`) now also requires
       `_pubsub is not None` (deliberate reconnect-safety fix, documented in
       its own docstring) — old test only stubs `_redis`/`_task`. Fix: also
       stub `_pubsub` in the test fixture.
- **Files:** `backend/tests/test_wallet.py`, `backend/tests/test_webhooks_main.py`,
  `backend/tests/test_spinr_pass_subscription.py`, `backend/tests/test_coverage_rides.py`,
  `backend/tests/test_drivers_extended.py`, `backend/tests/test_dispatch_cascade.py`,
  `backend/tests/test_dispatch_presence_failopen.py`, `backend/tests/test_utils_extended.py`,
  `backend/tests/test_websocket_token_revocation.py`, `backend/tests/test_p2_promo_wallet_loyalty.py`
  (start with bucket 1 above; a fresh `pytest -v` pass is still needed to confirm
  the exact full list — this breakdown was derived from a sample of 17 of the 156
  signatures plus a static `patch()`-target scan, not a full local run)
- **Approach:** fix bucket 1 first (pure patch-target sweep, no behavior
  questions, respects ≤3-files-per-subtask) to see how much it clears, then
  buckets 2–4 in order. Do not skip/xfail to turn CI green; fix or delete each
  test on its merits. Bucket 2 (`TestTransfer`) needs a product decision first
  — confirm wallet-to-wallet transfer is actually a dead/removed feature before
  deleting its tests, rather than assuming.
- **Acceptance:** ✅ met — `pytest` reports 0 failures on this branch (full
  suite: 4667 passed, 8 skipped, 1 xfailed); CI Guard Rails coverage gate is
  meaningful again once this merges to `main`.

### A5. PyJWT HIGH-severity CVE-2026-48526 (auth bypass) in backend image
- [x] **Status:** done — fixed via CR-2026-004, PR #2474 (merged 2026-07-27,
  squash sha `0026612`). PyJWT bumped 2.12.1→2.13.0 (part of a 13-package
  dependency bump also clearing the overlapping `G6 · Trivy container scan`
  findings). Full backend suite run before/after with byte-for-byte identical
  failure sets; targeted auth/JWT/MFA/OTP/token pass (542 tests) also
  unaffected. See `docs/change-log/2026-07-27-cr-2026-004-backend-dep-bump.md`.
- **Why:** `docker-image-scan` job flags `PyJWT==2.12.1` for `CVE-2026-48526`, an
  authentication-bypass-via-forged-JWT vulnerability, fixed in PyJWT `2.13.0`.
  Given CLAUDE.md's JWT trust model (admin JWTs are fully trusted on role/email/
  modules claims), an unpatched JWT-forgery CVE in the dependency stack is worth
  fixing ahead of its normal priority, independent of the specific PR that
  surfaced it (a docs-only change did not introduce this).
- **Files:** `backend/requirements.txt` (or `requirements.in`), regenerate
  `backend/requirements-locked.txt` via
  `pip-compile --generate-hashes --resolver=backtracking`
- **Approach:** bump PyJWT to `>=2.13.0`, regenerate the hash-locked requirements
  file per `docs/runbooks/dependency-update.md`, run the full auth test suite
  (`backend/tests/test_auth.py`, `test_admin_mfa_enforcement.py`,
  `test_admin_privilege_escalation.py`, `test_p3_admin_jwt_modules.py`) to confirm
  no behavior change, then re-run the Trivy image scan to confirm the finding clears.
- **Acceptance:** `docker-image-scan` job passes with 0 HIGH/CRITICAL findings for
  PyJWT; all auth tests still pass.

### A6. Flaky backend tests: `test_no_double_accept`, `test_ranks_by_vector_similarity_with_no_lexical_overlap`
- [x] **Status:** fixed (2026-07-27) — both tests rewritten to remove their
  timing/ordering assumptions; each passed 20/20 consecutive local runs after
  the fix (0/20 before, on the unmodified originals). PR: (branch
  `claude/a6-flaky-test-fixes`). Found while driving PR #2421 (A4 closure) to
  green; confirmed flaky via two `backend-test` CI runs on an identical commit
  producing different results (2 failed / 4665 passed, then 1 failed / 4666
  passed) plus local isolation runs. Neither test's file was touched by #2421
  — pre-existing flakiness, not a regression from that PR.
- **Why (root causes):**
  - `tests/test_rides.py::test_no_double_accept` raced two `asyncio.gather`
    coroutines against `patch("backend.routes.drivers._deps.db.update_one",
    AsyncMock(side_effect=[accepted_ride, None]))` — a fixed-order list. Real
    concurrent scheduling under `asyncio.gather` does not guarantee call order
    matches list order, so whichever coroutine's `update_one` call landed
    second could pull the `accepted_ride` slot meant for the "winner," or
    exhaust the list before both calls resolved, raising
    `StopAsyncIteration` and failing with `AttributeError:
    'StopAsyncIteration' object has no attribute 'status_code'`.
  - `tests/test_ai_tools_support.py::TestSearchFaqsSemantic::test_ranks_by_vector_similarity_with_no_lexical_overlap`
    persists freshly-embedded FAQ rows via a genuine fire-and-forget
    `asyncio.create_task` in `ai/tools_support.py::_schedule_persist` (never
    awaited by the caller — deliberate, so the user-facing tool call doesn't
    block on it). The test drained it with a fixed `await asyncio.sleep(0.05)`
    then asserted `update.assert_awaited()` — under CI scheduling load the
    background task could still be pending after 50ms, failing with
    `AssertionError: Expected mock to have been awaited`.
- **Fix:**
  - `test_no_double_accept`: replaced the fixed-order `side_effect` list with
    an `update_one`/`find_one` fake that models the real DB's "first caller
    whose conditional UPDATE still matches wins" semantics — an
    `asyncio.Lock`-guarded dict keyed by claim state, not call order. The test
    now exercises the actual race-handling logic instead of coincidental
    scheduling, and passes regardless of which driver's coroutine happens to
    run first.
  - `test_ranks_by_vector_similarity_with_no_lexical_overlap`: patched
    `tools_support.asyncio.create_task` to capture the task the production
    code creates, then `await`ed it directly after the tool call instead of
    sleeping — draining the background write is now deterministic, not
    time-based.
- **Files:** `backend/tests/test_rides.py`, `backend/tests/test_ai_tools_support.py`
- **Acceptance:** ✅ met — both tests pass 20/20 consecutive local runs. CI
  confirmation (≥3 consecutive green `backend-test` runs) pending merge.

### A7. 8 failing tests in `test_ai_tools_booking.py` on `main`
- [x] **Status:** done (2026-07-28) — actually 9 failing tests, not 8 (the
  count in this item's own text was stale). Root-caused to two distinct
  test-drift causes, both against genuinely-newer production code, not a
  production bug:
  1. **8 of 9** — a `_dropoff_pair_refusal` guard (shipped for the Walmart
     dropoff-label incident, correctly fails closed when Maps/Places is
     unavailable) now runs inside both `get_fare_quote` and
     `propose_ride_booking` and intercepts *before* the guard each test
     actually means to exercise (same-street, same-place, fare-quote,
     proposal). These tests mock Maps as unavailable to isolate an unrelated
     guard, which used to be safe — the new guard changed that. Fixed by
     bypassing it (`patch.object(tools_booking, "_dropoff_pair_refusal",
     AsyncMock(return_value=None))`), matching the pattern already
     established by sibling tests in the same file that predate this guard's
     addition.
  2. **1 of 9** (`test_proposal_reresolves_pickup_address_when_coords_are_stale`)
     — `_resolve_candidate_areas` (a batched service-area lookup added later
     as an optimization — "one read, not an N+1 loop" — for tagging geocode
     candidates) bypasses the older `_resolve_area` mock this one test still
     relied on exclusively. Fixed by also mocking
     `_resolve_candidate_areas`.
- **Files:** `backend/tests/test_ai_tools_booking.py` only — no production
  code changed; both guards are correct, newer behavior.
- **Acceptance:** ✅ met — `pytest tests/test_ai_tools_booking.py` reports
  72 passed, 0 failed. Full local backend suite re-run to confirm no
  regressions elsewhere.

### A8. Leaked un-awaited AsyncMock coroutines fail an arbitrary unrelated test under pytest 9
- [x] **Status:** done (2026-07-30). Fixed on branch `claude/a8-asyncmock-leak-fix`
  (5 commits). Diagnosed 2026-07-29 while verifying the admin driver search fix
  (`claude/admin-driver-search-design-ryh7yc`); not fixed there since the leaks
  were in test files that change didn't touch. Picked up as its own scoped task
  2026-07-30, re-diagnosed from scratch with `PYTHONTRACEMALLOC=5` (captures the
  real allocation site, not just the incidental GC-timing location the warning
  surfaces at) rather than trusting the original diagnosis's file list, which
  turned out not to match — see note below.
- **Symptom:** a full-suite run fails one test that passes in isolation, and
  *which* test fails changes between runs at the same commit. Observed (2026-07-29):
  `test_compliance_reports_http.py::test_knight_archer_report_filters_by_status`;
  observed again independently (2026-07-30) as
  `test_compliance_reports_http.py::test_driver_roster_filters_by_status` failing
  on PR #2903's `backend-test` CI run — same file, same root cause, different
  victim test, exactly matching the "any change to test count or ordering
  reshuffles the victim" mechanism below.
- **Root cause — two distinct bugs, not one:**
  1. **Mock-shape mismatch.** `tests/conftest.py`'s `autouse=True` fixture
     `mock_supabase_client` (applied to every test via `patch_external_dependencies`)
     set `.rpc` and `.table().execute` as `AsyncMock`, but production code always
     calls both **synchronously** — `repositories/_base.py`, `wallet_repo.py`,
     `ride_repo.py`, and `core/lifespan.py` all do
     `await run_sync(lambda: supabase.table(...).execute())`; the lambda body
     itself is never awaited. The mismatch created a coroutine every time
     synchronous code called the mocked method, which was then discarded
     un-awaited. Fixing this also surfaced a second, independent pre-existing bug
     it had been masking: the fixture's `.rpc` didn't model the real two-step
     `.rpc(name, params).execute()` chain (unlike `.table()`, and unlike ~10
     other test files that already correctly override
     `mock.rpc.return_value.execute.return_value = ...`) — `test_credit_happy_path`
     was silently relying on an earlier `AttributeError` from the async bug to
     produce its (test-tolerated) 503 path; once that async bug was fixed, the
     RPC chain's missing `.execute()` step surfaced as a real `decimal.InvalidOperation`
     crash instead. Fixed both together.
  2. **`spawn()`/`asyncio.create_task()` seam leaks.** Production fire-and-forget
     dispatch (`_deps.spawn(some_coroutine(...))`, used for push notifications,
     guest-booking notifications, quest progress, live-activity updates, batch
     offer-timeout scheduling) evaluates `some_coroutine(...)` to build the
     coroutine *before* `spawn()` — or, one layer deeper, `asyncio.create_task()`,
     the seam `spawn()`'s own docstring says tests are expected to intercept — ever
     runs. A bare `MagicMock()` standing in for either records the call and
     returns without ever awaiting or closing that coroutine argument. Real
     `asyncio.create_task()` takes ownership of it; the fix mirrors that by
     explicitly closing it (`tests/_factories.py:close_spawned_coro`, a shared
     `side_effect` helper — not `conftest.py`, since pytest loads conftest by
     file path and `from tests.conftest import X` doesn't work from other test
     modules, an existing convention already documented at the top of
     `_factories.py`).
  Both bugs independently produce the identical symptom (a leaked, un-awaited
  coroutine, warned about at an arbitrary later GC point), which is why they
  were investigated and fixed together as one item.
- **Why it matters:** this makes the suite an unreliable merge gate — a green
  run does not mean the leaks are gone, and a red run points at an innocent
  test. It also burns review time re-diagnosing the same thing each session.
- **Files actually fixed (verified via `PYTHONTRACEMALLOC=5`, not the original
  diagnosis's file list — see below):** `backend/tests/conftest.py`,
  `backend/tests/test_auth.py`, `backend/tests/test_offer_timeout.py`,
  `backend/tests/test_dispatch_metrics.py`,
  `backend/tests/test_dispatch_presence_failopen.py`,
  `backend/tests/test_company_guest_booking.py`,
  `backend/tests/test_corporate_company_bookings_coverage.py`,
  `backend/tests/test_corporate_company_bookings_routes.py`,
  `backend/tests/test_coverage_rides.py` (14 individual leak sites total across
  these 9 files), plus the new shared helper `backend/tests/_factories.py` and
  the enforcement rule in `backend/pytest.ini`.
- **Note on the original "known leak sources" list:** the 2026-07-29 diagnosis
  named `test_ride_accept_flow.py` + `test_drivers_extended.py` (7 warnings on
  an unmodified checkout) plus `test_e2e_ride_lifecycle.py` and
  `test_estimate_ghost_driver_filter.py`. None of these four appeared in this
  session's `PYTHONTRACEMALLOC=5` full-suite runs (multiple runs, zero
  ambiguity — tracemalloc reports the true allocation site, not the GC-timing
  victim). Given 3 consecutive full-suite runs at the current commit are
  completely clean (see Acceptance below), those four files' leaks were most
  likely already fixed independently between 2026-07-29 and this session — not
  re-verified against that specific commit range, but the current, actual state
  of `main` is confirmed clean regardless of how it got there.
- **Enforcement (the "regress silently" half of the original fix proposal) —
  also had a real bug, found and fixed.** The original proposal
  ("Add `-W error::RuntimeWarning`... once clean") doesn't work as literally
  written: the warning's actual category is
  `pytest.PytestUnraisableExceptionWarning`, not `RuntimeWarning` — the
  coroutine finalizer routes through `sys.unraisablehook`, which pytest's
  `unraisableexception` plugin wraps and re-emits; the embedded traceback text
  merely *mentions* "RuntimeWarning" inside the wrapped message. Confirmed with
  a disposable scratch test (`m = AsyncMock(); m()`, never awaited): under a
  bare `error::RuntimeWarning` filter it silently kept passing, 5/5 runs, no
  visible error at all. The correct filter also needs the inline `(?s)` DOTALL
  flag in its message regex — the wrapped message embeds a multi-line
  traceback before the "was never awaited" text, and `.` doesn't match
  newlines by default, so an otherwise-correct message pattern still silently
  never matches without it. Fixed filter (in `backend/pytest.ini`), reverified
  against the same scratch test: 5/5 runs correctly failed, each one correctly
  attributed to the actual leaking test rather than an arbitrary later one.
  Kept a second, narrower plain-`RuntimeWarning` rule too, since repeated runs
  of the identical scratch leak showed both delivery paths occur depending on
  GC timing on a given run — one rule alone isn't sufficient.
- **Acceptance:** ✅ met. Full backend suite produces zero
  "coroutine ... was never awaited" warnings (confirmed via
  `PYTHONTRACEMALLOC=5` runs during the fix, and via 3 consecutive default runs
  after), and 3 consecutive full-suite runs at the same final commit all show
  identical results — `5953 passed, 8 skipped, 1 xfailed, 5 warnings` (checked
  the content of all 5: one unrelated pre-existing `StarletteDeprecationWarning`
  about `httpx`/`starlette.testclient`, and four `InsecureKeyLengthWarning`
  from `test_auth.py`/`test_middleware_user_id.py` deliberately using
  short JWT test keys — none are leaks), exit code 0 each — and fail no tests.

### A25. Legacy driver/rider-migration audit (2026-08-11) — 3 P0 findings
- **Source:** `docs/audit/2026-08-11-driver-rider-migration-audit.md`
  (merged via #3662, not yet triaged into this file until now).
- **P0-C — rider CSV importer could silently overwrite PIPEDA-scrubbed PII
  on `pending_deletion`/`deleted` accounts.**
  - [x] **Status:** DONE (2026-08-11) — `rider_import_service.py` now reads
    `users.status` in `_prefetch_existing`/`build_plan` and skips (flags as
    `protected_skip`, no fields touched) any match whose status is
    `pending_deletion` or `deleted`, instead of repopulating falsy email/PII
    fields the way it did for a normal partial profile. Admin dashboard
    (Bulk Rider Import page) surfaces the new category with a distinct
    "Skipped — needs review" badge/stat. 2 new backend tests, full change
    impact log at `docs/change-log/2026-08-11-rider-import-pii-protection.md`.
    PR #3674.
- **P0-A — rider importer has no provenance trail.**
  - [x] **Status:** DONE — stale checkbox, corrected here. Already fixed in
    PR #3678 (same finding also listed as A28's P2-C, which already notes
    "no separate action needed"). Confirmed directly against
    `backend/services/rider_import_service.py`: every imported/updated
    rider row now sets `legacy_import_metadata` (see lines ~298-299,
    ~327). No code change needed; correcting this entry's own checkbox so
    it doesn't keep reading as open.
- **P0-B — 3 admin financial dashboards double-count legacy-imported ride
  earnings.**
  - [x] **Status:** DONE (2026-08-11) — new migrations
    `backend/migrations/302_ride_money_rollup_exclude_legacy.sql` and
    `backend/migrations/303_payouts_overview_ytd_exclude_legacy.sql` add a
    legacy-exclusion predicate to `admin_ride_money_rollup` (unconditional)
    and `admin_payouts_overview_aggregates`'s `ytd`/T4A CTE only
    (`earned_up_to_end`/`earned_up_to_prev`/`blocked_outstanding` stay
    unfiltered — they're paired with the offsetting `payouts` rows and are
    already arithmetically correct). The CSV export at
    `routes/admin/rides.py`'s `/earnings/rides` now drops legacy rides
    **post-fetch** via `drop_legacy_rides()`, not via a server-side filter —
    see A26 below for why. Full change impact log:
    `docs/change-log/2026-08-11-admin-legacy-earnings-exclusion.md`.
- P1/P2 findings from the same audit (3 P1, 4 P2) not yet triaged into this
  file — see the audit doc directly.

### A26. `EXCLUDE_LEGACY_RIDES` compiles to an unsatisfiable SQL predicate — was zeroing driver-facing earnings in production (CRITICAL, found 2026-08-11 while fixing A25/P0-B)
- [x] **Status:** DONE (2026-08-11) — confirmed live against production
  (`soavhtdhefowwvforzwb`, `ca-central-1`) via authorized Supabase MCP
  access, then fixed. A driver with 1 real, non-legacy $7.59 completed ride
  was returning 0 rows from the exact compiled filter — their
  `GET /drivers/balance` was reading `total_rides: 0`, `total_earnings:
  $0.00` despite having real, unpaid earnings. Added an explicit `$eq`
  operator to `repositories/_base.py`'s `_apply_filters` and changed
  `EXCLUDE_LEGACY_RIDES` to `{"legacy_import_metadata": {"$eq": {}}}` —
  single source of truth, fixes all 9+ call sites without touching them
  individually. Re-verified live against the same driver with the
  corrected predicate — now correctly returns their 1 real ride. Full
  backend suite: 11020 passed, 0 failed. Full Change Impact & Risk Log:
  `docs/change-log/2026-08-11-a26-exclude-legacy-rides-eq-fix.md`.
  <details><summary>Original finding (2026-08-11, pre-fix)</summary>

  Found by `spinr-migration-reviewer` while reviewing the P0-B migrations
  above; user directed: file it, don't fix broadly this session (no live
  Supabase access to verify safely at the time). Live Supabase access was
  authorized later the same session, enabling direct confirmation and fix.
- **What:** `utils/legacy_rides.py`'s
  `EXCLUDE_LEGACY_RIDES = {"legacy_import_metadata": None}` is merged
  directly into real `db_supabase.get_rows(...)` filter dicts at 9+ call
  sites. `repositories/_base.py`'s `_apply_filters` compiles a `None` value
  to `q.is_(k, "null")` → PostgREST `is.null` → real SQL `column IS NULL`.
  But `rides.legacy_import_metadata` (migration 268),
  `users.legacy_import_metadata` (256), and `drivers.legacy_import_metadata`
  (221) are all declared `NOT NULL DEFAULT '{}'::jsonb` — **no row in any of
  these tables can ever be SQL NULL** in that column, imported or not.
  `IS NULL` against a `NOT NULL` column matches **zero rows, always** — not
  "exclude legacy rides," but "exclude every row the filter touches."
- **Confirmed technically** (not yet confirmed live): traced
  `postgrest.base_request_builder.BaseFilterRequestBuilder.is_()` →
  `value = "null"` → `Filters.IS` → PostgREST `is.null` → Postgres
  `IS NULL`; cross-checked all three columns' DDL
  (`NOT NULL DEFAULT '{}'::jsonb`). If this reaches production traffic as
  analyzed, every query merging `EXCLUDE_LEGACY_RIDES` returns **zero rows**,
  not "zero legacy rows" — meaning `payable_balance`/`total_earnings` would
  compute as `$0` for every driver at those call sites, not just ones
  touched by legacy import.
- **Why not fixed here:** severity (driver payouts, live-tested surface) +
  the fact that a bug this size going unnoticed strains credulity — there
  may be a mitigating factor not visible from static analysis alone (e.g. a
  different code path in production, a compensating fallback, or the bug
  simply not being live yet). No live Supabase access in this sandbox to
  confirm actual behavior before touching money-critical driver-facing code.
- **Call sites to check/fix together** (all currently assume the filter
  works as a server-side exclusion):
  - `routes/drivers/earnings.py` — 7 places (driver balance + earnings
    endpoints)
  - `utils/driver_statement.py:216`
  - `utils/t4a_annual_job.py:194`
  - `routes/admin/drivers.py:2642`
- **What already avoids the bug:** the post-fetch companion
  `drop_legacy_rides()` (Python-truthy check on the fetched rows, not a
  DB-level filter) is unaffected and correct. A26's P0-B fix
  (`routes/admin/rides.py`'s `/earnings/rides` CSV export) deliberately uses
  `drop_legacy_rides()` instead of `EXCLUDE_LEGACY_RIDES` for this reason —
  see A25/P0-B above.
- **Suggested fix** (once live-verified): change
  `EXCLUDE_LEGACY_RIDES = {"legacy_import_metadata": None}` to something
  `_apply_filters` can actually compile against a `NOT NULL DEFAULT '{}'`
  column — e.g. add an `$eq` operator to `_SUPPORTED_FILTER_OPS` /
  `_build_or_clause_term` and use
  `{"legacy_import_metadata": {"$eq": {}}}`, or simplest: swap every
  `**EXCLUDE_LEGACY_RIDES`-in-a-filter call site to fetch normally and
  `drop_legacy_rides()` post-fetch (already proven safe, no `_apply_filters`
  change needed, but loses server-side row reduction on
  `limit=10000`-style calls).
- **First action for whoever picks this up:** confirm live in staging/prod
  whether `routes/drivers/earnings.py`'s balance/earnings endpoints are
  currently returning `0`/empty for drivers who have ANY completed ride
  (not just legacy-touched ones) — that's the smoking-gun symptom if this
  analysis is correct.
  </details>

### A27. Audit's 2 P1 findings (`docs/audit/2026-08-11-driver-rider-migration-audit.md`) — both CLOSED 2026-08-11
- **P1-A — `drivers.total_earnings` is dead code (fleet-wide admin stat
  always $0).**
  - [x] **Status:** DONE. `admin_get_driver_stats` and per-area breakdown
    now compute earnings live from completed, legacy-excluded rides (one
    batched query, not N+1). Also fixed a related gap found in the same
    pass: `admin_get_driver_live_stats` (per-driver "Earnings" header)
    lacked the legacy-ride exclusion its own "Payouts" tab already had —
    same driver, same screen, two different numbers (Phase 3 cross-surface
    finding #2 in the audit). Full Change Impact & Risk Log:
    `docs/change-log/2026-08-11-p1a-driver-earnings-dead-column.md`.
- **P1-B — legacy PST possibly folded silently into the fare line on
  imported historical receipts.**
  - [x] **Status:** CLOSED, but the investigation surfaced something bigger
    than the audit anticipated. Traced the *current* fare engine
    (`features.calculate_all_fees`) and found it carried a comment
    asserting "PST does NOT apply to rideshare" in Saskatchewan, with the
    live `service_areas` config for Saskatoon/Regina set `pst_enabled:
    false` — directly contradicting `.claude/context/regulatory-sk.md`'s
    documented rule ("PST (6%, SK) on fare where applicable — ride-share
    currently PST-applicable in SK"). **User confirmed PST does apply and
    the code was wrong** — Saskatoon/Regina were under-collecting PST on
    every live ride, not just legacy-imported ones. Per explicit user
    direction, enabled PST now in production (`pst_enabled=true,
    pst_rate=6` on the 4 real Saskatchewan `service_areas` rows only — not
    the unrelated `riyadh`/`riyadh airport` test rows), effective
    immediately for new fare quotes, no backdating of already-completed
    rides. Fixed the stale code comment; added the first direct unit tests
    for `calculate_all_fees`'s GST/PST/HST branches (none existed before).
    The original legacy-import question (was PST hidden in the imported
    receipts' fare line) is likely moot given PST wasn't being charged at
    all until this fix — no separate legacy-receipt remediation needed.
    Full Change Impact & Risk Log:
    `docs/change-log/2026-08-11-sk-pst-enable.md`.
  - **Not verified / left open**: whether historical remediation (crediting
    riders or drivers for the pre-fix under-collection period) is needed —
    explicitly out of scope per "no backdating" instruction; flag to
    Finance/Legal if that determination is still pending.
- P2 findings from the same audit: see A28 below, triaged 2026-08-12.

### A28. Audit's 4 P2 findings + P2-B (`docs/audit/2026-08-11-driver-rider-migration-audit.md`) — triaged 2026-08-12
- **P2-C — rider importer never writes `legacy_import_metadata`.**
  - [x] **Status:** already DONE — this is the exact same finding as P0-A
    (see A25 above), fixed in PR #3678. No separate action needed; noting
    the cross-reference here since the audit lists it under both P0 and P2
    numbering.
- **P2 — driver import validity gaps** (`driver_import_service.py`): VIN
  stored plaintext with no format/checksum check; email/phone accepted
  with no format validation; a document row can import with
  `status="approved"` and an already-past `expiry_date`.
  - [x] **Status:** DONE (2026-08-12) — fixed per the direction this entry
    itself scoped. `build_plan` now:
    1. Format-validates `phone` against the same `^\+1\d{10}$` shape
       `SendOTPRequest`/`VerifyOTPRequest` (`schemas.py`) require at
       signup, and `email` (when present — it's optional) against a
       permissive structural check (`^[^\s@]+@[^\s@]+\.[^\s@]+$`) — reject
       the row (`plan.errors`, skip) on either failure. Deliberately not
       full RFC 5322 email grammar: this is one-time CLI-operator CSV
       input, not a live user-facing form, so the bar is "catch a
       structurally broken value," matching this entry's own stated intent.
    2. Format/checksum-validates `vin` by reusing the existing
       `validators.validate_vin` (17-char ISO 3779 alphanumeric, I/O/Q
       excluded — the same helper `schemas.py` already used elsewhere for
       live vehicle registration, not a new checksum implementation) at
       **both** VIN write sites: the new-driver-insert path and the
       resumed-driver vehicle-update path (`vehicle_field_changes`'s
       `vin_plain`). A valid-but-differently-cased VIN is normalized
       (uppercased) rather than merely accepted, so downstream storage is
       consistent.
    3. Rejects a document row whose `status == "approved"` and
       `expiry_date` has already passed (`date.fromisoformat(expiry) <
       date.today()`) — a `status == "pending"` row with the same past
       date still imports (correct: it hasn't been approved yet, and the
       real runtime gate — `go_online`'s own expiry re-check,
       `routes/drivers/status.py:309-328` — still applies before a driver
       can go online either way). This stays defense-in-depth, exactly as
       this entry originally framed it, not a replacement for that gate.
    - **Files:** `backend/services/driver_import_service.py` (all three
      fixes); `backend/tests/test_driver_import_service.py` (6 new tests:
      malformed phone, malformed email, blank email is not an error, VIN
      format on new-driver insert incl. case-normalization, VIN format on
      resumed-driver update); `backend/tests/test_driver_import_service_coverage.py`
      (2 new tests: expired+approved document rejected, expired+pending
      document still allowed).
    - **Verification:** full existing suite for this module + its callers
      re-run clean: `test_driver_import_service.py` (23/23),
      `test_driver_import_service_coverage.py` (70/70 — includes the
      pre-existing `test_resume_updates_changed_vehicle_fields` /
      `test_resume_unchanged_vin_is_not_updated`, which already used real
      valid VINs and confirm the new VIN check doesn't reject legitimate
      values), `test_admin_driver_import.py` (9/9),
      `test_admin_drivers_coverage.py` (129/129) — 231/231 total, 0 failed.
      `services/driver_import_service.py` module coverage 98.51%
      (`coverage.xml`, measured directly — the aggregate `--cov=.` run's
      terminal table is too wide to grep the module's own row out of
      reliably). `python3 -c "import ast; ast.parse(...)"` clean after
      every edit.
    - **What was NOT verified:** not exercised against the real CLI script
      (`scripts/import_saskatoon_drivers.py`) or the admin HTTP upload
      flow end-to-end with a live Supabase — verified at the `build_plan`
      unit-test layer only (mocked/fake Supabase client), consistent with
      how every other validation rule in this file is tested. The
      "nullable-by-design, no `plan.errors` entry when blank" fields this
      entry also noted (`sgi_approved`/`work_authorization_status`/etc.)
      were confirmed intentional by the original audit and were not
      touched here.
  - Also noted: `sgi_approved`/`work_authorization_status`/
    `is_permanent_resident`/`is_citizen`/expiry dates/`decals_sent` are
    nullable-by-design with no `plan.errors` entry when blank — the audit
    flags this only to confirm it's the intended model (completeness
    enforced by downstream `status`/`is_verified` gating), not to request
    a change. No action needed unless product says otherwise.
- **Float-on-money in `routes/drivers/earnings.py`** (adjacent finding,
  flagged because it's inside the file the audit reviewed line-by-line
  anyway — a genuine CLAUDE.md Decimal-discipline violation, 4 call sites).
  - [x] **Status:** DONE (2026-08-12) — daily/weekly/monthly/comparison
    earnings aggregation now uses `_d()`/Decimal throughout instead of raw
    `float()` accumulation. Display-path only (no money movement), but
    brings the file into CLAUDE.md compliance. Full Change Impact & Risk
    Log: `docs/change-log/2026-08-12-driver-earnings-decimal-fix.md`.
- **`/balance` vs `/earnings` composition can diverge** (Phase 3
  cross-surface findings #6/#7): `/balance` sums fare components live and
  excludes `ride_incentive_claims` bonuses/cancellation fees that
  `/earnings` and driver statements include; `/earnings`-family endpoints
  trust the stored `driver_earnings` column directly. Undocumented as
  intentional or accidental.
  - [ ] **Status:** open — needs a product decision, not a blind code
    change. Reconciling the two compositions either way is a money-visible
    behavior change on a live-tested surface (driver balance/payout
    figures) and CLAUDE.md requires escalation when blast radius/intent is
    unclear on a surface like this. Flag to product/finance: should
    `payable_balance` include bonuses/cancellation fees (making it match
    `/earnings`), or is the current split deliberate (balance = withdrawable
    ride money only, earnings = full income picture)?
- **Admin "total rides" vs rider-app "total rides" use different
  definitions, unreconciled** (Phase 3 cross-surface finding #10): admin
  counts all-status lifetime rides; rider-app counts completed-only,
  period-scoped.
  - [ ] **Status:** open, low priority — the audit itself frames this as
    "by design," similar to the T4A-vs-earnings date-bucket difference
    (finding #8) which is already documented in code as intentional. Likely
    resolution is a one-line code comment on each definition rather than a
    behavior change, once product confirms both are meant to differ.
- **P2-B — no Change Impact Log exists for the driver or rider bulk-import
  paths themselves** (only booking-import and Stripe-mapping migration have
  runbooks/change-logs, despite both writing directly to `auth`/`users`/
  `drivers`).
  - [x] **Status:** DONE (2026-08-12) — backfilled both:
    `docs/change-log/2026-08-12-driver-import-service-backfill.md` and
    `docs/change-log/2026-08-12-rider-import-service-backfill.md`. Each
    documents the existing (already-shipped) validation rules, matching/
    resume-safety behavior, write order, blast radius (every other reader/
    writer of `users`/`drivers`/`driver_documents`/`legacy_import_metadata`
    found via grep), and — not previously written down anywhere — an
    operational rollback procedure for a bad batch. One asymmetry surfaced
    while writing the rider-import doc, flagged there rather than fixed
    here since it's out of scope for a docs-only pass: the rider importer's
    *updates* to already-existing users (fill-in-if-missing fields) are not
    cleanly revertible by batch tag the way newly-*created* rows are, since
    no pre-commit snapshot of the overwritten values is captured. Pure
    documentation, no code changed, nothing to test.

### A29. `spinr-regulatory-compliance-checker` follow-ups from the SK PST enablement (A27/#3723) — not previously filed
- **Correction note:** PR #3723's body said these 3 findings were "filed as
  `ACTION_ITEMS.md` follow-ups, not blocking this PR" — they were not
  actually added at the time. Filing them now (2026-08-12) to make that
  claim true.
- **No audit trail on the tax-rate admin endpoints themselves.** Two
  separate `PUT /areas/{area_id}/tax` endpoints exist
  (`features.py:722-744`'s `pricing_router` and
  `routes/admin/service_areas.py:841-856`), and neither writes an
  `audit_logs` row or requires a justification string for a tax-rate
  change — unlike the analogous surge-cap endpoint in `features.py`, which
  documents "no written-justification field... must not be a path to
  exceed the cap" and routes above-cap changes to an audited path. A
  tax-rate change carries real regulatory/financial weight (every rider's
  charge, CRA/SK remittance obligations) with zero admin-action audit
  trail today. Compounded in the actual 2026-08-11 PST-enablement event:
  the change was applied via direct Supabase access, bypassing both
  endpoints entirely — mitigated after the fact with 4 retroactive
  `audit_logs` rows (see A27/P1-B), but the underlying endpoints still
  have no audit requirement for the *next* tax-rate change made through
  the normal admin UI.
  - [x] **Status:** DONE (2026-08-12). Correction to this finding's own
    framing: neither dedicated `/areas/{id}/tax` endpoint is actually
    reachable from any frontend — grepped every `.tsx` file across
    `admin-dashboard`/`rider-app`/`driver-app`/`shared`, zero callers of
    either path. The admin-dashboard's real tax editor is the
    service-areas page's inline field editor, which goes through
    `PUT /api/admin/service-areas/{area_id}` (`admin_update_service_area`)
    — that endpoint already wrote a generic `service_area_updated` audit
    entry when tax fields changed, so the live path was not actually
    silent, just not held to the surge-cap's written-justification
    discipline. Fixed: `admin_update_service_area` now requires a
    `tax_justification` when any GST/PST/HST field is present (400 if
    missing) and writes a dedicated `tax_config_updated` audit entry,
    mirroring `surge_override_above_cap` exactly. The admin-dashboard's
    `handleFieldUpdate` now prompts for the justification (same
    `window.prompt` convention as the existing corporate-wallet-adjustment
    reason prompt) before sending a tax-field edit. Both dead-but-named
    endpoints (`features.py::update_area_tax`,
    `routes/admin/service_areas.py::admin_update_area_tax`) were hardened
    identically for consistency/future-proofing, not because they carry
    live traffic. 8 new tests (4 in `test_admin_service_areas_coverage.py`,
    3 in `test_features.py`'s new `TestAreaTaxJustification`); full suite
    for the affected files re-run clean (69/69). Real `npm run build` in
    `admin-dashboard` passed (not just `tsc`/dev server) per CLAUDE.md's
    requirement. Full Change Impact & Risk Log:
    `docs/change-log/2026-08-12-a29-tax-config-audit-justification.md`.
    **2026-08-12 addendum:** a parallel session independently reached the
    same fix for both dead endpoints (PR #3773) — closed as superseded by
    the above once discovered, since this fix is strictly more complete
    (it also fixed the actually-live `admin_update_service_area` path and
    the admin-dashboard UI, which #3773 didn't touch). One genuine gap
    #3773 found that this fix didn't cover: `admin_update_area_tax`
    (`routes/admin/service_areas.py`) 500s with an unhandled
    `AttributeError` instead of a 404 when `area_id` doesn't exist —
    `area` resolves to `None` and the final `{k: area.get(k) ...}` crashes;
    `features.py`'s sibling endpoint already guards this correctly. Fixed
    separately; see `docs/change-log/2026-08-12-a29-tax-endpoint-404-fix.md`.
- **`corporate_statement_pdf.py` GST/PST fallback risk.** Falls back to a
  single combined "Tax (GST/PST)" line (lines 93-98) whenever
  `tax_by_type` is empty — e.g. a statement period mixing pre-/post-PST-
  cutover rides where no ride happens to have a populated breakdown, or
  any future tax type the aggregator doesn't yet bucket. Low risk today
  since `_aggregate_rows` in `routes/corporate_company.py` already buckets
  by label, but the fallback path exists and would violate the
  separate-line-items rule (regulatory-sk.md) if ever hit for a period
  with real GST+PST both present.
  - [x] **Status:** done — kept the combined-line fallback (removing it and
    raising would deny a corporate customer their invoice over an edge
    case, which CLAUDE.md's "do not silently swallow errors" section treats
    as a worse outcome than a logged degrade path) but made it loud instead
    of silent. `generate_corporate_statement_pdf`
    (`utils/corporate_statement_pdf.py`) now calls a new
    `_log_combined_tax_fallback()` at the fallback site: no-ops when
    `tax_total` is genuinely zero (nothing to itemize, not a regulatory
    risk), otherwise emits `logger.error(...)` with company id / statement
    month / tax_total / the raw `tax_by_type` value, plus a best-effort
    `sentry_sdk.capture_message(..., tags={"domain": "corporate",
    "surface": "backend"})` (mirrors `services/ledger_service.py::escalate`'s
    no-op-if-unconfigured pattern). Rendered PDF output is unchanged in
    every case. 3 new tests in `test_corporate_statement_pdf.py` (zero-tax
    stays silent, nonzero-tax logs with expected context, `tax_by_type` key
    entirely absent also logs); full file re-run 12/12 pass, plus the
    8/8-passing route-level `test_corporate_statement_pdf_routes.py`
    (mocks the generator, so it exercises the two PDF-download routes
    end-to-end without hitting this fallback branch). **Not verified:**
    against a real Supabase/live corporate account, or that a Sentry event
    actually lands (delivery not observed, only the no-op-safe call path).
    Full Change Impact & Risk Log:
    `docs/change-log/2026-08-12-a29-gst-pst-fallback-hardening.md`.
- **No `service_area_tax_history` (or equivalent) audit table.** The PST
  enablement's only queryable-in-DB trace is the 4 retroactive `audit_logs`
  rows (A27); there's no dedicated append-only table capturing
  rate/enabled transitions over time the way `driver_insurance_periods`
  does for insurance periods. If SK/CRA ever audits "when exactly did
  Spinr start collecting PST," the only source of truth is those
  `audit_logs` rows plus the change-log markdown.
  - [ ] **Status:** open, low priority — `audit_logs` already covers the
    "what changed and why" need; a dedicated history table would only add
    value for high-volume tax-config churn, which this isn't yet. Revisit
    if tax-rate changes become more frequent.

### A30. Migrated-data visibility audit (2026-08-13) — Finding 0 resolved live, 3 findings remain open
- **Source:** `docs/audit/2026-08-13-migrated-data-visibility-audit.md`
  (follow-up to A25-A28, this time auditing whether legacy-imported data
  actually *renders* on rider/driver/admin screens, not just whether the
  underlying money math is correct).
- **Finding 0 — was the legacy ride importer ever actually committed to
  production?**
  - [x] **Status:** CLOSED (2026-08-13), live-verified via Supabase MCP
    against `soavhtdhefowwvforzwb`. **Yes** — 224 legacy rides exist in
    `rides` (`legacy_import_metadata->>'source' = 'legacy_mongo_booking_import'`),
    all `status='completed'`, matching the documented CSV-scope count
    exactly. The 2026-07-29 change-log's "not run against live Supabase"
    note was stale/undocumented, not an accurate description of current
    production state — the commit did happen, no follow-up doc ever
    recorded it. Two sample rides spot-checked end-to-end (rider/driver
    accounts active, offsetting `legacy_import` payout rows present and
    correctly amounted) — the visibility mechanism works correctly for
    matched records.
  - **Follow-up (not yet done):** backfill a short change-log note
    recording when/how the real production commit happened, since nothing
    in the repo currently documents it.
- **Finding 1 — real phone-match rate.**
  - [x] **Status:** measured (2026-08-13, live query). **100% of legacy
    rides have a matched rider** (0/224 NULL), **94.2% have a matched
    driver** (211/224, 13 NULL affecting 4 distinct riders — those riders
    see the ride, just without driver details). Downgraded from the
    original audit's P1 ("needs a live query to size") — this is a good
    real-world result, not an open risk. No action needed unless the 4
    affected riders specifically report an issue.
- **Finding 2 — two admin-dashboard detail panels (rider "Recent rides",
  10-row cap; driver "Rides" tab, 50-row cap, no pagination params sent)
  silently drop older imported rides with no "more exists" signal.**
  - [x] **Status:** DONE (2026-08-13). `routes/admin/drivers.py`'s
    `admin_get_driver_rides` now returns an accurate `total_count` via
    `count_documents()` alongside the existing fetch-capped `total`
    (additive field, both callers of the endpoint unaffected).
    `getDriverRides()` now requests the backend's own max (500) instead of
    its 50-row default. The rider "Recent rides" panel
    (`routes/admin/users.py`) already returned an accurate `total_rides`;
    both panels now show a "Showing N of Total — view all" note (rider
    panel) / "Showing the 500 most recent of N total rides" note (driver
    tab) only when the cap is actually hit, so the common case (every
    account checked live during this audit has well under 500 rides)
    renders no new UI. The rider panel's "view all" link deep-links
    `/dashboard/rides?search=<term>`, which needed the rides list page's
    `search` state to read from the URL on its very first render (a lazy
    `useState` initializer, not a second effect — the existing mount
    effect would otherwise fire its first fetch before a separate
    effect's `setSearch` took hold).
- **Finding 3 — driver-app earnings totals correctly exclude legacy-ride
  dollars (by design, avoids double-counting old-app payouts) with no
  on-screen explanation for the resulting trip-count-vs-earnings mismatch.**
  - [x] **Status:** DONE (2026-08-13). `ActivityView.tsx` now shows a
    one-line note above the earnings breakdown ("N ride(s) from your
    previous account are shown below but not counted here — those were
    already paid out") whenever the currently period/status-filtered
    trip list contains at least one legacy-imported ride. Computed
    client-side from data already in memory (`rideHistory`, which
    `/drivers/rides/history` already returns in full) — no new fetch, no
    backend change.
- **Finding 4 — no screen in rider-app, driver-app, or admin-dashboard
  visually marks a ride as "imported from the previous app" (one admin
  driver-summary stat card excepted).**
  - [x] **Status:** DONE (2026-08-13). Added a small "Imported" (rider-app:
    "Imported from your previous account") badge to a ride card/row
    wherever `legacy_import_metadata` is a non-empty object: rider-app
    Activity tab, driver-app trip history, admin main rides list, admin
    rider-detail "Recent rides" panel, admin driver-detail "Rides" tab.
    The rider-detail panel needed one backend change first — its column
    allowlist (`_DETAIL_RIDE_COLUMNS`, `routes/admin/users.py`) excluded
    `legacy_import_metadata` — everywhere else already returned the full
    row (`select("*")` is the `get_rows()` default). All other surfaces
    are frontend-only.
- **Verification across all three (2026-08-13):** backend — 2 tests
  (`test_admin_extended.py`: `count_documents` patched on the existing
  driver-rides test, new test pins `total` vs `total_count` divergence
  beyond the fetch cap). driver-app — 2 new tests in
  `ActivityView.test.tsx` (badge + explainer present for a legacy ride,
  both absent for a normal ride); full file 8/8 pass. rider-app/driver-app
  — `tsc --noEmit` clean on both. admin-dashboard — `tsc --noEmit` clean
  and a real `npm run build` (not just dev server, per CLAUDE.md) run
  twice, once after each of the two admin-dashboard commits, both clean.
  No test infra exists for any of the touched admin-dashboard page-level
  components (`rides/page.tsx`, `ride-list.tsx`, `users/page.tsx`,
  `drivers/page.tsx`) — consistent with the rest of this codebase's
  page.tsx files, not a gap introduced here.
- **What was NOT verified:** none of this was checked against the actual
  rendered screens (no screenshot/visual tooling in this repo, flagged
  generally elsewhere in this file); the rider-panel's `?search=` deep
  link was verified by reading the resulting fetch's options object, not
  by loading the admin dashboard and clicking through it end to end;
  whether any of the 4 riders or higher-ride-count accounts identified in
  Finding 1/2's live query actually see the new UI correctly in
  production (only 2 low-ride-count sample accounts were spot-checked
  live, both well under every cap these fixes address).

### A31. `GET /drivers/earnings` zeroed trip-count/distance/duration stats for drivers whose period rides are all legacy-imported (2026-08-13)
- **Source:** live user report against a real migrated driver's Activity
  screen ("All Time" showed `Total Earned $0.00` / `0 Total Trips` /
  `0.0 KM Driven` / `0h Online Time` directly above a rendered list of 17
  real completed rides).
- **Root cause:** `get_driver_earnings` (`backend/routes/drivers/earnings.py`)
  summed `total_rides`/`total_distance_km`/`total_duration_minutes` from the
  same `EXCLUDE_LEGACY_RIDES`-filtered rides list used for money totals.
  Those three fields aren't money — `utils/legacy_rides.py`'s own docstring
  says the exclusion "only governs money math" and imported rides "remain
  fully visible in ride history" — so a driver with zero non-legacy
  completed rides in the period got zeroed activity stats alongside the
  (correctly) zeroed earnings.
- [x] **Status:** DONE (2026-08-13). Added a second, unfiltered
  "all completed rides in period" query; the three activity fields now
  source from it. `average_per_ride` still divides by the money-rides count
  (now explicit, not reused from `total_rides`) so it isn't diluted by
  $0-earning legacy trips. 2 new regression tests
  (`test_earnings_coverage.py::TestGetDriverEarningsLegacyActivityStats`).
  Full Change Impact Log at
  `docs/change-log/2026-08-13-driver-earnings-legacy-activity-stats.md`.
- **Follow-up (not yet done):** `GET /drivers/balance`
  (`get_driver_balance`, same file) has the identical `total_rides =
  len(rides)` pattern on its own legacy-excluded query — same latent bug,
  left unfixed here because its `total_rides` response field has no
  frontend consumer today (`DriverBalance` TS type in
  `driver-app/store/driverStore.ts` doesn't include it). Fix opportunistically
  if/when a frontend surface starts reading `/balance`'s `total_rides`, or
  proactively for consistency — low priority since no one currently sees
  the wrong number.
- **Superseded by A32:** the note above about `average_per_ride` staying
  divided by the money-rides count "so it isn't diluted by $0-earning
  legacy trips" described the state as of this entry's date. A32
  (2026-08-13, same day) reversed that specific call: Avg per Trip is now
  a deliberately blended `total_earnings / total_rides`, per an explicit
  product decision that trip-count and dollar figures should use the same
  denominator everywhere. See A32.

### A32. Blended lifetime earnings — previous-app money is now permanent and unbadged on driver/rider surfaces (2026-08-13)
- **Source:** direct product decision, same conversation as A31. A31 fixed
  trip/distance/duration stats reading zero for all-legacy periods; the
  user asked the natural follow-on question — if a driver's total money
  earned and average per trip still exclude real, already-paid previous-app
  money, and read a technical "legacy"/"imported" framing, is that the
  smartest way to present it? Decision: no. Blend it into one honest
  number, drop all "legacy"/"imported" language from rider/driver-facing
  UI, keep the distinction in the backend and admin portal only.
- **Reverses two prior decisions, both explicitly superseded here:**
  - **A30 Finding 3/4** (2026-08-13, earlier same day) shipped a "N rides
    from your previous account are shown below but not counted here"
    explainer and an "Imported"/"Imported from your previous account"
    ride-card badge on rider-app, driver-app, and admin-dashboard. Now
    **removed from rider-app and driver-app** (no more badge, no more
    explainer — money is blended, so the explainer's premise is false).
    **Kept on admin-dashboard** — Spinr staff need the distinction for
    support/audit; that surface was never customer-facing.
  - **The `PREVIOUS_APP_VISIBLE_UNTIL` sunset** (`utils/legacy_rides.py`,
    2026-08-31, introduced 2026-08-12/13 as deliberate transition
    messaging with an end date) is **retired** for all three call sites
    that used it (`get_driver_balance`, `get_payout_history`,
    `build_statement`). Hiding a driver's own previous-app money on a
    calendar date would make their lifetime total look like it shrank —
    the same trust problem A31 fixed for trip counts, now closed for the
    dollar figure too. The helper function itself is untouched (still
    correct, just unused by these three call sites).
- [x] **Status:** DONE (2026-08-13).
  - **Backend:** `get_driver_balance`/`get_payout_history`/`build_statement`
    no longer gate on `previous_app_history_visible()` — previous-app
    money and payout rows are always included. No new fields; existing
    `previous_app_paid_total` (`/drivers/balance`) is now unconditional.
  - **driver-app Activity screen:** "All Time" Total Earned blends Spinr
    earnings + `previous_app_paid_total` (not blended for Today/Week/Month
    — no reliable per-period split for old-app transfer dates). New
    "Previously Paid" breakdown row makes the blend a visible, additive
    line item (every row sums to the total) rather than a footnote. Avg
    per Trip is now a simple `total_earnings / total_rides` over the same
    blended total and the already-all-inclusive trip count (A31). New "Avg
    Distance/Trip" stat tile. Removed the ride-card badge and the "not
    counted here" explainer.
  - **driver-app Payout screen:** "Total Earnings" breakdown item now
    blends the same way, so the figure matches the Activity screen instead
    of showing two different "Total Earnings" numbers on two screens.
    "Previously Paid" added as a 4th breakdown item (additive — only
    rendered when non-zero, so a driver with none sees the original
    3-item row unchanged). `AVAILABLE BALANCE` (the real withdrawable
    figure) is untouched — blending only ever happens in *display*
    totals, never in `payable_balance` math.
  - **driver-app Payout History screen:** "Previous app" section's copy no
    longer promises an Aug 31, 2026 cutoff or says the money "isn't part
    of your Spinr earnings" (both now false).
  - **rider-app Activity tab:** badge removed. No total/average change —
    riders don't have an earnings-exclusion figure to blend.
  - Full Change Impact Log:
    `docs/change-log/2026-08-13-blended-lifetime-earnings.md`.
- **Verification:** backend — `test_previous_app_sunset.py` rewritten to
  pin "always visible" instead of the two-branch cutoff (5/5 pass); full
  affected-file run (`test_previous_app_sunset.py`,
  `test_earnings_coverage.py`, `test_drivers_extended.py`,
  `test_payouts_coverage.py`, `test_driver_statement.py`,
  `test_driver_statement_pdf.py`) — 193/193 pass. driver-app —
  `ActivityView.test.tsx` updated (badge-removal + blended-total/average
  regression tests), 9/9 pass; `tsc --noEmit` clean. rider-app — `tsc
  --noEmit` clean (no test infra for `activity.tsx`, consistent with the
  rest of this app's screen-level files).
- **What was NOT verified:** not exercised against real Supabase or a real
  migrated driver account — reasoned from the code and unit-mocked tests
  only. No screenshot/visual verification (standing gap, no
  visual-regression tooling in this repo). `payout.tsx`/`payout-history.tsx`
  have no existing test file to extend (consistent with the rest of this
  app's screen-level files, not a gap introduced here) — verified by
  `tsc --noEmit` and manual code review only.
- **Follow-up, found live the same day (A33):** A32's blend added
  `driverBalance.previous_app_paid_total` (a Stripe-payout-ledger sum) on
  top of Spinr-only earnings. A real migrated driver testing the shipped
  fix still saw `Total Earned $0.00` / `Fare $0.00` / `Avg per Trip $0.00`
  under a correctly-populated `Total Trips` / `KM Driven` / `Online Time` —
  because that driver has real legacy RIDES but an incomplete previous-app
  payout backfill, so the ledger-derived figure was 0. See A33.

### A33. `get_driver_earnings`'s money math still legacy-excluded — A32's payout-ledger blend under-covered (2026-08-13, same day as A32)
- **Source:** live user report against the just-shipped A32 fix (see A32's
  follow-up note above) — the same migrated driver still saw all-zero
  dollar figures.
- **Root cause:** A32 blended `driverBalance.previous_app_paid_total`
  (sum of `payouts` rows with `payout_type='stripe_sync'` — a record of
  Stripe *transfers*) into the Activity screen's Total Earned. That figure
  depends on the previous-app payout-history backfill having a row for
  every driver's legacy earnings. `get_driver_earnings` itself still
  filtered its OWN money query with `EXCLUDE_LEGACY_RIDES`
  (`utils/legacy_rides.py`) — the same pattern A31 had already fixed for
  `total_rides`/`total_distance_km`/`total_duration_minutes`, left
  unfixed for money because A30 Finding 3 deliberately excluded legacy
  money by design at the time. A32 then tried to patch that exclusion back
  in from a different, less-complete source (the payout ledger) instead of
  removing it at the actual source (the ride query).
- [x] **Status:** DONE (2026-08-13). `get_driver_earnings` now sources
  BOTH activity stats and money (Fare/Tips/Bonus/Referral/Tax/Total
  Earned/Avg per Trip) from a single unfiltered ride query — the same
  `all_completed_rides` list A31 already introduced for activity stats,
  reused instead of duplicated. Each ride carries its own real
  `ride_completed_at`, so this stays correctly sliced per calendar period
  (no precision fabricated to make it work) and doesn't depend on the
  payout-ledger backfill's completeness. New `elapsed_days` field
  (fixed for today/week/month, measured from the earliest ride for "all")
  backs three new driver-app stat tiles: Avg Trips/Day, Avg KM/Day, and
  Online Time split into Total + Avg/Day (all requested directly by the
  user alongside this fix).
  - `driver-app` `ActivityView.tsx`: reads the now-blended
    `total_earnings`/`average_per_ride` directly; removed the
    client-side `previous_app_paid_total` addition (would now
    double-count) and the "Previously Paid" breakdown row it fed (now
    redundant — Fare/Tips/Bonus/Tax already include legacy money).
  - `get_driver_balance`/`payable_balance` (the Payout screen's
    withdrawable-balance math): **untouched** — still legacy-excluded by
    design, since that money was already paid out by the old app and must
    never be double-countable as withdrawable. This fix only ever affects
    the `/drivers/earnings` DISPLAY endpoint.
  - 2 tests in `TestGetDriverEarningsLegacyActivityStats` rewritten for
    the new blended-money behavior (full file: 42/42 pass); affected-file
    backend run 193/193 pass. `ActivityView.test.tsx` updated for the
    backend-driven blend and new tiles, 8/8 pass. `tsc --noEmit` clean.
  - Full Change Impact Log:
    `docs/change-log/2026-08-13-blended-earnings-money-inclusion-fix.md`.
- **What was NOT verified:** same boundary as A32 — not exercised against
  real Supabase or a real migrated driver account, no visual verification.

### A34. Legacy-imported ride count dropped 224 → 186 in production — CLOSED (2026-08-16), explained as intentional pre-launch test-account cleanup; two residual follow-ups spun off separately
- **Source:** dual-run cutover audit (Phase 0.4/0.4-followup), 2026-08-16. A30
  (2026-08-13, live-verified) recorded **224** rows for
  `legacy_import_metadata->>'source' = 'legacy_mongo_booking_import'`. The
  identical query, run live again 2026-08-16, returns **186** — a 38-row
  drop, confirmed real (not a filter/measurement difference — same result
  via both the old `!= '{}'` filter and the exact-source filter; not
  soft-deleted (`deleted_at IS NULL` on all 186 remaining, and the missing
  rows aren't hidden, they're gone); single import batch
  (`20260729184745`) both before and after, so no re-import/dedup ran;
  single Supabase project, zero branches; no application code path in this
  repo ever deletes from `rides`; zero `rides`-deletion rows in the app's
  own `audit_logs`).
- **Mechanism found (2026-08-16, via `pg_stat_statements`, not
  `postgres_logs`):** Postgres statement logging on this project only
  captures DDL (`CREATE`/`ALTER`/`COMMENT`) — confirmed by exhaustively
  sweeping `postgres_logs` across 2026-08-13 through 08-16 and finding
  **zero** plain `DELETE`/`UPDATE`/`INSERT` statements anywhere, from any
  source, despite `audit_logs` showing hundreds of real app-level ride
  writes in the same window. This means ordinary DML — from the app, a
  direct `psql` connection, or the Supabase dashboard SQL editor — is
  structurally invisible to `postgres_logs` on this project regardless of
  when it ran. `pg_stat_statements` (a separate, always-on catalog,
  `stats_reset` 2026-05-22) was the tool that actually found it:
  - A hand-written, phone-number-scoped account-deletion script (`DO $$
    ... v_phones TEXT[] ... `) exists and was run for real
    (`p_dry_run := false`) **twice on 2026-08-14** (`stats_since` timestamps
    20:13:03 and 20:38:25 UTC — squarely inside the 08-13→08-16 gap
    window), targeting 4 phone numbers total (1 in the first run, 3 in the
    second). A third, dry-run-only call against the same script exists too.
  - The script is a comprehensive right-to-delete/DSAR-style hard-delete:
    resolves `users` by phone → `drivers` → **`rides` by
    `driver_id`/`rider_id` match** → 16 groups of dependent tables → ends
    with `DELETE FROM rides WHERE id = ANY(v_ride_ids)`, then `drivers`,
    then `users`. It disables the append-only guard triggers on
    `driver_insurance_periods`, `driver_period_distances`, `disputes`, and
    `audit_logs` to do this (see separate flag below — this conflicts with
    this repo's own documented 7-year regulatory retention policy).
  - Confirmed against `bookings.csv`/`drivers.csv`/`customers.csv`: 3 of
    the 4 targeted phone numbers (`3062929175`, `3066009097`, `3065203304`
    in local 10-digit form) appear **repeatedly** in the legacy MongoDB
    export as both driver and customer records — several clearly test
    accounts ("Test YK", "Yy", "Hh", "Test Y") alongside apparently-real
    names ("Kiran", "Tristan", "Yash Kumar", "Ryan D"). Since the legacy
    importer links legacy bookings to real Spinr accounts by phone match
    (A30: 100% rider / 94.2% driver match rate), any of these phone
    numbers' Spinr accounts that had a legacy-imported ride attached would
    have had that ride swept up by this script's
    `rides.driver_id/rider_id`-based deletion — this is a coherent,
    well-evidenced explanation for some or all of the 38-row gap.
  - A **separate, distinct** script was also found in `pg_stat_statements`:
    an unconditional environment-wipe (`DELETE FROM rides`/`drivers`/
    non-admin `users` with no `WHERE`, same guard-trigger-disable pattern).
    Ruled out as the cause of *this* gap — its `stats_since` is
    2026-07-16, predating the 07-29 legacy import batch, and a wholesale
    wipe after 07-29 would have left either 0 rows or a new batch tag, not
    186 rows all carrying the *original* import batch id. Almost certainly
    a pre-import "clean the environment" step, not implicated here — but
    its mere existence (see flag below) is a standing risk independent of
    this finding.
- **Status: CLOSED (2026-08-16), explained by the repo owner.** Confirmed:
  the 4 targeted phone numbers were intentional test accounts ("Kiran",
  "Vikas", "Yash", "testy") deleted manually ahead of go-live while
  validating driver/rider reporting and activity-stats screens — the same
  validation pass that surfaced the `payout_gst_amount` import gap
  (`docs/change-log/2026-08-15-legacy-import-gst-preservation.md`). This
  matches the evidence exactly: "Kiran" and "Yash"/"Yash Kumar" were
  literally the names attached to two of the four phone numbers in the
  legacy CSV, and the test-labeled entries ("Test YK", "Yy", "Hh", "Test Y")
  cover "testy". **Not a bug, not unexplained data loss** — intentional
  pre-launch cleanup that happened to also remove legacy-imported rides
  linked to those test accounts' phone numbers via the importer's own
  phone-match logic. No further action needed on the row-count question
  itself; every legacy-migration figure produced after 2026-08-14 (i.e.
  everything in this session) reflects the post-cleanup state and does not
  need re-verification against the earlier 224 baseline.
- **Two residual items spun off separately** (both process/tooling gaps
  surfaced *by* this investigation, independent of the cleanup itself being
  legitimate) — see **A35** and **A36** below.
- **Why this matters beyond the row count itself:** any "live-verified"
  dollar figure or row count in this repo's audit docs (including A30,
  A31, A32, A33, and every figure in this session's own Phase 0 report) is
  a snapshot that can silently change with **no durable record of why** —
  `postgres_logs` does not capture DML on this project, so only
  `pg_stat_statements` (aggregate query shapes, no row-level detail, no
  actor identity) offers any forensic trail at all, and only for as long as
  `stats_reset` hasn't fired again.
- **What was NOT independently re-verified after the owner's confirmation:**
  the exact 38 ride IDs affected (not recoverable — the deleted
  `users`/`drivers` rows are gone, and `RAISE NOTICE` output isn't captured
  at this project's log verbosity); whether all 4 phone numbers' accounts
  specifically had a legacy-imported ride attached (only circumstantial
  CSV-presence evidence). Neither blocks closure — the mechanism, actor
  intent, and timing are all now independently corroborated.

### A35. Ad-hoc account-deletion scripts bypass the documented 7-year `driver_insurance_periods` retention policy
- **Source:** surfaced while investigating A34 (2026-08-16), not itself
  part of A34's original question.
- **Issue:** the phone-scoped test-account cleanup script found in
  `pg_stat_statements` (see A34) disables this repo's append-only
  regulatory guard triggers (`driver_insurance_periods_no_mutate`,
  `financial_events`'s delete gate, `audit_logs_no_delete`) in order to
  hard-delete `driver_insurance_periods` rows for the matched driver(s).
  CLAUDE.md's own PIPEDA section and `docs/runbooks/data-retention.md`
  both state insurance-period transitions must be retained for the full
  7-year SGI regulatory window *regardless* of a deletion request — the
  documented, sanctioned Step H DSAR process (`purge_pii_retention()`)
  enforces this by construction: it explicitly refuses to touch any
  account that has `rides`, `driver_insurance_periods`, `payouts`, or
  `bank_accounts` rows at all. This ad-hoc script does the opposite —
  it's not a variant of the sanctioned process, it's a separate,
  hand-written tool that never had that guard in the first place.
- **Confirmed benign in this instance** — A34's cleanup targeted test
  accounts, not real regulatory-covered drivers — but the script itself
  would do the same thing to a real driver's insurance-period history if
  reused for an actual DSAR request.
- [x] **Status:** fixed (2026-08-17). Two-part fix, both reviewed
  (`spinr-migration-reviewer` + `spinr-regulatory-compliance-checker`,
  verdicts: safe to merge / adequate partial fix — see
  `docs/change-log/2026-08-17-a35-retention-guard-monitor.md`):
  1. **Detection**: `check_disabled_guard_triggers()` (migration 317, read-only,
     `service_role`-only) + a new 6-hourly background loop
     (`backend/utils/retention_guard_monitor.py`) that alerts (CRITICAL log +
     Sentry `fatal` + one `audit_logs` row) if any `%_no_mutate`/`%_no_delete`
     guard trigger (plus the named legacy exception `audit_logs_no_update`) is
     found disabled. Never auto-remediates. Wired into both the spawn list
     and the loop watchdog's tracked-name list from day one.
  2. **Sanctioned replacement**: `backend/services/test_account_cleanup_service.py`
     — dry-run-only plan builder (no delete path built), buckets every
     phone-matched account into `safe_to_delete` / `blocked_regulated_data_present`
     using Step H's eligibility guard **plus** a check Step H itself is
     missing (see **A38** below).
  - **Honestly-stated, NOT closed by this fix**: the detection loop is a
    point-in-time poll — it cannot see a disable→mutate→re-enable cycle
    completed within one session (the actual shape of the 2026-08-14
    incident), at any polling cadence. Closing that needs a synchronous
    `ddl_command_end` event trigger, deliberately **not** built in this
    change (database-wide blast radius on every `ALTER TABLE` statement,
    untestable against live Postgres from this session) — spun off as
    **A37**.
  - **What was NOT verified:** whether this script has ever been run against
    an account that *did* have real `driver_insurance_periods` history —
    A34 only confirmed the 2026-08-14 runs were test accounts. Migration 317
    itself has **not been applied to production** in this session (repo
    convention is `scripts/migrate.py`, not ad-hoc application) — until it
    is, the new loop logs an RPC-not-found error every 6h (never a false
    positive/negative, verified by test) rather than actually detecting
    anything.

### A37. Real-time DDL detection for regulatory guard triggers (event trigger) — CLOSED (2026-08-17)
- **Source:** surfaced by `spinr-regulatory-compliance-checker`'s review of
  the A35 fix (2026-08-17) — see
  `docs/change-log/2026-08-17-a35-retention-guard-monitor.md`.
- **Issue:** A35's fix polls trigger state every 6h. Polling, at any
  cadence, cannot observe a disable → mutate/delete → re-enable cycle
  completed *within* a single `psql`/dashboard session — exactly the shape
  the 2026-08-14 incident actually had. The only mechanism that closes this
  is synchronous: a `CREATE EVENT TRIGGER ... ON ddl_command_end` that
  writes an append-only row the moment `ALTER TABLE ... {DIS,EN}ABLE
  TRIGGER` fires on a regulated table, inspecting
  `pg_event_trigger_ddl_commands()` inside the trigger function to filter to
  our guarded tables.
- **Why not built alongside A35:** an event trigger fires database-wide for
  *every* `ALTER TABLE` statement in the project, not just the guarded ones
  — a bug in its body (a raised exception, an unhandled edge case) risks
  breaking every future migration repo-wide, on a live-tested production
  system, with no way to test the function against a real Postgres instance
  from this session first. Per CLAUDE.md's "escalate, don't silently ship,
  when in doubt" rule — this needs a deliberate, tested, separately-reviewed
  PR, not a bundled addition to a same-day fix.
- **Fix:** migration 318 adds `guard_trigger_ddl_audit`, a
  `ddl_command_end` event trigger scoped to `WHEN TAG IN ('ALTER TABLE')`,
  that re-runs migration 317's `check_disabled_guard_triggers()` at the
  instant any `ALTER TABLE` finishes and writes an append-only
  `regulatory_guard_trigger_disabled_realtime` `audit_logs` row if a guard is
  found disabled at that moment. `_audit_guard_trigger_ddl()`'s entire body
  is wrapped in `EXCEPTION WHEN OTHERS THEN RAISE WARNING` so no internal bug
  can escape and block the triggering DDL.
  `backend/utils/retention_guard_monitor.py` was extended with
  `_fetch_realtime_events()`, merging these rows into the same
  per-`(table, trigger)` dedupe/escalation path as the existing 6h RPC poll —
  so a same-session disable/re-enable now pages within one poll cycle
  (previously: never), while the permanent record is instantaneous. Detail:
  `docs/change-log/2026-08-17-a37-guard-trigger-ddl-realtime-audit.md`.
- **Both original blockers addressed, not asserted away:** verified against
  a real, isolated Supabase database branch (not a mock) — unrelated
  `ALTER TABLE` succeeds cleanly with the trigger installed; disabling a
  guard produces an immediate correct audit row; re-enabling produces no
  error/no duplicate; and with `check_disabled_guard_triggers()` deliberately
  dropped to force an internal failure, a subsequent `ALTER TABLE` still
  succeeded — proving the exception-swallowing holds under real failure, not
  just in code review. See the change-log's §9 for the exact branch id and
  command sequence.
- **Status:** closed. Detect-only, as scoped — never re-enables a trigger,
  never blocks the DDL that disabled one; actual paging stays the existing
  6h loop's job (true sub-6h paging directly from SQL, e.g. `pg_notify` +
  a listener, is a reasonable future enhancement, deliberately not built
  here to avoid adding a new always-on process dependency to a
  database-wide DDL hook).

### A38. `purge_pii_retention()` Step H never checks `rides.driver_id` for a driver account — CLOSED (2026-08-17)
- **Source:** surfaced by `spinr-regulatory-compliance-checker`'s review of
  the A35 fix (2026-08-17) — the new `test_account_cleanup_service.py`
  deliberately added a `rides.driver_id` check that Step H itself lacks
  (see `backend/migrations/296_pipeda_30day_profile_scrub.sql`'s Step H
  body: it checks `driver_insurance_periods`/`payouts`/`bank_accounts` for a
  driver account, and `rides.rider_id` for any account, but never
  `rides.driver_id`).
- **Issue:** a driver account with completed ride history but, for some
  reason, no `driver_insurance_periods`/`payouts`/`bank_accounts` rows would
  currently pass Step H's own eligibility guard and be hard-deleted at the
  7-year mark despite having ride history — the sanctioned DSAR process has
  the same class of gap A35 found in the ad-hoc script, just narrower and
  less likely to be hit.
- **Not fixed as part of A35** — that fix made its own replacement tool
  stricter than Step H rather than attempting to also patch Step H's SQL,
  since Step H is money/regulatory-adjacent production code that changes
  hard-delete behavior and deserves its own dedicated review, not a
  drive-by edit inside an unrelated fix.
- **Fix (migration 321):** re-issues `CREATE OR REPLACE FUNCTION
  purge_pii_retention()` (same `migration-override-ok` pattern migration
  296 already established for this function) adding `EXISTS (SELECT 1 FROM
  rides r2 WHERE r2.driver_id = d.id)` to Step H's driver-side guard, in
  both the live-delete loop's `WHERE` clause and the dry-run `COUNT` query.
  Verified by diffing the full function body against migration 296's
  original: the ONLY substantive differences are the Step H comment and the
  two added `EXISTS` clauses — every other step (A–G, I–N), the result
  JSON, `REVOKE`/`GRANT`, and the audit-log insert are byte-identical.
  Strictly more conservative direction — can only exclude MORE accounts
  from hard-delete, never fewer; cannot retroactively affect any account
  already deleted under the old guard.
- **Manual review** (Codex auto-review off, C7/C9): `spinr-migration-
  reviewer` found one blocker (the migration was initially numbered 319,
  which two other PRs claimed on `main` while this branch was in progress
  — renamed to 321, the actual next-free slot, and every self-reference
  updated) — fixed. `spinr-regulatory-compliance-checker`: **SAFE TO
  MERGE**, confirmed the fix correctly serves the SK Transportation Act's
  7-year retention rule with no DSAR-stuck-forever risk (same convergence
  behavior as the pre-existing rider-side guard, via Step B's unconditional
  7-year ride purge).
- **Status:** closed. Full detail:
  `docs/change-log/2026-08-17-a38-step-h-driver-rides-guard.md`.

### A39. Two competing migration runners; the one CLAUDE.md documents (`migrate.py`) does not match production's actual `schema_migrations` schema — CLOSED (2026-08-17), docs corrected
- **Source:** found while manually applying migration 317 (the A35 fix) to
  production, since `python scripts/migrate.py` couldn't be run in this
  session (no `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`/`DATABASE_URL` in
  the shell) and applying it directly surfaced the mismatch.
- **Issue:** `backend/scripts/` contains **two** independent migration
  runners with **two different** `schema_migrations` table shapes:
  - `migrate.py` — expects `schema_migrations(version TEXT PRIMARY KEY,
    applied_at)`, defined by `backend/migrations/00_schema_migrations_table.sql`.
    This is the one `CLAUDE.md`'s "Database Migrations" section (and
    `backend/migrations/CLAUDE.md`, and `AGENTS.md`) documented as
    canonical.
  - `run_migrations.py` — expects `schema_migrations(filename TEXT PRIMARY
    KEY, checksum, applied_at, applied_by)`, defined by
    `backend/migrations/24_schema_migrations.sql` (an April 2026
    production-readiness fix, P0-B4, that added checksum verification and
    fixed a real duplicate-prefix ordering bug — see that migration's own
    top comment).
  - **Confirmed live** (`select table_schema, column_name ... from
    information_schema.columns where table_name='schema_migrations'`):
    production's actual table has `filename`/`checksum`/`applied_at`/
    `applied_by` — migration 24's shape, i.e. `run_migrations.py`'s.
    Existing rows are stamped `applied_by='backfill-verified'`, a bootstrap
    trace confirming `run_migrations.py` (or an equivalent manual process
    using its schema) is what's actually been used against production.
  - **`migrate.py` would fail immediately if run against production
    today** — `INSERT INTO schema_migrations (version) VALUES (%s)` against
    a table with no `version` column raises `column "version" does not
    exist`. No evidence it has ever successfully tracked a migration
    against the live database.
  - Confirmed via `.github/workflows/`: **CI never invokes either script**
    to actually apply migrations (`migration-check.yml` only lints
    filenames/format) — migrations have always been applied manually or via
    a one-off process, which is how this drift went unnoticed.
- **Fix:** corrected the documented command in `CLAUDE.md`,
  `backend/migrations/CLAUDE.md`, `AGENTS.md`, and
  `docs/runbooks/migration-conflict-detection.md` to
  `python -m backend.scripts.run_migrations`, with a note explaining why
  `migrate.py` is wrong.
- **Follow-up decision (2026-08-17, same day, product owner confirmed):**
  reconcile, not just delete — `run_migrations.py` had a real gap
  `migrate.py` didn't (no `CREATE/DROP INDEX CONCURRENTLY` support at all;
  it wraps every migration in one transaction, which Postgres rejects for
  `CONCURRENTLY`), and `migrate.py`'s CONCURRENTLY-safe statement splitter
  (`ACTION_ITEMS.md` B0) was real, tested, working code. Ported
  `_split_sql_statements` and the autocommit-routing logic from `migrate.py`
  into `run_migrations.py` (`_apply_one_autocommit`), moved/adapted both
  regression test files (`test_migration_concurrently_splitting.py`,
  `test_run_migrations_autocommit_chunks.py`, née
  `test_migrate_autocommit_chunks.py`) to test the ported functions, fixed
  every other living-doc/CI/code reference (`CLAUDE.md` ×2 blocks,
  `AGENTS.md` ×2 blocks, `docs/dev-setup.md`, the migration-conflict-
  detection runbook, `.github/workflows/migration-check.yml`'s comments,
  `.claude/commands/migration-check.md`, and a real runtime error message
  in `routes/admin/auto_payouts.py` that told an admin to run the deleted
  script), then deleted `backend/scripts/migrate.py` outright. Historical
  audit/change-log documents that mention `migrate.py` were deliberately
  left untouched — point-in-time records, not living docs (same convention
  followed throughout this session). 128 directly affected tests pass
  (`test_migration_concurrently_splitting.py`,
  `test_run_migrations_autocommit_chunks.py`,
  `test_financial_events_ride_id_fk_contract.py`,
  `test_unbalanced_scoped_migration.py`, `test_auto_payout.py`,
  `test_migration_ordering.py`, `test_migration_fk_column_types.py`).
- **What was NOT verified:** whether any deploy pipeline outside
  `.github/workflows/` (e.g. a Fly/Railway post-deploy hook, a manual
  runbook step not checked in this repo) invokes `migrate.py` specifically
  — only this repo's own CI config was checked.
- **Pre-merge reviewer finding, same day:** `spinr-migration-reviewer`
  (run per the standing "wait for the review, then open the PR"
  instruction) confirmed the ported runner code itself was correct,
  faithfully tested, and safe, but found the "fixed every other living
  reference" claim above was not actually complete — a full-repo grep
  turned up 11 more files still referencing `migrate.py`:
  `backend/requirements.in`, `backend/.coveragerc`, two
  `verify_migrations_*.sql` scripts, `docs/runbooks/admin-rollback.md`,
  `docs/runbooks/supabase-region-migration.md`,
  `docs/runbooks/deploy-migration-64-65.md`,
  `docs/runbooks/deploy-migration-297.md`, `.planning/ROADMAP.md`,
  `.planning/REQUIREMENTS.md`, `docs/driver-faqs-saskatchewan.md`.
  Verdict: "FIX BEFORE MERGE (non-blocking to production safety, blocking
  to the PR's own completeness claim)." 9 of the 11 are living docs and
  are now fixed — three of those fixes (`supabase-region-migration.md`,
  `deploy-migration-64-65.md`, `deploy-migration-297.md`) were substantive,
  not just a script-name swap: the original text also documented
  `migrate.py`'s `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` connection
  interface, which is wrong for `run_migrations.py` (needs `DATABASE_URL`).
  The other 2 (`verify_migrations_286_291.sql`, `verify_migrations_292_293.sql`)
  were deliberately left alone — the latter's own header comment states
  editing it would break the provenance of a result cited in
  `docs/change-log/2026-08-08-migration-verification-result.md`, i.e. a
  self-declared point-in-time record, same convention as the historical
  docs already excluded above. One more file the reviewer's list didn't
  catch (`docs/runbooks/stripe-identity-drift-manual-test.md`) was found
  and fixed in an independent final sweep. `.planning/graphs/graph.json`
  and `.last-build-snapshot.json` also reference `migrate.py` dozens of
  times but are auto-generated Graphify build outputs per `AGENTS.md`, not
  hand-authored docs — correctly left untouched.

### A36. `financial_events` is 0 rows in production despite active use by 42 files — CLOSED (2026-08-17), root cause found: neither hypothesis was right

- **Source:** surfaced while investigating A34 (2026-08-16), not itself
  part of A34's original question.
- **Issue:** `select count(*) from financial_events` returns **0** in
  production, despite the table being read/written by `webhooks.py`,
  `ledger_service.py`, `payment_service.py`, `stripe_reconcile.py`,
  `reconciliation.py`, `payment_retry.py`, and 36 other files per a repo
  grep — this table is the 7-year CRA/SOC2 money ledger
  (`backend/migrations/58_financial_events.sql`), not a dead/unused
  feature. A 0-row count on an actively-integrated ledger table in a
  system with confirmed real Stripe transaction activity (per this
  session's Phase 0.0/0.1 Stripe cross-check) is unexpected.
- **Root cause, established 2026-08-17 with hard evidence (both original
  hypotheses ruled out):**
  1. `services/ledger_service.py`'s `record_event` (the only writer of
     `financial_events`) is called from exactly two places:
     `payment_service.record_payment_event`/`record_refund_event` —
     **both only invoked from inside `routes/webhooks.py`'s
     `if ride_id:` branches**, where `ride_id = meta.get("ride_id")` comes
     from the incoming Stripe event's `metadata.ride_id` field
     (`routes/webhooks.py:747`, `:854`). No other code path ever calls
     either function.
  2. Every completed, card-paid ride in production is legacy-imported:
     `select count(*) from rides where status='completed' and
     payment_method in ('card','stripe')` → **186/186** carry
     `legacy_import_metadata->>'source' = 'legacy_mongo_booking_import'`.
     Legacy imports are bulk `INSERT`s from `booking_import_service.py` —
     they never touch the live Stripe webhook path and correctly never
     write `financial_events` (a different, deliberate mechanism handles
     their money bookkeeping — the `legacy_import`/`stripe_sync` payout
     types, see A31-A33).
  3. **Only 2 native (non-legacy) rides exist in all of production**
     (`select count(*) from rides where legacy_import_metadata->>'source'
     is distinct from 'legacy_mongo_booking_import'` → 2), both
     `status='cancelled'`, `payment_status='pending'` — **never charged.**
     No native Spinr ride has ever reached `status='completed'` with a
     real Stripe payment in this production database.
  4. Yet `stripe_events` has **1,232 real webhook deliveries** since
     2026-06-16, including 117 `payment_intent.succeeded` / 178
     `charge.succeeded` — genuine Stripe activity confirmed by inspecting
     actual payloads. Every one inspected carries
     `metadata = {"type": "card", "booking": "<24-hex ObjectId>",
     "user_id": "<24-hex ObjectId>"}` — the OLD app's MongoDB-shaped
     metadata contract, not the new app's `{"ride_id": "<uuid>",
     "user_id": "<uuid>"}` contract. **The old app is still live and
     processing real customer payments on the same Stripe account**, as
     recently as 2026-08-15 (the day before this investigation started).
  5. Because these events carry no `ride_id`, `routes/webhooks.py`'s
     `if ride_id:` branch — the only call site of `record_payment_event` —
     never executes for them. They fall through harmlessly to a
     catch-all `mark_stripe_event_processed()` (no retry storm), with one
     minor, low-severity side effect: the `if user_id:` push-notification
     branch (`routes/webhooks.py:877`) still fires
     `send_push_notification(user_id=<mongo_objectid>, ...)` for these —
     wrapped in `try/except`, silently swallowed, wasted work only, not a
     correctness bug (not fixed here, low priority — see Files below if
     picked up later).
- **Conclusion: neither original hypothesis was correct.** Not "wiped by
  the environment-wipe script" (that script's `stats_since` predates the
  first legacy import and, per the evidence above, there was never a real
  row to wipe from this specific table). Not "write path broken" — the
  write path (`ledger_service.py`'s retry + Sentry-escalation logic) is
  sound by inspection and has simply never been invoked with real traffic,
  because no native ride has ever completed a real payment yet. **A
  0-row `financial_events` table is the CORRECT, accurate state for a
  system that has not yet processed a single real completed ride** — not
  a bug.
- **New finding, more important than the original question:** the old app
  is confirmed, via live webhook payload evidence (not inference), to
  still be issuing real Stripe charges on the same Stripe account as of
  2 days before this investigation. This sharpens (with concrete evidence)
  the existing `docs/audit/2026-08-15-dual-run-cutover/P0-critical-
  money-and-regulatory.md` finding #2's "the old app is still running and
  we can't see inside it" into "confirmed still running, here is exactly
  what its webhook traffic looks like." Spun off as **A40** below.
- **Files reviewed (no code changed — this was a pure investigation):**
  `backend/services/ledger_service.py`, `backend/services/payment_service.py`,
  `backend/routes/webhooks.py` (lines 666-889 specifically).
- **What was NOT verified:** whether the old app's webhook traffic is
  expected/intentional for the current phase of the migration (a business
  question, not a code question) or whether it should be re-pointed away
  from the shared endpoint; whether any OTHER event type (beyond
  `payment_intent.succeeded`) silently no-ops the same way for old-app
  traffic — only `payment_intent.succeeded` payloads were inspected
  directly.

### A40. Old app confirmed still live, issuing real Stripe charges on the shared Stripe account (webhook-payload evidence, 2026-08-17) — CLOSED, dual-run confirmed intentional

- **Source:** surfaced while closing A36 (above) — inspecting real
  `stripe_events` payloads to explain why `financial_events` was empty.
- **Issue:** live production `stripe_events` (1,232 rows since
  2026-06-16) contains `payment_intent.succeeded` (117) and
  `charge.succeeded` (178) events whose `metadata` is shaped
  `{"type": "card", "booking": "<24-hex Mongo ObjectId>", "user_id":
  "<24-hex Mongo ObjectId>"}` — the OLD app's metadata contract, landing
  on THIS (new app's) Stripe webhook endpoint. Most recent observed:
  2026-08-15, real amounts (e.g. $9.95, $6.00, $4.46, $20.27 CAD),
  real `booking` references. This is concrete confirmation — not
  inference — that the old app is currently processing real customer
  payments on the same Stripe account the new app uses, and that events
  from both apps are being delivered to a shared webhook receiver.
- **Why this matters:** `docs/audit/2026-08-15-dual-run-cutover/P0-
  critical-money-and-regulatory.md` finding #2 already flagged the
  *structural* risk ("the new system has no idea the old app exists...
  possibly the *same* Stripe accounts the old app pays into") but framed
  it as unconfirmed ("we can't see inside it"). This finding closes that
  gap with direct evidence: the shared-account risk is not hypothetical,
  it is actively happening, today, and has been for at least the 2 months
  `stripe_events` has been capturing webhook deliveries (2026-06-16
  onward — possibly longer; that's just when this table's retention
  starts).
- **Current handling is safe, not silently wrong:** `routes/webhooks.py`
  correctly no-ops these events for ride/payment purposes (no `ride_id`
  in metadata → no ride update, no `financial_events` write, no double
  processing) — see A36. The one minor side effect (a wasted, silently
  swallowed push-notification attempt against an old-app user_id) is
  low-severity and not itself a data-integrity risk.
- **Open questions this raises:**
  1. Is the old app's continued live Stripe activity expected/sanctioned
     for the current migration phase, or should it have stopped by now?
     **CLOSED (2026-08-17) — confirmed by the product owner: dual-run is
     intentional right now.** Both apps coexisting and processing real
     payments during this transition period is the expected state, not an
     incident.
  2. Are OLD-app Connect/payout-side webhook events (`transfer.created`,
     `account.updated`, etc. — also present in the `stripe_events` type
     breakdown) similarly falling through unprocessed, or could any of
     those interact with NEW-app driver Connect accounts if a driver's
     Stripe account is shared across both apps (per P0 finding #2's "104
     of them already have Stripe payout accounts on file — possibly the
     *same* Stripe accounts the old app pays into")?
     **CLOSED (2026-08-17), reassuring answer:** `transfer.created` has
     **no handler at all** in `routes/webhooks.py` — it falls to the
     generic unhandled branch regardless of account, structurally inert.
     `account.updated` **is** looked up by connected-account ID
     (`services/stripe_kyc_sync.py::apply_account_update`, matched against
     `drivers.stripe_account_id`) — the one event type that genuinely
     could cross-match if an account were shared. Checked live: **zero**
     of the 15 `account.updated` events observed in `stripe_events` match
     any current driver's `stripe_account_id` **or**
     `stripe_account_id_superseded`. No evidence the shared-account risk
     has actually manifested at the Connect/webhook layer, even though it
     remains structurally possible per the original P0 finding. (Syncing
     `account.updated` regardless of which app triggered it would in any
     case be *correct* behavior if it ever did match — Stripe Connect
     account state is account-level truth, not app-level, so mirroring it
     from either app's trigger is not itself a bug.)
  3. Should the webhook endpoint eventually be split (old app → its own
     endpoint) before the Oct 31 decommission, or does it not matter
     since old-app events are already inert here? **Downgraded to a
     low-priority nice-to-have, not closed** — now that #1 confirms
     dual-run is intentional and #2 confirms current handling is safe
     (no Connect-account collision observed, ride/payment events correctly
     no-op), splitting the endpoint is cleanup/hygiene rather than a risk
     mitigation. Worth doing before the Oct 31 decommission so the old
     app's traffic naturally stops arriving here rather than needing to be
     manually confirmed silent, but not urgent.
- **Status:** CLOSED. All three questions resolved: #1 confirmed
  intentional by the product owner (2026-08-17); #2 closed with live
  evidence (no Connect-account overlap observed); #3 downgraded to a
  low-priority hygiene item, not a risk. No code change was needed —
  `routes/webhooks.py`'s existing metadata-gated handling was already
  correct for this traffic shape.
- **What was NOT verified:** whether this predates 2026-06-16
  (`stripe_events`'s observed floor, not necessarily when old-app traffic
  started); event types other than `payment_intent.succeeded`,
  `transfer.created`, and `account.updated` were not individually traced
  (though the general pattern — ride/booking-scoped handlers gate on
  `metadata.ride_id`, Connect-scoped handlers gate on `stripe_account_id`
  match — covers the two structurally distinct risk shapes).

## P1 — Fix before launch (code)

### B0. Migration runner shreds any migration whose text contains "CONCURRENTLY"
- **Update (2026-08-17, A39 follow-up):** `scripts/migrate.py` has been
  **deleted** (see A39 above — it targeted a `schema_migrations` shape that
  was never actually applied to production). This fix's code —
  `_split_sql_statements` and the autocommit-routing logic — was ported
  into `scripts/run_migrations.py` (the canonical runner) **before**
  `migrate.py` was deleted, so nothing was lost; `run_migrations.py` never
  had this fix applied to it directly until now, since it never had any
  CONCURRENTLY handling at all before this port (a real, separate gap this
  same follow-up closed). The description below is preserved as it was
  written against `migrate.py` at the time; read `scripts/run_migrations.py`
  and `backend/tests/test_migration_concurrently_splitting.py` /
  `backend/tests/test_run_migrations_autocommit_chunks.py` for the current,
  live location of this logic.
- [x] **Status:** done — `scripts/migrate.py` now has `_split_sql_statements`,
  a lexical scanner (comment/`'...'`-string/`$tag$...$tag$`-dollar-quote
  aware) replacing the naive `sql.split(";")`. `needs_autocommit` routing now
  checks the comment-free split statements instead of raw text, so a file
  whose only "CONCURRENTLY" is inside a comment correctly runs through the
  normal transactional path instead of the no-transaction autocommit path.
  Validated against all 44 CONCURRENTLY-mentioning migrations in the repo —
  zero produce a non-SQL-looking fragment (was 34 broken before the fix); 14
  of them are now correctly reclassified out of the autocommit path.
  `_KNOWN_UNSPLITTABLE` in `test_migration_concurrently_splitting.py` is now
  empty (kept as a frozenset, not deleted, so a future regression has
  somewhere obvious to record a real unsplittable file). Two new direct
  regression tests pin the original failure modes (mid-line semicolon inside
  a comment; a `$$`-quoted function body) plus one pinning the
  comment-only-CONCURRENTLY routing fix. `test_migrate_autocommit_chunks.py`
  updated for `_apply_migration_autocommit`'s new signature (takes
  pre-split `statements: list[str]` instead of raw `sql: str`, since the
  split now happens once in `apply_migration` rather than being redone
  inside the autocommit path). All 51 tests in both files pass. **Not yet
  verified:** an actual fresh-database `python scripts/migrate.py` run
  end-to-end against a real Postgres instance — this fix was validated by
  running the real splitter against every migration file's text and by unit
  tests with a mocked connection, not by applying the full migration set to
  a live throwaway schema.
- **Files:** `backend/scripts/migrate.py:195` (`_apply_migration_autocommit`),
  frozen list in `backend/tests/test_migration_concurrently_splitting.py`
  (`_KNOWN_UNSPLITTABLE`)
- **Problem:** `apply_migration` routes a file to the autocommit path when the
  string `CONCURRENTLY` appears **anywhere in the text, including a comment**.
  That path cannot use a transaction, so it does `sql.split(";")` and executes
  each chunk after stripping *leading* `--` lines. Two things break:
  1. A **mid-line semicolon in a prose comment** (`-- ... hot table; a plain
     build blocks ...`) splits inside the comment; the rest of that line becomes
     the first line of the next chunk, is not a comment, and is handed to
     Postgres as SQL. Migration 55 has exactly this — the runner would try to
     execute `safe to remove anytime if the planner regresses.`
  2. A **`$$`-quoted function body** is shredded at every semicolon inside
     `BEGIN … END`. Migrations like `196_wallet_apply_credit.sql` contain no
     concurrent index at all — they are only routed here because the word
     `CONCURRENTLY` appears in their rollback comment.
- **Why it has not bitten yet:** these migrations are recorded as applied, so
  they are never re-run. It bites the next time one is applied to a fresh
  environment (new staging project, disaster-recovery rebuild, a fresh Supabase
  project for a new province) — where it fails partway through, after earlier
  statements have already committed under autocommit.
- **Approach:** replace the naive `split(";")` with a splitter that skips
  semicolons inside `--` comments, `'…'` literals, and `$tag$…$tag$` bodies; and
  detect CONCURRENTLY from executable SQL rather than raw text so function-body
  migrations keep running in a single transaction. Then delete
  `_KNOWN_UNSPLITTABLE` — the test already asserts the property for every file
  not in it.
- **Acceptance:** `_KNOWN_UNSPLITTABLE` is empty and
  `test_migration_concurrently_splitting.py` passes over every migration;
  a fresh-database apply of the full migration set succeeds end to end.

### B1. `track_driver_online` accepts raw GPS for third-party analytics
- [x] **Status:** done — geohash-string-only signature; lat/lng dict raises
  TypeError, non-geohash string raises ValueError; contract pinned in
  `backend/tests/test_analytics_geohash.py`
- **Files:** `backend/utils/analytics.py:346`
- **Approach:** change the signature to accept a geohash string only; never accept
  or forward a lat/lng dict to Mixpanel/Amplitude. Add a test pinning the contract.
- **Acceptance:** no analytics interface accepts raw coordinates; test added.

### B2. Disputes store full legal names + RLS too broad + rounding
- [x] **Status:** done (2026-07-28) — investigated all 3 sub-issues. All 3
  turned out to already be fixed by prior migrations before this session
  started; only a narrow defense-in-depth gap remained. **Correction partway
  through**: an initial draft of the RLS fix assumed migration 10's
  `FOR ALL TO authenticated` policy was still live and tried to replace it —
  a `spinr-migration-reviewer` subagent review caught that this was stale
  (migration 142 already superseded it) before merge; see the migration's
  own header for the full correction narrative.
  1. **RLS too broad — already fixed by migration 142, months before this
     session.** `142_fix_rls_financial_tables.sql` dropped migration 10's
     `FOR ALL TO authenticated` policy and replaced it with SELECT-only
     policies (`"Admin read disputes"` role-checked, `"Rider read own
     disputes"` own-row), revoking all INSERT/UPDATE/DELETE/TRUNCATE grants
     from `authenticated`. The one gap it left: `service_role` still
     bypasses RLS by design (correct — backend needs INSERT/UPDATE) and
     nothing blocked a `service_role` DELETE. `backend/migrations/262_disputes_rls_lockdown.sql`
     closes exactly that gap with a `BEFORE DELETE` trigger blocking
     deletion for every role including `service_role` (pattern: `audit_logs`,
     migration 51) — it does not touch 142's RLS policies/grants at all.
     Confirmed via grep that no live code path deletes disputes — the one
     `delete_many("disputes", ...)` call (`routes/admin/support.py`) is dead
     code, never imported/mounted by `server.py` or `features.py`.
  2. **Refund-cent rounding — already fixed, no action needed.**
     `admin_resolve_dispute` (`routes/disputes.py:219`) uses
     `dollars_to_cents()`, which does proper Decimal HALF_UP conversion, not
     bare `int()` truncation. Covered by `backend/tests/test_dispute_refund_cents.py`.
     The backlog text describing this as still-broken was stale.
  3. **Full legal name in admin response — already fixed by migration 142,
     months before this session.** Migration 142 §3 already scrubbed the
     `disputes.user_name` column (`UPDATE disputes SET user_name = ''`) and
     its own comment states the backend "no longer writes this column" —
     the admin list endpoint enriches the display name at read time from
     `users` instead (PIPEDA data minimization, already done). My first pass
     at this investigation only grepped current application code and missed
     migration 142 entirely, incorrectly concluding the column was merely
     "dead but harmless" and that no fix was needed — the fix had already
     shipped. Corrected once the migration reviewer's findings surfaced the
     full picture.
- **Files:** `backend/migrations/262_disputes_rls_lockdown.sql` (new, DELETE-block
  trigger only — see file header for the full correction from its first draft)
- **Acceptance:** ✅ met — refund cents already use proper rounding; disputes
  RLS was already locked to SELECT-only by migration 142; the full-name PII
  scrub was already done by migration 142; DELETE on disputes is now blocked
  at the DB level for every role including `service_role`.

### B3. Driver location-update hot path (perf + Maps spend)
- [x] **Status:** done — branch `claude/eager-franklin-69ta0w` (3 commits + completion-flush fix)
- **Files:** `backend/routes/websocket.py`, `backend/utils/breadcrumbs.py`,
  `backend/utils/maps_eta.py`, `backend/utils/breadcrumb_buffer.py`
- **Done:**
  1. `resolve_active_rides_cached` — Redis 5s TTL, empty results cached, soft
     degrade (tests: `test_active_ride_cache.py`);
  2. ETA movement gate >100m, ride-scoped `driver:{id}:last_eta_loc`, 120s upper
     bound (tests: `test_maps_eta_movement_gate.py`);
  3. breadcrumb batching 10 points / 10s / ride-change, flush on WS disconnect
     and at complete_ride before trail aggregation (tests: `test_breadcrumb_buffer.py`).

### B4. WS per-user rate limit is per-replica only
- [x] **Status:** done (2026-07-28) — `ConnectionManager.note_user_message`
  now enforces the 30 msg/s cap via a Redis fixed-window counter (`INCR`
  then `EXPIRE 1` on the first increment), keyed on `user_id` and shared
  fleet-wide. `utils/redis_client.py` already transparently falls back to
  an in-process dict when `REDIS_URL` is unset, so local/dev/test needed
  no branching. If Redis IS configured but a call raises (network blip,
  Redis down), the limiter fails **open** to the original per-machine
  sliding-window bucket (renamed `_note_user_message_local`) rather than
  blocking every WS message fleet-wide on a transient Redis hiccup —
  matching the non-security-critical fail-open precedent already in
  `utils/rate_limiter.py`'s `RedisRateLimiter` (OTP keys fail closed;
  general limits degrade to memory).
- **Files:** `backend/socket_manager.py`, `backend/routes/websocket.py`
  (awaited the now-async call), `docs/runbooks/websockets.md`,
  `backend/tests/test_websocket_per_user_rate_limit.py`,
  `backend/tests/test_websocket_auth.py`.
- **Acceptance:** ✅ met — cap holds at N msg/s per user across all
  replicas via the shared Redis counter; verified with the existing
  30/31-message contract tests plus new coverage for the Redis-failure
  fallback path and its bucket cleanup. Full local backend suite re-run
  to confirm no regressions elsewhere.

### B5. Migrate AI place lookup to Places API (New) with hard locationRestriction
- [x] **Status:** done (2026-07-28) — the named-place ("places") branch of
  `_lookup_place_candidates` in `backend/ai/tools_booking.py` now calls
  Places API (New) Text Search (`places:searchText`) instead of the legacy
  Text Search API. Text Search (New)'s `locationRestriction` only accepts a
  **rectangle** (not a circle, unlike Autocomplete New) — that rectangle IS
  a hard filter: Google cannot return a candidate outside it at all, unlike
  the legacy API's soft `bounds`/`radius` params. A soft `locationBias`
  circle (matching the rider-app's Autocomplete-New pattern) rides alongside
  it purely as a relevance-ranking nudge. New helpers
  `build_text_search_payload` / `legacy_place_results_from_text_search` live
  in `backend/utils/google_places_new.py`, alongside the existing
  Autocomplete/Details helpers, and translate to/from the same legacy
  candidate shape the rest of `tools_booking.py` already expects — no
  downstream code (`_candidates_from_results`, dedup, precision flagging)
  needed to change.
  - **Also fixed, found while implementing**: `record_call("places_text_search")`
    was a string outside `maps_budget.py`'s `Sku` Literal and `_PRICE_USD`
    dict — every such call silently miscounted against a Redis key
    `estimate_today_usd()` never reads, so the circuit breaker was blind to
    this entire call type. Added the real `text_search_new` SKU (Places API
    (New) Text Search Pro pricing) so it now counts toward the daily budget.
  - **Verified NOT a gap** (the backlog text's other budget claim was
    stale): the fare Directions calls already call
    `record_call("directions")` (`utils/route_distance.py:734`), and
    `"directions"` was already a priced SKU — no fix needed there.
  - **Not in scope, intentionally**: the geocode branch (street addresses)
    stays on the legacy Geocoding API — Places API (New) has no forward-
    geocoding surface to migrate it to. The hard-filter fix for THAT branch
    is `components=locality:<city>`, already tracked separately as B7
    (blocked on `service_areas` gaining a real locality column).
  - **Deferred, not done here** (explicitly out of this item's title/scope):
    a hard `estimate_token` price-lock across the chat→card gap, a blocking
    surge sheet on the AI confirm card, and a structured (non-prose) payload
    for quote-card taps — these remain open follow-ups, not touched.
- **Files:** `backend/utils/google_places_new.py` (new payload
  builder/translator), `backend/ai/tools_booking.py` (new `_maps_post`
  helper + "places" branch rewrite), `backend/utils/maps_budget.py` (new
  `text_search_new` SKU), `backend/tests/test_ai_tools_booking.py` (rewrote
  `PLACES_OK` fixture + affected tests to the New API response shape; added
  `TestFindPlaceHardRestriction` pinning the hard rectangle, the no-bias-point
  case, and error-status handling), `backend/tests/test_maps_proxy.py`
  (pinned `text_search_new` counts toward `estimate_today_usd`).
- **Acceptance:** ✅ met — AI place lookups (named-place branch) never return
  a candidate outside the bias rectangle; `estimate_today_usd` now counts
  Text Search (New) calls (previously invisible) and already counted
  Directions calls (verified, not a real gap).

### B6. Measure Directions latency and re-tune the fare-estimate wait
- [ ] **Status:** in progress (2026-07-28) — the measurement half is done;
  the re-tuning half is genuinely blocked on live traffic this dev session
  cannot produce, not on more code work.
  - **Done:** `estimates.py`'s `_route_fetch()` now times every real
    Directions call and records it to `spinr_fare_directions_duration_ms`
    (new histogram, `utils/metrics.py`'s existing `observe`/`_metric_observe`
    plumbing — no new metrics infrastructure needed). Recorded in a `finally`
    block so a slow **or failed** call still shows up — a request that hits
    `DIRECTIONS_TIMEOUT_S` and gets cut off is exactly the signal this metric
    exists to surface, and a silently-dropped failure would hide the worst
    tail instead of measuring it. This follows the exact convention
    `utils/metrics.py`'s own `time_ms()` context manager documents ("Records
    even when the block raises — a slow failure is still latency the SLA
    dashboards must see").
  - **Not done, and can't be from this session**: picking the timeout from
    the observed p99. That requires real production request volume against
    the live Google Directions API — this dev session has neither live
    traffic nor Maps API access to generate a genuine distribution; a
    synthetic/mocked one would defeat the entire point of B6 (replacing
    judgement with data). `DIRECTIONS_TIMEOUT_S` / `_PRICING_ROUTE_WAIT_S`
    are therefore **unchanged** — still 1.5 s / 2.0 s, still by judgement,
    now with the instrumentation in place to replace that judgement once
    `spinr_fare_directions_duration_ms` has accumulated real traffic.
    `test_pricing_wait_stays_within_the_estimate_latency_budget` needed no
    change since the ceiling itself didn't move.
  - **Next step for whoever picks this back up**: let the metric collect for
    a representative window in production, pull the p99 from
    `/metrics` (or wherever it's scraped to), then decide per the original
    Action text — tighten both constants if the p99 sits well under 1.5 s,
    or move to pre-warming/caching common origin-destination pairs if
    Directions is routinely slower than the SLA allows.
- **Files:** `backend/routes/rides/estimates.py` (instrumentation),
  `backend/tests/test_ride_estimate_branches.py` (2 new tests: metric
  recorded on success, metric recorded even when the Directions call fails).
  `backend/routes/rides/_shared.py` / `backend/utils/metrics.py` needed no
  changes — the histogram plumbing already existed and `_shared.py`'s
  `DIRECTIONS_TIMEOUT_S` wasn't touched (no data to justify moving it yet).
- **Acceptance:** partially met — the latency distribution is now being
  recorded (the prerequisite the original acceptance text assumed already
  existed); the timeout itself is not yet re-justified by real data, and
  can't be inside a single dev session. Re-open once
  `spinr_fare_directions_duration_ms` has real production data to act on.

### B7. Give service areas a real locality so the geocode can be hard-filtered
- [x] **Status:** shipped — PR #2670 (merged 2026-07-28)
- **Why:** the Geocoding API treats `bounds` as a *soft* hint but `components`
  as a **hard** filter. `components=locality:Regina` would make it impossible
  for a Regina query to resolve to a same-named street in another city — the
  strongest available fix for cross-city mis-resolution. It is not wired up
  because `service_areas` has no city column, only `name`, which is a display
  label ("Regina Metro"); a wrong locality returns `ZERO_RESULTS` and breaks
  lookups outright, so a filter built on it is worse than none.
- **Action taken:** reused the existing (previously unpopulated) `city` column
  on `service_areas` — `routes/admin/service_areas.py` already read/wrote it —
  rather than adding a redundant `locality` column. Migration
  `263_service_areas_city_backfill.sql` adds the column defensively and
  backfills 5 known areas by name. `_lookup_place_candidates` in
  `backend/ai/tools_booking.py` now resolves the rider's service area and
  passes `components=locality:<city>|country:CA` when known, via the new
  `_geocode_with_locality_retry` helper, retrying unfiltered once on
  `ZERO_RESULTS`.
- **Files:** `backend/migrations/263_service_areas_city_backfill.sql`,
  `backend/ai/tools_booking.py`, `backend/tests/test_ai_tools_booking.py`
- **Acceptance:** met — a numbered street address in a covered city can no
  longer resolve to another city, and an unknown/unmatched locality degrades
  to prior unfiltered behaviour rather than to zero results.
- **Verified in production (2026-07-28):** `GET /api/v1/service-areas`
  confirms `city` is correctly populated for the real markets — `Regina` →
  `"Regina"`, `Saskatoon` → `"Saskatoon"`. The `riyadh` row (test/dev data,
  not a real market) still shows `city: ""` — migration 263 has not yet been
  applied against production (`schema_migrations` not updated; attempted via
  `scripts/migrate.py` but blocked by IPv6-only direct-host DNS resolution —
  see the `PG_CONNECTION_STRING` / Session pooler note in `CLAUDE.md`'s
  Commands section). Non-blocking: an empty `city` only causes that one row
  to fall back to the pre-PR unfiltered geocode behaviour, no regression.
  Low-priority follow-up: re-run `backend/scripts/migrate.py` against
  production via the Session pooler connection string once convenient.

### B8. Economy and XL quote identical fares (per-vehicle-type pricing unseeded)
- [x] **Status:** Regina/Saskatoon/Saskatoon Airport/Regina Airpot all
  CLOSED (2026-08-12) as an explicit **stopgap** — the 1.4×/1.8×
  multiplier proposal below was applied
  against production, fixing the "all tiers cost the same" defect for
  these two areas. **Not a claim about being priced below Uber** — the
  user explicitly raised that Spinr's 0% driver-commission model gives
  real room to undercut Uber's list price (distinct from chasing Uber's
  unsustainable promotional discounts), and just as explicitly deferred
  that question: this session has no live Uber comparative fare data to
  compute a real target, so absolute price-level-vs-Uber positioning is
  tracked as a separate follow-up, not resolved here. Full Change Impact
  Log: `docs/change-log/2026-08-12-b8-regina-saskatoon-vehicle-pricing.md`.
  **Two things the original proposal below got wrong, caught before
  running anything (see the change log for full detail):**
  1. **Live data had drifted since 2026-08-11.** Regina's live Economy
     rate was actually `0.02/0.02` (not the documented `2/2` — two orders
     of magnitude off, confirmed with the user as a decimal-entry error
     and corrected to `2.00` before applying multipliers). Saskatoon's
     live Economy `per_km` was `1.2` (not documented `1`), *higher* than
     XL's `1.0` — an inversion not in this entry's original scope,
     corrected in the same pass per user direction rather than shipping a
     second visible pricing oddity alongside the fix.
  2. **The drafted `UPDATE` statements below use the wrong JSON shape** —
     they assume `vehicle_pricing` is a JSON object keyed by vehicle-type
     name (`jsonb_set(..., '{XL}', ...)`), but the live schema is a JSON
     **array** of `{vehicle_type: "...", ...}` objects (confirmed against
     `routes/fares.py:249-254`'s actual read path). Running the SQL below
     as literally written would have been a **silent no-op** — do not run
     it verbatim; see the change log for the corrected full-array-replace
     statements that were actually executed.
  - **Final live values (Regina):** Economy `2.00/2.00`, XL `2.80/2.80`,
    Premium `3.60/3.60` (base_fare/per_km).
  - **Final live values (Saskatoon):** Economy `4.00/1.00`, XL
    `5.60/1.40`, Premium `7.20/1.80`.
  - **2026-08-12 reconciliation addendum:** a second, concurrent session
    picked up this same item at close to the same time (both independently
    got explicit user sign-off before writing). By the time the second
    session queried live production, Regina's Economy rate read
    `0.20/0.20` — 10× off from **both** this entry's documented before
    (`0.02/0.02`) and after (`2.00/2.00`) states. Saskatoon's live values
    matched this entry's documented final state exactly; only Regina had
    drifted. The second session also introduced and immediately
    self-corrected its own bug while investigating (a `jsonb_set(...,
    '{-1}', ...)` call meant to *append* a Premium tier instead *replaced*
    Regina's last array element, briefly deleting the XL row — caught via
    the query's own `RETURNING` output within the same turn, fixed with
    array-concatenation `||` instead). After surfacing the 10× discrepancy
    to the user directly (rather than guessing) and getting explicit
    confirmation to match this entry's documented `$2.00` baseline, Regina
    was reconciled back to the values above. **Net effect: Regina's live
    state now matches what this entry always said it should be** — the
    interim `$0.20`-baseline detour is closed, not a new open question.
    Flagged for anyone doing further live-Supabase pricing/config work:
    **re-query the specific row immediately before writing, even when
    ACTION_ITEMS.md says an item is already closed** — a merged doc commit
    doesn't guarantee the live table hasn't moved since, the way `git log`
    would for a code change. Full detail:
    `docs/change-log/2026-08-12-b8-regina-reconciliation.md`.
  - **Discovered, not fixed this pass — new follow-up items:**
    - [x] A 6th service area, **"Saskatoon Airport"** (created 2026-07-30,
      after the last full-area inventory this entry's original
      investigation ran), had the identical all-tiers-same-price defect.
      **CLOSED 2026-08-12** — same stopgap multiplier applied (this area
      has non-zero `per_min`/`min_fare`/`booking_fee`, unlike Regina/
      Saskatoon main, so the doc's "~1.2×/~1.5× on per_min/booking_fee"
      modest-scaling note was extended to `min_fare` too, treated as
      another floor-amount field rather than a per-unit rate). Final
      live values: Economy `3.50/1.50/0.25/8.00/2.00`, XL/Van (same
      multiplier, same resulting numbers per the doc's original "XL/Van
      ≈1.4×" framing) `4.90/2.10/0.30/9.60/2.40`, Premium
      `6.30/2.70/0.38/12.00/3.00` (base_fare/per_km/per_min/min_fare/
      booking_fee). Change Impact Log:
      `docs/change-log/2026-08-12-b8-airport-vehicle-pricing.md`.
    - [x] A service area literally named **"Regina Airpot"** (typo — missing
      the second "r") also carried the defect. **CLOSED 2026-08-12** —
      same fix applied, same final values as Saskatoon Airport above.
      The name typo itself was intentionally left untouched at the time —
      renaming a `service_areas.name` value needed its own blast-radius
      check first.
      - **Typo itself CLOSED 2026-08-12 (separate follow-up)**: blast-radius
        check performed (grepped all 4 surfaces for name-string matches on
        "Regina Airport"/"Regina Airpot"). Found two *historical, already-
        applied* migrations/changes that filtered on the correct spelling
        (`Regina Airport`) and would have silently missed this row —
        migration 263's `city` backfill, and the 2026-08-11 PST-enable
        change's own change log claim of "verified... all correctly
        named". Live-checked both before renaming: `city='Regina'` and
        `pst_enabled=true, pst_rate=6` were **already correctly set** on
        the real row despite the name mismatch (set via some other
        mechanism — not fully diagnosed, not needed to be, since the
        current state is correct) — no data gap from the typo itself.
        The only code reference found (`routes/service_areas.py`'s
        comment, `test_service_areas_public.py`'s docstring) is
        documentation/description, not a live name-string match — the
        actual filtering logic uses `is_airport`/`parent_service_area_id`
        flags, confirmed by reading the route and by all 44
        `test_service_areas_public.py`/`test_admin_service_areas_coverage.py`
        tests passing unchanged (they mock `get_rows` and assert on filter
        shape, not on the row's name). Renamed `'Regina Airpot'` →
        `'Regina Airport'` directly against production. Change Impact Log:
        `docs/change-log/2026-08-12-b8-regina-airport-rename.md`.
      - **New finding surfaced while checking this, NOT fixed — see B10**:
        the main **`Regina`** (non-airport) service area currently has
        `pst_enabled=false` despite `pst_rate=6` already being set, and
        despite the 2026-08-11 PST-enable change log explicitly claiming
        all 4 Saskatchewan rows (including plain "Regina") were set to
        `pst_enabled=true`. Live data contradicts that log. Not touched
        here — flagged as its own item pending user confirmation of
        whether this is a live tax-under-collection bug or an intentional
        reversal this session has no record of.
    - **Uber competitive positioning** (raised by the user): should
      Economy's absolute rate undercut Uber's current list price in
      Regina/Saskatoon, and by how much? Needs real comparative fare data
      this session doesn't have access to. `riyadh`/`riyadh airport`
      remain untouched (confirmed intentional international-market
      pricing per the original investigation).
- **(historical) Status:** open — **parked 2026-08-11 pending contributor sign-off on the
  multiplier proposal below** (superseding the earlier "parked pending a
  pricing decision (2026-07-27)" note — same blocker, now with the exact
  statements ready to run once approved). No code change needed; the join
  logic is already correct and tested. Do not run the `UPDATE` statements
  below until a contributor with pricing authority has explicitly approved
  the multipliers (or supplied different target rates) — this is a
  live-pricing change, not a docs/test change, and needs the same
  sign-off discipline as any other money-touching action per CLAUDE.md.
  **Root cause confirmed against production data** (queried live Supabase):
  this is a **data problem, not a code bug**. All 5 active `service_areas`
  rows have real `vehicle_pricing` JSONB entries — the fare service's
  per-vehicle-type join/lookup logic (`routes/fares.py::build_fares_for_area`)
  is correct and does NOT fall through to `DEFAULT_FARE` for these areas.
  The rows themselves were configured with **identical rate numbers across
  every vehicle type in every area**:
  - `Regina Airport`, `riyadh`, `riyadh airport`: all 4 vehicle types
    (Economy/XL/Van/Premium) carry the exact `DEFAULT_FARE` values
    (base_fare 3.50, per_km 1.50, per_min 0.25, min_fare 8, booking_fee 2) —
    almost certainly seeded by copying the fallback defaults verbatim rather
    than falling back to them at request time.
  - `Regina`, `Saskatoon`: Economy and XL both configured, but with the same
    numbers as each other in each area (Regina: 2/2/0/0/0 both; Saskatoon:
    4/1/0/0/0 both) — looks like a row was duplicated in the admin Vehicle
    Pricing editor and only the `vehicle_type` name field was changed, not
    the rate fields.
  - `riyadh`/`riyadh airport` are intentional (international market),
    not a data-hygiene concern — confirmed with product.
  - **Not a live-testing blocker**: booking end-to-end still works, no
    fare/receipt mismatch, no payment-integrity or safety issue — riders
    just see the same price across vehicle types, which could read as "the
    picker does nothing" if a tester notices.
- **Action (blocked on a pricing decision, not on more investigation)**:
  someone with pricing authority needs to supply real differentiated
  base_fare/per_km/per_min/min_fare/booking_fee values per vehicle type per
  area (or approve applying industry-standard multipliers off each area's
  existing Economy rate — proposed: XL/Van ≈1.4×, Premium ≈1.8× on
  base_fare/per_km, more modest ~1.2×/~1.5× on per_min/booking_fee) — then
  `UPDATE service_areas SET vehicle_pricing = ...` per area. No code change
  needed; the join logic is already correct and tested
  (`backend/tests/test_fares.py`).
  - **Proposed `UPDATE` statements, drafted and ready to run once
    approved** (Regina/Saskatoon only — `riyadh`/`riyadh airport` are
    intentionally out of scope per the product confirmation above; each
    area's own current Economy row is the multiplier base, values rounded
    to 2dp, `Decimal`-safe since this is direct SQL, not app-layer float
    arithmetic):
    ```sql
    -- Regina: Economy base_fare=2, per_km=2, per_min=0, min_fare=0, booking_fee=0
    UPDATE service_areas
    SET vehicle_pricing = jsonb_set(
      jsonb_set(
        vehicle_pricing,
        '{XL}', '{"base_fare": 2.80, "per_km": 2.80, "per_min": 0, "min_fare": 0, "booking_fee": 0}'::jsonb
      ),
      '{Premium}', '{"base_fare": 3.60, "per_km": 3.60, "per_min": 0, "min_fare": 0, "booking_fee": 0}'::jsonb
    )
    WHERE name = 'Regina';

    -- Saskatoon: Economy base_fare=4, per_km=1, per_min=0, min_fare=0, booking_fee=0
    UPDATE service_areas
    SET vehicle_pricing = jsonb_set(
      jsonb_set(
        vehicle_pricing,
        '{XL}', '{"base_fare": 5.60, "per_km": 1.40, "per_min": 0, "min_fare": 0, "booking_fee": 0}'::jsonb
      ),
      '{Premium}', '{"base_fare": 7.20, "per_km": 1.80, "per_min": 0, "min_fare": 0, "booking_fee": 0}'::jsonb
    )
    WHERE name = 'Saskatoon';
    ```
    Both areas' current per_min/min_fare/booking_fee are already 0 across
    the board, so the ~1.2×/~1.5× multiplier on those fields is a no-op
    here (0 × anything = 0) — only base_fare/per_km actually move. `Van`
    is not present as a distinct row in either area's current
    `vehicle_pricing` (only Economy/XL configured per the root-cause
    section above) — added scope note rather than guessing a Van row
    shape; confirm with the contributor whether Van needs seeding too
    before running. **Not yet executed against any environment** — this is
    a drafted proposal only, contingent on approval.
- **Files:** none (data-only fix) — reference only:
  `backend/routes/fares.py::build_fares_for_area`,
  `backend/tests/test_fares.py`
- **Acceptance:** each vehicle type in each area quotes genuinely different
  rates reflecting its class (XL/Premium priced above Economy).


### B9. Address+coordinate pairs are stored server-side with zero consistency validation
- [x] **Status:** CLOSED 2026-08-11 — geocode-verify + dedupe fix, `place_id`
  storage, and `CreateRideRequest` cross-field validation are all now shipped
  (see below). The only remaining piece named anywhere in this item's history
  — `place_id` re-resolve-on-save for saved addresses — was already flagged
  as its own separate, larger follow-up when `place_id` storage shipped, not
  part of what this item's Action text asked for.
- **Why:** the client-side carriers of mismatched pairs are fixed (recents v2,
  search-screen pin integrity, map-pick label binding), but the backend still
  accepts and replays unvalidated pairs:
  - `POST /addresses` (`backend/routes/addresses.py:29-42`) stores any
    `{address, lat, lng}` triple — no address↔coordinate cross-check, no
    service-area check, no expiry. Saved places render in the destination
    picker and are trusted verbatim.
  - `POST /favorites/from-ride/{ride_id}` (`backend/routes/favorites.py:135-158`)
    copies a ride's stored pair verbatim into a permanent favorite — a poisoned
    ride row gets laundered into a never-expiring replay source. (Unwired in
    rider-app today, but a live trap.) Its dedupe also compares `pickup_lat`
    and `dropoff_lat` only — longitude never (`favorites.py:71-72`).
  - `CreateRideRequest` (`backend/schemas.py:426-433`) persists client-supplied
    address strings beside coordinates with no cross-field validation, making
    the rides table itself a durable record of whatever pair the client sent.
- **Action:** store `place_id` with saved addresses and re-resolve on save;
  geocode-verify pairs at write time (reject > ~1 km mismatch); fix the
  favorites dedupe to compare both axes of both endpoints.
- **Done this pass:** added `backend/utils/address_verification.py` —
  best-effort geocode check used by both `POST /addresses` and
  `POST /favorites` (which `save_favorite_from_ride` delegates into, so both
  entry points from the Why section are covered). Fails OPEN on no API key,
  exhausted Maps budget, API/network error, `ZERO_RESULTS`, `partial_match`,
  or an imprecise (non-ROOFTOP/RANGE_INTERPOLATED) geocode — only rejects
  (400) on a confident precise-geocode mismatch > 1 km. Also fixed the
  favorites dedupe (`favorites.py`) to compare both lat AND lng of both
  pickup and dropoff, not latitude only.
- **Explicitly deferred:**
  - `place_id` storage — **done**: migration `284_saved_addresses_place_id.sql`
    adds a nullable `saved_addresses.place_id` column;
    `verify_address_matches_coordinate` (`utils/address_verification.py`)
    now returns `(ok, reason, place_id)` — the `place_id` is already present
    in the same Geocoding API response used for the mismatch check, so this
    is a free capture, not a second API call. `POST /addresses`
    (`routes/addresses.py`) stores it on the `SavedAddress` row;
    `POST /favorites` (`routes/favorites.py`) unpacks and discards the third
    value (favorites are a separate `favorite_routes` table with two
    endpoints per row — storing pickup/dropoff `place_id` there wasn't part
    of what this item named and would be its own follow-up, not folded in
    here). **Re-resolve-on-save** (using the stored `place_id` to confirm a
    saved address still points at the same real-world place on subsequent
    use) is a separate, larger follow-up — this pass only captures and
    stores the identifier, it doesn't yet do anything with it after save.
    Updated `tests/test_address_verification.py` (3-tuple return, `place_id`
    asserted in every branch) and
    `tests/test_p3_addresses_favorites_safety_disputes.py` (2 new tests:
    `place_id` persisted on success, `None` when verification fails open).
    **Verification note:** the "deferred to end-of-batch full-suite run" note
    left here by the original pass was never actually confirmed run —
    re-verified 2026-08-11 as its own explicit step before starting the
    `CreateRideRequest` work below: `pytest tests/test_address_verification.py
    tests/test_p3_addresses_favorites_safety_disputes.py -q --no-cov` → **40
    passed**, 0 failures.
  - `CreateRideRequest` cross-field validation — **done 2026-08-11**, in
    `routes/rides/booking.py`'s `create_ride` handler rather than
    `schemas.py` itself: pydantic `field_validator`s are synchronous and
    can't make the network call a geocode check requires, so this was
    structurally impossible to add as a schema validator — it has to live
    in the route. Reuses the existing `verify_address_matches_coordinate`
    helper (no new geocode logic), running both legs concurrently via
    `asyncio.gather` (bounds added latency to one Maps round-trip, not two).
    Placed *after* the existing pickup/dropoff/stop geofence gates so an
    out-of-service-area request is rejected by the free in-memory polygon
    check first, without spending a paid Maps call on a booking that would
    be rejected anyway. Same fail-open contract as the other two call
    sites. Dry run performed per CLAUDE.md's pre-merge release gate for
    state-machine/money-adjacent changes: 671 tests across every
    ride-booking-adjacent test file found via grep (`test_rides.py`,
    `test_create_ride_guard_clauses.py` — 3 new tests added here,
    `test_wav_dispatch.py`, `test_corporate_ride_payment.py`,
    `test_coverage_rides.py`, `test_admin_rides_coverage.py`,
    `test_corporate_surge_bypass.py`, `test_p0_ship_blockers.py`,
    `test_ai_tools_booking.py`) — 0 failures, 0 hangs, ~20s combined;
    confirmed the unmocked existing tests don't make a real network call
    (the internal `get_app_settings()` lookup resolves through the same
    mocked `db_supabase.get_rows` autouse fixture every other test already
    relies on). `ruff check` clean on all touched files. Full Change Impact
    Log: `docs/change-log/2026-08-11-b9-create-ride-address-coordinate-validation.md`.
    **Not verified:** no manual/staging repro against a real Maps API key;
    production Maps-budget/latency impact of the 2 extra geocode calls per
    booking is unmeasured (same open question B6 already documents for the
    Directions call); the rider-app's handling of the new 400 response was
    not implemented (backend-only change — a raw error string surfacing
    to the rider instead of a friendly retry prompt is a real, disclosed
    gap, not silently assumed covered); full ~9000-test backend suite was
    not run, only the targeted 671-test slice above.
- **Files:** `backend/routes/addresses.py`, `backend/routes/favorites.py`,
  `backend/routes/rides/_deps.py`, `backend/routes/rides/booking.py`,
  `backend/utils/address_verification.py` (new),
  `backend/tests/test_address_verification.py` (new),
  `backend/tests/test_p3_addresses_favorites_safety_disputes.py`,
  `backend/tests/test_create_ride_guard_clauses.py`
- **Acceptance:** met — no endpoint (`/addresses`, `/favorites`, or
  `POST /rides`) persists an address whose stored coordinate is more than
  ~1 km from where that address geocodes, when Google is confident about
  the geocode.

### B10. Compliance-module exports have no dual-approval gate (extends open AI-3)
- [x] **Status:** DONE (2026-07-29) — shipped across PR #2819 (schema,
  `services/admin_export_approvals.py`, `routes/admin/export_approvals.py`
  queue endpoints, Compliance + Data Transfer server-side gate wiring,
  backend tests) and PR #2820 (admin-dashboard Export Approvals queue page,
  sidebar entry, `202 approval_required` handling in `ExportTab.tsx` and
  `compliance/page.tsx`'s download/email flows). AI-3's shared dual-approval
  mechanism now exists: `settings.dual_approval_exports_enabled` (default
  `false`, dark-launched), a `1,000`-row threshold
  (`_APPROVAL_GATE_ROW_THRESHOLD`), self-approval blocked server-side
  (`require_super_admin` + a distinct-approver check), and both the
  Compliance (`gst-pst-remittance`, `insurance-period-audit`) and Data
  Transfer export endpoints wired through it.
- **Why:** `docs/threat-model/admin-panel.md`'s AI-3 ("Admin exports all
  users → offline PII leak") had been an OPEN P1 finding since the threat
  model was written — no dual-approval workflow existed for large exports
  anywhere in the admin panel. The Compliance & Tax Reporting module
  (`routes/admin/compliance.py`, shipped PR #2650) added two more export
  endpoints — `gst-pst-remittance` and `insurance-period-audit` — that
  return up to `_ROW_LIMIT = 10000` rows each with no gate, silently
  extending the same open risk. Flagged as gap G2 in
  `reports/audits/2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md`.
- **Files:** `backend/migrations/268_admin_export_approvals.sql`,
  `backend/services/admin_export_approvals.py`,
  `backend/routes/admin/export_approvals.py`,
  `backend/routes/admin/compliance.py`,
  `backend/routes/admin/data_transfer_export.py`,
  `admin-dashboard/src/app/dashboard/export-approvals/page.tsx`,
  `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx`,
  `admin-dashboard/src/lib/api.ts`, `admin-dashboard/src/components/sidebar.tsx`.
- **Acceptance:** met — any export > 1,000 rows (Compliance or Data
  Transfer) does not run until a *different* admin approves it from the
  Export Approvals queue; the flag is still off by default, zero behavior
  change until explicitly flipped.
- **Not yet done:** the flag has not been flipped on anywhere (dark-launch
  by design — flip is a separate, deliberate rollout decision, not part of
  this item's acceptance).

### B11. Data Transfer export: no dual-approval gate (extends open AI-3) + PIA recommendations not yet implemented
- [ ] **Status:** in progress (2026-07-29) — R-A through R-F all DONE/resolved.
  The dual-approval gate itself is now DONE (shipped as part of B10 above,
  PRs #2819/#2820 — Data Transfer's `export_entities` route is wired through
  the same shared gate as Compliance). Only R-G remains open, and only
  because it genuinely requires a human privacy/legal determination — a
  self-contained request package for that
  review has been prepared at `reports/legal/data-transfer-implied-consent-review.md`
  (2026-07-28), but the actual determination is still pending a named
  reviewer. Plus the still-open AI-3 dual-approval wiring (shared with B10,
  not specific to this item). The module's P0 gaps (access-control, missing
  PIA) were fixed 2026-07-28 (PRs #2685, #2687); this item tracks the PIA's
  own follow-up recommendations.
  - **R-A DONE:** investigating it before implementing found the original
    finding's premise was wrong — `bulk_operations` was never actually
    grantable to a non-super_admin (not in `AVAILABLE_MODULES`/`ALL_MODULES`/
    any `ROLE_PRESETS`), so access was already super_admin-only in practice,
    just fragile-by-omission. Fixed with an explicit `require_super_admin`
    dependency on all 5 routers instead of splitting a new module flag (the
    new-flag option would have kept the same fragile shape). See
    `docs/change-log/2026-07-28-data-transfer-router-super-admin-gate.md`.
  - **R-B DONE:** added `include_ride_gps`/`include_document_bytes` optional
    flags (both default `True`, unchanged behavior) — ride/document rows
    stay present either way, only GPS coordinates or document byte payloads
    are dropped when opted out. Admin-dashboard: two new checkboxes on the
    Export tab. See `docs/change-log/2026-07-28-data-transfer-export-scope-flags.md`.
  - **R-C DONE:** added a required `reason` field (10-200 chars) to the
    export request, migration 264 (nullable column, application-layer
    "required"), surfaced in the Jobs & History tab's new Reason column and
    in the audit-log metadata. Admin-dashboard: `ExportTab.tsx` (new
    textarea + client-side validation), `JobsTab.tsx` (new column). See
    `docs/change-log/2026-07-28-data-transfer-export-reason-field.md`.
  - **R-D DONE:** also had a wrong premise — no signed URL was ever exposed
    at export time (fully backgrounded route; the "7-day signed URL" was
    computed and immediately discarded, dead code). Removed the dead
    `create_signed_url` call instead of shortening a TTL nothing was exposed
    to. See `docs/change-log/2026-07-28-data-transfer-export-drop-unused-signed-url.md`.
  - All four PIA corrections/updates are reflected in
    `docs/privacy/2026-07-28-pia-data-transfer-export.md` itself (R-001/R-A,
    R-B, R-C, and R-D sections updated in place, not just here).
- **Why:** the Data Transfer export route (`routes/admin/data_transfer_export.py`)
  moves full-fidelity, unredacted PII (government ID numbers, exact GPS ride
  history, identity documents) for up to 100 entities per request with no
  dual-approval gate — the same class of gap as AI-3 (`docs/threat-model/admin-panel.md`)
  and B10 above, extended a second time. Full assessment:
  `docs/privacy/2026-07-28-pia-data-transfer-export.md`; audit trail:
  `reports/audits/2026-07-28-data-transfer-corporate-lifecycle-audit-v1.md`.
- **Action (from the PIA's ranked recommendations):**
  - [HIGH] ~~R-A~~ DONE — see above.
  - [HIGH] ~~R-B~~ DONE — see above.
  - [MEDIUM] ~~R-C~~ DONE — see above.
  - [MEDIUM] ~~R-D~~ DONE — see above.
  - ~~[MEDIUM] R-E: name this module in `docs/runbooks/data-breach.md`.~~ DONE
    2026-07-28 — the runbook already exists (CLAUDE.md's "to be created" note
    was stale; confirmed and corrected 2026-07-28). Added a dedicated §1a-i
    entry naming this module's data flow (full unredacted PII, up to 100
    entities, GPS precision, government IDs) as a designated high-sensitivity
    flow, with containment commands. See PIA doc §7/§8 for detail.
  - ~~[LOW] R-F: confirm `notification_preferences` needs to be in the export
    bundle at all.~~ RESOLVED-AS-IS 2026-07-28 — confirmed and kept. It's
    boolean opt-in/opt-out toggles only (no PII), and the module's stated
    purpose (reconstructing a working account in the target environment)
    genuinely needs it — dropping it would silently revert a migrated
    user's notification settings to defaults on re-import. No code change.
    Reasoning documented in the PIA doc §8.
  - [LOW] R-G: formal legal review of the implied-consent basis for this
    secondary use — **needs a human privacy/legal sign-off, not resolvable
    by an engineering task.** Still open; do not mark done without an actual
    reviewer name + date in the PIA's Section 9 sign-off table. **Request
    prepared 2026-07-28:** `reports/legal/data-transfer-implied-consent-review.md`
    packages the specific question, background, and what a closed-out review
    should record, following the house format of
    `reports/legal/supabase-region-attestation-checklist.md`. Also flags two
    facts not fully surfaced in the PIA itself: (1) `docs/legal/privacy-policy.md`
    (still unpublished) currently has no language covering this internal
    cross-environment data-movement use case — if legal concludes a distinct
    disclosure is needed, that draft is the place to add it before first
    publication; (2) the module's transfer stays entirely within Spinr's own
    Supabase project (no third-party recipient), which the request flags as
    relevant to the reasonable-secondary-use analysis. No privacy-officer/legal
    role is currently assigned in this repo to actually make the call — see
    the request's Status table.
  - When AI-3's shared dual-approval mechanism is built (see B10), wire this
    route through it too rather than a one-off gate.
- **Files:** `backend/routes/admin/data_transfer_export.py`,
  `backend/routes/admin/data_transfer_jobs.py`, `backend/migrations/`
  (new `reason` column for R-C), `docs/threat-model/admin-panel.md`
  (AI-3 row updated to reference this scope).
- **Acceptance:** not gating — acceptance is AI-3's own, same as B10, plus
  each PIA recommendation's own stated success criterion (see the PIA doc
  §8 for R-A through R-G).

### B12. Corporate billing: race-test coverage gaps and no compensating-transaction runbook
- [x] **Status:** DONE (2026-07-28, branch `claude/b12-corporate-coverage-runbook`)
  — the P0 gap (no regression test for the migration-258 allowance-cap race)
  was fixed 2026-07-28 (PR #2686); both remaining P1 items from the same
  audit are now closed:
  - ~~runbook~~ `docs/runbooks/corporate-compensating-transaction.md` written
    — detection via the ledger, target-balance compensating-delta computation
    (not a blind reversal), applying the correction through the same locked
    RPC, and reconciliation queries.
  - ~~coverage gap~~ all four files now ≥90% (measured via
    `pytest --cov=routes.corporate_rider --cov=routes.corporate_company_bookings
    --cov=routes.corporate_accounts --cov=routes.corporate_company
    --cov-report=term-missing backend/tests/ -k corporate`, 503 passed / 3
    skipped / 0 failed):
    `routes/corporate_rider.py` 65% → **96%**,
    `routes/corporate_company_bookings.py` 57% → **94%**,
    `routes/corporate_accounts.py` 79%/82% → **97%**,
    `routes/corporate_company.py` 79% → **93%**.
- **Why:** `reports/audits/2026-07-28-data-transfer-corporate-lifecycle-audit-v1.md`
  found: (1) no compensating-transaction runbook exists for a bad
  `corporate_wallet_apply_delta`/`corporate_allowance_apply_delta`
  application — the documented rollback ("drop the function") doesn't undo
  money already moved; (2) four corporate route files remain below the 90%
  money-path coverage floor: `routes/corporate_rider.py` (65%),
  `routes/corporate_company_bookings.py` (57%),
  `routes/corporate_accounts.py` (79%), `routes/corporate_company.py` (79%).
- **Action:** write a concrete compensating-transaction runbook (mirrors the
  CLAUDE.md rule that money deltas need more than `git revert`); raise the
  four listed files' coverage, prioritizing branches that touch
  allowance/wallet reads. Also verify KYB document Storage bucket RLS/access
  scoping (not confirmed in the audit) and track the v2-deferred corporate
  scope (cost centers, approval workflows, SSO/HRIS — currently only
  discoverable via `docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md`)
  here so it isn't lost.
- **Still open (out of scope for this pass, tracked here per the audit's own
  note):**
  - KYB document Storage bucket RLS/access scoping — flagged "not confirmed"
    by the audit; needs its own security-focused pass, not attempted here.
  - v2-deferred corporate scope (cost centers, approval workflows, SSO/HRIS)
    — see `docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md`.
- **Files:** `backend/services/corporate_wallet_service.py`,
  `backend/services/corporate_allowance_service.py`,
  `backend/routes/corporate_rider.py`, `backend/routes/corporate_company_bookings.py`,
  `backend/routes/corporate_accounts.py`, `backend/routes/corporate_company.py`,
  `docs/runbooks/corporate-compensating-transaction.md` (new),
  `backend/tests/test_corporate_rider_routes.py` (extended),
  `backend/tests/test_corporate_company_bookings_routes.py` (new),
  `backend/tests/test_corporate_sections.py` (extended),
  `backend/tests/test_corporate_accounts_lifecycle.py` (new),
  `backend/tests/test_corporate_company_gap_coverage.py` (new).
- **Acceptance:** runbook exists and is concrete/testable (not just "revert
  the commit") — met; all four listed files reach ≥90% coverage — met.

### B13. 22 drivers have no `regulatory_authority`/`regulatory_region` set (blocks the SGI-forms segregation guard from covering them)
- [x] **Status:** backfill done (2026-07-28, migration
  `265_drivers_regulatory_authority_backfill.sql`) — all 22 rows verified
  by `id` against the real project (`soavhtdhefowwvforzwb`) and confirmed
  to resolve to `service_areas` 'Regina' or 'Saskatoon' (both real
  Saskatchewan markets) before backfilling
  `regulatory_authority='SGI', regulatory_region='SK'`. Post-migration:
  `SELECT count(*) FROM drivers WHERE regulatory_authority IS NULL` → `0`;
  all 209 drivers are now `SGI`/`SK`. `routes/admin/sgi_forms.py` still
  hard-blocks generating an SGI D00032/D00033 for any driver whose
  `regulatory_authority` is explicitly set to something other than `"SGI"`
  (Alberta-expansion safety guard, unchanged by this backfill).
- **Why (original gap):** confirmed directly against the real staging schema
  (`SELECT regulatory_authority, regulatory_region, count(*) FROM drivers
  GROUP BY 1, 2`): 187 rows were `SGI`/`SK`, 21 fully NULL, 1 had
  `region=SK` but `authority=NULL` — all real Saskatchewan drivers (no
  Alberta data existed yet), so this was a backfill gap, not a
  misclassification.
- **Remaining action (guard tightening, not yet done):** now that the
  backfill is complete, `sgi_forms.py`'s `_out_of_scope_drivers()` can be
  tightened to require an explicit `regulatory_authority == "SGI"` match
  instead of treating NULL as in-scope — but hold off until Alberta's own
  `driver_import_service` onboarding path is confirmed to always populate
  the field for new AB drivers (otherwise the tightened guard would start
  blocking legitimate new AB drivers' own province-specific forms once
  those exist, not just protect against cross-province mixing). Not
  blocking: since 100% of drivers are now non-NULL, the NULL-passes
  branch is currently dead code in practice — the risk it originally
  covered (a NULL Alberta driver slipping through) can't happen yet
  because no Alberta driver data exists, and re-tightening is a small,
  isolated follow-up whenever Alberta onboarding actually starts.
- **Related, separate gap (not part of B13, not fixed here):**
  `service_areas.province`/`regulatory_authority`/`regulatory_region` are
  still NULL for 'Saskatoon', 'Regina Airpot', 'riyadh', and
  'riyadh airport' (only 'Regina' is populated) — noticed while verifying
  this backfill's source data. Doesn't block anything today (this
  migration keyed off verified driver `id`s directly, not the
  service_areas reference columns) but is a latent gap if any future code
  starts trusting `service_areas.regulatory_authority` as a source of
  truth for those areas.
- **Files:** `backend/routes/admin/sgi_forms.py` (unchanged — tightening
  still pending), `backend/migrations/265_drivers_regulatory_authority_backfill.sql`
  (new, applied).

### B14. SGI form company address split across dedicated fields + driver licence-number/class data gap
- [x] **Status:** address bug DONE (2026-07-29). Licence-number/class
  confirmed as a genuine **data gap**; a third, independent bug was found
  and fixed while building the remediation tool (admin driver-edit route
  wrote `license_number` as plaintext instead of Vault-encrypting it —
  see below). The backfill **queue/tooling is now built and live**
  (`/dashboard/driver-license-backfill`); the actual 22-driver data entry
  is a manual step for an admin to do in that screen, not something this
  session can perform (requires reading real government ID photos). The
  larger OCR/automated-onboarding proposal is written up but **not
  started**, pending a scope/vendor decision.
- **Why (address):** both real SGI templates (`D00032`/`D00033`) ship
  dedicated `Street address`/`City/town`/`Provincestate`/`Postalzip code`
  fields (confirmed via `PdfReader.get_fields()`), but
  `sgi_form_filler.py` was setting only the street-address field to one
  combined `"STREET, CITY, PROVINCE, COUNTRY, POSTAL"` string, leaving the
  template's own dedicated city/province/postal fields at their stale
  placeholder values — every generated form showed two disagreeing
  addresses across its own fields. Fixed: address split into
  street/city/province/postal constants, each mapped to its correct
  field; country dropped (neither template has a field for it). Two
  regression tests assert no city/province/postal/country string leaks
  into the street field.
- **Why (licence number/class):** traced field-mapping, PDF-slot naming,
  and Vault decryption end-to-end — all correct. Checked the real
  `drivers` table directly: 22 of 209 drivers (some already
  `is_verified: true`) have `NULL` `license_number`/`license_class`. Root
  cause: these are optional self-serve profile fields, never required at
  signup or at document-review approval, and the driver's-license photo
  each of these drivers *did* upload during onboarding is never OCR'd to
  populate the structured columns — an admin has to manually retype it,
  and nothing prompts that. Full analysis, immediate-remediation steps,
  and a reasoned automated-onboarding (OCR + capture-guidance +
  dual-approval) proposal in
  `docs/proposals/2026-07-29-driver-document-ocr-onboarding-automation.md`.
- **Third bug found + fixed while building remediation:** `routes/admin/
  drivers.py`'s `PUT /admin/drivers/{id}` (the exact endpoint the backfill
  tool needed to write through) wrote `license_number` as plaintext —
  unlike the self-serve profile-update and bulk-import paths, which both
  correctly call `_encrypt_driver_pii()` first. Any admin editing a
  driver's licence number via the dashboard was storing it unencrypted, a
  PIPEDA violation per that module's own docstring. Fixed before building
  anything on top of that endpoint, with a regression test asserting the
  raw value never reaches the DB write.
- **Immediate remediation — tooling DONE, data entry still open:** (1)
  `/dashboard/driver-license-backfill` (new admin page) lists exactly the
  drivers missing licence data via a new `missing_license` filter on
  `GET /admin/drivers`, lets an admin open the existing `DocumentReviewer`
  to view each driver's already-uploaded licence photo, and save via the
  now-fixed encrypting update path — an admin still needs to actually work
  through the queue (this session cannot reliably read government ID
  photos); (2) make licence-number/class entry a required part of the
  admin document-review "approve" action going forward, so this gap can't
  grow — small scoped change, still open, own PR + Change Impact Log (it
  changes an existing live admin workflow).
- **Larger proposal (not started, needs a decision):** OCR-assisted
  document intake with client-side capture guidance (Expo camera +
  quality gate), a purpose-built ID-OCR vendor (buy, not build — see
  proposal's reasoning), and a human dual-approval queue reusing the same
  state-machine shape as B10's export-approval gate. Recommends
  email/SMS notification-channel parity (today: push-only on document
  rejection, no channel at all on upload-received) as the fastest,
  vendor-independent first slice. See the proposal doc for full reasoning,
  PIPEDA precautions, and sizing.
- **Files:** `backend/services/data_transfer/sgi_form_filler.py` (address
  fix), `backend/tests/test_sgi_form_filler.py` (2 new regression tests),
  `backend/routes/admin/drivers.py` (encrypt-on-write fix + `missing_license`
  filter), `backend/tests/test_admin_business_logic.py` (encryption
  regression test), `backend/tests/test_admin_extended.py` (2 filter
  tests), `admin-dashboard/src/app/dashboard/driver-license-backfill/page.tsx`
  (new), `admin-dashboard/src/lib/api.ts`, `admin-dashboard/src/components/sidebar.tsx`,
  `docs/proposals/2026-07-29-driver-document-ocr-onboarding-automation.md`
  (new).
- **Acceptance (address, done):** generated D00032/D00033 PDFs have each
  address component in its correct dedicated field, verified by
  regenerating both forms and reading every field back. **Acceptance
  (encryption fix, done):** regression test confirms plaintext never
  reaches the DB write. **Acceptance (licence data, pending):** not
  gating — tracked here until an admin actually works through the
  `/dashboard/driver-license-backfill` queue and a decision is made on
  the larger proposal.

### B15. Rider/driver SOS: DB insert has no fallback on failure, and "PagerDuty" in domain-safety.md doesn't exist in code
- [ ] **Status:** partially done — the DB-insert-fallback / try-except
  sub-finding is **fixed and merged**: PR #2931
  (https://github.com/srikumarimuddana-lab/spinrvm/pull/2931) wraps the
  `safety_incidents` insert in `trigger_emergency`
  (`backend/routes/rides/safety.py`) in a try/except mirroring
  `backend/routes/safety.py:98-105`'s pattern — logs the full exception and
  returns a clean 503 instead of an unhandled 500. The two documentation
  inaccuracies noted below (PagerDuty claim, 3s hold duration) are also
  **fixed** in `.claude/context/domain-safety.md` (2026-08-01,
  `docs/change-log/2026-08-01-b15-doc-cleanup.md`). The three remaining
  sub-decisions from the original finding are now resolved as follows
  (product calls, relayed via engineering 2026-08-01 — **not** directly
  reviewed against the design docs by the product owner, noted explicitly
  per that relay):
  - **(a) Non-DB-dependent fallback (e.g. direct Twilio SMS bypassing the
    DB write) for the sustained-outage case: DECIDED — not building it.**
    Rationale: the existing 3× client retry (1s/2s backoff) plus the
    persistent amber "Not Sent — Call 911 directly" fallback UI is judged
    sufficient residual-risk mitigation for the ~3-4s outage window a
    sustained failure across all retries implies. `.claude/context/domain-safety.md`
    updated 2026-08-01 to record this as a closed decision rather than an
    open question.
  - **(b) Real on-call paging (PagerDuty/Opsgenie): DECIDED — build it,
    and it is now built**, shipped dark/disabled by default
    (`docs/change-log/2026-08-01-b15b-sos-paging.md`). New
    `backend/utils/safety_paging.py::page_on_call`, called from
    `trigger_emergency` right alongside the existing `notify_safety_team`
    call, best-effort/non-blocking (never raises, a failure is logged and
    swallowed like every other SOS side effect in that function). Config
    (`sos_paging_webhook_url`, `sos_paging_routing_key`) lives in
    `app_settings`, same pattern as Stripe/Twilio/Meta credentials —
    empty `sos_paging_webhook_url` (the shipped default, since no real
    PagerDuty/Opsgenie account exists yet) means zero HTTP calls and zero
    behavior change. `.claude/context/domain-safety.md` updated to
    describe the new channel as dark/disabled.
  - **(c) Rideless/standalone SOS path (see "Also noted" below): STILL
    UNDECIDED.** No product call has been made on this one — explicitly
    not addressed by this update, not silently dropped. Remains open.
  Checkbox stays `[ ]` solely because of (c) — (a) and (b) are both closed
  decisions now (one "won't build", one "built"), but the entry as a whole
  isn't done until (c) gets a product call too.
- **Why (original finding, now fixed by PR #2931 — kept for record):**
  `trigger_emergency` (`backend/routes/rides/safety.py:38-83`) —
  the rider/driver in-ride SOS endpoint — called
  `await _deps.db_supabase.insert_one("safety_incidents", incident)` at
  line 83 with **no surrounding try/except**. If that insert threw, the
  request 500'd before any of the subsequent steps ran: the admin WS
  broadcast, the safety-team email (`notify_safety_team`), and the
  emergency-contact SMS loop were all sequenced *after* the insert in the
  same function body. Its sibling endpoint for non-urgent reports,
  `backend/routes/safety.py:98-105` (`POST /safety/report`), wraps the
  identical-purpose insert in a try/except that logs the full exception and
  returns a clean 503 — `trigger_emergency` didn't follow that pattern.
  PR #2931 closed this gap by wrapping the insert in a try/except mirroring
  the sibling pattern. Separately, `.claude/context/domain-safety.md`
  described a rule that a DB
  failure on SOS should "fall back to direct Twilio + PagerDuty call with
  best-effort data" — grepped the whole backend for "pagerduty"
  (case-insensitive) and found zero implementation matches anywhere. What
  actually fires today on a successful SOS is a WS broadcast to the admin
  dashboard + an email to a safety distribution list + a `logger.critical()`
  line — no paging mechanism that would reach an on-call person not actively
  watching the dashboard or a log stream. The doc described intended
  behavior that was either never built or removed without a doc update;
  `.claude/context/domain-safety.md` has since been corrected (2026-08-01)
  to describe the actual channels. Real paging has since been decided and
  built — see Status (b) above and `docs/change-log/2026-08-01-b15b-sos-paging.md`.
- **Severity note (calibrated, not worst-case):** the client
  (`shared/components/SOSButton.tsx`) retries 3× (1s/2s backoff) and never
  shows a false "Alert Sent" — it only confirms success after a real 200,
  and on exhausted retries shows a persistent amber "Not Sent — Call 911
  directly" state with one-tap retry. So a transient DB blip self-heals via
  retry, and even a sustained outage across all 3 attempts never leaves the
  user believing help is coming when it isn't. The real gap is narrower:
  during a DB outage spanning all 3 client retries (~3-4s), **zero**
  emergency-contact SMS and **zero** safety-team notification fire through
  the backend path — exactly the scenario the doc's own fallback rule was
  meant to cover, and the code has no such fallback.
- **Also noted, separate/smaller finding, same trace:** the SOS endpoint has
  no rideless/standalone path — `ride_id` is a required path param and the
  handler 404s if the ride doesn't exist, contradicting the doc's payload
  example showing `ride_id?` as optional. `SOSButton.tsx` confirms this is
  intentional client-side (`if (!rideId)` shows "Emergency alert requires an
  active ride. Call 911 directly" instead of attempting a call) — so a
  rider who feels unsafe while waiting for pickup or just after drop-off has
  no in-app SOS path today, only a prompt to call 911 themselves. Product
  decision, not obviously a bug, but worth a deliberate call rather than
  silent-by-omission — **still open, not addressed by this update.** Doc
  also said hold duration is "3s"; code (`SOS_HOLD_MS`) is 1200ms — minor
  doc inaccuracy, **fixed** 2026-08-01 alongside the PagerDuty correction.
- **Files:**
  - `backend/routes/rides/safety.py` — **done**, PR #2931 wrapped the
    `insert_one` in a try/except mirroring
    `backend/routes/safety.py:98-105`'s pattern (503 on failure, full
    exception logged, never a silent 500). **2026-08-01:** also gained the
    (b) paging call — see below.
  - `.claude/context/domain-safety.md` — **done**, 2026-08-01: corrected the
    PagerDuty claim to match actual notification channels (admin WS
    broadcast + safety distribution-list email + `logger.critical()`, noted
    as a known gap rather than invented as fixed) and corrected hold
    duration 3s → 1.2s. The ride-required constraint (rideless SOS) was
    intentionally **not** touched — see "Also noted" above, it's a product
    decision, not a doc-accuracy issue. **2026-08-01 (same day, follow-up
    commit):** updated again to describe the new (b) paging channel and
    record the (a) "not building it" decision.
  - `backend/utils/safety_paging.py` — **new, 2026-08-01 (b):**
    `page_on_call` helper, provider-agnostic webhook POST (PagerDuty Events
    API v2 shape by default), reads `sos_paging_webhook_url` /
    `sos_paging_routing_key` from `app_settings`, defaults to disabled
    no-op, never raises.
  - `backend/schemas.py`, `backend/routes/admin/settings.py` — **new,
    2026-08-01 (b):** `sos_paging_webhook_url` / `sos_paging_routing_key`
    added to `AppSettings` + the admin settings API (masked routing key,
    `super_admin`-only to change, `https://` required on the webhook URL —
    same treatment as the `lms_api_base_url`/`lms_api_key` pair).
  - `backend/tests/test_sos_paging.py`, `backend/tests/test_admin_settings_lms_gate.py`
    — **new/extended, 2026-08-01 (b):** unit + integration coverage for the
    paging helper and its admin-settings gating.
  - Full before/after, risk, and rollback detail:
    `docs/change-log/2026-08-01-b15b-sos-paging.md`.
- **Approach:** insert-wrap step is done. (a) and (b) are now decided (see
  Status above — (a) won't build, (b) built dark). Only (c) remains open,
  requiring a product decision before any engineering work: whether a
  rideless/standalone SOS path should exist at all (see "Also noted").
- **Acceptance:** insert-wrap + doc-accuracy sub-items are done (PR #2931 +
  2026-08-01 doc cleanup). (a) is done (decision recorded, no code
  required). (b) is done: `page_on_call` fires with the correct payload
  shape when `app_settings` has paging configured, is a no-op when it
  doesn't, and a paging HTTP failure never blocks the SOS response — all
  three covered by `backend/tests/test_sos_paging.py`; see the change-log
  for full verification detail including what was **not** verified (no real
  PagerDuty/Opsgenie account to test against). (c) has no acceptance
  criteria yet — still undecided, entry stays open until a product call is
  made.

### B16. Driver SOS UX doesn't implement the discretion the design sketch chose it for
- [x] **Status:** CLOSED (2026-08-11) — implemented per the confirmed design
  intent (2026-08-01 relay, see below), dark-launched behind
  `app_settings.driver_discreet_sos_enabled` (default `False`). Shipped
  across two branches: `claude/b16-driver-sos-discreet-shield` (backend,
  merged PR #3596 — per-contact SOS status field, driver access to the
  trip share link, flag schema + `GET /settings` exposure) and
  `claude/b16-driver-sos-frontend` (new shared `SafetyShield.tsx` /
  `SafetyOverlay.tsx` components, `useHoldToConfirm`/`useEmergencyContacts`
  shared hooks, `useDriverSafetyTrigger`/`useDriverDiscreetSosFlag`
  driver-app hooks, and the flag-gated wiring into
  `driver-app/app/driver/(tabs)/index.tsx`). Full 12-subtask implementation
  plan (design sketch spec, exact file list, verification per subtask) is
  in the session transcript; each subtask landed as its own commit with
  its own tests.
  - **Bundled fix:** the driver-app `SOSButton` `onTrigger` swallowed its
    POST error (`try{...}catch(err){console.error}`, never rethrew) —
    `SOSButton`'s own retry/FAILED state could never activate for a real
    driver-side backend failure. Fixed as part of the wiring commit
    (rethrow instead); this is the one deliberate behavior change on the
    flag-off (default) path, called out explicitly rather than hidden.
  - **Not done, tracked as a fast-follow, not blocking closure:** (1) the
    bottom-action-bar "🛡 Safety" entry point on `ActiveRidePanel.tsx`
    (plan's subtask 12) — the shield's own hold-and-tap gestures already
    provide full functionality without it; (2) an admin-dashboard checkbox
    UI for the flag — flip via `PUT /api/admin/settings` directly until
    built; (3) the rideless/standalone SOS path question from B15(c) is
    still separately open, unrelated to this item.
  - **Verification:** backend — `pytest` across `test_p2_sos.py`,
    `test_coverage_rides.py`, `test_driver_discreet_sos_flag.py`,
    `test_public_settings.py` (new/extended, all pass). Frontend — new
    Jest/RNTL tests for every new hook and component (hold-gesture timing,
    contacts fetch, retry/backoff, flag fail-closed, shield/overlay
    render+interaction, and the invariant that a failed silent alert never
    shows `Alert.alert`): 50/51 driver-app suites pass (the one failure,
    `ActivityView.test.tsx`, is a pre-existing, unrelated
    `expo-router/react-navigation` resolution gap, confirmed via
    `git stash` to fail identically without any B16 changes present).
    `tsc --noEmit` clean on every touched file.
  - **NOT verified — explicit gap, not silently assumed covered:** no
    manual QA on a real device/simulator (gesture timing, blur/toast
    rendering, and the actual flag-off vs flag-on visual behavior aren't
    testable under Jest); no visual/snapshot regression tooling exists in
    this repo at all (standing gap); the flag has not been flipped on
    anywhere (dark-launch by design, same as every other `app_settings`
    rollout flag in this codebase).
  - Superseded text below (original finding + design rationale) kept for
    record.
- **Why:** sketch 011's stated design question is *"Can a driver call for
  help with one hand while driving [without alerting a threatening
  passenger]?"* It mocks 3 variants and explicitly rejects the
  loud/visible one: *"Full-screen red flash is visible to the
  passenger... dangerous in the scenario that needs it most."* The chosen
  winner, "Discreet Hold Shield," is dual-mode: a muted shield icon, hold
  3s → **silent** alert (no modal, no red flash — just a tiny badge + a
  small dark toast), or a short **tap** → a full Safety overlay (911
  button, "Share Live Trip Link," per-contact "✓ Notified" list, an
  explicit "Discreet mode on" label with a toggle, "I'm Safe — Close").
  Sketch 010 (rider) deliberately picked a *different* winner — tap opens
  the overlay, then a visible 2s hold inside it — because the design
  reasoning treats the rider's threat model as not requiring silence the
  way the driver's does.
  What's shipped (`shared/components/SOSButton.tsx`) is the same component
  for both apps (confirmed via grep — `driver-app/app/driver/(tabs)/index.tsx`
  and `driver-app/app/_layout.tsx` both import it, no driver-specific
  variant exists): one persistent **red** circular button, hold **1.2s**
  (matches neither sketch's 2s/3s — same hold-duration mismatch as B15,
  now cross-confirmed by a second, independent source), and on success
  fires a native `Alert.alert()` — an interruptive modal, not a silent
  confirmation. There is no silent/discreet path, no tap-vs-hold duality,
  no Safety overlay, no "Share Live Trip Link," no per-contact notified
  list, no discreet-mode toggle. The shipped driver UX is structurally
  closer to the sketch's own **rejected** Variant A than to the winning
  Variant C — the exact pattern the design process ruled out as most
  dangerous for the driver's actual threat scenario.
- **Files (original finding; superseded — see the CLOSED status block
  above for what actually shipped):**
  `shared/components/SOSButton.tsx`, `driver-app/app/driver/(tabs)/index.tsx`,
  `.planning/sketches/010-rider-sos/index.html`,
  `.planning/sketches/011-driver-sos/index.html`.
- **Approach (original — (b)/(c) resolved by the 2026-08-11 implementation,
  kept for record):** (a) is now **decided** — design intent confirmed, see
  Status above. (b) scoped as its own feature build: new
  `SafetyShield.tsx`/`SafetyOverlay.tsx` components rather than a
  `discreet` prop on `SOSButton` — `SOSButton.tsx` itself stayed untouched,
  so rider-app's own SOS UX carries zero risk from this change. (c) the
  rider/driver split was *not* abandoned — `SOSButton.tsx` remains
  rider-app's only SOS UI (sketch 010's own different winning design is
  out of scope for this item and still not implemented; not tracked here).
- **Acceptance:** met for the driver-only discreet-hold-shield build —
  hold 3s fires a silent alert (badge + toast, never `Alert.alert`); a
  short tap opens the Safety overlay (911 / alert-contacts / share-trip-
  link / per-contact notified list / "I'm Safe — Close"); dark-launched
  behind a flag with a zero-code rollback. Not part of this item's
  acceptance: flipping the flag on (operational follow-up, tracked in the
  Status block's "not done" list), the bottom-bar entry point (subtask 12,
  optional convenience), and rider-app's own sketch-010 redesign (separate,
  unopened scope).

### B17. `purge_pii_retention` Step B will FK-abort the entire daily retention purge once any paid ride crosses 7 years
- [x] **Status:** CLOSED (2026-08-10), **with an erratum found and fixed the
  same day**: the original 294 fix (`ON DELETE SET NULL`) does not actually
  work on its own. `financial_events_no_mutate` (migration 58/289) is a
  `BEFORE UPDATE FOR EACH ROW` trigger that unconditionally raises on any
  `UPDATE` it doesn't recognize (289 only carved out an exception for
  `DELETE`, gated by a GUC). PostgreSQL implements FK referential actions
  like `ON DELETE SET NULL` by issuing an internal `UPDATE` against the
  referencing table through the normal executor path — which fires that
  table's own row-level `BEFORE UPDATE` triggers exactly as a direct
  statement would (documented Postgres behavior, not an edge case). So the
  SET NULL action itself would fail when Step B deletes an old ride, and
  Step B would still abort — just with a trigger-raised `P0001` instead of a
  raw `foreign_key_violation`. Not caught by 294's own test because that
  suite is purely textual (no live Postgres in CI, same constraint every
  migration test in this repo works under) and only pinned the FK's `ON
  DELETE` clause, not its interaction with the immutability trigger. Fixed
  by `backend/migrations/295_financial_events_immutable_allows_fk_setnull.sql`,
  which extends `_financial_events_immutable()` to unconditionally (no GUC —
  Postgres's own FK machinery issues the internal UPDATE with no chance for
  application code to set a session GUC first) permit exactly one UPDATE
  shape: nulling `ride_id` with every other column pinned unchanged via
  equality checks. 8 new textual tests
  (`test_financial_events_fk_setnull_trigger_fix.py`); the Postgres semantics
  claim itself was independently verified by a second `spinr-migration-
  reviewer` pass before committing, given how consequential a wrong claim
  here would be. `docs/runbooks/data-retention.md`'s Step B section updated
  with the erratum.
  <details><summary>Original fix (294) and finding history</summary>

  **Status (2026-08-10, before the erratum):** decision taken with the user: `ON DELETE
  SET NULL` on `financial_events.ride_id` (not `CASCADE` — would delete the
  7-year CRA/SOC2 tax record itself — and not per-batch exception isolation on
  Step B — would leave every paid ride permanently un-purgeable). Implemented
  in `backend/migrations/294_financial_events_ride_id_set_null.sql`, same
  shape as migration 273's `driver_statements.driver_id → ON DELETE CASCADE`
  fix for the identical bug class in Step H: resolves the FK by column via
  `pg_constraint` (not Postgres' default name) so it converges regardless of
  how a given environment's constraint happens to be named, then re-adds it
  with `ON DELETE SET NULL`. Blast-radius grep across `backend/` found exactly
  one reader that joins on `financial_events.ride_id`
  (`utils/ledger_projection.py`), already `None`-safe since non-ride event
  types never had a `ride_id` to begin with. No live Postgres instance
  available in this session to apply the migration end-to-end (same caveat
  B0's fix carries) — verified instead via a static SQL-text contract test
  (`backend/tests/test_financial_events_ride_id_fk_contract.py`, mirroring the
  existing `test_wallet_apply_delta_contract.py` pattern) pinning `ON DELETE
  SET NULL` (not `CASCADE`), the correct target FK, and the dynamic
  constraint-name lookup. `docs/runbooks/data-retention.md` updated with
  Steps H–M (previously omitted entirely) and a new section on the two
  patterns append-only tables use when Step B/H/M reach into them (FK action
  vs. transaction-local GUC gate). Full writeup:
  `docs/change-log/2026-08-10-b17-financial-events-ride-fk-set-null.md`.
  <details><summary>History</summary>

  **Status (2026-08-07):** open — found during the PR #3464 regulatory audit,
  as an adjacent finding while fixing the *same shape of bug* in Step H
  (migration 289). Dormant, but on a fuse that starts burning 7 years after the
  first paid ride — no user action required to trigger it.
- **What breaks:** `financial_events.ride_id` references `rides(id)` with the
  Postgres default `NO ACTION` (`backend/migrations/58_financial_events.sql:28`
  — no `ON DELETE` clause). Step B runs a bare
  `DELETE FROM rides WHERE created_at < now() - 7y` with **no exception handler
  at all** (contrast Step H, which isolates per-account with
  `EXCEPTION WHEN foreign_key_violation`). Every paid ride has a retained
  `stripe_charge` header pointing at it, and **no purge step ever deletes
  non-DSAR `financial_events` rows** — so the first ride to cross 7 years raises
  `foreign_key_violation`, aborts the whole transaction, and rolls back Step A
  too, never reaching Steps C–M.
- **Why it matters more than the Step H bug this PR just fixed:** (1) *certain*
  to fire on the passage of time alone, where Step H needed a deletion request
  plus 7 years; (2) same total blast radius — GPS anonymization, ride deletion,
  chat/token/stripe-event cleanup, audit-log purge and every other regulatory
  window silently stop, repeating daily; (3) *less* protected — Step H at least
  had per-row isolation, Step B has none.
- **Options (needs a design call, not a one-liner):** NULL the `ride_id` on
  affected `financial_events` rows before Step B; or migrate the FK to
  `ON DELETE SET NULL`; or give Step B per-batch exception isolation. Each has a
  different consequence for the 7-year tax record's ability to link a charge
  back to its trip, so this is a retention-policy decision as much as a schema
  one. Note migration 289's change-log records this too, but a dated markdown
  file is not tracking — hence this entry.
- **Acceptance:** a decision recorded on which option is taken; the fix applied
  with a test that proves a 7-year-old paid ride can be purged without aborting
  the run; `docs/runbooks/data-retention.md` updated to describe Steps H–M,
  which it currently omits entirely.
  </details>
  </details>

### B18. Retention docs promise anonymize-not-delete; migration 216 implements hard-delete, and 289 makes it operative
- [x] **Status:** CLOSED for the narrower, unambiguous part of this ticket
  (2026-08-10): the anonymize-vs-delete decision itself is recorded as
  **keep Step H's hard-delete model as-is** (product-owner-directed in this
  session; NOT a substitute for real legal/founder sign-off if one is later
  required — un-shipping an already-operative model is out of scope for an
  agent session's unilateral call, and this decision doesn't require
  reversing anything, only building on top of it). Separately, and
  independent of that decision either way,
  `.claude/context/regulatory-sk.md`'s Right-to-delete #1 ("personal profile
  fields... scrubbed within 30 days") was found to be completely
  unimplemented — `delete_account_pipeda` left name/email/profile_image/
  `saved_addresses` fully live for the entire 7-year window. Fixed by
  `backend/migrations/296_pipeda_30day_profile_scrub.sql` (new Step N,
  anchored on `deletion_requested_at`, 14 new textual tests). `CLAUDE.md`,
  `regulatory-sk.md`, and `data-retention.md` reconciled to describe what
  actually ships (hard-delete at 7y, scrub at 30d) instead of the prior
  "anonymized, not deleted" claim.
  **Not closed — left as an explicit, documented gap:** `regulatory-sk.md`'s
  other promise, "rider identity linked to trip: 7 years (hashed after 2)",
  is a *general* rule (every ride, not just DSAR ones). Implementing it
  literally would null/hash `rides.rider_id` at 2 years, breaking every
  active rider's own trip-history screen and any admin/refund lookup by
  rider for rides older than 2 years — a live, real-user-facing regression
  needing real product/legal scoping before any code change, not something
  to ship under this ticket's momentum. Re-filed as its own follow-up rather
  than silently left in this closed ticket — see the new item filed
  immediately below this one's history.
  <details><summary>Original finding (2026-08-07)</summary>

- **The divergence:** three governing documents state that records are
  *anonymized* after the retention window —
  `.claude/context/regulatory-sk.md:45,87` ("rows are anonymized (user_id
  nulled, coordinates rounded to city centroid), **not deleted** — preserves
  statistical continuity for regulatory reporting"), `CLAUDE.md` §Compliance
  →"Deletion" ("Ride records become anonymized"), and
  `docs/runbooks/data-retention.md`, which additionally omits Steps H–M
  altogether. `backend/migrations/216_deletion_hard_delete_no_anonymize.sql:1-4`
  instead implements "the Uber/Lyft attributable-retention model — **NO
  anonymization**", hard-deleting DSAR accounts at 7 years.
- **Why now:** Step H has been inert since 216 shipped (it aborted on the
  migration-58 trigger). Migration 289 fixes that abort, so this PR is what
  makes the hard-delete path **operative in production for the first time**.
- **Note on substance:** hard-delete is not *less* PIPEDA-compliant than
  anonymize — arguably it is a stronger privacy outcome — and the 7-year floor
  is honored either way. The exposure is (a) the repo's own docs promise a
  different outcome than what ships, and (b) `data-retention.md:42-47` requires
  legal + founder sign-off for exactly this kind of change, recorded in the same
  PR, and no such sign-off is visible for 216 or 289. The anonymize rationale
  ("statistical continuity for SGI regulatory reporting") is a real tradeoff
  being foreclosed without a recorded decision.
- **Acceptance:** a recorded legal/founder decision on anonymize-vs-delete; the
  three documents reconciled with whichever is chosen; `data-retention.md`
  extended to cover Steps H–M.
  </details>

### B23. `regulatory-sk.md` promises rider identity is hashed after 2 years; nothing implements it, and the literal fix breaks live rider trip history
- [ ] **Status:** open — split out of B18 (2026-08-10) when B18's narrower
  30-day-scrub part closed. `.claude/context/regulatory-sk.md`'s trip-log
  retention table (line 43) promises "Rider identity linked to trip: 7 years
  (hashed after 2)" — a *general* rule for every ride, not just DSAR-deleted
  accounts. Nothing in `purge_pii_retention()` or anywhere else implements
  any 2-year hashing step.
- **Why this isn't a copy-paste of B18's Step N:** Step N (30-day profile
  scrub) only touches accounts that explicitly requested deletion — a small,
  self-selected population that has already agreed to lose access to their
  own history. This promise is different: it would apply to every active
  rider's ride record at 2 years regardless of whether they ever asked for
  anything, and `rides.rider_id` is the FK a rider's own "my trips" screen,
  and any admin/support/refund lookup by rider, actually joins on. Hashing
  or nulling it at 2 years breaks that lookup for every ride older than 2
  years, for every still-active user — a live, real-money-adjacent,
  real-user-facing regression, not a narrow backend fix.
- **Options needing a real product/legal decision, not an agent's unilateral
  call:** (a) exclude this from ride-level hashing entirely and treat the
  regulatory-sk.md line as describing a data-warehouse/reporting-layer
  concern rather than the live `rides` table rider queries and admin tools
  actually read; (b) a separate, access-controlled identity-lookup table
  that `rides` keeps pointing at (rather than `users` directly) so a 2-year
  "hash the direct link" step has something narrower to act on without
  touching the column every live query depends on; (c) accept the doc
  overstates the actual promise and correct `regulatory-sk.md` to match
  reality instead (this is itself a compliance-relevant document change per
  CLAUDE.md's regulatory-doc conventions, so it needs the same sign-off any
  other windows-table change would).
- **Severity:** dormant in the sense that nothing breaks today, but every
  day this stays open is a day `regulatory-sk.md` states something false
  about what the product does — an audit-readiness gap, not a code bug.
- **Acceptance:** a recorded decision on which option (or another) is taken;
  either an implementation matching it with a test proving no live rider
  trip-history/admin-lookup regression, or `regulatory-sk.md` corrected to
  match what's actually promised — not left silently divergent either way.

### B19. `payment_retry`'s `requires_capture` hold-recovery still uses the non-atomic two-write settlement
- [x] **Status:** CLOSED (2026-08-10) — found 2026-08-07 by the money-auditor pass on PR #3464.
  Fixed exactly as the Acceptance line below specifies: the `requires_capture` branch now
  calls `_finalize_card_settlement` (imported cross-module, same precedent as
  `utils/stripe_reconcile.py` and `routes/webhooks.py` already importing
  `_tip_ride_update` from `services/payment_service.py`) instead of its own
  `record_payment_event` + separate `update_ride`. `tip_d` passed in is the
  ride's own already-stored `tip_amount` (not a new tip), so
  `_finalize_card_settlement`'s `_tip_ride_update` always computes a zero
  delta and never touches `driver_earnings` — safe even though this loop's
  `SELECT` omits that column (documented inline so a future change to what's
  passed doesn't silently break it). The two existing `requires_capture`
  tests were updated to mock at the finalizer's own dependencies
  (`record_payment_event`, `db_supabase.update_ride`,
  `manager.send_personal_message`) instead of this module's `db.update_one`
  for the paid write, plus an exactly-one-header assertion mirroring
  `test_atomic_settle.py`'s matrix, per the acceptance criterion. 165 tests
  across the payment/loop surface (`test_payment_retry.py`,
  `test_payment_retry_coverage.py`, `test_replay_safety_payment_loops.py`,
  `test_atomic_settle.py`, `test_coverage_payments.py`, plus
  `test_cancellation_fee_card_charge.py`/`test_guest_auto_settle.py`/
  `test_payment_exhausted_alert_once.py`/`test_e4_d10_payment_3ds_quests.py`/
  `test_stripe_charge.py`) pass. See git history for the commit.
- **What:** `backend/utils/payment_retry.py` (the `requires_capture` branch) still
  does `Stripe capture → record_payment_event → separate update_ride` — the exact
  sequence PR #3464 replaced in `settle_card`'s two success paths with the atomic
  `settle_ride_card_payment` RPC. It was not wired to `_finalize_card_settlement`.
- **Severity:** low-moderate. It *does* inherit the durable-retry + Sentry
  escalation for free (it goes through the now-centralized
  `ledger_service.record_event`), so a lost header there is no longer silently
  swallowed — but the process-death window between the capture and the two writes
  is still open on this path. PR #3464 declared its blast radius as "the two
  settle_card success paths only", so this is a known partial close, not a
  regression.
- **Acceptance:** route the branch through `_finalize_card_settlement` so it picks
  up the flagged atomic path, with a test mirroring `test_atomic_settle.py`'s
  exactly-one-header matrix.

### B20. Ledger projection can misclassify a tip when a ride is stuck unpaid
- [x] **Status:** CLOSED (2026-08-10) — found 2026-08-07 by the money-auditor pass on PR #3464.
  Fixed with the source-aware settlement check the Acceptance line specifies:
  `_decompose`'s default (fare-settlement) branch now checks
  `ride.get("payment_status") != "paid"` and books DEGRADED (whole amount to
  `platform_revenue`, escalated to Sentry via the existing `escalate()` call)
  instead of silently decomposing from possibly-stale `driver_earnings`/
  `tax_amount` — reason `"ride_not_yet_settled"`. Deliberately **not**
  skip-and-retry, per this item's own warning: a skip would leave the event
  with no legs, sitting at the head of `financial_events_missing_legs`'
  oldest-first queue forever if the ride is never recovered, reintroducing
  the exact starvation the RPC's own filters exist to avoid. Source-aware by
  construction (not by an extra branch condition): `cancellation_fee` and
  `scheduled_cancel_notice_fee` events return earlier in `_decompose` and
  never reach this check, so a legitimately-cancelled (never `'paid'`) ride
  is unaffected — added a test proving exactly that
  (`test_cancellation_fee_on_unpaid_ride_is_not_affected_by_the_paid_check`).
  **Found and fixed in the same change:** `_RIDE_COLUMNS` (the projection's
  ride `SELECT`) omitted `payment_status` entirely — without adding it, the
  new check would have read `None != "paid"` and degraded **every** fare
  charge unconditionally, the opposite of the intended fix, with no
  in-memory `_decompose` unit test able to catch it (they build their own
  ride dicts, independent of what the SELECT fetches) — added a dedicated
  membership test (`test_ride_columns_fetch_payment_status`) mirroring the
  existing `discount_amount` one, which was added for the same reason after
  a prior promo-ride regression. 23 tests in `test_ledger_projection.py`
  pass (18 pre-existing + 5 new), plus 122 across the payment/loop test
  surface (`test_replay_safety_payment_loops.py`, `test_atomic_settle.py`,
  `test_payment_retry.py`, `test_payment_retry_coverage.py`,
  `test_coverage_payments.py`). See git history for the commit.
- **What:** `backend/utils/ledger_projection.py::_decompose` (default fare branch)
  reads `rides.driver_earnings` / `tax_amount` at projection time without checking
  `rides.payment_status`. Migration 287's 30-minute grace window covers the normal
  header-before-`update_ride` gap, but not a ride whose post-charge DB update failed
  and wasn't recovered within ~30-45 min (the path that returns 503 + a Sentry page).
  In that case the projection reads pre-tip earnings and books the tip to
  `platform_revenue` instead of `driver_payable`.
- **Severity:** low, and confined to the internal accounting overlay.
  `rides.driver_earnings` (the actual payout figure feeding T4A and driver
  statements) and `financial_events.delta_cents` (the tax record) are both
  unaffected, and the resulting legs still balance — so no unbalanced-legs alert
  fires and no driver is underpaid. It is a mis-attribution inside the double-entry
  view only.
- **Why not fixed inline:** the obvious fix (add `rides.payment_status = 'paid'` to
  migration 287's work-queue filter) is wrong as stated — cancellation-fee and
  notice-fee events legitimately point at rides that are cancelled, not paid, and a
  blanket filter would exclude them from projection permanently. A correct fix has
  to be source-aware, and the Python-side alternative (skip and retry next tick)
  reintroduces the head-of-queue starvation risk the degraded-legs design exists to
  avoid. Needs a deliberate design pass, not a one-liner.
- **Acceptance:** source-aware settlement check with no starvation regression, plus
  a projection test covering a stuck-`processing` fare ride.

### B21. Background-loop lock TTL is longer than the sleep, halving several loops' cadence
- [x] **Status:** CLOSED (2026-08-10) — found 2026-08-08 reviewing PR #3464. All 4 loops
  listed below now use `TTL = interval * 0.85` (matching `ledger_projection.py`'s
  existing fix — 0.05 headroom under the `1 - jitter_fraction` floor), with
  the same two regression tests per loop (`test_lock_ttl_expires_before_the_earliest_next_wake`,
  `test_..._loop_reacquires_its_own_lock_on_the_next_wake`) added to each
  loop's own `*_coverage.py` test file, adapted for `orphaned_hold_reconciler.py`'s
  extra unconditional startup jitter sleep. `payment_retry.py`'s misleading
  comment corrected. 113 tests across the affected loop/reaper test files
  pass. See git history for the 3 commits (payment_retry.py first per this
  item's own "look at first" guidance, then the two reapers together, then
  orphaned_hold_reconciler.py).
- **What:** the shared loop-shell idiom sets the Redis throttle lock with
  `TTL = interval * 1.5` and then sleeps `interval` (± jitter). The pod that ran
  the last tick therefore wakes while its OWN key is still alive, fails `SET NX`,
  and sleeps another full interval — so the loop actually runs every ~2 intervals.
  `payment_retry.py:629` states the intent explicitly and gets the arithmetic
  backwards: *"TTL is 1.5× interval so a real lock expires before the next
  election."* It does not.
- **Where:** `utils/payment_retry.py` (5 min → ~10), `utils/driver_claim_reaper.py`,
  `utils/offer_expiry_reaper.py`, `utils/orphaned_hold_reconciler.py`
  (`interval * 2` → ~3 intervals). Not audited beyond this list.
- **Severity:** low individually, but it silently halves throughput on sweeps
  whose whole purpose is bounded recovery latency — `payment_retry` is the one to
  look at first, since a failed payment waits ~10 min per attempt rather than 5.
  With several replicas the aggregate cadence lands nearer 1.5× than 2×, so it
  degrades quietly rather than visibly.
- **Why not fixed here:** each loop has its own interval, jitter, and multi-replica
  behaviour, and shortening the TTL trades exclusivity for cadence — safe only
  where the tick is idempotent by construction (as the projection's is, via
  `UNIQUE(event_id, account, side)`). `payment_retry` has the atomic
  `payment_status → retrying` claim so it is very likely also safe, but that is a
  money path and deserves its own change, not a drive-by.
- **Acceptance:** per-loop TTL below the minimum sleep, each with a test pinning
  the invariant (see `test_lock_ttl_expires_before_the_earliest_next_wake` and
  `test_projection_loop_reacquires_its_own_lock_on_the_next_wake`), and
  `payment_retry.py`'s misleading comment corrected.
- **2026-08-11 addendum:** a 5th instance of the identical bug found in
  `utils/suspension_reactivation.py` while working N7 (ACTION_ITEMS.md) —
  this item's own "not audited beyond this list" caveat held. Fixed with
  the same `interval * 0.85` formula and test pattern (2 new tests in
  `test_suspension_reactivation_coverage.py`). If another instance turns
  up, it's the same root cause, not a new item — extend this note rather
  than filing separately.

### B22. `G4a · pip-audit` is red on four advisories in three backend dependencies
- [x] **Status:** CLOSED (2026-08-11) — all three dependencies now bumped
  (`pypdf` 2026-08-10, `cryptography` and `h2` 2026-08-11). See the two
  dated updates below for each fix's own verification detail.
- **Status (history):** open — observed 2026-08-09 on PR #3464, which changed **no** dependency
  manifest (`git diff origin/main...HEAD -- '*requirements*'` is empty), so these are
  pre-existing on `main`. Filed rather than fixed in that PR, per release gate 8: a red
  gate left unexplained decays into one people stop reading.
- **Also explains `G6 · Trivy container scan` failures, not just `G4a`** — confirmed
  2026-08-10 on PR #3494: pulled the actual failing Trivy job log rather than
  assuming, and its one blocking finding is `cryptography` 49.0.0, `CVE-2026-69247`,
  HIGH, fixed in `50.0.0` — same package/pinned-version/fix-version triple as the
  `cryptography`/`PYSEC-2026-3552` row below, just surfaced under Trivy's own CVE
  alias rather than the PYSEC id `pip-audit` uses. Same root cause, same fix, same
  "needs deliberate JWT-path verification before bumping" caution — not a second,
  separate gap. Matches the precedent already noted in C6 (a PyJWT bump "also
  clearing the overlapping `G6 · Trivy container scan` findings") — one dependency
  pin, two scanners, two red gates. Do not file a new item if `G6` shows red on
  `cryptography` again; it's this one.
- **What:**

  | Package | Pinned | Advisory | Fixed in |
  |---|---|---|---|
  | ~~`cryptography`~~ | ~~49.0.0~~ → **50.0.0 (bumped 2026-08-11)** | PYSEC-2026-3552 (aka `CVE-2026-69247` per Trivy/`G6`) | 50.0.0 |
  | ~~`h2`~~ | ~~4.3.0~~ → **4.4.1 (bumped 2026-08-11)** | CVE-2026-71554 | 4.4.1 |
  | ~~`pypdf`~~ | ~~6.14.2~~ → **6.15.0 (bumped 2026-08-10)** | CVE-2026-71870, CVE-2026-71852 | 6.15.0 |

  **2026-08-11 update — `h2` bumped, done, item fully CLOSED:** `h2` is now
  `4.4.1` in `backend/requirements.txt`/`requirements-locked.txt` (its own
  `hpack` sub-dependency moved 4.1.0→4.2.0 automatically). Investigated the
  "riskiest" framing below directly rather than assuming it still held:
  `backend/supabase_client.py` **already runs the Supabase/PostgREST
  connection on HTTP/1.1**, not HTTP/2 — deliberately disabled in a prior
  fix after HTTP/2 GOAWAY frames caused real production `h2` thread-safety
  bugs (`RuntimeError: deque mutated during iteration` in h2's hpack table).
  So `repositories/_base.py`'s GOAWAY/`_HTTPX_NETWORK_EXC` retry handling —
  the reason this was flagged as riskiest — isn't actually exercised by
  live HTTP/2 traffic today. Grepped the whole backend for `http2=True`:
  exactly two call sites exist — `supabase_client.py` (disabled, as above)
  and `utils/apns_client.py:167` (Apple's APNs API requires HTTP/2 — the
  **actual** live `h2` consumer). Verified with a real `pytest` run: DB
  circuit-breaker/GOAWAY tests + the real APNs client tests together
  (`test_db_circuit_breaker.py`, `test_dispatch_db_errors.py`,
  `test_drivers_extended.py`, `test_corporate_repo_coverage.py`,
  `test_apns_client.py`, `test_apns_client_coverage.py`) — 226/226 pass.
  Broader DB-infrastructure sweep (`test_db.py`,
  `test_db_circuit_breaker_probe.py`, `test_db_error_branching.py`,
  `test_db_executor.py`, `test_db_supabase_helpers.py`,
  `test_health_db_ping.py`, `test_error_handling.py`,
  `test_dual_import_parity.py`, `test_monitoring_health.py`,
  `test_capacity_watchdog.py`) — 162 passed, 1 skipped, 0 failed. Full
  backend suite also completed clean: **10,698 passed, 8 skipped, 1
  xfailed, 0 failed** (649.71s) with `cryptography==50.0.0` and
  `h2==4.4.1`/`hpack==4.2.0` installed together. Local
  `pip-audit -r requirements-locked.txt` confirms both `h2 4.4.1` and
  `hpack 4.2.0` report zero vulnerabilities. **Not verified: real Docker
  build/Trivy re-scan** (no daemon in this session) or a real APNs
  sandbox/production push. Full writeup:
  `docs/change-log/2026-08-11-b22-h2-cve-bump.md`.

  **2026-08-11 update — `cryptography` bumped, done:** `cryptography` is now
  `50.0.0` in `backend/requirements.txt`/`requirements-locked.txt`, regenerated
  via `pip-compile --upgrade-package cryptography==50.0.0` (Python 3.12,
  matching CI's toolchain) — diffed both files line-by-line, confirmed no other
  package's pin moved. Direct-importer blast radius: `utils/sns_verify.py` (SNS
  webhook signature verification, `hazmat`/`x509`) plus the transitive JWT auth
  path via `pyjwt`/`google-auth`. Verified with a real `pytest` run against the
  new version (not reasoned about): 29 auth/JWT/token/crypto-adjacent test
  files, 665 passed/1 skipped/1 failed — the 1 failure is the already-documented
  `test_utils_extended.py` contextvar-leak pollution bug (A8-class), confirmed
  unrelated by running the affected file standalone (63/63 pass). Separately,
  `test_ses_webhook.py`'s **real** (non-mocked) RSA-generate→sign→verify
  roundtrip against `sns_verify.verify_sns_signature()` plus its two webhook
  coverage files: 118/118 pass. Local `pip-audit -r requirements-locked.txt`
  confirms `cryptography 50.0.0` now reports zero vulnerabilities. **Not
  verified: a real Docker build / Trivy re-scan** — no Docker daemon available
  in this session (same limitation as CR-2026-002); `G4a`/`G6` both going green
  is the final confirmation, to be checked once this merges and CI runs for
  real. Full writeup: `docs/change-log/2026-08-11-b22-cryptography-cve-bump.md`.

  **2026-08-10 update — `pypdf` bumped, done:** `pypdf` is now `6.15.0` in
  `backend/requirements.txt` and `backend/requirements-locked.txt` (new hashes
  pulled from PyPI for both the wheel and sdist). `h2` is **still open** — do
  not close this item's checkbox until it is also resolved or has a `[CR]`.
  Usage of `pypdf` is confined to
  `backend/services/data_transfer/sgi_form_filler.py` (fills the SGI regulatory
  AcroForm PDFs); the affected test files
  (`tests/test_sgi_form_filler.py`, `tests/test_driver_statement_pdf.py`,
  `tests/test_corporate_statement_pdf.py`) plus the broader PDF suite
  (`tests/test_corporate_statement_pdf_routes.py`, `tests/test_receipt_pdf.py`,
  `tests/test_subscription_invoice_pdf_coverage.py`,
  `tests/test_t4a_pdf_coverage.py` — none of which import `pypdf` directly, run
  for safety) all pass unmodified against `pypdf==6.15.0`, 71 tests total,
  0 failures. `h2` still needs its own careful verification per the notes below
  before this item can close.

- **Why it was not bumped inline:** gate 8 also says not to force a fix that breaks
  something else to turn a check green. Each of these needs its own verification:
  - **`h2`** is the riskiest. `repositories/_base.py` has bespoke retry handling for
    HTTP/2 `GOAWAY` and `httpx.NetworkError` (`run_sync`'s `_HTTPX_NETWORK_EXC` path) —
    the entire DB layer's transient-failure behaviour sits on top of this library. A
    version bump needs the DB retry/circuit-breaker tests run deliberately, not
    incidentally.
  - **`cryptography`** is pulled in via `google-auth` and `pyjwt`; a major bump
    (49 → 50) can move JWT signing/verification behaviour, which is the auth path.
  - **`pypdf`** is the mildest (document generation), and is the one worth doing first
    as a standalone change to confirm the gate goes green for the right reason.
- **Severity:** unknown until each advisory is read. The exploitability of an `h2` or
  `cryptography` CVE in *our* usage may well be nil, but "probably fine" is not the same
  as a documented accepted risk — which is the other outcome gate 8 allows, via a `[CR]`
  (`.github/ISSUE_TEMPLATE/ci_change_request.yml`).
- **Acceptance:** either each dependency bumped with its affected tests actually run, or
  a `[CR]` per advisory recording the accepted risk and why. Not a silent red check.
  For `cryptography` specifically, both `G4a` and `G6` going green together is the
  signal the fix is real (same finding, two gates) — one clearing without the other
  would mean the fix didn't actually reach the built image.

### B24. `G4b · yarn audit` / `G4c · npm audit` (JS deps) red on pre-existing transitive findings — blocks all open dependency-bump PRs identically
- [x] **CLOSED 2026-08-11.** Same shape as B22 (which covers `G4a · pip-audit`
  only) but for the JS side — no existing item covered this half.
- **Why:** found while verifying whether the 10 open dependabot PRs
  (#3473–#3487, admin-dashboard + backend) would break anything. `G4b`
  and/or `G4c` are red on **9 of the 10**, spanning bumps with nothing in
  common (`@radix-ui/react-alert-dialog`, `lucide-react`, `motion`, the
  admin-dashboard `testing` group, `next`, `hpack`, `pillow`, the backend
  `security-critical` and `testing` groups) — the pattern itself says
  shared root cause, not nine separate regressions. Confirmed directly
  rather than left as inference: ran `npm audit --audit-level=high` against
  a **clean `main` checkout with zero dependabot changes applied**
  (`admin-dashboard`) and got the identical findings —
  `brace-expansion` (HIGH, GHSA-rgw5-rvv9-x895), `fast-uri` (HIGH,
  GHSA-7p8r-x3mc-p8w7), `dompurify` (moderate, GHSA-55q2-fjhq-7xh7), `hono`
  (moderate, GHSA-8j4g-w8fx-2239) — all transitive, none traceable to a
  package any of these 10 PRs touch. `G4b`'s `rider-app`/`driver-app`
  matrix legs were not independently re-run against a clean `main` in this
  pass (no worktree with `node_modules` installed for those two apps at the
  time) — inferred from the same repeated-across-unrelated-PRs pattern, not
  independently confirmed the way admin-dashboard's `npm audit` was.
- **Not the same as B22:** B22 (`pip-audit`, backend Python:
  `pypdf`/`cryptography`/`h2`) is fully CLOSED as of 2026-08-11. This is
  the JS-side sibling gate — different scanner, different lockfiles,
  different packages, still open.
- **Fix:** bump each flagged package to its patched range in the relevant
  `package.json`/lockfile (`admin-dashboard`, `rider-app`, `driver-app`
  each maintain their own), following B22's per-package pattern — bump,
  run that surface's real test suite against the new version, confirm the
  specific gate goes green, don't bundle unrelated bumps into one commit.
  `brace-expansion`/`fast-uri`/`dompurify`/`hono` are almost certainly
  transitive (pulled in via a direct dependency, not listed directly in
  `admin-dashboard/package.json`) — first step for whoever picks this up
  is `npm ls <package>` per finding to identify the direct dependency to
  bump, same as B22 did for `h2`→`hpack`.
- **Severity:** unknown per-advisory until read, same caveat as B22 —
  document via `[CR]` (`.github/ISSUE_TEMPLATE/ci_change_request.yml`) for
  any finding judged not exploitable in Spinr's actual usage, rather than
  leaving the gate silently red.
- **Acceptance:** each flagged package bumped with its consuming
  surface's real test suite run against the new version, or a `[CR]` per
  advisory. `G4b` (all three JS matrix legs) and `G4c` both green is the
  signal the fix is real, same "two gates, one fix" logic as B22's
  `cryptography`/`G4a`+`G6` pairing.
- **Resolution (2026-08-11):** all three surfaces re-audited fresh (not the
  stale list above — `rider-app`/`driver-app` use `yarn audit`, not `npm
  audit`; `admin-dashboard` uses `npm audit`):
  - **admin-dashboard** (`npm audit fix`, resolves within existing semver
    ranges declared by parents — no `package.json` change, lockfile only):
    `brace-expansion` 5.0.8→5.0.9, `dompurify` 3.4.12→3.4.13, `fast-uri`
    3.1.4→3.1.5, `hono` 4.12.32→4.13.1, `ip-address` 10.2.0→10.5.0,
    `js-yaml` 4.3.0→4.3.1, `nanoid` 3.3.16→3.3.18. `npm audit
    --audit-level=high` → 0 vulnerabilities.
  - **rider-app** and **driver-app** (Yarn Classic 1.22.22, no `npm audit`
    equivalent — fixed via `package.json`'s `resolutions` block, since both
    apps pin transitive deps that way already): bumped the existing
    `js-yaml` floor `^4.3.0`→`^4.3.1` and `fast-uri` floor `^3.1.4`→`^3.1.5`;
    added `"nanoid": "^3.3.17"`. `brace-expansion` needed 4 **scoped**
    resolutions, not one blanket pin — it's present via 3 independent
    incompatible-major chains simultaneously (`minimatch`→1.x,
    `glob`/`@expo/fingerprint`/`@typescript-eslint/typescript-estree`→5.x)
    and a blanket resolution would force one major onto a consumer that
    doesn't want it. Yarn 1's syntax for a deeply-nested scoped resolution
    needs a `**/` glob prefix — a first attempt without it
    (`"minimatch/brace-expansion"`) silently no-opped, confirmed via `yarn
    why brace-expansion` showing unchanged versions before switching to
    `"**/minimatch/brace-expansion": "^1.1.18"` etc., which collapsed every
    instance to a single patched version in both apps. `driver-app` turned
    out to have the identical chain shape as `rider-app` (same 3 parents),
    so the same 4 scoped keys applied unchanged.
  - **Unpatchable, left as-is, documented rather than silently dropped:**
    `image-size` (HIGH, GHSA — via `expo > @expo/cli > @expo/metro >
    metro > image-size`) in both `rider-app` and `driver-app` — upstream
    has no patched version yet ("No patch available" per the npm
    advisory). This is a genuine gap, not a fix we chose to skip; re-check
    on the next dependency-bump pass rather than assuming it's resolved.
  - `admin-dashboard`'s `dompurify`/`hono`/`ip-address` findings named in
    the original writeup above were fixed incidentally by the same `npm
    audit fix` run (they were transitive siblings of the packages actually
    named in the `[ ]` acceptance criteria) — not a separate pass.
- **Verification:** real test suites run against the bumped versions in
  every surface, not just the audit tool re-run:
  - `admin-dashboard`: `npx vitest run` (160/160), `npx tsc --noEmit`
    (clean), a real `npm run build` (succeeded, full route manifest
    printed — not just dev server/`tsc --noEmit`, per this repo's
    CLAUDE.md convention), `npx eslint .` (0 errors, pre-existing warnings
    only).
  - `rider-app`: `npx jest --silent` → 440/440 passed, 52/52 suites. (A
    handful of non-fatal "Jest environment torn down" warnings appeared in
    the output from `privacySettingsToggles.test.tsx` — that file was not
    touched by this change and the suite fully passed, so this reads as
    pre-existing async-teardown noise, not a regression, though it was not
    independently bisected against a pre-change run to prove it.)
  - `driver-app`: `npx jest --silent` → 364/364 passed, 51/51 suites, no
    warnings.
  - Confirmed via `yarn audit --level high --json` in each app that the
    fix actually landed (not just that `yarn install` succeeded): both
    `rider-app` and `driver-app` went from `{brace-expansion, image-size}`
    down to just `{image-size}` (the known-unpatchable one above).
- **What was NOT verified:** this fix bumps versions but does not add any
  new automated dependency-audit regression tooling — the next unrelated
  dependency bump on any of these 3 surfaces can reintroduce a similar
  finding and won't be caught until CI's `G4b`/`G4c` next runs. No visual
  or E2E verification was done for any of the 3 apps (pure dependency
  version bumps, no application code touched) — reasoned as low-risk given
  every real test suite passed, not screenshotted/manually driven.

### B25. Maestro real-device mobile E2E (`.github/workflows/maestro-e2e.yml`) is wired but never fires — missing secrets, opt-in-only trigger, no iOS lane
- [ ] **Status:** open. Found while explaining the Playwright vs. Maestro
  split to a user (2026-08-11) — not a new regression, a pre-existing gap
  that was never tracked here.
- **Why this matters:** `rider-app/e2e/` and `driver-app/e2e/` (Playwright)
  only exercise the Expo **web export** (react-native-web), with backend
  API, WebSocket, Google Maps, and Firebase all mocked via `page.route()`.
  That suite cannot reproduce a native-module-only bug (the workflow's own
  header comment cites #3174 as the motivating example). `.maestro/rider/`
  and `.maestro/driver/` (12 YAML flows: 5 rider + 7 driver, covering
  login, ride request/cancel, schedule+cancel, SOS, mid-trip chat,
  go-online, accept-ride, verify-OTP, complete-trip, payout, in-trip chat)
  are the only thing in this repo that drives a real native build as an
  actual user would. `maestro-e2e.yml` exists and is a real implementation
  (EAS-builds a native Android APK on the `test` profile, uploads it to
  Maestro Cloud's hosted device farm, runs the flows there) — it is not a
  stub — but it currently cannot run at all.
- **Blockers, per the workflow's own inline comments:**
  1. Two required secrets are undocumented-as-present: `EXPO_TOKEN` (EAS
     Build permission) and `MAESTRO_CLOUD_API_KEY` (from
     console.mobile.dev, upload+run permission). Every run fails at the
     corresponding CLI login step until both exist — confirm with whoever
     owns repo secrets whether either has actually been added; do not
     assume from the workflow file's presence that they have.
  2. Trigger is `workflow_dispatch` or a PR labeled `run-maestro` only —
     by design, for EAS/Maestro Cloud billing discipline — so even once
     the secrets exist, this never runs passively; someone has to
     dispatch it or apply the label.
  3. iOS has no lane at all. `eas.json`'s `"test"` build profile has no
     iOS override because no Apple Developer credentials are configured
     in EAS yet. The workflow comment says to add an iOS job "once Apple
     credentials are provisioned" — that provisioning hasn't happened.
- **Historical drift (context, already resolved but worth knowing):**
  several audit docs (`reports/remediation/driver-new-issues-2026-04-23.md`,
  `reports/audits/2026-04-23-driver-P4-verification.md`,
  `reports/audits/OPEN-ITEMS-TRACKER.md`) flag that the original P4-5
  remediation item was scoped as Maestro E2E flows, but the team shipped
  Playwright-style web-export specs instead, leaving the `.maestro/` YAML
  flows "previously unwired to any CI job" (direct quote from
  `maestro-e2e.yml`'s own header) until this workflow was added. The
  flows and the workflow both exist now — the remaining gap is purely
  "never actually fires," not "doesn't exist."
- **Action:**
  1. Confirm with a repo/org admin whether `EXPO_TOKEN` and
     `MAESTRO_CLOUD_API_KEY` are already set; add them if not.
  2. Run the workflow once (`workflow_dispatch`, `apps: both`) to prove
     the Android lane actually completes end-to-end against Maestro
     Cloud, not just that CI YAML parses.
  3. Decide whether `run-maestro` should be applied automatically (e.g.
     via label-on-path-touch for `rider-app/`/`driver-app/` native code)
     rather than purely manual, given it's currently opt-in-only and easy
     to forget on a PR that actually needs native-device coverage.
  4. Provision Apple Developer credentials in EAS and add an iOS
     `eas.json` "test" profile override + iOS job to this workflow —
     until then there is zero real-device E2E coverage for iOS, Playwright
     included (Playwright's `chromium` project doesn't emulate iOS Safari
     / native iOS behavior either).
- **What was NOT verified:** whether the two secrets are actually present
  in repo/org settings (no access to check from this session — this item
  is written assuming they may be missing, per the workflow's own
  caveat, not confirmed missing); whether `run-maestro` has ever been
  applied to a real PR; whether a Maestro Cloud account/org is even
  provisioned yet at console.mobile.dev.

### B26. Regina (main, non-airport) service area shows `pst_enabled=false` despite `pst_rate=6` already set and a prior change log claiming it was enabled
- [ ] **Status:** open — **NOT fixed, still needs confirmation before touching**.
  Discovered 2026-08-12 as a side effect of the B8 "Regina Airpot" rename
  blast-radius check (unrelated task; querying `service_areas` to confirm
  it was safe to rename surfaced this by coincidence). **2026-08-13:** wrote up
  a standalone escalation brief for whoever picks this up with real
  research/legal access — timeline, the exact ask, who should answer it, and
  what to do once resolved, so this doesn't need to be re-explained from
  scratch a third time:
  `docs/compliance/2026-08-13-sk-pst-rideshare-determination-needed.md`.
  Still blocked; no data or code changed by this pass.
- **Research attempted 2026-08-12, inconclusive — asked the user directly
  whether `Regina.pst_enabled=false` is a bug or intentional; the user's
  answer was "PST is not applicable for Saskatchewan we might want to
  verify by researching" — directly contradicting the 2026-08-11 change
  log's claim that the user had confirmed PST *does* apply. Attempted to
  verify against the actual Government of Saskatchewan PST bulletin
  (PST-46 "Service Enterprises", the most relevant one for whether
  passenger-transportation/ride-sharing is an enumerated taxable service)
  and could not — `WebFetch` returned `EGRESS_BLOCKED` for every domain
  tried in this session's environment (`sets.saskatchewan.ca`,
  `www.saskatchewan.ca`, `canada.ca`, and third-party tax-law summary
  sites), so no primary source could be read directly. `WebSearch`'s own
  AI-summarized snippets leaned toward **GST-only for ride-sharing, PST
  not applicable** (one summary: *"for ride-sharing services specifically,
  the federal GST applies, not the provincial PST"*; another noted PST
  marketplace-facilitator rules cited for Uber in Saskatchewan apply to
  Uber Eats, not ride-sharing) — but these are second-hand AI summaries of
  search snippets, not verified primary-source text, and not trustworthy
  enough to act on for a live regulatory/tax determination. **Do not treat
  this research as resolving the question either way** — it only shows the
  2026-08-11 "PST applies" determination is now in genuine doubt, from two
  independent angles (the user's own current recollection, and an
  unverified-but-suggestive web search), not that "PST does not apply" is
  confirmed.
- **Also relevant, discovered as of this same date**: A29
  (`docs/change-log/2026-08-12-a29-tax-config-audit-justification.md`,
  merged same day, unrelated session) added a written-justification +
  dedicated audit-log requirement (`tax_config_updated`) to the *admin API*
  path for any GST/PST/HST field edit (`admin_update_service_area`). Any
  eventual fix to this item should go through that path (or at minimum
  record an equivalent justification + Change Impact Log) rather than a
  bare direct-SQL `UPDATE`, now that the org has explicitly recognized tax
  config changes as needing that discipline.
- **Root cause:** unconfirmed, and that's the point of this item — live data
  directly contradicts a prior session's own Change Impact Log.
  `docs/change-log/2026-08-11-sk-pst-enable.md` (2026-08-11, same day as the
  original B8 investigation, different session) states it applied
  `pst_enabled: false→true, pst_rate: 0→6` to "the 4 real Saskatchewan rows
  (Saskatoon, Saskatoon Airport, Regina, Regina Airport)" and explicitly
  lists as verification: *"Verified via direct Supabase query that only the
  4 real Saskatchewan `service_areas` rows were updated (`UPDATE ...
  RETURNING` showed exactly 4 rows, all correctly named)"*. Live query
  2026-08-12 shows Saskatoon, Saskatoon Airport, and Regina Airport (née
  Regina Airpot) all have `pst_enabled=true, pst_rate=6` as claimed — but
  plain **`Regina`** shows `pst_enabled=false, pst_rate=6`. The rate field
  landed; the enable flag didn't (or landed and was later flipped back off).
  Not the B8 vehicle-pricing fix's doing — B8's `UPDATE`s
  (`docs/change-log/2026-08-12-b8-regina-saskatoon-vehicle-pricing.md`)
  only ever `SET vehicle_pricing = ...`, never touched `pst_enabled`/
  `pst_rate` on any row.
- **Why this matters:** per `.claude/context/regulatory-sk.md` and the
  2026-08-11 change log's own finding, PST currently applies to
  Saskatchewan rideshare. If `pst_enabled=false` on `Regina` is a live bug
  (not an intentional, undocumented reversal), every Regina ride quoted
  since whenever this flag flipped false has been **under-collecting PST
  by 6% of the taxable fare** — the exact live tax-compliance gap the
  2026-08-11 change was supposed to close, now silently reopened for
  Spinr's primary market. This is not something to guess at or silently
  "fix" — could equally be an intentional, deliberate reversal this
  session has no record of (e.g. someone found a problem with Regina PST
  specifically and turned it off on purpose without logging it, per this
  same doc's own rollback-plan mechanism).
- **Action (blocked on an authoritative determination, not on more
  low-confidence research)**:
  1. Get a real answer to "does Saskatchewan PST apply to ride-sharing/
     passenger-transportation-for-hire fares" from a source that can
     actually be trusted for a live tax-compliance decision — an
     accountant/tax professional, direct contact with the SK Ministry of
     Finance Revenue Division, or a session with working web access to
     read the actual PST-46 bulletin text (this session's `WebFetch` was
     blocked to every domain tried, so it could not do this itself).
  2. Once resolved either way, this decides **both** `Regina`'s current
     `false` value *and* whether `Regina Airport`/`Saskatoon`/`Saskatoon
     Airport`'s current `true` values are themselves correct — this item
     is not "fix Regina to match the other three," it's "determine the
     actual policy and make all four rows match it," since the other
     three's `true` values now carry the same doubt as Regina's `false`.
  3. Whichever way it resolves: `UPDATE service_areas SET pst_enabled =
     <value> WHERE name IN (...)` per the A29 justification path above,
     plus explicitly decide whether any rides quoted during the
     inconsistency window need remediation (refund/credit if PST was
     charged and shouldn't have been, or vice versa) — not just a toggle
     flip, per the 2026-08-11 log's "no backdating" precedent.
  4. Write a Change Impact Log either way — this is exactly the kind of
     "silent behavior change to a live-tested flow" CLAUDE.md's pre-merge
     gates require documentation for.
- **Files:** none yet (data-only, pending decision) — reference:
  `docs/change-log/2026-08-11-sk-pst-enable.md`,
  `backend/features.py::calculate_all_fees`
- **Acceptance:** `Regina.pst_enabled` matches the deliberate, current,
  confirmed policy — and whichever way it resolves, that decision is
  written down so this doesn't silently drift a third time.

### B27. `charge.dispute.closed` mis-marks rides and can update the wrong dispute row; dispute fees never reach the ledger — CLOSED (2026-08-17)

- [x] **Status:** DONE (2026-08-17, found 2026-08-14 while writing
  `docs/runbooks/payment-dispute-evidence.md`). All three defects fixed and
  the `charge.dispute.updated` allowlist gap closed — see
  `docs/change-log/2026-08-17-b27-dispute-closed-webhook-fixes.md` for the
  full Change Impact Log. `spinr-money-auditor` reviewed the diff before PR
  creation (Codex auto-review is off, C7/C9) and found 2 real blockers in
  the first version of fix 3's ledger write — both fixed before merge:
  (a) the per-balance-transaction-type `event_type` string violated
  `financial_events`'s fixed CHECK-constraint enum (migration 58), which
  would have failed every insert 100% of the time; `event_type` is now
  always the literal `"stripe_dispute"`, with the Stripe subtype moved into
  `metadata`; (b) a falsy `user_id` (unresolved rider) risked an FK
  violation on `financial_events.user_id NOT NULL REFERENCES users(id)`;
  guarded both in the new `record_dispute_close_events()` function itself
  and at the webhook call site. 261 tests pass across the affected webhook/
  dispute/ledger suites, including a new `test_dispute_close_ledger.py`
  asserting directly on the ledger INSERT payload (the webhook-level tests
  mock the function and can't see this class of bug).
- **Issue/gap:** three defects in the `charge.dispute.closed` branch of
  `backend/routes/webhooks.py` (≈ line 1183):
  1. **`warning_closed` is treated as a loss.** Stripe fires
     `charge.dispute.closed` for `won`, `lost` *and* `warning_closed` (an
     early-fraud-warning/inquiry that resolved without becoming a real
     chargeback). The code is `new_payment_status = "paid" if
     dispute_status == "won" else "dispute_lost"` — so an inquiry that
     closed in our favour permanently marks a fully-paid ride
     `dispute_lost`, corrupting revenue reporting and the rider's record.
  2. **The dispute row is looked up by `payment_intent_id`, not by
     `stripe_dispute_id`.** `find_one("stripe_disputes",
     {"payment_intent_id": pi})` ignores the table's only unique key
     (`idx_stripe_disputes_dispute_id`). When the PI is absent Stripe sends
     `""`, and rows are inserted with `""` too — so a PI-less close can
     match an arbitrary earlier PI-less row and overwrite *its* status.
     Two disputes on one PI hit the same bug.
  3. **The dispute fee never lands in `financial_events`.** Stripe debits
     the disputed amount *and* a per-dispute fee via
     `dispute.balance_transactions`; neither is recorded, so
     `docs/runbooks/stripe-reconciliation.md` will show an unexplained
     delta for every chargeback.
- **Also missing:** `charge.dispute.updated` is not in
  `_STRIPE_HANDLED_EVENTS`, so `needs_response → under_review` transitions
  are invisible; `charge.dispute.funds_withdrawn` /
  `funds_reinstated` likewise.
- **Action:** key the close lookup on `stripe_dispute_id`; map
  `warning_closed` to a non-loss status (leave `paid`, or add a distinct
  value — do **not** reuse `dispute_lost`); record the balance-transaction
  amounts as `financial_events` rows; add `charge.dispute.updated` to the
  allowlist.
- **Risk of implementing:** low-moderate — webhook-only, additive on the
  ledger side. The `warning_closed` fix changes what an existing branch
  writes to `rides.payment_status`, so it needs a before/after in the
  Change Impact Log and a check for any consumer that reads
  `payment_status == 'dispute_lost'`.
- **Verification:** extend `backend/tests/test_routes_webhooks_coverage.py`
  — it covers `won`/`lost` but has no `warning_closed` case and no
  two-disputes-one-PI case.
- **Files:** `backend/routes/webhooks.py`, `backend/migrations/` (nullable
  `evidence_due_by`/fee columns if taken with C23),
  `backend/tests/test_routes_webhooks_coverage.py`
- **Acceptance:** a `warning_closed` event leaves a paid ride `paid`; a
  PI-less close updates only its own row; every closed dispute has matching
  `financial_events` rows for the debit and the fee.

### B28. `payouts.amount` is a legacy `FLOAT` column — every writer must `float()` a `Decimal` at the DB boundary

- **Source:** `spinr-money-auditor` review of the 2026-08-17 legacy-payout-
  correction write path (`docs/change-log/2026-08-17-legacy-payout-
  correction-writepath.md`) — flagged `legacy_payout_correction_service.py`'s
  `commit_write_plan` writing `"amount": float(r.amount)`, a literal
  `float()` on a money value going into a DB write, which CLAUDE.md's
  Decimal-only rule normally forbids outright.
- **Not a new violation** — confirmed via `backend/migrations/159_payouts_
  overview_aggregates_fn.sql`, `162_payout_stats_fn.sql`, and
  `303_..._ytd_exclude_legacy.sql` (all comment `payouts.amount is FLOAT`)
  that this is a real, pre-existing legacy column type, not a mistake
  introduced by any of these callers. Every other `payouts` writer in this
  repo already does the same thing at the same boundary
  (`services/stripe_payout_sync_service.py:407`,
  `services/booking_import_service.py`, `routes/drivers/payouts.py`) — all
  of them keep every arithmetic step upstream as `Decimal` and only convert
  to `float` at the literal moment of serializing the insert payload, which
  is the correct workaround for a `FLOAT` column, not a shortcut around the
  Decimal rule.
- **Status:** open, no owner assigned. The actual fix is a migration —
  `ALTER TABLE payouts ALTER COLUMN amount TYPE NUMERIC(10,2)` — plus
  auditing every one of the ~6 writers above to drop their `float()` cast
  once the column itself is exact. Low priority: no observed cent-level
  drift in production to date (payout amounts are small, few-decimal-place
  CAD figures well within `float`'s exact-representation range in practice),
  but it's a standing landmine for a writer that accumulates many small
  amounts before insert.
- **Files:** `backend/migrations/` (new `NN_payouts_amount_numeric.sql`),
  `backend/services/legacy_payout_correction_service.py`,
  `backend/services/stripe_payout_sync_service.py`,
  `backend/services/booking_import_service.py`,
  `backend/routes/drivers/payouts.py`.
- **Acceptance:** `payouts.amount` is `NUMERIC(10,2)`; every writer passes a
  `Decimal`/string, not `float()`, into the insert/update payload; a
  regression test asserting no writer calls `float()` on a `payouts.amount`
  value.

## P2 — Operational (no/low code — needs a human with dashboard access)

### C1. Failover drill — Railway ↔ Fly
- [ ] **Status:** never exercised
- **Action:** run the cutover in `docs/runbooks/railway-fly-failover.md` end-to-end
  in a low-traffic window; record actual timings and surprises back into the runbook.

### C2. Sentry alert rule — refresh-token theft tripwire
- [ ] **Status:** open (~5 min in Sentry UI, no code)
- **Action:** alert on message `REFRESH TOKEN REUSE DETECTED` → email/PagerDuty.
  The loguru→Sentry bridge already delivers it; it just needs a rule.

### C3. Production env sweep on Fly/Railway
- [ ] **Status:** partially done (SENTRY_DSN deployed via Fly Sentry extension — verify
  boot log shows "Sentry SDK initialized for error monitoring")
- **Action:** confirm `SENTRY_DSN`, `REDIS_URL`/`RATE_LIMIT_REDIS_URL`/`WS_REDIS_URL`,
  Firebase App Check enforcement, and `ENV=production` are set on **both** providers
  (standby drifts silently).

### C4. Staff MFA rollout comms
- [ ] **Status:** code shipped; people not yet notified
- **Action:** tell all admin staff that the next login forces authenticator enrollment
  (ADMIN_MFA_ENFORCED). Ensure ≥2 active super_admin accounts exist for the
  lost-phone reset path.

### C5. Re-enable Railway standby deploys (currently paused)
- [ ] **Status:** open — `deploy-backend.yml` (Railway) is deliberately blocked via a
  GitHub Environment protection rule (confirmed 2026-07-27). This was meant to be
  **temporary** but has no expiry/owner attached, so Railway has been silently
  drifting from `main` since the pause started — contradicts ADR-007's "hot standby,
  not a paper fallback" design, and means a Fly outage right now would fail over to
  a stale (possibly vulnerable, possibly schema-mismatched) build.
- **Action:** confirm the original reason for pausing no longer applies, then remove
  the Environment protection rule (or update its required reviewers/branch
  restriction) so `deploy-backend.yml` resumes auto-deploying on every push to
  `main`. Before flipping it back on: verify secret parity between Railway and Fly
  per ADR-007's own risk section (`JWT_SECRET`, `SUPABASE_*`, `FIREBASE_*`, Redis
  URLs), since Railway's env vars may also have drifted during the pause. Re-run
  the failover drill (C1) afterward to confirm the standby actually works end to
  end, not just that it deploys.
- **Owner / follow-up:** none assigned yet — flag in the next planning sync so this
  doesn't become a permanently-forgotten "temporary" gap.

### C6. `docker-image-scan` (Trivy): stale-pinned base image fixed; msgpack/setuptools findings were REAL and are now fixed
- [x] **Status:** done — but **the "false positive" conclusion recorded here
  on 2026-08-01 was WRONG, and is corrected below.** Both findings were
  genuine. Base-image staleness was fixed 2026-08-01 (digest refreshed).
  The msgpack/setuptools findings were fixed 2026-08-02 by removing pip from
  the runtime image (#3246) — see "CORRECTION 2026-08-02" below. Trivy was
  reporting accurately the entire time. The
  actual scan-config remediation (stop Trivy from trusting a stale embedded
  SBOM) is filed as **CR-2026-002**, GitHub issue
  [#3048](https://github.com/srikumarimuddana-lab/spinrvm/issues/3048), and
  tracked there rather than here. Originally found 2026-07-30 while
  triaging `docker-image-scan` on
  PR #2931 (a backend change, so this wasn't waved off as the usual
  unrelated-base-image noise without checking), then reproduced identically
  on PR #2934 (a *docs-only* PR touching zero backend files) — same two HIGH
  findings, byte-for-byte: `msgpack` `GHSA-6v7p-g79w-8964` (installed 1.1.2,
  fixed 1.2.1) and `setuptools` `CVE-2025-47273` (installed 70.3.0, fixed
  78.1.1), against `spinr-backend:ci-<sha>`.
- **First hypothesis (stale Docker layer cache) — investigated and
  retracted.** Located the job (`.github/workflows/ci.yml:705`,
  `docker-image-scan`): it runs on a fresh `ubuntu-latest` runner (a new VM
  per job, empty local Docker cache) with a bare `docker build --tag
  spinr-backend:ci-${{ github.sha }}` — no `--cache-from`, no
  `docker/setup-buildx-action`, no registry cache. There is no persistent
  cache mechanism here for a *build* to be stale from. That theory doesn't
  fit this workflow's actual shape.
- **Confirmed, real, separate finding: the base image pin is genuinely
  stale.** `backend/Dockerfile:12,36` pins
  `python:3.12.9-slim@sha256:48a11b7...`, captured **2026-04-29** per its own
  comment, which also says "refresh quarterly." Today is 2026-07-30 — three
  months past that cadence, and past the quarterly mark. This alone would
  explain OS-level (Debian) drift and plausibly `setuptools` specifically:
  Python's official Docker images bundle a fixed `ensurepip`-provided
  setuptools version tied to that image build date, and while the Dockerfile
  does run `pip install --upgrade pip setuptools` right after, that upgrade
  not visibly taking effect (or being satisfied by whatever's already
  resolvable at that point) is plausible but **not directly confirmed** —
  didn't reproduce a live build to verify.
- **`msgpack` remains genuinely unexplained.** Unlike setuptools, msgpack
  isn't part of any base image — it's installed exclusively via `pip install
  --require-hashes -r requirements-locked.txt`
  (`backend/Dockerfile:32,54`), and the lockfile (confirmed by direct read)
  pins `msgpack==1.2.1` with hashes. `--require-hashes` mode cannot silently
  substitute a different version. Also noticed but not chased further: the
  Dockerfile has two nearly-identical stages (an unnamed first stage
  building into `/install`, and a `runtime` stage that does its own
  independent `pip install` rather than `COPY --from=` the first stage's
  output) — the first stage's output appears to go unused, which is odd but
  doesn't by itself explain a version mismatch in the stage that's actually
  used.
- **Why it matters:** the base-image staleness is real and actionable on its
  own regardless of the msgpack mystery. The unexplained msgpack finding
  matters more: if `--require-hashes` can produce a package version that
  doesn't match its own lockfile, either something about this
  investigation's assumptions is wrong, or there's a real, more concerning
  build-reproducibility bug worth a second, deeper look (ideally with an
  actual local Docker build reproduced end-to-end, which wasn't available in
  this session — no Docker daemon in the sandbox).
- **Files:** `backend/Dockerfile` (base image digest, lines 12 & 36),
  `backend/requirements-locked.txt`, `.github/workflows/ci.yml` (the
  `docker-image-scan` job, line 705).
- **Approach:** (1) refresh the base image digest per
  `docs/runbooks/docker-image-pinning.md` — straightforward, do this
  regardless; (2) actually build `backend/Dockerfile` locally/in a scratch
  CI run and run `pip show msgpack` inside the resulting image to see what
  really gets installed and why, before assuming any explanation.
- **Acceptance:** base image digest refreshed and confirmed current;
  msgpack's installed-vs-locked mismatch either reproduced-and-explained or
  confirmed to no longer reproduce after a clean rebuild.
- **Update (2026-08-01) — base-image-staleness half only, done; msgpack half
  still open.** This sandbox had no Docker daemon (`docker version` connects
  fine to the client but `dial unix /var/run/docker.sock: connect: no such
  file or directory` on the API — confirmed still true, not assumed). The
  proxy also blocks the Docker Hub blob CDN outright (`recentRelayFailures`
  in `$HTTPS_PROXY/__agentproxy/status` shows repeated `connect_rejected` /
  gateway 403 for `production.cloudfront.docker.com`), so a plain `docker
  pull` / `docker manifest inspect` (the runbook's documented procedure)
  fails partway through. Worked around it by hitting the Docker registry
  v2 HTTP API directly (`auth.docker.io` for a pull token, then
  `registry-1.docker.io/v2/library/python/manifests/<tag>` with an
  `Accept: application/vnd.oci.image.index.v1+json` header for the
  `Docker-Content-Digest` response header) plus the `hub.docker.com/v2`
  metadata API for cross-checks — neither needs a blob download, and both
  are apparently unblocked by the proxy.
  - Found the pinned tag `python:3.12.9-slim`'s digest **had not moved at
    all** since the 2026-04-29 capture (`sha256:48a11b7...` — verified
    byte-identical via the registry API). Docker Hub does not rebuild a
    fixed patch-version tag after newer patches ship, so re-pinning that
    same tag would have been a no-op that didn't actually close any CVE
    gap — which defeats the stated purpose of the quarterly cadence.
  - Confirmed via the registry API, the Hub metadata API, and the floating
    `python:3.12-slim` alias (which always tracks the newest 3.12.x patch)
    — all three agree — that the current latest patch is `3.12.13-slim` at
    digest `sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd042
    66317710de`, last published 2026-07-16.
  - Bumped `backend/Dockerfile` (both stages) to `python:3.12.13-slim` at
    that verified digest; see `docs/change-log/
    2026-08-01-refresh-docker-base-image-digest.md` for the full Change
    Impact Log. **Not verified**: no local Docker build was possible (no
    daemon), so this is unverified beyond "the digest is real and current
    per two independent Docker-operated APIs" — `docker-image-scan` in CI
    on the draft PR is the actual build+Trivy verification.
  - Also noticed, out of scope for this pass: a second, apparently-unused
    root-level `Dockerfile` (different build recipe — venv + plain
    `requirements.txt`, no `--require-hashes`) pins the *same* stale
    `3.12.9-slim` digest via its own comment ("Q-5"). Neither `railway.json`
    nor `backend/fly.toml` reference it (`railway.json` explicitly points
    at `backend/Dockerfile`), so it looks orphaned rather than a live
    build path — left untouched since it's outside this task's scope, but
    worth a follow-up to confirm it's dead and either delete it or bring
    it in sync.
  - **msgpack half of C6 is untouched and still open** — genuinely
    requires a real Docker build to investigate, which remains unavailable
    in this environment.
- **CORRECTION (2026-08-02): the "false positive" conclusion below is WRONG.**
  The findings were real. A diagnostic step added to `docker-image-scan`
  (#3113) and run on `main` at `24b3e49`
  ([job 91442197631](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/30727439028/job/91442197631))
  inspected the built image directly and found:
  - `pip list` → `msgpack 1.2.1`, `setuptools 83.0.0`, `pip 26.2`
  - on disk → only `msgpack-1.2.1.dist-info` and `setuptools-83.0.0.dist-info`
  - **`pip/_vendor/vendor.txt` → `msgpack==1.1.2` and `setuptools==70.3.0`**
  - `docker image inspect … Config.Labels` → `null` (no Docker *attestation*)

  A follow-up diagnostic by another contributor found the missing piece: pip
  also ships its **own CycloneDX SBOM** at `pip/_vendor/bom.cdx.json`, listing
  those two versions with proper `pkg:pypi/...` purls. Trivy auto-detects any
  `*.cdx.json` / `*.spdx.json` inside a scanned image and — exactly as its own
  `WARN Third-party SBOM may lead to inaccurate vulnerability detection` says —
  prefers that component list over its live filesystem scan for vulnerability
  matching, while still using the live scan for the per-file inventory table.
  That is why the two tables in one Trivy run contradicted each other.

  So both halves are true, and the original either/or framing was the mistake:
  the versions really are declared inside the image (not invented by Trivy),
  **and** Trivy reached them by preferring a third-party SBOM over its own scan.

  **Two independent fixes have landed:**
  1. **#3246** — pip removed from the runtime image after dependency install,
     with a build-time assertion. This removes `bom.cdx.json` *and* pip's
     vendored code, and takes a package manager out of a production image.
     `G6 · Trivy container scan` passed for the first time on that PR.
  2. **`skip-files: '**/pip/_vendor/bom.cdx.json'`** on all four trivy-action
     steps (`ci.yml` ×2, `security-gates.yml` ×2), so Trivy ignores that one
     file rather than trusting it.

  With pip gone, (2) now targets a file that no longer exists — harmless, and
  worth keeping as a guard in case pip ever returns to the image. **Note the
  ordering risk:** `skip-files` alone would have been a suppression. Had it
  landed without (1), pip's vendored code would still be in the image and the
  scanner would have been configured not to see it.

  **Why the original reasoning failed:** every check in the superseded block
  below confirms the *application's* msgpack is 1.2.1. None looked for a
  second copy, and none looked for an SBOM file inside the filesystem — only
  for a Docker attestation label, which was legitimately `null`. "Our pin is
  correct, therefore the scanner is wrong" skipped asking where else that
  version string could come from.

- ~~**msgpack mystery: SOLVED (2026-08-01)**~~ — **SUPERSEDED, see the
  correction directly above. The conclusion in this block is incorrect and is
  retained only because the reasoning error is instructive.** Investigated PR
  #3044's `docker-image-scan` job (run
  `30713789884`, job `91406577991`) and the identical `G6 · Trivy container
  scan` job in `security-gates.yml` (run `30713789883`, job
  `91405783086`) — same PR, same commit, both Trivy invocations agree.
  Conclusion: **this is a Trivy tool false positive, not a real vulnerable
  dependency.** Evidence, in order:
  1. `backend/requirements.txt` and `backend/requirements-locked.txt` both
     already pin `msgpack==1.2.1` — the version Trivy itself calls "fixed".
  2. The actual `docker build` step's own pip install output in the job log
     shows the real, live install: `Successfully installed pip-26.2
     setuptools-83.0.0` and `Collecting msgpack==1.2.1 (from
     -r requirements-locked.txt (line 1746))` — both packages install at
     patched-or-newer versions, confirmed directly in the build log, not
     inferred.
  3. Trivy's **own** per-file python-pkg inventory table, printed earlier in
     the same scan run, lists
     `usr/local/lib/python3.12/site-packages/msgpack-1.2.1.dist-info/METADATA`
     and `.../setuptools-83.0.0.dist-info/METADATA` — both with **zero**
     findings. Trivy's live filesystem scan correctly sees the patched
     versions.
  4. Yet Trivy's summary "Python (python-pkg)" vulnerability table, printed
     immediately after in the same tool invocation, reports Installed
     Version `1.1.2` / `70.3.0` for those same two packages — directly
     contradicting its own per-file table from the same run.
  5. The scan log includes Trivy's own warning right before this
     contradiction: `WARN Third-party SBOM may lead to inaccurate
     vulnerability detection`. Strong evidence Trivy is trusting a
     stale/incorrect embedded SBOM (most likely a Docker BuildKit
     provenance/SBOM attestation baked into the image) for vulnerability
     *matching*, while still using its own live filesystem scan for the
     per-file inventory listing — two different data sources disagreeing
     inside one Trivy run.
  6. This exact false positive was observed **identically** across PRs
     #3033, #3042, #3043, and #3044 — confirming it reproduces on every
     backend image build regardless of the PR's actual diff, consistent
     with a scan-tooling issue rather than a real per-commit regression.
  - **Filed as CR-2026-002**, GitHub issue
    [#3048](https://github.com/srikumarimuddana-lab/spinrvm/issues/3048)
    (`[CR] Trivy container-scan (G6 / docker-image-scan) reports
    false-positive HIGH findings for msgpack/setuptools — trusting a stale
    embedded SBOM over its own live scan`). That issue carries the actual
    proposed fix (likely `--provenance=false --sbom=false` on the `docker
    build` step, or forcing Trivy to regenerate its own SBOM instead of
    consuming the embedded one) — not re-implemented here; this entry only
    documents that the mystery itself is solved and points to the CR for
    the scan-config remediation.
  - **Update (2026-08-01) — root-cause mechanism corrected; fix
    implemented.** The "Docker BuildKit provenance/SBOM attestation"
    hypothesis above (point 5) is **wrong** — not just unconfirmed, ruled
    out. The actual embedded SBOM is `pip`'s own, and it doesn't need a
    running Docker build to prove: this job's own logs (per point 2 above)
    show `Successfully installed pip-26.2` — reproduced *that exact pip
    version* in an isolated local venv (`pip install pip==26.2`, no Docker
    needed) and inspected what it carries. Two files inside pip's own
    `_vendor/` directory settle it:
    - `pip/_vendor/vendor.txt` — a plain-text list of pip's internally
      vendored dependencies (used for pip's own internal purposes, never
      imported by application code) — reads `msgpack==1.1.2` and
      `setuptools==70.3.0`, the exact "installed" versions Trivy reports.
    - `pip/_vendor/bom.cdx.json` — a genuine CycloneDX 1.4 SBOM (confirmed:
      `"bomFormat": "CycloneDX"`) that pip ships describing those same
      vendored copies, with proper `pkg:pypi/msgpack@1.1.2` /
      `pkg:pypi/setuptools@70.3.0` purls.

    Trivy auto-detects any `*.cdx.json`/`*.spdx.json` file inside a scanned
    image by extension (confirmed against current Trivy docs) and, per its
    own "Third-party SBOM may lead to inaccurate vulnerability detection"
    warning, prefers that file's component list for vulnerability matching
    over its own live filesystem scan — which is exactly the contradiction
    point 4 above describes. No OCI-level BuildKit attestation is involved;
    the offending file sits in ordinary site-packages, inside the image
    filesystem Trivy already walks for its per-file inventory.

    Fixed via `aquasecurity/trivy-action`'s native `skip-files` input
    (`skip-files: '**/pip/_vendor/bom.cdx.json'`) on all four Trivy steps
    across `G6 · Trivy container scan` (`security-gates.yml`) and
    `docker-image-scan` (`ci.yml`) — no `docker build` flag, no Dockerfile
    change, no application dependency touched. `ci.yml`'s diagnostic step
    (id "DIAGNOSTIC (CR-2026-002)") was extended rather than removed, to
    directly confirm `bom.cdx.json`'s presence/contents against the real
    built image on the fix's own CI run, not just the isolated-venv
    reproduction. See the PR for this change and CR-2026-002 / #3048 for
    the full writeup; **not verified end-to-end** — no Docker daemon in
    this sandbox either, same constraint noted throughout this item, so
    real CI on that PR is the actual confirmation, not this entry.

### B-AI1. Corporate rider booking via AI chat bypasses corporate billing
- [x] **Status:** done (2026-07-29) — found by the 2026-07-28 AI guardrail
  audit (branch `claude/rider-ai-location-selection-yn0mem`), fixed on branch
  `claude/b-ai1-corporate-billing-chat`. The in-chat booking card always
  booked with `corporateAccountId=undefined`:
  `rider-app/components/BookingProposalCard.tsx:155-159` called
  `createRide(paymentMethod, undefined, ...)` →
  `rider-app/store/rideStore.ts` sent `corporate_account_id: null,
  work_profile: null`, so corporate policy checks
  (`backend/routes/rides/booking.py:717-721`) never ran and the ride billed
  the rider personally. Only **wallet**-payment proposals booked inline (card
  proposals deep-link to `/ride-options`, where Bill-to-Business worked), so
  the exposure was corporate riders who said "pay with wallet" in chat.
- **Approach chosen (user confirmed via `AskUserQuestion` — see
  `docs/change-log/2026-07-29-b-ai1-corporate-billing-chat-bypass.md` §3):**
  mirror `/ride-options.tsx`'s own default — if the rider's Work Mode toggle
  (`useWorkProfileStore`) is on with an active company, book to that company
  by default, same as the standard screen already does for the same rider
  state. Not a new payer-selection design; the two rejected alternatives
  (force to `/ride-options` unconditionally, or add a new explicit
  payer-picker UI) are recorded in the change-log for reference if revisited.
- **Fix:** `BookingProposalCard.tsx` now reads `workModeEnabled`/
  `activeCompanyId`, computes `corporateAccountId` the same way
  `/ride-options` does, runs the same `checkRide()` client-side policy
  pre-check before booking (blocking with the policy-violation reason
  instead of silently booking), passes the id to `createRide`, and shows a
  "Charged to `<Company>`" pill on the card so the payer is visible before
  the rider confirms.
- **Files:** `rider-app/components/BookingProposalCard.tsx`,
  `rider-app/__tests__/bookingProposalCardCorporate.test.tsx` (new, 4 tests),
  `rider-app/__tests__/bookingProposalCardPromo.test.tsx` (added a
  `workProfileStore` mock stub — unrelated to that test's own assertions,
  needed once the component started importing the store).
- **Acceptance:** ✅ met — a corporate rider booking via AI chat with Work
  Mode on now gets the same payer (and the same client-side policy
  pre-check) as `/ride-options`; regression tests pin Work-Mode-off (no
  change), Work-Mode-on (books to company + shows the pill), policy-failure
  (blocks + shows the reason), and card-path-unaffected. Full `rider-app`
  suite re-run: 51 suites / 434 tests passed. `tsc --noEmit` clean.
- **Not verified (see change-log §10 for full list):** the real production
  build CLAUDE.md requires for `rider-app` was attempted
  (`expo export --platform web`) but fails before reaching any app code, on
  a pre-existing environment-level `react-native-fbsdk-next` config-plugin
  resolution error unrelated to this diff (same known issue as this
  session's rider/driver-app E2E CI noise) — not skipped, but not a passing
  build either. `tsc --noEmit` (clean) + full Jest suite (434/434) are the
  strongest verification available here. Also not exercised against a real
  backend/Supabase instance or a live corporate membership.

### C7. AI PR review is off by design (cost) — DECIDED 2026-08-01: stays off
- **Duplicate of existing CRs — noted 2026-08-02.** This was filed as a new
  finding without first searching `label:change-request`. It restates
  [#2503](https://github.com/srikumarimuddana-lab/spinrvm/issues/2503)
  ("Missing Anthropic credentials secret breaks the `review` CI check on
  every PR", open since 2026-07-27) and
  [#2497](https://github.com/srikumarimuddana-lab/spinrvm/issues/2497)
  (closed, same ground). The decision recorded below — leave the key unset —
  is the new part and stands; #2503 should be closed against it rather than
  left open describing an unresolved problem. **Check the open CR list before
  filing anything here**; several other entries from the same session turned
  out to be tracked already (#2771 deploy-backend, #2656/#2861 backend-test
  DSN, #3256 driver-app E2E, #3083 admin Playwright).
- [x] **Status:** closed — **decision taken: leave `ANTHROPIC_API_KEY` unset.**
  The per-PR API spend is not justified at this repo's volume (~24 merged
  PRs/day). The workflow remains in the repo, scoped and skipping cleanly;
  setting the secret is the only step needed to reverse this, so nothing is
  lost but the running cost. Docs corrected to match in the same change:
  `.claude/README.md` previously told readers to set the secret and described
  the deep agent audit as part of the PR pipeline — it now states the review
  is off and points at the on-demand path instead.
- **Compensating control (important):** the `spinr-*` audit agents still
  exist and still work — they are just not automatic. For any diff touching
  money, auth, migrations, dispatch, or safety, invoke
  `spinr-security-auditor` / `spinr-money-auditor` /
  `spinr-migration-reviewer` **manually** via the Agent tool before merge.
  The semantic rules listed below are the ones no static gate can catch, so
  skipping the manual pass on those surfaces is where the real exposure sits.
- **Correction to this entry's earlier wording:** it previously said
  `CLAUDE.md`'s PR-review-handling section "assumes an automated reviewer
  exists and has nothing to act on". That conflated two different reviewers.
  `CLAUDE.md:55-63` is about **Codex** (`chatgpt-codex-connector`), a
  separate GitHub App unaffected by `ANTHROPIC_API_KEY`; it was never
  describing this workflow. Nothing in `CLAUDE.md` referenced the Claude
  review at all — the inaccurate claims were in `.claude/README.md`.
- **Separately worth checking (not part of this decision):** no Codex review
  appeared on #3037, #3070, #3074 or #3086 either. If Codex is also inactive,
  `CLAUDE.md:55-63` is instructing agents to act on findings that never
  arrive — but that is a different integration with a different cause, and
  was not investigated here rather than assumed.
- **Prior analysis retained below.** **Amended 2026-08-01
  — the original wording of this entry was wrong** and is corrected here.
  It claimed the missing `ANTHROPIC_API_KEY` was an invisible capability
  gap. It is not: `.github/workflows/claude-review.yml:44-47` documents
  keyless operation explicitly — *"Review is advisory — a missing/invalid
  ANTHROPIC_API_KEY or model error should never block merging… until then
  PRs proceed without it"* — with `continue-on-error: true` to match. The
  key is unset deliberately, to avoid per-PR API spend. That is a
  legitimate call, and this entry now tracks the trade-off rather than
  reporting a fault.
- **What is factually true:** the job runs and exits without reviewing.
  Confirmed from job logs
  [`91408169618`](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/30714687039/job/91408169618)
  and `91408494259` (PR #3037) and again on #3070 — the env block prints
  `ANTHROPIC_API_KEY:` with no value. PRs #3037, #3070 and #3074 each merged
  with zero automated review.
- **The reporting bug (fixed 2026-08-01):** `continue-on-error` stops a
  failed run blocking the merge but does **not** stop the check reporting
  `failure`, so the advisory gate showed permanently red. That is an active
  harm independent of the cost question — a check that is always red trains
  reviewers to ignore red, and it demonstrably did: the C8 entry below was
  filed because red gates had become background noise. The keyless case now
  skips cleanly with a notice instead of failing.
- **What is actually lost while it stays off:** narrow but well-placed. The
  static gates (Bandit, Semgrep ×2, Gitleaks ×2, pip-audit, yarn/npm audit,
  ESLint security, plus the 7 guardrails) cannot check Spinr's *semantic*
  rules — `Decimal`-only money arithmetic, `claim_stripe_event` before
  webhook processing, ride state-machine transition guards, the
  `is_available ⇒ is_online` invariant, Period 2 starting at
  `driver_assigned`, the 2.5× surge cap, GPS/PII in logs. The three agents
  this workflow runs (`spinr-security-auditor`, `spinr-money-auditor`,
  `spinr-migration-reviewer`) exist for exactly that, and it is the class of
  bug that costs most during live app testing.
- **Why it was expensive:** ~24 PRs/day merge to `main`, and the workflow
  triggered on `synchronize` — every push, not every PR — so real volume was
  roughly 50–80 runs/day. Each run paid a large fixed cost (three agents
  each loading `CLAUDE.md` plus domain context) regardless of diff size, so
  a one-line markdown PR cost about what a payments refactor did.
- **Action / decision:** trigger scoping has been applied (drop
  `synchronize`, `paths-ignore` for markdown/`docs/`/`.claude/`), which
  removes most of the wasted volume while keeping coverage on code. What
  remains is a business decision: **set the key with a capped spend limit and
  billing alert, run one week, and price it from real numbers** — or leave
  it off permanently, in which case also drop `CLAUDE.md`'s
  PR-review-handling assumption that an automated reviewer exists, so the
  docs stop describing a reviewer that does not run.
- **Owner / follow-up:** needs someone with repo-secret and billing access;
  not fixable from a PR branch.

### C8. ~~`driver-app-test` reported success while its test fails locally~~ — RESOLVED, was my own measurement error (no gate defect)
- [x] **Status:** closed 2026-08-01, same day it was filed. **There is no gate
  reliability problem. The entry as originally written was wrong** and is
  corrected here rather than deleted, because the reasoning error is the
  useful part.
- **What the original entry claimed:** that `driver-app-test` concluded
  `success` on PR #3037 commit `4a5c976` (job
  [`91408494120`](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/30714809531/job/91408494120),
  19:29 UTC) while the same test failed locally on the same day, and that
  nothing explained the pass.
- **Actual explanation:** GitHub Actions `pull_request` workflows check out
  the **merge ref** (`refs/pull/N/merge` — branch merged with base), not the
  branch head. `.github/workflows/ci.yml` does not override this with an
  explicit `ref:`, so it gets the default. The fixture fix
  [#3042](https://github.com/srikumarimuddana-lab/spinrvm/pull/3042)
  (`0bb951e`) merged to `main` at **19:05 UTC**. Every `driver-app-test` run
  before that time tested a merge with a `main` that lacked the fix and
  failed; the 19:29 run tested a merge with a `main` that had it and passed.
  My local run failed because my working tree was #3037's branch, based on
  pre-#3042 `main` — so I was comparing CI's *merged* tree against a *stale
  unmerged* one and reading the difference as a CI defect.
- **Verification:** on `main` at `7a53caa`,
  `cd driver-app && TZ=UTC npx jest __tests__/components/ActivityView.test.tsx`
  → **6 passed, 6 total**, on 2026-08-01 (the 1st, the day the old fixture
  was supposed to fail).
- **Lesson worth keeping:** "CI disagrees with my local run" is not evidence
  of a CI defect until the two are known to be testing the same tree. For
  `pull_request` workflows they are not the same tree by default. Check what
  landed on the base branch between the two runs before escalating —
  `git log --oneline <base>` over the relevant window would have caught this
  in one command.
- **Related:** [#3041](https://github.com/srikumarimuddana-lab/spinrvm/issues/3041)
  (the fixture bug itself) was already fixed by #3042 and is closed. The
  diagnosis recorded in that issue — `Math.max(1, getDate() - 10)` collapsing
  the fixture date onto today when today is the 1st — was correct; only my
  later claim that it was still unfixed was wrong.

### C9. Codex auto-review stopped on 30 July — combined with C7, this repo has **no** automated PR review
- [ ] **Status:** open. The `chatgpt-codex-connector` GitHub App is installed
  and has worked — it has commented on **183 PRs** historically — but it has
  reviewed nothing for two days. This is a **stall, not an absence**, which
  should make the cause findable.
- **Evidence (2026-08-01):**
  - `commenter:app/chatgpt-codex-connector` → 183 PRs total.
  - Sorted by most recent, the newest is **#2877, created 2026-07-30T00:11Z**.
  - Restricting to `created:>=2026-07-30` returns **exactly one** result —
    that same #2877.
  - PRs #2878 → #3096 (roughly 200, including every PR merged on 1 August)
    have **no** Codex comment at all.
  - Method note: the search operator requires the `app/` prefix.
    `commenter:chatgpt-codex-connector` without it returns 0 results even
    though the app is active — a false negative that a control query
    (`commenter:app/github-actions` → 1079) caught. Anyone re-checking this
    should run the control first.
- **Why it matters:** `CLAUDE.md`'s PR-review-handling section instructs every
  agent session to *always* check Codex comments and act on them. For ~200
  consecutive PRs there have been none, so that instruction has had nothing
  behind it. Worse, silence reads as "no findings" rather than "no reviewer" —
  the same failure mode as a permanently-red check. Combined with **C7** (the
  Claude agent audit deliberately off on cost grounds), **this repo currently
  has no automated PR review from either vendor**, while in live app testing.
- **Action:** diagnose why Codex stopped around 29–30 July. Plausible and
  unchecked: lapsed subscription or billing state, revoked/expired app
  installation token, a config change in that window, or an org-level policy
  change. All are visible in GitHub app settings and the Codex dashboard —
  neither readable from a PR branch, so this needs someone with org access.
- **Interim mitigation (applied 2026-08-01):** `CLAUDE.md` now states both
  reviewers are inactive and directs sessions to run the `spinr-*` audit
  agents manually for diffs touching money, auth, migrations, dispatch, or
  safety. That is a social control, not an enforced one.
- **Note:** if the decision is that Codex stays off too, the honest follow-up
  is to delete the PR-review-handling section rather than leave it describing
  a workflow nobody runs — the same cleanup done for the Claude reviewer in
  #3096.

### C10. No reconciliation job for `stripe_events` rows stuck at `processed_at IS NULL`
- [x] **Status:** closed 2026-08-01, same day it was filed — found while
  fixing the `mark_stripe_event_processed` silent-error-swallow bug flagged
  by the `repositories/wallet_repo.py` coverage pass (PR #3098), fixed in
  the same session.
- **What's wrong:** two places in this codebase claim, in comments, that a
  background job reconciles `stripe_events` rows left with
  `processed_at IS NULL` — `repositories/wallet_repo.py`'s old
  `mark_stripe_event_processed` docstring ("the reconciliation job can still
  distinguish processed vs. stuck events") and `routes/webhooks.py:1578`
  ("Leave processed_at NULL for unknown/unhandled events so the nightly
  reconciliation job can replay them"). **Neither job exists.** Verified via
  `grep -rn "processed_at" backend/ --include="*.py"` and
  `grep -n "processed_at\|stripe_events" backend/utils/stripe_reconcile.py`
  (0 hits) — `utils/stripe_reconcile.py` (one of the 17 startup loops per
  `CLAUDE.md`) does not reference `stripe_events` or `processed_at` at all.
  The only code that ever reads `processed_at` back is the reactive,
  retry-triggered check inside `claim_stripe_event` (logs
  `logger.critical(...STUCK...)` on a duplicate-key insert) — which only
  fires if Stripe retries the *same* `event_id`, and Stripe won't retry an
  event that already got a 2xx response.
- **Two distinct row classes are affected, both silently unreconciled:**
  1. Rows where the webhook handler finished all side effects and returned
     2xx, but the final `mark_stripe_event_processed` DB write itself then
     failed (now logged loudly as of the fix below, but still never
     replayed/re-stamped).
  2. Rows for unknown/unhandled Stripe event types (`routes/webhooks.py`
     `else` branch, ~line 1571) — deliberately left `processed_at IS NULL`
     "so the nightly reconciliation job can replay them if they later
     become actionable." No such nightly job exists to do that replay.
- **Why it matters:** both comments describe a safety net for exactly the
  kind of state-inconsistency `CLAUDE.md`'s money/payments rules exist to
  prevent, and neither safety net is real. Low likelihood (requires a DB
  write to fail at exactly the wrong moment, or a new/renamed Stripe event
  type to arrive), but silent and undetectable if it happens — there's
  currently no dashboard, admin query, or alert that would ever surface a
  `stripe_events` row stuck at `processed_at IS NULL`.
- **Files:** `backend/utils/stripe_reconcile.py` (where the sweep would
  live, alongside its existing Stripe-reconciliation responsibilities),
  `backend/repositories/wallet_repo.py` (`mark_stripe_event_processed`),
  `backend/routes/webhooks.py` (~line 1578, the unhandled-event-type path).
- **Fix:** added `_reconcile_stuck_stripe_events()` to
  `backend/utils/stripe_reconcile.py`, called from the existing daily
  `_run_reconciliation_tick()` (02:00 UTC, same Redis leader lock as the
  rest of that file — no new loop, reuses existing replay-safety
  infrastructure rather than adding an 18th startup loop). Queries
  `stripe_events` for `processed_at IS NULL` rows older than a 5-minute
  grace window (matching `migrations/22_stripe_events.sql`'s own original
  design comment) and surfaces each as a `STRIPE_EVENT_STUCK_UNPROCESSED`
  discrepancy: `logger.error` (Sentry-bridged, `domain=payments`) plus the
  existing `audit_logs` summary row this file already writes daily.
- **Scope decision — detection/alert only, deliberately not auto-replay:**
  the original migration comment and both stale code comments this item
  found said a job "should replay" stuck events. Did not build that.
  `utils/stripe_reconcile.py`'s own established pattern for every other
  discrepancy type in this file (`STRIPE_ORPHAN`, `RIDE_PAYMENT_STUCK_PROCESSING`,
  payout discrepancies) is detection-only, with auto-heal — where it exists
  at all (`_maybe_heal_stuck_processing`) — behind an explicit, default-OFF
  app-setting flag. Matched that convention: for a stuck `stripe_events` row,
  "replay" would mean re-running webhook business logic against a stored
  payload, which risks double-processing a row whose side effects already
  succeeded (the DB-write-failure case is exactly that — only the final
  stamp failed, everything else already happened). Distinguishing
  "safe to replay" from "already done" would require trusting the payload's
  own claims, which this job does not do. Surfaced for manual review instead.
- **Both row classes now covered:** the DB-write-failure case (row class 1)
  also still logs `logger.error` at the point of failure itself
  (`mark_stripe_event_processed`, fixed earlier the same session) as the
  fast/loud signal; this sweep is the daily backstop in case that signal is
  ever missed. The unhandled-event-type case (row class 2) had no prior
  signal at all — this sweep is its only coverage, so it will typically
  surface up to ~24h after the event, not immediately (acceptable: `CLAUDE.md`
  itself notes ~24h daily cadence matches the bar already set for the
  other discrepancy types in this same file).
- **Files:** `backend/utils/stripe_reconcile.py` (new function
  `_reconcile_stuck_stripe_events`, new `_STUCK_STRIPE_EVENT_AFTER` constant,
  wired into `_run_reconciliation_tick`'s summary), `backend/tests/test_stripe_reconcile.py`
  (4 new tests), `backend/repositories/wallet_repo.py` and
  `backend/routes/webhooks.py` (comments corrected to point at the sweep
  instead of describing a job that didn't exist).
- **Verification:** `pytest tests/test_stripe_reconcile.py -q` → 45 passed
  (41 pre-existing + 4 new); new function individually measured at 100%
  coverage (`--cov=utils.stripe_reconcile`, no missed lines in its range).
  Full suite re-run — see the fix's own change-log for the exact count.
- **Acceptance:** met — a deliberately-stuck `stripe_events` row is detected
  and surfaced (alerted, not replayed — see scope decision above) within
  one daily sweep, with 4 regression tests proving it (aged-row flagged,
  fresh-row skipped, missing-timestamp over-reported rather than hidden,
  query-failure never raises).

### C11. Metrics aggregation & alerting not yet implemented — SLA/KPI table still unmeasured
- [ ] **Status:** open — design accepted (ADR-010, PR #3255, merged 2026-08-02);
  implementation not started. Tracked as **CR-2026-008**, issue
  [#3295](https://github.com/srikumarimuddana-lab/spinrvm/issues/3295).
- **What's wrong:** `backend/utils/metrics.py` is per-process only (its own
  docstring says so — no cross-replica aggregation, no exporter sidecar).
  `CLAUDE.md`'s P95 SLA table (dispatch offer→accept < 2s, fare calc < 300ms,
  WS fan-out < 100ms) and KPI table (match rate ≥ 85%, payment success ≥ 99%)
  cannot be computed from it today. Directly blocks item **B6** above
  (Directions-latency re-tuning), which needs a real p99 that currently has
  nowhere to accumulate across replicas.
- **Design exists, not yet built:** ADR-010
  (`docs/adr/010-metrics-aggregation-and-alerting.md`) recommends a
  Prometheus-agent-per-Fly-machine pushing to a managed backend (Grafana
  Cloud), with a concrete <1-day MVP: one dashboard panel + 2 alert rules
  (dispatch-latency breach, payment-failure-rate breach) wired to the
  existing `ALERT_WEBHOOK_URL` Slack channel `loop_watchdog` already uses.
- **Why not done yet:** requires infra/vendor provisioning (a Grafana Cloud
  account, a real Fly deploy) that no dev session/sandbox environment can do
  — genuinely needs an operator with Fly + Grafana Cloud access, not just
  code.
- **Open decision before implementing:** agent placement — colocate the
  scrape agent in `backend/Dockerfile`/`fly.toml` (touches the recently
  hardened, digest-pinned, Trivy-scanned runtime image — see C6/CR-2026-002
  — and could reopen that scan surface) vs. a standalone Fly app scraping
  over the private network (avoids touching the hardened image, but needs
  Fly Machines-API-based per-replica discovery glue since Fly's `.internal`
  DNS load-balances rather than fanning out to all replicas). Full tradeoff
  in ADR-010 §1 and issue #3295.
- **Constraints:** implementation needs real source changes
  (`Dockerfile`/`fly.toml`, or a new small standalone app) and a new
  dependency (the agent binary) — **not** purely docs/design past this
  point. Also needs a new Fly production secret (Grafana Cloud remote-write
  API key).
- **Risk if left undone:** none of `CLAUDE.md`'s SLA/KPI numbers are
  verified; a real dispatch-latency or payment-failure regression during
  live app testing would only surface via user complaints/support tickets,
  not an alert.
- **Risk of implementing:** low overall — doesn't touch ride/dispatch/
  payment/auth business logic — but see the Dockerfile/Trivy risk above if
  the colocated-agent option is chosen; otherwise routine additive-deploy
  risk only.
- **Effort estimate:** ~4–8 hours active engineering time (half a day to a
  full day) per ADR-010 §5, plus Grafana Cloud account lead time.
- **Verification once implemented:** confirm the Grafana dashboard panel
  populates from real production traffic, confirm the 2 alert rules don't
  false-fire against normal load, and confirm `docker-image-scan` (Trivy)
  is still green if the colocated-agent option was chosen.
- **Files (once implemented):** `backend/fly.toml`, `backend/Dockerfile`
  (or a new standalone app) + Grafana Cloud config (external, not in this
  repo).

### C12. Codecov uploads on `main` pushes silently fail — no token configured
- [x] **Status:** CLOSED 2026-08-16 — user explicitly chose "remove the
  step" over "fix the token" (no Codecov account access available in-session
  to generate one). All 4 `codecov/codecov-action@v6` upload steps removed
  from `ci.yml` (`backend-test`, the already-dead `frontend-test`,
  `driver-app-test`, `rider-app-test`) along with their now-orphaned
  `steps.codecov_upload.outcome == 'failure'` warning-annotation follow-ups.
  Verified the "Coverage Regression"/"Corporate Coverage Floor" guardrails
  in `ci-guardrails.yml` never depended on these uploads (they compute
  PR-branch coverage locally via `pytest --cov`), so this is a pure noise
  reduction — no gating behavior changed. **Investigating this surfaced a
  separate, more important gap — see C24.** Re-open (new item, don't reuse
  this checkbox) if Codecov access is ever obtained and the dashboard/
  historical-trend value is wanted back.
- **Prior status before closing:** open — partially addressed 2026-08-11 (the
  "also worth doing alongside" half, not the actual fix — see below). The
  `CODECOV_TOKEN`
  secret itself is still missing; do not close this checkbox until it's
  added and `token: ${{ secrets.CODECOV_TOKEN }}` is wired into the 3 steps
  below.
- **2026-08-11 update:** added a follow-up step after each of the 3
  *actually-running* `codecov/codecov-action@v6` calls (`backend-test`,
  `driver-app-test`, `rider-app-test`) that checks `steps.codecov_upload.outcome
  == 'failure'` (the step's real result, unmasked by `continue-on-error`) and
  emits a `::warning::` annotation plus a `$GITHUB_STEP_SUMMARY` entry when
  the upload fails. Does **not** change the job's overall conclusion — still
  green until the token exists — only stops the failure from being invisible.
  **Correction to the "4 times... backend/rider-app/driver-app/admin" count
  below: `admin-test` has no Codecov upload step at all** — checked directly
  against `ci.yml`, only `backend-test`, the disabled `frontend-test` (`if:
  false`, dead code, not touched), `driver-app-test`, and `rider-app-test`
  have one. So the real count of live jobs affected is 3, not 4. YAML
  validated via `yaml.safe_load` (parses clean, correct step count per job);
  no live GitHub Actions run available in this session to confirm the
  `::warning::`/summary actually fires on a real rejected upload — next
  push-triggered `main` run (which reliably rejects tokenless, per the
  original finding) is the real confirmation.
- **What's wrong:** `.github/workflows/ci.yml`'s `backend-test` job uploads
  to Codecov via `codecov/codecov-action@v6` with no `token:` input (tokenless
  upload) and `continue-on-error: true`. On push-triggered runs (as opposed to
  PRs from within the same repo) Codecov's tokenless mode rejects the upload:
  the job log shows `error - Upload queued for processing failed: {"message":
  "Token required - not valid tokenless upload"}` — but because of
  `continue-on-error: true`, the step itself still reports `conclusion:
  success` in the GitHub UI. Nothing about the green checkmark tells you the
  upload was rejected. Confirmed on run
  [`31348756297`](https://github.com/srikumarimuddana-lab/spinrvm/actions/runs/31348756297/job/93335534234)
  (commit `64a720e`, 2026-08-10).
- **Impact:** the Codecov dashboard has no current data for `main`-branch
  pushes (only whatever tokenless PR uploads succeeded, if any) — anyone
  checking Codecov instead of a live CI log for "what's our coverage right
  now" gets stale or missing numbers with no error surfaced. The same
  `codecov-action@v6` step (same tokenless/`continue-on-error` pattern)
  appears 4 times in `ci.yml` (once per test job:
  backend/rider-app/driver-app/admin), so this likely affects all four, not
  just backend — not independently re-verified here, flagging as an unknown.
- **Root cause:** no `CODECOV_TOKEN` repository secret is configured, and the
  workflow doesn't pass one via `with: token:`.
- **Why not fixed here:** requires a human with GitHub repo-admin access to
  generate a Codecov upload token (Codecov project settings → General →
  Repository Upload Token) and add it as `GitHub → repo → Settings → Secrets
  and variables → Actions → CODECOV_TOKEN` — no dev session/sandbox can do
  this. Once the secret exists, add `token: ${{ secrets.CODECOV_TOKEN }}` to
  each of the 4 `codecov-action@v6` steps in `ci.yml`.
- **Also worth doing alongside the token fix:** drop `continue-on-error:
  true` (or add a step afterward that great-greps the codecov CLI output for
  `"Upload queued for processing failed"` and fails loudly) so a *future*
  upload failure — token expiry, Codecov outage, config drift — surfaces as
  a red check instead of silently passing again. As written today, fixing
  the token alone reduces the immediate symptom but leaves the same
  false-green failure mode dormant for next time.
- **Not blocking:** actual test pass/fail and coverage-floor enforcement
  (the `--cov-fail-under` gate inside `pytest`) are unaffected — this is
  purely the external Codecov *reporting* path, not CI's own gate.

### C24. "Coverage Regression" guardrail cannot fail while `CODECOV_TOKEN` is unset — every PR auto-passes it
- [x] **Status:** partially CLOSED 2026-08-16, same day as found. The
  **honesty half** is fixed: `ci-guardrails.yml`'s "Assert no regression"
  step now distinguishes `base_pct <= 0` (no baseline — cannot verify
  anything) from a genuinely verified pass/fail, sets a
  `baseline_status` job output (`no-baseline` vs `verified`), and
  `guardrail-summary` renders the PR-facing table row as
  `⚠️ not verified (no baseline — see ACTION_ITEMS.md C24)` instead of a
  bare ✅ when there's no baseline — the false "PASS: Coverage within
  tolerance" message is gone; the step's own log and the PR summary now
  both say plainly that no regression check happened. **Still open:** this
  does not add real regression detection back — that still needs a real
  `CODECOV_TOKEN` (same blocker as C12, no account access available in any
  session so far). Deliberately did **not** make `base_pct <= 0` a hard
  `sys.exit(1)` — job-level `continue-on-error: true` was already present
  before this fix and a missing token isn't any individual PR author's
  fault; turning it into a blocking failure for every PR until someone
  adds the token would be a bigger, more disruptive behavior change than
  this fix's actual goal (truthful reporting), and wasn't asked for.
- **What's wrong:** `.github/workflows/ci-guardrails.yml`'s "Fetch base
  branch coverage from Codecov" step (`base_coverage` job step, ~line 82)
  calls `https://codecov.io/api/v2/github/.../branches/{base}/coverage`
  with `Authorization: Bearer ${CODECOV_TOKEN}`. With no `CODECOV_TOKEN`
  secret configured (same root cause as C12), that call fails; the script's
  own fallback (`... || echo "0"`) sets `BASE_PCT=0`. The next step,
  "Assert no regression", only fails when `base_pct > 0 and delta <
  -TOLERANCE` — since `base_pct` is always `0`, that condition can **never**
  be true. The gate does not degrade gracefully, it **structurally cannot
  fail**, and has printed "PASS: Coverage within tolerance" on every PR this
  session regardless of what the PR actually did to coverage.
- **Impact:** "Coverage Regression: success" in every Guard Rails summary
  this session (and, per the mechanism, every PR since this workflow was
  introduced) has been a rubber stamp, not a real check. A PR that
  genuinely tanked backend coverage would show the same green tick as one
  that improved it. This is a real gap in the release-quality gate, more
  consequential than C12's noisy-but-harmless upload failures.
- **Root cause:** same missing `CODECOV_TOKEN` secret as C12, but this is
  the half that actually changes gating behavior — C12 only affected
  external reporting.
- **Why not fixed here:** same constraint as C12 — no Codecov account
  access available in this session to generate a real token. Two real
  fixes, not mutually exclusive: (1) add `CODECOV_TOKEN` so the base-branch
  fetch actually works; (2) regardless of (1), make the "Assert no
  regression" step fail loudly (or at minimum warn) when `base_pct == 0`
  instead of silently treating "no baseline data" as "no regression" —
  those are different findings and should not share one code path.
- **Confirmed NOT shared:** checked `corporate-coverage-floor-gate` (the
  "Corporate Coverage Floor" guardrail) directly — it's a fully separate
  job that runs its own test suite and calls
  `scripts/check_corporate_coverage_floor.py` locally, no Codecov API call
  anywhere in it. Only "Coverage Regression" has this bug.

### C13. Required `pull_request`-triggered workflows silently never fire on some PRs
- [ ] **Status:** open — found 2026-08-10 on PR #3494. `CI/CD Pipeline`
  (`ci.yml`), `Security Gates`, `CI Guard Rails`, and `PR Checks` — all
  confirmed `active` workflows, all normally triggering on `pull_request`
  events per their own `on:` blocks — showed **zero runs** against either of
  two consecutive commits on that PR (the PR-open commit, and a follow-up
  empty commit pushed specifically to force a `synchronize` event). Verified
  directly against the Actions API (`list_workflow_runs`), not just the PR's
  check-runs view, which can lag. Nothing was stuck in `action_required`
  (rules out a first-time-contributor approval gate), and every workflow's
  `state` is `active` (rules out a disabled workflow). The only thing that
  did run was Vercel's own bot-posted status (expected — always fires, not
  a `pull_request`-triggered GitHub Actions workflow).
- **Why it matters:** this is the same failure *shape* as the already-tracked
  C9 (Codex auto-review going silent) and C7 (Claude review off by design) —
  a third, independent instance of "no automated PR review/gate signal
  arrives," this time hitting native GitHub Actions rather than a
  third-party bot. A PR whose required checks never even run cannot show a
  legitimate green state and risks being merged on manual override with zero
  automated verification, or blocked indefinitely with no actionable error.
- **What was tried:** pushing an empty commit to force a `synchronize`
  `pull_request` event — did not retrigger the workflows either. This rules
  out "the PR was opened as a draft and workflows correctly skip drafts" as
  the sole explanation (the PR was later marked ready for review, which is
  itself a `pull_request` event type `ci-guardrails.yml`/`pr-checks.yml`
  explicitly listen for, and still nothing fired).
- **Not resolvable from an engineering session** — no repo-admin access to
  check the two likely causes: (1) **Settings → Actions → General** — a
  "require approval for all outside collaborators" or similar restriction
  that doesn't surface as `action_required` in the API the way a
  fork-PR approval gate normally would, or an org-level Actions policy; (2)
  **Settings → Webhooks** — a failed/disabled delivery for the
  `pull_request` event specifically (other event types, e.g. this repo's
  own push-triggered workflows, were observed firing normally on the same
  commits).
- **Files:** none — this is a GitHub App/repo-configuration issue, not a
  workflow YAML defect. No `.github/workflows/*.yml` change is implicated;
  all four workflows' `on:` blocks are correctly configured.
- **Acceptance:** a repo admin confirms (or rules out) an Actions/webhook
  restriction via the two settings pages above; once addressed, a fresh PR's
  `CI/CD Pipeline` run should appear within the workflow's normal start
  latency (observed elsewhere in this file as low-minutes) of PR
  open/synchronize/ready-for-review.

### C14. `Migration Safety Check` false positives, and a blank-template PR merged with no compliance flags ticked
- [x] **Status:** the checker bugs are fixed (2026-08-10); the merged-PR
  process gap below is logged, not fixable after the fact.
- **Found on PR #3497** ("Claude/stripe card sync issue gepad1" — the title
  and PR body were stale/blank; the PR actually collected and Vault-encrypted
  driver SINs for T4A filing, added a Stripe-onboarding SIN gate, and closed
  two TOCTOU races a security audit found in the SIN writes — a real,
  well-reasoned 12-commit change). Investigated its 3 `Migration Safety
  Check` failures before assuming any were genuine:
  1. **11 "append-only: never edit a merged migration" failures — false
     positive, checker bug, now fixed.** Root cause:
     `.github/workflows/migration-check.yml`'s "Detect changed migration
     files" step diffed the PR against `github.event.pull_request.base.sha`
     directly (two-dot diff) instead of the actual merge-base. `base.sha` is
     the base branch's **current, continuously-moving** tip, not a fixed
     fork point — so any PR branch that falls behind `main` (hasn't
     merged/rebased in a while) sees every migration `main` gained since as
     "deleted by this PR" (present in the two-dot diff's base side, absent
     on head), which the per-file check then reports as an append-only
     violation even though the PR never touched those files. Confirmed by
     diffing the 11 flagged files between `main` and the PR branch: **byte-
     identical**, simply absent on the stale branch. Fixed: the "Detect
     changed migration files" step now diffs from `git merge-base BASE
     HEAD` instead of raw `BASE`, so only files the PR branch itself
     actually added/modified/deleted since it diverged are considered.
  2. **Rollback-comment check — false negative, checker bug, now fixed.**
     `^--\s+[Rr]ollback:` required an exact "Rollback:"/"rollback:"
     immediately after `-- `. PR #3497's new migration
     (`289_driver_sin_encrypted.sql`) documented rollback thoroughly under
     an all-caps `-- ROLLBACK` section header (including the non-obvious
     `vault.secrets` orphan-cleanup step) — didn't match. Grepped this
     repo's own 369 migration files: the strict regex only matched 248 of
     them; real usage varies a lot (`Rollback plan:`, `-- ROLLBACK (on
     paper):`, `/* Rollback: ... */`), none of which the old regex covered.
     `backend/migrations/CLAUDE.md` itself only requires "the rollback plan
     in a top comment," no specific phrasing. Loosened to `(?:--|/\*)\s*
     rollback\b`, case-insensitive, matched against comment text only (see
     #3 below) — re-run against all 369 migrations: 306 now pass (up from
     248), **zero regressions** (nothing that passed the old regex fails
     the new one).
  3. **Dangerous-ops warning — false positive, checker bug, now fixed.**
     Scanned the whole raw file text for `DROP TABLE`/`TRUNCATE`/`ALTER
     TABLE ... DROP COLUMN`, so a rollback *comment* describing exactly
     those statements (to undo the migration) tripped the same warning as
     if the migration executed them. Same root-cause shape as the
     `migrate.py` `CONCURRENTLY` misdetection fixed for B0 — a raw-text
     scan that doesn't distinguish comments from executable SQL. Added a
     simple line-based comment stripper (deliberately not the full
     tokenizer `migrate.py` needed — this script only ever reads comment
     *text*, never re-executes SQL, so a naive strip's worst failure mode
     is a missed/over-eager warning, not a broken apply) and scan only the
     remaining code. Re-run against all 369 migrations: dropped from 155
     flagged files to 7, and the 7 that remain are genuine executable
     `TRUNCATE`/`DROP COLUMN` statements (spot-checked) — zero newly-flagged
     files, zero regressions.
- **Real, small, still-open item this surfaced (not fixed here, informational
  only):** PR #3497's new migration is `289_driver_sin_encrypted.sql`, but
  `main` already has an unrelated `289_financial_events_purge_delete_gate.sql`
  — a genuine duplicate numeric prefix, now merged. Per this repo's own
  migration convention ("Duplicate numeric prefixes exist from history and
  are handled by full-filename keying — do not introduce new duplicates"),
  the runner is unaffected (its idempotency key is the full filename, which
  differs), but it's exactly the drift the convention says to avoid going
  forward. **Deliberately not renamed** — root CLAUDE.md's migration rules
  are explicit that "already-applied migrations must never be renamed" since
  the runner's idempotency key is the filename; renaming a merged migration
  risks a runner in any environment that already recorded
  `289_driver_sin_encrypted.sql` as applied re-attempting it under a new
  name. No functional fix needed; noted here so the next duplicate-prefix
  sighting isn't re-investigated from scratch.
- **Process gap this PR also exposed, not fixable retroactively:** #3497
  merged with its PR template completely blank — every Tier 1/2/4 field
  still the raw placeholder text, including Tier 3's `Money-touching` and
  `PIPEDA-relevant` compliance checkboxes unticked on a PR whose entire
  purpose is collecting and encrypting a government ID. Per root CLAUDE.md,
  this repo currently has **no automated PR review running** (see C7/C9) —
  nothing else was positioned to catch this before merge. Logged rather than
  silently passed over; no corrective action possible on an already-merged
  PR beyond noting it. If a `Required PR fields filled`-style gate (already
  proven out on PR #3494/#3501, this session) doesn't already block merge on
  a blank template repo-wide, that's worth confirming — it clearly should
  have stopped this one.
- **Files:** `.github/workflows/migration-check.yml` (all 3 fixes).
- **Verification:** re-ran the fixed rollback-comment and dangerous-ops
  logic against every one of the 369 files currently in
  `backend/migrations/` (not just the one PR that surfaced the bugs) — zero
  regressions on either check, confirmed via direct comparison against the
  old regex's pass/fail sets. The append-only merge-base fix was verified by
  confirming the 11 previously-flagged files are byte-identical between
  `main` and the PR branch (root-cause diagnosis), not by re-running the
  workflow itself (no open PR currently reproduces the stale-branch
  condition to test against live). Embedded Python script syntax-checked
  (`ast.parse`) after edits; YAML re-validated.
- **What was NOT verified:** the merge-base fix specifically, end-to-end
  against a real GitHub Actions run with a genuinely stale PR branch — the
  fix is a straightforward, well-understood `git merge-base` substitution
  with no ambiguity in what it should do, but it hasn't been observed
  clearing a real red check the way the other two fixes were confirmed
  against the full migrations corpus.

### C15. `bulk-operations` admin page: `useState` called after a conditional early return
- [x] **Status:** done (2026-08-10).
- **Why:** `BulkOperationsPage` (`admin-dashboard/src/app/dashboard/bulk-
  operations/page.tsx`) called `useState` for `discovering`/`setDiscovering`
  at line 348, **after** the component's `if (!isSuperAdmin) { return (...)
  }` early return at line 320. A genuine React `rules-of-hooks` violation
  (`G2 · ESLint security plugin` / `admin-test`), not a lint false positive:
  if `isSuperAdmin` ever changes without a full remount of this component,
  React's hook-call-count-per-render invariant breaks — the classic
  "Rendered more hooks than during the previous render" failure mode. Found
  2026-08-10 while investigating PR #3508 (which correctly identified this
  failure as pre-existing and unrelated to its own diff, and was right not
  to fix it inline — confirmed via `git diff` that the file is byte-
  identical to `main` before and after that PR). Untracked anywhere until
  now.
- **Fix:** moved the `useState(false)` declaration up to join the other 10
  hook calls at the top of the component, before the early return. One-line
  move, no behavior change to any hook's value or setter.
- **Files:**
  `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx`
- **Verification:** `npx eslint` on the file — `rules-of-hooks` error gone,
  the one remaining warning (`RiderImportSection` unused-var) is unrelated
  and pre-existing. `npx tsc --noEmit` — 27 pre-existing errors elsewhere in
  the admin-dashboard test suite (matches PR #3508's own count of "28
  pre-existing," off by one, unrelated to this file), zero in this file
  before or after. **Real production build** (`npm run build`, not just
  `tsc --noEmit`) passes, `bulk-operations` route builds. No automated test
  exists for this component (same standing gap #3508 itself flagged for the
  admin dashboard) — verified by lint + build + manual code read, not by a
  regression test.
- **What was NOT verified:** not exercised in a running browser — no visual
  regression tooling exists for this surface (standing gap, same one
  `ACTION_ITEMS.md`/change-logs elsewhere in this file already flag for
  admin-dashboard UI changes). The scenario this fix actually protects
  against (a super-admin's role changing mid-session without a remount) was
  not manually reproduced.

### C16. `admin-dashboard` test files were never covered by `next build`'s type-check, and where they briefly were (dependabot PR #3483), it broke the build
- [x] **Status:** CLOSED (2026-08-11) — did the "real fix" this entry itself
  scoped: added `"types": ["vitest/globals", "@testing-library/jest-dom"]`
  to `tsconfig.json`'s `compilerOptions` and dropped the `src/__tests__`
  exclusion entirely (all 20 test files are now honestly in the build's
  type-check scope, not just the 11 that were silently in-scope before).
  Fixed the 4 error sites that surfaced — 1 more than this entry's own
  scratch-worktree dry run found, see below:
  - `src/lib/__tests__/companyApi.test.ts:107` — the flagged one-liner:
    `as {...}` → `as unknown as {...}` (the mocked `NextResponse.json`
    return shape doesn't structurally overlap with the real
    `NextResponse<any>` type; zero functional risk, confirmed by this
    entry's own investigation).
  - `src/__tests__/dashboard/pages.smoke.test.tsx:414,449,463` — this
    entry's dry run had these 3 sites' root cause as "not yet diagnosed";
    it turned out to be simple: the shared `renderPage()` smoke-test helper
    expects `ComponentType<{}>`, but 3 page components
    (`/dashboard/surge`, `/dashboard/notifications`, `/dashboard/documents`
    — all simple `redirect()`-only stub pages with no explicit `return`)
    had their return type inferred as `void`, not `ReactNode`, since a
    function with no `return` statement defaults to `void` regardless of
    `redirect()`'s own `never` return type. Fixed by adding an explicit
    `: never` return-type annotation to each of the 3 page functions —
    `never` satisfies `ReactNode` by TS's covariance rules, and the runtime
    behavior (call `redirect()`, never actually return) is unchanged. No
    edit needed to `pages.smoke.test.tsx` itself.
  - Verification: `npx tsc --noEmit` clean (zero errors), `npx vitest run`
    — 160/160 passed (all 20 test files, up from the 11 the old build scope
    covered), and a real `npm run build` — full production build, exit
    clean, complete route manifest printed (not just `tsc --noEmit` or a
    dev server per CLAUDE.md's explicit requirement for any admin-dashboard
    change). `npx eslint` on every touched file — 0 errors, only
    pre-existing unrelated warnings in `pages.smoke.test.tsx`.
  - **What was NOT verified**: no visual/browser check of the 3 redirect
    pages beyond the build succeeding and the existing smoke tests passing
    — this repo has no visual regression tooling (standing gap, N12).
- **Why:** `tsconfig.json`'s `exclude` list only excluded `src/__tests__` by
  directory name. 11 of the app's 20 `*.test.ts(x)`/`*.spec.ts(x)` files live
  outside that directory (colocated `_components/*.test.tsx`, `hooks/*.test.tsx`,
  `lib/__tests__/*`, etc.) and were silently included in `next build`'s
  type-check scope all along — invisible only because nothing in the
  toolchain's own type-checking had ever surfaced an error in them, not
  because they were actually excluded. Found while verifying dependabot PR
  #3483 (`next` 16.2.12→16.3.0, one of the "next-stack" group bump): that
  PR's own changelog includes "Enable TypeScript CLI by default," and under
  the new stricter check `npm run build` fails with "Failed to type check"
  on 3 of those 11 files — confirmed via a clean before/after (`main`
  builds, the bump branch doesn't) that this is a real regression surfaced
  by the bump, not a flake. The three failures are two different root
  causes: missing `vitest`/`@testing-library/jest-dom` global types
  (`route-segments.test.ts`, `driver-statements-panel.test.tsx`), and one
  genuine type-narrowing bug in test code itself
  (`companyApi.test.ts:105` — `as {_data:...}` doesn't structurally overlap
  with the real `NextResponse<any>` return type; the test already passes at
  runtime under `vitest run`, so this is a compile-time-only strictness
  complaint, not a hidden behavioral bug).
- **Immediate unblock (applied to PR #3483 directly, not a fix for this item):**
  broadened `tsconfig.json`'s `exclude` to a filename-pattern match
  (`**/*.test.ts`, `**/*.test.tsx`, `**/*.spec.ts`, `**/*.spec.tsx`) covering
  all 20 test files consistently, instead of the incomplete
  directory-name-only pattern. Verified: `tsc --noEmit` clean, real
  `npm run build` passes, and the 3 previously-failing files still run and
  pass under `npx vitest run` (18/18) — the exclusion only removes them from
  the build's type-check, not from actual test execution.
- **The real fix (not yet done):** add `"types": ["vitest/globals",
  "@testing-library/jest-dom"]` to `tsconfig.json`'s `compilerOptions`, drop
  the test-file exclusions entirely, then fix what that surfaces. Already
  dry-ran this in a scratch worktree with **zero exclusions** (the honest
  full scope, including the 9 files under the old `src/__tests__` exclude
  that had never been type-checked at all): only **2 files, 4 error sites**
  total across the whole 20-file suite.
  - `companyApi.test.ts:105` — one-line `as unknown as {...}` cast, zero
    functional risk (confirmed above).
  - `src/__tests__/dashboard/pages.smoke.test.tsx` (3 sites) — a shared
    `renderPage()` smoke-test helper called with page components whose
    inferred return type doesn't satisfy `ComponentType<{}>`; root cause
    (helper's generic signature vs. specific page export types) not yet
    diagnosed — this is the one part of the estimate that's a real unknown
    until someone opens it.
- **User-experience effect:** none, either way. `*.test.ts(x)` files are
  never bundled into anything Next.js ships — no route, page, or API output
  includes them regardless of whether the build's `tsc` pass checks them.
  The benefit of doing the real fix is dev-experience/CI-signal quality
  only: correct IDE type-hints in test files, and a genuine safety net
  against test-code bugs like the `companyApi.test.ts` one (harmless today,
  but the pattern — a test file invisible to type-checking — is exactly how
  a *behavioral* mismatch could hide next time, not just a strictness
  complaint).
- **Files:** `admin-dashboard/tsconfig.json` (unblock, applied);
  `admin-dashboard/src/lib/__tests__/companyApi.test.ts`,
  `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx` (real fix,
  not yet applied).
- **Effort estimate for the real fix:** tsconfig change ~5 min +
  `companyApi.test.ts` one-liner ~5 min + `pages.smoke.test.tsx`
  investigation 30–60 min (unknown until opened) + full re-verification
  (`tsc` + `npm run build` + `vitest run`) ~15 min ≈ **under 2 hours** total.
- **What was NOT verified:** the `pages.smoke.test.tsx` root cause itself —
  scoped by error count and file count, not by actually fixing it. Not
  checked whether other admin-dashboard-adjacent surfaces (rider-app,
  driver-app) have the same directory-name-only test-exclude gap in their
  own `tsconfig.json`s — this item is scoped to admin-dashboard only,
  where it was found.

### C17. No CI job ever ran an actual Metro bundle — 8 consecutive EAS Mobile Update jobs failed silently on `main` after the SDK 57 bump before anyone noticed
- **CORRECTION (2026-08-11, later same day):** the line below ("EAS Mobile
  Update jobs" now healthy) is **wrong as a production status claim** — it
  was true only for the specific bundling failure this item and the linked
  change log describe. Checking the actual `EAS Mobile Update` run history
  (run #620, `f011ff3`, well after `d4b573c` merged) found bundling now
  succeeds but `eas update` still fails 100% of the time, at a **different,
  later step** neither this item's nor the change log's verification ran
  (`expo export` doesn't reach it; only the real `eas update` command does).
  See **C19** for the still-open, still-live bug and its fix. Leaving this
  item's text below as-written (it's accurate about what it actually fixed
  and verified) rather than editing history — read C19 for current reality.
- [x] **Status:** CI gate CLOSED (2026-08-11) — the underlying bundle break
  itself was already fixed same-day by commit `d4b573c` (see
  `docs/change-log/2026-08-11-metro-rngh-renderer-shim.md`); this item adds
  the missing preventive control so the *next* dependency-only break of this
  shape fails a PR check instead of failing 8 production OTA pushes in a row.
  Added `.github/workflows/mobile-bundle-smoke.yml`: on every PR touching
  `rider-app/**`, `driver-app/**`, or `shared/**`, runs
  `npx expo export --platform android` and `--platform ios` for both apps —
  the same bundling step `eas update`/`eas build` perform, run before merge
  instead of after. Mirrors `mobile-dep-check.yml`'s job/cache-key shape;
  intentionally **not** `continue-on-error` — a bundle failure must block
  merge, not degrade quietly the way `expo install --check` does in that
  workflow (which needs `EXPO_TOKEN` and degrades on purpose for that
  reason — this check needs neither and has no legitimate soft-fail case).
- **Root cause (of the CI gap, not the bundle break — that's in the change
  log above):** `mobile-dep-check.yml` runs `tsc --noEmit` and
  `expo install --check`. Neither performs an actual Metro bundle:
  `tsc` type-checks source, and RNGH's `RNRenderer.ts` typechecks fine (the
  import path is a valid TS string, the file just doesn't exist in RN 0.86's
  shipped shims at bundle-resolution time); `expo install --check` only
  diffs installed package *versions* against Expo's SDK compatibility table,
  it never resolves a single module. `eas-build.yml` ("EAS Mobile Update")
  is the only workflow that ever bundles, and it runs **after** merge, on
  every push to `main` — so the failure mode is: merge lands clean (both
  existing checks green), then the very next push-triggered OTA job dies at
  the bundle step, for every push to `main` touching either app, until
  someone looks at the EAS dashboard. That's the 8-update-in-a-row failure
  streak (`#605`–`#612`) visible in the EAS activity log the SDK 57 bump
  (`#605`, the Dependabot expo-stack group bump) kicked off — two follow-up
  PRs explicitly titled "complete the SDK 57 upgrade... left half-done"
  (`#607` rider-app, `#609` driver-app) fixed real app-level SDK 57 items but
  couldn't have caught this one: the break lived inside a third-party
  dependency's internal import, not in either app's own code, and nothing
  in the toolchain exercised it before a real EAS job did.
- **Risk & impact of the new check:** build-time-only CI addition, zero
  runtime/production code touched. Blast radius: PR merge gate for
  rider-app/driver-app/shared changes only — does not touch
  `eas-build.yml`'s OTA publish, `eas-native-build.yml`'s native build
  trigger, or `mobile-dep-check.yml`'s existing checks (all left as-is,
  running alongside this one). Failure mode if the new check itself is
  flaky: a false-red PR block, not a false-green — fails safe.
- **Effort:** ~1 hour (one workflow file, mirrors existing patterns; no new
  secrets, no infra). Follow-up not yet done: this check should be added to
  the repo's required-status-checks branch-protection list for `main` so it
  actually blocks merge rather than just reporting — that's a GitHub repo
  settings change outside this diff's scope, needs a repo admin.
- **Verification performed:** ran the exact commands the new CI job runs,
  locally, against current `main` (which already has the `d4b573c` shim
  redirect). `rider-app` — `npx expo export --platform android` exits 0,
  Hermes bundle produced (already verified same-day in the change-log
  entry above). `driver-app` — `yarn install --frozen-lockfile` then
  `npx expo export --platform android`, run fresh in this session (the
  change log explicitly flagged driver-app as **not** run in its own
  session — that gap is now closed): exit 0, `_expo/static/js/android/
  index-*.hbc` (8.2MB) produced, confirming the metro.config.js redirect
  works for driver-app too, not just rider-app. `--platform ios` not run
  for either app in this session (no meaningful iOS/Android code-path
  divergence expected for this specific break — RNGH's `resolveRequest`
  match is exact-string and platform-agnostic — but the CI job itself does
  run both platforms going forward, so this gap closes on its first PR run).
- **What was NOT verified:** whether the new workflow file's YAML is 100%
  correct GitHub Actions syntax beyond mirroring `mobile-dep-check.yml`
  structurally — not dry-run through `act` or an actual PR in this session;
  first real PR touching a mobile app will be the live test. Branch
  protection was not modified (see Effort above — explicitly out of scope,
  flagged for a human with repo-admin access).
- **Files:** `.github/workflows/mobile-bundle-smoke.yml` (new).

### C18. GitHub Actions steps use mutable version tags repo-wide, not pinned commit SHAs — Semgrep/GHAS flags it on every new workflow line
- [x] **Status:** CLOSED (2026-08-12) — all 176 `uses:` references across 23
  `.github/workflows/*.yml` files pinned to verified commit SHAs, with the
  human-readable version kept as a trailing comment
  (`uses: actions/checkout@<sha> # v7`). Full Change Impact Log:
  `docs/change-log/2026-08-12-c18-pin-github-actions-shas.md`.
  **How the "Verification reliability" blocker below was resolved:** this
  session's git proxy serves anonymous, read-only `git clone`/`git
  ls-remote` access to any public GitHub repo even when it isn't in this
  session's attached repository scope (confirmed via `add_repo`, which
  reports read access already available without needing attachment). Used
  that channel to clone all 19 distinct action repos referenced (18
  originally enumerated below + `github/codeql-action`, a subpath-style
  reference the original grep pattern missed — see the change log's §4) and
  resolve each `@vN` tag to its exact commit SHA via `git ls-remote --tags`
  against the real upstream repo, then cross-checked every SHA as a live ref
  tip on `origin` before use. This is a direct read of the actual git ref
  database — the same information `gh`/the GitHub API would return — not
  the release-page scrape the original investigation correctly rejected as
  unreliable for a character-exact hash.
  One finding worth flagging on its own: `8398a7/action-slack@v3` had no
  `v3` **tag** at all — it resolved to a **branch**, which is even more
  mutable than an unpinned major-version tag normally is. That reference is
  now pinned like everything else.
- **(historical) Status:** open — found 2026-08-11, via GitHub Advanced Security /
  Semgrep OSS comments on PR #3668 (6 findings, rule
  `yaml.github-actions.security.github-actions-mutable-action-tag`) on the
  new `mobile-bundle-smoke.yml` workflow added by that PR. Confirmed real:
  every `uses: actions/checkout@v7` / `actions/setup-node@v7` /
  `actions/cache@v6` line resolves a floating major-version tag, which the
  action owner (or anyone who compromises their account) can silently
  repoint to different code — the exact supply-chain mechanism behind the
  real-world `tj-actions/changed-files` and `reviewdog/action-setup`
  incidents Semgrep's rule description cites.
- **Why not fixed in this PR:** two reasons, not one.
  1. **Scope/consistency** — this is not specific to the new file. Repo-wide
     grep: **154 unpinned `uses:` references across 23 of the 24 files** in
     `.github/workflows/`. Pinning only the 6 lines Semgrep happened to flag
     (because they're new, not because they're uniquely risky) would leave
     the actual exposure — the other 148 references — untouched, while
     making this one file inconsistent with the rest of the repo's own
     convention.
  2. **Verification reliability** — pinning to a SHA means hardcoding a
     40-character string that, if wrong, breaks the workflow outright (a
     bad SHA doesn't degrade gracefully, it fails to resolve the action at
     all). This session has no reliable way to confirm one: this repo's
     GitHub access is scoped to `srikumarimuddana-lab/spinrvm` only — a
     direct API check against `api.github.com/repos/actions/checkout/...`
     is rejected at the proxy ("GitHub access to this repository is not
     enabled for this session"). The only alternative available here was
     scraping a rendered release page through a small-model web-fetch tool,
     which is exactly the kind of source you should *not* trust for a
     character-exact hash — a transposed digit is invisible until CI runs.
     Guessing was rejected on purpose, consistent with this repo's own
     "verify a newer/patched version actually works before pinning it"
     pre-merge gate (`CLAUDE.md` § Pre-merge release gates, item 8) — that
     principle applies just as much to *what you pin to* as to *what
     version you bump to*.
- **What this needs:** a session/operator with real `gh`/authenticated
  GitHub API access (or Dependabot's own SHA-pinning update mode, if
  enabled) to resolve all 154 references to verified commit SHAs in one
  sweep, plus a comment noting the human-readable version next to each pin
  (`uses: actions/checkout@<sha> # v7`) so future readers don't have to
  resolve the SHA back to a version themselves.
- **Risk if left as-is:** repo-wide supply-chain exposure to a compromised
  or repointed Action tag — not unique to CI/CD pipelines in general, but
  worth weighing against this repo's existing Trivy/cosign image-signing
  investment elsewhere in `ci.yml`, which addresses container supply chain
  but not this GitHub-Actions-level one.
- **Effort estimate:** small per-reference (find current SHA, replace,
  comment) but multiplies across 154 references in 23 files — realistically
  a half-day sweep plus a follow-up PR review pass, not a quick fix.
- **Files:** all of `.github/workflows/*.yml` except the one file (if any)
  that's already fully pinned — not individually enumerated here; run the
  grep in this entry's own investigation to get the current list.

### C19. `eas update` is still 100% broken on `main` today — a second, different bug downstream of the C17/RNGH fix, in `eas update`'s fingerprint-computation step
- [x] **Status:** DURABLY CLOSED (2026-08-11, same day, follow-up pass) —
  the actual `yarn.lock` resolution bug is now fixed at the source in both
  apps; the `EAS_SKIP_AUTO_FINGERPRINT` bypass below has been **removed**,
  not just documented as removable. Sequence: mitigated first (bypass),
  then durably fixed once the real root cause in `resolutions` was found —
  see "Durable fix" below for what actually shipped.
- **How found:** user asked to check the actual EAS Mobile Update run
  history from `#604` (last green) onward, rather than trust the C17 fix's
  own "already fixed" framing. Every run **including the latest at the time
  (`#620`, commit `f011ff3`, well after `d4b573c` — the C17/RNGH fix —
  merged)** is red for both apps. That framing gap is itself worth naming:
  C17's verification ran `expo export`, which stops at "bundle produced."
  `eas update` (the actual production command `eas-build.yml` runs) does
  strictly more after that — it also computes a project fingerprint before
  publishing — and that's where it now dies. **A green `expo export` does
  not mean a green `eas update`**; the two aren't the same command.
- **Root cause, reproduced directly (not inferred from a log alone):** ran
  `require('@expo/fingerprint').createFingerprintAsync(cwd)` in driver-app's
  installed `node_modules` (same library `eas update` calls internally) and
  got the identical crash byte-for-byte:
  `(0 , brace_expansion_1.expand) is not a function`, at
  `@expo/fingerprint/node_modules/minimatch/dist/commonjs/index.js:157`.
  `@expo/fingerprint@0.20.6` bundles its own `minimatch@10.2.5`, whose
  `package.json` declares `"brace-expansion": "^5.0.5"` — but that nested
  `minimatch` has **no nested `node_modules/brace-expansion` of its own**,
  so Node's resolution walks up and finds the top-level, incorrectly-hoisted
  `brace-expansion@1.1.18` instead (confirmed via
  `require.resolve('brace-expansion', {paths: [...]})`). `brace-expansion`
  v1's export is a bare function (`module.exports = expandTop`), not an
  object with an `.expand` property — hence `.expand is not a function`.
  The `yarn.lock` entry itself looks corrupted/stale: one block claims
  ranges `^1.1.18, ^1.1.7, ^2.0.1, ^2.0.2, ^5.0.5` **all** resolve to version
  `1.1.18` — which is semver-impossible for the `^2.x`/`^5.x` ranges in that
  same line — while a separate, correct `brace-expansion@^5.0.9: version
  "5.0.9"` entry exists elsewhere in the file but isn't the copy Node
  actually reaches from `@expo/fingerprint`'s nested `minimatch`. Blast
  radius of the conflicting range: grepped both apps' lockfiles —
  `minimatch@^10.2.2` has exactly **one** requester in each app
  (`@expo/fingerprint`, itself pulled in only via `expo@~57.0.9`), so this
  is narrow, not a wide dependency-tree conflict.
- **Why this step matters not at all for OTA compatibility here, and so is
  safe to skip:** both apps pin a **literal string** `runtimeVersion`
  (`rider-app/app.config.ts` `'2.0.0'`, `driver-app/app.config.ts`
  `'2.5.0'`), not the `'fingerprint'` policy — the apps' own code comments
  say EAS CLI rejects a `fingerprint`/`appVersion` policy for bare workflow.
  The fingerprint `eas update` computes here is unused dead weight for this
  app's actual compatibility mechanism, not a load-bearing check being
  bypassed.
- **Fix applied:** `EAS_SKIP_AUTO_FINGERPRINT: "1"` added to the `env:` of
  both `rider` and `driver` jobs' "Publish OTA update" step in
  `.github/workflows/eas-build.yml` — the exact bypass `eas update`'s own
  error message names (`⏩ To skip this step, set the environment variable:
  EAS_SKIP_AUTO_FINGERPRINT=1`).
- **Durable fix — what actually shipped:** the earlier root-cause writeup
  said `minimatch@^10.2.2`'s `brace-expansion` requirement resolves wrong
  because of "a corrupted `yarn.lock` entry." Digging one level further
  (this repo's own history, `git log -S`) found the actual origin: commit
  `09cbc59` ("B24 — bump 7 vulnerable transitive JS packages," same day,
  merged *before* `d4b573c`) added 4 scoped `**/`-glob `resolutions` to
  `package.json` specifically to avoid forcing one `brace-expansion` major
  onto every consumer — the right instinct — but two of the four patterns
  (`**/@expo/fingerprint/**/brace-expansion`, `**/@typescript-eslint/
  typescript-estree/**/brace-expansion`) use a **mid-path `**` wildcard**
  that Yarn Classic's selective-resolutions silently doesn't support: the
  patterns register with zero effect (confirmed empirically — removing them
  changes nothing; `yarn install --force`, deleting the lockfile entries and
  reinstalling, and adding a corrected 3-segment exact path
  `@expo/fingerprint/minimatch/brace-expansion` all produced byte-identical
  results). That PR's own verification (jest/tsc/build/audit across 3 apps)
  never exercised `@expo/fingerprint`'s fingerprint computation, so the
  silent no-op shipped unnoticed. Meanwhile the *other* two patterns'
  sibling rule, `**/minimatch/brace-expansion: ^1.1.18` (single-level `**/`
  prefix — the one Yarn Classic *does* support), matches **any** package
  literally named `minimatch` at any depth, including the `minimatch@10.2.5`
  instances bundled by `@expo/fingerprint`, `@typescript-eslint/
  typescript-estree`, and `glob` — forcing all of them down to
  `brace-expansion@1.1.18` regardless of their own declared `^5.0.5` need.
  **The actual fix**: scope that one rule to the specific old-minimatch
  branch it was meant for — `"**/minimatch@^3.0.0/brace-expansion":
  "^1.1.18"` (adding a semver constraint on the `minimatch` path segment,
  which Yarn Classic *does* honor) — then delete the two no-op patterns
  entirely (dead, misleading, no longer needed). One line changed, two
  deleted, in each app's `package.json`; `yarn.lock` regenerated from that.
  Result: 3 correctly-separated, semver-valid `brace-expansion` groups
  (`1.1.18` for the `^3.x` minimatch branch, `2.1.4` for the `^9.x` branch —
  previously *also* wrongly forced to `1.1.18`, a second latent bug this
  fix incidentally closes — and `5.0.9` for the `^10.x` branch used by
  `@expo/fingerprint`, `typescript-estree`, and `glob`). `EAS_SKIP_AUTO_
  FINGERPRINT` removed from `eas-build.yml` — no longer needed.
- **Risk & impact:** this bug has blocked **every** `eas update` OTA
  publish on `main` since at least 2026-08-01 (`#605` onward, 15+
  consecutive failed runs across both apps by the time this was found) —
  meaning no JS-only fix, however small or urgent, has actually reached a
  phone on the `production` channel in that window without a full native
  rebuild. That is the actual severity of this repo's mobile-update
  pipeline right now, not just "8 red dashboard rows."
- **User-experience effect:** none directly from this fix (build-pipeline
  only) — but its *absence* means every rider/driver-facing JS fix shipped
  since 2026-08-01 has been silently stuck, undelivered to installed apps,
  until this unblocks it.
- **Verification performed (durable fix, both apps):**
  - `require('@expo/fingerprint').createFingerprintAsync(cwd)` — the exact
    library call `eas update` makes — now returns a real hash for both apps
    (`driver-app`: `7dbbc2f9e0470162...`, `rider-app`: `090313f67818aa4a...`)
    instead of throwing. Re-ran after the dead-pattern cleanup too, with an
    identical resulting hash both times — confirms the cleanup was a true
    no-op on top of the real fix, not a coincidental masking change.
  - Physical `node_modules` nesting inspected directly (not inferred): 5
    distinct `brace-expansion` install locations across the 3 correct
    version branches, each resolving from the right consumer
    (`minimatch/node_modules/brace-expansion@1.1.18`, top-level
    `brace-expansion@2.1.4` serving the `^9.x` minimatch branch,
    `glob/`, `@expo/fingerprint/`, and `@typescript-eslint/
    typescript-estree/`'s own nested `brace-expansion@5.0.9` copies).
  - `npx expo export --platform android` — exit 0, real Hermes bundle
    produced — for **both** apps, confirming the resolution fix doesn't
    regress ordinary bundling.
  - Full `jest` suite: **driver-app 364/364 passed (51/51 suites)**;
    **rider-app 455/455 passed (54/54 suites)** on a clean re-run — one
    test (`verifyEmailScreen.test.tsx`, a 5s-timeout mount assertion) flaked
    on a single full-suite run but passed twice in isolation and once more
    on a full-suite re-run, confirming it's pre-existing parallelism
    flakiness (worker-teardown timing under full-suite load), not a
    regression from this change.
  - `git diff` on both `yarn.lock` files: exactly 3 `brace-expansion` lock
    entries touched, nothing else — confirms the narrow blast radius
    predicted (only `minimatch@^10.2.2`'s single requester chain, plus the
    incidental `^9.x`-branch correction) held in practice.
  - `EAS_SKIP_AUTO_FINGERPRINT` removed from both jobs in
    `.github/workflows/eas-build.yml` — the workaround is gone, not just
    documented as removable.
- **What was NOT verified:** the actual `eas update` command was still
  **not** run against the real EAS service in this session — no
  `EXPO_TOKEN`/Expo auth available here. Confidence rests on calling the
  identical underlying library function `eas update` calls, against the
  real project directory, with a real result — about as close to the real
  path as is reachable without production credentials — but the next real
  push to `main` touching either app is the actual, final proof. Also not
  independently re-verified: `eslint`/`tsc` full runs in either app after
  this change (jest + expo export + the direct fingerprint call were judged
  sufficient coverage for a dependency-resolution-only change; no source
  file was touched).
- **Files:** `.github/workflows/eas-build.yml` (bypass removed);
  `rider-app/package.json`, `rider-app/yarn.lock`, `driver-app/package.json`,
  `driver-app/yarn.lock` (durable fix).

### C20. SDK 57 alignment follow-ups that are EAS-build-gated (from the 2026-08-11 dependency-alignment pass — see `docs/change-log/2026-08-11-sdk57-dependency-alignment.md`)
- [ ] **Status:** OPEN — the alignment branch (`claude/sdk-55-57-upgrade-jrz0iv`)
  brought both apps to SDK 57's expected dependency versions and verified with
  tsc + jest + production `expo export` in both apps, but this environment
  cannot run EAS/native builds. Each sub-item below needs one EAS build (or a
  release-build device test) to close; none should be batched blind with the
  others — one variable per build, per `docs/dependency-upgrade-runbook.md`.
- [ ] **First EAS Android + iOS build off the alignment branch** — proves the
  native side of RNGH 2.32 / safe-area 5.7 / netinfo 12 / datetimepicker 9 /
  rider picker removals (a green `expo export` is NOT a green `eas build` —
  C17/C19 lesson). Then a device smoke: gestures (bottom sheets, map pan,
  hold-to-confirm SOS), safe-area insets, offline banner, driver onboarding
  date picker, and confirm EAS Observe app-start metrics resume (the
  `ObserveRoot` fix in BOTH apps' `app/_layout.tsx` — rider's duplicated
  instance was caught in the 2026-08-12 pre-merge review).
- [ ] **Try removing `ios.buildReactNativeFromSource: true`** (both
  app.config.ts): the expo-dev-launcher header mismatch it worked around was
  fixed upstream in SDK 56. One EAS iOS build with it off; if green, also
  retire `plugins/withFirebaseNonModularHeaders.js` in the same test (it
  exists to support the source-build + static-frameworks combo). Large iOS
  build-time win.
- [ ] **Try removing `plugins/withForceCompileSdk.js` and
  `plugins/withKspVersion.js`** — their documented removal criteria ("Expo SDK
  ≥56 ships compileSdk 36 default"; "expo-updates ksp mapping fixed") are now
  testable on 57. One EAS Android build with them disabled. See the
  removal-criteria table in `docs/android-build-strategy.md`.
- [ ] **@stripe/stripe-react-native 0.63.0 → 0.64.0** (rider; SDK 57 expects
  0.64.0) — HELD on 2026-08-11 by explicit user decision (payments surface
  mid-live-testing + Option C Kotlin-toolchain entanglement). Bump criteria:
  check 0.64.0's Kotlin/Stripe-Android versions against Option C first, EAS
  Android + iOS builds, `spinr-money-auditor` review, manual payment smoke
  (add card, PaymentSheet, ride payment, tip, Google Pay). Until then
  `expo install --check` will keep flagging it — that residue is intentional.
- [ ] **Try re-enabling Metro `unstable_enablePackageExports`** (both
  metro.config.js): @sentry/react-native now resolves ≥7.11 which may fix the
  frozen-ESM-namespace crash — but that crash was RELEASE-BUILD-ONLY under
  Hermes, so this needs a release-build device test + the bundle-diff check
  documented in the metro comment. Not flippable on tsc/jest evidence.
- [ ] **expo-speech-recognition 57.x watch** (rider): no 57-line release
  exists (npm latest = 56.0.1, verified 2026-08-11). Works on 57 today;
  re-check each SDK cycle and bump when the community package catches up.
- [ ] **Mobile lint debt under the SDK 57 ruleset** (round 1: 2026-08-12,
  branches `claude/c20-mobile-lint-rider-app` / `claude/c20-mobile-lint-driver-app`,
  PRs #3777/#3778, merged; round 2 driver-app: 2026-08-12, branch
  `claude/c20-lint-tier2-driver-app` — see
  `docs/change-log/2026-08-12-c20-lint-tier2-driver-app.md`):
  **driver-app now fully closed on 3 of its 4 remaining react-hooks
  categories; rider-app round 2 status tracked separately by that session.**
  - **Round 1 fixed**: rider 504 → 262 problems (48% down, 337 errors →
    167), driver 423 → 153 problems (64% down, 181 errors → 104). Categories
    closed to zero: `no-unused-vars`/`@typescript-eslint/no-unused-vars`,
    `import/first`, `import/no-duplicates`, `no-undef` (missing jest/node
    globals for plain-`.js` mock/setup files — config fix in both
    `eslint.config.js`), `react/no-unescaped-entities`,
    `@typescript-eslint/array-type` (driver), `import/no-named-as-default`
    (either fixed via named import or a documented suppression where a
    named import would break a test's `jest.mock()` shape — see below),
    `@typescript-eslint/no-require-imports` in all production code (app/,
    components/, hooks/, services/, store/, utils/ — either converted to a
    static import or given a documented suppression for genuinely-guarded
    native-module requires), and the 4 total `react-hooks/refs` "Cannot
    update ref during render" (write) findings — the higher-risk half of
    that rule.
  - Round 1 **found and fixed one real bug from a self-inflicted regression,
    not a pre-existing one**: converting `SpinrConfig`'s default import to a
    named import (mechanically matching the safe `Analytics`/`CarMarker`
    fix) broke `rider-app/utils/__tests__/aiChat.test.ts`, because several
    test files across both apps `jest.mock('@shared/config/spinr.config',
    () => ({ default: {...} }))` without a matching named export. Reverted
    in both apps; the ~9 other `SpinrConfig` default-import sites were left
    alone with a documented `eslint-disable` instead of "fixed" the same
    wrong way. Also removed one ~130-line dead `renderRideOfferPanel`
    function in driver-app's dashboard (superseded by the extracted
    `<RideOfferPanel>` component) found via `no-unused-vars` investigation.
  - **Round 2 fixed (driver-app only, this pass)**: driver 153 → 63 problems
    (59% down further, 104 errors → 31). Real (non-scoped) `yarn lint`
    measured fresh at the start of this round: 127 problems (95 errors, 32
    warnings) — the 153/104 figure above had already drifted stale by the
    time this round started. Three categories closed to zero:
    - `react-hooks/immutability` (15 → 0) — all 15 were the same
      "`X` is accessed before it is declared" pattern: a `fetch*`/`load*`
      function referenced inside an earlier `useEffect` but declared later
      in source order. Fixed by moving each function's declaration above
      its first use — pure reorder, zero behavior change (an effect body
      always runs after the full render commits, once every const is
      initialized). One file (`driver/payout.tsx`) needed 3 more functions
      reordered too (`loadBonuses`/`loadTaxYears`/`loadStripeStatus`,
      called from within `loadData`) once the compiler's analysis could
      reach past the first fixed violation — not a new bug, a
      previously-masked instance of the same pattern.
    - `react-hooks/purity` (5 → 0) — all 5 were `Date.now()` called
      somewhere reachable from render. 2 were inside an async `pickImage`
      handler (invoked from an `Alert.alert` button, already deferred/
      post-await) but still flagged; fixed by extracting a module-level
      `genFallbackFileName()` helper (the compiler's check doesn't cross
      into a separately-declared module-level function). 1 was a genuine
      render-body call inside a `.map()` (driver profile's document-expiry
      badges) — hoisted to a `getNowMs()` module-level helper called once
      per render, so every row agrees on the same "now" instead of
      microsecond drift, but still fresh per render like the original. 1
      (`notifications.tsx`'s `formatTime`) took no component state, so it
      was moved out of the component entirely. 1
      (`CarMarker.tsx`'s `lastFixTsRef` init) needed the same module-level-
      helper trick — notably, React's own documented "lazy ref init" guard
      pattern (`if (ref.current === null) ref.current = Date.now()`) was
      tried first and is **still flagged** by this rule version; noting
      that in case it resurfaces.
    - `react-hooks/preserve-manual-memoization` (1 → 0) — a `useMemo` in
      `ride-detail.tsx` read the same property twice, once via `ride?.X` and
      once via `ride.X`; the compiler's dependency inference couldn't prove
      both accesses were the same narrow property and widened its inferred
      dependency to all of `ride`, which didn't match the manually specified
      `[ride?.X]` dep array, so it skipped optimizing the component. Fixed
      by reading the property into a local const once. Same memoized value,
      same deps, zero behavior change.
    - `react-hooks/refs` **read**-during-render: 55 → 2 (53 closed). All 53
      were the verified-safe "stable animation/gesture driver" pattern this
      round's task explicitly called out as suppression-eligible:
      `useRef(new Animated.Value(x)).current` / `useRef(new
      AnimatedRegion({...})).current` / `useRef(PanResponder.create({...})).current`
      across `BrandSplash.tsx` (21), `RideOfferPanel.tsx` (9),
      `ActiveRidePanel.tsx` (8, including the exact `panResponder.panHandlers`
      case named in the task), `otp.tsx` (6), `DriverIdlePanel.tsx` (5),
      `DriverTopBar.tsx` (3), `CarMarker.tsx` (1). Every one was verified
      before suppressing — grepped each file for `.current =` and confirmed
      zero reassignment after creation — before adding a narrow
      `eslint-disable-next-line react-hooks/refs` at each read site (not a
      blanket file/rule-level disable). **2 remaining, deliberately NOT
      fixed**: `app/driver/(tabs)/index.tsx:696` (a `lastDirectionsFetchRef.current
      = {...}` **write**) and `:701` (a `mapRef.current` read), both inside
      the same `<MapViewDirections onReady={...}>` callback, itself nested
      in an IIFE embedded directly in JSX
      (`{cond && (() => { ... return <React.Fragment>...</React.Fragment>; })()}`).
      This is a write-shaped finding under the same `react-hooks/refs` rule
      this round was told not to touch ("do NOT touch any write-during-render
      findings, those were already fixed in #3778 — if you see any, that's a
      discrepancy, flag it") — round 1's #3778 fixed exactly 2 write findings,
      both in `ActiveRidePanel.tsx`; this is a third, different write finding
      in a different file that round 1 never touched and this round was
      scoped not to touch either. Flagging for a future round: the write
      likely isn't a real render-time mutation (it's inside an async
      `onReady` completion callback, not the synchronous render pass) but
      that needs the same kind of verification the other 53 got, not an
      assumption.
  - **Deliberately deferred, not fixed** (documented reasons, not silent;
    numbers below are post-round-2 for both apps — `react-hooks/refs`
    read-during-render, `purity`, `immutability`, and
    `preserve-manual-memoization` are now closed for both apps, see the two
    Round 2 bullets below, and are no longer listed here):
    - `react-hooks/exhaustive-deps` (72 rider / 32 driver, unchanged by
      either round-2 pass — confirms no regression from the reordering work
      done for `immutability`).
    - `react-hooks/set-state-in-effect` (**32 → 40 rider**, **17 → 27
      driver**) — rose in both apps, in both cases **not from a new bug**:
      fixing the `react-hooks/immutability` findings (functions referenced
      before declaration) let the compiler's analysis reach *past* that
      violation into the same effect body for the first time, surfacing
      pre-existing (already-there, not introduced) `setState` calls that
      had been masked behind the co-located immutability bailout. No code
      behavior changed in either app — this is previously-hidden debt
      becoming visible, the same phenomenon driver's `payout.tsx` showed
      mid-fix (see that round's change-log). Still out of scope to fix;
      flagged so the next round starts from the real number, not a stale
      one. `react-hooks/exhaustive-deps`/`react-hooks/set-state-in-effect`
      are exactly the findings CLAUDE.md/this bullet's own prior wording
      warns can be real bugs, not style noise; blindly adding a missing dep
      or reordering a `set`-during-render call can change re-render timing
      or introduce a loop. Needs per-finding review with real behavioral
      tracing, not a bulk pass.
    - `no-restricted-syntax` (14 rider / 2 driver, unchanged by either
      round) — the project's own raw-`error.message`-surfacing rule. Fixing
      these routes user-visible text through `getApiErrorMessage(err,
      fallback)`, which changes what text a rider/driver actually sees on
      an error — a real UX change, not a style fix, and out of scope for a
      lint-cleanup PR.
  - **Round 2 (2026-08-12, rider-app only, branch
    `claude/c20-lint-tier2-rider-app`)**: closed 4 of the deferred rider-app
    categories above to zero — `react-hooks/preserve-manual-memoization`
    (2→0), `react-hooks/purity` (9→0), `react-hooks/immutability` (11→0),
    and `react-hooks/refs` **read**-during-render (98→0, confirmed via fresh
    `yarn lint` before starting — matches this bullet's numbers exactly, not
    stale this time). `react-hooks/exhaustive-deps`,
    `react-hooks/set-state-in-effect`, and `no-restricted-syntax` were
    explicitly out of scope for this round per the task and remain untouched
    for rider-app; driver-app was not touched at all (its counts above are
    still accurate/unmeasured-this-round).
    - `refs`: two patterns. (1) `confirm-pickup.tsx`'s `originalLat`/
      `originalLng` — plain numbers read once, never reassigned, but
      consumed directly in JSX — converted `useRef`→`useState` (lazy
      initializer). (2) ~15 sites of the `useRef(new Animated.Value(x)).current`
      RN animation idiom across 10 files — extracted a shared
      `hooks/useAnimatedValue.ts` (`useAnimatedValue`/`useAnimatedValues`/
      `useStableRef`) centralizing the one audited suppression in one place
      instead of ~90 scattered disables; every call site now gets a plain
      value back with no further `.current` for the linter to flag.
    - `purity`: real fixes for 2 genuine render-time impurities (a
      `Math.random()` fallback marker heading in `ride-options.tsx` that was
      re-randomizing on every re-render — replaced with a deterministic
      per-driver hash; a `useMemo`-wrapped `Date.now()` in
      `SchedulePicker.tsx` — moved to `useState`'s lazy initializer, the
      React-blessed one-time-impure-read spot) plus one `Date.now()`
      JSX-prop pair moved into an event handler. The other ~4 findings were
      false positives from the linter's static analysis not distinguishing
      event-handler closures from render body (verified via trace, then
      suppressed with justification — same standard as the `refs` carve-out).
    - `immutability`: **turned out to be entirely a different pattern than
      this bullet's own description assumed** — all 11 findings (10 rider +
      the Toast.tsx one folded into the `refs` fix) were "function accessed
      before declared" (a mount `useEffect` referencing a fetch/load
      function defined later in the same component — works at runtime,
      flagged because the compiler's static analysis can't prove it), not
      `array.push()`/`obj.field = x` mutation. Fixed by reordering the
      function above the effect in 9 files; `useRiderSocket.ts`'s `connect`
      finding is a genuine self-reference (exponential-backoff reconnect
      calling itself) that can't be reordered — narrow suppression instead,
      after confirming the existing `connectGenRef` generation guard already
      bounds the stale-closure risk the rule warns about.
    - **Side effect worth flagging**: the immutability reordering made 8
      previously linter-invisible `react-hooks/set-state-in-effect` findings
      visible in the same 8 files (rider count 32→40) — same pre-existing
      setState calls, not new behavior; the linter simply couldn't trace
      into a forward-referenced function before. Left untouched (out of
      scope this round); flagging so the 40 isn't mistaken for new debt.
    - Full Change Impact Log:
      `docs/change-log/2026-08-12-c20-lint-tier2-rider-app.md`.
  - **Round 2 (2026-08-12, driver-app only, branch
    `claude/c20-lint-tier2-driver-app`)**: closed 3 of the 4 deferred
    driver-app categories above to (near-)zero — `react-hooks/immutability`
    (15→0, same "function accessed before declared" reorder pattern as
    rider's), `react-hooks/purity` (5→0, `Date.now()` reachable from render
    hoisted to module-level helpers), `react-hooks/preserve-manual-memoization`
    (1→0, same double-access-path `useMemo` dependency-widening issue as
    rider's), and `react-hooks/refs` **read**-during-render (55→2 — see
    below for the 2 deliberately unfixed). `react-hooks/exhaustive-deps`,
    `react-hooks/set-state-in-effect`, and `no-restricted-syntax` were
    explicitly out of scope for this round per the task and remain
    untouched for driver-app.
    - `refs`: all 53 closed were the verified-safe "stable animation/gesture
      driver" pattern (`useRef(new Animated.Value(x)).current` / `useRef(new
      AnimatedRegion({...})).current` / `useRef(PanResponder.create({...})).current`,
      including the `panResponder.panHandlers` case in `ActiveRidePanel.tsx`)
      — each verified via grep for `.current =` reassignment before
      suppressing, narrow per-site `eslint-disable-next-line`, not a blanket
      disable. **2 remaining, deliberately NOT fixed**:
      `app/driver/(tabs)/index.tsx:696` (a ref **write**,
      `lastDirectionsFetchRef.current = {...}`) and `:701` (a ref **read**,
      `mapRef.current`), both inside a `<MapViewDirections onReady={...}>`
      callback nested in an IIFE embedded in JSX — this round was scoped to
      reads only, and this write is a *third*, different write finding from
      the 2 round 1 (#3778) already closed in `ActiveRidePanel.tsx`; flagged
      per this round's task instructions rather than silently re-fixed.
      Needs the same kind of verification the other 53 got before closing.
    - **Side effect worth flagging** (same phenomenon as rider's round 2):
      fixing 10 of the 15 immutability findings surfaced 10 previously
      linter-invisible `react-hooks/set-state-in-effect` findings in the
      same files (driver count 17→27) — same pre-existing setState calls,
      not new behavior. Left untouched (out of scope this round).
    - Full Change Impact Log:
      `docs/change-log/2026-08-12-c20-lint-tier2-driver-app.md`.
    - `@typescript-eslint/no-require-imports` remaining (23 rider / 11
      driver) — all in `__tests__/`/`e2e/` files, where a dynamic
      `require()` mid-test-body is the idiomatic way to grab a
      `jest.mock()`'d module's mock functions for
      `.mockImplementationOnce()` etc.; converting these to static imports
      risks changing when Jest's module registry resolves them relative to
      `jest.mock()` hoisting, not worth the risk for a cosmetic warning.
    - `import/no-unresolved` (1, rider `scripts/render-voltra-templates.mjs`)
      — the script's own header comment already documents
      `@use-voltra/ios-server` as a manual post-`yarn install` step, not a
      repo dependency; "fixing" it would mean adding an unlisted package,
      out of scope.
  - **Round 3 (2026-08-12, rider-app only, branch
    `claude/c20-lint-tier3-rider-app`)**: closed the two rider-app
    categories round 2 explicitly deferred — `no-restricted-syntax`
    (14→0) and `react-hooks/set-state-in-effect` (40→1, one left
    deliberately unfixed for human review). Re-measured fresh via `npx
    eslint . --format json` at the start of the session (not trusted from
    ACTION_ITEMS.md), confirming both numbers matched the task brief
    exactly. driver-app was not touched.
    - `no-restricted-syntax` (14→0): the project's own rule banning raw
      `error.message` surfaced to users, routing instead through
      `getApiErrorMessage(err, fallback)`. Of the 14, only **8 were
      actual raw-error-to-UI sites** and got the intended fix (a real,
      if small, UX text change — see the round's Change Impact Log for
      the full before/after per site): `chat-driver.tsx` (hand-rolled
      `.detail || .message || fallback` ladder replaced 1:1 —
      `getApiErrorMessage`'s `extractError` already covers the `.detail`
      case), `manage-cards.tsx` (a Stripe `createPaymentMethod` error's
      raw `.message`; `getApiErrorMessage`'s own fallback ladder already
      handles a plain-object error with only `.message`), and
      `verify-email.tsx` (4 findings, all inside one local
      `resolveErrorCopy()` helper — a hand-rolled duplicate of
      `getApiErrorMessage` that lacked its noise-filtering and would
      have shown a raw "Request failed with status code 500"-style
      string verbatim if that happened to be `err.message`; kept its
      unique i18n `messageKey`→`tKey` lookup, which
      `getApiErrorMessage` has no equivalent for, but now sources that
      lookup's fallback text from `getApiErrorMessage` instead of raw
      `err.message`). **The other 6 were not user-facing** and did NOT
      get the `getApiErrorMessage` treatment, per this round's own task
      instructions to not force-fit a fix where the finding isn't
      actually a UI-surfaced case: 4 were `console.warn(...,
      e?.message ?? e)` logging calls in `app/_layout.tsx` (×3) and
      `app/ride-options.tsx` (×1) — fixed by passing the whole error
      object instead, matching the rule's own guidance text ("for
      logging, pass the whole error object") and an existing compliant
      call site already in the same file; 2 were
      `error?.message?.includes('already active')` control-flow checks
      in `payment-confirm.tsx`/`ride-options.tsx` used only to route to
      an already-active ride on a 409, not to build display text (the
      real user-visible message a few lines below both already used
      `getApiErrorMessage`) — narrow `eslint-disable-next-line` with a
      comment explaining why, not a fix.
    - `react-hooks/set-state-in-effect` (40→1): reviewed every finding
      individually (no bulk pass), tracing each effect's dependency
      array against the state it sets to confirm no cascading-loop risk,
      per CLAUDE.md's explicit warning that this rule can hide real
      bugs. Categorization: **39 (a) benign** — the state each site sets
      is never in that same effect's own dep array, so none can
      retrigger itself; covers mount-only data loads (deps `[]` or a
      stable `useCallback`), guarded one-shot state-machine transitions
      (`BookingProposalCard.tsx`'s `phase` field — traced all 5
      `setPhase` call sites in the file to confirm the transition is
      one-directional), countdown/timer resets synced to an external
      value (including the rider-side ~15s dispatch-offer-countdown
      display in `ride-status.tsx`, reviewed with extra care given the
      dispatch adjacency — no ride-state-machine or backend interaction,
      pure display), prop/store-to-local-state sync, and the canonical
      "synchronize an external system" pattern in `useRiderSocket.ts`'s
      connect/disconnect lifecycle effect (the hook driving the live
      ride WebSocket, 4 consumers — reviewed carefully given the blast
      radius; comment-only change, no logic touched). **0 (b)
      refactorable** — none of the 40 was a safe candidate for a
      lazy-`useState`-initializer or derive-during-render rewrite
      without a real behavior question attached (the sync-from-prop
      cases are user-editable local state initialized from a fetched
      value, not pure derived values). **1 (c) suspicious, left
      unfixed** — `rider-app/app/work-profile.tsx:76-85`: a second
      effect duplicates the mount effect's own `loadAll()` fetch of
      balance + the same `/rider/work-profile/:id/rides` endpoint,
      so both run concurrently on first mount when `activeCompanyId` is
      already set, racing on which response wins. Not a render loop, but
      looks like unintentional duplicated logic rather than a deliberate
      "load once vs. reload on company switch" split. Given a `TODO(C20)`
      comment instead of a suppression (left flagged, not silenced) —
      **needs a human decision**, not guessed at.
    - Full Change Impact Log:
      `docs/change-log/2026-08-12-c20-lint-tier3-rider-app.md`.
  - Mobile lint is still not a CI gate (`ci.yml` lints only `frontend/` +
    `admin-dashboard/`), so none of this blocked anything — it's cleanup
    debt closed opportunistically, not a red pipeline fixed.
  - **Round 3 (2026-08-12, driver-app only, branch
    `claude/c20-lint-tier3-driver-app`)**: closed BOTH categories round 2
    explicitly deferred for driver-app — `react-hooks/set-state-in-effect`
    and `no-restricted-syntax` — to zero. Fresh `npx eslint . --format json`
    measured at the start of this round (not trusting round 2's own
    already-stale 27 figure): 30 `set-state-in-effect` findings (round 2's
    log said 27 — had already drifted by 3 in the time between rounds) and
    2 `no-restricted-syntax` findings, confirmed against the actual starting
    commit. End-of-round fresh measurement: both 0. Total driver-app error
    count 40→8 (only pre-existing `react-hooks/exhaustive-deps` and
    `react-hooks/refs` findings remain, both explicitly out of scope this
    round too).
    - `no-restricted-syntax` (2→0): **both findings were logger-only
      `console.warn`/`console.log` calls, never surfaced to the driver** —
      not the raw-error-to-UI case the rule targets. Per the rule's own
      message ("For logging, pass the whole error object"), fixed by
      passing the caught error object itself instead of `.message`,
      matching existing house style elsewhere in the app (e.g.
      `app/_layout.tsx`'s many `console.log('[X] ... failed:', e)` calls).
      `getApiErrorMessage` was NOT used at either site since neither is
      user-facing — flagging explicitly since the task anticipated a
      possible discrepancy from the "always route through
      getApiErrorMessage" assumption, and this is it.
      (`app/driver/(tabs)/profile.tsx:102`, `app/profile-setup.tsx:87`.)
    - `set-state-in-effect` (30→0): per-finding review, not a bulk pass —
      categorized every finding:
      - **(a) benign, safe pattern — 29 of 30**: narrow
        `eslint-disable-next-line` + one-line justification each. Two
        recurring shapes: (1) mount-only `fetch*`/`load*` calls in an
        empty-deps effect (the function sets state only after its own
        `await`, never synchronously at the top of the effect) —
        `become-driver.tsx` (×2), `documents.tsx`, `driver/addresses.tsx`,
        `driver/destination-mode.tsx`, `driver/emergency-contacts.tsx`
        (verified `fetchContacts` is `useCallback([])`, stable),
        `driver/faq.tsx`, `driver/referral.tsx`, `driver/tax-documents.tsx`,
        `driver/subscription.tsx`, `legal.tsx`,
        `driver/lost-and-found-chat.tsx` (verified `loadCase` is
        `useCallback([])`, stable), `driver/ride-detail.tsx`,
        `driver/payout.tsx`'s `loadData`, `hooks/useDriverDashboard.ts`'s
        `refreshLocation` at mount; (2) reset/re-seed-on-dependency-change,
        verified the setState never feeds back into its own dep array —
        `driver/(tabs)/index.tsx` (×3: heatmap-on-idle, offer countdown
        re-seed, route/ETA reset), `driver/payout.tsx`'s GST-field sync,
        `driver/settings.tsx` (×2: WAV toggle, notification-prefs sync),
        `vehicle-info.tsx`'s form seed, `app/index.tsx`'s attempt-counter
        reset, `app/otp.tsx`'s resend-timer flag, `CancelReasonSheet.tsx`'s
        form reset, `ActiveRidePanel.tsx` (×2: live-distance reset,
        wait-timer reset — local DISPLAY-only accumulators, not the
        server-computed fare distance), `useDriverDashboard.ts`'s
        WS-connection-state sync on going offline, and
        `useDriverDashboard.ts`'s one-time `isOnline` profile-hydration
        effect (the most scrutinized of the 30 — traced the
        `onlineHydratedRef` guard chain line by line: the ref is set
        `true` on the same pass, before the `setIsOnline` call, so every
        subsequent run short-circuits at the top even though `isOnline` is
        itself in the dep array; only ever sets the driver-toggled
        `isOnline` flag, never the system-computed `is_available`, so the
        CLAUDE.md invariant `is_available ⇒ is_online` is untouched).
      - **(b) refactorable, zero behavior change — 1 of 30**:
        `components/CarMarker.tsx`'s `imageFailed` reset
        (`useEffect(() => setImageFailed(false), [imageUri])`) rewritten
        as React's documented "adjust state during render" pattern (a
        `prevImageUri` comparison, reset inline) instead of a suppression —
        same reset semantics, and arguably a small correctness improvement
        since the effect version could flash the fallback image for one
        render after `imageUri` changed before its post-commit pass
        corrected it.
      - **(c) suspicious / needs a human decision — 0 of 30**. No finding
        in this batch had a dependency array that didn't guard against
        re-firing, an unbounded setState-into-its-own-dep loop, or a logic
        change too uncertain to call safe. None flagged for escalation.
    - Dispatch/payments-risk-posture files got individual dedicated
      commits and extra scrutiny per CLAUDE.md: `driver/(tabs)/index.tsx`
      (main dashboard), `driver/payout.tsx` (money), `ActiveRidePanel.tsx`
      (active-ride bottom sheet), `hooks/useDriverDashboard.ts` (WS +
      online-flag) — none of the 11 findings across these 4 files touched
      ride-state-machine transitions, dispatch-offer accept/decline logic,
      or fare/wallet writes; all are local UI-display state resets or
      one-way syncs from server data.
    - Verification: `yarn tsc --noEmit` clean before and after; full
      `yarn jest` — only the pre-existing, documented
      `__tests__/androidAutoDistribution.test.ts` flake failed (confirmed
      untouched: zero diff to that test file or `eas.json`); targeted
      re-runs of `__tests__/components/ActiveRidePanel.test.tsx` (11/11),
      `__tests__/components/RideOfferPanel.test.tsx` (17/17, unchanged
      file, run as an adjacent-surface dispatch-offer check), and
      `hooks/__tests__/useDriverDashboard.chat.test.ts` (8/8).
    - Full Change Impact Log:
      `docs/change-log/2026-08-12-c20-lint-tier3-driver-app.md`.
  - **Round 4 (2026-08-12, driver-app only, branch
    `claude/c20-lint-tier4-driver-app`)**: closed the last deferred
    driver-app category — `react-hooks/exhaustive-deps` — the category
    flagged from round 1 onward as the highest-risk one and deliberately
    saved for last, needing the smallest batches and most scrutiny of any
    C20 category (unlike the earlier, more mechanical categories, this
    one can hide a genuine stale-closure bug on one side and a genuine
    "adding the dep re-fires something unwanted" regression on the
    other). rider-app's exhaustive-deps round is out of scope here,
    handled in parallel by a sibling session in a separate worktree.
    - Fresh `npx eslint . --format json` at branch start: 38
      `react-hooks/exhaustive-deps` findings across 20 files (confirmed
      against the actual branch-start commit, matching the task brief
      exactly — not stale). End of round: **0**. Every one of the 38 got
      individual review (full effect/callback body read, referenced
      value's definition read, stability traced) — no bulk/batch pass.
      Total driver-app lint problems: 57 (8 errors, 49 warnings) → 19 (8
      errors, 11 warnings) — the unchanged 8 errors are all pre-existing
      `react-hooks/purity`/`refs`/`immutability` findings untouched by
      this round (see below); the remaining 11 warnings are pre-existing
      `@typescript-eslint/no-require-imports` in `__tests__/` files,
      also untouched.
    - Categorization of the 38 (per the task's a/b/c/d scheme):
      - **(a) safe to add directly — 21/38**: the missing value was
        already stable — a Zustand store action/setter (verified each
        one is defined once inside that store's `create()` call and never
        redefined by a later `set()`, since `set()` shallow-merges and
        actions are never part of any partial-update payload — e.g.
        `initializeAuth`/`initializeLocation`, `clearError`,
        `fetchPayoutHistory`, `loadNavApp`, `fetchEarnings`,
        `fetchActiveRide`, `hydrateDriverRideState`, `setChatMessages`,
        `loadLanguage`/`loadNavApp`/`loadAlertPrefs`), a `useRef` object
        itself (not its `.current`-extracted value — `mapRef`), a route
        param that's immutable for the mounted screen's lifetime
        (`phoneNumber`), `router` (already an established stable/safe dep
        pattern elsewhere in this codebase), or a pure derivation of an
        already-listed dep (`CHAT_STORAGE_KEY` from `rideId`). Two were
        cleanup rather than additions: extracting an inline
        `useDriverStore.getState().error` / `navigationRef.isReady()` /
        `driverMe?.is_wav` expression the linter flagged as
        unstatically-checkable into a plain variable first (same value,
        same re-run trigger, just satisfies the rule honestly), and
        removing a module-level `StyleSheet.create()` constant
        (`styles`) from a `useCallback`'s deps per the linter's own
        "outer scope values aren't valid dependencies" guidance.
      - **(b) real fix, stabilized safely — 7/38**: an unstable
        function/value was wrapped in `useCallback`/fixed at its root so
        it could be added without extra re-runs — `become-driver.tsx`'s
        `fetchVehicleTypes`/`saveDraft`, `vehicle-info.tsx`'s
        `fetchVehicleTypes`, `ride-detail.tsx`'s `loadRide` (all
        `useCallback`'d on their one genuinely-reactive input, already an
        existing dep), `profile.tsx`'s `useFocusEffect` closing over
        TanStack Query's `refetchDriverMe` with an empty dep array
        (permanently-stale-closure risk if that reference ever changed),
        `destination-mode.tsx`'s `fetchDestination`. The most significant
        of the 7: `hooks/useRideOfferSound.ts` returned a **fresh
        `{ play, stop }` object literal every render** even though
        `play`/`stop` themselves were already permanently stable
        (`useCallback` with stable deps) — fixed by memoizing the
        returned object (`useMemo(() => ({ play, stop }), [play, stop])`).
        This was the one finding in the set that was a genuine
        dispatch-adjacent risk: `useDriverDashboard.ts`'s FCM
        foreground-ride-offer-message effect could not safely add
        `offerSound` before this fix — doing so would have unsubscribed
        + re-subscribed `onForegroundMessage` on every render, risking a
        missed ride-offer FCM message in that gap. Fixed at the hook's
        root (single consumer, verified via grep) instead of masked with
        a suppression downstream.
      - **(c) intentional exclusion, narrow suppression — 10/38**: 5 were
        the "don't clobber an already-user-picked value" mount-only-fetch
        guard pattern (`become-driver.tsx`/`profile-setup.tsx`'s
        `serviceAreaId`, `payout.tsx`'s `loadData` and its 4
        Promise.all'd functions — verified zero reactive closures across
        all 4 before leaving as a suppression rather than a
        useCallback-cascade on a money screen). 5 were the stable
        `useRef(new Animated.Value(x)).current` animation-driver idiom
        (`otp.tsx`'s `dotAnims`, `DriverIdlePanel.tsx`'s `goAnim`,
        `DriverTopBar.tsx`'s `bannerHeight`, `RideOfferPanel.tsx`'s
        `slideAnim`+`progressAnim`, `useDriverDashboard.ts`'s
        `fadeAnim`+`slideUpAnim`) — **a genuinely new discovery this
        round**: naively adding one of these (`otp.tsx`'s `dotAnims`) was
        actually tested, and it traded one lint violation for a
        different, worse one — a dependency array is evaluated during
        render, so listing a ref-derived `.current` value there counts as
        a render-time ref read under `react-hooks/refs` (which flagged
        "Cannot access refs during render" at that exact line once
        added). Every other instance of this idiom in the finding set was
        left excluded for the same confirmed reason rather than
        individually re-tested. One more: `profile-setup.tsx`'s
        profile-already-complete-check effect deliberately excludes
        `token`/`user` — that effect calls
        `useAuthStore.setState({ user: fresh })` inside its own body, so
        adding `user` as a dep would create a real (if likely benign)
        feedback-loop risk this investigation could not fully verify
        `router.replace()`'s idempotency against; excluded rather than
        guessed into the deps array.
      - **(d) needs a human decision — 0/38**: none. Every finding
        resolved to (a), (b), or (c) with a traceable, documented reason;
        none was left unfixed as ambiguous.
    - Dispatch/insurance-period/earnings-adjacent files got dedicated,
      isolated commits per CLAUDE.md's risk posture, each with the
      specific behavioral trace in its own commit message:
      `driver/(tabs)/index.tsx` (dashboard), `driver/payout.tsx` (money),
      `app/index.tsx` + `profile-setup.tsx` (auth routing),
      `RideOfferPanel.tsx` (ride-offer countdown), `ActiveRidePanel.tsx`
      (active-ride bottom sheet), and `hooks/useDriverDashboard.ts` (WS +
      dispatch-offer ingestion + online-flag), the last one split into 2
      commits so the one real behavioral fix (`useRideOfferSound.ts`) is
      independently revertable from the 5 mechanical additions. None of
      the 38 findings touched a ride-state-machine transition, a
      dispatch-offer accept/decline call, an insurance-period write, or a
      wallet/fare write path directly — every fix is local UI-effect
      wiring, a store-action dependency addition, or (for `offerSound`)
      an object-identity stabilization one layer removed from the actual
      WS/FCM message-handling logic, which is unchanged.
    - **Discrepancy flagged, not silently absorbed**: `hooks/useDriverDashboard.ts`
      still carries 6 pre-existing errors (1 `react-hooks/purity`, 4
      `react-hooks/refs`, 1 `react-hooks/immutability`) and
      `driver/(tabs)/index.tsx` carries the 2 already-documented
      `react-hooks/refs` findings from round 2 — all 8 confirmed present
      in the branch-start commit (unrelated to this round's changes) via
      diff against `origin/main`. ACTION_ITEMS.md's round-2/3 bullets
      describe `purity`/`immutability`/read-during-render `refs` as
      closed to (near-)zero for driver-app; this file's residual 6 is a
      discrepancy worth a human look, not something this round's
      exhaustive-deps-only scope covers fixing.
    - Verification: `yarn tsc --noEmit` clean before and after every
      commit; full `yarn jest` at the end — **379/379 passing, 53/53
      suites** (the previously-known `androidAutoDistribution.test.ts`
      flake is confirmed fixed at its root per this round's task brief —
      no failures of any kind, not excused as a known flake). Targeted
      re-runs after touching risk-posture files:
      `__tests__/components/ActiveRidePanel.test.tsx` (11/11),
      `__tests__/components/RideOfferPanel.test.tsx` (17/17, twice — once
      after the ref-suppression commit, once after the `offerSound`
      root-cause fix), `hooks/__tests__/useDriverDashboard.chat.test.ts`
      (8/8, twice, same reason).
    - Full Change Impact Log:
      `docs/change-log/2026-08-12-c20-lint-tier4-driver-app.md`.
  - **Round 4 (2026-08-12, rider-app only, branch
    `claude/c20-lint-tier4-rider-app`)**: closed `react-hooks/exhaustive-deps`
    — the category flagged from round 1 onward as the highest-risk of all
    C20 categories, saved for last, one finding at a time, no bulk fixes.
    Fresh `npx eslint . --format json` measured at the start of this round
    against the actual starting commit: **73 findings**, matching the task
    brief exactly (no drift this time). End of round: **1 remaining**
    (deliberately left, see below) — **73 → 1**. driver-app was not
    touched (sibling session's territory, separate worktree).
    - Every finding was read in full context (effect/callback body + the
      referenced value's own definition) and traced for stability before
      any fix — no bulk/batch guessing, per this round's task. Categorized
      a/b/c/d:
      - **(a) safe to add directly — 33 of 73**: the missing value was
        already stable as-is (a zustand store action/selector, `router`
        from `useRouter()` — confirmed by reading
        `node_modules/expo-router/build/hooks/useRouter.js`: it returns a
        module-level singleton, not a fresh object per call — a `useRef`
        value, a `useAnimatedValue`-created `Animated.Value`, or a
        primitive that's a pure derivation of an already-tracked dep) —
        added with no wrapping and no behavior-preserving reasoning
        required beyond confirming the stability itself.
      - **(b) real stale-closure bug or genuine gap, fixed safely — 37 of
        73**: the largest bucket. Sub-patterns, each verified individually:
        - Wrapped a plain (non-memoized) function in `useCallback` with its
          own correct deps before adding it — `handleFetchEstimates`
          (ride-options.tsx), `fetchLegalText` (legal.tsx), `loadData`/
          `loadRides` (saved-places.tsx/scheduled-rides.tsx), `fetchRide`
          (ride-details.tsx), `fetchTrackingUrl` (ride-tracking-webview.tsx),
          `hide` (Toast.tsx), `handleCancelPress`/`performCancel`/
          `handleCancel` (driver-arrived.tsx/driver-arriving.tsx/
          ride-status.tsx).
        - Rewrote a whole-object truthy check (`if (!currentRide)`,
          `const driver = currentRide` alias, `(currentRide as
          any)?.field` accessed non-optionally) into a narrowed
          optional-chained read of just the field(s) actually used, so the
          effect's deps could stay specific instead of needing the whole
          object — which updates on every ride poll/WS field change and
          would have re-fired the effect far more often than intended
          (map re-fits, animation restarts, notification re-posts).
          Applied in driver-arrived.tsx, driver-arriving.tsx (×3),
          ride-in-progress.tsx, ride-status.tsx (×2), useRideStatusNotification.ts
          (required changing `buildContent()`'s signature from whole
          ride/driver objects to flat scalar params — this hook drives the
          Android live-ride notification, so re-posting it on every poll
          would have been visible icon churn, not just a wasted render).
        - Closed real, if narrow, staleness gaps by adding a genuinely
          reactive value that a guard already made safe to add: `riderBill`/
          `cancellationFee`/`fare` into the back-button cancel-dialog
          effects (driver-arrived.tsx, driver-arriving.tsx,
          ride-in-progress.tsx, ride-status.tsx) — the dialog's fee/fare
          text now stays current if it updates without a status change
          also happening, which it previously wouldn't have; `selectedIndex`
          into ride-options.tsx's promo-fetch effect — promo eligibility
          now recomputes when the rider switches vehicle type, not only
          when a new estimates array arrives; `stops`/`mapBottomInset` into
          ride-options.tsx's map-fit effect — adding/removing a stop now
          refits the map instead of waiting for an unrelated dep to change;
          `activeCompanyId` into payment-confirm.tsx's and
          ride-options.tsx's corporate-toggle-sync effects.
        - Used a **latest-value ref** (read `.current` inside the effect
          instead of depending on the reactive value directly) where the
          value genuinely must NOT be a re-trigger, verified against a
          concrete regression each time: `ride-completed.tsx`'s
          `handleSubmitRef` (pre-existing pattern, extended to a second
          call site — avoids putting a payment-charging function with ~10
          reactive closures into a dep array) and **two new refs added in
          search-destination.tsx** (`pickupValueRef`/`dropoffValueRef`) —
          traced `handleTextChange` in that file and confirmed
          `pickup`/`dropoff` get set to `null` in the store the instant the
          rider types text diverging from the stored address; a plain dep
          on either would have made the corresponding effect fire
          mid-keystroke and silently overwrite what the rider just typed
          (GPS-sets-pickup effect for `pickup`; map-pick-return effect for
          `dropoff`) — a real regression, not a hypothetical, caught by
          reading the same file's own text-input handler before fixing.
        - `driver-arriving.tsx`'s `driverOriginSnapshot`: traced the
          existing null-guard and confirmed adding it as a dep causes at
          most one extra no-op invocation (guard fails on the re-run), not
          a loop — added rather than suppressed.
        - `search-destination.tsx`'s GPS-catch-up mount effect: added
          `userLocation` after confirming (grep across the store) that
          nothing in this codebase ever resets it back to `null` once set,
          so it re-fires at most once in practice; documented the
          understood cost (one extra idempotent `fetchSavedAddresses`/
          `loadRecentSearches` call) rather than treating that as risk-free.
      - **(c) intentional exclusion, narrow suppression — 2 of 73** (plus
        one hybrid finding that named two deps, one added as (b) and one
        suppressed as (c) — `FreeCancelTimer.tsx`'s `secondsLeft`, see
        below): `pick-on-map.tsx`'s mount effect (re-adding
        `hasApprox`/`approxLat`/`approxLng` would re-request the device
        location permission — a user-visible OS prompt — and re-fetch
        GPS/re-geocode on every spurious re-render); `search-destination.tsx`'s
        stops-sync effect (the whole `stops` array changes reference on
        every keystroke-triggered `updateStop()` call in the same file's
        `handleTextChange`, confirmed by reading it — depending on the
        whole array, not just `.length`, would overwrite a stop's
        in-progress edit); `FreeCancelTimer.tsx`'s `secondsLeft` (this
        effect intentionally owns ONE persistent `setInterval` that ticks
        `secondsLeft` down every second — adding it as a dep would tear
        down and recreate that interval on every tick).
      - **(d) suspicious / needs a human decision — 1 of 73, left
        UNFIXED (not suppressed — still shows in `yarn lint`)**:
        `rider-app/app/work-profile.tsx:90` (`loadAll` missing from the
        mount-only `useEffect(() => { loadAll(); }, [])`). `loadAll` is a
        `useCallback` keyed on `[activeCompanyId, fetchProfiles,
        fetchBalance]`, so adding it would make this "mount-only" effect
        ALSO re-run on every company switch — stacking a third fetch of
        `/rider/work-profile/:id/rides` on top of the two that round 3
        already found racing on the same endpoint in the effect right
        below this one (a TODO(C20) at lines ~79-91, left unfixed then for
        the same reason: an unresolved design question about whether these
        two effects should even both exist independently). Deciding this
        finding means guessing at that same unresolved intent — added a
        second `TODO(C20)` explaining this specifically rather than
        guessing either direction.
    - **A real regression was caught and fixed by this round's own
      verification step, not shipped**: adding `router` to
      `useRiderSocket.ts`'s `handleMessage` (a (a)-category fix — `router`
      is stable in the real app) broke
      `hooks/__tests__/useRiderSocket.reconnect.test.ts` (3 WebSocket
      instances opened where 1 was expected) because both of that hook's
      test files mocked `expo-router`'s `useRouter()` to return a **fresh**
      `{ push: jest.fn(), replace: jest.fn() }` object on every call —
      unlike the real module (confirmed via its source), which returns a
      stable singleton. The unstable mock made `handleMessage` → `connect`
      → the connect/disconnect lifecycle effect all recreate every render,
      opening extra sockets. Fixed by correcting both mocks to construct
      the router object once inside the factory closure (matching real
      `expo-router` behavior) — not by reverting the production fix. Full
      before/after: 18/19 → 19/19 passing on this hook's test files.
    - Extra scrutiny per the round's task, on top of the standard
      per-finding review: `useRiderSocket.ts` (WebSocket connect/reconnect
      — round 3 already reviewed a different rule, `set-state-in-effect`,
      on this same file), `ride-status.tsx`/`ride-tracking-webview.tsx`/
      `ride-options.tsx`/`driver-arriving.tsx` (ride-state-adjacent),
      `payment-confirm.tsx`/`ride-completed.tsx` (payment-adjacent, plus
      `driver-arrived.tsx`/`driver-arriving.tsx`/`ride-in-progress.tsx`/
      `ride-status.tsx`'s cancellation-fee dialogs), and every
      `setInterval`/`setTimeout`-containing effect touched (all traced for
      loop risk individually, none found).
    - Verification: `npx eslint . --format json` fresh at both start and
      end (start matched the task brief's 73 exactly). `npx tsc --noEmit`
      clean before and after. Full `npx jest --ci`: **468/468 passing,
      56/56 suites** (matches the stated baseline exactly — the previously
      tracked `androidAutoDistribution.test.ts` flake did not appear,
      confirming C20's own round-3 rider-app note that this flake is
      driver-app-specific). One unrelated, pre-existing, untouched test
      (`__tests__/privacySettingsToggles.test.tsx`) prints a benign
      post-teardown `ReferenceError: ... Jest environment has been torn
      down` after its own suite already reported passing — a leaked
      timer/async handle in that file, not a reported failure and not
      touched by this round (flagging since it's unusual output, not
      because it affected the pass count). Every `useCallback`/`useMemo`
      wrap added was grepped for other call sites in its file before
      committing to confirm no other effect/memo depends on that
      function's prior (non-memoized) identity.
    - 17 commits, each ≤3 files (smaller than prior rounds' batches per
      this round's explicit instruction, given the risk profile).
    - Full Change Impact Log:
      `docs/change-log/2026-08-12-c20-lint-tier4-rider-app.md`.
- [ ] **LogRocket major-version split**: rider `@logrocket/react-native`
  ^2.3.1 vs driver ^3.7.0 — two different native binaries of the same vendor
  SDK across the fleet, both still gated OFF on Android (hidden-API hang, see
  `docs/android-build-strategy.md` runtime section). Converge on one major
  (likely 3.x) next time either app cuts a binary, and re-test the Android 16
  hang before any re-enable.

### C21. Two PRs merged via GitHub's native auto-merge before/without their full check set completing — `main`'s required-status-checks list looks stale relative to the ~57 checks that actually run

- [ ] **Status:** open — found 2026-08-12, watching PRs #3719 and #3728 (the
  notification-throttling feature) end to end.
- **How found:** both PRs were opened as drafts by the same account
  (`ittalenthireca-sketch`), watched live via `subscribe_pr_activity`, then
  taken out of draft and merged — both actions by that same account, not by
  a human reviewer, and not by anything I (this session) triggered.
  - **#3719:** merged at the instant it left draft, while `CI/CD Pipeline`,
    `CI Guard Rails`, and `Security Gates` were all still `queued`/
    `in_progress` — none had reported a result yet. One check
    (`.github/workflows/maestro-e2e.yml`) had already **failed** before the
    merge. Only `Migration Safety Check` had actually completed (success).
  - **#3728:** merged with all 57 check runs accounted for, but 2 of them
    (`G4b · yarn audit (JS deps)` for rider-app and driver-app) were
    `failure` at merge time — pre-existing, unrelated to that PR's
    backend-only diff, but still red on the PR at the moment of merge.
- **Ruled out:** `.github/workflows/dependabot-auto-merge.yml` — the only
  auto-merge logic anywhere in this repo's workflows — is hard-gated
  `if: github.actor == 'dependabot[bot]'`. Neither PR was authored by that
  actor, so this workflow could not have fired either merge. Grepped all of
  `.github/workflows/` for any other `gh pr merge`/auto-merge invocation —
  none exists. This points to **GitHub's native per-PR auto-merge toggle**
  (`gh pr merge --auto`), which only waits for whatever branch protection
  lists as *required* — not for every check that happens to run.
- **Why it matters — same root-cause shape as three already-tracked items,
  now demonstrated concretely on two real merges rather than described
  abstractly for one check:**
  - **C13** (workflows sometimes never firing at all on a PR) already
    documents the same "gate isn't actually gating" theme for a different
    mechanism (webhook/Actions-policy). This item is the complementary
    case: checks *do* run, but merge doesn't wait for the ones that matter.
  - **The unnamed mobile-bundle-smoke item** (P1 section, "Follow-up not yet
    done: this check should be added to the repo's required-status-checks
    branch-protection list for `main`... needs a repo admin") already
    called out the exact mechanism suspected here, for one specific check.
    This item generalizes it: with 57 check runs now firing per PR, it's
    very likely only a small, possibly stale subset is actually in the
    required list.
  - **E8** (CODEOWNERS) separately confirms there is currently no
    functioning human-review gate either — placeholder team handles GitHub
    can't resolve, and "Require review from Code Owners" was never enabled
    in branch protection (same "needs repo-admin access no session has had"
    blocker). Combined with this item: a PR can go from open to merged on
    `main` with **zero** human involvement and **zero** guarantee that
    Security Gates / CI Guard Rails / the full test suite actually finished
    green — which directly contradicts root `CLAUDE.md`'s "Pre-merge
    release gates (mandatory while live app testing is active)" section.
  - In both observed cases the actual diffs were fine (subtask 1-2 config
    additive/inert; subtask 3-5's one real CI failure was caught, fixed, and
    confirmed green in a follow-up commit before the second merge) — this is
    a **process** near-miss, not a report of bad code reaching `main`. The
    risk is the *next* PR that merges this way without a fix already pushed
    in time.
- **Not resolvable from an engineering session** — no repo-admin access to
  read or change **Settings → Branches → main → required status checks**
  (to compare the required list against the ~57 checks that actually run
  and identify which ones — likely `CI Guard Rails`, `Security Gates` as a
  whole, `maestro-e2e.yml`, `G4b/G4c` audits — are missing from it), or
  **Settings → General → Allow auto-merge** (to confirm it's enabled
  repo-wide, which the Dependabot workflow's own header comment already
  states is a prerequisite for *that* workflow, but which also enables the
  native per-PR toggle observed here for non-Dependabot actors).
- **Acceptance:** a repo admin (1) reviews `main`'s required-status-checks
  list against the full set of checks that actually run and adds the ones
  that should block merge (at minimum: `CI Guard Rails`, `Security Gates`
  summary, `backend-test`/`admin-test`/`rider-app-test`/`driver-app-test`,
  `maestro-e2e.yml` once B25 gives it real coverage) — cross-reference the
  mobile-bundle-smoke item's identical ask so both get fixed in one settings
  pass, not two; (2) decides whether native per-PR auto-merge should stay
  available to non-Dependabot actors at all, given there is currently no
  functioning code-review gate (E8) to catch anything the required-checks
  list itself misses; (3) once real GitHub team handles exist, files them
  into `.github/CODEOWNERS` and enables "Require review from Code Owners"
  per E8's existing acceptance criteria — the two fixes together are what
  actually closes this gap, not required-checks alone.
- **Files:** none — this is a GitHub repo-settings issue, not a workflow
  YAML or application-code defect. No `.github/workflows/*.yml` change is
  implicated.

### C22. `scripts/migrate.py`'s tracking table doesn't match what's actually live on the production Supabase project — the runner may never have successfully recorded a migration against it — PARTIALLY RESOLVED (2026-08-17)

- [ ] **Status:** partially resolved (2026-08-17). `scripts/migrate.py` no
  longer exists — deleted by A39, which reconciled `run_migrations.py` to
  the correct (migration 24) `schema_migrations` shape and ported
  `migrate.py`'s one useful piece (CONCURRENTLY-safe splitting) first. That
  closes this item's original acceptance (1) and (2): the shape mismatch
  and the broken runner code are both gone. **Not closed** — acceptance
  (3), the actual live audit, is now more precisely quantified than before
  but still not complete:
  - **Tracking-table coverage, verified live** (Supabase MCP,
    `soavhtdhefowwvforzwb`): `schema_migrations` records 161 of 407 repo
    migration files. The table itself was only bootstrapped 2026-08-14
    (160 `backfill-verified` rows + 1 manual apply) and the bootstrap batch
    stopped around migration `239` — everything numerically after that
    (108 files) plus ~138 pre-window files with non-strictly-numeric
    naming were never recorded. **This is a mix of real gaps, not one
    cause** — spot-checks found migrations 286 and 297 genuinely live
    despite being untracked (bookkeeping-only gap), but migration 321
    (A38's regulatory fix, merged and marked CLOSED) had **never actually
    been applied** to production until this session (a real application
    gap, not just a tracking gap). **Applied 2026-08-17** (with explicit
    user confirmation via `AskUserQuestion`) — `purge_pii_retention()` now
    has A38's driver-ride guard live.
  - **A much more serious bug found while verifying 321's apply**: running
    `purge_pii_retention(true)` immediately after confirmed the function
    could not execute past **Step D** — `ride_messages.created_at` doesn't
    exist (the table has `timestamp`, per migration 98). Fixing that
    (migration 323) surfaced the identical bug one step later at **Step
    F** — `stripe_events.created_at` doesn't exist either (the table has
    `received_at`, per migration 22). Same bug class migration 187 already
    fixed once for `driver_location_history` (Step C) — a table
    pre-existing under a different column name than a later
    `CREATE TABLE IF NOT EXISTS` assumed, invisible because Postgres
    doesn't validate `plpgsql` column references until execution. **This
    means the entire daily PII/data-retention background loop
    (`utils/retention_purge.py`, ~03:00 UTC) has likely never completed a
    single successful run** — GPS anonymization at 3y, ride/DSAR
    hard-delete at 7y, and every step from D onward were silently failing
    every tick. Fixed 2026-08-17 (migrations 323, 324, both applied live
    with explicit user confirmation, both reviewed by
    `spinr-migration-reviewer` — verdict SAFE TO APPLY). A live
    column-existence sweep against every other table/column the function
    references found no further broken references — D and F were the
    last two. `purge_pii_retention(true)` now completes end-to-end and
    surfaced a real backlog (189,208 stale `surge_pricing` rows, 51
    expired `refresh_tokens`) — **no live purge was executed**, that's a
    separate decision left to the daily loop's next natural tick. Full
    detail: `docs/change-log/2026-08-17-c22-purge-pii-retention-broken-and-fixed.md`.
    **Note for future migrations touching `purge_pii_retention()`**:
    migrations 323/324 merged (PR #4116) without the
    `-- migration-override-ok: <reason>` marker that
    `ci-guardrails.yml`'s "redefines the same Postgres object" check
    requires (321 correctly has it; 323/324 don't) — the PR's Migration
    Safety Check failed on that and was merged over it anyway. Not fixed
    retroactively: the check is `pull_request`-only (no `push` trigger on
    `main`), so there's no standing red gate today, and editing an
    already-merged migration's content — even a comment-only marker —
    would violate this repo's explicit append-only convention for a
    cosmetic fix with zero functional effect. The **next** migration that
    redefines `purge_pii_retention()` needs its own
    `migration-override-ok` marker (as every one before 323/324 correctly
    had) — don't forget it.
  - **Still open**: the broader `schema_migrations` reconciliation (161/407
    tracked) itself — this session only individually verified and applied
    321/323/324, exactly the narrow, high-confidence action the original
    finding called for ("manually audit at least the highest-risk ones...
    don't blind-apply ~280 migrations' worth of accumulated drift in one
    shot"). The full reconciliation remains a substantial, separate,
    higher-stakes audit, same as originally scoped. Also still unverified:
    whether `110_settings_resend_email.sql` (the original 2026-08-13
    finding that started this item) has since landed — not re-checked in
    this session's narrower pass. Also flagged as a natural follow-up:
    whether any other `SECURITY DEFINER` function or background loop in
    this codebase has the same "table pre-existed under a different column
    name" bug class — not investigated beyond `purge_pii_retention()`.
- [ ] **Original status (2026-08-13, superseded above but kept for
  history):** open — found while investigating why the
  corporate-portal OTP email send has been failing since it shipped (see
  `docs/change-log/2026-08-13-corporate-otp-error-detail-and-ses-investigation.md`).
- **What's wrong:** two migration files define incompatible schemas for the
  same table name:
  - `backend/migrations/00_schema_migrations_table.sql` — `version TEXT
    PRIMARY KEY, applied_at`
  - `backend/migrations/24_schema_migrations.sql` — `filename TEXT PRIMARY
    KEY, checksum TEXT NOT NULL, applied_at, applied_by`, plus `ENABLE ROW
    LEVEL SECURITY`

  Both use `CREATE TABLE IF NOT EXISTS`, so whichever ran first on a given
  database wins the shape. **Verified live** (Supabase project
  `soavhtdhefowwvforzwb`, `information_schema.columns` for
  `public.schema_migrations`): the actual columns are `filename`,
  `checksum`, `applied_at`, `applied_by` — migration `24`'s shape, **not**
  `00`'s. `backend/scripts/migrate.py`'s `get_applied_versions()` (`SELECT
  version FROM schema_migrations`) and `apply_migration()` (`INSERT INTO
  schema_migrations (version) VALUES (%s) ...`) both hard-require a
  `version` column that does not exist on this project.
- **Why it matters:** `get_applied_versions()` catches its own query
  failure and falls back to treating nothing as applied (logged as a
  warning, not fatal) — so the runner doesn't crash outright, but it also
  cannot correctly determine what's already been applied, and its own
  bookkeeping `INSERT` (uncaught) would fail the first time it tries to
  record anything. This is very likely why `110_settings_resend_email.sql`
  (adds `resend_api_key`/`resend_from_email` to `settings`) never actually
  applied to this project — those columns are confirmed **missing** from
  the live `settings` table (verified via direct query), despite the
  migration file existing in-repo since well before this session. Other
  migrations between `24_` and the present may be similarly missing; not
  audited beyond `110` and the one new migration in this session
  (`307_email_send_log_error_detail.sql`, which was applied by hand via
  direct SQL specifically because `migrate.py` cannot be trusted against
  this project right now).
- **Not fixed in this session** — reconciling which of ~300 migration
  files have actually landed on production vs. only exist in-repo is a
  substantial, higher-stakes audit (each gap is a potential runtime bug
  waiting to be hit, like this one), not a quick fix, and touching the
  `schema_migrations` table itself on a live project is exactly the kind
  of action that needs a deliberate plan, not a same-session patch.
- **Acceptance:** (1) decide which shape `schema_migrations` should
  canonically have (migration `24`'s is richer — checksum + RLS — and is
  what's actually live, so likely make `migrate.py` match `24`, not the
  reverse); (2) fix `get_applied_versions()`/`apply_migration()` in
  `backend/scripts/migrate.py` to use the real column names; (3) once the
  runner can talk to the table, do a dry-run (`--dry-run`) against
  production to get the full list of migrations it believes are
  unapplied, and manually audit at least the highest-risk ones (anything
  touching money/auth/RLS) before actually applying — don't blind-apply
  ~280 migrations' worth of accumulated drift in one shot.
- **Files:** `backend/scripts/migrate.py`, `backend/migrations/00_schema_migrations_table.sql`, `backend/migrations/24_schema_migrations.sql`.

### C13. `tsc --noEmit` false-positives across all three frontend surfaces (pre-existing, not caused by any recent PR)
- [ ] **Status:** open — found 2026-08-03 while verifying PR #3382 (full-suite
  pass at that time: backend pytest 8742 passed/0 failed, rider-app jest
  434/434, driver-app jest 337/337, admin-dashboard vitest 157/157 — all
  green). Running a bare `npx tsc --noEmit` per surface afterward surfaced
  errors in all three, none in any file that PR (or any recent PR) touched —
  confirmed by diffing each error's file path against the PR's
  changed-files list.
- **What's wrong, two distinct causes:**
  1. **rider-app (7 errors) + driver-app (4 errors):** every error is
     `Cannot find module 'expo-router/react-navigation' or its corresponding
     type declarations'`, e.g. `app/(tabs)/account.tsx`,
     `hooks/useBottomSheetGuard.ts` (rider-app),
     `components/activity/ActivityView.tsx` (driver-app). The same subpath
     also fails to resolve under Jest (`Cannot find module
     'expo-router/react-navigation'`) in exactly one test suite per app —
     `hooks/__tests__/useBottomSheetGuard.test.tsx` (rider-app),
     `__tests__/components/ActivityView.test.tsx` (driver-app) — both
     already-known pre-existing failures, not new. The module exists and is
     imported successfully at runtime (Expo's Metro bundler resolves it
     fine); it's specifically `tsc`'s and Jest's module resolution that
     can't find its type declarations/CommonJS shape in this environment.
  2. **admin-dashboard (26 errors), all confined to `*.test.ts(x)` files:**
     missing `@testing-library/jest-dom` matcher types (`toBeInTheDocument`,
     `toBeDisabled` — `driver-statements-panel.test.tsx`) and missing Vitest
     global types (`describe`/`it`/`expect` — `route-segments.test.ts`).
     `admin-dashboard`'s `tsconfig.json` doesn't register Vitest's ambient
     types for a standalone `tsc` run; `vitest run` itself passed 157/157
     clean because Vitest's own transform/type-layer already has these
     globals, so this is a `tsc`-only artifact, not a real build or runtime
     issue.
- **Why it matters:** neither of these fails today's actual gates (`yarn
  jest --ci`, `vitest run`, and whatever CI runs are what's authoritative),
  but a bare `tsc --noEmit` is a normal thing to run when verifying a PR
  (as this session was asked to do) or to add as a CI check later — right
  now it produces guaranteed false-positive noise on every run regardless of
  what changed, which trains reviewers to ignore `tsc` output entirely and
  would mask a real new type error introduced in the same file.
- **Fix, per cause:**
  1. Track down why `expo-router/react-navigation`'s type declarations
     aren't resolving — likely a missing/stale `@types` entry, an
     `expo-router` version whose subpath exports changed, or a
     `moduleResolution`/`paths` config gap in `rider-app/tsconfig.json` and
     `driver-app/tsconfig.json`. Fix should make both `tsc --noEmit` and the
     one Jest suite per app (`useBottomSheetGuard.test.tsx`,
     `ActivityView.test.tsx`) pass.
  2. Add `vitest/globals` (or explicit `import { describe, it, expect } from
     'vitest'`) and `@testing-library/jest-dom`'s type augmentation to
     `admin-dashboard/tsconfig.json`'s `types` array (or an included
     `vitest-setup.d.ts`), matching whatever the project's `vitest.config.ts`
     `setupFiles` already registers at runtime.
- **Risk if left undone:** low today (doesn't block any real gate), but
  compounds — every new file that imports `expo-router/react-navigation` or
  every new admin-dashboard test file adds more permanent noise, and it's a
  standing trap for anyone who later wires `tsc --noEmit` into CI as an
  actual gate (it would be red from day one for reasons unrelated to their
  diff).
- **Risk of implementing:** low — type-declaration/tsconfig-only fix, no
  runtime behavior change in any of the three apps.
- **Verification once implemented:** `npx tsc --noEmit` clean (0 errors) in
  all three surfaces; `yarn jest --ci` (rider-app, driver-app) and `vitest
  run` (admin-dashboard) still fully green, including the two previously-
  failing-to-load Jest suites now loading successfully.
- **Files:** `rider-app/tsconfig.json`, `driver-app/tsconfig.json`,
  `admin-dashboard/tsconfig.json` (+ possibly a new `vitest-setup.d.ts` in
  admin-dashboard); no application code.

### C23. Chargeback operations: no deadline tracking, no admin visibility, no evidence tooling

- [ ] **Status:** open (filed 2026-08-14 alongside
  `docs/runbooks/payment-dispute-evidence.md`, which documents the manual
  workaround for all three).
- **Issue/gap:** the webhook records a chargeback and then nothing else
  happens. Specifically:
  1. **No `evidence_due_by`.** Stripe puts
     `dispute.evidence_details.due_by` on the event; we drop it. Miss the
     date (7–21 days depending on network) and the dispute is lost
     automatically with no evidence considered. Nothing warns as it
     approaches.
  2. **No admin UI over `stripe_disputes` at all.** The Disputes page
     (`admin-dashboard/src/app/dashboard/disputes`) reads the `disputes`
     table — rider-raised refund requests, a different thing. Card-network
     chargebacks are visible only via SQL or the Stripe Dashboard. The
     `charge_dispute_created` admin WS broadcast fires into a UI that has
     nowhere to show it.
  3. **No evidence pack.** Assembling a response is 4–6 endpoints and 3 SQL
     queries by hand (see the runbook). Everything needed already exists —
     invoice PDF, `route-map.png`, `location-trail`, ride timeline,
     `ride_offers`, account history, `ride_messages` — just not in one
     place.
  4. **No submission path.** `stripe.Dispute.modify(...)` is never called;
     evidence is uploaded manually in the Stripe Dashboard.
- **Action (in priority order):**
  1. Additive migration: `evidence_due_by timestamptz`,
     `evidence_submitted_at timestamptz`, `fee_cents integer` on
     `stripe_disputes`; populate `evidence_due_by` in the
     `charge.dispute.created` handler.
  2. Alert on approach — a Sentry rule on the existing `CHARGEBACK:` error
     log for the open event, plus a T-3-days warning (a replay-safe
     background loop per `spinr-background-loop`, or a Stripe Dashboard
     notification if we'd rather not add a 19th loop).
  3. "Chargebacks" tab on the existing Disputes page reading
     `stripe_disputes` — ride link, reason, amount, status, due date,
     days-remaining. Read-only first.
  4. `GET /api/admin/rides/{ride_id}/dispute-pack` → zip of invoice PDF,
     route-map PNG, GPS-trail CSV, timeline JSON, account-history summary,
     draft cover letter. PIPEDA-filtered by construction (driver_code only,
     never driver phone/plate/address; GPS clipped to
     `navigating_to_pickup` + `trip_in_progress`, matching what
     `route-map.png` already does).
  5. Only then consider submitting from admin via the Stripe Files API —
     higher risk, and the Dashboard works.
- **Why it's P2 not P1:** chargeback volume is currently ~zero, and the
  runbook makes the manual path workable. Items 1–2 should jump to P1 the
  first time a real dispute lands, because a missed deadline is an
  unrecoverable loss.
- **Files:** `backend/routes/webhooks.py`, new `backend/migrations/NN_*.sql`,
  `backend/routes/admin/rides.py` (pack endpoint),
  `admin-dashboard/src/app/dashboard/disputes/`
- **Acceptance:** every open chargeback is visible in admin with its
  deadline; a support agent can produce a complete, PIPEDA-clean evidence
  pack for a ride in one click.

## P3 — Post-launch backlog (tracked, not gating)

### Notification-channel coverage backlog (2026-08-08 audit, branch `claude/email-alerts-spinr-branding-l12lg2`)

Full scenario-by-scenario matrix with file:line for all 45 rider/driver events:
`docs/notification-channel-coverage.md`. Change Impact Log for what was fixed:
`docs/change-log/2026-08-08-driver-lifecycle-email-channel.md`.

_Closed by that branch (do not redo): the shared branded email layout + logo
route, the lifecycle-email policy layer with its `app_settings` kill switch,
driver approval/rejection/suspension/ban emails, document-expiry emails on all
four tiers, the silent document-approval reactivation (D5), the verify/unverify
policy bypass (D7/D8), the expiry-suspension priority tier (D13), and
`email_enabled` becoming a real preference for OPTIONAL-class mail (X1)._

_Also closed by the rider follow-up (`utils/rider_emails.py`, change log
`docs/change-log/2026-08-08-rider-lifecycle-emails.md`): welcome email (R4),
email-address-change security notice to the old address (R7), account-deletion
confirmation (R9), no-show fee receipt (R21), corporate guest receipt (R26,
formerly N2), refund (R29), wallet top-up (R30), and telling the blocked rider
when payment retries are exhausted (R32, formerly N4)._

Remaining, roughly in order of user impact:

- [x] **N1. Rider DSAR export assembles no data and sends no email (R10)** —
  CLOSED (2026-08-11): `POST /users/data-export` now spawns a background task
  that reuses (and extends) the driver-side export builder
  (`routes/drivers/tax_exports.py::_build_and_email_data_export`) to actually
  build and email the export, then reflects the real outcome on the queued
  `data_export_requests` row. Rate-limited to match the driver endpoint
  (`@dsar_export_limit`, 3/hour), since it now runs the same fan-out DB-reads
  + ZIP-build + Storage-upload + email pipeline. The reused function's own
  handling of a rider-only account was found to be incomplete during this
  fix (it previously exported only account + notification_preferences, no
  ride history) and was extended with a `rides_as_rider` + `saved_addresses`
  read, independent of the existing driver-shaped `rides` (gated on having a
  `drivers` row). Caught by an independent `spinr-regulatory-compliance-
  checker` subagent pass before merge. The admin status-change endpoint
  (`routes/admin/users.py:486`) still notifies nobody on manual status
  changes, and the DSAR queue's `status` CHECK constraint has no distinct
  `'failed'` value (a silently-failed auto-fulfillment currently looks
  identical to "not yet attempted") — both left as smaller, explicitly noted
  follow-ups rather than expanding this fix's scope further.
- [x] ~~**N2. Corporate guest rides get no receipt (R26)**~~ — done: the
  receipt hook now sits beside the Meta conversion hook in
  `auto_settle_guest_corporate`, gated on `not already_paid` so a replayed
  settlement does not re-send. A phone-only guest still has no email on file
  and is skipped silently.
- [x] **N3. Three push call sites pass the wrong ID and are silently dropped (X6)**
  — CLOSED (2026-08-11). Confirmed a third site beyond the two originally
  named: `utils/payment_retry.py::notify_driver_payout_failed` (called from
  `retry_stuck_payouts` with `payouts.driver_id`) and the in-progress-retry
  push in `retry_failed_payments` (`rides.driver_id`) both passed a
  `drivers.id` where `send_push_notification` requires `users.id`;
  `routes/admin/vehicle_fleet.py`'s lost-and-found notify passed the raw
  `fcm_token` string instead of any id. Fixed the two `payment_retry.py`
  sites with a shared batched `drivers.id → users.id` resolver
  (`_resolve_driver_user_ids`, one `$in` query per sweep tick rather than
  per row) and the `vehicle_fleet.py` site by passing the already-fetched
  `driver["user_id"]` instead of the token. 143 tests passing; new
  assertions specifically pin the resolved `users.id` (not the driver id or
  token) as what reaches `send_push_notification`'s first argument.
- [x] ~~**N4. Rider blocked from booking is never told (R32)**~~ — done:
  `_alert_admins_payment_exhausted` now emails the rider first, before the
  admin WS broadcast and pushes, and is self-swallowing so those still fire.
- [x] **N5. Rider-cancels-assigned-ride reaches the driver by WebSocket only
  (D29)** — CLOSED (2026-08-11). `routes/rides/cancellation.py`'s driver-notify
  block now also sends a push (`priority="dispatch"`, `target_app="driver"`,
  backgrounded via `spawn()`) alongside the existing WS message, mirroring
  the new-offer push convention in `matching.py`. 48 tests passing across
  `test_e2e_cancellation.py` (10, including a new one asserting the push
  fires with the correct driver `user_id`/priority/target_app/data),
  `test_corporate_company_bookings_routes.py`, and
  `test_ride_cancellation_branches.py`.
  **2026-08-11 follow-up:** the same WS-only gap existed in the same
  function's batch-dispatch pending-offers loop (a driver with a pending,
  not-yet-accepted offer, notified only if this ride is cancelled before
  any driver is assigned) — verified directly by grep before this follow-up
  landed, and closed the same way: identical `priority`/`target_app`/`data`
  push added alongside the existing WS message, inside the loop's existing
  try/except so a push failure is handled the same as a WS failure. 27 tests
  passing (`test_ride_cancellation_branches.py` + `test_e2e_cancellation.py`),
  1 new test pinning the push's exact args. Broader `-k cancel` sweep: 262
  passed. See `docs/change-log/2026-08-11-n5-batch-dispatch-push-fallback.md`.
- [x] ~~**N6. Stripe Connect KYC blocking payouts notifies nobody (D24)**~~ —
  done: `apply_account_update` now detects a genuine
  `stripe_payouts_enabled` True→False edge (comparing the pre-update
  `drivers` row already in scope against the freshly computed mirror
  update) and fires a guaranteed-delivery (`priority="account"`) push with a
  `/driver/payout` deeplink — never on a redelivery of an already-blocked
  account, and never on a driver's first-ever sync (pre-update value unset)
  being misread as a transition. A symmetric False→True "payouts resumed"
  push (`priority="normal"`) is included too. Notification failure is
  swallowed (logged, matching the subscription-cancelled push above it in
  `routes/webhooks.py`) so it can never undo the already-committed mirror
  write.
- [x] ~~**N7. Auto-reactivation after `suspended_until` lapses is silent (D21)**~~
  — done: `_reactivate_tick` now sends a push (`"Account reactivated" / "Your
  account is active again. Welcome back!"`) right after the audit-log insert,
  gated on the same "did our conditional update actually stick" check so a
  replica that loses the reactivation race never double-notifies. Applies to
  both riders and drivers (the `users` table query has no role filter).
  `target_app=None` (legacy `fcm_token`) is deliberate, not unresolved: the
  `users.role` column is admin-RBAC-only, not a rider/driver signal, and an
  account can be both via `is_rider`/`is_driver` — mirrors
  `routes/admin/users.py`'s manual-reactivation push, which already uses the
  same copy and the same `target_app=None`. Push-only, no email, matching
  that same manual precedent (it doesn't email either). 25 tests passing
  (`test_suspension_reactivation.py`, `test_suspension_reactivation_coverage.py`),
  6 new specifically on the notification behavior. **2026-08-11 follow-up:**
  a 5th instance of the B21 lock-TTL bug (`interval * 2` against a `1x`
  sleep, halving cadence) found in this same file while working this item —
  fixed separately, see B21's addendum below.
- [x] **N8. Delete dead `utils/receipt_email.py` (X4)** — CLOSED (2026-08-11).
  Deleted the file and its test; confirmed via repo-wide grep that every
  remaining `receipt_email`/`send_receipt_email` reference resolves to the
  live `utils/email_receipt.py` module, not the deleted one. Dropped the
  now-stale exemption entry from `test_all_emails_are_branded.py`'s
  `_UNBRANDED_BY_DESIGN` map.
- [x] ~~**N9. Five notification preferences are still dead columns (X2)**~~ —
  partially closed 2026-08-11: investigated every one of the 5
  `notification_preferences` columns end-to-end (grepped every read/write
  site backend-wide) and made a WIRE-or-REMOVE call per column, documented
  below. 2 wired (backend only); 3 determined genuinely dead after
  investigation, with frontend toggle removal flagged as follow-up (this
  session was backend-only, per scope).
  - **`earnings_summary` — WIRED.** `utils/driver_statement_job.py`'s
    `_process_driver` now calls a new `_earnings_summary_enabled(user_id)`
    (reads `notification_preferences.earnings_summary`, defaults True on a
    missing row or a lookup error — fails open, same posture as
    `push_enabled` in `features.send_push_notification`) right after the
    existing activity check and before the PDF render / email send. A
    driver who opted out gets a `driver_statements` row written with the new
    terminal status `skipped_opted_out` (so the period still converges and
    is never rescanned for them — same pattern as the pre-existing
    `skipped_no_email`/`skipped_inactive`), with `totals` still populated so
    the admin listing keeps the numbers even though nothing was emailed. No
    schema change needed — `driver_statements.status` has no CHECK
    constraint — but migration `300_driver_statements_skipped_opted_out_comment.sql`
    updates the column COMMENT (the documented status-lifecycle source of
    truth) to list the new value, per the append-only migration rule (never
    edit merged migration 272 in place).
  - **`ride_updates` — WIRED, centrally, in `backend/features.py`.**
    `send_push_notification` already read `push_enabled` for every
    non-time-critical push; it now also checks `ride_updates` for a narrow
    `_RIDE_UPDATE_PUSH_TYPES` set (`driver_accepted`, `driver_arrived`,
    `ride_started`, `ride_completed`, `ride_cancelled`, `ride_noshow`) —
    exactly what the rider-app's own copy calls this toggle
    (`rider-app/app/_layout.tsx`'s `ride-updates` Android channel: "Status
    updates for your current ride"). Gated in the single choke-point
    function rather than at each of the ~15 call sites, both to avoid
    preference-check drift and to avoid touching the same route files N10
    (in flight this same day) is editing per call site. Deliberately
    excludes: anything already `priority="dispatch"/"safety"/"account"`
    (the `time_critical` bypass runs first, so e.g. the driver-directed
    dispatch-priority `ride_cancelled` push in
    `routes/rides/cancellation.py` is unaffected — confirmed by a new
    regression test); `ride_offer_expired`/`auto_offline`/`quota_exhausted`
    (driver-availability bookkeeping, not "my ride's status"); and every
    `scheduled_ride_*` type (a different "upcoming booking reminder" UX,
    left for a follow-up rather than guessed at in this pass).
  - **`sms_enabled` — determined DEAD, no safe wiring found.** Every
    `send_sms`/`send_otp_sms` call site was inventoried
    (`routes/auth.py` OTP login — must never be preference-gated;
    `routes/rides/safety.py` SOS emergency-contact texts — safety-critical,
    never gate; `services/guest_notification_service.py` guest-ride SMS —
    carries the pickup OTP + tracking link, the guest's only channel,
    transactional; `utils/marketing_sms.py` — already CASL-gated by the
    *separate* `marketing_preferences.sms_opt_in` consent system, not this
    column; `routes/admin/messaging.py`'s `_send_sms_one` non-marketing
    branch — the file's own code comment documents this as deliberately
    ungated because it may carry "safety, outage" content, and the endpoint
    has no sub-classification to separate that from routine info, so gating
    it risks silently suppressing an operational/outage broadcast). No
    remaining call site is both real and safe to gate. Frontend follow-up:
    remove the "SMS Notifications" toggle from rider-app/driver-app
    settings (not verified/edited this session — backend-only scope).
  - **`safety_alerts` — determined DEAD, no safe wiring found; actively
    unsafe to wire.** The only `priority="safety"` push in the codebase is
    the SOS self-confirmation in `routes/rides/safety.py`
    ("Your emergency alert reached our safety team...") — one of the three
    guaranteed-delivery tiers in `features.send_push_notification` that
    *bypasses* the opt-out by design. Letting a `safety_alerts` toggle
    suppress that would directly contradict CLAUDE.md's safety guardrails.
    No other "safety advisory"-style informational push exists to gate
    instead. Frontend follow-up: remove the toggle (not verified/edited
    this session).
  - **`promotions` — determined DEAD/redundant, not wired.** Spinr already
    has a legally-scoped CASL consent system for marketing content
    (`services/marketing_consent.py`'s `marketing_preferences` table, migration
    190) that independently governs `utils/marketing_push.py` and
    `utils/marketing_sms.py`. Wiring `notification_preferences.promotions`
    as a second, differently-scoped opt-in for the same channel/category
    would create two sources of truth for "may we send this user
    promotional content" — a correctness and compliance risk, not a safe
    win. Frontend follow-up: remove the toggle, or (better, not decided
    here) point it at the existing `marketing_preferences.push_opt_in` /
    `sms_opt_in` read path if product wants a single settings-screen
    control — a product decision, not a backend one.
  - Tests: `backend/tests/test_notification_preferences.py` (4 new cases —
    ride_updates suppresses/doesn't-suppress by type, dispatch-priority
    bypass, opted-in still sends) and
    `backend/tests/test_driver_statement_job.py` (4 new cases — opted-out
    skips email but still claims/records totals, opted-in still sends,
    fail-open on lookup error, defaults-true with no prefs row).
- [x] **N10. Most rider pushes omit `target_app="rider"` (X5)** — they fall
  through to the legacy `users.fcm_token` column (`features.py:1664-1670`)
  rather than `fcm_token_rider`. Works today only because registration still
  mirrors both (`routes/notifications.py:329-336`); breaks silently if that
  mirroring is ever removed.
  **2026-08-11 update — Batch 1 done:** fixed the 10 clearest, unambiguous
  rider-directed call sites across 5 files —
  `routes/rides/lifecycle.py:113` (ride-started push),
  `routes/rides/matching.py:1347` (no-drivers-found auto-cancel),
  `utils/scheduled_rides.py:93,102,302,492,537` (delay notice ×2,
  policy-blocked, dispatch-fired, 10-min reminder),
  `utils/stuck_ride_sweeper.py:117` (stuck-ride auto-cancel), and
  `services/payment_service.py:1355,1402` (payment-failed ×2, fresh-charge
  path). Each got a regression test asserting `target_app="rider"` is passed
  (verified to fail if reverted). ~19 call sites remain, inventoried in
  `docs/change-log/2026-08-11-n10-rider-push-target-app-batch1.md`'s
  "Remaining scope" section.
  **2026-08-11 update — Batch 2 done:** fixed the 3 sites flagged as the
  next clearest batch — `routes/disputes.py:98` (dispute-created push),
  `routes/disputes.py:308` (dispute-resolved push), and
  `services/guest_notification_service.py:167` (corporate app-holder guest
  booking push) — all confirmed unambiguously rider-directed
  (`ride.get("rider_id")`/`dispute.get("user_id")`/`guest_user["id"]`, all
  rider accounts), each with a new regression test asserting
  `target_app="rider"`. Also closed out the two items this batch's parent
  note had deferred: `routes/rides/cancellation.py`'s two push call sites
  both already target the driver (`target_app="driver"`) — no rider-push
  gap there, nothing to fix; `utils/receipt_email.py` no longer exists
  (deleted by N8) — moot. That empties the batch-1 change-log's
  "clearly rider-directed, ready to fix" list. What's left per that same
  doc: 3 driver-directed sites missing `target_app="driver"` (a related but
  distinct gap), plus an "ambiguous/admin" bucket
  (`routes/notifications.py:108`'s `/test-push`, and the `routes/admin/*.py`
  broadcast endpoints where recipient role varies per admin selection) that
  needs its own per-call-site read rather than a batch sweep.
  **2026-08-11 update — driver batch done:** of the 3 flagged driver-directed
  sites, `utils/document_expiry.py:216` (and its sibling push at line 296)
  already had `target_app="driver"` — moot, already fixed by an earlier
  pass. Fixed the 2 genuinely open ones: `routes/rides/matching.py`'s
  auto-offline push (driver missed too many offers in a row) and
  `services/cancellation_service.py::pay_driver_cancellation_fee`'s payout
  push. Both confirmed driver-directed (`driver_user_id`, resolved from
  `get_driver_by_id`). New test file
  `tests/test_cancellation_service_driver_push.py` (nothing previously
  exercised `pay_driver_cancellation_fee` directly — every other
  cancellation test mocks it out entirely) plus a new assertion on the
  existing `test_offer_timeout_handler_auto_offline_notifies_and_pushes`.
  Only the "ambiguous/admin" bucket remains — deliberately not swept here,
  needs its own per-call-site read.
  **2026-08-11 update — admin/ambiguous bucket done, N10 fully closed:**
  read each of the 8 flagged call sites individually rather than batch-fixing
  — several turned out to already be correct or genuinely fine as-is:
  - **`routes/notifications.py:108` `/test-push`**: confirmed correct as-is,
    not a gap — its whole documented purpose is diagnosing the legacy
    `users.fcm_token` column specifically (`token_on_file`/`token_preview`/
    `platform_hint` all read that exact field), so staying on the legacy
    path is the intended behavior, not an oversight.
  - **`routes/admin/wallet.py`** (`admin_credit_wallet`/`admin_debit_wallet`):
    already correctly wired via a `_wallet_target_app(user)` helper from the
    earlier N15/R31 pass this session — no gap, nothing to fix.
  - **`routes/admin/rides.py:901`** (promo-applied push): already
    `target_app="rider"` from the N15/R33 pass — no gap.
  - **Genuinely fixed** (9 sites, 6 files): `routes/admin/documents.py`
    (document-rejection push → `driver`), `routes/admin/drivers.py` ×3
    (expiry nudge, photo approved, photo rejected → all `driver`),
    `routes/admin/rides.py` ×2 more (`admin_cancel_ride`'s driver leg →
    `driver`, rider leg → `rider`; the new-ride dispatch push → `driver`),
    `routes/admin/users.py` (`admin_update_user_status`'s account-status
    push → `rider` — settled by the endpoint's own docstring, which scopes
    it to "Suspend, ban, or reactivate a rider account"/"cannot request a
    ride"; it operates on the shared `users` table but is rider-only by
    design, not by accident), `routes/admin/vehicle_fleet.py`
    (lost-and-found push → `driver`).
  - **`routes/admin/faqs.py`**'s `admin_send_notification` broadcast: the
    `riders`/`drivers` loops now pass `target_app="rider"`/`"driver"`
    per-iteration. The `all` loop deliberately stays unset — it spans both
    roles with no per-user role lookup in that branch, so it can't map to a
    single column; this matches the existing, already-accepted precedent in
    `routes/admin/messaging.py`'s own `_target_app_for_audience`, which
    already returns `None` for its equivalent "all" case (legacy `fcm_token`
    fallback, not a bug — the correct behavior for a role-spanning
    broadcast is documented there, not just replicated blindly).
  - Tests: new `tests/test_n10_admin_push_target_app.py` (9 tests, one per
    fix site, function-level — importing each admin route module and
    patching its actual bound `send_push_notification` name, module-level
    for `documents.py`/`drivers.py`/`vehicle_fleet.py`, or the `features`
    module directly for `faqs.py`/`users.py`, which import it locally
    inside the function body rather than at module top — patching the
    wrong target here silently no-ops, the real function runs, and it logs
    "No user found for X — push dropped" and returns normally: several
    *pre-existing* tests in `test_admin_drivers_coverage.py` and similar
    files use exactly this ineffective `patch("features.
    send_push_notification", ...)` pattern against module-top-level-import
    call sites and were never actually intercepting the push — a real gap
    in this repo's test quality, flagged here rather than fixed wholesale
    since it's a pre-existing issue orthogonal to N10's own scope, not
    introduced by this change) — plus 2 new assertions extending existing
    tests in `tests/test_admin_rides_coverage.py`
    (`test_cancel_notifies_driver_and_rider_with_correct_target_app`,
    extended `test_create_ride_with_driver_status_driver_assigned_and_dispatches`).
    382 tests total across every touched/adjacent file, all passing.
  **What was NOT verified**: no live FCM/Expo push exercised for any of the
  9 sites (mocked per repo convention, matching every other N10 fix this
  session). The pre-existing ineffective-patch-target test-quality gap
  noted above was not audited or fixed across the rest of the test suite —
  flagged, not swept.
- [x] ~~**N16. Consolidate the two copies of the company-address assembly**~~
  — done 2026-08-11: `_coalesce`/`_postal_address` were byte-identical logic
  duplicated across `utils/company_details.py` and `utils/marketing_email.py`.
  Extracted into a new shared `utils/address_format.py`
  (`coalesce_setting`/`postal_address`, public names since it's now a real
  shared module, not a private per-file helper), imported back into both
  files under their original `_coalesce`/`_postal_address` local names so
  every call site is unchanged. Zero output-behavior change by design: the
  marketing CASL footer (`build_footer_html`/`build_footer_text`) and the
  transactional-email `CompanyDetails` both still receive exactly the same
  string for the same settings input — proven by running both files'
  existing test suites unmodified (`test_company_details.py` 22 tests,
  `test_marketing_email.py` 3 tests, plus `test_marketing_broadcast.py`,
  `test_all_emails_are_branded.py`, `test_branded_documents.py`,
  `test_branded_receipt_flag.py`, `test_email_layout.py`,
  `test_receipt_shell_snapshot.py` — 121 total, all pass unmodified) and by
  adding 9 new direct unit tests for the shared module in
  `tests/test_address_format.py`. Blast radius: grepped every other
  importer of `company_details`/`marketing_email` repo-wide — all import
  only public functions (`load_company_details`, `to_latin1`,
  `send_marketing_email`), none import the private `_coalesce`/
  `_postal_address` names directly, so no other file needed a change. This
  satisfies the item's own deferral condition — "touching it needs its own
  review" — by making the marketing copy's only change an import swap with
  a byte-for-byte-identical function body and proven zero output delta,
  rather than folding a behavior change into an unrelated commit.
- [x] ~~**N11. Retrofit the ride receipt and Spinr Pass invoice**~~ — done:
  both now use the shared header/footer, the real logo, `#FF3B30`, and the
  company name and address from the admin Settings page, in the email **and**
  in the attached PDFs. The receipt gained a plain-text alternative carrying
  the same GST/PST breakdown. Behind `branded_receipt_enabled` (migration
  288, defaults on); the pre-retrofit shell is kept verbatim and pinned by
  `tests/test_receipt_shell_snapshot.py`. See
  `docs/change-log/2026-08-08-receipt-invoice-branding-retrofit.md`.
- [x] ~~**N11b. Remaining un-retrofitted emails**~~ — done: corporate OTP,
  member invite, KYB decision, signup ops alert, admin broadcast and the
  T4A/DSAR export emails now render through `utils/email_layout`. Driver
  statements, corporate low-balance and the safety-team alert are branded
  indirectly — `features.send_email` now wraps a plain-text `body` in the
  shared shell when the caller supplies no `html`, so the next sender added
  that way is branded by default. Enforced by
  `tests/test_all_emails_are_branded.py`, which fails if any send site
  bypasses the layout without an argued allowlist entry. The admin broadcast
  additionally now **escapes** admin-authored free text, which the previous
  bare `<h2>`/`<p>` interpolation did not. See
  `docs/change-log/2026-08-09-all-emails-on-shared-branded-shell.md`.
- [ ] **N18. No light-on-dark variant of the Spinr logo** — `spinr_logo.png` is
  a charcoal wordmark with a red spiral "o", drawn for a light ground. That
  constrains the email layout in two places: the header band must stay light
  even in dark mode, and the dark footer carries the company name as text
  rather than the mark. A light-on-dark variant would let the email invert
  fully. Design decision, not a code one — see
  `docs/change-log/2026-08-09-email-header-uber-format.md`.
- [x] **N17 closed**: added `company_app_name` (default `"Spinr"`) as a new,
  independent setting alongside `company_name` (the legal entity) —
  `schemas.AppSettings.company_app_name`, wired through
  `routes/admin/settings.py`'s `SettingsUpdateRequest` the same way
  `company_logo_url` etc. are, and surfaced on the admin Settings page →
  Company Info tab (new "App Name" field next to "Company Name", each with a
  help line pointing at the other). `utils/company_details.CompanyDetails`
  gained an `app_name` field, resolved in `load_company_details()` from
  `company_app_name` with the same "falls back to Spinr" rule as every other
  field there. Swept all four named files to interpolate
  `company.app_name`/`{app_name}` instead of the literal word "Spinr" in
  BODY copy (subjects, "Open the ... app", "your ... wallet", "— The ...
  Team", the DSAR README/HTML/link-email copy) — footer/mailing-address/logo
  usages correctly stayed on `company_name` via the existing
  `company_details.py` plumbing, untouched.
  `utils/driver_status_notifications.py` and `utils/document_expiry.py`
  build their next-step copy as static/module-level strings ahead of the
  async settings load, so those two use a `{app_name}` placeholder
  substituted at send time (mirroring the existing `{support}` placeholder
  pattern already in `driver_status_notifications.py`) rather than an
  f-string. Tests: `tests/test_company_details.py` (schema default +
  independence from `company_name`), `tests/test_admin_settings_company_app_name.py`
  (request-model/loader wiring, mirroring `test_admin_settings_company_logo.py`),
  and one fallback+configured-value test per swept file
  (`tests/test_rider_emails_app_name.py`,
  `tests/test_driver_status_email_app_name.py`,
  `tests/test_document_expiry_app_name.py`,
  `tests/test_tax_exports_app_name.py`) — 61 new tests, all passing, plus the
  181 pre-existing tests across these files' own test suites re-verified
  green. Admin-dashboard: `npm run build` run and passed (exit 0), not just
  `tsc`/dev server, per CLAUDE.md's explicit requirement.
- [ ] **N11c. Delete the pre-retrofit receipt/invoice shell and its flag** —
  once the branded version has been seen in real inboxes. Two shells and a
  switch are a real carrying cost; both `_LEGACY_*` constants are commented
  to say so.
- [x] **N12. No visual/snapshot regression tooling for email** — **partially
  closed (2026-08-12).** The "nothing pins the whole rendered document" half
  is fixed: new `backend/tests/_html_snapshot.py` (golden-file diffing
  helper, missing-snapshot-writes-and-passes / drift-fails-with-a-diff /
  `SPINR_UPDATE_EMAIL_SNAPSHOTS=1` to update deliberately) plus
  `backend/tests/test_email_snapshots.py` (6 tests, 9 committed golden
  files under `backend/tests/snapshots/email/`) pins the full HTML+text
  output of `utils/email_layout.py`'s `render_email`/`render_from_text`
  (minimal and fully-populated shapes) and `utils/email_receipt.py`'s
  legacy/branded shells — the two real template generators in this repo.
  Verified the failure path actually fires (corrupted a golden file,
  confirmed a readable diff, restored it) before relying on it. No
  application code changed. **What this does NOT close, still a real
  gap:** actual rendering in Gmail/Outlook/Apple Mail — that needs a
  per-client renderer/screenshot pipeline this pass didn't build, same as
  N12's own original text already said. `utils/subscription_invoice.py`
  (PDF + kwargs, not raw HTML, DB-dependent) also wasn't brought into this
  net — different shape of problem. See
  `docs/change-log/2026-08-12-n12-email-snapshot-tooling.md`.
- [x] ~~**N13. Rider-side lifecycle emails**~~ — done for welcome (R4),
  email-address-change security notice (R7), account-deletion confirmation
  (R9), no-show fee (R21), refund (R29) and wallet top-up (R30). All live in
  `utils/rider_emails.py` and go through the policy layer, so the
  `lifecycle_emails_enabled` kill switch covers them.
- [ ] **N14. Rider email addresses are never verified (R5)** — **partially
  done.** The verification flow itself now exists and is tested:
  `POST /users/verify-email/request` + `POST /users/verify-email/confirm`
  (`routes/users.py`) reuse the corporate portal's exact OTP mechanics
  (`routes/auth.py:744`'s `_check_otp_lockout`/`_record_otp_failure`/
  `_clear_otp_failures`/`_enforce_otp_send_cap`, imported directly rather than
  re-implemented) — SHA-256-hashed code at rest, 5-failures/hour lockout,
  "1234" dev bypass only when `ENV != production`, refused outright in
  production with no email provider configured. Codes are stored in a new
  `rider_email_verification_otp` table (migration `299`, keyed on `user_id`
  rather than the corporate table's bare `email`, so the two flows' lockout
  buckets never collide even for the same address) and delivered through a
  new `send_email_verification_code` in `utils/rider_emails.py`, going through
  the same policy layer (`send_lifecycle_email`, TRANSACTIONAL class) as every
  other rider email. The request endpoint carries its own outer rate limit
  (`rider_email_verify_request_limit`, 3/hour, user-keyed) on top of the
  reused per-destination send cap. Confirming flips `email_verified` +
  `email_verified_at` on the caller's own `users` row only, and refuses if the
  account's email changed between request and confirm (code was minted for
  the old address). 12 tests in `tests/test_rider_email_verification.py`
  cover hashing, success, wrong-code, expiry, lockout, the email-changed
  guard, and both sides of the rate limit.
  **Two things this pass deliberately did NOT do, both open:**
  (a) **no rider-app UI calls either endpoint** — this was a backend-only
  session, so there is no "verify your email" prompt, banner, or settings
  entry anywhere in `rider-app/` yet. The capability exists and is safe to
  call, but nothing in the product surfaces it to a rider today. Follow-up.
  **2026-08-11 update: done.** `rider-app/app/verify-email.tsx` (new screen,
  sibling of `app/otp.tsx`'s phone-OTP flow) calls both endpoints and handles
  every documented response/error case (`already_verified` short-circuit,
  `PROFILE_EMAIL_MISSING`, `AUTH_OTP_INVALID`, `AUTH_OTP_EXPIRED`, the 3/hour
  request rate limit, the 5-failures/hour confirm lockout,
  `SYSTEM_SERVICE_UNAVAILABLE`) with plain-English copy resolved through the
  existing i18n `message_key` system, not raw backend sentinels. Entry point
  is a Verify/Verified pill added to the existing Email row on the Account
  tab's Personal Info card (`app/(tabs)/account.tsx`) — purely additive,
  discoverable only there, never a forced/blocking flow. 15 new tests
  (`__tests__/verifyEmailScreen.test.tsx`, 10;
  `__tests__/accountEmailVerification.test.tsx`, 5), full rider-app suite run
  for real: 455 passed / 0 failed across 54 suites. `yarn tsc --noEmit` clean.
  `yarn build:web` (`expo export --platform web`) production build completed
  successfully — not just a dev-server/`tsc` check. **Residual gap
  discovered, not fixed there (backend-file change, out of that PR's scope):**
  `GET /auth/me`'s response schema (`backend/schemas.py`'s `UserProfile`)
  didn't return `email_verified`, so the Account-screen badge was sourced
  from a local store merge (the confirm response itself carries
  `email_verified: true`) rather than a normal profile refresh — a verified
  rider's badge reverted to "not verified" after a full app restart. See
  `docs/change-log/2026-08-11-n14-rider-email-verify-ui.md` for full detail.
  **2026-08-11 follow-up: done.** `UserProfile` now declares
  `email_verified`/`email_verified_at`; every `UserProfile(**row)`
  construction site (11, across `routes/auth.py`/`routes/users.py`) already
  spreads the full DB row, so no other file needed a change — both fields
  now flow through automatically wherever a profile is returned, including
  `/auth/me`. New test pins both the verified-True case and a legacy row
  with the key entirely missing (defaults to `False`/`None`, no error).
  102 passed in the direct auth/email-verify suite, 168 in a broader
  schemas/users/auth sweep, 123 across the admin-users-adjacent files — all
  clean, 0 failed. See
  `docs/change-log/2026-08-11-n14-auth-me-email-verified-field.md`.
  (b) **whether/how to gate anything on `email_verified` remains an open
  product decision**, not resolved here — nothing was changed to require
  verification before booking, payouts, or any other flow, and CLAUDE.md's
  pre-merge gates (feature-flag anything user-visible; no silent behavior
  change) mean that decision needs explicit product sign-off before any
  gating ships, not a unilateral backend call.
  **Discovered existing consumer (not introduced by this change):**
  `routes/corporate_rider.py`'s `POST /corporate/join-domain` already 403s
  with `ERR_EMAIL_UNVERIFIED` when `email_verified` is falsy (added in
  migration 252, before this flow existed). Before this change that gate was
  permanently unsatisfiable for a rider — there was no way for the flag to
  ever become true — so join-domain 403'd for every rider, always. This
  change doesn't add a new gate; it makes the pre-existing one work as
  originally intended. The practical effect: a rider who runs the new
  verify-email flow can now successfully call join-domain where before they
  categorically could not. Flagging this explicitly since it's a real,
  immediately-live behavior change on a corporate-adjacent endpoint even
  though no new gating code was written.
- [x] **N15. Remaining silent rider surfaces** — grouped rather than split,
  since they share one cause (no notification call of any kind at the site):
  rider-initiated ride completion sends the rider nothing while the
  driver-initiated path does (R19, `routes/rides/lifecycle.py:126`); wallet
  debits/credits, promos and loyalty tier changes have zero notification calls
  (R31/R33/R34); scheduled rides have one reminder tier and no booking
  confirmation (R35/R37); rider SOS sends the rider no confirmation that help
  was alerted (R38); corporate allowance reset and exhaustion are silent, with
  exhaustion surfacing only as a 4xx at booking (R43/R44); and there is no
  "new device signed in" alert (R8).
  - [x] **R19 closed**: `rider_complete_ride` (`routes/rides/lifecycle.py`)
    now spawns the same "Ride Completed! ✅" push the driver-initiated path
    (`routes/drivers/ride_complete.py::complete_ride`) already sends,
    alongside the existing `ride_completed` WS message — `target_app="rider"`,
    `priority="normal"` (informational, not dispatch/safety-tier).
  - [x] **R31/R33/R34 closed** (discovery-first pass, scoped to the rider's
    *personal* wallet only — corporate wallet/allowance is R43/R44, a
    separate parallel workstream):
    - **R31 (wallet)**: `routes/wallet.py`'s `/top-up` was already covered (a
      push already fires in `routes/webhooks.py`'s `payment_intent.succeeded`
      handler — verified by reading it, not a gap). The genuinely silent
      choke point was `routes/admin/wallet.py`'s `admin_credit_wallet` /
      `admin_debit_wallet` (an admin moving money in/out of a rider or
      driver's wallet with zero trace to the user) — now sends a best-effort
      push, `target_app` resolved from the user's role. Deliberately left
      open: `utils/referral_payout.py`'s rider-referral wallet credit
      (separate money flow/audience, needs its own file + tests) and
      driver-side cancellation/no-show fee wallet debits in
      `routes/rides/cancellation.py` (owned by a parallel session).
    - **R33 (promos)**: the rider-initiated `POST /promotions/apply` already
      returns `discount_applied` synchronously — not silent, left alone. The
      real gap was the admin "apply promo on behalf of a rider" path
      (`apply_promo_for_admin`, called from
      `routes/admin/rides.py::admin_create_ride`) — now sends a best-effort
      push with the promo code + discount amount.
    - **R34 (loyalty)**: `routes/loyalty.py::earn_points_for_ride` already
      computed `tier_upgraded` in its response but never notified — now
      sends a best-effort push when a rider crosses a tier threshold.
    - All three: best-effort, `priority="normal"`, wrapped in try/except so a
      push failure can never surface as a failed money/promo/loyalty write
      that already committed. New tests in `test_admin_wallet_endpoints.py`,
      `test_admin_rides_coverage.py`, `test_loyalty.py`.
  - [x] **R35/R37 closed**: `routes/rides/booking.py::create_ride` now fires
    a "Scheduled ride confirmed" push (`priority="normal"`, no `target_app`
    override, matching `utils/scheduled_rides.py`'s existing rider-facing
    scheduled-ride pushes) at the moment a deferred scheduled ride is
    inserted, backgrounded via `_deps.spawn()` alongside this function's
    other post-insert side effects; the pre-existing ~10-minute reminder
    (`_send_reminder`) and driver-nudge/delay-notice tiers are unchanged.
    Known gap: `services/company_booking_service.py` (corporate guest
    booking) bypasses `create_ride` and does not get this confirmation —
    deliberate scope boundary, not a miss.
  - [x] **R38 closed** (2026-08-11): `trigger_emergency`
    (`backend/routes/rides/safety.py`) already notified the admin dashboard
    (WS), the safety-team email list, on-call paging, and emergency contacts
    (SMS) — but never told the triggering rider/driver themselves anything
    beyond the synchronous HTTP 200 (which `SOSButton.tsx` already turns
    into an "Alert Sent" dialog, foreground-only). Added one additive
    `_deps.spawn(_deps.send_push_notification(...))` call, self-swallowing
    (try/except, matches every other side effect in the function),
    `priority="safety"` (guaranteed-delivery tier per
    `features.py::send_push_notification`'s docstring — bypasses push
    opt-out, falls back to the retry queue), `target_app` routed to
    `"rider"`/`"driver"` by who triggered it. Copy: "SOS Alert Received" /
    "Your emergency alert reached our safety team and emergency contacts. If
    you're in immediate danger, call 911." — confirms receipt only, never
    claims to replace or guarantee 911 response (`CLAUDE.md` → "What Spinr
    Is NOT"). No existing SOS behavior touched (DB insert, admin broadcast,
    paging, contact SMS all unchanged) — see
    `docs/change-log/2026-08-11-n15d-rider-sos-confirmation.md` for full
    blast-radius check (isolated — `trigger_emergency` has exactly one call
    site, the route itself) and verification detail. Tests:
    `backend/tests/test_p2_sos.py::TestTriggerEmergency::test_rider_sos_confirmation_push_sent_to_triggering_rider`
    and `..._test_driver_sos_confirmation_push_targets_driver_app`, plus the
    26 pre-existing SOS tests re-run green.
  - [x] **R43/R44 closed** (2026-08-11): `utils/allowance_reset.py` now pushes
    the member a "your allowance has reset" notice right after a successful
    non-rollover reset (rollover allowances don't fire — `used` is untouched
    by that reset, so nothing changed for the rider); rate-limited for free
    by the loop's existing period-CAS claim, no new column. `services/
    payment_service.py::settle_corporate` now pushes "running low" (crosses
    below 20% remaining) or "allowance used up" (crosses to 0) exactly on
    the ride debit that causes the crossing — comparing this debit's own
    remaining-before/after is enough to fire at most once per crossing, no
    new rate-limit column either. Both `priority="normal"` (best-effort,
    non-time-critical; reasoning in the payment_service.py docstring —
    `"account"` tier is reserved for driver accept/reject/suspend per
    features.py, not a fit here). Deliberately NOT done: no in-app UI
    surfacing of these events beyond the existing Notifications inbox row
    every `send_push_notification` call already writes; no admin-configurable
    threshold (20% is hardcoded, not sourced from a product spec); the 4xx at
    booking time itself (`routes/rides/booking.py`'s `allowance_low`,
    `services/company_booking_service.py`'s `allowance_only` check) is
    unchanged — this is advance warning, not a removal of the block. Tests:
    `tests/test_corporate_allowance_reset.py` (3 new),
    `tests/test_corporate_ride_payment.py` (6 new) — all mock
    `send_push_notification` and assert exact fire/no-fire per scenario. Full
    Change Impact & Risk Log:
    `docs/change-log/2026-08-11-n15-r43-r44-corporate-allowance-notify.md`.
  - [x] **R8 closed** (2026-08-11): new-device sign-in alert, the last open
    piece of N15. No device-fingerprint infra existed before this — built
    on the substrate `refresh_tokens` already had rather than adding a new
    table: `issue_refresh_token` (`utils/refresh_tokens.py`) has always
    persisted `user_agent` per session, so a new `is_new_device(user_id,
    audience, user_agent)` checks whether that exact User-Agent string has
    ever minted a refresh token for this user+audience before. `ip` was
    deliberately not used as a fingerprint component — too unstable on
    mobile networks, would false-positive on every login. A blank/missing
    User-Agent can't be fingerprinted at all and is treated as "not new"
    (never alerts) rather than risking a false positive on every client
    that omits the header; a DB error on the check also fails quiet
    (returns "not new") so a notification-path hiccup can never block or
    slow down login. New `send_new_device_notice` in `utils/rider_emails.py`
    mirrors `send_email_changed_notice` (R7)'s established security-notice
    pattern — email, not push, since the new device's push token may
    belong to whoever is actually signing in. Wired into the 3 rider login
    call sites that represent a genuine new sign-in — `verify_otp`'s
    existing-user branch (the primary phone-OTP login),
    `reactivate_account` (PIPEDA self-serve reactivation — still a real
    sign-in), and `verify_company_email_otp`'s existing-user branch
    (`_issue_company_email_session`) — via one shared `_alert_if_new_device`
    helper in `routes/auth.py`, called BEFORE `issue_refresh_token` mints
    the current login's own row (otherwise that row would satisfy its own
    "have we seen this before" check). Fire-and-forget
    (`asyncio.create_task`), matching every other post-login side effect in
    this file (audit log, corporate invite activation) — never blocks or
    delays the auth response. Deliberately NOT wired: brand-new signups
    (both the OTP-verify new-user branch and the company-email new-user
    branch) — every device is "new" by definition on account creation, so
    alerting there would be pure noise, not a security signal; the driver
    Firebase-auth login (`audience="driver"`) — R8 is scoped to riders
    (N15's own title), and driver push-token/device conventions are a
    separate surface not touched here; the `/auth/refresh` token-rotation
    endpoint — rotating an already-issued session is not a new sign-in.
    Tests: 5 new in `tests/test_refresh_tokens_lifecycle.py`
    (`is_new_device`'s blank-UA/no-prior-row/prior-row-exists/DB-error/
    audience-scoping branches), 4 new in `tests/test_rider_account_emails.py`
    (`send_new_device_notice`'s addressing, copy, transactional class,
    failure-never-propagates), 4 new wiring tests across
    `tests/test_verify_otp_login_flow.py` and
    `tests/test_auth_remaining_endpoints.py` confirming `_alert_if_new_device`
    fires for each of the 3 existing-user login paths and is skipped for
    both new-signup paths — 90 tests total across the 4 touched test files,
    all passing (up from 78 before this change, confirming nothing existing
    broke). **What was NOT verified**: no live-Redis/live-Supabase
    integration test exercising the actual `refresh_tokens` round-trip
    across two real logins with the same/different User-Agent — unit tests
    confirm `is_new_device`'s own logic against a mocked `db.find_one`, not
    the full query-storage path (same level of verification the file's
    other DB-touching helpers already have). No rider-app UI change — this
    is backend/email-only, matching how R7's email-change notice shipped;
    a rider who signs in from a new device sees nothing different in the
    app itself, only an email. No frontend build/test run — no frontend
    files touched by this change.

### AI assistant / MCP guardrail backlog (2026-07-28 audit, branch `claude/rider-ai-location-selection-yn0mem`)

_Implemented from the same audit (do not redo): tapped-suggestion coordinate
plumbing (rider + admin console), never-re-ask-twice + no-internal-jargon +
driver-persona-secrecy prompt rules, per-tool timeouts for the Maps fan-out
tools, `/mcp` read-only enforcement + per-user daily cap, truncation-preserves-
guardrail-notes, threat-flagged turns excluded from the FAQ cache. Remaining:_

- [x] **AI1. `/ai/chat` rate limit is per-IP, not per-user** — done: the
  IP-keying half is fixed. `ai_chat_limit` (`utils/rate_limiter.py`) now uses
  a new `get_ai_chat_key`, which keys on the bearer token's `user_id`/`sub`
  claim (signature not verified for keying purposes — the real,
  signature-verified `get_current_user` dependency still gates the request
  itself downstream, so a forged token can only land in a throwaway bucket
  for a request that then 401s, never impersonate another user's bucket;
  mirrors the existing unverified-extraction pattern already established in
  `core/middleware.py::_extract_user_id` for log correlation) and falls back
  to IP only when no bearer token is present. 12 new unit tests in
  `tests/test_coverage_boost.py::TestGetAiChatKey` cover the user-id/sub
  claim paths, the two-IPs-one-token-one-bucket property, and every
  IP-fallback branch (no token, garbage token, no user claim). The
  **daily-cap fail-open** half was deliberately left alone: it's an
  existing, already-documented, cross-referenced design decision
  (`ai/orchestrator.py::_over_daily_cap`'s own docstring: "Fails OPEN with a
  loud log — the kill switch remains the hard stop when Redis is down"),
  not an oversight — changing an accepted trade-off wasn't this fix's call
  to make silently. **Not yet verified:** no live-Redis integration test
  exercising the actual distributed counter across two rotated IPs with the
  same token — the unit tests confirm the key function's own logic, not the
  full rate-limit-storage round trip (that's the same level of verification
  every other key-function in this file has, per existing test coverage).
- [ ] **AI1b. Daily-cap fail-open on Redis error — revisit as its own decision.**
  Spun off 2026-08-10 while reconciling a merge conflict: a parallel session
  independently built a fail-closed-with-a-floor alternative for
  `orchestrator._over_daily_cap` (process-local counter, generous fixed
  floor instead of the admin-configured cap, since `get_app_settings()`
  could itself be degraded) — discarded during reconciliation in favor of
  AI1's already-merged, deliberate "leave it alone, don't change an accepted
  trade-off silently" call, not because the alternative was wrong. Logged
  here rather than dropped: worth an explicit product/eng decision on
  whether a bounded fail-closed floor should replace the current fail-open
  policy, same treatment AI1 itself already got. Also flagging the sibling
  fail-open gap the same investigation found in `mcp_server.py`'s
  `_over_mcp_daily_cap` (same pattern, separate `/mcp` surface) — not yet
  tracked anywhere.
- [x] **AI2. Assistant output is persisted un-scrubbed** — only the user
  message passes `scrub_pii` (`orchestrator.py:145`); assistant text is
  streamed and stored raw in `ai_messages`, asymmetric with
  `conversations.py`'s stated contract and Sentry's strict scrubbing.
  Done (2026-07-30, PR #2914): `orchestrator.py` now scrubs `final_text`
  into `stored_text` via `scrub_pii(..., keep_trip_pins=True)` before
  `append_message`/`store_cached` — the live SSE stream to the client is
  unaffected, only the persisted/cached copy changes. Regression test
  `test_assistant_text_is_pii_scrubbed_before_persistence` in
  `test_ai_orchestrator.py`. See
  `docs/change-log/2026-07-30-ai-assistant-output-pii-scrub.md`.
- [x] **AI3. No cap on parallel tool calls per iteration** —
  `orchestrator.py` gathers all requested calls unbounded (6 iterations ×
  N calls, each able to hit Google Maps). Cap per-iteration fan-out (e.g. 5).
  Done (2026-08-01): `MAX_TOOL_CALLS_PER_ITERATION = 5` in `orchestrator.py`;
  excess calls in a turn get a synthetic budget-exceeded tool_result (not
  dropped) and a `logger.warning` + `spinr_ai_tool_calls_capped_total` metric.
  See `docs/change-log/2026-08-01-ai3-tool-call-cap.md`.
- [x] **AI4. `scheduled_time` reaches the proposal unvalidated** —
  `tools_booking.py` accepts any ≤80-char string; a hallucinated/past ISO
  time renders on the card and only fails at Confirm. Validate ISO-8601 +
  ≥5-min lead at proposal time. Done (2026-08-01) — branch
  `claude/ai4-validate-scheduled-time`: `propose_ride_booking` now parses
  `scheduled_time` with `datetime.fromisoformat` and requires ≥5-min lead
  before building the card, returning a structured `{"error": ...}` result
  on failure (same shape as the existing out-of-area refusal); the
  Confirm-time validator in `schemas.py` is unchanged (defense in depth).
- [x] **AI5. `find_place` offers out-of-service-area street addresses** —
  done: chose **visibly mark** over **filter** — a hard filter risked
  silently returning zero results for a legitimate numbered address just
  outside the boundary (unlike a named-place search, a street address
  usually has no in-area fallback candidate to substitute), so the
  candidate stays in the result but `find_place` now checks
  `best.get("in_service_area")` for street-address-shaped queries and, when
  false, sets `result["out_of_service_area"] = True` plus an explicit note
  telling the model not to quote/book it and to tell the rider it's outside
  coverage — same warning-note pattern already used right above it for the
  imprecise-address case. 3 new tests in `tests/test_ai_tools_booking.py::TestFindPlace`
  (out-of-area street address marked but not dropped, in-area street
  address unflagged, named-place search keeps its pre-existing hard
  filter unchanged). Full `test_ai_tools_booking.py` suite (97 tests)
  passes.
- [x] **AI6. No handling for pasted Google Maps URLs / raw coordinates** —
  done, two independent fixes: (1) raw coordinates — did NOT extend
  `keep_trip_pins`'s bracketed-pin exemption to bare/unbracketed
  coordinates (that would redefine what `keep_trip_pins` means — currently
  documented as app-generated pins specifically — a bigger privacy-tradeoff
  decision than this item asked for); instead added a `GROUND RULES` line
  in `ai/prompts.py` telling the model the literal `[COORDS]` token means
  the rider pasted coordinates that were removed before it ever saw them,
  and to ask for the address or offer `request_map_pin`. (2) Maps URLs —
  new `_looks_like_a_url` regex guard in `tools_booking.py::find_place`
  (checks for `http(s)://`, `goo.gl/`, `maps.app.goo.gl`, or
  `google.<tld>/maps`) short-circuits with a clear note *before* any Maps
  API call — defense-in-depth in case the model ignores the matching new
  prompt rule (also added) telling it not to pass a pasted link to
  `find_place`. 6 new tests in `tests/test_ai_tools_booking.py::TestFindPlace`
  (4 URL shapes rejected with zero HTTP calls made, an ordinary query
  unaffected). Full `test_ai_tools_booking.py`/`test_ai_orchestrator.py`
  (127 tests) pass. Did not attempt URL-unshortening/parsing to actually
  resolve a Maps link's destination — out of scope for "lightweight";
  the fix makes the assistant ask the rider instead of silently mishandling
  it, not resolve the link itself.
- [x] **AI7. Multilingual gap** — done, scoped to the reply-language half
  only: new `STYLE` rule in both `_RIDER_CORE` and `_DRIVER_CORE`
  (`ai/prompts.py`) telling the model to mirror the rider/driver's own
  language for every reply, not just the first one, and never switch on its
  own. **Deliberately left unchanged:** the hardcoded `language: "en"` /
  `"languageCode": "en"` params on the four Maps API call sites
  (`tools_booking.py` geocode calls, `google_places_new.py`'s Places API
  (New) requests) — those control what language Google returns *address
  text* in (e.g. street/place names), not what language the assistant
  replies in; Saskatchewan addresses/street names aren't meaningfully
  "translated" the way conversational text is (same convention as Google
  Maps itself, which shows local-language street names regardless of app
  UI language), and re-plumbing a `language` parameter through 4 call sites
  for uncertain benefit is a bigger, separate change than this item's
  "lightweight" framing. FAQ keyword matching stays English-only too, per
  the item's own framing — translating FAQ content is a data/content
  problem, not a prompt fix. New `test_prompts_mirror_the_users_language`
  in `tests/test_ai_tools_booking.py` pins both personas' new rule text.
  Full `test_ai_tools_booking.py`/`test_ai_orchestrator.py` (128 tests)
  pass. **Not verified:** no live LLM call was made to confirm the model
  actually follows this instruction in practice (e.g. correctly detects
  and sustains French across a multi-turn conversation) — this pins the
  prompt text only, the same level of verification every other prompt-rule
  fix in this backlog has (prompt content isn't executable, so there's no
  unit-testable "does the model comply" assertion).
- [x] **AI8. Stale action cards never expire client-side** — done: a card
  is now "live" only while no USER message has been sent after it — the
  moment the rider sends any new message, every earlier
  `fare_quote`/`location_suggestions`/`map_picker` card becomes visibly
  dimmed (opacity 0.45) and its `TouchableOpacity`(s) `disabled`, with an
  italic "The conversation has moved on — ask again if you still need
  this." note. New pure helper `rider-app/utils/staleAiCard.ts::lastUserMessageIndex`
  (mirrors the existing `activeRideRoute.ts` pattern: small, focused,
  independently testable) computes the boundary once per render via
  `useMemo`; `renderMessage` compares each item's FlatList `index` against
  it. **Deliberately excluded:** `support_action` (an older "Contact
  support"/cancel-ride link is still a valid way to reach support — no
  staleness risk in the same sense) and `booking_proposal` (the item's own
  text names "quote/suggestion/map-pin" specifically, not the proposal
  card; the real safety boundary for that one is server-side
  re-validation at `POST /rides`, a separate, higher-risk change out of
  scope here). Prop threading: `FareQuoteCard`/`LocationSuggestionsCard`
  gained a `disabled?: boolean` prop each. **Verification actually
  performed** (more than prior frontend fixes in this batch): ran the
  project's own `tsc --noEmit` (0 new errors — the only 7 project-wide
  errors are a pre-existing, unrelated `expo-router/react-navigation`
  missing-module issue also affecting one existing test suite) and the
  full `yarn jest` suite (434/434 individual tests pass; the one failing
  *suite* fails to even load, same pre-existing missing-module issue, not
  a regression) — plus a new dedicated `staleAiCard.test.ts` (4 cases:
  empty conversation, no user message yet, single user message, several
  turns with the boundary comparison itself pinned). **Not verified:** no
  running dev server / simulator — the visual dimming and disabled-tap
  behavior were reasoned through and type/logic-tested, not seen rendered.
- [x] **AI9. Admin AI console quote-card tap still prose-only** — same
  defect class fixed for suggestions;
  `admin-dashboard/.../ai-console/page.tsx:125-131` drops `[lat,lng]` and
  vehicle id. Move `buildQuoteBookingMessage` into
  `shared/utils/aiLocationMessages.ts` (re-export shim in
  `rider-app/components/bookingProposal.ts`) and use it in the console.
  Done (2026-08-01) — branch `claude/ai9-admin-quote-card-coords`:
  `buildQuoteBookingMessage` moved into `shared/utils/aiLocationMessages.ts`;
  `rider-app/components/bookingProposal.ts` re-exports it (rider call site
  unchanged); the admin console's quote-card `onClick` now calls the shared
  builder instead of its own prose-only template. See
  `docs/change-log/2026-08-01-ai9-admin-quote-card-coords.md`.
- [x] **AI10. No conversation-level concurrency lock server-side** — done:
  `orchestrator.py::run_chat_turn` is now a thin locking wrapper
  (`ai:conv_lock:{conversation_id}`, `redis_set_nx`/`redis_delete`, 90s TTL
  — a generous ceiling for a full multi-iteration tool-calling turn) around
  the renamed original implementation, `_run_chat_turn` (both
  `routes/ai.py` and `routes/admin/ai_console.py` still import the public
  `run_chat_turn` name unchanged). A second turn on the same
  `conversation_id` while the first is still in flight gets a clean
  `conversation_busy` error frame instead of silently racing; a brand-new
  conversation (`conversation_id is None`) has no shared id yet to race on,
  so it skips the lock entirely — verified by
  `test_new_conversation_skips_the_lock` asserting `redis_set_nx` is never
  called on that path. New `TestConversationLock` class in
  `tests/test_ai_orchestrator.py` (3 tests): a real interleaved-concurrency
  test using a blocking fake adapter + `asyncio.Event` handshake (not just
  a unit test of the lock function in isolation) confirms the second
  concurrent call is rejected while the first completes normally; a
  sequential-reuse test confirms the lock is released after completion, not
  left stale; the skip-lock test above. Full existing
  `test_ai_orchestrator.py`/`test_ai_pii.py`/`test_ai_admin_console.py`/
  `test_ai_tools_booking.py` suites (196 tests) still pass unchanged. **Not
  yet verified:** behavior against a real Redis instance under actual
  network-level concurrency — the concurrency test exercises the in-process
  fallback store (`REDIS_URL` unset in the test env, per this repo's
  documented Redis-transparency convention), not the real `SET NX EX`
  round-trip against Redis itself.
- [x] **AI11. Cancel-ride escalation UX** — done: added one new
  `escalate_to_support` category, `cancel_ride` (not a separate `ride_issue`
  category too — the item's concretely-described gap was specifically
  cancellation requests getting a generic ticket instead of a self-serve
  deep link; a broader `ride_issue` category wasn't scoped by this item and
  wasn't added to avoid inventing an undefined feature). Backend
  (`ai/tools_support.py`): `cancel_ride` maps to `/ride-status` in
  `_CATEGORY_LINKS` and gets its own response message ("You can cancel from
  your ride screen — tap below to go there.") instead of the default
  "handoff to human support" phrasing, which was actively misleading for a
  self-serve action. `ai/prompts.py`'s rider-only cancel-refusal rule now
  tells the model to use `category="cancel_ride"` for cancel requests
  specifically. Frontend (`rider-app/app/ai-assistant.tsx`): the
  `open_support` action card, on a `cancel_ride` category, now resolves the
  actual ride-owning screen via the same `activeRideRouteFor(status)`
  resolver the screen's own header back-button already uses (searching/
  driver_assigned/driver_accepted → `/driver-arriving`, driver_arrived →
  `/driver-arrived`, in_progress → `/ride-in-progress`), falling back to the
  backend's static `/ride-status` link only if there's no live ride state to
  resolve from (e.g. the ride already ended by the time the rider taps it) —
  reusing the existing resolver instead of hardcoding a second, possibly-
  inconsistent path. 6 new backend unit tests
  (`tests/test_ai_tools_support.py::TestEscalation::test_cancel_ride_links_to_ride_status_not_a_ticket_message`
  plus the existing suite unaffected); full
  `test_ai_tools_support.py`/`test_ai_orchestrator.py`/`test_ai_pii.py`/
  `test_ai_tools_booking.py`/`test_ai_admin_console.py` (232 tests) pass.
  **Not verified:** the `rider-app/app/ai-assistant.tsx` frontend change has
  no dedicated component test (none existed for this screen before this
  change either) and was not visually tested in a running app/simulator —
  reasoned through by reading the existing `activeRideRouteFor` +
  `useRideStore` usage pattern already in the same file (the header
  back-button handler), not executed. Flagging per this repo's own
  "state what was NOT verified" convention, not silently claiming full
  coverage.
- [x] **AI12. Admin console endpoint has no rate limiter and a stale
  docstring** — `routes/admin/ai_console.py` claims turns count against the
  daily cap; the orchestrator deliberately exempts them, and the endpoint has
  no `@ai_chat_limit` equivalent (super-admin-only + audited, so low risk).
  Done (2026-08-01): docstring now states the actual exemption (orchestrator's
  `_over_daily_cap`, gated on `admin_actor_id is None`); added
  `admin_ai_console_limit` (20/minute, matches `admin_ai_suggest_limit`'s
  admin+paid-LLM precedent) as a defensive ceiling on `POST /admin/ai/chat`.
  See `docs/change-log/2026-08-01-ai12-admin-console-rate-limit.md`.
- [ ] **AI14. Accepted risk: a tapped suggestion is trusted even when its
  geocode is only APPROXIMATE** — prompt rule 6b (PR #2774) treats any
  rider-tapped `location_suggestions` candidate as confirmed, so a numbered
  street address Google could only resolve to a street/neighbourhood centroid
  can be quoted and booked at that centroid rather than the building.
  `_dropoff_pair_refusal` does not catch this (it is a label-vs-pin
  *consistency* check biased near the passed pin, not a precision check).
  **This is deliberate**, not an oversight: the alternative — routing
  imprecise taps through `request_map_pin` — was considered and rejected for
  this iteration because it adds a step for every rider and degrades to a
  dead end on clients that don't advertise the `map_pin` capability
  (`tools_booking.py` returns `shown: False` there), which is the exact
  no-exit state that produced the original infinite loop. Raised by Codex
  review on PR #2774 (`backend/ai/prompts.py:96`).
  **Middle ground if this is revisited:** have `find_place` surface
  `precise=False` on the card and let the assistant quote immediately while
  offering the map pin as an optional refinement (the "quote + note" option),
  rather than gating the quote.
- [x] **AI13. No output-side leakage filter** — done: new
  `backend/ai/pii.py::filter_tool_leakage` regexes for snake_case-shaped
  tokens (`\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b`) generally, not just the
  current tool registry, so the backstop stays structural against a
  hallucinated or future internal-identifier name too. Wired into
  `orchestrator.py`'s existing `stored_text = scrub_pii(...)` line
  (`stored_text = filter_tool_leakage(scrub_pii(...))`), the same call site
  AI2's PII scrub already uses — **same scope decision as AI2**: this
  filters the persisted/replayed copy (`ai_messages`, the FAQ
  cross-user cache) only. The raw text has already streamed to the client
  live by the time this runs (SSE token-by-token), matching the existing,
  already-documented convention at that exact call site ("the raw text has
  already streamed to the client this turn... only stored/replayed copies
  change") — a true live-stream filter would need per-token buffering,
  which is an architectural change this item's own "lightweight" framing
  didn't ask for and wasn't attempted. 6 new unit tests in
  `tests/test_ai_pii.py::TestFilterToolLeakage` (registered + hypothetical
  future tool names, normal prose unaffected, multi-leak, idempotent, no
  cross-contamination with `scrub_pii`'s own uppercase placeholder tokens)
  plus 1 orchestrator-level regression test
  (`test_assistant_text_has_tool_leakage_filtered_before_persistence`)
  pinning the real call site the same way AI2's own regression test does.
- [x] **AI15. `backend/ai/pii.py` had no card-number or government-ID/SIN
  scrubbing** — `scrub_pii()` covered phones, emails, GPS coordinates, and
  postal codes but had zero regex coverage for payment card numbers, and zero
  coverage (with no documented mitigation, unlike the "names" gap) for
  government ID/SIN numbers. Both are explicit `CLAUDE.md` PIPEDA ban-list
  items; either would have reached a third-party LLM provider and
  `ai_messages` persistence unredacted if a rider pasted one into AI chat or
  a support ticket. Found by the newly-added `spinr-ai-guardrail-reviewer`
  agent's first real end-to-end `/ai-check` run, auditing `pii.py` itself.
  Done (2026-08-01, PR #3133): added a card-number pattern gated on
  recognized card-network IIN prefixes (Visa, Mastercard, Amex, Discover —
  same prefix-discriminator principle this file already used for phone
  numbers) and a grouped 3-3-3 SIN pattern (bare ungrouped 9 digits
  deliberately not matched — no reliable discriminator, would repeat this
  file's own documented timestamp-collision regression). Driver's license
  numbers remain out of scope (no fixed cross-provincial format); mitigated
  via data-minimization same as names. Also synced
  `backend/utils/log_guard.py`'s `_SCREEN` cheap pre-filter, without which
  the new patterns would exist in `pii.py` but never fire on the loguru sink
  path. Verified with a full `pytest` run post-merge (76/76 passing in
  `test_ai_pii.py` + `test_log_guard.py`, including 16 new tests). See
  `docs/change-log/2026-08-01-ai-pii-card-govid-coverage.md`.

- [x] **D1. PostGIS surge query** — stale, already substantially done by
  another session before this pass. `utils/surge_engine.py` already: (1)
  raised the fetch cap 500 → 5000 and made truncation loud (metric +
  warning) so a hit can never silently mis-price surge; (2) implemented
  `_count_supply_spatial()` — the actual PostGIS `ST_Covers` + GIST-index
  server-side count this item asked for (migration 170); (3) gated it
  behind `SURGE_SPATIAL_COUNT` (env flag, off by default, fallback-safe on
  any error) exactly per CLAUDE.md's "ship dark, verify in staging, then
  flip on" convention for anything touching a live-tested, money-adjacent
  path. **Deliberately not flipped on here**: turning the flag on is an
  ops decision gated on an actual staging rehearsal this session cannot
  perform (no staging environment — see C1/E1), not an engineering task
  left undone. No code change needed; correcting the stale item.
- [ ] **D2. Distributed tracing** — request-ID propagation exists (`X-Request-ID`);
  full OpenTelemetry only if multi-replica latency debugging becomes painful.
- [x] **D3. Driver destination mode** — backend was already fully built
  (`backend/routes/drivers/profile.py` L428-497: `POST/GET/DELETE
  /drivers/destination`; `backend/services/dispatch_service.py` L151-209
  already filters ride offers by `destination_mode`/`destination_lat`/
  `destination_lng`, gated no-op when off). The only real gap was the
  driver-app UI — added `driver-app/app/driver/destination-mode.tsx`, a
  new settings screen (status card, address input, activate/update/clear
  actions) reusing `addresses.tsx`'s geocode-on-save pattern (Places
  autocomplete+details via the backend proxy, not direct Google calls) to
  turn a free-text address into lat/lng before `POST /drivers/destination`.
  Linked from Settings → Account. i18n strings added to en/fr/es (no
  fallback-to-English exists in this app's `t()`, so all three needed
  entries to avoid a raw key showing in fr/es UI). Not run against a
  simulator/device and no `tsc`/`yarn jest` pass performed for this
  change, per the standing no-test-suite instruction — deferred to the
  end-of-batch verification pass.
- [x] **D4. Driver heatmap UI** — stale, already done. Backend
  `GET /drivers/demand-heatmap` (`backend/routes/drivers/profile.py` ~L235-260)
  is gated on the `service_area.show_demand_heatmap` admin setting and returns
  the heatmap cells. Driver-app already consumes it: `driver-app/app/driver/
  (tabs)/index.tsx` renders a `react-native-maps` `Heatmap` component fed from
  this endpoint. No code change needed; correcting the stale item.
- [ ] **D5. In-app VoIP calls** — Twilio Proxy PSTN masking already covers the need;
  VoIP is a cost/quality upgrade.
- [x] **D8. No rate limiting on SIN-touching admin endpoints** — done: added
  4 new `default_limiter.limit(...)` entries in `utils/rate_limiter.py` —
  `admin_sin_reveal_limit` (10/hour) on `POST /admin/drivers/{id}/reveal-sin`,
  `admin_sin_update_limit` (10/hour) on `POST /admin/drivers/{id}/update-sin`,
  `tax_id_import_validate_limit` (30/hour) on `POST /admin/tax-ids/import/validate`,
  `tax_id_import_commit_limit` (10/hour) on `POST /admin/tax-ids/import/commit`.
  10/hour for reveal/update is D8's own suggested figure. The two import
  endpoints reuse the validate/commit asymmetry already established by
  `data_transfer_import_*_limit`/`booking_import_*_limit`/`driver_import_commit_limit`
  (read-only dry-run gets the looser 30/hour, the write path gets 10/hour) —
  the per-call `MAX_ROWS` (500) cap already bounds blast radius per call, so
  the per-hour cap only needs to guard unbounded scripted looping. All 4 are
  keyed per-admin via `get_user_or_ip_key` (existing function, unmodified —
  decodes the JWT's `user_id` claim without verifying the signature; safe
  because `Depends(get_admin_user)` still re-verifies before any handler body
  runs), not per-IP like every other `admin_*` limiter in the file — a
  deliberate deviation, since IP keying would let multiple super_admins
  behind one office/VPN egress IP share (and exhaust) one bucket, or
  under-count a single admin who rotates IPs. Confirmed admin JWTs carry
  `user_id` (`routes/admin/auth.py::_mint_admin_access_token`), so no new key
  function was needed. Both `reveal-sin`/`update-sin`
  (`routes/admin/drivers.py`) and the two `tax-ids/import` endpoints
  (`routes/admin/tax_id_import.py`) needed a `request: Request` parameter
  added — `AsyncLimiter.limit` requires one to find the connection to key on,
  and none of the 4 endpoints previously took one. Still purely
  defense-in-depth per the audit's own framing: all 4 stay super_admin-gated
  + audit-logged before the limiter ever runs. New tests in
  `tests/test_admin_sin_rate_limiting.py` (5 cases) prove the actual
  `AsyncLimiter`/`MemoryStorage` mechanics at each configured rate — N
  allowed calls succeed, the N+1th raises `RateLimitExceeded` — plus one case
  proving two different admin JWTs from the same source IP get independent
  buckets (the per-admin-not-per-IP guarantee this item exists for). Grepped
  every other consumer of `get_user_or_ip_key`/`default_limiter`: both are
  unmodified, and only new module-level limiter objects were added — no
  shared behavior changed for any of the ~30 other `default_limiter.limit(...)`
  call sites across `routes/`.
- [ ] **D6. Read-only root filesystem** — blocked on host migration off Railway.
- [x] **D7. Admin analytics Redis cache** — done: `GET /admin/analytics/cancellation-reasons`
  now caches its response for 5 minutes (`_OVERVIEW_CACHE_TTL`, same TTL
  constant `/overview` already used — reused rather than duplicated), exact
  same `redis_get`/corrupt-cache-fallthrough/`redis_set`-with-fail-open
  pattern as the existing `/overview` endpoint (F-50). Cache key includes
  both `date_range` and `service_area_id` (`analytics:cancellation-reasons:{date_range}:{service_area_id or 'all'}`)
  so two different service-area filters don't collide on one cached entry.
  Updated the 3 existing tests in
  `tests/test_admin_analytics_coverage.py::TestCancellationReasons` to
  explicitly patch `redis_get`/`redis_set` (matching `TestAnalyticsOverview`'s
  own established convention) — without this, the module-level in-memory
  redis fallback (no `REDIS_URL` in the test env) would have let one test's
  cached result leak into a later test with the same default-params cache
  key and silently skip exercising its mocked RPC path, the same class of
  test-pollution bug already tracked as A8. Added 4 new tests mirroring
  `TestAnalyticsOverview`'s own cache-specific cases (cache hit skips the
  RPC entirely, corrupt cache falls through, different `service_area_id`
  values get different cache keys, a `redis_set` failure doesn't turn a
  200 into a 500). **Verification deferred to the end-of-batch run.**
- [x] **D8. Payment-retry admin alert via WS broadcast** — stale, already
  done by another session before this pass. `utils/payment_retry.py::_alert_admins_payment_exhausted`
  already calls `manager.broadcast_to_admins({...})` once for the real-time
  in-dashboard WS alert — the exact fix this item asked for. The remaining
  per-admin loop right below it is a **different, legitimately separate**
  channel: native mobile push notifications (`send_push_notification`) to
  reach admins who don't have the dashboard open, not a second redundant WS
  mechanism. FCM/APNs push delivery is inherently per-device-token, so that
  loop isn't the same kind of "one broadcast call replaces N per-admin
  calls" optimization WS pub/sub allows — collapsing it further (e.g. an
  FCM multicast batch call) would be a different, separate item, not what
  this one's own text asked for. No code change needed.
- [x] **D9. `compliance_export_events` has no purge job for its claimed 7-year
  retention** — done: new migration
  `285_retention_purge_compliance_export_events.sql`. Found the real blocker
  while implementing: migration 263's own `compliance_export_events_no_mutate`
  trigger blocks DELETE **unconditionally** (no session-flag bypass, unlike
  `audit_logs`'s equivalent trigger) — so a purge job literally could not
  have deleted a row even if one had been written, regardless of the
  migration comment's "7-year retention" claim. Fixed by mirroring migration
  56's exact `audit_logs` pattern: `_compliance_export_events_immutable()`
  now gates DELETE behind a new session-local flag
  (`spinr.compliance_export_events.allow_delete`) instead of blocking it
  outright — UPDATE stays unconditionally blocked, unchanged. Forked
  `purge_pii_retention()` verbatim from migration 228 (the current
  authoritative definition, confirmed via `grep` across all migrations —
  no later one replaces it) and added Step M: deletes
  `compliance_export_events` rows older than 7 years, setting/clearing the
  flag immediately around the DELETE (including on exception), exactly
  mirroring Step G's `audit_logs` handling. `utils/retention_purge.py`
  needed **no Python change** — it already logs whatever keys
  `purge_pii_retention()` returns generically; verified via
  `_split_sql_statements` (the B0 fix) that the new migration parses into 7
  clean top-level statements with no CONCURRENTLY. No dedicated Python test
  added: `tests/test_retention_purge.py`'s own docstring states this
  function is "exercised via migration + integration tests" — the Python
  suite only pins the generic RPC-response-passthrough wrapper, which is
  unchanged by this migration, matching the existing convention for every
  prior Step (A through L) added by migrations 56/117/141/143/216/228.
  **Verification deferred to the end-of-batch run** — like every SQL-only
  migration in this backlog, this needs an actual Postgres apply to fully
  confirm, not just static parsing.
- [x] **D10. `compliance_export_events` rollback command not re-verified
  against real staging** — `DROP TABLE IF EXISTS compliance_export_events;`
  (the migration's documented rollback) was verified by applying the
  migration to the local dev clone and dropping it there, never against the
  real staging Supabase project (`spinrmobileapp`) the migration was
  actually applied to during PR #2675's smoke test. **Status:** accepted as
  sufficient, not re-verified — per the audit's own framing (gap G10), this
  is optional given the table currently holds zero real rows in staging
  (confirmed during that same smoke test), so a `DROP TABLE IF EXISTS` on an
  empty table is exceedingly unlikely to behave differently in staging than
  locally. Re-running a destructive `DROP TABLE` against the shared staging
  project purely to prove a rollback command — without a concrete need to
  actually roll back — is not worth the risk for a statement this
  well-supported already; CLAUDE.md's guidance on destructive/hard-to-reverse
  actions favors skipping an unnecessary one over running it "just to be
  sure." Revisit if the table ever holds real data before this is re-verified.

## P4 — Industry-parity good-to-haves (verified missing 2026-06-09)

_Not launch-gating, but every mature platform at this stage has them. Ordered by
how much they de-risk a public launch._

- [ ] **E1. Staging environment** — deploys currently go `main` → production
  (Fly + Railway) with no intermediate environment. Stand up a staging Fly app +
  throwaway Supabase project with synthetic data; point a `staging` branch or
  manual workflow at it. Prereq for E2, E4, and safe migration rehearsal.
- [ ] **E2. Marketplace load/simulation testing** — harness BUILT on branch
  `claude/eager-franklin-69ta0w` (`loadtest/locustfile.py` + runbook with
  breaking-point register): rider+driver bots, real dispatch matchmaking, WS
  GPS pings, SLA gates from the CLAUDE.md table. **Execution still open** —
  blocked on E1 (no staging env). First run: seed bot accounts per
  `loadtest/README.md`, run the ramp scenario, record the breaking point.
- [x] **E3. Forced-upgrade gate for mobile apps** — done. `app_settings` gained
  `min_rider_app_version`/`min_driver_app_version` (empty = off, `schemas.py` +
  `routes/admin/settings.py` semver-pattern-validated). New
  `ForcedUpgradeMiddleware` (`core/middleware.py`) reads `X-App-Version`/
  `X-App-Platform` and returns 426 `upgrade_required` when a client is below
  the configured floor — soft-fails open on missing/unparseable headers or an
  unset minimum, mounted unconditionally (no ENV branch needed). Shared API
  client (`shared/api/client.ts`) gained `setAppIdentity()` (sends the two
  headers on every call) and `onForceUpgrade()` (fires on 426, mirrors the
  existing `setSignOutCallback` pattern). Both apps call `setAppIdentity()` at
  module load with `Constants.nativeApplicationVersion` and mount a new shared
  `ForceUpdateOverlay` (full-screen, non-dismissible) at their root, driven by
  `onForceUpgrade()`. Store links reuse the placeholder App Store/Play Store
  IDs already in `rider-app/app/become-driver.tsx`. Ships fully dark today —
  zero effect until an admin sets a non-empty minimum. Not run against a
  simulator/device, no `tsc`/`yarn jest`/pytest pass performed, per the
  standing no-test-suite instruction — deferred to the end-of-batch
  verification pass.
- [ ] **E4. Synthetic monitoring + SLO alerting** — nothing external probes the
  platform; a total outage is currently discovered by users. Add an external
  monitor (Checkly/UptimeRobot/Grafana synthetic) hitting `/health`, auth, and
  fare-estimate every minute from outside, alerting to PagerDuty. Tie alert
  thresholds to the CLAUDE.md SLA table (SLO + error budget).
- [x] **E5. Kill switches / feature flags** — CLOSED (2026-08-11). Correction
  found while scoping this: the "no documented kill switches" premise was only
  3/4 true — `scheduled_dispatch_enabled` already existed and gated
  `utils/scheduled_rides.py`'s loop (shipped 2026-08-02), it just had no admin
  API field or dashboard toggle. Built the other 3 from scratch, following
  that flag's own template (schema default `True`, fail-open on a
  settings-read error, one `app_settings` flag per subsystem — not per loop):
  - `surge_engine_enabled` — gates `utils/surge_engine.py`'s automatic
    recompute cycle only, layered on top of (not replacing) the existing
    per-service-area `surge_source`/`surge_enabled` controls. Off freezes
    multipliers at their last value — does not reset live pricing; pair with
    the existing per-area manual override for that.
  - `promo_redemption_enabled` — gates the single shared validation
    chokepoint (`routes/promotions.py::_validate_promo_for_user`) both the
    rider self-service and admin apply-on-behalf-of-rider paths already
    funnel through — one flag, one check, covers both.
  - `corporate_billing_enabled` — gates the automatic money-movement paths
    only: `services/payment_service.py::settle_corporate` and the 4
    corporate background loops (autotopup, low-balance, allowance reset, KYB
    reverification — the last of which already had its own specific toggle,
    now layered under this master one, same relationship as surge). Does
    **not** gate `services/corporate_wallet_service.py`'s low-level
    apply_topup/apply_adjustment/apply_refund helpers directly — those are
    also how an admin manually corrects/refunds something during the very
    incident that caused this switch to flip off.
  - Admin API gap fix: `scheduled_dispatch_enabled` was never added to
    `SettingsUpdateRequest` — there was previously no way to set it via the
    admin API at all, only a direct DB update. Fixed alongside the 3 new
    flags.
  - Admin dashboard: new "Kill Switches" card on the Settings page's
    Operations tab (all 4 toggles, help text stating what each pauses and
    the two scope-boundary notes above).
  - Discovered mid-implementation, worth recording: a formatter hook in this
    repo strips additions to some files' module-level except-branch import
    lists (hit this directly in `routes/promotions.py`). Worked around it
    the same way `services/payment_service.py::_atomic_settle_enabled`
    already did — a lazy (function-local) dual import of `get_app_settings`
    — for every subtask after that point.
  - Tests: ~24 new across 7 backend test files (schema/PUT-handler
    round-trip, flag-off/missing-key/settings-error-fails-open per gated
    path). Full regression sweep across every touched subsystem's test
    files: 100% pass, no regressions. Admin dashboard: no existing test file
    for the settings page (checked first, not invented); verified with a
    real `npm run build` (exit 0), not just `tsc`/dev server.
  - **Not verified**: no live-Supabase/live-Redis integration test, no
    staging repro, no manual admin-dashboard click-through of the actual
    toggles (build-verified only), and none of the 4 flags have been
    flipped off anywhere — this ships as pure additive capability, same
    dark-launch posture as every other flag in this backlog.
- [ ] **E6. Pre-launch DAST + third-party pentest** — SAST/Semgrep run in CI, but
  nothing exercises the running app (OWASP ZAP baseline scan against staging on a
  schedule), and a payments+PII platform should have one external penetration
  test before public launch. Budget item; book it.
- [ ] **E7. Backup-restore drill** — `docs/runbooks/pitr-restore.md` exists but
  (like the failover runbook) has never been exercised. Restore a Supabase PITR
  snapshot into a scratch project, verify row counts + a sample ride lifecycle,
  record actual RTO in the runbook. A backup is only real after a restore.
- [ ] **E8. CODEOWNERS + review routing** — partially done. Added
  `.github/CODEOWNERS` routing payments/corporate/wallet/surge, migrations,
  auth/security-sensitive files, dispatch, and safety paths to distinct
  owner groups, bottom-up-specific per GitHub's matching rules. **Still
  blocked**: the owner handles are placeholders
  (`@spinr-org/TBD-payments-reviewers` etc.) — this session has no real
  GitHub org/team roster to assign, and GitHub CODEOWNERS entries must
  resolve to actual org members/teams to have any effect. Also still open:
  enabling "Require review from Code Owners" in branch protection, which
  needs GitHub repo-admin access this session doesn't have. File the real
  team slugs in once eng leadership assigns owners, then flip the branch
  protection setting.
- [x] **E9. Blameless postmortem template** — done: new
  `docs/templates/postmortem.md` (mirrors `docs/templates/CHANGE_IMPACT_LOG.md`'s
  style) — summary, timeline, impact, root cause via 5-whys, what went
  well/wrong, action items table (owner + due date required per row),
  lessons for the framework. Found while building it: the four existing
  incident runbooks each specified a **different output path/timing** for
  their own postmortems (`docs/audit/postmortem-YYYY-MM-DD-<slug>.md` @ 5
  business days in `data-breach.md`; `reports/postmortems/YYYY-MM-DD-slug.md`
  @ 72h in `incident-response.md`; `reports/incidents/YYYY-MM-DD-sos.md` @
  72h in `sos-incident.md`; no explicit path in `security-incident.md`) —
  deliberately did **not** unify these (an existing, possibly intentional
  per-incident-class convention, not something this item asked to change);
  the template's own "Where this gets saved" section documents all four
  paths side by side instead. All four runbooks
  (`docs/runbooks/data-breach.md` §7, `docs/incident-response.md`'s
  Post-Mortem section, `docs/runbooks/security-incident.md` §9 checklist,
  `docs/runbooks/sos-incident.md`'s Post-Incident checklist) now reference
  the shared template for structure while keeping their own path/timing.
  Docs-only change, no code/tests to run.
- [x] **E10. License compliance scan** — done, and found half of this was
  already stale: `pip-licenses` (Python deps) was **already wired into CI**
  as `security-gates.yml`'s `G7 · pip-licenses (Python deps)` job (denylist
  strategy: GPL/AGPL/SSPL/Elastic/Commons Clause/BUSL) — the item's own text
  implied both halves were missing, only the JS half actually was. New
  `G7b · license-checker (JS deps)` job added right after G7, mirroring the
  existing `G4b · yarn audit (JS deps)` matrix job's exact structure
  (`fail-fast: false` across `[rider-app, driver-app, admin-dashboard,
  shared]` so one module's failure doesn't mask the others' unknown state —
  same rationale, same historical incident class this repo already hit once
  on G4b). Scoped to `--production --excludePrivatePackages` (shipped
  surfaces only, per the item's own framing — not devDependencies), same
  denylist family as G7 (`GPL;AGPL;LGPL;SSPL;Elastic;Commons-Clause;BUSL`).
  **Verification deferred to the end-of-batch run** (this session's
  token-budget constraint) — the new job's YAML syntax was reviewed by eye
  against the G4b job it mirrors, but was not dry-run through `act` or an
  actual GitHub Actions run, and the current dependency trees across the
  four JS modules were not audited for a real copyleft/proprietary license
  that would fail the new gate on first run. If it does fail on first run,
  that's real signal the gate is working, not a bug in the job — resolve
  per-package (swap, pin an alternate version, or get a documented CR
  exception), don't loosen the denylist to make it pass.
- [x] **E11. a11y checks in CI** — stale, already done by another session
  before this pass. `admin-dashboard/e2e/crawl-audit.spec.ts` already runs
  `@axe-core/playwright`'s `AxeBuilder` against every crawled route, with a
  per-route baseline-ratchet in `e2e/a11y-baseline.json` (64 pre-existing
  violations across 41 routes tracked as debt, but any route regressing
  past its own baseline fails the E2E suite; a route with no baseline entry
  defaults to 0 tolerance) — the code's own comment already cites this
  exact item ("WCAG 2.1 AA a11y ratchet (ACTION_ITEMS.md E11)"). No code
  change needed; correcting the stale checkbox.
- [x] **E12. On-call & escalation policy doc** — done: new
  `docs/runbooks/on-call.md`. Found while writing it: this repo already had
  a substantial Severity Ladder + escalation flow + roles table inside
  `docs/incident-response.md` — the genuinely missing piece was "who" (a
  rotation roster) and a single page a newly-paged engineer can read
  standalone, not a from-scratch severity matrix. Explicitly reconciles the
  **two separate severity vocabularies already in this codebase** that the
  item's own "P0 vs P1" phrasing conflates: engineering-incident SEV-1..4
  (`docs/incident-response.md`) vs support-ticket P0..P3
  (`CLAUDE.md`'s KPI table) — states plainly that a P1 support ticket does
  not auto-page, only a SEV-1/SEV-2 does, plus a support→engineering
  escalation path for the case where a ticket turns out to be a live
  incident. Restates (does not redefine) the existing escalation ladder and
  response-time targets from `docs/incident-response.md`/
  `docs/runbooks/sos-incident.md`/`CLAUDE.md`, explicitly marked as
  secondary to those sources so they can't silently drift apart. **The
  rotation roster itself is left as an explicit fillable table (cadence,
  handoff time, PagerDuty schedule link, escalation-policy name), not
  invented** — no real names/schedule exist in this repo to draw from, and
  fabricating them would be actively misleading in an ops document. Linked
  from `docs/incident-response.md`'s Runbook Index. Docs-only, no code.

### C25. Accepted risk: `image-size` HIGH advisories (1138808, 1138809) blocking `G4b · yarn audit` for `rider-app`/`driver-app` — no upstream patch exists

- [ ] **Status:** open, accepted risk — not a bug to fix, a standing gate
  exception to re-check periodically. Filed via `[CR]` #3718
  (`.github/ISSUE_TEMPLATE/ci_change_request.yml`), CR-2026-008. This item
  is the ACTION_ITEMS.md record the CR itself calls for (issue step 3(b))
  since Yarn Classic (v1) has no built-in per-advisory allowlist for `yarn
  audit` — there is no `--exclude`/`--ignore <advisory-id>` flag, so the
  only documented-acceptance path available is recording it here rather
  than either silently leaving `G4b` red-and-unexplained or weakening the
  gate itself.
- **What's accepted:** two HIGH-severity `image-size` advisories —
  1138808 (ICNS parser infinite-loop DoS) and 1138809 (JXL/HEIF parser
  infinite-loop DoS) — in both `rider-app/` and `driver-app/`, pulled in
  transitively via `expo > @expo/cli > @expo/metro > metro > image-size`
  (and the `metro-config` variant of the same chain). This is the same
  finding B24 already flagged as "unpatchable, left as-is, documented"
  when it closed the rest of the JS `yarn audit`/`npm audit` gate on
  2026-08-11 — B24 named it but didn't carry its own tracking item; this
  is that item, now that a `[CR]` exists for it.
- **Why no fix exists:** `image-size`'s latest published version is
  `2.0.2`, which is itself listed as vulnerable
  (`vulnerable_versions: <=2.0.2`, `patched_versions: <0.0.0` — the
  advisory-database convention for "no fix published yet"). There is no
  newer release to bump to, no `resolutions`/override target that
  resolves it without either breaking the Metro bundler toolchain or
  pinning an untested pre-release, and `metro` itself pins the vulnerable
  version — un-pinning it is not something this repo controls.
- **Why the risk is low enough to accept rather than block on:**
  `image-size` is exercised by Metro at local/CI build time only (parsing
  asset files during bundling) — a devDependency-of-a-devDependency, never
  shipped in the rider-app/driver-app runtime bundle that reaches
  end-user devices. The DoS vector requires feeding a malicious
  ICNS/JXL/HEIF image into the bundler process itself, which is not an
  externally-reachable attack surface for Spinr's production rider/driver
  apps.
- **Re-verified 2026-08-17** (this CR's implementing PR) against
  `origin/main` — `yarn audit --level high --json` in both `rider-app/`
  and `driver-app/` confirms the finding is still exactly these two
  advisories on `image-size` and nothing else has regressed alongside
  them (both apps: `{module_name: image-size, ids: [1138808, 1138809]}`,
  no other packages present at HIGH+ severity).
- **Gate left as-is, on purpose:** `security-gates.yml`'s `G4b` step keeps
  `continue-on-error: false` — this CR is accept-and-document, not
  weaken-the-gate. `G4b` will stay red for `rider-app`/`driver-app` until
  upstream ships a fix; that red is now expected and explained, not a
  silent failure.
- **Re-check cadence:** a session-level Claude routine already does a
  weekly check on issue #3718 itself (the `[CR]` issue tracking this
  acceptance) — that routine is the re-check mechanism for this item, not
  a duplicate one. When `image-size` (or `metro`'s pin of it) ships a
  patched version, close out both #3718 and this item together, bump the
  dependency, and confirm `G4b` goes green on both apps.

### C26. `pip-compile drift check` fails on any PR that touches `backend/requirements.in` — `requirements.txt` has drifted far out of sync with a fresh resolve, unrelated to the touching PR's actual diff

- [ ] **Status:** open, not yet a `[CR]` issue — flagged here per the
  "CI check red for a reason unrelated to your diff" rule rather than
  left unexplained.
- **Where surfaced:** PR #4085 (A39's `migrate.py` reconciliation), whose
  only change to `backend/requirements.in` was a one-line comment fix
  (`migrate.py` → `run_migrations.py`, no dependency added/removed/
  reordered). CI's `pip-compile drift check` job still failed —
  `.github/workflows/pip-compile-check.yml` only triggers when
  `backend/requirements.in`/`requirements.txt` changes, so this drift has
  presumably existed unnoticed for a while and this PR was simply the
  first to touch the trigger path.
- **What the failure actually shows:** the job's diff is almost entirely
  `+` (additions) — a fresh `pip-compile requirements.in --no-upgrade`
  resolves dozens of transitive packages (`pydantic`, `pytest`, `stripe`,
  `supabase`, `uvicorn`, etc.) that the checked-in `requirements.txt`
  doesn't currently pin at all, not just version bumps. That's consistent
  with `requirements.txt` being stale relative to `requirements.in`
  rather than a resolver nondeterminism artifact.
- **Not fixed in PR #4085**: regenerating `requirements.txt` correctly
  requires running `pip-compile` and reviewing/testing the resulting
  pinned versions — not something to do as a drive-by inside a
  comment-only-diff PR. Attempting it blind risks silently changing pins
  the fare/payment/auth code depends on without any of this repo's
  version-bump review discipline (see the `dependabot` bump commits
  throughout this file for the normal, reviewed path).
- **Next step:** someone with a working `pip-compile` environment should
  run `pip-compile backend/requirements.in --output-file
  backend/requirements.txt --strip-extras --no-header
  --annotation-style line --no-upgrade`, review the diff for anything
  surprising (should be additions/reorderings only, given `--no-upgrade`),
  test the backend against the regenerated lockfile, and merge that as
  its own dependency-only PR — then this check goes green for any future
  PR that touches `requirements.in`.

### C27. `ci-error-audit.yml` has created 2,483 open issues since 2026-04-28 — no cross-run dedup, no auto-close

- [ ] **Status:** open. Filed via `[CR]` #4112
  (`.github/ISSUE_TEMPLATE/ci_change_request.yml`), CR-2026-(assign). Found
  2026-08-17 while auditing the repo's CR backlog for unclosed-but-resolved
  items (the search that also found and closed #3764/#3765).
- **Measured, not estimated:** 2,483 of the repo's 2,509 total open issues
  (~99%) carry the `ci-audit` label. Oldest is **#143, created
  2026-04-28** — continuous unbroken accumulation since the system
  shipped, ~22/day sustained.
- **Root cause**: `scripts/ci-audit/create_github_issue.py`'s own
  docstring states its actual dedup scope — *"De-duplicates: if an open
  issue for the same run already exists, updates it"* — keyed on **run
  ID**, not error signature. The same recurring failure across different
  runs opens a fresh issue every time. No companion workflow anywhere in
  `.github/workflows/` closes these when the underlying job later goes
  green — grepped, confirmed absent.
- **Concrete harm observed this session**: a plain issue-title search for
  other open `[CR]`s returned unusable noise — GitHub's semantic search
  matched "CR" against "CI" and surfaced a page of `[CI Audit]` issues
  instead. Same "signal drowns in noise, trains people to stop looking"
  failure shape as C7/C8/C9 (a permanently-red or silently-broken
  automation becomes the expected state, so nobody notices when something
  in it actually matters).
- **Not fixed here** — this is a decision-needing CR (approval gate,
  `.github/ISSUE_TEMPLATE/ci_change_request.yml`), not implemented
  unilaterally. See #4112 for the proposed fingerprint-based dedup fix,
  the separate (larger) question of one-time backlog cleanup, and the
  auto-close design tradeoffs.

## Recently completed (do not redo)

| Item | Where |
|---|---|
| `/auth/refresh` reported 30-day `access_expires_at` (real TTL 15 min) | `b5648ba` |
| SOS accepts expired-but-signature-valid token (`get_current_user_allow_expired`) | `rides.py:4178` |
| `confirm_payment` raw dict → Pydantic model | `routes/payments.py:303` |
| Dispatch pushes moved off the request path (<2s offer SLA) | `e9283fc` |
| Estimate polyline fetch overlapped with fare work (<300ms SLA) | `d322709` |
| Partial recency index for estimate driver page | `5788367` |
| Admin JWT error-detail leaks fixed + MFA challenge audience pinned | `e3281ed` |
| MFA enforced for all staff logins (`ADMIN_MFA_ENFORCED`, enroll-scoped token) | `test_admin_mfa_enforcement.py` |
| Super-admin MFA reset (lost phone) + staff-page UI | `884b091`, `664d195` |
| TOTP secrets / backup-code hashes stripped from staff list/get | `664d195` |
| Production boot without `SENTRY_DSN` logs unmissable ERROR | `server.py` |
| All 6 sprint P0s + P1/P2 audit findings | `.claude/context/sprint-current.md` |
| Data Transfer job-history endpoints (bulk_operations flag → super_admin, closed cross-admin PII exposure) | `88d9c51` (PR #2685) |
| Corporate allowance-cap race regression test (no test existed for the migration-258 double-spend fix) | `4257690` (PR #2686) |
| PIA for Data Transfer export path (none existed for this PII-moving flow) | `48d2d0f` (PR #2687) |
