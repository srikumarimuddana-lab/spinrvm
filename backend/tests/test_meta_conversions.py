"""Meta Conversions API — hashing, de-duplication gates, retry, isolation.

Four things must hold for this integration to be trustworthy, and each has a
section below:

1. Raw PII never reaches Meta, and normalization matches Meta's spec exactly.
   A hash of the wrong normalization is worse than no hash — it silently
   matches nothing while looking like it works.
2. FirstRide fires exactly once per user, ever.
3. Backend-only events reuse a persisted event_id on retry, so a retry
   de-duplicates instead of double-counting.
4. Nothing in this subsystem can raise into a signup, settlement, or ride
   completion.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils import meta_capi

pytestmark = pytest.mark.unit


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ── 1. Normalization + hashing ──────────────────────────────────────────────


class TestNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Rider@Example.COM", "rider@example.com"),
            ("  rider@example.com  ", "rider@example.com"),
            ("RIDER@EXAMPLE.COM", "rider@example.com"),
        ],
    )
    def test_email_lowercased_and_trimmed(self, raw, expected):
        assert meta_capi.normalize_email(raw) == expected

    def test_email_plus_addressing_and_dots_are_preserved(self):
        """Meta hashes the address the user typed on their side too.

        Stripping dots or plus-tags here would produce a digest that can never
        match the one Meta computed, which reduces match quality rather than
        improving it — the intuitive "canonicalise harder" instinct is wrong.
        """
        assert meta_capi.normalize_email("First.Last+spinr@gmail.com") == "first.last+spinr@gmail.com"

    @pytest.mark.parametrize("junk", [None, "", "   ", "not-an-email", "@example.com", "rider@"])
    def test_junk_email_yields_no_hash(self, junk):
        assert meta_capi.normalize_email(junk) is None
        assert meta_capi.hash_email(junk) is None

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+13065551234", "13065551234"),
            ("1 (306) 555-1234", "13065551234"),
            ("  +1-306-555-1234  ", "13065551234"),
        ],
    )
    def test_phone_reduced_to_e164_digits(self, raw, expected):
        assert meta_capi.normalize_phone(raw) == expected

    @pytest.mark.parametrize("junk", [None, "", "12345", "abc"])
    def test_too_short_phone_yields_no_hash(self, junk):
        assert meta_capi.normalize_phone(junk) is None
        assert meta_capi.hash_phone(junk) is None

    def test_hashes_are_sha256_of_the_normalized_value(self):
        assert meta_capi.hash_email("Rider@Example.com") == _sha("rider@example.com")
        assert meta_capi.hash_phone("+1 (306) 555-1234") == _sha("13065551234")

    def test_external_id_is_hashed_not_raw(self):
        """A Spinr user id must never be readable in Meta's systems."""
        user_id = "8f1e4f60-720e-46b0-9b71-33c13d3af043"
        hashed = meta_capi.hash_external_id(user_id)
        assert hashed == _sha(user_id)
        assert user_id not in hashed


class TestBuildUserData:
    def test_all_pii_is_hashed_and_no_raw_value_survives(self):
        data = meta_capi.build_user_data(
            email="Rider@Example.com",
            phone="+13065551234",
            user_id="user-123",
        )
        serialized = str(data)
        for raw in ("Rider@Example.com", "rider@example.com", "13065551234", "3065551234", "user-123"):
            assert raw not in serialized, f"raw PII {raw!r} leaked into user_data"
        assert data["em"] == [_sha("rider@example.com")]
        assert data["ph"] == [_sha("13065551234")]
        assert data["external_id"] == [_sha("user-123")]

    def test_missing_fields_are_omitted_not_blanked(self):
        """Meta reads a present-but-empty field as a failed match.

        An empty string drags Event Match Quality down; an absent key is
        neutral. So a user with no email on file must produce no `em` key.
        """
        data = meta_capi.build_user_data(email=None, phone=None, user_id="user-123")
        assert "em" not in data
        assert "ph" not in data
        assert data["external_id"] == [_sha("user-123")]

    def test_fbp_and_fbc_pass_through_unhashed(self):
        """These are opaque Meta-issued tokens — hashing makes them useless."""
        data = meta_capi.build_user_data(user_id="u", fbp="fb.1.123.456", fbc="fb.1.123.abc")
        assert data["fbp"] == "fb.1.123.456"
        assert data["fbc"] == "fb.1.123.abc"


