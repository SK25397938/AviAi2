from dataclasses import dataclass, field

from .types import NodeType


@dataclass
class AirportNode:

    id: str

    name: str

    type: NodeType

    lat: float

    lon: float

    neighbors: list[str] = field(default_factory=list)

    metadata: dict = field(default_factory=dict)