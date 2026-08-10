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

from unittest.mock import AsyncMock, patch

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

        async def _update_one(_table, _filt, updates):
            captured["update"] = updates

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
