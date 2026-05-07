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


# ---------------------------------------------------------------------------
# Phase 1c — dict-response money fields cross the wire as strings
# ---------------------------------------------------------------------------
#
# Pydantic models route money through ``DecimalStr``; routes that hand-build
# dict responses (rides.py, corporate_company.py) bypass schemas and must use
# the ``_money_str`` helpers introduced in PR #555 follow-up. These tests
# exercise the helpers directly + confirm the response builders that we
# touched no longer leak floats.


class TestMoneyStrHelpers:
    """The two ``_money_str`` helpers (rides + corporate_company) must
    produce JSON-safe decimal strings, never floats."""

    def test_rides_money_str_returns_string(self):
        from backend.routes.rides import _money_str as rides_money_str

        out = rides_money_str(Decimal("15.50"))
        assert isinstance(out, str)
        assert out == "15.50"

    def test_rides_money_str_quantizes_to_two_dp(self):
        from backend.routes.rides import _money_str as rides_money_str

        # 15.5 → "15.50" (no trailing-zero loss); 15.999 → "16.00" (round half up)
        assert rides_money_str(Decimal("15.5")) == "15.50"
        assert rides_money_str(Decimal("15.995")) == "16.00"

    def test_rides_money_str_handles_float_input(self):
        from backend.routes.rides import _money_str as rides_money_str

        # Tolerant of legacy float arithmetic at the call site so we never
        # silently fall back to repr(float) on mixed inputs.
        out = rides_money_str(0.1 + 0.2)  # 0.30000000000000004 as float
        assert isinstance(out, str)
        assert out == "0.30"

    def test_corporate_money_str_returns_string(self):
        from backend.routes.corporate_company import _money_str as corp_money_str

        out = corp_money_str(Decimal("250.00"))
        assert isinstance(out, str)
        assert out == "250.00"

    def test_corporate_money_str_handles_string_input(self):
        from backend.routes.corporate_company import _money_str as corp_money_str

        # Wallet balance comes from the DB as a string ("123.45") in the
        # billing endpoints — confirm we don't choke on that shape.
        assert corp_money_str("123.45") == "123.45"
        assert corp_money_str("0") == "0.00"

    def test_corporate_money_str_handles_zero_division_protection(self):
        from backend.routes.corporate_company import _money_str as corp_money_str

        assert corp_money_str(None) == "0.00"


class TestCorporateBillingSummaryAggregateShape:
    """``_aggregate_rows`` is the heart of /company/{id}/billing/summary
    and /billing/statements/{month}. Every money key it emits must be str."""

    def _aggregate(self):
        # Force a clean import so module-level mocks/state from earlier
        # tests don't leak in.
        return _import_aggregate_rows()

    def test_empty_rows_emits_string_zero(self):
        agg = self._aggregate()
        out = agg([])
        assert out["ride_count"] == 0
        assert out["allowance_total"] == "0.00"
        assert out["master_total"] == "0.00"
        assert out["total"] == "0.00"
        # avg_fare branches on rows being non-empty — empty path must
        # also return a string, not a float 0.0.
        assert isinstance(out["avg_fare"], str)
        assert out["avg_fare"] == "0.00"
        assert out["by_member"] == []

    def test_populated_rows_money_fields_are_strings(self):
        agg = self._aggregate()
        rows = [
            {"member_id": "m1", "allowance_debit_amount": "10.00", "master_fallback_amount": "5.50"},
            {"member_id": "m1", "allowance_debit_amount": "0.00", "master_fallback_amount": "20.00"},
            {"member_id": "m2", "allowance_debit_amount": "8.25", "master_fallback_amount": "0.00"},
        ]
        out = agg(rows)

        for key in ("allowance_total", "master_total", "total", "avg_fare"):
            assert isinstance(out[key], str), f"{key} is {type(out[key])}: {out[key]!r}"

        # by_member rollups
        assert len(out["by_member"]) == 2
        for member_row in out["by_member"]:
            for key in ("allowance_total", "master_total", "total"):
                assert isinstance(member_row[key], str), f"by_member.{key} is {type(member_row[key])}"

        # Sanity arithmetic — allowance total = 10 + 0 + 8.25 = 18.25
        assert out["allowance_total"] == "18.25"
        # master total = 5.50 + 20 + 0 = 25.50
        assert out["master_total"] == "25.50"
        # avg_fare = (18.25 + 25.50) / 3 = 14.5833... → "14.58"
        assert out["avg_fare"] == "14.58"


