-- Ensure users.profile_image_status exists. Missing in some environments where
-- migration 08's conditional branch never ran, causing PUT /users/profile-image
-- to fail with PGRST204 "Could not find the 'profile_image_status' column".
-- Values: pending_review | approved | rejected.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS profile_image_status TEXT;

COMMENT ON COLUMN users.profile_image_status IS
    'Moderation state for profile_image: pending_review | approved | rejected';

-- Force PostgREST to reload its schema cache so the new column is visible
-- immediately (Supabase auto-reloads on DDL, but this is belt-and-suspenders).
NOTIFY pgrst, 'reload schema';
