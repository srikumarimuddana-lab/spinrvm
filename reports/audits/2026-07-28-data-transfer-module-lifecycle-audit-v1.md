# Data Transfer Module — Structured Lifecycle Audit v1

**Date:** 2026-07-28
**Scope:** `backend/routes/admin/data_transfer_{export,import,jobs,search}.py`,
`backend/services/data_transfer/*`, `backend/migrations/262_data_transfer_export_jobs.sql`,
`admin-dashboard/src/app/dashboard/data-transfer/`,
`admin-dashboard/src/app/dashboard/bulk-operations/`, associated tests.
**Trigger:** requested as a comparison audit after the Compliance & Tax
Reporting module's lifecycle audit (`2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md`)
— the user asked for the same structured format applied to Data Transfer.
**Author:** Claude (third-party audit — this session did not build this
module; it was built across an earlier, real phased project, Phase 1.1
through 6.1, 24+ commits between `a5468cc` and `e382b65`)

> **Read this as closer to a genuine third-party audit than the compliance
> report was.** I did not write this module. Unlike the compliance
> self-audit, findings here aren't things I personally introduced and
> caught under user pressure — they're gaps in someone else's (real,
> disciplined) work, found by applying the same structural lens. One
> conclusion up front: this module's *process* was measurably better than
> compliance's ad-hoc build (real phases, a Change Impact Log entry per
> commit, rate limiting and Sentry/Prometheus instrumentation shipped from
> early on) — but it still has the same class of RBAC-grant bug (G1) and a
> *worse* route-level testing gap (G3), because a good process without a
> testing gate for "does the actual HTTP route work" reliably produces the
> same hole a bad process does.

---

## 1. Coverage snapshot

| Scope | Coverage | Bar (CLAUDE.md) | Status |
|---|---|---|---|
| `services/data_transfer/entity_export_service.py` | **98%** | "Admin routes, utilities: ≥ 70%" | ✅ |
| `services/data_transfer/entity_import_service.py` | **93%** | ≥ 70% | ✅ |
| `services/data_transfer/sgi_form_filler.py` | **100%** | ≥ 70% | ✅ |
| `services/data_transfer/tabular_writer.py` | **100%** | ≥ 70% | ✅ |
| `services/data_transfer/bundle_zip_builder.py` | **100%** | ≥ 70% | ✅ |
| `services/data_transfer/sgi_field_maps.py` | **89%** | ≥ 70% | ✅ |
| `services/data_transfer/observability.py` | **50%** | ≥ 70% | ❌ |
| `services/data_transfer/bundle_document_uploader.py` | **39%** | ≥ 70% | ❌ |
| `routes/admin/data_transfer_export.py` | **39%** | ≥ 70% | ❌ |
| `routes/admin/data_transfer_import.py` | **62%** | ≥ 70% | ❌ |
| `routes/admin/data_transfer_jobs.py` | **37%** | ≥ 70% | ❌ |
| `routes/admin/data_transfer_search.py` | **69%** | ≥ 70% | ❌ (just under) |
| Full backend suite (`pytest -m "not slow"`) | **70.64%** | `--cov-fail-under=60` (pytest.ini) | ✅ |

Module aggregate: **696 statements, 176 missed → ~74.7%** — nominally
above the full-suite average, but that number is doing a lot of hiding.
Split the module in two and the picture is exactly the compliance
module's pre-G3 problem, expressed more sharply:

- **Service layer (business logic): 93–100%**, genuinely well tested —
  `test_entity_export_service.py` (176 lines), `test_entity_import_service.py`
  (307 lines), `test_sgi_form_filler.py` (106 lines) all exercise real
  branches with named regression tests, not padding.
- **Route layer (the actual HTTP surface an admin or attacker interacts
  with): 37–69%, zero dedicated test files.** `grep`-ing
  `backend/tests/` for `data_transfer_export`, `data_transfer_import`,
  `data_transfer_jobs`, or `data_transfer_search` by filename returns
  **nothing** — not "thin coverage," no file exists. Compliance's
  pre-G3 state at least had *some* coverage on its route file (57%) via
  helper-function tests; this module's four route files have never been
  imported by a test at all except incidentally through whatever App
  wiring `test_admin_routes_auth.py`-style smoke tests might exercise.

