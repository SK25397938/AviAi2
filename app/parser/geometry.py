from math import sqrt, atan2, degrees


def distance(p1, p2):
    return sqrt(
        (p1[0] - p2[0]) ** 2 +
        (p1[1] - p2[1]) ** 2
    )


def length(line):
    return distance(
        line["start"],
        line["end"]
    )


def angle(line):
    x1, y1 = line["start"]
    x2, y2 = line["end"]

    return (
        degrees(
            atan2(
                y2 - y1,
                x2 - x1
            )
        ) + 360
    ) % 180


def midpoint(line):
    x1, y1 = line["start"]
    x2, y2 = line["end"]

    return (
        (x1 + x2) / 2,
        (y1 + y2) / 2
    )


def bbox(line):
    x1, y1 = line["start"]
    x2, y2 = line["end"]

    return {
        "xmin": min(x1, x2),
        "ymin": min(y1, y2),
        "xmax": max(x1, x2),
        "ymax": max(y1, y2)
    }


def normalize_angle(a):
    a %= 180

    if a < 0:
        a += 180

    return a