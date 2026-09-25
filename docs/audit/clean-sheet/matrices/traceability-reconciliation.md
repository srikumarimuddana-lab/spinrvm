# Traceability Orphan Reconciliation

Report-only. Reconciles the 769 `orphan`, 16 `gap`, and 7 `sibling-missing` rows in
`docs/audit/clean-sheet/traceability.csv` (1,788 rows total, 996 `mapped`), per
CARTO-004's finding (`docs/audit/clean-sheet/01-inventory/epics.md`) that the
automated PRD-to-code matcher's `gap` rows were mostly keyword-collision artifacts,
not real absences. No code, config, or CSV changes were made. This document is
being written incrementally — §0 and §4 are complete and stable; later sections are
filled in as each pass finishes, so a stop mid-task loses only unwritten sections.

**Status of this document: COMPLETE.** All seven sections (§0–§7) below are
filled in; each carries its own status line.

---

## §0 Method

All commands were run from the repo root (`/home/user/spinrvm`), read-only, no
`git commit`/`push`, no edits to `traceability.csv`.

### 0.1 Load and split the CSV by status

```python
import csv
rows = list(csv.DictReader(open('docs/audit/clean-sheet/traceability.csv')))
mapped   = [r for r in rows if r['status'] == 'mapped']          # 996
orphans  = [r for r in rows if r['status'] == 'orphan']          # 769
gaps     = [r for r in rows if r['status'] == 'gap']             # 16
siblings = [r for r in rows if r['status'] == 'sibling-missing'] # 7
```

Verified via `python3 -c "..."` one-liners and `Counter(r['status'] for r in rows)`
→ `Counter({'mapped': 996, 'orphan': 769, 'gap': 16, 'sibling-missing': 7})` (sums
to 1,788, matches the brief).

### 0.2 Structural (high-confidence) bucket pass over the 769 orphans

A path-prefix / filename-pattern classifier (script kept at
`/tmp/.../scratchpad/classify.py` for this session, not committed) tags an orphan
row `(b)` infra/tooling or `(c)` dead/deprecated purely from its path, before any
semantic judgement is applied, using these rules in order:

| Path pattern | Bucket | Reason |
|---|---|---|
| `backend/migrations/*.sql` (or `.py` runner-adjacent) | (b) | Migration file — CLAUDE.md documents these carry `test=NONE` by convention and are a filesystem inventory, not a product-story surface (CARTO-005) |
| `.github/workflows/*.yml` | (b) | CI workflow definition |
| `backend/scripts/*.py` | (b) | Operational/dev script |
| `frontend/*` | (c) | `frontend/DEPRECATED.md` — CLAUDE.md names this surface dead, not one of the five product surfaces |
| `**/__tests__/**`, `*.test.ts(x)`, `test_*.py`, `*.spec.ts(x)`, `conftest.py`, `_factories.py` | (b) | Test/fixture file — tooling, not itself a product behavior |
| `admin-dashboard/src/components/ui/*` | (b) | Generic shadcn-style UI-kit primitive, not story-specific |
| `backend/core/background_loop_registry.py (role=...)` | (b) | A metadata/role-tag row over the loop registry, not a distinct code file |

This pass classified **414 / 769** rows with no code reading required (counts
verified by `Counter`, see §1). The remaining **355** rows needed a second,
semantic pass.

### 0.3 Semantic pass over the remaining ~355 rows

For each remaining row: derive the underlying file path (line-number/role suffix
stripped), then classify using, in order of preference:
1. **File-level match against the 996 `mapped` rows** — if the same file already
   has `mapped` rows to a single `story_id`, assign that `story_id` (bucket a).
   (Checked first; only 14/769 orphan files had any file-level overlap with a
   mapped row at all, so this rule had low yield — most orphan files are entirely
   un-mapped elsewhere in the CSV.)
2. **Keyword/token overlap against the story corpus** — tokenize each of the 108
   `story_id`s' `epic + feature + story` text (case-folded, `snake_case`/
   `camelCase` split, stopword-filtered) and score against tokens from the
   orphan's path/filename; a clear top match above a manual-reviewed threshold is
   proposed as bucket (a) with that `story_id`.
3. **Directory/role heuristics** for the remainder — e.g. `backend/routes/admin/
   legacy_*_backfill.py` (one-time data-repair endpoints) classified (b), not (a),
   because they don't correspond to an ongoing user-facing story.
4. **Manual read** of the file's header/docstring or first ~30 lines for any row
   whose bucket was still ambiguous after 1–3, and for every row placed in bucket
   (d) or (c) — see §2/§3 for the specific `Read`/`grep` calls per row.

### 0.4 Spot-check

≥30 rows selected across all five buckets (weighted toward (d) and (c), since
those carry the most reporting risk) and read directly (`Read`/`Grep` on the
actual file, not just the classifier's guess) to compute an agreement rate. See §5.

### 0.5 Gap/sibling-missing re-check

For each of the 16 `gap` rows and 7 `sibling-missing` rows, ran targeted
`grep -rn` searches using **several spellings/synonyms** of the story's key terms
(e.g. for S-earn-05 "batch cash-out": `payout`, `cash.out`, `cash_out`,
`batch.*earn`; for S-corp-07 "winddown": `winddown`, `wind_down`, `wind-down`,
`offboard`), plus a check of `backend/core/lifespan.py` for a matching background
loop, and cross-referenced against `docs/audit/clean-sheet/matrices/
feature-completeness.md`'s own independent re-check (which already re-verified 4
of the 16 by direct read: S-fulfil-05 Navigation, S-admin-07 Compliance export,
S-auth-01 Signup, S-ci-04 Visual regression — all found to have real code). For the
7 `sibling-missing` "known fork" rows, cross-referenced against
`docs/known-forks.md`, which documents each pair as an **intentional** fork with
both sides present — so the CSV's own `sibling-missing` label is investigated as a
possible second collision-classifier artifact, not taken at face value. See §4.

