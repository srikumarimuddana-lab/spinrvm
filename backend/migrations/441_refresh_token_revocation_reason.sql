-- Purpose: distinguish tokens killed by explicit sign-out from tokens whose
-- reuse indicates possible theft. Existing rows remain NULL and retain the
-- conservative replay-cascade behavior.
-- Rollback on paper: ALTER TABLE refresh_tokens DROP COLUMN revocation_reason;

ALTER TABLE refresh_tokens
    ADD COLUMN IF NOT EXISTS revocation_reason TEXT;

COMMENT ON COLUMN refresh_tokens.revocation_reason IS
    'Reason for explicit revocation; only allowlisted sign-out values suppress reuse cascades when replaced_by is NULL.';
