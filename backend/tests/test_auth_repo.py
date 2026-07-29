"""repositories/auth_repo.py — user lookup/creation + OTP CRUD helpers.

Thin pass-through wrappers around supabase-py chains. Two things worth
pinning per function: the "Supabase client not configured" branch (used
in dev/test environments without a real client — some helpers return
None, others raise since callers can't proceed without a user row), and
that the happy path hits the right table with the right filters.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# get_user_by_id
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_by_id_returns_none_when_supabase_unconfigured():
    with patch("repositories.auth_repo.supabase", None):
        from repositories.auth_repo import get_user_by_id

        assert await get_user_by_id("u1") is None


@pytest.mark.asyncio
async def test_get_user_by_id_returns_cached_row_without_db_call():
    with (
        patch("repositories.auth_repo.supabase", MagicMock()) as mock_sb,
        patch("repositories.auth_repo._read_cached_row", return_value={"id": "u1", "phone": "+1"}),
    ):
        from repositories.auth_repo import get_user_by_id

        row = await get_user_by_id("u1")

    assert row == {"id": "u1", "phone": "+1"}
    mock_sb.table.assert_not_called()


@pytest.mark.asyncio
async def test_get_user_by_id_negative_cache_sentinel_returns_none():
    with (
        patch("repositories.auth_repo.supabase", MagicMock()),
        patch("repositories.auth_repo._read_cached_row", return_value={}),
    ):
        from repositories.auth_repo import get_user_by_id

        assert await get_user_by_id("u1") is None


@pytest.mark.asyncio
async def test_get_user_by_id_cache_miss_queries_and_caches():
    fake = MagicMock()
    fake.data = [{"id": "u1", "phone": "+1"}]
    write_mock = AsyncMock()

    with (
        patch("repositories.auth_repo.supabase") as mock_sb,
        patch("repositories.auth_repo._read_cached_row", AsyncMock(return_value=None)),
        patch("repositories.auth_repo._write_cached_row", write_mock),
    ):
        chain = mock_sb.table.return_value.select.return_value.eq.return_value.is_.return_value
        chain.execute.return_value = fake
        from repositories.auth_repo import get_user_by_id

        row = await get_user_by_id("u1")

    assert row["id"] == "u1"
    mock_sb.table.assert_called_with("users")
    write_mock.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# get_user_by_phone
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_by_phone_returns_none_when_supabase_unconfigured():
    with patch("repositories.auth_repo.supabase", None):
        from repositories.auth_repo import get_user_by_phone

        assert await get_user_by_phone("+1") is None


@pytest.mark.asyncio
async def test_get_user_by_phone_queries_users_table():
    fake = MagicMock()
    fake.data = [{"id": "u1", "phone": "+1"}]
    with patch("repositories.auth_repo.supabase") as mock_sb:
        chain = mock_sb.table.return_value.select.return_value.eq.return_value.is_.return_value
        chain.execute.return_value = fake
        from repositories.auth_repo import get_user_by_phone

        row = await get_user_by_phone("+1")

    assert row["phone"] == "+1"
    mock_sb.table.assert_called_with("users")


# ─────────────────────────────────────────────────────────────────────────────
# create_user
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_user_raises_when_supabase_unconfigured():
    with patch("repositories.auth_repo.supabase", None):
        from repositories.auth_repo import create_user

        with pytest.raises(RuntimeError):
            await create_user({"phone": "+1"})


@pytest.mark.asyncio
async def test_create_user_invalidates_cache_for_result_and_payload_ids():
    fake = MagicMock()
    fake.data = [{"id": "u1", "phone": "+1"}]
    invalidate_mock = AsyncMock()

    with (
        patch("repositories.auth_repo.supabase") as mock_sb,
        patch("repositories.auth_repo.invalidate_user_cache", invalidate_mock),
    ):
        mock_sb.table.return_value.insert.return_value.execute.return_value = fake
        from repositories.auth_repo import create_user

        row = await create_user({"id": "u1", "phone": "+1"})

    assert row["id"] == "u1"
    # Called once for the result id and once for the payload id (both "u1"
    # here, but the function doesn't dedupe — both call sites fire).
    assert invalidate_mock.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# OTP helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_otp_record_raises_when_supabase_unconfigured():
    with patch("repositories.auth_repo.supabase", None):
        from repositories.auth_repo import insert_otp_record

        with pytest.raises(RuntimeError):
            await insert_otp_record({"phone": "+1", "code": "123456"})


@pytest.mark.asyncio
async def test_insert_otp_record_writes_row():
    fake = MagicMock()
    fake.data = [{"id": "otp1", "phone": "+1"}]
    with patch("repositories.auth_repo.supabase") as mock_sb:
        mock_sb.table.return_value.insert.return_value.execute.return_value = fake
        from repositories.auth_repo import insert_otp_record

        row = await insert_otp_record({"phone": "+1", "code": "123456"})

    assert row["id"] == "otp1"
    mock_sb.table.assert_called_with("otp_records")


@pytest.mark.asyncio
async def test_get_otp_record_returns_none_when_supabase_unconfigured():
    with patch("repositories.auth_repo.supabase", None):
        from repositories.auth_repo import get_otp_record

        assert await get_otp_record("+1", "123456") is None


@pytest.mark.asyncio
async def test_get_otp_record_filters_by_phone_code_and_unverified():
    fake = MagicMock()
    fake.data = [{"id": "otp1", "verified": False}]
    with patch("repositories.auth_repo.supabase") as mock_sb:
        chain = mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value
        chain.execute.return_value = fake
        from repositories.auth_repo import get_otp_record

        row = await get_otp_record("+1", "123456")

    assert row["id"] == "otp1"
    mock_sb.table.assert_called_with("otp_records")


@pytest.mark.asyncio
async def test_get_otp_record_by_phone_returns_none_when_supabase_unconfigured():
    with patch("repositories.auth_repo.supabase", None):
        from repositories.auth_repo import get_otp_record_by_phone

        assert await get_otp_record_by_phone("+1") is None


@pytest.mark.asyncio
async def test_get_otp_record_by_phone_orders_by_created_at_desc():
    fake = MagicMock()
    fake.data = [{"id": "otp-latest"}]
    with patch("repositories.auth_repo.supabase") as mock_sb:
        chain = (
            mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value
        )
        chain.execute.return_value = fake
        from repositories.auth_repo import get_otp_record_by_phone

        row = await get_otp_record_by_phone("+1")

    assert row["id"] == "otp-latest"


@pytest.mark.asyncio
async def test_verify_otp_record_returns_none_when_supabase_unconfigured():
    with patch("repositories.auth_repo.supabase", None):
        from repositories.auth_repo import verify_otp_record

        assert await verify_otp_record("otp1") is None


@pytest.mark.asyncio
async def test_verify_otp_record_stamps_verified_true():
    fake = MagicMock()
    fake.data = [{"id": "otp1", "verified": True}]
    with patch("repositories.auth_repo.supabase") as mock_sb:
        mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = fake
        from repositories.auth_repo import verify_otp_record

        row = await verify_otp_record("otp1")

    assert row["verified"] is True
    mock_sb.table.return_value.update.assert_called_with({"verified": True})


@pytest.mark.asyncio
async def test_delete_otp_record_returns_none_when_supabase_unconfigured():
    with patch("repositories.auth_repo.supabase", None):
        from repositories.auth_repo import delete_otp_record

        assert await delete_otp_record("otp1") is None


@pytest.mark.asyncio
async def test_delete_otp_record_deletes_by_id():
    fake = MagicMock()
    fake.data = [{"id": "otp1"}]
    with patch("repositories.auth_repo.supabase") as mock_sb:
        mock_sb.table.return_value.delete.return_value.eq.return_value.execute.return_value = fake
        from repositories.auth_repo import delete_otp_record

        row = await delete_otp_record("otp1")

    assert row["id"] == "otp1"
    mock_sb.table.assert_called_with("otp_records")
