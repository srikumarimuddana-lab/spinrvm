# Supabase Service-Role Key Rotation

The Supabase service-role key was previously exposed in version control and **must be rotated** before deploying to production.

## Steps

1. Go to **Supabase Dashboard → Settings → API**
2. Click **"Reset service_role key"** and confirm
3. Update `SUPABASE_SERVICE_ROLE_KEY` in the production environment (Render / Railway / Fly secrets) with the new key

## Why this matters

The service-role key bypasses Row Level Security (RLS) and has full read/write access to every table. Any key committed to git history should be treated as compromised and rotated immediately, even if the repository is private.

## After rotation

- Redeploy the backend so the new key is picked up
- Verify that `/health` and a basic API call succeed before enabling traffic
