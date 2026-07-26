from app.intelligence.nearest_airport import find_nearest_airport
import math


def bearing_deg(lat1, lon1, lat2, lon2):
    """Calculate bearing from point 1 to point 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - (
        math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    )

    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def heading_difference(h1, h2):
    """Smallest angle between two headings."""
    diff = abs(h1 - h2)
    return min(diff, 360 - diff)


def classify_flight_intent(lat, lon, heading):
    """
    Returns ARRIVAL / DEPARTURE / OVERFLIGHT
    """
    airport, distance_km = find_nearest_airport(lat, lon)

    if not airport or heading is None:
        return "OVERFLIGHT"

    airport_bearing = bearing_deg(lat, lon, airport["lat"], airport["lon"])
    diff = heading_difference(heading, airport_bearing)

    # Heading roughly towards airport
    if diff < 45 and distance_km < 80:
        return "ARRIVAL"

    # Heading away from airport
    if diff > 135 and distance_km < 80:
        return "DEPARTURE"

    return "OVERFLIGHT"
