# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | vikas@ngitservices.com (via Claude Code) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (see PR — filled after push) |
| Related issue or gap ID | ACTION_ITEMS.md B14, sub-item (2): "make licence-number/class entry a required part of the admin document-review 'approve' action going forward" |

## 1. Issue / gap identified

An admin could approve a driver's uploaded licence-document photo through
the document-review flow while the driver's structured `license_number`/
`license_class` columns stayed `NULL` forever. Nothing required those
fields at that approval step, and nothing surfaced the gap — 22 of 209
drivers accumulated this way (some already `is_verified: true`), forcing a
manual backfill (see B14's other sub-items, already closed/tooled). This
PR closes only the "gap can grow" half: going forward, that specific
approval can no longer create a new instance of the same gap.

## 2. Root cause

`license_number`/`license_class` are optional self-serve profile fields,
never required at signup nor checked at document-review approval time.
The driver's licence photo is never OCR'd to auto-populate the structured
columns, so an admin has to retype them manually — and the approve action
for the licence document itself never checked whether that retyping had
happened. Approving the *document* and populating the *driver row's
structured fields* were two unrelated actions that looked related.

## 3. Fix / remediation

`POST /api/admin/documents/{document_id}/review`
(`backend/routes/admin/documents.py::admin_review_driver_document`) — the
endpoint behind the `DocumentReviewer` component's Approve button (used
both from the main Drivers screen and from the
`/dashboard/driver-license-backfill` queue) — now checks, only when
`status == "approved"` and the document being approved matches the same
licence/driving/permit keyword heuristic the endpoint already uses to
propagate licence expiry to the legacy `drivers.license_expiry_date`
column (`_legacy_expiry_field_for_requirement`), whether the driver row
already has both `license_number` and `license_class` populated. If
either is missing, the request is rejected with `422` **before any
database write** (no partial state — the document stays `pending`).
Approving any other document type, or approving a licence document for a
driver who already has both fields on file (verified: 187 of 209
drivers), is completely unaffected — same code path, same response, same
timing as before this change.

No UI code was changed. The admin-dashboard's shared fetch client
(`admin-dashboard/src/lib/api/client.ts`) already surfaces a non-2xx
response's `detail` field verbatim as the thrown `Error`'s message, and
`document-reviewer.tsx`'s existing `submit()` catch block already shows
that message in a destructive toast ("Approval failed: <detail>"). A
client-side pre-check was considered (disable the Approve button when the
selected driver is known to be missing licence data) but would require
threading `license_number`/`license_class` as new props into
`DocumentReviewer` from all three of its callers (`drivers/page.tsx`,
`drivers/queue/page.tsx`, `driver-license-backfill/page.tsx`) — a larger,
multi-file UI change for a case that the backend already rejects cleanly
with an actionable message the instant an admin tries it. Judged
out-of-scope for this PR; if desired later it's a natural small follow-up,
not a correctness gap today (the backend is authoritative either way).

## 4. Risk & impact on existing functionality

**Blast radius — grepped for every other caller:**
- Backend: only two call sites of the route function found —
  `backend/tests/test_admin_document_approval_notify.py` and
  `backend/tests/test_n10_admin_push_target_app.py` (both pre-existing
  tests, neither approves a licence-keyword document, so neither is
  affected — verified by running them, see §9).
- Frontend: exactly two callers of `reviewDocument()`
  (`admin-dashboard/src/lib/api/driver-documents.ts`) —
  `document-reviewer.tsx`'s `submit()` (the reviewer modal, opened from
  both the main drivers table and the backfill queue page) and
  `drivers/page.tsx`'s `handleReviewDoc()` (an inline quick-approve path
  in the driver detail slideout). Both route through the same backend
  endpoint, so both get the gate automatically; no separate frontend
  change needed for either.
