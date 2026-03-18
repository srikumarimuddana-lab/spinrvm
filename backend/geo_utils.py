import math
from typing import List, Dict, Any

def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

def get_service_area_polygon(area: Dict[str, Any]) -> List[Dict[str, float]]:
    """
    Return polygon as list of {lat, lng} from a service area row.
    Supports both 'polygon' (list of {lat, lng}) and 'geojson' (GeoJSON geometry; coordinates are [lng, lat]).
    """
    if not area:
        return []
    poly = area.get("polygon")
    if isinstance(poly, list) and len(poly) >= 3:
        return [{"lat": float(p.get("lat", 0)), "lng": float(p.get("lng", 0))} for p in poly]
    geojson = area.get("geojson")
    if isinstance(geojson, dict):
        geom = geojson.get("geometry", geojson)
        coords = geom.get("coordinates") if isinstance(geom, dict) else None
        if coords is not None:
            # GeoJSON Polygon: coordinates[0] is exterior ring, each point [lng, lat]
            ring = coords[0] if isinstance(coords[0], (list, tuple)) and coords else coords
            if isinstance(ring, (list, tuple)) and len(ring) >= 3:
                return [{"lat": float(c[1]), "lng": float(c[0])} for c in ring]
    if isinstance(geojson, list) and len(geojson) >= 3:
        # List of [lng, lat] or [lat, lng] - assume [lng, lat] per GeoJSON
        return [{"lat": float(c[1]), "lng": float(c[0])} for c in geojson]
    return []

def point_in_polygon(lat: float, lng: float, polygon: List[Dict[str, float]]) -> bool:
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        if ((polygon[i]['lng'] > lng) != (polygon[j]['lng'] > lng) and
            lat < (polygon[j]['lat'] - polygon[i]['lat']) * (lng - polygon[i]['lng']) / 
            (polygon[j]['lng'] - polygon[i]['lng']) + polygon[i]['lat']):
            inside = not inside
        j = i
    return inside
