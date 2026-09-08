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


def test_payment_retry_loop_offloads_stripe() -> None:
    """C86: retry_failed_payments() (the payment_retry (5min) background loop)
    used to call PaymentIntent.retrieve/confirm/capture directly -- up to 3
    bare, synchronous Stripe HTTP round-trips per retried ride, per tick,
    each blocking the whole process's event loop, not just this loop."""
    blocking_calls = _blocking_stripe_lines("payment_retry.py", subdir="utils")
    assert blocking_calls == [], f"utils/payment_retry.py blocks on Stripe SDK calls at lines {blocking_calls}"


def test_reconciliation_offloads_stripe() -> None:
    """C86: _sum_stripe_intents() paginated stripe.PaymentIntent.list() with a
    bare, synchronous call per page -- now runs the whole pagination loop in
    a thread (see _list_and_sum in utils/reconciliation.py)."""
    blocking_calls = _blocking_stripe_lines("reconciliation.py", subdir="utils")
    assert blocking_calls == [], f"utils/reconciliation.py blocks on Stripe SDK calls at lines {blocking_calls}"


def test_stripe_reconcile_offloads_stripe() -> None:
    """C86: _run_reconciliation_tick()'s auto_paging_iter() over yesterday's
    PaymentIntents used a bare, synchronous call per page -- now runs the
    whole pagination loop in a thread (see _list_stripe_pis in
    utils/stripe_reconcile.py)."""
    blocking_calls = _blocking_stripe_lines("stripe_reconcile.py", stripe_name="_stripe", subdir="utils")
    assert blocking_calls == [], f"utils/stripe_reconcile.py blocks on Stripe SDK calls at lines {blocking_calls}"


@pytest.mark.parametrize(
    "service_file",
    ["stripe_payout_sync_service.py", "stripe_mapping_import_service.py"],
)
def test_already_offloaded_services_stay_offloaded(service_file: str) -> None:
    """C86 re-triage found these two already correctly ran their Stripe list
    calls via a named nested function + asyncio.to_thread(name) -- the
    original F8-validation pass missed this because it only grepped for a
    bare `stripe.*.retrieve/create/...(` call, not whether the enclosing
    function was itself thread-wrapped from its own caller. Locks that
    finding in as a regression test."""
    blocking_calls = _blocking_stripe_lines(service_file, subdir="services")
    assert blocking_calls == [], f"services/{service_file} blocks on Stripe SDK calls at lines {blocking_calls}"


def test_checker_flags_a_genuinely_unwrapped_lambda_call() -> None:
    """Proves idiom 1's detection isn't vacuously true: a bare stripe call
    inside a lambda that is NOT passed to asyncio.to_thread must still be
    flagged."""
    lines = _blocking_stripe_lines_from_source("run(lambda: stripe.PaymentIntent.retrieve('pi_1'))")
    assert lines == [1]


def test_checker_flags_a_genuinely_unwrapped_nested_function() -> None:
    """Proves idiom 2's detection isn't vacuously true: a nested function
    containing a bare stripe call, never passed to asyncio.to_thread
    anywhere, must still be flagged."""
    lines = _blocking_stripe_lines_from_source("def _list():\n    return stripe.PaymentIntent.list()\n_list()\n")
    assert lines == [2]


def test_checker_clears_a_correctly_wrapped_nested_function() -> None:
    """The positive counterpart to the two tests above -- confirms idiom 2
    actually clears the call when asyncio.to_thread(name) is present."""
    lines = _blocking_stripe_lines_from_source(
        "def _list():\n    return stripe.PaymentIntent.list()\nasyncio.to_thread(_list)\n"
    )
    assert lines == []


def _blocking_stripe_lines_from_source(source: str, stripe_name: str = "stripe") -> list[int]:
    """Same logic as _blocking_stripe_lines, against an in-memory source
    string instead of a file on disk -- for testing the checker itself."""
    return _blocking_stripe_lines_from_tree(ast.parse(source), stripe_name)


def _blocking_stripe_lines(route_file: str, stripe_name: str = "stripe", subdir: str = "routes") -> list[int]:
    source_path = Path(__file__).parents[1] / subdir / route_file
    return _blocking_stripe_lines_from_tree(ast.parse(source_path.read_text(encoding="utf-8")), stripe_name)


def _blocking_stripe_lines_from_tree(tree: ast.Module, stripe_name: str = "stripe") -> list[int]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    # Idiom 2 (C86): a multi-statement body (e.g. a pagination loop over
    # auto_paging_iter()) can't be inlined as a lambda -- this repo's other
    # safe idiom for that shape is a named nested function whose only caller
    # is `asyncio.to_thread(name, ...)` (see
    # services/stripe_payout_sync_service.py's `_list()`). Collect every name
    # passed as the first positional arg to an `asyncio.to_thread(...)` call
    # anywhere in the file.
    to_thread_wrapped_names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "asyncio"
            and node.func.attr == "to_thread"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            to_thread_wrapped_names.add(node.args[0].id)

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
        while ancestor is not None and not isinstance(ancestor, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            ancestor = parents.get(ancestor)

        if isinstance(ancestor, ast.Lambda):
            # Idiom 1: asyncio.to_thread(lambda: stripe.X.Y(...))
            wrapper = parents.get(ancestor)
            if (
                isinstance(wrapper, ast.Call)
                and isinstance(wrapper.func, ast.Attribute)
                and isinstance(wrapper.func.value, ast.Name)
                and wrapper.func.value.id == "asyncio"
                and wrapper.func.attr == "to_thread"
            ):
                continue
            blocking_calls.append(node.lineno)
            continue

        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)) and ancestor.name in to_thread_wrapped_names:
            continue  # Idiom 2 -- the whole enclosing function runs in a thread

        blocking_calls.append(node.lineno)

    return blocking_calls
