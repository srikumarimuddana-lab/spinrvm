from fastapi import APIRouter, Query
try:
    from ..db import db
    from ..utils import point_in_polygon, get_service_area_polygon
except ImportError:
    from db import db
    from utils import point_in_polygon, get_service_area_polygon

api_router = APIRouter(tags=["Fares"])

def serialize_doc(doc):
    return doc

@api_router.get("/vehicle-types")
async def get_vehicle_types():
    types = await db.vehicle_types.find({'is_active': True}).to_list(100)
    return serialize_doc(types)

@api_router.get("/fares")
async def get_fares_for_location(lat: float = Query(...), lng: float = Query(...)):
    import logging
    logger = logging.getLogger(__name__)
    
    # Fetch all active vehicle types (needed for both paths)
    vehicle_types = await db.vehicle_types.find({'is_active': True}).to_list(100)
    logger.info(f"Fares: Found {len(vehicle_types)} active vehicle types")
    
    if not vehicle_types:
        logger.warning("Fares: No active vehicle types found in database!")
        return []
    
    # Default fares function (used when no service area or no fare_configs)
    def build_default_fares(vt_list, surge=1.0):
        return [serialize_doc({
            'vehicle_type': vt,
            'base_fare': 3.50,
            'per_km_rate': 1.50,
            'per_minute_rate': 0.25,
            'minimum_fare': 8.00,
            'booking_fee': 2.00,
            'surge_multiplier': surge
        }) for vt in vt_list]
    
    # Try to find matching service area
    all_areas = await db.service_areas.find({'is_active': True}).to_list(100)
    matching_area = None
    for area in all_areas:
        poly = get_service_area_polygon(area)
        if poly and point_in_polygon(lat, lng, poly):
            matching_area = area
            break
    
    if not matching_area:
        logger.info(f"Fares: No matching service area for ({lat}, {lng}), using defaults")
        return build_default_fares(vehicle_types)
    
    logger.info(f"Fares: Matched service area '{matching_area.get('name', matching_area['id'])}'")
    surge = matching_area.get('surge_multiplier', 1.0)
    
    # Try to get fare_configs for this service area
    fares = await db.fare_configs.find({
        'service_area_id': matching_area['id'],
        'is_active': True
    }).to_list(100)
    
    if not fares:
        # No fare configs for this area — fall back to defaults with area surge
        logger.info(f"Fares: No fare_configs for area, using defaults with surge={surge}")
        return build_default_fares(vehicle_types, surge)
    
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
                'booking_fee': fare['booking_fee'],
                'surge_multiplier': surge
            })
    
    # If fare_configs exist but none matched vehicle types, fall back
    if not result:
        logger.info("Fares: fare_configs found but no matching vehicle types, using defaults")
        return build_default_fares(vehicle_types, surge)
    
    logger.info(f"Fares: Returning {len(result)} fare estimates")
    return result
