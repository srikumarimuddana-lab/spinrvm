"""admin_staff column additions must reload the PostgREST schema cache.

Regression (incident): POST /api/admin/auth/mfa/enroll failed in production
with PGRST204 "Could not find the 'mfa_secret_pending' column of 'admin_staff'
in the schema cache". The columns had been added by 52_admin_staff_mfa.sql /
56_admin_staff_security_columns.sql, but neither migration issued
`NOTIFY pgrst, 'reload schema';`, so PostgREST (the REST layer supabase-py
talks to) kept serving a stale schema and rejected every write to the new
columns — locking staff out of the forced-MFA login flow.

Convention (see 29/47/48/49/82/…): a migration that adds a column ends with
`NOTIFY pgrst, 'reload schema';` in the SAME file. Co-locating the reload is
what makes the new column reliably writable — relying on some later, unrelated
migration to happen to reload the cache is exactly the deployment-order fragility
that caused this incident.

This guards that every migration adding admin_staff columns co-locates a schema
reload. The two historical offenders (52, 56) are immutable append-only files
remediated by 232_admin_staff_schema_cache_reload.sql; they are grandfathered
here so the test fails loudly on any *new* offender.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_migrations import migration_sort_key  # noqa: E402

pytestmark = pytest.mark.unit

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# `ALTER TABLE admin_staff ... ADD COLUMN` (whitespace/newlines between clauses).
_ADDS_ADMIN_STAFF_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+admin_staff\b[\s\S]*?ADD\s+COLUMN",
    re.IGNORECASE,
)
_RELOADS_PGRST = re.compile(r"NOTIFY\s+pgrst\s*,\s*'reload schema'", re.IGNORECASE)

# Immutable, already-merged migrations that added admin_staff columns without a
# co-located NOTIFY. All are remediated by 232's global schema reload. Do NOT
# add to this set — new column-adds must reload the cache in their own file.
_GRANDFATHERED = {
    "25_refresh_tokens_and_token_version.sql",  # token_version
    "52_admin_staff_mfa.sql",                   # mfa_* (the incident)
    "56_admin_staff_security_columns.sql",      # locked_until, allowed_ips
}


def _ordered_migrations() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: migration_sort_key(p.name))


def test_admin_staff_column_adds_colocate_a_schema_cache_reload():
    offenders = []
    for path in _ordered_migrations():
        if path.name in _GRANDFATHERED:
            continue
        sql = path.read_text(encoding="utf-8")
        if _ADDS_ADMIN_STAFF_COLUMN.search(sql) and not _RELOADS_PGRST.search(sql):
            offenders.append(path.name)

    assert not offenders, (
        "These migration(s) add admin_staff columns without issuing "
        "`NOTIFY pgrst, 'reload schema';` in the same file. PostgREST will serve "
        "a stale schema cache and reject writes to the new column (PGRST204, the "
        f"MFA-enroll lockout incident). Add the NOTIFY: {offenders}"
    )


def test_incident_reload_migration_reloads_and_covers_mfa_secret_pending():
    fix = MIGRATIONS_DIR / "232_admin_staff_schema_cache_reload.sql"
    assert fix.exists(), "incident-fix migration 232 is missing"
    sql = fix.read_text(encoding="utf-8")
    assert _RELOADS_PGRST.search(sql), "232 must issue NOTIFY pgrst, 'reload schema'"
    assert "mfa_secret_pending" in sql, "232 must re-assert the mfa_secret_pending column"
