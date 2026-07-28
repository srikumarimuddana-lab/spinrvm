"""Tool registry core: validation, dispatch, audience gating, capping.

These tests use throwaway specs registered directly into TOOL_REGISTRY —
no DB, no provider SDKs. The contract pinned here is what keeps a confused
or adversarial model harmless: unknown tools/args come back as error
results (never exceptions), the caller decides the audience, handlers are
time-boxed, and oversized results are capped before re-entering context.
"""

import asyncio

import pytest

from backend.ai import tools as ai_tools
from backend.ai.tools import (
    TOOL_REGISTRY,
    TOOL_RESULT_MAX_CHARS,
    ToolSpec,
    execute_tool,
    register,
    tool_defs_for,
    validate_args,
)

USER = {"id": "user-1", "is_driver": False}


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Each test gets a clean registry; mark it loaded so ensure_registry_loaded
    doesn't import domain modules underneath the test."""
    saved = dict(TOOL_REGISTRY)
    saved_loaded = ai_tools._registry_loaded
    TOOL_REGISTRY.clear()
    ai_tools._registry_loaded = True
    yield
    TOOL_REGISTRY.clear()
    TOOL_REGISTRY.update(saved)
    ai_tools._registry_loaded = saved_loaded


def _spec(name="echo", audiences=frozenset({"rider"}), schema=None, handler=None, **kw):
    async def default_handler(user, **args):
        return {"echo": args, "user_id": user["id"]}

    return ToolSpec(
        name=name,
        description="test tool",
        input_schema=schema or {"type": "object", "properties": {}, "required": []},
        handler=handler or default_handler,
        audiences=audiences,
        **kw,
    )


class TestValidateArgs:
    SCHEMA = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "status": {"type": "string", "enum": ["completed", "cancelled"]},
            "query": {"type": "string", "maxLength": 100},
        },
        "required": ["limit"],
    }

    def test_valid(self):
        assert validate_args(self.SCHEMA, {"limit": 5, "status": "completed"}) == []

    def test_missing_required(self):
        assert any("missing required" in e for e in validate_args(self.SCHEMA, {}))

    def test_unknown_key_rejected(self):
        errs = validate_args(self.SCHEMA, {"limit": 5, "user_id": "victim-2"})
        assert any("unknown argument: user_id" in e for e in errs)

    def test_type_mismatch(self):
        assert any("must be a integer" in e for e in validate_args(self.SCHEMA, {"limit": "five"}))

    def test_bool_is_not_integer(self):
        assert validate_args(self.SCHEMA, {"limit": True}) != []

    def test_bounds(self):
        assert any(">= 1" in e for e in validate_args(self.SCHEMA, {"limit": 0}))
        assert any("<= 10" in e for e in validate_args(self.SCHEMA, {"limit": 99}))

    def test_enum(self):
        errs = validate_args(self.SCHEMA, {"limit": 1, "status": "in_progress"})
        assert any("must be one of" in e for e in errs)

    def test_string_length_cap(self):
        errs = validate_args(self.SCHEMA, {"limit": 1, "query": "x" * 101})
        assert any("at most 100" in e for e in errs)


class TestRegistry:
    def test_duplicate_name_raises(self):
        register(_spec())
        with pytest.raises(ValueError):
            register(_spec())

    def test_tool_defs_sorted_and_audience_filtered(self):
        register(_spec(name="zeta"))
        register(_spec(name="alpha"))
        register(_spec(name="driver_only", audiences=frozenset({"driver"})))
        defs = tool_defs_for("rider")
        assert [d["name"] for d in defs] == ["alpha", "zeta"]
        assert {"name", "description", "input_schema"} == set(defs[0].keys())


