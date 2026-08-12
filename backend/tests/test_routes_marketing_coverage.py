"""Coverage for routes/marketing.py (A1c, Sub-tier B).

Public unauthenticated CASL unsubscribe endpoints (RFC 8058 one-click POST +
human-visible GET) plus the authenticated rider/driver marketing-preferences
endpoints. Previously 58.57% coverage with no dedicated test file.

Route handler functions are called directly (bypassing FastAPI's Depends /
rate-limiter decorator machinery) with a plain `current_user` dict or a
`Request`-shaped stand-in, matching the pattern used elsewhere in this repo
for handler-level unit tests (see test_lost_and_found_route_coverage.py).
The `@limiter.limit(...)` decorator wraps the handlers, but slowapi's
decorator is a no-op unless `request.state.view_rate_limit` machinery is
invoked through the real ASGI request cycle — calling the decorated function
directly with a lightweight object exposing `.state`/`.headers`/`.scope` as
needed works the same way the existing lost_and_found-style tests exercise
decorated handlers elsewhere in this repo.

Per CLAUDE.md's "What Spinr Is NOT" — Spinr is not a data-harvesting product
and marketing sends must respect consent. This route enforces the CASL
unsubscribe contract: opting a channel out flips `marketing_preferences` AND
adds the contact to `marketing_suppressions` so an in-flight broadcast can't
still reach it (`services/marketing_consent.py` handles eligibility checks
for the send path itself, which is out of scope for this route-level file).

Bug found, not fixed (test-only scope): `unsubscribe_page`'s success path
always returns HTTP 200, but CASL/RFC 8058 semantics aside, if
`_process_unsubscribe` raises anything other than `UnsubscribeTokenError`
(e.g. a DB error from `marketing_consent.set_consent`), it propagates
unhandled out of both `unsubscribe_one_click` and `unsubscribe_page` instead
of being caught and surfaced as a clean 5xx — FastAPI's default exception
handler will turn it into an unstructured 500, which is arguably fine per
CLAUDE.md's "surface loudly" rule, but it does mean a transient DB error on
this *public, unauthenticated* endpoint looks identical to a server crash
rather than a friendly "try again" page. Not fixed here (test-only scope).

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_MODULE = "backend.routes.marketing"


class _FakeRequest:
    """Minimal stand-in for fastapi.Request — enough for slowapi's decorator
    to read request.state / request.headers / request.client without a real
    ASGI scope."""

    def __init__(self):
        self.state = type("State", (), {})()
        self.headers = {}
        self.client = None
        self.scope = {"type": "http"}


def _patches(**overrides):
    defaults = {
        f"{_MODULE}.verify_unsubscribe_token": None,  # set per-test; no sane default
        f"{_MODULE}.db_supabase.find_one": AsyncMock(return_value=None),
        f"{_MODULE}.marketing_consent.set_consent": AsyncMock(),
        f"{_MODULE}.marketing_consent.add_marketing_suppression": AsyncMock(return_value=True),
    }
    defaults.update(overrides)
    # Drop any None placeholders callers didn't override — those tests must
    # supply verify_unsubscribe_token explicitly.
    return {k: v for k, v in defaults.items() if v is not None}


def _start(patch_map):
    started = [patch(target, value) for target, value in patch_map.items()]
    for p in started:
        p.start()
    return started


def _stop(started):
    for p in started:
        p.stop()


# ── _process_unsubscribe ────────────────────────────────────────────────


class TestProcessUnsubscribe:
    @pytest.mark.anyio
    async def test_email_channel_resolves_target_and_suppresses(self):
        from backend.routes.marketing import _process_unsubscribe

        verify = AsyncMock() if False else None  # verify_unsubscribe_token is sync
        find_one = AsyncMock(return_value={"id": "user-1", "email": "rider@example.com", "phone": "+13065550100"})
        set_consent = AsyncMock()
        add_suppression = AsyncMock(return_value=True)
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.verify_unsubscribe_token": lambda token: {"user_id": "user-1", "channel": "email"},
                    f"{_MODULE}.db_supabase.find_one": find_one,
                    f"{_MODULE}.marketing_consent.set_consent": set_consent,
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                }
            )
        )
        try:
            channel = await _process_unsubscribe("sometoken")
            assert channel == "email"
            set_consent.assert_awaited_once_with("user-1", "email", False, source="unsubscribe_link")
            add_suppression.assert_awaited_once_with(
                "email", "rider@example.com", reason="unsubscribe", source="unsubscribe_link", user_id="user-1"
            )
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_sms_channel_uses_phone_target(self):
        from backend.routes.marketing import _process_unsubscribe

        find_one = AsyncMock(return_value={"id": "user-1", "email": "rider@example.com", "phone": "+13065550100"})
        add_suppression = AsyncMock(return_value=True)
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.verify_unsubscribe_token": lambda token: {"user_id": "user-1", "channel": "sms"},
                    f"{_MODULE}.db_supabase.find_one": find_one,
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                }
            )
        )
        try:
            channel = await _process_unsubscribe("sometoken")
            assert channel == "sms"
            add_suppression.assert_awaited_once_with(
                "sms", "+13065550100", reason="unsubscribe", source="unsubscribe_link", user_id="user-1"
            )
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_user_not_found_still_sets_consent_but_skips_suppression(self):
        """Response is existence-agnostic: a nonexistent user_id still gets
        set_consent called (idempotent no-op write) but no suppression row
        since there's no target to suppress."""
        from backend.routes.marketing import _process_unsubscribe

        set_consent = AsyncMock()
        add_suppression = AsyncMock()
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.verify_unsubscribe_token": lambda token: {"user_id": "ghost", "channel": "email"},
                    f"{_MODULE}.db_supabase.find_one": AsyncMock(return_value=None),
                    f"{_MODULE}.marketing_consent.set_consent": set_consent,
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                }
            )
        )
        try:
            channel = await _process_unsubscribe("sometoken")
            assert channel == "email"
            set_consent.assert_awaited_once()
            add_suppression.assert_not_awaited()
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_user_lookup_db_error_is_swallowed_and_logged_not_fatal(self):
        """A DB error resolving the target must not block the opt-out itself
        (CLAUDE.md: don't silently swallow DB errors elsewhere, but here the
        lookup is explicitly "best effort" per the module docstring — the
        opt-out write must still happen; only the suppression add is skipped)."""
        from backend.routes.marketing import _process_unsubscribe

        set_consent = AsyncMock()
        add_suppression = AsyncMock()
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.verify_unsubscribe_token": lambda token: {"user_id": "user-1", "channel": "email"},
                    f"{_MODULE}.db_supabase.find_one": AsyncMock(side_effect=RuntimeError("db down")),
                    f"{_MODULE}.marketing_consent.set_consent": set_consent,
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                }
            )
        )
        try:
            channel = await _process_unsubscribe("sometoken")
            assert channel == "email"
            set_consent.assert_awaited_once()
            add_suppression.assert_not_awaited()
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_invalid_token_propagates(self):
        from backend.routes.marketing import _process_unsubscribe
        from backend.utils.unsubscribe_token import UnsubscribeTokenError

        def _raise(token):
            raise UnsubscribeTokenError("bad token")

        patches = _start(_patches(**{f"{_MODULE}.verify_unsubscribe_token": _raise}))
        try:
            with pytest.raises(UnsubscribeTokenError):
                await _process_unsubscribe("bad")
        finally:
            _stop(patches)


