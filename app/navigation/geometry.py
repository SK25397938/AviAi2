import math

EARTH_RADIUS = 6378137.0


def destination_point(lat, lon, bearing, distance_nm):

    distance = distance_nm * 1852

    lat = math.radians(lat)
    lon = math.radians(lon)

    bearing = math.radians(bearing)

    angular = distance / EARTH_RADIUS

    new_lat = math.asin(
        math.sin(lat) * math.cos(angular)
        +
        math.cos(lat) * math.sin(angular) * math.cos(bearing)
    )

    new_lon = lon + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat),
        math.cos(angular) - math.sin(lat) * math.sin(new_lat)
    )

    return (
        math.degrees(new_lat),
        math.degrees(new_lon)
    )