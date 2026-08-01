from app.navigation.runway import Runway
from app.navigation.centerline import build_centerline
from app.navigation.grid_builder import build_grid


runway = Runway(
    ident="27",
    threshold_lat=19.0887,
    threshold_lon=72.8679,
    heading=270,
    length_nm=25
)

centerline = build_centerline(runway)

grid = build_grid(
    centerline,
    runway
)

print(f"Generated {len(grid)} rows\n")

for row in grid:

    print(row)