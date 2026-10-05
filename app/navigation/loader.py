import json
from pathlib import Path


BASE = Path("data/airports/VABB")

ROUTES_FILE = (
    Path(__file__).resolve().parent
    / "routes"
    / "vabb_routes.json"
)

ARRIVAL_PROFILES_FILE = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "vabb_arrival_profiles.json"
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


def load_arrival_profiles():
    """Load optional route/waypoint arrival constraints."""
    try:
        with ARRIVAL_PROFILES_FILE.open(encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    routes = payload.get("routes", {}) if isinstance(payload, dict) else {}
    if not isinstance(routes, dict):
        return {}
    normalized = {}
    for route_id, waypoints in routes.items():
        if not isinstance(waypoints, dict):
            continue
        normalized[str(route_id)] = {
            str(name).upper(): profile
            for name, profile in waypoints.items()
            if isinstance(profile, dict)
        }
    return normalized


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
