# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session on `staging`) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin, drivers |
| PR / commit link | `7039b818` + this commit, on `staging` |
| Related issue or gap ID | `GET /api/admin/drivers/stats` 500, request `04827d97` |

## 1. Issue / gap identified

`GET /api/admin/drivers/stats` returned 500 with
`APIError: {'message': 'JSON could not be generated', 'code': 400, 'details': "b'Bad Request'"}`.

The same root cause was silently breaking two background loops. Over the 24 h
retained in the project's edge logs: **207 rejected requests** across three
paths.

## 2. Root cause

`{"col": {"$in": [...]}}` compiles to a PostgREST `col=in.(v1,v2,…)` **URL query
parameter**, so the id list travels in the request line. The fleet is 910
drivers, so `users?select=*&id=in.(910 uuids)` is a **35,573-character URL**.
The edge proxy rejects it with a plain-text `Bad Request` before PostgREST sees
it — which is why the body is not PostgREST's JSON error shape (hence the
client's "JSON could not be generated") and why nothing appears in
`postgrest_logs`.

Measured, not inferred — from `edge_logs`:

| Path | 400s in 24 h | URL length | Caller |
|---|---|---|---|
| `/rest/v1/driver_documents` | 95 | 35,675 | `utils/document_expiry.py` |
| `/rest/v1/rides` | 13 (of 84) | 35,646 | `utils/stale_intent_reconciler.py` |
| `/rest/v1/users` | 28 | 35,573 | `routes/admin/drivers.py` (stats) |

The `users` 400 at `08:51:38.905` precedes the application error log at
`08:51:39.574` by 0.67 s, tying it to the reported request.

**Why it surfaced only now.** Until 2026-08-29 this endpoint died earlier with
`PGRST202` (the `admin_driver_earnings_rollup` function did not exist), which
masked everything downstream. Restoring that function let execution reach code
that was already broken. The 400s are not caused by that fix — the earliest in
the retained window is `2026-08-30T10:22`, and the driver count has sat at ~910
since 21 Aug.

**This also means the 2026-08-28 `document_expiry` fix did not restore that
loop.** That fix corrected three non-existent column names in the projection;
the query then failed on the next thing — URL length — and the sweep has still
been seeing zero documents on every tick. Stated plainly because the earlier
change log implies that loop was working.

## 3. Fix / remediation

- New `get_rows_batched_in()` in `repositories/_base.py`: splits the id set into
  batches of 150 (~6 KB request line) and concatenates. Pages **within** each
  batch rather than capping at `len(chunk)` — one key can match many rows, and
  capping would silently under-count. Offers no `order`/`offset`: ordering is
  per-request and cannot be honoured across concatenated batches. Empty values
  returns `[]` without querying, never an unfiltered scan.
- `routes/admin/drivers.py` — the stats `users` lookup uses it.
- `utils/stale_intent_reconciler.py` — the active-rides lookup uses it.
- `utils/document_expiry.py` — **drops** the `driver_id` filter instead. That
  list was every row of `drivers`, so the filter selected the same rows while
  costing a 35 KB URL. Fewer requests than batching, and the previous behaviour
  is preserved: a document whose `driver_id` has no `drivers` row is simply
  never looked up.

## 4. Risk & impact on existing functionality

**Blast radius of the new helper: none by construction.** It is additive — a new
function. No existing `get_rows` caller changes behaviour; `get_rows` itself is
untouched. Only the three call sites named above now route through it.

**Latent, NOT fixed here.** Other `$in` call sites pass fleet-sized id lists and
will fail the same way as they grow (or already do at lower volume). In
`routes/admin/drivers.py` alone: lines 196, 214, 568, 585, 1011, 1029, 1056,
1062, 1070, 1079, 1266, 1285. Each needs the same treatment; they are not in
this change because they were not observed failing and each needs its own read
of what `limit`/`order` means there. The practical threshold is roughly **840
UUIDs** (32 KB ÷ ~39 chars), so any list of every driver or every user is at or
past it today.

Behaviour changes to weigh:

- `document_expiry` now fetches approved documents for **all** drivers rather
  than a filtered set. Volume is ~1,700 rows at 500/page — 4 requests, versus a
  query that returned nothing. Rows for absent drivers are fetched and ignored.
- `stale_intent_reconciler`'s `CANDIDATE_LIMIT` now caps the **total** across
  batches, which matches the single-query semantics it replaces.