# ── 2. Transport: retry, fail-fast, isolation ───────────────────────────────


def _response(status_code: int, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_success = 200 <= status_code < 300
    resp.text = text
    return resp


class _FakeClient:
    """Stands in for httpx.AsyncClient as an async context manager."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _no_backoff_sleep():
    """Collapse retry backoff so tests don't actually wait."""
    with patch.object(meta_capi.asyncio, "sleep", new=AsyncMock()):
        yield


class TestTransport:
    async def _send(self, responses, **overrides):
        client = _FakeClient(responses)
        with patch.object(meta_capi.httpx, "AsyncClient", return_value=client):
            kwargs = dict(
                dataset_id="ds-1",
                event_name="Purchase",
                event_id="evt-1",
                event_time=1_700_000_000,
                user_data={"external_id": ["x"]},
                access_token="token",
            )
            kwargs.update(overrides)
            result = await meta_capi.send_meta_event(**kwargs)
        return result, client

    @pytest.mark.anyio
    async def test_success_on_first_attempt(self):
        result, client = await self._send([_response(200)])
        assert result is True
        assert len(client.calls) == 1

    @pytest.mark.anyio
    async def test_retries_transient_5xx_then_succeeds(self):
        result, client = await self._send([_response(503), _response(200)])
        assert result is True
        assert len(client.calls) == 2

    @pytest.mark.anyio
    async def test_retries_429(self):
        result, client = await self._send([_response(429), _response(200)])
        assert result is True
        assert len(client.calls) == 2

    @pytest.mark.anyio
    async def test_does_not_retry_4xx(self):
        """A 400 is a payload or auth bug. Retrying burns quota and changes
        nothing, so it must fail fast rather than hammer Graph three times."""
        result, client = await self._send([_response(400, "Invalid parameter")])
        assert result is False
        assert len(client.calls) == 1

    @pytest.mark.anyio
    async def test_gives_up_after_max_attempts(self):
        result, client = await self._send([_response(500), _response(500), _response(500)])
        assert result is False
        assert len(client.calls) == meta_capi._MAX_ATTEMPTS

    @pytest.mark.anyio
    async def test_network_error_is_retried_and_never_raises(self):
        """Isolation: a transport exception returns False, it does not
        propagate into the signup/settlement path that called it."""
        result, client = await self._send([OSError("connection reset"), _response(200)])
        assert result is True
        assert len(client.calls) == 2

    @pytest.mark.anyio
    async def test_total_transport_failure_returns_false_without_raising(self):
        result, _ = await self._send([OSError("down"), OSError("down"), OSError("down")])
        assert result is False

    @pytest.mark.anyio
    async def test_unconfigured_token_is_a_silent_no_op(self):
        """The pre-credential state must be harmless, not an error path."""
        with patch.object(meta_capi.httpx, "AsyncClient") as client_cls:
            assert await meta_capi.send_meta_event(
                dataset_id="ds-1",
                event_name="Purchase",
                event_id="e",
                event_time=1,
                user_data={},
                access_token="",
            ) is False
            client_cls.assert_not_called()

    @pytest.mark.anyio
    async def test_test_event_code_is_included_only_when_set(self):
        _, client = await self._send([_response(200)], test_event_code="TEST123")
        assert client.calls[0][1]["json"]["test_event_code"] == "TEST123"

        _, client = await self._send([_response(200)])
        assert "test_event_code" not in client.calls[0][1]["json"]

    @pytest.mark.anyio
    async def test_access_token_is_sent_as_a_param_not_in_the_url(self):
        """Keeps the token out of any URL that might be logged."""
        _, client = await self._send([_response(200)])
        url, kwargs = client.calls[0]
        assert "token" not in url
        assert kwargs["params"]["access_token"] == "token"

    @pytest.mark.anyio
    async def test_event_id_and_name_are_sent_verbatim(self):
        """Meta de-duplicates on (event_name, event_id) — any mangling here
        breaks de-duplication silently."""
        _, client = await self._send([_response(200)])
        payload = client.calls[0][1]["json"]["data"][0]
        assert payload["event_id"] == "evt-1"
        assert payload["event_name"] == "Purchase"


# ── 3. The FirstRide once-per-user gate ─────────────────────────────────────


@pytest.fixture
def meta_service():
    from backend.services import meta_conversions_service

    return meta_conversions_service


def _config(token="token", rider="ds-rider", driver="ds-driver", test_code=""):
    return meta_capi.MetaCapiConfig(
        access_token=token,
        rider_dataset_id=rider,
        driver_dataset_id=driver,
        test_event_code=test_code,
    )


class TestFirstRideGate:
    @pytest.mark.anyio
    async def test_claim_succeeds_once_then_never_again(self, meta_service):
        """update_one returns a row on the first claim and None thereafter,
        because the `first_ride_completed_at IS NULL` filter stops matching."""
        update = AsyncMock(side_effect=[{"id": "u1"}, None, None])
        with patch.object(meta_service.db_supabase, "update_one", update):
            assert await meta_service._claim_first_ride("u1") is True
            assert await meta_service._claim_first_ride("u1") is False
            assert await meta_service._claim_first_ride("u1") is False

    @pytest.mark.anyio
    async def test_claim_filters_on_null_so_postgrest_emits_is_null(self, meta_service):
        update = AsyncMock(return_value={"id": "u1"})
        with patch.object(meta_service.db_supabase, "update_one", update):
            await meta_service._claim_first_ride("u1")
        _table, filters, _payload = update.call_args[0]
        assert filters == {"id": "u1", "first_ride_completed_at": None}

    @pytest.mark.anyio
    async def test_db_failure_does_not_fire(self, meta_service):
        """Missing a conversion is recoverable; double-counting is not."""
        with patch.object(meta_service.db_supabase, "update_one", AsyncMock(side_effect=RuntimeError("db down"))):
            assert await meta_service._claim_first_ride("u1") is False

    @pytest.mark.anyio
    async def test_missing_rider_id_does_not_fire(self, meta_service):
        assert await meta_service._claim_first_ride(None) is False


class TestPurchaseEvents:
    @pytest.mark.anyio
    async def test_first_paid_ride_sends_purchase_and_firstride(self, meta_service):
        sent = AsyncMock(return_value=True)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "_claim_first_ride", AsyncMock(return_value=True)),
            patch.object(meta_service, "send_events", sent),
        ):
            await meta_service.send_ride_purchase(
                {"id": "ride-1", "promo_code": "SPINR75", "rider_id": "u1"},
                {"id": "u1", "email": "r@example.com", "phone": "+13065551234"},
                "24.99",
            )
        events = sent.call_args.kwargs["events"]
        assert [e["event_name"] for e in events] == ["Purchase", "FirstRide"]
        # Distinct ids: they are two different conversions, not one event sent
        # twice, so they must not share a de-duplication key.
        assert events[0]["event_id"] != events[1]["event_id"]

    @pytest.mark.anyio
    async def test_subsequent_ride_sends_purchase_only(self, meta_service):
        sent = AsyncMock(return_value=True)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "_claim_first_ride", AsyncMock(return_value=False)),
            patch.object(meta_service, "send_events", sent),
        ):
            await meta_service.send_ride_purchase({"id": "ride-2"}, {"id": "u1"}, "31.50")
        assert [e["event_name"] for e in sent.call_args.kwargs["events"]] == ["Purchase"]

    @pytest.mark.anyio
    async def test_promo_code_present_even_when_unused(self, meta_service):
        """The `promo_code = SPINR75` custom conversion filters on this
        property, and a filter cannot match events that omit the key."""
        sent = AsyncMock(return_value=True)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "_claim_first_ride", AsyncMock(return_value=False)),
            patch.object(meta_service, "send_events", sent),
        ):
            await meta_service.send_ride_purchase({"id": "r"}, {"id": "u1"}, "10.00")
        custom = sent.call_args.kwargs["events"][0]["custom_data"]
        assert custom["promo_code"] == ""
        assert custom["currency"] == "CAD"
        assert custom["content_category"] == "ride"

    @pytest.mark.anyio
    async def test_value_is_the_settled_charge_as_a_2dp_number(self, meta_service):
        sent = AsyncMock(return_value=True)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "_claim_first_ride", AsyncMock(return_value=False)),
            patch.object(meta_service, "send_events", sent),
        ):
            await meta_service.send_ride_purchase({"id": "r"}, {"id": "u1"}, "24.99")
        assert sent.call_args.kwargs["events"][0]["custom_data"]["value"] == 24.99

    @pytest.mark.anyio
    async def test_zero_dollar_ride_still_fires(self, meta_service):
        """A 100%-promo ride is the most expensive acquisition there is —
        suppressing it would hide exactly what promo_code exists to measure."""
        sent = AsyncMock(return_value=True)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "_claim_first_ride", AsyncMock(return_value=True)),
            patch.object(meta_service, "send_events", sent),
        ):
            await meta_service.send_ride_purchase({"id": "r", "promo_code": "SPINR75"}, {"id": "u1"}, "0")
        assert sent.call_args.kwargs["events"][0]["custom_data"]["value"] == 0.0

    @pytest.mark.anyio
    async def test_disabled_tracking_does_not_spend_the_first_ride_claim(self, meta_service):
        """Regression guard. Claiming before checking config would burn the
        once-per-user gate against a send that never happened, permanently
        disqualifying every rider who converted before the token was pasted in.
        """
        claim = AsyncMock(return_value=True)
        sent = AsyncMock(return_value=False)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config(token=""))),
            patch.object(meta_service, "_claim_first_ride", claim),
            patch.object(meta_service, "send_events", sent),
        ):
            await meta_service.send_ride_purchase({"id": "r"}, {"id": "u1"}, "10.00")
        claim.assert_not_awaited()
        sent.assert_not_awaited()


