-- Add profile_image column to users table (base64 encoded image)
-- This stores the actual image data in the database for security

ALTER TABLE users 
ADD COLUMN IF NOT EXISTS profile_image TEXT;

-- Add comment
COMMENT ON COLUMN users.profile_image IS 'Base64-encoded profile image data (stored directly in database for security)';
