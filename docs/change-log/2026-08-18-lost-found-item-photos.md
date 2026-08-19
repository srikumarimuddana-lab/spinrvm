# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | srikumarimuddana@gmail.com (with Claude Code) |
| Surface(s) | backend / rider-app / driver-app / shared |
| Domain (Sentry tag) | rides (Lost & Found) |
| PR / commit link | branch `claude/lost-found-chat-bugs-n4qodp` |
| Related issue or gap ID | Live-testing report: driver cannot send a photo of a found item |

## 1. Issue / gap identified

A driver who finds an item cannot send a picture of it — not in the Lost & Found
chat, and not through support. A photo is the single most useful piece of
evidence for a rider identifying their property ("is that my black case or
someone else's?"), and without it the thread degrades into text description.

## 2. Root cause

Not a bug — **the capability was never built**. `lost_and_found_messages` had
columns for `message` text only; there was no attachment column, no storage
bucket, and no upload endpoint. The chat UIs had a text input and nothing else.

Two adjacent findings surfaced while tracing it:

- **`client.post()` in the shared API client silently broke multipart uploads.**
  It unconditionally `JSON.stringify`'d the body, so a `FormData` became `"{}"`
  and the file never left the device. `client.put()` already had correct
  FormData handling; `post` did not. This is why `report-safety.tsx` hand-rolls
  a raw `fetch()` — and that workaround omits the App Check header, so it 401s
  under enforcement.
- **`POST /safety/report/{id}/photo` does not exist in the backend.**
  `routes/safety.py` exposes only `POST /report`. The driver safety-report
  screen posts photos to that missing route inside a `catch {}` commented
  "Photo upload failure is non-fatal", so safety evidence photos have been
  silently discarded. **Not fixed here** — different surface, flagged below.

## 3. Fix / remediation

- Migration 339 adds `image_key` / `image_mime` to `lost_and_found_messages`
  and provisions a **private** `lost-and-found` storage bucket.
- New `POST /lost-and-found/{case_id}/messages/image` accepts a multipart
  image, runs the same participant and closed-case guards as the text endpoint,
  stores the object, and inserts an image message.
- `GET /{case_id}/messages` mints a short-lived signed URL per attachment
  (concurrently, not in a loop). The DB stores the **key**, never a URL.
- Both chat screens gain a camera button (camera / library), an optimistic
  upload bubble with a spinner, inline image bubbles, and a tap-to-enlarge
  full-screen preview.
- `client.post()` now mirrors `client.put()`'s FormData handling.
- The text and image endpoints now share one `_notify_counterparty()` helper so
  they cannot drift on who gets pushed.

Also repaired in passing: the dual-import fallback branch in
`routes/lost_and_found.py` had lost `DuplicateRecordError` in my previous commit
(`3ebd031`), which would raise `NameError` in the duplicate-case `except` clause
when the module loads via the top-level import path.

## 4. Risk & impact on existing functionality

**Blast radius — stated, not assumed:**

- **`client.post()` is the highest-risk edit** — it is used by essentially every
  screen in both apps. The change is guarded by
  `body instanceof FormData`, which is false for every existing caller, so all
  current JSON call sites take a byte-identical path. Verified by both full app
  suites (533 + 524 tests) and both production bundles.
- `lost_and_found_messages` is read by the two chat screens and written by this
  router only. Grepped: no admin screen, export, or retention job selects its
  columns by name, so the two added columns break nothing.
- Migration 339 adds a CHECK constraint (`text OR image present`). Existing rows
  all have non-empty `message`, so it validates clean — **but see "not verified"**.
- New bucket, so no existing object namespace is touched.
- No ride-state-machine, money, dispatch, auth, or insurance-period path is
  involved. No background loop changed.

**Could this regress a working flow?** The `list_messages` response grows an
`image_url` field; both clients treat it as optional. The added signing work is
skipped entirely for text-only threads (`targets` is empty → no storage calls).

## 5. User-experience effect

- **Driver**: a camera button next to the chat input; can photograph a found
  item and send it. Uploads show a spinner over the thumbnail; a failure removes
  the optimistic bubble and toasts, rather than leaving a photo that looks sent.
- **Rider**: the same, plus can now see the driver's photo — the actual point of
  the feature.
- **Visible mid-session?** Yes, to anyone with an open case. Additive: no
  existing control moves or changes meaning.
- **Copy**: new strings are inline English on both screens, matching the
  surrounding un-translated copy in these two files. The driver app's i18n
  bundle is not used by this screen. Accessibility labels added on the attach
  button, image bubbles, and preview close target.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/339_lost_and_found_message_images.sql` | image columns, CHECK, private bucket | Somewhere to put the photo |
| `backend/routes/lost_and_found.py` | upload endpoint, signed-URL read, shared notify helper, import repair | The feature + fixing my earlier import regression |
| `backend/tests/test_lost_and_found_route_coverage.py` | 7 new tests | Guard rails on a new upload path |
| `shared/api/client.ts` | FormData support in `post()` | Multipart POSTs actually send the file |
| `driver-app/app/driver/lost-and-found-chat.tsx` | attach/upload/render/preview | Driver can send the photo |
| `rider-app/app/lost-and-found-chat.tsx` | attach/upload/render/preview | Rider can send and see it |

## 7. Before / after

```ts
// Before — a FormData body became the string "{}"; the file never left.
const response = await fetchWithTimeout(url, {
  method: 'POST', headers, body: body ? JSON.stringify(body) : undefined,
});

// After — mirrors put(): boundary set by fetch, App Check header retained.
const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
if (isFormData) delete headers['Content-Type'];
const response = await fetchWithTimeout(url, {
  method: 'POST', headers,
  body: isFormData ? (body as FormData) : (body ? JSON.stringify(body) : undefined),
});
```

```python
# Before — messages were text-only.
return {"messages": list(reversed(rows))}

# After — attachments get a fresh signed URL per read (keys are stored, not URLs).
return {"messages": await _sign_message_images(list(reversed(rows)))}
```

## 8. Rollback plan

- **Frontend + shared client**: `git revert`. No persisted state, no flag. The
  `post()` change is inert for non-FormData callers, so reverting it cannot
  strand data.
- **Backend route**: `git revert`. The endpoint only adds rows; reverting stops
  new uploads and leaves existing image messages rendering as empty-text
  bubbles (the client already tolerates a missing `image_url`).
- **Migration 339**: rollback SQL is in the file header — drop the two columns,
  the CHECK constraint, and the bucket. **Order matters**: drop the CHECK
  constraint before the columns. Objects already uploaded are deleted by the
  `DELETE FROM storage.objects` line; that is real data loss, so if any case is
  mid-return, drop only the constraint and leave the columns in place.

## 9. Verification performed

- [x] Automated tests — backend `pytest` **43 passed** (7 new: participant
      guard, closed case, empty file, storage failure → 502 with no row written,
      successful upload asserting `image_key`/`image_mime`/empty message/push,
      and two `_sign_message_images` cases). Route coverage **89%**.
- [x] driver-app `jest` **533 passed** (63 suites); rider-app `jest` **524 passed**
      (61 suites).
- [x] `tsc --noEmit` clean on both apps; `eslint` clean on both changed screens;
      `ruff check` clean on the backend route and tests.
- [x] **Real production build run** — `npx expo export --platform android`
      succeeded for driver-app (8.8 MB) and rider-app (8.6 MB). Not a dev server.
- [x] Blast-radius grep — `client.post` callers, `lost_and_found_messages`
      readers, storage bucket names, `expo-image-picker` availability in both
      apps, and existing upload endpoints.
- [x] Reviewed against CLAUDE.md — private bucket + no PII in logs (the original
      filename is never stored or logged); "do not silently swallow errors" (a
      failed upload raises 502 rather than reporting a sent message); N+1
      avoided in the signing path.
- [ ] Feature-flagged — **not** flagged. Justification: purely additive UI and a
      new endpoint; no existing behavior changes. Rollback is a clean revert.

## 10. What was NOT verified

- **Nothing was run against live Supabase.** Storage calls are mocked in tests.
  The bucket does not exist until Migration 339 is applied — **until then the
  upload endpoint will fail with "Bucket not found"**. Migration first, then
  deploy; this is exactly the drift that migration 202 was written to fix.
- **Migration 339 has not been applied anywhere.** The CHECK constraint assumes
  no existing row has an empty/whitespace `message`. That is true of every code
  path I read (both writers trim and the API enforces `min_length=1`), but it
  was **not** verified against production data. If such a row exists the
  migration fails on apply.
- **HEIC is rejected, not converted.** iPhone photos sent as HEIC get an
  actionable 400 from `_resolve_upload_type`. `expo-image-picker` re-encodes to
  JPEG in the common path, so this should be rare — but it is not handled, and
  I did not test on a real iOS device.
- **No end-to-end test of the actual multipart round-trip.** The tests call the
  handler directly with a stub upload; FastAPI's multipart parsing and the RN
  `FormData` file shape are not exercised together anywhere.
- **No visual regression tooling exists for these surfaces** (standing gap), so
  the image bubble, spinner overlay, and full-screen preview were reasoned about
  and unit-tested by behavior, not screenshotted.
- **No retention/purge step for the new bucket.** Case rows are covered by the
  existing retention job; the storage objects are not. Uploaded photos will
  persist after their case is purged. This should be added before the feature
  sees real volume — see the follow-up note below.

## 11. Follow-ups this change did NOT cover

1. ~~**`POST /safety/report/{id}/photo` is missing from the backend**~~ —
   **DONE**, see `2026-08-18-safety-report-photo-endpoint.md`.
2. ~~**`report-safety.tsx` should move to `api.post()`**~~ — **DONE**, same log.
3. **Storage purge for the `lost-and-found` bucket** (see above). Still open —
   and the `safety-evidence` bucket added later has the same gap.
