from dataclasses import dataclass


@dataclass
class Point:
    name: str
    lat: float
    lon: float


@dataclass
class Gate:
    point: Point
    altitude_ft: int
    speed_kts: int


@dataclass
class Localizer:
    ident: str
    frequency: float
    course: float


@dataclass
class Approach:

    runway: str

    entry: Gate

    vector: Gate

    intercept: Gate

    localizer: Localizer

    glideslope: float

    decision_altitude: int