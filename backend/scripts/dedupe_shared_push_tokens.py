#!/usr/bin/env python3
"""C136 T5: find push tokens held by more than one user and pick one owner.

Background (docs/audit/2026-09-24-c136-destination-mode-and-push-token-ownership.md):
before C136 T4, ``POST /notifications/register-token`` wrote a device token onto
the signing-in user but never removed it from whoever held it before. A phone
that switched accounts stayed reachable under both, so a push meant for one
person could ring another person's phone. T4 stops new crossings; this script
reports (and, only with explicit sign-off, repairs) the ones already on file.

    # 1. Report only. Reads only -- no writes. (default)
    python backend/scripts/dedupe_shared_push_tokens.py

    # 2. Repair. Needs BOTH flags. Requires Pandi + Kiran sign-off first.
    python backend/scripts/dedupe_shared_push_tokens.py --apply --i-have-signoff

Environment -- the same variables the backend itself reads:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

Rule: for every token held by more than one user (across ``push_tokens.user_id``
and ``users.fcm_token`` / ``fcm_token_rider`` / ``fcm_token_driver``), the
owner kept is the user whose ``push_tokens`` row for that token has the most
recent ``updated_at``. Every other user loses the token: their ``push_tokens``
rows with that token are deleted, and any of their three ``users`` columns
equal to it is set NULL. The kept owner is never touched, so a dual-role user
keeps the same token in both ``fcm_token_rider`` and ``fcm_token_driver``.

A token with no ``push_tokens`` row at all (only on ``users`` columns) has no
timestamp to decide by. It is reported as AMBIGUOUS and never changed.

Output: counts and user_ids only. Tokens are never printed or logged -- each
shared token is referred to by a sequential index (``token#1``...).

Rollback: none automatic. A detached user gets their token back the next time
that app registers (every app launch re-registers). Record the printed
user_ids before running with --apply.

DO NOT run against production without Pandi + Kiran sign-off (C136 plan, T5).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("dedupe_shared_push_tokens")

USER_TOKEN_COLUMNS = ("fcm_token", "fcm_token_rider", "fcm_token_driver")
_PAGE_SIZE = 1000


@dataclass
class SharedToken:
    """One token held by more than one user. ``token`` never leaves this process."""

    index: int
    token: str
    owners: list[str]
    keeper: Optional[str]
    # user_id -> push_tokens row ids to delete (non-keepers only)
    push_token_rows_to_delete: dict[str, list[str]] = field(default_factory=dict)
    # user_id -> users columns to null (non-keepers only)
    user_columns_to_clear: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ambiguous(self) -> bool:
        return self.keeper is None


def build_plan(push_rows: Iterable[dict[str, Any]], user_rows: Iterable[dict[str, Any]]) -> list[SharedToken]:
    """Pure: decide, per shared token, which user keeps it. No I/O.

    ``push_rows``: ``push_tokens`` rows with ``id, user_id, token, updated_at``.
    ``user_rows``: ``users`` rows with ``id`` plus the three token columns.
    ISO-8601 ``updated_at`` strings from PostgREST compare correctly as strings
    (same UTC offset format), which is what ``max`` relies on.
    """
    by_token_push: dict[str, list[dict[str, Any]]] = {}
    for row in push_rows:
        tok, uid = row.get("token"), row.get("user_id")
        if tok and uid:
            by_token_push.setdefault(tok, []).append(row)

    by_token_cols: dict[str, dict[str, list[str]]] = {}
    for row in user_rows:
        uid = row.get("id")
        if not uid:
            continue
        for col in USER_TOKEN_COLUMNS:
            tok = row.get(col)
            if tok:
                by_token_cols.setdefault(tok, {}).setdefault(str(uid), []).append(col)

    plan: list[SharedToken] = []
    for tok in sorted(set(by_token_push) | set(by_token_cols)):
        prows = by_token_push.get(tok, [])
        cols = by_token_cols.get(tok, {})
        owners = sorted({str(r["user_id"]) for r in prows} | set(cols))
        if len(owners) < 2:
            continue

        keeper: Optional[str] = None
        if prows:
            newest = max(prows, key=lambda r: (str(r.get("updated_at") or ""), str(r.get("id") or "")))
            keeper = str(newest["user_id"])

        entry = SharedToken(index=len(plan) + 1, token=tok, owners=owners, keeper=keeper)
        if keeper is not None:
            for r in prows:
                uid = str(r["user_id"])
                if uid != keeper and r.get("id"):
                    entry.push_token_rows_to_delete.setdefault(uid, []).append(str(r["id"]))
            for uid, ucols in cols.items():
                if uid != keeper:
                    entry.user_columns_to_clear[uid] = sorted(ucols)
        plan.append(entry)
    return plan


def summarize(plan: list[SharedToken]) -> dict[str, int]:
    return {
        "shared_tokens": len(plan),
        "ambiguous_tokens": sum(1 for p in plan if p.ambiguous),
        "users_to_detach": len({u for p in plan for u in (*p.push_token_rows_to_delete, *p.user_columns_to_clear)}),
        "push_token_rows_to_delete": sum(len(v) for p in plan for v in p.push_token_rows_to_delete.values()),
        "user_columns_to_clear": sum(len(v) for p in plan for v in p.user_columns_to_clear.values()),
    }


async def _page_all(db: Any, table: str, filters: dict, columns: str) -> list[dict]:
    """Read every matching row. Explicit ORDER BY: get_rows has none by default,
    and paging an unordered query can skip or repeat rows."""
    out: list[dict] = []
    offset = 0
    while True:
        page = await db.get_rows(table, filters, order="id", limit=_PAGE_SIZE, offset=offset, columns=columns)
        out.extend(page or [])
        if not page or len(page) < _PAGE_SIZE:
            return out
        offset += _PAGE_SIZE


async def load_rows(db: Any) -> tuple[list[dict], list[dict]]:
    push_rows = await _page_all(db, "push_tokens", {}, "id,user_id,token,updated_at")
    users: dict[str, dict] = {}
    for col in USER_TOKEN_COLUMNS:
        for row in await _page_all(db, "users", {col: {"$notnull": True}}, "id," + ",".join(USER_TOKEN_COLUMNS)):
            users[str(row["id"])] = row
    return push_rows, list(users.values())


async def apply_plan(db: Any, plan: list[SharedToken]) -> dict[str, int]:
    """Write the plan. Each write is filtered on the token value too, so a row that
    changed since the read (re-registered by its rightful device) is left alone."""
    done = {"push_token_rows_deleted": 0, "user_columns_cleared": 0, "failed": 0}
    for p in plan:
        if p.ambiguous:
            continue
        for uid, row_ids in p.push_token_rows_to_delete.items():
            for row_id in row_ids:
                try:
                    await db.delete_many("push_tokens", {"id": row_id, "user_id": uid, "token": p.token})
                    done["push_token_rows_deleted"] += 1
                except Exception:
                    done["failed"] += 1
                    logger.error("token#%d: push_tokens delete failed for user=%s", p.index, uid, exc_info=True)
        for uid, cols in p.user_columns_to_clear.items():
            for col in cols:
                try:
                    await db.update_one("users", {"id": uid, col: p.token}, {col: None})
                    done["user_columns_cleared"] += 1
                except Exception:
                    done["failed"] += 1
                    logger.error("token#%d: users.%s clear failed for user=%s", p.index, col, uid, exc_info=True)
    return done


def _print_report(plan: list[SharedToken], applying: bool) -> None:
    verb = "DETACHING" if applying else "WOULD DETACH"
    for p in plan:
        if p.ambiguous:
            logger.info(
                "token#%d AMBIGUOUS (no push_tokens row to date it) owners=%s -- left unchanged",
                p.index,
                ",".join(p.owners),
            )
            continue
        losers = sorted(set(p.push_token_rows_to_delete) | set(p.user_columns_to_clear))
        logger.info("token#%d keep=%s %s=%s", p.index, p.keeper, verb, ",".join(losers))
        for uid in losers:
            logger.info(
                "  token#%d user=%s push_tokens_rows=%d users_columns=%s",
                p.index,
                uid,
                len(p.push_token_rows_to_delete.get(uid, [])),
                ",".join(p.user_columns_to_clear.get(uid, [])) or "-",
            )


async def _main(apply_changes: bool) -> int:
    try:
        import db_supabase as db
    except ImportError:  # pragma: no cover - CLI convenience
        from backend import db_supabase as db  # type: ignore

    push_rows, user_rows = await load_rows(db)
    plan = build_plan(push_rows, user_rows)
    _print_report(plan, applying=apply_changes)
    logger.info("summary: %s", summarize(plan))

    if not apply_changes:
        logger.info("DRY RUN -- nothing was written. See the module docstring before using --apply.")
        return 0

    result = await apply_plan(db, plan)
    logger.info("applied: %s", result)
    return 1 if result["failed"] else 0


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the repair (default: dry run)")
    ap.add_argument(
        "--i-have-signoff",
        action="store_true",
        help="required with --apply: confirms Pandi + Kiran signed off (C136 T5)",
    )
    args = ap.parse_args(argv)
    if args.apply and not args.i_have_signoff:
        logger.error("--apply needs --i-have-signoff (Pandi + Kiran sign-off, C136 T5). Nothing written.")
        return 2
    return asyncio.run(_main(apply_changes=args.apply))


if __name__ == "__main__":
    sys.exit(main())
