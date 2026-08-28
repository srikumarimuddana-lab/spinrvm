# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (session on `staging`) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | fix on `staging`; regression introduced by `258ded5` (same day) |
| Related issue or gap ID | Reported error: `column driver_documents.expires_at does not exist` |

## 1. Issue / gap identified

The 12-hourly `document_expiry` background loop raised
`column driver_documents.expires_at does not exist` on **every tick on every replica**,
so its fleet-wide `driver_documents` fetch never returned a row. The loop caught the
error and fell open to legacy-fields-only checking, so it kept reporting success while
silently performing none of its per-document expiry checks.

## 2. Root cause

`258ded5` ("perf(loops): kill the two background-loop N+1s") replaced a per-driver
unprojected `SELECT *` with one fleet-wide **projected** query. The projection was
written by copying the field names the code below it reads:

`driver_id,expiry_date,expires_at,requirement_name,type`

Three of those five are not columns of `driver_documents`. They were dead `.get()`
fallbacks that were harmless only while the query was `SELECT *` — a missing key just
returned `None`. Naming them in a projection is different: PostgREST validates every
name in the select list and rejects the **whole** query on the first unknown one, which
is why the error names `expires_at` (the first bogus name) and not the other two.

Real columns (verified against production `information_schema`): `id`, `driver_id`,
`document_type`, `document_url`, `status`, `rejection_reason`, `uploaded_at`,
`updated_at`, `requirement_id`, `side`, `requirement_key`, `expiry_date`.

Why CI stayed green: the tests mock `db.get_rows` with a function that **ignores the
`columns` kwarg**, and their fixtures assert on an invented `requirement_name` key. The
mock happily returns a row that the real database would never produce.

Second, older bug found in the same lines: `doc_name` read
`requirement_name or type` — neither has ever been a column — so every document expiry
notice said the generic "Document", including before this regression.

## 3. Fix / remediation

Project the columns that actually exist, and derive the document's display name from
`document_type` (human label, e.g. "Driver's License") falling back to `requirement_key`
(slug, e.g. `drivers_license`). Dropped the dead `expires_at` read at the same call site.
Added a regression test that parses the projection string and fails on any name outside
the real column set — the check the existing mocks cannot perform.

## 4. Risk & impact on existing functionality

**Blast radius: isolated — one call site.** Grep for every `driver_documents` reader:

- `utils/document_expiry.py:148` — the only broken projection. Fixed here.
- `utils/driver_onboarding_reminders.py:376` — the only *other* explicit projection
  (`id,driver_id,requirement_id,requirement_key,document_type,status`); all six are real
  columns, unaffected.
- `routes/admin/documents.py:607` and `routes/drivers/_shared.py:846` — the **real-time
  eligibility gates** (go-online check and ride-accept check). Both fetch with no
  `columns=`, i.e. `SELECT *`, so they were never broken by this. Their identical
  `expiry_date or expires_at` fallbacks are equally dead but harmless; left untouched as
  pre-existing and out of scope.
- All other readers (`admin/drivers.py`, `admin/sgi_forms.py`, `users.py`) are
  unprojected or write-side. Unaffected.

**What was actually lost while broken:** only the *proactive* sweep — advance expiry
warnings and the 12h auto-suspension. Because the two real-time gates above kept working
on `SELECT *`, a driver with expired documents was still blocked at go-online and at
ride-accept. The regulatory gate held; the early-warning layer did not.

**Who was invisible to the sweep:** document types with no legacy
`drivers.*_expiry_date` counterpart — Vehicle Registration and Driver Abstract — had
*no* fallback coverage at all, since legacy-fields-only checking cannot see them. This is
exactly the gap migration 91 was created to close.

Live data as of this change (latest approved doc per driver/requirement):
39 expired, 26 expiring within 7 days. **Zero belong to a driver whose status is
`active`** — so no active driver was online with an expired document during the window.
The 26 expiring-soon drivers did not receive their warning notifications.

No ride-state, money, wallet, or insurance-period path is touched. The loop's fail-open
posture, pagination, suspension CAS claim, and presence-clearing are all unchanged.

## 5. User-experience effect

**Driver-facing, and it resumes a notification that had stopped firing.**

- Drivers with a document expiring within 7 days start receiving warning pushes/emails
  again. Some will now get a notice for a document that expired days ago, because the
  loop is seeing these rows for the first time — expected catch-up, not a new bug.
