"""Central data-scope enforcement (PIPEDA): the assistant can only ever read
the signed-in user's own data.

Covers the guarantees in backend/ai/tools.py that do NOT depend on any handler
remembering to filter by user id:
- a tool may not declare an identity arg (user_id/rider_id/driver_id/…)
- every id-style arg must be classified owned/public at registration
- a live call carrying an identity arg is rejected before the handler runs
- owned-resource ids are ownership-verified centrally (foreign id → not found)
- no authenticated user id → fail closed
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.ai import threat, tools, tools_rides
from backend.ai.tools import ToolSpec, execute_tool, register

RIDER = {"id": "rider-1"}


async def _noop(user, **kw):  # pragma: no cover - should never be reached in block tests
    return {"reached": True}


class TestRegistrationContract:
    def test_declaring_identity_arg_is_rejected(self):
        with pytest.raises(ValueError, match="forbidden identity arg"):
            register(
                ToolSpec(
                    name="_evil_identity_tool",
                    description="x",
                    input_schema={"type": "object", "properties": {"rider_id": {"type": "string"}}},
                    handler=_noop,
                )
            )
        assert "_evil_identity_tool" not in tools.TOOL_REGISTRY

    def test_unclassified_id_arg_is_rejected(self):
        with pytest.raises(ValueError, match="unclassified id arg"):
            register(
                ToolSpec(
                    name="_evil_unclassified_tool",
                    description="x",
                    input_schema={"type": "object", "properties": {"invoice_id": {"type": "string"}}},
                    handler=_noop,
                )
            )

    def test_public_id_arg_is_allowed(self):
        spec = register(
            ToolSpec(
                name="_ok_public_id_tool",
                description="x",
                input_schema={"type": "object", "properties": {"widget_id": {"type": "string"}}},
                handler=_noop,
                public_id_args=frozenset({"widget_id"}),
            )
        )
        assert spec.name in tools.TOOL_REGISTRY
        del tools.TOOL_REGISTRY["_ok_public_id_tool"]  # keep global registry clean

    def test_every_registered_id_arg_is_classified(self):
        tools.ensure_registry_loaded()
        for name, spec in tools.TOOL_REGISTRY.items():
            for prop in spec.input_schema.get("properties") or {}:
                if tools._is_id_arg(prop):
                    assert prop not in tools.FORBIDDEN_ID_ARGS, f"{name}: {prop} is a banned identity arg"
                    assert prop in spec.owned_id_args or prop in spec.public_id_args, (
                        f"{name}: id arg {prop} is unclassified"
                    )


class TestRuntimeEnforcement:
    @pytest.mark.anyio
    async def test_missing_user_id_fails_closed(self):
        result, ok = await execute_tool("get_active_ride", {}, user={})
        assert not ok and result["error"] == "not authorized"

    @pytest.mark.anyio
    async def test_model_supplied_identity_arg_blocked(self):
        # get_ride_history ignores unknown args at the schema layer, but the
        # identity denylist is the belt-and-suspenders guarantee. Use a tool and
        # smuggle rider_id past validate_args by targeting the denylist directly.
        with patch.object(tools, "validate_args", return_value=[]):
            with patch.object(tools_rides, "db_supabase") as db:
                result, ok = await execute_tool("get_ride_history", {"rider_id": "someone-else"}, user=RIDER)
        assert not ok and result["error"] == "not permitted"
        db.get_rows.assert_not_called()  # handler never ran

    @pytest.mark.anyio
    async def test_smuggled_identity_arg_blocked_ahead_of_schema(self):
        # Regression (Codex, PR #2049): get_ride_history's real schema rejects
        # rider_id as an unknown argument. Unless the identity denylist runs
        # BEFORE validate_args, the attempt is a generic "error" and never
        # reaches the security feed. It must classify as blocked. No
        # validate_args patch here — that's the whole point.
        with patch.object(tools_rides, "db_supabase") as db:
            result, ok = await execute_tool("get_ride_history", {"rider_id": "someone-else"}, user=RIDER)
        assert not ok and result["error"] == "not permitted"
        db.get_rows.assert_not_called()  # handler never ran

    @pytest.mark.anyio
    async def test_smuggled_identity_arg_records_security_event(self):
        with patch.object(tools_rides, "db_supabase"), patch.object(threat, "record_security_event") as rec_evt:
            await execute_tool("get_ride_history", {"rider_id": "someone-else"}, user=RIDER)
        assert rec_evt.called
        kw = rec_evt.call_args.kwargs
        assert kw["event_type"] == "tool_blocked" and kw["severity"] == "critical"
        assert kw["source"] == "tool"

    @pytest.mark.anyio
    async def test_foreign_owned_id_is_not_found(self):
        foreign = {"id": "ride-9", "rider_id": "OTHER-USER"}
        with patch.object(tools_rides.db_supabase, "get_ride", AsyncMock(return_value=foreign)) as get_ride:
            result, ok = await execute_tool("get_ride_details", {"ride_id": "ride-9"}, user=RIDER)
        assert not ok and result["error"] == "ride not found"
        get_ride.assert_awaited()  # ownership was checked

    @pytest.mark.anyio
    async def test_owned_id_reaches_handler(self):
        mine = {"id": "ride-1", "rider_id": "rider-1", "status": "completed", "driver_id": None}
        with patch.object(tools_rides.db_supabase, "get_ride", AsyncMock(return_value=mine)):
            result, ok = await execute_tool("get_ride_details", {"ride_id": "ride-1"}, user=RIDER)
        assert ok
        assert result["ride"]["id"] == "ride-1"


class TestToolAudit:
    """Layer-7 audit: every tool call is recorded PII-safely (arg names, not
    values; never results)."""

    def test_classify_outcome(self):
        assert tools._classify_outcome({}, True) == "success"
        assert tools._classify_outcome({"error": "not permitted"}, False) == "blocked"
        assert tools._classify_outcome({"error": "ride not found"}, False) == "not_found"
        assert tools._classify_outcome({"error": "the lookup took too long — try again"}, False) == "timeout"
        assert tools._classify_outcome({"error": "boom"}, False) == "error"

    @pytest.mark.anyio
    async def test_successful_call_is_audited(self):
        mine = {"id": "ride-1", "rider_id": "rider-1", "status": "completed", "driver_id": None}
        user = {**RIDER, "_conversation_id": "conv-9"}
        with (
            patch.object(tools_rides.db_supabase, "get_ride", AsyncMock(return_value=mine)),
            patch.object(tools, "_schedule_tool_audit") as sched,
        ):
            await execute_tool("get_ride_details", {"ride_id": "ride-1"}, user=user, audience="rider")
        rec = sched.call_args[0][0]
        assert rec["tool_name"] == "get_ride_details"
        assert rec["user_id"] == "rider-1"
        assert rec["audience"] == "rider"
        assert rec["ok"] is True and rec["outcome"] == "success"
        assert rec["arg_keys"] == ["ride_id"]  # NAME only, no value
        assert rec["conversation_id"] == "conv-9"
        assert isinstance(rec["latency_ms"], int)

    @pytest.mark.anyio
    async def test_blocked_call_audited_as_blocked_without_arg_values(self):
        with (
            patch.object(tools, "validate_args", return_value=[]),
            patch.object(tools, "_schedule_tool_audit") as sched,
        ):
            await execute_tool("get_ride_history", {"rider_id": "victim-123"}, user=RIDER)
        rec = sched.call_args[0][0]
        assert rec["ok"] is False and rec["outcome"] == "blocked"
        assert rec["arg_keys"] == ["rider_id"]  # the arg NAME is recorded
        assert "victim-123" not in str(rec)  # the arg VALUE is never logged

    @pytest.mark.anyio
    async def test_non_dict_args_do_not_crash_audit(self):
        # Regression (Codex, PR #2049): a provider can emit a JSON list/string
        # as tool arguments. The audit path must collect no arg_keys rather than
        # AttributeError on .keys() and abort the whole chat turn.
        with patch.object(tools, "_schedule_tool_audit") as sched:
            result, ok = await execute_tool("get_ride_history", ["not", "a", "dict"], user=RIDER)
        assert not ok  # malformed args surface as a validation error
        rec = sched.call_args[0][0]
        assert rec["arg_keys"] == []
