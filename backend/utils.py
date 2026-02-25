import math
from typing import List, Dict

def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

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
