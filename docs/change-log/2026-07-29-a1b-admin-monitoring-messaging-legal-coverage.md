# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude (agent session) |
| Surface(s) | backend (tests only) |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | `ACTION_ITEMS.md` A1b Track 1 item 4 — `backend/routes/admin/` coverage |

## 1. Issue / gap identified

Three admin route modules were below the 70% admin-routes coverage floor:
`routes/admin/monitoring.py` (~54%), `routes/admin/messaging.py` (~60%), and
`routes/admin/legal_documents.py` (~47%). `messaging.py` is admin-initiated
bulk push/email/SMS broadcast to riders/drivers — an untested bug there risks
a mass-send incident (spam or a silently-dropped broadcast). `legal_documents.py`
manages ToS/Privacy Policy content and the per-audience version counter the
apps use to trigger PIPEDA re-consent — an untested version-bump bug is a
consent-tracking correctness issue, not just a content bug.

## 2. Root cause

These modules simply never had dedicated unit tests for large parts of their
surface (existing tests covered only `/health`, `/email-deliverability`, the
profile-image backfill, and the immediate/scheduled send happy paths).

## 3. Fix / remediation

**Test-only change — no application code was modified.** Added three new
test files:
- `backend/tests/test_admin_legal_documents.py` — list, and PUT upsert
  validation + version-bump semantics (new row → v1; existing row → v+1;
  missing `version` column → coerced to 1 then bumped to 2; `type`/`doc_type`
  alias handling).
- `backend/tests/test_admin_messaging_coverage.py` — `_resolve_recipients`
  (all audience branches incl. service-area filter and particular_* paths),
  `_count_audience`, `_target_app_for_audience`, all three per-channel
  senders (`_send_push_one`/`_send_email_one`/`_send_sms_one`, marketing vs.
  transactional branches), `_fan_out` (success/failure tally, malformed
  recipient rows, stats-write-back failure not propagating, SMS-settings
  load only for transactional SMS), `admin_send_cloud_message` service-area
  plumbing + DB-insert-failure path, audience-preview, list/stats/cancel,
  and marketing-suppression add/remove.
- `backend/tests/test_admin_monitoring_coverage.py` — `build_monitoring_ride`
  shaping (full row + missing rider/driver), `fetch_monitoring_drivers`
  (including the presence-vs-intent offline case), `fetch_monitoring_rides`,
  Redis health (hit-rate calc, flushable-prefix flags), Redis connectivity
  probe (success + exception path), `flush-prefix` (confirm gate,
  allowlist gate, success), WebSocket health, infrastructure snapshot, and
  the `_humanize_bytes_local` helper.

No bugs were found that required fixing during this pass.

## 4. Risk & impact on existing functionality

- **Isolated — test-only change.** No production code in `routes/admin/`,
  `db_supabase.py`, or any shared helper was modified.
- Blast radius of the *tests themselves*: none — they patch module-level
  attributes (`db_supabase.*`, `db.*`, per-sender functions) inside their own
  test functions and don't share mutable global state with other test files.
  `admin_send_cloud_message`/`admin_upsert_legal_document`/monitoring
  endpoints are exercised directly as async functions (not via `TestClient`),
  consistent with the existing pattern in `test_monitoring_health.py` and
  `test_messaging_fan_out.py`.
- No other admin route file was touched (per task constraint — other agents
  are working `routes/admin/*` and `routes/auth.py` in parallel worktrees).

## 5. User-experience effect

None — test-only change, nothing user-facing shipped.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_admin_legal_documents.py` | New file — 10 tests | Cover list + upsert/version-bump logic (0% → covered) |
| `backend/tests/test_admin_messaging_coverage.py` | New file — 48 tests | Cover recipient resolution, per-channel senders, fan-out, management endpoints |
| `backend/tests/test_admin_monitoring_coverage.py` | New file — 19 tests | Cover live-map fetchers, Redis monitoring/flush, WS health, infrastructure snapshot |
| `ACTION_ITEMS.md` | Added 3 sub-bullets under A1b Track 1 item 4 | Record new measured coverage |
| `docs/change-log/2026-07-29-a1b-admin-monitoring-messaging-legal-coverage.md` | New file (this doc) | Mandatory Change Impact Log for a live-tested-adjacent (PIPEDA) surface |

## 7. Before / after

Not applicable — additive test files only, no behavior-changing diff to
existing code.

## 8. Rollback plan

Revert the three new test files (and the `ACTION_ITEMS.md`/change-log
additions) via `git revert` — this is safe here because the change is
test-only and touches no live data, migrations, Stripe charges, wallet
deltas, or ride state. No feature flag or data-level remediation is needed.

## 9. Verification performed

- [x] Automated tests run — unit only (all three new test files run green;
  see coverage numbers below). Ran together with pre-existing
  `test_messaging_fan_out.py`, `test_monitoring_health.py`,
  `test_email_deliverability.py` for the same modules.
- [x] Real pytest-cov output (not fabricated), measured per-file via
  `pytest tests/test_admin_legal_documents.py -q` /
  `pytest tests/test_admin_messaging_coverage.py tests/test_messaging_fan_out.py -q` /
  `pytest tests/test_admin_monitoring_coverage.py tests/test_monitoring_health.py tests/test_email_deliverability.py -q`:
  - `routes/admin/legal_documents.py`: `32 0 100%`
  - `routes/admin/messaging.py`: `255 7 97% (missed: 97, 99, 116, 233, 411-413)`
  - `routes/admin/monitoring.py`: `246 10 96% (missed: 302-306, 609, 614-617, 770)`
- [x] Full backend suite run after the change (`pytest tests/ -q`) to confirm
  zero regressions from the new test files.
- [ ] Manual repro in staging — not applicable, no runtime behavior changed.
- [x] Blast-radius grep performed — searched `backend/tests/` for existing
  references to `admin/monitoring`, `admin/messaging`, `admin/legal`, and
  `legal_document` before writing new tests, to avoid duplicating
  `test_messaging_fan_out.py`, `test_monitoring_health.py`, and
  `test_email_deliverability.py`.
- [x] Reviewed against CLAUDE.md testing conventions: `@pytest.mark.anyio`,
  patch target style matching `backend.db_supabase.supabase` convention
  (module-qualified patches), admin-routes ≥70% target met/exceeded on all
  three files.
- [x] PIPEDA-relevant: `legal_documents.py` tests specifically assert the
  version-bump semantics that back the "material change → re-consent
  required" flow (see CLAUDE.md → Compliance (PIPEDA) → User rights →
  Consent).

## 10. What was NOT verified

- No admin-dashboard frontend build was run — this PR touches only
  `backend/tests/`, no frontend code.
- The remaining uncovered lines in `messaging.py` (97, 99, 116, 233,
  411-413) and `monitoring.py` (302-306, 609, 614-617, 770) are narrow
  exception-log-and-continue branches / platform-specific fallbacks
  (e.g. `sys.platform == "darwin"` RSS-units branch, a `resource` import
  failure fallback) — judged diminishing-returns to chase further for this
  pass; not a functional gap in the tested happy/error paths.
- Not tested against live Supabase or real Redis/Twilio/FCM — all external
  calls are mocked per the existing `mock_supabase_client`/patch-based
  convention in this repo; this is unit-level coverage only, not
  integration coverage.
- No visual/snapshot regression tooling exists for the admin dashboard, but
  this PR has no frontend component, so that gap is not applicable here.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, test-only change)
- [x] Blast radius is stated: isolated, test-only, no other callers affected
- [x] No silent behavior change — no behavior changed at all
