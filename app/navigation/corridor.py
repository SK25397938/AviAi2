from .geometry import destination_point


def generate_corridor(runway, aircraft):
    threshold = runway["threshold"]

    outbound = (runway["heading"] + 180) % 360

    speed = aircraft.approach_speed

    if speed <= 145:
        intercept_nm = 8
    elif speed <= 155:
        intercept_nm = 10
    else:
        intercept_nm = 12

    vector_nm = intercept_nm + 5
    entry_nm = vector_nm + 5
    gs_capture_nm = intercept_nm - 4

    return {
        "entry_gate": destination_point(
            threshold["lat"],
            threshold["lon"],
            outbound,
            entry_nm
        ),

        "vector_gate": destination_point(
            threshold["lat"],
            threshold["lon"],
            outbound,
            vector_nm
        ),

        "intercept_gate": destination_point(
            threshold["lat"],
            threshold["lon"],
            outbound,
            intercept_nm
        ),

        "glideslope_capture": destination_point(
            threshold["lat"],
            threshold["lon"],
            outbound,
            gs_capture_nm
        )
    }