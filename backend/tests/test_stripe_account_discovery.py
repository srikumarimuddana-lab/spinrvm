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
        # The CSV is exactly what the validated import consumes, and the phone
        # is echoed VERBATIM from the driver row — the import looks it up
        # against drivers.phone, so the stored spelling is the one that hits.
        assert report["csv"] == "stripe_account_id,phone\nacct_1,3065550001\n"

    async def test_csv_breaking_phone_is_dropped_not_emitted(self):
        """A comma in the cell would shift stripe_account_id onto the wrong
        driver. Better to surface the match as phoneless."""
        report = await _run([_driver(phone='306,555"0001')], [_account()])
        assert report["matched"] == 1
        assert report["matches_without_phone"] == ["drv_1"]
        assert report["csv"] == ""

    async def test_email_match_is_case_insensitive(self):
        """The matcher lowers both sides itself — it must not depend on the
        fetchers having pre-normalized."""
        report = await _run([_driver(email="Driver@X.com")], [_account(email="dRiVeR@x.CoM")])
        assert report["matched"] == 1

    async def test_no_fuzzy_matching(self):
        """a@x.com must not match a+stripe@x.com or a@y.com — payout
        destinations are never guessed."""
        report = await _run(
            [_driver(email="a@x.com")], [_account(email="a+stripe@x.com"), _account("acct_2", "a@y.com")]
        )
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
            [
                _FakeStripeObject(
                    {
                        "id": "acct_1",
                        "email": "A@X.com",
                        "country": "CA",
                        "type": "express",
                        "details_submitted": True,
                        "payouts_enabled": True,
                        "created": 1,
                    }
                )
            ]
        )
        with patch("stripe.Account.list", MagicMock(return_value=fake_page)):
            out = svc._list_connected_accounts("sk_test_x")
        assert out == [
            {
                "id": "acct_1",
                "email": "a@x.com",
                "country": "CA",
                "type": "express",
                "details_submitted": True,
                "payouts_enabled": True,
                "created": 1,
            }
        ]

    def test_converter_prefers_accessor_and_still_handles_dicts(self):
        from backend.utils.stripe_config import stripe_object_to_dict

        assert stripe_object_to_dict(_FakeStripeObject({"a": 1})) == {"a": 1}
        assert stripe_object_to_dict({"a": 1}) == {"a": 1}


class TestDiscoveryToImportRoundTrip:
    """Discovery's CSV must actually match on upload.

    Discovery reads the phone off the driver row; the import looks that phone
    back up against ``drivers.phone`` with an ``IN`` query. If the two sides
    disagree on spelling the query returns nothing and EVERY row of a CSV we
    generated ourselves fails ``no_match`` — with the driver sitting right
    there in the table. These run the real matcher against a fake ``_select_in``
    that filters exactly like ``IN`` does, so a spelling mismatch fails here.
    """

    @staticmethod
    def _select_in_over(rows):
        def _fake(_table, _cols, column, values):
            wanted = set(values)
            return [r for r in rows if r.get(column) in wanted]

        return MagicMock(side_effect=_fake)

    async def _round_trip(self, stored_phone):
        driver = _driver(phone=stored_phone)
        report = await _run([driver], [_account()])
        assert report["csv"], "discovery emitted no CSV row"

        rows = svc.parse_mapping_rows(report["csv"], svc.KIND_DRIVERS)
        plan = svc.StripeMappingPlan(kind=svc.KIND_DRIVERS, batch="b")
        db_row = {"id": "drv_1", "phone": stored_phone, "stripe_account_id": None, "legacy_import_metadata": None}
        with patch.object(svc, "_select_in", self._select_in_over([db_row])):
            svc._build_local_driver_plan(rows, plan)
        return plan

    async def test_e164_stored_phone_round_trips(self):
        plan = await self._round_trip("+13065550001")
        assert [e.message for e in plan.errors] == []
        assert plan.driver_updates[0]["stripe_account_id"] == "acct_1"

    async def test_unnormalized_stored_phone_round_trips(self):
        """drivers.phone is E.164 by convention, but a row written by a path
        that skipped normalization must not silently poison the whole file."""
        plan = await self._round_trip("3065550001")
        assert [e.message for e in plan.errors] == []
        assert plan.driver_updates[0]["stripe_account_id"] == "acct_1"

    async def test_operator_typed_national_phone_still_matches_e164_row(self):
        """Hand-written CSVs predate discovery — 10-digit in, E.164 stored."""
        rows = [{"stripe_account_id": "acct_1", "phone": "306-555-0001"}]
        plan = svc.StripeMappingPlan(kind=svc.KIND_DRIVERS, batch="b")
        db_row = {"id": "drv_1", "phone": "+13065550001", "stripe_account_id": None, "legacy_import_metadata": None}
        with patch.object(svc, "_select_in", self._select_in_over([db_row])):
            svc._build_local_driver_plan(rows, plan)
        assert [e.message for e in plan.errors] == []
        assert plan.driver_updates[0]["driver_id"] == "drv_1"

    async def test_genuinely_absent_driver_still_errors(self):
        """Widening the lookup must not turn no_match into a false positive."""
        rows = [{"stripe_account_id": "acct_1", "phone": "3065559999"}]
        plan = svc.StripeMappingPlan(kind=svc.KIND_DRIVERS, batch="b")
        db_row = {"id": "drv_1", "phone": "+13065550001", "stripe_account_id": None, "legacy_import_metadata": None}
        with patch.object(svc, "_select_in", self._select_in_over([db_row])):
            svc._build_local_driver_plan(rows, plan)
        assert plan.driver_updates == []
        assert [e.field for e in plan.errors] == ["no_match"]

    def test_lookup_keys_never_collapse_to_last_ten_digits(self):
        """A last-10 canonical form would let +447700900001 collide with
        +17700900001 and redirect a payout to the wrong driver."""
        assert "7700900001" not in svc._phone_lookup_keys("+447700900001")