- Drivers with an already-expired document become subject to the 12h auto-suspension
  again. They were already unable to go online or accept rides, so this changes *when*
  they are told, not whether they can drive.
- **Copy change:** notices now name the document ("Your Vehicle Registration has
  expired") instead of the generic "Document". More specific and more actionable —
  consistent with the customer-centric tone standard.
- Not visible mid-ride. The sweep only suspends drivers, and a suspension cannot
  interrupt an `in_progress` ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/document_expiry.py` | Projection now names only real columns; `doc_name` reads `document_type`/`requirement_key`; dropped dead `expires_at` read; comment recording the PostgREST constraint | Fixes the query that failed every tick, and the always-generic document name |
| `backend/tests/test_document_expiry_coverage.py` | Fixtures use `document_type` instead of the non-existent `requirement_name`; 3 new tests (projection-vs-real-schema, name-in-notification, slug fallback) | The old fixtures asserted on a key the database cannot return, which is how this shipped green |

## 7. Before / after

```python
# Before — 3 of 5 names are not columns; PostgREST rejects the whole query
columns="driver_id,expiry_date,expires_at,requirement_name,type",
...
doc_name = doc.get("requirement_name") or doc.get("type") or "Document"
```

```python
# After — every name is a real column; label falls back to the slug
columns="driver_id,expiry_date,document_type,requirement_key",
...
doc_name = doc.get("document_type") or doc.get("requirement_key") or "Document"
```

## 8. Rollback plan

`git revert` is a complete rollback here. The change is read-path only: no migration, no
schema change, no backfill, and no write to live data whose shape differs from before.
Reverting restores the previous (broken) behaviour — the loop fails open and skips
document checks — which is degraded but not corrupt, and the real-time go-online and
ride-accept gates continue to block expired-document drivers either way.

The one live-data effect to be aware of when reverting: drivers auto-suspended by the
restored sweep stay suspended (`drivers.status = 'suspended'`), since a code revert does
not undo those writes. They are cleared the normal way — admin re-approval of a fresh
document — and every suspension is logged with the document that caused it.

## 9. Verification performed

- [x] **Reproduced the reported error against the real schema.** Built a harness that
      loads the real `check_expiring_documents` and validates the select list the way
      PostgREST does. Pre-fix it raises verbatim
      `column driver_documents.expires_at does not exist` and sends zero notifications;
      post-fix both scenarios pass (expired Vehicle Registration names the document; a
      row with `document_type = NULL` falls back to `drivers_abstract`).
- [x] Production schema confirmed via `information_schema.columns` on
      `soavhtdhefowwvforzwb` — `expires_at`, `requirement_name`, `type` do not exist.
- [x] New projection confirmed to execute against production (read-only `SELECT`).
- [x] Blast-radius grep: every `driver_documents` reference under `backend/`, and every
      `columns=` on that table (listed in §4).
- [x] `ruff check` clean on both files; both compile.
- [x] Reviewed against `CLAUDE.md`: query-filter/projection rules, do-not-swallow-errors
      (fail-open posture deliberately unchanged), background-loop replay safety
      (suspension CAS untouched), observability (log levels untouched).
- [ ] Not feature-flagged — this restores intended behaviour of an existing loop rather
      than adding new UX, and flagging it would mean shipping a known-broken regulatory
      warning path for longer.

### What was NOT verified

- **The repository's own pytest suite was not run.** This container has no backend
  dependencies installed and cannot install them: PyPI and `files.pythonhosted.org`
  return **403** under the environment's network policy, so
  `pip install -r backend/requirements.txt` fails. `pytest` itself was recoverable from
  the local `uv` cache, but `fastapi`/`supabase`/`pytest-asyncio` were not, and
  `backend/tests/conftest.py` cannot import without them. The three tests added here are
  therefore **verified by construction and by the equivalent harness, not by a pytest
  run** — they must be run in CI before merge.
- The harness stubs `db`, `features`, `socket_manager`, and the email/metrics helpers.
  Real Supabase, real push delivery, and real email rendering were not exercised.
- No staging run of the 12h loop; the catch-up notification burst described in §5 is
  reasoned from the live row counts, not observed.
- Backend-only change, so the repo's visual-regression gap does not apply.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
