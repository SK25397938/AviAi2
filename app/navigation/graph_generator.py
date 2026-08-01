from .geometry import destination_point


def generate_centerline(runway, spacing_nm=1.0, length_nm=25):

    threshold = runway["threshold"]

    outbound = (runway["heading"] + 180) % 360

    nodes = []

    distance = spacing_nm

    while distance <= length_nm:

        lat, lon = destination_point(
            threshold["lat"],
            threshold["lon"],
            outbound,
            distance
        )

        nodes.append({
            "id": f"N{int(distance)}",
            "distance_nm": distance,
            "lat": lat,
            "lon": lon
        })

        distance += spacing_nm

    return list(reversed(nodes))