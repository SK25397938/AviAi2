def detect_flight_phase(
    altitude_m: float | None,
    velocity_mps: float | None
) -> str:
    """
    Detect flight phase based on altitude and speed.
    Simple deterministic aviation logic.
    """

    if altitude_m is None or velocity_mps is None:
        return "UNKNOWN"

    # Ground / taxi
    if altitude_m < 50 and velocity_mps < 30:
        return "GROUND"

    # Takeoff / initial climb
    if altitude_m < 1000 and velocity_mps > 70:
        return "TAKEOFF"

    # Climb
    if 1000 <= altitude_m < 9000:
        return "CLIMB"

    # Cruise
    if altitude_m >= 9000 and velocity_mps > 200:
        return "CRUISE"

    # Descent
    if altitude_m >= 9000 and velocity_mps < 200:
        return "DESCENT"

    return "UNKNOWN"
