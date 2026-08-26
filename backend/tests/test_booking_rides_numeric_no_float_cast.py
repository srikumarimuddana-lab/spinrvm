"""ACTION_ITEMS.md B35 (follow-up to B28/B29/B30): backend/routes/rides/
booking.py -- the primary ride-creation write path -- writes several
`rides` columns via `_f(...)` (``_shared.py``'s ``_f = float``). Some of
those write sites target genuinely NUMERIC/DECIMAL columns (a real
instance of the same landmine class B28 closed for `payouts.amount`, B29
closed for `booking_import_service.py`, and B30 closed for
`_shared.py`'s `_reestimate_fare_for_stops`); others target genuinely
FLOAT8 (double precision) columns, where `_f()` is the *correct* choice
and must NOT be changed.

Verified live against information_schema.columns, project
soavhtdhefowwvforzwb, 2026-08-20 -- do not trust the grep-only candidate
list this item was originally filed with; it named base_fare/
distance_fare/time_fare/booking_fee/total_fare/driver_earnings/
admin_earnings/airport_fee as suspects too, but every one of those eight
is confirmed genuinely FLOAT8 here (they pass through the `Ride` Pydantic
model, whose fields are typed accordingly), so `_f()` is correct and left
untouched -- pinned as negative controls below so a future well-meaning
edit doesn't "fix" them into a type mismatch:

NUMERIC/DECIMAL (bug -- fixed by this diff, now `_money_str(`):
    rides.authorized_amount   NUMERIC(12,2)   (_attach_preauthorized_hold,
                                                _preauthorize_ride_card x2)
    rides.subtotal_fare       NUMERIC(10,2)   (create_ride promo branch)
    rides.discount_amount     NUMERIC(10,2)   (create_ride promo branch)
    rides.grand_total         NUMERIC(10,2)   (create_ride promo branch,
                                                via the `discounted_grand`
                                                variable)

DOUBLE PRECISION / FLOAT8 (correct as-is -- must stay `_f(`):
    rides.base_fare, distance_fare, time_fare, booking_fee, total_fare,
    driver_earnings, admin_earnings, airport_fee

See docs/change-log/2026-08-20-b35-booking-float-fix.md for the full
Change Impact & Risk Log.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_FILE = "routes/rides/booking.py"

_TOP_LEVEL_DEF_RE = re.compile(r"\n(?:async )?def ")


def _source() -> str:
    return (_BACKEND_ROOT / _FILE).read_text()


def _slice_from_marker(source: str, marker: str, *, label: str) -> str:
    """Return the text from `marker` up to (not including) the next
    top-level function definition -- i.e. roughly "the rest of this
    function's body". Good enough here because none of the scoped
    functions/blocks below contain a nested top-level `def`/`async def`.
    """
    idx = source.find(marker)
    assert idx != -1, f"{_FILE}: expected marker {marker!r} not found -- has {label} moved/renamed?"
    m = _TOP_LEVEL_DEF_RE.search(source, idx + len(marker))
    end = m.start() if m else len(source)
    return source[idx:end]


def _attach_preauthorized_hold_text() -> str:
    return _slice_from_marker(_source(), "async def _attach_preauthorized_hold(", label="_attach_preauthorized_hold")


def _preauthorize_ride_card_text() -> str:
    return _slice_from_marker(_source(), "async def _preauthorize_ride_card(", label="_preauthorize_ride_card")


def _promo_block_text() -> str:
    """The `if body.promo_code:` branch inside create_ride -- the one
    genuinely-NUMERIC write site in this file's ride-creation path."""
    source = _source()
    start = source.find("    if body.promo_code:")
    assert start != -1, f"{_FILE}: expected the promo-code branch marker not found -- has it moved?"
    end = source.find("    # ── Fare breakdown snapshot ──", start)
    assert end != -1, f"{_FILE}: expected the fare-breakdown-snapshot marker after the promo branch not found"
    return source[start:end]


