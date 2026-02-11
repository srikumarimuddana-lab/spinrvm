# Supabase schema & migration — Spinr

This document describes how to provision the schema and enable Row-Level Security (RLS) for the Spinr project.

Important: You need a Postgres connection string (PG_CONNECTION_STRING) or a Supabase Service Role with sufficient privileges to create tables and enable RLS. The publishable/anon key is NOT sufficient for admin tasks.

Steps (recommended - manual or CI):

1. Get DB connection string
   - In the Supabase console go to Settings → Database → Connection string → `postgres://...`
   - Save this as a secret (e.g., `PG_CONNECTION_STRING`)

2. Apply schema locally or in CI
   - Locally: install `psql` and run:
     ```bash
     export PG_CONNECTION_STRING="your_connection_string"
     psql "$PG_CONNECTION_STRING" -f backend/supabase_schema.sql
     ```
   - CI (recommended): add `PG_CONNECTION_STRING` as a GitHub secret and run the provided workflow `Apply Supabase Schema` (workflow_dispatch)

3. Enable Row-Level Security (RLS)
   - The SQL file `backend/supabase_schema.sql` contains table definitions; after applying it you should run additional RLS policies — example policies will be provided in `backend/supabase_rls.sql` (coming soon).

4. Configure backend
   - Set env vars in your deployment:
     - `SUPABASE_URL` (e.g. `https://<project>.supabase.co`)
     - `SUPABASE_SERVICE_ROLE_KEY` (service role key for server-to-server actions)
     - `USE_SUPABASE=true`

5. Smoke test
   - Start the backend in a staging environment and ensure that previously used endpoints (create ride, accept ride, start, complete) behave correctly.

6. Cutover
   - Schedule downtime for big-bang migration if you had existing Mongo data.
   - Run migration script `backend/migrate_to_supabase.py` to import data and generate mapping files in `migrations/`.

If you want, I can create a GitHub Action to apply the schema (requires `PG_CONNECTION_STRING` secret) and a follow-up action to enable RLS policies automatically when triggered.
