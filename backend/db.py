import os
from typing import Any, Dict, Optional, List
from . import supabase_client
from . import db_supabase

supabase = getattr(supabase_client, 'supabase', None)


class Cursor:
    def __init__(self, table: str, _filter: Optional[Dict] = None, _sort: Optional[Dict] = None):
        self.table = table
        self.filter = _filter or {}
        self.sort_field = None
        self.sort_desc = False
        if _sort:
            self.sort_field = _sort.get('field')
            self.sort_desc = _sort.get('desc', False)

        # Internal state for async iteration
        self._results = None
        self._iterator = None

    def sort(self, field: str, order: int):
        self.sort_field = field
        self.sort_desc = (order == -1)
        return self

    async def to_list(self, limit: int = 100):
        # Special-case some collections where db_supabase has helpers
        if self.table == 'rides' and 'rider_id' in self.filter:
            return await db_supabase.get_rides_for_user(self.filter['rider_id'], limit=limit)

        # Generic table fetch
        if not supabase:
            return []
        q = supabase.table(self.table).select('*')
        for k, v in (self.filter or {}).items():
            if isinstance(v, dict) and '$in' in v:
                q = q.in_(k, v['$in'])
            else:
                q = q.eq(k, v)
        if self.sort_field:
            q = q.order(self.sort_field, desc=self.sort_desc)
        if limit:
            q = q.limit(limit)
        res = q.execute()
        return res.data or []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._results is None:
            # When iterating, fetch a batch (e.g. 1000)
            self._results = await self.to_list(limit=1000)
            self._iterator = iter(self._results)

        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class Collection:
    def __init__(self, name: str):
        self.name = name

    def find(self, _filter: Optional[Dict] = None):
        return Cursor(self.name, _filter)

    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        # Map common lookups to db_supabase helpers
        if not _filter:
            _filter = {}
        if self.name == 'users':
            if 'id' in _filter:
                return await db_supabase.get_user_by_id(_filter['id'])
            if 'phone' in _filter:
                return await db_supabase.get_user_by_phone(_filter['phone'])

        if self.name == 'drivers' and 'id' in _filter:
            return await db_supabase.get_driver_by_id(_filter['id'])

        if self.name == 'rides' and 'id' in _filter:
            return await db_supabase.get_ride(_filter['id'])

        # Generic fetch via supabase
        if not supabase:
            return None
        q = supabase.table(self.name).select('*')
        for k, v in _filter.items():
            q = q.eq(k, v)
        q = q.limit(1)
        res = q.execute()
        data = res.data or []
        return data[0] if data else None

    async def insert_one(self, doc: Dict[str, Any]):
        if self.name == 'users':
            return await db_supabase.create_user(doc)
        if self.name == 'rides':
            return await db_supabase.insert_ride(doc)
        if self.name == 'otp_records':
            return await db_supabase.insert_otp_record(doc)
        if not supabase:
            return None
        res = supabase.table(self.name).insert(doc).execute()
        return (res.data or [None])[0]

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any]):
        # Support {'$set': {...}} updates
        update_data = update.get('$set') if isinstance(update, dict) and '$set' in update else update
        if self.name == 'drivers' and 'id' in _filter:
            # Use db_supabase helper for some atomic operations
            if 'is_available' in update_data:
                # set availability
                return await db_supabase.set_driver_available(_filter['id'], update_data.get('is_available'))
            return supabase.table('drivers').update(update_data).eq('id', _filter['id']).execute()

        if self.name == 'users' and 'id' in _filter:
            return supabase.table('users').update(update_data).eq('id', _filter['id']).execute()

        if self.name == 'rides' and 'id' in _filter:
            return await db_supabase.update_ride(_filter['id'], update_data)

        # Generic update
        if not supabase:
            return None
        q = supabase.table(self.name)
        for k, v in _filter.items():
            q = q.eq(k, v)
        return q.update(update_data).execute()

    async def delete_one(self, _filter: Dict[str, Any]):
        if self.name == 'otp_records' and 'id' in _filter:
            return await db_supabase.delete_otp_record(_filter['id'])
        if not supabase:
            return None
        q = supabase.table(self.name)
        for k, v in _filter.items():
            q = q.eq(k, v)
        return q.delete().execute()

    async def delete_many(self, _filter: Dict[str, Any]):
        # Used for otp_records.delete_many({'phone': phone})
        if self.name == 'otp_records' and 'phone' in _filter:
            return await db_supabase.delete_otps_for_phone(_filter['phone'])
        if not supabase:
            return None
        q = supabase.table(self.name)
        for k, v in _filter.items():
            q = q.eq(k, v)
        return q.delete().execute()

    async def count_documents(self, _filter: Dict[str, Any]):
        if not supabase:
            return 0
        q = supabase.table(self.name).select('id', count='exact')
        # handle $in
        for k, v in (_filter or {}).items():
            if isinstance(v, dict) and '$in' in v:
                q = q.in_(k, v['$in'])
            else:
                q = q.eq(k, v)
        res = q.execute()
        return res.count or 0


# Expose a db-like object with collections used in server.py
class DB:
    def __init__(self):
        names = ['users','drivers','rides','otp_records','settings','saved_addresses','vehicle_types','service_areas','fare_configs']
        for n in names:
            setattr(self, n, Collection(n))


db = DB()
