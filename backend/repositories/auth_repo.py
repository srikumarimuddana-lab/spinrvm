"""Auth / user / OTP repository — user lookups, creation, OTP CRUD.

Extracted from db_supabase.py (Phase 3 of god-object decomposition).
"""

from typing import Any, Dict, Optional

try:
    from ._base import (
        _USER_CACHE_TTL_SECONDS,
        _read_cached_row,
        _serialize_for_api,
        _single_row_from_res,
        _user_cache_key,
        _write_cached_row,
        _write_skipped,
        invalidate_user_cache,
        run_sync,
        supabase,
    )
except ImportError:
    from repositories._base import (  # type: ignore
        _USER_CACHE_TTL_SECONDS,
        _read_cached_row,
        _serialize_for_api,
        _single_row_from_res,
        _user_cache_key,
        _write_cached_row,
        _write_skipped,
        invalidate_user_cache,
        run_sync,
        supabase,
    )


# ============ User Helpers ============


async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None

    # Redis read-through cache (30s TTL). Absorbs the /auth/me-on-every-call
    # traffic pattern without adding staleness beyond the access-token TTL.
    key = _user_cache_key(user_id)
    cached = await _read_cached_row(key)
    if cached is not None:
        # {} is the negative-cache sentinel ("no such user") — preserve that meaning.
        return None if cached == {} else cached

    user = await run_sync(
        lambda: _single_row_from_res(
            supabase.table("users").select("*").eq("id", user_id).is_("deleted_at", "null").execute()
        )
    )
    await _write_cached_row(key, user, ttl=_USER_CACHE_TTL_SECONDS)
    return user


async def get_user_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(
        lambda: _single_row_from_res(
            supabase.table("users").select("*").eq("phone", phone).is_("deleted_at", "null").execute()
        )
    )


async def create_user(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    payload = _serialize_for_api(payload)
    result = await run_sync(lambda: _single_row_from_res(supabase.table("users").insert(payload).execute()))
    # Drop any negative-cache entry so the next get_user_by_id picks up
    # the fresh row instead of the "no such user" sentinel we may have
    # cached from a prior look-up attempt.
    if isinstance(result, dict):
        await invalidate_user_cache(result.get("id"))
    await invalidate_user_cache(payload.get("id") if isinstance(payload, dict) else None)
    return result


# ============ OTP Helpers ============


async def insert_otp_record(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    payload = _serialize_for_api(payload)
    return await run_sync(lambda: _single_row_from_res(supabase.table("otp_records").insert(payload).execute()))


async def get_otp_record(phone: str, code: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(
        lambda: _single_row_from_res(
            supabase.table("otp_records")
            .select("*")
            .eq("phone", phone)
            .eq("code", code)
            .eq("verified", False)
            .execute()
        )
    )


async def get_otp_record_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    """Fetch the most-recent unverified OTP record for a phone number.
    Used by verify_otp so the hash comparison can be done in constant time
    with hmac.compare_digest rather than via a DB equality predicate.
    """
    if not supabase:
        return None
    return await run_sync(
        lambda: _single_row_from_res(
            supabase.table("otp_records")
            .select("*")
            .eq("phone", phone)
            .eq("verified", False)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    )


async def verify_otp_record(record_id: str):
    if not supabase:
        _write_skipped("verify_otp_record", "otp_records")
        return None
    return await run_sync(
        lambda: _single_row_from_res(
            supabase.table("otp_records").update({"verified": True}).eq("id", record_id).execute()
        )
    )


async def delete_otp_record(record_id: str):
    if not supabase:
        _write_skipped("delete_otp_record", "otp_records")
        return None
    return await run_sync(
        lambda: _single_row_from_res(supabase.table("otp_records").delete().eq("id", record_id).execute())
    )
