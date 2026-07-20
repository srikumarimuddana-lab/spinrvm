# Regression guard for separating live WebSocket markers from route history.
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "routes" / "websocket.py").read_text(encoding="utf-8")


def test_ephemeral_driver_location_does_not_write_a_breadcrumb() -> None:
    """A ``durable:false`` marker updates/fans out live state without history writes."""
    assert 'if data.get("durable", True):\n                        await buffer_ride_breadcrumb(' in SOURCE


def test_legacy_driver_location_messages_remain_durable() -> None:
    """Omitting the flag preserves the legacy breadcrumb behavior during rollout."""
    assert 'data.get("durable", True)' in SOURCE
