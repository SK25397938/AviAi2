from app.navigation.speed_conversion import indicated_to_true_airspeed


def match_departure_profile(route, aircraft_type, profiles):
    if not isinstance(route, dict) or not isinstance(profiles, dict):
        return None
    route_id = str(route.get("route_id", "")).strip()
    route_name = str(route.get("name", "")).strip().upper()
    aircraft = str(aircraft_type or "").strip().upper()
    aircraft = {
        "A350-900": "A359", "A350-1000": "A350-1000", "A35K": "A350-1000",
        "A380": "A388", "B772": "B777-200", "B77L": "B777-200LR",
        "B77W": "B777-300ER", "B77F": "B777F", "B788": "B787-8",
        "B789": "B787-9", "B78X": "B787-10", "B744": "B747-400",
        "B748": "B747-8", "B748F": "B747-8F",
    }.get(aircraft, aircraft)
    rows = profiles.get((route_id, route_name, aircraft))
    if not isinstance(rows, list) or not rows:
        return None
    route_names = [str(point.get("name", "")).strip().upper()
                   for point in route.get("waypoints", []) if isinstance(point, dict)]
    profile_names = [str(row.get("waypoint", "")).strip().upper() for row in rows]
    if route_names != profile_names:
        return None
    return rows


def departure_speed_limit_gs(constraint, altitude_ft):
    if not isinstance(constraint, dict) or constraint.get("unit") not in {"kt", "kts", "knots"}:
        return None
    operator = constraint.get("operator")
    if operator not in {"<=", "<", "=", "@"}:
        return None
    return indicated_to_true_airspeed(float(constraint["value"]), altitude_ft)


