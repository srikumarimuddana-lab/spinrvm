"""Guard: every admin-writable `AppSettings` field has a `settings` column.

Why this test exists
--------------------
`sos_paging_webhook_url` / `sos_paging_routing_key` (ACTION_ITEMS.md B15(b))
were added to the `AppSettings` pydantic model *and* to the admin settings
API, but no migration ever added the backing columns. Nothing caught it for
months, because the failure mode is asymmetric:

* **Reads are fine.** ``settings_loader.get_app_settings()`` merges
  ``AppSettings()`` defaults over whatever the row actually contains, so a
  missing column silently resolves to its default — forever, with no error.
* **Writes are not.** ``admin_update_settings``
  (``routes/admin/settings.py``) builds ``model_dump(exclude_none=True)`` and
  hands the dict straight to PostgREST. An unknown column returns PGRST204,
  which fails the **entire** settings save — not just that one field. So each
  orphaned writable field is a latent 500 on the settings page, armed and
  waiting for the first operator who touches that control.

Running this test against `main` at the time it was written found five more
of the same defect beyond the SOS pair: ``ai_disabled_mode`` (the AI kill
switch's presentation half) and the four ``apns_*`` push-credential fields.
Migration 278 landed those.

Why "admin-writable" and not "every model field"
------------------------------------------------
Thirteen `AppSettings` fields legitimately have no column: the ``driver_map_*``
tuning knobs, the ``corporate_*`` cascade toggles, and the
``scheduled_ride_*`` flags. None of them appear in ``SettingsUpdateRequest``,
so nothing can write them and nothing can trigger the PGRST204 path — they are
read-only, default-backed constants that happen to live on the settings model.
Adding columns for them would imply a write path that does not exist.

Writability is therefore the precise invariant, and scoping the test to it
means no arbitrary allowlist to maintain: a field becomes covered the moment
someone makes it settable, which is exactly the moment it starts to matter.

This is a static text scan over the migration files. It needs no database, so
it runs in the unit tier and cannot be skipped by a missing Supabase.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set

import pytest

pytestmark = pytest.mark.unit

_BACKEND = Path(__file__).resolve().parent.parent
_MIGRATIONS = _BACKEND / "migrations"
_SCHEMAS_PY = _BACKEND / "schemas.py"
_ADMIN_SETTINGS_PY = _BACKEND / "routes" / "admin" / "settings.py"
# The pre-migration-tracking base schema. Columns on the original CREATE TABLE
# live here rather than in any NN_*.sql file.
_BASE_SCHEMA = _BACKEND / "supabase_schema.sql"

# Not a real settings column — `id` is the row key ('app_settings') and
# `updated_at` is stamped by the update path itself. Both exist on the table;
# neither is declared the way the scan below detects, and neither is
# meaningfully "drift".
_NOT_DRIFT = frozenset({"id", "updated_at"})


def _model_fields(source: str, class_name: str) -> list[str]:
    """Field names declared directly on a pydantic model class body."""
    match = re.search(
        rf"^class {class_name}\(BaseModel\):(.*?)(?=^(?:class |@|def ))",
        source,
        re.S | re.M,
    )
    assert match, f"could not locate class {class_name} — did it get renamed?"
    return re.findall(r"^\s{4}([a-z_][a-z0-9_]*)\s*:", match.group(1), re.M)


def _declared_settings_columns() -> Set[str]:
    """Every column ever declared on `public.settings`, across all SQL."""
    columns: Set[str] = set()
    sql_files = sorted(_MIGRATIONS.glob("*.sql"))
    if _BASE_SCHEMA.exists():
        sql_files.append(_BASE_SCHEMA)

    for path in sql_files:
        text = path.read_text(encoding="utf-8")

        # ALTER TABLE [public.]settings ... ADD COLUMN [IF NOT EXISTS] name
        for stmt in re.findall(r"ALTER TABLE\s+(?:public\.)?settings\b(.*?);", text, re.S | re.I):
            columns.update(
                name.lower()
                for name in re.findall(
                    r"ADD COLUMN(?:\s+IF NOT EXISTS)?\s+([a-z_][a-z0-9_]*)",
                    stmt,
                    re.I,
                )
            )

        # CREATE TABLE [IF NOT EXISTS] [public.]settings ( name TYPE, ... )
        for body in re.findall(
            r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(?:public\.)?settings\s*\((.*?)\n\s*\);",
            text,
            re.S | re.I,
        ):
            for line in body.splitlines():
                decl = re.match(r"\s*([a-z_][a-z0-9_]*)\s+[A-Za-z]", line)
                if not decl:
                    continue
                name = decl.group(1).lower()
                # Table-level constraint clauses, not columns.
                if name in {"primary", "unique", "constraint", "foreign", "check"}:
                    continue
                columns.add(name)

    return columns


def test_scan_finds_the_settings_table_at_all():
    """Self-check: if the scan silently matched nothing, every assertion below
    would pass vacuously and this guard would be worthless. Pin a floor."""
    columns = _declared_settings_columns()
    assert len(columns) > 50, (
        f"only found {len(columns)} settings columns — the SQL scan is probably "
        "broken (renamed table? changed migration formatting?), not the schema"
    )
    # A column from the base schema and one from a recent migration, so the
    # test fails loudly if either half of the scan regresses.
    assert "stripe_secret_key" in columns
    assert "sos_paging_webhook_url" in columns


def test_every_admin_writable_setting_has_a_column():
    """The invariant: if an admin can PUT it, PostgREST must be able to store it.

    A failure here means the named field would return PGRST204 and blow up the
    whole settings save the first time anyone sets it. Fix it by adding the
    column in a new migration — not by removing the field from the API.
    """
    writable = set(_model_fields(_ADMIN_SETTINGS_PY.read_text(encoding="utf-8"), "SettingsUpdateRequest"))
    columns = _declared_settings_columns()

    missing = sorted(f for f in writable if f not in columns and f not in _NOT_DRIFT)

    assert not missing, (
        "admin-writable settings fields with no backing column on public.settings: "
        f"{missing}. Each one is a latent 500 — admin_update_settings passes "
        "model_dump(exclude_none=True) straight to PostgREST, and an unknown "
        "column (PGRST204) fails the entire settings save, not just that field. "
        "Add the column in a new migration (see 277/278 for the template)."
    )


def test_app_settings_read_only_fields_are_not_writable():
    """The complement of the rule above, so the scoping stays honest.

    `AppSettings` fields with no column are only safe while nothing can write
    them. If someone adds one to SettingsUpdateRequest without a migration,
    the test above catches it — this one documents *which* fields are relying
    on that protection, so the list can't quietly grow unnoticed.
    """
    model_fields = set(_model_fields(_SCHEMAS_PY.read_text(encoding="utf-8"), "AppSettings"))
    writable = set(_model_fields(_ADMIN_SETTINGS_PY.read_text(encoding="utf-8"), "SettingsUpdateRequest"))
    columns = _declared_settings_columns()

    column_less = {f for f in model_fields if f not in columns and f not in _NOT_DRIFT}

    # Every column-less field must be read-only. This is the same assertion as
    # the test above viewed from the other side; it fails with a message aimed
    # at whoever just made one of these settable.
    settable_and_column_less = sorted(column_less & writable)
    assert not settable_and_column_less, (
        f"{settable_and_column_less} became admin-writable without a migration "
        "adding the column(s). Land the migration in the same change."
    )
