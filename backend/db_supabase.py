"""Supabase database access layer.

Shared infrastructure (run_sync, circuit breaker, generic CRUD, cache)
lives in repositories/_base.py.  This module re-exports everything from
_base so the 40+ existing callers continue to resolve, and adds
domain-specific helpers that will migrate to individual repository
modules in subsequent phases.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from .repositories._base import (  # noqa: F401 — re-exported
        _DB_EXECUTOR,
        _DRIVER_BY_USER_CACHE_TTL_SECONDS,
        _DRIVER_CACHE_TTL_SECONDS,
        _NEGATIVE_CACHE_SENTINEL,
        _USER_CACHE_TTL_SECONDS,
        DatabaseError,
        DuplicateRecordError,
        RetryPolicy,
        ServiceUnavailableException,
        _apply_filters,
        _breaker,
        _build_or_clause,
        _build_or_clause_term,
        _db_executor,
        _driver_by_user_cache_key,
        _driver_cache_key,
        _metric_prefix_for_key,
        _postgrest_pattern,
        _pre_invalidate_for_table,
        _read_cached_row,
        _rows_from_res,
        _serialize_for_api,
        _single_row_from_res,
        _user_cache_key,
        _write_cached_row,
        count_documents,
        delete_many,
        delete_one,
        find_one,
        get_rows,
        insert_many,
        insert_one,
        invalidate_driver_cache,
        invalidate_user_cache,
        ping,
        rpc,
        run_sync,
        supabase,
        update_one,
    )
except ImportError:
    from repositories._base import (  # type: ignore  # noqa: F401
        _DB_EXECUTOR,
        _DRIVER_BY_USER_CACHE_TTL_SECONDS,
        _DRIVER_CACHE_TTL_SECONDS,
        _NEGATIVE_CACHE_SENTINEL,
        _USER_CACHE_TTL_SECONDS,
        DatabaseError,
        DuplicateRecordError,
        RetryPolicy,
        ServiceUnavailableException,
        _apply_filters,
        _breaker,
        _build_or_clause,
        _build_or_clause_term,
        _db_executor,
        _driver_by_user_cache_key,
        _driver_cache_key,
        _metric_prefix_for_key,
        _postgrest_pattern,
        _pre_invalidate_for_table,
        _read_cached_row,
        _rows_from_res,
        _serialize_for_api,
        _single_row_from_res,
        _user_cache_key,
        _write_cached_row,
        count_documents,
        delete_many,
        delete_one,
        find_one,
        get_rows,
        insert_many,
        insert_one,
        invalidate_driver_cache,
        invalidate_user_cache,
        ping,
        rpc,
        run_sync,
        supabase,
        update_one,
    )

try:
    from .repositories.corporate_repo import (  # noqa: F401 — re-exported
        accept_member_invite,
        add_allowed_domain,
        create_kyb_upload_url,
        delete_allowed_domain,
        delete_corporate_account,
        ensure_corporate_wallet,
        find_companies_by_email_domain,
        get_all_corporate_accounts,
        get_allowance_request_by_id,
        get_corporate_account_by_id,
        get_corporate_member_by_id,
        get_corporate_members_for_user,
        get_corporate_policy,
        get_corporate_wallet_by_company,
        get_default_payment_method,
        get_member_allowance,
        get_member_by_invite_token,
        insert_allowance_request,
        insert_corporate_account,
        insert_corporate_member_invite,
        list_active_memberships_for_user,
        list_allowances_due_for_reset,
        list_allowed_domains,
        list_company_allowance_requests,
        list_company_allowances,
        list_company_members,
        list_company_ride_payment_sources,
        list_corporate_accounts_filtered,
        list_pending_allowance_requests_for_member,
        list_wallet_transactions,
        list_wallets_low_balance_no_autotopup,
        list_wallets_needing_autotopup,
        mark_low_balance_notified,
        record_kyb_decision,
        reset_allowance_period,
        sum_autotopups_today,
        update_allowance_request,
        update_corporate_account,
        update_corporate_account_status,
        update_corporate_member,
        update_corporate_stripe_customer_id,
        update_corporate_wallet_config,
        upsert_corporate_policy,
        upsert_member_allowance,
    )
except ImportError:
    from repositories.corporate_repo import (  # type: ignore  # noqa: F401
        accept_member_invite,
        add_allowed_domain,
        create_kyb_upload_url,
        delete_allowed_domain,
        delete_corporate_account,
        ensure_corporate_wallet,
        find_companies_by_email_domain,
        get_all_corporate_accounts,
        get_allowance_request_by_id,
        get_corporate_account_by_id,
        get_corporate_member_by_id,
        get_corporate_members_for_user,
        get_corporate_policy,
        get_corporate_wallet_by_company,
        get_default_payment_method,
        get_member_allowance,
        get_member_by_invite_token,
        insert_allowance_request,
        insert_corporate_account,
        insert_corporate_member_invite,
        list_active_memberships_for_user,
        list_allowances_due_for_reset,
        list_allowed_domains,
        list_company_allowance_requests,
        list_company_allowances,
        list_company_members,
        list_company_ride_payment_sources,
        list_corporate_accounts_filtered,
        list_pending_allowance_requests_for_member,
        list_wallet_transactions,
        list_wallets_low_balance_no_autotopup,
        list_wallets_needing_autotopup,
        mark_low_balance_notified,
        record_kyb_decision,
        reset_allowance_period,
        sum_autotopups_today,
        update_allowance_request,
        update_corporate_account,
        update_corporate_account_status,
        update_corporate_member,
        update_corporate_stripe_customer_id,
        update_corporate_wallet_config,
        upsert_corporate_policy,
        upsert_member_allowance,
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


# ============ Driver Helpers ============


async def get_driver_by_id(driver_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None

    key = _driver_cache_key(driver_id)
    cached = await _read_cached_row(key)
    if cached is not None:
        return None if cached == {} else cached

    driver = await run_sync(
        lambda: _single_row_from_res(
            supabase.table("drivers").select("*").eq("id", driver_id).is_("deleted_at", "null").execute()
        )
    )
    await _write_cached_row(key, driver, ttl=_DRIVER_CACHE_TTL_SECONDS)
    return driver


async def get_driver_by_user_id_cached(user_id: str) -> Optional[Dict[str, Any]]:
    """Driver row for the given user_id, with a 30s Redis cache.

    Hot path: get_current_user reads this on every authenticated request
    to set user["is_driver"]. Raw Supabase look-up adds ~40-80ms per call;
    caching collapses that to ~1ms on the hit path.
    """
    if not supabase:
        return None

    key = _driver_by_user_cache_key(user_id)
    cached = await _read_cached_row(key)
    if cached is not None:
        return None if cached == {} else cached

    rows = await run_sync(
        lambda: _rows_from_res(
            supabase.table("drivers").select("*").eq("user_id", user_id).is_("deleted_at", "null").limit(1).execute()
        )
    )
    driver = rows[0] if rows else None
    await _write_cached_row(key, driver, ttl=_DRIVER_BY_USER_CACHE_TTL_SECONDS)
    return driver


async def get_service_area_for_point(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """Return the first active service area containing (lat, lng) via PostGIS RPC, or None."""
    if not supabase:
        return None

    def _call():
        res = supabase.rpc(
            "get_service_area_for_point",
            {"lat": lat, "lng": lng},
        ).execute()
        rows = _rows_from_res(res)
        return rows[0] if rows else None

    try:
        return await run_sync(_call)
    except Exception as exc:
        logger.error(f"get_service_area_for_point failed: {exc}", exc_info=True)
        return None


async def find_nearby_drivers(lat: float, lng: float, radius_meters: float) -> List[Dict[str, Any]]:
    """Use PostGIS RPC to find nearby drivers."""
    if not supabase:
        return []

    def _fn():
        res = supabase.rpc("find_nearby_drivers", {"lat": lat, "lng": lng, "radius_meters": radius_meters}).execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


async def update_driver_location(driver_id: str, lat: float, lng: float):
    if not supabase:
        return None

    def _update():
        # The Supabase RPC seems to have a type mismatch (text vs uuid) error.
        # We'll bypass the RPC and update the table directly.
        # Assuming table has 'lat' and 'lng' columns (or 'location' if that works, but Traceback used lat/lng).

        # Note: If 'location' is a PostGIS column, we might need to update it too.
        # But failing RPC prevents any update. Direct update is safer for now.

        data = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc).isoformat()}
        supabase.table("drivers").update(data).eq("id", str(driver_id)).execute()
        return True

    return await run_sync(_update)


async def set_driver_available(driver_id: str, available: bool = True, total_rides_inc: int = 0):
    if not supabase:
        logger.warning("[GO-ONLINE] set_driver_available: supabase client is None!")
        return None

    await invalidate_driver_cache(driver_id=driver_id)

    def _update():
        payload: Dict[str, Any] = {"is_available": available}
        logger.info(
            f"[GO-ONLINE] set_driver_available CALLED driver_id={driver_id} "
            f"available={available} total_rides_inc={total_rides_inc} "
            f"payload={payload} (NOTE: only writes is_available, drops any "
            f"other fields the caller may have passed)"
        )
        if total_rides_inc == 0:
            res = supabase.table("drivers").update(payload).eq("id", driver_id).execute()
            logger.info(f"[GO-ONLINE] set_driver_available executed, res.data={getattr(res, 'data', None)}")
            return _single_row_from_res(res)

        # If increment needed, read then write (simulated atomic)
        # Ideally this should be an RPC or a better query if Supabase supported $inc
        cur = supabase.table("drivers").select("total_rides").eq("id", driver_id).execute()
        cur_data = _rows_from_res(cur)
        cur_val = cur_data[0].get("total_rides", 0) if cur_data else 0

        payload["total_rides"] = cur_val + total_rides_inc
        res = supabase.table("drivers").update(payload).eq("id", driver_id).execute()
        return _single_row_from_res(res)

    result = await run_sync(_update)
    # Driver row changed (is_available / total_rides) → evict both the
    # by-id and by-user cache entries.
    user_id = result.get("user_id") if isinstance(result, dict) else None
    await invalidate_driver_cache(driver_id=driver_id, user_id=user_id)
    return result


async def match_and_claim_driver(
    vehicle_type_id: str,
    pickup_lat: float,
    pickup_lng: float,
    radius_km: float,
    min_rating: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Atomically find and claim the nearest available driver via PostGIS RPC.

    Returns the claimed driver row, or None if no eligible driver is available.
    Uses SELECT ... FOR UPDATE SKIP LOCKED in the DB — safe under concurrent dispatch.
    """
    if not supabase:
        return None

    def _call():
        res = supabase.rpc(
            "match_and_claim_driver",
            {
                "p_vehicle_type_id": vehicle_type_id,
                "p_pickup_lat": pickup_lat,
                "p_pickup_lng": pickup_lng,
                "p_radius_km": radius_km,
                "p_min_rating": min_rating,
            },
        ).execute()
        rows = _rows_from_res(res)
        return rows[0] if rows else None

    try:
        result = await run_sync(_call)
        if result:
            await invalidate_driver_cache(driver_id=result["id"])
        return result
    except Exception as exc:
        logger.error(f"match_and_claim_driver RPC failed: {exc}", exc_info=True)
        return None


