"""ROADMAP N22: instant-payout daily velocity cap + fail-closed service-area gate.

Runs the real ``request_instant_payout`` through the real
``repositories._base`` CRUD helpers against ``mock_supabase_client`` (patched
in by conftest's autouse fixture), with a small per-table fake behind
``client.table(name)`` so each table returns its own rows and records the
PostgREST filter chain. Stripe is mocked; every rejection test asserts the
Transfer was never created and no payout row was reserved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

USER_ID = "user_cap"
DRIVER_ID = "driver_cap"
SA_ID = "sa_regina"


class _FakeTable:
    """Chainable stand-in for one supabase-py table builder."""

    _CHAIN = ("eq", "neq", "gt", "gte", "lt", "lte", "in_", "is_", "order", "limit", "range", "offset")

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.selects: list[list[tuple]] = []  # filter chain per select()
        self.inserts: list[dict] = []
        self.updates: list[dict] = []
        self._op: str | None = None
        self._chain: list[tuple] = []
        self._payload: dict | None = None

    def select(self, *_a, **_k):
        self._op, self._chain = "select", []
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload, self._chain = "update", payload, []
        return self

    def __getattr__(self, name):
        if name in self._CHAIN:

            def _record(*args, **_kw):
                self._chain.append((name, *args))
                return self

            return _record
        raise AttributeError(name)

    def execute(self):
        res = MagicMock()
        if self._op == "select":
            self.selects.append(list(self._chain))
            res.data = list(self.rows)
        elif self._op == "insert":
            self.inserts.append(dict(self._payload))
            res.data = [dict(self._payload)]
        else:
            self.updates.append(dict(self._payload))
            res.data = [dict(self._payload)]
        return res


def _driver(**extra) -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": USER_ID,
        "stripe_account_id": "acct_CAP",
        "gst_bn": "123456789RT0001",
        "stripe_id_number_provided": True,
        "service_area_id": SA_ID,
        **extra,
    }


def _area(**extra) -> dict:
    return {"id": SA_ID, "instant_payout_enabled": True, "timezone": "America/Regina", **extra}


def _req() -> StarletteRequest:
    return StarletteRequest(
        {"type": "http", "method": "POST", "path": "/drivers/payouts/instant", "query_string": b"", "headers": []}
    )


@pytest.fixture
def tables(mock_supabase_client):
    """Per-table fakes wired into the conftest-patched supabase client."""
    fakes = {
        "drivers": _FakeTable([_driver()]),
        "service_areas": _FakeTable([_area()]),
        "payouts": _FakeTable([]),
        "bank_accounts": _FakeTable([]),
    }
    mock_supabase_client.table.side_effect = lambda name: fakes.setdefault(name, _FakeTable([]))
    return fakes


async def _call(amount: str, settings: dict, payable: str = "1000.00"):
    """Invoke the endpoint; return (result_or_exception, Transfer.create mock)."""
    from backend.routes import drivers as drv

    transfer = MagicMock(return_value=MagicMock(id="tr_cap"))
    with (
        patch(
            "backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value={"payable_balance": payable})
        ),
        patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=settings)),
        patch("backend.routes.drivers._deps.stripe.Transfer.create", transfer),
        patch("backend.routes.drivers._deps.stripe.Payout.create", MagicMock(return_value=MagicMock(id="po_cap"))),
    ):
        try:
            out = await drv.request_instant_payout(
                req=drv.InstantPayoutRequest(amount=Decimal(amount)), request=_req(), current_user={"id": USER_ID}
            )
        except HTTPException as exc:
            out = exc
    return out, transfer


def _settings(cap=None) -> dict:
    s = {"stripe_secret_key": "sk_test_cap"}
    if cap is not None:
        s["instant_payout_daily_cap_cad"] = cap
    return s


# ── Cap enforcement ──────────────────────────────────────────────────────


class TestDailyCap:
    @pytest.mark.anyio
    async def test_under_cap_succeeds_and_ignores_rows_that_moved_no_money(self, tables):
        # PostgREST hands NUMERIC back as a JSON number — floats here on purpose.
        tables["payouts"].rows = [
            {"amount": 50.0, "status": "completed"},
            {"amount": 100.0, "status": "failed"},  # moved nothing
            {"amount": 30.0, "status": "reversed"},  # moved nothing
        ]
        out, transfer = await _call("100.00", _settings(cap=200))  # 50 + 100 <= 200

        assert not isinstance(out, HTTPException), out
        assert out["success"] is True
        transfer.assert_called_once()
        cap_query = tables["payouts"].selects[0]
        assert ("eq", "driver_id", DRIVER_ID) in cap_query
        assert ("eq", "payout_type", "instant") in cap_query
        assert any(c[0] == "gte" and c[1] == "created_at" for c in cap_query)

    @pytest.mark.anyio
    async def test_payout_landing_exactly_on_cap_is_allowed(self, tables):
        tables["payouts"].rows = [{"amount": 100.0, "status": "completed"}]
        out, transfer = await _call("100.00", _settings(cap="200.00"))

        assert not isinstance(out, HTTPException), out
        transfer.assert_called_once()

    @pytest.mark.anyio
    async def test_over_cap_rejected_before_any_stripe_call_or_reservation(self, tables):
        # stranded money sits in the driver's Connect account — it counts.
        tables["payouts"].rows = [
            {"amount": 100.0, "status": "completed"},
            {"amount": 50.0, "status": "stranded"},
        ]
        out, transfer = await _call("100.00", _settings(cap=200))  # 150 + 100 > 200

        assert isinstance(out, HTTPException)
        assert out.status_code == 429
        assert "$200.00 per day" in out.detail
        assert "$50.00 more today" in out.detail
        assert "Sunday" in out.detail
        transfer.assert_not_called()
        assert tables["payouts"].inserts == []  # never partially reserved

    @pytest.mark.anyio
    async def test_already_at_cap_rejects_even_the_minimum(self, tables):
        tables["payouts"].rows = [{"amount": 200.0, "status": "completed"}]
        out, transfer = await _call("5.00", _settings(cap=200))

        assert isinstance(out, HTTPException) and out.status_code == 429
        assert "$0.00 more today" in out.detail
        transfer.assert_not_called()
        assert tables["payouts"].inserts == []

    @pytest.mark.anyio
    async def test_row_limit_fails_closed(self, tables):
        from backend.routes.drivers.payouts import _INSTANT_CAP_ROW_LIMIT

        tables["payouts"].rows = [{"amount": 5.0, "status": "failed"}] * _INSTANT_CAP_ROW_LIMIT
        out, transfer = await _call("5.00", _settings(cap=5000))

        assert isinstance(out, HTTPException) and out.status_code == 429
        transfer.assert_not_called()

    @pytest.mark.anyio
    @pytest.mark.parametrize("settings", [_settings(), {**_settings(), "instant_payout_daily_cap_cad": None}])
    async def test_null_cap_is_unchanged_behaviour_and_skips_the_query(self, tables, settings):
        # Would be far over any cap — proves NULL means no cap at all.
        tables["payouts"].rows = [{"amount": 5000.0, "status": "completed"}]
        out, transfer = await _call("500.00", settings)

        assert not isinstance(out, HTTPException), out
        transfer.assert_called_once()
        assert tables["payouts"].selects == []  # no cap read when the cap is off
        assert tables["payouts"].inserts[0]["status"] == "reserved"


# ── Service-area gate (not behind the cap flag) ──────────────────────────


class TestServiceAreaGate:
    @pytest.mark.anyio
    @pytest.mark.parametrize("sa_id", [None, ""])
    async def test_no_service_area_rejected(self, tables, sa_id):
        tables["drivers"].rows = [_driver(service_area_id=sa_id)]
        out, transfer = await _call("50.00", _settings())  # cap NULL: gate is independent of it

        assert isinstance(out, HTTPException)
        assert out.status_code == 403
        assert "service area" in out.detail
        transfer.assert_not_called()
        assert tables["payouts"].inserts == []

    @pytest.mark.anyio
    async def test_unresolvable_service_area_rejected(self, tables):
        tables["service_areas"].rows = []
        out, transfer = await _call("50.00", _settings())

        assert isinstance(out, HTTPException) and out.status_code == 403
        transfer.assert_not_called()


# ── Day boundary ─────────────────────────────────────────────────────────


class _Frozen(datetime):
    """03:30 UTC on 2026-09-25 == 21:30 on 2026-09-24 in Regina (UTC-6, no DST)."""

    @classmethod
    def now(cls, tz=None):
        base = datetime(2026, 9, 25, 3, 30, tzinfo=timezone.utc)
        return base.astimezone(tz) if tz else base.replace(tzinfo=None)


class TestDayStart:
    @pytest.mark.parametrize(
        ("area", "expected"),
        [
            (_area(), datetime(2026, 9, 24, 6, 0, tzinfo=timezone.utc)),  # Regina midnight
            (_area(timezone="Not/AZone"), datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)),  # invalid -> UTC
            (_area(timezone=None), datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)),  # missing -> UTC
        ],
    )
    def test_day_start_uses_service_area_timezone_else_utc(self, area, expected):
        from backend.routes.drivers import payouts

        with patch.object(payouts, "datetime", _Frozen):
            assert payouts._instant_cap_day_start_utc(area) == expected