- A separate endpoint, `POST /api/admin/documents/upload`
  (`admin_upload_driver_document`), can also commit a document straight to
  `approved` when an admin uploads on a driver's behalf (used by
  `document-upload-dialog.tsx`, a different UI flow, not the "review"
  action this task scoped). **Deliberately left ungated** — the task was
  scoped to the document-*review* approve action specifically, and gating
  the upload path too would be a second, separately-reviewable change
  with its own blast radius (a driver's very first licence-photo upload,
  during onboarding, cannot yet have licence data on file — gating that
  path the same way would need different logic, not a copy-paste of this
  gate). Flagging this as a residual, intentionally out-of-scope gap in
  case a future admin workflow relies on it to also commit `approved`
  licence documents.
- Same-table readers of `drivers.license_number`/`license_class`: the
  driver profile response decrypt path (`routes/drivers/_shared.py`), the
  SGI form filler (`sgi_form_filler.py`), the `missing_license` admin
  filter (`routes/admin/drivers.py`), and the driver-edit encrypting
  update path fixed earlier in B14. None of those are modified here —
  this PR only adds a read-only precondition check before a write that
  was already happening.
- No ride state, dispatch, payment, or background-loop code touched.

**Could this regress a flow that currently works?** Only in the narrow,
intended sense: an admin approving a driver's licence *document* for a
driver who is **also** missing the structured licence *fields* will now
see a 422 where they previously saw success. That combination is exactly
the gap this PR exists to close — for the other 187 drivers (and every
other document type, for all 209), behavior is byte-for-byte identical to
before.

## 5. User-experience effect

- **Internal-admin facing only.** No rider, driver, or corporate-admin
  surface changes.
- Visible **mid-session** to an admin working the document queue: if they
  attempt to approve a licence document for a driver still missing
  `license_number`/`license_class`, the Approve action now fails with a
  toast reading "Approval failed: Driver's licence number/class must be
  on file before approval — use the driver-license-backfill queue or edit
  the driver record first." — actionable, points at the exact two
  remediation paths that already exist.
- For every other approval (the overwhelming majority), there is no
  observable change at all.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/documents.py` | Added a 422 pre-write gate in `admin_review_driver_document` blocking approval of a licence-keyword document when the driver row is missing `license_number`/`license_class`; hoisted the existing `req_name` derivation earlier in the function (before the DB write) so both the new gate and the pre-existing expiry-propagation block can share one lookup instead of querying `document_requirements` twice | Close ACTION_ITEMS.md B14 sub-item (2) — stop the licence-data gap from growing |
| `backend/tests/test_admin_document_review_license_gate.py` | New file: 5 tests covering the blocked path (missing number, missing class-only), the allowed path (data present), non-licence documents unaffected, and rejections unaffected | Regression coverage per repo testing conventions |
| `ACTION_ITEMS.md` | Marked B14 sub-item (2) done with a dated note; left the 22-driver manual backfill and the OCR proposal exactly as still-open/blocked | Keep the backlog accurate without touching unrelated, still-genuinely-blocked history |
| `docs/change-log/2026-08-18-b14-require-license-at-approve.md` | This file | Mandatory Change Impact Log for a live-tested admin-workflow change |

## 7. Before / after

```python
# Before (backend/routes/admin/documents.py, admin_review_driver_document)
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("driver_documents", {"id": document_id}, limit=1)
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    # Parse incoming expiry (accept ISO string or None).
    new_expiry_iso: Optional[str] = None
    ...
    # (approval always proceeded straight to the DB write, regardless of
    #  whether the driver had license_number/license_class on file)
```

```python
# After
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("driver_documents", {"id": document_id}, limit=1)
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    # req_name derived here now (hoisted from later in the function)
    req_name: Optional[str] = None
    if status == "approved":
        ...  # same lookup as before, just earlier

    if status == "approved" and _legacy_expiry_field_for_requirement(req_name) == "license_expiry_date":
        driver_id_for_gate = existing.get("driver_id")
        driver_for_gate = await db_supabase.get_driver_by_id(driver_id_for_gate) if driver_id_for_gate else None
        if (
            not driver_for_gate
            or not driver_for_gate.get("license_number")
            or not driver_for_gate.get("license_class")
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Driver's licence number/class must be on file before approval — "
                    "use the driver-license-backfill queue or edit the driver record first."
                ),
            )

    # Parse incoming expiry (accept ISO string or None).
    new_expiry_iso: Optional[str] = None
    ...
