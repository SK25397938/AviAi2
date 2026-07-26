from dataclasses import dataclass
from typing import Optional, List


@dataclass
class Runway:
    id: str
    designator: str
    opposite_designator: str
    length_m: float
    width_m: float
    surface: str
    coordinates: List[List[float]]
    active: bool = False


@dataclass
class Taxiway:
    id: str
    name: str
    coordinates: List[List[float]]
    direction: str = "BIDIRECTIONAL"
    status: str = "OPEN"


@dataclass
class Stand:
    id: str
    name: str
    latitude: float
    longitude: float
    terminal: Optional[str] = None
    status: str = "AVAILABLE"


@dataclass
class HoldingPoint:
    id: str
    name: str
    latitude: float
    longitude: float
    runway: Optional[str] = None
    taxiway: Optional[str] = None


@dataclass
class Airport:
    icao: str
    iata: str
    name: str
    latitude: float
    longitude: float
    elevation_ft: float
    runways: List[Runway]
    taxiways: List[Taxiway]
    stands: List[Stand]
    holding_points: List[HoldingPoint]