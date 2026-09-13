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

Migration 313 added the columns for the 24 fields below. `test_every_api_field_has_a_column`
originally only cross-checked those 24 against declared migration columns, so any
`SettingsUpdateRequest` field added *after* 313 (`directions_proxy_enabled`,
`driver_turn_by_turn_enabled`) was invisible to the regression loop — a missing-migration bug for
either would have shipped undetected. The check is now generalized to every field on
`SettingsUpdateRequest`, cross-referenced against `_declared_settings_columns()` (every migration's
`ADD COLUMN`) unioned with `_baseline_settings_columns()` (see C110 in ACTION_ITEMS.md).

`_baseline_settings_columns()` covers fields that predate migration tracking in this repo: it
parses `backend/supabase_schema.sql`'s bootstrap `CREATE TABLE settings (...)` block — the "run
this in the Supabase SQL Editor" DDL that is the actual origin of the table's original shape —
rather than asserting a hand-typed list. 7 fields that were neither in that bootstrap file nor in
any migration (`company_app_download_url`, `safety_team_email`, `safety_team_phone`,
`sos_show_share_trip`, `sos_show_report_issue`, `new_ride_requests_enabled`,
`dispute_stripe_evidence_submission_enabled` — two of them kill switches whose own Change Impact
Logs admit they were never exercised against a real Supabase row) got migration 419 instead of a
baseline guess, found by `spinr-test-coverage-reviewer`'s adversarial pass on this fix's first
draft.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.routes.admin.settings import SettingsUpdateRequest

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
_BOOTSTRAP_SCHEMA = Path(__file__).resolve().parents[1] / "supabase_schema.sql"

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
    """Columns declared on `settings` in the bootstrap schema script.

    `backend/supabase_schema.sql` is the "run this in the Supabase SQL Editor"
    bootstrap DDL — the actual origin of the table's original shape, which
    predates this repo's migration tracking (see module docstring). Parsed
    mechanically, the same way `_declared_settings_columns()` parses
    `ADD COLUMN`, rather than hand-typed: a hand-typed list can only ever be
    as good as whoever last reviewed it, which is exactly what put 7 fields
    with zero schema evidence into an earlier draft of this allowlist before
    `spinr-test-coverage-reviewer`'s pass caught it (see C110/C111 in
    ACTION_ITEMS.md) — they got migration 419 instead.
    """
    if not _BOOTSTRAP_SCHEMA.exists():
        return set()
    lines = _BOOTSTRAP_SCHEMA.read_text(encoding="utf-8").splitlines()
    columns: set[str] = set()
    in_settings_table = False
    depth = 0
    for line in lines:
        stripped = line.strip()
        if not in_settings_table:
            if re.match(
                r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?(public\.)?settings\s*\(",
                stripped,
                re.IGNORECASE,
            ):
                in_settings_table = True
                depth = 1
            continue
        # Track paren depth per line (handles a type like NUMERIC(10, 2)) so
        # the closing `);` of the CREATE TABLE, not a column definition, is
        # what ends the scan.
        depth += stripped.count("(") - stripped.count(")")
        if depth <= 0:
            break
        match = re.match(r"([a-z_][a-z0-9_]*)\s+", stripped, re.IGNORECASE)
        if match and match.group(1) not in {"id", "updated_at"}:
            columns.add(match.group(1))
    return columns


def _fields_missing_columns(fields: set[str], declared: set[str], baseline: set[str]) -> list[str]:
    """Pure helper so the regression logic is testable without a real request model."""
    return sorted(fields - declared - baseline - _NOT_PERSISTED)


def _regressed_settings_fields() -> list[str]:
    """The exact field-selection + gap-check `test_every_api_field_has_a_column` asserts on.

    Factored out (rather than inlined in the test) so
    `test_a_field_with_no_column_and_no_baseline_entry_is_caught` below can
    exercise this real code path — including the
    `SettingsUpdateRequest.model_fields` read — instead of only the extracted
    `_fields_missing_columns` set-arithmetic helper. A regression that
    reintroduces a `_EXPECTED_313_COLUMNS`-style filter here would be caught
    by that test; it would not have been if the test only called
    `_fields_missing_columns` directly.
    """
    declared = _declared_settings_columns()
    baseline = _baseline_settings_columns()
    api_fields = set(SettingsUpdateRequest.model_fields.keys())
    return _fields_missing_columns(api_fields, declared, baseline)


def test_every_api_field_has_a_column():
    """A field the API accepts with no column 500s the whole save.

    Covers every field on `SettingsUpdateRequest`, not just the 24 migration
    313 originally fixed — see the module docstring for why the narrower,
    pinned check this replaced missed two later fields.
    """
    regressed = _regressed_settings_fields()

    assert not regressed, (
        f"settings field(s) accepted by the API with no ADD COLUMN in any migration and not in "
        f"_baseline_settings_columns(): {regressed}. PUT /api/admin/settings sends these straight "
        "to Postgres, so the next save that includes one returns PGRST204 -> 500 and loses every "
        "other field in the same request. If this is a genuinely new field, add a migration. If "
        "it predates migration tracking and is confirmed working in production, add it to "
        "_baseline_settings_columns() with a reason."
    )
    assert _declared_settings_columns(), "no settings columns parsed from migrations — the parser is broken"


def test_a_field_with_no_column_and_no_baseline_entry_is_caught(monkeypatch):
    """Guards the generalized check itself against regressing back to pinned-313-only scope.

    Injects a hypothetical field with neither a migration nor a baseline
    entry directly into `SettingsUpdateRequest.model_fields` and asserts
    `_regressed_settings_fields()` — the same function the real test calls —
    flags it. Exercising the real field-selection path (not just
    `_fields_missing_columns` in isolation) means a future regression that
    reintroduces a narrower filter at that call site would fail this test,
    which is exactly the class of gap C110 found: a field added after 313
    was invisible to the old check because it only ever looked at the pinned
    313 set.
    """
    fake_fields = dict(SettingsUpdateRequest.model_fields)
    fake_fields["totally_new_unbacked_flag_for_this_test_only"] = next(iter(fake_fields.values()))
    monkeypatch.setattr(SettingsUpdateRequest, "model_fields", fake_fields)

    assert "totally_new_unbacked_flag_for_this_test_only" in _regressed_settings_fields()


def test_baseline_and_declared_do_not_overlap():
    """Sanity check on the two parsers, not a hand-maintained list anymore.

    A bootstrap-schema column that later also gained a migration's `ADD
    COLUMN IF NOT EXISTS` would be harmless in practice (idempotent), but an
    overlap is still worth surfacing — it usually means a migration re-added
    a column the table already had, which is a sign to double check the
    migration rather than something to silently allow.
    """
    declared = _declared_settings_columns()
    overlap = sorted(_baseline_settings_columns() & declared)
    assert not overlap, (
        f"{overlap} are declared both in supabase_schema.sql's bootstrap CREATE TABLE and in a "
        "migration's ADD COLUMN — harmless, but double check the migration isn't redundant."
    )


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
