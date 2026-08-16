"""Stripe SDK calls in async routes must not block the event loop."""

from __future__ import annotations

import ast
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _load_payments_function(name: str, **globals_override):
    """Compile one real route function without importing the full app graph."""
    source_path = Path(__file__).parents[1] / "routes" / "payments.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    isolated = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(isolated)
    namespace = {"asyncio": asyncio, **globals_override}
    exec(compile(isolated, source_path, "exec"), namespace)
    return namespace[name]


@pytest.mark.anyio
async def test_customer_creation_yields_to_event_loop() -> None:
    release = threading.Event()

    def blocking_create(**_kwargs):
        assert release.wait(timeout=1)
        return SimpleNamespace(id="cus_created")

    db = SimpleNamespace(
        get_user_by_id=AsyncMock(
            side_effect=[
                {"id": "user-1", "stripe_customer_id": None},
                {"id": "user-1", "stripe_customer_id": "cus_created"},
            ]
        ),
        update_one=AsyncMock(),
    )
    stripe = SimpleNamespace(Customer=SimpleNamespace(create=blocking_create))
    # The isolated namespace must carry every global the function body reads.
    # get_or_create_stripe_customer stamps the new customer's Stripe mode, so
    # the real (pure, no-I/O) helpers from utils.stripe_mode go in here too.
    from backend.utils.stripe_mode import key_mode, object_mode, stale_by_mode

    get_or_create_stripe_customer = _load_payments_function(
        "get_or_create_stripe_customer",
        db_supabase=db,
        stripe=stripe,
        HTTPException=RuntimeError,
        key_mode=key_mode,
        object_mode=object_mode,
        stale_by_mode=stale_by_mode,
        # Decides the identity kwargs (email) sent to Stripe. Loaded the same
        # isolated way rather than stubbed, so this test keeps exercising the
        # REAL argument set — a stub would let the two drift apart silently.
        # It is pure and reads no globals of its own.
        _customer_identity_fields=_load_payments_function("_customer_identity_fields"),
    )

    timer = threading.Timer(0.1, release.set)
    timer.start()
    ticker = asyncio.create_task(asyncio.sleep(0.01))
    try:
        assert await get_or_create_stripe_customer("user-1", "sk_test") == "cus_created"
    finally:
        release.set()
        timer.cancel()

    assert ticker.done(), "synchronous Stripe call blocked the event loop"
    await ticker


def test_all_payments_stripe_sdk_calls_are_offloaded() -> None:
    assert _blocking_stripe_lines("payments.py") == []


@pytest.mark.parametrize("route_file", ["wallet.py", "corporate_accounts.py"])
def test_customer_creation_routes_offload_stripe(route_file: str) -> None:
    blocking_calls = _blocking_stripe_lines(route_file)
    assert blocking_calls == [], f"{route_file} blocks on Stripe SDK calls at lines {blocking_calls}"


def test_dispute_refund_offloads_stripe() -> None:
    blocking_calls = _blocking_stripe_lines("disputes.py", stripe_name="_stripe")
    assert blocking_calls == [], f"disputes.py blocks on Stripe SDK calls at lines {blocking_calls}"


def _blocking_stripe_lines(route_file: str, stripe_name: str = "stripe") -> list[int]:
    source_path = Path(__file__).parents[1] / "routes" / route_file
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    blocking_calls: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == stripe_name
        ):
            continue

        ancestor = parents.get(node)
        while ancestor is not None and not isinstance(ancestor, ast.Lambda):
            ancestor = parents.get(ancestor)
        if ancestor is None:
            blocking_calls.append(node.lineno)
            continue

        wrapper = parents.get(ancestor)
        if not (
            isinstance(wrapper, ast.Call)
            and isinstance(wrapper.func, ast.Attribute)
            and isinstance(wrapper.func.value, ast.Name)
            and wrapper.func.value.id == "asyncio"
            and wrapper.func.attr == "to_thread"
        ):
            blocking_calls.append(node.lineno)

    return blocking_calls
