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


def get_aircraft_performance(
    aircraft_type
):

    return AIRCRAFT_PERFORMANCE.get(

        aircraft_type,

        {
            "spawn_altitude_ft": 10000,
            "spawn_speed_kts": 250
        }

    )