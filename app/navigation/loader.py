import json
from pathlib import Path

BASE = Path("data/airports/VABB")


def load_airport():
    with open(BASE / "airport.json") as f:
        return json.load(f)


def load_runways():
    with open(BASE / "runways.json") as f:
        return json.load(f)


def load_navigation():
    with open(BASE / "navigation.json") as f:
        return json.load(f)