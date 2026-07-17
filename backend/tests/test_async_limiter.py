"""Behavioral contract for the async endpoint limiter."""

import asyncio

import pytest
from limits.aio.storage import MemoryStorage
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from backend.utils.async_limiter import AsyncLimiter


def _request(path: str = "/probe", method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )


@pytest.mark.anyio
async def test_limit_raises_slowapi_exception_and_sets_request_state() -> None:
    limiter = AsyncLimiter(key_func=lambda request: request.client.host, storage=MemoryStorage())

    @limiter.limit("1/minute")
    async def endpoint(request: Request):
        return "ok"

    request = _request()
    assert await endpoint(request=request) == "ok"
    assert request.state.view_rate_limit[0].amount == 1

    with pytest.raises(RateLimitExceeded) as exc_info:
        await endpoint(request=request)

    assert exc_info.value.limit.limit.amount == 1


@pytest.mark.anyio
async def test_callable_limit_and_disabled_state_preserve_decorator_contract() -> None:
    limiter = AsyncLimiter(key_func=lambda: "global", storage=MemoryStorage())

    @limiter.limit(lambda: "1/minute")
    async def endpoint(request: Request):
        return "ok"

    request = _request()
    assert await endpoint(request=request) == "ok"
    limiter.enabled = False
    assert await endpoint(request=request) == "ok"


@pytest.mark.anyio
async def test_method_filter_and_cost_are_honored() -> None:
    limiter = AsyncLimiter(key_func=lambda request: "client", storage=MemoryStorage())

    @limiter.limit("2/minute", methods=["post"], cost=2)
    async def endpoint(request: Request):
        return "ok"

    assert await endpoint(request=_request(method="GET")) == "ok"
    assert await endpoint(request=_request(method="POST")) == "ok"
    with pytest.raises(RateLimitExceeded):
        await endpoint(request=_request(method="POST"))


@pytest.mark.anyio
async def test_awaited_storage_does_not_block_unrelated_coroutines() -> None:
    class DelayedStorage(MemoryStorage):
        async def incr(self, *args, **kwargs):
            await asyncio.sleep(0.05)
            return await super().incr(*args, **kwargs)

    limiter = AsyncLimiter(key_func=lambda request: "client", storage=DelayedStorage())

    @limiter.limit("1/minute")
    async def endpoint(request: Request):
        return "ok"

    ticker = asyncio.create_task(asyncio.sleep(0.01))
    assert await endpoint(request=_request()) == "ok"
    assert ticker.done()
    await ticker
