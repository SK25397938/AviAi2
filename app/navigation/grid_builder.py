from .geometry import destination_point


def build_grid(
    centerline,
    runway,
    width_nm=6
):

    rows = []

    left_heading = (runway.heading - 90) % 360
    right_heading = (runway.heading + 90) % 360

    for node in centerline:

        left_lat, left_lon = destination_point(

            node["lat"],
            node["lon"],

            left_heading,

            width_nm

        )

        right_lat, right_lon = destination_point(

            node["lat"],
            node["lon"],

            right_heading,

            width_nm

        )

        rows.append(

            {

                "center": node,

                "left": {

                    "id": node["id"].replace("N", "L"),

                    "lat": left_lat,

                    "lon": left_lon

                },

                "right": {

                    "id": node["id"].replace("N", "R"),

                    "lat": right_lat,

                    "lon": right_lon

                }

            }

        )

    return rows