```

## 8. Rollback plan

No migration, no data write, no feature flag introduced — this is a pure
read-then-conditionally-reject code path with zero effect on stored data.
**Rollback is a plain `git revert` of this PR's commit** (acceptable here
per the repo's own rule, since nothing here writes to live data — it only
adds a precondition on an existing write path; there is no Stripe charge,
wallet delta, or ride-state transition anywhere in this diff). Reverting
restores the exact prior behavior (approval always succeeds regardless of
licence-data state) with no cleanup needed on either side.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_admin_document_review_license_gate.py backend/tests/test_admin_document_approval_notify.py backend/tests/test_n10_admin_push_target_app.py -q` — 18 passed (5 new + 13 pre-existing in the two files sharing this endpoint), 0 failed. Global coverage-gate failure in the same run is expected/pre-existing when running a narrow subset of the suite (not specific to this change).
- [x] `ruff check backend/routes/admin/documents.py backend/tests/test_admin_document_review_license_gate.py` — all checks passed.
- [x] `ruff format --check` on the same two files — already formatted.
- [x] Blast-radius grep performed: `reviewDocument(` call sites (admin-dashboard), `admin_review_driver_document` call sites (backend, tests only), `adminUploadDriverDocument`/`admin_upload_driver_document` (the separate, deliberately-ungated upload path) — see §4.
- [x] Reviewed against relevant CLAUDE.md conventions: "Do not silently swallow errors" (this *adds* an explicit, loud 422 rather than swallowing anything); "Additive over destructive" (no existing column/behavior repurposed — purely a new precondition on one already-narrow write path); dual-import pattern untouched.
- [x] Manual reasoning through both branches (license-keyword doc / non-license doc; missing vs present license data) via the new unit tests, which exercise the real function with a mocked Supabase client (`mock_supabase_client`-style patching, per repo convention) rather than hitting a real DB.
- [ ] Not exercised against staging or a real Supabase instance — see "What was NOT verified" below.
- [x] Feature-flagged: **not flagged**, and that's a deliberate call, not an oversight — this is a narrow, backend-only, additive precondition on an internal-admin action with a `git revert` rollback path (see §8), not a rider/driver-facing or money-moving change; the repo's flagging guidance is aimed at exactly those higher-blast-radius cases.

## What was NOT verified

- Not tested against a real Supabase/Postgres instance — only against the
  mocked `db_supabase`/`get_driver_by_id` functions in the new unit tests,
  per this repo's existing pattern for this same file
  (`test_admin_document_approval_notify.py`,
  `test_n10_admin_push_target_app.py` use the identical mocking style).
- No admin-dashboard files were modified, so `tsc --noEmit` / `yarn build`
  were not run for this PR — there is no frontend diff to validate. (If a
  future PR adds the proactive client-side pre-check discussed in §3,
  that PR must run a real production build per CLAUDE.md, not just a dev
  server.)
- Not exercised in a staging environment against real driver rows; only
  reasoned about and unit-tested against realistic mock driver dicts
  mirroring the actual `drivers` table shape (`license_number` as an
  opaque Vault-ciphertext-shaped string, `license_class` as a plain short
  code, matching how `routes/drivers/_shared.py` treats these two
  columns).
- The separate `POST /api/admin/documents/upload` admin-upload-straight-
  to-approved path was identified but deliberately left out of scope (see
  §4) — not verified either way against this gate, because it isn't
  gated.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; no data
      mutation to unwind).
- [x] Blast radius is stated, not assumed (two backend test callers, two
      frontend callers, one deliberately-unaffected sibling endpoint, all
      named).
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in — §5 states plainly that this narrows a
      previously-always-succeeding admin action in one specific,
      previously-broken case.
