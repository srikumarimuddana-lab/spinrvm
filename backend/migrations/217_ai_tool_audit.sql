-- 217_ai_tool_audit.sql
--
-- Append-only audit trail for AI-assistant tool calls (Layer 7 governance).
-- Every tool the assistant runs is recorded from the central execute_tool path
-- (backend/ai/tools.py) so we have a "who ran what, when, and how it ended"
-- record for security review and incident response.
--
-- PIPEDA-safe by design: this table stores tool NAME, user_id (an id, not PII),
-- audience, outcome, latency and the ARGUMENT KEY NAMES only — never argument
-- VALUES or tool results, which can contain addresses/balances. Do not add a
-- column that stores raw arguments or results.
--
--   • user_id          TEXT    — the acting user (id only)
--   • audience         TEXT     — rider | driver (surface the call came from)
--   • tool_name        TEXT
--   • ok               BOOLEAN  — did the tool succeed
--   • outcome          TEXT     — success | blocked | not_found | timeout | error
--   • latency_ms       INTEGER
--   • arg_keys         TEXT[]   — argument NAMES supplied (no values)
--   • conversation_id  TEXT     — links the call to its chat conversation
--
-- Append-only: no UPDATE/DELETE policies are defined. RLS is enabled with NO
-- policies, so only the service role (backend) can read/write; the anon and
-- authenticated keys are denied entirely — this audit is backend-internal.
-- Retention is handled by the existing retention purge if/when configured.
--
-- Rollback: (manual, not expected)
--   DROP TABLE IF EXISTS ai_tool_audit;

CREATE TABLE IF NOT EXISTS ai_tool_audit (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id          TEXT        NOT NULL,
    audience         TEXT,
    tool_name        TEXT        NOT NULL,
    ok               BOOLEAN     NOT NULL,
    outcome          TEXT        NOT NULL,
    latency_ms       INTEGER,
    arg_keys         TEXT[],
    conversation_id  TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_tool_audit_user ON ai_tool_audit (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_tool_audit_tool ON ai_tool_audit (tool_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_tool_audit_outcome ON ai_tool_audit (outcome, created_at DESC)
    WHERE outcome <> 'success';

-- Backend (service role) bypasses RLS; enabling it with no policies denies the
-- anon/authenticated client keys, keeping this audit table backend-only.
ALTER TABLE ai_tool_audit ENABLE ROW LEVEL SECURITY;