# ── 4. Backend-only event idempotency ───────────────────────────────────────


class _Duplicate(Exception):
    """Stands in for repositories._base.DuplicateRecordError (matched by name)."""


class DuplicateRecordError(_Duplicate):
    pass


class TestBackendEventIdempotency:
    @pytest.mark.anyio
    async def test_first_claim_persists_and_returns_a_fresh_id(self, meta_service):
        insert = AsyncMock(return_value=None)
        with patch.object(meta_service.db_supabase, "insert_one", insert):
            event_id = await meta_service._claim_backend_event("DriverApproved:d1", "DriverApproved", "ds")
        assert event_id
        row = insert.call_args[0][1]
        assert row["dedup_key"] == "DriverApproved:d1"
        assert row["event_id"] == event_id
        assert row["status"] == "pending"

    @pytest.mark.anyio
    async def test_retry_reuses_the_stored_event_id(self, meta_service):
        """This is what makes a retry de-duplicate instead of double-count:
        Meta collapses (event_name, event_id), so the id must survive."""
        with (
            patch.object(meta_service.db_supabase, "insert_one", AsyncMock(side_effect=DuplicateRecordError())),
            patch.object(
                meta_service.db_supabase,
                "get_rows",
                AsyncMock(return_value=[{"event_id": "stored-id", "status": "failed"}]),
            ),
        ):
            assert await meta_service._claim_backend_event("DriverApproved:d1", "DriverApproved", "ds") == "stored-id"

    @pytest.mark.anyio
    async def test_already_sent_event_is_skipped(self, meta_service):
        with (
            patch.object(meta_service.db_supabase, "insert_one", AsyncMock(side_effect=DuplicateRecordError())),
            patch.object(
                meta_service.db_supabase,
                "get_rows",
                AsyncMock(return_value=[{"event_id": "stored-id", "status": "sent"}]),
            ),
        ):
            assert await meta_service._claim_backend_event("DriverApproved:d1", "DriverApproved", "ds") is None

    @pytest.mark.anyio
    async def test_conflict_with_unreadable_row_refuses_to_mint_a_second_id(self, meta_service):
        """Minting a fresh id here would double-count in Meta, which cannot be
        undone. A missed conversion can at least be reasoned about."""
        with (
            patch.object(meta_service.db_supabase, "insert_one", AsyncMock(side_effect=DuplicateRecordError())),
            patch.object(meta_service.db_supabase, "get_rows", AsyncMock(return_value=[])),
        ):
            assert await meta_service._claim_backend_event("DriverApproved:d1", "DriverApproved", "ds") is None

    @pytest.mark.anyio
    async def test_non_duplicate_db_error_skips_rather_than_raising(self, meta_service):
        with patch.object(meta_service.db_supabase, "insert_one", AsyncMock(side_effect=RuntimeError("db down"))):
            assert await meta_service._claim_backend_event("k", "DriverApproved", "ds") is None

    @pytest.mark.anyio
    async def test_driver_approved_is_sent_once_and_marked_sent(self, meta_service):
        send = AsyncMock(return_value=True)
        mark = AsyncMock()
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "_claim_backend_event", AsyncMock(return_value="evt-9")),
            patch.object(meta_service, "_mark_backend_event", mark),
            patch.object(meta_service, "send_meta_event", send),
        ):
            await meta_service.send_driver_approved({"id": "d1", "user_id": "u1"}, {"id": "u1", "phone": "+13065551234"})
        kwargs = send.call_args.kwargs
        assert kwargs["event_name"] == "DriverApproved"
        assert kwargs["event_id"] == "evt-9"
        assert kwargs["dataset_id"] == "ds-driver"
        # Backend workflow, not a user action in the app.
        assert kwargs["action_source"] == "system_generated"
        mark.assert_awaited_once()

    @pytest.mark.anyio
    async def test_driver_events_go_to_the_driver_dataset(self, meta_service):
        """Separate datasets are the primary guard against a driver
        application being reported as a rider acquisition."""
        send = AsyncMock(return_value=True)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "_claim_backend_event", AsyncMock(return_value="evt-1")),
            patch.object(meta_service, "_mark_backend_event", AsyncMock()),
            patch.object(meta_service, "send_meta_event", send),
        ):
            await meta_service.send_driver_activated(
                {"id": "d1", "user_id": "u1"}, {"id": "u1"}, {"id": "r1", "grand_total": "42.00"}
            )
        kwargs = send.call_args.kwargs
        assert kwargs["dataset_id"] == "ds-driver"
        assert kwargs["custom_data"]["value"] == 42.0
        assert kwargs["custom_data"]["currency"] == "CAD"

    @pytest.mark.anyio
    async def test_disabled_tracking_does_not_spend_the_driver_dedup_key(self, meta_service):
        claim = AsyncMock(return_value="evt")
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config(token=""))),
            patch.object(meta_service, "_claim_backend_event", claim),
        ):
            await meta_service.send_driver_approved({"id": "d1"}, {"id": "u1"})
        claim.assert_not_awaited()


