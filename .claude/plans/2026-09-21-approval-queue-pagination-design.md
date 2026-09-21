# Approval Queue: DB-side pagination, search, sort — design

Date: 2026-09-21 · Status: approved in chat (sections 1–4), pending written-spec review
Surface: backend + admin-dashboard (`/dashboard/drivers/queue`)

## Problem

`GET /api/admin/drivers/approval-queue` (`backend/routes/admin/drivers.py`) builds the queue in Python:
1. It scans three sources, each capped at `limit=1000`:
   - `drivers.status='pending'`
   - pending `driver_documents`
   - `users.profile_image_status='pending_review'`
2. It joins users, areas, vehicle types and documents in Python.
3. It computes `queue_started_at` per row, sorts, and slices `items[:limit]`.

The page asks for `limit=200` and filters tabs client-side. There is no search, no pagination, and no user-controlled sort.

## Goal

Admins can search the queue by driver **name, email, or phone**, sort by column, and page through it. Filtering, sorting, counting and paging all happen **in Postgres**: no full-queue pull into Python.

Non-goals:
- No change to approve/reject flows or to what counts as "in the queue".
- No new feature flag. The API change is backward compatible, and this is an internal admin page.

## Approach (chosen: A)

A SQL view plus a stats function, queried through the existing query layer: `_apply_filters` for escaping, `count_documents`, and `rpc`. Search runs on the view's flattened name, email and phone columns, using the Drivers page's tokenizer and phone-digit rules.

Rejected alternatives:
- **B, a single RPC function:** it duplicates the multi-token search in SQL, and dynamic sort needs dynamic SQL.
- **C, a trigger-maintained table:** too much machinery for a queue of tens to hundreds of rows, with a staleness risk.

## 1. Database — `backend/migrations/436_admin_driver_approval_queue.sql` (additive)

Production schema was checked 2026-09-21 on PG 17.6. `drivers` has **no `email`** column; `driver_documents` has **no `created_at`**; `vehicle_year` is `integer`; all IDs are `text`.

### View `admin_driver_approval_queue`, `WITH (security_invoker = true)`

The view has one row per driver needing attention. It keeps today's membership rules exactly:
- **new applicant:** `d.status = 'pending'`
- **resubmission:** not a new applicant, and has ≥ 1 `driver_documents.status = 'pending'`. Any driver status qualifies, as today.
- **photo-only:** `u.profile_image_status = 'pending_review'`, `d.status NOT IN ('banned','rejected')`, the user has no other qualifying driver row, and there is **one row per user** (`DISTINCT ON (user_id)`, earliest `created_at`). This mirrors today's `known_user_ids` dedup.

Columns:

| column | source / rule |
|---|---|
| `driver_id`, `user_id`, `status`, `created_at` | `drivers` |
| `first_name`, `last_name` | `users` |
| `full_name` | the users first+last composition, falling back to the drivers first (ignoring the literal `'Driver'` placeholder) + last; same rule as `_resolve_full_name` |
| `email` | `users.email` |
| `phone` | `COALESCE(NULLIF(btrim(u.phone),''), d.phone)` |
| `profile_image`, `profile_image_status` | `users` |
| `service_area_id`, `service_area_name` | `drivers` → `service_areas.name` |
| `vehicle_type_id`, `vehicle_type_name` | `drivers` → `vehicle_types.name` |
| `pending_docs_count` | `count(*)` of pending docs, or `0` |
| `queue_started_at` | new applicant → `d.created_at`; resubmission → `min(pending uploaded_at)`, falling back to `d.created_at`; photo-only → `u.updated_at`, falling back to `d.created_at` |
| `is_new_applicant`, `is_resubmission`, `has_pending_photo` | booleans per the rules above |
| `is_incomplete` | `NOT (all 10 required fields filled)`; see below |

`is_incomplete` ports `utils/profile_completeness.REQUIRED_FIELDS`:
- "Filled" means `IS NOT NULL` and, for text, `btrim(x) <> ''`.
- The fields are:
  - `full_name` (composed as above)
  - `phone` (users first, then drivers)
  - `email` (users only)
  - `vehicle_make`, `vehicle_model`, `vehicle_color`, `license_plate` (text)
  - `vehicle_year` (integer: `IS NOT NULL`)
  - `service_area_id`
  - `stripe_account_id`

### Function `admin_driver_approval_queue_stats(p_service_area_id text DEFAULT NULL)`

Returns one row: `total_pending`, `oldest_in_queue_hours`, `median_wait_hours`, `over_24h_count`, `new_applicants`, `resubmissions`, `photo_review`, `incomplete_profiles`. These are aggregates over the view, optionally filtered by area.
- Median uses `percentile_cont(0.5)`. Hours are rounded to 1 decimal, matching the current response.
- The function is `LANGUAGE sql STABLE SECURITY INVOKER` with `SET search_path = public`.

### Lockdown

Same pattern as migrations 286 and 346:
- `REVOKE ALL ON admin_driver_approval_queue FROM anon, authenticated;`
- `REVOKE EXECUTE ON FUNCTION admin_driver_approval_queue_stats(text) FROM PUBLIC, anon, authenticated;`

Only the backend's service role reads either object.

### Rollback

`DROP FUNCTION IF EXISTS admin_driver_approval_queue_stats(text); DROP VIEW IF EXISTS admin_driver_approval_queue;`. No table, column or data changes.

## 2. API — `GET /api/admin/drivers/approval-queue` (backward compatible)

