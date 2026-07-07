-- 218_ai_security_events.sql
--
-- Append-only feed of suspicious AI-assistant activity (Layer 7 — detection).
-- Fed from two places:
--   • backend/ai/threat.py::scan_message — prompt-injection / jailbreak /
--     impersonation / data-exfiltration heuristics on incoming messages.
--   • backend/ai/tools.py — a tool call blocked by the identity-arg denylist
--     (a clear attempt to act as another user).
-- The admin console reads this table to surface attacks and anomalies.
--
-- PIPEDA-safe: stores the matched SIGNAL TAGS and ids only — never the user's
-- message text or tool arguments/results. Do not add a raw-content column.
--
--   • user_id          TEXT     — the acting user (id only)
--   • audience         TEXT     — rider | driver
--   • conversation_id  TEXT
--   • event_type       TEXT     — prompt_injection | role_override |
--                                impersonation | data_exfiltration | secrets |
--                                tool_blocked
--   • severity         TEXT     — high | critical
--   • signals          TEXT[]   — matched heuristic category tags (no message text)
--   • source           TEXT     — message | tool
--
-- Append-only, RLS enabled with NO policies (service role only; anon/authenticated
-- denied). Retention handled by the existing purge if/when configured.
--
-- Rollback: (manual, not expected)
--   DROP TABLE IF EXISTS ai_security_events;

CREATE TABLE IF NOT EXISTS ai_security_events (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id          TEXT,
    audience         TEXT,
    conversation_id  TEXT,
    event_type       TEXT        NOT NULL,
    severity         TEXT        NOT NULL,
    signals          TEXT[],
    source           TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_security_events_recent ON ai_security_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_security_events_user ON ai_security_events (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_security_events_type ON ai_security_events (event_type, created_at DESC);

-- Backend (service role) bypasses RLS; enabling with no policies keeps this
-- security feed backend-only.
ALTER TABLE ai_security_events ENABLE ROW LEVEL SECURITY;
