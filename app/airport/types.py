from enum import Enum


class NodeType(Enum):

    GATE = "gate"

    PARKING = "parking"

    INTERSECTION = "intersection"

    TAXIWAY = "taxiway"

    TAXIWAY_ENTRY = "taxiway_entry"

    TAXIWAY_EXIT = "taxiway_exit"

    RUNWAY_ENTRY = "runway_entry"

    RUNWAY_EXIT = "runway_exit"

    RUNWAY_THRESHOLD = "runway_threshold"

    HOLD_SHORT = "hold_short"

    APRON = "apron"

    TERMINAL = "terminal"

    SERVICE_ROAD = "service_road"

    HELIPAD = "helipad"

    UNKNOWN = "unknown"