from dataclasses import dataclass, field


@dataclass
class AirportData:
    icao: str = ""
    name: str = ""
    elevation_ft: float = 0

    runways: list = field(default_factory=list)
    taxiways: list = field(default_factory=list)
    stands: list = field(default_factory=list)
    holding_points: list = field(default_factory=list)

    frequencies: list = field(default_factory=list)
    procedures: list = field(default_factory=list)