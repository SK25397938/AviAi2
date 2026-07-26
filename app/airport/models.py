from dataclasses import dataclass
from typing import Optional


@dataclass
class Runway:
    ref: str
    width: float
    geometry: list
    surface: Optional[str] = None


@dataclass
class Taxiway:
    name: Optional[str]
    geometry: list
    ref: Optional[str] = None
    direction: Optional[str] = None


@dataclass
class Gate:
    name: str
    position: tuple
    terminal: Optional[str] = None


@dataclass
class ParkingPosition:
    name: str
    position: tuple
    apron: Optional[str] = None
    terminal: Optional[str] = None


@dataclass
class HoldingPosition:
    name: Optional[str]
    position: tuple
    runway: Optional[str] = None
    taxiway: Optional[str] = None


@dataclass
class Apron:
    name: Optional[str]
    geometry: list