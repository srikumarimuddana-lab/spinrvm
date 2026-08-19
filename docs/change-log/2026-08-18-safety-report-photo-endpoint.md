# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | srikumarimuddana@gmail.com (with Claude Code) |
| Surface(s) | backend / driver-app |
| Domain (Sentry tag) | safety |
| PR / commit link | branch `claude/lost-found-chat-bugs-n4qodp` |
| Related issue or gap ID | Found while adding L&F photo attachments |

## 1. Issue / gap identified

**Every safety-report evidence photo a driver has ever attached was silently
discarded.** `driver-app/app/report-safety.tsx` POSTs each photo to
`/api/v1/safety/report/{incident_id}/photo`. That endpoint **did not exist** —
`routes/safety.py` exposed only `POST /report`. The client wrapped each upload
in `catch {}` commented "Photo upload failure is non-fatal", so the resulting
404 was swallowed and the driver saw "Report Submitted" either way.

Trust & safety has therefore been triaging incidents while believing no
photos were attached, when drivers believed they had attached them.

## 2. Root cause

Two independent faults, both required for the silence:

1. **The route was never implemented.** The client was written against an API
   that did not ship.
2. **`client.post()` could not send multipart anyway.** It unconditionally
   `JSON.stringify`'d its body, so a `FormData` became `"{}"`. That is *why*
   this screen hand-rolled a raw `fetch()` — and that workaround omits the
   `X-Firebase-AppCheck` header, so it would have 401'd under App Check
   enforcement even if the route had existed. (Fixed in the companion L&F
   photo commit on this branch.)

The `catch {}` is what made it invisible. A missing endpoint is an ordinary
bug; a missing endpoint behind a silent catch on a **safety** surface is the
failure this log is really about.

## 3. Fix / remediation

- Migration 335: `safety_incident_photos` table (RLS, service-role only) plus a
  **private** `safety-evidence` bucket.
- `POST /safety/report/{incident_id}/photo` implemented — reporter-only guard,
  4-photo cap matching the client's own picker limit, byte-sniffed content type,
  10 MB cap via the shared `read_upload_capped` helper.
- `GET /admin/safety/incidents/{id}` now returns `photos[]` with short-lived
  signed URLs, so stored evidence is actually reviewable. Without this the
  photos would be write-only.
- `report-safety.tsx` switched from the hand-rolled `fetch()` to `api.post()`
  (correct URL, auth + App Check headers), and **no longer fails silently**: a
  failed photo now produces a "Report Sent — Photos Failed" toast naming how
  many did not attach.

## 4. Risk & impact on existing functionality

**Blast radius:**

- **New table, new bucket, new endpoint** — nothing existing reads or writes
  either. `safety_incidents` is untouched.
- `GET /admin/safety/incidents/{id}` gains one key (`photos`). Grepped the
  admin dashboard: no component destructures this response exhaustively, and an
  added key cannot break a consumer that ignores it. The lookup is wrapped so a
  photos-table failure logs and returns `[]` rather than 500-ing the detail view
  a reviewer needs.
- `report-safety.tsx` is the only caller of the new endpoint. Its report-submit
  path is unchanged — only the photo loop and the success toast differ.
- The `GET /admin/safety/incidents` **list** endpoint is untouched, so the
  triage queue's hot query does not change.
- No ride-state, money, dispatch, auth, or insurance-period path involved. No
  background loop changed.

**Could this regress a working flow?** The photo loop previously always
"succeeded" (it swallowed everything). It now surfaces failures — that is the
point, but it means drivers who hit a genuine upload problem will start seeing a
warning toast they never saw before. That is correct behavior, not a regression,
though it will look like new breakage in support tickets. Worth telling the
safety team before deploy.

## 5. User-experience effect

- **Driver**: photos actually attach. If some fail, they are told which count
  failed instead of being falsely reassured. The report itself still submits —
  a photo failure never blocks filing a safety report.
- **Internal admin (trust & safety)**: incident detail now carries evidence
  photos. Reviewers who have been working without them will start seeing them
  on **new** reports.
- **Visible mid-session?** Yes for a driver filing a report.
- **Copy**: one new toast, "Report Sent — Photos Failed", with a specific count
  and what happens next ("Our team may contact you for them"). Non-technical
  and actionable.