async def claim_driver_atomic(driver_id: str) -> bool:
    """Atomically set is_available = false for driver if currently available."""
    if not supabase:
        return False

    await invalidate_driver_cache(driver_id=driver_id)

    def _claim():
        res = (
            supabase.table("drivers")
            .update({"is_available": False})
            .eq("id", driver_id)
            .eq("is_available", True)
            .execute()
        )
        data = _rows_from_res(res)
        return len(data) > 0

    claimed = await run_sync(_claim)
    if claimed:
        # The driver row is now is_available=false — make sure the cache
        # doesn't keep serving a stale "still available" value to
        # concurrent dispatch queries for the next 30s.
        await invalidate_driver_cache(driver_id=driver_id)
    return claimed


async def claim_ride_atomic(ride_id: str, driver_id: str) -> bool:
    """Atomically claim a ride offer for `driver_id`.

    Issues a single conditional UPDATE that sets
    ``status='driver_accepted'`` and ``driver_id=<driver>`` only if the
    ride is (1) identified by `ride_id`, (2) in an open, claimable state
    (`searching` or `driver_assigned`), and (3) either unassigned or
    already pre-assigned to THIS driver. Supabase's PostgREST layer
    evaluates all three filters atomically in one SQL statement, so two
    drivers racing to accept the same offer cannot both succeed — the
    loser's UPDATE matches zero rows and this function returns False.

    Returns:
        True  — we successfully claimed the ride and the driver-app can
                proceed with the ride flow.
        False — the ride was gone or already accepted by another driver;
                the caller should surface a "ride already taken" UX.
    """
    if not supabase:
        return False

    now_iso = datetime.now(timezone.utc).isoformat()

    def _claim():
        res = (
            supabase.table("rides")
            .update(
                {
                    "status": "driver_accepted",
                    "driver_id": driver_id,
                    "driver_accepted_at": now_iso,
                    "updated_at": now_iso,
                }
            )
            .eq("id", ride_id)
            # Status must be open/claimable. Any status past `driver_accepted`
            # (arrived / in_progress / completed / cancelled) is terminal for
            # the accept flow.
            .in_("status", ["searching", "driver_assigned"])
            # Ride must be either unassigned or already pre-assigned to this
            # driver. PostgREST's `.or_()` accepts a comma-separated filter
            # list; `is.null` maps to `IS NULL` in SQL.
            .or_(f"driver_id.is.null,driver_id.eq.{driver_id}")
            .execute()
        )
        data = _rows_from_res(res)
        return len(data) > 0

    return await run_sync(_claim)


