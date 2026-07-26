def edge_midpoint(edge, nodes):

    a = nodes[edge["start"]]

    b = nodes[edge["end"]]

    return (

        (a["lat"] + b["lat"]) / 2,

        (a["lon"] + b["lon"]) / 2

    )


def nearest_edge(lat, lon, edges, nodes):

    best = None

    best_dist = 1e9

    for edge in edges:

        mlat, mlon = edge_midpoint(

            edge,

            nodes

        )

        d = (

            (mlat - lat) ** 2

            +

            (mlon - lon) ** 2

        )

        if d < best_dist:

            best_dist = d

            best = edge["id"]

    return best