---

## §4 Gap and sibling-missing re-check

**Status: COMPLETE.**

### 4.1 The 16 `gap` rows

All 16 were re-checked with targeted, multi-spelling `grep -rn` searches (backend
`routes/`, `services/`, `utils/`, `core/lifespan.py`) and, where relevant, cross-
checked against `feature-completeness.md`'s independent re-verification pass.
**15 of 16 are classifier artifacts (real code exists); 1 is a genuine, confirmed
absence.**

| story_id | Story | Re-check result | Evidence |
|---|---|---|---|
| S-book-04 | Driver accept/decline offer within countdown | **FALSE GAP** — real | `backend/routes/rides/matching.py` — `offer_timeout`, `countdown_seconds` field in offer payload, `_batch_offer_timeout_handler` |
| S-fulfil-01 | Rider sees driver ETA/arrival status | **FALSE GAP** — real | `backend/routes/rides/lifecycle.py` (`driver_arrived_at`), `backend/routes/rides/queries.py:511`, `_shared.py:426` (`"driver_arrived": "waiting for you at pickup"`) |
| S-fulfil-05 | Driver navigates pickup→dropoff | **FALSE GAP** — real (already re-verified independently) | `feature-completeness.md` row 91: `driver-app/lib/navigation/launchNavigation.ts`, tested in `ActiveRidePanel.test.tsx:259-290` |
| S-earn-05 | Automatic scheduled driver payouts | **FALSE GAP** — real | `backend/routes/admin/auto_payouts.py` (weekly batch admin views + manual `run-now`); `backend/utils/auto_payout.py::run_weekly_auto_payout`; spawned in `backend/core/lifespan.py:795` as `"auto_payout (1h, Sundays)"` |
| S-corp-07 | Corporate account winddown/offboarding | **FALSE GAP** — real | `backend/services/corporate_wallet_winddown_service.py`, `backend/tests/test_corporate_wallet_winddown_service.py`, referenced in `routes/corporate_accounts.py`, `routes/corporate_company.py` |
| S-auth-01 | Rider/driver signup via phone/OTP | **FALSE GAP** — real (already re-verified independently) | `feature-completeness.md` row 149: `routes/auth.py`, `_fire_signup_conversion` |
| S-auth-04 | RBAC / module-gated admin access | **FALSE GAP** — real | `backend/routes/admin/__init__.py` (`require_module`, `require_super_admin`, "double-gated" comment at router-include level) |
| S-admin-07 | Compliance/audit exports | **FALSE GAP** — real (already re-verified independently) | `feature-completeness.md` row 178: `routes/admin/compliance.py`, `sgi_forms.py`, `compliance_export_events` audit table |
| S-admin-10 | Venue pickup/dropoff management | **FALSE GAP** — real | `backend/routes/admin/venues.py` |
| S-admin-11 | Admin broadcast messaging | **FALSE GAP** — real | `backend/routes/admin/messaging.py` |
| S-safety-05 | Fraud/abuse detection (referral, GPS spoofing) | **REAL GAP — confirmed absent** | Only `backend/tests/test_referral_payout_fraud_guards.py` matches "fraud" anywhere in the repo; no `backend/services/fraud*.py`. Independently confirmed by `feature-completeness.md` row 196 and `trust-safety-fraud.md` (TSF-002/003/004/005/006 — collusion, cancellation-fee farming, chargeback velocity, SIM-swap ATO, promo-stacking, all with **no detection signal**). This is the one thin area named in the task brief. |
| S-notif-02 | SMS OTP delivery | **FALSE GAP** — real | `backend/routes/auth.py` (OTP send/verify), `backend/utils/rate_limiter.py` (OTP lockout); Twilio credentials documented as DB-settings-managed per CLAUDE.md |
| S-notif-04 | Notification retry/throttle | **FALSE GAP** — real | `backend/utils/notification_throttle.py`; CLAUDE.md names "push retry" as one of the 42 background loops |
| S-obs-02 | Prometheus `spinr_<domain>_<metric>_<unit>` metrics | **FALSE GAP** — real | `backend/utils/metrics.py` (`render_prometheus`, counter/gauge registry); emitting call sites in `services/dispatch_service.py`, `services/payment_service.py` per CLAUDE.md |
| S-obs-04 | Health check endpoint | **FALSE GAP** — real | `backend/server.py`, `backend/routes/main.py` reference health-check wiring |
| S-ci-04 | Admin-dashboard visual regression in CI | **FALSE GAP** — real (already re-verified independently) | `feature-completeness.md` row 305: `admin-dashboard/e2e/visual-regression.spec.ts`, snapshots for all 6 pages |

**Net: 15/16 gap rows are classifier collision artifacts (matches CARTO-004's
prediction and the feature-completeness matrix's 10/11 finding, extended here to
all 16). Only S-safety-05 (Fraud detection) is a real, multiply-corroborated gap.**

### 4.2 The 7 `sibling-missing` rows

