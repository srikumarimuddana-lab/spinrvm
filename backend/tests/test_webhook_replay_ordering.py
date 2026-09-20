"""Adversarial Stripe webhook replay + ordering tests.

Existing coverage tests the idempotency *primitive* (`claim_stripe_event`
in `test_wallet_repo.py`) and tests the route's dedup branch by stubbing
`claim_stripe_event` to a constant `False` (`test_corporate_webhook.py::
test_corporate_topup_duplicate_event_is_noop`). Neither delivers the same
event twice through the HTTP route against a *stateful* claim table, and
nothing delivers events end-to-end in the wrong order — so the invariant
"a redelivery cannot re-run side effects" was asserted at one branch, never
exercised.

These tests drive the REAL `claim_stripe_event` / `unclaim_stripe_event` /
`mark_stripe_event_processed` against an in-memory `stripe_events` table
that enforces the same primary-key constraint migration 22 does, by
patching `repositories.wallet_repo.supabase` — the module that DEFINES
them, per CLAUDE.md's patch-target rule (patching `db_supabase` would not
work: it only re-exports).

The ordering scenario is the one `routes/webhooks.py`'s own comment block
(the N1 fix from the 2026-09-05 director review) describes as the real
money failure mode:

    1. PI on card 1 fails                       -> event A claimed
    2. a DB blip makes the handler unclaim A    -> A is redeliverable
    3. the rider retries; a later event settles the ride paid
    4. A is redelivered -> must NOT flip the paid ride back to "failed"

Step 4 flipping is what would prompt the rider to pay a second time and
mint a fresh PaymentIntent under a different idempotency key — a real
double charge.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


class _FakeStripeEventsTable:
    """In-memory `stripe_events` honouring event_id as PRIMARY KEY.

    Supports exactly the four chains the three repo functions issue:
      insert(row).execute()                               -> claim
      select("processed_at").eq(...).limit(1).execute()   -> claim's dup probe
      update({...}).eq(...).execute()                     -> mark processed
      delete().eq(...).is_("processed_at","null").execute() -> unclaim
    """

    def __init__(self, store: dict):
        self.store = store
        self._op = None
        self._payload = None
        self._event_id = None
        self._require_unprocessed = False

    # -- chain builders -------------------------------------------------
    def insert(self, row):
        self._op, self._payload = "insert", row
        return self

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def update(self, values):
        self._op, self._payload = "update", values
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        if col == "event_id":
            self._event_id = val
        return self

    def is_(self, col, val):
        if col == "processed_at" and val == "null":
            self._require_unprocessed = True
        return self

    def limit(self, _n):
        return self

    # -- terminal --------------------------------------------------------
    def execute(self):
        if self._op == "insert":
            eid = self._payload["event_id"]
            if eid in self.store:
                # Same string postgrest-py surfaces for a PK conflict; this is
                # what claim_stripe_event pattern-matches on to return False.
                raise Exception('duplicate key value violates unique constraint "stripe_events_pkey" (23505)')
            self.store[eid] = {**self._payload, "processed_at": None}
            return MagicMock(data=[self.store[eid]])

        if self._op == "select":
            row = self.store.get(self._event_id)
            return MagicMock(data=[{"processed_at": row["processed_at"]}] if row else [])

        if self._op == "update":
            row = self.store.get(self._event_id)
            if row:
                row.update(self._payload)
            return MagicMock(data=[row] if row else [])

        if self._op == "delete":
            row = self.store.get(self._event_id)
            if row and (not self._require_unprocessed or row["processed_at"] is None):
                del self.store[self._event_id]
            return MagicMock(data=[])

        raise AssertionError(f"unexpected op {self._op!r}")


def _stateful_supabase(store: dict):
    sb = MagicMock()
    sb.table.side_effect = lambda name: (
        _FakeStripeEventsTable(store)
        if name == "stripe_events"
        else pytest.fail(f"unexpected table {name!r} on the patched wallet_repo client")
    )
    return sb


def _topup_event(event_id="evt_dup_1"):
    return {
        "id": event_id,
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_dup_1",
                "amount_received": 50000,
                "currency": "cad",
                "metadata": {
                    "scope": "corporate_topup",
                    "company_id": "c1",
                    "wallet_id": "w1",
                    "initiated_by": "admin_1",
                },
            }
        },
    }


def _failed_event(event_id="evt_fail_A", ride_id="ride_1", pi_id="pi_card1"):
    return {
        "id": event_id,
        "type": "payment_intent.payment_failed",
        "data": {
            "object": {
                "id": pi_id,
                "metadata": {"ride_id": ride_id, "user_id": "u1"},
                "last_payment_error": {"message": "Your card was declined."},
            }
        },
    }


def _post(test_client, event):
    return test_client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": "t=1,v1=fake"},
    )


_SETTINGS = {"stripe_webhook_secret": "whsec_x", "stripe_secret_key": "sk_x"}


def test_duplicate_delivery_through_the_route_runs_side_effects_once(test_client):
    """Same event_id delivered twice against a real (stateful) claim table.

    Distinct from test_corporate_webhook.py's duplicate test, which stubs
    claim_stripe_event to a constant False — there, the first delivery never
    happens, so the claim table is never actually exercised.
    """
    store: dict = {}
    event = _topup_event()

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("repositories.wallet_repo.supabase", _stateful_supabase(store)),
        patch(
            "services.corporate_wallet_service.apply_topup",
            AsyncMock(return_value={"transaction_id": "t1", "balance_after": "500.00"}),
        ) as m_apply,
    ):
        first = _post(test_client, event)
        second = _post(test_client, event)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    assert first.json().get("duplicate") is not True
    assert second.json()["duplicate"] is True

    # The invariant that actually matters: the money moved exactly once.
    assert m_apply.await_count == 1, (
        f"apply_topup ran {m_apply.await_count} times for one event_id — "
        "a redelivery double-credited the corporate wallet"
    )
    assert list(store) == ["evt_dup_1"]
    assert store["evt_dup_1"]["processed_at"] is not None, "first delivery never marked processed"


def test_unclaimed_event_can_be_reclaimed_so_a_retry_reprocesses(test_client):
    """A handler that unclaims on a transient error must leave the event
    genuinely redeliverable — otherwise Stripe's retry is deduped and the
    failure is lost until a manual replay.

    Drives the real unclaim path: the ride lookup raises, so the handler
    unclaims and 5xxs; the redelivery must then be claimable again.
    """
    store: dict = {}
    event = _failed_event()

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("repositories.wallet_repo.supabase", _stateful_supabase(store)),
        patch("db_supabase.get_ride", AsyncMock(side_effect=RuntimeError("db blip"))),
    ):
        first = _post(test_client, event)

    assert first.status_code >= 500, f"transient ride-lookup failure should 5xx, got {first.status_code}"
    assert store == {}, (
        "event stayed claimed after a transient failure — Stripe's retry would be "
        "deduped and the payment failure lost permanently"
    )


def test_redelivered_failure_does_not_relabel_a_since_paid_ride(test_client):
    """The N1 ordering scenario, end-to-end.

    Event A (failure) is claimed, unclaimed by a DB blip, and redelivered
    *after* the ride has since been settled paid by a later attempt. The
    redelivery must be acked without flipping payment_status back to
    "failed" — that flip is what causes the rider to be charged twice.

    Unlike test_webhook_payment_failed_guard.py, the ride's paid state here
    is reached through a stateful store across two deliveries rather than
    being stubbed in for a single call.
    """
    store: dict = {}
    ride = {"id": "ride_1", "payment_status": "pending", "payment_intent_id": "pi_card1"}
    event_a = _failed_event()

    async def _get_ride(_rid):
        return dict(ride)

    async def _update_one(table, filters, update, **_kw):
        """Faithful-enough stand-in for the handler's compare-and-swap.

        The failure path writes via db_supabase.update_one("rides", <CAS
        predicate>, {"$set": ...}) — NOT update_ride. Patching the wrong
        one makes this whole test vacuous (the handler could never touch
        the fake ride, so "stays paid" would hold no matter what), which
        is exactly what an earlier draft of this test did.
        """
        assert table == "rides"
        for col, expected in filters.items():
            if ride.get(col) != expected:
                return None  # CAS predicate missed — no write, like Postgres
        ride.update(update.get("$set", update))
        return dict(ride)

    # 1 + 2: A is claimed, the ride lookup blips, the handler unclaims.
    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event_a),
        patch("repositories.wallet_repo.supabase", _stateful_supabase(store)),
        patch("db_supabase.get_ride", AsyncMock(side_effect=RuntimeError("db blip"))),
    ):
        _post(test_client, event_a)

    assert store == {}, "precondition: A must be unclaimed and redeliverable"

    # 3: the rider retries on another card and that attempt settles the ride.
    ride["payment_status"] = "paid"
    ride["payment_intent_id"] = "pi_card2"

    # 4: A is redelivered. It must NOT overwrite the settled ride.
    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event_a),
        patch("repositories.wallet_repo.supabase", _stateful_supabase(store)),
        patch("db_supabase.get_ride", AsyncMock(side_effect=_get_ride)),
        patch("db_supabase.update_one", AsyncMock(side_effect=_update_one)),
    ):
        redelivery = _post(test_client, event_a)

    assert redelivery.status_code == 200, (
        f"a superseded redelivery must be acked, not retried forever; got {redelivery.status_code}: {redelivery.text}"
    )
    assert ride["payment_status"] == "paid", (
        "redelivered failure flipped a settled ride back to "
        f"{ride['payment_status']!r} — this is the double-charge path the N1 "
        "guard in routes/webhooks.py exists to prevent"
    )


def test_redelivered_failure_on_the_same_pi_does_not_relabel_a_paid_ride(test_client):
    """Isolates the settled-status guard specifically.

    The test above sets a *different* PaymentIntent on the settled ride, so
    two independent guards both block the write (the settled-status check
    and the superseded-PI check) — disabling either one alone still passes,
    which makes it a defence-in-depth test rather than a guard-specific one.

    Here the redelivered failure carries the SAME PaymentIntent the ride is
    settled against, so the superseded-PI branch cannot fire and the
    settled-status check is the only thing standing between a redelivery
    and a paid ride being relabelled "failed".
    """
    store: dict = {}
    ride = {"id": "ride_2", "payment_status": "paid", "payment_intent_id": "pi_same"}
    event = _failed_event(event_id="evt_fail_same_pi", ride_id="ride_2", pi_id="pi_same")

    async def _get_ride(_rid):
        return dict(ride)

    async def _update_one(table, filters, update, **_kw):
        assert table == "rides"
        for col, expected in filters.items():
            if ride.get(col) != expected:
                return None
        ride.update(update.get("$set", update))
        return dict(ride)

    with (
        patch("routes.webhooks.get_app_settings", AsyncMock(return_value=_SETTINGS)),
        patch("stripe.Webhook.construct_event", return_value=event),
        patch("repositories.wallet_repo.supabase", _stateful_supabase(store)),
        patch("db_supabase.get_ride", AsyncMock(side_effect=_get_ride)),
        patch("db_supabase.update_one", AsyncMock(side_effect=_update_one)),
    ):
        resp = _post(test_client, event)

    assert resp.status_code == 200, resp.text
    assert ride["payment_status"] == "paid", (
        "a redelivered failure relabelled an already-paid ride as "
        f"{ride['payment_status']!r} on the same PaymentIntent — the rider "
        "would be prompted to pay again, minting a second charge"
    )
