from fastapi import APIRouter, Query
try:
    from ..db import db
    from ..utils import point_in_polygon
except ImportError:
    from db import db
    from utils import point_in_polygon

api_router = APIRouter(tags=["Fares"])

def serialize_doc(doc):
    return doc

@api_router.get("/vehicle-types")
async def get_vehicle_types():
    types = await db.vehicle_types.find({'is_active': True}).to_list(100)
    return serialize_doc(types)

@api_router.get("/fares")
async def get_fares_for_location(lat: float = Query(...), lng: float = Query(...)):
    # Use Python to find matching service area (since DB RPC might be missing/requires PostGIS)
    # areas = await db.rpc('get_service_area_for_point', {'lat': lat, 'lng': lng})
    all_areas = await db.service_areas.find({'is_active': True}).to_list(100)
    matching_area = None
    for area in all_areas:
        # Check if point is inside polygon
        # Ensure polygon is loaded correctly
        poly = area.get('polygon', [])
        if point_in_polygon(lat, lng, poly):
            matching_area = area
            break
    
    if not matching_area:
        vehicle_types = await db.vehicle_types.find({'is_active': True}).to_list(100)
        return [serialize_doc({
            'vehicle_type': vt,
            'base_fare': 3.50,
            'per_km_rate': 1.50,
            'per_minute_rate': 0.25,
            'minimum_fare': 8.00,
            'booking_fee': 2.00
        }) for vt in vehicle_types]
    
    fares = await db.fare_configs.find({
        'service_area_id': matching_area['id'],
        'is_active': True
    }).to_list(100)
    
    vehicle_types = await db.vehicle_types.find({'is_active': True}).to_list(100)
    vt_map = {vt['id']: serialize_doc(vt) for vt in vehicle_types}
    
    result = []
    for fare in fares:
        vt = vt_map.get(fare['vehicle_type_id'])
        if vt:
            result.append({
                'vehicle_type': vt,
                'base_fare': fare['base_fare'],
                'per_km_rate': fare['per_km_rate'],
                'per_minute_rate': fare['per_minute_rate'],
                'minimum_fare': fare['minimum_fare'],
                'booking_fee': fare['booking_fee']
            })
    
    return result
