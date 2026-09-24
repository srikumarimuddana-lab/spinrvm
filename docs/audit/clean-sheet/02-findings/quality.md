# R14 — Quality & Test Architect — Findings (W3)

Scope: `backend/tests/`, `rider-app/__tests__`, `driver-app/__tests__`,
`admin-dashboard/e2e`, `.maestro/`, `.github/workflows/*.yml`. Builds on
`00-history.md`, `W0-SUMMARY.md`, and the test-coverage remarks already made by
`dispatch.md`, `money-cra.md` (§6 invariants table), `security.md`,
`trust-safety-fraud.md`, `compliance.md` (§5 compliance-as-code table),
`rider-journey.md`, `driver-journey.md`, `strategy.md`, `corporate.md`. Those are
cited, not repeated, except where this pass found something none of them did.

## 0. Method

Read CLAUDE.md Testing Conventions + coverage table, the master prompt §4–§7,
`roles.md` §3, `greenfield-extensions.md` §5/§7, `W0-SUMMARY.md`, and grepped
`docs/audit/clean-sheet/02-findings/*.md` for existing test-related remarks
(dispatch/money-cra/security/trust-safety-fraud/compliance/rider-journey/
driver-journey/strategy/corporate — all read via targeted grep + selected full
reads, not full re-reads of 300+-line files already covering test topics).
Ran read-only local test discovery only (`pytest --collect-only -q --no-cov
-p no:cacheprovider`, whole-suite and per-marker); did not run the suite for
pass/fail. Read `.github/workflows/ci-guardrails.yml`, `security-gates.yml`,
`ci.yml`, `maestro-e2e.yml`, `eas-rollout-control.yml`, `.gitleaks.toml`,
`.gitleaksignore` directly. Spot-checked (did not exhaustively re-verify) a
sample of tests for real-vs-stubbed coverage and patch-target correctness per
CLAUDE.md's explicit warnings. Grepped `ACTION_ITEMS.md` before filing; no new
duplicate of the nine listed pre-duplicated IDs was introduced.

## 1. Steelman

What the repo gets right, and why it looks the way it does:

- **The `--cov-fail-under` gap CLAUDE.md itself used to admit is now closed for
  every named module.** `corporate-coverage-floor-gate`, `money-path-coverage-
  floor-gate`, and `admin-coverage-floor-gate` in `ci-guardrails.yml` are real,
  blocking (`continue-on-error: false`), PR-diff-scoped jobs backed by
  `backend/scripts/check_{corporate,money_path,admin}_coverage_floor.py` —
  each floor carries a dated provenance comment (measured value, PR/run link)
  rather than an invented number. This is materially better than "the docs
  say 80%" with nothing enforcing it — VERIFIED by reading the three scripts
  and their `ci-guardrails.yml` job bodies (`ci-guardrails.yml:511-903`).
- **The Stripe webhook path is the most defended surface in the test suite.**
  Every one of the ~16 handled `event_type` branches in `routes/webhooks.py`
  has ≥1 test file naming the literal event-type string (spot-checked
  `payout.paid`/`payout.failed`, `test_webhooks_main.py:320-412`: real,
  non-stubbed — calls the actual `wh.stripe_webhook()` handler, mocks only
  Stripe SDK `construct_event` + the DB layer, asserts the real side effect
  `update_mock.await_args.args[2]["status"] == "completed"`). This is the
  shape CLAUDE.md's "real vs. theater" warning asks for, and it shows: B42
  (53/55 lost `payment_failed` events, HIST) was a CI-gate problem, not a
  missing-test problem — the test existed.
- **The RLS test tier does allowed+denied pairing, not just denial.**
  `backend/tests/rls/test_core_tables_rls.py` (18 tests) pairs
  `test_authenticated_cannot_select_another_users_row` with allowed-path
  siblings in the same file, matching CLAUDE.md's explicit "both allowed and
  denied paths tested" requirement for every new RLS policy — genuinely
  hard to fake, since this tier runs against a real Postgres role, not
  `mock_supabase_client`.