# ── unsubscribe_one_click (POST) ────────────────────────────────────────


class TestUnsubscribeOneClick:
    @pytest.mark.anyio
    async def test_valid_token_returns_200(self):
        from backend.routes.marketing import unsubscribe_one_click

        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.verify_unsubscribe_token": lambda token: {"user_id": "user-1", "channel": "email"},
                    f"{_MODULE}.db_supabase.find_one": AsyncMock(return_value=None),
                }
            )
        )
        try:
            resp = await unsubscribe_one_click(_FakeRequest(), token="goodtoken")
            assert resp.status_code == 200
            assert resp.body == b'{"status":"unsubscribed"}'
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_invalid_token_returns_400_json_error(self):
        from backend.routes.marketing import unsubscribe_one_click
        from backend.utils.unsubscribe_token import UnsubscribeTokenError

        def _raise(token):
            raise UnsubscribeTokenError("bad token")

        patches = _start(_patches(**{f"{_MODULE}.verify_unsubscribe_token": _raise}))
        try:
            resp = await unsubscribe_one_click(_FakeRequest(), token="badtoken")
            assert resp.status_code == 400
            assert b"invalid token" in resp.body
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_empty_token_default_treated_as_invalid(self):
        """token defaults to "" when the mail client posts without a query
        string — verify_unsubscribe_token rejects it (malformed), so the
        real function (not a stub) is exercised here for the boundary."""
        from backend.routes.marketing import unsubscribe_one_click

        patches = _start(_patches())  # real verify_unsubscribe_token stays live
        # But _patches() requires verify_unsubscribe_token override — call
        # the module's real one explicitly instead by not patching it.
        _stop(patches)
        resp = await unsubscribe_one_click(_FakeRequest(), token="")
        assert resp.status_code == 400


# ── unsubscribe_page (GET) ───────────────────────────────────────────────


