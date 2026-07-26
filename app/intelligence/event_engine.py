def classify_event(aircraft: dict) -> str:
    altitude = aircraft.get("baro_altitude_m")
    speed = aircraft.get("velocity_mps")
    vertical = aircraft.get("vertical_state")
    behavior = aircraft.get("behavior")

    if altitude is None or speed is None:
        return "UNKNOWN"

    # Convert units
    altitude_ft = altitude * 3.28084
    speed_kts = speed * 1.94384

    # --- Descent logic ---
    if vertical == "RAPID_DESCENT":
        if altitude_ft < 12000 and speed_kts < 300:
            return "APPROACH_DESCENT"

        if altitude_ft > 15000 and speed_kts > 450:
            return "HIGH_ENERGY_DESCENT"

        return "UNSTABLE_DESCENT"

    # --- Level flight ---
    if vertical == "LEVEL":
        if altitude_ft > 25000:
            return "NORMAL_CRUISE"
        return "LEVEL_TRANSIT"

    # --- Climb ---
    if vertical == "ASCENDING":
        return "CLIMB_PHASE"

    return "NORMAL_OPERATION"
