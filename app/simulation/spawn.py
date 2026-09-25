from app.intelligence.aircraft_state import AircraftState as Aircraft
from app.aircraft.aircraft_type import (
    get_aircraft_performance
)


def spawn_aircraft(
    callsign,
    aircraft_type,
    latitude,
    longitude
):

    performance = get_aircraft_performance(
        aircraft_type
    )

    aircraft = Aircraft(

        callsign=callsign,

        aircraft_type=aircraft_type,

        lat=latitude,

        lon=longitude,

        altitude_ft=performance[
            "spawn_altitude_ft"
        ],

        speed_kts=performance[
            "spawn_speed_kts"
        ],

        heading_deg=270,

        vertical_speed_fpm=0

    )

    return aircraft
