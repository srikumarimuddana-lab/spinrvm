# Change Impact & Risk Log — driver signup document upload "unsupported format"

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-16 |
| Author | Claude Code (session: driver-signup-upload-format) |
| Surface(s) | backend, driver-app, rider-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/driver-signup-upload-format-f0hdxa` |
| Related issue or gap ID | live-testing report: "upload failed, unsupported format" during driver signup |

## 1. Issue / gap identified

Drivers signing up could not upload their identity documents: picking a photo from
the gallery, or a PDF from the file picker, returned "Upload failed" with an
unsupported-format error. Reported from live app testing. Only one path actually
worked — take-a-photo-with-the-camera, which happens to produce a JPEG that matches
what the app claimed it was sending.

## 2. Root cause

`POST /api/v1/upload` had two gates, and both trusted client-supplied metadata that
the mobile pickers do not report reliably:

1. `_validate_file_type` rejected the upload when the magic bytes identified a
   different type than the declared `Content-Type`.
2. An `ALLOWED_EXTENSIONS` check rejected the upload based on the extension of the
   client-supplied filename.

Neither input is trustworthy on mobile:

- `expo-image-picker`'s `asset.type` is the media **category** (`'image' | 'video'`),
  never a MIME type. `driver-app/app/become-driver.tsx` therefore hardcoded
  `'image/jpeg'` for every asset — so a gallery PNG or GIF tripped gate 1.
- An iPhone gallery asset keeps its original `IMG_0001.HEIC` filename even after
  expo-image-picker has re-encoded the bytes to JPEG (it does whenever `quality` is
  set, and it is set to 0.8). The bytes were a valid JPEG; the `.heic` filename
  tripped gate 2.
- Android's document picker frequently supplies a display name with **no** extension
  (`ext == ""` → gate 2) or `application/octet-stream` as the type (gate 1).
- `pickFile` used `asset.mimeType || 'image/jpeg'`, so a PDF whose mimeType the picker
  omitted was declared as a JPEG and tripped gate 1.

Reproduced against the pre-fix logic across the 11 combinations the pickers actually
produce: **7 of 11 were rejected**, including every gallery pick that was not a JPEG.

A second, smaller defect: `save_upload` (used by `POST /drivers/documents/upload` and
the admin manual upload) raised its `HTTPException(400)` *inside* a
`try: ... except Exception:` that re-raised as `HTTPException(500, "Could not save
file: ...")`, so an unsupported format surfaced to the client as a 500.

## 3. Fix / remediation

The file's own header bytes are now authoritative. `_resolve_upload_type` sniffs the
content and returns both the MIME type to store it under and the extension to store
it with; the client's declared type is consulted only when the header is
unrecognised, and the client's filename is not consulted at all.

HEIF/HEIC is now positively detected and rejected with an actionable message
("take the photo with the in-app camera, or set Settings → Camera → Formats → Most
Compatible"). It is **not** accepted: no browser renders HEIF, so storing one would
break admin document review silently at review time instead of loudly at upload time.

Client-side, `resolveUploadMimeType` in `shared/api/upload.ts` derives the declared
type from the file extension and ignores non-MIME picker values (`'image'`,
`'application/octet-stream'`). This is defence in depth — the backend no longer
depends on it — but it keeps the declared type correct for the fallback path.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, but narrow.** Grepped every consumer of each changed
symbol.

`_resolve_upload_type` (new) is called from exactly two places, both changed here:

| Caller | Route | Who uses it |
|---|---|---|
| `documents.upload_file` | `POST /api/v1/upload` | driver-app signup + documents screen, rider-app "become a driver" |
| `documents.save_upload` | `POST /drivers/documents/upload`, `POST /api/admin/documents/upload` | driver-app `documentStore.uploadDocument`, admin manual upload on behalf of a driver |

`_validate_file_type` is **unchanged** and still used by the callers whose declared
content-type *is* trustworthy — grepped for every one:

- `backend/routes/admin/vehicle_fleet.py:263,333` — admin illustration upload from a
  browser; has its own `_ILLUSTRATION_MIME_TYPES` allowlist on top.
- `backend/services/data_transfer/bundle_document_uploader.py:104` — bundle replay,
  which derives the content-type from a server-written manifest extension via its own
  `_EXT_TO_MIME_TYPE` map.

`ALLOWED_EXTENSIONS` is no longer read by either upload endpoint but is still imported
by `bundle_document_uploader.py:99` — left in place with a comment saying so, so it is
not deleted as dead code.

`shared/api/upload.ts::uploadFile` importers, all checked:
`driver-app/app/become-driver.tsx`, `rider-app/app/become-driver.tsx`. The new export
is additive; `uploadFile`'s signature is unchanged. `frontend/api/upload.ts` is a
separate legacy copy in the unused `frontend/` surface and was left alone.
`driver-app/app/documents.tsx` has its own local `getMimeFromUri` (same idea, narrower)
and was **not** changed — the backend fix already covers every case it was failing on.
The duplication between it and `resolveUploadMimeType` is a known, deliberate
leave-alone, not an oversight.

**What could regress:**

- Accepting content that was previously rejected is the intent, so the risk direction
  is "too permissive". Mitigations: the stored `content-type` is now always one of five
  known-safe values derived from the bytes (previously the client's raw string was
  passed straight to Supabase, so `image/jpg` and mislabelled WebP were being stored
  with a wrong content-type — that latent bug is also fixed); the stored extension is
  likewise derived, so a client can no longer choose the object key's extension.
- Unrecognised bytes + an allowed declared type is still accepted, exactly as before —
  this is not a widening. Unrecognised bytes + an unusable declared type is still
  rejected.
- No database schema, ride state, wallet delta, dispatch path, or background loop is
  touched. Nothing in `core/lifespan.py` reads these functions.

## 5. User-experience effect

**Driver-facing, and visible mid-session** to anyone currently stuck partway through
signup — a driver who retries an upload that failed a minute ago will now succeed
without any app update, since the fix is server-side.

- Driver: gallery photos (PNG/GIF/WebP), PDFs from the file picker, and iPhone gallery
  photos now upload instead of failing. No UI change.
- Driver, new copy: uploading a genuine HEIC/HEIF file now returns
  "HEIC/HEIF photos aren't supported. On iPhone, either take the photo with the in-app
  camera, or set Settings → Camera → Formats → Most Compatible and re-take it. JPG,
  PNG, GIF, WEBP and PDF are accepted." — specific, non-technical, actionable, and it
  replaces the previous "File type .heic not allowed", which told the driver nothing
  they could act on.
- Internal admin: documents are now stored with a content-type that matches their
  bytes, so previews in admin document review render correctly for formats that were
  previously stored mislabelled.
- Rider, corporate admin: no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/documents.py` | Added `_MIME_TO_EXTENSION`, `_HEIF_BRANDS`, `_is_heif`, `_sniff_mime_type`, `_resolve_upload_type`; `upload_file` and `save_upload` use the resolver instead of `_validate_file_type` + `ALLOWED_EXTENSIONS`; `save_upload` raises outside its try | Make the bytes authoritative; stop a 400 being masked as a 500 |
| `backend/tests/test_upload_file_format.py` | New — 29 tests over the picker matrix and the endpoint | Regression cover for each failing combination |
| `shared/api/upload.ts` | Added exported `resolveUploadMimeType` | Stop declaring a MIME type the file isn't |
| `driver-app/__tests__/lib/uploadMimeType.test.ts` | New — 13 cases | Pins the picker-metadata contract |
| `driver-app/app/become-driver.tsx` | `pickImage`/`pickFile` use `resolveUploadMimeType` | Was hardcoding `image/jpeg` / passing `asset.type` as a MIME type |
| `rider-app/app/become-driver.tsx` | `handleUpload` uses `resolveUploadMimeType` | Same `asset.mimeType \|\| 'image/jpeg'` fallback |

## 7. Before / after

```python
# Before — backend/documents.py, POST /api/v1/upload
content_type = file.content_type or "application/octet-stream"
_validate_file_type(content, content_type)          # 400 if bytes != declared type

ext = Path(original_filename).suffix.lower()        # client-controlled
if ext not in ALLOWED_EXTENSIONS:
    raise HTTPException(status_code=400, detail=f"File type {ext} not allowed")
```

```python
# After
content_type, ext = _resolve_upload_type(content, file.content_type or "application/octet-stream")
# bytes decide both; declared type is only a fallback for unrecognised headers
```

```tsx
// Before — driver-app/app/become-driver.tsx, pickImage()
// asset.type is 'image' | 'video', never a MIME type — so this is always 'image/jpeg'
const mimeType = asset.type === 'image' || !asset.type ? 'image/jpeg' : asset.type;
```

```tsx
// After
const mimeType = resolveUploadMimeType(name || asset.uri, asset.type);
```

## 8. Rollback plan

`git revert` is a sufficient rollback here, and this is one of the cases where that is
genuinely true rather than a cop-out:

- No migration, no schema change, no `app_settings` value, no feature flag.
- No live data is mutated. The only durable side effect is objects written to the
  `driver-documents` bucket, and those are written under the same shape as before
  (`{uuid}{ext}` + a `driver_documents` row pointing at a signed URL). Documents
  uploaded while this change is live remain readable after a revert — the stored
  extension and content-type are *more* accurate than what the old code wrote, and
  nothing reads them back through the removed gates.
- Reverting restores the previous rejection behavior; it does not orphan or corrupt
  anything already uploaded.

Not feature-flagged: see §9 for why.

## 9. Verification performed

- [x] **Backend suite, full.** `pytest -m "not slow"` → **11641 passed, 8 skipped,
      1 xfailed, 0 failed** (9m38s).
- [x] **Backend, targeted.** `pytest tests/test_upload_file_format.py
      tests/test_documents.py tests/test_admin_document_upload.py
      tests/test_bundle_document_uploader.py tests/test_bundle_document_uploader_coverage.py
      tests/test_admin_vehicle_fleet_coverage.py tests/test_corporate_kyb_upload.py
      tests/test_profile_image_upload.py` → **170 passed**. These are every test file
      covering a caller of `_validate_file_type` / `ALLOWED_EXTENSIONS` / `save_upload`.
- [x] **driver-app.** `tsc --noEmit` clean. `jest` → 434 passed, 2 failed — both
      failures are in `lib/androidAuto/__tests__/register.test.ts` (Android Auto map
      button count) and were **confirmed pre-existing** by re-running that file against
      a stashed working tree. Nothing in this change touches Android Auto.
      New `__tests__/lib/uploadMimeType.test.ts` → 13 passed.
- [x] **rider-app.** `tsc --noEmit` clean. `yarn test` → **480 passed, 480 total**.
- [x] **Real production build run** (not just a dev server or `tsc --noEmit`):
      `npx expo export --platform web` succeeded for **driver-app** and **rider-app** —
      the closest equivalent to a production build for these Expo surfaces, since neither
      has an `npm run build`. Native EAS builds were not run (no credentials here).
- [x] **ESLint** on the changed TS files → 0 errors.
- [x] **Pre-fix reproduction.** Ran the old `_validate_file_type` + `ALLOWED_EXTENSIONS`
      logic against the 11 (bytes, filename, declared-type) combinations the pickers
      produce: 7 rejected. Re-ran post-fix: the 4 that should still fail (HEIF, and
      unrecognised bytes with an unusable type) fail, the rest pass.
- [x] **Lint/format.** `ruff check` and `ruff format --check` clean on `documents.py`
      and the new test file.
- [x] **Blast-radius grep performed.** `_validate_file_type`, `ALLOWED_EXTENSIONS`,
      `ALLOWED_MIME_TYPES`, `_MAGIC_BYTES`, `_is_valid_webp`, `save_upload` across all
      `*.py`; `uploadFile`, `getDocumentAsync`, `launchImageLibraryAsync`,
      `launchCameraAsync`, `FormData` across all `*.ts`/`*.tsx`. Results in §4.
- [x] **Reviewed against `CLAUDE.md` conventions.** Do-not-swallow-errors (the
      `save_upload` 500-masking-a-400 fix is a direct application); PIPEDA logging (the
      original filename is still never logged or echoed back — pinned by a test);
      dual-import pattern preserved in the new test file.
- [x] **Not feature-flagged — justification.** The change only widens what the endpoint
      accepts on a flow that is *currently broken for most users*; a flag would gate the
      fix behind the same failure it repairs. It touches no shared frontend component,
      no money path, and no state machine. Gate 3 of the release gates asks for flags on
      user-visible non-trivial UX changes; the only new user-visible copy is a single
      error message that replaces a worse one on a path that previously always failed.

## 10. What was NOT verified

State explicitly, so silence does not imply coverage:

- **No native (EAS) build.** The production check for driver-app/rider-app was
  `expo export --platform web`, which exercises the Metro bundle and the full module
  graph but not the iOS/Android native compile. No EAS build credentials exist in this
  environment.
- **Not tested against live Supabase.** Every backend test mocks
  `documents.supabase`; the actual Storage `upload()` call with the new derived
  `content-type` was not exercised against a real bucket. If the `driver-documents`
  bucket has an allowed-MIME-types restriction configured, that is a separate
  server-side setting this change does not touch — but it now always sends one of five
  canonical types rather than arbitrary client strings, which can only help.
- **HEIF brand list is not exhaustive.** `_HEIF_BRANDS` covers the brands Apple emits
  (`heic`, `heix`, `heim`, `heis`, `hevc`, `hevx`, `heif`, `mif1`, `msf1`). An exotic
  HEIF brand outside that set would fall through to the declared-type fallback and be
  stored, then fail to render in admin review — the same as today's behavior, not a
  regression.
- **No visual regression check.** This repo has no automated visual/snapshot regression
  tooling for driver-app (standing gap, see `ACTION_ITEMS.md`). No UI markup changed
  here — only the MIME string passed to an existing upload call — so there is nothing to
  screenshot, but the absence of tooling is stated rather than implied.
- **Standing gap found while doing this work (not fixed here): `shared/**` tests never
  run.** The new test was originally written to `shared/api/__tests__/`, next to the
  three existing `client.*.test.ts` files — then found to be uncollectable. No runner
  has `shared/` in scope: driver-app and rider-app both use jest with `rootDir` at the
  app directory and no `roots` entry (verified with `jest --listTests` in driver-app:
  56 tests, none under `shared/`), and admin-dashboard's vitest `include` is
  `src/**/*.{test,spec}.{ts,tsx}`. So `shared/api/__tests__/client.authHeader.test.ts`,
  `client.refresh.test.ts` and `client.sos.test.ts` have never executed in CI. This
  change works around it — the new test lives in `driver-app/__tests__/lib/` and imports
  the real shared module by relative path — but the three orphaned tests remain dead.
  Worth an `ACTION_ITEMS.md` entry; deliberately out of scope here, since adding
  `roots` would newly activate three untested suites in the same PR as a live-surface fix.
- **Two pre-existing driver-app test failures remain red** (`androidAuto/register.test.ts`,
  map button count). Confirmed unrelated and pre-existing; not fixed here.
- **No end-to-end run on a physical device.** The picker behaviours this fix is built
  around (expo-image-picker re-encoding to JPEG while keeping the `.HEIC` filename;
  Android SAF omitting extensions) are established from the reported failures and the
  library's documented behaviour, and are covered by tests at the byte level — but no
  real iPhone/Android signup was performed in this environment.

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
