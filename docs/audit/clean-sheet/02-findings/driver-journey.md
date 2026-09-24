# R5 — Driver Journey Owner — Findings

**Lane:** R5 (W1) · **Scope:** every driver story — signup → document verification →
go-online → offer → trip → earnings → payout → tax slip → appeal → deactivation.
**Repo:** `/home/user/spinrvm`, driver-app/, `backend/routes/drivers/`,
`backend/services/driver_*`, `backend/utils/{driver_*,insurance_periods,
presence_sweeper,document_expiry,auto_payout}.py`.

**Status:** COMPLETE — all sections (journey map, finding cards, scenario cards,
Rebuild Delta card, steelman, top 5, NOT verified, human-only questions, escalations)
filled. Published incrementally per orchestrator instruction (never held only in
context); this is the final state as of 2026-09-24.

**Public-repo notice:** this file is written for people who need to fix these gaps,
not to reproduce them. Where a weakness could be exploited (fraud, auth, money), the
finding names the mechanism and the fix, not click-by-click reproduction steps.

**driver-app has no visual-regression tooling** (confirmed: no Playwright/snapshot
config under `driver-app/`, unlike admin-dashboard's seeded 6-page suite). Every UI
claim in this file is **reasoned from code** (screen source, component props,
accessibility labels) — never "seen on a screen." Stated once here, not repeated per
finding.

**Lanes already covering adjacent ground — cited, not re-derived:**
- `02-findings/dispatch.md` (R8): DISPATCH-003 (`set_driver_available` read-then-write
  race), atomic availability RPC dark-flagged (`driver_availability_v2_enabled`).
- `02-findings/trust-safety-fraud.md` (R11): Period 2 fires at `driver_assigned`
  (`matching.py:1433`), `driver_insurance_periods` append-only trigger (migration 64),
  document-expiry gate on `go_online` is real.
- `02-findings/compliance.md` (R12): COMP-001 (Class 5 vs Class 4 licence discrepancy),
  COMP-002 (eligibility rechecks dark behind `enforce_driver_eligibility_recheck`,
  default `false`, and the v2 availability path skips them entirely), COMP-009
  (independent-contractor agreement has no e-signature flow).
- `02-findings/money-cra.md` (R9): MONEY-001 (raw-float tip sums in `earnings.py`),
  MONEY-010 (payout not capped by amount collected), MONEY-012 (T4A Box 020 + 048 both
  filled for GST-registered drivers).

---

## 0. Method

Three passes per area: **Steelman** (why built this way) → **Attack** (fraudulent
driver, driver claiming employee status, hostile device, SGI/regulator, plaintiff's
lawyer, competitor recruiting drivers) → **Rebuild**. Evidence labelled VERIFIED /
INFERRED / ASSUMED / UNKNOWN.

<!-- SECTION: journey-map -->
## 1. Journey map

Evidence label per row is for the "code exists and does what the row says," not for
every cell (a11y/tests are frequently INFERRED — see the note under the table).

| Phase | Screen(s) | Endpoint(s) | Four states (loading/empty/error/success)? | A11y? | Test coverage? | Insurance period logged? |
|---|---|---|---|---|---|---|
| Signup / vehicle info | `driver-app/app/become-driver.tsx`, `profile-setup.tsx`, `vehicle-info.tsx` | `POST /drivers` (auto-created on first field save, `routes/drivers/profile.py:187`), `PATCH` profile fields | INFERRED (both screens have dedicated test files — VERIFIED existence, not a full-state audit) | Not audited this pass (R17 territory) | `becomeDriverScreen.test.tsx`, `profileSetupScreen.test.tsx` (VERIFIED to exist) | N/A — Period 0, `status=pending` blocks Period 1+ |
| Document upload | `documents.tsx` | `backend/documents.py` upload endpoints | Not verified this pass | Not audited | Not verified this pass | N/A |
| Admin verification | (admin-dashboard, not this lane's file) | `POST /admin/drivers/{id}/action` (`routes/admin/drivers.py:1535`) — `approve`/`reject`/`suspend`/`ban`/`unban`/`reactivate`; reason **required** for reject/suspend/ban (VERIFIED, `docs/driver-lifecycle-status-flow.md` §Phase 2) | N/A (admin surface) | N/A | Covered per R7/admin lane | N/A |
| Go-online | `driver-app/app/driver/(tabs)/index.tsx` "Go Online" toggle | `POST /drivers/{id}/status` (`routes/drivers/status.py:336+`) — document-expiry gate is unconditional (VERIFIED, `status.py:520-676`); the SK-eligibility recheck (class/age/vehicle-age/3-yr-experience) is real code gated behind `enforce_driver_eligibility_recheck` (default `false` — see COMP-002 in `compliance.md`, VERIFIED) | Not audited this pass | `TestGoOnlineEligibilityRecheck` suite + 5 tests added with C63 (VERIFIED per `ACTION_ITEMS.md` C63) | Period 0→1 at successful go-online (`status.py:1262-1273`, `record_period_transition`) |
| Offer | `driver-app/components/panels/RideOfferPanel.tsx` (+ `lib/androidAuto/CarOfferPanel.tsx`) | WS/FCM offer payload built in `routes/rides/matching.py:1550-1590` | Loading/error/empty not applicable (ephemeral push UI); accept/decline both have `isLoading` guards (VERIFIED) | Not audited this pass | `RideOfferPanel.test.tsx` exists (VERIFIED); C37 (`ACTION_ITEMS.md`) records a leaked-timer flake in this same test file, since fixed | Period 1→2 at `driver_assigned`/live-offer (`matching.py:1433`, cited from `trust-safety-fraud.md`, VERIFIED) |
| En route / arrived / in trip | `ActiveRidePanel.tsx`, driver map screen (`driver/(tabs)/index.tsx`) | `routes/drivers/ride_flow.py` accept/arrive/start | `isLoading` threaded through every action button (VERIFIED, A7 four-states table) | Not audited this pass | Present (not enumerated exhaustively this pass) | Period 2 already open at assignment; Period 3 opens at trip start (`ride_flow.py:1239,1314`) |
| Completion | `driver-app/app/driver/ride-detail.tsx` | `routes/drivers/ride_complete.py` | Not verified this pass | Not audited | Not verified this pass | Period 3 closes on completion (via `release_driver_and_close_period`, `insurance_periods.py:272+`) |
| Earnings | `driver-app/app/driver/(tabs)/activity.tsx`, `components/activity/ActivityView.tsx` | `GET /drivers/earnings`, `/earnings/trips`, `/earnings/daily|weekly|monthly|comparison` | Loading + error states present on `driver/notifications.tsx`-adjacent screens per A7; activity screen not independently re-checked | Not audited | `test_reconcile_legacy_driver_earnings.py` (traceability.csv, VERIFIED to exist) | N/A |
| Payout | `driver-app/app/driver/payout.tsx`, `payout-history.tsx` | `GET /payouts/history`, `POST /payouts/instant`, weekly `auto_payout.py` loop | Not verified this pass | Not audited | Not verified this pass | N/A |
| Tax slip | `driver-app/app/driver/tax-documents.tsx` | T4A annual job (`utils/t4a_pdf.py`, `t4a_annual_job.py`) — see MONEY-012 in `money-cra.md` | Orphan per `traceability.csv` (`tax-documents.tsx` flagged `ORPHAN` — no story mapped) | Not audited | Not verified this pass | N/A |
| Appeal | `driver-app/app/appeal.tsx` | `GET/POST /drivers/appeals` (`routes/drivers/appeals.py`) | Loading + submit-disabled states present (VERIFIED, code read in full) | `accessibilityLabel`/`accessibilityRole` present on the primary CTA (VERIFIED, spot-check) | Not verified this pass | N/A |
| Deactivation | `driver-app/app/account-deactivated.tsx`, `reactivate-account.tsx` | Go-online 403 (`status.py:439-446`) routes here client-side | Loading spinner while appeal status resolves (VERIFIED) | Not audited | Not verified this pass | Pinned at Period 0 for any non-`active` status (`docs/driver-lifecycle-status-flow.md` §Phase 3) |

<!-- SECTION: findings -->
## 2. Finding cards

### DRIVER-001 — A driver is never told *why* they were suspended/banned, in-app, anywhere
- Hierarchy: L2 Driver Lifecycle › L3 Deactivation & appeal › L4 "driver sees a clear, specific reason" › L5 suspend/ban → appeal
- Severity: MEDIUM   Priority score: S×B×L = 2×3×2 = 12
- Status: VERIFIED   Existing item: new (grepped `ACTION_ITEMS.md` for "deactivat"/"appeal"/"suspension reason" — no hit describing this exact gap; `docs/legal/driver-deactivation-appeals-policy.md` describes the *process*, not this UI gap)
- Adversary: plaintiff's lawyer ("my client was banned with no explanation and no way to contest specifics"); driver claiming employee status (an opaque, unappealable-feeling disciplinary process is exactly the kind of "unilateral control" fact pattern misclassification claims point to)
- Evidence: admin action requires a `suspension_reason`/`ban_reason` (`backend/routes/admin/drivers.py:1535`, confirmed by `docs/driver-lifecycle-status-flow.md` §Phase 2 — "Reason **required**"). The go-online rejection the driver actually sees is a fixed, generic string: `backend/routes/drivers/status.py:439-446` — `"Your account has been permanently suspended due to policy violations."` / `"Your account is currently suspended. Please contact support."` — neither interpolates `ban_reason`/`suspension_reason`. `backend/services/driver_appeals.py`'s `create_appeal()` *does* capture `original_reason` server-side onto the appeal row (so the reason is stored, not lost), but `driver-app/app/appeal.tsx` never renders `appeal.original_reason` anywhere in its JSX (grepped the full file — zero references); the screen shows only the driver's own submitted `driver_message` and the admin's `admin_note` reply. `driver-app/app/account-deactivated.tsx` shows only a static title ("Account suspended"/"Account banned") with no reason text at all.
- What happens (plain language): a driver who gets suspended or banned sees "due to policy violations" or "contact support" and nothing more specific, anywhere in the app — not on the lockout screen, not on the appeal form, not in the appeal history. The actual reason an admin was required to type in exists in the database and is even attached to the driver's own appeal record, but no screen ever displays it back to them. A driver has to file an appeal blind, guessing what they're appealing.
- Root cause: the appeal screen's own code comment shows this was a staged rollout ("NOT yet surfaced automatically from the suspension/ban toast... flagged as a deliberate follow-up, not an oversight") — the reason-capture plumbing (`original_reason`) was built for this purpose and then the render step was never finished.
- Recommendation: render `appeal.original_reason` on the appeal screen (data already exists, zero backend change) and interpolate the stored reason into the `account-deactivated.tsx` copy and the go-online 403 detail where policy allows (redact anything not driver-safe, e.g. internal-only notes, at write time). Alternative considered: leave as "contact support" by design, to force a support-mediated conversation — rejected because the appeal flow already exists specifically to avoid needing a support contact, and CLAUDE.md's driver-classification guidance favors transparency over discretion here.
- Blast radius: `driver-app/app/appeal.tsx`, `account-deactivated.tsx`, `backend/routes/drivers/status.py`'s two `AccountDisabledException` call sites — no other consumer of `driver_appeals.original_reason` found (grep).
- Rollout: additive (render an existing field); no migration, no flag needed for the appeal-screen fix. The go-online-error copy change touches a live-tested surface (driver lockout flow) — ship behind a flag if the reason text itself needs any sanitization pass first.
- Verification to close: manual check that a suspended test driver's appeal screen shows the admin's typed reason; a snapshot/unit test asserting `appeal.original_reason` renders when present.

### DRIVER-002 — Driver's lifetime earnings total and itemized trip list use two different legacy-ride inclusion rules (extends RECURRENCE-003)
- Hierarchy: L2 Driver Earnings & Payouts › L3 Earnings display › L4 S-earn-01 "I see accurate per-trip and period earnings" › L5 migrated driver, "All" period
- Severity: MEDIUM   Priority score: S×B×L = 2×3×2 = 12
- Status: VERIFIED   Existing item: extends RECURRENCE-003 (`docs/audit/clean-sheet/rapid-baseline-2026-09-24/standards-verified.md`, A32/A33) — this specific two-endpoint mismatch is a new, currently-live instance of that family, not previously named
- Adversary: auditor / driver themselves ("your app says I earned $X but I can only find $Y of it in my trip list")
- Evidence: `backend/routes/drivers/earnings.py:364-420` (`GET /earnings`, the period-summary/"Total Earned" figure) deliberately **includes** legacy-imported rides in its money totals — per its own comment, "Business decision 2026-08-13 (A32/A33)... no `EXCLUDE_LEGACY_RIDES` here anymore." `backend/routes/drivers/earnings.py:609-673` (`GET /earnings/trips`, the itemized per-trip list a driver would use to see *which* trips make up that total) still applies `**EXCLUDE_LEGACY_RIDES` in its filter dict (line ~634). Both endpoints are called from the same client store (`driver-app/store/driverStore.ts:1111,1161`) and feed the same Activity/Earnings surface.
- What happens (plain language): a migrated driver's "Total Earned" for "All time" (or any period spanning their legacy-imported trips) includes dollars from rides that will never appear if they scroll their trip history looking for them — the two numbers cannot be reconciled by the driver, and there is no in-app note explaining why.
- Root cause: the 2026-08-13 fix (A32/A33) deliberately removed the legacy exclusion from the *summary* money fields (correct — it fixed a driver seeing "$0.00 Total Earned" despite real completed trips) but the sibling `/earnings/trips` itemized-list endpoint was not updated in the same pass, so the two endpoints now disagree on what counts.
- Recommendation: either (a) also drop `EXCLUDE_LEGACY_RIDES` from `/earnings/trips` so every dollar in the summary has a corresponding line the driver can find, or (b) if legacy trips are deliberately hidden from the itemized list (e.g. incomplete data), add a summary-line disclosure ("includes $Z from your previous-app history") so the totals are explained rather than silently mismatched. Alternative considered: leave as-is since it's a display-only quirk affecting only the migrated-driver cohort — rejected because "my earnings don't add up" is exactly the kind of support ticket / distrust CLAUDE.md's dispute-prevention goals (greenfield-extensions §8) exist to design out, and the fix is a one-line filter change or a one-line UI disclosure.
- Blast radius: `routes/drivers/earnings.py` only; `driver-app/store/driverStore.ts` consumes both endpoints already, no client change needed either way.
- Rollout: additive, no flag needed for either option.
- Verification to close: a test driver with both legacy and non-legacy completed rides in the same period; assert `sum(trips[].driver_earnings) + tips == earnings.total_earned` (currently would fail for that fixture) — or, if disclosure is chosen, assert the disclosure line renders with the correct delta.

### DRIVER-003 — The 3-year licensed-experience check is wired but is a no-op for the existing driver fleet, and is dark for everyone regardless (builds on C63/COMP-002)
- Hierarchy: L2 Driver Lifecycle › L3 Go-online eligibility › L4 SK 3-year-experience rule › L5 existing driver, no `license_issue_date` on file
- Severity: MEDIUM (regulatory)   Priority score: S×B×L = 2×3×2 = 12
- Status: VERIFIED   Existing item: `ACTION_ITEMS.md` C63 (closed 2026-09-04, migration 405) — this entry updates C63's own "caveat carried forward" with a fresher, partially-closing fact this pass found and one still-open fact
- Adversary: SGI/regulator auditor; plaintiff's lawyer after a collision involving a driver who turns out to have < 3 years' experience
- Evidence: `backend/routes/drivers/status.py:774-809` implements the check correctly (fail-safe on `NULL`, matches CLAUDE.md's Saskatchewan Regulatory bullet) but only runs when `app_settings.enforce_driver_eligibility_recheck` is `true` (`backend/schemas.py:321`, default `False` — cross-referenced and already flagged fleet-wide as COMP-002 in `compliance.md`). **New this pass**: commit `b6c9696` (2026-09-23, `feat(driver-app): collect eligibility dates in application`) added `license_issue_date` collection to `become-driver.tsx`/`profile-setup.tsx` onboarding and `profile.tsx` self-edit, with client- and server-side validation (`backend/routes/drivers/profile.py:36-51`, `_validate_eligibility_date` rejects a licence issue date implying <3 years at write time, independent of the go-online flag) — this closes C63's "nothing populates it" caveat **for drivers who onboard or edit their profile from 2026-09-23 onward**. No migration or admin tool backfills `license_issue_date` for drivers who onboarded before that date (grepped `backend/migrations/`, `backend/scripts/` for `license_issue_date` — only the column-creation migration 405 exists); those drivers keep `NULL` and are fail-safe-unblocked by design, indefinitely, with or without the flag.
- What happens (plain language): today, no driver — new or existing — is actually blocked from going online for insufficient experience, because the enforcement flag is off. If the flag is turned on tomorrow, only drivers who have onboarded or touched their profile screen since 2026-09-23 will have data to check against; the rest of the fleet is silently grandfathered in with no experience check ever applied to them, and nothing surfaces that gap to an operator deciding whether to flip the flag.
- Root cause: the enforcement wiring (C63) and the data-collection wiring (this week's commit) shipped as two separate, correctly-scoped changes, but neither included (or was asked to include) a fleet coverage report — "what % of active drivers have `license_issue_date` on file" — so there's no visibility into whether flipping the flag today would actually check anyone.
- Recommendation: before flipping `enforce_driver_eligibility_recheck` to `true`, run a one-off report of `drivers.license_issue_date IS NULL AND status='active'` so the operator knows the real coverage; consider a one-time reminder push prompting existing active drivers to add their licence issue date (reusing the pattern in `driver_onboarding_reminders.py`).   Alternative considered: force a hard block on `NULL` — rejected, matches the fail-safe direction the other two eligibility sub-checks (class, vehicle-age) already use, and a NULL-blocks-everyone flip would instantly offline the entire pre-2026-09-23 fleet with no warning, violating CLAUDE.md gate 5 (no silent behavior change to a live-tested flow).
- Blast radius: `status.py` go-online path only while the flag is off (nil today, per COMP-002); the data-collection change touches onboarding/profile-edit forms only.
- Rollout: a coverage report is read-only, no rollout risk. Any reminder-push addition would be additive, no flag needed beyond what `driver_onboarding_reminders.py` already gates on.
- Verification to close: a query/report confirming coverage %, re-run periodically until the flag is considered for enablement.

### DRIVER-004 — A driver's decline of a ride offer lowers their future offer priority, and this is not disclosed anywhere in-app
- Hierarchy: L2 Ride Fulfillment › L3 Dispatch fairness › L4 "the offer card and its consequences are transparent" › L5 driver declines an offer
- Severity: MEDIUM   Priority score: S×B×L = 2×3×3 = 18
- Status: VERIFIED   Existing item: new (grepped `ACTION_ITEMS.md`/`compliance.md` for "acceptance rate" disclosure — COMP-009 in `compliance.md` flags the *existence* of acceptance-rate dispatch filtering as a classification risk requiring "thresholds published to drivers (not verified)"; this entry supplies that verification and finds it unmet)
- Adversary: driver claiming employee status (an algorithmic system that reduces future work opportunity in response to declining an offer is a control-of-work fact a misclassification claim would use — "the platform pressures me to accept trips I don't want"); regulator; competitor recruiting drivers ("Spinr secretly penalizes declines, we don't")
- Evidence: `backend/services/dispatch_service.py:76-90` (`rank_by_eta_with_acceptance`) sorts offer-eligible drivers by `effective_eta = eta_seconds / acceptance_rate` — a driver with a lower `acceptance_rate` is offered rides later relative to distance (floored at `rate = max(rate, 0.1)`, i.e. up to a 10× ETA penalty). `backend/repositories/driver_repo.py:360` (`update_acceptance_rate`) is called with `accepted=False` on an explicit decline (`routes/drivers/ride_flow.py:798`, `routes/rides/matching.py:2074`) — a decline lowers the EWMA rate the same way a timeout-miss does. Distinguishing positive: `reset_miss_streak` (the mechanism that force-offlines a driver after 3 consecutive **unanswered** offers, `driver_presence.py:552-591`) is explicitly reset on decline as well as accept (`ride_flow.py:804`, `matching.py:2074`, `matching.py:2092`) — so an active decline never triggers the auto-offline path, only silence does. Grepped `docs/driver-faqs-saskatchewan.md` and every `driver-app/app/driver/*.tsx` file for "acceptance rate" — zero hits.
- What happens (plain language): a driver who explicitly, deliberately declines a ride they don't want (e.g. a long airport run they'd rather skip, a low-fare trip) is not suspended or locked out — but they are quietly deprioritized in future dispatch ranking, and nothing in the app or FAQ tells them that declining has this effect or by how much.
- Root cause: `acceptance_rate` was built as a single signal feeding two different mechanisms (dispatch ranking + implicitly rewarding responsive drivers) without a matching disclosure surface, and the miss-streak auto-offline mechanism (which *is* driver-visible via a push notification) was likely assumed to be "the" acceptance-consequence drivers would notice, when it's actually a separate, narrower mechanism that specifically excludes declines.
- Recommendation: disclose the mechanic in the driver FAQ and/or a stats screen ("declining trips may affect how soon you're offered your next one") — doesn't require exposing the exact formula, just the existence and direction of the effect, matching what Uber/Lyft publish about acceptance-rate-linked features. Alternative considered: remove the acceptance-rate ranking factor entirely — rejected without a human product/legal call, since it's a real fairness lever (riders shouldn't wait behind a driver who reflexively declines everyone) and disclosure, not removal, is what closes the classification-risk gap COMP-009 already named.
- Blast radius: `dispatch_service.py`'s ranking function is used at both dispatch read sites (`matching.py:549,891` pull `acceptance_rate` into the candidate query) — single-point, not scattered.
- Rollout: doc/copy-only fix, no code or flag needed.
- Verification to close: FAQ/in-app copy published; COMP-009's "thresholds published to drivers" checklist item closed.

### DRIVER-005 — An `in_progress` ride abandoned by a dead/killed driver phone has no automated recovery; only alert-only detection and manual admin closure exist
- Hierarchy: L2 Ride Fulfillment › L3 In-trip resilience › L4 "driver phone dies mid-trip" (sweep-catalog §3.3 item 18, driver side) › L5 driver never returns
- Severity: MEDIUM (deliberate design, correctly reasoned — see Steelman) → HIGH for the affected rider/driver pair while open   Priority score: S×B×L = 2×2×2 = 8
- Status: VERIFIED   Existing item: new (A7 flagged this as "Handled (INFERRED from doc, not read)" — this pass reads the actual code and downgrades that to Partial with specifics)
- Adversary: hostile device (phone dies, OS kills the app); flaky network; plaintiff's lawyer ("the platform left my client's trip open/unresolved with no insurance-period closure")
- Evidence: `backend/utils/stuck_ride_sweeper.py` — by its own docstring and `_SEARCHING_TIMEOUT_MINUTES` constant, recovers **only** rides stuck in `searching` (pre-assignment); confirmed no `in_progress` handling anywhere in the file. `backend/utils/stale_in_progress_ride_alerter.py` (full module docstring read) is explicitly **alert-only**: it never mutates `rides.status` or the driver's `driver_insurance_periods` row, by design — CLAUDE.md's "transitions from `in_progress` are `completed` only, never `cancelled`" is cited in the module's own docstring as the reason an automated fix is out of scope (force-completing risks mischarging a trip that might still be genuinely ongoing). It pages a human via Sentry/structured log after `STALE_MINUTES = 10` of no `drivers.updated_at` location-batch write while `ride_started_at` is also ≥10 minutes old. Closure requires a human at `admin_complete_ride` (`routes/admin/rides.py`), which does correctly call `record_period_transition` to close Period 3 once invoked. On the driver's own side, `driver-app/store/driverStore.ts` persists the active ride to `AsyncStorage` (`DRIVER_RIDE_KEY`) and calls `fetchActiveRide()` on relaunch — so a driver whose phone restarts (not permanently dead) reconciles back to the still-`in_progress` server state and can complete the trip themselves; the gap is specifically the case where the driver never comes back.
- What happens (plain language): if a driver's phone dies mid-trip and they don't return to the app, the rider's ride sits `in_progress` indefinitely (the rider cannot book a new ride — `in_progress` is in `active_statuses`), and the driver's Period 3 insurance audit row stays open, until either the rider complains to support or the 10-minute alert reaches an admin who manually closes it. If the driver's phone merely restarts, the driver-app itself recovers cleanly with no gap.
- Root cause: deliberate, well-reasoned scope limit (see Steelman) — not an oversight, but the operational dependency ("a human must act on a Sentry alert") has no stated SLA in the code or, as far as this pass found, in a runbook.
- Recommendation: confirm a runbook exists for this specific alert with a response-time target (CLAUDE.md's P1 support target is <2h; this is arguably more time-sensitive since a rider is actively blocked from booking) — this is a documentation/process gap, not a code gap, given the deliberate safety reasoning already in place.   Alternative considered: none proposed by this lane beyond the runbook check — the "alert, don't auto-fix" design is the right call for a money/liability-sensitive transition and this lane agrees with the existing reasoning.
- Blast radius: `stale_in_progress_ride_alerter.py` (alert-only, no write) and `admin_complete_ride` (existing, correct).
- Rollout: n/a (no code change proposed).
- Verification to close: confirm a runbook/on-call path exists for this alert type; if not, that's the actual gap to close, not the alerting logic itself.

### DRIVER-006 — No battery-aware or low-power-mode adaptive behavior found in the driver-side trip location pipeline (gap, not a confirmed defect)
- Hierarchy: L2 Driver App › L3 Background location › L4 winter/battery operations (greenfield-extensions.md §12) › L5 low battery mid-shift
- Severity: LOW-MEDIUM (UNKNOWN actual impact)   Priority score: not scored — coverage gap, not a confirmed bug
- Status: INFERRED (absence-of-evidence, not a read-every-line confirmation)   Existing item: new
- Adversary: cold-weather Saskatchewan driver whose phone battery drains faster in winter and while running GPS/background location continuously for a full shift
- Evidence: grepped `driver-app/utils/tripLocationOutbox.ts` (631 lines, read in part), `backgroundAuth.ts`, and `driver-app/app/driver/(tabs)/index.tsx` for `battery`/`Battery`/`lowPowerMode` — zero hits. This is a negative-result grep across a subset of the location pipeline, not a full read of every driver-app file that could plausibly own this (e.g. `lib/androidAuto/carLocationTask.ts` not checked for battery awareness).
- What happens (plain language): unknown whether the app throttles location-update frequency, warns the driver, or behaves any differently as battery runs low or iOS/Android low-power mode engages — this pass found no code doing so in the files it checked, but did not exhaustively rule it out.
- Root cause: n/a — this is a coverage note.
- Recommendation: a follow-up pass specifically greps `expo-battery`/`Battery.*` APIs and every background-location config file before concluding this is a real gap vs. simply unimplemented-and-unneeded (e.g. if Expo's `Location` background task already degrades gracefully by OS default). Flagged here because greenfield-extensions §12 names "winter operations mode: battery-saver" as a gap area neither source prompt fully covered, and Saskatchewan winter driving is squarely this app's core market.
- Blast radius: n/a (no fix proposed, coverage gap only).
- Rollout: n/a.
- Verification to close: exhaustive grep for `expo-battery`/`DeviceMotion`/OS low-power APIs across `driver-app/`; if genuinely absent, decide whether it's needed given Expo's background-location defaults.

**Cited from other lanes (not re-derived — see each file for full finding cards):**
- **DISPATCH-003** (`dispatch.md`) — `set_driver_available`'s release path is a read-then-write, not atomic; the epoch-fenced replacement (`transition_driver_availability()`, migrations 457-464) exists but is dark behind `driver_availability_v2_enabled`. Directly affects `is_available ⇒ is_online` — a driver-journey invariant.
- **COMP-001/COMP-002** (`compliance.md`) — the licence-class gate may check the wrong class (Class 5 in code vs. a Class-4 regulation snippet, UNKNOWN which is current) and every SK eligibility sub-check beyond document expiry is dark by default; the v2 availability service path (`driver_availability_service.py`) skips the eligibility block entirely, a parity gap this lane did not re-verify.
- **COMP-009** (`compliance.md`) — the independent-contractor agreement has no e-signature flow; no driver has a recorded signed copy today.
- **MONEY-001** (`money-cra.md`) — `routes/drivers/earnings.py`'s `GET /earnings` and `/earnings/comparison` sum `tip_amount` as raw floats in 2 of 14+ call sites, drifting a driver's displayed tip total from the true Decimal sum.
- **MONEY-010** (`money-cra.md`) — payout is not capped by amount actually collected from the rider; the platform-funded-absorption policy is deliberate but unmeasured.
- **MONEY-012** (`money-cra.md`) — the T4A PDF fills both Box 020 and Box 048 with the same net-earnings figure for GST-registered drivers, which CRA's own box definitions treat as mutually exclusive categories — double-reports income on one slip.
- **SAFETY-003** (`trust-safety-fraud.md`) — `is_online` is pure driver-declared intent; a crashed/unreachable driver's Period 1 row can stay open past the point they were actually reachable. Favorable-direction ambiguity (more coverage, not less) — flagged there as a confirm-with-SGI question, not a defect.

<!-- SECTION: scenarios -->
## 3. Scenario cards — sweep-catalog §3.5 + driver side of §3.1–§3.3

Chain (greenfield-extensions §4): TRIGGER → DETECTION → SYSTEM STATE → USER EXPERIENCE →
BUSINESS RULE → RECOVERY → ESCALATION → AUDIT RECORD → TEST. Given per row only where
it adds information beyond the table's own columns; a short "Chain" note follows rows
that need it.

### §3.5 Driver lifecycle (all 5 items)

| # | Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status | Dispute risk |
|---|---|---|---|---|---|---|---|
| 39 | Licence/insurance/registration expires while online or mid-trip | Driver | Document `expiry_date` passes | Driver blocked from next go-online at minimum; ideally forced offline if already online | **Go-online**: hard-blocked, fail-closed on lookup failure (`status.py:520-676`, "refusing go-online... document verification cannot be bypassed"). **Already-online, document expires mid-shift**: `backend/utils/document_expiry.py`'s 12-hour loop force-suspends and disconnects the WS session (module docstring: "suspends drivers whose documents have already expired and disconnects their active WebSocket session") — but the loop runs every 12h, not on the exact expiry instant, so there is a window (up to 12h) where an online driver's documents have expired but the loop hasn't caught it yet. **Mid-trip** (document expires while `in_progress`): not verified whether the loop excludes active-trip drivers from mid-trip forced-suspend (a forced WS disconnect mid-trip would be a serious UX/safety regression) — flagged UNKNOWN, not read this pass. | **Handled** (go-online) / **Partial** (already-online, 12h window) / **Unknown** (mid-trip exclusion) | Medium — a driver disconnected mid-trip with no fare settled would be a real dispute |
| 40 | Deactivation: reason shown, appeal path, data retained | Admin, then Driver | Admin suspends/bans with required reason | Driver sees the specific reason and a clear appeal path; reason retained | Appeal path: real, tested, one-pending-at-a-time (`driver_appeals.py`, migration 320 partial unique index). Reason **retained** (`original_reason` stored on the appeal row) but **never shown** to the driver on any screen (see DRIVER-001 above). Data retention: `docs/driver-lifecycle-status-flow.md` — deletion is a soft tombstone, 7-year attributable retention, `driver_insurance_periods` untouched (append-only). | **Partial** (appeal path + retention: Handled; reason-shown: Unhandled — DRIVER-001) | Medium — see DRIVER-001 |
| 41 | Driver changes vehicle; document re-verification | Driver | Driver edits any vehicle field | Re-review before continuing to accept rides on new vehicle info | `status = needs_review` forced on any vehicle-field edit or flagged document re-upload while `active` (`profile.py:195`, `documents.py:263`), **and** the driver is forced offline in the same operation (`is_online=False`, `is_available=False`) — `docs/driver-lifecycle-status-flow.md` §Phase 4, VERIFIED. No automatic timeout back to `active` — requires an admin `approve`. | **Handled** | Low |
| 42 | Payout fails (bank account closed); negative balance | System (Stripe) | Weekly `auto_payout.py` Transfer attempt permanently fails | Driver notified with an actionable next step; money stays safely earmarked, not lost | Failure is classified (`permanent`/`retryable`/`ambiguous`, `_classify_stripe_error`); a `permanent` failure (e.g. closed/invalid destination account) sets `payouts.status='failed'` **and** proactively pushes the driver: `"Payout needs attention" / "We couldn't send your weekly payout. Our team has been notified and your balance is safe."` (`auto_payout.py` — the notify call sits directly after the `result["outcome"] == "failed"` branch in `run_weekly_auto_payout`, VERIFIED). Retryable/ambiguous failures stay `reserved` and are retried by an hourly sweep (`sweep_stale_reserved`) or escalated to `manual_reconcile` after `_MAX_RETRYABLE_ATTEMPTS`; that escalation path (`_retry_reserved_row`) does **not** call `_notify_driver` on escalation — not confirmed whether the driver is told when a retry-then-escalate case (vs. an immediate `permanent` failure) resolves. "Negative balance" concept: not found — `_compute_payable_balance` bottoms out via `MIN_PAYOUT_AMOUNT`/eligibility gates, no code path observed that lets a driver's balance go below $0 (a large post-payout refund/chargeback would need to be absorbed against a *future* balance, not clawed back — consistent with MONEY-010's "platform-funded absorption" policy cited from `money-cra.md`, not independently re-traced here). | **Handled** (immediate permanent failure) / **Partial** (retry-then-escalate driver notice, unverified) / **Unknown** (negative balance — likely N/A by design, not independently confirmed) | Low–Medium |
| 43 | Driver disputes a rider's low rating or false report | Driver | Driver receives a rating/report they believe is unfair | A dispute/flag mechanism with human review | Grepped `backend/routes/drivers/*.py` and every `driver-app/app/driver/*.tsx` for "dispute"/"contest"/"flag" + "rating" — **zero hits**. The only driver-side rating-adjacent surface found is the deactivation appeal flow (`appeal.tsx`), which only accepts `appeal_type` derived from `drivers.status` (suspended/banned/needs_review) — a driver who is still `active` but disagrees with a specific rating or a false rider report has no submission path in this code at all. | **Unhandled** | Medium — a pattern of un-contestable low ratings feeding into future deactivation risk (via whatever admin process uses ratings) is exactly the kind of "no due process" fact a misclassification/wrongful-deactivation claim would use |

**Chain note (item 43):** TRIGGER (rider submits low rating / false report) → DETECTION (none — no automated flag for "disputed" ratings) → SYSTEM STATE (rating written to `drivers` aggregate, presumably feeding some downstream admin process — not traced this pass) → USER EXPERIENCE (driver sees the rating drop, if visible at all in-app; no contest UI) → BUSINESS RULE (unknown — whether/how low ratings feed into suspension is outside this lane's evidence) → RECOVERY (none) → ESCALATION (a driver would have to fall back to a generic support contact, unverified whether one exists for this specific purpose) → AUDIT RECORD (none specific to a disputed rating) → TEST (none found). This chain has the most open links of anything in this table — recommend a dedicated follow-up.

### Driver side of §3.1 Booking & matching

| # | Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status | Dispute risk |
|---|---|---|---|---|---|---|---|
| 5 | Two drivers accept the same ride (race) | Driver | Simultaneous accept | Loser gets 409 + a clear "already taken" message, released back to available | Cited from `dispatch.md`/CLAUDE.md's documented invariant: Supabase update filters `{'status':'searching'}`, 0 rows → `ride_taken` WS event + 409 — not re-verified in this pass, treated as VERIFIED via CLAUDE.md's explicit documentation of this exact mechanism plus DISPATCH-003's context (same file family). | **Handled** (per existing docs; not independently re-read this pass) | Low |
| 6 | Offer timeout while driver's phone is backgrounded/locked | Driver | Push arrives, driver doesn't act within the offer window | Offer expires cleanly, re-offered to next driver, no charge/penalty for a background miss beyond the shared miss-streak mechanic | `increment_miss_streak` counts a timeout the same as any non-response (`driver_presence.py`); 3 consecutive → auto-offline **with an explicit push notification** telling the driver why (`matching.py:1877-1896`, "You missed N ride offers in a row"). This applies equally to a backgrounded-phone miss and a driver who is simply ignoring offers — the system cannot distinguish "phone was locked" from "driver chose not to respond," which is reasonable (both have the same effect on the rider's wait) but means a driver who was legitimately in a dead zone gets the same auto-offline as one who's avoiding offers. | **Handled** (offer expiry + driver-visible notice) — **Partial** on distinguishing cause | Low |
| 8 | Scheduled ride: driver cancels 10 min before; surge active at dispatch but not at booking | Driver | Driver cancels a pre-accepted scheduled ride shortly before pickup | Rider re-matched quickly; fare locked at booking-time surge, not dispatch-time surge (CLAUDE.md: "Never apply surge to scheduled rides booked outside the surge window") | Not independently re-traced this pass — cited from CLAUDE.md's explicit rule and A7's confirmation that `scheduled_rides.py` computes in UTC (DST-safe); the specific "driver cancels a scheduled ride 10 min out" re-dispatch path was not read. | **Unknown** (not verified this pass) | Medium |

### Driver side of §3.2 En route & pickup

| # | Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status | Dispute risk |
|---|---|---|---|---|---|---|---|
| 12 | Driver GPS frozen / drifting / teleporting | Driver | Bad GPS fix mid-route | Implausible points rejected, don't corrupt distance/fare or trigger false safety alerts | Cited from A7 INFO-005 (VERIFIED there): `routes/drivers/ride_flow.py:933-954` calls `evaluate_gps_plausibility()`/`check_location_integrity()` before trusting a location write; C64 (`ACTION_ITEMS.md`, closed 2026-09-04) extended this per-point coverage to WS location **batches** (previously only the last point in a batch was checked). | **Handled** | Low |
| 15 | Wrong rider gets in (verification PIN?) | Driver | Driver can't visually confirm the right rider | A trip-start PIN or equivalent the driver asks the rider for | Grepped `backend/routes/drivers/ride_flow.py` and `driver-app/components/panels/` for `pin`/`PIN`/`verification_code` in a rider-identity-confirmation sense — no hits found (the only "pin" hits in the codebase, per an earlier grep this pass, refer to the map *pickup pin location*, not an identity code). Rider name/rating/photo are shown pre-arrival (`RideOfferPanel`'s `rider_name`/`rider_rating`/`rider_profile_image` fields, confirmed in the interface) as the only identity signal. | **Unhandled** (no PIN mechanism found) | Medium — wrong-passenger pickup is a real liability/insurance scenario (a stranger riding under another person's trip record) |
| 17 | Driver's phone dies before pickup (pre-`driver_assigned` or during Period 2) | Driver | Phone dies while en route to pickup | Rider told promptly; ride re-dispatched, not left hanging | Not independently traced this pass beyond the general offer-miss/auto-offline mechanics (item 6 above) and the general driver-claim reaper (`driver_claim_reaper.py`, cited in `dispatch.md` context, not re-read here). Flagged UNKNOWN for the specific "dies after accept, before pickup" window. | **Unknown** (not verified this pass) | Medium |

### Driver side of §3.3 In trip & completion

| # | Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status | Dispute risk |
|---|---|---|---|---|---|---|---|
| 18 | Phone dies / app killed mid-trip | Driver | See DRIVER-005 above | Trip completes via a server-side fallback or clean manual path; rider not stranded indefinitely | See DRIVER-005: driver-side self-heal on relaunch is Handled; permanent-abandonment case is alert-only + manual admin close, no hard SLA found. | **Partial** (see DRIVER-005) | Medium |
| 19 | Driver forgets to end the trip; rider already out | Driver | Driver doesn't tap "Complete" after drop-off | Some form of prompt/reminder, or a distance/time-based nudge, so the meter doesn't silently keep running against the rider | Not verified this pass — no explicit "forgot to end trip" nudge/reminder mechanism was located in the files read (`ride_complete.py` not read end-to-end this pass). Flagged UNKNOWN; this is a plausible real dispute source (rider charged extra time/distance for a trip that was actually already over) and worth a dedicated follow-up read of `ride_complete.py` and any dropoff-proximity nudge logic. | **Unknown** (not verified this pass) | High if unhandled — direct overcharge risk |
| 20 | Driver ends trip early / far from destination | Driver | Driver taps "Complete" before reaching the stated dropoff | Fare reflects actual distance travelled; large deviation flagged for review | Not verified this pass — `ride_complete.py`'s fare-finalization logic against actual vs. planned distance was not read in this lane. | **Unknown** (not verified this pass) | Medium |
| 21 | Route deviation or long-hauling | Driver | Driver takes a materially longer route than expected | Safety alert path distinct from a fare-adjustment/fraud path; both should exist | `backend/utils/route_deviation_alerter.py` exists, flag-gated (`route_deviation_alert_enabled`), and per `trust-safety-fraud.md` (cited, not re-verified) shares the same replay-safe SET-NX escalation idiom as `safety_checkin_loop.py`. This appears to be the **safety**-alert path (a rider-safety concern about an unexpected route), not a fare-adjustment/long-hauling-fraud detector — whether a *separate* fraud-pattern check exists for "driver repeatedly takes 20%+ longer routes to inflate distance-based fare" was flagged as **UNKNOWN** by `trust-safety-fraud.md`'s own NOT-VERIFIED list ("driver-rider collusion on fake rides — not traced," "cancellation-fee farming — not traced") and not independently re-checked here. | **Handled** (safety-deviation alerting) / **Unknown** (fare-inflation-specific detection) | Medium |
| 24 | Vehicle breakdown or collision mid-trip — insurance Period 3 evidence | Driver | Collision/breakdown while `in_progress` | A clear first-notice-of-loss path; the trip's Period 3 row + location trace stands as evidence | Insurance-period evidence itself is solid: Period 3 opens at trip start with a mandatory `ride_id` link (`insurance_periods.py`, `ValueError` if missing), append-only, 7-year retention (CLAUDE.md, cross-referenced against `insurance-period-auditor.md`'s rule #2/#4). A **first-notice-of-loss (FNOL) workflow** — a dedicated collision/accident report with an evidence pack (photos, auto-attached route/period data) and an insurer hand-off — was not found. `driver-app/app/report-safety.tsx`'s incident categories are `'Road Hazard' | 'Passenger Behaviour' | 'Vehicle Issue' | 'Other'` (`report-safety.tsx:31`) — no "Collision"/"Accident" category, and no evidence-pack assembly logic found in the file (photo capture not confirmed either way, not read past the category list). A driver in an actual collision has the general SOS flow (calls emergency contacts + safety team, per `trust-safety-fraud.md`'s SOS hop table) and this generic report form, but nothing that specifically starts an insurance claim or packages the Period 3 ride's data for SGI. This exact gap is separately named as an open item in `greenfield-extensions.md` §12, consistent with what this pass found. | **Handled** (evidence trail exists and would support a claim if pulled manually) / **Unhandled** (no dedicated FNOL workflow to actually pull and hand off that evidence) | Medium |

<!-- SECTION: rebuild-delta -->
## 4. Rebuild Delta card — driver earnings transparency (the 0%-commission proof)

## Epic: Driver Earnings & Payouts — "driver keeps 100% of the fare, and can prove it to themselves"

- **Verdict per inherited pattern:** the underlying commitment (KEEP) — 0% commission is
  Spinr's brand-defining number and, per `money-cra.md`'s Steelman, `platform_share` is
  genuinely absent as a settable variable anywhere in `backend/` (VERIFIED by that
  lane's grep). The **display/reconciliation layer** around that commitment is MODIFY:
  the promise is real in the money math but not yet fully *provable* to a driver from
  inside the app, for three concrete, evidenced reasons — DRIVER-002 (two earnings
  endpoints disagree on legacy-ride inclusion), MONEY-001 (raw-float tip sums drift from
  the true total in 2 of 14+ call sites), and no single screen shows fare → driver share
  → tip → tax → net in one reconciled stack per trip.
- **Keep (already best-in-class):** the offer card itself already shows `driver_earnings`
  (not gross fare) pre-accept, with distance/duration/surge/destination all visible
  (`routes/rides/matching.py:1559-1590`, `RideOfferPanel.tsx` — confirmed this pass,
  §2 evidence). The weekly auto-payout with a GST/SIN compliance gate and per-outcome
  driver notification (`auto_payout.py`) is a mature, well-reasoned system per this
  lane's read of its failure-classification logic. `driver_earnings_with_tip()`
  (cited from `money-cra.md`'s Steelman) fixed a real class of underpayment bug by
  always recomputing from source instead of mutating a stale total — the right pattern.
- **Uber/Lyft do:** both platforms take a commission (typically ~25-30% observed by
  riders/drivers historically, INFERRED/general-knowledge, not a cited primary source
  this pass) and have historically faced driver-earnings-transparency criticism and
  litigation over exactly this kind of "the app total doesn't match what I actually got"
  gap — a well-known industry pain point, not a Spinr-specific failure mode.
  **Spinr today:** the commission side of that problem structurally cannot exist (no
  commission variable exists to hide), which is a genuine, defensible advantage — but
  the *proof* side (can a driver self-verify the math without trusting the app) has the
  same class of small drift bugs (DRIVER-002, MONEY-001) that erode trust in exactly the
  number that's supposed to be Spinr's differentiator.
- **Clean-sheet Spinr would:** ship one "Trip receipt" view, identical in structure to
  the rider's own receipt, showing: gross fare → driver's 100% share → tip → GST/PST
  withheld-or-not → net paid, for every trip, with a running reconciliation against the
  period summary and the actual bank deposit — so "0% commission" is not a marketing
  claim the driver has to trust, it's a number they can add up themselves from data the
  app already has.   **Why (the edge it creates):** turns a structural advantage (no
  commission) into a *felt* advantage (driver-verifiable trust), which is the harder,
  more defensible thing to copy — a competitor can announce "0% commission" in a press
  release; matching an app where every driver has personally verified the arithmetic
  takes longer and compounds as a retention/recruiting asset.
- **How (architecture/pattern):** one `driver_trip_receipt` view/endpoint that both the
  itemized trip list and a printable/exportable receipt draw from — same pattern the
  rider side already uses for `receipt_pdf.py` — instead of the current multiple
  earnings endpoints (`/earnings`, `/earnings/trips`, `/earnings/comparison`, `/earnings/
  weekly`, `/earnings/monthly`) each independently deciding what to include.
  **Who:** Driver Earnings & Payouts epic owner (per `traceability.csv`'s epic
  grouping) + whoever owns `receipt_pdf.py` on the rider side, since the pattern already
  exists and should be reused, not reinvented.   **When:** Now for DRIVER-002/MONEY-001
  (small, isolated fixes); Next for the unified trip-receipt view (a real feature, needs
  design + a flag).
- **Incremental path from today (no big-bang):** 1) fix DRIVER-002 and MONEY-001 (both
  one-file, additive, no flag needed) → 2) add a per-trip GST/PST line to the existing
  `/earnings/trips` response (data likely already exists via `tax_breakdown`/fare
  components, not independently confirmed this pass) → 3) build the unified receipt view
  behind a flag, pilot with a small driver cohort → 4) fold the existing standalone
  earnings endpoints into thin wrappers around the same underlying view so there's one
  source of truth, not five independently-filtered queries.
- **Cost/effort:** step 1 = S; step 2 = S–M; step 3 = M; step 4 = M.   **Risk:** low
  throughout (additive, display-only, no money-movement change).   **Reversibility:**
  fully reversible at every step (display layer only).   **Build/Buy/Partner/OSS:**
  Build — this is core product differentiation, not a commodity capability.
- **Advantage type:** trust/data (driver-verifiable arithmetic compounds as a retention
  and word-of-mouth-recruiting asset — a driver who has personally checked the math and
  found it honest is a stronger recruiter than an ad). Defensible in the sense that it
  requires sustained data-quality discipline (no more DRIVER-002/MONEY-001-class drift),
  not a one-time feature ship a competitor can clone overnight.
- **"Why not?":** the simplest version of this (fix the two known drift bugs, add a tax
  line to the existing trip list) requires no new architecture and could ship before the
  full unified-view rebuild — there's no reason to wait for step 3/4 to capture most of
  the trust benefit. The risk of *not* doing even the small fixes is that every "my
  earnings don't add up" support ticket quietly erodes the one number Spinr's whole
  driver pitch rests on.

<!-- SECTION: steelman -->
## 5. Steelman bullets

- **The offer card is genuinely fair by construction, not by accident.** It shows the
  driver's actual take (`driver_earnings`, not gross fare), pickup distance, trip
  distance/duration, surge multiplier, and both pickup **and** dropoff addresses before
  the driver has to decide (`matching.py:1550-1590`, `RideOfferPanel.tsx` — verified this
  pass by reading both the backend payload and the client render). This is table-stakes
  today but was a real, contested feature in the industry's history — Spinr ships it
  without the multi-year debate.
- **The insurance-period model is a single, well-documented source of truth** —
  `derive_insurance_period()` is pure (no DB/async/clock), keyword-only on purpose "to
  keep a transposed positional argument from silently misstating SGI commercial
  coverage," and degrades toward *less* coverage-claiming, never more, on an unknown
  ride status. `record_period_transition()` explicitly documents its own compliance
  trade-off (audit-write failures are logged, never raised, because blocking the driver
  state machine to guarantee an audit row is the wrong trade for a regulatory log). This
  is exactly the kind of "why was it built this way" reasoning the ground rules ask
  auditors to find before flagging something as wrong.
- **The go-online document-expiry gate fails closed, unconditionally, with no flag.**
  Unlike the SK-eligibility sub-checks (class/age/vehicle-age/3-year-experience), which
  are correctly flag-gated for a staged rollout, document expiry is hard-enforced today,
  and the code explicitly refuses to go online rather than guess when the document
  lookup itself fails ("refusing go-online... document verification cannot be
  bypassed," `status.py`). The regulatory floor is not the part that's soft.
- **Miss-streak auto-offline and acceptance-rate ranking are correctly kept separate
  mechanisms**, and the miss-streak one is driver-visible (a specific push notification
  naming the count) while explicitly excluding active declines from counting against it
  — a driver who says "no" to a ride is treated differently from one whose phone just
  didn't answer. The gap this lane found (DRIVER-004) is a disclosure gap on the ranking
  side, not a design flaw in either mechanism.
- **The 3-year-experience check (C63) and its onboarding data-collection follow-up
  (2026-09-23) both independently chose the fail-safe direction on missing data** —
  `NULL` unblocks rather than locks out — consistently, across two separate change sets
  built roughly seven weeks apart by (evidently) different sessions. That consistency
  under a "when in doubt, don't retroactively lock out an already-active driver" rule is
  a good sign for how this codebase handles rollout risk on a live-tested surface, even
  though the fleet-coverage visibility gap (DRIVER-003) remains real.
- **The stuck/stale ride recovery design deliberately draws a hard line at automation**
  for `in_progress` rides specifically because CLAUDE.md's "never `cancelled` after trip
  start" invariant makes an automated force-complete a money/liability risk, not just a
  UX one — `stale_in_progress_ride_alerter.py`'s docstring states this reasoning
  explicitly rather than leaving it to be inferred. This lane agrees with that call; the
  gap it found (DRIVER-005) is about the *human* response-time guarantee around that
  alert, not the alert-only design itself.
- **No control-of-work language was found anywhere this pass checked** —
  `docs/driver-faqs-saskatchewan.md`, `docs/legal/independent-contractor-agreement.md`,
  and every driver-app screen/component grepped for shift/uniform/mandatory-hours
  language came back clean (the only hits were unrelated code-comment uses of "shift"/
  "uniform" — camera framing and layout styling, not driver scheduling). This is a
  meaningful negative result for the classification-risk question this lane was charged
  with checking first.

<!-- SECTION: top5 -->
## 6. Top 5 findings

1. **DRIVER-004 — Undisclosed acceptance-rate ranking penalty for declining an offer**
   (MEDIUM, priority 18). Highest-priority item in this lane: it's the one finding that
   directly touches the contractor-classification adversary this lane was charged to
   run first, has clean evidence on both sides of the mechanism (dispatch ranking code
   + zero FAQ/in-app disclosure), and the fix is copy-only, near-zero risk. Closes the
   open half of `compliance.md`'s COMP-009 ("thresholds published to drivers, not
   verified").
2. **DRIVER-001 — Deactivation reason captured but never shown to the driver** (MEDIUM,
   priority 12). The data already exists (`original_reason` on the appeal row); this is
   a render-the-field-that's-already-there fix, and it directly answers this lane's
   charter question ("is every deactivation/suspension explainable").
3. **DRIVER-002 — Earnings summary and itemized trip list disagree on legacy-ride
   inclusion** (MEDIUM, priority 12). A fresh, currently-live instance of the
   RECURRENCE-003 family; directly answers "are earnings transparent per trip" with a
   concrete "not always, for one identifiable cohort" — and feeds the Rebuild Delta
   card's central argument (§4).
4. **DRIVER-003 — 3-year-experience check has no fleet-coverage visibility before a
   flag flip** (MEDIUM, priority 12). Not a code defect — a decision-support gap that
   could otherwise cause an operator to flip `enforce_driver_eligibility_recheck` on
   believing it protects the whole fleet when, as of today, it would only ever check
   drivers who touched onboarding/profile-edit since 2026-09-23.
5. **DRIVER-005 — No automated recovery for an abandoned `in_progress` ride, no
   confirmed runbook SLA around the alert** (MEDIUM→HIGH while open, priority 8). The
   underlying design is correct and well-reasoned (see Steelman); what's missing is
   confirmation that the "a human responds to this Sentry alert" half of the design
   actually has a response-time commitment anywhere.

Honourable mention: sweep-catalog item 43 (driver disputes a rating/false report) is
**Unhandled** with zero code found — not in the top 5 only because its severity is hard
to score without knowing how ratings feed into any downstream admin process, which this
lane did not trace. It may deserve escalation once that process is known.

<!-- SECTION: not-verified -->
## 7. NOT verified

- Whether an already-expired document forces suspension **mid-trip** (as opposed to
  already-online-but-idle) — `document_expiry.py`'s exclusion (or non-exclusion) of
  active-trip drivers from its 12-hour sweep was not read.
- `backend/routes/drivers/ride_complete.py` — not read end-to-end. This is where
  sweep-catalog items 19 (driver forgets to end trip) and 20 (ends trip early/far from
  destination) would be answered from, and both are flagged UNKNOWN/High-dispute-risk
  above specifically because this file wasn't read.
- Whether a fare-inflation-specific long-hauling/route-deviation *fraud* detector exists
  separate from the safety-alert path (`route_deviation_alerter.py`) — cited as UNKNOWN
  from `trust-safety-fraud.md`'s own NOT-VERIFIED list, not independently re-checked.
- `driver-app/app/report-safety.tsx` beyond its category enum and top-level form fields
  — whether photo/evidence capture exists at all was not confirmed either way.
- The retry-then-escalate payout path (`_retry_reserved_row` → `manual_reconcile`) —
  confirmed it does *not* call `_notify_driver` on escalation in the code read, but
  whether the driver is told through some other channel (email, in-app payout-history
  status text) was not checked.
- `backend/documents.py` (upload endpoints) and the documents.tsx screen — not read;
  the journey map's "Documents" row is the least-verified row in that table.
- Android Auto (`driver-app/lib/androidAuto/`) — only `carSurface.tsx`'s size and one
  hardware-caveat comment were confirmed to exist; this lane did not independently
  re-verify C70/C90/C103's "unverified on real hardware" status, only confirmed those
  ACTION_ITEMS entries exist and the surface itself is real, substantial code (865
  lines in `carSurface.tsx` alone, 14 files under `lib/androidAuto/`).
- Background-location power/battery behavior (DRIVER-006) — negative-result grep only,
  not an exhaustive read of every location-related file.
- Whether `driver-app/app/help.tsx`'s absence (flagged `sibling-missing` in
  `traceability.csv` against rider-app's equivalent) reflects a real driver-facing
  help-content gap or a different navigation structure — not read.
- Exact per-trip GST/PST line-item availability for drivers (whether `tax_breakdown` or
  equivalent fare-component data already exists per trip in a form the Rebuild Delta
  card's step 2 could reuse) — not confirmed, flagged as a design input to verify
  before scoping that step.
- SGI/CRA primary-source questions this lane's evidence touches (licence class,
  GST small-supplier exemption, T4A box semantics) are **owned by `compliance.md` and
  `money-cra.md`** (COMP-001, and the CRA table in `money-cra.md`) — not re-verified
  independently in this pass; carried forward by reference, not re-investigated.

<!-- SECTION: human-only -->
## 8. Human-only questions

- Is there an operational runbook (with a response-time target) for the
  `stale_in_progress_ride_alerter` Sentry alert today? If not, who owns writing one?
  (DRIVER-005)
- Does any downstream admin process actually use `drivers.rating` to affect
  deactivation risk, and if so, should a driver-facing rating-dispute path be built
  before or alongside whatever uses that number? (§3.5 item 43)
- Product/legal call on DRIVER-004: disclose the acceptance-rate-affects-ranking
  mechanic (recommended), remove the mechanism, or leave it undisclosed with a
  documented rationale — this lane recommends disclosure but the decision is not
  engineering's to make unilaterally on a live-tested, classification-risk-adjacent
  surface.
- Should `enforce_driver_eligibility_recheck` be flipped on now that the SK-eligibility
  sub-checks are code-complete, and if so, is the DRIVER-003 fleet-coverage gap
  (license_issue_date backfill) acceptable to ship with, or does a reminder-push
  campaign need to precede the flip? Same open question already carried by `compliance.md`'s COMP-002/H15 (production flag states) — this lane is not duplicating it, only naming the driver-specific angle.
- Whether a dedicated first-notice-of-loss (collision) workflow is planned, and on what
  timeline — this lane found the gap (§3.5 item 24) but building an insurer hand-off
  flow is a product/legal/SGI-relationship decision, not something to scope from code
  alone.
- COMP-001 (Class 5 vs. Class 4 licence requirement) directly gates who can legally
  drive for Spinr — this lane defers to `compliance.md`'s H1 but flags it here too since
  it's the single highest-consequence open question touching this lane's entire go-
  online flow.

<!-- SECTION: escalations -->
## 9. Escalations

Per this lane's charter, the following are **ASSUMED without a primary source** and are
carried forward rather than treated as settled:

- **Contractor-classification risk from DRIVER-004** (undisclosed acceptance-rate
  ranking penalty) is this lane's own legal-adjacent judgment call, not a cited
  determination from Saskatchewan/federal gig-work case law. `sweep-catalog.md` §4.4
  itself notes "deactivation fairness / appeal standards emerging in Canadian gig-work
  law — confirm current Saskatchewan and federal status" is unconfirmed platform-wide;
  DRIVER-001 and DRIVER-004 both sit on that same unconfirmed legal foundation. Escalate
  to counsel alongside COMP-009's broader independent-contractor-agreement review
  (`compliance.md`).
- **SGI's interpretation of Period 1 during a declared-online-but-unreachable window**
  (SAFETY-003, cited from `trust-safety-fraud.md`) directly affects this lane's go-
  online/insurance-period journey map row — no SGI primary source was consulted by
  either lane. Carried forward, not re-opened.
- **Licence class (Class 5 vs. Class 4)** — COMP-001 in `compliance.md` — this lane's
  entire go-online eligibility gate depends on the answer and currently enforces Class
  5, which may be the wrong class per an uncited regulation snippet found by the
  compliance lane. Escalating here again because it is this lane's single most
  consequential open question, not because it needs a second independent
  investigation — one human answer (SGI/counsel) closes it for both lanes.
- **GST/HST small-supplier exemption inapplicability to rideshare drivers** — the
  payout gate (`_require_gst_for_payout`) hard-blocks every driver's first payout on
  this premise; `money-cra.md`'s CRA table already carries this as ASSUMED with no
  primary-source citation found in-repo. This lane did not re-investigate independently
  but flags that the premise gates 100% of driver payouts, so the stakes of it being
  wrong are high enough to warrant priority in whatever primary-source review CLAUDE.md's
  tax-question backlog eventually runs.
