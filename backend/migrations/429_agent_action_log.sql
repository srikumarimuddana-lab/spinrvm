-- 429_agent_action_log.sql
--
-- Append-only audit trail for Claude Code / agentic-engineering actions taken
-- against this repo and its infra (security scans, generated reports, code
-- changes proposed or made, decisions escalated to a human owner). Distinct
-- from ai_tool_audit (217_ai_tool_audit.sql), which covers the rider/driver-
-- facing AI assistant's tool calls at runtime — this table covers engineering
-- automation: what an agent did while doing security/ops work on the codebase
-- itself, not what the product's AI assistant did for an end user.
--
-- PIPEDA-safe by design: agent/session identifiers and free-text summaries
-- only — never raw PII, GPS, payment data, or full request/response bodies.
-- Do not add a column that stores arbitrary user data.
--
--   • agent_name        TEXT     — e.g. 'spinr-security-auditor', 'full-audit',
--                                  'dast-zap-baseline', a session id for an
--                                  interactive Claude Code session
--   • task_description  TEXT     — short human-readable summary of the task
--   • action_type       TEXT     — scan | report | code_change | decision_request |
--                                  escalation | approval | other
--   • target_surface    TEXT     — backend | rider-app | driver-app |
--                                  admin-dashboard | infra | shared | multiple
--   • risk_domain       TEXT     — rides | payments | auth | corporate | safety |
--                                  infra | other (nullable; not every action has one)
--   • files_touched     TEXT[]   — repo-relative paths, names only, no content
--   • outcome           TEXT     — success | failed | blocked | needs_human_review
--   • outcome_detail    TEXT     — short free-text detail (e.g. finding count,
--                                  why blocked) — no PII, no secrets
--   • session_ref       TEXT     — link/id back to the originating Claude Code
--                                  session or CI run, for traceability
--   • started_at        TIMESTAMPTZ
--   • completed_at      TIMESTAMPTZ (nullable — set on completion)
--
-- Append-only: no UPDATE/DELETE policies are defined here. A completion can
-- be recorded either as a second row (preferred, keeps history) or, if the
-- caller must update the same row, that requires a follow-up migration
-- adding an explicit UPDATE policy scoped to the service role only — do not
-- assume UPDATE works against this table as merged.
--
-- RLS: enabled with a service-role-implicit-bypass write path (backend only)
-- and an explicit read-only SELECT policy for admin-role JWTs, matching the
-- admin-audit-log read pattern used elsewhere in the admin dashboard.
--
-- Rollback: (manual, not expected)
--   DROP TABLE IF EXISTS agent_action_log;

CREATE TABLE IF NOT EXISTS agent_action_log (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent_name       TEXT        NOT NULL,
    task_description TEXT        NOT NULL,
    action_type      TEXT        NOT NULL,
    target_surface   TEXT        NOT NULL,
    risk_domain      TEXT,
    files_touched    TEXT[],
    outcome          TEXT        NOT NULL,
    outcome_detail   TEXT,
    session_ref      TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_agent_action_log_agent ON agent_action_log (agent_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_log_surface ON agent_action_log (target_surface, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_action_log_outcome ON agent_action_log (outcome, created_at DESC)
    WHERE outcome <> 'success';
CREATE INDEX IF NOT EXISTS idx_agent_action_log_risk_domain ON agent_action_log (risk_domain, created_at DESC)
    WHERE risk_domain IS NOT NULL;

-- Backend (service role) bypasses RLS by design and is the only writer.
-- Admin JWTs (fully trusted per root CLAUDE.md's JWT trust model) get an
-- explicit read-only SELECT policy so the admin dashboard can list entries
-- without going through the service-role backend for every page load.
ALTER TABLE agent_action_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_action_log_admin_read ON agent_action_log
    FOR SELECT
    USING (
        (auth.jwt() ->> 'role') = 'admin'
    );
