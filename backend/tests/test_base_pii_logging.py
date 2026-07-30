"""PIPEDA regression tests for the shared DB layer's log emission.

`backend/repositories/_base.py` is the funnel every table's reads and writes
pass through, so a value-emitting log line there leaks across the whole product.
Two such leaks existed and these tests pin them shut:

1. A pair of ``[GO-ONLINE]`` debug lines in ``update_one`` logged the write
   payload (raw lat/lng on the driver location hot path) and ``res.data`` — the
   full PostgREST row, which for ``drivers`` carries plaintext ``name``,
   ``phone``, and ``vehicle_vin``.
2. The catch-all error line logged the raw Postgres error string, which embeds
   ``Key (phone)=(…)`` on a unique violation and ``Failing row contains (…)`` —
   the entire row — on a CHECK or NOT NULL violation, for *every* table.

Testing notes (both are traps that produce vacuously-passing tests):

* ``_base`` logs via **loguru**, which does not propagate to pytest's
  ``caplog``. A ``caplog``-based negative assertion here captures nothing and
  therefore always passes. We patch the module-level ``logger`` name instead and
  read ``call_args_list`` — the pattern established by ``test_p2_sos.py``.
* Every negative assertion is paired with a positive anchor asserting we
  captured something. Without it, a refactor that stops logging entirely would
  leave these tests green while proving nothing.
* The DB binding that governs ``update_one`` is
  ``backend.repositories._base.supabase``, **not** ``backend.db_supabase.supabase``
  — the latter is a separate name binding created by a re-export and patching it
  has no effect (see ``conftest.py``, which patches both for this reason).
"""

from unittest.mock import MagicMock, patch

import pytest

import backend.repositories._base as base

# Values planted in the payload / returned row. Each is on CLAUDE.md's
# never-log list and must not appear in any emitted line.
DRIVER_LAT = 52.133200
DRIVER_LNG = -106.670000
DRIVER_NAME = "Jane Testcase"
DRIVER_PHONE = "+13065551234"
DRIVER_VIN = "1HGBH41JXMN109186"


def _mock_table_returning(row: dict) -> MagicMock:
    """A Supabase table mock whose update/upsert chain is synchronous.

    The shared ``mock_supabase_client`` fixture does not make ``update``
    chainable and its ``execute`` is async, while ``run_sync`` calls ``execute``
    synchronously — so a bare fixture yields a MagicMock ``res.data`` rather
    than rows. This mirrors the override shape used in ``test_drivers.py``.
    """
    response = MagicMock()
    response.data = [row]

    query = MagicMock()
    query.update.return_value = query
    query.upsert.return_value = query
    query.eq.return_value = query
    query.execute = MagicMock(return_value=response)
    return query


def _captured(mock_logger) -> list[str]:
    """Every message string the patched logger was asked to emit."""
    messages: list[str] = []
    for level in ("info", "warning", "error", "debug", "critical"):
        for call in getattr(mock_logger, level).call_args_list:
            if call.args:
                messages.append(str(call.args[0]))
    return messages


@pytest.mark.anyio
async def test_update_one_drivers_logs_no_payload_or_row_values(mock_supabase_client):
    """A drivers write must log its shape, never its values or the returned row."""
    full_row = {
        "id": "drv_1",
        "name": DRIVER_NAME,
        "phone": DRIVER_PHONE,
        "vehicle_vin": DRIVER_VIN,
        "lat": DRIVER_LAT,
        "lng": DRIVER_LNG,
        "license_number": "vault-uuid-0000",
    }
    mock_supabase_client.table.return_value = _mock_table_returning(full_row)

    with (
        patch.object(base, "logger") as mock_logger,
        patch.object(base, "_pre_invalidate_for_table", new=_noop_async()),
    ):
        await base.update_one(
            "drivers",
            {"user_id": "usr_1"},
            {"lat": DRIVER_LAT, "lng": DRIVER_LNG, "heading": 91.0},
        )

    messages = _captured(mock_logger)

    # Positive anchor: without this, capturing nothing would pass every
    # assertion below and the test would prove nothing.
    assert messages, "expected update_one to emit at least one log line"
    joined = "\n".join(messages)

    # The payload values (raw GPS) must not appear, in any float spelling.
    assert "52.1332" not in joined, f"raw latitude leaked: {joined!r}"
    assert "106.67" not in joined, f"raw longitude leaked: {joined!r}"
    # The returned row's plaintext PII must not appear.
    assert DRIVER_NAME not in joined, f"driver name leaked: {joined!r}"
    assert DRIVER_PHONE not in joined, f"driver phone leaked: {joined!r}"
    assert DRIVER_VIN not in joined, f"vehicle VIN leaked: {joined!r}"

    # And the line must still be useful: table, key names, and outcome.
    assert "table=drivers" in joined
    assert "payload_keys=" in joined
    assert "rows_updated=1" in joined


