# Admin Dashboard Audit — Phase 5: Data Protection, Privacy & Logging

**Date:** 2026-04-26

---

## 1. PII Inventory — Admin-Accessible Data

The admin dashboard has access to the following sensitive fields across the Supabase tables it reads:

| Table | PII fields accessible to admin |
|---|---|
| `users` | `first_name`, `last_name`, `email`, `phone`, `city`, `status`, `created_at` |
| `drivers` | all `users` fields + `license_plate`, `vehicle_make`, `vehicle_model`, `rating`, `total_earnings`, `service_area_id`, `is_verified`, `is_online`, `lat`, `lng` |
| `rides` | `pickup_address`, `dropoff_address`, `pickup_lat`, `pickup_lng`, `dropoff_lat`, `dropoff_lng`, `rider_id`, `driver_id`, `rider_name`, `driver_name`, `rider_phone`, `driver_phone`, `rider_email` |
| `admin_staff` | `email`, `first_name`, `last_name`, `role`, `modules`, `last_login` |
| `wallet_transactions` | `user_id`, `amount`, `balance_after`, `description`, transaction metadata |
| `driver_documents` | document file references, document type, verification status |
| `support_messages` | message text, `sender_id`, ticket content |

All fields above are accessible to any admin with the corresponding module — no sub-field filtering is applied (e.g. a `support` module admin can read full user phone numbers via ticket views).

---

## 2. Export Endpoints — Unlogged, Unlimited Bulk PII Export

All data export functionality is implemented **client-side** with no server-side trace.

### Export implementations found

| Location | Export format | PII exported | Server log? |
|---|---|---|---|
| `users/page.tsx:162` | CSV (inline — not via export-csv.ts) | ID, name, email, phone, city | ❌ None |
| `drivers/page.tsx:249` | CSV via `exportToCsv` | ID, name, email, phone, license plate, earnings, rating | ❌ None |
| `rides/_components/ride-list.tsx:174` | CSV via `exportToCsv` | Pickup address, dropoff address, driver name, rider name, fare | ❌ None |
| `earnings/page.tsx:109, 274` | CSV via `exportToCsv` | Driver earnings, ride fares, Spinr Pass transactions | ❌ None |
| `rides/_components/ride-invoice.tsx:42` | PDF via jsPDF | Full rider name, rider phone, rider email, driver name, driver phone, driver license plate, pickup address, dropoff address, pickup lat/lng, dropoff lat/lng | ❌ None |

**Finding F-41:** All 5 export paths produce raw PII downloads with no server-side audit entry. An admin with the relevant module can export all riders' phone numbers and email addresses without leaving any trace in `audit_logs`, Railway logs, or Sentry.

**Finding F-42:** PDF ride invoice (`ride-invoice.tsx:138`) includes exact GPS coordinates (`pickup_lat/lng`, `dropoff_lat/lng` to 5 decimal places) in the downloaded file. PIPEDA data minimization requires coordinates to be at city/area granularity in admin outputs.

