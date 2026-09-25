# Test coverage matrix — backend domains and app surfaces

**Lane:** Tier B matrix (2 of 2) · **Wave:** post-W5 · **Date:** 2026-09-25 · **Mode:** report-only. No test was run, no code/config/data changed.

## §0 Method

- **Commands run this session** (collection only, never `pytest` without `--collect-only`):
  `cd backend && python3 -m pytest --collect-only -q --no-cov -p no:cacheprovider` (whole suite,
  and repeated with `-m unit` / `-m integration` / `-m slow` / `-m anyio`); domain buckets were
  collected by passing an explicit file list built from `ls tests | grep -iE '<pattern>' | grep
  '\.py$'` into the same `--collect-only` invocation (never a bare path glob — an early run without
  the `.py` filter picked up `tests/perf_rides_*.json` baseline files and produced a false
  `no tests collected`, fixed by filtering to `.py`).
- **File counts** for `rider-app`, `driver-app`, `shared`: `find <app> -iname "__tests__" -type d
  -not -path "*/node_modules/*" -exec find {} -name "*.test.*" \;`, counted, not run.
  `admin-dashboard` e2e/unit counts: `find admin-dashboard/e2e -name "*.spec.ts"` and `find
  admin-dashboard/src -iname "*.test.*"`. `.maestro/` flow count: file listing only.
  **These are file counts, not case counts** — Jest/Playwright case totals were not collected this
  session (would need `--listTests`/`--list` per app, out of this pass's time budget); this matches
  `02-findings/quality.md` §6's own disclosed limit.
- **Domain buckets are keyword-matched filenames, not a semantic classification** — the same
  caveat `01-inventory/epics.md` §0 states for its own classifier. A file can match more than one
  bucket (e.g. `test_ride_dispatch_*.py` matches both `ride` and `dispatch`), so bucket test counts
  are **not additive to the 17,436 total** and should be read as "at least this many tests touch
  this domain," not a partition.
- **Real-vs-stubbed spot checks**: reused `02-findings/quality.md` §3's table where it already
  covers a domain (rides, dispatch, fares/payments, corporate, safety, RLS — cited, not
  re-verified independently by this pass), and this pass separately opened one file each for
  **wallet**, **notifications**, and **AI** (the three CLAUDE.md-named domains quality.md's table
  didn't sample) to fill the gap. Labels follow quality.md's own legend: **Real** = invokes the
  actual changed function and asserts on real output/side-effects, with only the DB/external SDK
  mocked; **Stubbed** = the component under test itself is mocked away; **Present-not-reverified**
  = file exists, not opened this pass.
- **Coverage-minimum vs enforced-floor numbers** are read from `02-findings/quality.md` §5 (its own
  direct read of `backend/scripts/check_{corporate,money_path,admin}_coverage_floor.py`), cited
  here rather than re-derived, since those scripts were not re-opened this session.
- Evidence labels: VERIFIED (this session's own command or read) / cited-VERIFIED (another lane's
  direct read, not re-run this pass) / INFERRED / ASSUMED / UNKNOWN.

## §1 Backend suite headline (VERIFIED, this session)

| Bucket | Count |
|---|---|
| Total collected (`pytest --collect-only -q`, whole `backend/tests/`) | **17,436** |
| `-m unit` | 4,162 (23.9%) |
| `-m integration` | 5 (0.03%) |
| `-m slow` | 554 (3.2%) |
| `-m anyio` (separate axis — async, not tier) | 5,498 (31.5%) |
| No `unit`/`integration`/`slow` marker | ≈13,271 (76.1% of a marker partition; see QUAL-006 below) |
| RLS tier (`backend/tests/rls/`, separate invocation, self-skips without `TEST_DATABASE_URL`) | 18 files, not counted in 17,436 |
| Schemathesis fuzz pilot (`test_schemathesis_fuzz.py`, GET-only, `-m slow`) | 1 file, excluded from the base `--cov` run |

The 17,436 figure is ~44-45 tests above `02-findings/quality.md`'s own collection (17,391 on
2026-09-24) and `07-reverification.md`'s re-collection (17,392, same date) — read as ordinary
churn between sessions, not a discrepancy to chase. The marker-hygiene finding those two files
already filed (QUAL-006, re-verified independently and CONFIRMED in `07-reverification.md`) is
cited, not re-derived: ~73-76% of the suite carries none of the three documented tier markers, so
`pytest -m unit` (CLAUDE.md's "fast local loop") and `pytest -m "not slow"` (CLAUDE.md's pre-push
gate) both run a much smaller and less representative slice of the suite than their names imply.

## §2 Backend domain coverage (VERIFIED, this session's own collection)

| Domain | Keyword pattern used | Test files (`.py`) | Tests collected | Real-vs-stubbed spot check | CLAUDE.md minimum | Enforced floor (cited, quality.md §5) |
|---|---|---|---|---|---|---|
| Rides (general, incl. state machine) | `ride` | 80 | 1,638 | **Real** — `test_ride_state_machine.py::TestRequireRideInState`/`TestCancelStateGuardRider`/`TestCompleteRideAtomicGuard` invoke the real guard and CAS path (cited, quality.md §3) | `routes/rides.py` ≥80% | `routes/rides/` package 80.0% (matches) |
| Dispatch / offer / matching | `dispatch\|offer\|matching` | 39 | 517 | **Present-not-reverified** for the accept/timeout/scheduled-dispatch transitions — quality.md §3 flags these as scattered across 5+ files rather than centralized in `test_ride_state_machine.py`, contrary to CLAUDE.md's own instruction | `services/dispatch_service.py` ≥80% | `dispatch_service.py` 85.0% (exceeds) |
| Payments / fare / Stripe webhooks | `fare\|payment\|stripe\|webhook` | 66 | 1,315 | **Real** — `test_webhooks_main.py::TestStripeWebhookPayoutSettlement` calls the actual `stripe_webhook()` handler and asserts the real DB side effect (cited, quality.md §1); `test_corporate_surge_bypass.py` is Real at three layers (QUAL-003, WITHDRAWN — see §3) | `routes/payments.py`, `services/fare_service.py` ≥90% | `payments.py` **85.0%** (−5pts vs target, QUAL-005); `fare_service.py` 90.0% (matches) |
| Wallet | `wallet` | 18 | 261 | **Real** (this pass, `test_admin_wallet_endpoints.py:46-60`) — patches only `db_supabase.*` functions via `AsyncMock`, calls the real handler, asserts on the mocked audit-insert/get-rows call args, not a stub of the handler itself | not separately named (falls under `routes/payments.py`/corporate wallet floors) | `routes/corporate_wallet.py` 80.0% (matches) |
| Corporate | `corporate` | 65 | 882 | **Real** — `test_corporate_rpc_ride_idempotency.py::TestMigrationRideIdempotencyContract` statically parses the live migration body for the allowance-cap guard (cited, `07-reverification.md` CORP-002 note); `test_money_rpc_races.py` (path: `backend/tests/rls/money/`, not the top-level `tests/` this bucket counts) exercises real RPC races | `routes/corporate_*.py`, `services/corporate_*.py` target ≥80% | 75-95% per-module (2 modules at **−5pts vs target**: `corporate_accounts.py` 75%, `corporate_member_offboarding_service.py`/`corporate_suspension_service.py` 75%; QUAL-005) |
| Auth / OTP / JWT / tokens | `auth\|otp\|jwt\|token` | 42 | 628 | Present-not-reverified this pass; `test_sos_expired_token.py` cited Real by `trust-safety-fraud.md` (secondhand, per quality.md §3) | not separately named in the coverage table (auth lives under `routes/`, no dedicated CLAUDE.md floor) | none named |
| Admin | `admin` | 113 | 2,115 | **Real** (this pass, `test_admin_wallet_endpoints.py`, same file as the wallet row — admin and wallet buckets overlap by keyword) | `routes/admin/` ≥70% | `routes/admin/` 80.0% (exceeds) |
| Safety (SOS / insurance) | `sos\|safety\|insurance` | 29 | 396 | **Real** — `test_insurance_period_rpc.py` cited by `compliance.md` CC-11 (quality.md §3, present-not-reverified there); `test_sos_expired_token.py` Real per above | not separately named | none named |
| Notifications | `notification\|push\|fcm` | 23 | 300 | **Real** (this pass, `test_driver_needs_review_notification.py:22-41`) — `monkeypatch.setattr("features.send_push_notification", sent)`, calls the real notify path, asserts the exact push title/body/priority/target_app on the mock's `await_args` | not separately named | none named |
| AI | `^test_ai` | 32 | 792 | **Present-not-reverified this pass** (opened `test_ai_chat_route.py`'s header only — its own docstring says "e2e-ish via TestClient, orchestrator patched," consistent with Real, not independently confirmed by reading test bodies) | not separately named | none named |

Utils: CLAUDE.md's `utils/crypto.py` ≥90% floor is enforced at 95.0% (exceeds; per quality.md §5,
not independently re-verified this session). CLAUDE.md's general `utils/` ≥70% floor is enforced
at 85.0% (exceeds).

## §3 QUAL-001..007 — what each says about test coverage specifically

Cited from `02-findings/quality.md` §2 and cross-checked against `07-reverification.md` §1 where
that file re-checked the card. Not re-derived independently except where noted.

| ID | One-line | Coverage-relevant takeaway | Re-verification result |
|---|---|---|---|
| QUAL-001 | CI's two required "summary" gates false-red on a superseded/force-pushed run | Not itself a coverage gap — a merge-gate trust gap. Included here because a team trained to see false red on required checks is more likely to miss a real coverage-gate failure in the same channel. | Not sampled by `07-reverification.md` |
| QUAL-002 | The custom driver-export-PII secret-scan rule spans newlines and over-matches | Not a test-coverage finding; a CI-hygiene finding included in the same lane. Not re-derived here — see `02-findings/quality.md` for the mechanism; this file does not quote the rule's own keyword strings, per this audit's PII-string handling rule. | Not sampled |
| **QUAL-003** | **WITHDRAWN.** Originally claimed the corporate surge-exemption fare branch had no test. | **`backend/tests/test_corporate_surge_bypass.py` exists and is Real at three layers**: the `_is_corporate_paid` truth table (`:38-92`), the estimate endpoint dropping surge to 1.0 with a personal-ride negative control (`:193-240`), and the booking endpoint persisting 1.0 with a charge-site clamp case (`:370-443`). The original absence claim searched `"forcing surge"`, `"corporate_bypass"`, and a fixed list of fare test files, and missed this file by name — a too-narrow-grep failure, the same class `07-reverification.md` found in 3 of its own 16-card sample. | Orchestrator direct read, 2026-09-24; counted as a verifier error in the audit's own Step-6 error rate (`07-reverification.md` §3: "combined with the orchestrator's 10 prior hand checks, 9 correct, 1 wrong: QUAL-003"). **Do not cite this as an open gap.** |
| QUAL-004 | Admin/rider/driver Playwright E2E is `continue-on-error: true` on every PR; "blocking on main" gates no actual deploy | Coverage exists (the specs are real, non-stubbed) but cannot fail a PR — a release-gate finding more than a test-writing gap. `visual-regression-test` (admin-dashboard, 6 seeded pages) is the one genuinely blocking E2E-adjacent job. | Not sampled by name; `RR-02` in `matrices/risk-register.md` folds it in as HIGH, S×B×L=80 |
| QUAL-005 | Coverage floors sit below CLAUDE.md's literal stated minimums by design ("measured − 5, rounded down") | Directly relevant to §2 above — every "−5pts" row in this file's floor column is CI-passable today at a level CLAUDE.md calls insufficient, deliberately (not an oversight). | Not independently re-sampled this pass beyond citing the same three floor scripts |
| QUAL-006 | 72.8-76.1% of the backend suite carries no tier marker; the `integration` tier is functionally unused | Directly reproduced by this session's own §1 numbers (17,436/4,162/5/554). | `07-reverification.md`: **CONFIRMED**, re-collected 17,392/4,162/5/554/12,671(72.9%) on 2026-09-24, within 1 test of the original card |
| QUAL-007 | Two async test runners coexist (`pytest-asyncio` auto mode + `anyio` plugin); CLAUDE.md documents only one | Latent-fragility finding, not a coverage gap per se — flagged because a test author following CLAUDE.md's literal instruction may misunderstand which runner's fixture semantics apply. | Not sampled by `07-reverification.md`; label itself is VERIFIED (config) / INFERRED (risk) per the original card |

## §4 Frontend / mobile / E2E file counts (VERIFIED, this session — file counts only, not case counts)

| Surface | Location | Test files | Note |
|---|---|---|---|
| rider-app | every `__tests__/` dir (`app/`-adjacent `components/`, `hooks/`, `services/`, `store/`, `lib/`, `utils/`, plus top-level `__tests__/`), excluding `node_modules` | 141 | Broader scope than `quality.md`'s "92" (top-level `__tests__/` only) — this count sums every nested `__tests__` directory; both are correct under their own stated scope |
| driver-app | same pattern | 162 | Includes `lib/androidAuto/__tests__` |
| admin-dashboard | non-`e2e/` unit/component tests (`src/**/*.test.*`) | 83 | Matches quality.md's figure |
| admin-dashboard | `e2e/` (Playwright, incl. `visual-regression.spec.ts`) | 29 | quality.md reported 30 the prior day; 1-file drift, ordinary churn |
| shared | every `__tests__/` dir, excluding `node_modules` | 22 | Matches quality.md's figure |
| rider-app + driver-app | `.maestro/{rider,driver}/*.yaml` | 12 (5 rider + 7 driver) | Confirmed by direct listing; matches `ACTION_ITEMS.md` B25's count cited in `quality.md` §8 |

`.maestro/*.yaml` are declarative E2E flow specs run against a real EAS Android build via
`maestro-e2e.yml`, not unit tests — counted separately per CLAUDE.md's testing-tier taxonomy.
Case-level (per-`it`/per-`test`) counts for Jest/Playwright suites were not collected this
session — flagged as a gap in §6.

## §5 CLAUDE.md coverage minimums vs what can actually be shown (VERIFIED, cited from quality.md §5, cross-checked against this session's own domain counts in §2)

| CLAUDE.md-named module/domain | Stated minimum | What is actually enforced in CI | Gap |
|---|---|---|---|
| `routes/payments.py` | ≥90% | 85.0% blocking floor | **Open, by design** — QUAL-005 |
| `services/fare_service.py` | ≥90% | 90.0% blocking floor | none |
| `utils/crypto.py` | ≥90% | 95.0% blocking floor | none (exceeds) |
| `routes/rides/` (package) | ≥80% | 80.0% blocking floor | none |
| `services/dispatch_service.py` | ≥80% | 85.0% blocking floor | none (exceeds) |
| `routes/corporate_*.py`, `services/corporate_*.py` | target ≥80% | 75-95% per-module blocking floors | **2 modules open** (`corporate_accounts.py`, and 2 of 4 offboarding/suspension services at 75%) |
| `routes/admin/` | ≥70% | 80.0% blocking floor | none (exceeds) |
| `utils/` | ≥70% | 85.0% blocking floor | none (exceeds) |
| Whole backend suite | not stated | 60% `--cov-fail-under` (`pytest.ini:15`, cited) | not comparable — aggregate, not per-module |
| rider-app / driver-app / shared / admin-dashboard | no CLAUDE.md-stated percentage minimum for any frontend surface | none found (no `--cov-fail-under`-equivalent gate located for any JS/TS surface this session) | **No numeric floor exists to be under or over** — this is a structural gap in CLAUDE.md's own coverage table, which only names backend modules |

## §6 What could not be checked this pass

- **Pass/fail state of any test.** Only `--collect-only` was run, per the task's explicit
  instruction; whether the 17,436 collected backend tests currently pass is unknown.
- **Frontend/mobile case-level counts** (Jest `--listTests`/`--json`, Playwright `--list`) — only
  file counts were gathered (§4).
- **AI-domain real-vs-stubbed depth beyond one file's docstring** (§2) — `test_ai_chat_route.py`'s
  body was not read line-by-line this pass.
- **Production coverage percentages themselves** — this file reports the *floor* values the CI
  scripts enforce (cited from quality.md's direct read of the scripts), not a fresh coverage run;
  actual current percentages for any module were not measured this session.
- **Whether the 5,498 `-m anyio` tests correctly pair with `pytest-asyncio`'s `asyncio_mode=auto`
  tests without divergence** (QUAL-007's own stated residual — a 177-file sample was never
  completed by the original lane either).