@pytest.mark.anyio
async def test_update_one_logs_coarse_geohash_not_coordinates(mock_supabase_client):
    """Coordinates are summarised to a geohash cell, which is the allowed form."""
    mock_supabase_client.table.return_value = _mock_table_returning({"id": "drv_1"})

    with (
        patch.object(base, "logger") as mock_logger,
        patch.object(base, "_pre_invalidate_for_table", new=_noop_async()),
    ):
        await base.update_one("drivers", {"id": "drv_1"}, {"lat": DRIVER_LAT, "lng": DRIVER_LNG})

    joined = "\n".join(_captured(mock_logger))
    assert joined, "expected a log line"
    assert "geohash=" in joined, f"expected a geohash summary, got {joined!r}"
    # A precision-5 geohash is ~5km per cell — coarse enough to be an area, not
    # a position. Guard against someone raising precision to a locating value.
    geo = joined.split("geohash=")[1].split()[0]
    assert len(geo) <= 6, f"geohash precision too fine for logs: {geo!r}"


@pytest.mark.anyio
async def test_update_one_non_dict_payload_error_omits_the_payload(mock_supabase_client):
    """The bad-payload TypeError names the type, not the value.

    This message is both logged and carried in ``DatabaseError.details["original"]``,
    which CLAUDE.md instructs callers to log — so a payload repr here would leak
    through two sinks at once.
    """
    from backend.utils.error_handling import DatabaseError

    secret = f"{DRIVER_NAME} {DRIVER_PHONE}"
    with (
        patch.object(base, "supabase", MagicMock()),
        patch.object(base, "_pre_invalidate_for_table", new=_noop_async()),
        pytest.raises(DatabaseError) as exc_info,
    ):
        await base.update_one("drivers", {"id": "drv_1"}, {"$set": secret})

    original = str(exc_info.value.details.get("original", ""))
    # Still actionable — the caller can find the offending call site.
    assert "must be a dict" in original
    assert "drivers" in original
    assert "str" in original
    # But carries none of the payload.
    assert DRIVER_NAME not in original, f"payload leaked into exception details: {original!r}"
    assert DRIVER_PHONE not in original, f"payload leaked into exception details: {original!r}"


@pytest.mark.parametrize(
    "raw,must_not_contain",
    [
        (
            'duplicate key value violates unique constraint "drivers_phone_key"\n'
            f"DETAIL:  Key (phone)=({DRIVER_PHONE}) already exists.",
            DRIVER_PHONE,
        ),
        (
            'new row for relation "drivers" violates check constraint "drivers_lat_check"\n'
            f"DETAIL:  Failing row contains (drv_1, {DRIVER_NAME}, {DRIVER_PHONE}, {DRIVER_LAT}, {DRIVER_LNG}).",
            DRIVER_NAME,
        ),
        (
            f'insert or update on table "rides" violates foreign key constraint "rides_driver_id_fkey"\n'
            f'DETAIL:  Key (driver_id)=({DRIVER_VIN}) is not present in table "drivers".',
            DRIVER_VIN,
        ),
    ],
    ids=["unique_violation", "check_violation_full_row", "fk_violation"],
)
def test_redact_pg_error_strips_values_keeps_schema(raw, must_not_contain):
    """Postgres error text carries real column data; keep only the schema parts."""
    redacted = base._redact_pg_error(raw)

    assert must_not_contain not in redacted, f"value survived redaction: {redacted!r}"
    # The constraint name is schema, not data, and is what makes this actionable.
    assert "constraint" in redacted
    assert redacted, "redaction must not empty the message"


