"""Tests for backend/utils/agent_action_logger.py (migration 429).

Mirrors test_utils_extended.py's TestAuditLogger pattern: patch
db_supabase.insert_one directly, since insert_one is invoked via the
dual-import `db_supabase` binding in the module under test.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.utils.agent_action_logger import log_agent_action


class TestAgentActionLogger:
    def test_happy_path_returns_id(self):
        with patch(
            "backend.utils.agent_action_logger.db_supabase.insert_one",
            AsyncMock(return_value={"id": "row_1"}),
        ):
            result = asyncio.run(
                log_agent_action(
                    agent_name="spinr-security-auditor",
                    task_description="Reviewed auth changes in PR #1234",
                    action_type="scan",
                    target_surface="backend",
                    outcome="success",
                )
            )
        assert result == "row_1"

    def test_optional_fields_pass_through(self):
        captured = {}

        async def _capture(table, doc):
            captured["table"] = table
            captured["doc"] = doc
            return {"id": "row_2"}

        with patch("backend.utils.agent_action_logger.db_supabase.insert_one", _capture):
            asyncio.run(
                log_agent_action(
                    agent_name="full-audit",
                    task_description="Parallel reviewer sweep",
                    action_type="report",
                    target_surface="admin-dashboard",
                    outcome="needs_human_review",
                    risk_domain="auth",
                    files_touched=["backend/routes/admin/staff.py"],
                    outcome_detail="2 findings needing owner decision",
                    session_ref="session_abc123",
                    started_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
                    completed_at=datetime(2026, 9, 19, 0, 5, tzinfo=timezone.utc),
                )
            )

        assert captured["table"] == "agent_action_log"
        assert captured["doc"]["risk_domain"] == "auth"
        assert captured["doc"]["files_touched"] == ["backend/routes/admin/staff.py"]
        assert captured["doc"]["completed_at"] is not None

    def test_swallows_db_error(self):
        with patch(
            "backend.utils.agent_action_logger.db_supabase.insert_one",
            AsyncMock(side_effect=Exception("DB error")),
        ):
            # Should NOT raise — a logging failure must never break the
            # security scan / report task being logged.
            result = asyncio.run(
                log_agent_action(
                    agent_name="spinr-migration-reviewer",
                    task_description="Reviewed migration 429",
                    action_type="scan",
                    target_surface="backend",
                    outcome="success",
                )
            )
        assert result is None

    def test_rejects_invalid_action_type(self):
        with pytest.raises(ValueError):
            asyncio.run(
                log_agent_action(
                    agent_name="x",
                    task_description="x",
                    action_type="not_a_real_type",
                    target_surface="backend",
                    outcome="success",
                )
            )

    def test_rejects_invalid_outcome(self):
        with pytest.raises(ValueError):
            asyncio.run(
                log_agent_action(
                    agent_name="x",
                    task_description="x",
                    action_type="scan",
                    target_surface="backend",
                    outcome="not_a_real_outcome",
                )
            )
