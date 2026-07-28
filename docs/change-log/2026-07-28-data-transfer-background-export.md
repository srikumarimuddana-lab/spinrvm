# Change Impact & Risk Log — Data Transfer module: background the export route

## Issue/gap identified
Flagged in the earlier critical review: `data_transfer_export_jobs` has a
`pending → completed/failed` status lifecycle — the shape of a background
job — but the export route did the gather + ZIP-build + Storage-upload
**synchronously inline** in the request/response cycle. For the maximum
100-entity batch (each potentially carrying up to 500 rides + documents),
that's a real risk of tying up a request thread for a long time, exactly
the anti-pattern CLAUDE.md's SLA table calls out ("Awaiting Twilio/Stripe
inline in a request handler ... queue via `asyncio.create_task` or
background worker"). The existing DSAR self-export
(`routes/drivers/tax_exports.py::export_driver_data`) already solved this
with `BackgroundTasks.add_task` — this module diverged from that precedent
without a stated reason.

## Root cause
The synchronous version was written first (Phase 1.2) specifically because
the Jobs tab didn't exist yet to give an async flow anywhere to report to —
at the time, "return the download link directly" was the only UX available.
Once the Jobs & History tab landed (follow-up #2), the precondition for
doing this properly was in place, but the export route wasn't revisited
until now.

## Fix/remediation
- `backend/routes/admin/data_transfer_export.py`: `export_entities` now
  validates the request, inserts the `pending` job row, calls
  `background_tasks.add_task(_run_export_job, ...)`, and returns
  immediately (`202 Accepted`) with just `{job_id, status: "pending",
  requested_count}`. The actual gather/build/upload work moved into a new
  `_run_export_job()` background function — same logic as before, just no
  longer blocking the response. On failure, the job row is marked
  `failed` with `error_message`; there is no request left to raise an
  `HTTPException` to, so failures are visible only via the Jobs tab from
  this point on (this is the same tradeoff `_build_and_email_data_export`
  already accepts for the DSAR export).
- `backend/routes/admin/data_transfer_jobs.py`: added
  `GET /data-transfer/jobs/{job_id}` (single-job status) so the frontend has
  something to poll — the list endpoint alone isn't a good fit for "did my
  just-submitted job finish yet."
- `admin-dashboard/src/lib/api.ts`: `exportDataTransferEntities`'s return
  type changed from `DataTransferExportResult` (download_url inline) to
  `DataTransferExportQueuedResult` (job_id + pending status only); added
  `getDataTransferJob`.
- `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx`: `onExport`
  now polls `getDataTransferJob` every 2s (5-minute timeout) after queuing,
  then calls the existing `regenerateDataTransferJobDownload` (built for the
  Jobs tab, reused here) once the job leaves `pending`, and opens the
  download. User-visible flow changes from "click Export, wait, get a file"
  to "click Export, see a 'queued' toast, see a 'ready' toast a few seconds
  later, get a file" — same end result, no longer blocks the tab.

**Deliberately NOT done**: raising `MAX_ENTITIES_PER_EXPORT` above 100.
Backgrounding removes the request-timeout pressure that cap originally
existed to guard against, but a much larger batch is still a lot of
sequential work for one background task with no internal parallelism cap
or progress reporting — raising the limit as a side effect of this change
would be scope creep on an unrelated question. Left as a candidate for a
deliberate follow-up if larger batches are actually needed.

## Risk & impact on existing functionality
Blast radius: `export_entities` is the only caller of `_upload_bundle`
(unchanged internals, just moved into the new background function) and
the only writer of `data_transfer_export_jobs` on the export side (grep-
confirmed — the import route and purge loop are separate paths, unaffected).
`ExportTab.tsx` is the only consumer of `exportDataTransferEntities`
(grep-confirmed) — its return-type change is safe because nothing else
depends on the old shape. `getDataTransferJob`/the new backend endpoint are
net-new with no other callers yet. The SGI Forms tab and Import tab are
unaffected (separate routes, not touched).

One behavior change worth naming explicitly: the export response no longer
returns a download link directly, so **any external API consumer that
called `POST /data-transfer/export` expecting a synchronous `download_url`
in the response would break**. Grepped for other callers of this endpoint
beyond the frontend — none exist (it's a same-PR-vintage feature with a
single consumer), so this is safe today, but it's a breaking API contract
change and would need a version bump or dual-response transition period if
any other caller existed.

## User experience effect
Visible to admins with `bulk_operations` module access: exporting now shows
a "queued" toast immediately, followed by a "ready" toast when the file is
actually available, instead of the tab appearing to hang until the whole
gather/zip/upload finishes. For small batches (the common case) this is a
sub-second-to-few-seconds difference the admin will barely notice; for
large batches it's the difference between a blocked UI and a responsive one.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/routes/admin/data_transfer_export.py` | Backgrounded via `BackgroundTasks.add_task`; returns 202 + job_id immediately | Remove inline blocking work from the request/response cycle |
| `backend/routes/admin/data_transfer_jobs.py` | +`GET /data-transfer/jobs/{job_id}` | Give the frontend something to poll |
| `admin-dashboard/src/lib/api.ts` | `exportDataTransferEntities` return type changed; +`getDataTransferJob` | Match the new async contract |
| `admin-dashboard/src/app/dashboard/data-transfer/ExportTab.tsx` | Polls job status after queuing, then fetches the download link | Match the new async contract |

## Before/after snippet
```python
# before: gather/build/upload happens inline, response waits for all of it
bundles = await entity_export_service.gather_entity_bundles(pairs, body.doc_types)
file_bytes = builder(bundles)
signed_url, storage_path = await _upload_bundle(...)
return {"job_id": job_id, "download_url": signed_url, ...}

# after: response returns immediately; the same work runs after
background_tasks.add_task(_run_export_job, job_id, admin, pairs, body.doc_types, body.format)
return {"job_id": job_id, "status": "pending", "requested_count": len(pairs)}
```

## Rollback plan
Revert all four files to their prior committed state (`git revert` is safe
— no data migration, no schema change; `data_transfer_export_jobs` rows
written by either version have the same shape). If only the frontend needs
to roll back independently of the backend (unlikely to be useful, since the
two must move together), the old `ExportTab.tsx` would break against the
new backend's response shape — this pair is not independently revertible in
practice, call out for anyone reverting partially.

## Verification performed
- `python3 -m py_compile` on all three modified/new backend files — passes.
- `npx tsc --noEmit -p tsconfig.json` across the whole admin-dashboard
  project — zero errors attributable to any file this change touched.
- Confirmed `BackgroundTasks.add_task` is the exact pattern already used by
  `routes/drivers/tax_exports.py::export_driver_data` (parameter shape,
  import source) rather than inventing a new backgrounding mechanism.
- Grepped for every other caller of `exportDataTransferEntities`,
  `_upload_bundle`, and `POST /data-transfer/export` to confirm the breaking
  response-shape change has exactly one consumer (`ExportTab.tsx`, updated
  in the same commit) and no other integration depends on the old
  synchronous contract.

## What was NOT verified
- `fastapi` is not installed in this session's environment (consistent with
  every other backend verification note in this module's history) — could
  not actually construct the FastAPI app or exercise
  `BackgroundTasks.add_task` at runtime; verified via `py_compile` and
  precedent-matching against the DSAR export's identical pattern instead.
- Not exercised end-to-end (queue → poll → download) against a live backend
  or in a browser.
- No unit test added for the new polling logic or the background task
  function — standing coverage gap, not new to this change.
