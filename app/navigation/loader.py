import json
from pathlib import Path


BASE = Path("data/airports/VABB")

ROUTES_FILE = (
    Path(__file__).resolve().parent
    / "routes"
    / "vabb_routes.json"
)

DEPARTURE_ROUTES_FILE = (
    Path(__file__).resolve().parents[2]
    / "vabb_departure.json"
)


def load_airport():

    with open(
        BASE / "airport.json",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def load_runways():

    with open(
        BASE / "runways.json",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def load_navigation():

    with open(
        BASE / "navigation.json",
        encoding="utf-8"
    ) as file:

        return json.load(file)


def load_arrival_routes():

    with open(
        ROUTES_FILE,
        encoding="utf-8"
    ) as file:

        routes = json.load(file)["routes"]

    return {
        route["id"]: route
        for route in routes
    }


def load_departure_routes():

    with open(
        DEPARTURE_ROUTES_FILE,
        encoding="utf-8"
    ) as file:

        routes = json.load(file)

    return {
        route["route_id"]: route
        for route in routes.values()
    }