def explain_flight_state(aircraft: dict) -> str:
    """
    Generate aviation-style explanation for current flight state.
    """

    phase = aircraft.get("flight_phase")
    altitude = aircraft.get("baro_altitude_m")
    speed = aircraft.get("velocity_mps")
    callsign = aircraft.get("callsign", "Unknown aircraft")

    if phase == "GROUND":
        return f"{callsign} is on the ground, likely taxiing or parked."

    if phase == "TAKEOFF":
        return (
            f"{callsign} has recently departed and is in the takeoff phase, "
            f"accelerating through low altitude."
        )

    if phase == "CLIMB":
        return (
            f"{callsign} is climbing to its cruising altitude. "
            f"Current altitude is approximately {int(altitude)} meters."
        )

    if phase == "CRUISE":
        return (
            f"{callsign} is in cruise phase, maintaining a stable altitude "
            f"around {int(altitude)} meters at high speed."
        )

    if phase == "DESCENT":
        return (
            f"{callsign} is descending, likely preparing for arrival at its destination."
        )

    return f"{callsign} flight phase cannot be determined with current data."
