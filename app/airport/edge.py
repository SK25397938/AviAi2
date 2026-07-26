from dataclasses import dataclass


@dataclass
class AirportEdge:

    start: str

    end: str

    name: str

    kind: str

    length: float