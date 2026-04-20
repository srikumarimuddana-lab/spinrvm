import asyncio
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import httpx as _httpx
    _HTTPX_TIMEOUT_EXC = _httpx.TimeoutException
except ImportError:  # pragma: no cover
    _httpx = None  # type: ignore
    _HTTPX_TIMEOUT_EXC = None  # type: ignore

try:
    from .supabase_client import supabase  # type: ignore
except ImportError:
    from supabase_client import supabase  # type: ignore

try:
    from .utils.error_handling import DatabaseError, DuplicateRecordError, ServiceUnavailableException  # type: ignore
except ImportError:
    from utils.error_handling import DatabaseError, DuplicateRecordError, ServiceUnavailableException  # type: ignore

import time as _time
from typing import Callable, TypeVar

from loguru import logger

T = TypeVar("T")


class _CircuitBreaker:
    """Half-open circuit breaker for the Supabase connection pool.

    Opens after FAILURE_THRESHOLD failures within WINDOW seconds.
    Allows one probe after OPEN_DURATION seconds (half-open).
    Closes on first success in any state.
    """

    FAILURE_THRESHOLD = 5
    WINDOW = 30.0
    OPEN_DURATION = 60.0

    def __init__(self) -> None:
        self._state = "closed"
        self._failure_times: list = []
        self._opened_at: float | None = None

    def should_allow(self) -> bool:
        now = _time.monotonic()
        if self._state == "closed":
            return True
        if self._state == "open":
            if self._opened_at is not None and now - self._opened_at >= self.OPEN_DURATION:
                self._state = "half_open"
                return True
            return False
        return True  # half_open: allow one probe

    def record_success(self) -> None:
        if self._state != "closed":
            logger.info(f"[DB] Circuit breaker CLOSED (was {self._state})")
        self._state = "closed"
        self._failure_times.clear()
        self._opened_at = None

    def record_failure(self) -> None:
        now = _time.monotonic()
        self._failure_times = [t for t in self._failure_times if now - t < self.WINDOW]
        self._failure_times.append(now)
        if self._state == "closed" and len(self._failure_times) >= self.FAILURE_THRESHOLD:
            self._state = "open"
            self._opened_at = now
            logger.error("[DB] Circuit breaker OPENED — raising ServiceUnavailableException on future calls")
        elif self._state == "half_open":
            self._state = "open"
            self._opened_at = now
            logger.warning("[DB] Circuit breaker probe failed — back to OPEN")


_breaker = _CircuitBreaker()


async def run_sync(func: Callable[[], T]) -> T:
    """Run a synchronous Supabase call in a thread and retry once on transient
    HTTP/2 connection errors. Two flavors show up in practice:
      - h2.ConnectionTerminated / GOAWAY when the stream limit is reached on
        a long-lived connection.
      - httpx.RemoteProtocolError("Server disconnected") when Supabase's edge
        drops the HTTP/2 connection mid-response (observed on Railway with
        larger result sets — e.g. the 5000-row heatmap query)."""
    if not _breaker.should_allow():
        raise ServiceUnavailableException("database")
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, func)  # type: ignore
        _breaker.record_success()
        return result
    except Exception as exc:
        exc_name = type(exc).__name__
        exc_str = str(exc)
        is_conn_terminated = "ConnectionTerminated" in exc_name or "ConnectionTerminated" in exc_str
        is_remote_disconnect = (
            "RemoteProtocolError" in exc_name
            or "Server disconnected" in exc_str
            or "ConnectionClosed" in exc_name
        )
        is_timeout = _HTTPX_TIMEOUT_EXC is not None and isinstance(exc, _HTTPX_TIMEOUT_EXC)
        if is_conn_terminated or is_remote_disconnect or is_timeout:
            logger.warning(f"Supabase transient failure ({exc_name}) — retrying once: {exc}")
            await asyncio.sleep(0.25)
            try:
                result = await loop.run_in_executor(None, func)  # type: ignore
                _breaker.record_success()
                return result
            except Exception as retry_exc:
                _breaker.record_failure()
                retry_str = str(retry_exc)
                retry_str_lower = retry_str.lower()
                if "duplicate key" in retry_str_lower or "unique constraint" in retry_str_lower or "23505" in retry_str:
                    raise DuplicateRecordError(details={"original": retry_str}) from retry_exc
                raise DatabaseError(details={"original": retry_str}) from retry_exc
        _breaker.record_failure()
        exc_str_lower = exc_str.lower()
        if "duplicate key" in exc_str_lower or "unique constraint" in exc_str_lower or "23505" in exc_str:
            raise DuplicateRecordError(details={"original": exc_str}) from exc
        raise DatabaseError(details={"original": exc_str}) from exc


