"""Wallet, promo, pricing and saved-places read tools for the AI assistant.

All read-only and user-scoped. The wallet tool never creates a wallet row
(unlike GET /wallet's get_or_create) — a missing wallet simply reads as a
zero balance. Saved places include coordinates because the booking flow
(find_place → get_fare_quote) needs them; they are ephemeral tool results,
never logged or persisted (see backend/ai/tools.py contract).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from .tools import ToolSpec, register
except ImportError:
    from ai.tools import ToolSpec, register

try:
    from .. import db_supabase
except ImportError:
    import db_supabase

logger = logging.getLogger(__name__)

_PROMO_FIELDS = (
    "code",
    "description",
    "discount_type",
    "discount_value",
    "max_discount",
    "free_ride",
    "min_ride_fare",
    "first_ride_only",
    "expiry_date",
)

_TXN_FIELDS = ("type", "amount", "balance_after", "description", "created_at")


def _pick(row: Dict[str, Any], fields) -> Dict[str, Any]:
    return {k: row[k] for k in fields if row.get(k) is not None}


def _surge_for(area: Dict[str, Any]) -> float:
    # Mirrors routes/fares.py: surge applies only when the area master toggle
    # AND the live flag are both on; otherwise riders pay 1.0x.
    if area.get("surge_enabled") and area.get("surge_active"):
        return float(area.get("surge_multiplier", 1.0))
    return 1.0


async def get_wallet_balance(user: Dict[str, Any]) -> Dict[str, Any]:
    wallet = await db_supabase.find_one("wallets", {"user_id": user["id"]})
    if not wallet:
        return {"balance": "0.00", "currency": "CAD", "note": "No wallet activity yet."}
    return {
        "balance": str(wallet.get("balance", "0.00")),
        "currency": wallet.get("currency", "CAD"),
        "is_active": wallet.get("is_active", True),
    }


async def get_wallet_transactions(user: Dict[str, Any], limit: int = 5) -> Dict[str, Any]:
    wallet = await db_supabase.find_one("wallets", {"user_id": user["id"]})
    if not wallet:
        return {"transactions": [], "note": "No wallet activity yet."}
    txns = await db_supabase.get_rows(
        "wallet_transactions",
        {"wallet_id": wallet["id"]},
        order="created_at",
        desc=True,
        limit=limit,
    )
    return {"transactions": [_pick(t, _TXN_FIELDS) for t in txns or []]}


async def get_available_promos(user: Dict[str, Any]) -> Dict[str, Any]:
    promos = await db_supabase.get_rows("promotions", {"is_active": True}, limit=100)
    now = datetime.now(timezone.utc)
    visible = []
    for p in promos or []:
        # Private coupons assigned to other users are invisible here.
        assigned = p.get("assigned_user_ids") or []
        if assigned and user["id"] not in assigned:
            continue
        # Area-restricted promos are skipped — without a pickup point we
        # cannot tell whether they apply (same rule as /promo/available).
        if p.get("service_area_id"):
            continue
        expiry = p.get("expiry_date")
        if expiry:
            try:
                exp_dt = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if exp_dt < now:
                    continue
            except ValueError:
                pass
        visible.append(_pick(p, _PROMO_FIELDS))
    return {
        "promos": visible,
        "note": "Final eligibility (usage limits, first-ride rules, minimum fare) is confirmed when the promo is applied at booking.",
    }


async def get_service_info(user: Dict[str, Any], city: Optional[str] = None) -> Dict[str, Any]:
    areas = await db_supabase.get_rows("service_areas", {"is_active": True}, order="name", limit=100)
    if city:
        needle = city.strip().lower()
        areas = [
            a
            for a in (areas or [])
            if needle in str(a.get("city", "")).lower() or needle in str(a.get("name", "")).lower()
        ]
    types = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=50)
    return {
        "service_areas": [
            {
                "name": a.get("name"),
                "city": a.get("city"),
                "province": a.get("province"),
                "current_surge_multiplier": _surge_for(a),
            }
            for a in (areas or [])
        ],
        "vehicle_types": [
            {
                "name": t.get("name"),
                "description": t.get("description"),
                "capacity": t.get("capacity"),
            }
            for t in (types or [])
        ],
    }


async def explain_fare_rates(user: Dict[str, Any], city: Optional[str] = None) -> Dict[str, Any]:
    areas = await db_supabase.get_rows("service_areas", {"is_active": True}, order="name", limit=100)
    if city:
        needle = city.strip().lower()
        areas = [
            a
            for a in (areas or [])
            if needle in str(a.get("city", "")).lower() or needle in str(a.get("name", "")).lower()
        ]
    if not areas:
        return {"error": "no matching service area", "hint": "use get_service_info to list operating cities"}

    types = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=50)
    type_names = {t["id"]: t.get("name") for t in types or [] if t.get("id")}

    rates = []
    for area in areas[:3]:  # keep results small — riders ask about one city
        configs = await db_supabase.get_rows(
            "fare_configs", {"service_area_id": area.get("id"), "is_active": True}, limit=20
        )
        rates.append(
            {
                "area": area.get("name"),
                "city": area.get("city"),
                "current_surge_multiplier": _surge_for(area),
                "rates": [
                    {
                        "vehicle_type": type_names.get(c.get("vehicle_type_id"), "Standard"),
                        "base_fare": str(c.get("base_fare")),
                        "per_km": str(c.get("per_km_rate")),
                        "per_minute": str(c.get("per_minute_rate")),
                        "minimum_fare": str(c.get("minimum_fare")),
                        "booking_fee": str(c.get("booking_fee")),
                    }
                    for c in configs or []
                ],
            }
        )
    return {
        "fare_rates": rates,
        "note": "Fare = base + per-km × distance + per-minute × time, plus booking fee, any airport/area fees, GST/PST, times the surge multiplier shown to the rider before booking.",
    }


async def get_saved_places(user: Dict[str, Any]) -> Dict[str, Any]:
    rows = await db_supabase.get_rows("saved_addresses", {"user_id": user["id"]}, limit=20)
    return {
        "places": [
            {
                "name": r.get("name"),
                "address": r.get("address"),
                "lat": r.get("lat"),
                "lng": r.get("lng"),
            }
            for r in rows or []
        ]
    }


register(
    ToolSpec(
        name="get_wallet_balance",
        description=(
            "Call this when the rider asks about their Spinr wallet balance or whether a "
            "top-up went through. Returns the current balance in CAD."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=get_wallet_balance,
    )
)

register(
    ToolSpec(
        name="get_wallet_transactions",
        description=(
            "Call this when the rider asks where their wallet money went, about a specific "
            "top-up, charge or refund. Returns recent wallet ledger entries, newest first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "How many transactions to return (default 5).",
                }
            },
            "required": [],
        },
        handler=get_wallet_transactions,
    )
)

register(
    ToolSpec(
        name="get_available_promos",
        description=(
            "Call this when the rider asks about promo codes, discounts or offers. Returns "
            "currently active promos visible to this rider with their conditions."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=get_available_promos,
    )
)

register(
    ToolSpec(
        name="get_service_info",
        description=(
            "Call this when the rider asks where Spinr operates, whether surge pricing is "
            "active right now, or what vehicle options exist (e.g. 'what is Spinr XL'). "
            "Returns service areas with their live surge multiplier and active vehicle types."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "maxLength": 80,
                    "description": "Optional city/area name filter.",
                }
            },
            "required": [],
        },
        handler=get_service_info,
    )
)

register(
    ToolSpec(
        name="explain_fare_rates",
        description=(
            "Call this when the rider asks how fares are calculated — per-km rate, base "
            "fare, booking fee or minimum fare. Returns the published rate card per "
            "vehicle type for an area. Never invent rates."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "maxLength": 80,
                    "description": "Optional city/area name filter.",
                }
            },
            "required": [],
        },
        handler=explain_fare_rates,
    )
)

register(
    ToolSpec(
        name="get_saved_places",
        description=(
            "Call this to resolve the rider's saved places (e.g. 'home', 'work') into an "
            "address before quoting a trip, or when the rider asks what places they have "
            "saved."
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=get_saved_places,
    )
)
