from dataclasses import dataclass, field


@dataclass
class Node:

    id: int

    latitude: float

    longitude: float

    kind: str = "taxi"

    name: str | None = None

    taxiway: str | None = None

    apron: str | None = None

    runway: str | None = None

    gate: str | None = None

    parking: str | None = None

    terminal: str | None = None

    holding_point: str | None = None

    metadata: dict = field(default_factory=dict)