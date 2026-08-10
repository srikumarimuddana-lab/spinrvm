"""Driver SIN collection — validation, encrypt-on-write, never-read.

Spinr had no SIN of its own: the design assumed Stripe held one that could be
read back at T4A time, which is impossible (``individual.id_number`` is
write-only on Connect). These tests cover the replacement, and the properties
they pin are the ones that make holding a SIN defensible at all:

  * a number that reaches the column is a number CRA will accept,
  * it is never written as plaintext, on any path,
  * it is never returned, to the driver or to an admin,
  * no error message, anywhere, contains it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.routes.drivers import _shared
from backend.utils.sin import normalize_sin, sin_last4, validate_sin

pytestmark = [pytest.mark.unit]


# ── test data ────────────────────────────────────────────────────────────
# Built rather than hardcoded, so this repo contains no digit string that
# looks like somebody's real SIN. The check digit is computed with an
# INDEPENDENT Luhn implementation (table-driven, unlike the module's
# arithmetic one) so a bug in the code under test cannot mask itself.

_DOUBLED = {"0": 0, "1": 2, "2": 4, "3": 6, "4": 8, "5": 1, "6": 3, "7": 5, "8": 7, "9": 9}


def _luhn_total(digits: str) -> int:
    total = 0
    for position, char in enumerate(reversed(digits)):
        total += _DOUBLED[char] if position % 2 == 1 else int(char)
    return total


def _make_sin(prefix: str = "13579246") -> str:
    """8 chosen digits + the check digit that makes Luhn pass."""
    assert len(prefix) == 8 and prefix[0] != "0"
    for candidate in "0123456789":
        if _luhn_total(prefix + candidate) % 10 == 0:
            return prefix + candidate
    raise AssertionError("unreachable: some digit always closes Luhn")


VALID_SIN = _make_sin()


class TestValidator:
    def test_accepts_a_luhn_valid_nine_digit_number(self):
        assert validate_sin(VALID_SIN) == VALID_SIN

    def test_strips_formatting_drivers_actually_type(self):
        spaced = f"{VALID_SIN[:3]} {VALID_SIN[3:6]}-{VALID_SIN[6:]}"
        assert validate_sin(spaced) == VALID_SIN
        assert normalize_sin(spaced) == VALID_SIN

    @pytest.mark.parametrize("bad", ["", None, "   "])
    def test_empty_is_rejected(self, bad):
        with pytest.raises(ValueError, match="required"):
            validate_sin(bad)

    @pytest.mark.parametrize("bad", ["12345678", "1234567890"])
    def test_wrong_length_is_rejected(self, bad):
        with pytest.raises(ValueError, match="9 digits"):
            validate_sin(bad)

    def test_leading_zero_is_rejected(self):
        with pytest.raises(ValueError, match="start with 0"):
            validate_sin("0" + VALID_SIN[1:])

    def test_all_zeroes_is_rejected_even_though_luhn_passes(self):
        """000000000 sums to 0, which is 0 mod 10 — Luhn alone lets it
        through, so the repeated-digit guard is not redundant."""
        assert _luhn_total("000000000") % 10 == 0
        with pytest.raises(ValueError):
            validate_sin("000000000")

    def test_single_digit_typo_is_caught(self):
        """The whole reason for a checksum: one wrong digit is the likeliest
        mistake and is not discovered until CRA rejects the slip."""
        wrong = int(VALID_SIN[4]) + 1
        typo = VALID_SIN[:4] + str(wrong % 10) + VALID_SIN[5:]
        assert typo != VALID_SIN
        with pytest.raises(ValueError, match="checksum"):
            validate_sin(typo)

    def test_transposed_adjacent_digits_are_caught(self):
        swapped = VALID_SIN[:2] + VALID_SIN[3] + VALID_SIN[2] + VALID_SIN[4:]
        if swapped == VALID_SIN:
            pytest.skip("chosen digits happen to be equal; nothing transposed")
        with pytest.raises(ValueError):
            validate_sin(swapped)

    def test_leading_nine_is_accepted(self):
        """A 9 marks a temporary resident. They are lawful workers with valid
        SINs — rejecting them would lock them out of being paid."""
        temp = _make_sin("91234567")
        assert validate_sin(temp) == temp

    @pytest.mark.parametrize("bad", ["12345678", "1234567890", "000000000", "0" + VALID_SIN[1:], VALID_SIN[:8] + "X"])
    def test_no_error_message_ever_contains_the_digits(self, bad):
        """Validation errors are returned to the client and land in logs and
        Sentry. CLAUDE.md forbids government IDs in all three."""
        with pytest.raises(ValueError) as ei:
            validate_sin(bad)
        digits = normalize_sin(bad)
        if len(digits) >= 6:
            assert digits not in str(ei.value)

    def test_last4(self):
        assert sin_last4(VALID_SIN) == VALID_SIN[-4:]


class TestEncryptOnWriteNeverRead:
    """The SIN must be encrypted going in and must NOT come back out — it is
    in the write-only set, not the round-trip set."""

    def test_sin_is_in_the_write_only_set_not_the_round_trip_set(self):
        assert "sin" in _shared._VAULT_WRITE_ONLY_PII_FIELDS
        assert "sin" not in _shared._VAULT_PII_FIELDS

    @pytest.mark.anyio
    async def test_encrypt_covers_the_sin(self):
        with patch.object(_shared, "_vault_encrypt", AsyncMock(return_value="vault-token")) as enc:
            out = await _shared._encrypt_driver_pii({"sin": VALID_SIN, "gst_bn": "123456789RT0001"})
        assert out["sin"] == "vault-token"
        # gst_bn is not PII-at-rest and must pass through untouched.
        assert out["gst_bn"] == "123456789RT0001"
        enc.assert_awaited_once()

    @pytest.mark.anyio
    async def test_encrypt_still_covers_license_number(self):
        """Adding a second set must not drop the original one."""
        with patch.object(_shared, "_vault_encrypt", AsyncMock(return_value="tok")):
            out = await _shared._encrypt_driver_pii({"license_number": "S1234-5678"})
        assert out["license_number"] == "tok"

    @pytest.mark.anyio
    async def test_decrypt_leaves_the_sin_alone(self):
        """If `sin` were in the round-trip set, every profile poll would
        return it."""
        with patch.object(_shared, "_vault_decrypt", AsyncMock(return_value="PLAINTEXT")) as dec:
            out = await _shared._decrypt_driver_pii({"sin": "vault-token", "license_number": "lic-token"})
        assert out["sin"] == "vault-token"  # untouched
        assert out["license_number"] == "PLAINTEXT"  # still round-trips
        dec.assert_awaited_once()

    def test_sin_is_stripped_from_the_driver_self_response(self):
        assert "sin" in _shared._STRIP_FROM_SELF_RESPONSE


class TestUpdateProfileRoute:
    """PUT /drivers/me is the collection point, so it owns the guarantees:
    validate, encrypt, derive last4, and never echo the number back."""

    @staticmethod
    def _body(**kw):
        from backend.routes.drivers.profile import UpdateDriverProfileRequest

        return UpdateDriverProfileRequest(**kw)

    @staticmethod
    async def _call(body, existing_driver, captured):
        from backend.routes.drivers import profile as mod

        async def _get_rows(*_a, **_k):
            return [existing_driver] if existing_driver else []

        async def _update_one(_table, filt, updates):
            captured["filter"] = filt
            captured["update"] = updates
            # Real update_one returns the updated row; None means the filter
            # matched nothing (the compare-and-set losing a race).
            return updates

        async def _insert_one(_table, row):
            captured["insert"] = row

        async def _get_driver_by_id(_id):
            return {**(existing_driver or {}), **captured.get("update", {})}

        with (
            patch.object(mod.db_supabase, "get_rows", _get_rows),
            patch.object(mod.db_supabase, "update_one", _update_one),
            patch.object(mod.db_supabase, "insert_one", _insert_one),
            patch.object(mod.db_supabase, "get_driver_by_id", _get_driver_by_id),
            patch.object(_shared, "_vault_encrypt", AsyncMock(return_value="vault-token")),
        ):
            return await mod.update_my_driver(body, {"id": "user-1", "phone": "+13065550001"})

    @pytest.mark.anyio
    async def test_valid_sin_is_encrypted_and_last4_derived(self):
        captured: dict = {}
        driver = {"id": "drv-1", "user_id": "user-1", "status": "active"}
        await self._call(self._body(sin=VALID_SIN), driver, captured)
        written = captured["update"]
        assert written["sin"] == "vault-token"  # never the number
        assert written["sin_last4"] == VALID_SIN[-4:]
        assert written["sin_collected_at"]

    @pytest.mark.anyio
    async def test_response_never_contains_the_sin(self):
        captured: dict = {}
        driver = {"id": "drv-1", "user_id": "user-1", "status": "active"}
        resp = await self._call(self._body(sin=VALID_SIN), driver, captured)
        assert "sin" not in resp
        assert VALID_SIN not in str(resp)
        # last4 is deliberately present — it is how the app shows on-file state.
        assert resp["sin_last4"] == VALID_SIN[-4:]

    @pytest.mark.anyio
    async def test_invalid_sin_is_422_and_writes_nothing(self):
        from fastapi import HTTPException

        captured: dict = {}
        driver = {"id": "drv-1", "user_id": "user-1", "status": "active"}
        with pytest.raises(HTTPException) as ei:
            await self._call(self._body(sin="123456789"), driver, captured)
        assert ei.value.status_code == 422
        assert "update" not in captured  # nothing reached the DB
        assert "123456789" not in str(ei.value.detail)

    @pytest.mark.anyio
    async def test_supplying_a_sin_does_not_knock_a_verified_driver_offline(self):
        """It is a `safe_field` like gst_bn. If it were treated as a vehicle
        field, entering a SIN would flip an active driver to needs_review and
        force them offline mid-shift."""
        captured: dict = {}
        driver = {"id": "drv-1", "user_id": "user-1", "status": "active", "is_online": True}
        await self._call(self._body(sin=VALID_SIN), driver, captured)
        assert "status" not in captured["update"]
        assert "is_online" not in captured["update"]

    @pytest.mark.anyio
    async def test_first_ever_profile_write_encrypts_too(self):
        """The auto-create branch wrote `**updates` straight to the DB, so a
        driver whose FIRST write carried PII stored it as plaintext."""
        captured: dict = {}
        await self._call(self._body(sin=VALID_SIN, license_number="S1234"), None, captured)
        row = captured["insert"]
        assert row["sin"] == "vault-token"
        assert row["license_number"] == "vault-token"
        assert VALID_SIN not in str(row)

    @pytest.mark.anyio
    async def test_auto_create_response_does_not_echo_the_sin(self):
        captured: dict = {}
        resp = await self._call(self._body(sin=VALID_SIN), None, captured)
        assert VALID_SIN not in str(resp)
        assert "sin" not in resp


class TestT4AWiring:
    """T4A is the reason the SIN is collected. These pin where it may and may
    not appear: masked on everything the driver or an admin can pull, full
    only through the audited super_admin reveal."""

    def test_driver_slip_shows_the_sin_masked(self):
        from backend.utils.t4a_pdf import generate_t4a_pdf

        pdf = generate_t4a_pdf(
            {
                "year": 2026,
                "net_earnings": "1200.00",
                "total_trips": 40,
                "gst_registered": False,
                "sin_last4": "6789",
                "driver_name": "Test Driver",
            }
        )
        assert isinstance(pdf, (bytes, bytearray)) and len(pdf) > 500
        # The full number is not in the summary at all, so it cannot reach the
        # bytes; what matters is that generation succeeds with the new field.

    def test_slip_generates_when_no_sin_is_on_file(self):
        """A driver without a SIN must still get a slip — it tells them to add
        one. Raising here would take away the very document that explains the
        problem."""
        from backend.utils.t4a_pdf import generate_t4a_pdf

        pdf = generate_t4a_pdf({"year": 2026, "net_earnings": "1200.00", "total_trips": 40, "gst_registered": False})
        assert isinstance(pdf, (bytes, bytearray)) and len(pdf) > 500

    def test_pdf_label_is_latin1_safe(self):
        """The slip uses fpdf's core Helvetica, which is latin-1 only — a
        bullet character would fail to encode at render time."""
        import inspect

        from backend.utils import t4a_pdf

        src = inspect.getsource(t4a_pdf)
        sin_lines = [ln for ln in src.splitlines() if 'label_value("SIN"' in ln]
        assert sin_lines
        for line in sin_lines:
            line.encode("latin-1")  # raises if we ever put a bullet in there

    @pytest.mark.anyio
    async def test_summary_carries_last4_only(self):
        """get_t4a_summary is returned over the API — it must expose the
        masked value and the on-file flag, never `sin`."""
        from backend.routes.drivers import tax_exports as mod

        driver = {"id": "drv-1", "sin": "vault-uuid", "sin_last4": "6789", "gst_registered": False}

        async def _get_rows(table, *_a, **_k):
            return [driver] if table == "drivers" else []

        with (
            patch.object(mod.db_supabase, "get_rows", _get_rows),
            patch.object(mod, "_synced_earnings_for_year", AsyncMock(return_value=0), create=True),
        ):
            summary = await mod.get_t4a_summary(2026, {"id": "user-1", "first_name": "A", "last_name": "B"})
        assert summary["sin_last4"] == "6789"
        assert summary["sin_on_file"] is True
        assert "sin" not in summary or summary.get("sin") != "vault-uuid"


class TestStripePrefill:
    """Hand Stripe the SIN we hold so its form stops asking, and the driver is
    never asked twice for the most sensitive number they have."""

    @staticmethod
    def _mod():
        from backend.routes.drivers import payouts

        return payouts

    @pytest.mark.anyio
    async def test_no_sin_on_file_is_a_no_op(self):
        mod = self._mod()
        with patch.object(mod.stripe.Account, "retrieve", MagicMock()) as ret:
            out = await mod.prefill_sin_to_stripe({"id": "d1"}, "acct_1", "sk_test_x")
        assert out == "no_sin_on_file"
        ret.assert_not_called()  # no pointless Stripe round-trip

    @pytest.mark.anyio
    async def test_prefills_when_stripe_still_needs_it(self):
        mod = self._mod()
        with (
            patch.object(mod.stripe.Account, "retrieve", MagicMock(return_value={"individual": {}})),
            patch.object(mod.stripe.Account, "modify", MagicMock()) as modify,
            patch.object(mod, "_vault_decrypt", AsyncMock(return_value=VALID_SIN)),
        ):
            out = await mod.prefill_sin_to_stripe({"id": "d1", "sin": "vault-uuid"}, "acct_1", "sk_test_x")
        assert out == "prefilled"
        assert modify.call_args.kwargs["individual"] == {"id_number": VALID_SIN}

    @pytest.mark.anyio
    async def test_skips_when_stripe_already_has_one(self):
        """Stripe collected it in its own flow, or a previous run did this."""
        mod = self._mod()
        with (
            patch.object(
                mod.stripe.Account, "retrieve", MagicMock(return_value={"individual": {"id_number_provided": True}})
            ),
            patch.object(mod.stripe.Account, "modify", MagicMock()) as modify,
            patch.object(mod, "_vault_decrypt", AsyncMock()) as dec,
        ):
            out = await mod.prefill_sin_to_stripe({"id": "d1", "sin": "vault-uuid"}, "acct_1", "sk_test_x")
        assert out == "already_provided"
        modify.assert_not_called()
        dec.assert_not_awaited()  # not even decrypted when there is nothing to do

    @pytest.mark.anyio
    async def test_failed_decrypt_never_sends_a_uuid_to_stripe(self):
        """_vault_decrypt returns its input when it cannot decrypt. Unchecked,
        that would register a UUID as somebody's SIN with Stripe."""
        mod = self._mod()
        with (
            patch.object(mod.stripe.Account, "retrieve", MagicMock(return_value={"individual": {}})),
            patch.object(mod.stripe.Account, "modify", MagicMock()) as modify,
            patch.object(mod, "_vault_decrypt", AsyncMock(return_value="vault-uuid")),
        ):
            out = await mod.prefill_sin_to_stripe({"id": "d1", "sin": "vault-uuid"}, "acct_1", "sk_test_x")
        assert out == "decrypt_failed"
        modify.assert_not_called()

    @pytest.mark.anyio
    async def test_malformed_plaintext_is_not_sent(self):
        mod = self._mod()
        with (
            patch.object(mod.stripe.Account, "retrieve", MagicMock(return_value={"individual": {}})),
            patch.object(mod.stripe.Account, "modify", MagicMock()) as modify,
            patch.object(mod, "_vault_decrypt", AsyncMock(return_value="12345")),
        ):
            out = await mod.prefill_sin_to_stripe({"id": "d1", "sin": "vault-uuid"}, "acct_1", "sk_test_x")
        assert out == "decrypt_failed"
        modify.assert_not_called()

    @pytest.mark.anyio
    async def test_stripe_failure_never_blocks_onboarding(self):
        """A driver trying to set up payouts must not be stopped by a failed
        pre-fill. The worst case is Stripe asking them — where we were before."""
        mod = self._mod()
        with (
            patch.object(mod.stripe.Account, "retrieve", MagicMock(side_effect=RuntimeError("stripe down"))),
            patch.object(mod, "_vault_decrypt", AsyncMock(return_value=VALID_SIN)),
        ):
            out = await mod.prefill_sin_to_stripe({"id": "d1", "sin": "vault-uuid"}, "acct_1", "sk_test_x")
        assert out == "failed"  # returned, not raised

    @pytest.mark.anyio
    async def test_repaired_account_is_prefilled_too(self):
        """When a stranded account is retired and replaced mid-flow, the fresh
        account needs the SIN as much as the original did — otherwise the
        recovery path silently reintroduces the double prompt."""
        mod = self._mod()
        seen: list = []

        async def _before(acct):
            seen.append(acct)

        calls = {"n": 0}

        def _op(acct):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("No such account: acct_old")
            return "link"

        with (
            patch.object(mod, "_ensure_stripe_account", AsyncMock(return_value="acct_old")),
            patch.object(mod, "is_missing_on_key", MagicMock(return_value=True)),
            patch.object(mod._kyc, "retire_stripe_account", AsyncMock()),
            patch.object(mod, "_create_stripe_account", AsyncMock(return_value="acct_new")),
        ):
            acct, result = await mod.with_account_repair({"id": "d1"}, {"id": "u1"}, "sk_test_x", _op, before=_before)
        assert (acct, result) == ("acct_new", "link")
        assert seen == ["acct_old", "acct_new"]

    @pytest.mark.anyio
    async def test_before_hook_is_optional(self):
        """Every other caller of with_account_repair passes no hook."""
        mod = self._mod()
        with patch.object(mod, "_ensure_stripe_account", AsyncMock(return_value="acct_1")):
            acct, result = await mod.with_account_repair({"id": "d1"}, {"id": "u1"}, "sk_x", lambda a: "ok")
        assert (acct, result) == ("acct_1", "ok")


