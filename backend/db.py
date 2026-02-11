import os
from typing import Any, Dict, Optional, List
try:
    from . import db_supabase
except ImportError:
    import db_supabase

class Collection:
    def __init__(self, name: str):
        self.name = name

    class MockCursor:
        def __init__(self, collection_name, _filter, _sort=None):
            self.collection_name = collection_name
            self.filter = _filter
            self.sort_field = _sort.get('field') if _sort else None
            self.sort_desc = _sort.get('desc', False) if _sort else False

        def sort(self, field: str, order: int):
            self.sort_field = field
            self.sort_desc = (order == -1)
            return self

        async def to_list(self, limit: int = 100):
            if self.collection_name == 'rides' and 'rider_id' in (self.filter or {}):
                return await db_supabase.get_rides_for_user(self.filter['rider_id'], limit=limit)
            if self.collection_name == 'rides' and 'driver_id' in (self.filter or {}):
                # Handle status list filter if present
                statuses = None
                if 'status' in self.filter and isinstance(self.filter['status'], dict) and '$in' in self.filter['status']:
                    statuses = self.filter['status']['$in']
                return await db_supabase.get_rides_for_driver(self.filter['driver_id'], statuses=statuses, limit=limit)

            return await db_supabase.get_rows(
                self.collection_name,
                self.filter,
                order=self.sort_field,
                desc=self.sort_desc,
                limit=limit
            )

    def find(self, _filter: Optional[Dict] = None):
        return self.MockCursor(self.name, _filter)

    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        if not _filter:
            return None
        # Specialized lookups
        if self.name == 'users':
            if 'id' in _filter:
                return await db_supabase.get_user_by_id(_filter['id'])
            if 'phone' in _filter:
                return await db_supabase.get_user_by_phone(_filter['phone'])
        if self.name == 'drivers' and 'id' in _filter:
            return await db_supabase.get_driver_by_id(_filter['id'])
        if self.name == 'rides' and 'id' in _filter:
            return await db_supabase.get_ride(_filter['id'])
        if self.name == 'otp_records' and 'phone' in _filter and 'code' in _filter:
            return await db_supabase.get_otp_record(_filter['phone'], _filter['code'])

        # Generic lookup
        rows = await db_supabase.get_rows(self.name, _filter, limit=1)
        return rows[0] if rows else None

    async def insert_one(self, doc: Dict[str, Any]):
        if self.name == 'users':
            return await db_supabase.create_user(doc)
        if self.name == 'rides':
            return await db_supabase.insert_ride(doc)
        if self.name == 'otp_records':
            return await db_supabase.insert_otp_record(doc)

        # Generic insert
        if not db_supabase.supabase:
            return None
        res = db_supabase.supabase.table(self.name).insert(doc).execute()
        data = res.data
        return data[0] if data else None

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        update_data = update.get('$set') if isinstance(update, dict) and '$set' in update else update

        if self.name == 'drivers' and 'id' in _filter and 'lat' in update_data and 'lng' in update_data:
            # Use RPC for driver location update
            return await db_supabase.update_driver_location(_filter['id'], update_data['lat'], update_data['lng'])

        if self.name == 'drivers' and 'id' in _filter:
            # Use helper for availability if relevant
            if 'is_available' in update_data:
                return await db_supabase.set_driver_available(_filter['id'], update_data['is_available'])

        if self.name == 'otp_records' and 'id' in _filter and 'verified' in update_data:
            return await db_supabase.verify_otp_record(_filter['id'])

        if self.name == 'rides' and 'id' in _filter:
            return await db_supabase.update_ride(_filter['id'], update_data)

        # Generic update
        if not db_supabase.supabase:
            return None
        q = db_supabase.supabase.table(self.name)
        for k, v in _filter.items():
            q = q.eq(k, v)

        # Supabase-py update doesn't support upsert flag in .update(), only .upsert() method
        if upsert:
             # Upsert requires primary key or unique key in payload.
             # Merge filter into data if upserting
             payload = {**_filter, **update_data}
             return db_supabase.supabase.table(self.name).upsert(payload).execute()
        else:
             return q.update(update_data).execute()

    async def delete_one(self, _filter: Dict[str, Any]):
        if self.name == 'otp_records' and 'id' in _filter:
            return await db_supabase.delete_otp_record(_filter['id'])

        # Generic delete
        if not db_supabase.supabase:
            return None
        q = db_supabase.supabase.table(self.name)
        for k, v in _filter.items():
            q = q.eq(k, v)
        return q.delete().execute()

    async def delete_many(self, _filter: Dict[str, Any]):
        if self.name == 'otp_records' and 'phone' in _filter:
             # Custom logic: delete all OTPs for phone
             # Generic delete handles it if filter matches multiple rows
             pass

        if not db_supabase.supabase:
            return None
        q = db_supabase.supabase.table(self.name)
        for k, v in _filter.items():
            q = q.eq(k, v)
        return q.delete().execute()

    async def count_documents(self, _filter: Dict[str, Any]):
        return await db_supabase.count_documents(self.name, _filter)

    # Expose rpc
    async def rpc(self, func_name: str, params: Dict[str, Any]):
        if not db_supabase.supabase:
            return None
        res = db_supabase.supabase.rpc(func_name, params).execute()
        return db_supabase._rows_from_res(res)

class DB:
    def __init__(self):
        names = ['users','drivers','rides','otp_records','settings','saved_addresses','vehicle_types','service_areas','fare_configs']
        for n in names:
            setattr(self, n, Collection(n))

    async def rpc(self, func_name: str, params: Dict[str, Any]):
        if not db_supabase.supabase:
            return None
        res = db_supabase.supabase.rpc(func_name, params).execute()
        return db_supabase._rows_from_res(res)

db = DB()
