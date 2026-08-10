"""Endpoint tests for the admin bulk tax-ID (SIN + GST BN) import.

DB access goes through the async db_supabase helpers (get_rows/update_one),
so those are patched directly — no fake PostgREST client needed. The Vault
encrypt, audit logger, and background Stripe push are stubbed.
"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.sin import validate_sin


@pytest.fixture
def super_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


@pytest.fixture
def regular_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_2", "role": "admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def _mk_valid_sin(prefix: str = "12345678") -> str:
    """Brute-force the Luhn check digit so no real SIN appears in the repo."""
    for d in "0123456789":
        try:
            return validate_sin(prefix + d)
        except ValueError:
            continue
    raise AssertionError("unreachable")


VALID_SIN = _mk_valid_sin()
PHONE = "+13065551234"


def _csv(*rows: str) -> bytes:
    return ("\n".join(["phone,sin,gst_bn", *rows]) + "\n").encode()


def _driver(**extra) -> dict:
    return {"id": "drv-1", "phone": PHONE, "sin": None, "gst_bn": None, "stripe_account_id": None, **extra}


async def _enc(d):
    return {**d, **({"sin": "vault-token"} if "sin" in d else {})}


def _patches(drivers, update_mock=None):
    return (
        patch("db_supabase.get_rows", AsyncMock(return_value=drivers)),
        patch("db_supabase.update_one", update_mock or AsyncMock()),
        patch("routes.admin.tax_id_import.log_admin_action", AsyncMock(return_value="audit-1")),
        patch("routes.admin.tax_id_import._encrypt_driver_pii", AsyncMock(side_effect=_enc)),
        patch("routes.admin.tax_id_import._push_sins_to_stripe", AsyncMock()),
    )


def _post(test_client, path, csv_bytes):
    return test_client.post(path, files={"tax_csv": ("tax.csv", csv_bytes, "text/csv")})


VALIDATE = "/api/admin/tax-ids/import/validate"
COMMIT = "/api/admin/tax-ids/import/commit"


class TestAccess:
    def test_regular_admin_forbidden(self, test_client, regular_admin_override):
        resp = _post(test_client, VALIDATE, _csv(f"{PHONE},{VALID_SIN},"))
        assert resp.status_code == 403


class TestValidate:
    def test_clean_csv_reports_and_writes_nothing(self, test_client, super_admin_override):
        upd = AsyncMock()
        ps = _patches([_driver()], update_mock=upd)
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, VALIDATE, _csv(f"{PHONE},{VALID_SIN},123456789RT0001"))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["can_commit"] is True
        assert body["counts"] == {"rows": 1, "to_write": 1, "sin_to_write": 1, "gst_to_write": 1, "skipped": 0}
        upd.assert_not_awaited()
        # The report must never echo the SIN.
        assert VALID_SIN not in resp.text

    def test_wrong_header_422(self, test_client, super_admin_override):
        resp = _post(test_client, VALIDATE, b"phone,sin\n%b,%b\n" % (PHONE.encode(), VALID_SIN.encode()))
        assert resp.status_code == 422

    def test_unknown_phone_is_an_error(self, test_client, super_admin_override):
        ps = _patches([])
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, VALIDATE, _csv(f"{PHONE},{VALID_SIN},"))
        body = resp.json()
        assert body["can_commit"] is False
        assert body["errors"][0]["field"] == "phone"
        # row_ref carries the phone's last-4 only, never the full number.
        assert PHONE not in resp.text
        assert "…1234" in body["errors"][0]["row_ref"]

    def test_invalid_sin_is_an_error_without_digits(self, test_client, super_admin_override):
        ps = _patches([_driver()])
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, VALIDATE, _csv(f"{PHONE},123456789,"))  # fails Luhn
        body = resp.json()
        assert body["can_commit"] is False
        assert body["errors"][0]["field"] == "sin"
        assert "123456789" not in str(body["errors"])

    def test_sin_already_on_file_is_skipped_with_warning(self, test_client, super_admin_override):
        """NULL-only fill = the immutability rule; corrections go through
        the audited update-sin endpoint, never a CSV re-run."""
        ps = _patches([_driver(sin="vault-existing")])
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, VALIDATE, _csv(f"{PHONE},{VALID_SIN},123456789RT0001"))
        body = resp.json()
        assert body["can_commit"] is True
        assert any(w["field"] == "sin" and "update-sin" in w["message"] for w in body["warnings"])
        assert body["counts"]["sin_to_write"] == 0
        assert body["counts"]["gst_to_write"] == 1  # GST still fills

    def test_duplicate_phone_is_an_error(self, test_client, super_admin_override):
        ps = _patches([_driver()])
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, VALIDATE, _csv(f"{PHONE},{VALID_SIN},", f"{PHONE},,123456789RT0001"))
        body = resp.json()
        assert any(e["message"] == "duplicate phone in CSV" for e in body["errors"])

    def test_empty_row_is_an_error(self, test_client, super_admin_override):
        ps = _patches([_driver()])
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, VALIDATE, _csv(f"{PHONE},,"))
        body = resp.json()
        assert body["can_commit"] is False
        assert "neither" in body["errors"][0]["message"]

    def test_bad_gst_bn_is_an_error(self, test_client, super_admin_override):
        ps = _patches([_driver()])
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, VALIDATE, _csv(f"{PHONE},,12345"))
        body = resp.json()
        assert body["can_commit"] is False
        assert body["errors"][0]["field"] == "gst_bn"


class TestCommit:
    def test_commit_encrypts_writes_audits_and_pushes(self, test_client, super_admin_override):
        upd = AsyncMock()
        push = AsyncMock()
        ps = _patches([_driver(stripe_account_id="acct_9")], update_mock=upd)
        with ps[0], ps[1], ps[2] as log, ps[3], patch("routes.admin.tax_id_import._push_sins_to_stripe", push):
            resp = _post(test_client, COMMIT, _csv(f"{PHONE},{VALID_SIN},123456789RT0001"))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["committed"] is True
        assert body["written_sin"] == 1
        assert body["written_gst"] == 1
        assert body["stripe_push"] == "started"
        assert VALID_SIN not in resp.text  # never in the response

        # One compare-and-set write per column, each filtered on that column
        # being NULL so a concurrent in-app write wins over the CSV.
        assert upd.await_count == 2
        (sin_call, gst_call) = upd.await_args_list
        assert sin_call.args[1] == {"id": "drv-1", "sin": None}
        written = sin_call.args[2]
        assert written["sin"] == "vault-token"  # the Vault token, not the number
        assert written["sin_last4"] == VALID_SIN[-4:]
        assert written["sin_collected_at"]
        assert gst_call.args[1] == {"id": "drv-1", "gst_bn": None}
        assert gst_call.args[2]["gst_bn"] == "123456789RT0001"
        assert gst_call.args[2]["gst_registered"] is True

        # Background push got the token + account, never the plaintext.
        pushed = push.call_args.args[0]
        assert pushed == [{"driver_id": "drv-1", "sin_token": "vault-token", "account_id": "acct_9"}]

        # Audit carries counts only.
        metadata = log.await_args.args[4]
        assert metadata == {"written_sin": 1, "written_gst": 1, "stripe_pushes": 1}
        assert VALID_SIN not in str(metadata)

    def test_commit_refuses_on_errors_and_writes_nothing(self, test_client, super_admin_override):
        upd = AsyncMock()
        ps = _patches([], update_mock=upd)  # no matching driver -> error
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, COMMIT, _csv(f"{PHONE},{VALID_SIN},"))
        assert resp.status_code == 200
        assert resp.json()["committed"] is False
        upd.assert_not_awaited()

    def test_no_stripe_account_means_no_push(self, test_client, super_admin_override):
        push = AsyncMock()
        ps = _patches([_driver()])
        with ps[0], ps[1], ps[2], ps[3], patch("routes.admin.tax_id_import._push_sins_to_stripe", push):
            resp = _post(test_client, COMMIT, _csv(f"{PHONE},{VALID_SIN},"))
        body = resp.json()
        assert body["committed"] is True
        assert body["stripe_push"] == "not_applicable"
        push.assert_not_called()

    def test_rerun_of_same_csv_converges(self, test_client, super_admin_override):
        """After a commit, the same CSV validates clean with skip warnings —
        the NULL-only fill makes the importer idempotent."""
        upd = AsyncMock()
        ps = _patches([_driver(sin="vault-token", gst_bn="123456789RT0001")], update_mock=upd)
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, COMMIT, _csv(f"{PHONE},{VALID_SIN},123456789RT0001"))
        body = resp.json()
        assert body["committed"] is True
        assert body["written_sin"] == 0
        assert body["written_gst"] == 0
        upd.assert_not_awaited()


class TestNoSinLeaks:
    def test_every_response_shape_is_digit_clean(self, test_client, super_admin_override):
        """Belt-and-braces: no 9-digit run (a candidate SIN) in any report,
        except the GST BN which is legitimately 9 digits and not secret."""
        ps = _patches([_driver()])
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = _post(test_client, VALIDATE, _csv(f"{PHONE},{VALID_SIN},"))
        text_without_gst = resp.text.replace("123456789RT0001", "")
        assert not re.search(re.escape(VALID_SIN), text_without_gst)


class TestCommitBackgroundPush:
    def test_push_goes_through_spawn_not_a_bare_create_task(self, test_client, super_admin_override):
        """asyncio.create_task keeps only a weak reference — an unreferenced
        push task can be garbage-collected mid-flight, silently dropping SIN
        pushes. spawn() (utils/background.py) retains a strong reference."""
        spawned = MagicMock()
        ps = _patches([_driver(stripe_account_id="acct_9")])
        with ps[0], ps[1], ps[2], ps[3], ps[4], patch("routes.admin.tax_id_import.spawn", spawned):
            resp = _post(test_client, COMMIT, _csv(f"{PHONE},{VALID_SIN},"))
        assert resp.json()["stripe_push"] == "started"
        spawned.assert_called_once()
        # The spawned coroutine never runs under the MagicMock; close it so
        # the test doesn't emit a "never awaited" warning.
        spawned.call_args.args[0].close()


class TestCommitRaceGuard:
    def test_driver_who_entered_sin_mid_commit_is_not_clobbered(self, test_client, super_admin_override):
        """The plan validates against a snapshot; if the driver self-enters
        their SIN in the app before this row's write, the compare-and-set
        matches 0 rows and the CSV value is dropped with a warning — the
        driver's own entry wins, and no Stripe push happens for the row."""
        upd = AsyncMock(return_value=None)  # 0 rows matched
        push = AsyncMock()
        ps = _patches([_driver(stripe_account_id="acct_9")], update_mock=upd)
        with ps[0], ps[1], ps[2] as log, ps[3], patch("routes.admin.tax_id_import._push_sins_to_stripe", push):
            resp = _post(test_client, COMMIT, _csv(f"{PHONE},{VALID_SIN},123456789RT0001"))
        body = resp.json()
        assert body["committed"] is True
        assert body["written_sin"] == 0
        assert body["written_gst"] == 0
        assert body["stripe_push"] == "not_applicable"
        push.assert_not_called()
        assert any("since validation" in w["message"] for w in body["warnings"])
        # Audit reflects what actually happened, not what the CSV wanted.
        assert log.await_args.args[4] == {"written_sin": 0, "written_gst": 0, "stripe_pushes": 0}
