from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class SimulatedAircraft:
    id: str
    callsign: str
    flight_number: str
    airline: str
    aircraft_type: str
    wake_category: str
    origin: str
    destination: str
    latitude: float
    longitude: float
    altitude_ft: float
    speed_kts: float
    heading_deg: float
    vertical_speed_fpm: float
    phase: str
    status: str
    controller: str
    assigned_runway: Optional[str] = None
    assigned_gate: Optional[str] = None

    def to_dict(self):
        return asdict(self)