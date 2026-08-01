from .geometry import destination_point


def generate_grid(runway, spacing_nm=1.0, length_nm=25, lateral_nm=6):

    threshold = runway["threshold"]

    outbound = (runway["heading"] + 180) % 360

    left = (outbound - 90) % 360
    right = (outbound + 90) % 360

    nodes = []

    distance = spacing_nm

    while distance <= length_nm:

        center = destination_point(
            threshold["lat"],
            threshold["lon"],
            outbound,
            distance
        )

        left_node = destination_point(
            center[0],
            center[1],
            left,
            lateral_nm
        )

        right_node = destination_point(
            center[0],
            center[1],
            right,
            lateral_nm
        )

        nodes.append({

            "center": {

                "id": f"N{int(distance)}",

                "lat": center[0],

                "lon": center[1]

            },

            "left": {

                "id": f"L{int(distance)}",

                "lat": left_node[0],

                "lon": left_node[1]

            },

            "right": {

                "id": f"R{int(distance)}",

                "lat": right_node[0],

                "lon": right_node[1]

            }

        })

        distance += spacing_nm

    return list(reversed(nodes))