- **CI decay is visibly being fought, not ignored.** Multiple jobs
  (`security-gates.yml`'s `summary`, `ci-guardrails.yml`'s two coverage
  gates) carry inline comments citing the exact prior incident (C24, C74, CR
  #5572) that motivated the current logic, with the old bug's mechanism
  explained before the fix. That discipline is why §2's QUAL-001 below is a
  narrow residual gap, not "nobody looked."
- **The `.gitleaksignore` false-positive process is real, not a rubber
  stamp** — every entry names the PR, reproduces the match mechanically, and
  states why it isn't a real secret, which is exactly what let this pass
  identify the regex root cause in one read instead of re-litigating each
  incident (see QUAL-002).

## 2. Findings

### QUAL-001 — CI's two required "summary" gates (`ci-guardrails.yml`,
`security-gates.yml`) still go red on a superseded/force-pushed run despite a
targeted prior fix (CR #5572)
- Hierarchy: L2 Engineering Gates & CI/CD › L3 Required-checks aggregation › L4 — › L5 concurrency-cancelled PR run
- Severity: MEDIUM   Priority score: S×B×L = 3×4×4 = 48 (every PR, every force-push; blocks nothing permanently but manufactures false red on a required check)
- Status: VERIFIED (own-session observation, per orchestrator brief) + VERIFIED (code read)   Existing item: new (closest relatives: C24, C74 — both about a different failure mode of the same jobs, already closed)
- Adversary: none malicious — this is a reliability/trust-in-gates defect (flaky network / normal dev velocity), but a "Friday release" adversary benefits from it: a team trained to expect spurious red on their own required checks is more likely to override/re-run past a *real* red without reading it closely.
- Evidence: `ci-guardrails.yml:1783-1890` (`guardrail-summary` job, `Fail if any required gate failed` step, `if: contains(needs.*.result, 'failure') || (!cancelled() && contains(needs.*.result, 'cancelled'))`); `security-gates.yml:856-913` (`summary` job, identical pattern, comment cites CR #5572 verbatim and claims the fix "distinguishes the two"); PR #5759 run `36035912841`, job `107756383821` (ci-guardrails summary) and job `107756289004` (security-gates summary) both concluded `failure` on a commit superseded by a follow-up push in the same PR.
- What happens (plain language): a contributor pushes a fix-up commit while CI is still running on the previous push. `concurrency: cancel-in-progress: true` cancels the stale run's in-flight gate jobs (correct — that's the point of the concurrency group). The two roll-up "summary" jobs still execute (they use `if: always()`) and are supposed to recognize "my dependencies were cancelled because I was superseded, not because something is broken" and pass quietly. Instead they report `failure` on the abandoned commit — a same-shape false red to the one CR #5572 was written to close, on the exact two jobs CR #5572 patched.
- Root cause: the fix's `!cancelled()` guard assumes GitHub Actions' bare `cancelled()` function reflects "was the overall workflow run cancelled" from the point of view of a downstream job that itself keeps running via `if: always()`. In practice `cancelled()` evaluated inside a job's own `if:` reflects that job's own step-level cancellation state, not the run-level cancellation signal that concurrency's `cancel-in-progress` sends to the *other*, superseded jobs — a summary job that survives via `always()` and completes its own (trivial) steps normally was never itself cancelled, so `cancelled()` stays `false` inside it even when every job it depends on shows `result: cancelled` for exactly that reason. The `(!cancelled() && contains(needs.*.result, 'cancelled'))` clause therefore still fires whenever a supersede happens, which is the same trigger condition CR #5572 set out to neutralize — the fix narrowed the *comment's* claim without changing the actual outcome for this job shape.
- Recommendation: drop the `cancelled` branch from the fail condition entirely — fail only on `contains(needs.*.result, 'failure')`. In standard GitHub Actions usage a required job cannot be selectively cancelled independent of the whole run being superseded/cancelled (there is no API to cancel one job in isolation), so `cancelled` in `needs.*.result` is, in practice, always evidence of "this run was superseded," never evidence of "one gate broke while the others were fine." Treating it as a non-failure is therefore safe, not a coverage loss: a genuinely broken gate reports `failure`, not `cancelled`, and that branch is untouched. Alternative considered: keep chasing `cancelled()`/`github.run_attempt`/`steps.*.conclusion`-based detection of "was this specific run superseded" — rejected: GitHub's own docs and this repeated failure both show the semantics are not reliable enough to build a required check on, and the simpler rule (ignore `cancelled`, only fail on `failure`) gives the same practical guarantee with no race window.
- Blast radius: both `guardrail-summary` (`ci-guardrails.yml`) and `summary` (`security-gates.yml`) — confirmed by grep these are the only two copies of this exact pattern in the repo (per the code's own CR #5572 comment). No other workflow's aggregator uses this shape.
- Rollout: pure CI-config change to two `if:` expressions; no flag needed, no production code touched, no migration.
- Verification to close: push two commits in quick succession to a scratch PR (or reuse the pattern that produced run `36035912841`) and confirm the summary jobs on the superseded run show `skipped`/neutral rather than `failure`; confirm a real injected gate failure (e.g. a deliberately broken bandit rule) still fails the summary job unconditionally.

### QUAL-002 — `.gitleaks.toml`'s custom `spinr-driver-export-pii` rule spans
newlines and file content unrelated to its own keyword, producing a third
recurrence of the same false-positive class (npm scope name, Docker digest,
this very audit's own report)
- Hierarchy: L2 Engineering Gates & CI/CD › L3 Secret scanning (G5a/G5b) › L4 — › L5 custom regex rule
- Severity: LOW-MEDIUM   Priority score: S×B×L = 2×3×5 = 30 (false positive only — no security exposure, but now 3 confirmed incidents plus per-incident `.gitleaksignore` entries with multi-paragraph justifications, each requiring a human to re-derive "is this real")
- Status: VERIFIED   Existing item: new (the rule and its two prior FPs — #4778, #4829, #5456 — are already documented in `.gitleaks.toml`/`.gitleaksignore`; no ACTION_ITEMS entry currently tracks the regex itself)
- Adversary: none — but a *real* leak sitting in the same file as one of these ignored fingerprints would be invisible to a reviewer conditioned to see "another `spinr-driver-export-pii` FP, ignore it" (alert fatigue is the actual risk vector, not the regex itself).
- Evidence: `.gitleaks.toml:130-136` — `regex = '''['"][^'"@]+@[^'"@]+\.[^'"]{2,}['"]'''`, and a keyword pre-filter of five driver-export column names (see `.gitleaks.toml:130-136`; not repeated here, because naming them in this file would make the rule scan it). Three documented incidents in `.gitleaksignore`: PR #4829 (`:33-60`, two unrelated files sharing only one of those keywords, match spans "across unrelated decorator lines and punctuation with no email address anywhere in between" — the comment's own words), PR #5456 (`:109-117`, fake single-char test emails, "flags any email-shaped string, regardless of proximity"), and PR #5759 (`:119-126`, this audit's own `docs/audit/clean-sheet/02-findings/security.md`, matches starting at line 75 spanning into `@logrocket/react-native` and line 234 spanning into `python:3.12.13-slim@sha256`).
- What happens (plain language): any file containing one of the five keyword strings anywhere, plus a quote-@-dot-quote shape anywhere later in the file (even many lines later, since the character classes `[^'"@]` and `[^'"]` are not `.` and therefore match `\n` by default in RE2/Go regex, gitleaks' engine), trips G5a/G5b. Each hit costs a human a manual "is this real" investigation and a multi-line `.gitleaksignore` entry with a per-commit-hash fingerprint that breaks again on rebase (per the tool's own note at `.gitleaksignore:24-29`).
- Root cause: the regex was written to bound an email shape with quote marks, but used negated-character-classes (`[^'"@]`, `[^'"]`) instead of `.`, and RE2's negated classes include `\n` unless explicitly excluded — so there is no practical proximity limit between the keyword-triggering pre-filter and the pattern match; a match can start on one line and legitimately terminate many lines/paragraphs later, at the first available `quote…@…dot…2+chars…quote` shape anywhere downstream, regardless of content.
- Recommendation: tighten the regex to (a) exclude `\n` explicitly and (b) require an actual email-shaped local-part/domain instead of "any non-quote-non-@ run": `'''['"][A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}['"]'''`. This alone closes all three documented incidents: it can't span a line break (none of the allowed character classes include `\n`), it can't match `@logrocket/react-native` (no dot-then-2-24-letters immediately before the closing quote — `/react-native"` isn't a valid TLD shape), and it can't match `...@sha256:` (no literal `.` between `@` and the 64-hex-char digest before a closing quote). Cost: one-line regex edit + a re-run of gitleaks against the current tree to confirm no new true positives surface once the pattern tightens (expected: none, since real leaks in this shape would already have tripped the *broader* rule). Alternative considered: add a proximity/window constraint (keyword within N characters of match) instead of tightening the character classes — rejected as the secondary fix, not the primary one: it wouldn't have prevented the PR #5759 case (whose matches were genuinely within the same short doc, just crossing a line break), whereas excluding `\n` and requiring email shape closes all three observed cases directly; a proximity window is still worth adding as defense-in-depth if a fourth incident surfaces after this fix.
- Blast radius: `.gitleaks.toml` only (one rule); no other rule in the file uses a negated-class-without-newline-exclusion pattern (checked `spinr-sin-bank-pii`, `.gitleaks.toml:104-109` — its regex is `\d{9}` with no `[^...]` class, unaffected).
- Rollout: no flag needed — a gitleaks rule change takes effect on the next scan; not a runtime/production change. Re-run `gitleaks detect --no-git --config .gitleaks.toml` locally against the working tree before merging the regex change to confirm no unexpected new hits.
- Verification to close: run the tightened regex (via a small Python/Go re2 harness, since gitleaks itself needs the binary) against the three archived false-positive shapes in `.gitleaksignore` (`:33-60`, `:109-117`, `:119-126`) and confirm zero matches; run it against a synthetic real-shaped leak (one line holding a licence-number key and a quoted address at an `example.com` mailbox, built inside the test harness rather than written into a committed doc) and confirm it still matches.

### QUAL-003 — WITHDRAWN (orchestrator re-verification, 2026-09-24): the corporate surge-exemption fare branch is tested

> **Erratum.** This finding is false and is withdrawn. `backend/tests/test_corporate_surge_bypass.py`
> pins the exemption at three layers: the `_is_corporate_paid` truth table (`:38-92`), the
> estimate endpoint dropping surge to 1.0 for corporate context with a personal-ride negative
> control (`:193-240`), and the booking endpoint persisting 1.0 even over a token-locked surge,
> plus a charge-site clamp case (`:370-443`). The lane's search used the terms `forcing surge`,
> `corporate_bypass`, and a fixed list of fare test files, and missed this file by name.
> Status: VERIFIED false by direct read. Counted as a verifier error in the Step 6 error rate.
> The original card is kept below, struck, for audit trail only; do not act on it.

~~Original title: The corporate surge-exemption fare branch has code enforcement but no
located test naming or exercising it~~
- Hierarchy: L2 Payments & Money › L3 Fare calculation › L4 Surge pricing › L5 corporate-paid ride surge bypass
- Severity: HIGH   Priority score: S×B×L = 4×4×3 = 48 (CLAUDE.md hard "must-have-a-test" item; money-path module; regression here means an *overcharge* on a corporate-billed ride, invisible to the rider who isn't paying and to the driver who's paid the surged amount regardless — the corporate customer is the only one positioned to notice, via their invoice, well after the fact)
- Status: VERIFIED (code); VERIFIED (absence, by grep — see evidence)   Existing item: new (money-cra.md §"Mandatory closures" lists "corporate/scheduled exemption" as a closure it owns; this pass could not find where that closure produced a *test* for the corporate half specifically — the two lanes may have used different search terms; flagging here so it isn't silently assumed covered)
- Adversary: malicious insider / fraudulent corporate admin colluding with a driver during a demand spike — book on personal card first to confirm surge is active, then confirm the same trip on the corporate account is un-surged as expected (or, if the exemption silently regresses, is *not*, and the company eats the difference with no rider-side signal to catch it).
- Evidence: enforcement exists at `backend/routes/rides/booking.py:902` (log line literally reads `f"corp={body.corporate_account_id}: forcing surge {float(surge)} → 1.0"`) and `backend/routes/rides/estimates.py:415` (comment: "CLAUDE.md: surge does not apply to corporate-paid rides") / `:510` (`surge = Decimal("1.0") if corporate_bypass else _d(fare_info.get("surge_multiplier", 1.0))`). Searched `backend/tests/` for `"forcing surge"`, `"corporate_bypass"`, and `corporate` co-occurring with `surge` in `test_fares.py`, `test_fares_coverage.py`, `test_surge_engine.py`, `test_surge_override_clamp.py`: zero hits. The nearby `test_coverage_rides.py:1322-1429` tests are real but test a *different* corporate branch — whether a ride requires a stored card (payment-method classification), not whether surge is forced to 1.0 for a corporate-billed ride.
- What happens (plain language): nothing today — the code path is short and looks correct on inspection. But per CLAUDE.md's own "What must have a test" list ("Every fare calculation branch (tiers, surge, corporate, promo)"), this specific branch is exactly the kind of thing that gets refactored (e.g. when `estimates.py`'s corporate-bypass resolution logic is touched for an unrelated reason) with no regression signal until a corporate customer's invoice shows surge pricing that shouldn't be there.
- Root cause: the exemption was implemented in two call sites (`booking.py`, `estimates.py`) with no single canonical test file that exercises either, unlike the analogous `SURGE_CAP` clamp (which `strategy.md` confirms is tested at least at the cap-clamp level).
- Recommendation: add a test to `test_fares.py` or `test_fares_coverage.py` that (a) builds a fare/estimate request with `corporate_account_id` set + `work_profile=True` (matching the same corporate-billed classification `test_coverage_rides.py:1365-1404` already establishes) against a service area with an active auto-surge multiplier > 1.0, and asserts the returned `surge_multiplier`/fare total reflects 1.0×, not the active multiplier; a second case with the same active surge but no `corporate_account_id` asserting the surged value *is* applied (negative control, so the test can't pass by accident). Alternative considered: rely on the existing card-requirement tests as implicit coverage — rejected: they assert HTTP status codes from `create_ride`'s early validation branch, before fare calculation runs, so they cannot and do not exercise the surge value itself.
- Blast radius: `backend/routes/rides/booking.py`, `backend/routes/rides/estimates.py`, `backend/services/fare_service.py` (surge is a parameter, not computed there — confirmed by reading `fare_service.py:73-244`, which takes `surge: Decimal` from its caller rather than deriving corporate status itself, so both call sites must independently get this right — a second reason a shared test matters).
- Rollout: test-only change, no code/flag/migration.
- Verification to close: new test green; confirm it fails (red) if the `corporate_bypass` ternary in `estimates.py:510` or the `booking.py:902` assignment is temporarily reverted locally, proving the test actually exercises the branch rather than passing regardless (per CLAUDE.md's stubbed-dependency warning).

### QUAL-004 — Admin-dashboard/rider-app/driver-app Playwright E2E is
`continue-on-error: true` on every PR, and the "blocking on main" fallback
gates nothing that actually deploys
- Hierarchy: L2 Engineering Gates & CI/CD › L3 E2E release gate › L4 — › L5 PR merge / post-merge deploy
- Severity: HIGH   Priority score: S×B×L = 4×5×4 = 80 (applies to all three customer-facing web/mobile-web surfaces, on every PR, and the stated compensating control — "blocking on main" — does not actually block the thing it needs to block)
- Status: VERIFIED   Existing item: tracked as `[17-1]`, explicitly marked "✅ Fixed" in `reports/remediation/admin-PE-P2-before-launch.md:16` — this finding is that the "fix" leaves a real gap the tracker's own resolution note doesn't mention
- Adversary: hostile network/device persona and the plain "Friday release" pattern named in this audit's mandate — a broken E2E flow (e.g. a booking-flow regression) merges because the check that would have caught it never blocked the PR, and the one place it's supposed to catch it after merge (`main`) has no consumer wired to its result.
- Evidence: `.github/workflows/ci.yml:557,577` (`e2e-test`, admin-dashboard: `continue-on-error: ${{ github.event_name == 'pull_request' }}`), `:741,758` (`rider-app-test`'s Playwright E2E job, same pattern), `:818,832` (driver-app equivalent, same pattern). Contrast `:638-736` (`visual-regression-test`): no `continue-on-error` line at all — genuinely blocking on PRs, matching CLAUDE.md's B38 claim. Deploy gap: `:890-893` — `deploy-admin` job's `needs: [admin-test]` only (not `e2e-test`, not `visual-regression-test`) and `if: github.event_name == 'workflow_dispatch'` (manual-only in this workflow; Vercel's own git integration is the actual auto-deploy path for admin-dashboard per CLAUDE.md's Deployment section, and is not gated by any GitHub Actions check at all). `docs/change-log/2026-07-29-admin-dashboard-a11y-ci-gate.md:32` independently confirms and dates this: "a red `e2e-test` on `main` cannot block or delay a production deploy."
- What happens (plain language): a rider/driver/admin-facing regression that a Playwright E2E spec would catch can merge to `main` with a visibly red (but non-blocking) check on the PR, and nothing downstream — not a required-check rule (unconfirmed, C21), not `deploy-admin`, not Vercel's own build — reads that red status before the change reaches production. The one thing that *is* blocking (`visual-regression-test`) only catches pixel-level regressions on 6 seeded admin pages, not functional/flow regressions, and doesn't cover rider-app/driver-app at all (no visual tooling exists there per CLAUDE.md gate 6).
- Root cause: `[17-1]` was scoped and closed as "make E2E non-blocking on PRs, blocking on main, while the suite stabilises" — a reasonable staged-rollout call at the time — but the second half of that plan (something on `main` actually consuming the "blocking" result) was never built, so the staged rollout permanently stopped at stage one with no signal that stage two never landed.
- Recommendation: either (a) wire a required-status check or branch-protection rule on `main` that reads `e2e-test`/rider/driver E2E conclusion and blocks the *next* merge attempt if `main` itself is red (the common "keep main green" pattern — a merge queue or a scheduled "is main red" alert that pages before the next deploy), or (b) if branch-protection contents can't be confirmed/changed from this session (per `00-history.md`'s carried-forward human-only question), promote these three jobs to blocking-on-PR now that they've had since 2026-07-29 to "stabilise" — re-verify current flake rate first via their own historical run data before flipping, using the same evidence-based approach `update-visual-baselines.yml`/B38 used. Alternative considered: leave as-is and rely on `visual-regression-test` as the de facto gate — rejected: visual regression cannot catch a functional break (e.g., a booking button that renders correctly but posts to the wrong endpoint).
- Blast radius: `e2e-test` (admin-dashboard), `rider-app-test`'s E2E job, `driver-app-test`'s E2E job — three jobs, one file (`ci.yml`); branch-protection configuration (unknown contents, per `00-history.md` §9 human-only question) is the other half of this fix and is outside this session's visibility.
- Rollout: CI-config change; if flipping to blocking, do it as its own commit so a subsequent flaky-test fix isn't conflated with the gate change, and watch the next 5-10 PRs' real pass rate before treating it as fully blocking in spirit (not just in YAML).
- Verification to close: confirm (via GitHub API or a human with branch-protection access) whether `main`'s protection rule requires these checks at all today; if not, that's the actual root cause and this finding's fix is a branch-protection change, not a workflow-file change.

### QUAL-005 — Coverage floor gates enforce a *regression buffer* below
CLAUDE.md's literal stated minimums, not the minimums themselves
- Hierarchy: L2 Engineering Gates & CI/CD › L3 Coverage gates › L4 — › L5 per-module floor values
- Severity: MEDIUM   Priority score: S×B×L = 3×3×3 = 27
- Status: VERIFIED   Existing item: new (adjacent to, but distinct from, the "not yet enforced" gap CLAUDE.md itself already corrected — this is about what the *now-enforced* gate actually enforces)
- Adversary: none malicious — an engineering-process gap that a rushed PR (the "Friday release" pattern) can exploit unintentionally.
- Evidence: `backend/scripts/check_money_path_coverage_floor.py:100-106` — `routes/payments.py` floor = **85.0%** while its own docstring (`:56-58`) and CLAUDE.md both state the target as **≥90%**; the same file's module docstring (`:49-54`) documents the convention explicitly: "measured - 5, rounded down to the nearest 5" as a deliberate buffer. `backend/scripts/check_corporate_coverage_floor.py:65` — `routes/corporate_accounts.py` floor = **75.0%** ("measured 82%, 2026-07-28") while CLAUDE.md's later-dated corporate section states the module now measures ~97% aggregate and targets ≥80% — the floor was never re-measured/re-tightened after the module's coverage improved, so it still permits a regression to 75-79%, silently below the documented ≥80% target, with CI staying green.
- What happens (plain language): a PR that drops `routes/payments.py` test coverage to, say, 87% (still under the documented 90% minimum) passes CI cleanly — the gate was designed as "don't get *worse* than a snapshot taken on a specific date," not "stay at or above what CLAUDE.md promises," and nothing in the PR, the gate's summary comment, or the merged diff communicates that the two numbers differ.
- Root cause: the "measured minus 5, rounded down" convention (deliberately documented, not accidental) optimizes for avoiding false-fail on measurement noise/flaky mocks, at the cost of permanently sitting below the stated target for any module whose measured value was close to its target when the floor was set — and the floor was never designed to ratchet back up as real coverage improved past the original measurement.
- Recommendation: add a lightweight informational check (not a new blocking gate — the existing floor mechanism already does the real work) that, on a schedule or on `main`, re-measures each tracked module and warns (not fails) if measured coverage now clears `target + 5`, so the floor can be manually ratcheted upward — same "human decides, tooling proposes" pattern the repo already uses for `eas-rollout-control.yml`. Alternative considered: set floors equal to the literal CLAUDE.md target with no buffer — rejected: this is what the 2026-08-19 payments.py history already shows going wrong (86.1% measured against a 90% target caused exactly the failure this buffer exists to avoid without conflating "flaky" with "real regression"); the buffer is the right call, it just needs a return path.
- Blast radius: `routes/payments.py` (85% floor vs 90% target), `services/fare_service.py`/`utils/crypto.py` (both floors already ≥ their target — not affected), `routes/corporate_accounts.py` (75% floor vs current ~97% measured / 80% target — the largest documented gap of the manifest), `routes/corporate_company_bookings.py` (80% floor, matches 80% target exactly — no gap), the four "conservative 50%→measured" corporate rows (all now floored 5pts under their PR #3308 measurement, not re-checked against CLAUDE.md's later 79-100% per-module figures).
- Rollout: informational tooling only; no blocking behavior change, so no flag needed.
- Verification to close: a script that diffs `FLOOR_MANIFEST` values against the newest dated measurement cited in CLAUDE.md's corporate coverage paragraph and money-cra's own numbers, flags any floor sitting > 5pts below current reality (opportunity to ratchet up) or > 0pts below the *documented target* specifically (the actual promise-vs-enforcement gap this finding is about).

### QUAL-006 — 73% of the backend test suite carries no tier marker
(`unit`/`integration`/`slow`), and the `integration` tier is functionally
unused
- Hierarchy: L2 Engineering Gates & CI/CD › L3 Test pyramid › L4 — › L5 marker discipline
- Severity: MEDIUM   Priority score: S×B×L = 3×3×3 = 27
- Status: VERIFIED (`pytest --collect-only`, this session, `backend/`)   Existing item: new
- Adversary: none — a developer-experience/CI-hygiene gap, not a security/money issue, but it undermines the specific promises CLAUDE.md makes for the fast local loop.
- Evidence: full-suite collection: **17,391 tests**. `-m unit`: **4,162** (23.9%). `-m integration`: **5** (0.03%). `-m slow`: **554** (3.2%). `-m anyio`: 5,472 (separate axis, not a tier marker). `-m "not unit and not integration and not slow"`: **12,670** (72.8%) — carries none of the three documented tier markers.
- What happens (plain language): CLAUDE.md documents three concrete promises tied to these markers — `pytest -m unit` for "fast local loop" (target < 100ms/test), `pytest -m "not slow"` for pre-push, and a described "Integration: real Supabase against a throwaway test schema" tier with its own 2s/test target. In practice, `pytest -m unit` only ever runs a quarter of the suite (so "fast local loop" checks a minority of the code), `pytest -m "not slow"` (pre-push) still runs the 12,670 unmarked tests — most of the suite — so it isn't meaningfully faster than the full run, undercutting its stated purpose as a quick pre-push gate, and the "integration" tier as CLAUDE.md describes it (real Supabase) essentially does not exist as a distinct, discoverable set (5 tests) — most Supabase-touching tests are presumably just unmarked, indistinguishable by marker from a pure-mock unit test.
- Root cause: markers were added opportunistically to specific test files (matching HIST's "duplication-by-default" pattern — conventions exist but aren't mechanically enforced) rather than via a collection-time default or a lint rule requiring every test module to declare a tier.
- Recommendation: add a `pytest_collection_modifyitems` hook in `conftest.py` that auto-applies `unit` to any test whose module path isn't already `slow`/`integration`-marked and doesn't use a real-DB fixture (or, simpler: flip the default and require `slow`/`integration` to opt out, since those are the minority and their fixtures are already distinctive — a real-Supabase test already imports something an in-memory mock test doesn't). Add a CI check (same shape as `test_loguru_call_conventions.py`'s static scan) that fails if a new test file adds no tier marker at all. Alternative considered: leave it and just fix CLAUDE.md's wording to describe reality — rejected: the underlying promise (a genuinely fast, representative pre-push loop) is worth keeping; the doc is not wrong to want it, the enforcement is missing.
- Blast radius: none directly (test-only, no production code) — but every future author relying on "pytest -m unit is fast and representative" is mildly misled until fixed.
- Rollout: additive (a new conftest hook + a new CI lint check); no flag needed; roll out the hook first and observe marker distribution shift before adding the blocking lint check, so a first pass doesn't fail every existing unmarked file at once.
- Verification to close: re-run the four collection counts above after the hook lands and confirm the unmarked bucket is at or near 0; confirm `pytest -m unit`'s wall-clock time is now a genuinely small fraction of the full suite's.

### QUAL-007 — Two async test runners coexist (`pytest-asyncio` auto mode +
the `anyio` plugin), and CLAUDE.md's testing-conventions text describes only
one of them
- Hierarchy: L2 Engineering Gates & CI/CD › L3 Test pyramid › L4 — › L5 async test execution
- Severity: LOW-MEDIUM   Priority score: S×B×L = 2×3×3 = 18
- Status: VERIFIED (config) / INFERRED (risk characterization — no concrete failure observed this pass)   Existing item: new
- Adversary: none directly — a latent-fragility/doc-drift finding, matching HIST's "docs are snapshots" theme.
- Evidence: `backend/pytest.ini:6` — `asyncio_mode = auto` (pytest-asyncio's auto-detection mode; **any** `async def test_...` runs correctly without an explicit marker). `backend/tests/conftest.py:174-176` — `pytest_plugins = ["anyio"]`, loaded "so `@pytest.mark.anyio` is available," alongside the `asyncio_mode = auto` setting per its own comment. Collection: 5,472 tests carry the `anyio` marker; a file-level grep for the literal string "anyio" across every test file containing at least one `async def test_` found 177 files with zero occurrences (crude: whole-file text search, not scoped per-function — some of those 177 may have a class-level marker phrased differently, not independently re-verified for all 177). CLAUDE.md's Testing Conventions section states only "Use `@pytest.mark.anyio` for async tests (loaded explicitly in `conftest.py`)" — it does not mention `asyncio_mode = auto` or that unmarked async tests run anyway via a different runner.
- What happens (plain language): an unmarked `async def test_...` function does still execute today (via pytest-asyncio's auto mode) — this is not currently silently-skipped, contrary to what a literal reading of CLAUDE.md alone would suggest ("a missing marker either fails collection or silently doesn't run as intended"). But it means two different async test harnesses are active in the same suite, each with its own event-loop/fixture-scoping semantics (`anyio`'s fixtures vs. `pytest-asyncio`'s), and a test or fixture author following CLAUDE.md's literal instruction to always add `@pytest.mark.anyio` for "correctness" may unknowingly opt a test into the *other* runner's semantics for no functional reason, or an anyio-marked test may accidentally depend on a pytest-asyncio-scoped fixture (or vice versa) in a way that works today by coincidence of both plugins sharing the same underlying event loop implementation in this asyncio-only (no trio) configuration, but is not guaranteed.
- Root cause: `asyncio_mode = auto` appears to have been added as a safety net at some point (its own comment ties it to the `anyio` plugin's coexistence: "alongside the `asyncio_mode=auto` setting in pytest.ini") without CLAUDE.md's documented convention being updated to explain why both exist or which one a new test should target.
- Recommendation: document the actual dual-mechanism state in CLAUDE.md's Testing Conventions (one sentence: "async tests also run without the marker via `pytest-asyncio`'s `asyncio_mode = auto`; `@pytest.mark.anyio` is required specifically for tests that need anyio's own fixtures/parametrization, not for async execution in general") so the marker's real purpose is legible, and audit whether any test with an anyio-plugin-dependent fixture is missing the marker (the actual failure mode CLAUDE.md's current wording warns about) — a static test in the `test_loguru_call_conventions.py` style could assert every test using a known anyio-only fixture also carries the marker. Alternative considered: rip out one of the two mechanisms for a single async runner — rejected without more evidence of why both were introduced (git-blame/PR history not pulled this pass); a documentation fix is the safe, minimal first step; consolidation is a "Next," not a "Now."
- Blast radius: every async test in `backend/tests/` (7,248 `async def test_` functions found by grep) is nominally affected by which runner actually executes it, though the practical divergence risk is narrow (both runners share the asyncio backend here, no trio usage found).
- Rollout: doc-only recommendation; the static-test follow-up is additive tooling.
- Verification to close: CLAUDE.md wording updated and reviewed; a follow-up pass (out of this session's budget) samples the 177 files to confirm none actually needs anyio-specific fixtures without the marker.

## 3. Critical-scenario test table

`Status` legend: **Real** = invokes the actual changed function/branch and
asserts on its real output/side-effect (CLAUDE.md's bar). **Stubbed** = the
component under test (or its DB layer) is mocked away entirely — CLAUDE.md's
explicit "zero real coverage" case. **Present-not-reverified** = a test file
exists and was cited by an upstream lane as real, taken on their word, not
independently re-read this pass. **None** = no test located.

| Critical scenario | Test file:function | Status | Evidence |
|---|---|---|---|
| `_require_ride_in_state` guard (allowed/wrong-state/404/idempotent) | `test_ride_state_machine.py::TestRequireRideInState` (4 tests) | Real | direct read, `:37-105` |
| Cancel-guard: reject cancel after `in_progress`; allow from `searching`/`driver_assigned` | `test_ride_state_machine.py::TestCancelStateGuardRider` (5 tests) | Real | `:156-245` |
| Atomic complete under concurrent CAS race | `test_ride_state_machine.py::TestCompleteRideAtomicGuard::test_concurrent_completion_loses_cas_raises_ride_state_error` | Real | `:262-` |
| `driver_assigned → driver_accepted` (accept action) | not found in `test_ride_state_machine.py`; present in `test_dispatch_match_attempt_branches.py`, `test_dispatch_metrics.py` (per grep) | Present-not-reverified | grep only, `driver_accepted` string match |
| `driver_assigned → searching` (offer timeout / reaper) | `test_offer_expiry_reaper.py`, `test_offer_expiry_reaper_coverage.py`, `test_offer_timeout.py`, `test_offer_expiry_accept_race.py`, `test_driver_claim_reaper.py` | Present-not-reverified | file names only — **not** in `test_ride_state_machine.py`, contrary to CLAUDE.md's "add a case to `test_ride_state_machine.py`" instruction |
| `scheduled → searching` (dispatch-time transition) | no dedicated hit in `test_ride_state_machine.py`; scattered mentions in `test_p2_scheduled_rides.py`, `test_c26_scheduled_overlap.py` etc. | Present-not-reverified | grep only |
| **Gap:** CLAUDE.md instructs every new transition to get "a case in `test_ride_state_machine.py`" specifically; three of the six documented transitions (accept, offer-timeout reversion, scheduled-dispatch) live in *other* files instead, not centralized — see recommendation below |
| Surge auto-tier lookup + `SURGE_CAP` clamp | `test_surge_engine.py`, `test_surge_override_clamp.py` | Present-not-reverified | cited also by `strategy.md` §"Surge is bounded" |
| Corporate surge exemption (surge forced to 1.0 for corporate-billed rides) | `test_corporate_surge_bypass.py` (truth table + estimate + booking, with negative controls) | Real | orchestrator direct read; QUAL-003 withdrawn |
| Corporate card-requirement classification (adjacent, not the same branch) | `test_coverage_rides.py:1322-1429` (3 tests) | Real | direct read this pass |
| Promo fare branch | `test_promo_per_user_race.py`, `test_promo_rate_limit.py`, `test_promotions_coverage.py` | Present-not-reverified | file names only |
| Stripe `payout.paid`/`payout.failed` | `test_webhooks_main.py::TestStripeWebhookPayoutSettlement` (2+ tests) | Real | direct read, `:320-412` |
| Stripe `payment_intent.succeeded`/`.payment_failed`, `checkout.session.completed`, `charge.refunded`, `charge.dispute.*`, `customer.subscription.*`, `invoice.paid`/`.payment_failed`, `account.updated`, `refund.updated`/`.failed` (remaining ~13 of ~16 handled types) | ≥1-12 test files each, e.g. `test_webhook_stripe_v15.py`, `test_refund_lifecycle_webhook.py`, `test_corporate_webhook.py`, `test_webhooks_corporate_subscription.py`, `test_webhooks_coverage_gap.py` | Present-not-reverified | grep for literal event-type string; one spot-check (payout) came back Real, taken as a weak positive signal for the rest, not proof |
| RLS: rider cannot read/update another rider's `users`/`rides` row (denied) + own-row (allowed) | `backend/tests/rls/test_core_tables_rls.py` (18 tests, both directions) | Real (direct-Postgres-role tier) | direct read, `:69-255` |
| RLS: 16 other tracked policy files (corporate billing, disputes, safety, referral/payout, notifications, stripe-admin, transactional outbox, etc.) | `backend/tests/rls/test_*.py` (17 files total) | Present-not-reverified | file listing only, one spot-checked |
| SOS end-to-end incl. expired-token grace window | `test_sos_expired_token.py` (6 cases) | Real | cited and independently confirmed by `trust-safety-fraud.md` as VERIFIED; not re-read this pass, high-confidence secondhand |
| Insurance period transitions (Period 0-3, append-only) | `test_insurance_period_rpc.py` | Present-not-reverified | cited by `compliance.md` CC-11 |
| Refresh-token reuse detection / `token_version` force-logout | `test_refresh_token_reuse_detection.py` | Present-not-reverified | cited by `trust-safety-fraud.md` |
| Payout path (driver earnings → Stripe transfer, incl. legacy-ride exclusion) | `test_earnings_coverage.py`, `test_auto_payout.py` | Real (spot-checked `test_earnings_coverage.py:124-188` this pass — calls actual `get_driver_balance`, asserts real `payable_balance`/`total_earnings` values) | direct read |

## 4. CI gate inventory — advisory / continue-on-error / red

Not a full re-derivation of R13's SRE/CI lane (`reliability.md`) — scoped to
release-gate/test-execution jobs specifically, per this lane's charter.

| Job | File | On PR | On `main`/push | Note |
|---|---|---|---|---|
| `e2e-test` (admin-dashboard Playwright) | `ci.yml:558` | advisory (`continue-on-error: true`) | blocking | QUAL-004: "blocking" on main isn't consumed by any deploy gate |
| `rider-app-test`'s E2E job | `ci.yml:741` | advisory | blocking | same gap |
| driver-app E2E job | `ci.yml:818` | advisory | blocking | same gap |
| `visual-regression-test` | `ci.yml:638` | **blocking**, no `continue-on-error` | blocking | genuinely enforced (B38 closed, confirmed) |
| `coverage-regression-gate` | `ci-guardrails.yml:108,149` | advisory by design (`continue-on-error: true`) | same | whole-codebase aggregate only; intentionally advisory per its own header comment — a real per-module regression can hide here (this is *why* the three floor gates below exist) |
| `corporate-coverage-floor-gate` | `ci-guardrails.yml:528` | **blocking** | same | real, PR-diff-scoped (see §5) |
| `money-path-coverage-floor-gate` | `ci-guardrails.yml:640` (inferred from pattern, not independently line-verified this pass) | **blocking** | same | real, PR-diff-scoped |
| `admin-coverage-floor-gate` | `ci-guardrails.yml:738` | **blocking** | same | real, PR-diff-scoped |
| `guardrail-summary` | `ci-guardrails.yml:1784` | required, but see QUAL-001 | same | false-red on supersede |
| G5a gitleaks (git-history) | `security-gates.yml` | advisory (`continue-on-error: true`), by design pending a clean secrets baseline | same | see QUAL-002 for its noisiest rule |
| G1/G2/G3/G4a-c/G5b/G6/G7/G7b (bandit, eslint-security, semgrep, pip/yarn/npm-audit, bundle-secrets, container-scan, license checks) | `security-gates.yml` | **blocking** (`continue-on-error: false`) | same | confirmed by the `summary` job's own needs list + comment, `:856-882` |
| `summary` (Security gates) | `security-gates.yml:857` | required, but see QUAL-001 | same | false-red on supersede |
| `maestro-android` (Maestro real-device E2E) | `maestro-e2e.yml` | opt-in only (`workflow_dispatch` or PR labeled `run-maestro`) | never auto | B25 (ACTION_ITEMS, open) — see §8 |
| `mobile-test-placement-gate` | `ci-guardrails.yml` (listed in `guardrail-summary`'s `needs`) | blocking (in the required set) | same | not independently re-verified this pass — cited from the summary job's own `needs` list |
| `change-impact-log-gate` | `ci-guardrails.yml` (same) | blocking | same | not independently re-verified this pass |
| `offline-helper-tests` | `ci-guardrails.yml:1770-1781` | blocking | same | runs `scripts/test_validate_dast.py`, `scripts/test_payment_failure_alert.py`, `loadtest/test_target_guard.py` directly via pytest — a real gate, not just a label |

Global backend `--cov-fail-under`: `backend/pytest.ini:15` sets **60%**
whole-suite, run as part of the normal `pytest` invocation (not a separate
CI job) — this is the floor beneath which the *entire* suite (not any one
module) fails outright; distinct from and lower than every per-module floor
in §5.

## 5. Coverage floors vs enforced gates

| Module | CLAUDE.md minimum | Enforced `--cov-fail-under`-equivalent floor | Gap vs. stated minimum |
|---|---|---|---|
| `routes/payments.py` | ≥ 90% | 85.0% (`check_money_path_coverage_floor.py:101-106`) | **-5pts** — see QUAL-005 |
| `services/fare_service.py` | ≥ 90% | 90.0% (`:107-110`) | none |
| `utils/crypto.py` | ≥ 90% | 95.0% (`:111-114`) | none (floor exceeds target) |
| `routes/rides/` (package) | ≥ 80% | 80.0% (`:115-119`) | none |
| `services/dispatch_service.py` | ≥ 80% | 85.0% (`:120-123`) | none (floor exceeds target) |
| `routes/corporate_accounts.py` | ≥ 80% (target) | 75.0% (`check_corporate_coverage_floor.py:65`) | **-5pts vs target**, and stale vs. CLAUDE.md's later ~97%-measured figure |
| `routes/corporate_company.py` | ≥ 80% | 85.0% (`:66`) | none |
| `routes/corporate_company_bookings.py` | ≥ 80% | 80.0% (`:67`) | none |
| `routes/corporate_company_kyb.py` | ≥ 80% | 90.0% (`:68`) | none |
| `routes/corporate_rider.py` | ≥ 80% | 90.0% (`:69`) | none |
| `routes/corporate_signup.py` | ≥ 80% | 80.0% (`:70`) | none |
| `services/corporate_{allowance,membership,policy,wallet}_service.py` | ≥ 80% | 90-95% (`:71-74`) | none |
| `routes/corporate_wallet.py` | ≥ 80% | 80.0% (`:84`) | none |
| `services/corporate_member_offboarding_service.py` | ≥ 80% | 75.0% (`:85`) | **-5pts vs target** |
| `services/corporate_suspension_service.py` | ≥ 80% | 75.0% (`:86`) | **-5pts vs target** |
| `services/corporate_wallet_winddown_service.py` | ≥ 80% | 85.0% (`:87`) | none |
| `routes/admin/` | ≥ 70% | 80.0% (`check_admin_coverage_floor.py:74-77`) | none |
| `utils/` | ≥ 70% | 85.0% (`:78-81`) | none |
| Whole backend suite | not separately stated | 60% (`pytest.ini:15`) | not comparable — different scope (aggregate, not per-module) |

Read this table together with QUAL-005: every "-5pts" row is CI-passable
today at a level CLAUDE.md itself calls insufficient, by design (the
"measured minus 5" buffer convention), not by oversight — flag, don't treat
as an emergency.

## 6. Test pyramid counts (backend, `pytest --collect-only`, this session)

| Marker / bucket | Count | % of 17,391 |
|---|---|---|
| Total collected | 17,391 | 100% |
| `-m unit` | 4,162 | 23.9% |
| `-m integration` | 5 | 0.03% |
| `-m slow` | 554 | 3.2% |
| `-m anyio` (separate axis — async, not tier) | 5,472 | 31.5% |
| No `unit`/`integration`/`slow` marker | 12,670 | 72.8% |

RLS tier (`backend/tests/rls/`, separate `pytest` invocation, self-skips
without `TEST_DATABASE_URL`): 17 test files, not counted in the 17,391 above
(excluded via `-c /dev/null --confcutdir=tests/rls` per CLAUDE.md's own
instructions for that tier). Schemathesis fuzz pilot: 1 file
(`test_schemathesis_fuzz.py`), GET-only, run via `-m slow` on demand — not
part of the default collection either since it's excluded from the base
`--cov` run per `ci-guardrails.yml:191` (`--ignore=tests/test_schemathesis_fuzz.py`).

Frontend/E2E test file counts (file-count only, not case-count — not
independently run this pass):

| App | Location | Files |
|---|---|---|
| rider-app | `__tests__/` | 92 |
| driver-app | `__tests__/` | 104 |
| admin-dashboard | unit/component tests (non-`e2e/`) | 83 |
| admin-dashboard | `e2e/` (Playwright) | 30 |
| shared | `__tests__/`-equivalent | 22 |
| rider-app + driver-app | `.maestro/{rider,driver}/*.yaml` | 12 (5 rider + 7 driver, per B25) |

See §2's QUAL-006 for the interpretation (marker hygiene finding) and §1
Steelman for what's genuinely good about this distribution (the modules
that matter most for money/safety are the ones with real, spot-checked
coverage, even though tier-marker discipline is weak suite-wide).

## 7. Skips / xfails on critical paths

Backend: 7 total `@pytest.mark.skip`/`.skipif`/`.xfail` occurrences across
5 files (grepped `backend/tests/`, excluding `.pyc`):

| File:line | Kind | Reason given | Critical-path? |
|---|---|---|---|
| `test_ai_mcp.py:186,190` | `skipif` (paired, mutually exclusive) | MCP SDK present/absent — intentional environment-conditional coverage split, not a gap | No (AI tooling, not rides/money) |
| `test_ai_mcp_coverage.py:113` | `skipif` | MCP SDK not installed (lockfile not regenerated) | No |
| `test_corporate_b2b_schema.py:92,102` | `skip` (both `@pytest.mark.integration` too) | "requires live Supabase connection — run manually against staging/prod" | Corporate/money-adjacent, but explicitly hived off to a documented manual-run tier, not silently dropped |
| `test_p3_background_location.py:306-319` | `xfail(strict=False)`, class-level | "requires a real iOS/Android device with a background task runner... covered manually via `docs/MOBILE_SMOKE.md` §F step F-4" | Driver-app background location (safety/dispatch-adjacent) — legitimately un-automatable in CI without Detox, and the manual-coverage cross-reference is concrete (not "trust me") |

None of the 7 look like a quarantined flake hiding a real regression — every
one names either an environment precondition (SDK absent) or a genuine
CI-infrastructure limit (no real-device runner) with a named compensating
manual check, matching CLAUDE.md's spirit even though none of these skips
themselves are formally logged anywhere as a tracked exception.

Frontend (`rider-app/__tests__`, `driver-app/__tests__`, `admin-dashboard/__tests__`,
`admin-dashboard/e2e`, `shared/__tests__`): grepped for `.skip(`, `xit(`,
`xdescribe(`, `test.todo(` — **zero hits**. No quarantined JS/TS tests found.

## 8. Mobile & visual testing

- **Is there any mobile E2E that runs?** Not automatically. `.maestro/{rider,driver}/`
  (12 flows: login, ride request/cancel, schedule+cancel, SOS, mid-trip chat,
  go-online, accept-ride, verify-OTP, complete-trip, payout, in-trip chat) is
  the only suite in the repo that drives a real native build. `maestro-e2e.yml`
  is a genuine, non-stub implementation (EAS-builds a real Android APK on the
  `test` profile, uploads to Maestro Cloud) and its prior blocking bug (C76 —
  a `matrix` context used in a job-level `if:`, invalid per GitHub's own
  context-availability rules, producing 3,206 zero-duration "failure" runs on
  every push) is fixed. But it only fires via `workflow_dispatch` or a PR
  labeled `run-maestro`, and needs two secrets (`EXPO_TOKEN`,
  `MAESTRO_CLOUD_API_KEY`) whose actual presence in repo secrets is
  UNKNOWN from this session (cannot read GitHub secrets). This is
  `ACTION_ITEMS.md` B25 (open, `:7196-7235`) — cited, not re-filed. iOS has
  no lane at all (no Apple Developer credentials provisioned in EAS).
- **What `rider-app/e2e/`, `driver-app/e2e/` and `admin-dashboard/e2e/`
  (Playwright) actually cover:** per `.maestro/README.md:3-7`, these only
  exercise the Expo **web export** (react-native-web) with
  backend/WebSocket/Maps/Firebase mocked via `page.route()` — they cannot
  reproduce a native-module-only bug (the motivating historical example is
  #3174, cited in `maestro-e2e.yml`'s own header). See QUAL-004 for these
  jobs' gate status (advisory on PR).
- **Visual regression:** admin-dashboard only. `visual-regression-test`
  (`ci.yml:638-740`) is genuinely blocking (confirmed — no
  `continue-on-error` on that job), covers 6 seeded pages (`login`,
  `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`,
  `dashboard-settings`, `dashboard-rides`), and its one documented source of
  flakiness (`dashboard-monitoring`'s live map-tile fetch) is fixed via a
  `page.route()` stub — matches CLAUDE.md's B38 claim exactly, VERIFIED by
  direct read this pass, not just taken on the doc's word. Re-seeding a
  baseline after an intentional UI change needs `update-visual-baselines.yml`,
  which needs Actions-dispatch access this audit doesn't have — any admin
  page change with a seeded baseline should be flagged for human
  re-capture, not assumed spurious. rider-app and driver-app have **no**
  visual tooling at all (confirmed, no contradicting evidence found this
  pass) — CLAUDE.md's own gate 6 disclosure requirement applies to any
  future change there.
- **Release gates:** `[build]`-in-commit-message gating for EAS builds is
  real and matches CLAUDE.md exactly — `ci.yml:955` (`main` push) and
  `test-env.yml:155` (`staging` push), both gated on
  `contains(github.event.head_commit.message, '[build]')`. **OTA rollout
  control** (`eas-rollout-control.yml`) is a deliberately human-gated
  operator tool (`list`/`ramp-to-100`/`revert`, mutating actions require an
  explicit `group_id`, no auto-group-selection "on purpose" per its own
  header) — built in direct response to a real 2026-09-20→22 incident
  (four stuck rollouts blocking all production OTA, per its own header
  citing `docs/change-log/2026-09-22-ota-production-dispatch-only.md`) —
  this is release-gate infrastructure working as intended, not a gap.
- **App-store policy compliance** (payments, background location, account
  deletion — greenfield §12 item, lane R14/R12): not independently swept
  this pass; `test_p3_background_location.py`'s xfail (§7) shows background
  location permission *request* is covered (source presence + Jest
  coverage-file checks) but the continuity behavior itself is manual-only.
  Not otherwise assessed — out of this pass's budget; flag as **UNKNOWN**,
  not cleared.

## 9. Invariants → property-based tests (greenfield §5, consolidated)

Seeded from `greenfield-extensions.md` §5 and `money-cra.md` §6 (cited, not
copied verbatim); rows are consolidated across lanes with this lane's own
CI/test-process additions marked **(R14)**. "Holds today?" reflects what the
cited lane (or this pass) found, not a fresh re-verification unless marked
VERIFIED-R14.

| Invariant | Holds today? | Existing test | Property test that would prove it |
|---|---|---|---|
| The ledger balances: no money created/lost across charge → refund → payout | Partial (money-cra.md) | balance-parity tests cited there | for random rides × {ok, declined, refunded, disputed}, ledger deltas sum to exactly the Stripe-side deltas |
| Payout per ride ≤ collected; tips never double-paid | False-by-policy (money-cra.md — platform absorbs some gaps deliberately) | `driver_earnings_with_tip` idempotency tests | `driver_paid − collected == Σ absorbed[reason]` exactly, per money-cra.md §6 |
| A rider has at most one active ride | Not independently re-verified this pass | `active_statuses` invariant cited throughout dispatch.md | book-while-active attempt always 409s, for every one of the 5 active statuses |
| A trip can't reach `cancelled` after `in_progress`; no unknown statuses | Verified at the guard level (`test_ride_state_machine.py`, §3) | `TestCancelStateGuardRider` | fuzz `rides.status` transition attempts from every status × every action, assert only the documented edges succeed |
| `is_available ⇒ is_online` | Not independently re-verified this pass (dispatch.md flags a related race, `:197`) | dispatch.md cites a proposed concurrent test, not yet built | concurrent `set_driver_available(True)` + `is_online=False` flip, assert never both `is_available=True, is_online=False` |
| Insurance Period 3 ⇒ linked `in_progress` ride; period rows append-only | Verified (trust-safety-fraud.md, one canonical entrypoint confirmed) | `test_insurance_period_rpc.py` + migration 64 append-only trigger | RLS-tier test asserting the trigger rejects UPDATE/DELETE on `driver_insurance_periods` (compliance.md CC-11 already proposes this) |
| Applied surge ≤ 2.5× on every path; 1.0× on corporate-paid rides | Cap: verified (strategy.md, 21 clamp sites); corporate exemption: tested in `test_corporate_surge_bypass.py` (QUAL-003 withdrawn) | cap-clamp and corporate-bypass tests | none needed |
| A completed trip's fare is never changed silently | Partial (money-cra.md — logged but not audit-rowed) | none for the audit-row half | DB-trigger test: any fare-column write after `status=completed` + `payment_status ∈ COLLECTED` must carry an `audit_logs` row |
| A non-admin actor can never read/mutate another user's ride/wallet/documents | Mostly held; security.md found the enforcement pattern (fetch-then-compare) unlint-ed on 55/102 routes | `test_core_tables_rls.py` (DB layer only, not the API layer) | security.md's proposed static test: no `get_ride(`/`find_one("rides"` in `routes/` without a membership check within N lines |
| **(R14)** A required CI check's conclusion reflects its dependencies' *real* outcome, not an artifact of concurrency-cancellation | **False** (QUAL-001) | none | replay the two-push-in-quick-succession scenario in CI itself (a scheduled or PR-triggered self-test) and assert the summary job's conclusion is neutral, not `failure`, when every dependency's `result` is `cancelled` |
| **(R14)** Every test file citing a CLAUDE.md "must have a test" scenario actually invokes the changed code path, not a stub of it | Spot-checked True for `payout.paid`/`.failed`, `test_ride_state_machine.py`, `test_coverage_rides.py`'s corporate-card tests; also True for the corporate surge exemption (`test_corporate_surge_bypass.py`; QUAL-003 withdrawn) | this table (§3) | a coverage-diff tool that, for a named CLAUDE.md scenario, asserts the cited test file's coverage report shows the specific line(s) implementing that scenario as executed, not just the file as a whole |
| **(R14)** Every new test file declares a pyramid tier marker | **False** — 72.8% unmarked (QUAL-006) | none | static collection-time check per QUAL-006's recommendation |

## 10. Rebuild Delta

## Epic: Quality & Release Engineering
- Verdict per inherited pattern: **MODIFY** — the machinery (floor gates,
  RLS tier, Stripe webhook test density, the `.gitleaksignore`
  false-positive discipline) is genuinely above what a from-scratch
  two-person-team rebuild would likely have by this stage; the gaps found
  this pass (QUAL-001 through -007) are narrow, fixable defects in an
  otherwise sound design, not evidence the approach is wrong.
- Keep (already best-in-class): the dated-provenance coverage-floor scripts
  (`check_{corporate,money_path,admin}_coverage_floor.py`) — a floor that
  states *when and how* it was measured, with an explicit "don't lower it to
  make a PR pass" rule in its own docstring, is a genuinely good pattern
  worth generalizing (see QUAL-006's proposed auto-tier-marker hook, same
  spirit). The RLS allowed+denied pairing convention. The
  `.gitleaksignore` per-incident-justified-entry discipline (once the
  underlying regex is fixed, QUAL-002, the *process* around it is right).
- Uber/Lyft do: run mutation testing and property-based fuzzing on core
  pricing/dispatch logic at much greater scale, and gate merges on a live
  "is main green" dashboard that pages on-call, not just a per-PR check
  (industry pattern, not independently sourced this pass — ASSUMED from
  general SRE practice, flag for a primary source before citing externally).
  Spinr today: has the Schemathesis GET-only pilot (a real start) and no
  "is main green" consumer for the E2E jobs that are nominally blocking on
  `main` (QUAL-004).
- Clean-sheet Spinr would: (1) make "a required check's conclusion is
  accurate under concurrency-cancellation" a first-class, tested property of
  the CI system itself, not something discovered by a human reading a
  confusing red X (QUAL-001); (2) auto-classify every test's pyramid tier at
  collection time instead of relying on per-file opt-in (QUAL-006); (3) wire
  a real "main is green" gate that something (a merge queue, or at minimum
  a paging alert) actually consumes, closing the loop `[17-1]` opened but
  never finished (QUAL-004). Why: each of these turns a currently-manual,
  easy-to-miss signal into a mechanically-enforced one — the same shift
  the coverage-floor scripts already made for coverage regressions.
- How: (1) a two-line `if:` change plus a scratch-PR regression test: (2) a
  `conftest.py` collection hook + a static lint test; (3) either a
  branch-protection change (if access exists) or a scheduled "main health"
  workflow that pages when `main`'s last E2E run was red. Who: whoever owns
  `ci-guardrails.yml`/`security-gates.yml` (no `spinr-*` agent currently
  named for CI/CD ownership per `roles.md` — `spinr-cicd-infra-reviewer` is
  the closest mapped agent). When: QUAL-001/QUAL-002 are Now (small, no
  dependencies); QUAL-004/QUAL-006 are Next (need a human decision on
  branch-protection access and marker-migration blast radius first).
- Incremental path from today: step 1 (QUAL-001, QUAL-002 regex) — ship independently, each is a single-file, single-purpose change
  under CLAUDE.md's batch-size rule. Step 2 (QUAL-006 hook, opt-out not
  opt-in) — land with the hook only warning for one cycle before it blocks,
  matching the repo's own established "advisory-then-promote" pattern
  (G5b's own history, security-gates.yml comments). Step 3 (QUAL-004
  branch-protection or promote-to-blocking) — needs the human answer to
  "what does branch protection actually require today" first (carried
  forward from `00-history.md` §9's human-only question list).
- Cost/effort: QUAL-001 S, QUAL-002 S, QUAL-004 M (blocked on
  human access), QUAL-005 S (tooling-only), QUAL-006 M, QUAL-007 S (doc
  only). Risk: low across the board — every fix here either tightens a gate
  (fails safe) or documents reality more accurately; none touches a
  live-tested production code path. Reversibility: all fully reversible
  (CI config, doc text, one new test). Build (not buy/partner) — all
  in-repo tooling, consistent with the rest of the guardrails file.
- Advantage type: operational (a CI system contributors trust is a
  velocity advantage, not a customer-facing one) — not defensible against a
  competitor, but directly determines how fast the other 19 lanes' findings
  can actually ship without a "was that red real?" tax on every PR.
- "Why not?": QUAL-001 wasn't fixed correctly the first time (CR #5572)
  because `cancelled()`'s real semantics under concurrency-cancellation are
  genuinely non-obvious and under-documented by GitHub itself — this is a
  known class of GitHub Actions footgun, not a carelessness signal. QUAL-004
  exists because `[17-1]`'s staged rollout plan was reasonable but nobody
  came back to finish stage two — the same "advisory gate decays" pattern
  `00-history.md` already names as this repo's single most repeated root
  cause. A simpler fix for QUAL-004 specifically (delete the
  `continue-on-error` entirely, accept short-term PR friction) is available
  today without waiting on branch-protection access — worth trying first if
  the human answer to "can we see branch protection" takes a while.

## 11. Top 5

1. **QUAL-004** — admin-dashboard/rider-app/driver-app Playwright E2E is
   advisory on every PR, and the documented "blocking on main" fallback
   gates nothing that actually deploys (`[17-1]`'s stage-two never landed).
2. **QUAL-001** — the two required CI "summary" gates still false-red on a
   superseded/force-pushed run, on the exact two jobs a prior fix (CR
   #5572) targeted for this exact failure mode — VERIFIED on PR #5759's own
   check runs this session.
3. **QUAL-006** — 72.8% of the backend suite carries no pyramid-tier marker;
   `pytest -m unit`/`pytest -m "not slow"` don't triage most of the suite,
   undercutting CLAUDE.md's stated fast-loop/pre-push promises.
4. **QUAL-002** — the `spinr-driver-export-pii` gitleaks rule spans
   newlines and unrelated file content, producing a third confirmed
   false-positive recurrence (including on this very audit's own report) —
   small, mechanical, one-line fix available.
5. **QUAL-007** — two async test runners coexist (`pytest-asyncio` in auto
   mode and the `anyio` plugin) while CLAUDE.md documents only `anyio`.
   Doc-only fix. (Promoted into the top 5 after QUAL-003 was withdrawn.)

## 12. What was NOT verified

- **No test suite was executed.** All coverage/pass-fail claims are from
  reading test code, `pytest --collect-only` output, and prior lanes' cited
  results — never from a live `pytest` run (per this lane's explicit
  instruction: read-only discovery only, `--no-cov -p no:cacheprovider`).
- **Most Stripe webhook types' tests were confirmed present (grep) but not
  individually re-read for real-vs-stubbed depth** — only `payout.paid`/
  `.failed` was fully read this pass; the other ~13 types' test files are
  taken as a weak positive signal (file exists, names the event string),
  not confirmed real coverage in the CLAUDE.md sense.
- **The 16 of 17 RLS test files not spot-checked** (`test_core_tables_rls.py`
  was the only one fully read) — allowed+denied pairing was confirmed for
  that one file only, extrapolated (INFERRED, not VERIFIED) to the rest by
  naming convention.
- **The 177-file "no `anyio` string found" list (QUAL-007) was not
  individually reviewed** — a crude whole-file grep, not a per-function
  audit; some may carry a class-level marker phrased in a way the grep
  missed, or may genuinely not need the marker at all under
  `asyncio_mode = auto`.
- **Whether `EXPO_TOKEN`/`MAESTRO_CLOUD_API_KEY` actually exist as repo
  secrets** — cannot read GitHub Actions secrets from this session; B25's
  own text already flags this as unconfirmed.
- **Branch-protection rule contents for `main`** — cannot read from this
  session (carried forward from `00-history.md` §9); this is the single
  biggest unknown behind QUAL-004's actual severity (if E2E/visual checks
  are already required and enforced by branch protection regardless of
  `continue-on-error`'s YAML-level effect on the check's own conclusion,
  the practical risk is smaller than stated — but `continue-on-error: true`
  on a job means the job reports `success` to branch protection even when
  its own tests failed, which is why this finding stands regardless).
- **App-store policy compliance sweep** (greenfield §12) — not done this
  pass, explicitly marked UNKNOWN in §8, not cleared.
- **The 12 real Maestro flow files' own content** (whether they'd actually
  pass if dispatched) — not read; only their existence, wiring, and CI
  trigger conditions were verified.
- **Whether `mobile-test-placement-gate` and `change-impact-log-gate`
  (listed in `guardrail-summary`'s `needs`) are themselves sound** — cited
  from the summary job's dependency list only, their own job bodies were
  not read this pass (out of scope creep into R13's lane; flag if another
  lane hasn't covered them).
- **Whole-suite wall-clock timing** — did not measure whether `-m unit`
  actually meets the "< 100ms/test" target CLAUDE.md states; only counted
  how many tests carry the marker.

## 13. Human-only questions

- Does `main`'s branch-protection rule currently require `e2e-test`/
  rider-app/driver-app E2E, `visual-regression-test`, `guardrail-summary`,
  or `security-gates.yml`'s `summary` job? (Determines QUAL-004's and
  QUAL-001's real practical severity — carried forward from
  `00-history.md` §9, not newly discovered by this lane, but this lane's
  findings are the concrete reason the answer matters.)
- Are `EXPO_TOKEN` and `MAESTRO_CLOUD_API_KEY` present in repo/org secrets
  today? (B25 — determines whether Maestro mobile E2E can run at all once
  dispatched.)
- Is there appetite to flip `e2e-test`/rider/driver E2E from
  `continue-on-error: true` to blocking on PRs now, given `[17-1]`'s
  "while the suite stabilises" framing is 2+ months old
  (2026-07-29 → 2026-09-24) with no re-evaluation on record?
- Who owns `ci-guardrails.yml`/`security-gates.yml` day-to-day? (No
  `spinr-*` agent is named for CI/CD ownership in `roles.md`; the closest
  mapped agent, `spinr-cicd-infra-reviewer`, is a review agent, not an
  owner — relevant to who should action QUAL-001/QUAL-002.)

## 14. Escalations

- **QUAL-004** should go to whoever owns release process, not just
  engineering — it's a "does a broken checkout flow ever get automatically
  caught before a real rider hits it" question, squarely in CLAUDE.md's
  "product is in live app testing" framing, and the fix depends on a
  branch-protection access decision this session cannot make.
- **QUAL-001** is worth a fast-tracked fix independent of this audit's
  broader findings — it's small, mechanical, and actively erodes trust in
  required checks on every force-push, which compounds with every other
  lane's recommendation to *add* more required gates (a team that's
  learned to ignore red X's from summary jobs will ignore the new ones too).
