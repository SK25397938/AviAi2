import math


def heading(lat1, lon1, lat2, lon2):

    dlon = math.radians(lon2 - lon1)

    lat1 = math.radians(lat1)

    lat2 = math.radians(lat2)

    x = math.sin(dlon) * math.cos(lat2)

    y = (

        math.cos(lat1) *

        math.sin(lat2)

        -

        math.sin(lat1) *

        math.cos(lat2) *

        math.cos(dlon)

    )

    return (math.degrees(math.atan2(x, y)) + 360) % 360


def next_heading(graph, current, nxt):

    a = graph.get_node(current)

    b = graph.get_node(nxt)

    return heading(

        a["lat"],

        a["lon"],

        b["lat"],

        b["lon"]

    )