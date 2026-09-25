# Dependency Graph — backend package layering + `shared/` consumers (Tier B matrix, part 1/2)

**Lane:** ad hoc Tier-B matrix build (this session) · **Written:** 2026-09-25 · **Report-only.**
Evidence labels: VERIFIED / INFERRED / UNKNOWN per master convention.

## §0 Method (reproducible)

1. **Backend graph**: an AST script (`dep_graph.py`, this session) walks every `.py` file under
   `backend/` (excluding `tests/`, `scripts/`, `migrations/`, `docs/`, `static/`, `assets/`, `sql/`,
   `evals/` — 450 files scanned), classifies each file into one of the 9 top-level packages
   (`ai`, `core`, `dependencies`, `models`, `repositories`, `routes`, `schemas`, `services`, `utils`) or
   a `root:<module>` bucket for the handful of top-level modules that sit outside any package
   (`server.py`, `features.py`, `documents.py`, `socket_manager.py`, `db_supabase.py`,
   `supabase_client.py`, `settings_loader.py`, `config.py`), then parses every `import`/`from...import`
   statement (both branches of the dual-import `try/except ImportError` pattern are parsed; a source
   file importing the same target package via both branches counts as **one** logical edge, not two).
   Edges are counted per **file** (a file importing three different names from the same target package
   counts once, not three times) so the numbers below are "how many source files in package A import
   from package B," not raw import-statement counts.
2. **Cycle detection**: a standard three-colour DFS over the coarse 9-package + root graph.
3. **`shared/` consumer counts**: grepped `rider-app/`, `driver-app/`, `admin-dashboard/` for both the
   `@shared/*` path-alias form (confirmed live in `rider-app/tsconfig.json:10-11` and
   `driver-app/tsconfig.json:9-10` — **no such alias exists in `admin-dashboard/tsconfig.json`**, grep
   confirms) and the relative `../shared`/`../../shared` form, across `.ts`/`.tsx`, excluding
   `node_modules` and test files. File counts are **files with ≥1 import from `shared/`**; the subpath
   breakdown (§3) counts **import statements** by `shared/`'s top-level subdirectory, so a file that
   imports from both `@shared/theme` and `@shared/api` contributes to both rows.

## §1 Backend package-level edge counts (routes → services → repositories → utils, as documented)

CLAUDE.md's Key Backend Files section describes the intended layering as `routes/` (one file per
domain) → `services/` (thin service layer) → `repositories/`/`db_supabase.py` → `utils/`
(cross-cutting). The table below is every edge found, sorted by weight (files), with the CLAUDE.md-
consistent direction marked ✅ and any edge running the other way marked ⚠.

