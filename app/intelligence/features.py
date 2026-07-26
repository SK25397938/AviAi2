def extract_features(aircraft: dict):
    """
    Converts raw aircraft data into ML feature vector.
    """
    return [
        aircraft.get("baro_altitude_m", 0) or 0,
        aircraft.get("velocity_mps", 0) or 0,
        aircraft.get("heading_deg", 0) or 0,
        aircraft.get("vertical_trend", 0) or 0,
    ]
