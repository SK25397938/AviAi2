from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Flight:
    flight_number: str
    callsign: str
    airline_code: str
    airline_name: str
    aircraft_type: str
    wake_category: str
    origin: str
    destination: str
    scheduled_time: datetime
    operation_type: str
    terminal: Optional[str] = None
    assigned_gate: Optional[str] = None
    assigned_runway: Optional[str] = None