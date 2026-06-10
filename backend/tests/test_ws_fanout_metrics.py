"""KPI instrumentation for WebSocket fan-out (spinr_ws_fanout_duration_ms).

send_personal_message must record a latency sample on both delivery paths:
  - path=pubsub  (publish onto the Redis channel; cross-replica delivery)
  - path=local   (direct socket send when pub/sub is disabled/degraded)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils import metrics


def _fanout_count(path: str) -> int:
    series = metrics.snapshot()["histograms"].get("spinr_ws_fanout_duration_ms", {})
    cell = series.get((("path", path),))
    return cell["count"] if cell else 0


@pytest.mark.anyio
async def test_local_fanout_observed():
    from backend.socket_manager import ConnectionManager

    mgr = ConnectionManager()
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    await mgr.connect(ws, "rider_metrics")

    before = _fanout_count("local")
    with patch("backend.utils.ws_pubsub.pubsub.publish", AsyncMock(return_value=False)):
        await mgr.send_personal_message({"type": "ping"}, "rider_metrics")

    ws.send_json.assert_awaited_once_with({"type": "ping"})
    assert _fanout_count("local") == before + 1


@pytest.mark.anyio
async def test_pubsub_fanout_observed():
    from backend.socket_manager import ConnectionManager

    mgr = ConnectionManager()
    before = _fanout_count("pubsub")
    with patch("backend.utils.ws_pubsub.pubsub.publish", AsyncMock(return_value=True)):
        await mgr.send_personal_message({"type": "ping"}, "rider_metrics")

    assert _fanout_count("pubsub") == before + 1
