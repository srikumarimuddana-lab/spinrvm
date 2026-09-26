# Change Impact & Risk Log

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code (session), approach chosen by user (option A of A/B/C/D) |
| Surface(s) | backend (fixes a rider-app-facing flow; no rider-app code change) |
| Domain (Sentry tag) | safety |
| PR / commit link | (uncommitted — user commits separately) |
| Related issue | Sentry CRIMSON-SMOKE-7445-12Z / -12Y / -130 (first seen 2026-09-26 02:18 UTC) |

## 1. Issue / gap identified

Every rider safety report failed: the rider app's "Report Safety Issue" screen
(`rider-app/app/report-safety.tsx:36`) posts to `POST /api/v1/tickets/safety-report`,
which returned 503 with PGRST204 "Could not find the 'priority' column of
'support_tickets'". The rider saw a "Submit Failed" toast; the report reached no one.

## 2. Root cause

Two layered defects in `backend/features.py::create_safety_report`:
1. It wrote `"priority": "critical"` into `support_tickets`. No migration has ever
   created that column (verified: production `support_tickets` has id, user_id,
   subject, message, category, status, replies, created_at, updated_at; no
   migration in `backend/migrations/` adds `priority` to it). The admin-side
   `priority` fields belong to `disputes` / `zoho_desk_tickets`, not this table.
2. Even without (1), it only created a general support ticket. It never wrote a
   `safety_incidents` row, never called `notify_safety_team` (admin WS alert,
   email to the safety distribution list, CRITICAL on-call log), and never raised
   the urgent Zoho ticket. The driver app already uses the real pipeline,
   `POST /safety/report` (`driver-app/app/report-safety.tsx:114`); the rider app
   was never moved over. This is the "fixed in one app, never reached the
   sibling" pattern from CLAUDE.md release gate 10.

The line has been present since at least 2026-04-11 (it predates the ruff
cleanup commit `d815d2c7a`), and the production database dates from
2026-06-06, so the endpoint has most likely never worked in production.

## 3. Fix / remediation

