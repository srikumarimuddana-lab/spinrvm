"""ACTION_ITEMS.md B30: routes/rides/_shared.py's ``_reestimate_fare_for_stops``
(the shared fare-snapshot builder used by the live mid-trip stop-editing
path -- POST/DELETE /rides/{id}/stops in routes/rides/stops.py) writes
several `rides` columns that are NUMERIC/DECIMAL at the DB level (verified
against information_schema.columns 2026-08-20 -- see
docs/change-log/2026-08-20-b30-shared-fare-float-fix.md). A literal
float() cast on a NUMERIC target reintroduces binary floating-point
rounding error before the value ever reaches Postgres -- the same landmine
class B28 closed for payouts.amount and B29 closed for
booking_import_service.py's rides insert, this time on the live booking
path rather than an offline importer.

Sibling of test_booking_import_rides_numeric_no_float_cast.py: same
depth-aware static source-text scanning idea, needed here because the
`result = {...}` dict has its own nested-brace default-arg literals (e.g.
``fees_result.get("tax_breakdown", {})``) that a naive scan must not
misread as a second dict layer to flag.

Scope note (verified before fixing, not assumed): of the four columns
B30's filing named, only ``tax_amount`` and ``area_fees_total`` actually
had the float(_round(...)) bug in this file's scalar `result` dict.
``grand_total`` was already correctly written via `_money_str` (str) at
the scalar-column site -- this test pins that too, so it can't regress
back to float(). ``discount_amount`` is never written by this file at all
(only read for display in `_build_fare_breakdown`) -- there is nothing to
fix or pin for it here. A *second*, textually identical "grand_total" key
exists further down in the same function, but it belongs to the separate
`fare_breakdown_snapshot` JSONB column (migration 90) -- jsonb has no
NUMERIC concept, so it is correctly left as float() per the same carve-out
B28/B29 already established for jsonb sub-fields; this test's negative
control pins that it stays that way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_FILE = "routes/rides/_shared.py"

# Anchor scoped to _reestimate_fare_for_stops -- the function whose `result`
# dict is spread directly into a `rides` $set update by routes/rides/stops.py
# (add_stop_mid_trip / remove_stop_mid_trip).
_FUNC_MARKER = "async def _reestimate_fare_for_stops"
_RESULT_DICT_MARKER = "result = {"

# rides columns confirmed NUMERIC/DECIMAL (migrations 46/82; re-verified live
# against information_schema.columns, project soavhtdhefowwvforzwb,
# 2026-08-20): grand_total NUMERIC(10,2), tax_amount NUMERIC(8,2),
# area_fees_total NUMERIC(8,2), discount_amount NUMERIC(10,2).
# Only the first three are actually written by this file's `result` dict.
_NUMERIC_COLUMNS_FIXED_HERE = ("tax_amount", "area_fees_total")
_NUMERIC_COLUMN_ALREADY_CORRECT = "grand_total"


def _extract_balanced_block(source: str, open_brace_idx: int) -> str:
    """Return the text of the dict literal starting at ``open_brace_idx``,
    with the contents of any nested dict/list value blanked out (kept only
    at depth 1) -- same technique as
    test_booking_import_rides_numeric_no_float_cast.py's
    _top_level_ride_dict_text, needed here because of nested-brace default
    args like ``fees_result.get("tax_breakdown", {})``.
    """
    depth = 0
    out: list[str] = []
    for i in range(open_brace_idx, len(source)):
        ch = source[i]
        if ch in "{[":
            depth += 1
            out.append(ch if depth <= 1 else " ")
        elif ch in "}]":
            out.append(ch if depth <= 1 else " ")
            depth -= 1
            if depth == 0:
                break
        else:
            out.append(ch if depth <= 1 else " ")
    return "".join(out)


def _result_dict_text() -> str:
    source = (_BACKEND_ROOT / _FILE).read_text()
    func_idx = source.find(_FUNC_MARKER)
    assert func_idx != -1, f"{_FILE}: expected marker {_FUNC_MARKER!r} not found -- has the function moved?"
    dict_idx = source.find(_RESULT_DICT_MARKER, func_idx)
    assert dict_idx != -1, (
        f"{_FILE}: expected {_RESULT_DICT_MARKER!r} inside {_FUNC_MARKER!r} not found -- has it moved/renamed?"
    )
    open_idx = source.find("{", dict_idx)
    return _extract_balanced_block(source, open_idx)


def _fare_breakdown_snapshot_dict_text() -> str:
    """The separate, later `result["fare_breakdown_snapshot"] = {...}`
    assignment -- a JSONB column, not the scalar `result` dict above."""
    source = (_BACKEND_ROOT / _FILE).read_text()
    marker = 'result["fare_breakdown_snapshot"] = {'
    idx = source.find(marker)
    assert idx != -1, f"{_FILE}: expected marker {marker!r} not found -- has it moved?"
    open_idx = source.find("{", idx)
    return _extract_balanced_block(source, open_idx)


@pytest.mark.parametrize("column", _NUMERIC_COLUMNS_FIXED_HERE)
def test_numeric_rides_column_not_float_cast(column: str):
    block = _result_dict_text()
    bad_pattern = re.compile(rf'"{re.escape(column)}"\s*:\s*float\(')
    assert not bad_pattern.search(block), (
        f"{_FILE} casts rides.{column} with float() in _reestimate_fare_for_stops's "
        f"`result` dict -- {column} is NUMERIC/DECIMAL (verified against "
        f"information_schema.columns), so this must be str(_round(...)) instead."
    )


@pytest.mark.parametrize("column", _NUMERIC_COLUMNS_FIXED_HERE)
def test_numeric_rides_column_uses_str(column: str):
    """Positive check that the B30 fix actually landed for each column."""
    block = _result_dict_text()
    good_pattern = re.compile(rf'"{re.escape(column)}"\s*:\s*str\(')
    assert good_pattern.search(block), f"{_FILE} should serialize rides.{column} via str(), not float()"


def test_grand_total_scalar_write_stays_str():
    """`grand_total` was already correct (via _money_str, which is str()) at
    the scalar-column site before this fix -- pin it so it can't regress
    back to float() in a future edit."""
    block = _result_dict_text()
    bad_pattern = re.compile(rf'"{re.escape(_NUMERIC_COLUMN_ALREADY_CORRECT)}"\s*:\s*float\(')
    assert not bad_pattern.search(block), (
        f"{_FILE}: the scalar rides.grand_total write in _reestimate_fare_for_stops's "
        f"`result` dict must stay str()/_money_str(), never float()."
    )
    good_pattern = re.compile(rf'"{re.escape(_NUMERIC_COLUMN_ALREADY_CORRECT)}"\s*:\s*(str\(|_money_str\()')
    assert good_pattern.search(block), (
        f"{_FILE}: expected rides.grand_total to be written via str(...) or _money_str(...)"
    )


def test_discount_amount_not_written_by_this_function():
    """B30's filing named discount_amount as a fourth affected column, but
    this file never writes it -- confirmed by direct read before fixing
    anything (see docs/change-log/2026-08-20-b30-shared-fare-float-fix.md).
    Guards against silently reintroducing a float() write for it here
    without deliberately updating this test's scope."""
    block = _result_dict_text()
    assert '"discount_amount"' not in block, (
        f"{_FILE}: a 'discount_amount' key appeared in _reestimate_fare_for_stops's "
        f"result dict -- if this is intentional, it must be serialized via str(_round(...)), "
        f"not float(), and this test should be updated to cover it explicitly."
    )


def test_fare_breakdown_snapshot_grand_total_stays_float_jsonb_carveout():
    """Negative control: the *separate* nested `grand_total` key inside the
    `fare_breakdown_snapshot` JSONB column (migration 90) is correctly left
    as float() -- jsonb has no NUMERIC concept, matching the carve-out B28/B29
    already established for jsonb sub-fields. This must NOT be "fixed" to
    str() by a future well-meaning-but-wrong edit that conflates it with the
    scalar `rides.grand_total` column above."""
    block = _fare_breakdown_snapshot_dict_text()
    good_pattern = re.compile(r'"grand_total"\s*:\s*float\(')
    assert good_pattern.search(block), (
        f"{_FILE}: expected the fare_breakdown_snapshot jsonb 'grand_total' key to stay "
        f"float() -- it is a jsonb sub-field, not the scalar rides.grand_total NUMERIC column."
    )
