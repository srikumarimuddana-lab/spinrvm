"""
A1c Sub-tier C coverage: backend/routes/webhooks.py (75.40% -> target 85%+).

`test_webhooks_main.py` (the massive existing suite) exercises the
`/webhooks/stripe` route's `payment_intent.*` / `invoice.*` /
`customer.subscription.*` / `payout.*` branches extensively.
`test_orphan_refund.py` covers `_record_orphan_refund`.
`test_webhook_stripe_v15.py` covers `_event_to_plain_dict`.
`test_ses_webhook.py` / `test_twilio_inbound.py` cover the `/webhooks/ses`
and `/webhooks/twilio-inbound` routes end-to-end via signature-verified HTTP
calls.

None of the following module-private helper functions have a *direct* unit
test anywhere in the suite (confirmed via
`grep -rln "<helper_name>" backend/tests/*.py` returning nothing before this
file) — this file closes that gap by calling them directly:

- `_extract_invoice_payment_intent` — legacy top-level `payment_intent`
  (string and dict forms), the Basil `payments.data[].payment.payment_intent`
  shape, the `Invoice.retrieve(expand=["payments"])` fallback (success and
  exception), and the no-`invoice_id`/no-`stripe_secret` early return.
- `_invoice_period_end_iso` / `_invoice_period_start_iso` — present,
  missing, and malformed-timestamp branches.
- `_confirm_sns_subscription` — untrusted-URL refusal, success, non-2xx
  response, and request-exception branches.
- `_topic_arn_allowed` — unconfigured (allow+warn), mismatch (reject), match.
- `_suppress_address` — unnormalizable email early return, already-suppressed
  idempotent skip, insert success, `DuplicateRecordError` swallow,
  `DatabaseError` propagation.
- `_suppress_marketing_email` — unnormalizable target early return, user
  lookup exception swallowed (best-effort attribution), success path.
- `_handle_ses_notification` — malformed `Message` JSON, permanent vs.
  transient Bounce, Complaint, and Delivery/other pass-through.
- `_resolve_user_id_by_phone` — found, not found, and lookup-exception
  branches.
- `_handle_sms_keyword` — opt-out (consent + suppression) and opt-in
  (consent only, no suppression call) branches.

Patch target follows this module's own established convention (see
`test_ses_webhook.py`): bare `db_supabase.*` and `routes.webhooks.*` (not
`backend.routes.webhooks.*`), since `db_supabase` is imported into
`routes.webhooks`'s own namespace per CLAUDE.md's "patch target is the
module that defines the function under test" rule.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio

try:
    from utils.error_handling import DatabaseError, DuplicateRecordError
except ImportError:
    from backend.utils.error_handling import DatabaseError, DuplicateRecordError  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# _extract_invoice_payment_intent
# ---------------------------------------------------------------------------


class TestExtractInvoicePaymentIntent:
    def test_legacy_string_payment_intent(self):
        from routes.webhooks import _extract_invoice_payment_intent

        assert _extract_invoice_payment_intent({"payment_intent": "pi_123"}) == "pi_123"

    def test_legacy_dict_payment_intent(self):
        from routes.webhooks import _extract_invoice_payment_intent

        assert _extract_invoice_payment_intent({"payment_intent": {"id": "pi_456"}}) == "pi_456"

    def test_basil_payments_data_shape(self):
        from routes.webhooks import _extract_invoice_payment_intent

        invoice = {
            "payments": {
                "data": [
                    {"payment": {"payment_intent": "pi_basil_1"}},
                ]
            }
        }
        assert _extract_invoice_payment_intent(invoice) == "pi_basil_1"

    def test_no_invoice_id_or_secret_returns_none(self):
        from routes.webhooks import _extract_invoice_payment_intent

        assert _extract_invoice_payment_intent({}) is None
        assert _extract_invoice_payment_intent({"id": "in_1"}) is None  # no stripe_secret

    def test_retrieve_fallback_success(self):
        from routes.webhooks import _extract_invoice_payment_intent

        refreshed = MagicMock()
        refreshed.to_dict_recursive.return_value = {
            "payments": {"data": [{"payment": {"payment_intent": "pi_refetched"}}]}
        }
        with patch("stripe.Invoice.retrieve", MagicMock(return_value=refreshed)):
            result = _extract_invoice_payment_intent({"id": "in_1"}, stripe_secret="sk_test")
        assert result == "pi_refetched"

    def test_retrieve_fallback_exception_returns_none(self):
        from routes.webhooks import _extract_invoice_payment_intent

        with patch("stripe.Invoice.retrieve", MagicMock(side_effect=Exception("stripe down"))):
            result = _extract_invoice_payment_intent({"id": "in_1"}, stripe_secret="sk_test")
        assert result is None

    def test_empty_payment_intent_value_falls_through(self):
        from routes.webhooks import _extract_invoice_payment_intent

        # payment_intent="" is falsy -> treated as absent, no payments.data -> None
        assert _extract_invoice_payment_intent({"payment_intent": ""}) is None


# ---------------------------------------------------------------------------
# _invoice_period_end_iso / _invoice_period_start_iso
# ---------------------------------------------------------------------------


class TestInvoicePeriodHelpers:
    def test_period_end_present(self):
        from routes.webhooks import _invoice_period_end_iso

        invoice = {"lines": {"data": [{"period": {"end": 1700000000}}]}}
        result = _invoice_period_end_iso(invoice)
        assert result is not None
        assert result.startswith("2023")

    def test_period_end_missing_lines_returns_none(self):
        from routes.webhooks import _invoice_period_end_iso

        assert _invoice_period_end_iso({}) is None
        assert _invoice_period_end_iso({"lines": {"data": []}}) is None

    def test_period_end_malformed_returns_none(self):
        from routes.webhooks import _invoice_period_end_iso

        invoice = {"lines": {"data": [{"period": {"end": "not-a-timestamp"}}]}}
        assert _invoice_period_end_iso(invoice) is None

    def test_period_start_present(self):
        from routes.webhooks import _invoice_period_start_iso

        invoice = {"lines": {"data": [{"period": {"start": 1690000000}}]}}
        result = _invoice_period_start_iso(invoice)
        assert result is not None
        assert result.startswith("2023")

    def test_period_start_missing_returns_none(self):
        from routes.webhooks import _invoice_period_start_iso

        assert _invoice_period_start_iso({}) is None

    def test_period_start_malformed_returns_none(self):
        from routes.webhooks import _invoice_period_start_iso

        invoice = {"lines": {"data": [{"period": {"start": object()}}]}}
        assert _invoice_period_start_iso(invoice) is None


# ---------------------------------------------------------------------------
# _confirm_sns_subscription
# ---------------------------------------------------------------------------


class TestConfirmSnsSubscription:
    async def test_untrusted_url_refuses(self):
        from routes.webhooks import _confirm_sns_subscription

        with patch("utils.sns_verify.is_trusted_sns_url", MagicMock(return_value=False)):
            # Should return without raising and without attempting an HTTP call.
            await _confirm_sns_subscription({"SubscribeURL": "https://evil.example.com/x"})

    async def test_success_logs_confirmed(self):
        from routes.webhooks import _confirm_sns_subscription

        mock_resp = MagicMock(status_code=200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with (
            patch("utils.sns_verify.is_trusted_sns_url", MagicMock(return_value=True)),
            patch("httpx.AsyncClient", MagicMock(return_value=mock_client)),
        ):
            await _confirm_sns_subscription(
                {"SubscribeURL": "https://sns.ca-central-1.amazonaws.com/x", "TopicArn": "arn:1"}
            )
        mock_client.get.assert_awaited_once()

    async def test_non_2xx_response_not_confirmed(self):
        from routes.webhooks import _confirm_sns_subscription

        mock_resp = MagicMock(status_code=500)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with (
            patch("utils.sns_verify.is_trusted_sns_url", MagicMock(return_value=True)),
            patch("httpx.AsyncClient", MagicMock(return_value=mock_client)),
        ):
            # Should not raise even though the confirm failed.
            await _confirm_sns_subscription({"SubscribeURL": "https://sns.ca-central-1.amazonaws.com/x"})

    async def test_request_exception_swallowed(self):
        from routes.webhooks import _confirm_sns_subscription

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network down"))

        with (
            patch("utils.sns_verify.is_trusted_sns_url", MagicMock(return_value=True)),
            patch("httpx.AsyncClient", MagicMock(return_value=mock_client)),
        ):
            # Must not propagate.
            await _confirm_sns_subscription({"SubscribeURL": "https://sns.ca-central-1.amazonaws.com/x"})


# ---------------------------------------------------------------------------
# _topic_arn_allowed
# ---------------------------------------------------------------------------


class TestTopicArnAllowed:
    async def test_unconfigured_allows_with_warning(self):
        from routes.webhooks import _topic_arn_allowed

        with patch("routes.webhooks.get_app_settings", AsyncMock(return_value={})):
            assert await _topic_arn_allowed("arn:anything") is True

    async def test_mismatch_rejected(self):
        from routes.webhooks import _topic_arn_allowed

        with patch(
            "routes.webhooks.get_app_settings",
            AsyncMock(return_value={"aws_ses_sns_topic_arn": "arn:expected"}),
        ):
            assert await _topic_arn_allowed("arn:wrong") is False

    async def test_match_allowed(self):
        from routes.webhooks import _topic_arn_allowed

        with patch(
            "routes.webhooks.get_app_settings",
            AsyncMock(return_value={"aws_ses_sns_topic_arn": "arn:expected"}),
        ):
            assert await _topic_arn_allowed("arn:expected") is True


# ---------------------------------------------------------------------------
# _suppress_address
# ---------------------------------------------------------------------------


class TestSuppressAddress:
    async def test_unnormalizable_email_returns_early(self):
        from routes.webhooks import _suppress_address

        with patch("utils.email_provider.normalize_email", MagicMock(return_value=None)):
            # Should return without touching the DB.
            with patch("db_supabase.find_one", AsyncMock()) as mock_find:
                await _suppress_address("not-an-email", reason="bounce", detail="x", message_id="m1")
            mock_find.assert_not_called()

    async def test_already_suppressed_is_idempotent(self):
        from routes.webhooks import _suppress_address

        with (
            patch("utils.email_provider.normalize_email", MagicMock(return_value="a@b.com")),
            patch("db_supabase.find_one", AsyncMock(return_value={"email": "a@b.com"})),
            patch("db_supabase.insert_one", AsyncMock()) as mock_insert,
        ):
            await _suppress_address("A@B.com", reason="bounce", detail="x", message_id="m1")
        mock_insert.assert_not_called()

    async def test_insert_success(self):
        from routes.webhooks import _suppress_address

        with (
            patch("utils.email_provider.normalize_email", MagicMock(return_value="a@b.com")),
            patch("db_supabase.find_one", AsyncMock(return_value=None)),
            patch("db_supabase.insert_one", AsyncMock()) as mock_insert,
        ):
            await _suppress_address("a@b.com", reason="bounce", detail="General", message_id="m1")
        mock_insert.assert_awaited_once()

    async def test_duplicate_record_error_swallowed(self):
        from routes.webhooks import _suppress_address

        with (
            patch("utils.email_provider.normalize_email", MagicMock(return_value="a@b.com")),
            patch("db_supabase.find_one", AsyncMock(return_value=None)),
            patch("db_supabase.insert_one", AsyncMock(side_effect=DuplicateRecordError())),
        ):
            # Should not raise.
            await _suppress_address("a@b.com", reason="bounce", detail="General", message_id="m1")

    async def test_database_error_propagates(self):
        from routes.webhooks import _suppress_address

        with (
            patch("utils.email_provider.normalize_email", MagicMock(return_value="a@b.com")),
            patch("db_supabase.find_one", AsyncMock(return_value=None)),
            patch(
                "db_supabase.insert_one",
                AsyncMock(side_effect=DatabaseError(details={"original": "boom"})),
            ),
        ):
            with pytest.raises(DatabaseError):
                await _suppress_address("a@b.com", reason="bounce", detail="General", message_id="m1")


# ---------------------------------------------------------------------------
# _suppress_marketing_email
# ---------------------------------------------------------------------------


class TestSuppressMarketingEmail:
    async def test_unnormalizable_target_returns_early(self):
        from routes.webhooks import _suppress_marketing_email

        with (
            patch("services.marketing_consent.normalize_target", MagicMock(return_value=None)),
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock()) as mock_add,
        ):
            await _suppress_marketing_email("bad", reason="bounce", detail="x", message_id="m1")
        mock_add.assert_not_called()

    async def test_user_lookup_exception_does_not_block_suppression(self):
        from routes.webhooks import _suppress_marketing_email

        with (
            patch("services.marketing_consent.normalize_target", MagicMock(return_value="a@b.com")),
            patch("db_supabase.find_one", AsyncMock(side_effect=Exception("db hiccup"))),
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock()) as mock_add,
        ):
            await _suppress_marketing_email("a@b.com", reason="bounce", detail="x", message_id="m1")
        mock_add.assert_awaited_once()
        # Attribution failed -> user_id passed as None, suppression still happened.
        assert mock_add.call_args.kwargs.get("user_id") is None

    async def test_success_attributes_user(self):
        from routes.webhooks import _suppress_marketing_email

        with (
            patch("services.marketing_consent.normalize_target", MagicMock(return_value="a@b.com")),
            patch("db_supabase.find_one", AsyncMock(return_value={"id": "user-1"})),
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock()) as mock_add,
        ):
            await _suppress_marketing_email("a@b.com", reason="bounce", detail="x", message_id="m1")
        assert mock_add.call_args.kwargs.get("user_id") == "user-1"


# ---------------------------------------------------------------------------
# _handle_ses_notification
# ---------------------------------------------------------------------------


class TestHandleSesNotification:
    async def test_bad_message_json_ignored(self):
        from routes.webhooks import _handle_ses_notification

        result = await _handle_ses_notification({"Message": "not-json{{{"})
        assert result == {"received": True, "ignored": "bad_message"}

    async def test_permanent_bounce_suppresses_both_channels(self):
        import json as _json

        from routes.webhooks import _handle_ses_notification

        inner = {
            "notificationType": "Bounce",
            "bounce": {
                "bounceType": "Permanent",
                "bounceSubType": "General",
                "bouncedRecipients": [{"emailAddress": "a@b.com"}],
            },
            "mail": {"messageId": "m1"},
        }
        with (
            patch("routes.webhooks._suppress_address", AsyncMock()) as mock_suppress,
            patch("routes.webhooks._suppress_marketing_email", AsyncMock()) as mock_marketing,
        ):
            result = await _handle_ses_notification({"Message": _json.dumps(inner)})
        mock_suppress.assert_awaited_once()
        mock_marketing.assert_awaited_once()
        assert result["suppressed"] == 1
        assert result["marketing_suppressed"] == 1

    async def test_transient_bounce_only_suppresses_marketing(self):
        import json as _json

        from routes.webhooks import _handle_ses_notification

        inner = {
            "notificationType": "Bounce",
            "bounce": {
                "bounceType": "Transient",
                "bounceSubType": "General",
                "bouncedRecipients": [{"emailAddress": "a@b.com"}],
            },
            "mail": {"messageId": "m1"},
        }
        with (
            patch("routes.webhooks._suppress_address", AsyncMock()) as mock_suppress,
            patch("routes.webhooks._suppress_marketing_email", AsyncMock()) as mock_marketing,
        ):
            result = await _handle_ses_notification({"Message": _json.dumps(inner)})
        mock_suppress.assert_not_called()
        mock_marketing.assert_awaited_once()
        assert result["suppressed"] == 0
        assert result["marketing_suppressed"] == 1

    async def test_complaint_suppresses_both_channels(self):
        import json as _json

        from routes.webhooks import _handle_ses_notification

        inner = {
            "notificationType": "Complaint",
            "complaint": {
                "complaintFeedbackType": "abuse",
                "complainedRecipients": [{"emailAddress": "a@b.com"}],
            },
            "mail": {"messageId": "m1"},
        }
        with (
            patch("routes.webhooks._suppress_address", AsyncMock()) as mock_suppress,
            patch("routes.webhooks._suppress_marketing_email", AsyncMock()) as mock_marketing,
        ):
            result = await _handle_ses_notification({"Message": _json.dumps(inner)})
        mock_suppress.assert_awaited_once()
        mock_marketing.assert_awaited_once()
        assert result["suppressed"] == 1
        assert result["marketing_suppressed"] == 1

    async def test_delivery_notification_is_noop(self):
        import json as _json

        from routes.webhooks import _handle_ses_notification

        inner = {"notificationType": "Delivery", "mail": {"messageId": "m1"}}
        result = await _handle_ses_notification({"Message": _json.dumps(inner)})
        assert result["suppressed"] == 0
        assert result["marketing_suppressed"] == 0
        assert result["type"] == "Delivery"


# ---------------------------------------------------------------------------
# _resolve_user_id_by_phone
# ---------------------------------------------------------------------------


class TestResolveUserIdByPhone:
    async def test_found_returns_id(self):
        from routes.webhooks import _resolve_user_id_by_phone

        with (
            patch("services.marketing_consent.normalize_target", MagicMock(return_value="+15551234567")),
            patch("db_supabase.find_one", AsyncMock(return_value={"id": "user-9"})),
        ):
            result = await _resolve_user_id_by_phone("5551234567")
        assert result == "user-9"

    async def test_not_found_returns_none(self):
        from routes.webhooks import _resolve_user_id_by_phone

        with (
            patch("services.marketing_consent.normalize_target", MagicMock(return_value="+15551234567")),
            patch("db_supabase.find_one", AsyncMock(return_value=None)),
        ):
            result = await _resolve_user_id_by_phone("5551234567")
        assert result is None

    async def test_lookup_exception_never_raises(self):
        from routes.webhooks import _resolve_user_id_by_phone

        with (
            patch("services.marketing_consent.normalize_target", MagicMock(return_value="+15551234567")),
            patch("db_supabase.find_one", AsyncMock(side_effect=Exception("db down"))),
        ):
            result = await _resolve_user_id_by_phone("5551234567")
        assert result is None


# ---------------------------------------------------------------------------
# _handle_sms_keyword
# ---------------------------------------------------------------------------


class TestHandleSmsKeyword:
    async def test_opt_out_sets_consent_and_suppresses(self):
        from routes.webhooks import _handle_sms_keyword

        with (
            patch("routes.webhooks._resolve_user_id_by_phone", AsyncMock(return_value="user-1")),
            patch("services.marketing_consent.set_consent", AsyncMock()) as mock_set,
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock()) as mock_suppress,
        ):
            await _handle_sms_keyword("+15551234567", opted_in=False)
        mock_set.assert_awaited_once_with("user-1", "sms", False, source="sms_stop")
        mock_suppress.assert_awaited_once()

    async def test_opt_in_sets_consent_without_suppression(self):
        from routes.webhooks import _handle_sms_keyword

        with (
            patch("routes.webhooks._resolve_user_id_by_phone", AsyncMock(return_value="user-1")),
            patch("services.marketing_consent.set_consent", AsyncMock()) as mock_set,
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock()) as mock_suppress,
        ):
            await _handle_sms_keyword("+15551234567", opted_in=True)
        mock_set.assert_awaited_once_with("user-1", "sms", True, source="rider_app")
        mock_suppress.assert_not_called()

    async def test_unknown_user_still_suppresses_on_opt_out(self):
        from routes.webhooks import _handle_sms_keyword

        with (
            patch("routes.webhooks._resolve_user_id_by_phone", AsyncMock(return_value=None)),
            patch("services.marketing_consent.set_consent", AsyncMock()) as mock_set,
            patch("services.marketing_consent.add_marketing_suppression", AsyncMock()) as mock_suppress,
        ):
            await _handle_sms_keyword("+15551234567", opted_in=False)
        mock_set.assert_not_called()
        mock_suppress.assert_awaited_once()