class TestUnsubscribePage:
    @pytest.mark.anyio
    async def test_email_channel_confirmation_page_says_emails(self):
        from backend.routes.marketing import unsubscribe_page

        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.verify_unsubscribe_token": lambda token: {"user_id": "user-1", "channel": "email"},
                    f"{_MODULE}.db_supabase.find_one": AsyncMock(return_value=None),
                }
            )
        )
        try:
            resp = await unsubscribe_page(_FakeRequest(), token="goodtoken")
            assert resp.status_code == 200
            assert b"marketing emails" in resp.body
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_sms_channel_confirmation_page_says_messages(self):
        from backend.routes.marketing import unsubscribe_page

        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.verify_unsubscribe_token": lambda token: {"user_id": "user-1", "channel": "sms"},
                    f"{_MODULE}.db_supabase.find_one": AsyncMock(return_value=None),
                }
            )
        )
        try:
            resp = await unsubscribe_page(_FakeRequest(), token="goodtoken")
            assert resp.status_code == 200
            assert b"marketing messages" in resp.body
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_invalid_token_returns_400_error_page(self):
        from backend.routes.marketing import unsubscribe_page
        from backend.utils.unsubscribe_token import UnsubscribeTokenError

        def _raise(token):
            raise UnsubscribeTokenError("bad token")

        patches = _start(_patches(**{f"{_MODULE}.verify_unsubscribe_token": _raise}))
        try:
            resp = await unsubscribe_page(_FakeRequest(), token="badtoken")
            assert resp.status_code == 400
            assert b"invalid" in resp.body
        finally:
            _stop(patches)


# ── get_marketing_preferences ────────────────────────────────────────────


class TestGetMarketingPreferences:
    @pytest.mark.anyio
    async def test_returns_bool_coerced_prefs_with_version(self):
        from backend.routes.marketing import get_marketing_preferences

        get_prefs = AsyncMock(
            return_value={"email_opt_in": True, "sms_opt_in": None, "push_opt_in": False, "consent_version": "1"}
        )
        patches = _start(_patches(**{f"{_MODULE}.marketing_consent.get_preferences": get_prefs}))
        try:
            result = await get_marketing_preferences(current_user={"id": "user-1"})
            assert result == {
                "email_opt_in": True,
                "sms_opt_in": False,
                "push_opt_in": False,
                "consent_version": "1",
            }
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_default_all_false_for_missing_row(self):
        from backend.routes.marketing import get_marketing_preferences

        get_prefs = AsyncMock(
            return_value={"user_id": "user-1", "email_opt_in": False, "sms_opt_in": False, "push_opt_in": False}
        )
        patches = _start(_patches(**{f"{_MODULE}.marketing_consent.get_preferences": get_prefs}))
        try:
            result = await get_marketing_preferences(current_user={"id": "user-1"})
            assert result["email_opt_in"] is False
            assert result["consent_version"] is None
        finally:
            _stop(patches)


# ── update_marketing_preferences ─────────────────────────────────────────