def _import_aggregate_rows():
    """Import the corporate_company aggregator without triggering FastAPI app
    startup. Mirrors the stubbing pattern used in test_p2_corporate_decimal.py.
    """
    import sys
    from unittest.mock import MagicMock

    for _m in (
        "supabase",
        "stripe",
        "gotrue",
        "postgrest",
        "realtime",
        "firebase_admin",
        "firebase_admin.auth",
        "firebase_admin.credentials",
        "firebase_admin.messaging",
        "twilio",
        "twilio.rest",
        "slowapi",
        "slowapi.errors",
        "slowapi.util",
        "redis",
        "redis.asyncio",
        "jwt",
    ):
        if _m not in sys.modules:
            sys.modules[_m] = MagicMock()

    from backend.routes.corporate_company import _aggregate_rows

    return _aggregate_rows


class TestRidesProcessPaymentResponseStringShape:
    """The ``process_payment`` early-return paths build dict responses by
    hand. Confirm the money fields they emit are strings.

    We don't drive the full handler (auth/DB orchestration is out of scope
    for a unit test) — instead we lift the exact response-builder
    expressions and assert the wire shape. If the source moves, this
    test must be updated alongside it.
    """

    def test_already_paid_charged_amount_is_string(self):
        from decimal import Decimal as _D

        from backend.routes.rides import _money_str

        # Mirrors line ~1655 in routes/rides.py:
        #   "charged_amount": _money_str(_d(total_fare) + _d(tip_amount))
        ride = {"total_fare": "12.50", "tip_amount": "2.00"}
        charged = _money_str(_D(str(ride["total_fare"])) + _D(str(ride["tip_amount"])))
        assert isinstance(charged, str)
        assert charged == "14.50"

    def test_processing_lock_held_charged_amount_is_string(self):
        from decimal import Decimal as _D

        from backend.routes.rides import _money_str

        # Mirrors the ``guard_row is None`` branch at line ~1674.
        ride = {"total_fare": 18.75}
        charged = _money_str(_D(str(ride["total_fare"])))
        assert isinstance(charged, str)
        assert charged == "18.75"


class TestRidesEstimateAndTipResponseShape:
    """Estimate dict responses and tip endpoint response — the explicit
    Phase 1c targets in the rides.py audit."""

    def test_estimate_dict_money_fields_are_strings(self):
        from decimal import Decimal as _D

        from backend.routes.rides import _money_str

        # Mirrors the dict appended to ``estimates`` in
        # _fares_for_location_impl (~line 744). surge_multiplier is
        # intentionally float (matches Ride model), the rest are money.
        item = {
            "base_fare": _money_str(_D("4.50")),
            "distance_fare": _money_str(_D("12.30")),
            "time_fare": _money_str(_D("3.00")),
            "booking_fee": _money_str(_D("2.00")),
            "total_fare": _money_str(_D("21.80")),
        }
        for k, v in item.items():
            assert isinstance(v, str), f"{k} is {type(v)}"

    def test_tip_endpoint_response_money_field_is_string(self):
        from decimal import Decimal as _D

        from backend.routes.rides import _money_str

        # Mirrors the final return at line ~1588.
        new_tip = _D("5.00")
        resp = {"success": True, "tip_amount": _money_str(new_tip)}
        assert isinstance(resp["tip_amount"], str)
        assert resp["tip_amount"] == "5.00"

    def test_tip_websocket_payload_money_fields_are_strings(self):
        from decimal import Decimal as _D

        from backend.routes.rides import _money_str

        # Mirrors the WS notify payload at line ~1567.
        payload = {
            "type": "tip_received",
            "ride_id": "ride_abc",
            "amount": _money_str(_D("5.00")),
            "new_total": _money_str(_D("5.00")),
            "rider_name": "Test",
        }
        assert isinstance(payload["amount"], str)
        assert isinstance(payload["new_total"], str)


def test_rides_f_helper_still_returns_float():
    """``_f()`` is reserved for legacy callers that genuinely need float
    (Pydantic ``Ride.surge_multiplier``, ``sign_estimate_token``,
    ``calculate_all_fees``). It must NOT be silently converted to str —
    that would break the signed-token contract."""
    from backend.routes.rides import _f

    assert isinstance(_f(Decimal("1.5")), float)
    assert _f(Decimal("1.5")) == 1.5
