from .geometry import destination_point
def build_centerline(
    runway,
    spacing_nm=1.0
):
    nodes = []
    distance = runway.length_nm
    while distance >= 1:
        lat, lon = destination_point(
            runway.threshold_lat,
            runway.threshold_lon,
            runway.heading,
            distance
        )
        nodes.append(
            {
                "id": f"N{int(distance)}",
                "distance_nm": distance,
                "lat": lat,
                "lon": lon
            }
        )
        distance -= spacing_nm
    return nodes