class TestSinOnboardingGate:
    """SIN before Stripe is enforced by the backend, not just ordered in the
    app checklist. Without the gate a driver could mint an onboarding link
    with no SIN on file, pushing the question back into Stripe's form and
    leaving Spinr with no copy for the T4A."""

    @staticmethod
    def _mod():
        from backend.routes.drivers import payouts

        return payouts

    def test_gate_blocks_without_any_sin(self):
        from fastapi import HTTPException

        mod = self._mod()
        with pytest.raises(HTTPException) as exc:
            mod._require_sin_for_onboarding({"id": "d1"})
        assert exc.value.status_code == 422
        assert "SIN" in exc.value.detail

    def test_gate_passes_with_vault_copy(self):
        self._mod()._require_sin_for_onboarding({"id": "d1", "sin": "vault-uuid"})

    def test_gate_passes_with_legacy_stripe_flag(self):
        """Drivers whose SIN already lives at Stripe (entered in Stripe's own
        form before in-app collection existed) must not be asked again."""
        self._mod()._require_sin_for_onboarding({"id": "d1", "stripe_id_number_provided": True})

    @pytest.mark.anyio
    async def test_onboard_endpoint_422_without_sin(self):
        from fastapi import HTTPException

        mod = self._mod()
        with (
            patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "d1", "user_id": "u1"}])),
            patch.object(mod.db_supabase, "get_user_by_id", AsyncMock(return_value={"id": "u1"})),
        ):
            with pytest.raises(HTTPException) as exc:
                await mod.onboard_stripe(current_user={"id": "u1"})
        assert exc.value.status_code == 422

    @pytest.mark.anyio
    async def test_account_session_endpoint_422_without_sin(self):
        from fastapi import HTTPException

        mod = self._mod()
        with (
            patch.object(mod.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "d1", "user_id": "u1"}])),
            patch.object(mod.db_supabase, "get_user_by_id", AsyncMock(return_value={"id": "u1"})),
        ):
            with pytest.raises(HTTPException) as exc:
                await mod.stripe_account_session(current_user={"id": "u1"})
        assert exc.value.status_code == 422

    def test_payout_gate_accepts_vault_copy(self):
        """_require_sin_for_payout used to insist on Stripe's flag alone; the
        Vault copy satisfies the T4A requirement on its own."""
        self._mod()._require_sin_for_payout({"id": "d1", "sin": "vault-uuid"})

    def test_payout_gate_still_blocks_without_any_sin(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            self._mod()._require_sin_for_payout({"id": "d1"})
        assert exc.value.status_code == 422

    def test_gate_errors_never_contain_digits(self):
        """PIPEDA guard in the same spirit as the validator tests: the 422
        bodies must never carry a 9-digit run that could be a SIN."""
        import re

        from fastapi import HTTPException

        mod = self._mod()
        for fn in (mod._require_sin_for_onboarding, mod._require_sin_for_payout):
            with pytest.raises(HTTPException) as exc:
                fn({"id": "d1"})
            assert not re.search(r"\d{9}", exc.value.detail)


class TestSinImmutability:
    """After first entry the SIN is locked. Self-serve overwrites are how a
    typo'd (or someone else's) number silently corrupts the T4A record —
    corrections go through an admin who verifies the CRA-issued document."""

    @pytest.mark.anyio
    async def test_second_write_is_403(self):
        from fastapi import HTTPException

        captured: dict = {}
        driver = {"id": "drv-1", "user_id": "user-1", "status": "active", "sin": "vault-token-old"}
        with pytest.raises(HTTPException) as exc:
            await TestUpdateProfileRoute._call(TestUpdateProfileRoute._body(sin=VALID_SIN), driver, captured)
        assert exc.value.status_code == 403
        assert "update" not in captured  # nothing written

    @pytest.mark.anyio
    async def test_first_write_still_allowed(self):
        captured: dict = {}
        driver = {"id": "drv-1", "user_id": "user-1", "status": "active"}
        await TestUpdateProfileRoute._call(TestUpdateProfileRoute._body(sin=VALID_SIN), driver, captured)
        assert captured["update"]["sin"] == "vault-token"

    @pytest.mark.anyio
    async def test_first_write_on_auto_created_row_allowed(self):
        """No drivers row yet (brand-new driver) — the auto-create branch must
        accept the first SIN, not trip over the immutability check."""
        captured: dict = {}
        await TestUpdateProfileRoute._call(TestUpdateProfileRoute._body(sin=VALID_SIN), None, captured)
        assert captured["insert"]["sin"] == "vault-token"

    @pytest.mark.anyio
    async def test_other_fields_still_editable_when_sin_locked(self):
        """The lock is on the SIN alone — a driver with a SIN on file can
        still update GST, language, etc."""
        captured: dict = {}
        driver = {"id": "drv-1", "user_id": "user-1", "status": "active", "sin": "vault-token-old"}
        await TestUpdateProfileRoute._call(TestUpdateProfileRoute._body(gst_bn="123456789RT0001"), driver, captured)
        assert captured["update"]["gst_bn"] == "123456789RT0001"

    @pytest.mark.anyio
    async def test_403_detail_never_contains_digits(self):
        import re

        from fastapi import HTTPException

        driver = {"id": "drv-1", "user_id": "user-1", "status": "active", "sin": "vault-token-old"}
        with pytest.raises(HTTPException) as exc:
            await TestUpdateProfileRoute._call(TestUpdateProfileRoute._body(sin=VALID_SIN), driver, {})
        assert not re.search(r"\d{4}", exc.value.detail)


class TestSinFirstWriteRace:
    """The in-memory 403 check reads the driver once; two concurrent first
    writes both pass it. The write itself must therefore be a compare-and-set
    (filter sin IS NULL) so the loser gets a 409 instead of silently
    overwriting the winner."""

    @pytest.mark.anyio
    async def test_sin_write_filters_on_sin_is_null(self):
        captured: dict = {}
        driver = {"id": "drv-1", "user_id": "user-1", "status": "active"}
        await TestUpdateProfileRoute._call(TestUpdateProfileRoute._body(sin=VALID_SIN), driver, captured)
        assert captured["filter"] == {"id": "drv-1", "sin": None}

    @pytest.mark.anyio
    async def test_non_sin_write_is_unfiltered(self):
        captured: dict = {}
        driver = {"id": "drv-1", "user_id": "user-1", "status": "active"}
        await TestUpdateProfileRoute._call(TestUpdateProfileRoute._body(gst_bn="123456789RT0001"), driver, captured)
        assert captured["filter"] == {"id": "drv-1"}

    @pytest.mark.anyio
    async def test_losing_the_race_is_409(self):
        from unittest.mock import patch as _patch

        from fastapi import HTTPException

        from backend.routes.drivers import profile as mod

        driver = {"id": "drv-1", "user_id": "user-1", "status": "active"}

        async def _get_rows(*_a, **_k):
            return [driver]

        with (
            _patch.object(mod.db_supabase, "get_rows", _get_rows),
            # 0 rows matched: another request set the SIN between our read
            # and this write.
            _patch.object(mod.db_supabase, "update_one", AsyncMock(return_value=None)),
            _patch.object(_shared, "_vault_encrypt", AsyncMock(return_value="vault-token")),
        ):
            with pytest.raises(HTTPException) as exc:
                await mod.update_my_driver(
                    TestUpdateProfileRoute._body(sin=VALID_SIN), {"id": "user-1", "phone": "+13065550001"}
                )
        assert exc.value.status_code == 409
        assert VALID_SIN not in exc.value.detail
