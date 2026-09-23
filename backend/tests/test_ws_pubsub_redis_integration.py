"""Real Redis contract test for durable WebSocket replay publication.

Set WS_TEST_REDIS_URL to a disposable loopback Redis URL using database 15,
for example ``redis://127.0.0.1:6399/15``. The test refuses non-loopback
addresses and always deletes the unique test keys it creates.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from unittest.mock import MagicMock
from urllib.parse import urlparse

import pytest
import redis.asyncio as redis_asyncio

from backend.utils.ws_pubsub import CHANNEL, _WSPubSub

_REDIS_URL = os.environ.get("WS_TEST_REDIS_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _REDIS_URL, reason="set WS_TEST_REDIS_URL to disposable loopback Redis database 15"),
]


@pytest.mark.anyio
async def test_concurrent_durable_publishes_preserve_order_payload_and_retention():
    parsed = urlparse(_REDIS_URL)
    assert parsed.hostname in {"127.0.0.1", "localhost", "::1"}, "test Redis must be loopback-only"
    assert parsed.path == "/15", "test Redis must use disposable database 15"

    client = redis_asyncio.from_url(_REDIS_URL, decode_responses=True)
    subscriber = None
    client_id = f"pr5725-ws-{uuid.uuid4().hex}"
    seq_key = f"spinr:ws:seq:{client_id}"
    outbox_key = f"spinr:ws:outbox:{client_id}"
    connected = False
    try:
        await client.ping()
        connected = True
        subscriber = client.pubsub()
        await subscriber.subscribe(CHANNEL)
        await subscriber.get_message(timeout=1)

        publisher = _WSPubSub()
        publisher._redis = client
        publisher._pubsub = MagicMock()
        publisher._task = asyncio.current_task()
        payloads = [
            {
                "type": "ride_offered",
                "id": str(index),
                "unicode": "Saskatoon 🚗",
                "quoted": 'a"b',
                "nested": {"ok": True},
            }
            for index in range(52)
        ]

        assert all(await asyncio.gather(*(publisher.publish(client_id, message) for message in payloads)))

        replay = await publisher.get_outbox(client_id)
        assert [item["seq"] for item in replay] == list(range(3, 53))
        assert len({item["data"]["id"] for item in replay}) == 50
        assert {item["data"]["id"] for item in replay} <= {str(index) for index in range(52)}
        assert all(
            item["data"]["unicode"] == "Saskatoon 🚗"
            and item["data"]["quoted"] == 'a"b'
            and item["data"]["nested"] == {"ok": True}
            for item in replay
        )
        ttl_ms = await client.pttl(outbox_key)
        assert 0 < ttl_ms <= 300_000

        published = []
        for _ in payloads:
            message = await asyncio.wait_for(
                subscriber.get_message(ignore_subscribe_messages=True, timeout=1),
                timeout=2,
            )
            assert message and message["type"] == "message"
            envelope = json.loads(message["data"])
            assert envelope["kind"] == "unicast"
            assert envelope["client_id"] == client_id
            published.append(envelope["message"])
        assert [item["seq"] for item in published] == list(range(1, 53))
        assert {item["data"]["id"] for item in published} == {str(index) for index in range(52)}
        assert replay == published[2:]
    finally:
        if subscriber is not None:
            await subscriber.unsubscribe(CHANNEL)
            await subscriber.aclose()
        if connected:
            await client.delete(seq_key, outbox_key)
        await client.aclose()
