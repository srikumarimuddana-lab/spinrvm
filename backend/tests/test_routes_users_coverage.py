"""Coverage for routes/users.py (A1c, Sub-tier B).

Profile CRUD, PIPEDA data-export/account-deletion, phone update, profile
image upload, corporate account linking, emergency contacts, and the rider
referral program. Was at 39.86% coverage with no dedicated test file (a
narrow duplicate-email test exists in test_profile_duplicate_email.py and is
not repeated here beyond one guard-branch smoke test for context).

Route handler functions are called directly (bypassing FastAPI's Depends
machinery) with a plain `current_user` dict, matching the pattern already
used elsewhere in this repo (see test_lost_and_found_route_coverage.py).

PIPEDA note: this module is deletion/export-sensitive. Tests assert on
status codes / structured fields, not on any logged PII.

Bug found, not fixed (test-only scope): `delete_account` (DELETE
/users/profile) is the "hard delete" path per its docstring ("Permanently
delete the current user's account and all associated data") but it only
soft-deletes the `users` row (`update_one(..., {"deleted_at": now})`) and
never removes/anonymizes the row itself — behaviourally near-identical to
`delete_account_pipeda` (DELETE /users/account), which is explicitly the
soft-delete/tombstone endpoint. Whether both endpoints coexisting with
near-identical behavior but different docstrings/audit-action names
(`dsar_deletion_executed` vs `dsar_deletion_requested`) is intentional is
unclear from the code alone; flagging for a human to confirm which route is
actually wired into the rider app's "Delete my account" flow.

Test-only change — no application code modified.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.routes.users import (
    ApplyRiderReferralRequest,
    CreateProfileRequest,
    EmergencyContactCreate,
    LinkCorporateRequest,
    UpdatePhoneRequest,
    _compress_profile_image,
    _fmt_money,
    _fulfill_rider_data_export,
    _ride_phrase,
    _rider_referral_summary,
    add_emergency_contact,
    apply_rider_referral,
    create_profile,
    delete_account,
    delete_account_pipeda,
    delete_emergency_contact,
    get_emergency_contacts,
    get_profile,
    get_rider_referral_info,
    get_rider_referrals,
    link_corporate_account,
    request_data_export,
    store_profile_image,
    update_phone,
    upload_profile_image,
)
from backend.utils.error_handling import SpinrException

pytestmark = pytest.mark.unit

_USER_ROW = {
    "id": "user-1",
    "phone": "+13060000001",
    "first_name": "Sam",
    "last_name": "Rider",
    "email": "sam@example.com",
    "gender": "Male",
    "role": "rider",
    "created_at": "2026-06-15T00:00:00Z",
    "profile_complete": True,
}
_CURRENT_USER = {"id": "user-1", "email": "sam@example.com", "role": "rider", "token_version": 1}


def _patches(**overrides):
    defaults = {
        "backend.routes.users.db_supabase.get_user_by_id": AsyncMock(return_value=_USER_ROW),
        "backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[]),
        "backend.routes.users.db_supabase.update_one": AsyncMock(return_value={}),
        "backend.routes.users.db_supabase.insert_one": AsyncMock(return_value={}),
        "backend.routes.users.db_supabase.delete_many": AsyncMock(return_value=None),
        "backend.routes.users.db_supabase.delete_one": AsyncMock(return_value=None),
        "backend.routes.users.db_supabase.count_documents": AsyncMock(return_value=0),
        "backend.routes.users.log_admin_action": AsyncMock(),
        "backend.routes.users.revoke_all_for_user": AsyncMock(),
        "backend.routes.users.redis_delete": AsyncMock(),
        # Emergency-contact Vault encryption (migration 357): default to a
        # passthrough so tests unrelated to emergency contacts are
        # unaffected; TestVaultEmergencyContact* below overrides these to
        # assert on the actual RPC names/tokens.
        "backend.routes.users.vault_encrypt": AsyncMock(side_effect=lambda _rpc, v, _hint="": v),
        "backend.routes.users.vault_decrypt": AsyncMock(side_effect=lambda _rpc, v, _hint="": v),
    }
    defaults.update(overrides)
    return [patch(target, value) for target, value in defaults.items()]


def _start(patches):
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


# ── get_profile ─────────────────────────────────────────────────────────


class TestGetProfile:
    @pytest.mark.anyio
    async def test_returns_profile(self):
        patches = _start(_patches())
        try:
            result = await get_profile(current_user=_CURRENT_USER)
            assert result.id == "user-1"
            assert result.email == "sam@example.com"
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_missing_user_raises_404(self):
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_user_by_id": AsyncMock(return_value=None)}))
        try:
            with pytest.raises(HTTPException) as exc:
                await get_profile(current_user=_CURRENT_USER)
            assert exc.value.status_code == 404
        finally:
            _stop(patches)


# ── create_profile ──────────────────────────────────────────────────────


def _profile_req(email="fresh@example.com", gender="Male", role=None):
    return CreateProfileRequest(first_name="Sam", last_name="Rider", email=email, gender=gender, role=role)


class TestCreateProfile:
    @pytest.mark.anyio
    async def test_invalid_gender_raises_400(self):
        patches = _start(_patches())
        try:
            with pytest.raises(HTTPException) as exc:
                await create_profile(request=_profile_req(gender="Robot"), current_user=_CURRENT_USER)
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_duplicate_email_raises_structured_exception(self):
        other = {"id": "someone-else", "email": "fresh@example.com"}
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[other])}))
        try:
            with pytest.raises(SpinrException) as exc:
                await create_profile(request=_profile_req(), current_user=_CURRENT_USER)
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_email_change_resets_verification(self):
        captured = {}

        async def _update(table, filt, fields):
            captured.update(fields)

        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update),
                }
            )
        )
        try:
            await create_profile(
                request=_profile_req(email="new-email@example.com"),
                current_user={**_CURRENT_USER, "email": "sam@example.com"},
            )
            assert captured["email"] == "new-email@example.com"
            assert captured["email_verified"] is False
            assert captured["email_verified_at"] is None
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_same_email_does_not_reset_verification(self):
        captured = {}

        async def _update(table, filt, fields):
            captured.update(fields)

        patches = _start(_patches(**{"backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update)}))
        try:
            await create_profile(
                request=_profile_req(email="sam@example.com"),
                current_user={**_CURRENT_USER, "email": "sam@example.com"},
            )
            assert "email_verified" not in captured
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_driver_role_sets_role_and_is_driver_without_touching_is_rider(self):
        captured = {}

        async def _update(table, filt, fields):
            captured.update(fields)

        patches = _start(_patches(**{"backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update)}))
        try:
            await create_profile(request=_profile_req(role="driver"), current_user=_CURRENT_USER)
            assert captured["role"] == "driver"
            assert captured["is_driver"] is True
            assert "is_rider" not in captured
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_rider_role_sets_is_rider(self):
        captured = {}

        async def _update(table, filt, fields):
            captured.update(fields)

        patches = _start(_patches(**{"backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update)}))
        try:
            await create_profile(request=_profile_req(role="rider"), current_user=_CURRENT_USER)
            assert captured["is_rider"] is True
            assert "role" not in captured
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_missing_updated_user_raises_500(self):
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_user_by_id": AsyncMock(return_value=None)}))
        try:
            with pytest.raises(HTTPException) as exc:
                await create_profile(request=_profile_req(), current_user=_CURRENT_USER)
            assert exc.value.status_code == 500
        finally:
            _stop(patches)


# ── request_data_export ─────────────────────────────────────────────────


def _closing_spawn(captured):
    """Test double for utils.background.spawn: records the coroutine it was
    given (so a test can assert *whether* fulfillment was triggered) without
    ever actually running it — this is a unit test for request_data_export,
    not an integration test of the real export pipeline, which has its own
    coverage in test_dsar_export.py. Closes the coroutine to avoid a
    "coroutine was never awaited" warning.
    """

    def _spawn(coro):
        captured["coro"] = coro
        coro.close()
        return None

    return _spawn


class TestRequestDataExport:
    @pytest.mark.anyio
    async def test_success_spawns_fulfillment_when_email_on_file(self):
        captured = {}
        patches = _start(_patches(**{"backend.routes.users.spawn": _closing_spawn(captured)}))
        try:
            result = await request_data_export(current_user=_CURRENT_USER)
            assert result["success"] is True
            assert "request_id" in result
            assert "response_due_at" in result
            # N1: the request must actually be fulfilled, not just queued.
            assert "coro" in captured
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_no_email_on_file_skips_fulfillment_but_still_records_request(self):
        captured = {}
        no_email_user = {**_CURRENT_USER, "email": None}
        patches = _start(_patches(**{"backend.routes.users.spawn": _closing_spawn(captured)}))
        try:
            result = await request_data_export(current_user=no_email_user)
            # The DSAR request itself is never lost even without an email —
            # it stays queued for an admin to fulfil manually.
            assert result["success"] is True
            assert "coro" not in captured
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_insert_failure_surfaces_as_503_not_swallowed(self):
        patches = _start(
            _patches(**{"backend.routes.users.db_supabase.insert_one": AsyncMock(side_effect=RuntimeError("db down"))})
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await request_data_export(current_user=_CURRENT_USER)
            assert exc.value.status_code == 503
        finally:
            _stop(patches)


# ── _fulfill_rider_data_export ──────────────────────────────────────────


class TestFulfillRiderDataExport:
    @pytest.mark.anyio
    async def test_success_marks_request_completed(self):
        captured = {}

        async def _update(table, filt, fields):
            captured["table"] = table
            captured["filt"] = filt
            captured["fields"] = fields

        with (
            patch(
                "backend.routes.drivers.tax_exports._build_and_email_data_export",
                AsyncMock(return_value=True),
            ),
            patch("backend.routes.users.db_supabase.update_one", AsyncMock(side_effect=_update)),
        ):
            await _fulfill_rider_data_export("user-1", "sam@example.com", "req-1")

        assert captured["table"] == "data_export_requests"
        assert captured["filt"] == {"id": "req-1"}
        assert captured["fields"]["status"] == "completed"
        assert captured["fields"]["completed_at"] is not None

    @pytest.mark.anyio
    async def test_failure_leaves_request_pending_not_falsely_completed(self):
        captured = {}

        async def _update(table, filt, fields):
            captured["fields"] = fields

        with (
            patch(
                "backend.routes.drivers.tax_exports._build_and_email_data_export",
                AsyncMock(return_value=False),
            ),
            patch("backend.routes.users.db_supabase.update_one", AsyncMock(side_effect=_update)),
        ):
            await _fulfill_rider_data_export("user-1", "sam@example.com", "req-1")

        assert captured["fields"]["status"] == "pending"
        assert captured["fields"]["completed_at"] is None

    @pytest.mark.anyio
    async def test_status_update_failure_does_not_raise(self):
        # The export already succeeded or failed and was logged by
        # _build_and_email_data_export itself — a failure updating the queue
        # row's status must not propagate out of this background task.
        with (
            patch(
                "backend.routes.drivers.tax_exports._build_and_email_data_export",
                AsyncMock(return_value=True),
            ),
            patch(
                "backend.routes.users.db_supabase.update_one",
                AsyncMock(side_effect=RuntimeError("db down")),
            ),
        ):
            await _fulfill_rider_data_export("user-1", "sam@example.com", "req-1")


# ── delete_account_pipeda (soft-delete / tombstone) ─────────────────────


class TestDeleteAccountPipeda:
    @pytest.mark.anyio
    async def test_success_bumps_token_version_and_revokes_sessions(self):
        captured = {}

        async def _update(table, filt, fields):
            if table == "users":
                captured.update(fields)

        revoke = AsyncMock()
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update),
                    "backend.routes.users.revoke_all_for_user": revoke,
                }
            )
        )
        try:
            result = await delete_account_pipeda(current_user=_CURRENT_USER)
            assert result["success"] is True
            assert captured["status"] == "pending_deletion"
            assert captured["token_version"] == 2
            revoke.assert_awaited_once_with("user-1")
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_missing_token_version_defaults_to_zero_then_increments(self):
        captured = {}

        async def _update(table, filt, fields):
            if table == "users":
                captured.update(fields)

        patches = _start(_patches(**{"backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update)}))
        try:
            user_no_version = {"id": "user-1", "email": "sam@example.com"}
            await delete_account_pipeda(current_user=user_no_version)
            assert captured["token_version"] == 1
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_session_revocation_failure_is_nonfatal(self):
        """revoke_all_for_user raising must not fail the whole deletion request."""
        patches = _start(
            _patches(**{"backend.routes.users.revoke_all_for_user": AsyncMock(side_effect=RuntimeError("redis down"))})
        )
        try:
            result = await delete_account_pipeda(current_user=_CURRENT_USER)
            assert result["success"] is True
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_users_update_failure_surfaces_as_500(self):
        patches = _start(
            _patches(**{"backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=RuntimeError("db down"))})
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await delete_account_pipeda(current_user=_CURRENT_USER)
            assert exc.value.status_code == 500
        finally:
            _stop(patches)


# ── delete_account (DELETE /users/profile) ──────────────────────────────


class TestDeleteAccount:
    @pytest.mark.anyio
    async def test_success_soft_deletes_and_purges_ancillary_data(self):
        delete_many = AsyncMock()
        patches = _start(_patches(**{"backend.routes.users.db_supabase.delete_many": delete_many}))
        try:
            result = await delete_account(current_user=_CURRENT_USER)
            assert result == {"success": True, "message": "Account permanently deleted"}
            deleted_tables = {call.args[0] for call in delete_many.await_args_list}
            assert deleted_tables == {"driver_documents", "emergency_contacts", "saved_addresses"}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_failure_surfaces_as_500(self):
        patches = _start(
            _patches(**{"backend.routes.users.db_supabase.delete_many": AsyncMock(side_effect=RuntimeError("db down"))})
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await delete_account(current_user=_CURRENT_USER)
            assert exc.value.status_code == 500
        finally:
            _stop(patches)


# ── update_phone ────────────────────────────────────────────────────────


class TestUpdatePhone:
    @pytest.mark.anyio
    async def test_too_short_raises_400(self):
        patches = _start(_patches())
        try:
            with pytest.raises(HTTPException) as exc:
                await update_phone(UpdatePhoneRequest(phone="123"), current_user=_CURRENT_USER)
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_phone_in_use_raises_400(self):
        other = {"id": "someone-else", "phone": "+13060000009"}
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[other])}))
        try:
            with pytest.raises(HTTPException) as exc:
                await update_phone(UpdatePhoneRequest(phone="+13060000009"), current_user=_CURRENT_USER)
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_success(self):
        patches = _start(_patches())
        try:
            result = await update_phone(UpdatePhoneRequest(phone="+13060000009"), current_user=_CURRENT_USER)
            assert result.id == "user-1"
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_missing_updated_user_raises_500(self):
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_user_by_id": AsyncMock(return_value=None)}))
        try:
            with pytest.raises(HTTPException) as exc:
                await update_phone(UpdatePhoneRequest(phone="+13060000009"), current_user=_CURRENT_USER)
            assert exc.value.status_code == 500
        finally:
            _stop(patches)


# ── _compress_profile_image / store_profile_image ───────────────────────


class TestCompressProfileImage:
    def test_falls_back_to_original_bytes_on_bad_image(self):
        content, content_type = _compress_profile_image(b"not-a-real-image", "image/jpeg")
        assert content == b"not-a-real-image"
        assert content_type == "image/jpeg"


class TestStoreProfileImage:
    @pytest.mark.anyio
    async def test_no_storage_client_falls_back_to_base64(self):
        patches = _start(_patches())
        with patch("backend.routes.users.db_supabase.supabase", None):
            try:
                result = await store_profile_image("user-1", b"bytes", "image/jpeg")
                assert result.startswith("data:image/jpeg;base64,")
            finally:
                _stop(patches)

    @pytest.mark.anyio
    async def test_storage_upload_success_returns_public_url(self):
        fake_sb = AsyncMock()
        bucket = AsyncMock()
        bucket.upload = lambda **kw: None
        bucket.get_public_url = lambda path: "https://storage.example/profile-photos/" + path
        storage = AsyncMock()
        storage.from_ = lambda name: bucket
        fake_sb.storage = storage

        patches = _start(_patches())
        with patch("backend.routes.users.db_supabase.supabase", fake_sb):
            try:
                result = await store_profile_image("user-1", b"bytes", "image/png")
                assert result.startswith("https://storage.example/profile-photos/user-1/")
            finally:
                _stop(patches)

    @pytest.mark.anyio
    async def test_storage_upload_failure_falls_back_to_base64(self):
        fake_sb = AsyncMock()
        storage = AsyncMock()

        def _raise(name):
            raise RuntimeError("storage down")

        storage.from_ = _raise
        fake_sb.storage = storage

        patches = _start(_patches())
        with patch("backend.routes.users.db_supabase.supabase", fake_sb):
            try:
                result = await store_profile_image("user-1", b"bytes", "image/png")
                assert result.startswith("data:image/png;base64,")
            finally:
                _stop(patches)


# ── upload_profile_image ─────────────────────────────────────────────────


class _FakeUploadFile:
    def __init__(self, content: bytes, content_type: str):
        self.content_type = content_type
        self._content = content

    async def read(self):
        return self._content


class TestUploadProfileImage:
    @pytest.mark.anyio
    async def test_invalid_content_type_raises_400(self):
        patches = _start(_patches())
        try:
            with pytest.raises(HTTPException) as exc:
                await upload_profile_image(file=_FakeUploadFile(b"x", "application/pdf"), current_user=_CURRENT_USER)
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_oversized_file_raises_400(self):
        big = b"x" * (5 * 1024 * 1024 + 1)
        patches = _start(_patches())
        try:
            with pytest.raises(HTTPException) as exc:
                await upload_profile_image(file=_FakeUploadFile(big, "image/jpeg"), current_user=_CURRENT_USER)
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_rider_upload_is_auto_approved(self):
        captured = {}

        async def _update(table, filt, fields):
            captured.update(fields)

        patches = _start(_patches(**{"backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update)}))
        with patch("backend.routes.users.db_supabase.supabase", None):
            try:
                await upload_profile_image(
                    file=_FakeUploadFile(b"img-bytes", "image/jpeg"),
                    current_user={**_CURRENT_USER, "role": "rider"},
                )
                assert captured["profile_image_status"] == "approved"
            finally:
                _stop(patches)

    @pytest.mark.anyio
    async def test_driver_upload_is_pending_review(self):
        captured = {}

        async def _update(table, filt, fields):
            captured.update(fields)

        patches = _start(_patches(**{"backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update)}))
        with patch("backend.routes.users.db_supabase.supabase", None):
            try:
                await upload_profile_image(
                    file=_FakeUploadFile(b"img-bytes", "image/jpeg"),
                    current_user={**_CURRENT_USER, "role": "driver"},
                )
                assert captured["profile_image_status"] == "pending_review"
            finally:
                _stop(patches)

    @pytest.mark.anyio
    async def test_missing_updated_user_raises_500(self):
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_user_by_id": AsyncMock(return_value=None)}))
        with patch("backend.routes.users.db_supabase.supabase", None):
            try:
                with pytest.raises(HTTPException) as exc:
                    await upload_profile_image(file=_FakeUploadFile(b"img", "image/jpeg"), current_user=_CURRENT_USER)
                assert exc.value.status_code == 500
            finally:
                _stop(patches)


# ── link_corporate_account ───────────────────────────────────────────────


class TestLinkCorporateAccount:
    @pytest.mark.anyio
    async def test_unlink_with_none_id_skips_membership_check(self):
        captured = {}

        async def _update(table, filt, fields):
            captured.update(fields)

        patches = _start(_patches(**{"backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update)}))
        try:
            result = await link_corporate_account(
                LinkCorporateRequest(corporate_account_id=None), current_user=_CURRENT_USER
            )
            assert captured["corporate_account_id"] is None
            assert result.id == "user-1"
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_unknown_account_raises_404(self):
        patches = _start(_patches())
        try:
            with pytest.raises(HTTPException) as exc:
                await link_corporate_account(
                    LinkCorporateRequest(corporate_account_id="acct-1"), current_user=_CURRENT_USER
                )
            assert exc.value.status_code == 404
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_non_member_raises_403(self):
        account = {"id": "acct-1"}
        # First get_rows call → account lookup (found); second → membership lookup (empty).
        get_rows = AsyncMock(side_effect=[[account], []])
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_rows": get_rows}))
        try:
            with pytest.raises(HTTPException) as exc:
                await link_corporate_account(
                    LinkCorporateRequest(corporate_account_id="acct-1"), current_user=_CURRENT_USER
                )
            assert exc.value.status_code == 403
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_active_member_links_successfully(self):
        account = {"id": "acct-1"}
        membership = {"id": "mem-1", "status": "active"}
        get_rows = AsyncMock(side_effect=[[account], [membership]])
        captured = {}

        async def _update(table, filt, fields):
            captured.update(fields)

        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": get_rows,
                    "backend.routes.users.db_supabase.update_one": AsyncMock(side_effect=_update),
                }
            )
        )
        try:
            result = await link_corporate_account(
                LinkCorporateRequest(corporate_account_id="acct-1"), current_user=_CURRENT_USER
            )
            assert captured["corporate_account_id"] == "acct-1"
            assert result.id == "user-1"
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_missing_updated_user_raises_500(self):
        account = {"id": "acct-1"}
        membership = {"id": "mem-1"}
        get_rows = AsyncMock(side_effect=[[account], [membership]])
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": get_rows,
                    "backend.routes.users.db_supabase.get_user_by_id": AsyncMock(return_value=None),
                }
            )
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await link_corporate_account(
                    LinkCorporateRequest(corporate_account_id="acct-1"), current_user=_CURRENT_USER
                )
            assert exc.value.status_code == 500
        finally:
            _stop(patches)


# ── emergency contacts ────────────────────────────────────────────────────


class TestGetEmergencyContacts:
    @pytest.mark.anyio
    async def test_success(self):
        contacts = [{"id": "c1", "user_id": "user-1", "name": "Mom", "phone": "+13060000000", "relationship": "Family"}]
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=contacts)}))
        try:
            result = await get_emergency_contacts(current_user=_CURRENT_USER)
            assert result == {"contacts": contacts}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_db_error_surfaces_as_503_not_swallowed(self):
        patches = _start(
            _patches(**{"backend.routes.users.db_supabase.get_rows": AsyncMock(side_effect=RuntimeError("db down"))})
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await get_emergency_contacts(current_user=_CURRENT_USER)
            assert exc.value.status_code == 503
        finally:
            _stop(patches)


class TestAddEmergencyContact:
    @pytest.mark.anyio
    async def test_success(self):
        insert_one = AsyncMock()
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[]),
                    "backend.routes.users.db_supabase.insert_one": insert_one,
                }
            )
        )
        try:
            result = await add_emergency_contact(
                EmergencyContactCreate(name="Mom", phone="+13060000000", relationship="Family"),
                current_user=_CURRENT_USER,
            )
            assert result["success"] is True
            assert result["contact"]["name"] == "Mom"
            insert_one.assert_awaited_once()
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_count_check_db_error_surfaces_as_503(self):
        patches = _start(
            _patches(**{"backend.routes.users.db_supabase.get_rows": AsyncMock(side_effect=RuntimeError("db down"))})
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await add_emergency_contact(
                    EmergencyContactCreate(name="Mom", phone="+13060000000"), current_user=_CURRENT_USER
                )
            assert exc.value.status_code == 503
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_max_contacts_raises_400(self):
        existing = [{"id": f"c{i}"} for i in range(3)]
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=existing)}))
        try:
            with pytest.raises(HTTPException) as exc:
                await add_emergency_contact(
                    EmergencyContactCreate(name="Mom", phone="+13060000000"), current_user=_CURRENT_USER
                )
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_invalid_phone_raises_400(self):
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[])}))
        try:
            with pytest.raises(HTTPException) as exc:
                await add_emergency_contact(EmergencyContactCreate(name="Mom", phone="123"), current_user=_CURRENT_USER)
            assert exc.value.status_code == 400
        finally:
            _stop(patches)


class TestVaultEmergencyContactEncryption:
    """Migration 357 / PIA SPINR-PIA-2026-01: name+phone must go through the
    Vault RPCs on write and read, never touch the DB as plaintext."""

    @pytest.mark.anyio
    async def test_add_encrypts_name_and_phone_before_insert(self):
        insert_one = AsyncMock()
        vault_encrypt = AsyncMock(side_effect=lambda _rpc, v, _hint="": f"vault-token-{v}")
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[]),
                    "backend.routes.users.db_supabase.insert_one": insert_one,
                    "backend.routes.users.vault_encrypt": vault_encrypt,
                }
            )
        )
        try:
            result = await add_emergency_contact(
                EmergencyContactCreate(name="Mom", phone="+13060000000", relationship="Family"),
                current_user=_CURRENT_USER,
            )
            stored = insert_one.call_args.args[1]
            assert stored["name"] == "vault-token-Mom"
            assert stored["phone"] == "vault-token-+13060000000"
            for call in vault_encrypt.call_args_list:
                assert call.args[0] == "encrypt_emergency_contact_pii"
            # The response returned to the rider is plaintext, not the vault
            # token they just wrote — this is their own submission, not a
            # stored-row echo.
            assert result["contact"]["name"] == "Mom"
            assert result["contact"]["phone"] == "+13060000000"
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_add_fails_closed_when_vault_unavailable(self):
        insert_one = AsyncMock()
        vault_encrypt = AsyncMock(side_effect=HTTPException(status_code=503, detail="Encryption service unavailable"))
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[]),
                    "backend.routes.users.db_supabase.insert_one": insert_one,
                    "backend.routes.users.vault_encrypt": vault_encrypt,
                }
            )
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await add_emergency_contact(
                    EmergencyContactCreate(name="Mom", phone="+13060000000"), current_user=_CURRENT_USER
                )
            assert exc.value.status_code == 503
            # Never falls through to writing plaintext on a Vault failure.
            insert_one.assert_not_awaited()
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_get_decrypts_each_contact(self):
        stored = [
            {"id": "c1", "user_id": "user-1", "name": "vault-token-name", "phone": "vault-token-phone", "relationship": "Family"}
        ]
        vault_decrypt = AsyncMock(side_effect=lambda _rpc, v, _hint="": v.replace("vault-token-", ""))
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=stored),
                    "backend.routes.users.vault_decrypt": vault_decrypt,
                }
            )
        )
        try:
            result = await get_emergency_contacts(current_user=_CURRENT_USER)
            assert result["contacts"][0]["name"] == "name"
            assert result["contacts"][0]["phone"] == "phone"
            for call in vault_decrypt.call_args_list:
                assert call.args[0] == "decrypt_emergency_contact_pii"
        finally:
            _stop(patches)


class TestDeleteEmergencyContact:
    @pytest.mark.anyio
    async def test_success(self):
        contact = {"id": "c1", "user_id": "user-1"}
        delete_one = AsyncMock()
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[contact]),
                    "backend.routes.users.db_supabase.delete_one": delete_one,
                }
            )
        )
        try:
            result = await delete_emergency_contact("c1", current_user=_CURRENT_USER)
            assert result == {"success": True}
            delete_one.assert_awaited_once_with("emergency_contacts", {"id": "c1"})
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_not_found_raises_404(self):
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[])}))
        try:
            with pytest.raises(HTTPException) as exc:
                await delete_emergency_contact("c1", current_user=_CURRENT_USER)
            assert exc.value.status_code == 404
        finally:
            _stop(patches)


# ── rider referral program ────────────────────────────────────────────────


def _referral_patches(**overrides):
    defaults = {
        "backend.routes.users.area_id_for_rider": AsyncMock(return_value=None),
        "backend.routes.users.resolve_referral_terms": AsyncMock(
            return_value={"rides": 1, "referrer": Decimal("5"), "referee": Decimal("5")}
        ),
        "backend.routes.users.paid_referral_earnings": AsyncMock(return_value=None),
        "backend.routes.users.paid_referee_earnings": AsyncMock(return_value=None),
    }
    defaults.update(overrides)
    return _patches(**defaults)


class TestPureHelpers:
    def test_fmt_money_whole_number(self):
        assert _fmt_money(5) == "5"

    def test_fmt_money_fractional(self):
        assert _fmt_money("5.5") == "5.50"

    def test_ride_phrase_singular(self):
        assert _ride_phrase(1) == "takes their first ride"

    def test_ride_phrase_plural(self):
        assert _ride_phrase(3) == "completes 3 rides"


class TestRiderReferralSummary:
    @pytest.mark.anyio
    async def test_summary_without_referees_omits_list(self):
        user = {"id": "user-1", "referral_code": None}
        patches = _start(_referral_patches(**{"backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[])}))
        try:
            summary = await _rider_referral_summary(user, include_referees=False)
            # No stored referral_code → derived from "RIDE" + first 8 chars of the user id.
            assert summary["referral_code"] == "RIDEUSER-1"
            assert "referees" not in summary
            assert summary["total_referrals"] == 0
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_summary_with_referees_counts_qualified(self):
        user = {"id": "user-1", "referral_code": "MYCODE"}
        referred = [{"id": "r1", "first_name": "A", "last_name": "B", "created_at": "2026-01-01"}]
        get_rows = AsyncMock(return_value=referred)
        count_documents = AsyncMock(return_value=1)  # meets rides_required=1
        patches = _start(
            _referral_patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": get_rows,
                    "backend.routes.users.db_supabase.count_documents": count_documents,
                }
            )
        )
        try:
            summary = await _rider_referral_summary(user, include_referees=True)
            assert summary["referral_code"] == "MYCODE"
            assert summary["total_referrals"] == 1
            assert summary["qualified_referrals"] == 1
            assert summary["pending_referrals"] == 0
            assert summary["referees"][0]["qualified"] is True
            assert summary["referees"][0]["status"] == "earned"
            # No paid snapshot yet → falls back to estimate (referrer_reward * qualified).
            assert summary["referral_earnings"] == "5"
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_summary_asserts_grand_total_filter_applied(self):
        """Wiring check: the completed-rides count must filter on grand_total >
        0, mirroring utils.referral_payout._process_one's qualification check
        (ranked blocker #6 / audit finding N2). Asserts the actual filter dict
        passed to count_documents rather than trusting a return value alone."""
        user = {"id": "user-1", "referral_code": "MYCODE"}
        referred = [{"id": "r1", "first_name": "A", "last_name": "B", "created_at": "2026-01-01"}]
        get_rows = AsyncMock(return_value=referred)
        count_documents = AsyncMock(return_value=1)
        patches = _start(
            _referral_patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": get_rows,
                    "backend.routes.users.db_supabase.count_documents": count_documents,
                }
            )
        )
        try:
            await _rider_referral_summary(user, include_referees=True)
        finally:
            _stop(patches)
        table, filters = count_documents.await_args.args[0], count_documents.await_args.args[1]
        assert table == "rides"
        assert filters["rider_id"] == "r1"
        assert filters["status"] == "completed"
        assert filters["grand_total"] == {"$gt": 0}

    @pytest.mark.anyio
    async def test_summary_zero_fare_ride_does_not_count_as_qualified(self):
        """A referee whose only completed ride cost $0 (fully covered by a
        first_ride_only/free_ride promo) must NOT show as qualified — that ride
        alone must not satisfy referral qualification (ranked blocker #6 /
        audit finding N2, 2026-08-19)."""
        user = {"id": "user-1", "referral_code": "MYCODE"}
        referred = [{"id": "r1", "first_name": "A", "last_name": "B", "created_at": "2026-01-01"}]
        get_rows = AsyncMock(return_value=referred)

        async def count_documents(table, filters):
            # Simulate the DB: r1 has exactly one completed ride, and it's $0.
            if filters.get("grand_total") == {"$gt": 0}:
                return 0
            return 1

        patches = _start(
            _referral_patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": get_rows,
                    "backend.routes.users.db_supabase.count_documents": AsyncMock(side_effect=count_documents),
                }
            )
        )
        try:
            summary = await _rider_referral_summary(user, include_referees=True)
        finally:
            _stop(patches)
        assert summary["qualified_referrals"] == 0
        assert summary["referees"][0]["qualified"] is False
        assert summary["referees"][0]["status"] == "in_progress"

    @pytest.mark.anyio
    async def test_paid_earnings_snapshot_wins_over_estimate(self):
        user = {"id": "user-1", "referral_code": "MYCODE"}
        patches = _start(
            _referral_patches(
                **{
                    "backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[]),
                    "backend.routes.users.paid_referral_earnings": AsyncMock(return_value=Decimal("15")),
                }
            )
        )
        try:
            summary = await _rider_referral_summary(user, include_referees=False)
            assert summary["referral_earnings"] == "15"
        finally:
            _stop(patches)


class TestGetRiderReferralInfo:
    @pytest.mark.anyio
    async def test_user_not_found_raises_404(self):
        patches = _start(
            _referral_patches(**{"backend.routes.users.db_supabase.get_user_by_id": AsyncMock(return_value=None)})
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await get_rider_referral_info(current_user=_CURRENT_USER)
            assert exc.value.status_code == 404
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_success_omits_referees(self):
        patches = _start(_referral_patches())
        try:
            result = await get_rider_referral_info(current_user=_CURRENT_USER)
            assert "referees" not in result
            assert "referral_code" in result
        finally:
            _stop(patches)


class TestGetRiderReferrals:
    @pytest.mark.anyio
    async def test_user_not_found_raises_404(self):
        patches = _start(
            _referral_patches(**{"backend.routes.users.db_supabase.get_user_by_id": AsyncMock(return_value=None)})
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await get_rider_referrals(current_user=_CURRENT_USER)
            assert exc.value.status_code == 404
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_success_includes_referees(self):
        patches = _start(_referral_patches())
        try:
            result = await get_rider_referrals(current_user=_CURRENT_USER)
            assert "referees" in result
        finally:
            _stop(patches)


class TestApplyRiderReferral:
    @pytest.mark.anyio
    async def test_already_applied_raises_400(self):
        user = {**_USER_ROW, "referral_code_used": "RIDEABCDEFGH"}
        patches = _start(_patches(**{"backend.routes.users.db_supabase.get_user_by_id": AsyncMock(return_value=user)}))
        try:
            with pytest.raises(HTTPException) as exc:
                await apply_rider_referral(
                    ApplyRiderReferralRequest(referral_code="RIDEABCDEFGH"), current_user=_CURRENT_USER
                )
            assert exc.value.status_code == 400
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_ride_prefixed_code_resolves_via_id_suffix_lookup(self):
        referrer = {"id": "referrer-1"}
        # get_rows call order: RIDE-prefix id-suffix lookup succeeds first.
        get_rows = AsyncMock(return_value=[referrer])
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_user_by_id": AsyncMock(
                        return_value={**_USER_ROW, "referral_code_used": None}
                    ),
                    "backend.routes.users.db_supabase.get_rows": get_rows,
                }
            )
        )
        try:
            result = await apply_rider_referral(
                ApplyRiderReferralRequest(referral_code="RIDEABCDEFGH"), current_user=_CURRENT_USER
            )
            assert result == {"success": True, "referral_code": "RIDEABCDEFGH"}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_custom_code_resolves_via_referral_code_column(self):
        referrer = {"id": "referrer-1"}
        # Not RIDE-prefixed → skips id-suffix branch, goes straight to referral_code lookup.
        get_rows = AsyncMock(return_value=[referrer])
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_user_by_id": AsyncMock(
                        return_value={**_USER_ROW, "referral_code_used": None}
                    ),
                    "backend.routes.users.db_supabase.get_rows": get_rows,
                }
            )
        )
        try:
            result = await apply_rider_referral(
                ApplyRiderReferralRequest(referral_code="mycustomcode"), current_user=_CURRENT_USER
            )
            assert result == {"success": True, "referral_code": "MYCUSTOMCODE"}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_id_suffix_lookup_exception_is_logged_and_falls_back(self):
        referrer = {"id": "referrer-1"}
        # First call (RIDE id-suffix lookup) raises; code must fall back to the
        # referral_code column lookup rather than propagating/crashing.
        get_rows = AsyncMock(side_effect=[RuntimeError("transient"), [referrer]])
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_user_by_id": AsyncMock(
                        return_value={**_USER_ROW, "referral_code_used": None}
                    ),
                    "backend.routes.users.db_supabase.get_rows": get_rows,
                }
            )
        )
        try:
            result = await apply_rider_referral(
                ApplyRiderReferralRequest(referral_code="RIDEABCDEFGH"), current_user=_CURRENT_USER
            )
            assert result == {"success": True, "referral_code": "RIDEABCDEFGH"}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_invalid_code_raises_404(self):
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_user_by_id": AsyncMock(
                        return_value={**_USER_ROW, "referral_code_used": None}
                    ),
                    "backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[]),
                }
            )
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await apply_rider_referral(ApplyRiderReferralRequest(referral_code="NOPE"), current_user=_CURRENT_USER)
            assert exc.value.status_code == 404
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_self_referral_raises_400(self):
        self_row = {"id": "user-1"}
        patches = _start(
            _patches(
                **{
                    "backend.routes.users.db_supabase.get_user_by_id": AsyncMock(
                        return_value={**_USER_ROW, "referral_code_used": None}
                    ),
                    "backend.routes.users.db_supabase.get_rows": AsyncMock(return_value=[self_row]),
                }
            )
        )
        try:
            with pytest.raises(HTTPException) as exc:
                await apply_rider_referral(
                    ApplyRiderReferralRequest(referral_code="SELFCODE"), current_user=_CURRENT_USER
                )
            assert exc.value.status_code == 400
        finally:
            _stop(patches)
