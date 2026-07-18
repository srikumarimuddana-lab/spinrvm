"""ADR-008: BACKGROUND_LOOPS_ENABLED=false must spawn zero background loops.

The canary deploy stage relies on this — it shares the production data
plane, and a canary running payment/purge loops would execute new loop
code against prod data before promotion.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from backend.core import lifespan as lifespan_mod
from backend.core.config import settings


def _enter_lifespan():
    """Run the real lifespan context to the yield point and back."""

    async def _run():
        app = MagicMock()
        app.state = MagicMock()
        async with lifespan_mod.lifespan(app):
            # Capture the spawned-loop list at the yield point.
            return list(app.state.background_tasks)

    return asyncio.run(_run())


class TestLoopGate:
    def test_loops_disabled_spawns_nothing(self):
        with (
            patch.object(lifespan_mod, "init_database", AsyncMock(return_value=None)),
            patch.object(settings, "ENV", "development"),
            patch.object(settings, "BACKGROUND_LOOPS_ENABLED", False),
            patch.object(settings, "DEPLOY_STAGE", "canary"),
        ):
            tasks = _enter_lifespan()
        assert tasks == [], f"expected no background loops, got {[t.get_name() for t in tasks]}"

    def test_loops_enabled_spawns_loops(self):
        with (
            patch.object(lifespan_mod, "init_database", AsyncMock(return_value=None)),
            patch.object(settings, "ENV", "development"),
            patch.object(settings, "BACKGROUND_LOOPS_ENABLED", True),
        ):
            tasks = _enter_lifespan()
        # Exact count evolves with the codebase; the invariant is that the
        # loop set is non-trivially large and includes the dispatch-critical
        # loops. All were cancelled on context exit.
        names = [t.get_name() for t in tasks]
        assert len(names) >= 10
        assert any("scheduled_dispatcher" in n for n in names)
        assert all(t.cancelled() or t.done() for t in tasks)
