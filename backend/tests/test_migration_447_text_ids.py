"""447 fix-forward: rides.id / drivers.id are TEXT, never uuid.

444 and 445 typed these ids as uuid (444 cannot be created; 445 fails on every
call). They are skip-listed and 447 carries the corrected definitions. The
last test guards every future migration against the same mistake.
"""

import re
from pathlib import Path

try:
    from backend.scripts.run_migrations import NEVER_APPLY
except ImportError:
    from scripts.run_migrations import NEVER_APPLY

MIGRATIONS = Path(__file__).parents[1] / "migrations"
FIX = MIGRATIONS / "447_fix_payment_ops_and_live_marker_text_ids.sql"

# A ride/driver id column or function parameter declared as uuid.
_UUID_ID = re.compile(r"\b(p_)?(ride|driver)_id\s+uuid\b", re.IGNORECASE)
_LINE_COMMENT = re.compile(r"--[^\n]*")


def _code(path: Path) -> str:
    return _LINE_COMMENT.sub("", path.read_text())


def test_broken_444_and_445_are_skip_listed():
    assert "444_ride_payment_operations.sql" in NEVER_APPLY
    assert "445_monotonic_live_driver_marker.sql" in NEVER_APPLY


def test_447_uses_text_ids_and_drops_uuid_overload():
    sql = FIX.read_text()
    assert "ride_id text NOT NULL REFERENCES public.rides(id)" in sql
    assert "p_driver_id text, p_captured_at timestamptz, p_values jsonb" in sql
    assert "DROP FUNCTION IF EXISTS public.update_live_driver_marker(uuid, timestamptz, jsonb)" in sql
    assert "GRANT EXECUTE ON FUNCTION public.update_live_driver_marker(text, timestamptz, jsonb) TO service_role" in sql
    assert "CREATE TABLE IF NOT EXISTS public.ride_payment_operations" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY" not in sql
    assert not _UUID_ID.search(_code(FIX))


def test_new_migrations_never_type_ride_or_driver_ids_as_uuid():
    offenders = []
    for path in MIGRATIONS.glob("*.sql"):
        match = re.match(r"(\d+)", path.name)
        if not match or int(match.group(1)) < 447:
            continue
        if _UUID_ID.search(_code(path)):
            offenders.append(path.name)
    assert offenders == [], f"rides.id/drivers.id are TEXT; uuid-typed ids in {offenders}"
