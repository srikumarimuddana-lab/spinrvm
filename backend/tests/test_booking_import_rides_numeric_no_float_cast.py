"""ACTION_ITEMS.md B29: several `rides` columns written by
booking_import_service.py's `ride` insert payload are NUMERIC/DECIMAL at the
DB level (verified against information_schema.columns 2026-08-18 -- see
docs/change-log/2026-08-18-b29-booking-import-rides-numeric.md) even though
several *other* columns in the same dict are genuinely FLOAT8 and must stay
that way. A literal float() cast on a NUMERIC target reintroduces binary
floating-point rounding error before the value ever reaches Postgres --
exactly the landmine class B28 closed for payouts.amount.

Sibling of test_payouts_amount_no_float_cast.py: same static source-text
scanning idea, but this file's target dict (`ride`) nests several jsonb
sub-dicts of its own -- including one, `fare_breakdown_snapshot`, with an
unrelated "grand_total" key of its own -- so this version blanks out
nested-brace content rather than reusing the payouts sibling's flat
`_extract_bracketed_block` helper. See `_top_level_ride_dict_text` below.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_FILE = "services/booking_import_service.py"

# The dict literal assigned to `ride` -- the payload appended to
# plan.rides_to_insert and ultimately inserted into the `rides` table.
_RIDE_DICT_MARKER = "ride: dict[str, Any] = {"

# rides columns confirmed NUMERIC/DECIMAL (information_schema, 2026-08-18):
#   grand_total       NUMERIC(10,2)
#   tax_amount        NUMERIC(8,2)
#   area_fees_total   NUMERIC(8,2)
#   discount_amount   NUMERIC(10,2)
# These MUST be serialized via str(Decimal), never float().
_NUMERIC_RIDES_COLUMNS = ("grand_total", "tax_amount", "area_fees_total", "discount_amount")

# rides columns confirmed genuinely double precision (FLOAT8) -- float() is
# correct for these and this test must NOT flag them. Listed explicitly so a
# future column-type change is caught by a failing assertion below rather
# than silently drifting.
_FLOAT8_RIDES_COLUMNS = (
    "distance_km",
    "base_fare",
    "distance_fare",
    "time_fare",
    "booking_fee",
    "airport_fee",
    "surge_multiplier",
    "total_fare",
    "tip_amount",
    "driver_earnings",
    "admin_earnings",
    "pickup_lat",
    "pickup_lng",
    "dropoff_lat",
    "dropoff_lng",
)


def _top_level_ride_dict_text(source: str, start_idx: int) -> str:
    """From the first '{' at/after start_idx (the `ride = {...}` literal),
    return only the text belonging to keys at depth 1 -- i.e. the scalar
    `rides` columns -- with the contents of any nested dict/list value
    (tax_breakdown, area_fees_breakdown, fare_breakdown_snapshot,
    legacy_import_metadata: all jsonb columns, each with their own
    same-named-or-not sub-keys such as fare_breakdown_snapshot's own nested
    "grand_total") blanked out.

    This matters because `ride` dict has a nested "grand_total" inside
    fare_breakdown_snapshot (a jsonb column, correctly still float() --
    that's a receipt line total, not the scalar rides.grand_total column) --
    a naive whole-block regex would false-positive on it. Depth-tracking
    keeps this test scoped to the actual scalar column being written.
    """
    open_idx = source.find("{", start_idx)
    depth = 0
    out_chars: list[str] = []
    for i in range(open_idx, len(source)):
        ch = source[i]
        if ch in "{[":
            depth += 1
            out_chars.append(ch if depth <= 1 else " ")
        elif ch in "}]":
            out_chars.append(ch if depth <= 1 else " ")
            depth -= 1
            if depth == 0:
                break
        else:
            out_chars.append(ch if depth <= 1 else " ")
    return "".join(out_chars)


def _ride_dict_top_level_text() -> str:
    source = (_BACKEND_ROOT / _FILE).read_text()
    idx = source.find(_RIDE_DICT_MARKER)
    assert idx != -1, f"{_FILE}: expected marker {_RIDE_DICT_MARKER!r} not found -- has the ride dict moved?"
    return _top_level_ride_dict_text(source, idx)


@pytest.mark.parametrize("column", _NUMERIC_RIDES_COLUMNS)
def test_numeric_rides_column_not_float_cast(column: str):
    block = _ride_dict_top_level_text()
    bad_pattern = re.compile(rf'"{re.escape(column)}"\s*:\s*float\(')
    assert not bad_pattern.search(block), (
        f"{_FILE} casts rides.{column} with float() in the `ride` insert payload -- "
        f"{column} is NUMERIC/DECIMAL (verified against information_schema.columns), "
        f"so this must be str(Decimal_value) instead (see the other B29-fixed columns "
        f"for the pattern)."
    )


@pytest.mark.parametrize("column", _NUMERIC_RIDES_COLUMNS)
def test_numeric_rides_column_uses_str(column: str):
    """Positive check that the fix actually landed for each column this item closes."""
    block = _ride_dict_top_level_text()
    good_pattern = re.compile(rf'"{re.escape(column)}"\s*:\s*str\(')
    assert good_pattern.search(block), f"{_FILE} should serialize rides.{column} via str(), not float()"


@pytest.mark.parametrize("column", _FLOAT8_RIDES_COLUMNS)
def test_float8_rides_column_left_as_float_cast_or_literal(column: str):
    """Negative control: columns confirmed genuinely FLOAT8 must be left alone.
    This does not require a float() call to exist (some are float literals
    like `0.0`, not casts) -- it only guards against someone "fixing" these
    to str() by mistake, which would be a no-op at best and a type confusion
    at worst on a column that was never NUMERIC."""
    block = _ride_dict_top_level_text()
    wrong_pattern = re.compile(rf'"{re.escape(column)}"\s*:\s*str\(')
    assert not wrong_pattern.search(block), (
        f"{_FILE}: rides.{column} is genuinely FLOAT8 (double precision) per "
        f"information_schema.columns -- it should NOT be wrapped in str(), only the "
        f"NUMERIC/DECIMAL columns in _NUMERIC_RIDES_COLUMNS need that treatment."
    )