class TestUpdateMarketingPreferences:
    @pytest.mark.anyio
    async def test_opt_in_email_sets_consent_no_suppression(self):
        from backend.routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences

        set_consent = AsyncMock()
        add_suppression = AsyncMock()
        get_prefs = AsyncMock(return_value={"email_opt_in": True, "sms_opt_in": False, "push_opt_in": False})
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.marketing_consent.set_consent": set_consent,
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                    f"{_MODULE}.marketing_consent.get_preferences": get_prefs,
                }
            )
        )
        try:
            body = MarketingPreferencesUpdate(email_opt_in=True)
            result = await update_marketing_preferences(
                body, current_user={"id": "user-1", "email": "rider@example.com", "phone": "+13065550100"}
            )
            set_consent.assert_awaited_once_with("user-1", "email", True, source="rider_app", consent_version="1")
            add_suppression.assert_not_awaited()
            assert result["email_opt_in"] is True
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_opt_out_email_sets_consent_and_adds_suppression(self):
        from backend.routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences

        set_consent = AsyncMock()
        add_suppression = AsyncMock()
        get_prefs = AsyncMock(return_value={"email_opt_in": False, "sms_opt_in": False, "push_opt_in": False})
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.marketing_consent.set_consent": set_consent,
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                    f"{_MODULE}.marketing_consent.get_preferences": get_prefs,
                }
            )
        )
        try:
            body = MarketingPreferencesUpdate(email_opt_in=False, source="driver_app")
            await update_marketing_preferences(
                body, current_user={"id": "user-1", "email": "rider@example.com", "phone": "+13065550100"}
            )
            set_consent.assert_awaited_once_with("user-1", "email", False, source="driver_app", consent_version=None)
            add_suppression.assert_awaited_once_with(
                "email", "rider@example.com", reason="unsubscribe", source="unsubscribe_link", user_id="user-1"
            )
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_opt_out_sms_uses_phone_target(self):
        from backend.routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences

        add_suppression = AsyncMock()
        get_prefs = AsyncMock(return_value={"email_opt_in": False, "sms_opt_in": False, "push_opt_in": False})
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                    f"{_MODULE}.marketing_consent.get_preferences": get_prefs,
                }
            )
        )
        try:
            body = MarketingPreferencesUpdate(sms_opt_in=False)
            await update_marketing_preferences(
                body, current_user={"id": "user-1", "email": "rider@example.com", "phone": "+13065550100"}
            )
            add_suppression.assert_awaited_once_with(
                "sms", "+13065550100", reason="unsubscribe", source="unsubscribe_link", user_id="user-1"
            )
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_opt_out_push_never_adds_suppression(self):
        """push has no external unsubscribe/suppression list — see
        marketing_consent.is_eligible, which gates push purely on
        push_opt_in. The route must not attempt a push suppression add."""
        from backend.routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences

        set_consent = AsyncMock()
        add_suppression = AsyncMock()
        get_prefs = AsyncMock(return_value={"email_opt_in": False, "sms_opt_in": False, "push_opt_in": False})
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.marketing_consent.set_consent": set_consent,
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                    f"{_MODULE}.marketing_consent.get_preferences": get_prefs,
                }
            )
        )
        try:
            body = MarketingPreferencesUpdate(push_opt_in=False)
            await update_marketing_preferences(body, current_user={"id": "user-1"})
            set_consent.assert_awaited_once_with("user-1", "push", False, source="rider_app", consent_version=None)
            add_suppression.assert_not_awaited()
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_missing_target_skips_suppression_even_on_opt_out(self):
        """Opt-out with no email on file for the user must not blow up or
        write a suppression row for an empty target."""
        from backend.routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences

        add_suppression = AsyncMock()
        get_prefs = AsyncMock(return_value={"email_opt_in": False, "sms_opt_in": False, "push_opt_in": False})
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                    f"{_MODULE}.marketing_consent.get_preferences": get_prefs,
                }
            )
        )
        try:
            body = MarketingPreferencesUpdate(email_opt_in=False)
            await update_marketing_preferences(body, current_user={"id": "user-1"})  # no "email" key
            add_suppression.assert_not_awaited()
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_none_values_are_left_untouched(self):
        """Partial update: fields not provided (None) must not trigger any
        set_consent call for that channel."""
        from backend.routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences

        set_consent = AsyncMock()
        get_prefs = AsyncMock(return_value={"email_opt_in": False, "sms_opt_in": False, "push_opt_in": False})
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.marketing_consent.set_consent": set_consent,
                    f"{_MODULE}.marketing_consent.get_preferences": get_prefs,
                }
            )
        )
        try:
            body = MarketingPreferencesUpdate()  # all None
            await update_marketing_preferences(body, current_user={"id": "user-1"})
            set_consent.assert_not_awaited()
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_multiple_channels_in_one_call_each_get_own_event(self):
        from backend.routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences

        set_consent = AsyncMock()
        add_suppression = AsyncMock()
        get_prefs = AsyncMock(return_value={"email_opt_in": True, "sms_opt_in": False, "push_opt_in": True})
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.marketing_consent.set_consent": set_consent,
                    f"{_MODULE}.marketing_consent.add_marketing_suppression": add_suppression,
                    f"{_MODULE}.marketing_consent.get_preferences": get_prefs,
                }
            )
        )
        try:
            body = MarketingPreferencesUpdate(email_opt_in=True, sms_opt_in=False, push_opt_in=True)
            await update_marketing_preferences(
                body, current_user={"id": "user-1", "email": "rider@example.com", "phone": "+13065550100"}
            )
            assert set_consent.await_count == 3
            # Only the sms opt-out should have triggered a suppression add.
            add_suppression.assert_awaited_once_with(
                "sms", "+13065550100", reason="unsubscribe", source="unsubscribe_link", user_id="user-1"
            )
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_default_source_is_rider_app(self):
        from backend.routes.marketing import MarketingPreferencesUpdate, update_marketing_preferences

        set_consent = AsyncMock()
        get_prefs = AsyncMock(return_value={"email_opt_in": True, "sms_opt_in": False, "push_opt_in": False})
        patches = _start(
            _patches(
                **{
                    f"{_MODULE}.marketing_consent.set_consent": set_consent,
                    f"{_MODULE}.marketing_consent.get_preferences": get_prefs,
                }
            )
        )
        try:
            body = MarketingPreferencesUpdate(email_opt_in=True)
            assert body.source == "rider_app"
            await update_marketing_preferences(body, current_user={"id": "user-1"})
            _, kwargs = set_consent.await_args
            assert kwargs["source"] == "rider_app"
        finally:
            _stop(patches)