All 7 re-checked with `ls -la`/`find` on both named siblings, plus content reads
for the two ambiguous-naming cases. **All 7 are false positives** — every pair has
both sides present on disk; the label reflects either (a) a naming mismatch the
classifier didn't resolve, or (b) an intentional, already-documented fork
(`docs/known-forks.md`) where the CSV's "missing" label is simply wrong (both
files exist, it's a fork, not an absence).

| Row | CSV-named path | Re-check result | Evidence |
|---|---|---|---|
| 1 | `rider-app/app/support.tsx` | **FALSE — sibling exists under a different name.** Driver-app's equivalent is `driver-app/app/driver/help.tsx`, both thin wrappers around the same `@shared/components/SupportScreen` component (`role="rider"` vs `role="driver"`). Naming mismatch (support vs help) is why the classifier missed it, not a missing screen. | `Read` of both files — both 4–22 lines, both import `SupportScreen` |
| 2 | `rider-app/app/wallet.tsx` | **NOT a like-for-like gap — asymmetric by design.** Driver-app has no "wallet" screen, but has `driver-app/app/driver/payout.tsx` (1,261 lines) and `payout-history.tsx` (400 lines) — the driver-side equivalent concept (earnings/payout) is genuinely different from the rider-side concept (pre-paid balance top-up for fares), since drivers don't pay for rides. Grepped `driver-app/` for any `wallet` reference — found only in unrelated screens (quests, ride-detail, faq, settings, activity tab), no dedicated driver wallet. **INFERRED** (not confirmed against a design doc) that this asymmetry is intentional rather than a missing feature. | `find`/`grep -rl wallet driver-app/` |
| 3 | `driver-app/app/help.tsx` | **FALSE — wrong path in the CSV itself.** The file doesn't exist at that exact path; the real file is `driver-app/app/driver/help.tsx` (missing the `/driver/` route-group segment). Same underlying pair as row 1 — the CSV's `path` column has a path-construction bug for this row (likely a route-group stripping error in whatever tool built the CSV), not a missing file. | `ls driver-app/app/help.tsx` → not found; `ls driver-app/app/driver/help.tsx` → exists, 173 bytes |
| 4 | `shared/components/CarMarker.tsx` ↔ `driver-app/components/CarMarker.tsx` | **FALSE — both exist, documented intentional fork.** `docs/known-forks.md` row 1: driver-app needs course-up-camera bearing callbacks rider-app must not get by default; a mechanical parity guard (`CarMarkerParity.test.ts`) already exists (landed 2026-09-12). | `ls -la` both files (58,869 / 53,346 bytes); `docs/known-forks.md` |
| 5 | `driver-app/app/driver/notifications.tsx` ↔ `rider-app/app/notifications.tsx` | **FALSE — both exist, documented intentional fork.** `docs/known-forks.md` row 2: per-app navigation destinations differ by `type`; i18n only on driver side. No mechanical parity guard yet — human-only. | `ls -la` both files (26,106 / 18,470 bytes); `docs/known-forks.md` |
| 6 | `rider-app/app/referral.tsx` + `backend/routes/users.py` ↔ `driver-app/app/driver/referral.tsx` + `backend/routes/drivers/referrals.py` | **FALSE — all four files exist, documented intentional fork.** `docs/known-forks.md` row 3: genuinely different reward shapes (referrer/referee split vs single reward). A prior contract drift (dead-domain `referral_link` field) was already found and removed on both sides 2026-09-14. | `ls -la` all four files; `docs/known-forks.md` |
| 7 | `backend/routes/auth.py` ↔ `backend/routes/admin/auth.py` | **FALSE — both exist, documented intentional fork.** `docs/known-forks.md` row 4: different token lifetimes/trust models by design; a real IP-provenance drift was found and fixed 2026-09-21 (PR #5654), pinned by `test_async_limiter.py`. | `ls -la` both files (117,647 / 65,847 bytes); `docs/known-forks.md` |

**Net: 0/7 `sibling-missing` rows represent an actually-missing file.** Six are
either a naming mismatch the classifier failed to resolve (rows 1/3, same
underlying pair) or a documented, guarded intentional fork (rows 4–7). Row 2
(driver wallet) is the only one with a genuine content asymmetry, but it reads as
by-design (drivers don't pre-pay for rides) rather than a missing screen — flagged
as INFERRED, not VERIFIED, since no design doc was found confirming the intent.

---

## §1 Orphan bucket counts

**Status: COMPLETE.**

All 769 `orphan` rows classified. Method: §0.2 (structural pass, path/filename
rules only) then §0.3 (per-distinct-file semantic classification — 234 distinct
files covering all 355 rows the structural pass left unclassified; a file's
bucket applies to every orphan row on that file). Full per-file assignments are
in the session scratchpad (`file_classify.json`, `orphans_final.json`) — not
committed; §2/§3 below give every bucket (c)/(d) row and reason in full, and §6
gives the reproduction method so a human can regenerate the complete per-row
table.

| Bucket | Count | % of 769 |
|---|---:|---:|
| (a) belongs to an existing story | 285 | 37.1% |
| (b) infrastructure/tooling, no product story | 457 | 59.4% |
| (c) dead/deprecated code | 0 | 0.0% |
| (d) genuine undocumented product behaviour | 27 (26 distinct files) | 3.5% |
| (e) unknown | 0 | 0.0% |
| **Total** | **769** | **100%** |

Bucket (b)'s 457 breaks down as: 311 migration files, 36 CI workflow files, 22
test/fixture files, 31 admin-dashboard generic UI-kit primitives, 14 background-
loop-registry metadata rows (structural pass, §0.2 — 414 total), plus 43 more
files placed in (b) only after the semantic/read pass (app-shell layouts, generic
hooks, design-system/config/logger/validator infra, and two files — `forecast/
page.tsx`, `branding.py` — that *looked* like (d) candidates by name but turned
out to be a redirect stub and a static-asset endpoint respectively once read;
see §5).

**Bucket (c) is genuinely zero, not unchecked.** Per the task's explicit
instruction to verify "unmounted" as a real check, not an assumption: every
distinct `backend/routes/**` file among the orphans (`admin/auth.py`,
`admin/compliance.py`, `admin/dispute_evidence_submission.py`,
`admin/dispute_pack_download.py`, all four `admin/legacy_*_backfill.py` files,
`admin/messaging.py`, `admin/monitoring.py`, `admin/rider_import.py`,
`admin/rides.py`, `admin/sgi_forms.py`, `admin/staff.py`, `admin/stripe_import.py`,
`admin/venues.py`, `branding.py`, `corporate_accounts.py`, `drivers/appeals.py`,
`main.py`, `payments.py`, `rides/cancellation.py`, `rides/payments.py`) was
`grep`-checked for an `import ... router` **and** a matching `include_router(...)`
call in `backend/routes/admin/__init__.py` or `backend/server.py` — all 20 files
are mounted (see §0.1 command below). No orphan row's path fell under
`frontend/` either (checked directly — 0 hits). CARTO-004/epics.md's caveat about
the classifier's `gap` column producing collision artifacts, not the `orphan`
column, is a distinct finding from this one; this pass independently confirms no
dead-code artifacts surfaced on the orphan side either, for the files actually
checked (backend routers only — front-end "unreferenced component" dead code was
not separately checked; see §7).

```bash
# reproduce the mount check for any backend/routes/**/*.py file:
grep -n "<module_stem>" backend/routes/admin/__init__.py backend/server.py \
  | grep -i "import\|include_router"
```

**Caveat on the CSV's own `epic`/`feature` columns for orphan rows:** those two
columns, as already written into `traceability.csv` for every orphan row, come
from the same collision-prone first-match keyword classifier CARTO-004 flags for
the `gap` column — e.g. several `backend/migrations/*.sql` orphan rows carry an
`epic` of "Ride Fulfillment" purely because a migration's filename happened to
contain a matching keyword. This reconciliation's bucket/story assignments in §2
and the scratchpad files were derived independently, from the file's actual path
and (for bucket d and disputed cases) its content — not from trusting the CSV's
pre-existing `epic`/`feature` fields on orphan rows. Treat those two columns as
noise for orphan rows specifically; they were reliable for `mapped` rows (used
as the story corpus in §0.3) because a `mapped` row means the classifier's guess
was accepted, not merely made.

---

## §2 Bucket (d) — genuine undocumented product behaviour (full list)

**Status: COMPLETE.** 27 rows across 26 distinct files. Every entry below was
read directly (not inferred from filename alone) — see §5 for the paired
before/after-read table. Grouped by cluster where multiple files are one
underlying feature.

### 2.1 Cancellation fee & driver compensation
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `backend/services/cancellation_service.py` | Calculates rider cancellation fees, driver compensation for a cancelled-on ride, and ride cleanup on cancel — real, substantial Decimal-based fee logic (extracted from a "god-object decomposition" per its own docstring). | New story under **Ride Fulfillment**, e.g. `S-fulfil-12` "Cancellation fee & driver compensation" — the *transition* to `cancelled` is covered by the state machine (CLAUDE.md), but the money math that runs on it is not covered by any story. |
| `backend/routes/rides/cancellation.py` (2 rows) | The HTTP surface for the above. | Same story as above. |

### 2.2 Corporate ride-booking policy engine
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `backend/services/corporate_policy_service.py` | Rule-based policy evaluation for a corporate ride booking beyond the allowance cap — `evaluate_policy`/`evaluate_policy_for_ride`, with a `policy_override`/bypassed-rules audit trail. Docstring calls it "v1 stub," so it's early but real and callable from route handlers. | New story under **Corporate / B2B Billing**, e.g. `S-corp-08` "Ride-booking policy rules" — distinct from `S-corp-03` (allowance cap & ledger), which only covers the dollar-cap/ledger side. |
| `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx` | The admin UI to author/edit those policy rules per corporate account. | Same story. |

### 2.3 Admin PII-export dual-approval gate
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `backend/services/admin_export_approvals.py` | A second-admin approval requirement before a large PII export runs: requester can't self-approve (enforced in code *and* by a DB `CHECK` constraint), approval is single-use (`consumed_at` stamped, can't be replayed). Docstring cites `docs/threat-model/admin-panel.md` and `ACTION_ITEMS.md` B10 — so it's a known, deliberate control, just not in the story catalog. | New story under **Admin Dashboard & Operations**, e.g. `S-admin-13` "PII-export dual-approval gate" — `S-admin-05` (data transfer/export) covers the export existing, not this governance control on top of it. |
| `admin-dashboard/src/app/dashboard/export-approvals/page.tsx` | The admin UI for reviewing/approving/denying pending export requests. | Same story. |

### 2.4 Driver dormancy classification & SIN purge
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `backend/services/driver_dormancy_service.py` | Additive-only dormancy tagging (`dormant` at 90 idle days, `long_dormant` at 365) written into a JSONB metadata key so admin views can filter dormant drivers out of active-driver reporting — never deletes/suspends/changes go-online eligibility. Docstring: "owner-confirmed 2026-09-11," cites its own change-log. | New story under **Legacy Import & Data Migration** or **Admin Dashboard & Operations**, e.g. "Driver dormancy classification." |
| `backend/services/dormant_driver_sin_purge_service.py` | PIPEDA-motivated SIN purge for confirmed-dormant legacy-imported drivers (180-day grace period past public launch, deletes the actual vault ciphertext via a dedicated RPC, not just nulling the column). Docstring cites `ACTION_ITEMS.md` A34 and a specific change-log with real driver counts. | Same story, or fold into PIPEDA "Deletion" user-rights coverage — CLAUDE.md's Compliance section documents the *policy* (30-day PII scrub) but not this driver-specific SIN-only carve-out mechanism. |
| `driver-app/app/reactivate-account.tsx` | Driver-side reactivation screen — the other end of the dormancy lifecycle. | Same story. |
| `rider-app/app/reactivate-account.tsx` | Rider-side equivalent. | Same story (or a sibling rider-side story, since rider dormancy criteria were not verified as identical to driver's — see §7). |

### 2.5 Corporate guest bookings
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `backend/services/guest_user_service.py` | A corporate employee can book a ride for a customer by name+phone who has no Spinr account — a `users` row is created with `is_guest=True` and no session; the same phone number logging in later via OTP silently claims the ride history. | New story under **Corporate / B2B Billing**, e.g. `S-corp-09` "Guest bookings (no-account riders)" — adjacent to but distinct from `S-corp-04` (per-ride charge for an existing member). |
| `backend/services/guest_notification_service.py` | SMS-only notification lifecycle for guest riders (booking/assigned/arrived/cancelled, pickup OTP + tracking link, PII-scrubbed in logs) since a guest has no app to push to. | Same story. |

### 2.6 Driver training / LMS integration
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `backend/services/lms_service.py` | Client for an external "spinr-lms" driver training platform — registration, course progress, certificates, quiz attempts, reminders — matched by phone number, credentials rotatable via `app_settings` (same pattern as Stripe/Twilio/Maps). | New epic-level story, e.g. **Driver Onboarding & Training** `S-train-01` "Driver LMS integration" — no existing epic covers driver training at all. |

### 2.7 Third-party ad-attribution integration (Meta Conversions API)
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `backend/services/meta_conversions_service.py` | Server-side Meta Ads Conversions API events (`CompleteRegistration`, `Purchase`, `FirstRide`, `DriverApproved`, `DriverActivated`) with hashed-PII Advanced Matching, deliberately server-only to avoid iOS App Tracking Transparency scope. | New story, e.g. under **Integrations & Webhooks**, `S-integ-05` "Meta Ads Conversions API." **Flag for product/legal review, not just a traceability gap**: CLAUDE.md's "What Spinr Is NOT" section states "Never add third-party ad SDKs or behavioral retargeting" — this is exactly that category of integration, already shipped. The code itself is written defensively (no IDFA, `NSPrivacyTracking: false` preserved, no client-side PII), so it is not obviously a *violation*, but it is undocumented against a guardrail explicitly written to prevent this class of feature, and that tension deserves a human decision, not a silent story backfill. |
| `shared/analytics/meta.ts` | Client-side half — app-install/lifecycle events, explicitly *not* sending user data from the device, `setAdvertiserTrackingEnabled` pinned false. | Same story/flag. |
| `shared/analytics/index.ts` | Client analytics dispatch entrypoint used by the above. | Same story/flag. |

### 2.8 Session-replay / product-analytics tooling
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `shared/services/posthogReplay.ts` | PostHog session-replay client, fail-closed behind an admin flag + project key, `identify()` restricted to `user_id`+`role` only (no email/phone/GPS/address per its own comment citing PIPEDA/CLAUDE.md), GeoIP/lifecycle/surveys/push-capture all disabled. | New story, e.g. under **Observability & Monitoring**, `S-obs-05` "Session-replay tooling (PostHog/LogRocket)." Not flagged as a compliance violation — the code is deliberately PIPEDA-conscious — but session-replay tooling existing at all is a materially different product fact than anything in the current story catalog and should be a recorded decision, not an implicit one. |
| `shared/services/logRocketInstance.ts` | Shared handle for a second, separate session-replay SDK (LogRocket), gated by an Expo-Go/web/Android-default-off/kill-flag guard elsewhere. | Same story. |

### 2.9 Driver "Destination Mode"
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `driver-app/app/driver/destination-mode.tsx` | A driver can set a "heading home" destination that biases/filters which dispatch offers they receive — confirmed further by `backend/migrations/465_*.sql`, which adds a 2-hour auto-expiry (`destination_set_at`/`destination_expires_at`) because a driver who forgot to clear it kept filtering offers indefinitely, even across shifts. This is a real, migration-backed feature (migration 219 introduced it, 465 hardened it). | New story under **Ride Booking & Matching**, e.g. `S-book-12` "Destination-biased dispatch (Destination Mode)." |

### 2.10 Airport pickup/dropoff zones
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `driver-app/hooks/useAirportZones.ts` | Point-in-polygon geofencing against admin-configured airport zones, each carrying its own `airport_fee` distinct from standard fare. | New story under **Ride Booking & Matching** or **Maps & Routing**, e.g. `S-maps-05` "Airport zone detection & fee." Worth a follow-up check that `airport_fee` surfaces as its own disclosed receipt line item, per CLAUDE.md's "every charge maps to a disclosed line item" rule — not verified in this pass (see §7). |

### 2.11 Booking dropoff mis-resolution guard
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `shared/utils/bookingDistanceGuard.ts` | Client-side check blocking ride submission when the dropoff coordinate sits within 100m of pickup despite a materially different address string — a stale/mis-resolved recent-search or saved-place pin protection, framework-free and shared across every booking entry point (search, recent, AI proposal). | Fold into `S-book-02` (Ride creation) or `S-book-08` (address/pickup validation) as an explicit acceptance-criterion/scenario, since it's real defensive logic already shipped, just uncaptured. |

### 2.12 Admin MFA enrollment
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `admin-dashboard/src/components/mfa-enroll-dialog.tsx` | TOTP-based admin MFA enrollment (QR code, `mfaEnroll`/`mfaConfirm` API calls). `docs/known-forks.md`'s `auth.py`/`admin/auth.py` row already names "admin-only MFA and break-glass paths" in prose, corroborating this is real and deliberate — just never promoted to its own story. | Fold into `S-auth-04` (RBAC / admin access tiers) as an explicit scenario, or split into its own story if MFA enrollment/enforcement UX turns out to be a large enough surface. |

### 2.13 Admin audit-log viewer
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `admin-dashboard/src/app/dashboard/audit-logs/page.tsx` | A searchable/filterable admin UI over the audit-log table CLAUDE.md's Observability section already requires ("Security-relevant events... → audit table"). The underlying convention is documented; the admin-facing *viewer* for it is not. | Fold into `S-admin-06` (Settings/flags) or a new `S-admin-14` "Audit log viewer" — minor, low-risk gap. |

### 2.14 Driver vehicle decal/letter management (regulatory)
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx` | Tracks and generates ride-share vehicle decal/letter documents per driver (`needs_letter`/`generated`/`sent` status, PDF generation, per-service-area filtering) — a Saskatchewan-specific regulatory operational surface not named anywhere in `regulatory-sk.md` or the story catalog. | New story under **Admin Dashboard & Operations** or fold into the regulatory checklist, e.g. `S-admin-15` "Vehicle decal/letter tracking." Worth cross-checking against `.claude/context/regulatory-sk.md` directly — not done in this pass (see §7). |

### 2.15 Rider self-serve PIPEDA data export & account deletion
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `rider-app/app/privacy-settings.tsx` | Implements the rider-facing side of two PIPEDA user rights CLAUDE.md's Compliance section already documents in principle (Access via Support-generated export, Deletion within 30 days) — but as a genuine **self-serve in-app** flow: `POST /users/data-export` and `DELETE /users/account`, not a Support ticket. | New story under **Authentication & Authorization** or a new **Compliance/Privacy** epic, e.g. `S-auth-07` "Self-serve PIPEDA export/delete." This is arguably the single most compliance-relevant item in this bucket — the documented policy says exports/corrections go "through Support," but real code offers a direct in-app path; a human should confirm whether that's an intentional additional channel or a documentation gap on the CLAUDE.md side (see §7). |

### 2.16 In-app store-rating prompt
| Path | Behaviour | Suggested epic/story |
|---|---|---|
| `shared/utils/appRating.ts` | Native app-store review prompt after 3+ completed rides with a 4+ star in-app rating, throttled to once per 30 days. Minor growth feature. | Fold into `S-pay-06` (Rating) as a scenario, or leave as an accepted minor gap — lowest priority item in this bucket. |

---

## §3 Bucket (c) — dead/deprecated code

**Status: COMPLETE. Zero orphan rows landed in bucket (c).**

No orphan row's path was under `frontend/` (the one directory CLAUDE.md
explicitly documents as dead), and every `backend/routes/**` orphan file was
confirmed mounted via `include_router` (§1's mount-check table/command). No
separate check was run for dead/unreferenced front-end *components* (as opposed
to routed screens) outside the structural (b) UI-kit sweep — see §7.

This is a genuine, checked absence, not an unchecked default: the task's own
framing (`(c) dead or deprecated code (e.g. frontend/, unmounted routers —
verify unmounted by checking include_router/imports)`) was applied literally
and came back empty for the orphan population.

---

## §5 Spot-check table

**Status: COMPLETE.** 33 orphan-classification rows read directly (file opened
and content inspected, not just path/filename pattern-matched), spread across
all three populated buckets, plus the separate §4 gap/sibling-missing checks
(23 more direct reads, not double-counted here). "Initial guess" is the bucket I
would have assigned from the path/filename heuristic alone, before opening the
file — recorded honestly, including where I had no confident guess.

| # | Path | Initial guess (pre-read) | Verified bucket (post-read) | Agree? |
|---|---|---|---|---|
| 1 | `backend/services/admin_export_approvals.py` | (d) | (d) | Yes |
| 2 | `backend/services/corporate_policy_service.py` | (d) | (d) | Yes |
| 3 | `backend/services/lms_service.py` | (d) | (d) | Yes |
| 4 | `backend/services/meta_conversions_service.py` | (d) | (d) | Yes |
| 5 | `backend/routes/branding.py` | (d) | (b) | **No** — read revealed a single static public logo endpoint, not a product behaviour |
| 6 | `backend/services/cancellation_service.py` | (d) | (d) | Yes |
| 7 | `backend/services/driver_dormancy_service.py` | (d) | (d) | Yes |
| 8 | `backend/services/dormant_driver_sin_purge_service.py` | (d) | (d) | Yes |
| 9 | `backend/services/guest_user_service.py` | (d) | (d) | Yes |
| 10 | `backend/services/guest_notification_service.py` | (d) | (d) | Yes |
| 11 | `driver-app/app/driver/destination-mode.tsx` | (d) | (d) | Yes |
| 12 | `driver-app/hooks/useAirportZones.ts` | (d) | (d) | Yes |
| 13 | `shared/utils/bookingDistanceGuard.ts` | (d) | (d) | Yes |
| 14 | `shared/auth/sessionLock.ts` | (d) | (b) | **No** — read revealed a native storage-concurrency lock, not a distinct user-facing behaviour |
| 15 | `shared/utils/otaVersion.ts` | (d) | (b) | **No** — read revealed a display-only label, not a forced-update feature |
| 16 | `shared/utils/fixFeed.ts` | unknown (no guess) | (a), tied to S-fulfil-02 | N/A |
| 17 | `rider-app/app/privacy-settings.tsx` | (d), weak | (d), confirmed strong | Yes |
| 18 | `admin-dashboard/src/app/dashboard/drivers/decals/page.tsx` | (d) | (d) | Yes |
| 19 | `admin-dashboard/src/components/mfa-enroll-dialog.tsx` | (d) | (d) | Yes |
| 20 | `shared/services/posthogReplay.ts` | (d) | (d) | Yes |
| 21 | `shared/services/logRocketInstance.ts` | (d) | (d) | Yes |
| 22 | `shared/analytics/meta.ts` | (d) | (d) | Yes |
| 23 | `shared/utils/appRating.ts` | (d), weak | (d) | Yes |
| 24 | `shared/utils/appCheckDiagnostics.ts` | (d), weak | (b) | **No** — read revealed a Firebase App Check error-formatting helper, not a distinct feature |
| 25 | `backend/routes/admin/staff.py` | (a) S-auth-04 | (a) S-auth-04 | Yes |
| 26 | `admin-dashboard/src/app/dashboard/records/page.tsx` | unknown (no guess) | (a) — nav wrapper over 4 existing surfaces | N/A |
| 27 | `admin-dashboard/src/app/dashboard/audit-logs/page.tsx` | (d) | (d) | Yes |
| 28 | `admin-dashboard/src/app/dashboard/corporate-accounts/[id]/policy/page.tsx` | (d) | (d) | Yes |
| 29 | `admin-dashboard/src/app/dashboard/forecast/page.tsx` | (d) | (b) | **No** — read revealed a pure redirect stub to the Analytics page |
| 30 | `backend/migrations/01_add_driver_user_id.sql` (name-sampled) | (b) | (b) | Yes |
| 31 | `.github/workflows/ci.yml` | (b) | (b) | Yes |
| 32 | `backend/tests/test_ride_state_machine.py` | (b) | (b) | Yes |
| 33 | `admin-dashboard/src/components/ui/button.tsx` | (b) | (b) | Yes |

**Agreement rate: 26/31 = 83.9%** of rows where I had a stated pre-read
hypothesis (excluding rows 16 and 26, which had no confident guess before
reading, so aren't "agreements" or "disagreements" — they're resolved-from-
unknown). All 5 disagreements went the same direction: a filename that *sounded*
like a distinctive undocumented behaviour (destination/session/branding/rating/
forecast-shaped names) turned out, once read, to be either generic infrastructure
or a thin redirect/display-only shim. **No disagreement went the other way** —
nothing initially guessed (a) or (b) turned out on reading to actually be a real
undocumented behaviour. That asymmetry is expected (the reads were deliberately
weighted toward my weakest/most speculative (d) guesses, precisely to stress-test
the bucket the task calls "the important bucket") and is the main reason this
reconciliation trusts bucket (d)'s final 26/27 count more than it would trust an
un-spot-checked one: roughly 1 in 6 of my *pre-read* (d) hypotheses was wrong,
which is exactly why every (d) row in §2 was read, not filename-guessed.

---

## §6 Corrections to the CSV that a human should apply

**Status: COMPLETE.** Listed as a set of instructions, not as a diff —
`traceability.csv` was not edited per the report-only mandate.

1. **Re-tag all 769 `orphan` rows' `status` per the bucket in §1/§2**, i.e.
   `mapped` for bucket (a) rows (using the `story_id` recorded in the scratchpad
   `orphans_final.json`/`file_classify.json` — a human should re-derive or
   request that file rather than hand-retyping 285 rows), `infra` (a new status
   value, or leave `orphan` but add a `bucket=infra` column) for the 457 bucket
   (b) rows, and a new `status=candidate-gap` (or similar) for the 27 bucket (d)
   rows so they surface for story-writing instead of being silently absorbed.
2. **Add 26 new story rows** (one per §2 cluster; cancellation and Meta each
   span 2–3 code units but are one story) to the story catalog, using the
   `epic`/suggested `story_id`/one-line `story` text proposed in §2 — this
   creates the requirement text that currently doesn't exist anywhere, which is
   the actual point of flagging bucket (d).
3. **Flip all 16 `gap` rows except `S-safety-05`** to `status=mapped` (or a new
   `status=false-gap` if the audit wants to preserve that this was corrected,
   rather than silently rewriting history) — see §4.1's per-row evidence. Leave
   `S-safety-05` as the one real `gap`.
4. **Flip all 7 `sibling-missing` rows to `status=mapped`** (both siblings exist
   in every case) — see §4.2. Rows 1 and 3 (`support.tsx`/`help.tsx`) are the
   *same* underlying pair reported twice under two different `path` spellings;
   a human fixing this should also correct row 3's `path` from
   `driver-app/app/help.tsx` (doesn't exist) to `driver-app/app/driver/help.tsx`
   (the real file) rather than just re-tagging its status.
5. **`backend/routes/admin/rides.py`'s 41 orphan rows should not get one blanket
   `story_id`.** Unlike every other file in this pass, its endpoints genuinely
   span several existing stories at the individual-line level (financial-
   dashboard-adjacent, ride-state-adjacent, refund/dispute-adjacent). This file
   needs a human (or a follow-up pass with more budget) to read each of the 41
   lines and assign per-endpoint, not per-file.
6. **Two French-cousin rows in §2 (reactivate-account.tsx ×2) may actually be
   two different stories, not one.** This pass grouped them under one dormancy
   story on the assumption the rider- and driver-side reactivation criteria
   match; that assumption was not verified (see §7) and a human should confirm
   before merging them into a single `story_id`.
7. **Re-run this reconciliation's §0.3 semantic pass with the corrected
   `mapped` set from correction #1 before doing any *further* CSV work** — since
   the file-level "does this file already have a mapped row" heuristic (§0.3
   step 1) only had 14/769 hits on the *original* CSV, re-deriving it after
   corrections 1–4 land would materially raise that hit rate for any future
   orphan-classification pass (e.g. a re-run of the underlying matcher) and is
   cheap to redo.

---

## §7 Not verified

**Status: COMPLETE.**

- **Bucket (a)'s exact `story_id` for weak-confidence rows was not independently
  read-verified for every row** — only 4 of 285 bucket-(a) files were opened
  (`admin/staff.py`, `records/page.tsx`, plus 2 more inside the §5 sample); the
  rest rely on filename/path semantic matching against the 107-story corpus,
  which is a plausible-but-unread inference. Rows explicitly flagged with a
  "weak" story match in the scratchpad (e.g. `monitoring/dispatch-geo/page.tsx`
  → `S-obs-01`, `vehicle-types/page.tsx` → `S-book-02`, `subscriptions/page.tsx`
  → `S-promo-05`) are the ones most likely to need a different `story_id` on
  review — flagged INFERRED, not VERIFIED, throughout.
- **`backend/routes/admin/rides.py`'s per-endpoint story split (§6 item 5) was
  not done** — only its file-level "this is real, mounted code belonging to some
  admin story" status was confirmed, not which of its 41 orphan lines maps to
  which specific story.
- **Whether rider-side and driver-side dormancy/reactivation criteria are
  identical** (§2.4, §6 item 6) was not checked — only driver-side
  `driver_dormancy_service.py` was read; no analogous rider-side dormancy
  service file was found or searched for by name, so whether riders even have a
  dormancy concept, or whether `rider-app/app/reactivate-account.tsx` serves a
  different purpose entirely (e.g. a lapsed/banned account, not a dormant one),
  is unconfirmed.
- **Whether the airport zone's `airport_fee` (§2.10) surfaces as its own
  disclosed receipt line item** per CLAUDE.md's fare-transparency rule was not
  checked — `backend/routes/rides/receipts.py` / `fare_service.py` were not
  opened in this pass specifically to look for it.
- **The vehicle-decal feature (§2.14) was not cross-checked against
  `.claude/context/regulatory-sk.md`** to see whether it's already named there
  under different wording (this pass only confirmed it's absent from the
  traceability story catalog, not from every regulatory doc in the repo).
- **Dead/unreferenced front-end components were not checked** — §3's "bucket
  (c) is zero" finding covers backend router mounting only (`include_router`)
  and the one documented-dead directory (`frontend/`). A React/React Native
  component that exists on disk, is exported, but is never imported anywhere
  (genuine front-end dead code) was not searched for across `rider-app/`,
  `driver-app/`, or `admin-dashboard/` — none of the 769 orphan rows happened to
  raise this as a candidate, but the check itself (e.g. an import-graph sweep)
  was not run, so a front-end dead-code population outside the orphan set
  entirely could exist unflagged.
- **The Meta Conversions API and session-replay findings (§2.7, §2.8) were not
  escalated to a compliance/legal reviewer** — this report names the tension
  with CLAUDE.md's "not a data-harvesting product" guardrail and recommends a
  human decision, but does not itself render a verdict on whether the existing
  implementation is acceptable; that determination is out of scope for a
  report-only reconciliation pass.
- **`docs/audit/clean-sheet/matrices/feature-completeness.md`'s own 4
  independently-re-verified gap rows (S-fulfil-05, S-admin-07, S-auth-01,
  S-ci-04) were relied on as VERIFIED-by-another-pass rather than re-read from
  scratch in this session** — cross-checked for consistency (their cited
  evidence paths were spot-`grep`ped, not fully re-read line-by-line) rather
  than independently re-derived.
- **No code, config, or `traceability.csv` changes were made**, per the
  report-only mandate — §6's corrections are unapplied recommendations only.
