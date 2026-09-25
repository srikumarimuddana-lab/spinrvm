# Traceability Orphan Reconciliation

Report-only. Reconciles the 769 `orphan`, 16 `gap`, and 7 `sibling-missing` rows in
`docs/audit/clean-sheet/traceability.csv` (1,788 rows total, 996 `mapped`), per
CARTO-004's finding (`docs/audit/clean-sheet/01-inventory/epics.md`) that the
automated PRD-to-code matcher's `gap` rows were mostly keyword-collision artifacts,
not real absences. No code, config, or CSV changes were made. This document is
being written incrementally — §0 and §4 are complete and stable; later sections are
filled in as each pass finishes, so a stop mid-task loses only unwritten sections.

**Status of this document: IN PROGRESS.** Sections are appended as completed; see
the per-section status line.

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

*(Remaining sections — §1 bucket counts, §2 bucket (d) full list, §3 bucket (c)
list, §5 spot-check table, §6 CSV corrections, §7 not verified — are being filled
in next; this file will be re-published as each is appended.)*
