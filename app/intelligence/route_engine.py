from app.intelligence.airports import AIRPORTS
import math


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2 +
        math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def infer_route(lat, lon, vertical_state):
    if lat is None or lon is None or not AIRPORTS:
        return None, None

    nearest = None
    nearest_dist = float("inf")

    for airport in AIRPORTS:
        d = haversine(lat, lon, airport["lat"], airport["lon"])
        if d < nearest_dist:
            nearest_dist = d
            nearest = airport

    if not nearest:
        return None, None

    if vertical_state in ("ASCENDING", "RAPID_CLIMB"):
        return nearest["icao"], None

    if vertical_state in ("DESCENDING", "RAPID_DESCENT"):
        return None, nearest["icao"]

    return None, None
