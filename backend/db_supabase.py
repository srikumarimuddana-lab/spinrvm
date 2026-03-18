import re
import asyncio
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date

try:
    from .supabase_client import supabase  # type: ignore
except ImportError:
    from supabase_client import supabase  # type: ignore

from loguru import logger

from typing import TypeVar, Callable

T = TypeVar('T')

async def run_sync(func: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func)  # type: ignore

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

# ============ Corporate Accounts Functions ============

async def get_all_corporate_accounts(
    skip: int = 0, 
    limit: int = 100, 
    search: Optional[str] = None, 
    is_active: Optional[bool] = None
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
        query = supabase.table('corporate_accounts').select('*').range(skip, skip + limit - 1)
        
        if search:
            # Search in name, contact_name, and contact_email
            # Using ilike for case-insensitive search
            query = query.or_(
                f"name.ilike.%{search}%,contact_name.ilike.%{search}%,contact_email.ilike.%{search}%"
            )
        
        if is_active is not None:
            query = query.eq('is_active', is_active)
        
        query = query.order('created_at', desc=True)
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
            res = supabase.table('corporate_accounts').select('*').eq('id', validated_id).single().execute()
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
        raise RuntimeError('Supabase client not configured')
    
    account_data = _serialize_for_api(account_data)
    
    def _fn():
        res = supabase.table('corporate_accounts').insert(account_data).execute()
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
        res = supabase.table('corporate_accounts').update(update_data).eq('id', account_id).execute()
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
        res = supabase.table('corporate_accounts').delete().eq('id', account_id).execute()
        # If deletion was successful, affected rows will be > 0
        return res.count > 0 if res.count is not None else False
    
    return await run_sync(_fn)

# ============ User Helpers ============

async def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('users').select('*').eq('id', user_id).execute()
    ))

async def get_user_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('users').select('*').eq('phone', phone).execute()
    ))

async def create_user(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not supabase:
        raise RuntimeError('Supabase client not configured')
    payload = _serialize_for_api(payload)
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('users').insert(payload).execute()
    ))

# ============ Driver Helpers ============

async def get_driver_by_id(driver_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(lambda: _single_row_from_res(
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
        
        data = {'lat': lat, 'lng': lng, 'updated_at': datetime.utcnow().isoformat()}
        supabase.table('drivers').update(data).eq('id', str(driver_id)).execute()
        return True

    return await run_sync(_update)

async def set_driver_available(driver_id: str, available: bool = True, total_rides_inc: int = 0):
    if not supabase:
        return None

    def _update():
        payload: Dict[str, Any] = {'is_available': available}
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

    return await run_sync(_update)

async def claim_driver_atomic(driver_id: str) -> bool:
    """Atomically set is_available = false for driver if currently available."""
    if not supabase:
        return False

    def _claim():
        res = supabase.table('drivers').update({'is_available': False}).eq('id', driver_id).eq('is_available', True).execute()
        data = _rows_from_res(res)
        return len(data) > 0

    return await run_sync(_claim)

# ============ Ride Helpers ============

async def get_ride(ride_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('rides').select('*').eq('id', ride_id).execute()
    ))

async def insert_ride(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError('Supabase client not configured')
    payload = _serialize_for_api(payload)
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('rides').insert(payload).execute()
    ))

async def update_ride(ride_id: str, updates: Dict[str, Any]):
    if not supabase:
        return None
    updates = _serialize_for_api(updates)
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('rides').update(updates).eq('id', ride_id).execute()
    ))

async def get_rides_for_user(rider_id: str, limit: int = 100):
    if not supabase:
        return []
    return await run_sync(lambda: _rows_from_res(
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

    return await run_sync(_fn)

# ============ OTP Helpers ============

async def insert_otp_record(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError('Supabase client not configured')
    payload = _serialize_for_api(payload)
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('otp_records').insert(payload).execute()
    ))

async def get_otp_record(phone: str, code: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('otp_records').select('*').eq('phone', phone).eq('code', code).eq('verified', False).execute()
    ))

async def verify_otp_record(record_id: str):
    if not supabase:
        return None
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('otp_records').update({'verified': True}).eq('id', record_id).execute()
    ))

async def delete_otp_record(record_id: str):
    if not supabase:
        return None
    return await run_sync(lambda: _single_row_from_res(
        supabase.table('otp_records').delete().eq('id', record_id).execute()
    ))

# ============ Query Helpers ============

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
            # Add more query operators as needed
        else:
            q = q.eq(k, v)
    return q

async def get_rows(table: str, filters: Optional[Dict[str, Any]] = None, order: Optional[str] = None, desc: bool = False, limit: Optional[int] = None, offset: Optional[int] = None):
    if not supabase:
        return []

    def _fn():
        q = supabase.table(table).select('*')
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
        q = supabase.table(table).select('id', count='exact', head=True)
        q = _apply_filters(q, filters)
        res = q.execute()
        if hasattr(res, 'count') and res.count is not None:
            return int(res.count)
        return 0

    return await run_sync(_fn)

async def insert_one(table: str, doc: Dict[str, Any]):
    if not supabase:
        return None
    doc = _serialize_for_api(doc)
    return await run_sync(lambda: _single_row_from_res(
        supabase.table(table).insert(doc).execute()
    ))

async def update_one(table: str, filters: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
    if not supabase:
        return None

    def _fn():
        update_data = update.get('$set', update)
        update_data = _serialize_for_api(update_data)

        if upsert:
            # Upsert requires merging filters and update data
            payload = {**filters, **update_data}
            res = supabase.table(table).upsert(payload).execute()
        else:
            q = supabase.table(table).update(update_data)
            q = _apply_filters(q, filters)
            res = q.execute()

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


async def execute_query(query: str, params: Optional[Dict[str, Any]] = None):
    """
    Execute a raw SQL SELECT query and return all rows.
    Uses Supabase's raw API to execute queries.
    
    Args:
        query: SQL query string (e.g., 'SELECT * FROM settings')
        params: Optional dictionary of query parameters
        
    Returns:
        List of dictionaries representing rows
    """
    if not supabase:
        return []
    
    def _fn():
        try:
            # Use Supabase's raw() method for SELECT queries
            response = supabase.rpc('exec_sql', {'query': query, 'params': params or {}})
            if hasattr(response, 'execute'):
                result = response.execute()
                return result.data if result.data else []
            return response.data if response.data else []
        except Exception as e:
            logger.warning(f"execute_query warning: {e}")
            return []
    
    return await run_sync(_fn)


async def execute_write(query: str, params: Optional[Dict[str, Any]] = None):
    """
    Execute a raw SQL INSERT, UPDATE, or DELETE query.
    Uses Supabase's raw API to execute queries.
    
    Args:
        query: SQL query string (e.g., 'INSERT INTO settings (key, value) VALUES ($1, $2)')
        params: Optional dictionary of query parameters
        
    Returns:
        Dictionary with execution results
    """
    if not supabase:
        return {'success': False, 'error': 'No supabase connection'}
    
    def _fn():
        try:
            # Use Supabase's raw() method for write queries
            response = supabase.rpc('exec_sql', {'query': query, 'params': params or {}})
            if hasattr(response, 'execute'):
                result = response.execute()
                return {'success': True, 'data': result.data}
            return {'success': True, 'data': response.data}
        except Exception as e:
            logger.warning(f"execute_write warning: {e}")
            return {'success': False, 'error': str(e)}
    
    return await run_sync(_fn)