# ============ Ride Helpers ============


async def get_ride(ride_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(
        lambda: _single_row_from_res(
            supabase.table("rides").select("*").eq("id", ride_id).is_("deleted_at", "null").execute()
        )
    )


async def insert_ride(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    payload = _serialize_for_api(payload)
    logger.info(f"[DEBUG-INSERT-RIDE] payload keys: {sorted(payload.keys())}")
    logger.info(f"[DEBUG-INSERT-RIDE] full payload: {payload}")
    try:
        result = await run_sync(lambda: _single_row_from_res(supabase.table("rides").insert(payload).execute()))
        logger.info(f"[DEBUG-INSERT-RIDE] SUCCESS ride_id={payload.get('id')}")
        return result
    except Exception as e:
        logger.error(f"[DEBUG-INSERT-RIDE] FAILED: {e}")
        raise


async def update_ride(ride_id: str, updates: Dict[str, Any]):
    if not supabase:
        return None
    # Strip MongoDB-style $set wrapper if present
    updates = updates.get("$set", updates)
    updates = _serialize_for_api(updates)
    return await run_sync(
        lambda: _single_row_from_res(supabase.table("rides").update(updates).eq("id", ride_id).execute())
    )


async def claim_ride_payment_processing(ride_id: str) -> bool:
    """Atomically transition payment_status from 'pending' to 'processing'.

    Returns True if this caller claimed the row (proceed to call Stripe).
    Returns False if another concurrent request already claimed it (return 409).
    Raises if Supabase is unreachable.
    """
    if not supabase:
        raise RuntimeError("Supabase client not configured")

    def _fn() -> bool:
        res = (
            supabase.table("rides")
            .update({"payment_status": "processing"})
            .eq("id", ride_id)
            .eq("payment_status", "pending")
            .execute()
        )
        rows = _rows_from_res(res)
        return len(rows) > 0

    return await run_sync(_fn)


async def get_rides_for_user(rider_id: str, limit: int = 100):
    if not supabase:
        return []
    return await run_sync(
        lambda: _rows_from_res(
            supabase.table("rides")
            .select("*")
            .eq("rider_id", rider_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    )


async def get_rides_for_driver(
    driver_id: str,
    statuses: Optional[List[str]] = None,
    limit: int = 100,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    if not supabase:
        return []

    def _fn():
        q = supabase.table("rides").select("*").eq("driver_id", driver_id)
        if statuses:
            status_filters = ",".join([f"status.eq.{s}" for s in statuses])
            q = q.or_(status_filters)
        if from_date:
            q = q.gte("created_at", from_date)
        if to_date:
            q = q.lt("created_at", to_date)
        q = q.order("created_at", desc=True).limit(limit)
        return _rows_from_res(q.execute())

    return await run_sync(_fn)


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
        return None
    return await run_sync(
        lambda: _single_row_from_res(
            supabase.table("otp_records").update({"verified": True}).eq("id", record_id).execute()
        )
    )


async def delete_otp_record(record_id: str):
    if not supabase:
        return None
    return await run_sync(
        lambda: _single_row_from_res(supabase.table("otp_records").delete().eq("id", record_id).execute())
    )


# ── Query Helpers, Generic CRUD, and rpc are now in repositories/_base.py ──
# Re-exported via the import block at the top of this file.


# ============ Atomic Wallet RPCs (P0-4, P0-5, P0-6) ============


async def wallet_increment_balance(wallet_id: str, amount: "Decimal") -> "Decimal":
    """Atomically increment a wallet balance. Returns the new balance."""
    from decimal import Decimal as _Decimal  # noqa: PLC0415

    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        res = supabase.rpc(
            "wallet_increment_balance",
            {"p_wallet_id": wallet_id, "p_amount": str(amount)},
        ).execute()
        data = getattr(res, "data", None)
        if data is None:
            raise DatabaseError(details={"original": "wallet_increment_balance: no data returned"})
        return _Decimal(str(data))

    return await run_sync(_fn)


async def wallet_pay_for_ride(wallet_id: str, ride_id: str, amount: "Decimal") -> "Decimal":
    """Atomically debit wallet and mark ride paid. Returns the new balance.

    Raises ValueError('insufficient_funds') if balance < amount.
    """
    from decimal import Decimal as _Decimal  # noqa: PLC0415

    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        try:
            res = supabase.rpc(
                "wallet_pay_for_ride",
                {"p_wallet_id": wallet_id, "p_ride_id": ride_id, "p_amount": str(amount)},
            ).execute()
        except Exception as exc:
            msg = str(exc).lower()
            if "insufficient_funds" in msg:
                raise ValueError("insufficient_funds") from exc
            if "wallet not found" in msg:
                raise ValueError("wallet_not_found") from exc
            raise
        data = getattr(res, "data", None)
        if data is None:
            raise DatabaseError(details={"original": "wallet_pay_for_ride: no data returned"})
        return _Decimal(str(data))

    return await run_sync(_fn)


async def wallet_transfer(sender_id: str, recipient_id: str, amount: "Decimal") -> "tuple[Decimal, Decimal]":
    """Atomically transfer between two wallets. Returns (sender_balance, recipient_balance).

    Raises ValueError('insufficient_funds') if sender balance < amount.
    """
    from decimal import Decimal as _Decimal  # noqa: PLC0415

    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        try:
            res = supabase.rpc(
                "wallet_transfer",
                {
                    "p_sender_id": sender_id,
                    "p_recipient_id": recipient_id,
                    "p_amount": str(amount),
                },
            ).execute()
        except Exception as exc:
            msg = str(exc).lower()
            if "insufficient_funds" in msg:
                raise ValueError("insufficient_funds") from exc
            if "wallet not found" in msg:
                raise ValueError("wallet_not_found") from exc
            raise
        data = getattr(res, "data", None)
        if not data:
            raise DatabaseError(details={"original": "wallet_transfer: no data returned"})
        row = data[0] if isinstance(data, list) else data
        return (
            _Decimal(str(row["sender_balance"])),
            _Decimal(str(row["recipient_balance"])),
        )

    return await run_sync(_fn)


async def increment_promo_uses(promo_id: str, max_uses: int) -> bool:
    """Atomically increment promo uses if uses < max_uses. Returns True if
    the promo still had capacity (row updated), False if exhausted.
    Callers should raise HTTP 409 on False.
    """
    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        res = supabase.rpc(
            "increment_promo_uses",
            {"p_promo_id": promo_id, "p_max_uses": max_uses},
        ).execute()
        data = getattr(res, "data", None)
        return data is True or data == 1 or (isinstance(data, list) and len(data) > 0)

    return await run_sync(_fn)


async def fare_split_pay_share(wallet_id: str, participant_id: str, amount: "Decimal") -> "Decimal":
    """Atomically deduct `amount` from `wallet_id` and mark `participant_id`
    as paid in a single Postgres transaction. Returns the new wallet balance.
    Raises ValueError('insufficient_funds') when balance is insufficient.
    """
    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        try:
            res = supabase.rpc(
                "fare_split_pay_share",
                {
                    "p_wallet_id": wallet_id,
                    "p_participant_id": participant_id,
                    "p_amount": str(amount),
                },
            ).execute()
        except Exception as exc:
            msg = str(exc).lower()
            if "insufficient_funds" in msg:
                raise ValueError("insufficient_funds") from exc
            raise
        data = getattr(res, "data", None)
        if data is None:
            raise DatabaseError(details={"original": "fare_split_pay_share returned no data"})
        return data

    try:
        raw = await run_sync(_fn)
        return Decimal(str(raw))
    except Exception as exc:
        msg = str(exc)
        if "insufficient_funds" in msg:
            raise ValueError("insufficient_funds") from exc
        raise


# ============ Rides Admin Dashboard – New Helpers ============


async def get_ride_count_by_date_range(start_iso: str, end_iso: str) -> int:
    """Count rides created within a date range using Supabase SDK."""
    if not supabase:
        return 0

    def _fn():
        res = (
            supabase.table("rides")
            .select("id", count="exact")
            .limit(1)
            .gte("created_at", start_iso)
            .lt("created_at", end_iso)
            .execute()
        )
        if hasattr(res, "count") and res.count is not None:
            return int(res.count)
        return 0

    return await run_sync(_fn)


async def get_ride_details_enriched(ride_id: str) -> Optional[Dict[str, Any]]:
    """Get a ride with enriched rider/driver details, flags, complaints, lost items."""
    if not supabase:
        return None

    def _get_ride():
        return _single_row_from_res(supabase.table("rides").select("*").eq("id", ride_id).execute())

    ride = await run_sync(_get_ride)
    if not ride:
        return None

    rider_id = ride.get("rider_id")
    driver_id = ride.get("driver_id")

    # --- Batch 1: all queries that depend only on rider_id / driver_id / ride_id ---

    async def _fetch_rider():
        if not rider_id:
            return None
        return await run_sync(
            lambda rid=rider_id: _single_row_from_res(
                supabase.table("users")
                .select("first_name,last_name,phone,email,profile_image,status,created_at")
                .eq("id", rid)
                .execute()
            )
        )

    async def _fetch_rider_area():
        if not rider_id:
            return None
        return await run_sync(
            lambda rid=rider_id: _single_row_from_res(
                supabase.table("rides")
                .select("service_area_id")
                .eq("rider_id", rid)
                .neq("service_area_id", "null")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
        )

    async def _fetch_rider_count():
        if not rider_id:
            return None
        return await run_sync(
            lambda rid=rider_id: (
                supabase.table("rides").select("id", count="exact").limit(1).eq("rider_id", rid).execute()
            )
        )

    async def _fetch_driver():
        if not driver_id:
            return None
        return await run_sync(
            lambda did=driver_id: _single_row_from_res(
                supabase.table("drivers")
                .select(
                    "name,phone,vehicle_make,vehicle_model,vehicle_color,vehicle_year,vehicle_vin,license_plate,rating,status,photo_url,vehicle_type_id,total_rides,service_area_id"
                )
                .eq("id", did)
                .execute()
            )
        )

    async def _fetch_driver_completed():
        if not driver_id:
            return None
        return await run_sync(
            lambda did=driver_id: (
                supabase.table("rides")
                .select("id", count="exact")
                .limit(1)
                .eq("driver_id", did)
                .eq("status", "completed")
                .execute()
            )
        )

    async def _fetch_driver_total():
        if not driver_id:
            return None
        return await run_sync(
            lambda did=driver_id: (
                supabase.table("rides").select("id", count="exact").limit(1).eq("driver_id", did).execute()
            )
        )

    async def _fetch_rider_flags():
        if not rider_id:
            return []
        return await run_sync(
            lambda rid=rider_id: _rows_from_res(
                supabase.table("flags")
                .select("*")
                .eq("target_type", "rider")
                .eq("target_id", rid)
                .eq("is_active", True)
                .order("created_at", desc=True)
                .execute()
            )
        )

    async def _fetch_driver_flags():
        if not driver_id:
            return []
        return await run_sync(
            lambda did=driver_id: _rows_from_res(
                supabase.table("flags")
                .select("*")
                .eq("target_type", "driver")
                .eq("target_id", did)
                .eq("is_active", True)
                .order("created_at", desc=True)
                .execute()
            )
        )

    async def _fetch_complaints():
        return await run_sync(
            lambda rid=ride_id: _rows_from_res(
                supabase.table("complaints").select("*").eq("ride_id", rid).order("created_at", desc=True).execute()
            )
        )

    async def _fetch_lost_items():
        return await run_sync(
            lambda rid=ride_id: _rows_from_res(
                supabase.table("lost_and_found").select("*").eq("ride_id", rid).order("created_at", desc=True).execute()
            )
        )

    async def _fetch_location_trail():
        return await run_sync(
            lambda rid=ride_id: _rows_from_res(
                supabase.table("driver_location_history")
                .select("lat,lng,speed,heading,tracking_phase,timestamp")
                .eq("ride_id", rid)
                .order("timestamp")
                .limit(5000)
                .execute()
            )
        )

    (
        rider,
        rider_area,
        rider_count_res,
        driver,
        driver_completed_res,
        driver_total_assigned_res,
        rider_flags,
        driver_flags,
        ride_complaints,
        ride_lost_items,
        ride_location_trail,
    ) = await asyncio.gather(
        _fetch_rider(),
        _fetch_rider_area(),
        _fetch_rider_count(),
        _fetch_driver(),
        _fetch_driver_completed(),
        _fetch_driver_total(),
        _fetch_rider_flags(),
        _fetch_driver_flags(),
        _fetch_complaints(),
        _fetch_lost_items(),
        _fetch_location_trail(),
    )

    # --- Batch 2: lookups that depend on batch 1 results ---
    rider_area_id = rider_area.get("service_area_id") if rider_area else None
    driver_area_id = driver.get("service_area_id") if driver else None
    vtype_id = driver.get("vehicle_type_id") if driver else None

    async def _fetch_service_area(area_id):
        if not area_id:
            return None
        return await run_sync(
            lambda aid=area_id: _single_row_from_res(
                supabase.table("service_areas").select("name,city").eq("id", aid).execute()
            )
        )

    async def _fetch_vehicle_type(vid):
        if not vid:
            return None
        return await run_sync(
            lambda v=vid: _single_row_from_res(
                supabase.table("vehicle_types").select("name,description,capacity").eq("id", v).execute()
            )
        )

    area, d_area, vtype = await asyncio.gather(
        _fetch_service_area(rider_area_id),
        _fetch_service_area(driver_area_id),
        _fetch_vehicle_type(vtype_id),
    )

    # --- Assemble rider fields ---
    if rider_id and rider:
        ride["rider_name"] = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or rider_id[:12]
        ride["rider_phone"] = rider.get("phone", "")
        ride["rider_email"] = rider.get("email", "")
        ride["rider_profile_image"] = rider.get("profile_image", "")
        ride["rider_status"] = rider.get("status", "active")
        ride["rider_joined"] = rider.get("created_at", "")

    if rider_id:
        ride["rider_region"] = area.get("name", "") if area else ""
        ride["rider_city"] = area.get("city", "") if area else ""
        ride["rider_total_rides"] = (
            int(rider_count_res.count)
            if rider_count_res is not None and hasattr(rider_count_res, "count") and rider_count_res.count is not None
            else 0
        )

    # --- Assemble driver fields ---
    if driver_id and driver:
        ride["driver_name"] = driver.get("name", driver_id[:12])
        ride["driver_phone"] = driver.get("phone", "")
        ride["driver_vehicle_make"] = driver.get("vehicle_make", "")
        ride["driver_vehicle_model"] = driver.get("vehicle_model", "")
        ride["driver_vehicle_color"] = driver.get("vehicle_color", "")
        ride["driver_vehicle_year"] = driver.get("vehicle_year")
        ride["driver_vehicle_vin"] = driver.get("vehicle_vin", "")
        ride["driver_license_plate"] = driver.get("license_plate", "")
        ride["driver_rating"] = driver.get("rating", 0)
        ride["driver_status"] = driver.get("status", "active")
        ride["driver_photo_url"] = driver.get("photo_url", "")
        ride["driver_region"] = d_area.get("name", "") if d_area else ""
        ride["driver_city"] = d_area.get("city", "") if d_area else ""
        ride["driver_vehicle"] = f"{driver.get('vehicle_make', '')} {driver.get('vehicle_model', '')}".strip()
        ride["driver_total_rides"] = driver.get("total_rides", 0)

        if vtype:
            ride["driver_vehicle_type_name"] = vtype.get("name", "")
            ride["driver_vehicle_capacity"] = vtype.get("capacity", 0)

        completed = (
            int(driver_completed_res.count)
            if driver_completed_res is not None
            and hasattr(driver_completed_res, "count")
            and driver_completed_res.count is not None
            else 0
        )
        total_assigned = (
            int(driver_total_assigned_res.count)
            if driver_total_assigned_res is not None
            and hasattr(driver_total_assigned_res, "count")
            and driver_total_assigned_res.count is not None
            else 0
        )
        ride["driver_acceptance_rate"] = round((completed / total_assigned * 100), 1) if total_assigned > 0 else 0
        ride["driver_completed_rides"] = completed

    # --- Flags ---
    flags = [{**f, "_party": "rider"} for f in rider_flags] + [{**f, "_party": "driver"} for f in driver_flags]
    ride["flags"] = flags
    ride["rider_flag_count"] = sum(1 for f in flags if f.get("_party") == "rider")
    ride["driver_flag_count"] = sum(1 for f in flags if f.get("_party") == "driver")

    # --- Ride-level data ---
    ride["complaints"] = ride_complaints
    ride["lost_and_found"] = ride_lost_items
    ride["location_trail"] = ride_location_trail

    return ride


async def create_flag(flag_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a flag and check if auto-ban threshold (3) is reached."""
    if not supabase:
        raise RuntimeError("Supabase client not configured")

    flag_data = _serialize_for_api(flag_data)

    # Insert flag
    flag = await run_sync(lambda: _single_row_from_res(supabase.table("flags").insert(flag_data).execute()))

    # Count active flags for this target
    target_type = flag_data["target_type"]
    target_id = flag_data["target_id"]

    count_res = await run_sync(
        lambda: (
            supabase.table("flags")
            .select("id", count="exact")
            .limit(1)
            .eq("target_type", target_type)
            .eq("target_id", target_id)
            .eq("is_active", True)
            .execute()
        )
    )
    active_count = int(count_res.count) if hasattr(count_res, "count") and count_res.count is not None else 0

    auto_banned = False
    if active_count >= 3:
        ban_table = "users" if target_type == "rider" else "drivers"
        await run_sync(lambda: supabase.table(ban_table).update({"status": "banned"}).eq("id", target_id).execute())
        auto_banned = True

    return {"flag": flag, "active_flag_count": active_count, "auto_banned": auto_banned}


async def create_complaint(complaint_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Insert a complaint record."""
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    complaint_data = _serialize_for_api(complaint_data)
    return await run_sync(lambda: _single_row_from_res(supabase.table("complaints").insert(complaint_data).execute()))


async def resolve_complaint(complaint_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve or dismiss a complaint."""
    if not supabase:
        return None
    update_data = _serialize_for_api(update_data)
    return await run_sync(
        lambda: _single_row_from_res(supabase.table("complaints").update(update_data).eq("id", complaint_id).execute())
    )


async def create_lost_and_found(item_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Insert a lost and found report."""
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    item_data = _serialize_for_api(item_data)
    return await run_sync(lambda: _single_row_from_res(supabase.table("lost_and_found").insert(item_data).execute()))


async def update_lost_and_found(item_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update a lost and found item status."""
    if not supabase:
        return None
    update_data = _serialize_for_api(update_data)
    return await run_sync(
        lambda: _single_row_from_res(supabase.table("lost_and_found").update(update_data).eq("id", item_id).execute())
    )


async def get_ride_location_trail(ride_id: str) -> List[Dict[str, Any]]:
    """Get driver location trail for a specific ride."""
    if not supabase:
        return []
    return await run_sync(
        lambda: _rows_from_res(
            supabase.table("driver_location_history")
            .select("lat,lng,speed,heading,tracking_phase,timestamp")
            .eq("ride_id", ride_id)
            .order("timestamp")
            .limit(5000)
            .execute()
        )
    )


async def get_live_ride_data(ride_id: str) -> Optional[Dict[str, Any]]:
    """Get live ride data including current driver location."""
    if not supabase:
        return None

    ride = await run_sync(lambda: _single_row_from_res(supabase.table("rides").select("*").eq("id", ride_id).execute()))
    if not ride:
        return None

    driver_id = ride.get("driver_id")
    if driver_id:
        driver = await run_sync(
            lambda did=driver_id: _single_row_from_res(
                supabase.table("drivers")
                .select("name,phone,lat,lng,vehicle_make,vehicle_model,vehicle_color,license_plate,rating,photo_url")
                .eq("id", did)
                .execute()
            )
        )
        if driver:
            ride["driver_current_lat"] = driver.get("lat", 0)
            ride["driver_current_lng"] = driver.get("lng", 0)
            ride["driver_name"] = driver.get("name", "")
            ride["driver_phone"] = driver.get("phone", "")
            ride["driver_vehicle"] = f"{driver.get('vehicle_make', '')} {driver.get('vehicle_model', '')}".strip()
            ride["driver_license_plate"] = driver.get("license_plate", "")
            ride["driver_rating"] = driver.get("rating", 0)
            ride["driver_photo_url"] = driver.get("photo_url", "")

    rider_id = ride.get("rider_id")
    if rider_id:
        rider = await run_sync(
            lambda rid=rider_id: _single_row_from_res(
                supabase.table("users").select("first_name,last_name,phone").eq("id", rid).execute()
            )
        )
        if rider:
            ride["rider_name"] = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip()
            ride["rider_phone"] = rider.get("phone", "")

    return ride


async def get_user_status(user_id: str) -> Optional[str]:
    """Get user account status (active/suspended/banned)."""
    if not supabase:
        return None
    user = await run_sync(
        lambda: _single_row_from_res(supabase.table("users").select("status").eq("id", user_id).execute())
    )
    return user.get("status", "active") if user else None


async def get_driver_status_by_user(user_id: str) -> Optional[str]:
    """Get driver account status by user_id (active/suspended/banned)."""
    if not supabase:
        return None
    driver = await run_sync(
        lambda: _single_row_from_res(supabase.table("drivers").select("status").eq("user_id", user_id).execute())
    )
    return driver.get("status", "active") if driver else None


async def get_flags_for_target(target_type: str, target_id: str) -> List[Dict[str, Any]]:
    """Get all active flags for a rider or driver."""
    if not supabase:
        return []
    return await run_sync(
        lambda: _rows_from_res(
            supabase.table("flags")
            .select("*")
            .eq("target_type", target_type)
            .eq("target_id", target_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .execute()
        )
    )


# ── Stripe webhook idempotency ────────────────────────────────────────
# See migration 22_stripe_events.sql. These helpers back
# routes/webhooks.py's dedup path: Stripe retries every event until we
# return 2xx within 20s, so we MUST treat a replay of the same event.id
# as a no-op — otherwise we double-mark rides paid, double-credit
# wallets, and double-activate subscriptions.

# PostgreSQL unique_violation SQLSTATE — raised as part of the error
# string by postgrest-py when an INSERT conflicts with the PK.
_PG_UNIQUE_VIOLATION = "23505"


async def claim_stripe_event(event_id: str, event_type: str, payload: Dict[str, Any]) -> bool:
    """Atomically claim a Stripe webhook event for processing.

    Returns True if this call inserted the event row (caller should
    proceed to process it). Returns False if the event_id is already
    present (a retry — caller should return 200 without doing work).

    Raises if Supabase is unreachable or the error is not a unique
    violation — in that case the caller should return 5xx so Stripe
    retries later.
    """
    if not supabase:
        raise RuntimeError("Supabase client not configured — cannot persist stripe event")

    serialized_payload = _serialize_for_api(payload)

    def _fn() -> bool:
        try:
            supabase.table("stripe_events").insert(
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "payload": serialized_payload,
                }
            ).execute()
            return True
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if _PG_UNIQUE_VIOLATION in msg or "duplicate key" in msg or "already exists" in msg:
                # Check if the previous claim was actually completed. A row with
                # processed_at=NULL means a prior handler crashed mid-way and Stripe
                # is retrying — log a CRITICAL so the reconciliation alert fires, but
                # still return False (do not re-process automatically to avoid
                # double-charging). The ops team can replay via the admin endpoint.
                existing = (
                    supabase.table("stripe_events").select("processed_at").eq("event_id", event_id).limit(1).execute()
                )
                if existing.data and existing.data[0].get("processed_at") is None:
                    logger.critical(
                        "Stripe event %s is STUCK: claimed but never marked processed. Manual reconciliation required.",
                        event_id,
                    )
                else:
                    logger.info("Stripe event %s already processed — deduplicating", event_id)
                return False
            raise

    return await run_sync(_fn)


async def mark_stripe_event_processed(event_id: str) -> None:
    """Stamp processed_at=now() on a previously claimed stripe event row.

    Called after the handler has finished the business-logic work for
    an event. Failure here is non-fatal — the reconciliation job can
    still distinguish processed vs. stuck events by the presence of
    the updated_at stamp, and Stripe will not retry since we returned
    2xx. Log and swallow.
    """
    if not supabase:
        return

    def _fn():
        supabase.table("stripe_events").update({"processed_at": datetime.now(timezone.utc).isoformat()}).eq(
            "event_id", event_id
        ).execute()

    try:
        await run_sync(_fn)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to stamp processed_at on stripe event {event_id}: {e}")


# Corporate functions are now in repositories/corporate_repo.py — re-exported via the import block.

# ping() is now in repositories/_base.py — re-exported via the import block.
