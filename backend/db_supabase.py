"""Simple Supabase-based data access helpers.

This module provides a small subset of the DB operations used by the server. It uses the
`supabase` client and runs blocking calls on a threadpool via `asyncio.to_thread` so they
can be used from async FastAPI handlers.

Note: These helpers are intentionally minimal — they are suitable for smoke testing and
incremental refactor. We keep behavior similar to the Mongo helpers used previously.
"""
import asyncio
from typing import Optional, List, Dict, Any
from .supabase_client import supabase


def _single_row_from_res(res: Any) -> Optional[Dict[str, Any]]:
    if not res:
        return None
    # Handle both dict-based responses and supabase APIResponse objects
    data = None
    if isinstance(res, dict):
        data = res.get('data')
    else:
        # supabase-py returns an APIResponse with .data attribute
        data = getattr(res, 'data', None)
    if not data:
        return None
    if isinstance(data, list):
        return data[0]
    return data


def _rows_from_res(res: Any) -> List[Dict[str, Any]]:
    if not res:
        return []
    if isinstance(res, dict):
        return res.get('data') or []
    return getattr(res, 'data', []) or []


async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('users').select('*').eq('id', user_id).execute()))


async def get_user_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('users').select('*').eq('phone', phone).execute()))


async def create_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not supabase:
        raise RuntimeError('Supabase client not configured')
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('users').insert(payload).execute()))


async def find_available_drivers(vehicle_type_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    if not supabase:
        return []
    def _fn():
        q = supabase.table('drivers').select('*').eq('is_online', True).eq('is_available', True)
        if vehicle_type_id:
            q = q.eq('vehicle_type_id', vehicle_type_id)
        q = q.limit(limit)
        return _rows_from_res(q.execute())

    return await asyncio.to_thread(_fn)


async def claim_driver_atomic(driver_id: str) -> bool:
    """Atomically set is_available = false for driver if currently available. Returns True if successful."""
    if not supabase:
        return False

    def _claim():
        # Update with conditional where is_available = true
        res = supabase.table('drivers').update({'is_available': False}).eq('id', driver_id).eq('is_available', True).execute()
        data = res.get('data') or []
        return len(data) > 0

    return await asyncio.to_thread(_claim)


async def set_driver_available(driver_id: str, available: bool = True, total_rides_inc: int = 0):
    if not supabase:
        return None

    def _update():
        payload = {'is_available': available}
        # Supabase PostgREST doesn't support incremental operators in the same request; use two-step if increment requested
        res = supabase.table('drivers').update(payload).eq('id', driver_id).execute()
        if total_rides_inc and total_rides_inc != 0:
            # Read current value and increment
            cur = supabase.table('drivers').select('total_rides').eq('id', driver_id).execute()
            cur_data = cur.get('data') or []
            cur_val = cur_data[0].get('total_rides', 0) if cur_data else 0
            supabase.table('drivers').update({'total_rides': cur_val + total_rides_inc}).eq('id', driver_id).execute()
        return _single_row_from_res(res)

    return await asyncio.to_thread(_update)


async def insert_ride(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError('Supabase client not configured')
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('rides').insert(payload).execute()))


async def get_ride(ride_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('rides').select('*').eq('id', ride_id).execute()))


# OTP helpers
async def insert_otp_record(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError('Supabase client not configured')
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('otp_records').insert(payload).execute()))


async def get_otp_record(phone: str, code: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('otp_records').select('*').eq('phone', phone).eq('code', code).eq('verified', False).execute()))


async def delete_otp_record(record_id: str):
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('otp_records').delete().eq('id', record_id).execute()))


async def verify_otp_record(record_id: str):
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('otp_records').update({'verified': True}).eq('id', record_id).execute()))


async def update_ride(ride_id: str, updates: Dict[str, Any]):
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('rides').update(updates).eq('id', ride_id).execute()))


async def get_driver_by_id(driver_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('drivers').select('*').eq('id', driver_id).execute()))


async def get_vehicle_type_by_id(vehicle_type_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(supabase.table('vehicle_types').select('*').eq('id', vehicle_type_id).execute()))


async def get_rides_for_user(rider_id: str, limit: int = 100):
    if not supabase:
        return []
    return await asyncio.to_thread(lambda: _rows_from_res(supabase.table('rides').select('*').eq('rider_id', rider_id).order('created_at', desc=True).limit(limit).execute()))


async def get_rides_for_driver(driver_id: str, statuses: Optional[List[str]] = None, limit: int = 100):
    if not supabase:
        return []

    def _fn():
        q = supabase.table('rides').select('*').eq('driver_id', driver_id)
        if statuses:
            # PostgREST doesn't support $in directly; build OR filter via or_()
            # supabase-py allows filter string in .or_()
            status_filters = ','.join([f"status.eq.{s}" for s in statuses])
            q = q.or_(status_filters)
        q = q.order('created_at', desc=True).limit(limit)
        return _rows_from_res(q.execute())

    return await asyncio.to_thread(_fn)


async def update_driver_location(driver_id: str, lat: float, lng: float):
    if not supabase:
        return None

    def _update():
        res = supabase.table('drivers').update({'lat': lat, 'lng': lng}).eq('id', driver_id).execute()
        return _single_row_from_res(res)

    return await asyncio.to_thread(_update)


# Generic helpers
def _apply_filters(q, filters: Optional[Dict[str, Any]]):
    if not filters:
        return q
    for k, v in filters.items():
        # support simple equality, list 'in' and boolean
        if isinstance(v, dict):
            if '$in' in v and isinstance(v['$in'], (list, tuple)):
                q = q.in_(k, list(v['$in']))
            elif '$gt' in v:
                q = q.gt(k, v['$gt'])
            elif '$lt' in v:
                q = q.lt(k, v['$lt'])
            else:
                # unsupported operator; skip
                continue
        else:
            q = q.eq(k, v)
    return q


async def count_documents(table: str, filters: Optional[Dict[str, Any]] = None) -> int:
    if not supabase:
        return 0

    def _fn():
        q = supabase.table(table).select('id', count='exact')
        q = _apply_filters(q, filters)
        res = q.execute()
        # APIResponse.data may be list
        data = getattr(res, 'data', None) or (res.get('data') if isinstance(res, dict) else None)
        if not data:
            return 0
        # PostgREST returns list of rows; count is in res.count if available
        if hasattr(res, 'count') and res.count is not None:
            return int(res.count)
        # Fallback: return length of data
        return len(data)

    return await asyncio.to_thread(_fn)


async def get_rows(table: str, filters: Optional[Dict[str, Any]] = None, order: Optional[str] = None, desc: bool = False, limit: Optional[int] = None):
    if not supabase:
        return []

    def _fn():
        q = supabase.table(table).select('*')
        q = _apply_filters(q, filters)
        if order:
            q = q.order(order, desc=desc)
        if limit:
            q = q.limit(limit)
        return _rows_from_res(q.execute())

    return await asyncio.to_thread(_fn)


async def get_all_drivers(limit: int = 1000):
    return await get_rows('drivers', None, order='created_at', desc=True, limit=limit)


async def get_all_rides(limit: int = 10000):
    return await get_rows('rides', None, order='created_at', desc=True, limit=limit)
