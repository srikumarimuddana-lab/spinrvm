"""ACTION_ITEMS.md B36: services/fare_service.py's ``recalculate_fare_for_distance``
(the fare recompute run on ride completion when the actual trip distance
differs meaningfully from the booking-time estimate -- called from
routes/drivers/ride_complete.py, whose result dict is merged directly into
the `rides` $set update) writes several `rides` columns. Only one of them,
``grand_total``, is NUMERIC at the DB level -- verified against
information_schema.columns (project soavhtdhefowwvforzwb) 2026-08-20 --
so a literal float() cast on it reintroduces binary floating-point
rounding error before the value ever reaches Postgres, the same landmine
class B28/B29/B30/B35 closed elsewhere in this repo.

Scope note (verified before fixing, not assumed -- this is the corrective
pass on the "NOT fixed here" note left by B35's own filing): of the five
fields this function returns, only ``grand_total`` is actually a NUMERIC
column bug.

    distance_km        FLOAT8 -- not touched by this fix (plain float(),
                        never went through _f/_money_str at all)
    distance_fare       FLOAT8 -- _f() is correct, left as-is
    total_fare          FLOAT8 -- _f() is correct, left as-is
    driver_earnings     FLOAT8 -- _f() is correct, left as-is
    grand_total         NUMERIC(10,2) -- _f() was WRONG; fixed to _money_str()

This mirrors B35's own corrected finding that most of `rides`' money
columns are genuinely FLOAT8 (driver_earnings/total_fare/base_fare/etc. --
see migrations 159/303 comments) and only a handful (grand_total,
discount_amount, subtotal_fare, authorized_amount) are NUMERIC. Do not
"fix" the FLOAT8 fields below to str() -- that would itself be a bug
(re-quantizing what Postgres already stores as a float, and if any caller
ever compares e.g. driver_earnings as a numeric type it would now see a
Postgres string-typed value flowing through a float8 column, which the
driver-side codepaths were never written to expect).
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_FILE = "services/fare_service.py"

_FUNC_MARKER = "def recalculate_fare_for_distance("
_RETURN_DICT_MARKER = "return {"

# rides columns this function actually writes, confirmed live against
# information_schema.columns (project soavhtdhefowwvforzwb) 2026-08-20.
_FLOAT8_COLUMNS = ("distance_fare", "total_fare", "driver_earnings")
_NUMERIC_COLUMN_FIXED_HERE = "grand_total"


def _return_dict_text() -> str:
    source = (_BACKEND_ROOT / _FILE).read_text()
    func_idx = source.find(_FUNC_MARKER)
    assert func_idx != -1, f"{_FILE}: expected marker {_FUNC_MARKER!r} not found -- has the function moved?"
    # Only look within this function's body -- stop at the next top-level `def`/`class`.
    next_def_idx = source.find("\nclass FareService", func_idx)
    if next_def_idx == -1:
        next_def_idx = len(source)
    func_body = source[func_idx:next_def_idx]

    dict_idx = func_body.find(_RETURN_DICT_MARKER)
    assert dict_idx != -1, (
        f"{_FILE}: expected {_RETURN_DICT_MARKER!r} inside {_FUNC_MARKER!r} not found -- has it moved/renamed?"
    )
    open_idx = func_body.find("{", dict_idx)
    depth = 0
    out: list[str] = []
    for ch in func_body[open_idx:]:
        out.append(ch)
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
    return "".join(out)


def test_grand_total_is_not_float_cast():
    """The B36 fix: grand_total is NUMERIC(10,2) on `rides`, so this must
    never be a literal float() (nor `_f()`, this file's float-cast helper)."""
    block = _return_dict_text()
    bad_pattern = re.compile(rf'"{_NUMERIC_COLUMN_FIXED_HERE}"\s*:\s*_f\(')
    assert not bad_pattern.search(block), (
        f"{_FILE} casts rides.grand_total with _f() in recalculate_fare_for_distance's "
        f"return dict -- grand_total is NUMERIC(10,2) (verified against "
        f"information_schema.columns), so this must be _money_str(...) instead."
    )


def test_grand_total_uses_money_str():
    """Positive check that the B36 fix actually landed."""
    block = _return_dict_text()
    good_pattern = re.compile(rf'"{_NUMERIC_COLUMN_FIXED_HERE}"\s*:\s*_money_str\(')
    assert good_pattern.search(block), (
        f"{_FILE}: expected rides.grand_total to be written via _money_str(...) in "
        f"recalculate_fare_for_distance's return dict."
    )


@pytest.mark.parametrize("column", _FLOAT8_COLUMNS)
def test_float8_columns_stay_f_cast(column: str):
    """Negative control: these columns are genuinely FLOAT8 on `rides` --
    _f() is correct for them and must NOT be "fixed" to _money_str()/str()
    by a future well-meaning-but-wrong edit that conflates them with
    grand_total."""
    block = _return_dict_text()
    good_pattern = re.compile(rf'"{re.escape(column)}"\s*:\s*_f\(')
    assert good_pattern.search(block), (
        f"{_FILE}: expected rides.{column} to stay serialized via _f() in "
        f"recalculate_fare_for_distance -- it is FLOAT8, not NUMERIC (verified against "
        f"information_schema.columns)."
    )


# ── Runtime behavior: the actual bytes returned, not just source-text shape ──


def _completed_ride(**overrides):
    ride = {
        "id": "ride_1",
        "base_fare": 3.50,
        "distance_fare": 0.15,
        "time_fare": 0.25,
        "booking_fee": 2.00,
        "airport_fee": 0,
        "distance_km": 0.1,
        "total_fare": 8.00,
    }
    ride.update(overrides)
    return ride


def test_grand_total_returned_as_decimal_safe_string_not_float():
    """Runtime pin: grand_total must come back as a str, never a float --
    the B36 bug this test suite exists to catch."""
    from services.fare_service import recalculate_fare_for_distance

    ride = _completed_ride(
        distance_fare=15.00,
        time_fare=5.00,
        distance_km=10,
        total_fare=25.50,
        area_fees_total=Decimal("0.03"),  # fractional-cent input, see dry-run scenario
    )
    out = recalculate_fare_for_distance(ride, actual_distance_km=8)
    assert isinstance(out["grand_total"], str), (
        f"grand_total must be a Decimal-safe string, got {type(out['grand_total'])}: {out['grand_total']!r}"
    )
    # 22.50 (3.50 + 12.00 + 5.00 + 2.00) + 0.03 area fees = 22.53, exact --
    # a float round-trip through _f() is the historical failure mode this
    # guards against (IEEE-754 binary fractions don't represent 0.03 exactly).
    assert out["grand_total"] == "22.53"


def test_float8_fields_still_returned_as_float():
    """Runtime pin: the genuinely-FLOAT8 fields must stay plain floats --
    confirms the fix didn't overcorrect into the FLOAT8 columns."""
    from services.fare_service import recalculate_fare_for_distance

    out = recalculate_fare_for_distance(_completed_ride(), actual_distance_km=0.05)
    assert isinstance(out["distance_fare"], float)
    assert isinstance(out["total_fare"], float)
    assert isinstance(out["driver_earnings"], float)
