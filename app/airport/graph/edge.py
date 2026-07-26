from dataclasses import dataclass, field


@dataclass
class Edge:

    id: int

    start: int

    end: int

    length: float

    name: str | None = None

    edge_type: str = "taxiway"

    taxiway: str | None = None

    runway: str | None = None

    apron: str | None = None

    speed_limit: float | None = None

    one_way: bool = False

    active: bool = True

    metadata: dict = field(default_factory=dict)