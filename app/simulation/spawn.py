import random

from app.aircraft.database import get_aircraft
from app.intelligence.aircraft_state import AircraftState
from app.navigation.geometry import destination_point


def spawn_aircraft(
    callsign,
    aircraft_type,
    airport_lat,
    airport_lon
):

    profile = get_aircraft(aircraft_type)

    distance = random.uniform(50, 70)

    bearing = random.uniform(0, 360)

    lat, lon = destination_point(
        airport_lat,
        airport_lon,
        bearing,
        distance
    )

    heading = (bearing + 180) % 360

    return AircraftState(
        callsign=callsign,
        aircraft_type=aircraft_type,
        lat=lat,
        lon=lon,
        altitude_ft=random.randint(
            profile["arrival_altitude"][0],
            profile["arrival_altitude"][1]
        ),
        speed_kts=random.randint(
            profile["arrival_speed"][0],
            profile["arrival_speed"][1]
        ),
        heading_deg=heading,
        vertical_speed_fpm=-1000
    )