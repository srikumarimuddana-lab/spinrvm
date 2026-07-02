# Supabase Storage Buckets

Buckets the backend expects to exist. Create them once via the Supabase
dashboard (Storage → New bucket) or the Supabase CLI, then the app
writes to them at runtime.

## `driver-documents` (private)

- **Visibility:** Private (signed URLs, 1-hour TTL)
- **Used by:** `backend/documents.py` — driver KYC uploads (licence
  scans, insurance, profile photos).
- **Policies:** service-role writes; no public reads. The app mints
  short-TTL signed URLs for display.

## `kyb-documents` (private)

- **Visibility:** Private (signed upload URLs)
- **Used by:** `backend/db_supabase.py` — corporate KYB verification.

## `vehicle-illustrations` (public)

- **Visibility:** Public
- **Provisioned by:** `backend/migrations/202_admin_media_storage_buckets.sql`
- **Used by:** `backend/routes/admin/vehicle_fleet.py` — admin-uploaded
  vehicle type illustrations and custom map markers.
- **Limits:** 500 KB; PNG, JPEG, or WebP for illustrations; PNG or WebP
  for custom markers.
- **If the bucket is missing:** admin vehicle image uploads fail with
  `Storage upload failed` / `Bucket not found`.

## `audio-assets` (public)

- **Visibility:** Public
- **Provisioned by:** `backend/migrations/202_admin_media_storage_buckets.sql`
- **Used by:** `backend/routes/admin/settings.py` — admin-uploaded driver
  ride-offer sound.
- **Limits:** 500 KB; MP3 or WAV.
- **If the bucket is missing:** ride-offer sound uploads fail with
  `Storage upload failed` / `Bucket not found`.

## `ride-snapshots` (public)

- **Visibility:** **Public**
- **Used by:** `backend/routes/drivers.py::_generate_and_store_ride_snapshot`
  — background task on ride completion renders a PNG of the route
  (pickup/dropoff markers + phase_polylines on OSM tiles) and uploads
  it here. The URL is stored on `rides.route_snapshot_url` and
  embedded in:
    - Admin ride drawer (`<img>` instead of a live MapLibre re-render)
    - Rider email receipt (`<img>` under the fare amount)
- **Why public:** email clients can't authenticate, so the URL has to
  be anonymously fetchable. Filenames are `ride_{uuid}.png` — the UUID
  is un-enumerable, so the URL itself is the only capability.

**One-time setup:**

1. Supabase dashboard → Storage → **New bucket**
2. Name: `ride-snapshots`
3. **Public bucket: ✅ on**
4. (Optional hardening) add a bucket-level RLS policy allowing only
   the service-role key to `INSERT` / `UPDATE` / `DELETE` while anon
   `SELECT` stays open:

```sql
-- On storage.objects
CREATE POLICY "ride-snapshots service-role writes"
  ON storage.objects FOR ALL
  USING (bucket_id = 'ride-snapshots' AND auth.role() = 'service_role');

CREATE POLICY "ride-snapshots public read"
  ON storage.objects FOR SELECT
  USING (bucket_id = 'ride-snapshots');
```

**If the bucket is missing:** uploads fail, the pipeline logs a
warning, and `rides.route_snapshot_url` stays null. The admin drawer
and the email both already handle null — the drawer falls back to the
live MapLibre map with phase polylines, and the email receipt simply
omits the map section. Nothing breaks; you just don't get the cached
image benefit until the bucket exists.
