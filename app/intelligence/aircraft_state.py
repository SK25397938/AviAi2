from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Clearance:

    heading: Optional[float] = None
    altitude_ft: Optional[int] = None
    speed_kts: Optional[int] = None

    direct_node: Optional[str] = None

    runway: Optional[str] = None


@dataclass
class ActiveClearance:

    event: str = "SPAWN"

    controller: str = ""

    instruction: str = ""

    heading: Optional[float] = None

    altitude_ft: Optional[int] = None

    speed_kts: Optional[int] = None

    next_node: Optional[str] = None

    runway: Optional[str] = None

    completed: bool = True


@dataclass
class AircraftState:

    callsign: str
    aircraft_type: str

    lat: float
    lon: float

    altitude_ft: int
    speed_kts: int
    heading_deg: float
    vertical_speed_fpm: int

    target_heading_deg: Optional[float] = None
    target_altitude_ft: Optional[int] = None
    target_speed_kts: Optional[int] = None

    assigned_node: Optional[str] = None
    target_node: Optional[str] = None

    destination: str = "N1"

    route: list = field(default_factory=list)
    route_index: int = 0

    phase: str = "ARRIVAL"

    approach_phase: str = "INTERCEPT"

    state: str = "ENROUTE"

    on_ground: bool = False

    landing_complete: bool = False

    controller: str = ""

    last_instruction: str = ""

    assigned_runway_exit: Optional[str] = None

    exit_distance_km: Optional[float] = None

    runway_vacated: bool = False

    spoilers_deployed: bool = False

    reverse_thrust: bool = False

    taxi_route: list = field(default_factory=list)

    taxi_index: int = 0

    parking_stand: Optional[str] = None

    clearance: Clearance = field(default_factory=Clearance)

    active_clearance: ActiveClearance = field(
        default_factory=ActiveClearance
    )

    pending_event: str = "SPAWN"

    ai_busy: bool = False

    recommended_altitude: Optional[int] = None

    recommended_speed: Optional[int] = None

    def __post_init__(self):

        self.target_heading_deg = self.heading_deg
        self.target_altitude_ft = self.altitude_ft
        self.target_speed_kts = self.speed_kts

    def update_position(
        self,
        lat,
        lon,
        altitude,
        speed,
        heading,
        vertical_speed
    ):

        self.lat = lat
        self.lon = lon
        self.altitude_ft = altitude
        self.speed_kts = speed
        self.heading_deg = heading
        self.vertical_speed_fpm = vertical_speed

    def assign_node(
        self,
        node
    ):

        self.assigned_node = node

    def assign_target(
        self,
        node
    ):

        self.target_node = node

    def assign_heading(
        self,
        heading
    ):

        self.target_heading_deg = heading
        self.clearance.heading = heading

    def assign_altitude(
        self,
        altitude
    ):

        self.target_altitude_ft = altitude
        self.clearance.altitude_ft = altitude

    def assign_speed(
        self,
        speed
    ):

        self.target_speed_kts = speed
        self.clearance.speed_kts = speed

    def assign_runway(
        self,
        runway
    ):

        self.clearance.runway = runway

    def assign_clearance(
        self,
        clearance
    ):

        self.active_clearance = clearance

        self.active_clearance.completed = False

        if clearance.heading is not None:
            self.assign_heading(clearance.heading)

        if clearance.altitude_ft is not None:
            self.assign_altitude(clearance.altitude_ft)

        if clearance.speed_kts is not None:
            self.assign_speed(clearance.speed_kts)

        if clearance.runway:
            self.assign_runway(clearance.runway)

        if clearance.next_node and not self.route:
            self.target_node = clearance.next_node

        self.controller = clearance.controller

        self.last_instruction = clearance.instruction

    def assign_route(
        self,
        route
    ):

        self.route = route
        self.route_index = 0

        if route:

            self.assigned_node = route[0]

            if len(route) > 1:

                self.target_node = route[1]

            else:

                self.target_node = None

    def advance_route(self):

        if not self.route:
            return

        self.route_index += 1

        if self.route_index >= len(self.route) - 1:

            self.assigned_node = self.route[-1]
            self.target_node = None

            return

        self.assigned_node = self.route[self.route_index]

        self.target_node = self.route[self.route_index + 1]

    def complete_clearance(
        self
    ):

        self.active_clearance.completed = True

    def __str__(self):

        return (
            f"{self.callsign} | "
            f"{self.aircraft_type} | "
            f"{self.phase} | "
            f"{self.approach_phase} | "
            f"{self.state} | "
            f"{self.altitude_ft:.0f} ft | "
            f"{self.speed_kts:.0f} kt"
        )
