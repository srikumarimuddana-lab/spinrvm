"""ai/guardrails.py: the bounded, process-local fallback cap (AI1b, #3742)
used by orchestrator._over_daily_cap / mcp_server._over_mcp_daily_cap only
on the Redis-exception path.
"""

from unittest.mock import patch

import backend.ai.guardrails as guardrails


class TestFallbackOverCap:
    def setup_method(self):
        guardrails._fallback_counts.clear()

    def test_bounds_at_fallback_cap(self):
        for _ in range(guardrails._FALLBACK_DAILY_CAP):
            assert guardrails.fallback_over_cap("u1", 1000) is False
        assert guardrails.fallback_over_cap("u1", 1000) is True

    def test_effective_cap_is_min_of_configured_and_floor(self):
        assert guardrails.fallback_over_cap("u1", 2) is False
        assert guardrails.fallback_over_cap("u1", 2) is False
        assert guardrails.fallback_over_cap("u1", 2) is True

    def test_per_user_budgets_are_independent(self):
        for _ in range(guardrails._FALLBACK_DAILY_CAP):
            assert guardrails.fallback_over_cap("u1", 1000) is False
        assert guardrails.fallback_over_cap("u1", 1000) is True
        # u2 has its own, untouched budget
        assert guardrails.fallback_over_cap("u2", 1000) is False

    def test_resets_across_a_day_boundary(self):
        with patch.object(guardrails, "datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "20260101"
            for _ in range(guardrails._FALLBACK_DAILY_CAP):
                assert guardrails.fallback_over_cap("u1", 1000) is False
            assert guardrails.fallback_over_cap("u1", 1000) is True

            mock_dt.now.return_value.strftime.return_value = "20260102"
            # new day -- budget is fresh again, and the stale day's key is pruned
            assert guardrails.fallback_over_cap("u1", 1000) is False
            assert not any(k.endswith(":20260101") for k in guardrails._fallback_counts)

    def test_metric_emitted_only_when_fallback_invoked(self):
        with patch.object(guardrails, "_metric_inc") as mock_inc:
            guardrails.fallback_over_cap("u1", 1000)
            mock_inc.assert_called_once_with("spinr_ai_fallback_cap_total")
