-- Rollback: UPDATE users SET profile_image_status = 'pending_review'
--   WHERE role = 'rider' AND profile_image_status = 'approved'
--   AND profile_image IS NOT NULL;

-- Riders' profile photos do not go through admin review.
-- Any rider who uploaded a photo before this fix is stuck on 'pending_review'
-- with no way to be approved. Approve them all now.
UPDATE users
SET profile_image_status = 'approved'
WHERE role = 'rider'
  AND profile_image_status = 'pending_review'
  AND profile_image IS NOT NULL;
