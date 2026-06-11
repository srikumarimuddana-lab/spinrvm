-- 145_ai_conversations_admin_actor.sql
-- Rollback:
--   ALTER TABLE public.ai_conversations DROP COLUMN IF EXISTS admin_actor_id;
--
-- Super-admin AI console (routes/admin/ai_console.py): admins can start a
-- test conversation ON BEHALF OF a rider/driver so the thread reflects in
-- the user's real account. Stamp those threads with the acting admin's id
-- for transparency and audit joins; organic user threads keep NULL.

ALTER TABLE public.ai_conversations
    ADD COLUMN IF NOT EXISTS admin_actor_id text;

COMMENT ON COLUMN public.ai_conversations.admin_actor_id IS
    'Admin id when the thread was started from the super-admin AI console on the user''s behalf; NULL for organic threads. Every console action is also audit-logged.';

NOTIFY pgrst, 'reload schema';
