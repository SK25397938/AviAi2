from math import hypot


def _distance(a, b):

    return hypot(
        a[0] - b[0],
        a[1] - b[1]
    )


def apply_semantics(airport):

    taxiways = airport.get("taxiways", [])

    changed = True

    while changed:

        changed = False

        for tw in taxiways:

            geometry = tw.get("geometry", [])

            if len(geometry) < 2:
                continue

            if tw.get("name") is not None:
                continue

            start = geometry[0]
            end = geometry[-1]

            nearby = {}

            for other in taxiways:

                if other is tw:
                    continue

                other_geometry = other.get("geometry", [])

                if len(other_geometry) < 2:
                    continue

                if other.get("name") is None:
                    continue

                pts = (
                    other_geometry[0],
                    other_geometry[-1]
                )

                for p in pts:

                    if _distance(start, p) < 0.00015:
                        nearby[other["name"]] = nearby.get(other["name"], 0) + 1

                    if _distance(end, p) < 0.00015:
                        nearby[other["name"]] = nearby.get(other["name"], 0) + 1

            if nearby:

                tw["name"] = max(
                    nearby,
                    key=nearby.get
                )

                changed = True

    return airport