def _ride_constructor_text() -> str:
    """The `ride = Ride(...)` call -- passes through Pydantic's `Ride`
    model, whose base_fare/distance_fare/time_fare/booking_fee/total_fare/
    driver_earnings/admin_earnings fields back genuinely FLOAT8 `rides`
    columns, not the NUMERIC ones this diff fixes."""
    source = _source()
    start = source.find("    ride = Ride(\n")
    assert start != -1, f"{_FILE}: expected the `ride = Ride(` marker not found -- has it moved?"
    end = source.find("\n    )\n", start)
    assert end != -1, f"{_FILE}: expected the closing `)` of the Ride(...) call not found"
    return source[start : end + len("\n    )\n")]


def _airport_fee_line_text() -> str:
    source = _source()
    marker = 'ride_data["airport_fee"] ='
    idx = source.find(marker)
    assert idx != -1, f"{_FILE}: expected marker {marker!r} not found -- has it moved?"
    return source[idx : idx + 80]


# ── NUMERIC/DECIMAL `authorized_amount` write sites ─────────────────────


def test_attach_preauthorized_hold_authorized_amount_not_float_cast():
    block = _attach_preauthorized_hold_text()
    bad_pattern = re.compile(r'"authorized_amount"\s*:\s*_f\(')
    assert not bad_pattern.search(block), (
        f"{_FILE}: _attach_preauthorized_hold casts rides.authorized_amount with _f() "
        f"(= float()) -- authorized_amount is NUMERIC(12,2) (verified against "
        f"information_schema.columns), so this must be _money_str(...) instead."
    )
    good_pattern = re.compile(r'"authorized_amount"\s*:\s*_money_str\(')
    assert good_pattern.search(block), (
        f"{_FILE}: expected _attach_preauthorized_hold to serialize rides.authorized_amount "
        f"via _money_str(), not _f()/float()."
    )


def test_preauthorize_ride_card_authorized_amount_not_float_cast():
    """_preauthorize_ride_card has TWO authorized_amount write sites (the
    initial buffered hold, and the fare-only retry on a buffer-tipped
    decline) -- both must be fixed."""
    block = _preauthorize_ride_card_text()
    bad_pattern = re.compile(r'"authorized_amount"\s*:\s*_f\(')
    assert not bad_pattern.search(block), (
        f"{_FILE}: _preauthorize_ride_card casts rides.authorized_amount with _f() "
        f"(= float()) -- authorized_amount is NUMERIC(12,2), must be _money_str(...)."
    )
    good_pattern = re.compile(r'"authorized_amount"\s*:\s*_money_str\(')
    matches = good_pattern.findall(block)
    assert len(matches) == 2, (
        f"{_FILE}: expected exactly 2 `_money_str(`-serialized authorized_amount write "
        f"sites in _preauthorize_ride_card (initial hold + fare-only retry), found "
        f"{len(matches)}. If a write site was added/removed, update this test's count."
    )


# ── NUMERIC/DECIMAL promo-application write sites ────────────────────────


@pytest.mark.parametrize("column", ["subtotal_fare", "discount_amount"])
def test_promo_branch_numeric_column_not_float_cast(column: str):
    block = _promo_block_text()
    bad_pattern = re.compile(rf'"{re.escape(column)}"\s*:\s*_f\(')
    assert not bad_pattern.search(block), (
        f"{_FILE}: create_ride's promo-application branch casts rides.{column} with "
        f"_f() (= float()) -- {column} is NUMERIC(10,2) (verified against "
        f"information_schema.columns), so this must be _money_str(...) instead."
    )
    good_pattern = re.compile(rf'"{re.escape(column)}"\s*:\s*_money_str\(')
    assert good_pattern.search(block), (
        f"{_FILE}: expected create_ride's promo branch to serialize rides.{column} via _money_str(), not _f()/float()."
    )


