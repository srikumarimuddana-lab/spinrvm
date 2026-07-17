"""Async-compatible subset of SlowAPI's endpoint limiter contract."""

from __future__ import annotations

import asyncio
import inspect
from functools import wraps
from typing import Callable

from limits import parse_many
from limits.aio.storage import MemoryStorage, RedisStorage
from limits.aio.strategies import FixedWindowRateLimiter
from slowapi.errors import RateLimitExceeded
from slowapi.wrappers import Limit
from starlette.requests import Request


def _storage_from_uri(uri: str):
    if uri == "memory://":
        return MemoryStorage()
    if uri.startswith(("redis://", "rediss://", "async+redis://", "async+rediss://")):
        return RedisStorage(uri, implementation="redispy", wrap_exceptions=True)
    raise ValueError(f"Unsupported async rate-limit storage URI: {uri.split(':', 1)[0]}")


class AsyncLimiter:
    """Await rate-limit storage without changing route decorator call sites."""

    def __init__(self, *, key_func, default_limits=None, storage_uri="memory://", storage=None) -> None:
        self.enabled = True
        self._key_func = key_func
        self._default_limits = [item for value in (default_limits or []) for item in parse_many(value)]
        self._storage = storage or _storage_from_uri(storage_uri)
        self._limiter = FixedWindowRateLimiter(self._storage)

    def limit(
        self,
        limit_value,
        key_func: Callable[..., str] | None = None,
        per_method: bool = False,
        methods: list[str] | None = None,
        error_message: str | None = None,
        exempt_when: Callable[..., bool] | None = None,
        cost: int | Callable[..., int] = 1,
        override_defaults: bool = True,
    ):
        def decorator(func):
            if not asyncio.iscoroutinefunction(func):
                raise TypeError("AsyncLimiter can only decorate async endpoints")

            parameters = list(inspect.signature(func).parameters)
            connection_name = next(
                (name for name in parameters if name in {"request", "websocket"}),
                None,
            )
            if connection_name is None:
                raise TypeError(f'No "request" or "websocket" argument on function "{func.__name__}"')
            connection_index = parameters.index(connection_name)

            @wraps(func)
            async def wrapper(*args, **kwargs):
                if self.enabled:
                    request = kwargs.get(connection_name)
                    if request is None and len(args) > connection_index:
                        request = args[connection_index]
                    if not isinstance(request, Request):
                        raise TypeError("parameter `request` must be an instance of starlette.requests.Request")
                    await self._check(
                        request,
                        limit_value,
                        key_func or self._key_func,
                        per_method,
                        methods,
                        error_message,
                        exempt_when,
                        cost,
                        override_defaults,
                    )
                return await func(*args, **kwargs)

            return wrapper

        return decorator

    async def _check(
        self, request, limit_value, key_func, per_method, methods, error_message, exempt_when, cost, override_defaults
    ) -> None:
        if exempt_when is not None and exempt_when():
            return
        if methods is not None and request.method.lower() not in {method.lower() for method in methods}:
            return

        value = limit_value() if callable(limit_value) else limit_value
        items = parse_many(value)
        if not override_defaults:
            items.extend(self._default_limits)

        limit_key = key_func(request) if "request" in inspect.signature(key_func).parameters else key_func()
        scope = request.url.path
        if per_method:
            scope = f"{scope}:{request.method}"
        identifiers = [limit_key, scope]
        if not all(identifiers):
            return

        limit_for_header = None
        for item in items:
            wrapped = Limit(
                item, key_func, None, per_method, methods, error_message, exempt_when, cost, override_defaults
            )
            if limit_for_header is None or item < limit_for_header[0]:
                limit_for_header = (item, identifiers)
            request_cost = cost(request) if callable(cost) else cost
            if not await self._limiter.hit(item, *identifiers, cost=request_cost):
                request.state.view_rate_limit = (item, identifiers)
                raise RateLimitExceeded(wrapped)

        request.state.view_rate_limit = limit_for_header