**Finding F-43:** No row-count cap on CSV exports. `drivers/page.tsx:249` exports the full `sorted` array which contains all drivers loaded into the browser session (up to the API's `limit` ceiling). `rides/page.tsx:174` exports up to 10,000 rides in a single download. There is no server-side rate limit or row-count ceiling on export operations.

---

## 3. Sentry Configuration — Missing PII Scrubbing

### Client config (`sentry.client.config.ts`)

```typescript
Sentry.init({
  // ...
  integrations: [
    Sentry.replayIntegration({
      maskAllText: true,    // ✅ Session replays mask text
      blockAllMedia: false,
    }),
  ],
});
```

Session replays have `maskAllText: true` — good.

**Gap:** No `beforeSend` callback. When a JavaScript exception occurs in the dashboard (e.g. an API error thrown while displaying user data), the error event's `extra` and `contexts` fields can include the component state that triggered the exception, which may contain full user objects with `email`, `phone`, and `name` fields.

Example: a crash while rendering a user's profile row would send Sentry an event with the raw user object in the React component's state.

### Server config (`sentry.server.config.ts`)

```typescript
Sentry.init({
  dsn: process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN,
  tracesSampleRate: ...,
  environment: ...,
  enabled: ...,
  // NO beforeSend
});
```

No `beforeSend` and no `ignoreErrors` / `allowUrls` configuration. Server-side Next.js errors (API route errors, SSR errors) that include PII in their context are sent to Sentry unredacted.

**Finding F-44:** Neither `sentry.client.config.ts` nor `sentry.server.config.ts` implement a `beforeSend` hook to strip PII (email, phone, names, addresses) from error event contexts before transmission to Sentry's US-hosted infrastructure. This is a PIPEDA cross-border data transfer concern: personal information about Saskatchewan residents may be transmitted to and stored on US Sentry servers without explicit consent for that transfer.

---

## 4. Backend Log Redaction

### Python backend (`backend/routes/admin/`)

A full scan of all `logger.*` calls in the 21 admin route files found:

- **No raw email addresses** in log messages. Emails appear only in the `audit_logs` table's `user_email` column (via the legacy `log_audit()` helper in `maintenance.py`).
- **No phone numbers** in any log message.
- **No JWT or token values** in any log message.
- **No GPS coordinates** in any log message.
- `auth.py:388`: logs `user_id` and `token_version` (no PII). ✅
- `auth.py:454`: logs admin staff `id` only (no PII). ✅
- `drivers.py:626`: logs `driver_id`, `status`, `reason` (no PII). ✅
- `messaging.py:84`: logs `audience`, `title`, success/failure counts (no PII in the message itself — titles set by admin are not PII). ✅

Backend logs are clean. **No PII leakage found in stdout/logger output.**

---

## 5. Audit Log Schema Inconsistency

Two conflicting schemas exist for `audit_logs`:

**Schema A** (migration `06_cloud_messaging.sql`, used by `maintenance.py:log_audit()`):
```
id TEXT, action TEXT, entity_type TEXT, entity_id TEXT,
user_email TEXT, details TEXT, created_at TIMESTAMPTZ
```

**Schema B** (migration `08_complete_schema.sql`, used by `staff.py`, `users.py`, `wallet.py`, `drivers.py`):
```
id UUID, actor_id TEXT, actor_role TEXT, action TEXT,
resource TEXT, resource_id TEXT, details JSONB, ip_address TEXT,
created_at TIMESTAMPTZ
```

Schema A stores `user_email` (the actor's email address) instead of `actor_id`. Schema B correctly uses the actor's ID.

**Finding F-45:** `audit_logs` has two incompatible schemas across migrations. The `user_email` column in Schema A violates PIPEDA data minimization (storing PII where an opaque ID suffices). The `GET /audit-logs` search endpoint in `maintenance.py:279` searches by `user_email` regex — full email addresses are searchable and visible in the audit log admin view.

---

## 6. Audit Log Integrity — No Append-Only Enforcement

The `audit_logs` RLS policy in `06_cloud_messaging.sql` grants `FOR ALL` to authenticated admins:

```sql
CREATE POLICY "Admin full access audit_logs"
ON audit_logs FOR ALL
TO authenticated
USING (...users.role IN ('admin', 'super_admin')...);
```

`FOR ALL` includes `DELETE` and `UPDATE`. An admin can delete or modify audit log entries, defeating the forensic integrity of the log.

The service role policy also grants `FOR ALL TO service_role`, which means the backend service role can delete audit entries — relevant if an attacker gains access to the service role key.

**Finding F-46:** `audit_logs` RLS policy allows `DELETE` and `UPDATE` by admin users — audit log is not append-only. No `PIPEDA`-required immutability is enforced at the DB layer.

---

## 7. Audit Log Retention

No cleanup job, scheduled deletion, or TTL policy exists for `audit_logs`. The table grows indefinitely. This is a positive finding from an evidence-preservation standpoint — but:

- No minimum retention period is explicitly enforced (e.g. `>= 1 year`).
- No partitioning or archival strategy is defined.
- At scale (millions of admin actions), the table will grow unbounded.

**Observation:** Retention is implicitly unlimited (no deletion). A formal retention policy (minimum 1 year active, archive to cold storage after) should be documented even if no code change is needed today.

---

## 8. Data Residency

| Service | Configured region | Compliance status |
|---|---|---|
| Supabase | URL from `SUPABASE_URL` env var (placeholder `supabase.co` — actual region set at project creation) | ⚠️ Not verifiable from code — must confirm project is `ca-central-1` in Supabase dashboard |
| Redis | `REDIS_URL` env var — no region constraint in code | ⚠️ Not verifiable from code |
| Sentry | US-hosted by default (no EU/CA instance configured) | ⚠️ Cross-border transfer for PII in error events |
| Vercel | Deployment region not pinned in `next.config.ts` or `vercel.json` | ⚠️ May default to US-East |
| Railway (backend) | Region set in Railway dashboard — not in code | ⚠️ Not verifiable from code |

CLAUDE.md states "Supabase project must be in a Canadian region (ca-central-1 or equivalent)" — compliance cannot be confirmed from the codebase alone. No runtime assertion or startup check enforces the region.

**Finding F-47:** Data residency cannot be verified from the codebase. Four of five data stores have region determined by environment configuration alone, with no code-level enforcement or startup assertion. PIPEDA cross-border transfer concerns apply to Sentry (US-hosted, no `beforeSend` PII scrubbing).

---

## 9. Phase 5 New Findings

| ID | Finding | Severity |
|---|---|---|
| F-41 | All 5 bulk export paths (CSV + PDF) have no server-side audit entry — untraced PII extraction | MEDIUM |
| F-42 | Ride invoice PDF includes exact GPS coordinates (5 decimal places) — violates PIPEDA data minimization | MEDIUM |
| F-43 | No row-count cap or rate limit on bulk exports — unlimited PII download possible | LOW |
| F-44 | No `beforeSend` PII scrubbing in Sentry client or server config — PII in error contexts sent to US Sentry | MEDIUM |
| F-45 | `audit_logs` has two incompatible schemas; `user_email` column stores PII where actor_id suffices | LOW |
| F-46 | `audit_logs` RLS allows `DELETE` and `UPDATE` — not append-only, forensic integrity not guaranteed | MEDIUM |
| F-47 | Data residency not enforceable from code — Supabase/Redis/Vercel/Railway regions set by env only; Sentry is US-hosted | LOW (env-dependent) |
