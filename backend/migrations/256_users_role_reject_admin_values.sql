-- Rollback: ALTER TABLE users DROP CONSTRAINT IF EXISTS chk_users_role_not_admin;
--
-- Defense in depth for the admin-privilege-escalation fix. Admin authorization
-- is now gated on the _admin_verified marker set only by _verify_admin_payload
-- (aud=spinr:admin + JTI + admin_staff active + token_version + idle timeout),
-- never on users.role. This constraint stops the users.role column from ever
-- holding an admin-role string in the first place, so the old landmine value
-- can't be recreated by an ops data-fix, a migration bug, or a future feature
-- reusing the column.
--
-- Real admin identities live in admin_staff, NOT in users.role. This column is
-- only ever 'rider' / 'driver' (admin/users.py keeps 'driver' in sync for
-- backward-compat queries).
--
-- Forward-compatible: NOT VALID skips the full-table scan on ALTER so this is
-- safe against live traffic; the constraint is enforced for all new writes
-- immediately. A separate VALIDATE pass can be run in a maintenance window
-- after confirming no legacy admin-role rows remain (expected: the single
-- historical make_admin.py target, which should be migrated to admin_staff or
-- reset to its real rider/driver role first).

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_users_role_not_admin'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT chk_users_role_not_admin
            CHECK (role IS NULL OR role NOT IN
                ('admin', 'super_admin', 'operations', 'support', 'finance', 'custom'))
            NOT VALID;
    END IF;
END $$;
