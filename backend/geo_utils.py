import math
from typing import Any, Dict, List


def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def multi_leg_distance(
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    stops: List[Dict[str, Any]] | None = None,
) -> float:
    """Total distance (km) of the route pickup → stops → dropoff.

    Sums the haversine length of each leg through the intermediate stops, so a
    multi-stop ride is priced on the actual detour rather than the straight
    pickup→dropoff line. With no stops this is identical to a single
    ``calculate_distance`` call.

    Stops missing coordinates, or carrying the placeholder ``(0, 0)`` an
    unfilled stop field sends from the rider app, are skipped — they must not
    inflate the fare or bend the route into the ocean off West Africa.
    """
    points = [(pickup_lat, pickup_lng)]
    for stop in stops or []:
        lat = stop.get("lat")
        lng = stop.get("lng")
        # `0`/`0.0` placeholder or missing coord → not a real stop. A genuine
        # Saskatchewan coordinate is never exactly on the equator/prime meridian.
        if not lat or not lng:
            continue
        points.append((lat, lng))
    points.append((dropoff_lat, dropoff_lng))
    return sum(
        calculate_distance(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
        for i in range(len(points) - 1)
    )


def get_service_area_polygon(area: Dict[str, Any]) -> List[Dict[str, float]]:
    """
    Return polygon as list of {lat, lng} from a service area row.
    Supports both 'polygon' (list of {lat, lng}) and 'geojson' (GeoJSON geometry; coordinates are [lng, lat]).
    The admin dashboard stores GeoJSON dicts in the polygon column, so both are handled.
    """
    if not area:
        return []
    poly = area.get("polygon")
    if isinstance(poly, list) and len(poly) >= 3:
        return [{"lat": float(p.get("lat", 0)), "lng": float(p.get("lng", 0))} for p in poly]
    # Admin dashboard stores GeoJSON dict in the polygon column; fall through to GeoJSON parsing.
    geojson = poly if isinstance(poly, dict) else area.get("geojson")
    if isinstance(geojson, dict):
        geom = geojson.get("geometry", geojson)
        coords = geom.get("coordinates") if isinstance(geom, dict) else None
        if coords:
            # GeoJSON Polygon: coordinates[0] is exterior ring, each point [lng, lat].
            # Guard against empty `coords` before indexing — admins occasionally save
            # malformed shapes via the Service Areas screen and we don't want that to
            # IndexError inside the airport-fee check.
            ring = coords[0] if isinstance(coords[0], (list, tuple)) else coords
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
        if (polygon[i]["lng"] > lng) != (polygon[j]["lng"] > lng) and lat < (polygon[j]["lat"] - polygon[i]["lat"]) * (
            lng - polygon[i]["lng"]
        ) / (polygon[j]["lng"] - polygon[i]["lng"]) + polygon[i]["lat"]:
            inside = not inside
        j = i
    return inside
