import os
from typing import Any, Dict, Optional, List, Union
try:
    from . import db_supabase
except ImportError:
    import db_supabase

# Provide a db variable for backward compatibility
# This will be set to DB instance after the class is defined
db = None
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

    def skip(self, offset: int):
        self._offset = offset
        return self

    def limit(self, limit: int):
        # Store limit if needed, but for now to_list takes limit
        self._limit = limit
        return self

    async def to_list(self, limit: int = 100):
        # Override limit if set by .limit()
        if hasattr(self, '_limit'):
            limit = self._limit
        
        offset = getattr(self, '_offset', None)

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
            limit=limit,
            offset=offset
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

    async def insert_many(self, docs: List[Dict[str, Any]]):
        if not docs:
            return type('Result', (), {'inserted_ids': []})()
        
        # Simple loop for now as Supabase might not have bulk insert exposed in db_supabase yet
        ids = []
        for doc in docs:
            await self.insert_one(doc)
            ids.append(doc.get('id'))
            
        return type('Result', (), {'inserted_ids': ids})()

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
                     return type('Result', (), {'modified_count': 1 if success else 0, 'matched_count': 1 if success else 0})()

                # Check for increment total_rides
                inc_val = 0
                if isinstance(update, dict) and '$inc' in update and 'total_rides' in update['$inc']:
                    inc_val = update['$inc']['total_rides']

                return await db_supabase.set_driver_available(_filter['id'], update_data['is_available'], total_rides_inc=inc_val)

        if self.name == 'otp_records' and 'id' in _filter and 'verified' in update_data:
            res = await db_supabase.verify_otp_record(_filter['id'])
            return type('Result', (), {'modified_count': 1 if res else 0, 'matched_count': 1 if res else 0})()

        if self.name == 'rides' and 'id' in _filter:
            res = await db_supabase.update_ride(_filter['id'], update_data)
            return type('Result', (), {'modified_count': 1 if res else 0, 'matched_count': 1 if res else 0})()

        # Generic update
        res = await db_supabase.update_one(self.name, _filter, update, upsert=upsert)
        return type('Result', (), {'modified_count': 1 if res else 0, 'matched_count': 1 if res else 0})()

    async def update_many(self, _filter: Dict[str, Any], update: Dict[str, Any]):
        """Note: Supabase update natively updates all rows matching the filter."""
        update_data = update.get('$set') if isinstance(update, dict) and '$set' in update else update
        res = await db_supabase.update_one(self.name, _filter, update_data, upsert=False)
        return type('Result', (), {'modified_count': 1 if res else 0, 'matched_count': 1 if res else 0})()

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
        self.users = UserCollection('users')
        self.drivers = DriverCollection('drivers')
        self.rides = RideCollection('rides')
        self.otp_records = OTPCollection('otp_records')
        self.settings = SettingsCollection('settings')
        self.saved_addresses = SavedAddressCollection('saved_addresses')
        self.vehicle_types = VehicleTypeCollection('vehicle_types')
        self.service_areas = ServiceAreaCollection('service_areas')
        self.fare_configs = FareConfigCollection('fare_configs')
        self.support_tickets = SupportTicketCollection('support_tickets')
        self.faqs = FAQCollection('faqs')
        self.area_fees = AreaFeeCollection('area_fees')
        self.driver_documents = DriverDocumentCollection('driver_documents')
        self.document_requirements = DocumentRequirementCollection('document_requirements')
        self.surge_pricing = SurgePricingCollection('surge_pricing')
        self.document_files = DocumentFileCollection('document_files')
        self.driver_location_history = DriverLocationHistoryCollection('driver_location_history')
        self.corporate_accounts = CorporateAccountCollection('corporate_accounts')
        self.ride_messages = RideMessageCollection('ride_messages')
        self.emergencies = EmergencyCollection('emergencies')
        self.emergency_contacts = EmergencyContactCollection('emergency_contacts')
        self.bank_accounts = BankAccountCollection('bank_accounts')
        self.payouts = PayoutCollection('payouts')
        self.promo_codes = PromoCodeCollection('promo_codes')
        self.promotions = PromotionCollection('promotions')
        self.promo_applications = PromoApplicationCollection('promo_applications')
        self.disputes = DisputeCollection('disputes')
        self.notifications = NotificationCollection('notifications')
        self.notification_preferences = NotificationPreferenceCollection('notification_preferences')

    async def rpc(self, func_name: str, params: Dict[str, Any]):
        return await db_supabase.rpc(func_name, params)

    async def get_rows(self, table: str, filters: Optional[Dict[str, Any]] = None, order: Optional[str] = None, desc: bool = False, limit: Optional[int] = None, offset: Optional[int] = None):
        """Paginated row fetch for admin and other callers."""
        return await db_supabase.get_rows(table, filters, order, desc, limit, offset)

    async def fetchall(self, query: str, params: Optional[Dict[str, Any]] = None):
        """
        Execute a raw SQL SELECT query and return all rows.
        
        Args:
            query: SQL query string (e.g., 'SELECT * FROM settings')
            params: Optional dictionary of query parameters
            
        Returns:
            List of dictionaries representing rows
        """
        return await db_supabase.execute_query(query, params)

    async def fetchone(self, query: str, params: Optional[Dict[str, Any]] = None):
        """
        Execute a raw SQL SELECT query and return the first row.
        
        Args:
            query: SQL query string (e.g., 'SELECT * FROM settings WHERE id = $1')
            params: Optional dictionary of query parameters
            
        Returns:
            Dictionary representing the first row, or None if no results
        """
        rows = await db_supabase.execute_query(query, params)
        return rows[0] if rows else None

    async def execute(self, query: str, params: Optional[Dict[str, Any]] = None):
        """
        Execute a raw SQL INSERT, UPDATE, or DELETE query.
        
        Args:
            query: SQL query string (e.g., 'INSERT INTO settings (key, value) VALUES ($1, $2)')
            params: Optional dictionary of query parameters
            
        Returns:
            Dictionary with execution results
        """
        return await db_supabase.execute_write(query, params)

