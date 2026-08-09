"""Email-based discovery of Stripe accounts for unlinked drivers.

The refresh tooling follows ``drivers.stripe_account_id`` and never guesses,
so an account that plainly exists in the Stripe dashboard is invisible until
the column is filled. Discovery proposes those links — and the tests pin the
property that keeps it safe: it is READ-ONLY and strict. Exact email equality
only, one-to-one only, already-linked accounts excluded, ambiguity reported
rather than resolved. All writes stay in the validated import.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.services import stripe_mapping_import_service as svc

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _driver(did="drv_1", email="a@x.com", phone="3065550001", **extra) -> dict:
    row = {
        "id": did,
        "email": email,
        "phone": phone,
        "user_id": f"user_{did}",
        "stripe_account_id": None,
        "stripe_account_id_superseded": None,
    }
    row.update(extra)
    return row


def _account(aid="acct_1", email="a@x.com", **extra) -> dict:
    row = {
        "id": aid,
        "email": email,
        "country": "CA",
        "type": "express",
        "details_submitted": True,
        "payouts_enabled": True,
        "created": 1735603200,
    }
    row.update(extra)
    return row


def _patches(drivers, accounts, linked=None):
    linked_rows = MagicMock()
    linked_rows.data = [{"stripe_account_id": a} for a in (linked or [])]
    sb = MagicMock()
    sb.table.return_value.select.return_value.not_.is_.return_value.execute.return_value = linked_rows
    return (
        patch.object(svc, "_unlinked_drivers_with_emails", MagicMock(return_value=drivers)),
        patch.object(svc, "_list_connected_accounts", MagicMock(return_value=accounts)),
        patch.object(svc, "supabase", sb),
    )


async def _run(drivers, accounts, linked=None):
    p1, p2, p3 = _patches(drivers, accounts, linked)
    with p1, p2, p3:
        return await svc.discover_driver_accounts_by_email("sk_test_x")


class TestMatching:
    async def test_exact_email_match_is_proposed_with_csv(self):
        report = await _run([_driver()], [_account()])
        assert report["matched"] == 1
        m = report["matches"][0]
        assert (m["driver_id"], m["stripe_account_id"], m["matched_on"]) == ("drv_1", "acct_1", "email")
        # The CSV is exactly what the validated import consumes.
        assert report["csv"] == "stripe_account_id,phone\nacct_1,+13065550001\n"

    async def test_email_match_is_case_insensitive(self):
        """The matcher lowers both sides itself — it must not depend on the
        fetchers having pre-normalized."""
        report = await _run([_driver(email="Driver@X.com")], [_account(email="dRiVeR@x.CoM")])
        assert report["matched"] == 1

    async def test_no_fuzzy_matching(self):
        """a@x.com must not match a+stripe@x.com or a@y.com — payout
        destinations are never guessed."""
        report = await _run([_driver(email="a@x.com")], [_account(email="a+stripe@x.com"), _account("acct_2", "a@y.com")])
        assert report["matched"] == 0
        assert report["unmatched_drivers"] == 1

    async def test_already_linked_account_is_never_proposed(self):
        """An account on someone else's driver row must not be offered to a
        second driver, even on an exact email hit."""
        report = await _run([_driver()], [_account()], linked=["acct_1"])
        assert report["matched"] == 0

    async def test_retired_driver_is_flagged(self):
        d = _driver(stripe_account_id_superseded="acct_old")
        report = await _run([d], [_account()])
        assert report["matches"][0]["was_retired"] is True


class TestAmbiguity:
    async def test_one_email_two_accounts_is_ambiguous_not_guessed(self):
        report = await _run([_driver()], [_account("acct_a"), _account("acct_b")])
        assert report["matched"] == 0
        assert len(report["ambiguous"]) == 1
        assert sorted(report["ambiguous"][0]["email_accounts"]) == ["acct_a", "acct_b"]

    async def test_two_drivers_one_account_is_ambiguous(self):
        report = await _run([_driver("drv_a"), _driver("drv_b")], [_account()])
        assert report["matched"] == 0
        assert sorted(report["ambiguous"][0]["email_drivers"]) == ["drv_a", "drv_b"]


class TestEdges:
    async def test_match_without_phone_is_surfaced_not_dropped(self):
        """The import matches on phone, so a phoneless match cannot ride the
        CSV — it must be reported, or the operator thinks it imported."""
        report = await _run([_driver(phone="")], [_account()])
        assert report["matched"] == 1
        assert report["matches_without_phone"] == ["drv_1"]
        assert report["csv"] == ""  # header-only collapses to empty

    async def test_driver_without_email_counts_unmatched(self):
        report = await _run([_driver(email="")], [_account()])
        assert report["matched"] == 0
        assert report["unmatched_drivers"] == 1

    async def test_account_cap_fails_loud(self):
        import itertools

        fake = MagicMock()
        fake.auto_paging_iter.return_value = ({"id": f"acct_{i}", "email": ""} for i in itertools.count())
        with patch("stripe.Account.list", MagicMock(return_value=fake)):
            with pytest.raises(RuntimeError, match="refusing to match partially"):
                svc._list_connected_accounts("sk_test_x", cap=50)


class TestRoute:
    async def test_super_admin_only(self):
        from backend.routes.admin.stripe_import import discover_stripe_driver_accounts

        with pytest.raises(HTTPException) as ei:
            await discover_stripe_driver_accounts(admin={"id": "a", "role": "admin"})
        assert ei.value.status_code == 403

    async def test_reports_and_audits(self):
        from backend.routes.admin import stripe_import as route_mod

        report = {
            "matches": [],
            "ambiguous": [],
            "matched": 2,
            "unmatched_drivers": 1,
            "unmatched_accounts": 0,
            "matches_without_phone": [],
            "csv": "stripe_account_id,phone\n",
        }
        audit = AsyncMock()
        with (
            patch.object(route_mod, "get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test_x"})),
            patch.object(route_mod.import_svc, "discover_driver_accounts_by_email", AsyncMock(return_value=report)),
            patch.object(route_mod, "log_admin_action", audit),
        ):
            resp = await route_mod.discover_stripe_driver_accounts(admin={"id": "a", "role": "super_admin"})
        assert resp["matched"] == 2
        # Non-null entity_id — audit_logs.entity_id is NOT NULL and the logger
        # swallows its own failures.
        assert audit.await_args.args[3] == "matched:2"


class TestUnlinkedDriverFetcher:
    """The fetcher itself was untested (every matcher test patches it), which
    is exactly how `select("...email...")` against a table with no email
    column reached production as a 42703. These pin the real query shape."""

    def _fake_supabase(self, drivers_rows, users_rows):
        selected: dict[str, str] = {}

        def _table(name):
            t = MagicMock()

            def _select(cols):
                selected[name] = cols
                q = MagicMock()
                res = MagicMock()
                res.data = drivers_rows if name == "drivers" else users_rows
                q.is_.return_value.execute.return_value = res
                q.in_.return_value.execute.return_value = res
                q.execute.return_value = res
                return q

            t.select.side_effect = _select
            return t

        sb = MagicMock()
        sb.table.side_effect = _table
        return sb, selected

    def test_never_selects_email_from_drivers(self):
        """drivers has NO email column — selecting it 42703s in production."""
        sb, selected = self._fake_supabase([], [])
        with patch.object(svc, "supabase", sb):
            svc._unlinked_drivers_with_emails()
        assert "email" not in selected["drivers"].split(",")

    def test_email_resolves_via_users_and_is_lowered(self):
        drivers = [{"id": "drv_1", "phone": "306", "user_id": "u1", "stripe_account_id": None}]
        users = [{"id": "u1", "email": "Driver@X.com"}]
        sb, _ = self._fake_supabase(drivers, users)
        with patch.object(svc, "supabase", sb), patch.object(svc, "_select_in", MagicMock(return_value=users)):
            out = svc._unlinked_drivers_with_emails()
        assert out[0]["email"] == "driver@x.com"

    def test_driver_with_no_user_row_gets_empty_email(self):
        drivers = [{"id": "drv_1", "phone": "306", "user_id": "u_gone", "stripe_account_id": None}]
        sb, _ = self._fake_supabase(drivers, [])
        with patch.object(svc, "supabase", sb), patch.object(svc, "_select_in", MagicMock(return_value=[])):
            out = svc._unlinked_drivers_with_emails()
        assert out[0]["email"] == ""


class _FakeStripeObject:
    """Mimics the deployed SDK's StripeObject: __getitem__ over _data, NO
    keys()/__iter__. dict() on this falls back to integer-index sequence
    iteration and raises KeyError: 0 — the exact production failure."""

    def __init__(self, data):
        self._data = data

    def __getitem__(self, k):
        return self._data[k]

    def to_dict_recursive(self):
        return dict(self._data)


class TestStripeObjectConversion:
    def test_dict_on_the_fake_reproduces_the_production_failure(self):
        """Sanity-check the fixture actually models the bug."""
        with pytest.raises(KeyError):
            dict(_FakeStripeObject({"id": "acct_1"}))

    def test_lister_survives_non_mapping_stripe_objects(self):
        fake_page = MagicMock()
        fake_page.auto_paging_iter.return_value = iter(
            [_FakeStripeObject({"id": "acct_1", "email": "A@X.com", "country": "CA",
                                "type": "express", "details_submitted": True,
                                "payouts_enabled": True, "created": 1})]
        )
        with patch("stripe.Account.list", MagicMock(return_value=fake_page)):
            out = svc._list_connected_accounts("sk_test_x")
        assert out == [{
            "id": "acct_1", "email": "a@x.com", "country": "CA", "type": "express",
            "details_submitted": True, "payouts_enabled": True, "created": 1,
        }]

    def test_converter_prefers_accessor_and_still_handles_dicts(self):
        from backend.utils.stripe_config import stripe_object_to_dict

        assert stripe_object_to_dict(_FakeStripeObject({"a": 1})) == {"a": 1}
        assert stripe_object_to_dict({"a": 1}) == {"a": 1}
