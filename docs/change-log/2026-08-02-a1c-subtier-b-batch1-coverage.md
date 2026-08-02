# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | backend / admin (health check, driver tax docs, subscription billing) |
| PR / commit link | (this branch: `claude/a1c-subtier-b-batch1`) |
| Related issue or gap ID | ACTION_ITEMS.md A1c Sub-tier B, batch 1 |

## 1. Issue / gap identified

Three files sat at documented near-zero coverage in ACTION_ITEMS.md's A1c
Sub-tier B list:

- `backend/routes/main.py` — **0%** (52 stmts), flagged with "worth a quick
  look at what this even is before writing tests for it."
- `backend/utils/t4a_pdf.py` — **4.40%** (91 stmts). CRA T4A tax-slip PDF
  generation for driver earnings.
- `backend/utils/subscription_invoice_pdf.py` — **7.97%** (138 stmts).
  Spinr Pass subscription invoice PDF generation.

## 2. Root cause

**`routes/main.py`**: this module defines its own `api_router` with `GET /`
and `GET /health`, but it is **never mounted**. `server.py` implements its
own independent `/health` directly on `app` (server.py:204) and has no `/`
route at all. Confirmed:

```
grep -n "routes\.main\|main_router\|import main" server.py   -> no hits
grep -n '@app.get("/")' server.py                             -> no hits
grep -n '"/health"' server.py routes/*.py
    server.py:204:@app.get("/health")
    routes/main.py:23:@api_router.get("/health")
```

