import json
from pathlib import Path

BASE = Path("data/airports/VABB")
ROUTES_FILE = Path(__file__).resolve().parent / "routes" / "vabb_routes.json"


def load_airport():
    with open(BASE / "airport.json") as f:
        return json.load(f)


def load_runways():
    with open(BASE / "runways.json") as f:
        return json.load(f)


def load_navigation():
    with open(BASE / "navigation.json") as f:
        return json.load(f)


def load_arrival_routes():
    with open(ROUTES_FILE, encoding="utf-8") as file:
        routes = json.load(file)["routes"]

    return {
        route["id"]: route
        for route in routes
    }