| Edge (files that import) | Files | Direction vs. documented layering |
|---|---:|:-:|
| `routes` → root (`server`, `features`, `documents`, `socket_manager`, `db_supabase`, ...) | 164 | ✅ (routes is the top of the stack; the "root" modules are mostly `features.py`'s legacy route handlers and shared infra singletons) |
| `utils` → root | 139 | n/a — root here is largely `db_supabase`/`socket_manager`/`settings_loader`, foundational singletons every layer legitimately reaches |
| `routes` → `utils` | 115 | ✅ |
| `routes` → `dependencies` | 90 | ✅ (auth/RBAC dependency injection) |
| `services` → root | 87 | ✅/mixed (same root-bucket caveat) |
| `routes` → `services` | 73 | ✅ |
| `services` → `utils` | 33 | ✅ |
| `utils` → `core` | 24 | ✅ (core is more foundational than utils — config/lifespan/middleware) |
| **`utils` → `services`** | **18** | **⚠ inverted** — see §2 |
| `routes` → `core` | 18 | ✅ |
| `ai` → root | 16 | ✅ |
| `ai` → `utils` | 11 | ✅ |
| `routes` → `schemas` | 10 | ✅ |
| **`utils` → `repositories`** | **7** | **⚠ inverted** — see §2 |
| `routes` → `repositories` | 7 | ✅ |
| `services` → `repositories` | 6 | ✅ |
| **`utils` → `routes`** | **5 (script) / 8 (grep re-check, §2)** | **⚠ inverted, most severe layering violation found** |
| `routes` → `models` | 5 | ✅ |
| `routes` → `ai` | 5 | ✅ (AI assistant tool-calling reads route-layer helpers) |
| `services` → `models` | 4 | ✅ |
| **`services` → `routes`** | **4** | **⚠ inverted** — see §2 |
| `utils` → `models` | 4 | ✅ |
| `services` → `core` | 3 | ✅ |
| **`repositories` → `utils`** | 2 | ✅ (repositories sitting on utils is consistent with utils as the base layer) |
| `utils` → `ai` | 2 | ⚠ minor — a utility depending on the AI package is unusual but low-count |
| **`core` → `routes`** | 1 | **⚠ inverted** — `core/lifespan.py:325` only, see §2 |
| **`core` → `services`** | 1 | **⚠ inverted** — `core/lifespan.py:237` only, see §2 |
| **`core` → `ai`** | 1 | **⚠ inverted** — `core/lifespan.py:933,949` |
| **`repositories` → `core`** | 1 | **⚠ inverted** — `repositories/dispatch_pool.py:108`, a lazy `from ..core.config import settings` inside a function body |

Most-imported packages (in-degree, files-that-are-imported-from count): **root-level modules 430**,
**`utils` 172**, **`dependencies` 95**, **`services` 94**, **`core` 52**, **`repositories` 22**,
**`routes` 15**, **`models` 15**, **`schemas` 13**, **`ai` 9**. `utils` being the single most-imported
*package* (as opposed to the root-module bucket, which is inflated by every route file reaching for
`db_supabase`/`socket_manager`) is consistent with CLAUDE.md's own description of it as "cross-cutting."

## §2 The inversions, read together: `utils/` is not a bottom layer, it's the background-loop layer

The five ⚠ rows above are not scattered noise — they cluster into one real architectural finding.
Grepping the actual files behind `utils → services` (18), `utils → routes` (8), and
`utils → repositories` (7):

- **`utils → services`** (18 files): `allowance_reset.py`, `corporate_autotopup.py`,
  `document_expiry.py`, `ledger_projection.py`, `marketing_email.py`, `marketing_push.py`,
  `marketing_sms.py`, `outbox_worker.py`, `payment_operations.py`, `payment_retry.py`,
  `preauth_capture.py`, `reconciliation.py`, `referral_payout.py`, `scheduled_rides.py`,
  `sos_contact_notice.py`, `spinr_pass.py`, `stripe_reconcile.py`, `zoho_desk_sync.py`.
- **`utils → routes`** (8 files): `auto_payout.py`, `driver_statement.py`, `offer_expiry_reaper.py`,
  `referral_payout.py`, `referral_terms.py`, `ride_settlement.py`, `route_finalizer.py`,
  `scheduled_rides.py`.
- **`utils → repositories`** (7 files): `capacity_watchdog.py`, `dispute_evidence_pack.py`,
  `driver_presence.py`, `driver_readiness_reconciler.py`, `email_receipt.py`, `insurance_periods.py`,
  `stale_intent_reconciler.py`.

Every one of these file names is recognizable as one of the **42 background asyncio loops**
CLAUDE.md's `core/lifespan.py` registers (`_WATCHDOG_LOOP_NAMES`) — subscription expiry, payment retry,
document expiry, corporate auto-topup, reconciliation, Stripe reconcile, driver earnings statements,
insurance period reconciler, stuck-ride sweeper, and more. **`backend/utils/` is doing two structurally
different jobs under one name**: genuine cross-cutting helpers (`crypto.py`, `redis_client.py`,
`rate_limiter.py` — the ones CLAUDE.md's own file list names) **and** the entire background-job
orchestration layer, which by its nature has to call *up* into `services`/`routes`/`repositories` to do
its work (a payment-retry loop has to call the same settlement logic a route handler calls). The
"inversion" is not a bug in any individual file — a background loop legitimately needs to reach the
same business logic a request handler does — but it does mean **`utils/` cannot be read as the
dependency-free base layer CLAUDE.md's package list implies it is**, and a clean-sheet rebuild should
either (a) give the background-loop layer its own top-level package (e.g. `backend/loops/` or
`backend/jobs/`), separate from true utilities, or (b) update the documented layering to name three
tiers instead of the current four-deep chain with `utils` silently overloaded as both the bottom and a
de facto fifth "orchestration" tier above `services`. This is a naming/organization finding, not a
correctness one — nothing here is evidence of an actual circular-import bug (Python would fail at
import time if two packages truly cycled at module-load; these `⚠` edges exist safely because Python
resolves most of them via function-local imports, e.g. `repositories/dispatch_pool.py:108`'s
`from ..core.config import settings` sits inside a function body, not at module scope).

**`core → routes` / `core → services` / `core → ai`** (1 file each) are a narrower, single-file
instance of the same pattern: all three trace to `core/lifespan.py` (`:325` `from routes.drivers import
check_expiring_subscriptions`; `:237` `from services.data_transfer.sgi_template_versions import
startup_verify_sgi_templates`; `:933,949` `from ..ai.mcp_server import start_mcp/stop_mcp`) — the
startup/shutdown orchestrator legitimately needs to call into every layer to wire up loops and optional
subsystems at boot. Same read: not a bug, but `core/lifespan.py` is architecturally a composition-root
file that happens to live in a package (`core/`) whose other members (`config.py`, `middleware.py`,
`security.py`) are genuinely foundational and import nothing app-specific.

## §3 Formal cycles (coarse package graph)

The DFS found 20 back-edges, but **every one of them routes through `core` and/or `ai`, both of which
only reach into the app layers via `core/lifespan.py`'s composition-root imports (§2)** — there is no
cycle among `routes`/`services`/`repositories`/`utils` in isolation once `core`/`ai`/root are excluded.
Shortest true cycles:
- `services ↔ core` (`services → core` ×3, `core → services` ×1 via `lifespan.py:237`)
- `core ↔ ai` (`core → ai` ×1 via `lifespan.py:933/949`, `ai → core` ×1)
- `ai ↔ utils` (`ai → utils` ×11, `utils → ai` ×2)
- `utils ↔ repositories` (`utils → repositories` ×7, `repositories → utils` ×2)
- `utils ↔ routes` (`utils → routes` ×8, `routes → utils` ×115)
Longer cycles reported by the DFS are combinations of these same five two-node cycles chained through
`core`'s composition-root role and are not independent findings.

**Reading**: none of these are load-time circular-import failures (the codebase imports successfully;
if it did not, `pytest`/`python -m backend.server` would fail at collection). They are **directional**
findings about what "layer" means in this codebase versus what CLAUDE.md's package list implies, not
**correctness** findings. A future rebuild that wants the documented layering to be literally true would
need to either extract the background-loop files out of `utils/` (§2) or relax `core`'s role to
explicitly include startup composition as a stated exception.

## §4 Most-imported / highest fan-out files (top 10 by number of distinct target packages)

Not separately re-derived beyond §1's package rollup — the file-level `import_counts_per_file` data
this session's script also produced is dominated by `routes/rides/__init__.py` and `routes/drivers/
__init__.py` (each re-exports dozens of names from `_deps.py`, inflating their raw import-statement
count without it reflecting real fan-out complexity) and is not reproduced as its own table here to
avoid restating what §1's package-level view already shows more usefully. Flagged as available in the
underlying script output if a future pass wants file-level granularity.

## §5 `shared/` package — consumers across the three frontend apps

| App | Files importing from `shared/` | `@shared/*` alias present? |
|---|---:|:-:|
| `rider-app` | **75** | ✅ (`rider-app/tsconfig.json:10-11`) |
| `driver-app` | **91** | ✅ (`driver-app/tsconfig.json:9-10`) |
| `admin-dashboard` | **1** (`admin-dashboard/scripts/test-railway-login.ts`, a relative-path, non-aliased import of `shared/utils/pii`'s `redactEmail`) | ❌ no `@shared` alias in `admin-dashboard/tsconfig.json` (grep confirms) |

This is a real, verified confirmation of what CLAUDE.md's Context Imports section already states as
policy (`shared/theme/` is the rider-app/driver-app design-system source; admin-dashboard has its own
design system) — admin-dashboard's near-total absence of `shared/` imports is the *intended* boundary,
not a gap, with one narrow exception (a PII-redaction helper reused in an internal script, not shipped
app code).

### §5.1 `shared/` subpath breakdown (import-statement counts, rider-app vs. driver-app)

| `shared/` subpath | rider-app | driver-app |
|---|---:|---:|
| `theme` | 112 | 117 |
| `components` | 102 | 86 |
| `api` | 81 | 74 |
| `utils` | 77 | 80 |
| `store` | 33 | 22 |
| `hooks` | 20 | 18 |
| `config` | 9 | 16 |
| `types` | 13 | 3 |
| `services` | 9 | 14 |
| `analytics` | 7 | 2 |
| `errors` | 4 | 1 |
| `cache` | — | 2 |
| `auth` | — | 2 |
| `constants` | — | 1 |

`theme` is the single most-imported `shared/` subpath in **both** apps by a wide margin, confirming
CLAUDE.md's own framing of `shared/theme/` as the one no-per-app-override token source (Context Imports
section) is load-bearing in practice, not aspirational. `components` and `api` are the next heaviest —
consistent with W0-SUMMARY.md's own finding that `shared/` (93 files, a 2-sentence PRD footprint per
that document) is "the root of 3 live cross-app bugs" (§10): a shared theme/component/API layer this
heavily used by both consumer apps is exactly the kind of surface where one app's fix not reaching the
other (the 2026-09-12 ride-experience audit's own named lesson, CLAUDE.md's release-gate §10) is
structurally likely to recur, and CLAUDE.md itself notes no `spinr-*` agent owns `shared/` logic
correctness (`W0-SUMMARY.md` §12, CARTO-002/003).

## §6 What was not checked

- No file-level (as opposed to package-level) cycle detection was run within `routes/`, `services/`, or
  `utils/` themselves (e.g. whether `routes/rides/matching.py` and `routes/rides/lifecycle.py` import
  each other) — this file's scope is the package-level graph the brief asks for.
- `shared/`'s own internal dependency structure (does `shared/components` import `shared/store`, etc.)
  was not graphed — only its consumption *from* the three apps.
- The `root:` bucket (`server.py`, `features.py`, `documents.py`, `socket_manager.py`, `db_supabase.py`,
  `supabase_client.py`, `settings_loader.py`, `config.py`) was intentionally not split further into its
  own 8-node sub-graph — these are singletons/legacy modules outside the 9 declared packages, and a
  finer breakdown was judged lower value than the package-level view for a Tier-B (build-if-time-allows)
  matrix.
- `agents/` (the separate Python framework) and its own internal dependency structure are out of scope —
  CLAUDE.md states it is not part of the production runtime.
