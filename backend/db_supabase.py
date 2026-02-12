"""Supabase data access helpers.

This module provides DB operations used by the server. It uses the
`supabase` client and runs blocking calls on a threadpool via `asyncio.to_thread` so they
can be used from async FastAPI handlers.
"""
import asyncio
import logging
from typing import Optional, List, Dict, Any, Union
try:
    from .supabase_client import supabase
except ImportError:
    from supabase_client import supabase

logger = logging.getLogger(__name__)

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
        return data[0] if len(data) > 0 else None
    return data

def _rows_from_res(res: Any) -> List[Dict[str, Any]]:
    if not res:
        return []

    data = None
    if isinstance(res, dict):
        data = res.get('data')
    else:
        data = getattr(res, 'data', None)

    return data or []

# ============ User Helpers ============

async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('users').select('*').eq('id', user_id).execute()
    ))

async def get_user_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('users').select('*').eq('phone', phone).execute()
    ))

async def create_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not supabase:
        raise RuntimeError('Supabase client not configured')
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('users').insert(payload).execute()
    ))

# ============ Driver Helpers ============

async def get_driver_by_id(driver_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('drivers').select('*').eq('id', driver_id).execute()
    ))

async def find_nearby_drivers(lat: float, lng: float, radius_meters: float) -> List[Dict[str, Any]]:
    """Use PostGIS RPC to find nearby drivers."""
    if not supabase:
        return []

    def _fn():
        res = supabase.rpc('find_nearby_drivers', {
            'lat': lat,
            'lng': lng,
            'radius_meters': radius_meters
        }).execute()
        return _rows_from_res(res)

    return await asyncio.to_thread(_fn)

async def update_driver_location(driver_id: str, lat: float, lng: float):
    if not supabase:
        return None

    def _update():
        supabase.rpc('update_driver_location', {
            'driver_id': driver_id,
            'lat': lat,
            'lng': lng
        }).execute()
        return True

    return await asyncio.to_thread(_update)

async def set_driver_available(driver_id: str, available: bool = True, total_rides_inc: int = 0):
    if not supabase:
        return None

    def _update():
        payload = {'is_available': available}
        if total_rides_inc == 0:
            res = supabase.table('drivers').update(payload).eq('id', driver_id).execute()
            return _single_row_from_res(res)

        # If increment needed, read then write (simulated atomic)
        # Ideally this should be an RPC or a better query if Supabase supported $inc
        cur = supabase.table('drivers').select('total_rides').eq('id', driver_id).execute()
        cur_data = _rows_from_res(cur)
        cur_val = cur_data[0].get('total_rides', 0) if cur_data else 0

        payload['total_rides'] = cur_val + total_rides_inc
        res = supabase.table('drivers').update(payload).eq('id', driver_id).execute()
        return _single_row_from_res(res)

    return await asyncio.to_thread(_update)

async def claim_driver_atomic(driver_id: str) -> bool:
    """Atomically set is_available = false for driver if currently available."""
    if not supabase:
        return False

    def _claim():
        res = supabase.table('drivers').update({'is_available': False}).eq('id', driver_id).eq('is_available', True).execute()
        data = _rows_from_res(res)
        return len(data) > 0

    return await asyncio.to_thread(_claim)

# ============ Ride Helpers ============

async def get_ride(ride_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('rides').select('*').eq('id', ride_id).execute()
    ))

async def insert_ride(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError('Supabase client not configured')
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('rides').insert(payload).execute()
    ))

async def update_ride(ride_id: str, updates: Dict[str, Any]):
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('rides').update(updates).eq('id', ride_id).execute()
    ))

async def get_rides_for_user(rider_id: str, limit: int = 100):
    if not supabase:
        return []
    return await asyncio.to_thread(lambda: _rows_from_res(
        supabase.table('rides').select('*').eq('rider_id', rider_id).order('created_at', desc=True).limit(limit).execute()
    ))

async def get_rides_for_driver(driver_id: str, statuses: Optional[List[str]] = None, limit: int = 100):
    if not supabase:
        return []

    def _fn():
        q = supabase.table('rides').select('*').eq('driver_id', driver_id)
        if statuses:
            status_filters = ','.join([f"status.eq.{s}" for s in statuses])
            q = q.or_(status_filters)
        q = q.order('created_at', desc=True).limit(limit)
        return _rows_from_res(q.execute())

    return await asyncio.to_thread(_fn)

# ============ OTP Helpers ============

async def insert_otp_record(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError('Supabase client not configured')
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('otp_records').insert(payload).execute()
    ))

async def get_otp_record(phone: str, code: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('otp_records').select('*').eq('phone', phone).eq('code', code).eq('verified', False).execute()
    ))

async def verify_otp_record(record_id: str):
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('otp_records').update({'verified': True}).eq('id', record_id).execute()
    ))

async def delete_otp_record(record_id: str):
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table('otp_records').delete().eq('id', record_id).execute()
    ))

# ============ Generic Helpers (for Mongo compatibility) ============

def _apply_filters(q, filters: Optional[Dict[str, Any]]):
    if not filters:
        return q
    for k, v in filters.items():
        if isinstance(v, dict):
            if '$in' in v and isinstance(v['$in'], (list, tuple)):
                q = q.in_(k, list(v['$in']))
            elif '$gt' in v:
                q = q.gt(k, v['$gt'])
            elif '$gte' in v:
                q = q.gte(k, v['$gte'])
            elif '$lt' in v:
                q = q.lt(k, v['$lt'])
            elif '$lte' in v:
                q = q.lte(k, v['$lte'])
            elif '$ne' in v:
                q = q.neq(k, v['$ne'])
            # Add more mongo operators as needed
        else:
            q = q.eq(k, v)
    return q

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

async def count_documents(table: str, filters: Optional[Dict[str, Any]] = None) -> int:
    if not supabase:
        return 0

    def _fn():
        q = supabase.table(table).select('id', count='exact', head=True)
        q = _apply_filters(q, filters)
        res = q.execute()
        if hasattr(res, 'count') and res.count is not None:
            return int(res.count)
        return 0

    return await asyncio.to_thread(_fn)

async def insert_one(table: str, doc: Dict[str, Any]):
    if not supabase:
        return None
    return await asyncio.to_thread(lambda: _single_row_from_res(
        supabase.table(table).insert(doc).execute()
    ))

async def update_one(table: str, filters: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
    if not supabase:
        return None

    def _fn():
        update_data = update.get('$set', update)

        if upsert:
            # Upsert requires merging filters and update data
            payload = {**filters, **update_data}
            res = supabase.table(table).upsert(payload).execute()
        else:
            q = supabase.table(table).update(update_data)
            q = _apply_filters(q, filters)
            res = q.execute()

        return _single_row_from_res(res)

    return await asyncio.to_thread(_fn)

async def delete_many(table: str, filters: Dict[str, Any]):
    if not supabase:
        return None

    def _fn():
        q = supabase.table(table).delete()
        q = _apply_filters(q, filters)
        res = q.execute()
        return _rows_from_res(res)

    return await asyncio.to_thread(_fn)

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

    return await asyncio.to_thread(_fn)
