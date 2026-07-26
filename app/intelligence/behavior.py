def classify_behavior(altitude, speed, vertical_state):
    if altitude is None or speed is None:
        return "INSUFFICIENT_DATA"

    # Climb logic
    if vertical_state in ["ASCENDING", "RAPID_CLIMB"]:
        return "CLIMB"

    # Descent logic
    if vertical_state == "RAPID_DESCENT" and altitude > 8000:
        return "DESCENT_INITIATED"

    if vertical_state == "DESCENDING":
        if altitude < 3000:
            return "FINAL_APPROACH"
        return "DESCENT"

    # Level logic
    if vertical_state == "LEVEL":
        if altitude > 8000 and speed > 200:
            return "CRUISE"
        if altitude < 3000:
            return "APPROACH"

    return "NORMAL_OPERATION"
