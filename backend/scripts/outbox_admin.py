#!/usr/bin/env python3
"""Inspect and redrive transactional-outbox dead letters.

Commands::

    python -m scripts.outbox_admin list-dead
    python -m scripts.outbox_admin show <id>
    python -m scripts.outbox_admin redrive <id> --actor-id <ops-id>

Output is IDs, topic, ride_id, attempt counts, timestamps, and allow-listed
error codes only. Email, phone, GPS, and provider exception text are never
printed even if a malformed row somehow contains them.

Requires the same ``SUPABASE_URL`` / ``SUPABASE_SERVICE_ROLE_KEY`` as the
backend. No live send is performed; redrive only resets a dead-lettered row
to ``pending`` so the worker can claim it again.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SAFE_COLUMNS = (
    "id",
    "topic",
    "dedupe_key",
    "status",
    "attempt_count",
    "max_attempts",
    "available_at",
    "leased_until",
    "leased_by",
    "dead_lettered_at",
    "last_error_code",
    "redrive_count",
    "created_at",
    "updated_at",
)


def public_view(row: Dict[str, Any]) -> Dict[str, Any]:
    """Project a row to operator-safe fields. Payload extras are dropped."""
    out: Dict[str, Any] = {key: row.get(key) for key in _SAFE_COLUMNS}
    payload = row.get("payload")
    ride_id = None
    if isinstance(payload, dict):
        candidate = payload.get("ride_id")
        if isinstance(candidate, str) and candidate:
            ride_id = candidate
    out["ride_id"] = ride_id
    return out


async def cmd_list_dead(limit: int = 50) -> list[Dict[str, Any]]:
    from services import outbox

    rows = await outbox.list_dead_letters(limit=limit)
    return [public_view(r) for r in rows if isinstance(r, dict)]


async def cmd_show(message_id: str) -> Optional[Dict[str, Any]]:
    from services import outbox

    row = await outbox.get_message(message_id)
    if not isinstance(row, dict):
        return None
    return public_view(row)


async def cmd_redrive(message_id: str, actor_id: str) -> bool:
    from services import outbox

    return bool(await outbox.redrive(message_id, actor_id))


def _print(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list-dead", help="List dead-lettered outbox rows")
    list_p.add_argument("--limit", type=int, default=50)

    show_p = sub.add_parser("show", help="Show one outbox row (safe fields only)")
    show_p.add_argument("id")

    redrive_p = sub.add_parser("redrive", help="Reset a dead-lettered row to pending")
    redrive_p.add_argument("id")
    redrive_p.add_argument("--actor-id", required=True, help="Operator id recorded in audit_logs")
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.command == "list-dead":
        _print(await cmd_list_dead(limit=args.limit))
        return 0
    if args.command == "show":
        row = await cmd_show(args.id)
        if row is None:
            print("not found", file=sys.stderr)
            return 1
        _print(row)
        return 0
    if args.command == "redrive":
        ok = await cmd_redrive(args.id, args.actor_id)
        _print({"ok": ok, "id": args.id})
        return 0 if ok else 2
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