`create_safety_report` now delegates to `routes.safety.submit_safety_report`
with `category="Other"` (the driver app's catch-all value) and the rider's
description, via the dual-import pattern (relative import, then the
top-level `except ImportError` branch that production actually runs). Its request
model gains the same `1..4000` length bounds as `routes.safety.SafetyReportRequest`,
so bad input is a clean 422 rather than a 500 from re-validation inside the
delegated call.

Alternatives considered (presented to and decided by the user):
- **B — drop `priority`**: stops the error but files reports into the general
  support queue with no safety-team alert. Rejected: a safety report that nobody
  is paged for is worse than one that visibly fails.
- **C — add a `priority` column**: same outcome as B plus a schema change.
- **D — change the rider app to call `/safety/report`**: the right long-term shape
  (gains location, ride context, photos), but needs a mobile release and installed
  builds would keep hitting the old path. Recommended follow-up, not a substitute.

## 4. Risk & impact on existing functionality

- **Callers of `/tickets/safety-report`**: only `rider-app/app/report-safety.tsx`
  (grep across rider-app, driver-app, shared, admin-dashboard). It awaits the call
  and ignores the response body, so the response shape changing from a ticket
  object to `{"success": true, "incident_id": ...}` is invisible to it.
- **`submit_safety_report` itself is unchanged** — same insert, same notify, same
  Zoho spawn, same 503 on DB failure. It gains one more caller.
- **Safety team load**: rider reports now reach the safety queue, alerts and
  email. That is the intended behaviour; previously there were none because the
  endpoint never succeeded.
- **`support_tickets`**: this handler no longer writes there. `create_ticket`
  (general support) is untouched. Nothing in backend or admin-dashboard reads
  `support_tickets` filtered to `category == "safety"`, so no admin view loses
  rows (confirmed by the safety review below).
- **Rate limiting**: neither `/tickets/safety-report` nor `/safety/report` has a
  per-route limiter decorator; both rely on the global middleware, which the
  outer request still passes through once. Calling `submit_safety_report` as a
  function therefore bypasses nothing either route had.
- **Pre-existing, not changed here**: `routes/safety.py`'s module docstring says a
  "SEV-1 issue (weapon, assault, medical) also broadcasts to on-call admins", but
  neither `submit_safety_report` nor `notify_safety_team` branches on category —
  every report gets the same WS alert + email + CRITICAL log. Rider "Other" reports
  get exactly what driver "Other" reports get today. Worth a separate look.
- **Circular import**: `routes/safety.py` imports `notify_safety_team` from
  `features.py` at module load, so the reverse import is done lazily inside the
  handler. Covered by a test that asserts both import branches bind
  `submit_safety_report` (the same trap that once left `notify_safety_team` unbound
  in production — `test_safety_notify_import.py`).
- **Blast radius**: one handler in `features.py`; no schema, no migration, no
  other route touched.

## 5. User-experience effect

Rider: "Report Safety Issue" now succeeds and shows the existing success toast
("Your safety report has been submitted. Our trust and safety team will review it
immediately."), which is now actually true. Internal admins: rider reports appear
in the Safety incidents queue with category "Other" and role "rider", and trigger
the same live alert, email and Zoho ticket as driver reports. No change for anyone
mid-session; a rider who retries after deploy simply succeeds.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/features.py` | `create_safety_report` delegates to `routes.safety.submit_safety_report`; request model gets `min_length=1, max_length=4000`; `Field` import | Route rider reports into the real safety pipeline; clean 422 on bad input |
| `backend/tests/test_rider_safety_report_pipeline.py` | New (5 tests) | Reproduces the bug (red before the fix), guards the pipeline, the 503 path, input bounds, and both import branches |
| `docs/change-log/2026-09-26-rider-safety-report-pipeline.md` | New | This entry |

## 7. Before / after

```python
# Before
ticket = {..., "category": "safety", "status": "open", "priority": "critical", ...}
await db_supabase.insert_one("support_tickets", ticket)   # PGRST204 every time
return ticket
```

```python
# After
return await submit_safety_report(
    IncidentReportRequest(category="Other", description=req.description),
    current_user,
)   # safety_incidents row + notify_safety_team + urgent Zoho ticket
```

## 8. Rollback plan

Code-only change with no data migration: redeploy the previous backend release
(Fly `fly deploy --image <previous>` / Railway rollback in its dashboard). Rolling
back restores the always-failing behaviour, so there's no data to unwind —
`safety_incidents` rows created while the fix was live are real reports and must
be kept regardless.

## 9. Verification performed

- [x] Reproduced first: the new tests failed against the unfixed code for the
      right reason (the handler returned a support-ticket body, not an incident).
- [x] After the fix: 5/5 new tests; 52 existing safety tests
      (`TestSafety`, `test_safety_notify_import.py`, `test_safety_incident_photos.py`,
      `test_admin_safety_incidents.py`); 247 support/ticket/features tests — all pass.
- [x] `ruff check` / `ruff format --check` clean on both touched Python files.
- [x] Production schema checked directly (read-only query) — confirms no
      `priority` column; no migration ever intended one.
- [x] Impact checked: Sentry shows the error first seen 2026-09-26 02:18 UTC, 2
      attempts only, none earlier in 90 days — no evidence of lost real reports.
- [x] Independent review by `spinr-safety-sos-reviewer` against the actual diff:
      verdict SAFE TO MERGE, no blockers. Confirmed server-side role derivation,
      both import branches, no PII in logs, admin safety queue renders
      category "Other"/role "rider", no rate-limit asymmetry. Its two notes are
      recorded in section 4.

## 10. What was NOT verified

- Not exercised against live Supabase or a staging deploy — mocked
  `db_supabase`/`notify_safety_team` only; the real insert path is the unchanged,
  already-live `submit_safety_report`.
- Not tested from a real rider-app build (no mobile/visual regression tooling
  exists for rider-app); the app-side contract (awaits call, ignores body) was
  read from source, not run.
- Email/Zoho side effects not exercised end to end (they're best-effort,
  fire-and-forget, and unchanged).
