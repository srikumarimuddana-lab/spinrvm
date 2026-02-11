# Database Migration Instructions

The backend has been upgraded to use **PostGIS** for scalable geospatial queries and to remove MongoDB legacy code.

## Prerequisites

1.  **Supabase Project**: You need a Supabase project.
2.  **Environment Variables**: Ensure `backend/.env` has:
    ```bash
    SUPABASE_URL=your_project_url
    SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
    ```
    *Note: If you only have the anonymous/publishable key, some administrative actions (like enabling PostGIS) might fail. You must run the SQL scripts via the Supabase Dashboard SQL Editor.*

## Steps to Migrate

### 1. Apply Schema Changes
Run the following SQL scripts in your Supabase Dashboard (SQL Editor):

1.  **Copy content from `backend/sql/01_postgis_schema.sql`**
    *   This enables the `postgis` extension.
    *   Adds `location` (geography) columns to `drivers`, `rides`, and `service_areas`.
    *   Creates efficient geospatial functions (`find_nearby_drivers`, `get_service_area_for_point`).

### 2. Verify Backend
Run the smoke test to verify connectivity:
```bash
python3 -m backend.tests_smoke_supabase
```

### 3. Start Server
```bash
python3 -m backend.server
```
