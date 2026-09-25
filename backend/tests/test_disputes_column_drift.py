"""Every key routes/disputes.py writes to `disputes` must be a real column.

The `disputes` row is written with a plain dict and no column allowlist, so a
key with no column is rejected by PostgREST with PGRST204. That 500s the whole
create or resolve. Until migration 477 this was live: production had none of
requested_amount, original_fare, resolution or refund_result, so
create_dispute and admin_resolve_dispute would both have 500'd (0 disputes in
production as of 2026-09-25). On the refund path, the Stripe refund is issued
BEFORE the update, so the 500 would come after money moved.

Allowed columns = the production snapshot (read-only information_schema check,
2026-09-25) unioned with every `ADD COLUMN` on `disputes` declared in a
migration. The keys are captured at runtime from the handlers' actual
insert_one/update_one payloads on every write path, not parsed from source.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

# information_schema.columns for public.disputes in production, 2026-09-25.
_PROD_SNAPSHOT_2026_09_25 = frozenset(
    {
        "id",
        "ride_id",
        "user_id",
        "user_name",
        "user_type",
        "reason",
        "description",
        "status",
        "refund_amount",
        "resolution_status",
        "resolution_notes",
        "resolved_at",
        "resolved_by",
        "admin_note",
        "created_at",
        "updated_at",
        "zoho_ticket_id",
    }
)

MOD = "backend.routes.disputes"


def _migration_declared_columns() -> set[str]:
    cols: set[str] = set()
    for path in _MIGRATIONS.glob("*.sql"):
        code = re.sub(r"--[^\n]*", "", path.read_text())
        for stmt in code.split(";"):
            if not re.search(r"ALTER\s+TABLE\s+(IF\s+EXISTS\s+)?(public\.)?disputes\b", stmt, re.IGNORECASE):
                continue
            cols.update(
                m.group(1).lower()
                for m in re.finditer(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)", stmt, re.I)
            )
    return cols


_ALLOWED = _PROD_SNAPSHOT_2026_09_25 | _migration_declared_columns()


def test_migration_477_declares_the_four_missing_columns():
    assert {"requested_amount", "original_fare", "resolution", "refund_result"} <= _migration_declared_columns()


async def test_create_dispute_writes_only_known_columns():
    from backend.routes.disputes import CreateDisputeRequest, create_dispute

    ride = {"id": "ride_1", "rider_id": "rider_1", "status": "completed", "total_fare": 20.0}
    insert = AsyncMock()

    async def get_rows(table, *args, **kwargs):
        return []

    with (
        patch(f"{MOD}.db_supabase.get_ride", AsyncMock(return_value=ride)),
        patch(f"{MOD}.db_supabase.get_rows", get_rows),
        patch(f"{MOD}.db_supabase.insert_one", insert),
        patch(f"{MOD}.send_push_notification", AsyncMock()),
        patch(f"{MOD}._spawn", MagicMock()),
        patch(f"{MOD}.create_ticket_for_dispute", MagicMock()),
    ):
        await create_dispute(
            CreateDisputeRequest(ride_id="ride_1", reason="overcharged", description="x"),
            current_user={"id": "rider_1"},
        )
    table, row = insert.await_args.args[:2]
    assert table == "disputes"
    assert set(row) - _ALLOWED == set(), f"disputes has no column for {sorted(set(row) - _ALLOWED)}"


@pytest.mark.parametrize(
    ("resolution", "flag", "ride_extra"),
    [
        ("approved", True, {"stripe_charge_id": "pi_1"}),  # Stripe refund
        ("partial_refund", True, {}),  # manual_required
        ("approved", False, {"stripe_charge_id": "pi_1"}),  # flag off, not_issued
        ("rejected", False, {}),
    ],
)
async def test_resolve_dispute_writes_only_known_columns(resolution, flag, ride_extra):
    from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

    dispute = {"id": "d1", "ride_id": "ride_1", "user_id": "rider_1", "status": "open", "original_fare": 20.0}
    update = AsyncMock()
    with (
        patch(f"{MOD}.db_supabase.get_rows", AsyncMock(return_value=[dispute])),
        patch(
            f"{MOD}.db_supabase.get_ride", AsyncMock(return_value={"id": "ride_1", "rider_id": "rider_1", **ride_extra})
        ),
        patch(f"{MOD}.db_supabase.update_one", update),
        patch(
            f"{MOD}.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test", "admin_dispute_refunds_enabled": flag}),
        ),
        patch(f"{MOD}.log_admin_action", AsyncMock()),
        patch(f"{MOD}.send_push_notification", AsyncMock()),
        patch("backend.services.admin_money_caps.get_app_settings", AsyncMock(return_value={})),
        patch("stripe.Refund.create", MagicMock(return_value=MagicMock(status="succeeded", id="re_1"))),
    ):
        await admin_resolve_dispute(
            dispute_id="d1",
            req=ResolveDisputeRequest(resolution=resolution, refund_amount=Decimal("5.00")),
            current_admin={"id": "admin_1", "role": "admin"},
        )
    table, _filters, row = update.await_args.args[:3]
    assert table == "disputes"
    assert set(row) - _ALLOWED == set(), f"disputes has no column for {sorted(set(row) - _ALLOWED)}"
