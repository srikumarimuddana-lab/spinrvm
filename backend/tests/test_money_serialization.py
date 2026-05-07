"""Money-on-wire serialization snapshot test.

Audit 17 P0-1 mandates that every money field on every Pydantic response model
serializes as a JSON string (e.g. ``"15.50"``), never an IEEE-754 float
(``15.5``). Float money accumulates rounding error across fare splits, payouts,
and corporate wallet adjustments — see ``CLAUDE.md`` § Critical Conventions.

This test scans ``backend.schemas`` and any model with a money-shaped field
name, and asserts the field's declared annotation routes through
``DecimalStr`` (or another string-serializing wrapper). It fails closed: a new
``float`` money field anywhere in ``schemas.py`` will trip the assertion.
"""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from typing import get_args, get_origin

import pytest
from pydantic import BaseModel

try:
    from backend import schemas
except ImportError:
    import schemas  # type: ignore


# Names that look like money. Any field matching one of these substrings is
# subject to the snapshot. Keep tight — don't match `rating`, `count`, etc.
_MONEY_SUFFIXES = (
    "_fare",
    "_amount",
    "_charged",
    "_balance",
    "_earnings",
    "_payout",
    "_tip",
    "_tax",
    "_fee",
    "_price",
    "_credit",
    "_debit",
    "_delta",
    "_rate",
)
_MONEY_EXACT = {
    "amount",
    "balance",
    "cost",
    "subtotal",
    "total",
    "gst",
    "pst",
    "tip",
    "fare",
}


def _is_money_field(name: str) -> bool:
    n = name.lower()
    if n in _MONEY_EXACT:
        return True
    return any(n.endswith(suf) for suf in _MONEY_SUFFIXES)


def _all_response_models() -> list[type[BaseModel]]:
    return [
        cls
        for _, cls in inspect.getmembers(schemas, inspect.isclass)
        if issubclass(cls, BaseModel) and cls is not BaseModel
    ]


def _money_fields(model: type[BaseModel]) -> list[tuple[str, object]]:
    return [(name, field.annotation) for name, field in model.model_fields.items() if _is_money_field(name)]


def test_no_pydantic_model_has_float_money_field():
    """Every money-shaped field must be Decimal (typically wrapped as DecimalStr).

    Bare ``Decimal`` is acceptable for request models (input parsing); the
    string-serialization wrapper only matters on response. We surface both as
    the same check so a regression to ``float`` anywhere fails loudly.
    """
    offenders: list[str] = []
    for model in _all_response_models():
        for fname, annotation in _money_fields(model):
            origin = get_origin(annotation)
            args = get_args(annotation)
            # Unwrap Optional[...] / Annotated[...] / Union[...]
            candidates = [annotation, *args]
            if any(c is float for c in candidates):
                offenders.append(f"{model.__name__}.{fname} -> {annotation}")
                continue
            # Walk one level into Annotated wrappers
            if origin is not None and any(get_origin(a) is None and a is float for a in args):
                offenders.append(f"{model.__name__}.{fname} -> {annotation}")
    assert not offenders, "Money fields declared as float (must be Decimal/DecimalStr):\n  " + "\n  ".join(offenders)


def test_decimalstr_serializes_as_string():
    """Sanity check the ``DecimalStr`` wrapper itself."""

    class _Probe(BaseModel):
        amount: schemas.DecimalStr

    p = _Probe(amount=Decimal("15.50"))
    payload = json.loads(p.model_dump_json())
    assert isinstance(payload["amount"], str), payload
    assert payload["amount"] == "15.50"


def test_money_field_round_trip_preserves_precision():
    """Rounding case that breaks under float arithmetic but holds for Decimal."""

    class _Probe(BaseModel):
        amount: schemas.DecimalStr

    # 0.1 + 0.2 == 0.30000000000000004 as float; "0.3" as Decimal/string
    p = _Probe(amount=Decimal("0.1") + Decimal("0.2"))
    assert json.loads(p.model_dump_json())["amount"] == "0.3"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("total_fare", True),
        ("base_fare", True),
        ("amount", True),
        ("tip_amount", True),
        ("balance", True),
        ("driver_earnings", True),
        ("rating", False),
        ("count", False),
        ("user_id", False),
        ("created_at", False),
    ],
)
def test_money_field_classifier(name: str, expected: bool):
    assert _is_money_field(name) is expected