def test_promo_branch_discounted_grand_not_float_cast():
    """`discounted_grand` feeds both the `update_one("rides", ...)` write
    (rides.grand_total, NUMERIC(10,2)) and the in-memory `fresh_ride` mirror
    used for the admin monitoring WebSocket broadcast -- both must carry a
    Decimal-safe string, not a float."""
    block = _promo_block_text()
    bad_pattern = re.compile(r"discounted_grand\s*=\s*_f\(")
    assert not bad_pattern.search(block), (
        f"{_FILE}: create_ride's promo branch assigns `discounted_grand` via _f() "
        f"(= float()) -- it is written into rides.grand_total (NUMERIC(10,2)), so "
        f"this must be _money_str(...) instead."
    )
    good_pattern = re.compile(r"discounted_grand\s*=\s*_money_str\(")
    assert good_pattern.search(block), (
        f"{_FILE}: expected `discounted_grand` to be assigned via _money_str(), not _f()."
    )


def test_promo_branch_fresh_ride_mirror_not_float_cast():
    """The in-memory `fresh_ride["subtotal_fare"/"discount_amount"]` mirror
    assignments aren't a DB write themselves, but they feed the admin
    monitoring WS broadcast (build_monitoring_ride) with the same values --
    keep them Decimal-safe strings too, matching the DB write two lines up."""
    block = _promo_block_text()
    for column in ("subtotal_fare", "discount_amount"):
        bad_pattern = re.compile(rf'fresh_ride\["{re.escape(column)}"\]\s*=\s*_f\(')
        assert not bad_pattern.search(block), (
            f"{_FILE}: fresh_ride[{column!r}] mirror assignment uses _f() (= float()) -- "
            f"should be _money_str(...) to match the rides.{column} DB write."
        )
        good_pattern = re.compile(rf'fresh_ride\["{re.escape(column)}"\]\s*=\s*_money_str\(')
        assert good_pattern.search(block), f"{_FILE}: expected fresh_ride[{column!r}] to be assigned via _money_str()."


# ── Negative controls: genuinely FLOAT8 columns must stay `_f(` ──────────

_FLOAT8_RIDE_CONSTRUCTOR_FIELDS = (
    "base_fare",
    "distance_fare",
    "time_fare",
    "booking_fee",
    "total_fare",
    "driver_earnings",
    "admin_earnings",
)


@pytest.mark.parametrize("field", _FLOAT8_RIDE_CONSTRUCTOR_FIELDS)
def test_ride_constructor_float8_field_still_uses_f(field: str):
    """Negative control -- these seven fields on the `Ride(...)` call are
    confirmed genuinely FLOAT8 (double precision) `rides` columns (not
    NUMERIC, despite being named as candidates in B35's original grep-only
    filing). `_f()` is correct here; this guards against a future edit
    "fixing" them into a str()/_money_str() type mismatch against the
    Pydantic `Ride` model's float-backed columns."""
    block = _ride_constructor_text()
    good_pattern = re.compile(rf"{re.escape(field)}\s*=\s*_f\(")
    assert good_pattern.search(block), (
        f"{_FILE}: expected `ride = Ride(...)`'s {field}= argument to stay _f(...) -- "
        f"rides.{field} is genuinely FLOAT8 (double precision) per "
        f"information_schema.columns, not NUMERIC."
    )
    wrong_pattern = re.compile(rf"{re.escape(field)}\s*=\s*_money_str\(")
    assert not wrong_pattern.search(block), (
        f"{_FILE}: `ride = Ride(...)`'s {field}= argument should NOT be _money_str(...) -- "
        f"rides.{field} is FLOAT8, not NUMERIC; a Decimal-string here would round-trip "
        f"through the Ride model's float-typed field unnecessarily."
    )


def test_airport_fee_still_uses_f():
    """Negative control -- rides.airport_fee is confirmed genuinely FLOAT8
    (double precision), not NUMERIC. `_f()` is correct."""
    line = _airport_fee_line_text()
    assert re.match(r'ride_data\["airport_fee"\]\s*=\s*_f\(', line), (
        f'{_FILE}: expected ride_data["airport_fee"] to stay _f(...) -- rides.airport_fee '
        f"is genuinely FLOAT8 (double precision) per information_schema.columns, not NUMERIC."
    )
