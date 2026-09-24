# 00 — History: How Spinr Got Here

**Lane:** R1 Historian · **Wave:** W0 · **Programme:** Spinr Clean-Sheet Rebuild Audit (full)
**Date:** 2026-09-24 · **Mode:** report-and-recommend only (this file is the lane's only write)

> Every statement carries an evidence label: **VERIFIED** (read the code/doc/API directly), **INFERRED** (reasoned from evidence, not executed), **ASSUMED** (needed to proceed; a human must confirm), **UNKNOWN** (must be obtained; who can answer is named). Severity per `audit-framework/templates/run-audit.md`; priority score = severity × blast radius × likelihood on a 1–5 scale each.

## Evidence scope — what history was actually read

| Source | Coverage actually read | Label |
|---|---|---|
| Local git clone | **Shallow: 319 commits, 2026-09-21 → 2026-09-24 only** (`git log` oldest = 2026-09-21). Used only for last-3-day fix-commit frequency. | VERIFIED |
| GitHub API (`list_commits`, `list_pull_requests`, `search_pull_requests`, owner `srikumarimuddana-lab`, repo `spinrvm`) | Full default-branch history from PR #1 (2026-04-16) to PR #5756 (2026-09-24). Per-file commit counts were fetched path-scoped, paginated at 100, for 21 hotspot files (§2). Codex-reviewer PR counts fetched with `commenter:app/chatgpt-codex-connector`. | VERIFIED |
| `ACTION_ITEMS.md` | 29,244 lines; all `###` headings enumerated (101 open `- [ ]`, 221 closed `- [x]` list items); ~60 entries read in full or header+status. | VERIFIED |
| `docs/change-log/` | All 1,470 files (2026-07-26 → 2026-09-24) scanned by script for backticked file-path citations (normalized to repo paths, counted as distinct entries per path) and for family keywords; ~35 entries read for issue/root-cause text. | VERIFIED |
| `docs/incidents/` (3), `docs/adr/` (16), `docs/runbooks/` (56), `.claude/context/*.md` (10), `.claude/agents/*.md` (28), `docs/known-forks.md`, `docs/PRD.md`, `CLAUDE.md`, `AGENTS.md` (via C84) | Read for the drift and timeline sections; not every runbook was read line-by-line. | VERIFIED (partial) |
| `.github/workflows/*.yml` (52 files) | All 52 grepped for `continue-on-error`, `schedule:`, `workflow_dispatch`, `secrets.*`, `if:` gating; ~12 read in part. | VERIFIED |
| Backend code | Spot-verified for every doc-vs-code claim in §4 (paths:lines cited). Not a code audit. | VERIFIED per row |
| Rapid baseline (`docs/audit/clean-sheet/rapid-baseline-2026-09-24/standards-verified.md` §2) | Used as the starting hypothesis for the five known families; each re-verified against primary sources below. | VERIFIED |

**Not read:** closed GitHub issues (`[CR]` issues are cited only where `ACTION_ITEMS.md` links them), PR review threads other than the Codex count, GitHub branch-protection settings (no session has ever had that access — `docs/audit/2026-08-27-cicd-gates-guardrails-audit.md:29`), production Supabase/Fly/Railway state, GitHub Actions run history beyond what `ACTION_ITEMS.md` records.

**Velocity context (VERIFIED):** 5,756 PRs in 161 days (~36/day); 1,470 change-log entries in 61 days (~24/day; weekly: W31=279, W32=61, W33=119, W34=267, W35=124, W36=192, W37=167, W38=111, W39=149 partial); the shallow clone shows ~106 commits/day over its 3-day window. Most commits are authored by agent sessions (commit trailers `Co-developed with Claude Code`; PR #5748's review was by Codex). This matters for every family below: the same class of bug is being found and fixed by *different* sessions that do not share working memory, so recurrence is the default outcome unless a mechanical gate exists.

---

## §1 Recurrence families

Method: started from the five families in the rapid baseline, verified each against `ACTION_ITEMS.md` headers, change-log root-cause text, and code; then swept the change-log filenames and `ACTION_ITEMS.md` headings for any other root cause fixed ≥ 2 times. A family is listed only where the *root cause* (not just the symptom area) recurs. Where an existing `ACTION_ITEMS.md` id covers the family, it is named — none of these are filed as new items; the point of each card is *why the systemic fix never landed*.

### HIST-001 — Float-on-NUMERIC money writes (Decimal lost at the DB boundary)
- Hierarchy: L2 Payments & Ledger › L3 Fare/payout persistence › L4 money-write helpers › L5 "a Decimal is `float()`-cast into a NUMERIC column"
- Severity: HIGH   Priority score: 4×4×3 = 48
- Status: VERIFIED   Existing item: B28, B29, B30, B35, B36 (all closed); no umbrella item
- Adversary: auditor / plaintiff's lawyer (cent-level receipt discrepancies), regulator (GST/PST line items)
- Evidence — occurrences (all VERIFIED from `ACTION_ITEMS.md` headers and change-log filenames):
  1. B28 `payouts.amount` legacy FLOAT column — found 2026-08-17 by `spinr-money-auditor` on the legacy-payout-correction write path; closed 2026-08-18 by migration 331 (`ALTER … TYPE NUMERIC(10,2)`), `docs/change-log/2026-08-18-b28-payouts-amount-numeric.md`.
  2. B29 `booking_import_service.py` `float()` on already-NUMERIC `rides` columns — found *while reviewing B28's own scoping claim*, 2026-08-18; `2026-08-18-b29-booking-import-rides-numeric.md`.
  3. B30 `routes/rides/_shared.py` `float(_round(...))` on the same four columns, **live booking path** — found *by B29's blast-radius grep*, 2026-08-18; closed 2026-08-20 (`2026-08-20-b30-shared-fare-float-fix.md`). Its own status line admits "this entry never updated to reflect" the close (`ACTION_ITEMS.md:7715`).
  4. B35 `routes/rides/booking.py` promo branch — found *while fixing B30*, 2026-08-20, PR #4312; original filing was 8-of-9 false positives from a grep-only claim (`ACTION_ITEMS.md:7928-7936`).
  5. B36 `fare_service.recalculate_fare_for_distance` — flagged as "adjacent, NOT fixed here" in B35's PR, closed 2026-08-20 (`2026-08-20-fare-service-recalc-float-fix.md`).
  6. Earlier/adjacent same-class fixes: `2026-08-12-driver-earnings-decimal-fix.md`, `2026-08-19-admin-decimal-round-convention-fix.md`, `2026-08-22-payouts-decimal-round-half-up-fastfollow.md`, `2026-08-22-receipt-files-semgrep-coverage.md` (receipt generators were outside the semgrep gate's scope until found post-ship).
  7. PR #5 (2026-04-19, "Improve rate limiting, payment idempotency, and financial precision") — the *first* money-precision fix is three days into the repo's history (`list_pull_requests` asc).
- Timeline: 2026-04-19 (PR #5) → 2026-08-12 → 2026-08-17…08-22 (five-link chain in six days) → gate widened 2026-08-22.
- What happens (plain language): a rider's `grand_total` or a driver's payout is written with binary-float rounding; receipts can disagree with the ledger by a cent, and GST/PST line items (regulatory) inherit the error.
- Root cause: the repository layer (`repositories/_base.py`) accepts untyped dicts, so nothing at the write boundary knows a column is NUMERIC; the only guards are (a) `.claude/hooks/pre-commit` check 6, which is **WARNING-only** (`.claude/hooks/pre-commit:113-119`, VERIFIED) despite `CLAUDE.md` saying "a pre-commit hook blocks float arithmetic", and (b) CI semgrep rule `spinr-no-float-in-money` (`.semgrep/spinr-rules.yml:48`) which is blocking (`security-gates.yml:359` "Money-safety gate (SR-03, blocking)") but carries 7 `pattern-not`/`pattern-not-inside` exclusions and does not run pre-commit (0 `semgrep` references in the hook).
- Why the systemic fix never landed: each link was discovered by the *previous* fix's blast-radius grep rather than by a one-time repo-wide sweep — `ACTION_ITEMS.md` contains 13 entries phrased "found while fixing/investigating/verifying …" (VERIFIED count). The schema fix (B28) was done for one column; a typed write boundary (or a DB-side `CHECK`/domain type + a test that asserts every `float(` in a money module is inside `_f()`) was never proposed as a single item. `CLAUDE.md`'s "Decimal only" rule is prose.
- Recommendation: one item, not five — (1) a money-column registry (table.column → NUMERIC) generated from `information_schema` in CI, (2) a repository-level write guard that rejects `float` values for registered columns (fail loud, 500 in dev/test, metric in prod), (3) flip pre-commit check 6 to blocking or delete it and say so in CLAUDE.md. Alternative considered: keep expanding the semgrep rule — rejected because exclusions already grew to 7 and it cannot see the column type.
- Blast radius: every writer of `rides`/`payouts`/`driver_earnings`/`financial_events` money columns (B29's list: 11 columns); `_f()`/`_fd()` helpers in `fare_service.py`.
- Rollout: additive guard behind a settings flag; Rollback: flag off (no data change).
- Verification to close: a test that inserts `float(1.1)` into a registered column and asserts rejection; grep shows zero `float(` outside `_f`/`_fd` in money modules.

### HIST-002 — CarMarker fork divergence (and a third, un-registered copy)
- Hierarchy: L2 Rider/Driver ride experience › L3 Live vehicle marker › L4 `CarMarker.tsx` (×2) + tracking-page marker › L5 "fix lands in one copy, sibling stays broken"
- Severity: HIGH   Priority score: 3×4×5 = 60
- Status: VERIFIED   Existing item: C90 (open), C100 (closed ×2 — duplicate id), C101 (closed), C70 (open); `docs/known-forks.md`
- Adversary: competitor (visible "car drives sideways" in a live-tested app), abusive rider (screenshots)
- Evidence — occurrences:
  1. `docs/known-forks.md:8-13` (VERIFIED): the two copies "had diverged five separate times, with fixes ported one-way by hand"; the sideways-car bug fixed in driver-app on 2026-09-11 "was still live for every rider-app user a day later, discovered only because an audit happened to look."
  2. `2026-09-07-rider-app-marker-parity-fix.md` — two 2026-09-05 driver-app smoothing fixes never ported to `shared/`.
  3. `2026-09-11-rider-app-marker-first-fix-snap-parity.md` — same defect ported after being "deliberately not touched" for surgical scope.
  4. `2026-09-12-carmarker-route-rebase-and-android-rotation-port.md` (C101) — route-rebase + Android rotation missing from `shared/`.
  5. `2026-09-12-carmarker-fork-parity-guard.md` — `shared/components/__tests__/CarMarkerParity.test.ts` lands; it diffs the **props interface only** (`docs/known-forks.md` "Parity guard" column: "an internal-logic-only divergence … still needs a human to notice").
  6. `2026-09-15-android-carmarker-frozen.md` — Android car frozen, route erases (driver-app).
  7. **`2026-09-22-track-page-car-marker-never-rotated.md`** — a *third* marker implementation on the public share-link page `admin-dashboard/src/app/track/[rideId]/page.tsx` (4 fix commits in the last 3 days, shallow log) drew the car pointing north on a live trip in Regina. This copy is **not** in `docs/known-forks.md` (VERIFIED: registry has 4 rows, none for the track page).
  8. C100: the driver-app test file for CarMarker was broken by an `expo-image` migration and stayed red on `main` until 2026-09-11 (`ACTION_ITEMS.md:13519`, `:26888` — filed twice).
  9. C90/C70: every phone-screen and Android-Auto marker fix since 2026-08-16 is "JS-level only, unverified on real hardware" (open).
- Full-history commit counts (VERIFIED, GitHub `list_commits` path-scoped): `driver-app/components/CarMarker.tsx` 34, `shared/components/CarMarker.tsx` 25, `driver-app/lib/androidAuto/carSurface.tsx` 100; change-log entries citing: 34 / 26 / 35. 26 change-log filenames contain `marker`/`carmarker`.
- Timeline: 2026-08-28 (heading-zero) → 08-30 (×4 rounds 7/8) → 09-02 → 09-05 (driver-only fixes) → 09-07 (port) → 09-11 (driver fix) → 09-12 (audit finds two more misses; registry + guard) → 09-15 → 09-22 (third copy).
- What happens: a rider watches their assigned car glide sideways or freeze; the public tracking link a rider shares with family shows the car pointing the wrong way.
- Root cause: duplication is the codebase's default answer to "one app needs different behaviour" (`known-forks.md` "why forked"); the parity guard covers props, not logic; the pre-commit registry check 11 is warning-only (`.claude/hooks/pre-commit:253-266`, VERIFIED); no device verification exists in any agent session (C103).
- Why the systemic fix never landed: `docs/known-forks.md` itself says "This is a stopgap, not a fix." The obvious systemic fix — one component with a `courseUp` prop — was rejected as scope on each occasion ("surgical change" rule in CLAUDE.md), and each session that touched one copy was following that rule correctly. The rule optimizes for small diffs, and small diffs are exactly how a fork drifts.
- Recommendation: reconcile into one `shared/components/CarMarker.tsx` with driver-only props gated by an explicit flag (delete the driver-app copy); register the track-page marker in `known-forks.md` today; make check 11 blocking when both files exist and only one is staged (allow-override via commit trailer). Alternative: keep the fork and extend the guard to diff function bodies — rejected: a body diff of intentionally different files is noise.
- Blast radius: rider-app ride screens (`ride-options.tsx`, `ride-in-progress.tsx`, `driver-arriving.tsx`), driver-app dashboard, Android Auto surface, admin track page.
- Rollout: flagged component swap; Rollback: flag back to old component (no data).
- Verification to close: `CarMarkerParity.test.ts` becomes unnecessary (single file); device pass recorded per C90.

### HIST-003 — Legacy-import exclusion and visibility (one predicate at 59 call sites)
- Hierarchy: L2 Legacy migration (dual-run) › L3 Earnings/stats/promo/admin aggregates › L4 `EXCLUDE_LEGACY_RIDES` › L5 "the filter is wrong, missing, or the product decision flipped"
- Severity: CRITICAL (was: drivers saw $0.00 in production)   Priority score: 5×4×3 = 60
- Status: VERIFIED   Existing item: A25, A26, A27, A28, A30, A31, A32, A33, A34 (duplicate id), A41, B31
- Adversary: plaintiff's lawyer (driver earnings misstatement), regulator (T4A), fraudster (promo eligibility, B31)
- Evidence — occurrences:
  1. A26 (2026-08-11, CRITICAL): `EXCLUDE_LEGACY_RIDES` compiled to an unsatisfiable predicate → a real driver's balance read `$0.00`; fixed by adding `$eq` to `repositories/_base.py`.
  2. A31 (2026-08-13): `GET /drivers/earnings` zeroed trip-count/distance for all-legacy periods (non-money fields filtered by the money filter).
  3. A32 (2026-08-13): product decision — blend previous-app money, drop "legacy" wording.
  4. A33 (2026-08-13, same day): A32's blend under-covered `get_driver_earnings`.
  5. B31 (2026-08-20): `routes/promotions.py` three ride-count sites have **no** legacy exclusion (271+ rows).
  6. A30 (2026-08-13): visibility audit; A34 (2026-08-16): legacy ride count 224→186 unexplained until traced to test-account cleanup.
  7. A41 (2026-08-19): 5-agent data-quality sweep.
  8. 62 change-log filenames contain `legacy` (VERIFIED); 59 non-test backend files reference `EXCLUDE_LEGACY_RIDES`/`legacy_import_metadata` (VERIFIED grep).
- Timeline: 2026-07-29 first legacy-booking-import entry → 08-11 audit (3 P0) → 08-13 (four items in one day) → 08-19/20 → 08-22 batch import → 08-27/28 driver import → 09-10 pre-launch flag riders.
- What happens: a migrated driver opens Activity and sees `$0.00 / 0 trips` above 17 real rides (A31's own description).
- Root cause: "legacy" is a JSONB marker (`legacy_import_metadata`) tested by a Mongo-shaped filter constant, applied by hand at every aggregate; the product meaning of "exclude" changed mid-testing (A32) so every call site had to be revisited; no view/column/flag represents the cohort.
- Why the systemic fix never landed: the import itself was being run and re-run in production during live testing (A30 "Finding 0 — was the importer ever committed to production?"), so every session was firefighting a surface rather than designing the cohort model; the rapid baseline's INFERRED note ("no persistent legacy cohort marker") is confirmed.
- Recommendation: a generated column `rides.is_legacy_import boolean` + a DB view for driver/rider aggregates; replace the 59 references with the column; a test that fails if any aggregate query on `rides` lacks the column filter or an explicit `include_legacy=True`. Alternative: keep the constant and add a lint — rejected: the constant's semantics already flipped once.
- Blast radius: 59 files; admin financial dashboards; T4A/statements; promotions.
- Rollout: additive column backfilled from JSONB; dual-read window; Rollback: keep JSONB, stop reading the column.
- Verification to close: A31's repro against a fixture driver whose rides are all legacy.

### HIST-004 — Silent swallow / warning-and-continue / advisory gates
- Hierarchy: L2 Reliability › L3 Error handling on DB/payment/CI paths › L4 `except: logger.warning(); continue` and `continue-on-error: true` › L5 "the failure is logged and forgotten"
- Severity: CRITICAL (B42 lost 53 of 55 `payment_failed` events over ~2 months)   Priority score: 5×5×4 = 100
- Status: VERIFIED   Existing item: B42 (closed), C135 (open), C10, C74, C12, C24, C21/C73 (open), plus the 2026-09-23 sweep
- Adversary: fraudster (a lost `payment_failed` = a ride that stays "paid"), auditor
- Evidence — occurrences (backend):
  1. `2026-08-01-fix-mark-stripe-event-processed-swallow.md` — the function's docstring *argued* the swallow was acceptable; it was not (C10 reconciliation job added same day).
  2. `2026-08-11-redis-set-nx-fail-loud.md` — `except … logger.warning` with neither `raise` nor `return`, falling through into the in-process fallback intended only for "Redis not configured".
  3. `2026-08-12-corporate-otp-email-send-fail-open.md` — `send_transactional_email` "documented … to return a bool and never raise"; callers proceeded on failure.
  4. B42 (found 2026-09-06 via A43, closed 2026-09-11): `routes/webhooks.py` CAS write set a column that no migration created; every invocation raised uncaught; **53/55 `payment_intent.payment_failed` events since 2026-07-15 were permanently lost** (`ACTION_ITEMS.md` B42 body, VERIFIED).
  5. C135 (open, 2026-09-23): 7 of 8 `unclaim_stripe_event()` call sites ignore its `False` return; the change-log says the anti-pattern was "independently re-found in 10 separate" places (`2026-09-23-unclaim-stripe-event-error-severity.md` §2).
  6. C55: `record_period_transition` is a *documented* exception to the no-swallow rule (compliance write logs ERROR + metric, no alert rule existed).
  7. C80: fire-and-forget done-callbacks swallowed cancellation.
- Evidence — occurrences (CI): C74 (summary jobs `if: always()` never failed — both security-gates and ci-guardrails, fixed 2026-09-08); C12 (Codecov tokenless, closed by deletion); C24 (coverage-regression gate could not fail); 21 live `continue-on-error: true` lines across `ci.yml`, `ci-guardrails.yml`, `security-gates.yml`, `security-report.yml`, `mobile-dep-check.yml`, `dependabot-auto-merge.yml`, `claude-review.yml` (VERIFIED grep; §3 lists each with its job).
- Timeline: 2026-08-01 → 08-11 → 08-12 → 09-06 (B42 found) → 09-08 (C74) → 09-11 (B42 closed) → 09-23 (C135 sweep, still open).
- Root cause: the rule ("never `logger.warning` and continue on a DB/auth/payment error") lives in `CLAUDE.md` prose; the only mechanical enforcement is `tests/test_loguru_call_conventions.py` (which checks *how* loguru is called, not *whether* the error is re-raised). CI gates are promoted from advisory to blocking one at a time, each with a CR.
- Why the systemic fix never landed: each occurrence was fixed as a local pattern ("wrapped in the same try/except + unclaim + 503 pattern the read already uses"). A repo-wide AST check for `except` blocks on DB/Stripe/Redis calls that neither re-raise nor return an error was proposed nowhere. On the CI side, the reason is explicit: `security-gates.yml:10-14` keeps gitleaks G5a advisory "for a specific, still-open" reason (an unrotated key in history — see §9 Q2).
- Recommendation: (1) a semgrep rule (blocking, like SR-03) for `except Exception` in `routes/webhooks.py`, `repositories/`, `utils/redis_client.py`, `services/payment_service.py` whose body has no `raise`/`return`/`unclaim`; (2) a quarterly inventory of `continue-on-error: true` with an owner per line (§3 is the first one). Alternative: rely on `spinr-*` reviewer agents — rejected: C135 shows a reviewer-found pattern recurring 10× anyway.
- Blast radius: webhooks, payment retry, Redis helpers, corporate email, every CI summary job.
- Rollout: rule in warn mode one week, then blocking; Rollback: rule off.
- Verification to close: the rule catches the exact B42 shape when the fix commit is reverted in a scratch branch.

### HIST-005 — Doc-vs-code drift (numbers and statuses copied by hand)
- Hierarchy: L2 Engineering system › L3 Operating docs (CLAUDE.md, AGENTS.md, runbooks, agents, context) › L4 hardcoded counts/statuses › L5 "the doc describes a system that no longer exists"
- Severity: MEDIUM (individually) / HIGH (aggregate: agents act on the docs)   Priority score: 3×5×5 = 75
- Status: VERIFIED   Existing item: C83, C84 (closed twice), C85, A39, B23, B34, B15, hygiene audit 2026-08-24
- Adversary: auditor; malicious insider (stale runbook = wrong incident response)
- Evidence — the background-loop count alone has drifted five times: 16 (`spinr-dispatch-reviewer.md:48`, AGENTS.md pre-C84, fly.toml pre-C83) → 18 (`spinr-safety-sos-reviewer.md:31`, `capacity-scaling.md:63`) → 41 (C83/C84, 2026-09-08) → 42 (`CLAUDE.md:191,250`, `spinr-realtime-reliability-reviewer.md:3`, `spinr-agent-fleet-strategist.md`) → **44 `_spawn(` calls and 44 registry entries today** (`backend/core/lifespan.py`, `backend/core/background_loop_registry.py`, VERIFIED). `_WATCHDOG_LOOP_NAMES` is now computed at runtime (`lifespan.py:804-806`), so any literal number is guaranteed to drift. Full table in §4.
- Timeline: `CLAUDE.md` carries ≥ 8 dated self-corrections (2026-08-21, 08-23 ×2, 09-04, 09-09, 09-14 ×2, 09-21); `AGENTS.md` fixed 09-08 and again the same day (C84 follow-up found 3 more); `sprint-current.md` stale from 2026-05-06 until refreshed 2026-09-21; `.planning/PROJECT.md` still says pre-launch (redirect note only).
- Root cause: the same fact (loop count, review status, fleet shape, JWT model) is written in 5–10 places by hand, and the session that changes the code is rarely the session that owns the doc. No doc-lint; no generated docs.
- Why the systemic fix never landed: every correction was itself a doc edit ("corrected 2026-…, always re-read fresh"). The pattern of *adding a correction paragraph* instead of *removing the number* is visible in `CLAUDE.md` (the loop-count line still says 42 while telling the reader the registry is canonical).
- Recommendation: generate the volatile facts — a `scripts/docs/facts.py` that emits loop count, router count, migration head, review-bot status — and a test that `CLAUDE.md` contains no literal for a generated fact. Alternative: quarterly doc audit (`/tooling-check`) — rejected as sole fix: it ran 2026-09-08 and the count drifted again by 09-20.
- Blast radius: every agent session reads these files first.
- Verification to close: the §4 table is empty on re-run.

### HIST-006 — Migration drift: prefix collisions and unapplied files
- Hierarchy: L2 Data platform › L3 Schema migrations › L4 `backend/migrations/` + `run_migrations.py` › L5 "merged ≠ applied; two files share a number"
- Severity: HIGH (C43's RLS-enable for the `settings` table holding Stripe keys is in the unapplied set)   Priority score: 4×5×4 = 80
- Status: VERIFIED   Existing item: C36 (closed via CR #4187), C75 (closed), G2 (closed; **duplicate id with a bylaw item**), C44 (closed), C125 (open), C22, A39
- Adversary: malicious insider (unapplied RLS), regulator (PII purge functions applied by hand)
- Evidence:
  1. 68 numeric prefixes are shared by ≥ 2 of 550 migration files (VERIFIED `ls | uniq -c`); commit `fea60e4` landed three same-PR pairs CHECK B missed (`CLAUDE.md` migrations paragraph).
  2. C36 → CR #4187 → PR #4192 (2026-08-18) hard-fails collisions; nightly sweep added 2026-08-31 (#4642); **red every night 2026-09-02 → 09-08** over an accepted pair not in `.known_duplicate_prefixes.json` (C75).
  3. G2 (2026-08-21): 116 files never recorded as applied; ~95 applied via side channels without tracking rows, ~17 genuinely missing (incl. safety/PII), 4 wrong-as-merged (now `NEVER_APPLY`).
  4. C44 (2026-08-27→09-04): 363–369 unapplied; C125 (open 2026-09-23): 8 pending incl. `379_enable_rls_settings_document_files_driver_imports.sql` (C43).
  5. 25 migration filenames contain fix/repair/correct (VERIFIED) — schema mistakes are corrected forward, which is correct policy but shows the rate.
  6. Application to production is manual: `apply-supabase-schema.yml` is `workflow_dispatch`-only with `PG_CONNECTION_STRING` (VERIFIED); no deploy workflow runs `run_migrations.py`.
- Timeline: 2026-08-17 (A39 second runner deleted) → 08-18 (collision hard-fail) → 08-21 (G2) → 08-27 (C44) → 09-02..08 (nightly red) → 09-23 (C125 open).
- Root cause: numbering by hand across parallel sessions; no migrate-on-deploy; filename is the idempotency key so renames are forbidden.
- Why the systemic fix never landed: "applying needs `DATABASE_URL`, which no sandboxed session has" (C125) — the human step was never automated because it was always someone's manual decision; the CHECK B gate was strengthened twice instead of removing the need (timestamp-based names or an allocator).
- Recommendation: (1) migrate-on-deploy job gated on the CI/Security summary (same evidence-link pattern `2026-09-23-fly-deploy-gate.md` just introduced for Fly); (2) timestamp prefixes for new files. Alternative: keep manual apply + nightly `--status` alert — rejected: G2 shows side-channel applies happen anyway.
- Blast radius: `run_migrations.py`, `migration-check.yml`, `migration-duplicate-nightly.yml`, `backend/migrations/CLAUDE.md`.
- Rollback: migrations are forward-only; a migrate-on-deploy job needs the runbook's per-migration rollback SQL — already required by `CLAUDE.md`.
- Verification to close: `run_migrations.py --status` on production shows 0 pending after every `main` deploy.

### HIST-007 — Tracker drift: duplicate `ACTION_ITEMS.md` ids and stale statuses
- Hierarchy: L2 Engineering system › L3 Backlog tracker › L4 `ACTION_ITEMS.md` › L5 "two items share an id; an item stays open after its fix shipped"
- Severity: MEDIUM   Priority score: 2×5×5 = 50
- Status: VERIFIED   Existing item: C13 (2nd), C111, C112, C118 (all self-labelled duplicates); no umbrella
- Adversary: auditor (which "C100" was closed?)
- Evidence: nine `###` ids appear twice — **A34, A40, C100, C111, C112, C118, C129, C13, G2** (VERIFIED `grep -oE "^### [A-Z]+[0-9]+" | uniq -c`; the task brief expected only C100 and A40). List-level duplicates: AI1, D8 (×2 each). C71's fix was labelled "C70" in its PR, commits and change-log because C70 was claimed concurrently (`ACTION_ITEMS.md:24356-24364`). Stale-status: C27, C36, C100, C113, C120 each record "already fixed on `main` by other work before this item was picked up" or "this entry never updated to reflect"; C118's note: "kept as-is per the existing C13/C100/C111/C112 precedent rather than renumbered".
- Root cause: one 29,244-line file; ids assigned by whichever session is writing; parallel sessions.
- Why the systemic fix never landed: it is treated as cosmetic ("kept as-is … precedent"). It is not: duplicate ids break the de-dup rule this audit programme relies on (§4 ground rules "grep ACTION_ITEMS first").
- Recommendation: an id allocator (a one-line `NEXT_ID` file updated atomically, or GitHub issues as the source of truth with `ACTION_ITEMS.md` generated). Alternative: rename duplicates now — rejected without an allocator; they would recur.
- Verification to close: `grep -oE "^### [A-Z]+[0-9]+" ACTION_ITEMS.md | sort | uniq -d` is empty in CI.

### HIST-008 — loguru called with stdlib `logging` conventions
- Hierarchy: L2 Observability › L3 Backend logging › L4 ~50 loguru modules › L5 "`exc_info=`/`extra=`/`%s` silently swallowed"
- Severity: MEDIUM (tracebacks and context never reach Sentry)   Priority score: 3×4×4 = 48
- Status: VERIFIED   Existing item: C60, C65, C69, C71 (all closed)
- Evidence — occurrences: `2026-08-04-loguru-format-and-exc-info-sweep.md` (~40 modules, "both defects are silent by construction"); `2026-08-19-documents-loguru-call-convention-fix.md`; C60 (9 sites in the outbox, 2026-09-03); C65 (the gate selected files by the literal import line, so `matching.py` — which takes `logger` from `_deps.py` — was never scanned); C69 (`extra=` at 6 sites); C71 (`%`-style placeholders in `matching.py`).
- Root cause: two logging APIs in one codebase; the guard test was written to the first defect's shape and each later defect was a new shape.
- Why the systemic fix never landed: the right fix (one logger facade, or stdlib everywhere with a loguru sink) was out of scope for each; the static test grew instead — which worked (C65/C69 added detectors), so this family is **closing**. Kept here because it shows the pattern that works: a *static test that resolves re-exports*.
- Recommendation: keep; add `logger.warning(...)` on DB/payment paths to the same scanner (links to HIST-004).

### HIST-009 — RLS policies written for an auth model the app never used
- Hierarchy: L2 Security › L3 Row-Level Security › L4 `auth.uid()`/`users.role` policies › L5 "policy is unreachable or wrong; nobody notices because the backend bypasses RLS"
- Severity: HIGH (dormant defence-in-depth; C43's tables include `settings` with vendor keys)   Priority score: 4×5×2 = 40
- Status: VERIFIED   Existing item: C108 (closed, doc-only), C107, C123, C124, C129 (closed), C43 (open, deferred by user 2026-08-25), B2, B40, C49
- Evidence: `auth.users` is empty in production (C108); the single `users.role='admin'` row has no `auth.users` row (C107); the `role IN ('admin','super_admin')` idiom is unreachable on 10 (C107) + 6 + 2 (C123) + ledger tables (C129); a stray policy no migration created existed on `corporate_accounts` (C124); RLS disabled on 4 tables (C43). 20 change-log filenames contain `rls`.
- Root cause: end-user auth is custom `JWT_SECRET` tokens and the backend uses the service-role key exclusively (`CLAUDE.md` Testing Conventions, C108). Policies are therefore never exercised by real traffic, so a wrong policy produces no error.
- Why the systemic fix never landed: the decision to keep RLS "dormant-but-correct" is recorded and reasonable; but the enabling migration for the 4 disabled tables (379) has sat in the pending set since 2026-08-31 (`2026-08-31-c43-rls-enable-migration-prep.md`) because HIST-006's apply step is manual and the owner deferred it.
- Recommendation: fold 379 into the first migrate-on-deploy run after the legacy work; keep the `tests/rls` tier. Alternative: drop RLS entirely and document — rejected: the tier is cheap and C43's tables hold secrets.

### HIST-010 — Insurance-period transitions written independently at every state change
- Hierarchy: L2 Safety & Compliance › L3 TNC insurance periods › L4 `driver_insurance_periods` writers › L5 "a driver's period is wrong because one writer forgot"
- Severity: CRITICAL (regulatory liability; `CLAUDE.md`: "Misclassification is a regulatory and insurance liability")   Priority score: 5×4×3 = 60
- Status: VERIFIED   Existing item: B34, C46, C55, C66 (closed); 2026-09-20/23 entries; PR #5748 (in flight)
- Evidence — occurrences (17 change-log filenames contain `insurance-period`):
  1. `2026-08-19-insurance-period-status-fixes.md` — offline-guard status list and online-path busy-ride list "written independently and drifted".
  2. B34 (2026-08-20): the corrections table `domain-safety.md` described did not exist (migration 355 added it).
  3. `2026-08-27-insurance-period-mid-trip-guards.md`; C46: 156/186 legacy rides had diverging Period-2 reconstructions, corrected in production 2026-08-27.
  4. `2026-09-02-insurance-period-window-precedence-fix.md` — two writers used `driver_accepted_at or assigned_at`, the reverse of three other sites.
  5. C55 (2026-09-04): no Period-2 reconciler, no live alert rule.
  6. C66 (2026-09-04): admin ride mutations could open Period 1 for an offline driver.
  7. `2026-09-20-insurance-period-derivation.md`: "CLAUDE.md's Period 0–3 table was implemented **twice, independently**, and the two implementations disagree" about a live batch offer.
  8. `2026-09-23-insurance-period-close-gaps.md`: every forced-offline writer was a plain `drivers` update; none treated going offline as a period boundary; the reconciler cannot self-heal because it scans only `is_online = True`.
  9. PR #5748 (2026-09-24, Codex review): "An exception handler let claims/offers commit even when Period 2 failed" — fixed by rolling back the RPC transaction.
- Timeline: 2026-07-29 (coverage) → 08-12 (Period-2-at-assignment doc fix) → 08-18/20 (reconstruction) → 08-27 → 09-02 → 09-04 → 09-13 → 09-20 → 09-23 → 09-24.
- Root cause: `CLAUDE.md` says "derive period from ride state, not from the driver UI", but the code *records* transitions as side effects at each call site (`matching.py:1433`, `admin/rides.py:1371`, `ride_flow.py:476,1239`, offline writers), so derivation and recording disagree.
- Why the systemic fix never landed: it is landing now — derivation (09-20) and the v2 claim RPC that commits Period 2 atomically with the claim (#5748). This family is **in transition**; the risk is a half-migrated state where v1 writers and v2 RPC coexist (F-series is flag-gated per #5748's change-log).
- Recommendation: finish v2, then delete the v1 `record_period_transition` call sites and make the reconciler the single derivation; add the Period-2 reconciler C55 asked for. Alternative: keep both and reconcile nightly — rejected: 7-year regulatory rows should not depend on a nightly repair.

### HIST-011 — Non-atomic check-then-act on shared state
- Hierarchy: L2 Reliability › L3 Concurrency › L4 read-then-`update_one` › L5 "two replicas/requests interleave"
- Severity: HIGH   Priority score: 4×4×3 = 48
- Status: VERIFIED   Existing item: B19, C54, C56, C66, C77, C104, C133 (all closed)
- Evidence: B19 (`payment_retry` two-write settlement); C54 (batch-claim loop leaves drivers claimed on exception); C56 (`claim_driver_atomic` used the read retry policy); C66 (no optimistic lock on `admin_complete_ride`); C77 (`charge.refunded` no CAS/dedupe/replay); C104 (`maps_budget` check-then-increment across 12+ call sites, two passes 09-12/09-13); C133 (`cancel_ride_rider` reads before its own claim, 2026-09-23). The ride-acceptance race guard itself (`{'status':'searching'}` filter) is the one documented CAS pattern.
- Root cause: PostgREST-shaped helpers make "read, decide, update" the natural shape; `update_one` returns the row, but nothing guides a writer to use the post-write row (C133's fix).
- Why the systemic fix never landed: each fix added a filter or a `dedupe_key` locally; the repository never grew a `compare_and_swap(table, id, expect, set)` helper with a name that reviewers grep for.
- Recommendation: add the helper; semgrep for `get_rows(... ) … update_one(` on the same id in one function without a status filter. Alternative: move all to DB RPCs (the v2 direction) — right long-term, too slow as the only fix.

### HIST-012 — PII reaching a new egress each time (logs, FCM, AI, Zoho, exceptions, git)
- Hierarchy: L2 Privacy (PIPEDA) › L3 Data egress › L4 per-sink redaction › L5 "a new sink ships without the scrub"
- Severity: HIGH   Priority score: 4×4×4 = 64
- Status: VERIFIED   Existing item: C78, C79, C112 (2nd), C113; incidents 2026-07-30 and 2026-09-12
- Evidence (20 change-log filenames contain `pii`/`redact`): 2026-07-30 ×4 (AI output scrub, base logging, runtime log guard "privacy enforcement lived entirely at authoring time … nothing at emission time", Sentry frame vars); 08-01 gov-id in AI cards; 08-18 AI tool result; 09-05 4xx exception text; 09-08 `SpinrException.details` (C78) and F06 AI gaps; 09-09 Zoho Desk; 09-14 FCM offer payload and admin-assign FCM (`matching.py` had `_FCM_EXCLUDE`, the admin path did not); 09-19 debug-offer FCM (C113); 09-15 driver-licence export. Plus the service-role key in public git history for 3.5 months (incident 2026-07-30) and the driver-PII history rewrite/force-push (incident 2026-09-12; GitHub purge request **DRAFT, NOT SUBMITTED**).
- Root cause: redaction is per-sink (`utils/pii.py`, `sentry_scrub.py`, `_FCM_EXCLUDE` ×3 copies) rather than a typed boundary; each new channel (FCM data, AI tool result, support-desk sync, exception detail) is a fresh sink.
- Why the systemic fix never landed: the same "surgical" reason as HIST-002 — the fix is applied where the leak was seen. Three copies of the FCM exclusion set exist (VERIFIED by C112/C113/09-14 texts).
- Recommendation: one `egress.redact(payload, channel)` used by FCM, WS, AI, Zoho, exception handler; a test that every `send_push`/`send_to_*` call passes through it. Alternative: gitleaks-style scanning of outbound payloads in tests — useful but reactive.

### HIST-013 — CI that silently does not run (permissions, triggers, matrix, outages)
- Hierarchy: L2 Engineering system › L3 GitHub Actions › L4 workflow triggers/permissions › L5 "green because nothing ran"
- Severity: HIGH   Priority score: 4×5×3 = 60
- Status: VERIFIED   Existing item: C13 (open since 2026-08-10), C17, C76, C92, C93, C94 (partial), C95, C96, B25 (open)
- Evidence: C13 — four required workflows produced zero runs on two consecutive commits of PR #3494, empty commit did not retrigger; C17 — 8 consecutive EAS update jobs failed silently after the SDK 57 bump; C76 — `maestro-e2e.yml` invalid `matrix` in job `if` → **3,206 zero-duration failed runs**; C92 — visual regression never ran on non-backend PRs (`needs: [backend-test]`); C93 — `detect-changes` 403 on every PR; C94/C95 — missing `permissions:`; C96 — every job failing near-instantly for ~1 day (billing suspected, unconfirmed); B25 — Maestro never fires (secrets/opt-in).
- Root cause: no meta-check that a required workflow *ran*; per-workflow `permissions:` blocks added by hand; the same "absence reads as success" shape as C9.
- Why the systemic fix never landed: the fix for "did it run" is branch protection's required-checks list (C21) — which no session can see (§9 Q1). `main-branch-guard.yml` is the backstop built for exactly this (VERIFIED header) and is itself only a notification.

### HIST-014 — `main` is red / tests stale after merge
- Hierarchy: L2 Quality › L3 Test suite on `main` › L4 backend-test, driver-app-test › L5 "the tip is red and merges continue"
- Severity: HIGH   Priority score: 4×5×4 = 80
- Status: VERIFIED   Existing item: A4, A6, A7, A8, C45, C51, C57, C60, C61, C100, C120, C122 (open, recurred), C130; PRs #5624, #5651 (2026-09-21)
- Evidence: A4 (2026-07-27) cleared 156 failing tests; C45 (08-27) 3 failures on a docs-only PR; C57 (09-03) a fix verified twice locally was **lost by GitHub's squash-merge capturing a stale branch snapshot**; C62 swept 119 PRs for the same and found none more; 2026-09-04: `main` red for ~2.5 h across **14 consecutive merges** (`main-branch-guard.yml` header); C120/C130 (09-20/21); local shallow log shows `fix(tests): unblock backend-test CI — post-review test staleness on main (#5624)` and `#5651` within the last 3 days; 4 change-log filenames contain `stale-test(s)`.
- Root cause: C21/C73 (merge does not wait for `backend-test`), parallel sessions rebasing mocks, and tests that assert on mock call shapes.
- Why the systemic fix never landed: the fix is not in the repo's power (branch protection). Everything the repo *could* do — `main-branch-guard`, `ci-audit-autoclose`, 35-minute timeout (C126) — is a mitigation.

### HIST-015 — "Fixed in one copy, never reached the sibling" beyond CarMarker
- Hierarchy: cross-cutting › L5 "a contract exists in two files and only one is updated"
- Severity: HIGH   Priority score: 4×4×4 = 64
- Status: VERIFIED   Existing item: `docs/known-forks.md` (4 rows), C131, C34, A39, HIST-010 item 7
- Evidence: notifications inbox forked and drifted twice (2026-08-18, 2026-09-14) (`known-forks.md` row 2); rider/driver referral endpoints both emitted a dead `referral_link` on a domain that never resolved (row 3, C119); `routes/auth.py` vs `routes/admin/auth.py` — admin got `get_real_client_ip()` and a guard test, rider/driver kept slowapi's socket peer, so **every rider/driver refresh-token row recorded the Fly proxy IP into 7-year audit logs** until PR #5654 (row 4); scheduled-ride validation duplicated for corporate guest booking (C34); two migration runners with different tracking schemas (A39); the insurance table implemented twice (HIST-010); three FCM exclusion sets (HIST-012). The registry's own header: the sideways-car bug "stayed live for riders for a full day because nothing forced a second look" (also `CLAUDE.md` gate 10).
- Root cause: the "surgical changes" rule plus per-app ownership produce parallel implementations of one contract; the only cross-check is a warning.
- Recommendation: treat every row of `known-forks.md` as a reconciliation ticket with a date, not a permanent registry; where a fork must stay, require a *contract test* (the auth row now has one) as the definition of done.

### HIST-016 — Timezone/DST handled per call site
- Severity: MEDIUM   Priority score: 3×3×3 = 27   Status: VERIFIED   Existing item: C30, C34 (closed)
- Evidence: `2026-08-02-scheduled-rides-track-a-10-dst-fallback.md` (round-trip guard missed the fall-back hour), `2026-08-11-scheduled-time-dst-timezone-wiring.md` (`scheduled_timezone` added in PR #3283 but "two things were missed"), `2026-08-19-corporate-statement-tax-timezone-fix.md`, C30 (DST guard opt-in per request; closed "additive half only"), C34 (corporate guest booking bypassed it). Five occurrences.
- Root cause: timezone is an optional request field; `America/Regina` (fixed UTC−6, no DST — the nightly sweep's own comment) is not the server-side default.
- Recommendation: default `scheduled_timezone` from the service area server-side; reject absent tz for scheduled rides (C30's "enforce" half).

### HIST-017 — Three OTP systems hardened separately
- Severity: MEDIUM   Priority score: 3×3×3 = 27   Status: VERIFIED   Existing item: A43 (OTP regex), runbook `otp-lockout-false-positive.md`
- Evidence: login phone OTP (`routes/auth.py`: SEC-008 lockout, A43's Unicode-digit regex defect shipped by the 47-second merge), corporate e-mail OTP (`2026-08-03-corporate-portal-otp-email-bypass.md`, `2026-08-12-…-fail-open.md`, `2026-08-13-…-ses-investigation.md`), pickup OTP/PIN (`2026-09-05-pickup-otp-bruteforce-hardening.md`), plus `2026-09-20-email-otp-sets-email-verified.md`. 10 change-log filenames contain `otp`.
- Root cause: three code paths generate/verify short codes with separate lockout, hashing and messaging.
- Recommendation: one `utils/otp.py` (hash, attempt counter, lockout, dev bypass) used by all three.

### Families checked and NOT found to recur (≥ 2 same-root-cause fixes)
- **WS event ordering:** one backend fix (`2026-09-23-ws-replay-ordering.md`: `INCR` and outbox append not in one Redis transaction) plus client-side stale-response guards in `rider-app/store/rideStore.ts` (`rideStore.clearedRide-refetch`, `rideStore.cancel-flicker`, `rideStore.eventVersion` tests; 3 fix commits 2026-09-22/23). Two different root causes (server sequencing vs client acceptance of stale HTTP responses) — **INFERRED emerging family, not yet a recurrence**; `rideStore.ts` has 102 commits in full history (§2).
- **Redis fail-open semantics per loop:** `2026-08-11-redis-set-nx-fail-loud.md` and B21 (lock TTL > sleep on 4 loops) are two fixes; `CLAUDE.md:250` documents that "not every one of the 28 fails open on a Redis error the same way". 36 files call `redis_set_nx`/`try_acquire_leader_lock` (VERIFIED). Counted under HIST-004 rather than as its own family.
- **Stripe idempotency:** `claim_stripe_event` is consistently used; the recurring defect is the *unclaim/swallow* path (HIST-004), not idempotency itself.

---

## §2 Hotspot map

Two independent measures, both VERIFIED:
- **Commits (full history):** GitHub `list_commits` scoped by `path`, default branch, 2026-04-16 → 2026-09-24, paginated at 100. Counts every commit touching the file (fixes *and* features) — the API cannot filter by message. Two files returned exactly 100 with an empty page 2 (`carSurface.tsx`, `websocket.py`); treat those two as "≥ 100, exact count INFERRED".
- **Change-log entries:** number of distinct `docs/change-log/*.md` files whose text cites the path in backticks (basename-only citations resolved to a repo path when unique; `page.tsx`/`index.tsx`/`_layout.tsx` and similar ambiguous names were dropped — ~33 citations each — so admin-dashboard/rider-app page files are under-counted).
- **Last-3-day fix commits:** local shallow clone, `git log --grep '^fix'`.

Per-surface change-log volume (entries citing any path under the prefix): backend 955 · admin-dashboard 435 · driver-app 312 · rider-app 248 · migrations 181 · shared 175 · workflows 79 (of 1,470).

### Backend
| File | Commits (full) | Change-log entries | Fix commits (last 3 d) | Families |
|---|---|---|---|---|
| `backend/routes/admin/drivers.py` | **143** | 70 | — | HIST-005 (stale docstring) |
| `backend/routes/admin/rides.py` | **131** | 80 | — | HIST-010, HIST-011 (C66) |
| `backend/routes/auth.py` | **128** | 56 | 5 | HIST-015, HIST-017, C131/C132 |
| `backend/routes/webhooks.py` | **109** | 56 | **8** | HIST-004 (B42, C135), HIST-011 (C77) |
| `backend/routes/websocket.py` | ≥100 | 26 | 4 | C64, C109 |
| `backend/core/lifespan.py` | 88 | **108** | — | HIST-005 (loop count), HIST-004 (fail-open) |
| `backend/services/payment_service.py` | 68 | 58 | — | HIST-001, HIST-011 |
| `backend/routes/rides/matching.py` | 62 | 60 | — | HIST-010, HIST-012 (`_FCM_EXCLUDE`), HIST-008 (C71) |
| `backend/utils/insurance_periods.py` | 49 | — | 4 | HIST-010 |
| `backend/repositories/_base.py` | 41 | 50 | — | HIST-003 (A26), HIST-008 (C69), HIST-011 |
| `backend/routes/rides/booking.py` | 23 | 47 | — | HIST-001 (B35) |
| `backend/schemas.py` | n/f | 72 | — | HIST-016 (C30/C34) |
| `backend/services/driver_import_service.py` | n/f | 52 | — | HIST-003 |
| `backend/services/booking_import_service.py` | n/f | 47 | — | HIST-001 (B29), HIST-003 |
| `backend/routes/admin/settings.py` / `admin/__init__.py` / `server.py` | n/f | 48 / 51 / 49 | — | — |
| `backend/utils/payment_retry.py` | n/f | — | 7 | HIST-011 (B19), B21 |
| `backend/routes/drivers/location.py` | n/f | — | 7 | C64, driver availability v2 |

### Rider app
| File | Commits | Change-log | Fix (3 d) | Families |
|---|---|---|---|---|
| `rider-app/store/rideStore.ts` | **102** | 9 | **7** | stale-event handling (emerging) |
| `rider-app/app/ride-options.tsx` | n/f | 21 (+31 ambiguous) | — | HIST-002 consumer |
| `rider-app/app/ride-details.tsx` | n/f | 21 | — | — |
| `rider-app/app/_layout.tsx` | n/f | 19 | 5 | — |
| `rider-app/app/ride-completed.tsx` | n/f | 14 | 6 | — |
| `rider-app/hooks/useRiderSocket.ts` | n/f | 9 | 5 | no `driver_assigned` handler (§4) |
| `rider-app/app/otp.tsx` | n/f | 12 | — | HIST-017 |

### Driver app
| File | Commits | Change-log | Fix (3 d) | Families |
|---|---|---|---|---|
| `driver-app/lib/androidAuto/carSurface.tsx` | ≥100 | 35 | — | HIST-002 (C70 unverified on hardware) |
| `driver-app/hooks/useDriverDashboard.ts` | 69 | 34 | — | — |
| `driver-app/components/CarMarker.tsx` | 34 | 34 | 4 | HIST-002 |
| `driver-app/__tests__/components/CarMarker.test.tsx` | n/f | 18 | — | C100 (×2) |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | n/f | 17 | 5 | — |
| `driver-app/app/driver/ride-detail.tsx` / `settings.tsx` / `payout.tsx` | n/f | 18 / 17 / 16 | — | — |
| `driver-app/utils/backgroundLocation.ts` | n/f | 16 | — | PR #5480/#5483 (Codex-reviewed) |

### Admin dashboard
| File | Commits | Change-log | Families |
|---|---|---|---|
| `admin-dashboard/src/lib/api.ts` | **234** (highest in repo) | 68 | single API client for every page |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | n/f | 51 | — |
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | n/f | 43 | — |
| `admin-dashboard/src/components/sidebar.tsx` | n/f | 29 (+24 ambiguous) | RECURRENCE-005 (stale gating comment) |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | n/f | 29 | C15 |
| `admin-dashboard/e2e/visual-regression.spec.ts` | n/f | 23 | B38, C91, C92 |
| `admin-dashboard/src/app/track/[rideId]/page.tsx` | n/f | — | HIST-002 (third marker copy; 4 fix commits in 3 d) |

### Shared
| File | Commits | Change-log | Fix (3 d) | Families |
|---|---|---|---|---|
| `shared/components/CarMarker.tsx` | 25 | 26 | 4 | HIST-002 |
| `shared/api/client.ts` | 11 | 25 | — | token refresh interceptor |
| `shared/store/authStore.ts` | n/f | 20 | — | C120 |
| `shared/components/SupportScreen.tsx` (+ test) | n/f | 8 | 6 (+7) | — |
| `shared/utils/vehicleTracking.ts` / `markerPlayback.ts` | n/f | 12 / 11 | 3 | HIST-002 |

### Migrations
Migrations are append-only, so "fix commits per file" is not meaningful; the signal is the *rate* of forward corrections: 25 of 550 filenames contain `fix`/`repair`/`correct` (e.g. `57_purge_pii_retention_regression_fix.sql`, `67_fix_purge_pii_retention_actor_id.sql`, `70_fix_financial_events_rls.sql`, `80_fix_match_and_claim_driver_type.sql`, `434_fix_audit_logs_delete_trigger_conflict.sql`), 68 prefixes are duplicated, and the most-cited migrations in the change-log are `434`, `419`, `416`, `399` (outbox), `334`, `322`, `269`, `221` (3 entries each). See HIST-006.

### Workflows
| File | Commits | Change-log | Notes |
|---|---|---|---|
| `.github/workflows/ci.yml` | **122** | 39 | C12, C17, C92, C93, C126; 7 `continue-on-error` lines |
| `.github/workflows/security-gates.yml` | 36 | 22 | C74; gitleaks G5a advisory |
| `.github/workflows/ci-guardrails.yml` | n/f | 13 | C24, C47, C74; 5 advisory jobs |
| `.github/workflows/update-visual-baselines.yml` | n/f | 13 | B38 (needs human dispatch) |
| `.github/workflows/eas-build.yml` / `eas-native-build.yml` | n/f | 13 / 11 | C17, C19, B41 |
| `.github/workflows/migration-check.yml` | n/f | 10 | C36 (CHECK B, twice) |
| `.github/workflows/maestro-e2e.yml` | n/f | 9 | B25, C76 |
| `CLAUDE.md` (not a workflow, listed for scale) | 80 | 387 citations | HIST-005 |

Reading of the map: the four most-changed backend files are exactly the ones on live-tested money/auth/admin surfaces, and the admin API client has been touched 234 times — more than any code file — which is consistent with the admin dashboard being the surface where every backend change is observed first. `lifespan.py` is the most-*cited* file (108 entries) while only 88 commits: many entries cite it for the loop registry without changing it, which is the HIST-005 mechanism in miniature.

---

## §3 Decay signals

Rules applied (from `.claude/agents/spinr-agent-fleet-strategist.md`, which defines coverage but — VERIFIED — contains no explicit "decay" rules; the rule used here is the one `ACTION_ITEMS.md` C9 states: *silence reads as "no findings" rather than "no reviewer" — the same failure mode as a permanently-red check*). A signal is listed if a gate, monitor, workflow or review is red, dormant, advisory, unwired or unprovisioned, whether or not it is by design.

| # | Signal | Evidence (path:line / id) | Status today | Since | Impact | Existing id |
|---|---|---|---|---|---|---|
| 1 | `main` required-status-checks list stale; merges complete before checks finish | `ACTION_ITEMS.md` C21 (#3719/#3728 merged with checks in flight), C73/A43 (#5048 merged 47 s after opening; `backend-test` failed 16 min later; `Required PR fields filled` already red); `docs/audit/2026-08-27-cicd-gates-guardrails-audit.md:16,29` ("no session … has ever had read/write access to Settings → Branches"); `main-branch-guard.yml:1-20` backstop | **Open** | 2026-08-12 (C21), materialised 2026-09-06 (B42) | Every gate below is advisory in practice | C21, C73, A43 |
| 2 | `pull_request` workflows silently never fire on some PRs | C13 (`ACTION_ITEMS.md:14931`; zero runs on two commits of #3494, empty commit did not retrigger) | **Open** | 2026-08-10 | Same "absence = green" shape | C13 |
| 3 | Claude PR review off by design (no `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN`) | `claude-review.yml:93-99` skips cleanly; C7 "DECIDED 2026-08-01: stays off"; issues #2503, #2497 | Dormant by decision | 2026-07-27 | No AI review from this vendor | C7 |
| 4 | Codex auto-review silent | C9: last comment #2877 (2026-07-30), 183 PRs; ~200 PRs unreviewed. **Now resumed:** `search_pull_requests commenter:app/chatgpt-codex-connector` → 192 PRs total; 8 PRs created ≥ 2026-08-01 have Codex comments, earliest **#5480 (2026-09-16)**, latest #5748 (2026-09-24, 8 findings acted on — `2026-09-24-pr5748-codex-review-fixes.md`) | **Resumed 2026-09-16 after 48 days; `CLAUDE.md` and C9 still say silent** | 2026-07-30 → 2026-09-16 | 48 days of no automated review during live testing; docs now wrong the other way | C9 (needs update) |
| 5 | Codecov tokenless uploads | C12 (closed 2026-08-16 by **removing** all 4 upload steps; `grep codecov ci.yml` = 0); C24 coverage-regression gate replaced by self-computed baseline 2026-08-27 | Resolved by deletion | 2026-07 → 08-27 | No external coverage history | C12, C24 |
| 6 | Playwright e2e jobs advisory on every PR | `ci.yml:577,758,832` — `e2e-test`, `rider-app-e2e`, `driver-app-e2e` have job-level `continue-on-error: ${{ github.event_name == 'pull_request' }}` | Advisory on PRs, blocking only on `main` push | UNKNOWN (predates change-log) | Web-export e2e cannot block a PR | none found — **new** |
| 7 | `ci-guardrails.yml` advisory jobs | job-level `continue-on-error: true` at `:108` `shared-coverage-run`, `:245` `coverage-regression-gate`, `:803` `lint-trend-gate`, `:1533` `breaking-change-gate` (duplicate-prefix check), `:1654` `risk-label` (Change-Impact-Log presence for sensitive PRs) | Advisory | by design (`ci-guardrails.yml:50`) | The Change Impact Log gate CLAUDE.md calls mandatory is checked by an advisory job; the blocking `change-impact-log-gate` (`:1619`) exists separately — VERIFIED | C36 (breaking-change), C24 |
| 8 | gitleaks G5a (git history) advisory | `security-gates.yml:596` job-level `continue-on-error: true`; header `:10-14` "for a specific, still-open" reason; `2026-08-27-cicd-gates-guardrails-audit.md:134`: a "real, still-valid Supabase `service_role` key is sitting in git history and hasn't been rotated" | Advisory | 2026-07-30 | Secret-history scanning cannot block | incident 2026-07-30 (see §9 Q2) |
| 9 | Semgrep SARIF upload / CodeQL upload failing | C94 partially fixed 2026-09-08 (`actions: read`), deeper repo-setting blocker remains; `security-gates.yml:405,423` upload steps `continue-on-error: true` | **Partially open** | 2026-09-08 | Findings don't reach GitHub Security tab | C94 |
| 10 | Maestro real-device E2E never fires | B25 open (secrets `MAESTRO_CLOUD_API_KEY` unset, opt-in label, no iOS); C76: invalid `matrix` in job `if` produced **3,206** zero-duration failures until 2026-09-08; `maestro-ios-macos-runner.yml` similar gating | Wired, dormant | 2026-08-11 (B25) | No native-device coverage; rider/driver have no visual tooling (CLAUDE.md gate 6) | B25, C76, C95 |
| 11 | admin visual-regression had zero baselines | B38 "documented no-op since it was added", closed 2026-09-04; C91 baseline never renders a driver marker; C92 job never ran on non-backend PRs (`needs: [backend-test]`), fixed 2026-09-08; today `ci.yml:640-642` has no `continue-on-error` and `if: always() && (main || pull_request)` — VERIFIED blocking | Active since 2026-09-04/08 | added → 2026-09-04 | Re-seeding needs `update-visual-baselines.yml` dispatch a human must run | B38, C91, C92 |
| 12 | RLS dormant (no real Supabase sessions) | C108: `auth.users` empty; every `auth.uid()` policy unreachable; `tests/rls` proves logic only | By design, documented 2026-09-14 | always | Defence-in-depth off | C108 |
| 13 | RLS disabled on 4 production tables incl. `settings` (Stripe/Twilio/Maps keys) | C43 open, deferred by user 2026-08-25; migration `379_enable_rls_settings_document_files_driver_imports.sql` prepared 2026-08-31, in C125's 8 pending, **not applied** | **Open** | 2026-08-25 | Any leaked anon/service key path reads vendor secrets | C43, C125 |
| 14 | Production migrations applied by hand | `apply-supabase-schema.yml` `workflow_dispatch` only; G2 (116 untracked), C44 (7 stale), C125 (8 pending) | **Open** (C125) | 2026-08-21 | Schema drift; see HIST-006 | G2, C44, C125 |
| 15 | Synthetic (external) checks are a spec only | `monitoring/synthetic-checks.yaml:6-9`: "nothing in this repo talks to Checkly / UptimeRobot / Grafana Synthetic Monitoring / PagerDuty"; `docs/runbooks/synthetic-monitoring.md`; E4 | Unprovisioned | 2026-09-03 | No outside-in uptime check; the 2026-09-02 `api-spinr.spinr.ca` cert outage had "nothing watching it" (#4911 commit body) | E4, E13 |
| 16 | `loop_watchdog` / `capacity_watchdog` alerts are no-ops without `ALERT_WEBHOOK_URL` | `backend/core/config.py:330` default `None`; `lifespan.py:800-801` "No-op when ALERT_WEBHOOK_URL is unset"; `capacity-scaling.md:264,282` | Whether set in prod: **UNKNOWN** (Fly secrets not readable) | — | Stale-loop and DB-saturation alerts may go nowhere | C11, E13 |
| 17 | Grafana/metrics alerting unprovisioned | C11 open: ADR-010 accepted 2026-08-02, `metrics-agent/` config merged 2026-08-17 "inert/undeployed"; CR-2026-008 (#3295) needs a human to choose option A/B and create a Grafana Cloud account; `fly-mixed-fleet.md:3` "Grafana and email alerts are deferred at the user's request" | Unprovisioned | 2026-08-02 | KPI/SLA tables in CLAUDE.md unmeasured | C11, CR-2026-008 |
| 18 | `secret-rotation-monitor.yml` (monthly) | E13: "Last rotated dates are TBD" — tracker has placeholders | Runs, but on placeholder data | 2026-09-03 | Cannot alert on real rotation age | E13 |
| 19 | `renewal-calendar-monitor.yml` (weekly) | E13: "All dates are TBD placeholders — no vendor account access" | Runs on placeholders | 2026-09-03 | — | E13 |
| 20 | `supabase-capacity-monitor.yml` | gated on `SUPABASE_ACCESS_TOKEN`/`SUPABASE_PROJECT_REF` "unset today, so no-ops" (`:6-7,72`) | No-op | 2026-09-03 | — | E13 |
| 21 | `billing-usage-monitor.yml` | skips Stripe/Twilio balance checks when `STRIPE_SECRET_KEY`/`TWILIO_*` unset (`:71,135`); whether set: UNKNOWN | Possibly no-op | 2026-09-03 | — | E13 |
| 22 | `cert-domain-monitor.yml` | runs 3×/week; C-series note (`ACTION_ITEMS.md:28403`) "cannot catch this class by construction" (a cert stuck un-issued) | Active, known blind spot | 2026-09-03 | — | E13 |
| 23 | `dast-zap-baseline.yml` (weekly) needs `STAGING_URL`; staging deploy only on push to a `staging` branch with `FLY_API_TOKEN_STAGING` | `dast-zap-baseline.yml:48-74`; `deploy-backend-staging.yml:55-107`; `ACTION_ITEMS.md:19121` "no staging environment — see C1/E1" | Likely no-op (**INFERRED**; 4 remote refs match `staging`, environment provisioning UNKNOWN) | — | No DAST | E1 |
| 24 | Railway standby deploy + parity monitor red on wrong service name | C5: workflows hardcoded `spinr-backend`, real service `spinrvm`; fixed PR #5608 2026-09-21; green runs 35623989078… and 35637184754 | Resolved | ~2026-06 → 2026-09-21 | Standby "looked" stale for months; it was not | C5 |
| 25 | Failover drill never run | C1 "never exercised" | Open | — | ADR-007 unproven | C1 |
| 26 | Sentry refresh-token-reuse alert rule | C2 open ("~5 min in Sentry UI") | Open | 2026-07 | Theft tripwire logs but does not page | C2 |
| 27 | `ci-error-audit.yml` opened 2,483 issues with no dedup | C27 fixed 2026-08-18 (#4130); C72 fingerprint refined 2026-09-08; `ci-audit-autoclose.yml` daily | Resolved | 2026-04-28 → 08-18 | Issue tracker noise made real failures invisible | C27, C72 |
| 28 | Nightly duplicate-prefix sweep red 6 nights | C75 (2026-09-02 → 09-08) | Resolved | — | A red nightly nobody acted on for 6 days | C75 |
| 29 | Every Actions job failing near-instantly (~1 day) | C96, 2026-09-08 20:23 → 09-09; root cause "still not billing-confirmed" | Resolved, cause UNKNOWN | — | No CI at all for a day | C96 |
| 30 | 11 `workflow_dispatch`-only workflows | `android-production-internal`, `apply-supabase-schema`, `bootstrap-fly`, `bootstrap-metrics-agent`, `deploy-fly-signed-image` ("EXPERIMENTAL, manual only"; C121, C127), `diagnose-rider-play-verification`, `eas-native-build`, `eas-rollout-control`, `generate-upload-keystore`, `submit-play-store`, `update-visual-baselines` (VERIFIED) | Manual by design | — | Each depends on a human with Actions-dispatch access; agents cannot run them (C103) | C121, C127, B38 |
| 31 | `dependabot-auto-merge.yml` approve step | `:98` `continue-on-error: true` (best-effort approve) | Advisory by design | — | — | — |
| 32 | `mobile-dep-check.yml` Expo audit | `:85,129` `continue-on-error: true` (needs `EXPO_TOKEN`) | Advisory | — | — | CR-2026-008 note in PR template |
| 33 | `security-report.yml` artifact downloads | `:118,127,136` `continue-on-error: true` | Best-effort | — | Weekly report may be partial | — |
| 34 | `.kilo/` config | `CLAUDE.md` table: dormant; label corrected 2026-09-14 | Dormant | 2026-04-14 | — | — |
| 35 | `.claude/context/sprint-current.md` | stale 2026-05-06 → refreshed 2026-09-21; session-start hook "flags this every session; silently ignored" (hygiene audit) | Resolved | 3.5 months | Agents loaded 3.5-month-old sprint context | hygiene audit |
| 36 | Android Auto / phone marker device verification | C70 open (last hardware pass 2026-08-16), C90 open, C103 access gap | Open | 2026-08-16 | HIST-002 fixes unverified on hardware | C70, C90, C103 |
| 37 | `insurance_period` write-failure alert | C55 closed by adding a reconciler; "no confirmed live Grafana/Sentry rule" | Depends on #17 | — | — | C55 |
| 38 | Coverage-gate jobs externally cancelled mid-suite | C47 closed with root cause "still not identified" | Cause UNKNOWN | 2026-08-27 | — | C47 |

Summary: of 52 workflows, 13 are scheduled, 11 are manual-only, and 21 live `continue-on-error: true` lines exist (7 in `ci.yml`, 11 in `ci-guardrails.yml` incl. steps, 5 in `security-gates.yml`, 3 in `security-report.yml`, 2 in `mobile-dep-check.yml`, 1 each in `claude-review.yml` and `dependabot-auto-merge.yml`; VERIFIED grep). The two signals that matter most are #1 and #2, because they convert every other gate into advice.

---

## §4 Doc-vs-code drift

Every row was checked against the cited code path in this session. "Resolved" rows are kept because they show the mechanism (a doc corrected by adding a paragraph, not by removing the volatile fact).

| # | Doc path:line | Claim | Code path:line | Reality | Label | Existing id / new |
|---|---|---|---|---|---|---|
| 1 | `CLAUDE.md:191` and `:250` | "spawns 42 background asyncio loops" / "the 42 startup loops" | `backend/core/lifespan.py` — 44 `_spawn("…")` calls; `backend/core/background_loop_registry.py` — 44 `(name, placement)` entries (14 `api`, 3 `worker_wave1`, 27 `deferred`) | 44, not 42; `_WATCHDOG_LOOP_NAMES` is now computed at runtime (`lifespan.py:804-806`), so no literal can stay correct | VERIFIED | C83/C84 precedent; **new** (CLAUDE.md itself) |
| 2 | `.claude/agents/spinr-dispatch-reviewer.md:48` | "16 loops per `core/lifespan.py`" | same | 44 | VERIFIED | new |
| 3 | `.claude/agents/spinr-safety-sos-reviewer.md:31` | "one of the 18 startup loops" | same | 44 | VERIFIED | new |
| 4 | `.claude/agents/spinr-realtime-reliability-reviewer.md:3,32`; `spinr-agent-fleet-strategist.md` ("the 42 background loops"); `spinr-notification-ux-reviewer.md:57` | 42 | same | 44 (the realtime reviewer correctly says "re-read fresh", then states 42 anyway) | VERIFIED | new |
| 5 | `docs/runbooks/capacity-scaling.md:37-40` | pool = 8 machines, 6 suspended (`auto_stop_machines = "suspend"`), "the 1 GB VM", cold boot includes "18 background loops" (`:63-65`) | `backend/fly.toml` — `[[vm]]` app `shared-cpu-2x` 4 GB ×2 + burst `shared-cpu-1x` 2 GB ×6, `auto_stop_machines = "off"`; `deploy-fly.yml:139` `flyctl scale count app=2 burst=6`; runbook last touched 2026-09-03 (`83c4529`), fleet resized 2026-09-16 (`ba28a92`, `aa105d4`) after the 2026-09-15 OOM incident (`fly-mixed-fleet.md:7`) | Runbook describes the pre-09-16 fleet; the newer `fly-mixed-fleet.md` is correct but `capacity-scaling.md` is the one `CLAUDE.md`/on-call cite | VERIFIED | new |
| 6 | `CLAUDE.md:238`; `ACTION_ITEMS.md:3835`; `docs/known-forks.md:40` | "admin JWTs are fully trusted (role+email+modules in claims)" | `backend/dependencies/__init__.py:355-360` — non-env admins: `get_rows("admin_staff", {"id": user_id})` on every request; inactive → 401 `ERR_ACCOUNT_INACTIVE`; token-version mismatch → 401; env-admin path reads a token version from settings and **fails closed 503** if unreadable (`:340-352`) | Admin tokens are re-validated per request against `admin_staff` (activity, `token_version`); "fully trusted" is false in the direction that matters for revocation. The claim is repeated in three places | VERIFIED | new (doc fix); C81/F12 touched the same code |
| 7 | `CLAUDE.md:233` ("Money arithmetic … A pre-commit hook blocks float arithmetic in fare code") | blocks | `.claude/hooks/pre-commit:113-119` check 6 prints `WARNING — possible float money arithmetic` and does **not** set `BLOCKED=1`; no `semgrep` in the hook | Pre-commit warns; the blocking gate is CI (`security-gates.yml:359` SR-03) with 7 exclusions | VERIFIED | new |
| 8 | `CLAUDE.md` "PR review handling" block ("Codex has been silent since 30 July … no automated PR review is running on this repo, from either vendor"); `ACTION_ITEMS.md` C9 status "open" | Codex silent | GitHub search: 192 PRs commented by `app/chatgpt-codex-connector`; 8 created ≥ 2026-08-01 (#5480 2026-09-16 … #5748 2026-09-24); `docs/change-log/2026-09-24-pr5748-codex-review-fixes.md` acts on 8 Codex findings | Codex resumed 2026-09-16; docs say the opposite | VERIFIED | C9 (update, don't re-file) |
| 9 | `.claude/context/domain-dispatch.md:40` | WS event `driver_assigned` backend → rider (ride_id, driver_id, eta, vehicle) | `grep -rn '"type": "driver_assigned"' backend/` → no emitter; `matching.py` sends `new_ride_assignment` to the **driver** (`:1556`) and `driver_timeout`/`ride_cancelled` to the rider (`:1864-1868`, `:2331-2335`); `rider-app/hooks/useRiderSocket.ts` handles `driver_accepted`, `driver_arrived`, `ride_started`, `driver_timeout`, `ride_status_changed`… and has **no** `driver_assigned` case | The documented event does not exist on either side; the rider learns of assignment via `driver_accepted` (`ride_flow.py:614`) | VERIFIED | new |
| 10 | `docs/threat-model/rider-app.md:52,126,149` | RS-4 TLS pinning "**OPEN** — pinning not yet confirmed in code", P1, score 64 | `docs/audit/clean-sheet-prompt/standards-and-scale.md:24,62` — "No certificate pinning. This is a deliberate trade-off (pinning breaks rotation)"; `ls docs/adr` 001–016, no pinning ADR | Threat model says open P1; standards doc says decided; no ADR records the decision | VERIFIED (via rapid baseline SEC-A2-004, re-checked `ls docs/adr`) | rapid baseline N10 |
| 11 | `backend/routes/admin/drivers.py:1973-1981` (docstring of `admin_verify_driver`) | production `drivers` "has no `updated_at` (and no `verified_at`) column"; "Only set columns that actually exist" | `backend/migrations/12_driver_lifecycle_status.sql:9` `ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ` | Column exists since migration 12; the docstring encodes a pre-migration-12 workaround, so verification timestamps are silently not written | VERIFIED (schema); whether production has the column: INFERRED from G2's applied set | rapid baseline DRIFT-003 |
| 12 | `.claude/agents/spinr-fraud-auditor.md:46-49` | "As of this agent's introduction there is no such cap found in `referral_payout.py`" (velocity) | `backend/utils/referral_payout.py:112-171` — 24 h rolling `_VELOCITY_WINDOW_SECONDS`, `_DEFAULT_VELOCITY_CAP_PER_REFERRER = 5`, admin-tunable via `app_settings.referral_payout_velocity_cap_per_day`; migration `336_referral_payout_velocity_cap_setting.sql` | Cap exists; the agent will keep flagging a gap that is closed | VERIFIED | new |
| 13 | `CLAUDE.md:191` | "mounts ~25 routers"; "`routes/admin/` — 15+ admin-only endpoints" | `backend/server.py` 62 `include_router` calls; `backend/routes/admin/` 60 `.py` files | Under-counted ~2.5×; harmless but shows the age of the paragraph | VERIFIED | new (minor) |
| 14 | `CLAUDE.md:192` | "`backend/db_supabase.py` — ~66 helper functions wrapping `supabase-py`" | `backend/db_supabase.py` has 0 `def`s — it re-exports `repositories/*` (as `CLAUDE.md`'s own Testing Conventions paragraph says) | Two paragraphs of the same file disagree about what the module is | VERIFIED | new (minor) |
| 15 | `.planning/PROJECT.md:3-8` | "pre-launch … device testing is the immediate next milestone" | `CLAUDE.md` "currently going through live app testing with real users"; `docs/runbooks/saskatoon-launch.md:444` still lists items "before public launch — not yet documented" | Flagged stale 2026-08-25 with a redirect note only; the underlying question (is this pre-launch, soft launch or public?) is not answered anywhere — see §9 Q7 | VERIFIED | hygiene audit 2026-08-24 |
| 16 | `docs/incidents/2026-07-30-supabase-service-role-key-public-exposure.md:126` | "Key rotation — owner-actioned, confirmed in progress 2026-07-30" | `docs/audit/2026-08-27-cicd-gates-guardrails-audit.md:134`: gitleaks G5a stays advisory because "a real, still-valid Supabase `service_role` key is sitting in git history and hasn't been rotated"; `security-gates.yml:10-14` "for a specific, still-open" reason; `docs/audit/breach-record.md` has no "rotat" text | Two dated documents disagree on whether the P0 key was rotated; the CI gate is being held advisory on the assumption it was not | VERIFIED (contradiction) | **§9 Q2 — human only** |
| 17 | `AGENTS.md` (Codex-facing) | Railway-only deploy, 16 loops, `routes/rides.py`, migration "101/102", `.Codex/` paths, a `graphify-out/` graph | reality per C84 | Fixed 2026-09-08, then 3 more instances found the same day | VERIFIED (via C84) | C84 closed |
| 18 | `backend/fly.toml` comments | "16"/"18" loops | 41 at the time | Fixed 2026-09-08 by pointing at the registry — the right pattern | VERIFIED | C83 closed |
| 19 | `.claude/context/domain-safety.md:100` | corrections go to `driver_insurance_period_corrections` | migration `355_driver_insurance_period_corrections.sql` | Was drift (B34, no table) → now true; also `:32-38` PagerDuty is a webhook shape with no account (B15 corrected) | VERIFIED | B34, B15 closed |
| 20 | `.claude/context/regulatory-sk.md:43-50` | rider identity "hashed after 2 years" | never implemented | Corrected 2026-08-22 to attributable retention | VERIFIED | B23 closed |
| 21 | `.claude/context/sprint-current.md` | Sprint 1 (2026-05-06) as current | — | Refreshed 2026-09-21; the file now carries a header explaining it was stale for 3.5 months | VERIFIED | hygiene audit |
| 22 | `docs/change-log/2026-09-05-*.md` (8 files) | cite `docs/audit/2026-09-05-engineering-director-review-round3.md` | file never committed | Footnoted 2026-09-08 | VERIFIED | C85 closed |
| 23 | `CLAUDE.md` KPI section | "Pull current values via `/kpi`" | no such endpoint | Corrected 2026-09-14 in place (paragraph kept) | VERIFIED | CLAUDE.md self-correction |
| 24 | `CLAUDE.md` deployment paragraph | Railway standby "blocked by a GitHub Environment protection rule" | wrong service name (C5) | Corrected 2026-09-21 by appending the true story; the paragraph is now ~20 lines of superseded theories | VERIFIED | C5 closed |
| 25 | `.claude/context/memory.md` (5 entries, 2026-09-11/14) | D5 in-app VoIP "premise was stale"; A28 "total rides" definitions intentionally different; N14 no e-mail-verification gating | `2026-09-20-email-otp-sets-email-verified.md` makes corporate e-mail OTP set `email_verified` for the *existing* join-domain gate | Consistent — N14 said the existing corporate gate was out of scope. No contradiction found in memory.md | VERIFIED | — |
| 26 | `docs/incidents/2026-09-10-…skia….md` | reported as fleet-wide P0 | Sentry: 3 events, 1 user | The incident doc itself corrects the severity — good pattern, no drift | VERIFIED | — |
| 27 | `docs/incidents/2026-09-12-github-purge-request….md:2` | "DRAFT, NOT SUBMITTED. Needs a human" | — | Still draft 12 days later (no later change-log mentions submission) | INFERRED | §9 Q3 |

Pattern across the table: 7 of the 27 rows are the same fact (loop count) in different files; 3 rows are the same false sentence (admin JWT) copied verbatim; rows 23–24 show `CLAUDE.md` fixing itself by appending rather than replacing, which is why the file is 600+ lines and still wrong in rows 1, 6, 7, 8, 13, 14.

---

## §5 Timeline

| Date | Phase / event | Evidence | Label |
|---|---|---|---|
| 2026-02-14 | ADR-006 Railway as primary (pre-dates this repo; written in the upstream `ittalenthireca-sketch/Spinr` history) | `docs/adr/006-railway-deployment.md:3` | VERIFIED |
| 2026-04-11/12 | Env template "sanitized"; repo created **PUBLIC** with full history including a live `service_role` key | incident 2026-07-30 §1 timeline | VERIFIED (doc) |
| 2026-04-15 → 04-20 | Key re-added to HEAD, then removed again | same | VERIFIED (doc) |
| 2026-04-16 | PR #1 "Merge upstream ittalenthireca-sketch/Spinr into spinrvm" — this repo's PR history begins | `list_pull_requests` asc | VERIFIED |
| 2026-04-18/19 | PR #2 driver-app audit v4 (253 findings); PRs #3–#5: refresh-token field bug, ride-state validation on complete/cancel, "payment idempotency and financial precision" — the state-machine and money families are present from day 3 | `list_pull_requests` asc | VERIFIED |
| 2026-04-22 | First Claude config audit (`docs/claude-audit-2026-04-22.md`) | `CLAUDE.md` `.kilo` row | VERIFIED |
| 2026-04-28 | `ci-error-audit.yml` starts opening issues (2,483 by 2026-08-18) | C27 | VERIFIED |
| 2026-05-02 / 05-06 | `.planning/REQUIREMENTS.md` (GSD); Sprint 1 "P0 security/safety" — last real `sprint-current.md` update until 09-21 | `docs/PRD.md` header; `sprint-current.md` header | VERIFIED |
| 2026-05-30 / 06-04 | `fly.toml` added; ADR-007 Fly primary + `deploy-fly.yml` (parallel deploy with Railway) | `list_commits path=backend/fly.toml`; ADR-007 | VERIFIED |
| 2026-07-15 | First `payment_intent.payment_failed` webhook received — real Stripe traffic exists by this date | B42 body | VERIFIED (doc) |
| 2026-07-26 | Change-log convention starts (`2026-07-26-corporate-allowance-cap-race-fix.md`); week W31 produces 279 entries | `ls docs/change-log` | VERIFIED |
| 2026-07-27 | A4: 156 failing backend tests cleared; issue #2503 (Claude review has no credentials) | A4; C7 | VERIFIED |
| 2026-07-28 | First "live app testing" wording in a change-log | `2026-07-28-data-transfer-sgi-fixes-and-features.md` | VERIFIED |
| 2026-07-30 | **P0**: service-role key found in public history (undetected 3.5 months because `.gitleaks.toml` had zero rules); Codex's last review for 48 days (#2877) | incident doc; C9 | VERIFIED |
| 2026-08-01 | C7 decided: Claude review stays off; C9 filed; `CLAUDE.md` says "no automated review" | C7, C9 | VERIFIED |
| 2026-08-02 | ADR-010 metrics/alerting accepted (never provisioned) | C11 | VERIFIED |
| 2026-08-07/08 | Fly burst pool: 2 → 8 machines, limits 250 → 750/1000 | `271de56`, `7979499` | VERIFIED |
| 2026-08-10 → 08-12 | C13 (workflows never fire); SDK 57 bump breaks EAS (C17/C19); legacy migration audit finds earnings zeroed in production (A25/A26); C21 auto-merge race | ids | VERIFIED |
| 2026-08-13 | Four legacy-earnings items in one day (A30–A33); product decision to blend previous-app money | A31–A33 | VERIFIED |
| 2026-08-15 → 08-17 | Dual-run cutover audit; **old app confirmed still live and charging on the shared Stripe account** (A40, "dual-run confirmed intentional"); `migrate.py` deleted (A39) | A40, A39 | VERIFIED |
| 2026-08-17 → 08-20 | Float chain B28→B36 (HIST-001); CR #4187 hard-fails prefix collisions; whole-app fleet audit (2026-08-18) | ids | VERIFIED |
| 2026-08-21 | G2: 116 migration files never recorded as applied | G2 | VERIFIED |
| 2026-08-24/25 | Repo hygiene audit (stale planning docs); DB deadline-rejection alert storm (C42); user defers C43 (RLS on 4 tables) until legacy work ends | ids | VERIFIED |
| 2026-08-27 | CI/CD gates audit names C21 the #1 finding ("every gate … is advisory in practice"); insurance-period GPS corrections applied to production (C46) | audit:16; C46 | VERIFIED |
| 2026-08-31 | RLS DB-role test tier; nightly duplicate-prefix sweep; migration 379 prepared (not applied) | change-logs | VERIFIED |
| 2026-09-02 | `api-spinr.spinr.ca` outage — Fly cert never provisioned, "nothing watching it" | PR #4911 body | VERIFIED |
| 2026-09-03 | E13 monitors added (cert/domain/renewal/secret-rotation/capacity), most on placeholder data; C57 squash-merge lost a verified fix; C62 sweep | ids | VERIFIED |
| 2026-09-04 | Visual-regression baselines finally seeded (B38); `main` red for 2.5 h across 14 merges → `main-branch-guard.yml` | B38; workflow header | VERIFIED |
| 2026-09-06/07 | #5048 (34 files, money/auth) merged **47 s** after opening, before CI → B42 + OTP regex defect; A43/C73 filed | A43 | VERIFIED |
| 2026-09-08 | PR #5088 hardening (C74–C87: summary jobs never failed, Maestro 3,206 failures, AGENTS.md drift…); C96 all-Actions outage begins | `9d087e3`; C96 | VERIFIED |
| 2026-09-10/11 | Skia crash incident (severity corrected); B42 closed — 53/55 `payment_failed` events lost since 07-15; **`main` force-pushed** to purge driver PII files; sideways-car fixed for drivers only | incidents; B42; known-forks | VERIFIED |
| 2026-09-12 | Ride-experience benchmark audit finds 5 CarMarker divergences → `docs/known-forks.md` + parity guard; GitHub purge request drafted (not submitted) | known-forks; incident | VERIFIED |
| 2026-09-14 | `spinr.app` phantom domain (96 references, never registered) removed; notifications fork drifts a second time | C119; known-forks row 2 | VERIFIED |
| 2026-09-15/16 | Fly OOM incident (6 kills on 1 GB) → mixed fleet 2×4 GB + 6×2 GB; **Codex resumes** (#5480) | `fly-mixed-fleet.md:7`; `ba28a92`; search | VERIFIED |
| 2026-09-19 → 09-21 | Fleet-strategist agent; C107/C123 unreachable-RLS fixes; insurance derivation; Railway service-name fix (C5); `sprint-current.md` refreshed; Sentry triage command (C129) | ids | VERIFIED |
| 2026-09-23 | Fly deploy gated on CI/Security evidence; WS replay ordering; worker-fleet preflight; C133; C135 (open) | change-logs | VERIFIED |
| 2026-09-24 | Driver availability v2 backend (#5727/#5748, Codex-reviewed, Period 2 inside the claim RPC); clean-sheet audit prompt (#5753) and rapid baseline (#5756 open) | PRs | VERIFIED |
| 2026-10-31 (planned) | Old-app decommission target ("tentative") | `docs/runbooks/old-app-decommission.md:1` | VERIFIED (plan) |

Phases, in one line each: **Apr** import + first audits (money/state bugs already present) → **May–Jun** planning docs, Fly added → **Jul 26** change-log discipline begins as live testing starts → **Jul 30–Aug 1** security incident, both review bots gone → **Aug 7–21** capacity, legacy import in production, float chain, schema drift discovered → **Aug 24–Sep 4** gates audited, most found advisory → **Sep 6–11** the 47-second merge and its fallout; history rewrite → **Sep 12–24** parity/fork awareness, fleet resize, review resumes, v2 dispatch lands, this audit.

---

## §6 Steelman — what the history shows was done right

- **The change-log is real and dense.** 1,470 entries in 61 days, each with issue / root cause / risk / rollback / "what was NOT verified" sections (VERIFIED template use across the ~35 read). This audit was possible only because of it; most companies at this stage have nothing comparable.
- **`ACTION_ITEMS.md` corrects itself in public.** C5, C17, C73, B35, C113 and others carry dated "Correction:" paragraphs admitting an earlier framing was wrong, and C62 was a proactive sweep for a failure class (lost squash-merge fixes) after two instances. Duplicate ids (HIST-007) are a cost of that openness, not of concealment.
- **Gates were promoted, not just added.** G5b → blocking 2026-08-27; SR-03 money gate blocking; corporate coverage floor blocking (`ci-guardrails.yml:528`); CHECK B hardened twice; summary jobs made to fail (C74); `main-branch-guard` built as a backstop for the one gap the repo cannot fix itself.
- **Static tests that resolve re-exports** (`test_loguru_call_conventions.py`, C65) and **contract tests for forks** (`CarMarkerParity.test.ts`, the auth real-IP guard) are the two mechanisms that have actually stopped a family from recurring. They should be the template.
- **Decisions are recorded with reasons**: the 3.5 s fare-estimate wait as an accepted SLA exception; RLS "dormant-but-correct" (C108) with a doc-only close; `NEVER_APPLY` for 4 wrong-as-merged migrations instead of editing history; A32's product decision; the skia incident's severity downgrade written up honestly.
- **Money and safety were treated with the right caution once found**: migration 331 for `payouts.amount`, B42's structural fix ("covers this bug's class, not just this instance"), C46's production correction of 156 insurance-period rows with a validated tool, the v2 claim RPC rolling back when Period 2 fails (#5748).
- **Dual-run was handled as a decision, not an accident**: A40 confirmed the old app's charges were intentional; the decommission runbook has named owners and a date.
- **The fork registry exists and says what it is** ("a stopgap, not a fix"). Knowing the limit of a control is rarer than having the control.

---

## §7 Top 5 findings

1. **The merge gate is advisory in practice (HIST-013/014; §3 #1–2).** `main`'s required-checks list has never been audited by anyone with access (C21, open since 2026-08-12); #5048 merged in 47 seconds with a red required check and shipped B42 (53/55 `payment_failed` events lost) and an OTP defect on a live-tested surface. Every other gate in §3 inherits this. VERIFIED. Owner: repo admin (§9 Q1).
2. **Duplication-by-default with warn-only parity (HIST-002/015).** Five CarMarker divergences, a third marker copy nobody registered, three FCM exclusion sets, two notification inboxes, two auth files, two insurance-period implementations. The "surgical change" rule is doing its job and producing this. VERIFIED. Fix is a rule change, not a code change.
3. **"Never swallow" is prose (HIST-004).** The same anti-pattern was fixed in 2026-08-01, 08-11, 08-12, 09-11 and is open again in C135 (7 of 8 sites) after being "independently re-found in 10 separate" places. The one mechanism that worked elsewhere (a static test) has not been pointed at this class. VERIFIED.
4. **Docs are snapshots, not derived (HIST-005; §4).** The loop count has drifted five times and is wrong today in `CLAUDE.md`; `CLAUDE.md` says Codex is silent while Codex reviewed the last four PRs; the on-call capacity runbook describes a fleet that was replaced on 2026-09-16; three files repeat a false JWT-trust sentence. Agents read these files first. VERIFIED.
5. **Production schema is applied by hand (HIST-006; §3 #13–14).** 116 files untracked (G2), 7 stale (C44), 8 pending today (C125) including the RLS enable for the `settings` table that holds Stripe/Twilio/Maps keys (C43, deferred since 2026-08-25). The apply step needs a `DATABASE_URL` no session has. VERIFIED.

---

## §8 NOT VERIFIED

- GitHub branch-protection / rulesets for `main` (no session has access; C21). Everything about "required checks" is inferred from merge timing evidence in `ACTION_ITEMS.md`.
- Whether `ALERT_WEBHOOK_URL`, `ALERT_EMAIL_*`, `STRIPE_SECRET_KEY`/`TWILIO_*` (monitor), `SUPABASE_ACCESS_TOKEN`, `MAESTRO_CLOUD_API_KEY`, `FLY_API_TOKEN_STAGING`, `STAGING_URL` are set in GitHub/Fly secrets — §3 rows 16, 20, 21, 23 assume "unset" from the workflows' own comments and `ACTION_ITEMS.md`.
- Production database state: which of C125's 8 migrations are applied today; whether `verified_at` exists on production `drivers` (§4 row 11).
- The exact commit counts for `driver-app/lib/androidAuto/carSurface.tsx` and `backend/routes/websocket.py` (page 1 returned exactly 100, page 2 empty).
- Change-log per-file counts for ambiguous basenames (`page.tsx`, `index.tsx`, `_layout.tsx`, `conftest.py`, `_shared.py`, `otp.tsx`, `sidebar.tsx`, `ride-options.tsx`, `CarMarker.tsx` when cited by basename): dropped, so admin-dashboard/rider-app pages and both CarMarker copies are under-counted in §2.
- Codex's *reason* for stopping (07-30) and resuming (09-16) — only the comment timestamps were checked, not the app installation or billing.
- The Railway standby's actual deploy history before 2026-09-21 (C5's account is taken from `ACTION_ITEMS.md`).
- C96's root cause (billing suspected, "not billing-confirmed").
- Whether the 2026-07-30 service-role key was rotated (§4 row 16 — the repo's own documents disagree).
- Any GitHub issue body (`[CR]` issues) beyond what `ACTION_ITEMS.md` quotes.
- `docs/PRD.md` was grepped for a handful of claims (commission, surge cap, loops, transfer, VoIP), not read end-to-end against code.
- Whether the rapid baseline's RECURRENCE-003 "no persistent legacy cohort marker" is still true after the 2026-09-10 pre-launch flag work (`2026-09-10-pre-launch-flag-riders.md` was not read).
- No production build, test run, or command with side effects was executed by this lane.

---

## §9 Open questions only a human can answer

1. **Branch protection (C21/C73/A43):** what is in Settings → Branches → `main` → required status checks today, is "require branches to be up to date" on, and was #5048 merged via an admin bypass? Without this, every gate in §3 is advisory and HIST-014 will continue.
2. **Service-role key rotation:** the 2026-07-30 incident says rotation was "owner-actioned, confirmed in progress"; the 2026-08-27 CI audit and `security-gates.yml:10-14` say it "hasn't been rotated" and keep gitleaks G5a advisory on that basis. Which is true? If rotated, G5a can be made blocking and the incident closed; if not, this is still a P0.
3. **GitHub purge request (incident 2026-09-12):** has the support ticket for the driver-PII history rewrite been submitted? The document is still marked DRAFT.
4. **Codex:** why did reviews stop on 2026-07-30 and resume on 2026-09-16 (billing, app install, config)? Should `CLAUDE.md`'s review-handling section be restored to "act on Codex comments" now that it is active?
5. **Alert delivery:** is `ALERT_WEBHOOK_URL` (and the e-mail variant) set on the Fly app? If not, `loop_watchdog` and `capacity_watchdog` have never paged anyone, and C55's insurance-period alert has no channel.
6. **Migrations:** who applies C125's 8 pending files, when, and is the owner comfortable automating migrate-on-deploy (HIST-006) now that the legacy work that deferred C43 is winding down?
7. **What phase is the product actually in?** `CLAUDE.md` says "live app testing with real users"; `saskatoon-launch.md` still lists pre-public-launch gates (company insurance, SGI turnaround, Privacy Policy URLs); `.planning/PROJECT.md` says pre-launch; real Stripe charges have flowed since at least 2026-07-15. Is this an invite-only soft launch, and is there a rider/driver count the audit should size against?
8. **Old-app decommission (2026-10-31):** is the date firm, and does the old app still issue charges on the shared Stripe account (A40)? The dual-run explains several HIST-003 recurrences.
9. **Device verification (C70/C90/C103):** will any session or person get a real Android/iOS device and an Android Auto DHU? Every marker fix since 2026-08-16 is unverified on hardware.
10. **Staging:** does a staging Fly app / Supabase project exist and is anything deployed to it? `dast-zap-baseline.yml`, `deploy-backend-staging.yml` and `test-env.yml` all assume one.
11. **Actions budget (C96):** was the 2026-09-08 outage a minutes/billing cap? If so, at ~36 PRs/day with ~57 checks each, when does it recur?
12. **Rule change appetite:** CLAUDE.md's "surgical changes" and "prefer additive" rules are the direct cause of HIST-002/015. Is the owner willing to add "reconcile or contract-test a fork before the second fix lands" as a rule with the same weight?

---

*End of file. All sections complete; no context exhaustion. Written by the R1 Historian lane, 2026-09-24.*
