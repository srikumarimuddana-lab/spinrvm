-- Rollback:
--   DROP POLICY IF EXISTS financial_events_select ON financial_events;
--   CREATE POLICY financial_events_select ON financial_events
--       FOR SELECT USING (
--           user_id = auth.uid()::text
--           OR (SELECT role FROM users WHERE id = auth.uid()::text) = 'admin'
--       );

-- The original SELECT policy in migration 58 used a correlated subquery
-- `(SELECT role FROM users WHERE id = auth.uid()::text)` that executes
-- once per row returned. Replace it with a JWT claim check which is O(1).
-- Admin JWTs carry `role` in their claims (see CLAUDE.md JWT trust model).

DROP POLICY IF EXISTS financial_events_select ON financial_events;

CREATE POLICY financial_events_select ON financial_events
    FOR SELECT USING (
        user_id = auth.uid()::text
        OR (auth.jwt() -> 'user_metadata' ->> 'role') = 'admin'
        OR (auth.jwt() ->> 'role') = 'admin'
    );

NOTIFY pgrst, 'reload schema';
