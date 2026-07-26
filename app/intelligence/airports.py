import csv
import math
from pathlib import Path

AIRPORTS = []

EARTH_RADIUS_KM = 6371.0


def haversine(lat1, lon1, lat2, lon2):
    """Distance in km between two lat/lon points"""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + \
        math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2

    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_KM * c


def load_airports(csv_path: str):
    """Load global airports into memory"""
    global AIRPORTS
    AIRPORTS.clear()

    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Airport file not found: {csv_path}")

    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row["latitude_deg"] or not row["longitude_deg"]:
                continue

            AIRPORTS.append({
                "icao": row["ident"],
                "iata": row["iata_code"],
                "name": row["name"],
                "lat": float(row["latitude_deg"]),
                "lon": float(row["longitude_deg"]),
                "type": row["type"]
            })

    print(f"[AIRPORTS] Loaded {len(AIRPORTS)} airports globally")


def nearest_airport(lat, lon, max_km=30):
    """Find nearest airport within max_km"""
    nearest = None
    best_dist = max_km

    for ap in AIRPORTS:
        d = haversine(lat, lon, ap["lat"], ap["lon"])
        if d < best_dist:
            best_dist = d
            nearest = ap

    return nearest