@pytest.mark.anyio
async def test_db_error_path_redacts_before_log_and_before_details():
    """The redaction must be wired into run_sync's error tail, not just available.

    Covers the call site rather than the helper: a regression that stops calling
    ``_redact_pg_error`` would leave every helper-level test above green, so this
    drives a real Postgres-shaped failure through ``run_sync`` and inspects both
    sinks — the log line and ``DatabaseError.details["original"]``, which
    CLAUDE.md instructs callers to log when handling a DatabaseError.
    """
    from backend.utils.error_handling import DatabaseError

    pg_error = (
        'new row for relation "drivers" violates check constraint "drivers_lat_check"\n'
        f"DETAIL:  Failing row contains (drv_1, {DRIVER_NAME}, {DRIVER_PHONE}, {DRIVER_LAT}, {DRIVER_LNG})."
    )

    def _boom():
        raise RuntimeError(pg_error)

    with patch.object(base, "logger") as mock_logger:
        with pytest.raises(DatabaseError) as exc_info:
            await base.run_sync(_boom)

    # Sink 1: the log line.
    messages = _captured(mock_logger)
    assert messages, "expected run_sync to log the DB failure"
    joined = "\n".join(messages)
    assert DRIVER_NAME not in joined, f"row values leaked into the log: {joined!r}"
    assert DRIVER_PHONE not in joined, f"row values leaked into the log: {joined!r}"
    assert "52.1332" not in joined, f"row values leaked into the log: {joined!r}"
    # Still actionable: the constraint name survives.
    assert "drivers_lat_check" in joined

    # Sink 2: the exception details callers are told to log.
    original = str(exc_info.value.details.get("original", ""))
    assert DRIVER_NAME not in original, f"row values leaked into details: {original!r}"
    assert DRIVER_PHONE not in original, f"row values leaked into details: {original!r}"
    assert "drivers_lat_check" in original


@pytest.mark.anyio
async def test_duplicate_key_path_redacts_the_conflicting_value():
    """A unique violation must not carry the conflicting value into its details."""
    from backend.utils.error_handling import DuplicateRecordError

    def _boom():
        raise RuntimeError(
            'duplicate key value violates unique constraint "drivers_phone_key"\n'
            f"DETAIL:  Key (phone)=({DRIVER_PHONE}) already exists."
        )

    with patch.object(base, "logger"):
        with pytest.raises(DuplicateRecordError) as exc_info:
            await base.run_sync(_boom)

    original = str(exc_info.value.details.get("original", ""))
    assert DRIVER_PHONE not in original, f"conflicting value leaked: {original!r}"
    # The column name and constraint are what make this actionable — keep them.
    assert "phone" in original
    assert "drivers_phone_key" in original


def test_redact_pg_error_preserves_a_clean_message():
    """A message with no embedded values passes through unchanged."""
    clean = 'permission denied for table "drivers"'
    assert base._redact_pg_error(clean) == clean


def test_redact_pg_error_handles_empty():
    assert base._redact_pg_error("") == ""


def test_log_safe_write_emits_only_key_names():
    """The write summary is an allowlist: key names in, values never."""
    summary = base._log_safe_write(
        "drivers",
        {"user_id": "usr_1"},
        {"name": DRIVER_NAME, "phone": DRIVER_PHONE, "lat": DRIVER_LAT, "lng": DRIVER_LNG},
    )

    assert "table=drivers" in summary
    assert "name" in summary and "phone" in summary  # key names are fine
    assert DRIVER_NAME not in summary
    assert DRIVER_PHONE not in summary
    assert "52.1332" not in summary
    assert "usr_1" not in summary, "filter values must not be emitted either"


def test_log_safe_write_omits_geohash_when_no_coordinates():
    summary = base._log_safe_write("users", {"id": "usr_1"}, {"email_verified": True})
    assert "geohash=" not in summary
    assert "table=users" in summary


def _noop_async():
    """An awaitable no-op stand-in for the cache-invalidation hook."""

    async def _inner(*_args, **_kwargs):
        return None

    return _inner