class TestExecuteTool:
    @pytest.mark.anyio
    async def test_happy_path_injects_user(self):
        register(_spec())
        result, ok = await execute_tool("echo", {}, user=USER)
        assert ok is True
        assert result["user_id"] == "user-1"

    @pytest.mark.anyio
    async def test_unknown_tool_is_error_result(self):
        result, ok = await execute_tool("nope", {}, user=USER)
        assert ok is False
        assert "unknown tool" in result["error"]

    @pytest.mark.anyio
    async def test_audience_gate(self):
        register(_spec(name="driver_only", audiences=frozenset({"driver"})))
        result, ok = await execute_tool("driver_only", {}, user=USER, audience="rider")
        assert ok is False
        _, ok2 = await execute_tool("driver_only", {}, user=USER, audience="driver")
        assert ok2 is True

    @pytest.mark.anyio
    async def test_invalid_args_is_error_result(self):
        register(
            _spec(
                schema={
                    "type": "object",
                    "properties": {"n": {"type": "integer"}},
                    "required": ["n"],
                }
            )
        )
        result, ok = await execute_tool("echo", {"n": "NaN"}, user=USER)
        assert ok is False
        assert "must be a integer" in result["error"]

    @pytest.mark.anyio
    async def test_handler_exception_is_contained(self):
        async def boom(user, **args):
            raise RuntimeError("db exploded")

        register(_spec(handler=boom))
        result, ok = await execute_tool("echo", {}, user=USER)
        assert ok is False
        # Internals never leak to the model.
        assert "db exploded" not in result["error"]

    @pytest.mark.anyio
    async def test_timeout_is_contained(self, monkeypatch):
        monkeypatch.setattr(ai_tools, "TOOL_TIMEOUT_SECONDS", 0.01)

        async def slow(user, **args):
            await asyncio.sleep(1)
            return {}

        register(_spec(handler=slow))
        result, ok = await execute_tool("echo", {}, user=USER)
        assert ok is False
        assert "too long" in result["error"]

    @pytest.mark.anyio
    async def test_per_tool_timeout_override_shrinks(self):
        """A spec-level timeout wins over the global default (tight side)."""

        async def slow(user, **args):
            await asyncio.sleep(1)
            return {}

        register(_spec(handler=slow, timeout_seconds=0.01))
        result, ok = await execute_tool("echo", {}, user=USER)
        assert ok is False
        assert "too long" in result["error"]

    @pytest.mark.anyio
    async def test_per_tool_timeout_override_extends(self, monkeypatch):
        """A spec-level timeout also wins on the generous side — the Maps
        fan-out tools (find_place / get_fare_quote / propose_ride_booking)
        legitimately exceed the 5 s global default at their worst case, and
        used to die mid-quote with 'the lookup took too long'."""
        monkeypatch.setattr(ai_tools, "TOOL_TIMEOUT_SECONDS", 0.01)

        async def slowish(user, **args):
            await asyncio.sleep(0.05)
            return {"ok": True}

        register(_spec(handler=slowish, timeout_seconds=2.0))
        result, ok = await execute_tool("echo", {}, user=USER)
        assert ok is True
        assert result["ok"] is True

    def test_maps_fanout_tools_carry_extended_timeout(self):
        """The three booking tools that fan out to Google Maps must keep a
        generous per-tool timeout — losing it reintroduces the mid-quote
        timeout this override exists to fix."""
        saved = dict(TOOL_REGISTRY)
        TOOL_REGISTRY.clear()
        ai_tools._registry_loaded = False
        try:
            ai_tools.ensure_registry_loaded()
            for name in ("find_place", "get_fare_quote", "propose_ride_booking"):
                spec = TOOL_REGISTRY[name]
                assert spec.timeout_seconds is not None and spec.timeout_seconds > ai_tools.TOOL_TIMEOUT_SECONDS, name
        finally:
            TOOL_REGISTRY.clear()
            TOOL_REGISTRY.update(saved)
            ai_tools._registry_loaded = True

    @pytest.mark.anyio
    async def test_oversized_result_capped(self):
        async def huge(user, **args):
            return {"rows": ["x" * 100] * 200}

        register(_spec(handler=huge))
        result, ok = await execute_tool("echo", {}, user=USER)
        assert ok is True
        assert result["_truncated"] is True
        assert len(result["preview"]) <= TOOL_RESULT_MAX_CHARS

    @pytest.mark.anyio
    async def test_client_action_survives_truncation(self):
        """The card payload never enters the model context, so it must not
        count against — or be destroyed by — the result cap."""
        action = {"type": "fare_quote", "quotes": [{"vehicle_type": "Economy"}]}

        async def huge_with_card(user, **args):
            return {"rows": ["x" * 100] * 200, "_client_action": action}

        register(_spec(handler=huge_with_card))
        result, ok = await execute_tool("echo", {}, user=USER)
        assert ok is True
        assert result["_truncated"] is True
        assert result["_client_action"] == action

    @pytest.mark.anyio
    async def test_guardrail_keys_survive_truncation(self):
        """A multi-vehicle quote can blow the 4000-char cap; before this
        guard, truncation deleted the 'Do NOT quote on it' note and the
        needs_correction sentinel while the client card survived — the model
        lost exactly the instruction that made the oversized result safe."""

        async def huge_with_note(user, **args):
            return {
                "rows": ["x" * 100] * 200,
                "note": "Warning: do NOT quote on it.",
                "needs_correction": "dropoff_label_mismatch",
                "imprecise_address": True,
            }

        register(_spec(handler=huge_with_note))
        result, ok = await execute_tool("echo", {}, user=USER)
        assert ok is True
        assert result["_truncated"] is True
        assert result["note"] == "Warning: do NOT quote on it."
        assert result["needs_correction"] == "dropoff_label_mismatch"
        assert result["imprecise_address"] is True