def _serialize_for_api(data: Any) -> Any:
    """Recursively convert datetime/date objects to ISO format strings."""
    if isinstance(data, dict):
        return {k: _serialize_for_api(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_serialize_for_api(v) for v in data]
    if isinstance(data, (datetime, date)):
        return data.isoformat()
    return data


def _single_row_from_res(res: Any) -> Optional[Dict[str, Any]]:
    if not res:
        return None
    # Handle both dict-based responses and supabase APIResponse objects
    data = None
    if isinstance(res, dict):
        data = res.get("data")
    else:
        # supabase-py returns an APIResponse with .data attribute
        data = getattr(res, "data", None)

    if not data:
        return None

    if isinstance(data, list):
        return data[0] if len(data) > 0 else None
    return data


def _rows_from_res(res: Any) -> List[Dict[str, Any]]:
    if not res:
        return []

    data = None
    if isinstance(res, dict):
        data = res.get("data")
    else:
        data = getattr(res, "data", None)

    return data or []


# ============ Corporate Accounts Functions ============


async def get_all_corporate_accounts(
    skip: int = 0, limit: int = 100, search: Optional[str] = None, is_active: Optional[bool] = None
) -> List[Dict[str, Any]]:
    """
    Get all corporate accounts with optional filtering and pagination.

    Args:
        skip: Number of records to skip
        limit: Maximum number of records to return
        search: Search term for company name, contact name, or email
        is_active: Filter by active status

    Returns:
        List of corporate accounts
    """
    if not supabase:
        return []

    def _fn():
        query = supabase.table("corporate_accounts").select("*").range(skip, skip + limit - 1)

        if search:
            # Escape special PostgREST ilike characters to prevent filter injection
            safe = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            # Strip characters that could break PostgREST filter syntax
            safe = re.sub(r"[,\.\(\)]", "", safe)
            query = query.or_(f"name.ilike.%{safe}%,contact_name.ilike.%{safe}%,contact_email.ilike.%{safe}%")

        if is_active is not None:
            query = query.eq("is_active", is_active)

        query = query.order("created_at", desc=True)
        return _rows_from_res(query.execute())

    return await run_sync(_fn)


async def get_corporate_account_by_id(validated_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a corporate account by ID.

    Args:
        validated_id: Validated corporate account ID

    Returns:
        Corporate account data or None if not found
    """
    if not supabase:
        return None

    def _fn():
        try:
            res = supabase.table("corporate_accounts").select("*").eq("id", validated_id).single().execute()
            return _single_row_from_res(res)
        except Exception as e:
            # If no rows found, Supabase raises an exception
            logger.debug(f"No corporate account found with ID {validated_id}: {e}")
            return None

    return await run_sync(_fn)


async def insert_corporate_account(account_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Insert a new corporate account.

    Args:
        account_data: Corporate account data to insert

    Returns:
        Created corporate account data or None if failed
    """
    if not supabase:
        raise RuntimeError("Supabase client not configured")

    account_data = _serialize_for_api(account_data)

    def _fn():
        res = supabase.table("corporate_accounts").insert(account_data).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def update_corporate_account(account_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Update an existing corporate account.

    Args:
        account_id: ID of the account to update
        update_data: Data to update

    Returns:
        Updated corporate account data or None if failed
    """
    if not supabase:
        return None

    update_data = _serialize_for_api(update_data)

    def _fn():
        res = supabase.table("corporate_accounts").update(update_data).eq("id", account_id).execute()
        return _single_row_from_res(res)

    return await run_sync(_fn)


async def delete_corporate_account(account_id: str) -> bool:
    """
    Delete a corporate account.

    Args:
        account_id: ID of the account to delete

    Returns:
        True if successful, False otherwise
    """
    if not supabase:
        return False

    def _fn():
        res = supabase.table("corporate_accounts").delete().eq("id", account_id).execute()
        # If deletion was successful, affected rows will be > 0
        return res.count > 0 if res.count is not None else False

    return await run_sync(_fn)


# ============ User Helpers ============


async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(lambda: _single_row_from_res(
        supabase.table("users").select("*").eq("id", user_id).is_("deleted_at", "null").execute()
    ))


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
    return await run_sync(lambda: _single_row_from_res(supabase.table("users").insert(payload).execute()))


# ============ Driver Helpers ============


async def get_driver_by_id(driver_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(
        lambda: _single_row_from_res(
            supabase.table("drivers").select("*").eq("id", driver_id).is_("deleted_at", "null").execute()
        )
    )


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

        data = {"lat": lat, "lng": lng, "updated_at": datetime.utcnow().isoformat()}
        supabase.table("drivers").update(data).eq("id", str(driver_id)).execute()
        return True

    return await run_sync(_update)


async def set_driver_available(driver_id: str, available: bool = True, total_rides_inc: int = 0):
    if not supabase:
        logger.warning("[GO-ONLINE] set_driver_available: supabase client is None!")
        return None

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

    return await run_sync(_update)


async def claim_driver_atomic(driver_id: str) -> bool:
    """Atomically set is_available = false for driver if currently available."""
    if not supabase:
        return False

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

    return await run_sync(_claim)


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

    now_iso = datetime.utcnow().isoformat()

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
    return await run_sync(lambda: _single_row_from_res(
        supabase.table("rides").select("*").eq("id", ride_id).is_("deleted_at", "null").execute()
    ))


async def insert_ride(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    payload = _serialize_for_api(payload)
    return await run_sync(lambda: _single_row_from_res(supabase.table("rides").insert(payload).execute()))


async def update_ride(ride_id: str, updates: Dict[str, Any]):
    if not supabase:
        return None
    # Strip MongoDB-style $set wrapper if present
    updates = updates.get("$set", updates)
    updates = _serialize_for_api(updates)
    return await run_sync(
        lambda: _single_row_from_res(supabase.table("rides").update(updates).eq("id", ride_id).execute())
    )


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


async def get_rides_for_driver(driver_id: str, statuses: Optional[List[str]] = None, limit: int = 100):
    if not supabase:
        return []

    def _fn():
        q = supabase.table("rides").select("*").eq("driver_id", driver_id)
        if statuses:
            status_filters = ",".join([f"status.eq.{s}" for s in statuses])
            q = q.or_(status_filters)
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


# ============ Query Helpers ============


def _apply_filters(q, filters: Optional[Dict[str, Any]]):
    if not filters:
        return q
    for k, v in filters.items():
        if isinstance(v, dict):
            if "$in" in v and isinstance(v["$in"], (list, tuple)):
                q = q.in_(k, list(v["$in"]))
            elif "$gt" in v:
                q = q.gt(k, v["$gt"])
            elif "$gte" in v:
                q = q.gte(k, v["$gte"])
            elif "$lt" in v:
                q = q.lt(k, v["$lt"])
            elif "$lte" in v:
                q = q.lte(k, v["$lte"])
            elif "$ne" in v:
                q = q.neq(k, v["$ne"])
            elif "$nin" in v and isinstance(v["$nin"], (list, tuple)):
                # Supabase: not.in_(k, values)
                q = q.not_.in_(k, list(v["$nin"]))
            # Add more query operators as needed
        else:
            q = q.eq(k, v)
    return q


async def get_rows(
    table: str,
    filters: Optional[Dict[str, Any]] = None,
    order: Optional[str] = None,
    desc: bool = False,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
):
    if not supabase:
        return []

    def _fn():
        q = supabase.table(table).select("*")
        q = _apply_filters(q, filters)
        if order:
            q = q.order(order, desc=desc)
        if limit is not None and offset is not None:
            # Supabase .range is 0-based inclusive: range(offset, offset+limit-1)
            q = q.range(offset, offset + limit - 1)
        elif limit:
            q = q.limit(limit)
        elif offset is not None:
            q = q.offset(offset)
        return _rows_from_res(q.execute())

    return await run_sync(_fn)


async def count_documents(table: str, filters: Optional[Dict[str, Any]] = None) -> int:
    if not supabase:
        return 0

    def _fn():
        # count="exact" makes PostgREST include the total count in Content-Range.
        # We limit to 1 row so we don't fetch the full dataset; res.count still
        # reflects the total rows matching the filter (not the page size).
        # Note: head=True is NOT a valid parameter for select() in postgrest-py
        # 2.x — using it would raise TypeError and cause a 500 on every call.
        q = supabase.table(table).select("id", count="exact")
        q = _apply_filters(q, filters)
        q = q.limit(1)
        res = q.execute()
        if hasattr(res, "count") and res.count is not None:
            return int(res.count)
        return 0

    return await run_sync(_fn)


async def find_one(table: str, filters: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return the first row matching filters, or None."""
    rows = await get_rows(table, filters, limit=1)
    return rows[0] if rows else None


async def insert_one(table: str, doc: Dict[str, Any]):
    if not supabase:
        return None
    doc = _serialize_for_api(doc)
    return await run_sync(lambda: _single_row_from_res(supabase.table(table).insert(doc).execute()))


async def insert_many(table: str, docs: List[Dict[str, Any]]):
    """Bulk insert using Supabase's native batch insert (single round-trip)."""
    if not supabase or not docs:
        return []
    serialized = [_serialize_for_api(d) for d in docs]
    return await run_sync(lambda: _rows_from_res(supabase.table(table).insert(serialized).execute()))


async def update_one(table: str, filters: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
    if not supabase:
        if table == "drivers":
            logger.warning("[GO-ONLINE] db_supabase.update_one: supabase client is None!")
        return None

    def _fn():
        update_data = update.get("$set", update)
        update_data = _serialize_for_api(update_data)

        if table == "drivers":
            logger.info(
                f"[GO-ONLINE] db_supabase.update_one about to execute: "
                f"table={table} filters={filters} payload={update_data} upsert={upsert}"
            )

        if upsert:
            # Upsert requires merging filters and update data
            payload = {**filters, **update_data}
            res = supabase.table(table).upsert(payload).execute()
        else:
            q = supabase.table(table).update(update_data)
            q = _apply_filters(q, filters)
            res = q.execute()

        if table == "drivers":
            raw_data = None
            try:
                raw_data = getattr(res, "data", None) if res else None
            except Exception:
                raw_data = "<error reading res.data>"
            logger.info(
                f"[GO-ONLINE] db_supabase.update_one executed: "
                f"res_type={type(res).__name__ if res else 'None'} "
                f"res_data={raw_data}"
            )

        return _single_row_from_res(res)

    return await run_sync(_fn)


async def delete_many(table: str, filters: Dict[str, Any]):
    if not supabase:
        return None

    def _fn():
        q = supabase.table(table).delete()
        q = _apply_filters(q, filters)
        res = q.execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


async def delete_one(table: str, filters: Dict[str, Any]):
    # Note: Supabase delete is always "delete matching rows".
    # To delete strictly one, we'd need to limit, but delete doesn't support limit easily in basic client.
    # We'll just use delete_many logic but maybe warn if we wanted only one.
    return await delete_many(table, filters)


async def rpc(func_name: str, params: Dict[str, Any]):
    if not supabase:
        return None

    def _fn():
        res = supabase.rpc(func_name, params).execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


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

    # Fetch rider details
    rider_id = ride.get("rider_id")
    if rider_id:
        rider = await run_sync(
            lambda rid=rider_id: _single_row_from_res(
                supabase.table("users")
                .select("first_name,last_name,phone,email,profile_image,status,created_at")
                .eq("id", rid)
                .execute()
            )
        )
        if rider:
            ride["rider_name"] = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or rider_id[:12]
            ride["rider_phone"] = rider.get("phone", "")
            ride["rider_email"] = rider.get("email", "")
            ride["rider_profile_image"] = rider.get("profile_image", "")
            ride["rider_status"] = rider.get("status", "active")
            ride["rider_joined"] = rider.get("created_at", "")

        # Rider's service area (region) from most recent ride
        rider_area = await run_sync(
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
        rider_area_id = rider_area.get("service_area_id") if rider_area else None
        if rider_area_id:
            area = await run_sync(
                lambda aid=rider_area_id: _single_row_from_res(
                    supabase.table("service_areas").select("name,city").eq("id", aid).execute()
                )
            )
            ride["rider_region"] = area.get("name", "") if area else ""
            ride["rider_city"] = area.get("city", "") if area else ""
        else:
            ride["rider_region"] = ""
            ride["rider_city"] = ""

        # Rider's total past rides count
        rider_count_res = await run_sync(
            lambda rid=rider_id: (
                supabase.table("rides").select("id", count="exact").limit(1).eq("rider_id", rid).execute()
            )
        )
        ride["rider_total_rides"] = (
            int(rider_count_res.count) if hasattr(rider_count_res, "count") and rider_count_res.count is not None else 0
        )

    # Fetch driver details
    driver_id = ride.get("driver_id")
    if driver_id:
        driver = await run_sync(
            lambda did=driver_id: _single_row_from_res(
                supabase.table("drivers")
                .select(
                    "name,phone,vehicle_make,vehicle_model,vehicle_color,vehicle_year,vehicle_vin,license_plate,rating,status,photo_url,vehicle_type_id,total_rides,service_area_id"
                )
                .eq("id", did)
                .execute()
            )
        )
        if driver:
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

            # Driver region/service area
            driver_area_id = driver.get("service_area_id")
            if driver_area_id:
                d_area = await run_sync(
                    lambda aid=driver_area_id: _single_row_from_res(
                        supabase.table("service_areas").select("name,city").eq("id", aid).execute()
                    )
                )
                ride["driver_region"] = d_area.get("name", "") if d_area else ""
                ride["driver_city"] = d_area.get("city", "") if d_area else ""
            else:
                ride["driver_region"] = ""
                ride["driver_city"] = ""
            ride["driver_vehicle"] = f"{driver.get('vehicle_make', '')} {driver.get('vehicle_model', '')}".strip()
            ride["driver_total_rides"] = driver.get("total_rides", 0)

            # Compute acceptance rate: completed / (completed + cancelled as driver)
            vtype_id = driver.get("vehicle_type_id")
            if vtype_id:
                vtype = await run_sync(
                    lambda vid=vtype_id: _single_row_from_res(
                        supabase.table("vehicle_types").select("name,description,capacity").eq("id", vid).execute()
                    )
                )
                if vtype:
                    ride["driver_vehicle_type_name"] = vtype.get("name", "")
                    ride["driver_vehicle_capacity"] = vtype.get("capacity", 0)

            # Acceptance rate: total rides assigned to driver vs cancelled by driver
            driver_completed_res = await run_sync(
                lambda did=driver_id: (
                    supabase.table("rides")
                    .select("id", count="exact")
                    .limit(1)
                    .eq("driver_id", did)
                    .eq("status", "completed")
                    .execute()
                )
            )
            completed = (
                int(driver_completed_res.count)
                if hasattr(driver_completed_res, "count") and driver_completed_res.count is not None
                else 0
            )
            driver_total_assigned_res = await run_sync(
                lambda did=driver_id: (
                    supabase.table("rides").select("id", count="exact").limit(1).eq("driver_id", did).execute()
                )
            )
            total_assigned = (
                int(driver_total_assigned_res.count)
                if hasattr(driver_total_assigned_res, "count") and driver_total_assigned_res.count is not None
                else 0
            )
            ride["driver_acceptance_rate"] = round((completed / total_assigned * 100), 1) if total_assigned > 0 else 0
            ride["driver_completed_rides"] = completed

    # Fetch flags for both rider and driver
    flags = []
    if rider_id:
        rider_flags = await run_sync(
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
        flags.extend([{**f, "_party": "rider"} for f in rider_flags])
    if driver_id:
        driver_flags = await run_sync(
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
        flags.extend([{**f, "_party": "driver"} for f in driver_flags])
    ride["flags"] = flags
    ride["rider_flag_count"] = sum(1 for f in flags if f.get("_party") == "rider")
    ride["driver_flag_count"] = sum(1 for f in flags if f.get("_party") == "driver")

    # Fetch complaints for this ride
    ride["complaints"] = await run_sync(
        lambda: _rows_from_res(
            supabase.table("complaints").select("*").eq("ride_id", ride_id).order("created_at", desc=True).execute()
        )
    )

    # Fetch lost and found items for this ride
    ride["lost_and_found"] = await run_sync(
        lambda: _rows_from_res(
            supabase.table("lost_and_found").select("*").eq("ride_id", ride_id).order("created_at", desc=True).execute()
        )
    )

    # Fetch location trail for this ride
    ride["location_trail"] = await run_sync(
        lambda: _rows_from_res(
            supabase.table("driver_location_history")
            .select("lat,lng,speed,heading,tracking_phase,timestamp")
            .eq("ride_id", ride_id)
            .order("timestamp")
            .limit(5000)
            .execute()
        )
    )

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
                logger.info(f"Stripe event {event_id} already claimed — treating as duplicate")
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


# ── Corporate Accounts (B2B v1) ──────────────────────────────────────

async def list_corporate_accounts_filtered(
    *,
    status: Optional[str],
    size_tier: Optional[str],
    search: Optional[str],
    skip: int,
    limit: int,
) -> List[Dict[str, Any]]:
    """List corporate accounts with optional status / size-tier / name-search filters."""
    def _fn():
        q = supabase.table("corporate_accounts").select("*")
        if status:
            q = q.eq("status", status)
        if size_tier:
            q = q.eq("size_tier", size_tier)
        if search:
            # Escape PostgREST ilike special chars to prevent filter injection
            # (same pattern as get_all_corporate_accounts above).
            safe = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            safe = re.sub(r"[,\.\(\)]", "", safe)
            q = q.or_(f"name.ilike.%{safe}%,legal_name.ilike.%{safe}%")
        q = q.order("created_at", desc=True).range(skip, skip + limit - 1)
        return _rows_from_res(q.execute())
    return await run_sync(_fn)


async def update_corporate_account_status(company_id: str, status: str) -> Optional[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_accounts")
            .update({"status": status})
            .eq("id", company_id)
            .execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def record_kyb_decision(
    *,
    company_id: str,
    reviewer_id: str,
    approved: bool,
    note: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Record a KYB approve/reject decision. Approval flips status to active.

    Rejection flips status to suspended so the company can re-upload and be
    re-reviewed without creating a fresh account.
    """
    new_status = "active" if approved else "suspended"
    patch = {
        "status": new_status,
        "kyb_reviewed_at": datetime.now(timezone.utc).isoformat(),
        "kyb_reviewed_by": reviewer_id,
    }
    if note:
        patch["kyb_review_note"] = note  # column added in a follow-up migration if desired

    def _fn():
        res = (
            supabase.table("corporate_accounts")
            .update(patch)
            .eq("id", company_id)
            .execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def get_corporate_wallet_by_company(company_id: str) -> Optional[Dict[str, Any]]:
    """Return the master wallet row for a company, or None."""
    def _fn():
        res = (
            supabase.table("corporate_wallets")
            .select("*")
            .eq("company_id", company_id)
            .limit(1)
            .execute()
        )
        return _rows_from_res(res)

    rows = await run_sync(_fn)
    return rows[0] if rows else None


async def update_corporate_stripe_customer_id(
    *, company_id: str, stripe_customer_id: str
) -> None:
    """Persist the Stripe customer id for a corporate account."""
    def _fn():
        supabase.table("corporate_accounts").update(
            {"stripe_customer_id": stripe_customer_id}
        ).eq("id", company_id).execute()
    await run_sync(_fn)


async def ensure_corporate_wallet(*, company_id: str) -> Dict[str, Any]:
    """Idempotently create the master wallet for a company. Returns the row."""
    def _select():
        res = (
            supabase.table("corporate_wallets")
            .select("*")
            .eq("company_id", company_id)
            .limit(1)
            .execute()
        )
        return _rows_from_res(res)

    existing = await run_sync(_select)
    if existing:
        return existing[0]

    def _insert():
        res = (
            supabase.table("corporate_wallets")
            .insert({"company_id": company_id, "balance": 0, "currency": "CAD"})
            .execute()
        )
        return _single_row_from_res(res)

    created = await run_sync(_insert)
    return created or {}


async def get_corporate_members_for_user(user_id: str) -> List[Dict[str, Any]]:
    """Return all corporate_members rows for a user where status='active'.

    Hot path: called on every work-profile check.
    """
    def _fn():
        res = (
            supabase.table("corporate_members")
            .select("id, company_id, role, policy_override")
            .eq("user_id", user_id)
            .eq("status", "active")
            .execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


_KYB_CONTENT_EXT = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
}


async def create_kyb_upload_url(
    *, company_id: str, content_type: str, ttl_seconds: int = 3600
) -> Dict[str, Any]:
    """Return a short-lived signed upload URL for a KYB document.

    The bucket 'kyb-documents' is private; the caller uploads with the
    returned URL and we later record the object path on the corporate
    account when review completes.
    """
    import uuid
    from datetime import timedelta

    ext = _KYB_CONTENT_EXT[content_type]
    path = f"kyb/{company_id}/{uuid.uuid4()}.{ext}"

    def _fn():
        return supabase.storage.from_("kyb-documents").create_signed_upload_url(path)

    signed = await run_sync(_fn)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    return {
        "signed_url": signed["signed_url"],
        "path": signed.get("path", path),
        "expires_at": expires_at,
    }


async def list_wallets_needing_autotopup() -> List[Dict[str, Any]]:
    """Return wallets where auto_topup_enabled and balance < threshold.

    Threshold is filtered in Python because supabase-py doesn't support
    cross-column comparisons in .filter().
    """
    def _fn():
        return (
            supabase.table("corporate_wallets")
            .select("*")
            .eq("auto_topup_enabled", True)
            .execute()
        )

    res = await run_sync(_fn)
    rows = _rows_from_res(res)
    return [
        r
        for r in rows
        if r.get("auto_topup_threshold") is not None
        and float(r["balance"]) < float(r["auto_topup_threshold"])
    ]


async def sum_autotopups_today(wallet_id: str) -> float:
    """Sum of today's successful top-ups for a wallet (for daily-cap enforcement)."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    def _fn():
        return (
            supabase.table("corporate_wallet_transactions")
            .select("amount")
            .eq("wallet_id", wallet_id)
            .eq("type", "topup")
            .gte("created_at", today_start.isoformat())
            .execute()
        )

    res = await run_sync(_fn)
    rows = _rows_from_res(res)
    return sum(float(r["amount"]) for r in rows)


async def get_default_payment_method(
    stripe_customer_id: str, stripe_secret: str
) -> Optional[str]:
    """Return the Stripe customer's first card payment method, if any."""
    import stripe

    def _fn():
        return stripe.PaymentMethod.list(
            customer=stripe_customer_id, type="card", api_key=stripe_secret
        )

    methods = await run_sync(_fn)
    data = getattr(methods, "data", None) or []
    return data[0].id if data else None


async def list_wallets_low_balance_no_autotopup() -> List[Dict[str, Any]]:
    """Wallets with auto-topup disabled whose balance has dipped below threshold."""
    def _fn():
        return (
            supabase.table("corporate_wallets")
            .select("*")
            .eq("auto_topup_enabled", False)
            .execute()
        )

    res = await run_sync(_fn)
    rows = _rows_from_res(res)
    return [
        r
        for r in rows
        if r.get("auto_topup_threshold") is not None
        and float(r["balance"]) < float(r["auto_topup_threshold"])
    ]


async def mark_low_balance_notified(*, wallet_id: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()

    def _fn():
        supabase.table("corporate_wallets").update(
            {"low_balance_notified_at": now_iso}
        ).eq("id", wallet_id).execute()

    await run_sync(_fn)


async def list_wallet_transactions(
    *, wallet_id: str, skip: int = 0, limit: int = 50
) -> List[Dict[str, Any]]:
    """Return the most recent ledger entries for a wallet, newest first."""
    upper = skip + max(limit, 1) - 1

    def _fn():
        return (
            supabase.table("corporate_wallet_transactions")
            .select("*")
            .eq("wallet_id", wallet_id)
            .order("created_at", desc=True)
            .range(skip, upper)
            .execute()
        )

    res = await run_sync(_fn)
    return _rows_from_res(res)


async def update_corporate_wallet_config(
    *, wallet_id: str, patch: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Patch one or more configuration columns on a corporate_wallets row."""
    def _fn():
        res = (
            supabase.table("corporate_wallets")
            .update(patch)
            .eq("id", wallet_id)
            .execute()
        )
        return _single_row_from_res(res)

    return await run_sync(_fn)


# ============================================================
# Corporate B2B Plan 3 — members, allowances, requests, domains
# ============================================================

# ---------- Members ----------
async def insert_corporate_member_invite(
    *,
    company_id: str,
    email: str,
    role: str,
    invite_token: str,
    invited_by: str,
    policy_override: bool = False,
) -> Dict[str, Any]:
    def _fn():
        res = (
            supabase.table("corporate_members")
            .insert({
                "company_id": company_id,
                "invited_email": email,
                "role": role,
                "invite_token": invite_token,
                "invited_at": datetime.utcnow().isoformat(),
                "invited_by": invited_by,
                "policy_override": policy_override,
                "status": "invited",
            })
            .execute()
        )
        return _single_row_from_res(res) or {}
    return await run_sync(_fn)


async def list_company_members(
    *,
    company_id: str,
    statuses: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    def _fn():
        q = supabase.table("corporate_members").select("*").eq("company_id", company_id)
        if statuses:
            q = q.in_("status", statuses)
        res = q.order("created_at", desc=False).execute()
        return _rows_from_res(res)
    return await run_sync(_fn)


async def get_corporate_member_by_id(member_id: str) -> Optional[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_members")
            .select("*").eq("id", member_id).limit(1).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def get_member_by_invite_token(token: str) -> Optional[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_members")
            .select("*").eq("invite_token", token).limit(1).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def list_active_memberships_for_user(user_id: str) -> List[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_members")
            .select("*").eq("user_id", user_id).eq("status", "active").execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def update_corporate_member(
    member_id: str, patch: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    if not patch:
        return await get_corporate_member_by_id(member_id)
    patch = {**patch, "updated_at": datetime.utcnow().isoformat()}
    def _fn():
        res = (
            supabase.table("corporate_members")
            .update(patch).eq("id", member_id).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def accept_member_invite(
    *, member_id: str, user_id: str
) -> Optional[Dict[str, Any]]:
    """Atomically flip invited → active and stamp user_id + joined_at.

    Guarded by `.eq("status", "invited")` so we only flip pending invites,
    preventing replay against an already-consumed token.
    """
    patch = {
        "status": "active",
        "user_id": user_id,
        "joined_at": datetime.utcnow().isoformat(),
        "invite_token": None,
        "updated_at": datetime.utcnow().isoformat(),
    }
    def _fn():
        res = (
            supabase.table("corporate_members")
            .update(patch)
            .eq("id", member_id)
            .eq("status", "invited")
            .execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


# ---------- Allowances ----------
async def get_member_allowance(member_id: str) -> Optional[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_member_allowances")
            .select("*").eq("member_id", member_id).limit(1).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def upsert_member_allowance(
    *, member_id: str, patch: Dict[str, Any]
) -> Dict[str, Any]:
    """Insert if no allowance row exists, else update. Returns the row."""
    existing = await get_member_allowance(member_id)
    if existing:
        def _upd():
            res = (
                supabase.table("corporate_member_allowances")
                .update({**patch, "updated_at": datetime.utcnow().isoformat()})
                .eq("id", existing["id"])
                .execute()
            )
            return _single_row_from_res(res) or existing
        return await run_sync(_upd)

    def _ins():
        res = (
            supabase.table("corporate_member_allowances")
            .insert({"member_id": member_id, "used": 0, "status": "active", **patch})
            .execute()
        )
        return _single_row_from_res(res) or {}
    return await run_sync(_ins)


async def list_company_allowances(company_id: str) -> List[Dict[str, Any]]:
    """Join allowances with their members, scoped to one company."""
    def _fn():
        res = (
            supabase.table("corporate_member_allowances")
            .select(
                "*, member:corporate_members!inner"
                "(id,company_id,user_id,invited_email,status,role)"
            )
            .eq("member.company_id", company_id)
            .execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def list_allowances_due_for_reset(as_of: str) -> List[Dict[str, Any]]:
    """Active fixed_recurring allowances whose period_end < as_of (ISO date)."""
    def _fn():
        res = (
            supabase.table("corporate_member_allowances")
            .select("*")
            .eq("type", "fixed_recurring")
            .eq("status", "active")
            .lt("period_end", as_of)
            .execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def reset_allowance_period(
    *, allowance_id: str, period_start: str, period_end: str
) -> Optional[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_member_allowances")
            .update({
                "period_start": period_start,
                "period_end": period_end,
                "auto_approved_this_period": 0,
                "updated_at": datetime.utcnow().isoformat(),
            })
            .eq("id", allowance_id)
            .execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


# ---------- Allowance requests ----------
async def insert_allowance_request(
    *, member_id: str, amount: float, reason: str, status: str = "pending"
) -> Dict[str, Any]:
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .insert({
                "member_id": member_id,
                "amount": amount,
                "reason": reason,
                "status": status,
            })
            .execute()
        )
        return _single_row_from_res(res) or {}
    return await run_sync(_fn)


async def list_pending_allowance_requests_for_member(
    member_id: str,
) -> List[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .select("*")
            .eq("member_id", member_id)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def list_company_allowance_requests(
    company_id: str, statuses: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    def _fn():
        q = (
            supabase.table("corporate_allowance_requests")
            .select(
                "*, member:corporate_members!inner"
                "(id,company_id,invited_email,user_id)"
            )
            .eq("member.company_id", company_id)
        )
        if statuses:
            q = q.in_("status", statuses)
        res = q.order("created_at", desc=True).execute()
        return _rows_from_res(res)
    return await run_sync(_fn)


async def get_allowance_request_by_id(
    request_id: str,
) -> Optional[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .select("*").eq("id", request_id).limit(1).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


async def update_allowance_request(
    *,
    request_id: str,
    status: str,
    reviewed_by: Optional[str],
    decision_notes: Optional[str],
) -> Optional[Dict[str, Any]]:
    patch = {
        "status": status,
        "reviewed_by": reviewed_by,
        "reviewed_at": datetime.utcnow().isoformat(),
        "decision_notes": decision_notes,
    }
    def _fn():
        res = (
            supabase.table("corporate_allowance_requests")
            .update(patch).eq("id", request_id).execute()
        )
        return _single_row_from_res(res)
    return await run_sync(_fn)


# ---------- Allowed domains ----------
async def add_allowed_domain(
    *, company_id: str, domain: str
) -> Dict[str, Any]:
    def _fn():
        res = (
            supabase.table("corporate_allowed_domains")
            .insert({"company_id": company_id, "domain": domain})
            .execute()
        )
        return _single_row_from_res(res) or {"company_id": company_id, "domain": domain}
    return await run_sync(_fn)


async def list_allowed_domains(company_id: str) -> List[Dict[str, Any]]:
    def _fn():
        res = (
            supabase.table("corporate_allowed_domains")
            .select("*").eq("company_id", company_id).execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)


async def delete_allowed_domain(*, company_id: str, domain: str) -> None:
    def _fn():
        supabase.table("corporate_allowed_domains").delete().eq(
            "company_id", company_id
        ).eq("domain", domain).execute()
    await run_sync(_fn)


async def find_companies_by_email_domain(domain: str) -> List[Dict[str, Any]]:
    """Active companies that whitelist this email domain for auto-match."""
    def _fn():
        res = (
            supabase.table("corporate_allowed_domains")
            .select(
                "company_id, corporate_accounts:corporate_accounts!inner"
                "(id,name,status)"
            )
            .eq("domain", domain)
            .eq("corporate_accounts.status", "active")
            .execute()
        )
        return _rows_from_res(res)
    return await run_sync(_fn)
