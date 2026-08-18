"""ACTION_ITEMS.md B28: payouts.amount is NUMERIC(10,2) as of migration 331
(was a legacy FLOAT8 column). Every writer that builds a payouts insert
payload must serialize its Decimal amount via str(), never float() -- a
literal float() cast reintroduces binary floating-point rounding error
before the value ever reaches Postgres, exactly the landmine this migration
was meant to close.

Static source-text check (same style as the repo's pre-commit money-
arithmetic hook) rather than a runtime assertion, so it catches the mistake
even in a code path this test suite doesn't happen to exercise at runtime.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Every module that builds a dict to insert into the `payouts` table.
_PAYOUT_WRITER_FILES = [
    "services/legacy_payout_correction_service.py",
    "services/stripe_payout_sync_service.py",
    "services/booking_import_service.py",
    "routes/drivers/payouts.py",
]

# Matches `"amount": float(...)` (any whitespace) -- the exact regression
# this migration/fix closes.
_BAD_PATTERN = re.compile(r'"amount"\s*:\s*float\(')

# Markers that scope the check to dicts actually destined for the `payouts`
# table's insert/update payload -- some of these files (booking_import_
# service.py in particular) also build unrelated dicts with an "amount" key
# for a jsonb column on `rides` (fare_breakdown line items), which is a
# different column and correctly out of scope for this check.
_PAYOUTS_DICT_MARKERS = (
    "payouts_to_insert.append(",
    "to_insert = [",  # legacy_payout_correction_service.py's payouts list-comprehension
)


def _extract_bracketed_block(source: str, start_idx: int) -> str:
    """From the first '{' or '[' at/after start_idx, return the source text
    up to its matching close bracket (simple depth-counting scan -- these
    payout dicts/lists have no nested braces of their own)."""
    open_idx = min((i for i in (source.find("{", start_idx), source.find("[", start_idx)) if i != -1))
    opener = source[open_idx]
    closer = "}" if opener == "{" else "]"
    depth = 0
    for i in range(open_idx, len(source)):
        if source[i] == opener:
            depth += 1
        elif source[i] == closer:
            depth -= 1
            if depth == 0:
                return source[open_idx : i + 1]
    return source[open_idx:]  # unterminated -- return the rest, still checkable


@pytest.mark.parametrize("relpath", _PAYOUT_WRITER_FILES)
def test_no_float_cast_on_payouts_amount(relpath: str):
    source = (_BACKEND_ROOT / relpath).read_text()
    blocks = []
    for marker in _PAYOUTS_DICT_MARKERS:
        idx = source.find(marker)
        if idx != -1:
            blocks.append(_extract_bracketed_block(source, idx))
    # routes/drivers/payouts.py has neither marker -- it builds its payouts
    # dict inline and passes it through db_supabase.insert_one/update_one,
    # which serialize Decimal correctly on their own (no manual cast at all,
    # see repositories/_base.py's _serialize_for_api). Fall back to a
    # whole-file check there so this test still means something for it.
    if not blocks:
        blocks = [source]
    for block in blocks:
        matches = _BAD_PATTERN.findall(block)
        assert not matches, (
            f"{relpath} casts a payouts.amount value with float() -- payouts.amount is "
            f"NUMERIC as of migration 331, so this must be str(Decimal_value) instead "
            f"(see the other writers for the pattern)."
        )


def test_payout_service_writers_use_str_not_float():
    """Positive check that the fix actually landed in the two services that
    build raw payouts.amount payloads (bypass db_supabase.insert_one, so
    they need an explicit str() cast -- see each file's own comment)."""
    for relpath in (
        "services/legacy_payout_correction_service.py",
        "services/stripe_payout_sync_service.py",
        "services/booking_import_service.py",
    ):
        source = (_BACKEND_ROOT / relpath).read_text()
        assert '"amount": str(' in source, f"{relpath} should serialize payouts.amount via str(), not float()"
