AIRCRAFT_PERFORMANCE = {

    # Airbus A320 family
    "A320": {
        "spawn_altitude_ft": 10000,
        "spawn_speed_kts": 250
    },

    # Airbus A350
    "A359": {
        "spawn_altitude_ft": 11000,
        "spawn_speed_kts": 260
    },


    # Boeing 737 family
    "B737": {
        "spawn_altitude_ft": 10000,
        "spawn_speed_kts": 250
    },

    "B737-700": {
        "spawn_altitude_ft": 10000,
        "spawn_speed_kts": 250
    },

    "B737-800": {
        "spawn_altitude_ft": 10000,
        "spawn_speed_kts": 250
    },

    "B737-900": {
        "spawn_altitude_ft": 10000,
        "spawn_speed_kts": 250
    },

    "B737-8": {
        "spawn_altitude_ft": 10000,
        "spawn_speed_kts": 250
    },

    "B737-9": {
        "spawn_altitude_ft": 10000,
        "spawn_speed_kts": 250
    },

    "B737-10": {
        "spawn_altitude_ft": 10000,
        "spawn_speed_kts": 250
    },


    # Boeing 787 Dreamliner
    "B788": {
        "spawn_altitude_ft": 11000,
        "spawn_speed_kts": 260
    },

    "B789": {
        "spawn_altitude_ft": 11000,
        "spawn_speed_kts": 260
    },

    "B78X": {
        "spawn_altitude_ft": 11000,
        "spawn_speed_kts": 260
    },


    # Boeing 777
    "B77W": {
        "spawn_altitude_ft": 12000,
        "spawn_speed_kts": 270
    },

    "B77F": {
        "spawn_altitude_ft": 12000,
        "spawn_speed_kts": 270
    },

    "B77L": {
        "spawn_altitude_ft": 12000,
        "spawn_speed_kts": 270
    },


    # Boeing 747
    "B744": {
        "spawn_altitude_ft": 13000,
        "spawn_speed_kts": 270
    },

    "B748": {
        "spawn_altitude_ft": 13000,
        "spawn_speed_kts": 270
    },

    "B748F": {
        "spawn_altitude_ft": 13000,
        "spawn_speed_kts": 270
    },


    # Airbus A380
    "A388": {
        "spawn_altitude_ft": 13000,
        "spawn_speed_kts": 270
    }

}

MINIMUM_ARRIVAL_SPEED_KTS = {
    "A320": 170, "A321": 175, "B737": 170, "B737-700": 170,
    "B737-800": 170, "B737-900": 170, "B737-8": 170, "B737-9": 170,
    "B737-10": 170, "A359": 180, "A350-900": 180, "A350-1000": 180,
    "B788": 180, "B789": 180, "B78X": 180, "B787-8": 180,
    "B787-9": 180, "B787-10": 180, "B77W": 185, "B77F": 185,
    "B77L": 185, "B777-200": 185, "B777-200LR": 185,
    "B777-300ER": 185, "B777F": 185, "B744": 185, "B747-400": 185,
    "B748": 185, "B748F": 185, "B747-8": 185, "B747-8F": 185,
    "A388": 185, "A380": 185,
}


def get_aircraft_performance(
    aircraft_type
):
    aliases = {
        "A350-900": "A359", "A350-1000": "A359", "B787-8": "B788",
        "B787-9": "B789", "B787-10": "B78X", "B777-300ER": "B77W",
        "B777-200LR": "B77L", "B747-8": "B748", "B747-8F": "B748F",
        "A380": "A388",
    }
    canonical = aliases.get(aircraft_type, aircraft_type)
    result = dict(AIRCRAFT_PERFORMANCE.get(
        canonical, {"spawn_altitude_ft": 10000, "spawn_speed_kts": 250}
    ))
    result["minimum_arrival_speed_kts"] = MINIMUM_ARRIVAL_SPEED_KTS.get(
        aircraft_type, MINIMUM_ARRIVAL_SPEED_KTS.get(canonical, 170)
    )
    return result
