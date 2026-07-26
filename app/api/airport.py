from pathlib import Path
import json
import math

from fastapi import APIRouter

from app.airport.graph.builder import AirportGraphBuilder
from app.airport.semantics import AirportSemantics

router = APIRouter()


def find_nan(obj, path="root"):

    if isinstance(obj, float):

        if math.isnan(obj):
            print("NaN FOUND:", path)

    elif isinstance(obj, dict):

        for k, v in obj.items():
            find_nan(v, f"{path}.{k}")

    elif isinstance(obj, list):

        for i, v in enumerate(obj):
            find_nan(v, f"{path}[{i}]")


@router.get("/airport/{icao}")
def airport(icao: str):

    path = Path(f"data/airports/{icao.upper()}.json")

    with open(path, encoding="utf-8") as f:

        data = json.load(f)

    find_nan(data)

    return data

@router.get("/airport/{icao}/graph")
def airport_graph(icao: str):

    airport_path = Path(f"data/airports/{icao.upper()}.json")

    with open(airport_path, encoding="utf-8") as f:
        airport = json.load(f)

    builder = AirportGraphBuilder(airport_path)
    graph = builder.build()

    semantics = AirportSemantics(icao)

    nodes = [
        {
            "id": node.id,
            "lat": node.latitude,
            "lon": node.longitude,
            "kind": node.kind
        }
        for node in graph.nodes.values()
    ]

    edges = [
        {
            "id": edge.id,
            "start": edge.start,
            "end": edge.end,
            "length": edge.length,
            "type": edge.edge_type,
            "name": edge.name
        }
        for edge in graph.edges.values()
    ]

    gates = []

    for gate in airport["gates"]:

        if not gate["position"]:
            continue

        node = graph.gates.get(gate["name"])

        gates.append({
            "name": gate["name"],
            "lat": gate["position"][1],
            "lon": gate["position"][0],
            "node": node
        })

    parking = []

    for stand in airport["parking_positions"]:

        if not stand["position"]:
            continue

        node = graph.parking_positions.get(stand["name"])

        parking.append({
            "name": stand["name"],
            "lat": stand["position"][1],
            "lon": stand["position"][0],
            "node": node
        })

    holding = []

    for i, hp in enumerate(airport["holding_positions"]):

        if not hp["position"]:
            continue

        name = f"H{i+1}"

        node = graph.holding_points.get(name)

        holding.append({
            "name": name,
            "lat": hp["position"][1],
            "lon": hp["position"][0],
            "node": node
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "gates": gates,
        "parking_positions": parking,
        "holding_points": holding,
        "runway_entries": graph.runway_entries,
        "runways": airport["runways"],
        "semantics": {
            "taxiways": semantics.taxiways(),
            "aprons": semantics.aprons(),
            "holding_points": semantics.holding_points(),
            "runway_entries": semantics.runway_entries(),
            "aliases": semantics.aliases()
        }
    }