- **Historic photos are NOT recoverable** — they were never uploaded. Nothing
  to backfill.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/335_safety_incident_photos.sql` | new table + private bucket | Somewhere to put evidence |
| `backend/routes/safety.py` | new photo endpoint + upload imports | The route the client always called |
| `backend/routes/admin/safety.py` | `photos[]` w/ signed URLs on incident detail | Otherwise evidence is write-only |
| `backend/tests/test_safety_incident_photos.py` | 8 new tests | Guard rails incl. a route-exists regression test |
| `driver-app/app/report-safety.tsx` | `api.post`, correct URL, failures surfaced | Uploads work and stop being silent |

## 7. Before / after

```tsx
// Before — wrong transport, no App Check header, failure swallowed entirely.
await fetch(`${SpinrConfig.backendUrl}/api/v1/safety/report/${reportId}/photo`, {
  method: 'POST',
  headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  body: fd as any,
});
} catch {
  // Photo upload failure is non-fatal
}
```

```tsx
// After — api.post attaches auth + App Check; failures are counted and shown.
await api.post(`/safety/report/${reportId}/photo`, fd);
} catch (photoErr) {
  failedPhotos += 1;
  console.error('[report-safety] evidence upload failed', photoErr);
}
// ...
if (failedPhotos > 0) showToast('warning', 'Report Sent — Photos Failed', ...);
```

## 8. Rollback plan

- **Backend route + admin detail**: `git revert`. Both are additive; reverting
  stops new uploads and drops the `photos` key. Rows and objects already written
  are orphaned but harmless (nothing else reads them).
- **Driver app**: `git revert` restores the old silent behavior. Note that
  reverting the client *alone* is safe; reverting the **shared `client.post`
  FormData change** (in the companion L&F commit) without also reverting this
  screen would break these uploads, since it now relies on that support.
- **Migration 335**: rollback SQL is in the file header. **Dropping the bucket
  destroys evidence attached to open incidents** — if anything is under review,
  drop the table only and leave the bucket. Flagged in the migration comment too.

## 9. Verification performed

- [x] Automated tests — **8 new** (`test_safety_incident_photos.py`), run via
      `TestClient` so the **real multipart parse executes**: route-exists
      regression guard, successful upload asserting the sniffed content type and
      storage key, 404 unknown incident, 403 non-reporter, photo-cap, empty file,
      non-image, and storage-failure → 502 with no row written.
- [x] Related suites green: safety + admin-safety + L&F = **113 passed**.
- [x] driver-app `jest` **552 passed** (64 suites); rider-app `jest` **527 passed**.
- [x] `tsc --noEmit` and `eslint` clean on driver-app; `ruff check` clean on all
      three backend files.
- [x] **Real production build run** — `npx expo export --platform android`
      succeeded for driver-app.
- [x] Blast-radius grep — `safety_incidents` readers, `report-safety.tsx`
      callers, admin dashboard consumers of the incident-detail response,
      existing storage bucket names.
- [x] Reviewed against CLAUDE.md — "do not silently swallow errors" (this change
      exists to undo one); no PII in logs (no filename, no description logged —
      only incident id and byte count); private bucket; RLS on the new table.
- [ ] Feature-flagged — **not** flagged. Additive endpoint + additive response
      key; rollback is a clean revert.

## 10. What was NOT verified

- **Nothing ran against live Supabase.** Storage is mocked. **The bucket does
  not exist until migration 335 is applied — until then every upload returns
  502.** Apply the migration before deploying the client change, or drivers
  trade a silent failure for a visible one.
- **No admin-dashboard UI renders `photos[]` yet.** The API returns them; the
  React admin page does not display them. A reviewer will not see evidence in
  the browser until that UI is built — **this change alone does not close the
  loop for trust & safety.** I did not build it because it is a separate surface
  and was not part of the ask. This is the most important open gap here.
- **No backfill is possible** — the historic photos were never uploaded
  anywhere. Any incident filed before this deploy has no evidence to recover.
- **The 4-photo cap is not race-safe.** Two concurrent uploads could both read
  3 existing and both insert, yielding 5. Low impact (a cap overshoot on a
  low-frequency path), and deliberately not solved with a constraint here.
- **HEIC is rejected, not converted** — same limitation as the L&F path; not
  tested on a real iOS device.
- **No retention/purge for the `safety-evidence` bucket.** Rows cascade-delete
  with their incident, but the storage objects do not. Given safety evidence has
  a regulatory retention dimension, the retention rule should be decided
  deliberately rather than defaulted.
- **Not tested end-to-end from a real device** — the driver-side flow was
  verified by unit tests and a production bundle build, not by filing an actual
  report on hardware.
