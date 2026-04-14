import logging
import os
import sys
from typing import Any, Dict, List, Optional

try:
    from . import db_supabase
except ImportError:
    import db_supabase

# [GO-ONLINE DEBUG] dedicated logger for tracing the update dispatch path.
# Emits to stdout at INFO level so it shows up in Docker / Fly / Render
# logs without needing log-level changes, and so deploy platforms like
# Railway do NOT tag it as severity=error (which they do for any stderr
# output regardless of Python log level). Remove these lines once the bug
# is confirmed resolved.
_goonline_logger = logging.getLogger("goonline.debug")
if not _goonline_logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("[GO-ONLINE] %(message)s"))
    _goonline_logger.addHandler(_h)
    # Only emit diagnostic traces when DEBUG env var is set to avoid PII leak in production
    _debug_enabled = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
    _goonline_logger.setLevel(logging.DEBUG if _debug_enabled else logging.WARNING)
    _goonline_logger.propagate = False

# Dedicated diagnostic logger for route handlers. Route modules import
# `diag_logger` from this file instead of using the stdlib/loguru loggers,
# which were being silently dropped by the deployment's log configuration
# (uvicorn + default Python logging level = WARNING, so logger.info in
# routes went nowhere). This logger has its own StreamHandler wired to
# stdout so every line reaches the deploy log stream with the correct
# severity. Use the "[TAG] message" convention so different traces can be
# greped independently.
diag_logger = logging.getLogger("spinr.diag")
if not diag_logger.handlers:
    _h2 = logging.StreamHandler(sys.stdout)
    _h2.setFormatter(logging.Formatter("%(message)s"))
    diag_logger.addHandler(_h2)
    # Only emit diagnostic traces when DEBUG env var is set to avoid PII leak in production
    diag_logger.setLevel(logging.DEBUG if _debug_enabled else logging.WARNING)
    diag_logger.propagate = False

# Provide a db variable for backward compatibility
# This will be set to DB instance after the class is defined
db = None