`git log --oneline -- routes/main.py` shows no commit that ever wired it
in. **This module is dead code** — an unreferenced, unmounted duplicate of
the real health check, not a stub-in-progress or a route reachable through
some other mount path. It was not deleted (out of scope for a coverage
pass — a repo-owner call), but per the task's explicit instruction to flag
this loudly rather than force tests onto dead code, this is called out
here and in ACTION_ITEMS.md. The two functions were still tested directly
(bypassing the app, since there's no route to hit through a TestClient)
because they are genuinely importable/exercisable code, even if
unreachable in production today.

**`utils/t4a_pdf.py`** and **`utils/subscription_invoice_pdf.py`**: both
are real, live-called PDF generators, but every existing test that
exercises their callers mocks the PDF generation function out entirely:

- `tests/test_t4a_email.py`: `patch("backend.routes.drivers._deps.generate_t4a_pdf", return_value=b"%PDF-1.4 fake")`
- `tests/test_subscription_invoice.py`: docstring literally says "rendering
  (fpdf) lives in subscription_invoice_pdf.py and is mocked here so the
  actual PDF rendering is not exercised"

So the actual fpdf2 rendering code — the money-formatting, tax-line-item
branching, and dual-import fallbacks — had genuinely never run in CI.

## 3. Fix / remediation

Test-only change. Three new test files, no application code modified.

- **`backend/tests/test_routes_main_coverage.py`** (13 tests) — calls
  `root()` and `health_check()` directly. Covers: the happy path (db ok,
  loops ok, with/without a `request` object, with/without
  `background_tasks`); the DB-ping-fails branch (both the natural
  "relative import also fails" path and, via a scoped
  `builtins.__import__` patch, the DB-ping-succeeds-via-fallback and
  DB-error-with-`.details`-attribute paths); the loop-monitor-unhealthy
  branch; and all three loop-monitor import-failure permutations (bare
  ImportError → relative succeeds, bare ImportError → relative also fails,
  bare import succeeds but the call itself raises a non-ImportError).
  **Result: 52/52 stmts, 100%.**
- **`backend/tests/test_t4a_pdf_coverage.py`** (12 tests) — calls
  `generate_t4a_pdf()` directly with summary dicts shaped like the real
  caller (`routes/drivers/tax_exports.py::get_t4a_summary`'s return
  value). Covers: GST-registered-with-BN / GST-registered-without-BN /
  not-registered; zero-trips/zero-earnings; every `.get(...) or default`
  fallback via a near-empty summary; `generated_at` truncation; the
  `_fmt_money` helper's Decimal-coercion happy path and its
  except→`"0.00"` fallback (both as a direct unit test and end-to-end
  inside a full PDF render with a genuinely un-Decimal-able
  `net_earnings`); and the module's `from . import report_branding` /
  `from utils import report_branding` dual-import fallback. **Result:
  91/91 stmts, 100%.**
- **`backend/tests/test_subscription_invoice_pdf_coverage.py`** (11
  tests) — calls `generate_subscription_invoice_pdf()` directly with
  Decimal fixtures shaped like the real callers
  (`routes/drivers/subscriptions.py`, `utils/subscription_invoice.py`).
  Covers: SK (GST+PST), an HST-only province, a hypothetical
  all-three-taxes case, zero-tax, zero-subtotal (the `_pct` divide-by-zero
  guard), `subscription_cycle` vs one-time billing-reason label,
  with/without a Stripe receipt URL, missing-driver-name fallback, and the
  `_d`/`_q`/`_fmt` money helpers directly. **Result: 137/138 stmts, 99%.**

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to new test files.** No application code in any
  of the three target files, or anywhere else, was modified.
  - `routes/main.py`'s `root`/`health_check` — **no callers anywhere**
    (dead code, confirmed above). Zero production blast radius.
  - `utils.t4a_pdf.generate_t4a_pdf` — called from exactly two places, both
    in `routes/drivers/tax_exports.py`: `download_t4a_pdf` (driver-facing
    PDF download endpoint) and `_email_t4a_document` (email attachment).
    Neither modified.
  - `utils.subscription_invoice_pdf.generate_subscription_invoice_pdf` —
    called from `routes/drivers/subscriptions.py`'s
    `_send_subscription_invoice_email` (already covered by this session's
    earlier `test_subscriptions_coverage.py` pass, which mocks this exact
    function) and from `utils/subscription_invoice.py`'s
    `build_subscription_invoice_pdf` (used by `routes/admin/subscriptions.py`'s
    `admin_resend_subscription_invoice`, the audit-logged admin resend
    endpoint). Neither modified.
- **Money-adjacent, but test-only.** Both PDF generators format real
  dollar amounts (T4A net earnings; Spinr Pass subtotal/GST/PST/HST/total)
  for CRA-facing and rider/driver-facing documents. No Decimal/money
  arithmetic in the source files was touched; every new test fixture uses
  `Decimal`, never float, per CLAUDE.md convention.
- **Test-harness finding, not a production risk**: while writing
  `test_routes_main_coverage.py`, discovered that this repo's
  `tests/conftest.py` deliberately aliases every `"backend.<bare>"` import
  to the *same* module object as the bare `"<bare>"` one (see
  `_BareModuleAliasFinder`/`_MIRRORED_BARE_ROOTS`, which includes
  `"routes"`), so a module's `__name__`/`__package__` stays pinned to
  whichever spelling was imported first for the rest of the test session.
  This means `routes/main.py`'s two internal relative-import fallbacks
  (`from .. import db_supabase`, `from ..utils.loop_monitor import
  get_loop_status`) can never naturally succeed inside this test harness
  once the bare module is canonical — attempting `from backend.routes
  import main` yields the literal same object, same failure. This is a
  test-infrastructure property, not a bug in `routes/main.py` or in
  `conftest.py` (the aliasing exists on purpose, to let patches applied to
  one spelling be visible through the other); it only mattered here because
  reaching 100% coverage on the fallback *success* branches required
  working around it with a scoped `builtins.__import__` patch (documented
  inline in the test file). No other Sub-tier A/B file's tests are known
  to depend on that particular fallback succeeding, so this is not flagged
  as blocking anything else — noted here for the next session that hits
  the same wall.
- **Real cross-test-pollution bug found and fixed in this session's own
  new test, before merge** (not a finding about production code): the
  first draft of `test_healthy_with_no_request` asserted
  `result["loops"] == {}`, which passed when the file was run alone but
  failed when run as part of the full suite — `utils/loop_monitor.py`'s
  `_heartbeats` dict is process-global, and other tests in the suite
  (e.g. `test_surge_engine.py`) exercise real background-loop code that
  calls `record_heartbeat()` for real loop names, leaking into any test
  that calls `get_loop_status(None)` afterwards in the same process. Fixed
  by pinning `utils.loop_monitor._heartbeats` to an explicit empty dict
  for the duration of the three tests that call the real
  `get_loop_status()`, and by renaming a fixture's fake loop name to
  something (`"not_a_real_loop_name (test)"`) that can never collide with
  a real `LOOP_THRESHOLDS` entry. This is scoped to the new test file
  only — `utils/loop_monitor.py` itself was not touched, and no other
  existing test in the repo was found to make the same unscoped assertion
  (grepped for `get_loop_status` call sites in `tests/`).

## 5. User-experience effect

None — test-only change. No rider/driver/corporate-admin/internal-admin
facing behavior changes. `routes/main.py` being dead code means there is
no UX surface to affect either way.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_routes_main_coverage.py` | New file — 13 tests | Close coverage gap on `routes/main.py` (0% → 100%) |
| `backend/tests/test_t4a_pdf_coverage.py` | New file — 12 tests | Close coverage gap on `utils/t4a_pdf.py` (4.40% → 100%) |
| `backend/tests/test_subscription_invoice_pdf_coverage.py` | New file — 11 tests | Close coverage gap on `utils/subscription_invoice_pdf.py` (7.97% → 99%) |
| `docs/change-log/2026-08-02-a1c-subtier-b-batch1-coverage.md` | New file (this log) | Required per CLAUDE.md for anything touching a live-tested surface |
| `ACTION_ITEMS.md` | Updated A1c Sub-tier B's three-file bullet | Track progress per the existing series format |

## 7. Before / after

Not applicable — purely additive test files; no existing behavior-changing
diff in any of the three target files.

## 8. Rollback plan

`git revert` — pure test/doc addition, no live-data footprint, no
application code touched, no migration.

## 9. Verification performed

- [x] Each new test file run alone (`--no-cov`): all pass (13 / 12 / 11 —
  36 total).
- [x] Run together with each target's pre-existing related test files
  (`test_routes_main_coverage.py` +
  `test_t4a_pdf_coverage.py` + `test_subscription_invoice_pdf_coverage.py`
  + `test_t4a_email.py` + `test_subscription_invoice.py` +
  `test_admin_subscription_invoice.py`), with coverage:
  `pytest tests/test_routes_main_coverage.py tests/test_t4a_pdf_coverage.py
  tests/test_subscription_invoice_pdf_coverage.py tests/test_t4a_email.py
  tests/test_subscription_invoice.py tests/test_admin_subscription_invoice.py
  -q --cov=routes.main --cov=utils.t4a_pdf --cov=utils.subscription_invoice_pdf
  --cov-report=term-missing --no-cov-on-fail` — **54 passed**, no
  collisions.
- [x] Coverage measured (same command as above):
  - `routes/main.py`: **0% → 100%** (52/52 stmts)
  - `utils/t4a_pdf.py`: **4.40% → 100%** (91/91 stmts)
  - `utils/subscription_invoice_pdf.py`: **7.97% → 99%** (137/138 stmts —
    the one remaining line is a dead `bold=True` branch in a private
    closure never invoked by any caller in the file, see section 4/finding
    below)
- [x] Full backend suite, clean directly-comparable pair at this branch's
  final commit (`pytest tests/ -q --no-cov`, run twice at the identical
  commit — once with this batch's three new test files temporarily moved
  out, once restored — rather than trusting two runs taken at different
  points in a fast-moving `main`): **before: 7623 passed, 8 skipped, 1
  xfailed, 0 failed. After: 7659 passed, 8 skipped, 1 xfailed, 0 failed.**
  Delta is exactly +36, matching the 36 tests added here (13 + 12 + 11), 0
  regressions. (An earlier baseline taken at an older `main` commit before
  this branch was fast-forwarded read 7573 passed; not used as the
  reported number since it wasn't measured at the same commit as the
  "after" run and `main` was moving fast this session — the clean pair
  above supersedes it.) Two other concurrent same-day sessions' test
  files (`test_zoho_desk_db.py`, `test_zoho_desk_sync.py` — a separate
  Sub-tier B batch, different branch/PR) were untracked in the shared
  working tree during these runs and are included in both the before and
  after counts identically, so they don't affect the delta.
- [x] Blast-radius grep performed for all three files: see section 4
  above, every real caller enumerated and confirmed unmodified.
- [x] A real cross-test-pollution bug in this session's own first draft
  (not production code) was caught by this full-suite run and fixed
  before merge — see section 4.
- [ ] Manual repro / staging check — not applicable, test-only change with
  no deployable behavior difference (and `routes/main.py` has no
  deployable surface at all, being unmounted).
- [ ] Feature-flagged — not applicable, test-only.
- [ ] Production build (`npm run build` equivalent) — not applicable,
  backend-only Python change; no `admin-dashboard`/`rider-app`/`driver-app`
  surface touched.

## 10. What was NOT verified

- Not run against real Supabase — mocked throughout, matching repo
  convention for this test tier.
- PDF *visual* output was not screenshotted or diffed against any golden
  file — no visual-regression tooling exists in this repo for generated
  PDFs (standing gap, same category CLAUDE.md flags for other
  visually-invisible changes). Verification here is limited to: the byte
  stream starts with `%PDF`, is non-trivially sized, and every
  input-shape/branch combination completes without raising. The actual
  rendered layout (font sizes, cell widths, whether numbers overlap
  labels) was not visually inspected.
- `routes/main.py`'s dead-code finding is based on static grep + git log,
  not a runtime trace of every possible server-startup code path — if some
  other entry point (a script, a Lambda handler, a different `server.py`
  variant not seen) imports and mounts this router, that would not have
  shown up in this search. Confidence is high given the specific,
  deliberate greps performed (server.py has its own independently
  implemented `/health` at a different line than this module's, which is
  the strongest signal — a real integration wouldn't need two competing
  health-check implementations).
- The `bold=True` dead-branch finding in `subscription_invoice_pdf.py`'s
  `_item_row` closure was confirmed via grep of every call site in the
  file (none pass `bold=True`), not via a broader repo-wide search for any
  other place that might construct/call the closure indirectly (not
  possible in Python for a closure local to another function, so this is
  definitional, not a sampling gap).
- A concurrent session's Sub-tier B batch 2 log (`services/zoho_desk_db.py`,
  `utils/zoho_desk_sync.py`, same day, different PR) reports observing one
  failure it attributes to `test_routes_main_coverage.py` in a mixed run,
  calling it a pre-existing order/timing-dependent flake. This is the same
  failure this session found and root-caused directly (not left as an
  unexplained flake): `TestHealthCheckBareHappyPath::test_healthy_with_no_request`
  asserted `result["loops"] == {}`, which holds when the file runs alone but
  not when it runs after any test that exercises a real background loop
  (`utils/loop_monitor.py`'s `_heartbeats` dict is a process-global
  singleton; `test_surge_engine.py` and others call the real
  `record_heartbeat()`, and `get_loop_status(None)` falls back to
  `list(snapshot.keys())` when nothing is explicitly registered). Fixed by
  pinning `utils.loop_monitor._heartbeats` to an explicit empty dict for
  the three affected tests, and reproduced the fix under the exact failure
  condition (`pytest tests/test_surge_engine.py
  tests/test_routes_main_coverage.py -q --no-cov`, run in that order,
  passes cleanly post-fix). Two full-suite runs after the fix — 7659
  passed with this batch's files present, 7623 passed with them removed
  (delta +36, exactly the new test count) — corroborate this, so this is
  reported as fixed and verified, not merely "not re-chased."
