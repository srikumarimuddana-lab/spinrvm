"""Report formatters — turn raw API data into human-readable text."""
from __future__ import annotations

from typing import Any


def format_infra(data: dict[str, Any]) -> str:
    lines = ["*Infrastructure Health*"]
    bh = data.get("backend_health", {})
    lines.append(f"  Backend: {bh.get('status', 'unknown')}")
    br = data.get("backend_ready", {})
    lines.append(f"  Ready: {br.get('status', 'unknown')}")
    lines.append(f"  Metrics: {'available' if data.get('metrics_available') else 'unavailable'}")
    lines.append(f"  URL: {data.get('configured_url', '?')}")
    return "\n".join(lines)


def format_ops(data: dict[str, Any]) -> str:
    lines = ["*Operations Report*"]
    for section in ("rides", "drivers", "payments", "dispatch"):
        block = data.get(section, {})
        status = block.get("status", "unknown")
        lines.append(f"\n  [{section.title()}] status={status}")
        if status == "ok" and isinstance(block.get("data"), dict):
            for k, v in block["data"].items():
                lines.append(f"    {k}: {v}")
    return "\n".join(lines)


def format_full(data: dict[str, Any]) -> str:
    parts = []
    if "infra" in data:
        parts.append(format_infra(data["infra"]))
    if "ops" in data:
        parts.append(format_ops(data["ops"]))
    return "\n\n".join(parts) if parts else "No report data available."
