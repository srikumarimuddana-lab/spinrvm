# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code session (spinr migration work) |
| Surface(s) | backend, admin |
| Domain (Sentry tag) | drivers |
| PR / commit link | #4633 (`claude/migration-batch-readiness-wicr1d`) |
| Related issue or gap ID | `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §4 Phase 1; `docs/runbooks/legacy-migration-playbook.md` item #11 |

## 1. Issue / gap identified

Phase 1's legacy Mongo driver importer (`build_mongo_driver_import_plan`/
`commit_mongo_driver_import_plan`) existed only as a CLI script. No admin-dashboard-reachable
execution path existed, so applying it required direct backend/CLI access — not something an
ops admin could do from the dashboard.

## 2. Root cause

Not a bug — this was the next planned subtask, explicitly deferred earlier the same day
("build this before anything else" for the core service/CLI, admin route flagged as a
remaining subtask). Requested and authorized this session.

## 3. Fix / remediation

New `routes/admin/legacy_driver_import.py`, mirroring `routes/admin/driver_import.py`'s
existing validate/commit pattern for the Saskatoon CSV importer exactly, for this different
CSV/service-layer pair:

- `POST /api/admin/legacy-drivers/import/validate` — parse + build the plan, return a dry-run
  report (counts, warnings, errors) plus a signed validation token. No writes.
- `POST /api/admin/legacy-drivers/import/commit` — requires that token (bound to this exact
  batch/CSV-sha256/admin id, reusing `utils/driver_import_token.py` unchanged — it's already
  fully generic), re-validates, and only if clean calls `commit_mongo_driver_import_plan`.

Reused directly rather than duplicated: `utils/driver_import_token.py` (sign/verify, no
Saskatoon coupling), the validate-then-commit-token safety pattern, the `asyncio.to_thread`
offload for the synchronous Supabase-calling plan/commit functions, and the PIPEDA-safe
report serialization (`old_driver_id`/`field`/`message` only). New: a dedicated
`legacy_mongo_driver_import_commit_limit` rate-limit bucket (`10/hour`, own quota, not shared
with the Saskatoon importer's), and `read_mongo_export_csv_text()` in the service layer — the
existing `read_csv_text()` goes through header normalization that corrupts this CSV's `_id`
column, the same bug class already documented on the file-path reader
(`read_mongo_export_csv`).

## 4. Risk & impact on existing functionality

- **Blast radius: this is the first point in Phase 1 where the importer becomes actually
  reachable.** Everything built earlier today (the plan/commit functions, the CLI) was
  correct but inert without an execution path; this route is that path. Gated the same way
  every other bulk-driver-write admin route is: router-level `Depends(get_admin_user)` (via
  `admin_router`) + `require_module("drivers")` at the `include_router()` call + the
  validate-token requirement on commit + the rate limit — four independent gates, not one.
- Grepped for other consumers of `read_mongo_export_csv_text`: none yet (new function, one
  caller). Grepped for other consumers of `driver_import_token.py`'s sign/verify: only
  `driver_import.py` and this new file — both bind on `(batch, csv_sha256, admin_id)`
  independently, so a token minted by one endpoint cannot be replayed against the other (the
  payload carries no endpoint identifier, but each endpoint re-derives its own
  batch/csv_sha256/admin_id from its own request and requires an exact match — a token from
  `/drivers/import/validate` would only verify against `/legacy-drivers/import/commit` if an
  attacker also controlled a CSV that hashed identically and reused the same batch string,
  which is not a meaningfully easier attack than forging the token itself).
- No existing route, test, or consumer touches `routes/admin/legacy_driver_import.py` (new
  file) or the two new service-layer/rate-limiter additions in a way that changes their
  behavior — confirmed via the full driver-import test family re-run (190/190 pass).
- This route can now create real `users`/`drivers` rows and mutate existing ones (link/enrich)
  in production, gated behind `drivers`-module admin access — same production-write risk
  profile as the existing Saskatoon `driver_import.py` route, not a new category of risk.

## 5. User-experience effect

Admin-facing only (an operator with `drivers` module access can now run this import from an
HTTP client or a future admin-dashboard UI — no dashboard UI page exists yet, this is API-only).
No rider/driver-facing change. Any driver row this route creates still lands forced
`needs_review`/unverified/offline/unavailable, same as the CLI path — nothing here changes what
a rider or driver sees.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/legacy_driver_import.py` | New — validate/commit endpoints | The admin execution path this Change Impact Log covers |
| `backend/routes/admin/__init__.py` | New router imported + mounted with `require_module("drivers")` | Wire the route in, same gate as the sibling Saskatoon importer |
| `backend/services/driver_import_service.py` | New `read_mongo_export_csv_text()` | In-memory (admin-upload) equivalent of the existing file-path reader, same raw-preservation logic |
| `backend/utils/rate_limiter.py` | New `legacy_mongo_driver_import_commit_limit` (`10/hour`) | Dedicated quota, matching this codebase's one-limiter-per-importer convention |
| `backend/tests/test_admin_legacy_driver_import.py` | New — 10 HTTP-layer tests | Cover both endpoints, both existing-match sub-populations, the token/rate-limit gates, and the `_id`-preservation regression this route exists to avoid |
| `docs/runbooks/legacy-migration-playbook.md` | Item #11 updated | Canonical Oct 30 checklist stays current |
| `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` | Phase 1 status updated | Keep the parent plan doc in sync |

