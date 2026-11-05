"""Route-position profile helpers for VABB RWY 27 arrival initialization."""

import math
from app.navigation.speed_conversion import indicated_to_true_airspeed, mach_to_true_airspeed


def arrival_schedule_groundspeed(aircraft_type, altitude_ft):
    altitude = max(0.0, float(altitude_ft))
    aircraft = str(aircraft_type or "").upper()
    widebody = aircraft.startswith(("A33", "A35", "A38", "B74", "B77", "B78", "B787"))
    indicated_offset = 10.0 if aircraft.startswith(("A388", "A380", "B747")) else (
        5.0 if widebody else (2.0 if aircraft.startswith("A321") else 0.0)
    )
    maximum_mach = 0.80 if widebody else 0.78
    mach_fraction = max(0.0, min(1.0, (altitude - 20000.0) / 9000.0))
    mach = 0.70 + mach_fraction * (maximum_mach - 0.70)
    if altitude >= 22000.0:
        return mach_to_true_airspeed(mach, altitude)
    if altitude >= 18000.0:
        fraction = (altitude - 18000.0) / 4000.0
        indicated_schedule = indicated_to_true_airspeed(300.0 + indicated_offset, altitude)
        mach_schedule = mach_to_true_airspeed(mach, altitude)
        return indicated_schedule + fraction * (mach_schedule - indicated_schedule)
    if altitude >= 12000.0:
        indicated = 220.0 + (altitude - 12000.0) * (80.0 / 6000.0)
    elif altitude >= 10000.0:
        indicated = 220.0
    elif altitude >= 7000.0:
        indicated = 210.0 + (altitude - 7000.0) * (10.0 / 3000.0)
    elif altitude >= 4000.0:
        indicated = 180.0 + (altitude - 4000.0) * (30.0 / 3000.0)
    else:
        indicated = 150.0 + altitude * (30.0 / 4000.0)
    indicated += indicated_offset
    return indicated_to_true_airspeed(indicated, altitude)


def resolve_spawn_altitude(route_id, waypoints, profiles, latitude, longitude,
                           fallback_altitude_ft):
    """Interpolate the CSV's simulation altitude along the assigned route.

    Published rule types remain attached to their waypoints and are enforced by
    guidance there. This value initializes the aircraft between constraints;
    it does not turn carried values into published restrictions.
    """
    route_profiles = profiles.get(str(route_id), {}) if isinstance(profiles, dict) else {}
    ordered = []
    for point in waypoints:
        name = str(point.get("name", "")).upper()
        profile = route_profiles.get(name)
        if not isinstance(profile, dict):
            return {"altitude_ft": fallback_altitude_ft, "resolved": False,
                    "waypoint": name, "reason": "route waypoint has no CSV profile"}
        distance = profile.get("cum_nm")
        altitude = profile.get("profile_altitude_ft")
        if not isinstance(distance, (int, float)) or not isinstance(altitude, (int, float)):
            return {"altitude_ft": fallback_altitude_ft, "resolved": False,
                    "waypoint": name, "reason": "profile distance or simulation altitude is missing"}
        ordered.append((point, float(distance), float(altitude)))
    if not ordered:
        return {"altitude_ft": fallback_altitude_ft, "resolved": False,
                "waypoint": "<route start>", "reason": "route has no profiled waypoints"}

    cumulative = 0.0
    best = None
    for index, (point, _, _) in enumerate(ordered[:-1]):
        following = ordered[index + 1][0]
        mean_lat = math.radians((float(point["latitude"]) + float(following["latitude"])) / 2)
        scale_x = 60.0 * math.cos(mean_lat)
        dx = (float(following["longitude"]) - float(point["longitude"])) * scale_x
        dy = (float(following["latitude"]) - float(point["latitude"])) * 60.0
        px = (float(longitude) - float(point["longitude"])) * scale_x
        py = (float(latitude) - float(point["latitude"])) * 60.0
        length_sq = dx * dx + dy * dy
        fraction = 0.0 if length_sq == 0 else max(0.0, min(1.0, (px * dx + py * dy) / length_sq))
        distance_sq = (px - fraction * dx) ** 2 + (py - fraction * dy) ** 2
        start_nm, end_nm = ordered[index][1], ordered[index + 1][1]
        progress = start_nm + fraction * (end_nm - start_nm)
        candidate = (distance_sq, progress, index, fraction)
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None:
        return {"altitude_ft": ordered[0][2], "resolved": True,
                "progress_nm": ordered[0][1], "segment": (0, 0)}

    _, progress, index, fraction = best
    lower, upper = ordered[index], ordered[index + 1]
    span = upper[1] - lower[1]
    fraction = 0.0 if span <= 0 else max(0.0, min(1.0, (progress - lower[1]) / span))
    altitude = lower[2] + (upper[2] - lower[2]) * fraction
    return {"altitude_ft": round(altitude), "resolved": True,
            "progress_nm": progress, "segment": (index, index + 1),
            "start_waypoint": lower[0].get("name"), "end_waypoint": upper[0].get("name")}


def resolve_spawn_speed(waypoints, profiles, route_id, progress_nm, current_speed_kts):
    """Resolve the first applicable speed restriction at or downstream of a spawn."""
    route_profiles = profiles.get(str(route_id), {}) if isinstance(profiles, dict) else {}
    for point in waypoints:
        name = str(point.get("name", "")).upper()
        profile = route_profiles.get(name)
        if not isinstance(profile, dict):
            return {"speed_kts": current_speed_kts, "resolved": False, "waypoint": name}
        distance = profile.get("cum_nm")
        if not isinstance(distance, (int, float)):
            return {"speed_kts": current_speed_kts, "resolved": False, "waypoint": name}
        if distance + 1e-6 < progress_nm:
            continue
        constraint = profile.get("speed")
        if not isinstance(constraint, dict):
            continue
        speed = constraint.get("knots")
        if not isinstance(speed, (int, float)) or speed <= 0:
            return {"speed_kts": current_speed_kts, "resolved": False, "waypoint": name}
        value = (float(speed) if constraint.get("type") == "AT"
                 else min(float(current_speed_kts), float(speed)))
        return {"speed_kts": value, "resolved": True, "waypoint": name,
                "basis": constraint.get("basis"), "rule": constraint.get("type")}
    return {"speed_kts": current_speed_kts, "resolved": True,
            "waypoint": None, "basis": "no applicable restriction"}
