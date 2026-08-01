from dataclasses import dataclass


@dataclass
class Waypoint:

    name: str

    type: str

    lat: float

    lon: float


@dataclass
class Procedure:

    name: str

    category: str

    runway: list

    route: list