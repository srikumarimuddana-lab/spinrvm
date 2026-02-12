import os
from typing import Any, Dict, Optional, List, Union
try:
    from . import db_supabase
except ImportError:
    import db_supabase

class MockCursor:
    def __init__(self, collection_name: str, _filter: Optional[Dict], _sort: Optional[Dict] = None):
        self.collection_name = collection_name
        self.filter = _filter
        self.sort_field = _sort.get('field') if _sort else None
        self.sort_desc = _sort.get('desc', False) if _sort else False

    def sort(self, field: str, order: int):
        self.sort_field = field
        self.sort_desc = (order == -1)
        return self

    def limit(self, limit: int):
        # Store limit if needed, but for now to_list takes limit
        self._limit = limit
        return self

    async def to_list(self, limit: int = 100):
        # Override limit if set by .limit()
        if hasattr(self, '_limit'):
            limit = self._limit

        if self.collection_name == 'rides' and 'rider_id' in (self.filter or {}):
            return await db_supabase.get_rides_for_user(self.filter['rider_id'], limit=limit)

        if self.collection_name == 'rides' and 'driver_id' in (self.filter or {}):
            # Handle status list filter if present
            statuses = None
            if self.filter and 'status' in self.filter and isinstance(self.filter['status'], dict) and '$in' in self.filter['status']:
                statuses = self.filter['status']['$in']
            return await db_supabase.get_rides_for_driver(self.filter['driver_id'], statuses=statuses, limit=limit)

        return await db_supabase.get_rows(
            self.collection_name,
            self.filter,
            order=self.sort_field,
            desc=self.sort_desc,
            limit=limit
        )

class Collection:
    def __init__(self, name: str):
        self.name = name

    def find(self, _filter: Optional[Dict] = None):
        return MockCursor(self.name, _filter)

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

        return await db_supabase.insert_one(self.name, doc)

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        update_data = update.get('$set') if isinstance(update, dict) and '$set' in update else update

        # Special RPC updates
        if self.name == 'drivers' and 'id' in _filter and 'lat' in update_data and 'lng' in update_data:
            return await db_supabase.update_driver_location(_filter['id'], update_data['lat'], update_data['lng'])

        if self.name == 'drivers' and 'id' in _filter:
            if 'is_available' in update_data:
                # Check if we are doing atomic claim
                if update_data['is_available'] is False and _filter.get('is_available') is True:
                     success = await db_supabase.claim_driver_atomic(_filter['id'])
                     return type('Result', (), {'modified_count': 1 if success else 0})()

                # Check for increment total_rides
                inc_val = 0
                if isinstance(update, dict) and '$inc' in update and 'total_rides' in update['$inc']:
                    inc_val = update['$inc']['total_rides']

                return await db_supabase.set_driver_available(_filter['id'], update_data['is_available'], total_rides_inc=inc_val)

        if self.name == 'otp_records' and 'id' in _filter and 'verified' in update_data:
            return await db_supabase.verify_otp_record(_filter['id'])

        if self.name == 'rides' and 'id' in _filter:
            return await db_supabase.update_ride(_filter['id'], update_data)

        # Generic update
        res = await db_supabase.update_one(self.name, _filter, update, upsert=upsert)
        return type('Result', (), {'modified_count': 1 if res else 0})()

    async def delete_one(self, _filter: Dict[str, Any]):
        if self.name == 'otp_records' and 'id' in _filter:
            res = await db_supabase.delete_otp_record(_filter['id'])
            return type('Result', (), {'deleted_count': 1 if res else 0})()

        res = await db_supabase.delete_one(self.name, _filter)
        return type('Result', (), {'deleted_count': len(res) if res else 0})()

    async def delete_many(self, _filter: Dict[str, Any]):
        res = await db_supabase.delete_many(self.name, _filter)
        return type('Result', (), {'deleted_count': len(res) if res else 0})()

    async def count_documents(self, _filter: Dict[str, Any]):
        return await db_supabase.count_documents(self.name, _filter)

    async def rpc(self, func_name: str, params: Dict[str, Any]):
        return await db_supabase.rpc(func_name, params)

class DB:
    def __init__(self):
        names = [
            'users', 'drivers', 'rides', 'otp_records', 'settings', 'saved_addresses',
            'vehicle_types', 'service_areas', 'fare_configs',
            'support_tickets', 'faqs', 'area_fees'
        ]
        for n in names:
            setattr(self, n, Collection(n))

    async def rpc(self, func_name: str, params: Dict[str, Any]):
        return await db_supabase.rpc(func_name, params)

db = DB()