class MockCursor:
    def __init__(self, collection_name: str, _filter: Optional[Dict], _sort: Optional[Dict] = None):
        self.collection_name = collection_name
        self.filter = _filter
        self.sort_field = _sort.get("field") if _sort else None
        self.sort_desc = _sort.get("desc", False) if _sort else False

    def sort(self, field: str, order: int):
        self.sort_field = field
        self.sort_desc = order == -1
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
        if hasattr(self, "_limit"):
            limit = self._limit

        offset = getattr(self, "_offset", None)

        if self.collection_name == "rides" and "rider_id" in (self.filter or {}):
            return await db_supabase.get_rides_for_user(self.filter["rider_id"], limit=limit)

        if self.collection_name == "rides" and "driver_id" in (self.filter or {}):
            # Handle status list filter if present
            statuses = None
            if (
                self.filter
                and "status" in self.filter
                and isinstance(self.filter["status"], dict)
                and "$in" in self.filter["status"]
            ):
                statuses = self.filter["status"]["$in"]
            return await db_supabase.get_rides_for_driver(self.filter["driver_id"], statuses=statuses, limit=limit)

        return await db_supabase.get_rows(
            self.collection_name, self.filter, order=self.sort_field, desc=self.sort_desc, limit=limit, offset=offset
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
        if self.name == "users":
            if "id" in _filter:
                return await db_supabase.get_user_by_id(_filter["id"])
            if "phone" in _filter:
                return await db_supabase.get_user_by_phone(_filter["phone"])

        if self.name == "drivers" and "id" in _filter:
            return await db_supabase.get_driver_by_id(_filter["id"])

        if self.name == "rides" and "id" in _filter:
            return await db_supabase.get_ride(_filter["id"])

        if self.name == "otp_records" and "phone" in _filter and "code" in _filter:
            return await db_supabase.get_otp_record(_filter["phone"], _filter["code"])

        # Generic lookup
        rows = await db_supabase.get_rows(self.name, _filter, limit=1)
        return rows[0] if rows else None

    async def insert_one(self, doc: Dict[str, Any]):
        if self.name == "users":
            return await db_supabase.create_user(doc)
        if self.name == "rides":
            return await db_supabase.insert_ride(doc)
        if self.name == "otp_records":
            return await db_supabase.insert_otp_record(doc)

        return await db_supabase.insert_one(self.name, doc)

    async def insert_many(self, docs: List[Dict[str, Any]]):
        if not docs:
            return type("Result", (), {"inserted_ids": []})()

        # Bulk insert in a single round-trip
        await db_supabase.insert_many(self.name, docs)
        ids = [doc.get("id") for doc in docs]
        return type("Result", (), {"inserted_ids": ids})()

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        update_data = update.get("$set") if isinstance(update, dict) and "$set" in update else update

        if self.name == "drivers":
            _goonline_logger.info(
                f"Collection.update_one ENTRY table={self.name} "
                f"filter={_filter} raw_update={update} "
                f"unwrapped_update_data={update_data}"
            )

        # Special RPC updates
        if self.name == "drivers" and "id" in _filter and "lat" in update_data and "lng" in update_data:
            _goonline_logger.info(f"Collection.update_one BRANCH=update_driver_location driver_id={_filter['id']}")
            return await db_supabase.update_driver_location(_filter["id"], update_data["lat"], update_data["lng"])

        if self.name == "drivers" and "id" in _filter:
            if "is_available" in update_data:
                # Atomic claim: is_available flip True -> False gated on
                # current value == True. Dispatch layer depends on this
                # race-safe path; keep as-is.
                if update_data["is_available"] is False and _filter.get("is_available") is True:
                    _goonline_logger.info(f"Collection.update_one BRANCH=atomic_claim driver_id={_filter['id']}")
                    success = await db_supabase.claim_driver_atomic(_filter["id"])
                    return type(
                        "Result", (), {"modified_count": 1 if success else 0, "matched_count": 1 if success else 0}
                    )()

                # Only hijack into set_driver_available when the update is
                # PURELY an is_available toggle (optionally with a total_rides
                # increment). If the caller is also writing is_online,
                # updated_at, or any other column (e.g. the go-online handler
                # in routes/drivers.py), we MUST fall through to the generic
                # update path — set_driver_available would silently drop
                # those fields and only is_available would move, which caused
                # a silent go-online failure.
                other_keys = [k for k in update_data.keys() if k != "is_available"]
                if not other_keys:
                    _goonline_logger.info(
                        f"Collection.update_one BRANCH=set_driver_available "
                        f"(pure is_available toggle) driver_id={_filter['id']} "
                        f"is_available={update_data['is_available']}"
                    )
                    inc_val = 0
                    if isinstance(update, dict) and "$inc" in update and "total_rides" in update["$inc"]:
                        inc_val = update["$inc"]["total_rides"]
                    return await db_supabase.set_driver_available(
                        _filter["id"], update_data["is_available"], total_rides_inc=inc_val
                    )
                # else: fall through to the generic update path below.
                _goonline_logger.info(
                    f"Collection.update_one BRANCH=fallthrough_generic "
                    f"(is_available present but other keys also present: {other_keys}) "
                    f"driver_id={_filter['id']}"
                )

        if self.name == "otp_records" and "id" in _filter and "verified" in update_data:
            res = await db_supabase.verify_otp_record(_filter["id"])
            return type("Result", (), {"modified_count": 1 if res else 0, "matched_count": 1 if res else 0})()

        if self.name == "rides" and "id" in _filter:
            res = await db_supabase.update_ride(_filter["id"], update_data)
            return type("Result", (), {"modified_count": 1 if res else 0, "matched_count": 1 if res else 0})()

        # Generic update
        if self.name == "drivers":
            _goonline_logger.info(f"Collection.update_one BRANCH=generic_update filter={_filter} update={update}")
        res = await db_supabase.update_one(self.name, _filter, update, upsert=upsert)
        if self.name == "drivers":
            _goonline_logger.info(
                f"Collection.update_one GENERIC_RESULT raw_res={res!r} (None/falsy => zero rows affected)"
            )
        return type("Result", (), {"modified_count": 1 if res else 0, "matched_count": 1 if res else 0})()

    async def update_many(self, _filter: Dict[str, Any], update: Dict[str, Any]):
        """Note: Supabase update natively updates all rows matching the filter."""
        update_data = update.get("$set") if isinstance(update, dict) and "$set" in update else update
        res = await db_supabase.update_one(self.name, _filter, update_data, upsert=False)
        return type("Result", (), {"modified_count": 1 if res else 0, "matched_count": 1 if res else 0})()

    async def delete_one(self, _filter: Dict[str, Any]):
        if self.name == "otp_records" and "id" in _filter:
            res = await db_supabase.delete_otp_record(_filter["id"])
            return type("Result", (), {"deleted_count": 1 if res else 0})()

        res = await db_supabase.delete_one(self.name, _filter)
        return type("Result", (), {"deleted_count": len(res) if res else 0})()

    async def delete_many(self, _filter: Dict[str, Any]):
        res = await db_supabase.delete_many(self.name, _filter)
        return type("Result", (), {"deleted_count": len(res) if res else 0})()

    async def count_documents(self, _filter: Dict[str, Any]):
        return await db_supabase.count_documents(self.name, _filter)

    async def rpc(self, func_name: str, params: Dict[str, Any]):
        return await db_supabase.rpc(func_name, params)


class DB:
    def __init__(self):
        self.users = UserCollection("users")
        self.drivers = DriverCollection("drivers")
        self.rides = RideCollection("rides")
        self.otp_records = OTPCollection("otp_records")
        self.settings = SettingsCollection("settings")
        self.saved_addresses = SavedAddressCollection("saved_addresses")
        self.vehicle_types = VehicleTypeCollection("vehicle_types")
        self.service_areas = ServiceAreaCollection("service_areas")
        self.fare_configs = FareConfigCollection("fare_configs")
        self.support_tickets = SupportTicketCollection("support_tickets")
        self.faqs = FAQCollection("faqs")
        self.area_fees = AreaFeeCollection("area_fees")
        self.driver_documents = DriverDocumentCollection("driver_documents")
        self.document_requirements = DocumentRequirementCollection("document_requirements")
        self.surge_pricing = SurgePricingCollection("surge_pricing")
        self.document_files = DocumentFileCollection("document_files")
        self.driver_location_history = DriverLocationHistoryCollection("driver_location_history")
        self.corporate_accounts = CorporateAccountCollection("corporate_accounts")
        self.ride_messages = RideMessageCollection("ride_messages")
        self.emergencies = EmergencyCollection("emergencies")
        self.emergency_contacts = EmergencyContactCollection("emergency_contacts")
        self.bank_accounts = BankAccountCollection("bank_accounts")
        self.payouts = PayoutCollection("payouts")
        self.promo_codes = PromoCodeCollection("promo_codes")
        self.promotions = PromotionCollection("promotions")
        self.promo_applications = PromoApplicationCollection("promo_applications")
        self.disputes = DisputeCollection("disputes")
        self.notifications = NotificationCollection("notifications")
        self.notification_preferences = NotificationPreferenceCollection("notification_preferences")
        # Spinr Pass subscription tables + driver requirements. Previously
        # unregistered, which caused AttributeError at any call site that
        # wasn't wrapped in try/except — notably the go-online route, which
        # crashed at db.driver_subscriptions.find_one(...). BaseCollection
        # is fine: these tables only need generic find_one/find/update_one
        # which the parent Collection class delegates to db_supabase by
        # table name.
        self.driver_subscriptions = BaseCollection("driver_subscriptions")
        self.subscription_plans = BaseCollection("subscription_plans")
        self.driver_requirements = BaseCollection("driver_requirements")
        self.driver_notes = BaseCollection("driver_notes")
        self.driver_activity_log = BaseCollection("driver_activity_log")
        self.driver_daily_stats = BaseCollection("driver_daily_stats")
        self.cloud_messages = BaseCollection("cloud_messages")
        self.audit_logs = BaseCollection("audit_logs")
        self.push_tokens = BaseCollection("push_tokens")
        self.admin_staff = BaseCollection("admin_staff")
        self.support_messages = BaseCollection("support_messages")
        # Favorite routes
        self.favorite_routes = BaseCollection("favorite_routes")
        # Loyalty program
        self.loyalty_accounts = BaseCollection("loyalty_accounts")
        self.loyalty_transactions = BaseCollection("loyalty_transactions")
        # P1-07: In-app wallet
        self.wallets = BaseCollection("wallets")
        self.wallet_transactions = BaseCollection("wallet_transactions")
        # P1-08: Fare splitting
        self.fare_splits = BaseCollection("fare_splits")
        self.fare_split_participants = BaseCollection("fare_split_participants")
        # P1-09: Quest / bonus challenges
        self.quests = BaseCollection("quests")
        self.quest_progress = BaseCollection("quest_progress")
        # Stripe webhook dedup (migration 22; audit P0-B2)
        self.stripe_events = BaseCollection("stripe_events")
        # Refresh tokens for rotation + revocation (migration 25; audit P0-S3)
        self.refresh_tokens = BaseCollection("refresh_tokens")

    async def rpc(self, func_name: str, params: Dict[str, Any]):
        return await db_supabase.rpc(func_name, params)

    async def get_rows(
        self,
        table: str,
        filters: Optional[Dict[str, Any]] = None,
        order: Optional[str] = None,
        desc: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ):
        """Paginated row fetch for admin and other callers."""
        return await db_supabase.get_rows(table, filters, order, desc, limit, offset)


class BaseCollection(Collection):
    def __init__(self, name: str):
        super().__init__(name)


class UserCollection(BaseCollection):
    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        if not _filter:
            return None
        if "id" in _filter:
            return await db_supabase.get_user_by_id(_filter["id"])
        if "phone" in _filter:
            return await db_supabase.get_user_by_phone(_filter["phone"])
        return await super().find_one(_filter)


class DriverCollection(BaseCollection):
    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        if not _filter:
            return None
        if "id" in _filter:
            return await db_supabase.get_driver_by_id(_filter["id"])
        return await super().find_one(_filter)

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        _goonline_logger.info(f"DriverCollection.update_one ENTRY filter={_filter} update={update}")
        # Note: callers typically wrap updates in {'$set': {...}}, in which case
        # the checks below (which look at the outer dict) will not match and we
        # fall through to the parent class's update_one, which does the $set
        # unwrap and then handles the special cases. Only legacy callers that
        # pass an unwrapped update dict hit these branches directly.
        if "id" in _filter and "lat" in update and "lng" in update:
            _goonline_logger.info("DriverCollection.update_one BRANCH=update_driver_location")
            return await db_supabase.update_driver_location(_filter["id"], update["lat"], update["lng"])
        if "id" in _filter and "is_available" in update:
            if update["is_available"] is False and _filter.get("is_available") is True:
                _goonline_logger.info("DriverCollection.update_one BRANCH=atomic_claim (unwrapped)")
                success = await db_supabase.claim_driver_atomic(_filter["id"])
                return type(
                    "Result", (), {"modified_count": 1 if success else 0, "matched_count": 1 if success else 0}
                )()
            # Same guard as the parent class's fix: only hijack into
            # set_driver_available when the update is PURELY an is_available
            # toggle. If the caller is also writing other columns, fall
            # through to the parent's generic path so nothing gets dropped.
            other_keys = [k for k in update.keys() if k != "is_available" and not k.startswith("$")]
            if not other_keys:
                _goonline_logger.info(
                    "DriverCollection.update_one BRANCH=set_driver_available (unwrapped, pure toggle)"
                )
                inc_val = 0
                if isinstance(update, dict) and "$inc" in update and "total_rides" in update["$inc"]:
                    inc_val = update["$inc"]["total_rides"]
                return await db_supabase.set_driver_available(
                    _filter["id"], update["is_available"], total_rides_inc=inc_val
                )
            _goonline_logger.info(
                f"DriverCollection.update_one BRANCH=fallthrough_to_super "
                f"(unwrapped, is_available + other keys: {other_keys})"
            )
        _goonline_logger.info("DriverCollection.update_one BRANCH=super().update_one")
        return await super().update_one(_filter, update, upsert)


class RideCollection(BaseCollection):
    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        if not _filter:
            return None
        if "id" in _filter:
            return await db_supabase.get_ride(_filter["id"])
        return await super().find_one(_filter)

    async def insert_one(self, doc: Dict[str, Any]):
        return await db_supabase.insert_ride(doc)

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        if "id" in _filter:
            res = await db_supabase.update_ride(_filter["id"], update)
            return type("Result", (), {"modified_count": 1 if res else 0, "matched_count": 1 if res else 0})()
        return await super().update_one(_filter, update, upsert)


class OTPCollection(BaseCollection):
    async def find_one(self, _filter: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        if not _filter:
            return None
        if "phone" in _filter and "code" in _filter:
            return await db_supabase.get_otp_record(_filter["phone"], _filter["code"])
        return await super().find_one(_filter)

    async def update_one(self, _filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        if "id" in _filter and "verified" in update:
            res = await db_supabase.verify_otp_record(_filter["id"])
            return type("Result", (), {"modified_count": 1 if res else 0, "matched_count": 1 if res else 0})()
        return await super().update_one(_filter, update, upsert)

    async def delete_one(self, _filter: Dict[str, Any]):
        if "id" in _filter:
            res = await db_supabase.delete_otp_record(_filter["id"])
            return type("Result", (), {"deleted_count": 1 if res else 0})()
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
