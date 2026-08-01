from app.navigation.runway import Runway
from app.navigation.centerline import build_centerline


runway = Runway(
    ident="27",
    threshold_lat=19.0887,
    threshold_lon=72.8679,
    heading=270,
    length_nm=25
)

centerline = build_centerline(
    runway
)

print(f"Generated {len(centerline)} centerline nodes\n")

for node in centerline:

    print(node)