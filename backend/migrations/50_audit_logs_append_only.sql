-- 50_audit_logs_append_only.sql
-- Make audit_logs immutable: block DELETE and UPDATE via trigger so no role
-- (including service_role, which bypasses RLS) can mutate existing rows.
--
-- Rollback:
--   DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs;
--   DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs;
--   DROP FUNCTION IF EXISTS prevent_audit_log_mutation();

CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  RAISE EXCEPTION 'audit_logs is append-only — mutation denied (%, %)', TG_OP, OLD.id;
END;
$$;

-- Block DELETE
DROP TRIGGER IF EXISTS audit_logs_no_delete ON audit_logs;
CREATE TRIGGER audit_logs_no_delete
  BEFORE DELETE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();

-- Block UPDATE
DROP TRIGGER IF EXISTS audit_logs_no_update ON audit_logs;
CREATE TRIGGER audit_logs_no_update
  BEFORE UPDATE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
