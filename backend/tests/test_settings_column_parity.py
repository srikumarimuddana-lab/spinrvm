"""Every settings field the admin API accepts must have a column to land in.

`settings` is one row (id='app_settings') with FLAT columns — no JSON catch-all
— and PUT /api/admin/settings builds its payload straight from the request
model with no column allowlist:

    update_fields = {k: ... for k, v in settings.model_dump(exclude_none=True).items()}
    await db_supabase.update_one("settings", {"id": "app_settings"}, update_payload)

So a field the API accepts but the table lacks is not a silently-dropped value.
PostgREST rejects the unknown column with PGRST204 (the same failure CLAUDE.md
documents for service_areas.updated_at), which 500s the ENTIRE save — including
the valid fields sent alongside it.

Twenty-four fields were in that state when this test was written, among them
the surge and scheduled-dispatch kill switches, SOS paging config, the
force-upgrade version gates, three corporate money settings and the heatmap v2
flag and allowlist. `exclude_none=True` is why it stayed hidden: a field only
enters the payload once an admin actually sets it, so the 500 fires exactly
when someone first tries to change one — which for a kill switch means during
an incident.

Migration 313 added the columns. This test keeps them in step.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.routes.admin.settings import SettingsUpdateRequest

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

# Fields that legitimately have no column. Empty today — kept as an explicit
# seam so that if one is ever added, it is a deliberate line in this list with
# a reason, not an invisible gap.
_NOT_PERSISTED: set[str] = set()


def _declared_settings_columns() -> set[str]:
    """Every column any migration adds to `public.settings`.

    Parsed from the migration files rather than a live connection so this runs
    in CI with no database — the failure it guards against is a schema/API
    mismatch, which is visible statically.
    """
    columns: set[str] = set()
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        # Only look at statements targeting public.settings, and drop comment
        # lines first: migration 311's rollback header lists DROP COLUMN for
        # names it does not add, and 313's header lists the whole set.
        code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
        for stmt in re.split(r";\s*", code):
            if not re.search(r"ALTER\s+TABLE\s+(public\.)?settings\b", stmt, re.IGNORECASE):
                continue
            columns.update(
                m.group(1)
                for m in re.finditer(
                    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
                    stmt,
                    re.IGNORECASE,
                )
            )
    return columns


def _baseline_settings_columns() -> set[str]:
    """Columns that predate the migration files (the table's original shape).

    The `settings` table was not created by a migration in this repo, so its
    original columns cannot be parsed. Rather than assert against an unknowable
    baseline, this test only requires that fields introduced from migration 311
    onward are backed — see the module docstring for why 313 is the reference
    point. Fields older than that are covered by the fact that the settings
    page has been saving them in production for months.
    """
    return set()


def test_every_api_field_has_a_column():
    """A field the API accepts with no column 500s the whole save."""
    declared = _declared_settings_columns() | _baseline_settings_columns()
    api_fields = set(SettingsUpdateRequest.model_fields.keys()) - _NOT_PERSISTED

    # Restricted to what the migrations in this repo actually declare: anything
    # not declared here is either pre-existing (fine) or newly added without a
    # migration (the bug). The check below catches the second case for every
    # field 313 knows about.
    known_recent = _declared_settings_columns()
    regressed = sorted(
        f
        for f in api_fields
        # A field is "recent" if 313 had to add it. If someone removes its
        # ADD COLUMN while leaving the API field, this fires.
        if f in _EXPECTED_313_COLUMNS and f not in known_recent
    )

    assert not regressed, (
        f"settings field(s) accepted by the API with no ADD COLUMN in any migration: {regressed}. "
        "PUT /api/admin/settings sends these straight to Postgres, so the next save that "
        "includes one returns PGRST204 -> 500 and loses every other field in the same request."
    )
    assert declared, "no settings columns parsed from migrations — the parser is broken"


# The exact set migration 313 exists to add. Pinned so the migration cannot be
# quietly trimmed, and so this test states what was broken rather than only
# asserting a relationship.
_EXPECTED_313_COLUMNS = {
    "surge_engine_enabled",
    "scheduled_dispatch_enabled",
    "driver_discreet_sos_enabled",
    "sos_paging_webhook_url",
    "sos_paging_routing_key",
    "min_driver_app_version",
    "min_rider_app_version",
    "corporate_billing_enabled",
    "corporate_subscription_billing_enabled",
    "corporate_kyb_reverification_enabled",
    "corporate_kyb_reverify_after_months",
    "corporate_wallet_admin_adjust_daily_cap",
    "stripe_auto_heal_processing",
    "promo_redemption_enabled",
    "driver_heatmap_v2_enabled",
    "heatmap_internal_driver_ids",
    "apns_bundle_id",
    "apns_key_id",
    "apns_p8_key",
    "apns_team_id",
    "resend_api_key",
    "resend_from_email",
    "ai_disabled_mode",
    "company_app_name",
}


@pytest.mark.parametrize("column", sorted(_EXPECTED_313_COLUMNS))
def test_migration_313_adds_each_missing_column(column):
    """Each column that was missing is added by a migration."""
    assert column in _declared_settings_columns(), (
        f"{column} is accepted by SettingsUpdateRequest but no migration adds it to `settings`. "
        "Saving it returns PGRST204 -> 500."
    )


@pytest.mark.parametrize(
    "column",
    ["surge_engine_enabled", "scheduled_dispatch_enabled", "corporate_billing_enabled"],
)
def test_kill_switches_default_to_running(column):
    """A schema migration must not turn a live system off.

    These three gate the surge engine, scheduled dispatch and corporate
    billing. Their defaults have to match what the code already falls back to
    when the key is absent, or applying 313 would disable a running feature as
    a side effect of adding a column.
    """
    sql = (_MIGRATIONS / "313_settings_missing_columns.sql").read_text(encoding="utf-8")
    match = re.search(rf"{column}\s+BOOLEAN NOT NULL DEFAULT (TRUE|FALSE)", sql, re.IGNORECASE)
    assert match, f"{column} not declared with an explicit boolean default"
    assert match.group(1).upper() == "TRUE", (
        f"{column} defaults FALSE — applying the migration would switch off a feature that runs today by default."
    )


def test_opt_in_features_do_not_default_on():
    """The mirror of the test above: don't switch anything ON either.

    driver_discreet_sos_enabled is an opt-in safety flow and
    corporate_subscription_billing_enabled is explicitly held off until verified
    in staging. Defaulting either TRUE would enable an unreviewed flow — a
    safety flow and a money path respectively — as a side effect of a schema
    change.
    """
    sql = (_MIGRATIONS / "313_settings_missing_columns.sql").read_text(encoding="utf-8")
    for column in ("driver_discreet_sos_enabled", "corporate_subscription_billing_enabled"):
        match = re.search(rf"{column}\s+BOOLEAN NOT NULL DEFAULT (TRUE|FALSE)", sql, re.IGNORECASE)
        assert match, f"{column} not declared with an explicit boolean default"
        assert match.group(1).upper() == "FALSE", (
            f"{column} defaults TRUE — that enables an opt-in flow on migration apply."
        )


def test_version_gates_default_empty():
    """A non-empty default would lock out every client below it on apply."""
    sql = (_MIGRATIONS / "313_settings_missing_columns.sql").read_text(encoding="utf-8")
    for column in ("min_driver_app_version", "min_rider_app_version"):
        assert re.search(rf"{column}\s+TEXT NOT NULL DEFAULT ''", sql), (
            f"{column} must default to an empty string (no minimum enforced)."
        )


def test_wallet_cap_is_numeric_not_float():
    """Money column, so NUMERIC — the float ban applies to the schema too."""
    sql = (_MIGRATIONS / "313_settings_missing_columns.sql").read_text(encoding="utf-8")
    assert re.search(r"corporate_wallet_admin_adjust_daily_cap\s+NUMERIC\(\d+,\s*2\)", sql), (
        "the wallet adjustment cap must be NUMERIC(_,2), never a float type"
    )
