"""
features.py – Extended feature endpoints for Spinr.
Includes: Support Tickets, FAQs, Surge Pricing, Scheduled Rides,
           Multi-stop Rides, Safety Toolkit, Push Notifications.
"""
import uuid
import secrets
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

try:
    from .db import db
except ImportError:
    from db import db

logger = logging.getLogger(__name__)

# ============ Routers ============
support_router = APIRouter(prefix="/api")
admin_support_router = APIRouter(prefix="/api/admin")


# ============ Geometry Helpers ============

def point_in_polygon(lat: float, lng: float, polygon: List[Dict[str, float]]) -> bool:
    """Ray-casting algorithm to check if a point is inside a polygon.
    polygon is a list of dicts with 'lat' and 'lng' keys.
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i].get('lat', 0), polygon[i].get('lng', 0)
        yj, xj = polygon[j].get('lat', 0), polygon[j].get('lng', 0)
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


async def calculate_airport_fee(pickup_lat: float, pickup_lng: float,
                                dropoff_lat: float, dropoff_lng: float) -> Dict[str, Any]:
    """Check if pickup or dropoff falls in an airport zone.
    Returns {'airport_fee': float, 'airport_zone_name': str | None, 'is_pickup': bool, 'is_dropoff': bool}
    """
    areas = await db.service_areas.find({'is_airport': True}).to_list(50)
    result = {'airport_fee': 0.0, 'airport_zone_name': None, 'is_pickup': False, 'is_dropoff': False}

    for area in areas:
        polygon = area.get('polygon', [])
        fee = float(area.get('airport_fee', 0))
        if fee <= 0 or len(polygon) < 3:
            continue

        pickup_in = point_in_polygon(pickup_lat, pickup_lng, polygon)
        dropoff_in = point_in_polygon(dropoff_lat, dropoff_lng, polygon)

        if pickup_in or dropoff_in:
            result['airport_fee'] = fee
            result['airport_zone_name'] = area.get('name', 'Airport')
            result['is_pickup'] = pickup_in
            result['is_dropoff'] = dropoff_in
            break  # Use the first matching airport zone

    return result

# ============ Pydantic Models ============

class CreateTicketRequest(BaseModel):
    subject: str
    message: str
    category: str = "general"

class ReplyToTicketRequest(BaseModel):
    message: str

class CreateFaqRequest(BaseModel):
    question: str
    answer: str
    category: str = "general"
    sort_order: int = 0

class UpdateFaqRequest(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    category: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None

class ScheduleRideRequest(BaseModel):
    rider_id: str
    vehicle_type_id: str
    pickup_address: str
    pickup_lat: float
    pickup_lng: float
    dropoff_address: str
    dropoff_lat: float
    dropoff_lng: float
    distance_km: float
    duration_minutes: int
    scheduled_time: str  # ISO 8601 datetime string
    stops: List[Dict[str, Any]] = []

class AddStopRequest(BaseModel):
    address: str
    lat: float
    lng: float
    order: int = 0

class ShareTripRequest(BaseModel):
    contact_name: str
    contact_phone: str

class UpdateSurgeRequest(BaseModel):
    surge_active: Optional[bool] = None
    surge_multiplier: Optional[float] = None

class RegisterFcmTokenRequest(BaseModel):
    token: str

class SendNotificationRequest(BaseModel):
    user_id: str
    title: str
    body: str
    data: Dict[str, str] = {}


# ============ Airport Fee Check (User/App facing) ============

@support_router.get("/rides/airport-fee")
async def check_airport_fee(
    pickup_lat: float = Query(...),
    pickup_lng: float = Query(...),
    dropoff_lat: float = Query(...),
    dropoff_lng: float = Query(...),
):
    """Check if a ride involves an airport zone and return the fee.
    Call this before ride request to show the airport surcharge in the fare estimate.
    """
    result = await calculate_airport_fee(pickup_lat, pickup_lng, dropoff_lat, dropoff_lng)
    return result


# ============ Support Tickets (User-facing) ============

@support_router.post("/tickets")
async def create_ticket(req: CreateTicketRequest, user_id: str = Query(...)):
    """Create a new support ticket."""
    ticket = {
        'id': str(uuid.uuid4()),
        'user_id': user_id,
        'subject': req.subject,
        'message': req.message,
        'category': req.category,
        'status': 'open',
        'replies': [],
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    }
    await db.support_tickets.insert_one(ticket)
    return ticket

@support_router.get("/tickets")
async def get_user_tickets(user_id: str = Query(...)):
    """Get all tickets for a specific user."""
    tickets = await db.support_tickets.find({'user_id': user_id}).sort('created_at', -1).to_list(100)
    return tickets

@support_router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    """Get a specific ticket by ID."""
    ticket = await db.support_tickets.find_one({'id': ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket

# ============ FAQs (User-facing) ============

@support_router.get("/faqs")
async def get_faqs(category: Optional[str] = None):
    """Get all active FAQs, optionally filtered by category."""
    query: Dict[str, Any] = {'is_active': True}
    if category:
        query['category'] = category
    faqs = await db.faqs.find(query).sort('sort_order', 1).to_list(200)
    return faqs

# ============ Admin: Support Tickets ============

@admin_support_router.get("/tickets")
async def admin_get_tickets(status: Optional[str] = None):
    """Get all support tickets (admin)."""
    query: Dict[str, Any] = {}
    if status:
        query['status'] = status
    tickets = await db.support_tickets.find(query).sort('created_at', -1).to_list(500)
    return tickets

@admin_support_router.post("/tickets/{ticket_id}/reply")
async def admin_reply_ticket(ticket_id: str, req: ReplyToTicketRequest):
    """Reply to a support ticket."""
    ticket = await db.support_tickets.find_one({'id': ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    reply = {
        'message': req.message,
        'author': 'admin',
        'created_at': datetime.utcnow().isoformat(),
    }

    replies = ticket.get('replies', [])
    replies.append(reply)

    await db.support_tickets.update_one(
        {'id': ticket_id},
        {'$set': {
            'replies': replies,
            'status': 'in_progress',
            'updated_at': datetime.utcnow(),
        }}
    )
    return {'status': 'replied', 'reply': reply}

@admin_support_router.post("/tickets/{ticket_id}/close")
async def admin_close_ticket(ticket_id: str):
    """Close a support ticket."""
    result = await db.support_tickets.update_one(
        {'id': ticket_id},
        {'$set': {'status': 'closed', 'updated_at': datetime.utcnow()}}
    )
    if not result:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {'status': 'closed'}

# ============ Admin: FAQs ============

@admin_support_router.get("/faqs")
async def admin_get_faqs():
    """Get all FAQs (including inactive) for admin."""
    faqs = await db.faqs.find().sort('sort_order', 1).to_list(500)
    return faqs

@admin_support_router.post("/faqs")
async def admin_create_faq(req: CreateFaqRequest):
    """Create a new FAQ."""
    faq = {
        'id': str(uuid.uuid4()),
        'question': req.question,
        'answer': req.answer,
        'category': req.category,
        'sort_order': req.sort_order,
        'is_active': True,
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    }
    await db.faqs.insert_one(faq)
    return faq

@admin_support_router.put("/faqs/{faq_id}")
async def admin_update_faq(faq_id: str, req: UpdateFaqRequest):
    """Update an existing FAQ."""
    update_data: Dict[str, Any] = {'updated_at': datetime.utcnow()}
    if req.question is not None:
        update_data['question'] = req.question
    if req.answer is not None:
        update_data['answer'] = req.answer
    if req.category is not None:
        update_data['category'] = req.category
    if req.sort_order is not None:
        update_data['sort_order'] = req.sort_order
    if req.is_active is not None:
        update_data['is_active'] = req.is_active

    await db.faqs.update_one({'id': faq_id}, {'$set': update_data})
    return await db.faqs.find_one({'id': faq_id})

@admin_support_router.delete("/faqs/{faq_id}")
async def admin_delete_faq(faq_id: str):
    """Delete a FAQ."""
    await db.faqs.delete_one({'id': faq_id})
    return {'deleted': True}


# ============ Admin: Surge Pricing ============

@admin_support_router.put("/service-areas/{area_id}/surge")
async def admin_update_surge(area_id: str, req: UpdateSurgeRequest):
    """Update surge pricing for a service area."""
    update_data: Dict[str, Any] = {}
    if req.surge_active is not None:
        update_data['surge_active'] = req.surge_active
    if req.surge_multiplier is not None:
        if req.surge_multiplier < 1.0 or req.surge_multiplier > 10.0:
            raise HTTPException(status_code=400, detail="Multiplier must be between 1.0 and 10.0")
        update_data['surge_multiplier'] = req.surge_multiplier

    if update_data:
        await db.service_areas.update_one({'id': area_id}, {'$set': update_data})

    area = await db.service_areas.find_one({'id': area_id})
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")
    return area


# ============ Admin: Area Fees (Pricing) ============

pricing_router = APIRouter(prefix="/api/admin")


class CreateAreaFeeRequest(BaseModel):
    fee_name: str
    fee_type: str = "custom"        # airport | night | toll | event | holiday | custom
    calc_mode: str = "flat"         # flat | per_km | percentage
    amount: float = 0.0
    description: Optional[str] = None
    conditions: Dict[str, Any] = {}  # e.g. {"start_hour": 23, "end_hour": 5}
    is_active: bool = True


class UpdateAreaFeeRequest(BaseModel):
    fee_name: Optional[str] = None
    fee_type: Optional[str] = None
    calc_mode: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class UpdateTaxConfigRequest(BaseModel):
    gst_enabled: Optional[bool] = None
    gst_rate: Optional[float] = None
    pst_enabled: Optional[bool] = None
    pst_rate: Optional[float] = None
    hst_enabled: Optional[bool] = None
    hst_rate: Optional[float] = None


@pricing_router.get("/areas/{area_id}/fees")
async def get_area_fees(area_id: str):
    """Get all fees for a service area."""
    fees = await db.area_fees.find({'service_area_id': area_id}).sort('created_at', 1).to_list(100)
    return fees


@pricing_router.post("/areas/{area_id}/fees")
async def create_area_fee(area_id: str, req: CreateAreaFeeRequest):
    """Add a fee to a service area."""
    area = await db.service_areas.find_one({'id': area_id})
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")

    valid_modes = ['flat', 'per_km', 'percentage']
    if req.calc_mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"calc_mode must be one of: {valid_modes}")

    fee = {
        'id': str(uuid.uuid4()),
        'service_area_id': area_id,
        'fee_name': req.fee_name,
        'fee_type': req.fee_type,
        'calc_mode': req.calc_mode,
        'amount': req.amount,
        'description': req.description,
        'conditions': req.conditions,
        'is_active': req.is_active,
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    }
    await db.area_fees.insert_one(fee)
    return fee


@pricing_router.put("/areas/{area_id}/fees/{fee_id}")
async def update_area_fee(area_id: str, fee_id: str, req: UpdateAreaFeeRequest):
    """Update an area fee."""
    update_data: Dict[str, Any] = {'updated_at': datetime.utcnow()}
    for field in ['fee_name', 'fee_type', 'calc_mode', 'amount', 'description', 'conditions', 'is_active']:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val

    if 'calc_mode' in update_data and update_data['calc_mode'] not in ['flat', 'per_km', 'percentage']:
        raise HTTPException(status_code=400, detail="calc_mode must be flat, per_km, or percentage")

    await db.area_fees.update_one({'id': fee_id, 'service_area_id': area_id}, {'$set': update_data})
    return await db.area_fees.find_one({'id': fee_id})


@pricing_router.delete("/areas/{area_id}/fees/{fee_id}")
async def delete_area_fee(area_id: str, fee_id: str):
    """Delete an area fee."""
    await db.area_fees.delete_one({'id': fee_id, 'service_area_id': area_id})
    return {'deleted': True}


@pricing_router.put("/areas/{area_id}/tax")
async def update_area_tax(area_id: str, req: UpdateTaxConfigRequest):
    """Update tax configuration for a service area."""
    update_data: Dict[str, Any] = {}
    for field in ['gst_enabled', 'gst_rate', 'pst_enabled', 'pst_rate', 'hst_enabled', 'hst_rate']:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val

    if update_data:
        await db.service_areas.update_one({'id': area_id}, {'$set': update_data})

    area = await db.service_areas.find_one({'id': area_id})
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")
    return {
        'gst_enabled': area.get('gst_enabled', True),
        'gst_rate': area.get('gst_rate', 5.0),
        'pst_enabled': area.get('pst_enabled', False),
        'pst_rate': area.get('pst_rate', 0.0),
        'hst_enabled': area.get('hst_enabled', False),
        'hst_rate': area.get('hst_rate', 0.0),
    }


@pricing_router.get("/areas/{area_id}/tax")
async def get_area_tax(area_id: str):
    """Get tax configuration for a service area."""
    area = await db.service_areas.find_one({'id': area_id})
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")
    return {
        'gst_enabled': area.get('gst_enabled', True),
        'gst_rate': area.get('gst_rate', 5.0),
        'pst_enabled': area.get('pst_enabled', False),
        'pst_rate': area.get('pst_rate', 0.0),
        'hst_enabled': area.get('hst_enabled', False),
        'hst_rate': area.get('hst_rate', 0.0),
    }


@pricing_router.get("/areas/{area_id}/vehicle-pricing")
async def get_vehicle_pricing(area_id: str):
    """Get all fare configs for a service area grouped by vehicle type."""
    configs = await db.fare_configs.find({'service_area_id': area_id}).to_list(50)
    vehicles = await db.vehicle_types.find().to_list(50)
    return {'fare_configs': configs, 'vehicle_types': vehicles}


@pricing_router.put("/drivers/{driver_id}/area")
async def assign_driver_area(driver_id: str, service_area_id: str = Query(...)):
    """Assign a driver to a service area (restricts them to that zone)."""
    area = await db.service_areas.find_one({'id': service_area_id})
    if not area:
        raise HTTPException(status_code=404, detail="Service area not found")

    await db.drivers.update_one(
        {'id': driver_id},
        {'$set': {'service_area_id': service_area_id}}
    )
    return {'driver_id': driver_id, 'service_area_id': service_area_id, 'area_name': area.get('name')}


# ============ Fee Calculation Helpers ============

async def calculate_all_fees(
    pickup_lat: float, pickup_lng: float,
    dropoff_lat: float, dropoff_lng: float,
    distance_km: float, subtotal: float,
    ride_time_hour: Optional[int] = None
) -> Dict[str, Any]:
    """Calculate all area fees + taxes for a ride based on pickup/dropoff location.
    Returns {'fees': [...], 'fees_total': float, 'tax_amount': float, 'tax_breakdown': {...}, 'grand_total': float}
    """
    from datetime import datetime as dt
    if ride_time_hour is None:
        ride_time_hour = dt.utcnow().hour

    # Find which service area the pickup is in
    all_areas = await db.service_areas.find({'is_active': True}).to_list(100)
    matched_area = None
    for area in all_areas:
        polygon = area.get('polygon', [])
        if len(polygon) >= 3 and point_in_polygon(pickup_lat, pickup_lng, polygon):
            matched_area = area
            break

    result = {
        'fees': [],
        'fees_total': 0.0,
        'tax_amount': 0.0,
        'tax_breakdown': {},
        'service_area_id': matched_area['id'] if matched_area else None,
        'service_area_name': matched_area.get('name') if matched_area else None,
    }

    if not matched_area:
        return result

    # Get all active fees for this area
    area_fees_list = await db.area_fees.find({
        'service_area_id': matched_area['id'],
        'is_active': True
    }).to_list(50)

    fees_total = 0.0
    fee_items = []

    for fee in area_fees_list:
        fee_type = fee.get('fee_type', 'custom')
        calc_mode = fee.get('calc_mode', 'flat')
        amount = float(fee.get('amount', 0))
        conditions = fee.get('conditions', {})

        # Check conditions
        if fee_type == 'night':
            start_h = conditions.get('start_hour', 23)
            end_h = conditions.get('end_hour', 5)
            if start_h > end_h:  # Crosses midnight (e.g., 23-5)
                if not (ride_time_hour >= start_h or ride_time_hour < end_h):
                    continue
            else:
                if not (start_h <= ride_time_hour < end_h):
                    continue

        if fee_type == 'airport':
            # Check if pickup or dropoff is in an airport zone
            airport_areas = await db.service_areas.find({'is_airport': True}).to_list(20)
            in_airport = False
            for ap in airport_areas:
                ap_poly = ap.get('polygon', [])
                if len(ap_poly) >= 3:
                    if point_in_polygon(pickup_lat, pickup_lng, ap_poly) or \
                       point_in_polygon(dropoff_lat, dropoff_lng, ap_poly):
                        in_airport = True
                        break
            if not in_airport:
                continue

        # Calculate the fee amount based on calc_mode
        if calc_mode == 'flat':
            fee_value = amount
        elif calc_mode == 'per_km':
            fee_value = amount * distance_km
        elif calc_mode == 'percentage':
            fee_value = (amount / 100.0) * subtotal
        else:
            fee_value = amount

        fee_value = round(fee_value, 2)
        fees_total += fee_value
        fee_items.append({
            'id': fee.get('id'),
            'name': fee.get('fee_name'),
            'type': fee_type,
            'calc_mode': calc_mode,
            'amount': amount,
            'calculated_value': fee_value,
        })

    result['fees'] = fee_items
    result['fees_total'] = round(fees_total, 2)

    # Calculate taxes
    taxable_amount = subtotal + fees_total
    tax_breakdown = {}
    tax_total = 0.0

    if matched_area.get('hst_enabled'):
        hst_rate = float(matched_area.get('hst_rate', 0))
        hst_amount = round(taxable_amount * (hst_rate / 100.0), 2)
        tax_breakdown['HST'] = {'rate': hst_rate, 'amount': hst_amount}
        tax_total += hst_amount
    else:
        if matched_area.get('gst_enabled', True):
            gst_rate = float(matched_area.get('gst_rate', 5.0))
            gst_amount = round(taxable_amount * (gst_rate / 100.0), 2)
            tax_breakdown['GST'] = {'rate': gst_rate, 'amount': gst_amount}
            tax_total += gst_amount

        if matched_area.get('pst_enabled', False):
            pst_rate = float(matched_area.get('pst_rate', 0))
            pst_amount = round(taxable_amount * (pst_rate / 100.0), 2)
            tax_breakdown['PST'] = {'rate': pst_rate, 'amount': pst_amount}
            tax_total += pst_amount

    result['tax_amount'] = round(tax_total, 2)
    result['tax_breakdown'] = tax_breakdown

    return result


@support_router.get("/rides/fare-estimate")
async def fare_estimate(
    pickup_lat: float = Query(...), pickup_lng: float = Query(...),
    dropoff_lat: float = Query(...), dropoff_lng: float = Query(...),
    distance_km: float = Query(...), duration_minutes: int = Query(...),
    vehicle_type_id: str = Query(...),
):
    """Full fare estimate including base fare, area fees, and taxes."""
    # Get fare config
    fare_config = await db.fare_configs.find_one({'vehicle_type_id': vehicle_type_id})
    if fare_config:
        base_fare = fare_config.get('base_fare', 3.50)
        distance_fare = distance_km * fare_config.get('per_km_rate', 1.50)
        time_fare = duration_minutes * fare_config.get('per_minute_rate', 0.25)
        booking_fee = fare_config.get('booking_fee', 2.0)
        minimum_fare = fare_config.get('minimum_fare', 8.0)
    else:
        base_fare, distance_fare = 3.50, distance_km * 1.50
        time_fare, booking_fee, minimum_fare = duration_minutes * 0.25, 2.0, 8.0

    subtotal = max(base_fare + distance_fare + time_fare + booking_fee, minimum_fare)

    # Calculate area fees + taxes
    fees_result = await calculate_all_fees(
        pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
        distance_km, subtotal
    )

    grand_total = round(subtotal + fees_result['fees_total'] + fees_result['tax_amount'], 2)

    return {
        'base_fare': round(base_fare, 2),
        'distance_fare': round(distance_fare, 2),
        'time_fare': round(time_fare, 2),
        'booking_fee': booking_fee,
        'subtotal': round(subtotal, 2),
        'area_fees': fees_result['fees'],
        'area_fees_total': fees_result['fees_total'],
        'tax_amount': fees_result['tax_amount'],
        'tax_breakdown': fees_result['tax_breakdown'],
        'grand_total': grand_total,
        'service_area': fees_result.get('service_area_name'),
    }


# ============ Scheduled Rides ============

@support_router.post("/rides/schedule")
async def schedule_ride(req: ScheduleRideRequest):
    """Schedule a ride for a future time."""
    try:
        scheduled_dt = datetime.fromisoformat(req.scheduled_time.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scheduled_time format. Use ISO 8601.")

    if scheduled_dt < datetime.utcnow() + timedelta(minutes=15):
        raise HTTPException(status_code=400, detail="Scheduled time must be at least 15 minutes from now.")

    # Compute fare like a normal ride
    # Look up fare config
    areas = await db.service_areas.find().to_list(100)
    # For simplicity, use first active area (in production, match pickup location to area polygon)
    fare_config = await db.fare_configs.find_one({'vehicle_type_id': req.vehicle_type_id})

    if fare_config:
        base_fare = fare_config.get('base_fare', 3.50)
        distance_fare = req.distance_km * fare_config.get('per_km_rate', 1.50)
        time_fare = req.duration_minutes * fare_config.get('per_minute_rate', 0.25)
        booking_fee = fare_config.get('booking_fee', 2.0)
        total = max(base_fare + distance_fare + time_fare + booking_fee, fare_config.get('minimum_fare', 8.0))
    else:
        base_fare = 3.50
        distance_fare = req.distance_km * 1.50
        time_fare = req.duration_minutes * 0.25
        booking_fee = 2.0
        total = max(base_fare + distance_fare + time_fare + booking_fee, 8.0)

    # Apply surge if active
    for area in areas:
        if area.get('surge_active') and area.get('surge_multiplier', 1.0) > 1.0:
            surge = area['surge_multiplier']
            distance_fare *= surge
            time_fare *= surge
            total = max(base_fare + distance_fare + time_fare + booking_fee, fare_config.get('minimum_fare', 8.0) if fare_config else 8.0)
            break

    # Apply airport fee if pickup or dropoff is in an airport zone
    airport_result = await calculate_airport_fee(
        req.pickup_lat, req.pickup_lng, req.dropoff_lat, req.dropoff_lng
    )
    airport_fee = airport_result['airport_fee']
    total += airport_fee

    ride = {
        'id': str(uuid.uuid4()),
        'rider_id': req.rider_id,
        'vehicle_type_id': req.vehicle_type_id,
        'pickup_address': req.pickup_address,
        'pickup_lat': req.pickup_lat,
        'pickup_lng': req.pickup_lng,
        'dropoff_address': req.dropoff_address,
        'dropoff_lat': req.dropoff_lat,
        'dropoff_lng': req.dropoff_lng,
        'distance_km': req.distance_km,
        'duration_minutes': req.duration_minutes,
        'base_fare': round(base_fare, 2),
        'distance_fare': round(distance_fare, 2),
        'time_fare': round(time_fare, 2),
        'booking_fee': booking_fee,
        'airport_fee': round(airport_fee, 2),
        'airport_zone_name': airport_result.get('airport_zone_name'),
        'total_fare': round(total, 2),
        'driver_earnings': round(total - booking_fee, 2),
        'admin_earnings': round(booking_fee + airport_fee, 2),
        'status': 'scheduled',
        'is_scheduled': True,
        'scheduled_time': scheduled_dt,
        'stops': req.stops,
        'ride_requested_at': datetime.utcnow(),
        'created_at': datetime.utcnow(),
        'updated_at': datetime.utcnow(),
    }

    await db.rides.insert_one(ride)
    return ride

@support_router.get("/rides/scheduled")
async def get_scheduled_rides(user_id: str = Query(...)):
    """Get all scheduled rides for a user."""
    rides = await db.rides.find({
        'rider_id': user_id,
        'is_scheduled': True,
        'status': 'scheduled',
    }).sort('scheduled_time', 1).to_list(50)
    return rides

@support_router.delete("/rides/scheduled/{ride_id}")
async def cancel_scheduled_ride(ride_id: str):
    """Cancel a scheduled ride."""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get('status') != 'scheduled':
        raise HTTPException(status_code=400, detail="Only scheduled rides can be cancelled this way")

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {'status': 'cancelled', 'cancelled_at': datetime.utcnow()}}
    )
    return {'cancelled': True}


# ============ Multi-stop Rides ============

@support_router.post("/rides/{ride_id}/stops")
async def add_stop(ride_id: str, req: AddStopRequest):
    """Add a stop to an existing ride."""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get('status') in ['completed', 'cancelled']:
        raise HTTPException(status_code=400, detail="Cannot add stops to completed/cancelled rides")

    stops = ride.get('stops', [])
    new_stop = {
        'id': str(uuid.uuid4()),
        'address': req.address,
        'lat': req.lat,
        'lng': req.lng,
        'order': req.order,
        'arrived_at': None,
        'completed_at': None,
    }
    stops.append(new_stop)
    stops.sort(key=lambda s: s.get('order', 0))

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {'stops': stops, 'updated_at': datetime.utcnow()}}
    )
    return {'stops': stops}

@support_router.put("/rides/{ride_id}/stops/{stop_id}/complete")
async def complete_stop(ride_id: str, stop_id: str):
    """Mark a stop as completed."""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    stops = ride.get('stops', [])
    for stop in stops:
        if stop.get('id') == stop_id:
            stop['completed_at'] = datetime.utcnow().isoformat()
            stop['arrived_at'] = stop.get('arrived_at') or datetime.utcnow().isoformat()
            break
    else:
        raise HTTPException(status_code=404, detail="Stop not found")

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {'stops': stops, 'updated_at': datetime.utcnow()}}
    )
    return {'stops': stops}


# ============ Safety Toolkit ============

@support_router.post("/rides/{ride_id}/share")
async def share_trip(ride_id: str, req: ShareTripRequest):
    """Share a live trip link with a contact."""
    ride = await db.rides.find_one({'id': ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Generate or reuse the share token
    token = ride.get('shared_trip_token') or secrets.token_urlsafe(32)

    contacts = ride.get('shared_trip_contacts', [])
    contacts.append({
        'name': req.contact_name,
        'phone': req.contact_phone,
        'shared_at': datetime.utcnow().isoformat(),
    })

    await db.rides.update_one(
        {'id': ride_id},
        {'$set': {
            'shared_trip_token': token,
            'shared_trip_contacts': contacts,
        }}
    )

    # The share URL format – frontend or web page would render this
    share_url = f"/trip/live/{token}"

    return {
        'share_url': share_url,
        'token': token,
        'contacts': contacts,
    }

@support_router.get("/trip/live/{token}")
async def get_shared_trip(token: str):
    """Get live trip info via share token (no auth required)."""
    ride = await db.rides.find_one({'shared_trip_token': token})
    if not ride:
        raise HTTPException(status_code=404, detail="Trip not found or link expired")

    # Return safe subset of ride data  – no sensitive info
    return {
        'status': ride.get('status'),
        'pickup_address': ride.get('pickup_address'),
        'dropoff_address': ride.get('dropoff_address'),
        'pickup_lat': ride.get('pickup_lat'),
        'pickup_lng': ride.get('pickup_lng'),
        'dropoff_lat': ride.get('dropoff_lat'),
        'dropoff_lng': ride.get('dropoff_lng'),
        'driver_name': None,  # Would be populated from driver lookup
        'vehicle_info': None,
        'stops': ride.get('stops', []),
        'ride_started_at': str(ride.get('ride_started_at', '')),
    }


# ============ Push Notification Helpers ============

@support_router.post("/users/fcm-token")
async def register_fcm_token(req: RegisterFcmTokenRequest, user_id: str = Query(...)):
    """Register/update the user's FCM token for push notifications."""
    await db.users.update_one(
        {'id': user_id},
        {'$set': {'fcm_token': req.token}}
    )
    return {'registered': True}