| param | values | default |
|---|---|---|
| `limit` | 1–200 | 50 (unchanged) |
| `offset` | ≥ 0 | 0 |
| `service_area_id` | id | none (unchanged) |
| `segment` | `all`, `new`, `resubmitted`, `photo`, `incomplete` | `all` |
| `search` | ≤ 128 chars | none |
| `sort_by` | `waiting`, `name`, `applied`, `service_area`, `pending_docs`, `vehicle_type` | `waiting` |
| `sort_dir` | `asc`, `desc` | `asc` for `waiting` (oldest `queue_started_at` first = longest wait, today's order); `asc` otherwise |

- **Sort mapping:**
  - `waiting` → `queue_started_at`
  - `name` → `full_name`
  - `applied` → `created_at`
  - `service_area` → `service_area_name`
  - `pending_docs` → `pending_docs_count`
  - `vehicle_type` → `vehicle_type_name`
  - Every sort adds a `driver_id` tie-break, so pages stay stable.
  - An unknown `sort_by`, `sort_dir` or `segment` returns 400.
- **Segment → filter:** `new` → `is_new_applicant`, `resubmitted` → `is_resubmission`, `photo` → `has_pending_photo`, `incomplete` → `is_incomplete`.
- **Search** runs directly on the view, in one query.
  - Trim the term to 128 characters.
  - **Phone-shaped term:** if `_phone_digits(term)` returns digits, the filter is `{"phone": ILIKE digits}`. This check runs on the whole term, so "(306) 555-1234" matches the stored "+13065551234".
  - **Otherwise:** split the term with `_driver_search_tokens` (max 5 words). Each word becomes `{"$or": [full_name ILIKE, email ILIKE, phone ILIKE]}`, and the words are ANDed with `$and`, the same shape `_resolve_driver_search_user_ids` uses. So "Nighil Kumar" matches `full_name` "Nighil Kumar".
  - There's no pre-query and no `user_id IN (…)` list, which avoids the ~150-ID URL limit (`_IN_BATCH_SIZE`); the count uses the same filter. The escaping layer (`_escape_like`) handles LIKE wildcards; never `re.escape` the term.
  - The term is never logged.
- **Queries:**
  1. The page: a new repository function `get_approval_queue_page(filters, order_col, desc, limit, offset)` in `repositories/driver_repo.py`. It uses `_apply_filters`, then `.order(order_col, desc, nullsfirst=False).order("driver_id")`, then `.range(offset, offset+limit-1)`. `get_rows` takes only one order column, and the shared helper isn't changed.
  2. The total: `count_documents(view, filters, id_column="driver_id")`. The view has no `id` column.
  3. The stats: `rpc("admin_driver_approval_queue_stats", {"p_service_area_id": …})`

  Queries 2 and 3 run concurrently. Stats ignore `search` and `segment`, so they are queue-wide per area, as today.
- **Page enrichment** runs in Python on ≤ `limit` rows only:
  - `missing_docs_count`, from the area's `required_documents` plus approved docs for the page's drivers
  - `profile_completeness_score`, for display
  - Neither is sortable.
- **Response:** `{ stats, items, total, limit, offset }`. The `items` shape is unchanged, so existing fields keep their names, and `profile_photo_url` comes from `profile_image`.
- **DB errors:** return 503, logged with `logger.error`, per CLAUDE.md. Never fall back to an empty queue.

## 3. UI — `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx`

- **Search input** ("Search name, email or phone"): debounced 300 ms, sent as `search`.
- **Tabs:** send `segment`. Tab badges keep using `stats`.
- **Sortable headers:** Driver (`name`), Waiting (`waiting`), Applied (`applied`), Area (`service_area`), Vehicle (`vehicle_type`), Pending docs (`pending_docs`). They use the existing `SortableHead` with server-driven state.
- **Footer:** the existing `components/ui/pagination.tsx` with `totalCount={total}`, plus a page-size select (10/25/50/100, default 25).
- Changing search, tab, sort, area or page size resets to page 0.
- After approve/reject or a photo action, reload the current page. If it comes back empty and the page is > 0, step back one page.
- The empty state distinguishes "No results for '<term>'" from "Queue is empty".
- `getApprovalQueue()` in `lib/api/driver-queue.ts` gains the new params; `ApprovalQueueResponse` gains `total`, `limit` and `offset`.

## 4. Testing, review, rollout

**Backend** (`backend/tests/`, `mock_supabase_client`):
- param → filters, order and offset mapping
- each segment
- search builds the right filter on the view: a digits-only phone clause for phone-shaped terms, and the per-word `$and`/`$or` clause (multi-word names, email, phone) otherwise; no `$in` pre-query
- 400 on an invalid `sort_by`, `sort_dir` or `segment`
- response shape and enrichment
- 503 on DB error
- **parity test:** parses the migration's `is_incomplete` field list and asserts it equals `REQUIRED_FIELDS`

**Frontend:** vitest for the `getApprovalQueue` query string, plus page behaviour (debounced search, reset to page 0, pagination wiring). Also tsc, eslint and a real `npm run build`.

**Review:** `spinr-migration-reviewer` and `spinr-security-auditor` on the diff before the PR. Plus the Change Impact Log entry.

**Rollout order:** migration → backend → dashboard.
- An old dashboard on the new backend works: it sends `limit=200`, and the new fields are ignored.
- A new dashboard on the old backend degrades: no total, no server paging.

**Not verified by unit tests:** the view's SQL itself, since the database is mocked. Dry-run it through `run_migrations.py --dry-run` if a `DATABASE_URL` is available; otherwise say so. The approvals page has no visual-regression baseline.

**Work order** (≤ 3 files each, one commit each):
1. migration
2. repository function + endpoint + backend tests
3. API client + page + frontend tests
4. change log
