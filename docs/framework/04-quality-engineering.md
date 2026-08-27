# Pillar 4 — Quality Engineering

> How Spinr knows a change works: test tiers, per-domain coverage floors
> with a ratchet, a fleet of specialized reviewer agents, and an explicit
> ledger of what is *not* covered. The rule underneath all of it: coverage
> claims must be measured, and gaps must be stated, never implied away.

## Verified baseline (measured 2026-08-27, this repo, clean tree)

| Surface | Runner | Result |
|---|---|---|
| backend | `pytest` (775 test files) | **12,905 passed / 0 failed** (8 skipped, 1 xfailed), 93.38% coverage, 15 min |
| rider-app | `jest` | **1,741 passed / 0 failed** (123 suites) |
| driver-app | `jest` | **1,297 passed / 0 failed** (116 suites) |
| admin-dashboard | `vitest` | **367 passed / 0 failed** (37 files) |

A fully green four-surface baseline is the framework's precondition: every
future claim of "tests pass" is relative to this, and a red baseline would
have been reported red — the framework never launders a failing suite into
a passing summary.

## Test tiers (backend)

| Tier | Scope | Budget |
|---|---|---|
| Unit (`-m unit`) | single function, all deps mocked | < 100 ms/test |
| Integration | real Supabase, throwaway schema | < 2 s/test |
| E2E (`test_e2e_*.py`) | full searching → completed lifecycle, mock payments | — |
| Performance (`perf_baseline.py`) | critical paths vs `perf_*_before.json` baselines | regression = failure |

Mechanics that keep tests honest:
- `mock_supabase_client` fixture; factories in `tests/_factories.py`.
- Patch the `supabase` binding **in the module that defines the function
  under test** (`repositories._base.supabase` for generic CRUD, the domain
  repo for domain functions) — `db_supabase` only re-exports, so patching it
  is a silent no-op that produces fake green.
- `filterwarnings = error` on un-awaited-coroutine leaks: an async test that
  forgot to await fails instead of passing vacuously.
- A fully-stubbed dependency gives **zero real coverage** — the
  `spinr-test-coverage-reviewer` agent flags tests that only exercise their
  own mocks.

## Coverage: floors + ratchet, per domain

Backend gate: `--cov-fail-under=60`, ratcheted 6 → 40 → 50 → 60 with 65/70
next and 80 the ceiling (`pytest.ini`; plan in
`docs/testing/coverage-ratchet-plan.md`, tracked as B37). Measured
2026-08-27: **93.38% aggregate** — the gate trails reality by 33 points,
so the next ratchet steps are essentially free to take. On top of the
global gate, per-domain floors from `CLAUDE.md`:

| Domain | Floor |
|---|---|
| payments, fare service, crypto | ≥ 90% |
| rides, dispatch | ≥ 80% |
| corporate routes/services | ≥ 80% (measured ~92% aggregate 2026-08-02) |
| admin routes, utilities | ≥ 70% |

Frontend thresholds are enforced in each runner config and ratchet the same
way: rider-app lines 73 / functions 69 / branches 63; driver-app lines 65 /
statements 63 / functions 60; admin-dashboard is the honest laggard
(thresholds ~10–19%, scoped to `src/lib|store|components|app`) — its floor
is set at measured reality and ratchets upward rather than pretending.

## What must always have a test

- Every new ride state transition (`test_ride_state_machine.py` case).
- Every fare-calculation branch: tiers, surge, corporate, promo.
- Every auth/RLS policy — the allowed **and** the denied path.
- Every Stripe webhook type before it can reach production.
- Every verified reviewer/audit finding gets a regression test with its fix.

## The reviewer-agent fleet is a quality layer, not a formality

22 specialized `spinr-*` agents review by domain (security, money, surge,
dispatch, insurance periods, migrations, corporate billing/reporting,
fraud, safety/SOS, realtime/loops, performance SLAs, observability,
accessibility, design consistency, RBAC, AI guardrails, CI/infra,
edge cases, test coverage, regulatory, legal readiness). Routing: `/review`
selects by diff; `/full-audit` runs the fleet; money/auth/migration/
dispatch/safety PRs get a manual auditor pass while automated PR review is
down (C7/C9). The `audit-framework/` dimension checklists make fleet audits
repeatable across AI assistants, and their findings land as dated reports
in `docs/audit/` with ranked blockers.

## E2E and the stated gaps (the anti-overclaim ledger)

Playwright E2E exists on all three web-capable surfaces and Maestro exists
for real-device mobile — but the framework requires naming what is inert:

- **Visual regression: zero active coverage on all surfaces.** rider-app and
  driver-app have none; admin-dashboard's CI job self-skips with no
  committed baselines (B38). UI changes say "reasoned about, not
  screenshotted" until baselines are seeded.
- **Maestro never fires** (missing secrets, opt-in trigger, no iOS lane —
  B25).
- **`test-env.yml` cannot fail** — `|| true` suppressors mean its green is
  decorative and the error-audit chain is blind to it (CR-2026-008).
- **No schema-validation library on frontend forms** — money/compliance
  forms are validated ad hoc (B39), so form-level tests overstate safety.

These four lines are the difference between this framework and a checkbox
process: the gaps are tracked (each has an ACTION_ITEMS ID), owned, and
repeated in every relevant Change Impact Log until closed.

## Verification vocabulary

Change Impact Logs and PR descriptions use precise claims:
- "unit tests pass" names the command and count;
- "production build run" means `npm run build` / `expo export` — a dev
  server or `tsc --noEmit` is not equivalent and must not be described as
  a build;
- "not verified" lists real boundaries (e.g. "mocked Supabase only, not
  staging") — silence never implies coverage.