async def send_push_notification(user_id: str, title: str, body: str, data: Dict[str, str] = {}):
    """Send a push notification to a user via Firebase Cloud Messaging."""
    try:
        from firebase_admin import messaging
    except ImportError:
        logger.warning("firebase_admin not available for push notifications")
        return False

    user = await db.users.find_one({'id': user_id})
    if not user or not user.get('fcm_token'):
        logger.info(f"No FCM token for user {user_id}")
        return False

    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data=data,
            token=user['fcm_token'],
        )
        response = await asyncio.to_thread(messaging.send, message)
        logger.info(f"Push notification sent to {user_id}: {response}")
        return True
    except Exception as e:
        logger.error(f"Failed to send push notification: {e}")
        return False


@admin_support_router.post("/notifications/send")
async def admin_send_notification(req: SendNotificationRequest):
    """Send a push notification to a specific user (admin)."""
    success = await send_push_notification(req.user_id, req.title, req.body, req.data)
    return {'sent': success}


# ============ Scheduled Ride Background Checker ============

async def check_scheduled_rides():
    """Background task: dispatches scheduled rides when their time arrives."""
    while True:
        try:
            now = datetime.utcnow()
            # Find rides scheduled within the next 5 minutes
            window = now + timedelta(minutes=5)

            scheduled = await db.rides.find({
                'status': 'scheduled',
                'is_scheduled': True,
            }).to_list(50)

            for ride in scheduled:
                sched_time = ride.get('scheduled_time')
                if sched_time and isinstance(sched_time, str):
                    sched_time = datetime.fromisoformat(sched_time.replace('Z', '+00:00'))

                if sched_time and sched_time <= window:
                    # Transition to "searching" so the normal matching logic picks it up
                    await db.rides.update_one(
                        {'id': ride['id']},
                        {'$set': {
                            'status': 'searching',
                            'ride_requested_at': datetime.utcnow(),
                            'updated_at': datetime.utcnow(),
                        }}
                    )
                    logger.info(f"Dispatched scheduled ride {ride['id']}")

                    # Send push notification to rider
                    await send_push_notification(
                        ride['rider_id'],
                        "Ride Dispatched! 🚗",
                        f"Your scheduled ride to {ride.get('dropoff_address', 'destination')} is being matched with a driver.",
                        {'ride_id': ride['id'], 'type': 'scheduled_dispatch'}
                    )
        except Exception as e:
            logger.error(f"Scheduled ride checker error: {e}")

        await asyncio.sleep(60)  # Check every minute