**Why this matters concretely, not just as a number:** the export route
(`data_transfer_export.py`) is the single highest-blast-radius endpoint
found in either module — it returns **unredacted, decrypted PII plus raw
document file bytes** for up to 100 users/drivers at once
(`entity_export_service.py`'s own docstring: *"no redaction, because the
operator already has full admin visibility into the source data"*), yet
its own route wiring (background-task dispatch, job-row creation, the
`MAX_ENTITIES_PER_EXPORT` guard, the 202-vs-400-vs-422 status logic) has
**39% test coverage and no test file**. The one thing standing between
"only intended entities exported" and "off-by-one lets 101 through" or
"a malformed request silently drops the entity cap" is code nobody has
written a test against.

---

## 2. Lifecycle phase-by-phase assessment

### Phase 1 — Inception: **implicit but coherent**
No formal problem statement exists as a standalone document, but unlike
compliance's three-times-pivoted scope, the git history shows a single,
consistent thread from the first commit (`a5468cc feat(admin): add
data-transfer export core`) through to nav consolidation
(`e382b65`) — export → import → search → SGI forms → jobs history →
background execution → rate limiting → observability, each phase
building on the last without rework. **Reasoning why this matters:** the
*outcome* of a good inception (stable scope, no thrash) is visible even
without the inception *artifact* — this is evidence that informal
planning happened somewhere (a chat, a doc outside this repo), even
though it isn't recoverable from the repo itself. **Recommendation:**
same as compliance's — write the 5-question answer as the literal first
commit message or a `docs/change-log/` entry numbered `phase-0`, so the
next module gets the artifact this one is missing, not just the good
outcome.

### Phase 2 — Requirements: **stronger than compliance's, one real gap found**
Functional requirements are legible from the phase commit messages
themselves (`Phase 2.1: import core`, `Phase 3.1: unified search
endpoint`, `Phase 5.1: SGI compliance PDF form-fill service`) — a
reader can reconstruct the full requirement set without needing PR
descriptions. Non-functional requirements are *partially* explicit:
`MAX_ENTITIES_PER_EXPORT = 100` and `MAX_ZIP_BYTES = 200_000_000` are
both justified in-code (comments explain *why* those numbers, not just
that they exist) — a real improvement over compliance's un-derived
`_ROW_LIMIT = 10000`.

**Concrete, high-value gap found here — same threat-model mismatch as
compliance, arguably worse:** `docs/threat-model/admin-panel.md`'s
`AI-3` finding (*"Admin exports all users → offline PII leak"*,
mitigation *"dual-approval on any export > 1,000 rows"*, still **OPEN**)
was recently updated (this session, PR #2682) to name compliance's two
endpoints explicitly. **It does not name Data Transfer's export
endpoint at all**, despite that endpoint being the closer, more literal
match to AI-3's own attack tree wording — `AAT-1` line 118 reads
*`2.1 Use "Export all" endpoint → [AI-3 OPEN — needs approval gate]`*,
and Data Transfer's `/data-transfer/export` is genuinely a multi-entity
"export all [selected]" endpoint returning full-fidelity PII + document
files, not aggregate tabular numbers like compliance's reports. The
10/hour rate limit (`data_transfer_export_limit`) meaningfully reduces
exploitability versus compliance's unlimited endpoints, but does not
substitute for the dual-approval gate AI-3 itself calls for — a
compromised admin session can still exfiltrate 100 full identity+document
bundles per hour, indefinitely, with zero second-party check.
**Reasoning on the right fix:** identical logic to compliance's G2 — do
not build a Data-Transfer-specific approval gate; extend AI-3's row in
the threat model to name this endpoint too (now covering three endpoints
across two modules), so the eventual shared implementation closes all of
them in one shot instead of needing a third bespoke retrofit.

### Phase 3 — Design: **stronger than compliance's — real ADR-caliber decisions, still undocumented as ADRs**
Real, well-reasoned design decisions exist in code comments: the
decision to **background** the export route
(`data_transfer_export.py`'s own docstring: *"This replaced an earlier
synchronous version that gathered/zipped/uploaded inline — a real risk
for the max 100-entity batch... tying up a request/response cycle"*) is
exactly ADR material — a rejected prior approach, a concrete reason, a
measurable trade-off (an admin now polls for job status instead of
getting an immediate file). So is the deliberate **non-reuse of the DSAR
self-export redaction path** (`entity_export_service.py`: *"no
redaction, because the operator already has full admin visibility"*) —
a security-relevant decision a future engineer could plausibly get
backwards by "simplifying" the two export paths into one. Neither has an
ADR. `docs/adr/` now has 8 entries (`008` added this session for
compliance's branded/fixed_format split) — none for Data Transfer's
background-job pattern, which is arguably the more architecturally
significant decision of the two modules (it introduces an entirely new
execution model — fire-and-poll versus request/response — that a future
"add a fifth Data Transfer operation" effort will need to either follow
or deliberately break from).

Data model: migration 262 (`data_transfer_export_jobs`) follows
`backend/migrations/CLAUDE.md` correctly — explicit per-action
`service_role` policies (not `FOR ALL`), RLS enabled, rollback documented
with an *ordered, sequenced* rollback procedure (remove code references
first, deploy, then drop table) that is meaningfully more rigorous than
compliance's bare `DROP TABLE IF EXISTS` — this migration's author
clearly thought about the fact that dropping the table while live code
still references it would break the export/purge path.

### Phase 4 — Development: **followed CLAUDE.md's stated gates well, with the same systemic near-miss as compliance**
What went right, concretely:
- A real bug was found and fixed post-launch through what looks like
  genuine operational vigilance, not luck: `d3dc2f8 fix(admin):
  decrypt/re-encrypt license_number across SGI forms + export/import` —
  a PII-encryption correctness bug caught and fixed as its own dedicated
  commit, with its own Change Impact Log entry
  (`docs/change-log/2026-07-28-data-transfer-license-number-encryption-fix.md`).
- Performance was treated as a first-class concern, not an afterthought:
  `4978da8 perf(admin): background the Data Transfer export route` is a
  dedicated commit reworking the execution model specifically because
  the synchronous version was identified as a real risk — this is the
  kind of self-correction compliance's build never exhibited.
- Security was added incrementally and specifically:
  `de1322b security(admin): rate-limit the Data Transfer export route`
  is its own commit with its own rationale, not bundled into a feature
  commit.
- Observability was added as a dedicated phase:
  `d444fd7 observability(admin): add Sentry + Prometheus instrumentation
  to Data Transfer` — this is exactly compliance's G5, already closed
  here from early in the module's life, not retrofitted under audit
  pressure.
- 22 `docs/change-log/2026-07-28-data-transfer-*.md` entries exist — one
  per meaningful commit, matching CLAUDE.md's Change Impact & Risk Log
  convention far more consistently than compliance's build did (which
  had none until this session's audit-driven PRs).

What's a systemic gap, not a one-off: **the same structural hole
compliance had — no development-phase gate ever asked "does the actual
route handler have a test," only "does the underlying service logic have
a test."** Every one of the real, positive practices above (perf
commit, security commit, observability commit) happened at the *service*
or *cross-cutting* layer. None of them produced a route-level test. This
is worse here than in compliance specifically because rate limiting and
backgrounding — both genuinely good decisions — are *themselves*
route-layer behavior (the `@data_transfer_export_limit` decorator, the
`BackgroundTasks.add_task` wiring, the 202-status contract) that no test
currently verifies actually functions as designed.

### Phase 5 — Testing: **service layer well-tested, route/integration/E2E layer absent — a sharper version of compliance's gap**
- Unit tests (service layer): 760 lines across 5 files, well-reasoned,
  each traceable to real behavior (entity resolution conflicts, document
  fetch failures, SGI field mapping, zip bundling). Genuinely strong.
- Integration tests (route layer): **none — zero test files reference
  any of the four route modules.** This is a stricter absence than
  compliance's pre-G3 state, which at minimum had *helper-level* tests
  inside a file that imported the route module.
- E2E tests: **none.** `admin-dashboard/e2e/` has 29 spec files as of
  this session (28 pre-existing + `compliance.spec.ts` added this
  session for G4); Data Transfer, despite having 4 distinct UI tabs
  (`ExportTab.tsx`, `ImportTab.tsx`, `JobsTab.tsx`, `SgiFormsTab.tsx`)
  and the standalone legacy `bulk-operations/page.tsx`, has zero.
- Component/frontend unit tests: **none** — no `*.test.*` file exists
  under either `admin-dashboard/src/app/dashboard/data-transfer/` or
  `.../bulk-operations/`.
- Rate-limit-specific testing: **none.** Compliance's G6 fix (this
  session, PR #2688) added `test_compliance_rate_limit.py` verifying the
  11th-request-429 behavior with the real SlowAPI limiter. Data
  Transfer's export route has had a real rate limit in production since
  `de1322b` — with no test ever confirming it actually enforces the
  10/hour boundary, or that the boundary survives a future refactor.

### Phase 6 — Deployment: **followed convention correctly, same rollback-verification gap as compliance**
All 24+ commits are `main`-direct or PR-merged following repo convention.
Migration 262's rollback procedure is well-documented (see Phase 3
above) but, like compliance's migration 263, was never re-verified
against the real staging project it was applied to — this audit did not
find evidence either way (staging application wasn't part of this
session's scope for this module), so this is flagged as **unknown, not
confirmed-fine or confirmed-broken** — a narrower finding than
compliance's G10, which at least had a documented "yes, this was applied
to staging" data point to reason about.

### Phase 7 — Launch/Hypercare: **no formal hypercare, but real post-launch correction happened**
The `license_number` encryption fix (`d3dc2f8`) and the Jobs-tab
follow-up (`05a402a feat(admin): add Jobs & History tab reading
data_transfer_export_jobs (follow-up)` — closing a gap where the export
jobs table existed but nothing ever read it back) both show the module
was actively watched and corrected after initial ship, not abandoned.
This is healthier hypercare behavior than compliance exhibited, even
without a formal process — but it's still not a *process*, it's
evidence of an attentive builder, which doesn't transfer to the next
module or the next contributor by default.

### Phase 8 — Operations: **stronger than compliance's on retention, identical gap on RBAC-grant, worse gap on rate-limit coverage**
- **Retention/purge: already solved, unlike compliance's G8.**
  `utils/data_export_purge.py`'s hourly loop explicitly purges
  `data_transfer_export_jobs` rows past `expires_at`
  (`.not_.is_("expires_at", "null").lt("expires_at", now_iso)`), with
  per-row error isolation. This is exactly what compliance's
  `compliance_export_events` table lacks — a real, live enforcement
  mechanism, not just a comment claiming one exists.
- **Monitoring/metrics: already solved, unlike compliance's original
  G5.** `services/data_transfer/observability.py` provides
  `record_export_result`/`record_import_result`/`record_sgi_form_result`
  (Prometheus-style counters, `spinr_data_transfer_*_total` naming) and
  `capture_failure` (domain-tagged Sentry capture) — and both are
  actually *called* from the route layer (`data_transfer_export.py`
  line 143-154, `data_transfer_import.py` line 102-112), not just
  defined and unused. This is the file compliance's own G5 fix
  (`_capture_export_failure` in `routes/admin/compliance.py`) explicitly
  modeled itself on — the pattern this session invented for compliance
  already existed here first.
- **Rate limiting: partially solved, and the gap is more consequential
  than compliance's was.** Only `/data-transfer/export` is rate-limited
  (`data_transfer_export_limit`, 10/hour). `/data-transfer/import/validate`,
  `/data-transfer/import/commit`, `/data-transfer/search`, and all three
  `/data-transfer/jobs*` routes have **zero rate limiting**. This is a
  materially different risk than compliance's G6 (which was "no rate
  limit on two read-only report endpoints"): `import/commit` is a
  **bulk-write path that creates `users`/`drivers` rows** and accepts
  uploads up to 200 MB; `search` runs `count_documents` (a full
  head-count query) with an unauthenticated-beyond-JWT text filter; an
  unthrottled admin session (or a compromised one) could drive either
  into meaningful DB load with no per-minute/per-hour ceiling at all.
- **Feature is not actually grantable to non-super-admins — same bug
  class as compliance's G1, now fixed.** `"bulk_operations"` was missing
  from `ALL_MODULES` in `admin-dashboard/src/app/dashboard/staff/page.tsx`,
  identical to compliance's G1. **Fixed in PR #2700** (this session,
  prior to this audit being written) — verified via a real production
  build. One nuance worth documenting that the G1 fix itself doesn't
  surface: `sidebar.tsx` wires **two separate pages** to the
  `bulk_operations` module key — `/dashboard/data-transfer` (which
  correctly gates via `useRequireModule("bulk_operations")`, and is the
  page this fix was intended to unlock) and `/dashboard/bulk-operations`
  (the older, still-live page, whose own docstring states it is
  *"Strictly role === 'super_admin'"* and enforces that with an
  independent `useAuthStore` role check, not the module grant).
  **Confirmed no regression:** `bulk-operations/page.tsx` does not rely
  on the module grant for its access control, so making
  `bulk_operations` grantable does not weaken it. But this is a landmine
  for a future engineer: two pages, one module key, two different
  intended access models, with the difference documented only in a code
  comment on one of the two files. **No audit-framework module scope
  file** exists for Data Transfer either (`audit-framework/modules/` has
  `compliance-reporting.md` from this session but nothing for Data
  Transfer) — same gap as compliance's pre-G9 state.
- **No runbook.** Same absence as compliance — if an export job silently
  stalls in `pending` or the purge loop misfires, there's no discoverable
  operational document; the Sentry/Prometheus instrumentation (a real
  strength) still needs a human process on the other end that doesn't
  exist yet.

---

## 3. Gap register (prioritized)

| # | Gap | Severity | Where | Fix effort | Recommended fix |
|---|---|---|---|---|---|
| H1 | `"bulk_operations"` missing from `ALL_MODULES` — feature ungrantable to non-super-admins | **High** (functional) | `admin-dashboard/src/app/dashboard/staff/page.tsx` | 1 line | **Already fixed — PR #2700.** |
| H2 | No dual-approval/threshold gate on the export endpoint — the single most literal instance of open threat-model finding AI-3 in the codebase (full PII + document bytes, up to 100 entities/request) | **High** (documented security risk; higher-fidelity data than compliance's instance of the same finding) | `docs/threat-model/admin-panel.md`'s AI-3 row, `routes/admin/data_transfer_export.py` | Small (docs) now / Medium (real gate) later | Extend AI-3's row to name `/data-transfer/export` explicitly, same pattern as PR #2682 did for compliance. Do not build a one-off gate — wire into AI-3's eventual shared mechanism. |
| H3 | Import/search/jobs routes have zero rate limiting — `import/commit` is an unthrottled bulk-write path (creates `users`/`drivers` rows, accepts uploads to 200 MB) | **High** (write-path, not just read-path — more consequential than compliance's G6) | `routes/admin/data_transfer_{import,jobs,search}.py` | Small | Apply the same `default_limiter`/SlowAPI pattern already used on `/data-transfer/export`; a write-path like `import/commit` should get a *tighter* limit than a read-path, not the same or none. |
| H4 | Route handlers untested — zero test files for any of the 4 admin routes (37-69% coverage, all below the 70% bar); the export route specifically (39%) gates the highest-blast-radius endpoint in either module | **High** (worse absence than compliance's pre-G3 state — no file, not just low coverage) | `routes/admin/data_transfer_{export,import,jobs,search}.py` | Medium | Add `TestClient`-based tests per route: entity-count-cap enforcement (400/422), background-task dispatch + job-row creation (202), rate-limit boundary (10/hour → 429), import validate/commit success + `can_commit=false` + 502-on-partial-failure, jobs list/detail/download including the 404/409/410 paths. |
| H5 | No E2E test for any Data Transfer or Bulk Operations admin-dashboard surface | **Medium** | `admin-dashboard/e2e/` | Small–medium (4 tabs + 1 legacy page = more surface than compliance's single page) | Playwright specs mirroring `compliance.spec.ts`'s pattern — one per tab at minimum (Export, Import, Jobs, SGI Forms), plus a spec confirming `bulk-operations/page.tsx`'s super-admin-only gate actually renders the denial card for a non-super-admin mock. |
| H6 | No ADR for the background-job execution model or the deliberate non-reuse of DSAR redaction logic | **Low** (documentation debt — both decisions are well-reasoned in code comments already, unlike a truly undocumented decision) | `docs/adr/` | Small | One ADR covering both: why export runs as a background task + poll-for-status rather than request/response, and why this module's exports are unredacted by design (operator-to-operator transfer, not a self-service PIPEDA export). |
| H7 | No audit-framework module scope file — this surface won't be swept by future systematic audits | **Low** | `audit-framework/modules/` | Small | Add `audit-framework/modules/data-transfer.md`, same shape as this session's `compliance-reporting.md`. |
| H8 | Migration 262's rollback command not confirmed re-verified against real staging (status: unknown, not confirmed-broken) | **Low** | staging Supabase project | Trivial | If staging verification of this migration ever happened, document it (mirrors compliance's now-resolved G10); if it never did, this is a smaller, narrower version of the same open question compliance had. |
| H9 | Two admin-dashboard pages (`data-transfer`, `bulk-operations`) share one RBAC module key (`bulk_operations`) with two different intended access models — one relies on the grant system, one hard-codes `role === "super_admin"` independently — documented only in a code comment on the second file | **Low** (no current bug — confirmed no regression from the H1 fix — but a real landmine for the next engineer to touch either file) | `admin-dashboard/src/components/sidebar.tsx:130-131`, `.../bulk-operations/page.tsx` | Trivial | A short comment in `sidebar.tsx` next to both nav entries cross-referencing the other, so the split access model is visible from either file, not just one. |

---

## 4. What this module does *not* need (explicitly, to avoid over-correcting)

- **No canary/blue-green deploy strategy** — same reasoning as
  compliance: low-traffic admin surface, not a KPI-tracked consumer path.
- **No load/performance testing beyond what already exists** — the
  module already made a real, deliberate performance decision
  (backgrounding the export route) *because* of a load concern; the
  entity/byte caps (`MAX_ENTITIES_PER_EXPORT`, `MAX_ZIP_BYTES`) already
  bound worst-case cost. Formal load testing would be gold-plating
  unless usage patterns change materially.
- **No new observability instrumentation** — `observability.py` already
  covers this module correctly; it needs *tests confirming it fires*,
  not more instrumentation.
- **No new retention/purge job** — `data_export_purge.py` already
  handles this module's export jobs table; nothing to add.
- **No dedicated Terraform/IaC** — same as compliance, no new
  infrastructure, only application code + one migration against
  existing Supabase.

---

## 5. Recommended next steps, in order

1. **H1 — already done** (PR #2700, prior to this audit).
2. **H3 (rate limiting)** first among the remaining items — it's the
   only gap here with a live, unthrottled bulk-*write* path in
   production today, a materially different risk profile than any
   read-only gap in either module's register. Small, mechanical fix.
3. **H2 (AI-3 threat-model scoping)** — same reasoning as compliance's
   G2: cheap to do now (extend one existing row), and the module this
   applies to hasn't been named in the open finding at all yet, unlike
   compliance's endpoints which already were as of this session.
4. **H4 (route-level tests)** — larger effort than H2/H3, but the
   highest-value single item on this list: it's the only gap that
   directly protects the entity-count cap and rate-limit boundary on
   the module's highest-blast-radius endpoint from silently regressing
   in a future refactor.
5. **H5 (E2E)** — batch with H4 once route-level tests exist, since
   both will likely surface the same edge cases from different layers.
6. **H6, H7, H9** — documentation debt, batch into one follow-up PR,
   same pattern as compliance's G4+G7+G9 batch.
7. **H8** — register as an open question in `ACTION_ITEMS.md` rather
   than chasing it now; resolve opportunistically the next time this
   module's migration needs touching for any other reason.

---
_Generated by Claude Code as a structured lifecycle audit of a module
built in an earlier, separate effort (Phase 1.1–6.1, commits `a5468cc`
through `e382b65`), requested as a comparison point against the
Compliance & Tax Reporting module's self-audit performed earlier in this
session._