# ── 5. Registration events + content_category separation ────────────────────


class TestRegistrationEvents:
    @pytest.mark.anyio
    async def test_rider_registration_uses_rider_dataset_and_category(self, meta_service):
        send = AsyncMock(return_value=True)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "send_meta_event", send),
        ):
            await meta_service.send_rider_registration(
                {"id": "u1", "phone": "+13065551234"}, event_id="shared-id", registration_method="phone"
            )
        kwargs = send.call_args.kwargs
        assert kwargs["dataset_id"] == "ds-rider"
        assert kwargs["event_name"] == "CompleteRegistration"
        assert kwargs["custom_data"]["content_category"] == "rider"
        assert kwargs["custom_data"]["status"] is True

    @pytest.mark.anyio
    async def test_driver_registration_uses_driver_dataset_and_category(self, meta_service):
        send = AsyncMock(return_value=True)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "send_meta_event", send),
        ):
            await meta_service.send_driver_registration({"id": "u2", "phone": "+13065551234"}, event_id="shared-id-2")
        kwargs = send.call_args.kwargs
        assert kwargs["dataset_id"] == "ds-driver"
        assert kwargs["custom_data"]["content_category"] == "driver_application"

    @pytest.mark.anyio
    async def test_registration_forwards_the_callers_event_id_verbatim(self, meta_service):
        """The client fires its SDK event with this same id. If the server
        substituted its own, Meta would count the signup twice."""
        send = AsyncMock(return_value=True)
        with (
            patch.object(meta_service, "get_config", AsyncMock(return_value=_config())),
            patch.object(meta_service, "send_meta_event", send),
        ):
            await meta_service.send_rider_registration({"id": "u1"}, event_id="the-shared-id")
        assert send.call_args.kwargs["event_id"] == "the-shared-id"