- Batching turns 1 request into ~7 for a 910-id set. Both loops are 12 h/15 min
  scale, and the stats endpoint is admin-only, so the added round-trips are not
  on any rider or driver hot path.

No ride state, money write, wallet delta, insurance-period row, or dispatch path
is touched.

## 5. User-experience effect

- **Internal admin:** Drivers → Stats stops returning 500. It has been failing
  for every admin.
- **Drivers:** document-expiry warnings and auto-suspension resume. Expect a
  catch-up burst — the sweep has not seen a document since the batching change
  landed, so drivers with an already-expired document will be notified and
  suspended on the next tick. The real-time go-online and ride-accept gates were
  never affected, so nobody drove on expired documents in the meantime.
- **Riders: none.** `stale_intent_reconciler` is internal bookkeeping.
- No copy change. Nothing visible mid-ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/_base.py` | New `get_rows_batched_in()` + `_IN_BATCH_SIZE`/`_BATCH_PAGE_SIZE` | Split oversized `$in` sets below the URL limit |
| `backend/db_supabase.py` | Re-export in both dual-import blocks | Callers reach it as `db_supabase.get_rows_batched_in` |
| `backend/routes/admin/drivers.py` | Stats `users` lookup batched | The reported 500 |
| `backend/utils/stale_intent_reconciler.py` | Active-rides lookup batched | 13 × 400/24 h |
| `backend/utils/document_expiry.py` | Redundant `driver_id` `$in` removed | 95 × 400/24 h |

## 7. Before / after

```python
# Before — one 35,573-char URL, rejected by the edge proxy
await db_supabase.get_rows("users", {"id": {"$in": user_ids}}, limit=max(len(user_ids), 1))
```

```python
# After — 7 requests of <=150 ids, ~6 KB each
await db_supabase.get_rows_batched_in("users", "id", user_ids)
```

## 8. Rollback plan

`git revert` of both commits is a complete rollback: code-only, no migration, no
schema change, no data written. Reverting restores the 400s — degraded, not
corrupt. The helper is additive, so reverting it cannot affect any caller that
was not changed here.

The one live-data consideration: drivers auto-suspended by the restored
`document_expiry` sweep stay suspended (`drivers.status = 'suspended'`), since a
code revert does not undo those writes. They clear the normal way — admin
re-approval of a fresh document — and each suspension is logged with the
document that caused it.

## 9. Verification performed

- [x] **Root cause confirmed from the project's own edge logs**, not inferred:
      exact URLs, lengths, status codes, per-path counts, and the 0.67 s lead
      over the application error. Absence from `postgrest_logs` confirms the
      rejection happens before PostgREST.
- [x] **The shipped helper source was executed** against a stubbed `get_rows`:
      910 keys 1:1 → 910 rows over 7 requests, max 150 ids/request; 300 keys ×
      12 rows → 3,600 rows (the truncation case, which **failed an earlier draft
      of this helper** and is why in-batch paging exists); `limit=50` → 50 rows,
      stops after 1 request; empty → 0 rows and 0 requests; `extra_filters`
      carried into every batch.
- [x] Batch size checked against the evidence: 150 × ~39 chars ≈ 6 KB, versus
      the 35,573 that is known to fail.
- [x] Blast-radius grep for every `$in` call site; the unfixed ones are listed
      in §4 rather than left implied.
- [x] All five files compile; the helper is re-exported in both dual-import
      blocks.

### What was NOT verified

- **No endpoint was called and no test suite was run.** PyPI returns 403 under
  this environment's network policy, so backend dependencies cannot be
  installed, and the proxy returns 403 on CONNECT to `supabase.co`, so the REST
  API is unreachable from here. The helper was verified by executing its own
  source in isolation — real shipped code, stubbed transport.
- **No test was added to the repo.** The isolation harness is not committable as
  a unit test without the dependencies to import `_base`. `get_rows_batched_in`
  is a shared primitive and should get real coverage in `backend/tests/` — the
  fan-out truncation case in particular.
- **The exact URL ceiling was not measured** — only that 35,573 fails and that
  every observed successful request is far smaller. 150 was chosen with margin,
  not calibrated.
- **A second, unrelated 400 was found and left alone**: 71 requests to
  `/rest/v1/rides` at only 306 chars, from the scheduled-dispatch/surge path
  (`select=pickup_lat,pickup_lng&status=eq.scheduled&…&scheduled_pickup_time=gte.…&scheduled_pickup_time=lte.…`).
  Not a URL-length problem and not diagnosed here.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
