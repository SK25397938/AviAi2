from app.simulation.manager import AircraftManager
from app.simulation.spawn import spawn_aircraft
from app.simulation.physics import move_aircraft

from app.intelligence.arrival_manager import ArrivalManager
from app.intelligence.arrival_ai import ArrivalAI
from app.intelligence.guidance_ai import GuidanceAI
from app.intelligence.approach_ai import ApproachAI
from app.intelligence.landing_ai import LandingAI

from app.intelligence.mistral.controller import mistral_controller

from app.navigation.runway import Runway
from app.navigation.centerline import build_centerline
from app.navigation.grid_builder import build_grid
from app.navigation.graph_builder import build_graph


class SimulationEngine:

    def __init__(
        self,
        airport_lat=19.0887,
        airport_lon=72.8679
    ):

        self.manager = AircraftManager()

        self.airport_lat = airport_lat
        self.airport_lon = airport_lon

        self.runway = Runway(
            ident="27",
            threshold_lat=airport_lat,
            threshold_lon=airport_lon,
            heading=270,
            length_nm=25
        )

        self.centerline = build_centerline(
            self.runway
        )

        self.grid = build_grid(
            self.centerline,
            self.runway
        )

        self.graph = build_graph(
            self.grid
        )

        self.arrival = ArrivalManager(
            self.graph
        )

        self.arrival_ai = ArrivalAI()

        self.guidance = GuidanceAI(
            self.graph
        )

        self.approach_ai = ApproachAI(
            self.runway
        )

        self.landing_ai = LandingAI(
            self.runway
        )

    def spawn(
        self,
        aircraft_type,
        callsign
    ):

        aircraft = spawn_aircraft(
            callsign,
            aircraft_type,
            self.airport_lat,
            self.airport_lon
        )

        self.arrival.assign_entry(
            aircraft
        )

        self.manager.add(
            aircraft
        )

    def update(
        self,
        dt=1.0
    ):

        traffic = self.manager.all()

        for aircraft in traffic:

            self.arrival_ai.update(
                aircraft
            )

            mistral_controller.update(
                aircraft,
                traffic
            )

            self.guidance.update(
                aircraft
            )

            if aircraft.phase == "FINAL":

                self.approach_ai.update(
                    aircraft
                )

            self.landing_ai.update(
                aircraft
            )

            move_aircraft(
                aircraft,
                dt
            )