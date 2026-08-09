"""Admin Stripe key-mode drift diagnostic.

The audit is the only thing that tells an operator how widespread a test→live
cutover's damage is. Its contract:

  * super_admin only — it reports which drivers have an unreachable payout
    destination, and the probe spends Stripe quota;
  * the probe classifies, it does not repair. Re-provisioning stays on the
    paths where the affected user is present;
  * an ambiguous Stripe error is `inconclusive`, never `stranded` — a revoked
    key must not be reported as "every identity is gone".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from fastapi import HTTPException

from backend.routes.admin.stripe_mode_audit import (
    ProbeRequest,
    probe_stripe_identities,
    stripe_mode_audit,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

SUPER = {"id": "adm_1", "role": "super_admin", "email": "ops@spinr.ca"}
PLAIN = {"id": "adm_2", "role": "admin", "email": "staff@spinr.ca"}
LIVE_KEY = "sk_live_" + "abc123"


def _customer(cid: str, livemode: bool):
    obj = MagicMock()
    obj.id = cid
    obj.get = lambda k, default=None: {"livemode": livemode}.get(k, default)
    obj.livemode = livemode
    return obj


def _rows(*ids: str) -> list[dict]:
    return [{"id": f"row_{i}", "stripe_customer_id": cid} for i, cid in enumerate(ids)]


class TestAccessControl:
    async def test_audit_rejects_non_super_admin(self):
        with pytest.raises(HTTPException) as ei:
            await stripe_mode_audit(admin=PLAIN)
        assert ei.value.status_code == 403

    async def test_probe_rejects_non_super_admin(self):
        with pytest.raises(HTTPException) as ei:
            await probe_stripe_identities(ProbeRequest(kind="riders"), admin=PLAIN)
        assert ei.value.status_code == 403


class TestProbeRequestValidation:
    def test_limit_is_bounded(self):
        with pytest.raises(ValueError):
            ProbeRequest(kind="riders", limit=0)
        with pytest.raises(ValueError):
            ProbeRequest(kind="riders", limit=10_000)

    def test_kind_is_closed(self):
        with pytest.raises(ValueError):
            ProbeRequest(kind="everything")


class _ProbeHarness:
    def __init__(self, rows: list[dict], retrieve, secret: str = LIVE_KEY):
        self.updates: list = []

        async def _update_one(table, filters, update):
            self.updates.append((table, filters, update))

        async def _run_sync(fn):
            return fn()

        select_result = MagicMock()
        select_result.data = rows

        self._patches = [
            patch(
                "backend.routes.admin.stripe_mode_audit.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": secret}),
            ),
            patch("backend.routes.admin.stripe_mode_audit.run_sync", side_effect=_run_sync),
            patch(
                "backend.routes.admin.stripe_mode_audit.db_supabase.supabase",
                MagicMock(),
            ),
            patch(
                "backend.routes.admin.stripe_mode_audit.db_supabase.update_one",
                side_effect=_update_one,
            ),
            patch("backend.routes.admin.stripe_mode_audit.log_admin_action", AsyncMock()),
            patch("stripe.Customer.retrieve", retrieve),
        ]
        self._select_result = select_result

    def __enter__(self):
        for p in self._patches:
            p.start()
        # The chained PostgREST builder must terminate in our fixed row set.
        sb = __import__("backend.routes.admin.stripe_mode_audit", fromlist=["db_supabase"]).db_supabase.supabase
        sb.table.return_value.select.return_value.not_.is_.return_value.is_.return_value.limit.return_value.execute.return_value = (  # noqa: E501
            self._select_result
        )
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class TestProbe:
    async def test_resolvable_identity_is_stamped_from_its_livemode(self):
        retrieve = MagicMock(return_value=_customer("cus_ok", livemode=True))
        with _ProbeHarness(_rows("cus_ok"), retrieve) as h:
            result = await probe_stripe_identities(ProbeRequest(kind="riders"), admin=SUPER)
        assert (result["probed"], result["resolvable"], result["stranded"]) == (1, 1, 0)
        assert h.updates[0][2] == {"stripe_customer_id_mode": "live"}
        # Filtered on the id so a row re-provisioned mid-probe isn't mis-stamped.
        assert h.updates[0][1] == {"id": "row_0", "stripe_customer_id": "cus_ok"}

    async def test_stamp_false_classifies_without_writing(self):
        retrieve = MagicMock(return_value=_customer("cus_ok", livemode=True))
        with _ProbeHarness(_rows("cus_ok"), retrieve) as h:
            result = await probe_stripe_identities(ProbeRequest(kind="riders", stamp=False), admin=SUPER)
        assert result["resolvable"] == 1
        assert h.updates == []

    async def test_resource_missing_is_reported_stranded_not_repaired(self):
        retrieve = MagicMock(
            side_effect=stripe.error.InvalidRequestError(
                "No such customer: 'cus_gone'", param=None, code="resource_missing"
            )
        )
        with _ProbeHarness(_rows("cus_gone"), retrieve) as h:
            result = await probe_stripe_identities(ProbeRequest(kind="riders"), admin=SUPER)
        assert (result["stranded"], result["stranded_ids"]) == (1, ["cus_gone"])
        # Diagnostic only: repair belongs on the paths where the user is present.
        assert h.updates == []

    @pytest.mark.parametrize(
        "exc",
        [
            stripe.error.AuthenticationError("Invalid API Key provided"),
            stripe.error.APIConnectionError("connection dropped"),
            stripe.error.RateLimitError("slow down"),
        ],
    )
    async def test_ambiguous_error_is_inconclusive_not_stranded(self, exc):
        """A revoked key must not be reported as 'every identity is gone' —
        that reading is what would justify a destructive bulk repair."""
        with _ProbeHarness(_rows("cus_a"), MagicMock(side_effect=exc)) as h:
            result = await probe_stripe_identities(ProbeRequest(kind="riders"), admin=SUPER)
        assert (result["stranded"], result["inconclusive"]) == (0, 1)
        assert result["stranded_ids"] == []
        assert h.updates == []

    async def test_mixed_batch_is_partitioned(self):
        def _retrieve(oid, api_key=None):
            if oid == "cus_gone":
                # Stripe names the object in the message; is_missing_on_key
                # requires it to be the one we asked about.
                raise stripe.error.InvalidRequestError(
                    "No such customer: 'cus_gone'", param=None, code="resource_missing"
                )
            return _customer(oid, livemode=True)

        with _ProbeHarness(_rows("cus_ok", "cus_gone"), MagicMock(side_effect=_retrieve)):
            result = await probe_stripe_identities(ProbeRequest(kind="riders"), admin=SUPER)
        assert (result["probed"], result["resolvable"], result["stranded"]) == (2, 1, 1)

    async def test_empty_population_short_circuits(self):
        retrieve = MagicMock(side_effect=AssertionError("nothing to probe"))
        with _ProbeHarness([], retrieve):
            result = await probe_stripe_identities(ProbeRequest(kind="riders"), admin=SUPER)
        assert result["probed"] == 0

    async def test_unconfigured_stripe_is_503(self):
        with _ProbeHarness(_rows("cus_a"), MagicMock(), secret=""):
            with pytest.raises(HTTPException) as ei:
                await probe_stripe_identities(ProbeRequest(kind="riders"), admin=SUPER)
        assert ei.value.status_code == 503