## 7. Before / after

Pure addition — no existing behavior-changing diff. `driver_import.py` (the Saskatoon
importer's route) is untouched; nothing about its behavior changes.

## 8. Rollback plan

No feature flag exists or is needed. This route has never been called against production (no
admin-dashboard UI wired up to it yet, so only a direct API caller with `drivers` module
access could reach it, and no such call has been made from this session). Reverting the commit
removes the route entirely with no data-level cleanup required. If it is later called and needs
reversal after the fact, rollback follows the same three shapes as the CLI's own docstring
(new rows / linked accounts / enriched drivers — see
`docs/change-log/2026-08-27-legacy-driver-existing-match-link-enrich.md` §8), since this route
calls the identical `commit_mongo_driver_import_plan`.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_admin_legacy_driver_import.py` (10 pass) and the full
      driver-import test family (8 files, 190/190 pass, zero collateral breakage).
- [x] `ruff check` / `ruff format --check` clean on all changed/new files.
- [x] HTTP-layer regression proof, not just unit coverage: one test seeds the CSV with a raw
      Mongo `_id` column and asserts the row imports cleanly, proving the route reads it with
      `read_mongo_export_csv_text` and not the header-normalizing `read_csv_text`.
- [x] Both existing-match sub-populations exercised end-to-end through the real HTTP route
      (not just the service-layer unit tests already covering them): linking a new driver to
      an existing account, and enriching an existing driver's history with its live fields
      provably untouched.
- [x] Blast-radius grep performed: confirmed `driver_import_token.py` has exactly two callers
      now (this route and the Saskatoon one), each independently bound; confirmed no other
      caller of the two new functions/rate-limiter exists.
- [ ] Manual repro in staging — not applicable, no live credentials in this session and no
      admin-dashboard UI calls this route yet.
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA (reports carry only
      `old_driver_id`/`field`/`message`), auth (four independent gates, matched to the
      existing Saskatoon route's posture, not weaker), rate limiting (dedicated bucket, not
      borrowed from an unrelated importer's quota).

## 10. Sign-off

- [x] Rollback plan is concrete: no live data exists yet, so revert-the-commit is complete; the
      future-post-`--apply` rollback path is stated and points at the existing documented
      three-shape rollback for the underlying commit function.
- [x] Blast radius stated: this route's gates, its one new dependency's shared-token safety,
      and its zero other consumers were checked, not assumed.
- [x] No silent behavior change to an already-shipped flow — this is a new route; nothing
      existing changes behavior.
