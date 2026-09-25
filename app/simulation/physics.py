import math


EARTH_RADIUS_M = 6371000

TURN_RATE_DEG_PER_SEC = 3.0
TAXI_TURN_RATE_DEG_PER_SEC = 12.0

CLIMB_RATE_FPM = 1500
DESCENT_RATE_FPM = 1500

ACCELERATION_KTS_PER_SEC = 2.0


def normalize_heading(angle):

    return angle % 360


def shortest_turn(
    current,
    target
):

    return (
        (target - current + 540) % 360
    ) - 180


def update_heading(
    aircraft,
    dt
):

    if aircraft.target_heading_deg is None:

        return

    diff = shortest_turn(
        aircraft.heading_deg,
        aircraft.target_heading_deg
    )

    if aircraft.state == "TAXI":

        turn_rate = TAXI_TURN_RATE_DEG_PER_SEC

    else:

        turn_rate = TURN_RATE_DEG_PER_SEC

    max_turn = (
        turn_rate * dt
    )

    if abs(diff) <= max_turn:

        aircraft.heading_deg = (
            aircraft.target_heading_deg
        )

    elif diff > 0:

        aircraft.heading_deg += max_turn

    else:

        aircraft.heading_deg -= max_turn

    aircraft.heading_deg = normalize_heading(
        aircraft.heading_deg
    )


def update_speed(
    aircraft,
    dt
):

    if aircraft.target_speed_kts is None:

        return

    diff = (
        aircraft.target_speed_kts
        - aircraft.speed_kts
    )

    step = (
        ACCELERATION_KTS_PER_SEC * dt
    )

    if abs(diff) <= step:

        aircraft.speed_kts = (
            aircraft.target_speed_kts
        )

    elif diff > 0:

        aircraft.speed_kts += step

    else:

        aircraft.speed_kts -= step

    aircraft.speed_kts = max(
        0,
        aircraft.speed_kts
    )


def update_altitude(
    aircraft,
    dt
):

    if aircraft.target_altitude_ft is None:

        aircraft.vertical_speed_fpm = 0

        return

    diff = (
        aircraft.target_altitude_ft
        - aircraft.altitude_ft
    )

    if abs(diff) < 1:

        aircraft.altitude_ft = (
            aircraft.target_altitude_ft
        )

        aircraft.vertical_speed_fpm = 0

        return

    if diff > 0:

        climb = (
            CLIMB_RATE_FPM
            * dt
            / 60
        )

        aircraft.altitude_ft += min(
            climb,
            diff
        )

        aircraft.vertical_speed_fpm = (
            CLIMB_RATE_FPM
        )

    else:

        descent = (
            DESCENT_RATE_FPM
            * dt
            / 60
        )

        aircraft.altitude_ft -= min(
            descent,
            -diff
        )

        aircraft.vertical_speed_fpm = (
            -DESCENT_RATE_FPM
        )


def update_position(
    aircraft,
    dt
):

    speed_ms = (
        aircraft.speed_kts
        * 0.514444
    )

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

    angular_distance = (
        distance
        / EARTH_RADIUS_M
    )

    new_lat = math.asin(

        math.sin(lat)
        * math.cos(angular_distance)

        +

        math.cos(lat)
        * math.sin(angular_distance)
        * math.cos(heading)

    )

    new_lon = lon + math.atan2(

        math.sin(heading)
        * math.sin(angular_distance)
        * math.cos(lat),

        math.cos(angular_distance)

        -

        math.sin(lat)
        * math.sin(new_lat)

    )

    aircraft.lat = math.degrees(
        new_lat
    )

    aircraft.lon = math.degrees(
        new_lon
    )


def move_aircraft(
    aircraft,
    dt=1.0
):

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

    update_position(
        aircraft,
        dt
    )