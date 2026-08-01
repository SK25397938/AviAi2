import math
EARTH_RADIUS_M = 6371000
TURN_RATE_DEG_PER_SEC = 3.0
CLIMB_RATE_FPM = 1500
DESCENT_RATE_FPM = 1500
ACCELERATION_KTS_PER_SEC = 2

def normalize_heading(angle):
    return angle % 360

def shortest_turn(current, target):
    return ((target - current + 540) % 360) - 180

def update_heading(aircraft, dt):
    diff = shortest_turn(
        aircraft.heading_deg,
        aircraft.target_heading_deg
    )
    max_turn = TURN_RATE_DEG_PER_SEC * dt
    if abs(diff) <= max_turn:
        aircraft.heading_deg = aircraft.target_heading_deg
    elif diff > 0:
        aircraft.heading_deg += max_turn
    else:
        aircraft.heading_deg -= max_turn
    aircraft.heading_deg = normalize_heading(
        aircraft.heading_deg
    )

def update_speed(aircraft, dt):
    diff = aircraft.target_speed_kts - aircraft.speed_kts
    step = ACCELERATION_KTS_PER_SEC * dt
    if abs(diff) <= step:
        aircraft.speed_kts = aircraft.target_speed_kts
    elif diff > 0:
        aircraft.speed_kts += step
    else:
        aircraft.speed_kts -= step

def update_altitude(aircraft, dt):
    diff = aircraft.target_altitude_ft - aircraft.altitude_ft
    if diff == 0:
        aircraft.vertical_speed_fpm = 0
        return
    if diff > 0:
        climb = CLIMB_RATE_FPM * dt / 60
        aircraft.altitude_ft += min(
            climb,
            diff
        )
        aircraft.vertical_speed_fpm = CLIMB_RATE_FPM
    else:
        descent = DESCENT_RATE_FPM * dt / 60
        aircraft.altitude_ft -= min(
            descent,
            -diff
        )
        aircraft.vertical_speed_fpm = -DESCENT_RATE_FPM

def move_aircraft(aircraft, dt=1.0):
    print(
    f"BEFORE {aircraft.callsign}",
    aircraft.lat,
    aircraft.lon,
    aircraft.heading_deg,
    aircraft.speed_kts
)
    update_heading(
        aircraft,
        dt
    )
    update_speed(
        aircraft,
        dt
    )
    update_altitude(
        aircraft,
        dt
    )
    speed_ms = aircraft.speed_kts * 0.514444
    distance = speed_ms * dt
    heading = math.radians(
        aircraft.heading_deg
    )
    lat = math.radians(
        aircraft.lat
    )
    lon = math.radians(
        aircraft.lon
    )
    new_lat = math.asin(
        math.sin(lat)
        * math.cos(distance / EARTH_RADIUS_M)
        +
        math.cos(lat)
        * math.sin(distance / EARTH_RADIUS_M)
        * math.cos(heading)
    )
    new_lon = lon + math.atan2(
        math.sin(heading)
        * math.sin(distance / EARTH_RADIUS_M)
        * math.cos(lat),
        math.cos(distance / EARTH_RADIUS_M)
        -
        math.sin(lat)
        * math.sin(new_lat)
    )
    aircraft.lat = math.degrees(new_lat)
    aircraft.lon = math.degrees(new_lon)
    print(
    f"AFTER  {aircraft.callsign}",
    aircraft.lat,
    aircraft.lon
)