class BaseCollection(Collection):
    def __init__(self, name: str):
        super().__init__(name)

class UserCollection(BaseCollection):
    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        if not _filter:
            return None
        if 'id' in _filter:
            return await db_supabase.get_user_by_id(_filter['id'])
        if 'phone' in _filter:
            return await db_supabase.get_user_by_phone(_filter['phone'])
        return await super().find_one(_filter)

class DriverCollection(BaseCollection):
    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        if not _filter:
            return None
        if 'id' in _filter:
            return await db_supabase.get_driver_by_id(_filter['id'])
        return await super().find_one(_filter)

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        if 'id' in _filter and 'lat' in update and 'lng' in update:
            return await db_supabase.update_driver_location(_filter['id'], update['lat'], update['lng'])
        if 'id' in _filter and 'is_available' in update:
            if update['is_available'] is False and _filter.get('is_available') is True:
                success = await db_supabase.claim_driver_atomic(_filter['id'])
                return type('Result', (), {'modified_count': 1 if success else 0, 'matched_count': 1 if success else 0})()
            inc_val = 0
            if isinstance(update, dict) and '$inc' in update and 'total_rides' in update['$inc']:
                inc_val = update['$inc']['total_rides']
            return await db_supabase.set_driver_available(_filter['id'], update['is_available'], total_rides_inc=inc_val)
        return await super().update_one(_filter, update, upsert)

class RideCollection(BaseCollection):
    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        if not _filter:
            return None
        if 'id' in _filter:
            return await db_supabase.get_ride(_filter['id'])
        return await super().find_one(_filter)

    async def insert_one(self, doc: Dict[str, Any]):
        return await db_supabase.insert_ride(doc)

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        if 'id' in _filter:
            res = await db_supabase.update_ride(_filter['id'], update)
            return type('Result', (), {'modified_count': 1 if res else 0, 'matched_count': 1 if res else 0})()
        return await super().update_one(_filter, update, upsert)

class OTPCollection(BaseCollection):
    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        if not _filter:
            return None
        if 'phone' in _filter and 'code' in _filter:
            return await db_supabase.get_otp_record(_filter['phone'], _filter['code'])
        return await super().find_one(_filter)

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        if 'id' in _filter and 'verified' in update:
            res = await db_supabase.verify_otp_record(_filter['id'])
            return type('Result', (), {'modified_count': 1 if res else 0, 'matched_count': 1 if res else 0})()
        return await super().update_one(_filter, update, upsert)

    async def delete_one(self, _filter: Dict[str, Any]):
        if 'id' in _filter:
            res = await db_supabase.delete_otp_record(_filter['id'])
            return type('Result', (), {'deleted_count': 1 if res else 0})()
        return await super().delete_one(_filter)

class SettingsCollection(BaseCollection):
    pass

class SavedAddressCollection(BaseCollection):
    pass

class VehicleTypeCollection(BaseCollection):
    pass

class ServiceAreaCollection(BaseCollection):
    pass

class FareConfigCollection(BaseCollection):
    pass

class SupportTicketCollection(BaseCollection):
    pass

class FAQCollection(BaseCollection):
    pass

class AreaFeeCollection(BaseCollection):
    pass

class DriverDocumentCollection(BaseCollection):
    pass

class DocumentRequirementCollection(BaseCollection):
    pass

class SurgePricingCollection(BaseCollection):
    pass

class DocumentFileCollection(BaseCollection):
    pass

class DriverLocationHistoryCollection(BaseCollection):
    pass

class CorporateAccountCollection(BaseCollection):
    pass

class RideMessageCollection(BaseCollection):
    pass

class EmergencyCollection(BaseCollection):
    pass

class EmergencyContactCollection(BaseCollection):
    pass

class BankAccountCollection(BaseCollection):
    pass

class PayoutCollection(BaseCollection):
    pass

class PromoCodeCollection(BaseCollection):
    pass

class PromotionCollection(BaseCollection):
    pass

class PromoApplicationCollection(BaseCollection):
    pass

class DisputeCollection(BaseCollection):
    pass

class NotificationCollection(BaseCollection):
    pass

class NotificationPreferenceCollection(BaseCollection):
    pass

# Initialize db instance after all classes are defined
db = DB()


